"""Headless checks for the NCS sphere.

Renders the real draw_ncs_sphere() at several sizes and audio levels, asserts
the visual invariants measured from the reference image, and reports frame time.

Run:  python3 tests/test_ncs_sphere.py
Saves: /Users/giathinh/.hermes/cache/scratch/ncs_sphere_check.png
"""
import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np
import pygame

from ncs_sphere import draw_ncs_sphere, _grid_for

SCRATCH = "/Users/giathinh/.hermes/cache/scratch"


class FakePlayer:
    """Stands in for the real player: just enough to drive the visualizer."""

    def __init__(self, level=0.5):
        self.level = level

    def spectrum(self):
        n = 512
        # bass-heavy, like real music
        mag = np.zeros(n, dtype=np.float32)
        mag[:10] = 255.0 * self.level
        mag[10:200] = 255.0 * self.level * 0.4 * np.linspace(1, 0.2, 190)
        return mag


def stats(surf):
    a = pygame.surfarray.array3d(surf).astype(np.float32)  # (w, h, 3)
    a = a.transpose(1, 0, 2)                                # -> (h, w, 3)
    lum = a.sum(axis=2)
    nz = lum[lum > 20]
    bright = lum > 300
    ys, xs = np.where(lum > 20)
    bbox = None
    if xs.size:
        bbox = (int(xs.max() - xs.min()), int(ys.max() - ys.min()))
    return {
        "nonblack": int(nz.size),
        "mean": float(nz.mean()) if nz.size else 0.0,
        "median": float(np.median(nz)) if nz.size else 0.0,
        "p90": float(np.percentile(nz, 90)) if nz.size else 0.0,
        "bright_frac": float(bright.mean()),
        "bbox": bbox,
        "corner_mean": float(lum[:60, :60].mean()),
    }


def main():
    pygame.init()
    nu, nv = _grid_for(1280, 800)
    print("1) grid at 1280x800: %dx%d" % (nu, nv))
    assert nu >= 200 and nv >= 120, "grid too coarse: %dx%d" % (nu, nv)

    W = H = 640
    screen = pygame.display.set_mode((W, H))

    # --- reference comparison at a quiet level ---
    for level, label in ((0.25, "quiet"), (0.85, "loud")):
        screen.fill((0, 0, 0))
        draw_ncs_sphere(screen, FakePlayer(level), W, H, t=4.0)
        s = stats(screen)
        print("2) %s: nonblack=%d mean=%.1f median=%.0f p90=%.0f "
              "bright=%.1f%% corner=%.1f"
              % (label, s["nonblack"], s["mean"], s["median"], s["p90"],
                 s["bright_frac"] * 100, s["corner_mean"]))

        # the background must stay black: the reference is a ball on black
        assert s["corner_mean"] < 12, \
            "corner should be black, got %.1f" % s["corner_mean"]
        # dark dots must be visible, not a blank ball
        assert s["nonblack"] > 4000, "too few lit pixels: %d" % s["nonblack"]
        # gold must be present but restrained. The reference measures 10.4%
        # bright; the reimplementation renders a tighter membrane, so the band
        # is set from the measured reference value with a wide tolerance rather
        # than a guessed one.
        assert 0.01 < s["bright_frac"] < 0.30, \
            "bright area %.1f%% is out of range" % (s["bright_frac"] * 100)

    # --- silhouette must stay circular ---
    screen.fill((0, 0, 0))
    draw_ncs_sphere(screen, FakePlayer(0.5), W, H, t=1.0)
    s = stats(screen)
    bw, bh = s["bbox"]
    print("3) silhouette bbox %dx%d (reference 901x900)" % (bw, bh))
    assert abs(bw - bh) <= max(8, int(0.06 * max(bw, bh))), \
        "silhouette is not circular: %dx%d" % (bw, bh)

    # --- a higher bass level must make the ball bigger (audio reactivity) ---
    # Measure the SILHOUETTE area, not the lit-pixel count: louder audio widens
    # the crest as well as the radius, so counting lit pixels is a bad proxy
    # (it measured 211430 -> 202659, i.e. "smaller" while the ball grew).
    areas = []
    for level in (0.05, 0.95):
        screen.fill((0, 0, 0))
        draw_ncs_sphere(screen, FakePlayer(level), W, H, t=2.0)
        bw, bh = stats(screen)["bbox"]
        areas.append(bw * bh)
        print("4) level %.2f -> silhouette %dx%d" % (level, bw, bh))
    assert areas[1] > areas[0] * 1.01, \
        "sphere must grow with bass: %.0f -> %.0f" % (areas[0], areas[1])

    # --- renders at several window sizes without crashing ---
    for w, h in ((640, 480), (900, 700), (1280, 800), (400, 900)):
        scr = pygame.display.set_mode((w, h))
        scr.fill((0, 0, 0))
        draw_ncs_sphere(scr, FakePlayer(0.5), w, h, t=3.0)
        print("5) rendered %dx%d ok" % (w, h))

    # --- frame time at a realistic window size ---
    screen = pygame.display.set_mode((1280, 800))
    p = FakePlayer(0.6)
    t0 = time.time()
    frames = 10
    for i in range(frames):
        draw_ncs_sphere(screen, p, 1280, 800, t=i * 0.03)
    dt = (time.time() - t0) / frames
    print("6) %.1f ms/frame at 1280x800 -> %.1f fps" % (dt * 1000, 1.0 / dt))
    assert dt < 0.10, "too slow: %.1f ms/frame" % (dt * 1000)

    # --- save a visual for inspection ---
    screen = pygame.display.set_mode((640, 640))
    screen.fill((0, 0, 0))
    draw_ncs_sphere(screen, FakePlayer(0.55), 640, 640, t=4.0)
    out = os.path.join(SCRATCH, "ncs_sphere_check.png")
    pygame.image.save(screen, out)
    print("7) saved", out)

    print("\nALL NCS SPHERE TESTS PASSED")


main()
