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

# How long the first Esc stays "armed" for the second one. Long enough to
# press twice on purpose, short enough that a stray Esc ten seconds later
# does not quit the app.
QUIT_CONFIRM_SECS = 2.5

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
                    txt = f"ok done: {st.name}"
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
        # Output gain, 0.0..1.0. Applied in the callback so the control API
        # has a real volume to set -- previously the stream wrote raw samples
        # at full scale and only `muted` existed, so /volume would have been a
        # lie. Read once per block, so it costs nothing in the hot path.
        self.volume = 1.0
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
        elif self.volume != 1.0:
            out *= self.volume
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
        # Truncate from the LEFT so the tail -- the part that actually
        # identifies the folder -- stays readable. This used to be
        #
        #     while len(shown) > 52 and "/" in shown[1:]:
        #         shown = "…" + shown[shown.index("/", 1):]
        #
        # which strips one leading character and prepends an ellipsis, so the
        # string never gets shorter: `len(shown)` stays above 52 and any path
        # with a "/" left in it spins forever. It froze the app on its very
        # first frame for every library path longer than 52 characters, and
        # only for long ones, which is why it survived every manual test with
        # a short path like ~/Downloads.
        shown = folder if len(folder) <= 52 else "…" + folder[-(52 - 1):]
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
# Control API panel
# --------------------------------------------------------------------------
# 1.0.x embedded a chat panel that shelled out to the `hermes` CLI. That made
# a music player depend on one specific agent install and forced the user to
# type into the app's own text box. control_api.py replaces it with a
# loopback HTTP API, so Claude Code, OpenCode, curl, or anything else on this
# machine can drive playback. The panel below just shows the URL and token.


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


class ApiPanel:
    """Read-only panel showing how to drive the app from an agent.

    Deliberately not a chat box. The app has no opinion about which agent you
    use; it just exposes a loopback API, and this panel tells you the URL,
    the token, and one example so you can copy something that works.
    """

    def __init__(self, font, rect, describe):
        self.font = font
        self.rect = rect
        self.describe = describe      # callable -> one-line status

    def handle_key(self, event):
        if event.key in (pygame.K_ESCAPE, pygame.K_q):
            return "close"
        return None

    def draw(self, screen, t, w, h):
        r = self.rect
        pad = 18
        panel = pygame.Surface((r.width, r.height), pygame.SRCALPHA)
        panel.fill((8, 10, 16, 242))
        pygame.draw.rect(panel, (0, 230, 190, 150), panel.get_rect(), 2)
        screen.blit(panel, r.topleft)

        head = self.font.render("CONTROL API  ·  any agent on this machine",
                                True, (0, 230, 190))
        screen.blit(head, (r.x + pad, r.y + 12))
        pygame.draw.line(screen, (255, 255, 255, 30),
                         (r.x + pad, r.y + 36), (r.x + r.width - pad, r.y + 36))

        base = r.y + 52
        line_h = self.font.get_linesize() + 6
        rows = [
            ("endpoint", self.describe()),
            ("", ""),
            ("example", "curl -H 'Authorization: Bearer <token>' \\"),
            ("", "     http://127.0.0.1:8777/status"),
            ("", "curl -X POST -H 'Authorization: Bearer <token>' \\"),
            ("", "     -d '{\"query\": \"ncs\"}' http://127.0.0.1:8777/play"),
            ("", ""),
            ("webhook", "for bots that cannot send a header:"),
            ("", "  curl -X POST -d 'track=daft punk' \\"),
            ("", "  'http://127.0.0.1:8777/webhook?token=<token>'"),
            ("", "  plain-text reply; also takes action=, next=, volume=, seek="),
            ("", ""),
            ("endpoints", "GET  /status  /tracks  /tracks/search?q="),
            ("", "POST /play /pause /resume /next /prev /seek"),
            ("", "     /volume /muted /visualizer /video /webhook"),
            ("", ""),
            ("note", "loopback only; nothing off this machine can reach it."),
            ("", "Esc closes."),
        ]
        colour_muted = (140, 145, 160)
        for i, (label, text) in enumerate(rows):
            if not text and not label:
                continue
            y = base + i * line_h
            if y > r.y + r.height - line_h * 2:
                break
            if label and label in ("endpoint", "example", "endpoints", "note"):
                c = (0, 230, 190)
            else:
                c = colour_muted
            screen.blit(self.font.render(text, True, c), (r.x + pad, y))


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


def torrent_close_rect(font, w, h):
    """Where the X sits, in window coordinates. Shared by draw and hit-test.

    Returns a square Rect big enough to click comfortably -- a 12px glyph is a
    cruel click target, and this used to have no target at all.
    """
    ow, oh = 700, 240
    ox, oy = (w - ow) // 2, (h - oh) // 2 - 50
    return pygame.Rect(ox + ow - 44, oy + 10, 34, 30)


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

    # A visible close control. The panel used to be dismissible only by
    # pressing ESC -- and because the panel kept drawing itself after a
    # download finished, ESC mostly reached the app-wide quit handler instead
    # and closed the program. Now the way out is drawn on the thing you are
    # trying to close. Plain "x", not "✕": the monospace face the launcher
    # resolves has no glyph for U+2715 and renders tofu boxes.
    close = torrent_close_rect(font, w, h)
    hover = close.collidepoint(pygame.mouse.get_pos())
    xcol = (255, 120, 120) if hover else (150, 155, 170)
    pygame.draw.rect(screen, (255, 90, 90, 60) if hover else (40, 44, 60),
                     close, border_radius=5)
    xsurf = font.render("x", True, xcol)
    screen.blit(xsurf, (close.centerx - xsurf.get_width() // 2,
                        close.centery - xsurf.get_height() // 2))

    box = pygame.Rect(ox + 20, oy + 44, ow - 40, 40)
    pygame.draw.rect(screen, (26, 30, 44), box, border_radius=6)
    pygame.draw.rect(screen, (60, 70, 95), box, 1, border_radius=6)
    shown = text[-58:] if len(text) > 58 else text
    cursor = "|" if int(time.time() * 2) % 2 == 0 else ""
    screen.blit(font.render(shown + cursor, True, (235, 235, 245)),
                (box.x + 10, box.y + 12))

    y = oy + 100
    for s in statuses:
        # "ok", not "✔": U+2714 has no glyph in the monospace face the
        # launcher resolves and rendered as a tofu box.
        done = s.startswith(("ok", "done"))
        col = (0, 230, 190) if done else (170, 200, 230)
        screen.blit(font.render(s, True, col), (ox + 20, y))
        y += 22
    if notice:
        colr = {"info": (150, 210, 255), "warn": (255, 200, 90),
                "error": (255, 110, 110)}.get(notice[0], (200, 200, 200))
        screen.blit(font.render(notice[1][:70], True, colr), (ox + 20, oy + oh - 56))

    tip = font.render("Enter start · Esc or x close", True, (130, 135, 150))
    screen.blit(tip, (ox + 20, oy + oh - 30))


def main():
    # --version must work before anything is created, and must read the same
    # VERSION the updater compares against, so a frozen build that lost its
    # bundled VERSION file is visible here rather than silently reporting 0.0.0.
    if "--version" in sys.argv[1:]:
        import version as _v
        print(f"HashPlay {_v.APP_VERSION}")
        return

    # The config is loaded before the folder is chosen on purpose: the startup
    # folder has to honour what the setup wizard saved last run, or the wizard
    # would be re-asked every launch and its answer thrown away.
    # Load -> migrate -> merge, in that order: migrations must see the real
    # stored shape before defaults fill in the gaps.
    cfg = appconfig.load_config()
    try:
        cfg = appmigrations.migrate(cfg)
    except Exception as exc:            # a bad file must not brick the app
        print(f"settings ignored ({exc}); using defaults")
        cfg = appconfig.default_config()

    # Precedence: an explicit path on the command line, then whatever the
    # wizard or settings panel saved, then ~/Music.
    folder_is_explicit = False
    if len(sys.argv) > 1:
        folder = sys.argv[1]
        folder_is_explicit = True
    else:
        saved = (cfg.get("library_folder") or "").strip()
        folder_is_explicit = bool(saved)
        folder = saved or os.path.expanduser("~/Music")

    # An explicitly chosen folder IS the answer to the wizard's question. Seed
    # it into cfg so needs_setup() sees it, otherwise the first-run wizard
    # pops up modal over a library that is already loaded and correct. The
    # ~/Music fallback deliberately does not seed: a brand-new user with an
    # empty ~/Music is exactly who the wizard is for.
    if folder_is_explicit and not (cfg.get("library_folder") or "").strip():
        cfg["library_folder"] = folder

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

    # ---- quitting needs intent ------------------------------------------
    # ESC used to quit the app from any non-modal state, so a stray ESC aimed
    # at a panel -- the torrent list, say -- closed the app instead. Quitting
    # now takes two ESC presses within QUIT_CONFIRM_SECS; anything else
    # disarms it. Q is untouched: it is a deliberate binding, not a reflex
    # key you hit while aiming at something else.
    quit_armed_until = 0.0

    # The torrent list used to be drawn whenever a session existed, so it
    # stayed on screen -- and kept intercepting ESC -- long after you were
    # done with it. Dismissing it is explicit now; T brings it back.
    torrents_dismissed = False
    last_scan = 0.0
    notice = None          # (kind, text)
    notice_until = 0.0
    hover_idx = None
    base_y = h - 90

    chat_open = False
    chat_panel = None

    # ---- settings, keymap ---------------------------------------------
    # cfg was already loaded (and migrated) above, before the folder was
    # chosen, so the saved library_folder could be honoured.
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
                           "F visual  V video  C api  T torrent  "
                           "O folder  M mute  Q quit",
                           True, (120, 120, 130))

    hint_surf = render_hint()
    hint_w = hint_surf.get_width()

    def push_notice(entry, duration=3.0):
        nonlocal notice, notice_until
        notice = entry
        notice_until = time.time() + duration

    # ---- control API ----------------------------------------------------
    # Replaces the 1.0.x Hermes chat panel. Loopback HTTP so any agent on this
    # machine -- Claude Code, OpenCode, a script, curl -- can control playback
    # without the app knowing or caring which one it is. Started here, after
    # push_notice exists, so a bind failure can actually be reported.
    import control_api as capi_mod
    api_token = capi_mod.load_or_make_token(
        os.path.join(appconfig.CONFIG_DIR, "api_token"))
    api = capi_mod.ControlAPI(
        host=cfg.get("api_host") or "127.0.0.1",
        port=int(cfg.get("api_port") or 8777),
        token=api_token,
        enabled=bool(cfg.get("api_enabled", True)),
    )
    if api.enabled and not api.start():
        # Both on screen AND in the log. A bind failure used to be an 8-second
        # toast and nothing else: miss it and the app looks completely normal
        # while having no remote control at all, which is the hardest kind of
        # failure to notice. The panel (C) also shows it, but nobody presses C
        # when the feature they are not using is the thing that is broken.
        why = f"control API unavailable: {api.last_error}"
        print(why)
        print(why, file=sys.stderr)
        push_notice(("error", f"{why} -- remote control is off"), 10.0)
    api_open = False
    api_panel = None               # built lazily, needs the font

    # ---- first-run setup ------------------------------------------------
    # A first launch with no library folder has nothing to show and no obvious
    # next step, so the wizard walks it: welcome, dependency report, folder
    # choice, summary. It edits cfg in place (the same contract
    # SettingsPanel uses) and reports through on_finish. `needs_setup()` is
    # False once a folder is chosen or the user skips, so it never nags.
    import setup_wizard as setup_mod
    _setup_finished = []

    def _on_setup_done(summary):
        _setup_finished.append(summary)
        nonlocal folder
        save_settings()
        if summary.get("skipped"):
            push_notice(("info", "setup skipped - press O to pick a folder"), 5.0)
        else:
            missing = summary.get("missing") or []
            if missing:
                push_notice(("info",
                             f"library ready; missing: {', '.join(missing)}"), 6.0)
            else:
                push_notice(("info", "library ready"), 3.0)

    setup_wizard = setup_mod.SetupWizard(font, cfg, on_finish=_on_setup_done)
    setup_active = setup_wizard.needs_setup()
    if not setup_active:
        setup_wizard.active = False

    nonlocal_selected = [selected]  # for media_key sync

    # ---- API command handlers -------------------------------------------
    # These run on the MAIN thread, called from api.drain() once per frame.
    # That is the whole point of the queue in control_api: SDL is not
    # thread-safe, so the HTTP thread must never touch the player directly.
    def _cur():
        return tracks[selected] if 0 <= selected < len(tracks) else {}

    def _safe_position(p):
        """player.position() reads audio-thread state; never let it raise.

        The callback rewrites samples/pos as it plays, and a transient failure
        here would take down the whole frame -- and with it the app -- just
        because an agent asked for /status.
        """
        try:
            return round(float(p.position()), 2)
        except Exception:
            return 0.0

    def _safe_volume(p):
        try:
            return round(float(p.volume), 3)
        except Exception:
            return 1.0

    def _need_tracks():
        if not tracks:
            raise RuntimeError("the library is empty; press O to pick a folder")
        return True

    def _reject_during_setup():
        if setup_active:
            raise RuntimeError(
                "the first-run setup wizard is open and is modal; finish or "
                "skip it (Esc) before controlling playback")

    def _select_by_query(q):
        q = (q or "").lower().strip()
        if not q:
            raise ValueError("play needs an index, a query, or nothing")
        for i, tr in enumerate(tracks):
            hay = f"{tr.get('title','')} {tr.get('artist','')} {tr.get('album','')}".lower()
            if q in hay:
                return i
        # fall back to a loose subsequence match so "drft" finds "Drift"
        for i, tr in enumerate(tracks):
            hay = f"{tr.get('title','')} {tr.get('artist','')}".lower()
            if all(ch in hay for ch in q if not ch.isspace()):
                return i
        raise ValueError(f"nothing in the library matches {q!r}")

    def api_cmd_tracks(_a):
        return [{"index": i, "title": tr.get("title"),
                 "artist": tr.get("artist"), "album": tr.get("album"),
                 "path": tr.get("path")} for i, tr in enumerate(tracks)]

    def api_cmd_search(a):
        q = (a.get("q") or "").lower().strip()
        if not q:
            return api_cmd_tracks({})
        hits = []
        for i, tr in enumerate(tracks):
            hay = f"{tr.get('title','')} {tr.get('artist','')} {tr.get('album','')}".lower()
            if q in hay:
                hits.append({"index": i, "title": tr.get("title"),
                             "artist": tr.get("artist")})
        return {"query": q, "count": len(hits), "results": hits}

    def api_cmd_play(a):
        nonlocal selected
        _reject_during_setup()
        _need_tracks()
        if "index" in a:
            idx = int(a["index"])
            if not 0 <= idx < len(tracks):
                raise IndexError(f"index {idx} out of range (0..{len(tracks)-1})")
        elif a.get("query"):
            idx = _select_by_query(a["query"])
        else:
            # bare /play means "resume what is loaded"
            if not player.paused:
                return {"status": "already playing",
                        "track": _cur().get("title")}
            player.toggle_pause()
            return {"status": "resumed", "track": _cur().get("title")}
        selected = idx
        nonlocal_selected[0] = selected
        player.load(tracks[selected]['path'])
        return {"status": "playing", "index": selected,
                "track": tracks[selected].get("title")}

    def api_cmd_pause(_a):
        if not player.track_path:
            raise RuntimeError("nothing is loaded")
        if not player.paused:
            player.toggle_pause()
        return {"status": "paused", "track": _cur().get("title")}

    def api_cmd_resume(_a):
        if not player.track_path:
            raise RuntimeError("nothing is loaded")
        if player.paused:
            player.toggle_pause()
        return {"status": "playing", "track": _cur().get("title")}

    def _step(delta):
        nonlocal selected
        _reject_during_setup()
        _need_tracks()
        selected = (selected + delta) % len(tracks)
        nonlocal_selected[0] = selected
        player.load(tracks[selected]['path'])
        return {"index": selected, "track": tracks[selected].get("title")}

    def api_cmd_next(_a):
        return _step(1)

    def api_cmd_prev(_a):
        return _step(-1)

    def api_cmd_seek(a):
        if not player.track_path:
            raise RuntimeError("nothing is loaded")
        # Player.seek() is relative by design; /seek {"seconds": n} is what an
        # agent naturally wants, so convert here rather than making every
        # caller do the subtraction against a position it cannot trust.
        if "seconds" in a:
            target = max(0.0, float(a["seconds"]))
            if "duration" in a:      # trust the caller's duration if it passed one
                dur = max(0.0, float(a["duration"]))
            else:
                dur = player.duration()
            if dur > 0 and target > dur:
                raise ValueError(f"cannot seek to {target}s; the track is {dur:.1f}s")
            delta = target - _safe_position(player)
            player.seek(delta)
            return {"seeked_to": round(_safe_position(player), 2),
                    "duration": round(dur, 2)}
        if "delta" in a:
            player.seek(float(a["delta"]))
            return {"seeked_to": round(_safe_position(player), 2)}
        raise ValueError("seek needs 'seconds' or 'delta'")

    def api_cmd_volume(a):
        nonlocal muted
        if "value" in a:
            v = float(a["value"])
            if not 0.0 <= v <= 1.0:
                raise ValueError("volume 'value' must be 0.0..1.0")
            player.volume = v
            if v > 0 and muted:
                muted = False
                player.muted = False
        elif "delta" in a:
            player.volume = max(0.0, min(1.0, player.volume + float(a["delta"])))
        else:
            raise ValueError("volume needs 'value' (0.0..1.0) or 'delta'")
        return {"volume": round(player.volume, 3)}

    def api_cmd_muted(a):
        nonlocal muted
        want = a.get("value")
        muted = (not muted) if want is None else bool(want)
        player.muted = muted
        return {"muted": muted}

    def api_cmd_visualizer(a):
        nonlocal vis_mode_idx
        if a.get("next"):
            vis_mode_idx = (vis_mode_idx + 1) % len(modes)
            return {"mode": modes[vis_mode_idx], "modes": modes}
        mode = a.get("mode")
        if not mode:
            return {"mode": modes[vis_mode_idx], "modes": modes}
        m = str(mode).lower()
        if m not in [x.lower() for x in modes]:
            raise ValueError(f"unknown visualizer {mode!r}; have {modes}")
        vis_mode_idx = [x.lower() for x in modes].index(m)
        return {"mode": modes[vis_mode_idx]}

    def api_cmd_video(a):
        if a.get("off"):
            video_slot.clear(by_user=True)
            return {"video": "off"}
        src = a.get("source")
        if not src:
            raise ValueError("video needs 'source' or 'off': true")
        video_slot.request(src, sync_to=player.position() if player.track_path else 0.0)
        return {"video": src, "pinned": True}

    API_HANDLERS = {
        "play": api_cmd_play, "pause": api_cmd_pause, "resume": api_cmd_resume,
        "next": api_cmd_next, "prev": api_cmd_prev, "seek": api_cmd_seek,
        "volume": api_cmd_volume, "muted": api_cmd_muted,
        "visualizer": api_cmd_visualizer, "video": api_cmd_video,
        "__tracks": api_cmd_tracks, "__search": api_cmd_search,
    }

    # ---- action dispatch ------------------------------------------------
    # Every user-rebindable key funnels through here. Keeping one table means
    # the keymap, the settings panel and the media keys all agree on what an
    # action does, and adding a binding never means editing the event chain.

    def do_pick_folder():
        """Choose a library folder, rescan, and remember the choice.

        Shared by the O key and the settings panel's folder row, so both do
        exactly the same thing.
        """
        nonlocal folder, selected
        picked = pick_folder_dialog(folder)
        if not (picked and os.path.isdir(picked)):
            push_notice(("info", "folder picker cancelled"))
            return False
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
        return True

    def do_rescan_folder():
        """Adopt cfg["library_folder"] as the library and rebuild the list.

        Separate from do_pick_folder because the setup wizard and the settings
        panel can both set the folder without going through a native dialog.
        """
        nonlocal folder, selected
        picked = (cfg.get("library_folder") or "").strip()
        if not (picked and os.path.isdir(picked)):
            return False
        folder = picked
        folders[0] = folder
        tracks.clear()
        scan_library(folders, tracks)
        selected = 0
        if tracks:
            player.load(tracks[0]['path'])
        return True

    def do_action(act, shifted=False):
        nonlocal selected, muted, vis_mode_idx, api_open, settings_open
        nonlocal video_overlay_open, video_overlay_text, overlay_open, folder
        nonlocal running, setup_active
        if setup_active:
            # First-run wizard is modal. It owns the keyboard until it is done
            # or skipped; a stray keypress must not change tracks or open the
            # API panel behind it.
            return
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
        elif act in ("open_api", "open_hermes"):
            # "open_hermes" is accepted as an alias: it is the name 1.0.x saved
            # to settings.json, and a keymap on disk should never be a dead
            # key just because the panel behind it got renamed.
            api_open = not api_open
            if api_open:
                if api_panel is None:
                    api_panel = ApiPanel(font, pygame.Rect(0, 0, w, h),
                                         api.describe)
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
            do_pick_folder()
        elif act == "open_settings":
            settings_open = not settings_open
            if settings_open:
                if settings_panel is None:
                    import settings_panel as sp
                    settings_panel = sp.SettingsPanel(
                        cfg, keymap, pick_folder=do_pick_folder, font=font)
                settings_panel.open()
                settings_open = settings_panel.is_open()
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
    frame_no = 0
    while running:
        frame_no += 1
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
                if api_open:
                    api_panel.rect = pygame.Rect(0, 0, w, h)
                if settings_open and settings_panel is not None:
                    settings_panel.rect = getattr(settings_panel, "rect", None)
                push_notice(("info", f"resized to {w}x{h}"))
            elif event.type == pygame.KEYDOWN:
                if setup_active:
                    # Modal first-run wizard: it consumes the key and nothing
                    # else sees it, so no keymap action fires behind it.
                    outcome = setup_wizard.handle_key(event)
                    if outcome == "changed":
                        # A folder was accepted partway through the flow; the
                        # list should be real behind the remaining steps, not
                        # empty until the final Enter.
                        do_rescan_folder()
                    elif outcome in ("done", "skipped"):
                        setup_active = setup_wizard.is_active()
                    continue
                if settings_open and settings_panel is not None:
                    # The panel owns every key while it is open, so a rebind
                    # capture cannot be stolen by a normal binding.
                    verdict = settings_panel.handle_key(event)
                    if verdict == "close":
                        settings_open = False
                        save_settings()
                    elif verdict in ("rebind", "toggled", "changed"):
                        save_settings()
                    settings_open = settings_panel.is_open()
                    continue
                if api_open:
                    if api_panel.handle_key(event) == "close":
                        api_open = False
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
                # Disarm a pending quit-confirm on any key that is not ESC.
                # Done as a statement rather than an `elif` in the chain below,
                # so the key still does its job -- an `elif` here would swallow
                # it, and T would stop opening the torrent box.
                if (event.key != pygame.K_ESCAPE
                        and time.time() < quit_armed_until):
                    quit_armed_until = 0.0
                if event.key == pygame.K_ESCAPE:
                    # Layered dismissal, most-specific first. ESC used to fall
                    # straight through to `running = False` whenever the
                    # torrent *list* was merely visible, so the natural thing
                    # to do -- ESC the box you are looking at -- killed the
                    # app. Each layer gets a chance to consume it; only when
                    # nothing is open does ESC mean "quit", and then it has to
                    # be pressed twice.
                    if overlay_open:
                        overlay_open = False
                        overlay_text = ""
                    elif torrents and not torrents_dismissed:
                        torrents_dismissed = True
                    elif time.time() < quit_armed_until:
                        running = False
                    else:
                        quit_armed_until = time.time() + QUIT_CONFIRM_SECS
                        push_notice(("warn", "press Esc again to quit"), 2.0)
                elif event.key == pygame.K_q:
                    running = False
                elif event.key == pygame.K_t:
                    overlay_open = True
                    torrents_dismissed = False
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
                mx, my = event.pos
                # The torrent panel's X, checked before anything else so a
                # click on it can never fall through to the playlist rows
                # underneath. Available whether or not the text prompt is
                # open, because the list stays up after a download finishes.
                if torrents and not (torrents_dismissed and not overlay_open) \
                        and torrent_close_rect(font, w, h).collidepoint((mx, my)):
                    overlay_open = False
                    overlay_text = ""
                    torrents_dismissed = True
                    quit_armed_until = 0.0
                    continue
                if overlay_open:
                    continue
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

        # ---- control API, serviced on the main thread --------------------
        # Order matters: publish state first so a command that reads /status
        # sees this frame, then drain, so the HTTP thread's reply is sent the
        # same frame. Both must be here, not in the HTTP thread, because SDL
        # is not thread-safe.
        api.publish({
            "playing": bool(player.track_path) and not player.paused,
            "paused": bool(player.paused),
            "track": _cur().get("title"),
            "artist": _cur().get("artist"),
            "index": selected,
            "position": _safe_position(player),
            "volume": _safe_volume(player),
            "muted": muted,
            "visualizer": modes[vis_mode_idx],
            "visualizers": modes,
            "video": (video_slot.message or ("playing" if video_slot.is_active()
                                             else "off")),
            "track_count": len(tracks),
            "library_folder": folder,
            # An agent polling /status can tell it needs to wait for the user
            # rather than assuming the player is broken.
            "setup_pending": bool(setup_active),
            "api": {"host": api.host, "port": api.port, "version": 1},
            # A monotonically rising frame number. An agent polling /status
            # can tell a busy app from a wedged one: if this stops moving, the
            # main loop is not turning over and no command will ever be
            # serviced. Cheaper and more honest than inferring it from a
            # stalled playhead.
            "frame": frame_no,
        })
        api.drain(API_HANDLERS)

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
        # Draw torrent overlay. Gated on the panel not being dismissed: the
        # input prompt still shows (it is modal), but a finished download no
        # longer parks a panel over the app waiting to eat an ESC.
        if torrents and not (torrents_dismissed and not overlay_open):
            statuses = torrents.status_lines()
            if notice or overlay_open:
                draw_torrent_overlay(screen, font, w, h, overlay_text, statuses,
                                     notice)

        # Draw the control-API panel on top when it is open
        if api_open:
            api_panel.draw(screen, t, w, h)

        # Draw the video-source overlay above everything else
        if video_overlay_open:
            draw_video_overlay(screen, font, w, h, video_overlay_text)

        # Settings sits on top of the video prompt: it is modal.
        if settings_open and settings_panel is not None:
            settings_panel.draw(screen, font=font, w=w, h=h)

        # The first-run wizard is the most modal thing here: it covers the
        # whole window, and nothing underneath should look interactive.
        if setup_active and setup_wizard.is_active():
            setup_wizard.draw(screen, font=font, w=w, h=h)

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

        # The first ESC arms a quit. Make that visible: a confirm you cannot
        # see is just an unresponsive app.
        if time.time() < quit_armed_until:
            left = quit_armed_until - time.time()
            box = font.render(f"press Esc again to quit  ({left:.1f}s)",
                              True, (255, 190, 90))
            bw, bh = box.get_width() + 40, box.get_height() + 24
            bx, by = (w - bw) // 2, int(h * 0.32)
            shade = pygame.Surface((bw, bh), pygame.SRCALPHA)
            shade.fill((24, 18, 8, 235))
            screen.blit(shade, (bx, by))
            pygame.draw.rect(screen, (255, 190, 90), (bx, by, bw, bh), 2,
                             border_radius=8)
            screen.blit(box, (bx + 20, by + 12))

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