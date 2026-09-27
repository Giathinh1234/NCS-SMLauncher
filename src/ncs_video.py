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
import shutil
import subprocess

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

    def __init__(self, source, width, height, fps=TARGET_FPS, start_at=0.0):
        self.source = source
        self.proc = None
        self.fps = max(1, int(fps))
        self.frame_bytes = width * height * 3
        self.width = width
        self.height = height
        self.frame_index = 0
        self.start_at = max(0.0, float(start_at))
        self.last_error = ""
        self.eof = False
        self._buffer = bytearray()
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
        self._open(start_at=max(0.0, float(seconds)))

    # ---- frame production ----------------------------------------------
    def next_surface(self, box):
        """A pygame Surface for the next frame, letterboxed into `box`.

        `box` is a pygame.Rect. Returns None at end of stream or on a dead
        pipe, so the caller can fall back to another visualizer.
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

        while len(self._buffer) < self.frame_bytes:
            try:
                chunk = self.proc.stdout.read(self.frame_bytes * 2)
            except (OSError, ValueError):
                chunk = b""
            if not chunk:
                self.eof = True
                return None
            self._buffer.extend(chunk)

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

        bx, by, bw, bh = fit_rect(self.width, self.height, box.width, box.height)
        if (bw, bh) == (surf.get_width(), surf.get_height()):
            return surf, pygame.Rect(bx, by, bw, bh)
        scaled = pygame.transform.smoothscale(surf, (bw, bh))
        return scaled, pygame.Rect(bx, by, bw, bh)

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
    _x, _y, target_w, target_h = fit_rect(src_w, src_h, box_w, box_h)
    if target_w < 1 or target_h < 1:
        raise VideoError(f"could not size a frame for {os.path.basename(source)}")

    start = 0.0 if sync_to is None else max(0.0, float(sync_to))
    visual = VideoVisual(source, target_w, target_h, start_at=start)
    visual.duration = duration
    visual.note = note
    return visual
