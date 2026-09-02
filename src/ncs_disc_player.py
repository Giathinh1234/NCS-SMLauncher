#!/usr/bin/env python3
"""
NCS-Style Music Disc Player - Simple & Resizable
================================================
A clean, resizable music player with disc visualizer showing track metadata.
"""

import os
import sys
import math
import time
import glob
import threading

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
    import mutagen
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC
    from mutagen.easyid3 import EasyID3
except ImportError:
    sys.exit("Missing dependency 'mutagen'. Run: pip install mutagen")

try:
    import numpy as np
except ImportError:
    sys.exit("Missing dependency 'numpy'. Run: pip install numpy")


# ============================================================================
# CONFIGURATION
# ============================================================================
AUDIO_EXTENSIONS = ("*.mp3", "*.wav", "*.ogg", "*.flac", "*.m4a")
SAMPLE_RATE = 44100
FFT_SIZE = 2048
NUM_BARS = 32  # Reduced for cleaner disc visual

APP_DIR = os.path.dirname(os.path.abspath(__file__))
MUSIC_FOLDER = os.path.join(APP_DIR, "music")
os.makedirs(MUSIC_FOLDER, exist_ok=True)

# ============================================================================
# TRACK METADATA HELPER
# ============================================================================
def get_track_metadata(filepath):
    """Extract metadata from audio file."""
    try:
        audio = EasyID3(filepath) if filepath.lower().endswith('.mp3') else None
        if audio:
            title = audio.get('title', [os.path.basename(filepath)])[0]
            artist = audio.get('artist', ['Unknown Artist'])[0]
            album = audio.get('album', ['Unknown Album'])[0]
            year = audio.get('date', [''])[0]
        else:
            # Fallback for non-MP3 files
            title = os.path.basename(filepath)
            artist = 'Unknown Artist'
            album = 'Unknown Album'
            year = ''
        
        # Try to get album art
        album_art = None
        try:
            if filepath.lower().endswith('.mp3'):
                tags = ID3(filepath)
                for tag in tags.values():
                    if isinstance(tag, APIC):
                        album_art = bytes(tag)
                        break
        except:
            pass
            
        return {
            'title': title,
            'artist': artist,
            'album': album,
            'year': year,
            'art': album_art,
            'path': filepath
        }
    except Exception as e:
        print(f"Metadata error for {filepath}: {e}")
        return {
            'title': os.path.basename(filepath),
            'artist': 'Unknown Artist',
            'album': 'Unknown Album',
            'year': '',
            'art': None,
            'path': filepath
        }

# ============================================================================
# AUDIO PLAYER
# ============================================================================
class Player(threading.Thread):
    """Streams decoded audio through sounddevice while tracking position."""
    
    def __init__(self):
        super().__init__(daemon=True)
        self.samples = None
        self.pos = 0
        self.paused = True
        self.muted = False
        self.track_path = None
        self.metadata = None
        self.lock = threading.Lock()
        self.ring = np.zeros(FFT_SIZE * 2, dtype=np.float32)
        self.stream = sd.OutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            blocksize=1024, callback=self._callback
        )
    
    def _callback(self, outdata, frames, time_info, status):
        out = np.zeros((frames, 1), dtype=np.float32)
        with self.lock:
            if self.samples is not None and not self.paused:
                end = self.pos + frames
                chunk = self.samples[self.pos:end]
                out[:len(chunk), 0] = chunk
                self.pos += len(chunk)
                if self.pos >= len(self.samples):
                    self.pos = len(self.samples) - 1
                if len(chunk):
                    self.ring = np.roll(self.ring, -len(chunk))
                    self.ring[-len(chunk):] = chunk
            elif self.samples is not None and self.paused:
                self.ring = np.roll(self.ring, -frames)
                self.ring[-frames:] *= 0.9
        if self.muted:
            out[:] = 0
        outdata[:] = out
    
    def load(self, filepath):
        """Load and decode audio file."""
        samples = decode_file(filepath)
        metadata = get_track_metadata(filepath)
        with self.lock:
            self.samples = samples
            self.pos = 0
            self.track_path = filepath
            self.metadata = metadata
            self.paused = False
    
    def toggle_pause(self):
        with self.lock:
            if self.samples is not None:
                self.paused = not self.paused
    
    def seek(self, delta_s):
        with self.lock:
            if self.samples is not None:
                self.pos = int(np.clip(
                    self.pos + delta_s * SAMPLE_RATE, 0, len(self.samples)
                ))
    
    def previous_track(self, tracks, current_index):
        """Get previous track index."""
        if not tracks:
            return current_index
        return (current_index - 1) % len(tracks)
    
    def next_track(self, tracks, current_index):
        """Get next track index."""
        if not tracks:
            return current_index
        return (current_index + 1) % len(tracks)
    
    def duration(self):
        return len(self.samples) / SAMPLE_RATE if self.samples is not None else 0.0
    
    def position(self):
        return self.pos / SAMPLE_RATE if self.samples is not None else 0.0
    
    def spectrum(self):
        """Get frequency spectrum for visualization."""
        window = self.ring.copy()
        spec = np.abs(np.fft.rfft(window * np.hanning(len(window))))
        freqs = np.fft.rfftfreq(len(window), 1.0 / SAMPLE_RATE)
        # Focus on lower frequencies for disc effect
        edges = np.geomspace(50, 2000, NUM_BARS + 1)  # 50Hz to 2kHz
        bins = []
        for i in range(NUM_BARS):
            lo, hi = edges[i], edges[i + 1]
            idx = (freqs >= lo) & (freqs < hi)
            v = spec[idx].max() if idx.any() else 0.0
            bins.append(v)
        mag = np.array(bins, dtype=np.float32)
        mag = np.log1p(mag * 3) / math.log(1 + 10)  # Gentler curve
        return np.clip(mag, 0, 1)

# ============================================================================
# AUDIO DECODING
# ============================================================================
def decode_file(path):
    """Decode audio file to mono float32 samples."""
    data = miniaudio.decode_file(path, nchannels=1, sample_rate=SAMPLE_RATE,
                                 output_format=miniaudio.SampleFormat.SIGNED16)
    samples = np.frombuffer(data.samples, dtype=np.int16).astype(np.float32) / 32768.0
    return samples

# ============================================================================
# VISUALIZER & UI
# ============================================================================
def neon_color(t, sat=0.95, val=1.0):
    """Generate neon HSV color."""
    r, g, b = colorsys.hsv_to_rgb(t % 1.0, sat, val)
    return int(r * 255), int(g * 255), int(b * 255)

def draw_disc_visualizer(screen, player, w, h, t):
    """Draw NCS-style rotating disc with track info."""
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    cx, cy = w // 2, h // 2
    
    # Get spectrum data
    mag = player.spectrum()
    
    # Disc parameters
    disc_radius = min(w, h) * 0.35
    hole_radius = disc_radius * 0.25
    
    # Rotation based on time and audio energy
    bass_energy = float(np.mean(mag[:len(mag)//4])) if len(mag) > 0 else 0.0
    rotation_speed = 0.5 + bass_energy * 2.0  # Slower base rotation
    angle = (t * rotation_speed) % (2 * math.pi)
    
    # Draw outer disc glow
    for i in range(5):
        glow_radius = disc_radius + 10 * (i + 1)
        glow_alpha = max(0, 40 - i * 8)
        glow_color = (*neon_color((t * 0.02 + i * 0.1) % 1.0, 0.8, 0.6), glow_alpha)
        pygame.draw.circle(surf, glow_color, (cx, cy), int(glow_radius))
    
    # Draw main disc
    disc_color = (20, 25, 35, 220)  # Dark bluish
    pygame.draw.circle(surf, disc_color, (cx, cy), int(disc_radius))
    
    # Draw center hole
    hole_color = (10, 12, 18, 255)
    pygame.draw.circle(surf, hole_color, (cx, cy), int(hole_radius))
    
    # Draw rotating sticker/label area
    label_radius = disc_radius * 0.8
    label_points = []
    num_points = 32
    for i in range(num_points):
        pt_angle = angle + (2 * math.pi * i / num_points)
        radius_mod = 1.0 + 0.1 * math.sin(i * 0.5 + t * 2)  # Slight pulse
        x = cx + math.cos(pt_angle) * label_radius * radius_mod
        y = cy + math.sin(pt_angle) * label_radius * radius_mod
        label_points.append((x, y))
    
    if len(label_points) >= 3:
        # Draw label with track color based on metadata
        if player.metadata:
            # Generate consistent color from track title
            title_hash = sum(ord(c) for c in player.metadata['title']) % 100
            hue = title_hash / 100.0
            label_color = (*neon_color(hue, 0.9, 0.8), 200)
        else:
            label_color = (100, 200, 255, 180)
        pygame.draw.polygon(surf, label_color, label_points)
    
    # Draw frequency rays (like EQ bars but radial)
    ray_length = disc_radius * 0.6
    ray_start = hole_radius + disc_radius * 0.1
    for i, m in enumerate(mag):
        if m < 0.02:  # Skip very low energy
            continue
        ray_angle = angle + (2 * math.pi * i / len(mag)) - math.pi/2
        ray_mag = m * 0.8 + 0.2  # Minimum visibility
        
        # Color based on frequency position (bass=red, treble=violet)
        hue = i / len(mag)
        ray_color = (*neon_color(hue, 0.9, 0.9), int(150 + m * 100))
        
        # Calculate ray endpoints
        x1 = cx + math.cos(ray_angle) * ray_start
        y1 = cy + math.sin(ray_angle) * ray_start
        x2 = cx + math.cos(ray_angle) * (ray_start + ray_length * ray_mag)
        y2 = cy + math.sin(ray_angle) * (ray_start + ray_length * ray_mag)
        
        width = max(1, int(2 + m * 4))
        pygame.draw.line(surf, ray_color, (int(x1), int(y1)), (int(x2), int(y2)), width)
        
        # Outer glow for strong frequencies
        if m > 0.5:
            glow_color = (*ray_color[:3], 60)
            pygame.draw.line(surf, glow_color, (int(x1), int(y1)), (int(x2), int(y2)), width + 2)
    
    # Center badge with track info
    if player.metadata:
        badge_radius = hole_radius * 0.8
        badge_color = (0, 10, 20, 200)
        pygame.draw.circle(surf, badge_color, (cx, cy), int(badge_radius))
        
        # Inner badge glow
        inner_badge_color = (*neon_color(t * 0.05) , 100)
        pygame.draw.circle(surf, inner_badge_color, (cx, cy), int(badge_radius * 0.7))
    
    # Draw outer ring
    ring_color = (40, 50, 70, 180)
    pygame.draw.circle(surf, ring_color, (cx, cy), int(disc_radius), 2)
    
    screen.blit(surf, (0, 0))

def draw_info_panel(screen, font, font_big, player, w, h):
    """Draw track info panel at bottom."""
    panel_height = 140
    panel_y = h - panel_height
    
    # Panel background
    panel_surf = pygame.Surface((w, panel_height), pygame.SRCALPHA)
    panel_surf.fill((10, 15, 25, 200))
    pygame.draw.rect(panel_surf, (0, 25, 50, 100), (0, 0, w, panel_height), 1)
    
    if player.metadata:
        # Track title
        title = player.metadata['title']
        if len(title) > 35:
            title = title[:32] + "..."
        title_surf = font_big.render(title, True, (255, 255, 255))
        panel_surf.blit(title_surf, (20, 20))
        
        # Artist - Album
        artist = player.metadata['artist']
        album = player.metadata['album']
        if len(artist) > 30:
            artist = artist[:27] + "..."
        if len(album) > 30:
            album = album[:27] + "..."
        info_text = f"{artist} • {album}"
        info_surf = font.render(info_text, True, (200, 200, 220))
        panel_surf.blit(info_surf, (20, 60))
        
        # Year if available
        year = player.metadata['year']
        if year:
            year_surf = font.render(f"Year: {year}", True, (180, 180, 200))
            panel_surf.blit(year_surf, (20, 90))
        
        # Progress bar
        if player.samples is not None:
            progress = player.position() / player.duration() if player.duration() > 0 else 0
            bar_width = w - 40
            bar_height = 6
            bar_x = 20
            bar_y = panel_height - 25
            
            # Background
            pygame.draw.rect(panel_surf, (30, 35, 45), (bar_x, bar_y, bar_width, bar_height))
            # Progress
            pygame.draw.rect(panel_surf, (0, 200, 180), 
                           (bar_x, bar_y, int(bar_width * progress), bar_height))
            # Glow on progress
            if progress > 0:
                glow_width = int(bar_width * progress * 0.1)
                pygame.draw.rect(panel_surf, (0, 255, 255, 100),
                               (bar_x + int(bar_width * progress) - glow_width//2, 
                                bar_y - 2, glow_width, bar_height + 4))
    
    # Controls hint
    controls = "SPACE: Play/Pause | ←→: Seek | ↑↓: Prev/Next | ESC: Quit"
    controls_surf = font.render(controls, True, (120, 120, 140))
    panel_surf.blit(controls_surf, (20, panel_height - 50))
    
    screen.blit(panel_surf, (0, panel_y))

# ============================================================================
# MAIN APPLICATION
# ============================================================================
def scan_music_folder(folder):
    """Scan folder for music files."""
    found = []
    for ext in AUDIO_EXTENSIONS:
        found.extend(glob.glob(os.path.join(folder, "**", ext), recursive=True))
    return sorted(found)

def main():
    # Use command line argument or default music folder
    music_folder = sys.argv[1] if len(sys.argv) > 1 else MUSIC_FOLDER
    if not os.path.isdir(music_folder):
        os.makedirs(music_folder, 1)
        print(f"Created music folder: {music_folder}")
        print("Please add some music files and restart.")
        return
    
    tracks = scan_music_folder(music_folder)
    if not tracks:
        print(f"No music files found in {music_folder}")
        print(f"Supported formats: {', '.join(AUDIO_EXTENSIONS)}")
        print(f"Please add some music files to {music_folder} and restart.")
        return
    
    print(f"Found {len(tracks)} track(s)")
    
    # Initialize pygame
    pygame.init()
    w, h = 1000, 700  # Starting size
    screen = pygame.display.set_mode((w, h), pygame.RESIZABLE)
    pygame.display.set_caption("NCS Music Disc Player")
    clock = pygame.time.Clock()
    
    # Fonts
    font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 16)
    font_big = pygame.font.SysFont("consolas,menlo,dejavusansmono", 20, bold=True)
    
    # Player state
    player = Player()
    player.stream.start()
    
    selected = 0
    if tracks:
        player.load(tracks[selected])
    
    paused = False
    start_time = time.time()
    
    print("Controls:")
    print("  SPACE: Play/Pause")
    print("  LEFT/RIGHT: Seek +/- 5 seconds")
    print("  UP/DOWN: Previous/Next track")
    print("  ESC: Quit")
    print("  Drag window edges to resize")
    
    running = True
    while running:
        t = time.time() - start_time
        
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE:
                # Handle window resizing
                w, h = max(800, event.w), max(600, event.h)
                screen = pygame.display.set_mode((w, h), pygame.RESIZABLE)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    player.toggle_pause()
                    paused = player.paused
                elif event.key == pygame.K_LEFT:
                    player.seek(-5)
                elif event.key == pygame.K_RIGHT:
                    player.seek(5)
                elif event.key == pygame.K_UP:
                    # Previous track
                    selected = player.previous_track(tracks, selected)
                    player.load(tracks[selected])
                elif event.key == pygame.K_DOWN:
                    # Next track
                    selected = player.next_track(tracks, selected)
                    player.load(tracks[selected])
        
        # Auto-advance when track ends
        if (player.samples is not None and not player.paused and 
            player.position() >= player.duration() - 0.5 and 
            player.duration() > 0):
            selected = player.next_track(tracks, selected)
            player.load(tracks[selected])
        
        # Clear screen with dark gradient
        screen.fill((5, 8, 15))
        
        # Draw visualizer
        draw_disc_visualizer(screen, player, w, h, t)
        
        # Draw info panel
        draw_info_panel(screen, font, font_big, player, w, h)
        
        pygame.display.flip()
        clock.tick(60)
    
    # Cleanup
    player.stream.stop()
    pygame.quit()
    print("Player stopped.")

if __name__ == "__main__":
    main()