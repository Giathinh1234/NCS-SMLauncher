"""Where does the NCS sphere's frame time actually go?

Measures at the real default window size with a real spectrum, and breaks the
cost down by stage so any optimisation is aimed at the actual bottleneck
rather than a guess.
"""
import os
import sys
import time
import cProfile
import pstats
import io as _io

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np
import pygame
pygame.init()
screen = pygame.display.set_mode((1280, 800))

import ncs_sphere as ns


class FakePlayer:
    def spectrum(self):
        t = time.time()
        return (0.5 + 0.4 * np.sin(np.linspace(0, 12, 64) + t * 3)).astype(np.float32)


W, H = 1280, 800
player = FakePlayer()

print("SPHERE PROFILE @ 1280x800")
print("1) grid sizing")
ns._sphere_cache.clear()
nu, nv = ns._grid_for(W, H)
t0 = time.perf_counter()
geo = ns._sphere_geometry(W, H)
cold = time.perf_counter() - t0
t0 = time.perf_counter()
geo = ns._sphere_geometry(W, H)
warm = time.perf_counter() - t0
points = geo["base"].shape[0]
print(f"   grid {nu}x{nv} -> {points} points")
print(f"   geometry build: cold {cold*1000:.1f} ms, cached {warm*1000:.3f} ms")
print(f"   SPHERE_POINTS_MAX={ns.SPHERE_POINTS_MAX} "
      f"SPHERE_PPP_TARGET={ns.SPHERE_PPP_TARGET}")

print("2) end-to-end draw_ncs_sphere")
# This machine is shared (load average was 24 while measuring, mostly other
# apps), so wall-clock MEAN is meaningless here. The MINIMUM over many frames
# is the best estimate of the real cost: contention can only ever add time.
# Both are reported so the noise is visible rather than hidden.
ns._reset_anim()
for _ in range(3):
    ns.draw_ncs_sphere(screen, player, W, H, 0.0)
N = 30
samples = []
for i in range(N):
    t0 = time.perf_counter()
    ns.draw_ncs_sphere(screen, player, W, H, float(i))
    samples.append(time.perf_counter() - t0)
samples.sort()
best = samples[0]
median = samples[len(samples) // 2]
print(f"   best  {best*1000:6.2f} ms   (the real cost; 16.67 ms = 60 FPS)")
print(f"   median{median*1000:6.2f} ms   (inflated by load, shown for honesty)")
print(f"   verdict: {'OK' if best < 0.0167 else 'TOO SLOW'} at best-case")
total = best

print("3) stage breakdown")
mag = player.spectrum()
bass, mid, high = ns._bands(mag)
dt = 1 / 60.0
t0 = time.perf_counter()
for i in range(N):
    ns._sphere_field(geo["lat"], geo["lon"], float(i), bass, mid, high, dt)
field = (time.perf_counter() - t0) / N
print(f"   _sphere_field (the wave math): {field*1000:.2f} ms")

n = W * H
t0 = time.perf_counter()
for _ in range(N):
    buf = np.zeros((H, W, 3), dtype=np.float32)
alloc = (time.perf_counter() - t0) / N
print(f"   zeroing the (h,w,3) buffer:    {alloc*1000:.2f} ms  "
      f"({buf.nbytes/1e6:.1f} MB)")

pts = min(points, 200000)
idx = np.random.randint(0, n, pts)
vals = np.random.rand(pts).astype(np.float64)
t0 = time.perf_counter()
for _ in range(N):
    _ = np.bincount(idx, weights=vals, minlength=n)
bc = (time.perf_counter() - t0) / N
print(f"   one bincount over {n} bins:    {bc*1000:.2f} ms  "
      f"({n*8/1e6:.1f} MB float64 per call)")
print(f"   x3 channels = {bc*3*1000:.2f} ms just for accumulation")

t0 = time.perf_counter()
for _ in range(N):
    pygame.surfarray.blit_array(screen, np.zeros((W, H, 3), dtype=np.uint8))
blit = (time.perf_counter() - t0) / N
print(f"   blit_array to screen:          {blit*1000:.2f} ms")

print("4) cProfile, 5 frames, top 12 by cumulative time")
pr = cProfile.Profile()
pr.enable()
for i in range(5):
    ns.draw_ncs_sphere(screen, player, W, H, float(i))
pr.disable()
s = _io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(12)
for line in s.getvalue().splitlines()[4:20]:
    print("   " + line)

print("5) render scale vs point budget, best-of-N")
print("   The accumulator is sized by the render buffer and the gathers by the")
print("   point count, so the two knobs trade against each other.")
print(f"   {'scale':>6} {'ppp':>5} {'~pts':>7} {'best ms':>9} {'verdict':>8}")
for scale in (1.0, 0.5, 0.4, 0.33):
    for ppp in (3.5, 5.0, 7.0):
        ns.SPHERE_RENDER_SCALE = scale
        ns.SPHERE_PPP_TARGET = ppp
        ns._sphere_cache.clear()
        ns._reset_anim()
        for _ in range(3):
            ns.draw_ncs_sphere(screen, player, W, H, 0.0)
        got = []
        for i in range(12):
            t0 = time.perf_counter()
            ns.draw_ncs_sphere(screen, player, W, H, float(i))
            got.append(time.perf_counter() - t0)
        per = min(got)
        g = ns._grid_for(max(2, int(W * scale)), max(2, int(H * scale)))
        npts = int(0.741 * g[0] * g[1])
        print(f"   {scale:6.2f} {ppp:5.1f} {npts:7d} {per*1000:9.2f} "
              f"{'OK' if per < 0.0167 else 'SLOW':>8}")
ns.SPHERE_RENDER_SCALE = 0.5
ns.SPHERE_PPP_TARGET = 3.5

print("SPHERE PROFILE DONE")
