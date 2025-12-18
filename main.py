import asyncio
import os
import discord
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from discord.ext import commands
from dotenv import load_dotenv
import re

import utilities

# To run:
# clear; clear; /usr/local/bin/python3.12 /Users/michaelschuff/Desktop/CarrotJams/main.py

load_dotenv()
token = os.getenv('discordToken')
spotifyClientID = os.getenv('spotifyClientID')
spotifyClientSecret = os.getenv('spotifyClientSecret')

intents = discord.Intents(messages=True, guilds=True, members=True, message_content=True, presences=True, voice_states=True)
bot = commands.Bot(command_prefix=']', intents=intents)
spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=spotifyClientID,
                                                           client_secret=spotifyClientSecret))

# Optimized FFmpeg options: reconnect + explicit audio parameters (48kHz, stereo, 96kbps Opus)
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -ar 48000 -ac 2 -b:a 96k -compression_level 5 -application audio"
}
FFMPEG_PATH = "/opt/local/bin/ffmpeg"
# FFMPEG_PATH = "/usr/local/bin/ffmpeg"

FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_delay_max 5"
)


# ---------- yt-dlp options (lower bitrate, explicit node path, SABR-safe clients) ----------
YDL_OPTS = {
    # prefer Opus <= 128kbps, fallback to other opus / best audio
    "format": "ba[acodec=opus][abr<=128]/ba[acodec=opus]/bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["web", "default"]
        }
    },
    # explicitly point to your node runtime to avoid yt-dlp JS detection issues
    "exe": {"js": "/opt/homebrew/bin/node"}
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
    if not session.q.has_next():
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


def _extract_info_sync(query, is_url):
    """Run inside a thread: create YoutubeDL and extract info synchronously."""
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        if is_url:
            return ydl.extract_info(query, download=False)
        else:
            return ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]


def _extract_audio_url_from_info(info) -> str | None:
    """
    Given a fully-extracted yt-dlp video info dict,
    return a direct audio-only stream URL.
    """
    formats = info.get("formats", []) or []
    audio_formats = [
        f for f in formats
        if f.get("acodec")
        and f.get("acodec") != "none"
        and (not f.get("vcodec") or f.get("vcodec") == "none")
        and f.get("url")
    ]

    if not audio_formats:
        return None

    # Prefer highest abr (already capped by YDL_OPTS)
    best = max(audio_formats, key=lambda f: f.get("abr") or 0)
    return best["url"]

def _is_probable_url(text: str) -> bool:
    """Simple heuristic to detect URLs (supports youtube links & http(s))."""
    text_lower = text.lower()
    return text_lower.startswith("http://") or text_lower.startswith("https://") or "youtube.com" in text_lower or "youtu.be" in text_lower or "spotify.com" in text_lower

def is_spotify_link(text: str) -> bool:
    return "spotify.com" in text

async def fetch_video_info(loop, ctx, arg, is_url):
    try:
        return await loop.run_in_executor(None, _extract_info_sync, arg, is_url)
    except Exception as e:
        await ctx.send("Erro ao extrair informações do YouTube.")
        print("yt-dlp extract error:", e)
        return None
    
async def handle_youtube(ctx, arg, session, loop, is_url, voice_channel, spotify_playlist_search):
    info = await fetch_video_info(loop, ctx, arg, is_url)
    if not info:
        return
    
    # print("info:",info)
    tracks_added = 0
    thumb = None
    title = "Unknown title"
    # ---------------- PLAYLIST HANDLING ----------------
    if info.get("_type") == "playlist":
        entries = info.get("entries", [])

        for entry in entries:
            if not entry or not entry.get("original_url"):
                continue
            info =  await fetch_video_info(loop, ctx, entry.get("original_url"), True)
            audio_url = _extract_audio_url_from_info(info)
            if not audio_url:
                await ctx.send("Erro: Não consegui pegar audio do YouTube.")
                return

            title = info.get("title", "Unknown title")
            thumb = None
            if info.get("thumbnails"):
                thumb = info["thumbnails"][0].get("url")

            session.q.enqueue(title, audio_url, thumb)
            tracks_added += 1

    # ---------------- SINGLE VIDEO ----------------
    else:
        audio_url = _extract_audio_url_from_info(info)
        if not audio_url:
            await ctx.send("Erro: Não consegui pegar audio do YouTube.")
            return

        title = info.get("title", "Unknown title")
        thumb = None
        if info.get("thumbnails"):
            thumb = info["thumbnails"][0].get("url")

        session.q.enqueue(title, audio_url, thumb)
        tracks_added = 1

    # ---------------- VOICE CONNECTION ----------------
    if not ctx.voice_client:
        try:
            vc = await voice_channel.connect()
        except Exception as e:
            await ctx.send("Erro ao conectar no canal de voz.")
            print("connect error:", e)
            return
    else:
        vc = ctx.voice_client



    # ---------------- START PLAYBACK IF IDLE ----------------
    if not vc.is_playing():
        session.q.set_first_as_current()

        source = discord.FFmpegOpusAudio(
            session.q.current_music.url,
            executable=FFMPEG_PATH,
            before_options=FFMPEG_BEFORE_OPTIONS,
            options=FFMPEG_OPTIONS
        )

        vc.play(source, after=lambda e: prepare_continue_queue(ctx))
    # print(session.q.queue)

    if tracks_added == 1 and not spotify_playlist_search:
        if thumb:
            await ctx.send(thumb)
        await ctx.send(f"Tocando agora: {title}")
    return tracks_added

async def handle_spotify(ctx, arg, session, loop, is_url, voice_channel):
    tracks_added = 0
    # ---------------- PLAYLIST HANDLING ----------------
    if "playlist" in arg:
        results = spotify.playlist_items(arg)
        print(results)
        for item in results["items"]:
            track_info = item["track"]
            query = f"{track_info['name']} {', '.join(map(lambda a: a["name"], track_info['artists']))}"
            tracks_added += await handle_youtube(ctx, query, session, loop, False, voice_channel, True)
    elif "album" in arg:
        results = spotify.album_tracks(arg)
        print(results)
        for track_info in results["items"]:
            query = f"{track_info['name']} {', '.join(map(lambda a: a["name"], track_info['artists']))}"
            tracks_added += await handle_youtube(ctx, query, session, loop, False, voice_channel, True)
    else:
        track_info = spotify.track(arg)
        print(track_info)
        query = f"{track_info['name']} {', '.join(map(lambda a: a["name"], track_info['artists']))}"
        tracks_added += await handle_youtube(ctx, query, session, loop, False, voice_channel, False)
        
    return tracks_added

@bot.command(name='play', guild=GUILD_ID)
async def play(ctx, *, arg):
    try:
        voice_channel = ctx.author.voice.channel
    except AttributeError:
        await ctx.send("Tu não tá conectado num canal de voz, burro")
        return

    session = check_session(ctx)
    loop = asyncio.get_event_loop()
    is_url = _is_probable_url(arg)
    is_spotify = is_spotify_link(arg)
    tracks_added = 0
    # ---------------- SPOTIFY HANDLING ----------------
    if is_spotify:
       tracks_added += await handle_spotify(ctx, arg, session, loop, is_url, voice_channel)

    # ---------------- YOUTUBE HANDLING ----------------
    else:
        tracks_added += await handle_youtube(ctx, arg, session, loop, is_url, voice_channel, False)
    # Initial extraction (may be playlist OR single video)

    # ---------------- FEEDBACK ----------------
    if tracks_added > 1:
        await ctx.send(f"🎶 Adicionadas **{tracks_added} músicas** da playlist.")
    


@bot.command(name='next', aliases=['skip'], guild=GUILD_ID)
async def next(ctx):
    session = check_session(ctx)
    if not session.q.has_next():
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
    # print(session.q.queue)


@bot.command(name='print', guild=GUILD_ID)
async def print_info(ctx):
    session = check_session(ctx)
    await ctx.send(f"Session ID: {session.id}")
    await ctx.send(f"Música atual: {session.q.current_music.title}")
    queue = [q[0] for q in session.q.queue]
    await ctx.send(f"Queue: {queue}")




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


@bot.command(name='stop', aliases=['clear', 'leave'], guild=GUILD_ID)
async def stop(ctx):
    session = check_session(ctx)
    voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice and voice.is_playing():
        session.q.clear_queue()
        await voice.disconnect()
    else:
        await ctx.send("Não tem nada tocando ô abobado.")


bot.run(token)
