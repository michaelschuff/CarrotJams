import asyncio
import discord
from discord.ext import commands
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os


import utilities
# Optimized FFmpeg options: reconnect + explicit audio parameters (48kHz, stereo, 96kbps Opus) 
FFMPEG_OPTIONS = { 
	"before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", 
	"options": "-vn -ar 48000 -ac 2 -b:a 96k -compression_level 5 -application audio" 
}
# old: 
# FFMPEG_PATH = "/opt/local/bin/ffmpeg" 
# new: FFMPEG_PATH = "/usr/local/bin/ffmpeg" 
FFMPEG_BEFORE_OPTIONS = ( "-reconnect 1 " "-reconnect_streamed 1 " "-reconnect_delay_max 5" ) 
# ---------- yt-dlp options (lower bitrate, explicit node path, SABR-safe clients) ---------- 
YDL_OPTS = { # prefer Opus <= 128kbps, fallback to other opus / best audio 
	"format": "ba[acodec=opus][abr<=128]/ba[acodec=opus]/bestaudio/best", 
	"noplaylist": False, 
	"quiet": True, 
	"extractor_args": { 
		"youtube": { 
			"player_client": ["web", "default"] 
		} 
	}, # explicitly point to your node runtime to avoid yt-dlp JS detection issues 
	"exe": {"js": "/opt/homebrew/bin/node"}
}

# old: 
# FFMPEG_PATH = "/opt/local/bin/ffmpeg" 
# new: 
FFMPEG_PATH = "/usr/local/bin/ffmpeg"

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

def _extract_audio_url_from_info(info) -> str | None: 
	""" Given a fully-extracted yt-dlp video info dict, return a direct audio-only stream URL. """ 
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

def _extract_info_sync(query, is_url): 
	"""Run inside a thread: create YoutubeDL and extract info synchronously.""" 
	with yt_dlp.YoutubeDL(YDL_OPTS) as ydl: 
		if is_url: 
			return ydl.extract_info(query, download=False) 
		else: 
			return ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]

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
            info = await fetch_video_info(loop, ctx, entry.get("original_url"), True) 
            audio_url = _extract_audio_url_from_info(info) 
            if not audio_url: 
                await ctx.send("Erro: Não consegui pegar audio do YouTube.") 
                return 
            title = info.get("title", "Unknown title") 
            thumb = None 
            if info.get("thumbnails"): 
                thumb = info["thumbnails"][0].get("url") 
            session.q.enqueue(title, audio_url, thumb, entry.get("original_url")) 
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
        session.q.enqueue(title, audio_url, thumb, info.get("original_url")) 
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
        for item in results["items"]: 
            track_info = item["track"] 
            query = f"{track_info['name']} {', '.join(map(lambda a: a["name"], track_info['artists']))}" 
            tracks_added += await handle_youtube(ctx, query, session, loop, False, voice_channel, True) 
    elif "album" in arg: 
        results = spotify.album_tracks(arg) 
        for track_info in results["items"]: 
            query = f"{track_info['name']} {', '.join(map(lambda a: a["name"], track_info['artists']))}" 
            tracks_added += await handle_youtube(ctx, query, session, loop, False, voice_channel, True) 
    else: 
        track_info = spotify.track(arg) 
        query = f"{track_info['name']} {', '.join(map(lambda a: a["name"], track_info['artists']))}" 
        tracks_added += await handle_youtube(ctx, query, session, loop, False, voice_channel, False) 
    return tracks_added

def prepare_continue_queue(ctx): 
	fut = asyncio.run_coroutine_threadsafe(continue_queue(ctx), bot.loop) 
	try: 
		fut.result() 
	except Exception as e: 
		print("prepare_continue_queue error:", e) 

async def continue_queue(ctx): 
	session = check_session(ctx) 
	if session.is_paused: 
		return 

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

class Music(commands.Cog):
    """Music playback commands"""

    
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.sessions = []

        self.spotify = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=os.getenv("spotifyClientID"),
                client_secret=os.getenv("spotifyClientSecret"),
            )
        )

        self.FFMPEG_OPTIONS = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn -ar 48000 -ac 2 -b:a 96k",
        }

        self.FFMPEG_PATH = "/usr/local/bin/ffmpeg"

        self.YDL_OPTS = {
            "format": "ba[acodec=opus][abr<=128]/bestaudio/best",
            "quiet": True,
        }

    # ---------- helpers ----------

    def check_session(self, ctx):
        for s in self.sessions:
            if s.guild == ctx.guild and s.channel == ctx.author.voice.channel:
                return s

        session = utilities.Session(ctx.guild, ctx.author.voice.channel, id=len(self.sessions))
        self.sessions.append(session)
        return session

    # def is_url(self, text: str) -> bool:
    #     return text.startswith("http")

    # ---------- commands ----------

    @commands.command(
        help="Play a song or playlist from YouTube or Spotify.",
        brief="Play music",
        usage="<url | search terms>"
    )
    async def play(self, ctx, *, query: str):
        """Play a song or playlist from YouTube or Spotify."""
        try:
            voice_channel = ctx.author.voice.channel
        except AttributeError:
            await ctx.send("You must be connected to a voice channel.")
            return

        session = self.check_session(ctx)

        if not ctx.voice_client:
            await voice_channel.connect()


        is_url = _is_probable_url(query) 
        is_spotify = is_spotify_link(query) 
        loop = asyncio.get_event_loop()
        tracks_added = 0 
        # ---------------- SPOTIFY HANDLING ---------------- 
        if is_spotify: 
            tracks_added += await handle_spotify(ctx, query, session, loop, is_url, voice_channel)
        # ---------------- YOUTUBE HANDLING ---------------- 
        else: 
            tracks_added += await handle_youtube(ctx, query, session, loop, is_url, voice_channel, False)
         # Initial extraction (may be playlist OR single video) 
         # ---------------- FEEDBACK ---------------- 
        if tracks_added > 1: 
             await ctx.send(f"🎶 Adicionadas **{tracks_added} músicas** da playlist.")


    @commands.command(
        aliases=["next"],
        help="Skip the current song.",
        brief="Skip song",
        usage=""
    )
    async def skip(self, ctx):
        """Skip the current song."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        else:
            await ctx.send("Nothing is playing.")

    @commands.command(
        help="Pause playback.",
        brief="Pause music",
        usage=""
    )
    async def pause(self, ctx):
        """Pause playback."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
        else:
            await ctx.send("Nothing is playing.")

    @commands.command(
        help="Resume playback.",
        brief="Resume music",
        usage=""
    )
    async def resume(self, ctx):
        """Resume playback."""
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
        else:
            await ctx.send("Nothing to resume.")

    @commands.command(
        help="Stop playback and clear the queue.",
        brief="Stop music",
        usage=""
    )
    async def stop(self, ctx):
        """Stop playback and clear the queue."""
        if ctx.voice_client:
            ctx.voice_client.stop()
        else:
            await ctx.send("Nothing is playing.")

    @commands.command(
        help="Disconnect the bot from voice.",
        brief="Leave voice",
        usage=""
    )
    async def leave(self, ctx):
        """Disconnect the bot from voice."""
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
        else:
            await ctx.send("Not connected.")
