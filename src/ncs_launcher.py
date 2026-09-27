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
    F             cycle visualizer mode
                  (bars / mirror / radial / disc / album)
    C             open the interactive Hermes chat panel
                  (type a question, Enter sends, Esc closes)
    T             open torrent overlay — paste an infohash or magnet link,
                  it downloads natively via libtorrent and lands in your library
    O             change source folder
    Esc / Q       quit
    The window is resizable (minimum 640x480).

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
import shutil
import queue
import colorsys
from PIL import Image, ImageDraw
from mutagen.mp3 import MP3
from mutagen.id3 import ID3NoHeaderError
import io
import hashlib

import numpy as np

from media_keys import MediaKeyTap, open_accessibility_settings
from ncs_sphere import draw_ncs_sphere
import ncs_video
import config as appconfig
import actions as appactions
import migrations as appmigrations

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


# --------------------------------------------------------------------------
# Real NCS sphere: a 3D point cloud on a Fibonacci sphere, displaced by a
# flowing wave field, splatted additively with numpy (fast enough for 60fps)
# --------------------------------------------------------------------------
# The NCS sphere lives in its own module (src/ncs_sphere.py) so it can be
# iterated on and benchmarked headlessly without importing the whole app.
# Imported at the top of the file with the other modules.


def draw_visualizer(screen, player, w, h, mode, t, current_track_metadata,
                    video_slot=None):
    cx, cy = w // 2, h // 2
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    base_y = h - 90

    if mode == "video":
        # The video paints the whole window. It is drawn FIRST and returns,
        # so the UI, chat and overlays still land on top of it.
        if video_slot is not None and video_slot.draw(screen, pygame.Rect(0, 0, w, h),
                                                      player):
            return
        # no live frame (armed with no sidecar, failed, or ended): say so once
        # rather than showing a black rectangle, and let F move on.
        msg = getattr(video_slot, "message", "") or "no video for this track"
        hint = pygame.font.Font(None, 22).render(
            f"video: {msg}", True, (150, 155, 170))
        screen.blit(hint, (cx - hint.get_width() // 2,
                           cy - 90 - hint.get_height()))
        return

    mag = player.spectrum()

    if mode == "radial":
        # The real NCS ball: 3D point-cloud sphere with a flowing gold membrane
        draw_ncs_sphere(screen, player, w, h, t)
    elif mode == "disc":
        # --- Rotating Vinyl Disc Visualizer ---
        disc_radius = int(min(w, h) * 0.28)
        bass = float(np.mean(mag[:10])) if len(mag) >= 10 else 0.0
        pulse_radius = disc_radius + int(bass * 14)

        # Subtle outer neon glow
        core_col = neon_color((t * 0.05) % 1.0)
        pygame.draw.circle(surf, (*core_col, 35), (cx, cy), pulse_radius + 12)
        pygame.draw.circle(surf, (*core_col, 60), (cx, cy), pulse_radius + 6)

        # Black vinyl body
        pygame.draw.circle(surf, (16, 16, 18), (cx, cy), pulse_radius)
        pygame.draw.circle(surf, (35, 35, 40), (cx, cy), pulse_radius, 2)

        # Vinyl grooves
        for gr in range(int(pulse_radius * 0.42), pulse_radius - 6, 7):
            shade = 24 if (gr // 7) % 2 == 0 else 32
            pygame.draw.circle(surf, (shade, shade, shade + 2), (cx, cy), gr, 1)

        # Vinyl shine / highlight sheen
        shine_angle = (t * 0.4) % (2 * math.pi)
        for sa in (shine_angle, shine_angle + math.pi):
            p1 = (cx + math.cos(sa - 0.2) * pulse_radius * 0.95, cy + math.sin(sa - 0.2) * pulse_radius * 0.95)
            p2 = (cx + math.cos(sa + 0.2) * pulse_radius * 0.95, cy + math.sin(sa + 0.2) * pulse_radius * 0.95)
            pygame.draw.polygon(surf, (255, 255, 255, 18), [(cx, cy), p1, p2])

        # Center Album Art Sticker (Circular, rotating)
        sticker_r = int(disc_radius * 0.36)
        art_path = current_track_metadata.get('art_path')
        if art_path and os.path.exists(art_path):
            try:
                art_img = Image.open(art_path).convert("RGBA")
                sz = sticker_r * 2
                art_img = art_img.resize((sz, sz), Image.Resampling.LANCZOS)
                # Rotate image over time
                rot_deg = -(t * 45) % 360
                art_img = art_img.rotate(rot_deg, resample=Image.Resampling.BICUBIC)
                
                # Circular mask
                mask = Image.new('L', (sz, sz), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, sz, sz), fill=255)
                art_img.putalpha(mask)
                
                art_surf = pygame.image.frombytes(art_img.tobytes(), art_img.size, "RGBA")
                surf.blit(art_surf, art_surf.get_rect(center=(cx, cy)))
            except Exception:
                pygame.draw.circle(surf, (50, 50, 60), (cx, cy), sticker_r)
        else:
            pygame.draw.circle(surf, (45, 45, 55), (cx, cy), sticker_r)
            pygame.draw.circle(surf, core_col, (cx, cy), sticker_r, 2)

        # Center spindle hole
        pygame.draw.circle(surf, (10, 10, 14), (cx, cy), max(3, int(sticker_r * 0.16)))
        pygame.draw.circle(surf, (80, 80, 90), (cx, cy), max(3, int(sticker_r * 0.16)), 1)

        # Reactive radial sound rays around vinyl edge
        num_rays = len(mag)
        ray_start = pulse_radius + 4
        max_ray = min(w, h) * 0.14
        for i, m in enumerate(mag):
            if m <= 0.01:
                continue
            ang = 2 * math.pi * i / num_rays - math.pi / 2
            ray_col = neon_color(i / num_rays + t * 0.08)
            sx = cx + math.cos(ang) * ray_start
            sy = cy + math.sin(ang) * ray_start
            ex = cx + math.cos(ang) * (ray_start + m * max_ray)
            ey = cy + math.sin(ang) * (ray_start + m * max_ray)
            rw = max(1, int(1 + m * 4))
            pygame.draw.line(surf, ray_col, (int(sx), int(sy)), (int(ex), int(ey)), rw)
            if rw > 1:
                pygame.draw.line(surf, (*ray_col, 60), (int(sx), int(sy)), (int(ex), int(ey)), rw + 2)

    elif mode == "album":
        # --- Normal boring square album cover mode ---
        box_size = int(min(w, h) * 0.48)
        bass = float(np.mean(mag[:10])) if len(mag) >= 10 else 0.0
        pulse_box = box_size + int(bass * 12)
        art_path = current_track_metadata.get('art_path')
        
        # Outer glow & shadow
        glow_col = neon_color((t * 0.05) % 1.0)
        glow_rect = pygame.Rect(0, 0, pulse_box + 16, pulse_box + 16)
        glow_rect.center = (cx, cy)
        pygame.draw.rect(surf, (*glow_col, 50), glow_rect, border_radius=10)
        
        if art_path and os.path.exists(art_path):
            try:
                art_img = Image.open(art_path).convert("RGBA")
                art_img = art_img.resize((pulse_box, pulse_box), Image.Resampling.LANCZOS)
                art_surf = pygame.image.frombytes(art_img.tobytes(), art_img.size, "RGBA")
                surf.blit(art_surf, art_surf.get_rect(center=(cx, cy)))
                # Sleek border
                border_rect = pygame.Rect(0, 0, pulse_box, pulse_box)
                border_rect.center = (cx, cy)
                pygame.draw.rect(surf, (255, 255, 255, 80), border_rect, 2, border_radius=4)
            except Exception:
                box_rect = pygame.Rect(0, 0, pulse_box, pulse_box)
                box_rect.center = (cx, cy)
                pygame.draw.rect(surf, (20, 22, 30), box_rect, border_radius=6)
                pygame.draw.rect(surf, glow_col, box_rect, 2, border_radius=6)
        else:
            box_rect = pygame.Rect(0, 0, pulse_box, pulse_box)
            box_rect.center = (cx, cy)
            pygame.draw.rect(surf, (20, 22, 30), box_rect, border_radius=6)
            pygame.draw.rect(surf, glow_col, box_rect, 2, border_radius=6)
            # Default music note / placeholder text inside box
            ph_font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 18, bold=True)
            txt = ph_font.render("NO ALBUM ART", True, (120, 130, 150))
            surf.blit(txt, txt.get_rect(center=(cx, cy)))

        # Bottom audio visualizer mini-bar right beneath the square album
        avail_w = int(pulse_box * 0.95)
        num_m_bars = 24
        bar_w = max(2, avail_w // num_m_bars - 2)
        start_bx = cx - (num_m_bars * (bar_w + 2)) // 2
        by = cy + pulse_box // 2 + 18
        for i in range(num_m_bars):
            m = mag[i % len(mag)]
            bh = max(3, int(m * 30))
            b_col = neon_color(i / num_m_bars + t * 0.08)
            pygame.draw.rect(surf, b_col, (start_bx + i * (bar_w + 2), by, bar_w, bh), border_radius=2)

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

        # "radial" (the NCS sphere) already painted itself onto screen
        screen.blit(surf, (0, 0))


def draw_ui(screen, font, font_big, tracks, selected, player, w, h, muted,
            folder=None, hover_idx=None):
    base_y = h - 90
    pygame.draw.line(screen, (255, 255, 255, 40), (40, base_y), (w - 40, base_y))

    if player.track_path:
        name = os.path.splitext(os.path.basename(player.track_path))[0]
        state = "PAUSED" if player.paused else "NOW PLAYING"
        label = f"{state}  ▸ {name}"
        # stop the label before it collides with the time readout
        max_label_w = max(80, w - 300)
        while font.size(label)[0] > max_label_w and len(label) > 8:
            label = label[:-1]
        if label != f"{state}  ▸ {name}":
            label = label[:-1] + "…"
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
    panel_w = 340
    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel.fill((10, 10, 18, 150))
    pygame.draw.rect(panel, (255, 255, 255, 25), panel.get_rect(), 1)
    screen.blit(panel, (24, 24))

    head = font.render("LIBRARY", True, (0, 230, 190))
    screen.blit(head, (40, 34))
    visible_start = max(0, min(selected - max_visible_rows // 2, len(tracks) - max_visible_rows))
    for row_i, ti in enumerate(range(visible_start,
                                     min(visible_start + max_visible_rows, len(tracks)))):
        name = os.path.splitext(os.path.basename(tracks[ti]['path']))[0]
        # Clip row text so it never overflows the menu panel
        row_rect = pygame.Rect(34, 62 + row_i * 26, 320, 22)
        if ti == selected:
            pygame.draw.rect(screen, (0, 220, 180), row_rect, border_radius=4)
            col = (10, 14, 18)
        elif hover_idx is not None and ti == hover_idx:
            pygame.draw.rect(screen, (40, 50, 70), row_rect, border_radius=4)
            col = (255, 255, 255)
        else:
            col = (235, 235, 245)

        max_text_w = row_rect.width - 20
        txt_surf = font.render(name, True, col)
        if txt_surf.get_width() > max_text_w:
            while len(name) > 3 and font.size(name + "…")[0] > max_text_w:
                name = name[:-1]
            txt_surf = font.render(name + "…", True, col)
        screen.blit(txt_surf, (44, 66 + row_i * 26))


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


# --------------------------------------------------------------------------
# Interactive Hermes chat panel
# --------------------------------------------------------------------------
HERMES_BIN = os.path.expanduser("~/.local/bin/hermes")
if not os.path.exists(HERMES_BIN):
    HERMES_BIN = shutil.which("hermes") or "hermes"


def wrap_text(font, text, max_w):
    """Greedy word wrap; falls back to hard char wrapping for long tokens."""
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], ""
    for word in words:
        if font.size(word)[0] > max_w:
            if cur:
                lines.append(cur)
                cur = ""
            chunk = ""
            for ch in word:
                if font.size(chunk + ch)[0] > max_w and chunk:
                    lines.append(chunk)
                    chunk = ch
                else:
                    chunk += ch
            cur = chunk
            continue
        trial = word if not cur else cur + " " + word
        if font.size(trial)[0] <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


class HermesChat:
    """A text-input panel that runs `hermes -z` on a worker thread.

    Rendering and input happen on the main pygame thread; the subprocess runs
    in the background and posts its reply back through a queue.
    """

    def __init__(self, font, rect):
        self.font = font
        self.rect = rect
        self.messages = []          # list of (role, text)
        self.input_buf = ""
        self.caret_on = True
        self.scroll = 0             # index of first visible wrapped line
        self.busy = False
        self._queue = queue.Queue()
        self._thread = None
        self.add("hermes", "Hi — I'm Hermes. Ask me about your library, "
                          "the visualizers, or controls. Enter sends, "
                          "Esc closes.")

    def add(self, role, text):
        self.messages.append((role, text))

    def submit(self, context):
        prompt = self.input_buf.strip()
        if not prompt or self.busy:
            return
        self.add("you", prompt)
        self.input_buf = ""
        self.scroll = 10 ** 9
        self.busy = True
        self._thread = threading.Thread(
            target=self._worker, args=(prompt, context), daemon=True)
        self._thread.start()

    def _worker(self, prompt, context):
        full = context + "\n\n" + prompt if context else prompt
        try:
            r = subprocess.run([HERMES_BIN, "-z", full],
                               capture_output=True, text=True, timeout=300)
            out = (r.stdout or "").strip()
            if not out:
                err = (r.stderr or "").strip()
                out = err if err else "(no response from hermes)"
        except subprocess.TimeoutExpired:
            out = "(hermes timed out after 300s)"
        except FileNotFoundError:
            out = (f"(hermes not found at {HERMES_BIN} — "
                   "install it or add it to PATH)")
        except Exception as e:
            out = f"(hermes error: {e})"
        self._queue.put(out)

    def poll(self):
        """Drain finished replies; returns True if one landed this frame."""
        got = False
        while True:
            try:
                reply = self._queue.get_nowait()
            except queue.Empty:
                break
            self.add("hermes", reply)
            self.scroll = 10 ** 9
            self.busy = False
            got = True
        return got

    def handle_key(self, event):
        if event.key == pygame.K_ESCAPE:
            return "close"
        if event.key == pygame.K_RETURN or event.key == pygame.K_KP_ENTER:
            self.submit(self.context)
            return None
        if event.key == pygame.K_BACKSPACE:
            self.input_buf = self.input_buf[:-1]
            return None
        if event.key == pygame.K_UP:
            self.scroll = max(0, self.scroll - 1)
            return None
        if event.key == pygame.K_DOWN:
            self.scroll += 1
            return None
        if event.key == pygame.K_PAGEUP:
            self.scroll = max(0, self.scroll - 6)
            return None
        if event.key == pygame.K_PAGEDOWN:
            self.scroll += 6
            return None
        if event.unicode and event.unicode.isprintable():
            self.input_buf += event.unicode
        return None

    def draw(self, screen, t, w, h):
        self.context = getattr(self, "context", "")
        r = self.rect
        pad = 18
        max_w = r.width - pad * 2
        inner_w = max(60, max_w)

        panel = pygame.Surface((r.width, r.height), pygame.SRCALPHA)
        panel.fill((8, 10, 16, 242))
        pygame.draw.rect(panel, (0, 230, 190, 150), panel.get_rect(), 2)
        screen.blit(panel, r.topleft)

        # header
        head = self.font.render("HERMES  ·  interactive", True, (0, 230, 190))
        screen.blit(head, (r.x + pad, r.y + 12))
        state = "thinking..." if self.busy else "ready"
        scol = (255, 200, 90) if self.busy else (140, 145, 160)
        stxt = self.font.render(state, True, scol)
        screen.blit(stxt, (r.x + r.width - pad - stxt.get_width(), r.y + 12))
        pygame.draw.line(screen, (255, 255, 255, 30),
                         (r.x + pad, r.y + 36), (r.x + r.width - pad, r.y + 36))

        # wrap all messages into a flat list of (text, colour, is_user)
        wrapped = []
        for role, text in self.messages:
            colour = (235, 235, 245) if role == "you" else (170, 235, 220)
            prefix = "› " if role == "you" else ""
            for i, line in enumerate(wrap_text(self.font, text, inner_w)):
                wrapped.append(((prefix if i == 0 else "  ") + line, colour))

        # input box height reserved at the bottom
        box_h = 40
        view_top = r.y + 44
        view_bottom = r.y + r.height - box_h - 22
        line_h = self.font.get_linesize()
        max_rows = max(1, (view_bottom - view_top) // line_h)

        self.scroll = max(0, min(self.scroll, max(0, len(wrapped) - 1)))
        start = min(self.scroll, max(0, len(wrapped) - max_rows))
        for i, (line, colour) in enumerate(wrapped[start:start + max_rows]):
            screen.blit(self.font.render(line, True, colour),
                        (r.x + pad, view_top + i * line_h))

        if len(wrapped) > max_rows:
            more = self.font.render(
                f"  ({len(wrapped) - start - max_rows} more lines, "
                "↑↓ to scroll)", True, (110, 115, 130))
            screen.blit(more, (r.x + pad, view_bottom + 2))

        # input box
        box = pygame.Rect(r.x + pad, r.y + r.height - box_h - 8,
                          max_w, box_h - 8)
        pygame.draw.rect(screen, (24, 28, 40), box, border_radius=6)
        pygame.draw.rect(screen, (0, 230, 190, 110), box, 1, border_radius=6)
        # keep the caret visible by scrolling the input left when it overflows
        shown = self.input_buf
        while self.font.size(shown)[0] > box.width - 40 and shown:
            shown = shown[1:]
        caret = "▌" if (self.caret_on and int(t * 2) % 2 == 0) else " "
        screen.blit(self.font.render(shown + caret, True, (255, 255, 255)),
                    (box.x + 10, box.y + 8))


def draw_video_overlay(screen, font, w, h, text):
    """Prompt for a video path, a .strm file, or a web URL."""
    panel = pygame.Surface((w, h), pygame.SRCALPHA)
    panel.fill((6, 8, 12, 236))
    screen.blit(panel, (0, 0))

    title = font.render("VIDEO SOURCE", True, (0, 230, 190))
    screen.blit(title, (w // 2 - title.get_width() // 2, h // 2 - 96))

    lines = [
        "a video file (.mp4 .mkv .webm .avi .mov)",
        "a .strm file containing a path or URL",
        "a web link - the stream is extracted, never the ad page",
        "",
    ]
    for i, line in enumerate(lines):
        surf = font.render(line, True, (150, 155, 170))
        screen.blit(surf, (w // 2 - surf.get_width() // 2, h // 2 - 58 + i * 26))

    shown = text or ""
    if len(shown) > 60:
        shown = "..." + shown[-57:]
    box = font.render(f"> {shown}_", True, (235, 235, 245))
    screen.blit(box, (w // 2 - box.get_width() // 2, h // 2 + 62))

    hint = font.render("Enter play  ·  Esc cancel  ·  backspace deletes",
                       True, (110, 115, 130))
    screen.blit(hint, (w // 2 - hint.get_width() // 2, h // 2 + 104))

    if not ncs_video.have_ffmpeg():
        warn = font.render("ffmpeg not found on PATH - video cannot play",
                           True, (255, 120, 90))
        screen.blit(warn, (w // 2 - warn.get_width() // 2, h // 2 + 136))
    elif not ncs_video.have_ytdlp():
        warn = font.render("yt-dlp not found - web links will not resolve",
                           True, (255, 190, 90))
        screen.blit(warn, (w // 2 - warn.get_width() // 2, h // 2 + 136))


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
    MIN_W, MIN_H = 640, 480
    screen = pygame.display.set_mode((w, h), pygame.RESIZABLE)
    pygame.display.set_caption("NCS Music Launcher")
    flags = pygame.RESIZABLE
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)
    font_big = pygame.font.SysFont("consolas,menlo,dejavusansmono", 28, bold=True)

    player = Player()
    player.stream.start()

    torrents = TorrentManager() if lt is not None else None

    selected = 0
    vis_mode_idx = 0
    modes = ["bars", "mirror", "radial", "disc", "album", "video"]
    # The video layer. ffmpeg must exist or the key is refused with a reason
    # rather than silently doing nothing.
    video_slot = ncs_video.VideoSlot()
    video_overlay_open = False
    video_overlay_text = ""
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

    chat_open = False
    chat_panel = HermesChat(font, pygame.Rect(0, 0, w, h))

    # ---- settings, keymap, migrations ---------------------------------
    # Load -> migrate -> merge, in that order: migrations must see the real
    # stored shape before defaults fill in the gaps.
    cfg = appconfig.load_config()
    try:
        cfg = appmigrations.migrate(cfg)
    except Exception as exc:            # a bad file must not brick the app
        push_notice(("error", f"settings ignored: {exc}"), 6.0)
        cfg = appconfig.default_config()
    keymap = appactions.Keymap(cfg.get("keymap") or {})
    show_hints = bool(cfg.get("show_hints", True))
    settings_open = False
    settings_panel = None               # built lazily, needs the font

    def save_settings():
        """Persist the keymap. Never let a write failure kill the session."""
        cfg["keymap"] = dict(keymap.bindings)
        try:
            appconfig.save_config(cfg)
        except Exception as exc:
            push_notice(("error", f"could not save settings: {exc}"), 5.0)

    def render_hint():
        return font.render("↑↓ select  ⏎ play  space pause  ←→ seek  "
                           "F visual  V video  C hermes  T torrent  "
                           "O folder  M mute  Q quit",
                           True, (120, 120, 130))

    hint_surf = render_hint()
    hint_w = hint_surf.get_width()

    def push_notice(entry, duration=3.0):
        nonlocal notice, notice_until
        notice = entry
        notice_until = time.time() + duration

    nonlocal_selected = [selected]  # for media_key sync

    # ---- action dispatch ------------------------------------------------
    # Every user-rebindable key funnels through here. Keeping one table means
    # the keymap, the settings panel and the media keys all agree on what an
    # action does, and adding a binding never means editing the event chain.
    def do_action(act, shifted=False):
        nonlocal selected, muted, vis_mode_idx, chat_open, settings_open
        nonlocal video_overlay_open, video_overlay_text, overlay_open, folder
        nonlocal running
        if act == "select_prev":
            if tracks:
                selected = (selected - 1) % len(tracks)
                nonlocal_selected[0] = selected
                player.load(tracks[selected]['path'])
        elif act == "select_next":
            if tracks:
                selected = (selected + 1) % len(tracks)
                nonlocal_selected[0] = selected
                player.load(tracks[selected]['path'])
        elif act == "play_pause":
            if player.track_path:
                player.toggle_pause()
        elif act == "play":
            if player.track_path and player.paused:
                player.toggle_pause()
        elif act == "seek_back":
            if not player.paused and player.track_path:
                player.seek(-5)
        elif act == "seek_forward":
            if not player.paused and player.track_path:
                player.seek(5)
        elif act == "toggle_mute":
            muted = not muted
            player.muted = muted
        elif act == "cycle_visualizer":
            vis_mode_idx = (vis_mode_idx + 1) % len(modes)
        elif act == "open_hermes":
            chat_open = not chat_open
            if chat_open:
                pygame.key.start_text_input()
        elif act == "open_video":
            # V toggles the layer off; Shift+V (or V with nothing playing)
            # asks for an explicit path / .strm / URL.
            if video_slot.is_active() and not shifted:
                video_slot.clear(by_user=True)
                push_notice(("info", "video off"))
            elif ncs_video.have_ffmpeg():
                video_overlay_open = True
                video_overlay_text = ""
                pygame.key.start_text_input()
            else:
                push_notice(("error", "ffmpeg is required for video "
                                      "(brew install ffmpeg)"), 6.0)
        elif act == "open_torrent":
            if torrents is not None:
                overlay_open = True
                pygame.key.start_text_input()
            else:
                push_notice(("error", "libtorrent is not available"), 5.0)
        elif act == "open_folder":
            picked = pick_folder_dialog(folder)
            if picked and os.path.isdir(picked):
                folder = picked
                folders[0] = folder
                cfg["library_folder"] = folder
                tracks.clear()
                scan_library(folders, tracks)
                selected = 0
                if tracks:
                    player.load(tracks[0]['path'])
                push_notice(("info", f"library: {folder}"))
                save_settings()
            else:
                push_notice(("info", "folder picker cancelled"))
        elif act == "open_settings":
            settings_open = not settings_open
            if settings_open:
                if settings_panel is None:
                    import settings_panel as sp
                    settings_panel = sp.SettingsPanel(cfg, keymap, font)
                settings_panel.open()
        elif act == "quit":
            running = False
        else:
            push_notice(("info", f"unbound action: {act}"), 2.0)

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
            elif event.type == pygame.VIDEORESIZE:
                # real resize support: clamp to a usable minimum, then re-derive
                # every layout value that used to be baked in at startup
                nw = max(MIN_W, event.w)
                nh = max(MIN_H, event.h)
                screen = pygame.display.set_mode((nw, nh), pygame.RESIZABLE)
                w, h = screen.get_size()
                base_y = h - 90
                hint_surf = render_hint()
                hint_w = hint_surf.get_width()
                if chat_open:
                    chat_panel.rect = pygame.Rect(0, 0, w, h)
                push_notice(("info", f"resized to {w}x{h}"))
            elif event.type == pygame.KEYDOWN:
                if settings_open and settings_panel is not None:
                    # The panel owns every key while it is open, so a rebind
                    # capture cannot be stolen by a normal binding.
                    verdict = settings_panel.handle_key(event)
                    if verdict == "close":
                        settings_open = False
                        save_settings()
                    elif verdict in ("rebound", "toggled", "changed"):
                        save_settings()
                    continue
                if chat_open:
                    # the chat panel owns every key while it is open
                    if chat_panel.handle_key(event) == "close":
                        chat_open = False
                    continue
                if video_overlay_open:
                    # paste a video path, a .strm file, or a web URL
                    if event.key == pygame.K_ESCAPE:
                        video_overlay_open = False
                        video_overlay_text = ""
                        pygame.key.stop_text_input()
                    elif event.key == pygame.K_RETURN and video_overlay_text.strip():
                        want = video_overlay_text.strip()
                        video_overlay_open = False
                        video_overlay_text = ""
                        pygame.key.stop_text_input()
                        # an explicit choice is pinned: track changes will not
                        # override it with a sidecar
                        video_slot.pin(want)
                        if modes[vis_mode_idx] != "video":
                            vis_mode_idx = modes.index("video")
                        push_notice(("info", f"video: {os.path.basename(want)}"))
                    elif event.key in (pygame.K_BACKSPACE, pygame.K_DELETE):
                        video_overlay_text = video_overlay_text[:-1]
                    elif event.unicode and event.unicode.isprintable():
                        video_overlay_text += event.unicode
                    continue
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
                elif event.key == pygame.K_v:
                    # V toggles the video layer; Shift+V (or V while it is
                    # already on) asks for an explicit path / .strm / URL.
                    if video_slot.enabled and not video_slot._pinned:
                        if video_slot.is_active() and not (
                                event.mod & pygame.KMOD_SHIFT):
                            video_slot.clear()
                            push_notice(("info", "video off"))
                        else:
                            video_overlay_open = True
                            video_overlay_text = ""
                            pygame.key.start_text_input()
                    else:
                        video_overlay_open = True
                        video_overlay_text = ""
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
                else:
                    # Anything not claimed by an overlay goes through the
                    # user keymap, so a rebind in settings takes effect here
                    # without touching this chain. Structural keys (ESC,
                    # RETURN in an overlay) are handled before this point.
                    act = keymap.resolve(event.key)
                    if act:
                        do_action(act, bool(event.mod & pygame.KMOD_SHIFT))

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

        # Draw visualizer. The video slot follows the selected track so a
        # sidecar appears on its own; a pinned source ignores track changes.
        current_metadata = tracks[selected] if tracks else {}
        if modes[vis_mode_idx] == "video" and tracks:
            video_slot.follow_track(tracks[selected]['path'])
        draw_visualizer(screen, player, w, h, modes[vis_mode_idx], t,
                        current_metadata, video_slot=video_slot)

        # Draw UI
        draw_ui(screen, font, font_big, tracks, selected, player, w, h, muted,
                folder, hover_idx)

        # Draw the interactive Hermes chat on top when it is open
        if chat_open:
            cur = tracks[selected] if tracks else {}
            cur_title = cur.get('title') or "nothing"
            cur_artist = cur.get('artist') or "unknown artist"
            chat_panel.context = (
                "You are embedded in the NCS Music Launcher, a pygame music "
                "player. Current track: "
                f"'{cur_title}' by {cur_artist}. "
                f"State: {'paused' if player.paused else 'playing'}, "
                f"visualizer mode: {modes[vis_mode_idx]}, "
                f"{len(tracks)} track(s) in the library at {folder}."
            )
            chat_panel.poll()
            chat_panel.draw(screen, t, w, h)

        # Draw torrent overlay
        if torrents:
            statuses = torrents.status_lines()
            if notice:
                draw_torrent_overlay(screen, font, w, h, overlay_text, statuses, notice)
            elif overlay_open:
                draw_torrent_overlay(screen, font, w, h, overlay_text, statuses, None)

        # Draw the video-source overlay above everything else
        if video_overlay_open:
            draw_video_overlay(screen, font, w, h, video_overlay_text)

        # Settings sits on top of the video prompt: it is modal.
        if settings_open and settings_panel is not None:
            settings_panel.draw(screen, w, h)

        # Draw hint
        if show_hints:
            hint_surf = render_hint()
            hx, hy = w - hint_w - 24, h - 24
            if hx < 24:                   # too narrow for the full hint
                hx = 24
                short = font.render("↑↓ ⏎ space ←→ F V C T O M Q", True,
                                    (120, 120, 130))
                screen.blit(short, (24, hy))
            else:
                screen.blit(hint_surf, (hx, hy))

        pygame.display.flip()
        clock.tick(60)

    # Cleanup. Reap the ffmpeg pipe before pygame goes away, or a detached
    # ffmpeg survives the app and keeps decoding into a closed pipe.
    video_slot.clear(by_user=False)
    if torrents and torrents.session:
        torrents.session.pause()
    pygame.quit()


if __name__ == "__main__":
    main()