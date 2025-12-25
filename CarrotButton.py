import discord
from discord.ext import commands
import music_cog

class PrevTrackButton(discord.ui.Button):
    def __init__(self, cog: "Music", ctx: commands.Context, label, style, custom_id):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.cog = cog
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.cog.action_previous(self.ctx, True)

class NextTrackButton(discord.ui.Button):
    def __init__(self, cog: "Music", ctx: commands.Context, label, style, custom_id):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.cog = cog
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.cog.action_skip(self.ctx, True)

class PauseResumeTrackButton(discord.ui.Button):
    def __init__(self, cog: "Music", ctx: commands.Context, label, style, custom_id):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.cog = cog
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        vc = self.ctx.voice_client
        if not vc:
            return

        if vc.is_playing():
            await self.cog.action_pause(self.ctx, True)
        elif vc.is_paused():
            await self.cog.action_resume(self.ctx, True)

class ClearQueueButton(discord.ui.Button):
    def __init__(self, cog: "Music", ctx: commands.Context, label, style, custom_id):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.cog = cog
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.cog.action_clear(self.ctx, True)


class LeaveButton(discord.ui.Button):
    def __init__(self, cog: "Music", ctx: commands.Context, label, style, custom_id):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.cog = cog
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.cog.action_leave(self.ctx, True)