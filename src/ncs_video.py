"""Video background for the visual slot, decoded through an ffmpeg pipe.

Why ffmpeg and not OpenCV: `cv2` ships its own SDL2 dylib, and on this machine
it collides with pygame's copy -- 13 duplicate Objective-C classes, including
`SDLWindow` and `SDLAppDelegate`, which is a live crash risk rather than a
warning. Piping raw frames from ffmpeg avoids the conflict entirely and adds
no Python dependency, because ffmpeg is already required for the rest of the
build.

The video is VISUAL ONLY. The app already owns audio playback through
miniaudio/sounddevice, so ffmpeg is invoked with `-an` and its frames are
slaved to the player's playhead. Two videos playing audio at once is a bug,
not a feature.

What this module supports:
  * ordinary video files (anything ffmpeg can demux: .mp4, .mkv, .webm, ...)
  * `.strm` files, which are text files containing a single URL or path
  * a website URL, resolved to a direct media stream via yt-dlp when present
    (installed already), which is what makes "paste a link, get the video with
    no ads" work: yt-dlp picks the stream, and the page's ad markup is never
    rendered because nothing ever loads the page.
"""
import os
import select
import shutil
import subprocess
import time

import numpy as np
import pygame

# Extensions we will try to play as video in the visual slot.
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".mpg",
                   ".mpeg", ".wmv", ".flv", ".ts", ".m2ts", ".ogv", ".3gp")

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
YTDLP = shutil.which("yt-dlp") or shutil.which("yt_dlp")

# Frames are pulled at this rate. The visual is a background, not a film
# player; asking ffmpeg for the source rate would mean decoding far more than
# the window can show.
TARGET_FPS = 30

# Upper bound on the frame size we actually pull down the pipe.
#
# A full-window 1280x720 rgb24 frame is 2.7 MB, and the per-frame cost is
# dominated by moving that many bytes, not by decoding. Measured at 1280x800:
# a full-resolution pipe cost 34 ms/frame while a 640-wide pipe cost far less
# for a visually indistinguishable result, because the video is a soft moving
# background and smoothscale is the right tool for that. Cap the pipe, let
# pygame scale the result.
MAX_PIPE_WIDTH = 640


class VideoError(Exception):
    """Anything the user should see a readable message about."""


def have_ffmpeg():
    return shutil.which(FFMPEG) is not None or os.path.exists(FFMPEG)


def have_ytdlp():
    return YTDLP is not None and (os.path.exists(YTDLP) or shutil.which(YTDLP))


def is_video_file(path):
    """True when `path` looks like something this module should try to play."""
    if not path:
        return False
    ext = os.path.splitext(path)[1].lower()
    return ext in VIDEO_EXTENSIONS or ext == ".strm"


def read_strm(path):
    """A `.strm` file holds one URL or path as its first non-empty line."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    return line
    except OSError as e:
        raise VideoError(f"cannot read {os.path.basename(path)}: {e}")
    raise VideoError(f"{os.path.basename(path)} is empty")


def _looks_like_url(target):
    return target.startswith(("http://", "https://"))


def resolve_source(path_or_url, allow_web=True, timeout=30):
    """Turn a user-supplied thing into something ffmpeg can open.

    Returns (source_string, note) where `note` explains any web resolution, so
    the UI can tell the user where the video actually came from.
    """
    if not path_or_url:
        raise VideoError("nothing to play")

    target = path_or_url.strip()

    if os.path.isfile(target) and target.lower().endswith(".strm"):
        target = read_strm(target)

    if os.path.isfile(target):
        return target, ""

    if _looks_like_url(target):
        if not allow_web:
            raise VideoError("web sources are disabled in settings")
        if not have_ytdlp():
            raise VideoError("yt-dlp is needed for web links "
                             "(pip install yt-dlp)")
        return _resolve_web(target, timeout=timeout)

    if not os.path.exists(target):
        raise VideoError(f"no such file: {os.path.basename(target)}")
    return target, ""


def _resolve_web(url, timeout=30):
    """Ask yt-dlp for a direct progressive MP4 so ffmpeg can read it."""
    cmd = [YTDLP, "--no-playlist", "--no-warnings", "--quiet",
           "-f", "best[ext=mp4][vcodec!*=av01]/best[ext=mp4]/best",
           "-o", "-", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise VideoError("yt-dlp timed out")
    except OSError as e:
        raise VideoError(f"yt-dlp could not run: {e}")
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1][:160] if detail else "no stream found"
        raise VideoError(f"yt-dlp: {tail}")
    return proc.stdout.strip(), f"resolved from {url}"


def probe(source, timeout=15):
    """(width, height, duration_seconds, has_audio) for a source, or None.

    ffprobe failing is not fatal: ffmpeg can often still open a file whose
    header ffprobe dislikes, so the caller falls back to a sane default size.
    """
    cmd = [FFPROBE, "-v", "error", "-print_format", "json",
           "-show_streams", "-show_format", source]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
        import json
        data = json.loads(proc.stdout or "{}")
    except Exception:
        return None
    if not data:
        return None

    width = height = 0
    duration = 0.0
    has_audio = False
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not width:
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
        elif stream.get("codec_type") == "audio":
            has_audio = True
    fmt = data.get("format", {})
    if fmt.get("duration"):
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = 0.0
    if not width or not height:
        return None
    return width, height, duration, has_audio


# Never upscale a source by more than this. A tiny source blown up to a 4K
# window costs real CPU in ffmpeg's scaler and buys no visible detail; past
# this point the result is blurry either way.
MAX_UPSCALE = 4.0


def fit_rect(src_w, src_h, box_w, box_h, allow_upscale=True):
    """Largest (x, y, w, h) with the source aspect ratio that fills `box`.

    Letterboxes on the short axis when the aspects differ, so the frame is
    centred and never squashed. A background visual is better filling the
    window than floating in black bars, so upscaling is allowed up to
    MAX_UPSCALE; pass allow_upscale=False to fit strictly inside instead.
    """
    if src_w <= 0 or src_h <= 0 or box_w <= 0 or box_h <= 0:
        return 0, 0, max(1, box_w), max(1, box_h)

    scale = min(box_w / src_w, box_h / src_h)
    if not allow_upscale:
        scale = min(scale, 1.0)
    else:
        scale = min(scale, MAX_UPSCALE)

    w = max(1, int(src_w * scale))
    h = max(1, int(src_h * scale))
    return (box_w - w) // 2, (box_h - h) // 2, w, h


class VideoVisual:
    """A live ffmpeg frame source that yields pygame surfaces.

    One instance owns at most one ffmpeg process. `close()` is idempotent and
    is called from the draw path's failure branch, so it must be safe to call
    twice on a half-dead process.
    """

    duration = 0.0
    note = ""
    src_width = 0
    src_height = 0

    def __init__(self, source, width, height, fps=TARGET_FPS, start_at=0.0,
                 src_size=None):
        self.source = source
        self.proc = None
        self.fps = max(1, int(fps))
        if src_size:
            self.src_width, self.src_height = src_size
        else:
            self.src_width, self.src_height = width, height
        self.frame_bytes = width * height * 3
        self.width = width
        self.height = height
        self.frame_index = 0
        self.start_at = max(0.0, float(start_at))
        self.last_error = ""
        self.eof = False
        self._buffer = bytearray()
        self._last_surface = None    # reused when no new frame is ready
        self._open(start_at=self.start_at)

    # ---- process lifecycle ---------------------------------------------
    def _open(self, start_at=0.0):
        cmd = [FFMPEG, "-v", "error"]
        if start_at > 0:
            cmd += ["-ss", f"{start_at:.3f}"]
        cmd += ["-re", "-i", self.source,
                "-an",                      # audio belongs to the Player
                "-sn",
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "-vf", f"fps={self.fps},scale={self.width}:{self.height}",
                "-"]
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=self.frame_bytes * 4)
        except OSError as e:
            self.proc = None
            self.last_error = f"ffmpeg could not start: {e}"
            raise VideoError(self.last_error)

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def close(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdout:
                proc.stdout.close()
        except OSError:
            pass
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except OSError:
            pass

    def seek(self, seconds):
        """Restart the pipe at a new position. Used to follow the playhead."""
        self.close()
        self.frame_index = 0
        self.eof = False
        self._buffer = bytearray()
        self._last_surface = None      # stale frame belongs to the old position
        self._open(start_at=max(0.0, float(seconds)))

    # ---- frame production ----------------------------------------------
    # How long the very first frame may block the draw. A background that has
    # never shown anything is worse than a few ms of latency, so the first
    # read waits; every read after that is non-blocking.
    FIRST_FRAME_WAIT = 0.75

    def _read_available(self, want, timeout):
        """Read up to `want` bytes, waiting at most `timeout` seconds.

        With timeout 0 this never blocks. That is the difference between a
        video that paces the UI and one that stalls it: ffmpeg with `-re`
        emits frames at real time, so a blocking read on every frame would
        hold the main loop for a whole frame interval. The app runs at 60 FPS
        and the video at 30, so most frames should reuse the previous surface
        rather than wait for the next one.
        """
        if self.proc is None or self.proc.stdout is None:
            return b""
        try:
            fd = self.proc.stdout.fileno()
        except (OSError, ValueError):
            return b""
        try:
            ready, _w, _x = select.select([fd], [], [], max(0.0, timeout))
        except (OSError, ValueError):
            return b""
        if not ready:
            return b""
        try:
            # read1(), NOT read(): read(n) on a BufferedReader blocks until it
            # has n bytes or hits EOF, so a "ready" pipe that has only half a
            # frame would still stall the draw path. Measured: one such call
            # blocked for 268 ms. read1() returns whatever has arrived.
            reader = self.proc.stdout
            if hasattr(reader, "read1"):
                return reader.read1(want)
            return reader.read(want)
        except (OSError, ValueError):
            return b""

    def next_surface(self, box, allow_stale=True):
        """A pygame Surface for the next frame, letterboxed into `box`.

        The pipe produces a capped-size frame; this scales it to fill `box`.
        The first frame waits briefly (nothing on screen is worse than a few
        ms of latency); after that, a frame interval with nothing ready
        returns the PREVIOUS surface rather than blocking. Returns None at end
        of stream or on a dead pipe, so the caller can fall back.
        """
        if self.frame_bytes < 3 or self.width < 1 or self.height < 1:
            # A zero-size frame would reshape into an empty array and take
            # pygame down with it. Fail soft instead.
            self.eof = True
            self.close()
            return None

        if not self.is_alive() or self.proc is None or self.proc.stdout is None:
            self.eof = True
            return None

        first = self._last_surface is None
        deadline = (time.monotonic() + self.FIRST_FRAME_WAIT) if first else 0.0

        while len(self._buffer) < self.frame_bytes:
            timeout = 0.0
            if first:
                timeout = max(0.0, deadline - time.monotonic())
                if timeout <= 0.0:
                    break
            chunk = self._read_available(self.frame_bytes * 2, timeout)
            if not chunk:
                # Nothing ready. Distinguish "not yet" from "finished": if the
                # process is done and the buffer is short, it really is EOF.
                if self.proc.poll() is not None:
                    self.eof = True
                    return None
                if allow_stale and self._last_surface is not None:
                    return self._last_surface
                return None
            self._buffer.extend(chunk)

        if len(self._buffer) < self.frame_bytes:
            # First frame never arrived inside the wait. Report honestly
            # rather than painting garbage.
            if self.proc.poll() is not None:
                self.eof = True
            return None

        raw = bytes(self._buffer[:self.frame_bytes])
        del self._buffer[:self.frame_bytes]

        try:
            arr = np.frombuffer(raw, dtype=np.uint8)
            arr = arr.reshape(self.height, self.width, 3)
        except ValueError:
            self.eof = True
            return None

        # (w, h) -> pygame surfaces, one transpose per frame, unavoidable
        surf = pygame.surfarray.make_surface(np.transpose(arr, (1, 0, 2)))
        self.frame_index += 1

        # Destination: the source aspect, filling the box.
        bx, by, bw, bh = fit_rect(self.src_width, self.src_height,
                                   box.width, box.height)
        dest = pygame.Rect(bx, by, bw, bh)
        if (surf.get_width(), surf.get_height()) == (bw, bh):
            self._last_surface = (surf, dest)
            return self._last_surface
        # The pipe is deliberately smaller than the window (see
        # MAX_PIPE_WIDTH), so this upscale is the normal path, not an
        # exception. smoothscale beats a raw blit for a soft moving image.
        scaled = pygame.transform.smoothscale(surf, (bw, bh))
        self._last_surface = (scaled, dest)
        return self._last_surface

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def build_visual(path_or_url, box, allow_web=True, sync_to=None):
    """One-call construction: resolve, probe, open. Raises VideoError.

    `sync_to` is an optional float seconds; when given the pipe is opened at
    that offset so the video starts in step with the audio.
    """
    if not have_ffmpeg():
        raise VideoError("ffmpeg is not installed or not on PATH")
    if not have_ytdlp() and _looks_like_url(str(path_or_url).strip()):
        raise VideoError("yt-dlp is needed for web links (pip install yt-dlp)")

    source, note = resolve_source(path_or_url, allow_web=allow_web)
    info = probe(source)

    box_w = max(2, box.width)
    box_h = max(2, box.height)
    if info:
        src_w, src_h, duration, _has_audio = info
    else:
        # Unknown geometry: ask ffmpeg to produce the box size directly.
        src_w, src_h, duration = box_w, box_h, 0.0

    # fit_rect returns (x, y, w, h) -- take the size, not the offsets
    _x, _y, dest_w, dest_h = fit_rect(src_w, src_h, box_w, box_h)
    if dest_w < 1 or dest_h < 1:
        raise VideoError(f"could not size a frame for {os.path.basename(source)}")

    # The pipe is capped: a full-window frame is ~2.7 MB and moving those bytes
    # dominates the frame cost. Decode small, let pygame scale up.
    pipe_w, pipe_h = dest_w, dest_h
    if pipe_w > MAX_PIPE_WIDTH:
        shrink = MAX_PIPE_WIDTH / pipe_w
        pipe_w = max(2, int(pipe_w * shrink))
        pipe_h = max(2, int(pipe_h * shrink))

    start = 0.0 if sync_to is None else max(0.0, float(sync_to))
    visual = VideoVisual(source, pipe_w, pipe_h, start_at=start,
                         src_size=(src_w, src_h))
    visual.duration = duration
    visual.note = note
    return visual


# A sidecar for an audio track, by extension preference. Ordered so the
# smallest, most compatible container wins when several exist.
SIDECAR_EXTENSIONS = (".mp4", ".m4v", ".webm", ".mkv", ".mov", ".avi", ".flv")
SIDECAR_DIRS = ("", "videos", "video", ".video")


def find_sidecar(audio_path):
    """A video that belongs to `audio_path`, or None.

    Looks for the same basename with a video extension, in the track's own
    folder and in a conventional `videos/` subfolder. This is what makes the
    feature usable without any configuration: drop `Song.mp4` next to
    `Song.mp3` and the visual just appears.
    """
    if not audio_path:
        return None
    base = os.path.splitext(audio_path)[0]      # absolute, no extension
    stem = os.path.basename(base)               # the name only
    parent = os.path.dirname(audio_path)
    if not parent:
        parent = "."
    for sub in SIDECAR_DIRS:
        folder = os.path.join(parent, sub) if sub else parent
        for ext in SIDECAR_EXTENSIONS:
            # Build from the STEM, not from the absolute base, otherwise the
            # candidate is already absolute and the subfolder is never joined.
            candidate = os.path.join(folder, stem + ext)
            if os.path.isfile(candidate):
                return candidate
    return None


class VideoSlot:
    """Owns at most one live video and paints it into the visual area.

    The launcher's draw path calls `draw()` every frame. It never raises: any
    failure turns the slot off and reports why, so the visualizer cycle can
    fall back to another mode instead of taking the app down.
    """

    def __init__(self, allow_web=True, on_error=None):
        self.allow_web = allow_web
        self.on_error = on_error
        self.visual = None
        self.source = None
        self.message = ""
        self.enabled = False
        self.frames_drawn = 0
        self._want = None          # the source we would open next draw
        self._last_track = None
        self._seeked_to = -1.0
        self._pinned = False       # an explicit user choice outranks sidecars
        self._user_off = False     # the user pressed V to turn video off
        self._source_size = (0, 0)  # probed (w, h) of the current source

    # ---- state ---------------------------------------------------------
    def is_active(self):
        return self.visual is not None and not self.visual.eof

    def close(self):
        if self.visual is not None:
            self.visual.close()
        self.visual = None
        self.enabled = False

    def request(self, path_or_url, sync_to=None):
        """Queue a source; it is opened on the next draw, not here.

        Opening an ffmpeg pipe can block for a moment, and this is called from
        a key handler, so the work is deferred to the draw path.
        """
        if not path_or_url:
            self.close()
            self.message = ""
            return
        self._want = (path_or_url, sync_to)
        self.enabled = True

    def clear(self, by_user=True):
        self._want = None
        self.close()
        self.message = ""
        if by_user:
            self._user_off = True

    def toggle(self, path_or_url=None, sync_to=None):
        """V key: flip the video layer on or off.

        Turning it on without a source is legal and useful -- it arms the slot
        so the next track carrying a video picks it up automatically. The flip
        keys off `enabled`, not `is_active`, so an armed-but-empty slot can
        still be switched back off.
        """
        if self.enabled or self.is_active():
            self.clear(by_user=True)
            self.message = "video off"
            return False
        self._user_off = False
        if path_or_url:
            self.request(path_or_url, sync_to)
        else:
            # armed, waiting for follow_track() to supply a sidecar
            self.enabled = True
        self.message = "video on"
        return True

    # ---- per-frame -----------------------------------------------------
    def _fail(self, text):
        self.message = text
        self.visual = None
        self.enabled = False
        self._want = None
        # An automatic failure is NOT the user turning video off, so the slot
        # must stay eligible to pick up a later track that does have a video.
        self._user_off = False
        if self.on_error:
            self.on_error(text)
        return False

    def draw(self, screen, box, player=None):
        """Paint one frame. Returns True when a video frame was drawn."""
        if not self.enabled:
            return False

        # Open (or switch to) whatever was requested. A pending request must be
        # honoured even while another video is live, otherwise following a new
        # track silently keeps showing the previous track's video.
        if self._want is not None:
            want, sync = self._want
            self._want = None
            if self.visual is not None:
                self.visual.close()
                self.visual = None
            try:
                self.visual = build_visual(want, box, allow_web=self.allow_web,
                                            sync_to=sync)
                self.source = want
                self._seeked_to = -1.0
                self._source_size = self._probed_size(want)
                if getattr(self.visual, "note", ""):
                    self.message = self.visual.note
            except VideoError as e:
                return self._fail(str(e))
            except Exception as e:               # ffmpeg missing, bad args...
                return self._fail(f"video failed: {e}")

        if self.visual is None:
            # Armed but nothing to show yet: stay on and wait for a sidecar
            # rather than silently disarming ourselves.
            return False

        # A resized window means a differently sized frame. The target size is
        # derived from the SOURCE aspect letterboxed into the box, not from the
        # box alone -- comparing against the box would mismatch forever and
        # rebuild the pipe on every single frame.
        want_w, want_h = self._target_size(box)
        if (self.visual.width, self.visual.height) != (want_w, want_h):
            try:
                self.visual.close()
                self.visual = build_visual(self.source, box,
                                           allow_web=self.allow_web)
                self._seeked_to = -1.0
            except VideoError as e:
                return self._fail(str(e))

        # Follow the playhead, but only when it has actually drifted, so a
        # 30fps pipe is not restarted every frame.
        if player is not None:
            try:
                pos = float(player.position())
            except Exception:
                pos = None
            if pos is not None and abs(pos - self._seeked_to) > 1.0:
                if getattr(self.visual, "duration", 0.0):
                    wrapped = pos % self.visual.duration
                else:
                    wrapped = pos
                try:
                    self.visual.seek(wrapped)
                    self._seeked_to = wrapped
                except Exception:
                    pass

        result = self.visual.next_surface(box)
        if result is None:
            reason = "video ended" if self.visual.eof else "video stopped"
            return self._fail(reason)

        surf, rect = result
        screen.blit(surf, rect.topleft)
        self.frames_drawn += 1
        return True

    # ---- track awareness -----------------------------------------------
    def follow_track(self, track_path, player=None, box=None):
        """Point the slot at whatever video belongs to this track.

        Skipped when the user has pinned their own source or explicitly turned
        video off. A slot that merely FAILED on an earlier track is still
        eligible, so a later track with a good video recovers on its own.
        """
        if self._pinned or self._user_off:
            return False
        sidecar = find_sidecar(track_path)
        if not sidecar:
            return False
        if sidecar != self.source:
            self.request(sidecar, None)
        return True

    def pin(self, path_or_url, sync_to=None):
        """An explicit user choice; survives track changes."""
        self._pinned = True
        self.request(path_or_url, sync_to)

    def unpin(self):
        self._pinned = False

    # ---- sizing helpers -------------------------------------------------
    def _probed_size(self, want):
        """(w, h) of the source itself, for the aspect ratio."""
        try:
            source, _note = resolve_source(want, allow_web=self.allow_web)
            info = probe(source)
        except Exception:
            info = None
        if info:
            return info[0], info[1]
        return 0, 0

    def _target_size(self, box):
        """The PIPE size build_visual would choose for this box.

        This is the capped size, not the on-screen size, because that is what
        VideoVisual.width/height hold.
        """
        dest_w, dest_h = self._target_size_raw(box)
        if dest_w > MAX_PIPE_WIDTH:
            shrink = MAX_PIPE_WIDTH / dest_w
            return max(2, int(dest_w * shrink)), max(2, int(dest_h * shrink))
        return dest_w, dest_h

    def _target_size_raw(self, box):
        """The on-screen size: source aspect filling the box."""
        sw, sh = self._source_size
        if sw <= 0 or sh <= 0:
            return max(2, box.width), max(2, box.height)
        _x, _y, w, h = fit_rect(sw, sh, max(2, box.width), max(2, box.height))
        return max(1, w), max(1, h)


def _slot_size(box):
    _x, _y, w, h = fit_rect(box.width, box.height, box.width, box.height)
    return w, h