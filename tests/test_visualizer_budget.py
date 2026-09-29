"""Pin the visualizer's frame cost, so the optimisation cannot silently undo.

Numbers here are budgets, not targets: they are the 60 FPS budget (16.7 ms)
with headroom, and they were set from measured post-fix costs with a wide
margin so ordinary machine-to-machine variation does not make this flaky.

The failure this guards against is real. Before the work, mirror cost 21 ms
per frame and disc 17 ms -- over budget, so the visualizer was the main loop's
bottleneck, and idle cost exactly as much as playing because the album art was
decoded and rotated from scratch 60 times a second. Any of that can come back
quietly, and nothing else in the suite would notice.
"""
import gc
import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np                                    # noqa: E402
import pygame                                         # noqa: E402
import ncs_launcher as L                              # noqa: E402

W, H = 1280, 748
FRAME_BUDGET_MS = 16.7

# Per-mode ceilings. Measured after the fix: bars 1.9, mirror 1.5, disc 2.3,
# album 3.1, radial 12.3. Before it: bars 13.9, mirror 21.2, disc 17.4,
# album 10.0. The NCS sphere is the point of the app and is
# allowed the most; these are ~2x measured for margin, and everything non-ball
# is budgeted against the 60 FPS frame time.
BUDGETS_MS = {
    "bars": 4.0, "mirror": 4.0, "disc": 5.0, "album": 6.0, "radial": 16.0,
}

fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


def make_art():
    path = "/tmp/hp_perf_art.png"
    if not os.path.exists(path):
        from PIL import Image
        Image.new("RGBA", (256, 256), (90, 40, 160, 255)).save(path)
    return path


class FakePlayer:
    def __init__(self, playing=True):
        rng = np.random.default_rng(99)
        self._spec = (rng.random(64).astype(np.float32) * 0.8 + 0.2)
        self.track_path = "/tmp/fake.mp3" if playing else None
        self.paused = not playing

    def spectrum(self, n=64):
        return self._spec[:n]

    def position(self):
        return 12.0

    def duration(self):
        return 180.0


def cost_ms(mode, player, meta, frames=60, repeats=4):
    """min-of-N. Single-shot timing swung by 3 ms/frame here, which is larger
    than several of these budgets."""
    screen = pygame.Surface((W, H))
    for _ in range(12):
        L.draw_visualizer(screen, player, W, H, mode, 1.0, meta, None)
    best = None
    for _ in range(repeats):
        gc.collect()
        t0 = time.process_time()
        for i in range(frames):
            L.draw_visualizer(screen, player, W, H, mode, i * 0.016, meta, None)
        c = (time.process_time() - t0) / frames * 1000
        best = c if best is None else min(best, c)
    return best


pygame.init()
meta = {"art_path": make_art(), "artist": "T", "title": "T"}
playing = FakePlayer(True)
idle = FakePlayer(False)

# One retry before declaring a budget blown. Running the full suite means
# this file runs right after the sphere and video tests, and the machine is
# not idle; a single-shot measurement there failed a check that passes
# reliably on its own. A performance test that fails intermittently is worse
# than no test, because it teaches people to re-run it until it goes green.
# The numbers are not loosened -- a genuinely slow frame fails both times.
ATTEMPT = {"n": 0}


def measure_all():
    rows = {}
    for mode, budget in BUDGETS_MS.items():
        L.drop_render_caches()
        p_cost = cost_ms(mode, playing, meta)
        L.drop_render_caches()
        i_cost = cost_ms(mode, idle, meta)
        rows[mode] = (p_cost, i_cost, budget)
    return rows


def budgets_hold(rows):
    return all(p < b and i < b for p, i, b in rows.values())


print("VISUALIZER BUDGETS  (60 FPS = 16.7 ms/frame)")
print(f"{'mode':10} {'playing':>9} {'idle':>9} {'budget':>9}")
print("-" * 40)
_rows = measure_all()
if not budgets_hold(_rows):
    print("  (first pass over budget -- remeasuring; the machine may be busy)")
    _rows = measure_all()
for mode, (p, i, budget) in _rows.items():
    print(f"{mode:10} {p:8.2f}ms {i:8.2f}ms {budget:8.2f}ms")
    check(f"{mode:8} playing stays under {budget} ms", p < budget, f"{p:.2f} ms")
    check(f"{mode:8} idle stays under {budget} ms", i < budget, f"{i:.2f} ms")

L.drop_render_caches()
print("\nTHE POINT OF THE WORK: idle must not cost more than playing")
for mode in ("disc", "album", "bars", "mirror"):
    L.drop_render_caches()
    p = cost_ms(mode, playing, meta)
    L.drop_render_caches()
    i = cost_ms(mode, idle, meta)
    # Idle may be a little different, but it was previously *equal* to playing
    # (16.5 vs 17.4 ms for disc) because the art pipeline ran regardless.
    check(f"{mode:8} idle is not pricier than playing ({i:.2f} vs {p:.2f})",
          i <= p * 1.15 + 0.2, f"idle {i:.2f} vs playing {p:.2f}")

print("\nTHE OTHER POINT: album art must not be re-decoded every frame")
# Measured the wrong way round at first: cost_ms() runs 12 warmup frames
# before timing, so a "cold" measurement had already filled the cache and the
# check came out backwards. This measures the genuinely first frame -- cache
# cleared, no warmup -- against steady state.
screen = pygame.Surface((W, H))
L.drop_render_caches()
gc.collect()
t0 = time.process_time()
L.draw_visualizer(screen, playing, W, H, "disc", 1.0, meta, None)
first = (time.process_time() - t0) * 1000

gc.collect()
t0 = time.process_time()
for i in range(40):
    L.draw_visualizer(screen, playing, W, H, "disc", 1.0 + i * 0.016, meta, None)
steady = (time.process_time() - t0) / 40 * 1000

# The first frame pays a PNG decode plus a LANCZOS resize; every frame after
# it should be far cheaper. If the cache silently stopped working, the two
# would converge.
print(f"   first frame {first:.2f} ms, steady state {steady:.2f} ms")
check(f"the first frame is dearer than steady state ({first:.2f} > {steady:.2f})",
      first > steady * 1.3, f"first {first:.2f} steady {steady:.2f}")
check("steady state is not paying for a decode", steady < first * 0.8,
      f"first {first:.2f} steady {steady:.2f}")

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("VISUALIZER BUDGET TESTS PASSED")
