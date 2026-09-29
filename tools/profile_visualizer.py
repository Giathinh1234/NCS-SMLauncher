"""Where does the visualizer's time and memory actually go?

Measures draw_visualizer per mode, in a real process, with tracemalloc for
allocation and time.process_time for CPU. Nothing is inferred: each mode is
called against a real surface and the numbers are what the loop pays.
"""
import gc
import os
import sys
import time
import tracemalloc

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np                                    # noqa: E402
import pygame                                         # noqa: E402
import ncs_launcher as L                              # noqa: E402

W, H = 1280, 748
MODES = ["radial", "disc", "album", "bars", "mirror", "video"]


class FakePlayer:
    """Enough of Player for the visualizer: a spectrum and a playhead."""

    def __init__(self, playing=True, seed=1234):
        rng = np.random.default_rng(seed)
        self._spec = rng.random(64).astype(np.float32)
        self._spec[1] = self._spec[3] = self._spec[5] = 0.0
        self.track_path = "/tmp/fake.mp3" if playing else None
        self.paused = not playing
        self.muted = False
        self.volume = 1.0

    def spectrum(self, n=64):
        return self._spec[:n]

    def position(self):
        return 12.0

    def duration(self):
        return 180.0


def rss_mb():
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * 4096 / 1e6
    except OSError:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


def make_art():
    """A real small PNG, so the album-art decode path is genuinely measured
    rather than skipped by a None."""
    path = "/tmp/hp_profile_art.png"
    if not os.path.exists(path):
        try:
            from PIL import Image
            Image.new("RGBA", (256, 256), (90, 40, 160, 255)).save(path)
        except Exception:
            return None
    return path


def bench(mode, player, frames=90, warm=15, meta=None, repeats=5):
    screen = pygame.Surface((W, H))
    font = pygame.font.Font(None, 22)
    for _ in range(warm):
        L.draw_visualizer(screen, player, W, H, mode, 1.0, meta, None)

    # min-of-N: the fastest run is the least noisy estimate of the real cost,
    # and it is the one that does not punish a mode for whatever else the
    # machine was doing. Single-shot timing here was swinging by 3 ms/frame.
    cpus = []
    for _ in range(repeats):
        gc.collect()
        c0 = time.process_time()
        for i in range(frames):
            L.draw_visualizer(screen, player, W, H, mode, i * 0.016, meta, None)
        cpus.append((time.process_time() - c0) / frames * 1000)
    cpu = min(cpus)
    spread = max(cpus) - min(cpus)

    gc.collect()
    tracemalloc.start()
    base_alloc = tracemalloc.get_traced_memory()[0]
    wall0 = time.perf_counter()
    for i in range(frames):
        L.draw_visualizer(screen, player, W, H, mode, i * 0.016, meta, None)
    wall = (time.perf_counter() - wall0) / frames * 1000
    peak = tracemalloc.get_traced_memory()[1] / 1e6
    live = (tracemalloc.get_traced_memory()[0] - base_alloc) / 1e6
    tracemalloc.stop()
    return cpu, wall, peak, live, spread


pygame.init()
print(f"window {W}x{H}, {len(MODES)} modes, 90 frames each after 10 warmup\n")
print(f"{'mode':10} {'cpu ms/f':>9} {'spread':>8} {'peak MB':>9} {'live MB':>9}")
print("-" * 50)

playing = FakePlayer(playing=True)
idle = FakePlayer(playing=False)

META = {"art_path": make_art(), "artist": "Test", "title": "Track"}
print("album art:", META["art_path"] or "NONE (art decode path untested)")
rows = {}
for m in MODES:
    cpu, wall, peak, live, spread = bench(m, playing, meta=META)
    rows[m] = cpu
    print(f"{m:10} {cpu:9.2f} {spread:8.2f} {peak:9.2f} {live:9.2f}")

print("\nidle (nothing playing) -- the user asked about this specifically")
print(f"{'mode':10} {'cpu ms/f':>9} {'spread':>8} {'peak MB':>9} {'live MB':>9}")
print("-" * 50)
idle_rows = {}
for m in MODES:
    if m == "video":
        continue
    cpu, wall, peak, live, spread = bench(m, idle, meta=META)
    idle_rows[m] = cpu
    print(f"{m:10} {cpu:9.2f} {spread:8.2f} {peak:9.2f} {live:9.2f}")

print(f"\nprocess RSS: {rss_mb():.1f} MB")
non_ball = [m for m in MODES if m not in ("radial", "video")]
worst = max((rows[m], m) for m in non_ball)
print(f"worst non-NCS-ball mode: {worst[1]} at {worst[0]:.2f} ms/frame cpu")
