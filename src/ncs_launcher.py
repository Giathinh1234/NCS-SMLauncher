#!/usr/bin/env python3
"""
NCS-Style Music Launcher (with native torrent downloads)
========================================================
A terminal-launched music player with a real-time FFT visualizer
in the spirit of NoCopyrightSounds' neon bar visuals.

Usage:
    python ncs_launcher.py [music_folder]

Controls:
    Up / Down     select track
    Enter         play selected track
    Space         pause / resume
    Left / Right  seek +/-5s
    M             mute
    F             cycle visualizer mode (bars / mirror / radial / disc)
    T             open torrent overlay — paste an infohash or magnet link,
                  it downloads natively via libtorrent and lands in your library
    O             change source folder
    Esc / Q       quit

Requires:
    pip install numpy sounddevice miniaudio pygame libtorrent mutagen pillow
"""

import os
import sys
import math
import glob
import time
import json
import urllib.parse
import webbrowser
import threading
import subprocess
import queue
import colorsys
from PIL import Image, ImageDraw
from mutagen.mp3 import MP3
from mutagen.id3 import ID3NoHeaderError
import io
import hashlib

import numpy as np

from media_keys import MediaKeyTap, open_accessibility_settings

try:
    import sounddevice as sd
except ImportError:
    sys.exit("Missing dependency 'sounddevice'. Run: pip install sounddevice")

try:
    import miniaudio
except ImportError:
    sys.exit("Missing dependency 'miniaudio'. Run: pip install miniaudio")

try:
    import pygame
except ImportError:
    sys.exit("Missing dependency 'pygame'. Run: pip install pygame")

try:
    import libtorrent as lt
except ImportError:
    lt = None   # torrent features disabled, everything else still works


AUDIO_EXTENSIONS = ("*.mp3", "*.wav", "*.ogg", "*.flac", "*.m4a")
SAMPLE_RATE = 44100
FFT_SIZE = 2048
NUM_BARS = 64

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TORRENT_DIR = os.path.join(APP_DIR, "torrent-downloads")
TORRENT_STATE_DIR = os.path.join(TORRENT_DIR, ".state")
ALBUM_ART_CACHE_DIR = os.path.join(APP_DIR, ".album_art_cache")
os.makedirs(ALBUM_ART_CACHE_DIR, exist_ok=True)

EASTER_KEYWORDS = ["fernando alonso", "seven nation army"]
EASTER_DIR = os.path.join(APP_DIR, "easter_eggs")
os.makedirs(EASTER_DIR, exist_ok=True)

# --------------------------------------------------------------------------
# Torrent manager (native, via libtorrent)
# --------------------------------------------------------------------------
class TorrentManager:
    """Downloads infohashes/magnet links to TORRENT_DIR in a background session."""

    def __init__(self):
        self.messages = queue.Queue()   # (kind, text) kind in info/warn/error/done
        self.session = None
        self.handles = {}               # infohash-string -> lt.torrent_handle
        self.lock = threading.Lock()
        if lt is not None:
            self.session = lt.session({
                'listen_interfaces': '0.0.0.0:6881',
                'alert_mask': lt.alert.category_t.status_notification
                              | lt.alert.category_t.error_notification,
            })
            threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def normalize(uri):
        uri = uri.strip()
        if not uri:
            return None
        if uri.startswith("magnet:") or uri.startswith("http"):
            return uri
        # bare hex/base32 infohash -> magnetize
        if len(uri) == 40 and all(c in "0123456789abcdefABCDEF" for c in uri):
            return "magnet:?xt=urn:btih:" + uri.lower()
        if len(uri) == 32 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in uri):
            return "magnet:?xt=urn:btih:" + uri
        return None

    def start_download(self, uri):
        if self.session is None:
            self.messages.put(("error", "libtorrent not available"))
            return False
        magnet = self.normalize(uri)
        if not magnet:
            self.messages.put(("error", "not a valid infohash / magnet link"))
            return False
        try:
            params = lt.parse_magnet_uri(magnet)
            ih = str(params.info_hashes.v1 if hasattr(params.info_hashes, "v1")
                     else params.info_hashes)
            key = ih[:16]
            with self.lock:
                if key in self.handles:
                    self.messages.put(("warn", "already downloading that one"))
                    return False
                params.save_path = TORRENT_DIR
                h = self.session.add_torrent(params)
                self.handles[key] = h
            name = params.name or ih
            self.messages.put(("info", f"downloading: {name}"))
            return True
        except Exception as e:
            self.messages.put(("error", f"could not start: {e}"))
            return False

    def status_lines(self):
        """Compact per-torrent status for the overlay."""
        lines = []
        with self.lock:
            items = list(self.handles.items())
        for key, h in items:
            try:
                st = h.status()
                if st.is_seeding or st.is_finished:
                    txt = f"✔ done: {st.name}"
                    with self.lock:
                        self.handles.pop(key, None)   # finished; stop tracking
                else:
                    peers = st.num_peers
                    pct = int(st.progress * 100)
                    spd = st.download_payload_rate / 1000
                    txt = f"↓ {pct}%  {peers}p  {spd:.0f}kB/s  {st.name[:28]}"
                lines.append(txt)
            except Exception:
                pass
        return lines[-3:]   # show at most 3

    def _loop(self):
        while True:
            alerts = self.session.pop_alerts()
            for a in alerts:
                if a.category() & lt.alert.category_t.error_notification:
                    msg = str(a)
                    if "metadata" not in msg.lower():
                        self.messages.put(("error", msg[:80]))
            time.sleep(1)


# --------------------------------------------------------------------------
# Audio decode + playback
# --------------------------------------------------------------------------
def decode_file(path):
    data = miniaudio.decode_file(path, nchannels=1, sample_rate=SAMPLE_RATE,
                                 output_format=miniaudio.SampleFormat.SIGNED16)
    samples = np.frombuffer(data.samples, dtype=np.int16).astype(np.float32) / 32768.0
    return samples


class Player(threading.Thread):
    """Streams decoded audio through sounddevice while tracking position."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples = None
        self.pos = 0
        self.paused = True
        self.muted = False
        self.track_path = None
        self.lock = threading.Lock()
        self.ring = np.zeros(FFT_SIZE * 2, dtype=np.float32)
        self.stream = sd.OutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            blocksize=1024, callback=self._callback)

    def _callback(self, outdata, frames, time_info, status):
        out = np.zeros((frames, 1), dtype=np.float32)
        with self.lock:
            if self.samples is not None and not self.paused:
                end = self.pos + frames
                chunk = self.samples[self.pos:end]
                out[:len(chunk), 0] = chunk
                self.pos += len(chunk)
                if self.pos >= len(self.samples):
                    self.pos = len(self.samples) - 1   # hold at end; UI advances
                if len(chunk):
                    self.ring = np.roll(self.ring, -len(chunk))
                    self.ring[-len(chunk):] = chunk
            elif self.samples is not None and self.paused:
                self.ring = np.roll(self.ring, -frames)
                self.ring[-frames:] *= 0.9
        if self.muted:
            out[:] = 0
        outdata[:] = out

    def load(self, path):
        samples = decode_file(path)
        with self.lock:
            self.samples = samples
            self.pos = 0
            self.track_path = path
            self.paused = False

    def toggle_pause(self):
        if self.samples is not None:
            self.paused = not self.paused

    def seek(self, delta_s):
        with self.lock:
            if self.samples is not None:
                self.pos = int(np.clip(
                    self.pos + delta_s * SAMPLE_RATE, 0, len(self.samples)))

    def duration(self):
        return len(self.samples) / SAMPLE_RATE if self.samples is not None else 0.0

    def position(self):
        return self.pos / SAMPLE_RATE if self.samples is not None else 0.0

    def spectrum(self):
        window = self.ring.copy()
        spec = np.abs(np.fft.rfft(window * np.hanning(len(window))))
        freqs = np.fft.rfftfreq(len(window), 1.0 / SAMPLE_RATE)
        edges = np.geomspace(50, 16000, NUM_BARS + 1)
        bins = []
        for i in range(NUM_BARS):
            lo, hi = edges[i], edges[i + 1]
            idx = (freqs >= lo) & (freqs < hi)
            v = spec[idx].max() if idx.any() else 0.0
            bins.append(v)
        mag = np.array(bins, dtype=np.float32)
        mag = np.log1p(mag * 8) / math.log(1 + 40)
        return np.clip(mag, 0, 1)


def neon_color(t, sat=0.95, val=1.0):
    r, g, b = colorsys.hsv_to_rgb(t % 1.0, sat, val)
    return int(r * 255), int(g * 255), int(b * 255)


def draw_visualizer(screen, player, w, h, mode, t, current_track_metadata):
    mag = player.spectrum()
    cx, cy = w // 2, h // 2
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    base_y = h - 90

    if mode == "radial":
        # NCS style "ball" visualizer
        cx, cy = w // 2, h // 2
        max_radius = min(w, h) * 0.22
        # Use bass energy (first ~10 bins) for the ball's pulsation
        bass = float(np.mean(mag[:10])) if len(mag) >= 10 else 0.0
        ball_radius = max_radius * (0.35 + bass * 0.65)  # 35%-100% of max_radius
        # Core color cycles slowly
        core_hue = (t * 0.05) % 1.0
        core_col = neon_color(core_hue)
        # Outer glow layers
        for glow in range(3):
            glow_radius = ball_radius + 8 * (glow + 1)
            glow_alpha = 60 - 20 * glow
            glow_col = (*core_col, max(0, glow_alpha))
            pygame.draw.circle(surf, glow_col, (cx, cy), int(glow_radius))
        # Solid core
        pygame.draw.circle(surf, (*core_col, 255), (cx, cy), int(ball_radius))
        # Rays: one per frequency bin, length scaled by magnitude
        num_rays = len(mag)
        for i, m in enumerate(mag):
            if m <= 0.01:  # skip near-zero bins
                continue
            ang = 2 * math.pi * i / num_rays - math.pi / 2  # start at top
            hue_shift = i / num_rays  # spread hue around circle
            ray_col = neon_color((core_hue + hue_shift * 0.3) % 1.0)
            # inner start just outside the ball
            start_r = ball_radius + 2
            end_r = ball_radius + m * (max_radius - ball_radius) * 1.2
            x0 = cx + math.cos(ang) * start_r
            y0 = cy + math.sin(ang) * start_r
            x1 = cx + math.cos(ang) * end_r
            y1 = cy + math.sin(ang) * end_r
            width = max(1, int(2 + m * 6))  # thicker for stronger bins
            pygame.draw.line(surf, ray_col, (int(x0), int(y0)),
                             (int(x1), int(y1)), width)
            # add a tiny outer "glow" bloom by drawing a slightly wider, softer line underneath
            if width > 2:
                bloom_col = (*ray_col, 80)
                pygame.draw.line(surf, bloom_col, (int(x0), int(y0)),
                                 (int(x1), int(y1)), width + 4)
    elif mode == "disc":
        # --- Rotating Disc Visualizer ---
        disc_radius = min(w, h) * 0.3  # Size of the central disc
        disc_color_hue = (t * 0.03) % 1.0
        disc_color = neon_color(disc_color_hue, sat=0.8, val=0.7)

        # Disc shadow/glow
        for glow_step in range(3, 0, -1):
            glow_color = (*disc_color, int(255 / (4 - glow_step) * 0.2))
            pygame.draw.circle(surf, glow_color, (cx, cy), int(disc_radius + glow_step * 8))

        # Main disc
        pygame.draw.circle(surf, disc_color, (cx, cy), int(disc_radius))

        # Album Art Sticker
        art_path = current_track_metadata.get('art_path')
        if art_path and os.path.exists(art_path):
            try:
                album_art = Image.open(art_path).convert("RGBA")
                # Resize art to fit disc sticker area (e.g., 60% of disc radius)
                sticker_radius = int(disc_radius * 0.6)
                art_size = (sticker_radius * 2, sticker_radius * 2)
                album_art = album_art.resize(art_size, Image.Resampling.LANCZOS)

                # Create a circular mask for the album art
                mask = Image.new('L', art_size, 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.ellipse((0, 0, art_size[0], art_size[1]), fill=255)
                album_art.putalpha(mask)

                # Convert PIL Image to Pygame Surface
                album_art_pg = pygame.image.fromstring(album_art.tobytes(), album_art.size, album_art.mode)

                # Position the sticker in the center of the disc
                art_rect = album_art_pg.get_rect(center=(cx, cy))
                surf.blit(album_art_pg, art_rect)

            except Exception as e:
                print(f"Error loading album art {art_path}: {e}")

        # Radial bars (same as original 'radial' but centered on disc edge)
        num_rays = len(mag)
        ray_start_radius = disc_radius + 10  # Start rays just outside the disc
        max_ray_length = min(w, h) * 0.15
        for i, m in enumerate(mag):
            if m <= 0.01: continue
            ang = 2 * math.pi * i / num_rays - math.pi / 2
            ray_col = neon_color(i / num_rays + t * 0.08) # Slightly different hue shift

            start_x = cx + math.cos(ang) * ray_start_radius
            start_y = cy + math.sin(ang) * ray_start_radius
            end_x = cx + math.cos(ang) * (ray_start_radius + m * max_ray_length)
            end_y = cy + math.sin(ang) * (ray_start_radius + m * max_ray_length)

            width = max(1, int(1 + m * 4))
            pygame.draw.line(surf, ray_col, (int(start_x), int(start_y)), (int(end_x), int(end_y)), width)
            # Add a soft bloom
            if width > 1:
                bloom_col = (*ray_col, 60)
                pygame.draw.line(surf, bloom_col, (int(start_x), int(start_y)), (int(end_x), int(end_y)), width + 2)
    else:
        avail_w = min(w * 0.68, 1200)
        smooth_w = max(3, int((avail_w / NUM_BARS) * 0.72))
        gap = max(1, int((avail_w / NUM_BARS) * 0.28))
        total = NUM_BARS * (smooth_w + gap)
        x = (w - total) // 2
        for i, m in enumerate(mag):
            col = neon_color(i / NUM_BARS + t * 0.08)
            bh = 4 + float(m) * (h * 0.45)
            rect = pygame.Rect(x, int(base_y - bh), smooth_w, int(bh))
            glow = pygame.Surface(rect.size, pygame.SRCALPHA)
            glow.fill((*col, 70))
            surf.blit(glow, rect.inflate(6, 6).topleft)
            pygame.draw.rect(surf, col, rect)
            if mode == "mirror":
                rect_m = pygame.Rect(x, base_y + gap, smooth_w, int(bh * 0.7))
                fade = pygame.Surface(rect_m.size, pygame.SRCALPHA)
                fade.fill((*col, 110))
                surf.blit(fade, rect_m.topleft)
            x += smooth_w + gap

    screen.blit(surf, (0, 0))


def draw_ui(screen, font, font_big, tracks, selected, player, w, h, muted,
            folder=None, hover_idx=None):
    base_y = h - 90
    pygame.draw.line(screen, (255, 255, 255, 40), (40, base_y), (w - 40, base_y))

    if player.track_path:
        name = os.path.splitext(os.path.basename(player.track_path))[0]
        state = "PAUSED" if player.paused else "NOW PLAYING"
        label = f"{state}  ▸ {name}"
        txt = font.render(label, True, (255, 255, 255))
        screen.blit(txt, (40, h - 60))
        dur, pos = player.duration(), player.position()
        frac = pos / dur if dur else 0
        pygame.draw.rect(screen, (80, 80, 80), (40, h - 30, w - 260, 4))
        pygame.draw.rect(screen, (0, 220, 180),
                         (40, h - 30, int((w - 260) * frac), 4))
        ttxt = font.render(f"{int(pos)//60}:{int(pos)%60:02d} / "
                           f"{int(dur)//60}:{int(dur)%60:02d}", True,
                           (170, 170, 170))
        screen.blit(ttxt, (w - 210, h - 42))

    # current source folder label (top-right)
    if folder:
        shown = folder
        while len(shown) > 52 and "/" in shown[1:]:
            shown = "…" + shown[shown.index("/", 1):]
        ft = font.render("SOURCE ▸ " + shown + "   [O to change]", True,
                         (0, 210, 175))
        screen.blit(ft, (w - ft.get_width() - 24, 20))

    if muted:
        mt = font.render("MUTED", True, (255, 90, 90))
        screen.blit(mt, (w - 100, 20))

    max_visible_rows = max(1, (h - 170) // 26)
    num_shown = min(len(tracks), max_visible_rows)
    panel_h = max(60, 60 + num_shown * 26)
    panel = pygame.Surface((340, panel_h), pygame.SRCALPHA)
    panel.fill((10, 10, 18, 150))
    pygame.draw.rect(panel, (255, 255, 255, 25), panel.get_rect(), 1)
    screen.blit(panel, (24, 24))

    head = font.render("LIBRARY", True, (0, 230, 190))
    screen.blit(head, (40, 34))
    visible_start = max(0, min(selected - max_visible_rows // 2, len(tracks) - max_visible_rows))
    for row_i, ti in enumerate(range(visible_start,
                                     min(visible_start + max_visible_rows, len(tracks)))):
        name = os.path.splitext(os.path.basename(tracks[ti]['path']))[0]
        name = name if len(name) <= 38 else name[:37] + "…"
        if ti == selected:
            pygame.draw.rect(screen, (0, 220, 180),
                             (34, 62 + row_i * 26, 320, 22), border_radius=4)
            col = (10, 14, 18)
        elif hover_idx is not None and ti == hover_idx:
            pygame.draw.rect(screen, (40, 50, 70),
                             (34, 62 + row_i * 26, 320, 22), border_radius=4)
            col = (255, 255, 255)
        else:
            col = (235, 235, 245)
        screen.blit(font.render(name, True, col), (44, 66 + row_i * 26))


def extract_metadata_and_art(file_path):
    """Extract metadata and album art from an audio file.
    Returns a dict with keys: title, artist, album, year, art_path.
    art_path is a path to a cached image file (or None).
    """
    metadata = {
        'title': os.path.splitext(os.path.basename(file_path))[0],
        'artist': '',
        'album': '',
        'year': '',
        'art_path': None
    }
    try:
        if file_path.lower().endswith('.mp3'):
            audio = MP3(file_path)
            if audio.tags:
                # Title
                if 'TIT2' in audio.tags:
                    metadata['title'] = str(audio.tags['TIT2'])
                # Artist
                if 'TPE1' in audio.tags:
                    metadata['artist'] = str(audio.tags['TPE1'])
                # Album
                if 'TALB' in audio.tags:
                    metadata['album'] = str(audio.tags['TALB'])
                # Year
                if 'TDRC' in audio.tags:
                    metadata['year'] = str(audio.tags['TDRC']).split('-')[0]
                # Album art
                for tag in audio.tags.values():
                    if tag.FrameID == 'APIC':
                        art_data = tag.data
                        # Hash the data to create a cache filename
                        art_hash = hashlib.md5(art_data).hexdigest()
                        art_path = os.path.join(ALBUM_ART_CACHE_DIR, f"{art_hash}.jpg")
                        if not os.path.exists(art_path):
                            image = Image.open(io.BytesIO(art_data))
                            image.save(art_path, "JPEG")
                        metadata['art_path'] = art_path
                        break
    except ID3NoHeaderError:
        pass
    except Exception as e:
        print(f"Error reading metadata from {file_path}: {e}")
    return metadata


def get_easter_art_for_track(track_path):
    """Return a PIL Image for easter egg if track filename matches keywords."""
    filename = os.path.basename(track_path).lower()
    for keyword in EASTER_KEYWORDS:
        if keyword in filename:
            # Look for an image with same base name (without extension) in EASTER_DIR
            base = os.path.splitext(os.path.basename(track_path))[0]
            # Try common extensions
            for ext in (".png", ".jpg", ".jpeg"):
                candidate = os.path.join(EASTER_DIR, base + ext)
                if os.path.exists(candidate):
                    try:
                        img = Image.open(candidate).convert("RGBA")
                        return img
                    except Exception:
                        pass
            # If no specific image, look for a default image for the keyword
            default_img = os.path.join(EASTER_DIR, keyword.replace(" ", "_") + ".png")
            if os.path.exists(default_img):
                try:
                    img = Image.open(default_img).convert("RGBA")
                    return img
                except Exception:
                    pass
            # If still none, return None to fall back to normal visualizer.
            return None
    return None


def scan_library(folders, tracks):
    """Scan folders for audio files and update tracks list with metadata dicts.
    Returns number of new tracks found.
    """
    found = []
    for folder in folders:
        if os.path.isdir(folder):
            for ext in AUDIO_EXTENSIONS:
                found.extend(glob.glob(os.path.join(folder, "**", ext),
                                       recursive=True))
    # New files are those not already in tracks (by path)
    existing_paths = {t['path'] for t in tracks if isinstance(t, dict) and 'path' in t}
    new_files = [f for f in found if f not in existing_paths]
    new_tracks = []
    for f in new_files:
        metadata = extract_metadata_and_art(f)
        metadata['path'] = f
        new_tracks.append(metadata)
    tracks.extend(new_tracks)
    # Sort by path for consistency
    tracks.sort(key=lambda x: x['path'])
    return len(new_tracks)


def pick_folder_dialog(current):
    """Native macOS folder picker via AppleScript; returns path or None."""
    import subprocess as sp
    script = (
        'set init to POSIX file "%s"\n'
        'set p to choose folder with prompt "Choose a music folder" '
        'default location init\n'
        'return POSIX path of p' % current
    )
    try:
        r = sp.run(["osascript", "-e", script], capture_output=True,
                   text=True, timeout=300)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().rstrip("/") or "/"
    except Exception:
        pass
    return None


def draw_torrent_overlay(screen, font, w, h, text, statuses, notice):
    ow, oh = 700, 240
    ox, oy = (w - ow) // 2, (h - oh) // 2 - 50
    panel = pygame.Surface((ow, oh), pygame.SRCALPHA)
    panel.fill((12, 14, 22, 238))
    pygame.draw.rect(panel, (0, 230, 190, 120), panel.get_rect(), 2)
    screen.blit(panel, (ox, oy))

    head = font.render("TORRENT — paste infohash or magnet link", True,
                       (0, 230, 190))
    screen.blit(head, (ox + 20, oy + 14))

    box = pygame.Rect(ox + 20, oy + 44, ow - 40, 40)
    pygame.draw.rect(screen, (26, 30, 44), box, border_radius=6)
    pygame.draw.rect(screen, (60, 70, 95), box, 1, border_radius=6)
    shown = text[-58:] if len(text) > 58 else text
    cursor = "|" if int(time.time() * 2) % 2 == 0 else ""
    screen.blit(font.render(shown + cursor, True, (235, 235, 245)),
                (box.x + 10, box.y + 12))

    y = oy + 100
    for s in statuses:
        col = (0, 230, 190) if s.startswith(("✔", "done")) else (170, 200, 230)
        screen.blit(font.render(s, True, col), (ox + 20, y))
        y += 22
    if notice:
        colr = {"info": (150, 210, 255), "warn": (255, 200, 90),
                "error": (255, 110, 110)}.get(notice[0], (200, 200, 200))
        screen.blit(font.render(notice[1][:70], True, colr), (ox + 20, oy + oh - 56))

    tip = font.render("Enter start · Esc close", True, (130, 135, 150))
    screen.blit(tip, (ox + 20, oy + oh - 30))


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/Music")
    folders = [folder]
    os.makedirs(TORRENT_DIR, exist_ok=True)
    folders.append(TORRENT_DIR)

    tracks = []  # list of dicts with keys: path, title, artist, album, year, art_path
    scan_library(folders, tracks)
    if not tracks:
        print(f"No audio files yet in '{folder}'. Add files or press T to "
              "download from an infohash.")
    else:
        print(f"Found {len(tracks)} track(s)")

    pygame.init()
    # Initialize joystick support
    pygame.joystick.init()
    joysticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
    for joystick in joysticks:
        joystick.init()
    w, h = 1280, 720
    screen = pygame.display.set_mode((w, h), pygame.RESIZABLE)
    pygame.display.set_caption("NCS Music Launcher")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)
    font_big = pygame.font.SysFont("consolas,menlo,dejavusansmono", 28, bold=True)

    player = Player()
    player.stream.start()

    torrents = TorrentManager() if lt is not None else None

    selected = 0
    vis_mode_idx = 0
    modes = ["bars", "mirror", "radial", "disc", "album"]
    muted = False
    start_time = time.time()
    if tracks:
        player.load(tracks[0]['path'])
        current_easter_art = get_easter_art_for_track(tracks[0]['path'])
    else:
        current_easter_art = None
    bg_pulse = np.zeros(NUM_BARS, dtype=np.float32)

    overlay_open = False
    overlay_text = ""
    last_scan = 0.0
    notice = None          # (kind, text)
    notice_until = 0.0
    hover_idx = None
    base_y = h - 90

    def render_hint():
        return font.render("↑↓ select  ⏎ play  space pause  ←→ seek  "
                           "F visual  T torrent  O folder  M mute  Q quit",
                           True, (120, 120, 130))

    hint_surf = render_hint()
    hint_w = hint_surf.get_width()

    def push_notice(entry, duration=3.0):
        nonlocal notice, notice_until
        notice = entry
        notice_until = time.time() + duration

    nonlocal_selected = [selected]  # for media_key sync

    # Media key handling (macOS)
    def media_key_handler(action):
        nonlocal selected, nonlocal_selected
        if not tracks:
            return
        if action == "play_pause":
            player.toggle_pause()
            if player.paused:
                push_notice(("info", "paused"))
            else:
                push_notice(("info", "playing"))
        elif action == "prev":
            if player.paused and tracks:
                selected = (selected - 1) % len(tracks)
                nonlocal_selected[0] = selected
                player.load(tracks[selected]['path'])
                push_notice(("info", f"previous: {tracks[selected]['title']}"))
        elif action == "next":
            if player.paused and tracks:
                selected = (selected + 1) % len(tracks)
                nonlocal_selected[0] = selected
                player.load(tracks[selected]['path'])
                push_notice(("info", f"next: {tracks[selected]['title']}"))
        elif action == "seek_back":
            if not player.paused and tracks:
                player.seek(-5)
                push_notice(("info", "seek -5s"))
        elif action == "seek_forward":
            if not player.paused and tracks:
                player.seek(5)
                push_notice(("info", "seek +5s"))

    media_tap = MediaKeyTap(
        on_play_pause=lambda: media_key_handler("play_pause"),
        on_next=lambda: media_key_handler("next"),
        on_previous=lambda: media_key_handler("previous")
    )
    media_tap.start()

    running = True
    while running:
        t = time.time() - start_time
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if overlay_open:
                    if event.key == pygame.K_ESCAPE:
                        overlay_open = False
                        overlay_text = ""
                    elif event.key == pygame.K_RETURN and overlay_text.strip():
                        ok = torrents.start_download(overlay_text.strip()) \
                            if torrents else False
                        if not torrents:
                            push_notice(("error",
                                         "libtorrent missing: pip install libtorrent"))
                        elif ok:
                            overlay_text = ""
                        # keep overlay open so progress is visible
                    elif event.key in (pygame.K_BACKSPACE, pygame.K_DELETE):
                        overlay_text = overlay_text[:-1]
                    elif event.unicode and event.unicode.isprintable():
                        overlay_text += event.unicode
                    continue
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key == pygame.K_t:
                    overlay_open = True
                    pygame.key.start_text_input()
                elif event.key == pygame.K_o:
                    picked = pick_folder_dialog(folder)
                    if picked and os.path.isdir(picked):
                        folder = picked
                        folders[0] = folder
                        tracks.clear()
                        scan_library(folders, tracks)
                        selected = 0
                        if tracks:
                            player.load(tracks[0]['path'])
                        push_notice(("info", f"library: {folder}"))
                    else:
                        push_notice(("info", "folder picker cancelled"))
                elif event.key == pygame.K_UP:
                    if tracks:
                        selected = (selected - 1) % len(tracks)
                        nonlocal_selected[0] = selected
                        player.load(tracks[selected]['path'])
                elif event.key == pygame.K_DOWN:
                    if tracks:
                        selected = (selected + 1) % len(tracks)
                        nonlocal_selected[0] = selected
                        player.load(tracks[selected]['path'])
                elif event.key == pygame.K_SPACE:
                    if player.track_path:
                        player.toggle_pause()
                elif event.key == pygame.K_RETURN:
                    if player.track_path and player.paused:
                        player.toggle_pause()
                elif event.key == pygame.K_LEFT:
                    if not player.paused and player.track_path:
                        player.seek(-5)
                elif event.key == pygame.K_RIGHT:
                    if not player.paused and player.track_path:
                        player.seek(5)
                elif event.key == pygame.K_m:
                    muted = not muted
                    player.muted = muted
                elif event.key == pygame.K_f:
                    vis_mode_idx = (vis_mode_idx + 1) % len(modes)
                elif event.key == pygame.K_EQUALS or event.key == pygame.K_KP_PLUS:
                    pass  # volume up not implemented
                elif event.key == pygame.K_MINUS or event.key == pygame.K_KP_MINUS:
                    pass  # volume down not implemented

            elif event.type == pygame.JOYBUTTONDOWN:
                if event.joyindex < len(joysticks):
                    joystick = joysticks[event.joyindex]
                    if event.button == 0:  # A
                        player.toggle_pause()
                    elif event.button == 1:  # B
                        player.toggle_pause()  # pause
                    elif event.button == 2:  # X  -> next
                        if tracks:
                            selected = (selected + 1) % len(tracks)
                            nonlocal_selected[0] = selected
                            player.load(tracks[selected]['path'])
                    elif event.button == 3:  # Y  -> prev
                        if tracks:
                            selected = (selected - 1) % len(tracks)
                            nonlocal_selected[0] = selected
                            player.load(tracks[selected]['path'])
            elif event.type == pygame.JOYAXISMOTION:
                if event.joyindex < len(joysticks):
                    joystick = joysticks[event.joyindex]
                    # Axis 3: right stick horizontal -> seek
                    if event.axis == 3:
                        val = event.value
                        if abs(val) > 0.2:  # deadzone
                            # Map -1..1 to -10..+10 seconds seek
                            delta = val * 10  # seconds
                            if not player.paused and player.track_path:
                                player.seek(delta)
                    # Axis 1: left stick vertical -> navigate playlist (optional)
                    elif event.axis == 1:
                        val = event.value
                        if abs(val) > 0.2:
                            # Map to up/down: negative = up, positive = down
                            steps = int(val * 3)  # sensitivity
                            if tracks:
                                selected = (selected - steps) % len(tracks)
                                nonlocal_selected[0] = selected
                                player.load(tracks[selected]['path'])
            elif event.type == pygame.JOYHATMOTION:
                if event.joyindex < len(joysticks):
                    hat_x, hat_y = event.value  # each -1,0,1
                    if hat_y == 1:  # D-pad up
                        if tracks:
                            selected = (selected - 1) % len(tracks)
                            nonlocal_selected[0] = selected
                            player.load(tracks[selected]['path'])
                    elif hat_y == -1:  # D-pad down
                        if tracks:
                            selected = (selected + 1) % len(tracks)
                            nonlocal_selected[0] = selected
                            player.load(tracks[selected]['path'])
                    # hat_x for horizontal scroll? skip.
            elif event.type == pygame.MOUSEMOTION:
                if overlay_open:
                    continue
                mx, my = event.pos
                max_visible_rows = max(1, (h - 170) // 26)
                # hover playlist rows
                if 24 <= mx <= 364 and 62 <= my <= 62 + max_visible_rows * 26:
                    row = (my - 62) // 26
                    visible_start = max(0, min(selected - max_visible_rows // 2,
                                               len(tracks) - max_visible_rows))
                    ti = visible_start + row
                    if 0 <= row < max_visible_rows and 0 <= ti < len(tracks):
                        hover_idx = ti
                        pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND)
                    else:
                        pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)
                else:
                    pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if overlay_open:
                    continue
                mx, my = event.pos
                max_visible_rows = max(1, (h - 170) // 26)
                # click playlist rows
                if 24 <= mx <= 364 and 62 <= my <= 62 + max_visible_rows * 26:
                    row = (my - 62) // 26
                    visible_start = max(0, min(selected - max_visible_rows // 2,
                                               len(tracks) - max_visible_rows))
                    ti = visible_start + row
                    if 0 <= row < max_visible_rows and 0 <= ti < len(tracks):
                        selected = ti
                        nonlocal_selected[0] = ti
                        player.load(tracks[ti]['path'])
                    continue
                # click seek bar
                bar_x, bar_y, bar_w = 40, h - 30, w - 260
                if player.track_path and abs(my - bar_y) < 8 \
                        and bar_x <= mx <= bar_x + bar_w:
                    frac = max(0.0, min(1.0, (mx - bar_x) / bar_w))
                    with player.lock:
                        player.pos = int(frac * len(player.samples))
                    continue
                # click source label → change folder
                if folder and 20 <= my <= 42 and mx > w - 460:
                    picked = pick_folder_dialog(folder)
                    if picked and os.path.isdir(picked):
                        folder = picked
                        folders[0] = folder
                        tracks.clear()
                        scan_library(folders, tracks)
                        selected = 0
                        if tracks:
                            player.load(tracks[0]['path'])
                        push_notice(("info", f"library: {folder}"))
                    continue
                # click transport buttons (bottom-left icons drawn as text zones)
                btn_zone = pygame.Rect(w - hint_w - 24, h - 30, hint_w + 10, 26)
                # mute toggle zone (top-right under source label)
                if w - 110 <= mx <= w - 40 and 44 <= my <= 64:
                    muted = not muted
                    player.muted = muted
                    continue
                # visualizer mode cycle on click anywhere else in viz area
                if my < base_y - (h * 45 // 100):
                    vis_mode_idx = (vis_mode_idx + 1) % len(modes)
            elif event.type == pygame.MOUSEWHEEL:
                if tracks and not overlay_open:
                    selected = (selected - int(event.y)) % len(tracks)
                    nonlocal_selected[0] = selected
                    player.load(tracks[selected]['path'])

        # Update selected from media key thread (if changed)
        if nonlocal_selected[0] != selected and tracks:
            selected = nonlocal_selected[0]
            player.load(tracks[selected]['path'])

        # Clear old notices
        if notice and time.time() > notice_until:
            notice = None

        # Draw background gradient
        bg_val = int(20 + 10 * math.sin(t * 0.2))
        screen.fill((bg_val, bg_val // 2 + 6, bg_val + 14))

        # Draw visualizer
        current_metadata = tracks[selected] if tracks else {}
        draw_visualizer(screen, player, w, h, modes[vis_mode_idx], t, current_metadata)

        # Draw UI
        draw_ui(screen, font, font_big, tracks, selected, player, w, h, muted,
                folder, hover_idx)

        # Draw torrent overlay
        if torrents:
            statuses = torrents.status_lines()
            if notice:
                draw_torrent_overlay(screen, font, w, h, overlay_text, statuses, notice)
            elif overlay_open:
                draw_torrent_overlay(screen, font, w, h, overlay_text, statuses, None)

        # Draw hint
        hint_surf = render_hint()
        screen.blit(hint_surf, (w - hint_w - 24, h - 24))

        pygame.display.flip()
        clock.tick(60)

    # Cleanup
    if torrents and torrents.session:
        torrents.session.pause()
    pygame.quit()


if __name__ == "__main__":
    main()