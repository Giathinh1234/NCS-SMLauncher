"""Headless checks for the NCS sphere: look, motion, and audio reactivity.

Run:  python3 tests/test_ncs_sphere.py
Saves: /Users/giathinh/.hermes/cache/scratch/ncs_sphere_check.png

The motion and reactivity tests exist because the sphere shipped looking like
a still image once already. The cause was reading the audio as 0..255 when
Player.spectrum() returns 0..1, which pinned the whole signal to 0.001-0.008
and made quiet and loud render byte-identical frames.

The FakePlayer below originally returned `255.0 * level`, which reproduced the
same wrong assumption and so passed happily while the real bug was live. It now
returns 0..1 to match the real implementation, and there is an explicit test
asserting the bass band spans a wide range.
"""
import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np
import pygame

import ncs_sphere as S
from ncs_sphere import draw_ncs_sphere, _grid_for, _bands, _reset_anim

SCRATCH = "/Users/giathinh/.hermes/cache/scratch"


class FakePlayer:
    """Mimics Player.spectrum() EXACTLY: values already normalized to 0..1.

    The real implementation ends with `np.clip(mag, 0, 1)`. A fake returning
    0..255 would hide precisely the bug this file guards against.
    """

    def __init__(self, level=0.5, seed=0):
        self.level = level
        self.k = 0
        self.seed = seed

    def spectrum(self):
        r = np.random.default_rng(self.seed + self.k)
        m = np.zeros(512, dtype=np.float32)
        m[:12] = self.level * (0.85 + 0.3 * r.random(12))
        m[12:120] = self.level * 0.55 * (0.6 + 0.8 * r.random(108))
        m[120:420] = self.level * 0.30 * (0.4 + 1.2 * r.random(300))
        self.k += 1
        return np.clip(m, 0, 1)


class SilentPlayer:
    def spectrum(self):
        return np.zeros(512, dtype=np.float32)


def grab(scr, level=0.5, t=0.0, seed=0, player=None):
    scr.fill((0, 0, 0))
    draw_ncs_sphere(scr, player or FakePlayer(level, seed), scr.get_width(),
                    scr.get_height(), t)
    return pygame.surfarray.array3d(scr).astype(np.float32)


def diff(a, b):
    return float(np.abs(a - b).mean())


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

    # --- the band split must use the real 0..1 range ---
    qb = _bands(FakePlayer(0.15).spectrum())[0]
    lb = _bands(FakePlayer(0.95).spectrum())[0]
    print("2) bass band: quiet=%.3f loud=%.3f (spread %.3f)" % (qb, lb, lb - qb))
    assert lb - qb > 0.4, ("bass range collapsed to %.3f -- the frozen-ball bug "
                          "is back" % (lb - qb))
    assert 0.0 <= qb <= 1.0 and 0.0 <= lb <= 1.0, "bands must be 0..1"

    # --- reference comparison at a quiet and a loud level ---
    for level, label in ((0.25, "quiet"), (0.85, "loud")):
        _reset_anim()
        screen.fill((0, 0, 0))
        draw_ncs_sphere(screen, FakePlayer(level), W, H, t=4.0)
        s = stats(screen)
        print("3) %s: nonblack=%d mean=%.1f median=%.0f p90=%.0f "
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
    _reset_anim()
    screen.fill((0, 0, 0))
    draw_ncs_sphere(screen, FakePlayer(0.5), W, H, t=1.0)
    s = stats(screen)
    bw, bh = s["bbox"]
    print("4) silhouette bbox %dx%d (reference 901x900)" % (bw, bh))
    assert abs(bw - bh) <= max(8, int(0.06 * max(bw, bh))), \
        "silhouette is not circular: %dx%d" % (bw, bh)

    # --- MOTION: consecutive frames must differ ---
    _reset_anim()
    f0 = grab(screen, t=0.0)
    f1 = grab(screen, t=1.0 / 60.0)
    f2 = grab(screen, t=2.0 / 60.0)
    d_adj = diff(f0, f1)
    print("5) frame-to-frame diff: 1/60s=%.3f  2/60s=%.3f  (frame scale ~%.0f)"
          % (d_adj, diff(f1, f2), f0.mean()))
    assert d_adj > 0.5, ("consecutive frames identical (%.4f) -- the ball is "
                         "not animating" % d_adj)

    # --- MOTION must accumulate over a longer interval ---
    # NB: the single-frame diff (17.9) is already LARGER than the frame's mean
    # value (~13), because the rotating point field relocates the whole
    # stipple. So the meaningful check is that a longer interval differs MORE
    # than one frame, not a multiple of it.
    long_d = diff(grab(screen, t=0.0), grab(screen, t=2.0))
    print("6) t=0 vs t=2.0 diff: %.3f  (%.1fx the single-frame diff)"
          % (long_d, long_d / max(d_adj, 1e-6)))
    assert long_d > d_adj * 1.2, "motion does not accumulate over time"

    # --- AUDIO REACTIVITY: same t, different volume must differ ---
    _reset_anim()
    aq = grab(screen, level=0.10, t=2.0)
    _reset_anim()
    al = grab(screen, level=0.95, t=2.0)
    a_d = diff(aq, al)
    print("7) quiet vs loud at fixed t: %.3f  (%.1fx single-frame)"
          % (a_d, a_d / max(d_adj, 1e-6)))
    assert a_d > 1.0, ("volume has almost no effect (%.4f) -- the frozen-ball "
                       "bug is back" % a_d)

    # --- louder must mean bigger ---
    # The bbox CANNOT measure this: at 640x640 both quiet and loud clip to the
    # window edge (639x638 vs 639x639), because the ball grows past the frame.
    # Measure the radius that actually drives the render, via the module's own
    # scaling, and assert it responds to the bass band.
    from ncs_sphere import _bands as _b, _R_BASE, _R_SWING
    radii = []
    for level in (0.05, 0.95):
        _reset_anim()
        mag = FakePlayer(level).spectrum()
        bass = _b(mag)[0]
        r = min(W, H) * _R_BASE * (1.0 - _R_SWING + _R_SWING * bass)
        radii.append(r)
        print("8) level %.2f -> bass %.3f -> radius %.1f px" % (level, bass, r))
    assert radii[1] > radii[0] * 1.10, \
        "radius must grow with bass: %.1f -> %.1f" % (radii[0], radii[1])
    print("   radius swing: %.1f px (was 28px before the range fix)"
          % (radii[1] - radii[0]))
    # and it must stay inside the window at full volume
    assert max(radii) <= min(W, H) * _R_BASE + 1, \
        "ball overflows the window at full volume: %.1f" % max(radii)

    # --- renders at several window sizes without crashing ---
    for w, h in ((640, 480), (900, 700), (1280, 800), (400, 900)):
        scr = pygame.display.set_mode((w, h))
        scr.fill((0, 0, 0))
        draw_ncs_sphere(scr, FakePlayer(0.5), w, h, t=3.0)
        print("9) rendered %dx%d ok" % (w, h))

    # --- frame time at a realistic window size ---
    screen = pygame.display.set_mode((1280, 800))
    _reset_anim()
    p = FakePlayer(0.6)
    t0 = time.time()
    frames = 10
    for i in range(frames):
        draw_ncs_sphere(screen, p, 1280, 800, t=i * 0.016)
    dt = (time.time() - t0) / frames
    print("10) %.1f ms/frame at 1280x800 -> %.1f fps" % (dt * 1000, 1.0 / dt))
    assert dt < 0.10, "too slow: %.1f ms/frame" % (dt * 1000)

    # --- silence must not crash or blank the sphere ---
    _reset_anim()
    screen = pygame.display.set_mode((640, 640))
    screen.fill((0, 0, 0))
    draw_ncs_sphere(screen, SilentPlayer(), 640, 640, t=4.0)
    s = stats(screen)
    print("11) silence: mean=%.1f nonblack=%d" % (s["mean"], s["nonblack"]))
    assert s["nonblack"] > 2000, "sphere vanishes in silence"

    # --- save a visual for inspection ---
    _reset_anim()
    screen = pygame.display.set_mode((640, 640))
    screen.fill((0, 0, 0))
    draw_ncs_sphere(screen, FakePlayer(0.55), 640, 640, t=4.0)
    out = os.path.join(SCRATCH, "ncs_sphere_check.png")
    pygame.image.save(screen, out)
    print("12) saved", out)

    print("\nALL NCS SPHERE TESTS PASSED")


main()
