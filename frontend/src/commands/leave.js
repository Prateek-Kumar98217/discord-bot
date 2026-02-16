const { SlashCommandBuilder } = require("discord.js");
const { leaveVoiceChannel } = require("../voice/voiceManager");

module.exports = {
  data: new SlashCommandBuilder()
    .setName("leave")
    .setDescription("Leave the current voice channel and stop recording"),

  async execute(interaction) {
    const connection = leaveVoiceChannel(interaction.guild.id);

    if (connection) {
      await interaction.reply({
        content: "👋 Left the voice channel. Recording stopped.",
      });
    } else {
      await interaction.reply({
        content: "❌ I'm not in a voice channel!",
        ephemeral: true,
      });
    }
  },
};
