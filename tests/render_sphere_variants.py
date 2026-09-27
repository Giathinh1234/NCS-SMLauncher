"""Render the sphere at several internal scales so they can be compared.

The frame budget forces a choice between internal resolution (cost) and point
density (how close the ball looks to the reference). This writes the images so
the decision is made by looking, not by guessing from a number.
"""
import os
import sys
import time

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np
import pygame
pygame.init()
screen = pygame.display.set_mode((1280, 800))

import ncs_sphere as ns

OUT = "/Users/giathinh/.hermes/cache/scratch"
os.makedirs(OUT, exist_ok=True)


class FakePlayer:
    """A fixed, lively spectrum so every variant draws the same wave state."""
    def spectrum(self):
        return (0.45 + 0.40 * np.sin(np.linspace(0, 11, 64))).astype(np.float32)


W, H = 1280, 800
player = FakePlayer()

print("SPHERE VISUAL COMPARISON")
print(f"window {W}x{H}")
for scale, ppp in ((1.0, 3.5), (1.0, 5.0), (1.0, 7.0), (0.5, 3.5), (0.5, 2.0)):
    ns.SPHERE_RENDER_SCALE = scale
    ns.SPHERE_PPP_TARGET = ppp
    ns._sphere_cache.clear()
    ns._reset_anim()
    # step to a fixed, non-trivial point in the animation
    for i in range(37):
        ns.draw_ncs_sphere(screen, player, W, H, float(i))

    got = []
    for i in range(12):
        t0 = time.perf_counter()
        ns.draw_ncs_sphere(screen, player, W, H, float(i))
        got.append(time.perf_counter() - t0)
    per = min(got)

    g = ns._grid_for(max(2, int(W * scale)), max(2, int(H * scale)))
    npts = int(0.741 * g[0] * g[1])
    tag = f"scale{scale}_ppp{ppp}".replace(".", "")
    path = os.path.join(OUT, f"sphere_{tag}.png")
    pygame.image.save(screen, path)

    arr = pygame.surfarray.array3d(screen)
    lit = int((arr.max(axis=2) > 24).sum())
    print(f"  {path}")
    print(f"      scale {scale}  ppp {ppp}  ~{npts} pts  best {per*1000:.2f} ms"
          f"  {'OK' if per < 0.0167 else 'SLOW'}  lit px {lit}")

print("COMPARISON DONE")
