const { SlashCommandBuilder } = require("discord.js");
const { joinVoiceChannel } = require("../voice/voiceManager");

module.exports = {
  data: new SlashCommandBuilder()
    .setName("join")
    .setDescription("Join your current voice channel and start recording"),

  async execute(interaction) {
    const member = interaction.member;
    const voiceChannel = member.voice.channel;

    if (!voiceChannel) {
      return interaction.reply({
        content: "You need to be in a voice channel first!",
        ephemeral: true,
      });
    }

    // Check bot permissions
    const permissions = voiceChannel.permissionsFor(interaction.client.user);
    if (!permissions.has("Connect") || !permissions.has("Speak")) {
      return interaction.reply({
        content: "I need permissions to join and speak in that voice channel!",
        ephemeral: true,
      });
    }

    try {
      await joinVoiceChannel(voiceChannel, interaction.guild);
      await interaction.reply({
        content: `🎙️ Joined **${voiceChannel.name}** and recording audio streams!`,
      });
    } catch (error) {
      console.error("[Join] Error:", error);
      await interaction.reply({
        content: "Failed to join the voice channel.",
        ephemeral: true,
      });
    }
  },
};
