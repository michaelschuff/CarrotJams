import asyncio
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from music_cog import Music

# To run on old: 
# clear; clear; /usr/local/bin/python3.12 /Users/michaelschuff/Desktop/CarrotJams/main.py 
# To run on new: 
# clear; clear; /usr/local/bin/python3.12 /Users/michaelschuff/ComputerScience/Projects/CarrotJams/main.py

load_dotenv()
token = os.getenv("discordToken")

intents = discord.Intents(
    messages=True,
    guilds=True,
    members=True,
    message_content=True,
    presences=True,
    voice_states=True,
)

bot = commands.Bot(
    command_prefix="]",
    intents=intents,
    help_command=None
)
bot.help_command = commands.DefaultHelpCommand(
    command_attrs={ "hidden": True }
)


@bot.event
async def on_ready():
    await bot.add_cog(Music(bot))
    print(f"Logged in as {bot.user}")

bot.run(token)
