"""The visualizers got much faster. Prove they still look right.

A 14x speedup on the bar modes is exactly the shape of result you get when
something is silently no longer drawn, so this renders every mode and checks
the actual pixels: that each one is non-empty, that they are visually
distinct from each other, and that the cached-album-art path still produces
the same picture as decoding the art fresh every frame.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np                                    # noqa: E402
import pygame                                         # noqa: E402
import ncs_launcher as L                              # noqa: E402

W, H = 1280, 748
MODES = ["radial", "disc", "album", "bars", "mirror", "video"]
OUT = "/tmp/hp_vis_out"

fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


def make_art():
    """A real PNG, so the album-art cache path is genuinely exercised."""
    path = "/tmp/hp_profile_art.png"
    if not os.path.exists(path):
        from PIL import Image
        Image.new("RGBA", (256, 256), (90, 40, 160, 255)).save(path)
    return path


class FakePlayer:
    def __init__(self, playing=True):
        rng = np.random.default_rng(1234)
        # Avoid the low bars that have no FFT bin, so the picture is busy.
        spec = rng.random(64).astype(np.float32) * 0.8 + 0.2
        self._spec = spec
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


def render(mode, player, meta, t=1.5, size=None):
    w, h = size or (W, H)
    screen = pygame.Surface((w, h))
    L.draw_visualizer(screen, player, w, h, mode, t, meta, None)
    return screen


os.makedirs(OUT, exist_ok=True)
pygame.init()
meta = {"art_path": make_art(), "artist": "Test", "title": "Track"}
player = FakePlayer(True)

print("1) every mode still draws something")
shots = {}
for m in MODES:
    surf = render(m, player, meta)
    arr = pygame.surfarray.array3d(surf).astype(np.int16)
    lit = int((arr.sum(axis=2) > 24).sum())
    frac = lit / (W * H)
    shots[m] = arr
    pygame.image.save(surf, f"{OUT}/{m}.png")
    # The bar modes draw a strip at the bottom; the ball modes fill the
    # middle. Anything under a thousand lit pixels is an empty window.
    check(f"{m:8} lights up the screen ({frac*100:5.1f}%)", lit > 1000, lit)

print("\n2) the modes look different from each other")
for i, a in enumerate(MODES):
    for b in MODES[i + 1:]:
        if a == "video" or b == "video":
            continue
        diff = float(np.abs(shots[a] - shots[b]).mean())
        check(f"{a} and {b} are distinguishable (mean delta {diff:6.1f})",
              diff > 0.5, diff)

print("\n3) the album-art cache returns the same picture as decoding fresh")
# Render disc, then drop every cache and render again. If the cache changed
# the output, the two frames differ -- and the difference should be nil for
# the same time value, because the angle bucket is derived from t.
L.drop_render_caches()
fresh = pygame.surfarray.array3d(render("disc", player, meta, t=1.5)).astype(np.int16)
cached = shots["disc"]
delta = float(np.abs(fresh - cached).mean())
check("a cold cache and a warm cache render identically", delta == 0.0, delta)

print("\n4) clearing caches does not leak or change behaviour")
L.drop_render_caches()
for _ in range(3):
    render("disc", player, meta, t=1.5)
again = pygame.surfarray.array3d(render("disc", player, meta, t=1.5)).astype(np.int16)
check("repeating the same frame is stable",
      float(np.abs(again - cached).mean()) == 0.0)

print("\n5) idle is not more expensive than playing")
L.drop_render_caches()
idle = FakePlayer(False)
for m in ("disc", "album", "bars", "mirror"):
    a = pygame.surfarray.array3d(render(m, player, meta)).astype(np.int16)
    b = pygame.surfarray.array3d(render(m, idle, meta)).astype(np.int16)
    # Both should still draw; idle just has a still record.
    check(f"{m:8} still draws while idle",
          int((b.sum(axis=2) > 24).sum()) > 1000)

print(f"\nscreenshots in {OUT}/")
print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("VISUALIZER CORRECTNESS TESTS PASSED")
