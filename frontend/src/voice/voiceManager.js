const {
  joinVoiceChannel: djsJoinVoiceChannel,
  VoiceConnectionStatus,
  entersState,
  getVoiceConnection,
} = require("@discordjs/voice");
const { AudioRecorder } = require("./audioRecorder");

// Map of guild ID -> AudioRecorder instance
const recorders = new Map();

/**
 * Join a voice channel and begin subscribing to user audio streams.
 */
async function joinVoiceChannel(voiceChannel, guild) {
  // Destroy existing connection if any
  const existingConnection = getVoiceConnection(guild.id);
  if (existingConnection) {
    existingConnection.destroy();
    recorders.get(guild.id)?.destroy();
    recorders.delete(guild.id);
  }

  const connection = djsJoinVoiceChannel({
    channelId: voiceChannel.id,
    guildId: guild.id,
    adapterCreator: guild.voiceAdapterCreator,
    selfDeaf: false, // Must NOT be deaf to receive audio
    selfMute: true,
  });

  // Wait for the connection to be ready
  try {
    await entersState(connection, VoiceConnectionStatus.Ready, 20_000);
    console.log(`[Voice] Connected to ${voiceChannel.name} in ${guild.name}`);
  } catch (error) {
    connection.destroy();
    throw error;
  }

  // Create a recorder for this guild
  const recorder = new AudioRecorder(connection, guild.id);
  recorders.set(guild.id, recorder);

  // Subscribe to the audio receiver for speaking events
  const receiver = connection.receiver;

  // When a user starts speaking, subscribe to their audio stream
  receiver.speaking.on("start", (userId) => {
    recorder.handleUserSpeaking(userId, receiver);
  });

  // Handle disconnection / reconnection
  connection.on(VoiceConnectionStatus.Disconnected, async () => {
    try {
      // Try to reconnect
      await Promise.race([
        entersState(connection, VoiceConnectionStatus.Signalling, 5_000),
        entersState(connection, VoiceConnectionStatus.Connecting, 5_000),
      ]);
      // Reconnecting...
    } catch {
      // Could not reconnect, clean up
      connection.destroy();
      recorders.get(guild.id)?.destroy();
      recorders.delete(guild.id);
    }
  });

  connection.on(VoiceConnectionStatus.Destroyed, () => {
    recorders.get(guild.id)?.destroy();
    recorders.delete(guild.id);
    console.log(`[Voice] Connection destroyed for guild ${guild.id}`);
  });

  return connection;
}

/**
 * Leave the voice channel for a guild.
 */
function leaveVoiceChannel(guildId) {
  const connection = getVoiceConnection(guildId);
  if (connection) {
    recorders.get(guildId)?.destroy();
    recorders.delete(guildId);
    connection.destroy();
    return true;
  }
  return false;
}

module.exports = { joinVoiceChannel, leaveVoiceChannel };
