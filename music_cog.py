import asyncio
import discord
from discord.ext import commands
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
from discord.ui import View, Button, Select
import utilities
import CarrotButton


# Optimized FFmpeg options: reconnect + explicit audio parameters (48kHz, stereo, 96kbps Opus) 
FFMPEG_OPTIONS = { 
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", 
    "options": "-vn -ar 48000 -ac 2 -b:a 96k -compression_level 5 -application audio" 
}
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
FFMPEG_PATH = "/opt/local/bin/ffmpeg" 
# new: 
# FFMPEG_PATH = "/usr/local/bin/ffmpeg"




EMBED_QUEUE_MAX_TITLE_LENGTH = 50


class Music(commands.Cog):
    """Music playback commands"""

    def _is_probable_url(self, text: str) -> bool: 
        """Simple heuristic to detect URLs (supports youtube links & http(s)).""" 
        text_lower = text.lower() 
        return text_lower.startswith("http://") or text_lower.startswith("https://") or "youtube.com" in text_lower or "youtu.be" in text_lower or "spotify.com" in text_lower
    def is_spotify_link(self, text: str) -> bool: 
        return "spotify.com" in text

    async def fetch_video_info(self, loop, ctx, arg, is_url): 
        try: 
            return await loop.run_in_executor(None, self._extract_info_sync, arg, is_url) 
        except Exception as e: 
            await ctx.send("Erro ao extrair informações do YouTube.") 
            print("yt-dlp extract error:", e) 
            return None

    def _extract_audio_url_from_info(self, info) -> str | None: 
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

    def _extract_info_sync(self, query, is_url): 
        """Run inside a thread: create YoutubeDL and extract info synchronously.""" 
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl: 
            if is_url: 
                return ydl.extract_info(query, download=False) 
            else: 
                return ydl.extract_info(f"ytsearch:{query}", download=False)['entries'][0]

    async def handle_youtube(self, ctx, arg, session, loop, is_url, voice_channel, spotify_playlist_search): 
        info = await self.fetch_video_info(loop, ctx, arg, is_url) 
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
                info = await self.fetch_video_info(loop, ctx, entry.get("original_url"), True) 
                audio_url = self._extract_audio_url_from_info(info) 
                if not audio_url: 
                    await ctx.send("Erro: Não consegui pegar audio do YouTube.") 
                    continue
                title = info.get("title", "Unknown title") 
                thumb = None 
                if info.get("thumbnails"): 
                    thumb = info["thumbnails"][0].get("url") 
                session.q.enqueue(title, audio_url, thumb, entry.get("original_url")) 
                tracks_added += 1 
        # ---------------- SINGLE VIDEO ---------------- 
        else: 
            audio_url = self._extract_audio_url_from_info(info) 
            if not audio_url: 
                await ctx.send("Erro: Não consegui pegar audio do YouTube.") 
                return 0
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
                return tracks_added
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
            vc.play(source, after=lambda e: self.prepare_continue_queue(ctx)) 
            # print(session.q.queue) 
            # if tracks_added == 1 and not spotify_playlist_search: 
                # if thumb: 
                    # await ctx.send(thumb) 
                    # await ctx.send(f"Tocando agora: {title}") 
        return tracks_added

    async def handle_spotify(self, ctx, arg, session, loop, is_url, voice_channel): 
        tracks_added = 0 
        # ---------------- PLAYLIST HANDLING ---------------- 
        if "playlist" in arg: 
            results = spotify.playlist_items(arg) 
            for item in results["items"]: 
                track_info = item["track"] 
                query = f"{track_info['name']} {', '.join(map(lambda a: a["name"], track_info['artists']))}" 
                tracks_added += await self.handle_youtube(ctx, query, session, loop, False, voice_channel, True) 
        elif "album" in arg: 
            results = spotify.album_tracks(arg) 
            for track_info in results["items"]: 
                query = f"{track_info['name']} {', '.join(map(lambda a: a["name"], track_info['artists']))}" 
                tracks_added += await self.handle_youtube(ctx, query, session, loop, False, voice_channel, True) 
        else: 
            track_info = spotify.track(arg) 
            query = f"{track_info['name']} {', '.join(map(lambda a: a["name"], track_info['artists']))}" 
            tracks_added += await self.handle_youtube(ctx, query, session, loop, False, voice_channel, False) 
        return tracks_added


    

    
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



    def prepare_continue_queue(self, ctx): 
        fut = asyncio.run_coroutine_threadsafe(self.continue_queue(ctx), self.bot.loop) 
        try: 
            fut.result() 
        except Exception as e: 
            print("prepare_continue_queue error:", e) 

    async def continue_queue(self, ctx): 
        session = self.check_session(ctx) 
        if session.is_paused: 
            return 

        if not session.q.loop and session.end_of_queue:
            await ctx.send("Acabou a queue, brother.") 
            return 

        if session.q.curr_index == -1:
            session.next()



        if not session.q.loop and session.q.curr_index == len(session.q)-1:
            session.end_of_queue = True

        voice = discord.utils.get(self.bot.voice_clients, guild=session.guild) 

        if not voice: 
            # If voice client not found, connect 
            vc = await session.channel.connect()
        else: 
            vc = voice 

        try: 
            source = discord.FFmpegOpusAudio( 
                session.q.current_music.url, 
                executable=FFMPEG_PATH, 
                before_options=FFMPEG_BEFORE_OPTIONS, 
                options=FFMPEG_OPTIONS 
            )
        except Exception as e: 
            await ctx.send("Erro ao preparar audio (probe).") 
            print("from_probe error:", e) 
            return 

        if vc.is_playing(): 
            vc.stop() 

        vc.play(source, after=lambda e: self.prepare_continue_queue(ctx))

    def get_embed_view(self, ctx: commands.Context, session):
        formatted_queue = ""
        if len(session.q) > 0:
            for m in range(min(len(session.q), 10)):
                abb_title = session.q[m][0]
                if len(abb_title) > EMBED_QUEUE_MAX_TITLE_LENGTH:
                    abb_title = abb_title[:EMBED_QUEUE_MAX_TITLE_LENGTH-3:]
                    abb_title += "..."

                index = str(m + 1) + "."
                if session.q.curr_index == m:
                    if session.is_paused:
                        index = "⏸️"
                    elif session.stopped:
                        index = "⏹️"
                    else:
                        index = "▶️"

                formatted_queue += "\n" + index + " [" + abb_title + "](" + session.q[m][3] + ")"


        embed_title = "Now Playing"
        if session.is_paused:
            embed_title = "Paused"
        if session.stopped:
            embed_title = "Stopped"
        if not session.q.current_music:
            embed_title = "Waiting"

        description_title = "Nenhuma faixa selecionada"
        if session.q.current_music:
            description_title = session.q.current_music.title

        # Send new embed
        embed = discord.Embed(
            title=embed_title,
            description=description_title,
            color=discord.Color.orange()
        )

        if session.q.current_music:
            embed.set_thumbnail(url=session.q.current_music.thumb)
        else:
            embed.set_thumbnail(url="https://t4.ftcdn.net/jpg/02/04/10/95/360_F_204109503_OxuR11rq9CLkEFkjWphOBABSDTBTNJrc.jpg")

        embed.add_field(name="Queue:",value=formatted_queue)

        # view = PlayerView(self, ctx)


        view = discord.ui.View()
        prev_track = CarrotButton.PrevTrackButton(self, ctx, "⏮️", discord.ButtonStyle.secondary, "prev_track")
        pp_track = None
        if session.is_paused or session.stopped:
            pp_track = CarrotButton.PauseResumeTrackButton(self, ctx, "▶️", discord.ButtonStyle.success, "pp_track")
        else:
            pp_track = CarrotButton.PauseResumeTrackButton(self, ctx, "⏸️", discord.ButtonStyle.primary, "pp_track")

        next_track = CarrotButton.NextTrackButton(self, ctx, "⏭️", discord.ButtonStyle.secondary, "next_track")
        clear_queue = CarrotButton.ClearQueueButton(self, ctx, "🚫", discord.ButtonStyle.secondary, "clear_queue")
        leave = CarrotButton.LeaveButton(self, ctx, "☠️", discord.ButtonStyle.secondary, "leave")

        # Add buttons to the view
        view.add_item(prev_track)
        view.add_item(pp_track)
        view.add_item(next_track)
        view.add_item(clear_queue)
        view.add_item(leave)
        return embed, view


    async def edit_player_message(self, ctx: commands.Context):
        session = self.check_session(ctx)

        embed, view = self.get_embed_view(ctx, session)

        if session.player_message_id and session.player_channel_id:
            try:
                channel = self.bot.get_channel(session.player_channel_id)
                if channel:
                    old_msg = await channel.fetch_message(session.player_message_id)
                    await old_msg.edit(embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden):
                pass  # message already gone or no perms

    async def replace_player_message(
        self,
        ctx: commands.Context
    ):
        session = self.check_session(ctx)

        # Delete old message if it exists
        if session.player_message_id and session.player_channel_id:
            try:
                channel = self.bot.get_channel(session.player_channel_id)
                if channel:
                    old_msg = await channel.fetch_message(session.player_message_id)
                    await old_msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass  # message already gone or no perms


        
        embed, view = self.get_embed_view(ctx, session)
        msg = await ctx.send(embed=embed, view=view)

        # Store new message info
        session.player_message_id = msg.id
        session.player_channel_id = msg.channel.id





    async def action_play(self, ctx: commands.Context, query: str, from_button: bool):
        if not query:
            return

        try:
            voice_channel = ctx.author.voice.channel
        except AttributeError:
            await ctx.send("Vamos fazer uma ligação, brother.")
            return

        session = self.check_session(ctx)

        if not ctx.voice_client:
            await voice_channel.connect()

        is_url = self._is_probable_url(query)
        is_spotify = self.is_spotify_link(query)
        loop = asyncio.get_event_loop()
        tracks_added = 0

        session.is_paused = False

        if is_spotify:
            tracks_added += await self.handle_spotify(
                ctx, query, session, loop, is_url, voice_channel
            )
        else:
            tracks_added += await self.handle_youtube(
                ctx, query, session, loop, is_url, voice_channel, False
            )

        if tracks_added >= 1:
            session.end_of_queue = False
        if not from_button:
            await self.replace_player_message(ctx)
        else:
            await self.edit_player_message(ctx)
    
    async def action_skip(self, ctx: commands.Context, from_button: bool):
        session = self.check_session(ctx)
        if ctx.voice_client:
            if session.q.has_next():
                session.q.next()
                ctx.voice_client.stop()
                if not from_button:
                    await self.replace_player_message(ctx)
                else:
                    await self.edit_player_message(ctx)
            else:
                await ctx.send("Acabou a queue, brother.")
        else:
            await ctx.send("Não estou conectado, brother.")


    async def action_previous(self, ctx: commands.Context, from_button: bool):
        session = self.check_session(ctx)
        if ctx.voice_client:
            if session.q.has_previous():
                session.q.previous()
                session.end_of_queue = False
                ctx.voice_client.stop()
                if not from_button:
                    await self.replace_player_message(ctx)
                else:
                    await self.edit_player_message(ctx)
            else:
                await ctx.send("Não há faixa anterior, brother.")
            

        else:
            await ctx.send("Não estou conectado, brother.")


    async def action_pause(self, ctx: commands.Context, from_button: bool):
        session = self.check_session(ctx)

        if ctx.voice_client and ctx.voice_client.is_playing():
            session.is_paused = True
            ctx.voice_client.pause()
            if not from_button:
                await self.replace_player_message(ctx)
            else:
                await self.edit_player_message(ctx)
        else:
            await ctx.send("Não está tocando nada, brother.")


    async def action_resume(self, ctx: commands.Context, from_button: bool):
        session = self.check_session(ctx)
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            session.is_paused = False
            session.stopped = False
            if not from_button:
                await self.replace_player_message(ctx)
            else:
                await self.edit_player_message(ctx)
        elif not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
            source = discord.FFmpegOpusAudio( 
                session.q.current_music.url, 
                executable=FFMPEG_PATH, 
                before_options=FFMPEG_BEFORE_OPTIONS, 
                options=FFMPEG_OPTIONS 
            )
            session.is_paused = False

            if not from_button:
                await self.replace_player_message(ctx)
            else:
                await self.edit_player_message(ctx)
            ctx.voice_client.play(source, after=lambda e: self.prepare_continue_queue(ctx)) 

        else:
            await ctx.send("Não há nada para retomar, brother.")

    async def action_clear(self, ctx: commands.Context, from_button: bool):
        session = self.check_session(ctx)
        session.q.clear_queue()
        if ctx.voice_client:
            ctx.voice_client.stop()

        if not from_button:
            await self.replace_player_message(ctx)
        else:
            await self.edit_player_message(ctx)


    async def action_leave(self, ctx: commands.Context, from_button: bool):
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
        else:
            await ctx.send("Não estou conectado, brother.")

    async def action_loop(self, ctx: commands.Context, from_button: bool):
        session.q.loop = True
        if not from_button:
            await self.replace_player_message(ctx)
        else:
            await self.edit_player_message(ctx)

    async def action_unloop(self, ctx: commands.Context, from_button: bool):
        session.q.loop = False
        if not from_button:
            await self.replace_player_message(ctx)
        else:
            await self.edit_player_message(ctx)


    # ---------- commands ----------
    @commands.command(
        help="Play a song or playlist from YouTube or Spotify.",
        brief="Play music",
        usage="<url | search terms>"
    )
    async def play(self, ctx, *, query: str):
        await self.action_play(ctx, query, False)


    @commands.command(
        aliases=["next"],
        help="Skip the current song.",
        brief="Skip song",
        usage=""
    )
    async def skip(self, ctx):
        """Skip the current song."""
        await self.action_skip(ctx, False)
    
    @commands.command(
            aliases=["prev"],
            help="Plays the previous song (if it exists).",
            brief="Plays the previous song (if it exists).",
            usage=""
        )
    async def previous(self, ctx):
        """Skip the current song."""
        await self.action_previous(ctx, False)

    @commands.command(
        help="Pause playback.",
        brief="Pause music",
        usage=""
    )
    async def pause(self, ctx):
        """Pause playback."""
        await self.action_pause(ctx, False)


    @commands.command(
        help="Resume playback.",
        brief="Resume music",
        usage=""
    )
    async def resume(self, ctx):
        """Resume playback."""
        await self.action_resume(ctx, False)

    @commands.command(
        help="Loop the queue",
        brief="Loop the queue",
        usage=""
    )
    async def loop(self, ctx):
        """Loop the queue."""
        await self.action_loop(ctx, False)

    @commands.command(
        help="Unloop the queue",
        brief="Unloop the queue",
        usage=""
    )
    async def unloop(self, ctx):
        """Unloop the queue."""
        await self.action_unloop(ctx, False)

    @commands.command(
        help="Clear the queue.",
        brief="Clear the queue.",
        usage=""
    )
    async def clear(self, ctx):
        """Resume playback."""
        await self.action_clear(ctx, False)


    # @commands.command(
    #     help="Stop playback and clear the queue.",
    #     brief="Stop music",
    #     usage=""
    # )
    # async def stop(self, ctx):
    #     """Stop playback and clear the queue."""
    #     session = self.check_session(ctx)
    #     if ctx.voice_client:
    #         ctx.voice_client.stop()
    #         session.stopped = True
    #         await self.replace_player_message(ctx)
    #     else:
    #         await ctx.send("Nothing is playing.")

    @commands.command(
        help="Disconnect the bot from voice.",
        brief="Leave voice",
        usage=""
    )
    async def leave(self, ctx):
        """Disconnect the bot from voice."""
        await self.action_leave(ctx, False)



    @commands.command(
        aliases=["show_player", "show", "player", ],
        help="Bring up info about the current session.",
        brief="Bring up info about the current session.",
        usage=""
    )
    async def print(self, ctx):
        """Print info about about the session"""
        session = self.check_session(ctx)
        await self.replace_player_message(ctx)
