"""_splat must be exactly the full-window accumulation, whatever the extent.

The bounding-box accumulator is an optimization, so it has to be bit-for-bit
equivalent to the naive version. The first version of this test used random
points that happened to span the full width of the window, which hid a real
stride bug: a bbox narrower than the window has its own row-major stride, and
subtracting a linear origin instead of re-indexing produced garbage. Real
point clouds -- a sphere -- do NOT span the window, so the test uses circular
and clustered sets too.
"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np
import ncs_sphere as ns


def reference(w, h, sx, sy, r, g, b, kernel):
    """The obvious implementation: accumulate into the whole window."""
    ref = np.zeros((h, w, 3), dtype=np.float32)
    for off in kernel:
        dx, dy = off[0], off[1]
        wt = off[2] if len(off) == 3 else 1.0
        xx = sx + dx
        yy = sy + dy
        m = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h)
        np.add.at(ref[..., 0], (yy[m], xx[m]), r[m] * wt)
        np.add.at(ref[..., 1], (yy[m], xx[m]), g[m] * wt)
        np.add.at(ref[..., 2], (yy[m], xx[m]), b[m] * wt)
    return ref


rng = np.random.default_rng(4)
fails = 0
cases = 0

print("_splat equivalence with full-window accumulation")

point_sets = {}

# 1) dense, spans the whole window (the case that hid the stride bug)
n = 400
point_sets["full width"] = (
    rng.integers(0, 200, n).astype(np.int32),
    rng.integers(0, 150, n).astype(np.int32),
    200, 150)

# 2) a circle, like the sphere: strictly narrower than the window
n = 600
ang = rng.random(n) * 2 * np.pi
rad = 40 * np.sqrt(rng.random(n))
point_sets["circle, bbox < window"] = (
    (100 + rad * np.cos(ang)).astype(np.int32),
    (75 + rad * np.sin(ang)).astype(np.int32),
    200, 150)

# 3) a small cluster in one corner
n = 250
point_sets["corner cluster"] = (
    rng.integers(2, 18, n).astype(np.int32),
    rng.integers(3, 15, n).astype(np.int32),
    200, 150)

# 4) points clipped hard at every edge, bbox offset from the origin
n = 300
point_sets["edge-clipped"] = (
    rng.integers(-8, 12, n).astype(np.int32),
    rng.integers(95, 160, n).astype(np.int32),
    200, 150)

# 5) a single point
point_sets["single point"] = (np.array([7], np.int32), np.array([9], np.int32),
                              40, 30)

# 6) everything off-screen -> no contribution at all
point_sets["all off-screen"] = (np.array([-50, -20, 500], np.int32),
                                np.array([-50, 900, 3], np.int32), 40, 30)

for label, (sx, sy, w, h) in point_sets.items():
    n_pts = len(sx)
    r = (rng.random(n_pts) * 60).astype(np.float32)
    g = (rng.random(n_pts) * 60).astype(np.float32)
    b = (rng.random(n_pts) * 60).astype(np.float32)
    for kname, kernel in (("1-tap", ns._DOT), ("9-tap", ns._FILL3)):
        got = ns._splat(w, h, sx, sy, r, g, b, kernel)
        ref = reference(w, h, sx, sy, r, g, b, kernel)
        err = float(np.abs(got - ref).max())
        inside = ((sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)).sum()
        strict = inside > 0 and (sx.min() >= 0 or sx.max() < w - 1)
        cases += 1
        ok = err < 1e-3
        if not ok:
            fails += 1
        print(f"  {'ok ' if ok else 'FAIL'} {label:24} {kname:6} "
              f"max err {err:.2e}  {w}x{h}")
    # a second call must ACCUMULATE into the same buffer, not reset it
    out = ns._splat(w, h, sx, sy, r, g, b, ns._DOT)
    one = out.copy()
    ns._splat(w, h, sx, sy, r, g, b, ns._DOT, out=out)
    d = float(np.abs(out - one * 2).max())
    if d > 1e-3:
        print(f"  FAIL accumulate-into-existing {label}: max err {d:.2e}")
        fails += 1

print(f"{cases} comparisons, {fails} failures")
assert fails == 0, f"{fails} mismatches"
print("SPLAT EQUIVALENCE PASSED")
