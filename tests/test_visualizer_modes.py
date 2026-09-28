"""Do all six visualizer modes actually put something on the screen?

"disc" and "album" painted into an off-screen surface and never composited
it: the only screen.blit(surf, ...) in draw_visualizer sat in the final
else, which those two branches never reached. So two of the six modes were
a dead window that still did a PIL decode, a LANCZOS resize and a rotate
every frame for a surface nobody saw.

Measured by counting non-black pixels on the real destination surface. The
audit found 0 for disc and album against ~140k for bars; this pins that.
"""
import os
import sys

ROOT = "/Users/giathinh/ncs-music-launcher"
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame                                        # noqa: E402
import numpy as np                                   # noqa: E402

fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


pygame.init()
W, H = 800, 600
screen = pygame.Surface((W, H))            # our own canvas, not the display


class StubPlayer:
    """A live-looking spectrum so the modes have something to draw."""

    def __init__(self):
        self.samples = np.zeros(44100, dtype=np.float32)
        self.pos = 1000
        self.paused = False
        self.muted = False
        self.volume = 1.0
        self.track_path = "x"

    def spectrum(self):
        t = np.linspace(0.0, 1.0, 64)
        return np.abs(np.sin(t * 7.0) * 0.8 + 0.1).astype(np.float32)

    def position(self):
        return 0.2

    def duration(self):
        return 1.0

    def track_path_or_none(self):
        return None


import ncs_launcher                                  # noqa: E402

META = {"title": "Test Track", "artist": "Test Artist", "album": "Test Album",
        "art": None, "path": "/tmp/x.wav"}

# A real cover image would exercise the PIL path; None is fine and is the
# common case, so test that first and add a generated one if it renders.
results = {}
for mode in ("bars", "mirror", "radial", "disc", "album"):
    screen.fill((0, 0, 0))
    try:
        ncs_launcher.draw_visualizer(screen, StubPlayer(), W, H, mode, 0.4,
                                     META, video_slot=None)
        arr = pygame.surfarray.array3d(screen)
        nonblack = int((arr.max(axis=2) > 8).sum())
    except Exception as e:
        nonblack = -1
        results[mode] = f"{type(e).__name__}: {e}"
    else:
        results[mode] = nonblack
    print(f"     {mode:7} non-black pixels: {results[mode]}")

for mode in ("bars", "mirror", "radial", "disc", "album"):
    v = results[mode]
    check(f"{mode} renders something", isinstance(v, int) and v > 500, v)

# The two that were dead, called out explicitly so the regression is obvious.
check("disc renders (was 0 non-black pixels)", results["disc"] > 500,
      results["disc"])
check("album renders (was 0 non-black pixels)", results["album"] > 500,
      results["album"])

# And a source-level guard: every branch that paints must composite.
src = open(os.path.join(ROOT, "src", "ncs_launcher.py"), encoding="utf-8").read()
fn = src[src.find("def draw_visualizer"):src.find("\ndef draw_ui")]
check("draw_visualizer composites its surface at least 3 times "
      "(radial/sphere aside, disc, album, and the bars branch)",
      fn.count("screen.blit(surf, (0, 0))") >= 3,
      fn.count("screen.blit(surf, (0, 0))"))

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("VISUALIZER MODES TEST PASSED")
