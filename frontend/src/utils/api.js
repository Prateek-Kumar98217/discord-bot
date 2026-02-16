const axios = require("axios");
const FormData = require("form-data");

const BACKEND_API_URL =
  process.env.BACKEND_API_URL || "http://localhost:3001/api/audio";

/**
 * Sends a recorded PCM audio buffer to the backend API as a WAV file.
 *
 * @param {object} params
 * @param {string} params.userId - Discord user ID
 * @param {string} params.guildId - Discord guild ID
 * @param {Buffer} params.pcmBuffer - Raw PCM audio data (signed 16-bit LE)
 * @param {number} params.sampleRate - Sample rate (48000)
 * @param {number} params.channels - Number of channels (2)
 * @param {number} params.durationMs - Duration in milliseconds
 */
async function sendAudioToBackend({
  userId,
  guildId,
  pcmBuffer,
  sampleRate,
  channels,
  durationMs,
}) {
  // Wrap PCM in a WAV container for the backend
  const wavBuffer = createWavBuffer(pcmBuffer, sampleRate, channels);

  const form = new FormData();
  form.append("audio", wavBuffer, {
    filename: `${userId}-${Date.now()}.wav`,
    contentType: "audio/wav",
  });
  form.append("userId", userId);
  form.append("guildId", guildId);
  form.append("durationMs", String(Math.round(durationMs)));
  form.append("sampleRate", String(sampleRate));
  form.append("channels", String(channels));
  form.append("timestamp", new Date().toISOString());

  const response = await axios.post(BACKEND_API_URL, form, {
    headers: {
      ...form.getHeaders(),
    },
    maxContentLength: Infinity,
    maxBodyLength: Infinity,
    timeout: 30000,
  });

  console.log(
    `[API] Sent audio for user ${userId} — Status: ${response.status}`,
  );
  return response.data;
}

/**
 * Creates a WAV file buffer from raw PCM data.
 * WAV format: RIFF header + PCM data
 */
function createWavBuffer(pcmBuffer, sampleRate, channels) {
  const bitsPerSample = 16;
  const byteRate = sampleRate * channels * (bitsPerSample / 8);
  const blockAlign = channels * (bitsPerSample / 8);
  const dataSize = pcmBuffer.length;
  const headerSize = 44;

  const wavBuffer = Buffer.alloc(headerSize + dataSize);

  // RIFF header
  wavBuffer.write("RIFF", 0);
  wavBuffer.writeUInt32LE(36 + dataSize, 4); // File size - 8
  wavBuffer.write("WAVE", 8);

  // fmt sub-chunk
  wavBuffer.write("fmt ", 12);
  wavBuffer.writeUInt32LE(16, 16); // Sub-chunk size (16 for PCM)
  wavBuffer.writeUInt16LE(1, 20); // Audio format (1 = PCM)
  wavBuffer.writeUInt16LE(channels, 22); // Number of channels
  wavBuffer.writeUInt32LE(sampleRate, 24); // Sample rate
  wavBuffer.writeUInt32LE(byteRate, 28); // Byte rate
  wavBuffer.writeUInt16LE(blockAlign, 32); // Block align
  wavBuffer.writeUInt16LE(bitsPerSample, 34); // Bits per sample

  // data sub-chunk
  wavBuffer.write("data", 36);
  wavBuffer.writeUInt32LE(dataSize, 40); // Data size

  // Copy PCM data
  pcmBuffer.copy(wavBuffer, headerSize);

  return wavBuffer;
}

module.exports = { sendAudioToBackend };
