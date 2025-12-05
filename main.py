import asyncio
import os
import discord
import yt_dlp
from discord.ext import commands
from dotenv import load_dotenv

import utilities

# To run:
# clear; clear; /usr/local/bin/python3.12 /Users/michaelschuff/Desktop/CarrotJams/main.py

load_dotenv()
token = os.getenv('discordToken')

intents = discord.Intents(messages=True, guilds=True, members=True, message_content=True, presences=True, voice_states=True)
bot = commands.Bot(command_prefix=']', intents=intents)

# Optimized FFmpeg options: reconnect + explicit audio parameters (48kHz, stereo, 96kbps Opus)
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -ar 48000 -ac 2 -b:a 96k -compression_level 5 -application audio"
}

sessions = []
GUILD_ID = discord.Object(id=882313681369718806)

def check_session(ctx):
    if len(sessions) > 0:
        for i in sessions:
            if i.guild == ctx.guild and i.channel == ctx.author.voice.channel:
                return i
        session = utilities.Session(ctx.guild, ctx.author.voice.channel, id=len(sessions))
        sessions.append(session)
        return session
    else:
        session = utilities.Session(ctx.guild, ctx.author.voice.channel, id=0)
        sessions.append(session)
        return session


def prepare_continue_queue(ctx):
    fut = asyncio.run_coroutine_threadsafe(continue_queue(ctx), bot.loop)
    try:
        fut.result()
    except Exception as e:
        print("prepare_continue_queue error:", e)


async def continue_queue(ctx):
    session = check_session(ctx)
    if not session.q.theres_next():
        await ctx.send("Acabou a queue, brother.")
        return

    session.q.next()

    voice = discord.utils.get(bot.voice_clients, guild=session.guild)
    if not voice:
        # If voice client not found, connect
        vc = await session.channel.connect()
    else:
        vc = voice

    try:
        source = await discord.FFmpegOpusAudio.from_probe(session.q.current_music.url, **FFMPEG_OPTIONS)
    except Exception as e:
        await ctx.send("Erro ao preparar audio (probe).")
        print("from_probe error:", e)
        return

    if vc.is_playing():
        vc.stop()

    vc.play(source, after=lambda e: prepare_continue_queue(ctx))
    await ctx.send(session.q.current_music.thumb)
    await ctx.send(f"Tocando agora: {session.q.current_music.title}")


@bot.command()
async def sync(ctx: commands.Context):
    print("syncing...")
    await bot.tree.sync(guild=ctx.guild)
    print("done syncing.")


# ---------- yt-dlp options (lower bitrate, explicit node path, SABR-safe clients) ----------
YDL_OPTS = {
    # prefer Opus <= 128kbps, fallback to other opus / best audio
    "format": "ba[acodec=opus][abr<=128]/ba[acodec=opus]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["web", "default", "web_safari"]
        }
    },
    # explicitly point to your node runtime to avoid yt-dlp JS detection issues
    "exe": {"js": "/opt/homebrew/bin/node"}
}
# ----------------------------------------------------------------------------------------


def _extract_info_sync(query, is_url):
    """Run inside a thread: create YoutubeDL and extract info synchronously."""
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        if is_url:
            return ydl.extract_info(query, download=False)
        else:
            return ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]


def _is_probable_url(text: str) -> bool:
    """Simple heuristic to detect URLs (supports youtube links & http(s))."""
    text_lower = text.lower()
    return text_lower.startswith("http://") or text_lower.startswith("https://") or "youtube.com" in text_lower or "youtu.be" in text_lower


@bot.command(name='play', guild=GUILD_ID)
async def play(ctx, *, arg):
    """
    Non-blocking yt-dlp extraction + lower-bitrate audio selection + safe voice flow.
    """
    try:
        voice_channel = ctx.author.voice.channel
    except AttributeError:
        await ctx.send("Tu não tá conectado num canal de voz, burro")
        return

    session = check_session(ctx)

    loop = asyncio.get_event_loop()
    is_url = _is_probable_url(arg)

    # Offload extraction to a thread so the event loop isn't blocked.
    try:
        info = await loop.run_in_executor(None, _extract_info_sync, arg, is_url)
    except Exception as e:
        await ctx.send("Erro ao extrair informações do YouTube.")
        print("yt-dlp extract error:", e)
        return

    # Choose the best audio-only format (prefer audio-only with abr value)
    formats = info.get("formats", []) or []
    audio_formats = [
        f for f in formats
        if f.get("acodec") and f.get("acodec") != "none" and (not f.get("vcodec") or f.get("vcodec") == "none")
    ]

    if not audio_formats:
        # Sometimes the top-level info has direct url (rare). Try fallback:
        url_direct = info.get("url")
        if url_direct:
            url = url_direct
        else:
            await ctx.send("Erro: Não consegui pegar audio do YouTube.")
            return
    else:
        # pick the format with highest abr (but since we limited abr<=128 in selector, it will be <=128)
        best = max(audio_formats, key=lambda f: f.get("abr") or 0)
        url = best.get("url")

    # Safely get thumbnail and title
    thumb = None
    if info.get("thumbnails"):
        thumb = info["thumbnails"][0].get("url")
    title = info.get("title", "Unknown title")

    session.q.enqueue(title, url, thumb)

    # Voice connect flow: await connect and reuse ctx.voice_client if present
    if not ctx.voice_client:
        try:
            vc = await voice_channel.connect()
        except Exception as e:
            await ctx.send("Erro ao conectar no canal de voz.")
            print("connect error:", e)
            return
    else:
        vc = ctx.voice_client

    if vc.is_playing():
        await ctx.send(thumb or "")
        await ctx.send(f"Adicionado à queue: {title}")
        return
    else:
        await ctx.send(thumb or "")
        await ctx.send(f"Tocando agora: {title}")
        session.q.set_last_as_current()

        try:
            source = await discord.FFmpegOpusAudio.from_probe(url, **FFMPEG_OPTIONS)
        except Exception as e:
            await ctx.send("Erro ao preparar audio (probe).")
            print("from_probe error:", e)
            return

        vc.play(source, after=lambda ee: prepare_continue_queue(ctx))


@bot.command(name='next', aliases=['skip'], guild=GUILD_ID)
async def skip(ctx):
    session = check_session(ctx)
    if not session.q.theres_next():
        await ctx.send("Não tem porra nenhuma na fila, mangolão")
        return

    voice = discord.utils.get(bot.voice_clients, guild=session.guild)
    if voice and voice.is_playing():
        voice.stop()
        return
    else:
        session.q.next()
        try:
            source = await discord.FFmpegOpusAudio.from_probe(session.q.current_music.url, **FFMPEG_OPTIONS)
        except Exception as e:
            await ctx.send("Erro ao preparar audio (probe).")
            print("from_probe error:", e)
            return
        voice.play(source, after=lambda e: prepare_continue_queue(ctx))


@bot.command(name='print', guild=GUILD_ID)
async def print_info(ctx):
    session = check_session(ctx)
    await ctx.send(f"Session ID: {session.id}")
    await ctx.send(f"Música atual: {session.q.current_music.title}")
    queue = [q[0] for q in session.q.queue]
    await ctx.send(f"Queue: {queue}")


@bot.command(name='leave', guild=GUILD_ID)
async def leave(ctx):
    voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice and voice.is_connected():
        check_session(ctx).q.clear_queue()
        await voice.disconnect()
    else:
        await ctx.send("Bot not connect, so it can't leave.")


@bot.command(name='pause', guild=GUILD_ID)
async def pause(ctx):
    voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice and voice.is_playing():
        voice.pause()
    else:
        await ctx.send("Não ta tocando porra nenhuma mlk")


@bot.command(name='resume', guild=GUILD_ID)
async def resume(ctx):
    voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice and voice.is_paused():
        voice.resume()
    else:
        await ctx.send("Música já ta pausada, mangolao")


@bot.command(name='stop', guild=GUILD_ID)
async def stop(ctx):
    session = check_session(ctx)
    voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice and voice.is_playing():
        voice.stop()
        session.q.clear_queue()
    else:
        await ctx.send("Não tem nada tocando ô abobado.")


bot.run(token)
