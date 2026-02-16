const { EndBehaviorType } = require("@discordjs/voice");
const prism = require("prism-media");
const { sendAudioToBackend } = require("../utils/api");

const SILENCE_DURATION_MS =
  parseInt(process.env.SILENCE_DURATION_MS, 10) || 1000;
const SAMPLE_RATE = 48000; // Discord sends 48kHz audio
const CHANNELS = 2; // Stereo
const FRAME_DURATION = 20; // 20ms Opus frames

/**
 * Manages per-user audio recording with Voice Activity Detection (VAD).
 * After SILENCE_DURATION_MS of silence, the buffered audio is sent to the backend.
 */
class AudioRecorder {
  constructor(connection, guildId) {
    this.connection = connection;
    this.guildId = guildId;

    // Map of userId -> user recording state
    this.users = new Map();
    this.destroyed = false;
  }

  /**
   * Called when a user starts speaking. Subscribes to their opus audio stream
   * if not already subscribed.
   */
  handleUserSpeaking(userId, receiver) {
    if (this.destroyed) return;

    // If we already have an active subscription for this user, just reset silence timer
    if (this.users.has(userId)) {
      const userState = this.users.get(userId);
      userState.isSpeaking = true;
      this._resetSilenceTimer(userId);
      return;
    }

    console.log(`[Recorder] Subscribing to user ${userId}`);

    const userState = {
      userId,
      audioChunks: [],
      isSpeaking: true,
      silenceTimer: null,
      subscription: null,
    };

    // Subscribe to the user's audio stream
    // EndBehaviorType.Manual means the stream won't auto-close(most important part)
    const opusStream = receiver.subscribe(userId, {
      end: {
        behavior: EndBehaviorType.Manual,
      },
    });

    // Decode Opus -> signed 16-bit little-endian PCM
    const decoder = new prism.opus.Decoder({
      rate: SAMPLE_RATE,
      channels: CHANNELS,
      frameSize: (SAMPLE_RATE * FRAME_DURATION) / 1000,
    });

    userState.opusStream = opusStream;
    userState.decoder = decoder;

    opusStream.pipe(decoder);

    decoder.on("data", (pcmChunk) => {
      if (this.destroyed) return;

      // Buffer the PCM data
      userState.audioChunks.push(pcmChunk);
      userState.isSpeaking = true;

      // Reset the silence timer every time we receive audio data
      this._resetSilenceTimer(userId);
    });

    decoder.on("error", (err) => {
      console.error(
        `[Recorder] Decoder error for user ${userId}:`,
        err.message,
      );
    });

    opusStream.on("error", (err) => {
      console.error(
        `[Recorder] Opus stream error for user ${userId}:`,
        err.message,
      );
    });

    opusStream.on("close", () => {
      console.log(`[Recorder] Opus stream closed for user ${userId}`);
      this._flushAndSend(userId);
    });

    this.users.set(userId, userState);
    this._resetSilenceTimer(userId);
  }

  /**
   * Reset the silence timer for a user. After SILENCE_DURATION_MS of no new
   * audio data, we consider the user stopped speaking and flush the buffer.
   */
  _resetSilenceTimer(userId) {
    const userState = this.users.get(userId);
    if (!userState) return;

    if (userState.silenceTimer) {
      clearTimeout(userState.silenceTimer);
    }

    userState.silenceTimer = setTimeout(() => {
      userState.isSpeaking = false;
      this._flushAndSend(userId);
    }, SILENCE_DURATION_MS);
  }

  /**
   * Flush the audio buffer for a user and send it to the backend API.
   */
  async _flushAndSend(userId) {
    const userState = this.users.get(userId);
    if (!userState || userState.audioChunks.length === 0) return;

    // Grab the buffered audio and reset
    const chunks = userState.audioChunks;
    userState.audioChunks = [];

    // Concatenate all PCM chunks into one buffer
    const pcmBuffer = Buffer.concat(chunks);

    // Only send if we have a meaningful amount of audio (> 100ms)
    const minBytes = (SAMPLE_RATE * CHANNELS * 2 * 100) / 1000; // 2 bytes per sample (16-bit)
    if (pcmBuffer.length < minBytes) {
      console.log(
        `[Recorder] Skipping short audio from user ${userId} (${pcmBuffer.length} bytes)`,
      );
      return;
    }

    const durationMs = (pcmBuffer.length / (SAMPLE_RATE * CHANNELS * 2)) * 1000;
    console.log(
      `[Recorder] Sending ${durationMs.toFixed(0)}ms of audio from user ${userId}`,
    );

    try {
      await sendAudioToBackend({
        userId,
        guildId: this.guildId,
        pcmBuffer,
        sampleRate: SAMPLE_RATE,
        channels: CHANNELS,
        durationMs,
      });
    } catch (error) {
      console.error(
        `[Recorder] Failed to send audio for user ${userId}:`,
        error.message,
      );
    }
  }

  /**
   * Clean up all user streams and timers.
   */
  destroy() {
    this.destroyed = true;

    for (const [userId, userState] of this.users) {
      if (userState.silenceTimer) {
        clearTimeout(userState.silenceTimer);
      }
      // Flush remaining audio
      this._flushAndSend(userId);

      // Clean up streams
      try {
        userState.decoder?.destroy();
        userState.opusStream?.destroy();
      } catch {
        // Ignore cleanup errors
      }

      console.log(`[Recorder] Cleaned up user ${userId}`);
    }

    this.users.clear();
  }
}

module.exports = { AudioRecorder };
