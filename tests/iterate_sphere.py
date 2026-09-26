#!/usr/bin/env python3
"""Iterate on the NCS sphere look. Renders a grid of variants side by side.

Usage: python3 tests/iterate_sphere.py
Out:   /Users/giathinh/.hermes/cache/scratch/sphere_variants.png

All parameters below are MEASURED from the reference, not guessed:

  silhouette      901 x 900 px                 -> a perfect CIRCLE
  crest           RGB (255, 227, 152)
  dark dots       RGB ( 55,  45,   9)
  background      pure black
  bright area     10.4% of pixels
  DARK-REGION LUMINANCE  mean 202.9, median 155, p90 468, p99 569

  (dark-region luminance of a candidate, same crop) :
      round 6 variant A   mean 73.9  median 67  p90 129  p99 285
      TARGET              mean 202.9 median 155 p90 468 p99 569
  so the dark dots needed to be ~2.7x brighter. That is the one number this
  round exists to hit.

What each earlier round actually taught:
  r1  Fibonacci lattice  -> no line structure at all. Use a lat/lon grid.
  r2  disp_amp 0.16      -> the "sphere" became a potato. The reference
                             silhouette is a circle: keep disp_amp <= 0.04.
  r3  peak_lum 1.9 + 5x5 -> additive clipping to WHITE. One gold point must
                             be lum 1.0, and fill kernels must be small.
  r4  uniform lat/lon    -> pole convergence made two hot spots. Give each
                             row cos(lat) points.
  r5  36k points         -> 26.6 px/pt, the membrane could only ever be
                             speckle. 900x570 = 2.9 px/pt fills smoothly.
  r6  no density norm    -> 3.4 points/px accumulated additively and turned
                             the interior olive. Normalise by 1/points-per-px.
  r7  rim on screen radius -> displacement smeared the band and it ate the
                             ball. The silhouette of the UNIT sphere is the
                             z == 0 circle, so anchor the rim there.
"""
import os, sys, math, time
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np
import pygame

SCRATCH = "/Users/giathinh/.hermes/cache/scratch"
REF = "/Users/giathinh/Downloads/ExwK_GkWEAENp4e.jpg"
TILE = 400
COLS, ROWS = 4, 2

CREST = (255.0, 227.0, 152.0)
DARK = (55.0, 45.0, 9.0)
DOT = ((0, 0),)
K3 = ((-1, 0, 0.55), (0, 0, 1.0), (1, 0, 0.55),
      (0, -1, 0.55), (0, 1, 0.55),
      (-1, -1, 0.22), (1, -1, 0.22), (-1, 1, 0.22), (1, 1, 0.22))


def latlon_uniform(nu, nv, jitter=0.30, seed=7):
    rng = np.random.default_rng(seed)
    lat = np.linspace(-np.pi / 2 + 0.004, np.pi / 2 - 0.004, nv)
    xs, ys, zs = [], [], []
    for la in lat:
        n_row = max(3, int(round(nu * math.cos(la))))
        lon = (np.arange(n_row) + rng.random() * 0.5) * (2 * np.pi / n_row)
        xs.append(np.cos(la) * np.cos(lon))
        ys.append(np.full(n_row, math.sin(la)))
        zs.append(np.cos(la) * np.sin(lon))
    p = np.stack([np.concatenate(xs), np.concatenate(ys),
                  np.concatenate(zs)], axis=1).astype(np.float32)
    p += rng.normal(0, jitter / 100.0, p.shape).astype(np.float32)
    p /= np.linalg.norm(p, axis=1, keepdims=True)
    return p


def splat(w, h, sx, sy, R, G, B, offsets=(), weight=1.0):
    n = w * h
    idxs, Rs, Gs, Bs = [], [], [], []
    for dx, dy in offsets:
        xx, yy = sx + dx, sy + dy
        m = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h)
        if not m.any():
            continue
        idxs.append(yy[m] * w + xx[m])
        Rs.append(R[m] * weight); Gs.append(G[m] * weight)
        Bs.append(B[m] * weight)
    if not idxs:
        return np.zeros((h, w, 3), np.float32)
    idx = np.concatenate(idxs)
    return np.stack([
        np.bincount(idx, weights=np.concatenate(Rs), minlength=n),
        np.bincount(idx, weights=np.concatenate(Gs), minlength=n),
        np.bincount(idx, weights=np.concatenate(Bs), minlength=n),
    ], axis=-1).reshape(h, w, 3)


def render(base, w, h, t, *, field_fn, disp_amp=0.035, persp_fov=3.0,
           rim_w=0.055, rim_boost=0.45, dark_floor=1.0, peak=1.0,
           ridge_lo=0.72, ridge_pow=2.6, fill_w=0.38, fill_lo=0.30,
           spin=0.0, tilt=0.35, depth_dim=0.18, sat=0.9, norm_pow=0.55):
    surf = pygame.Surface((w, h))
    cx = cy = w // 2
    radius = min(w, h) * 0.44

    bx, by, bz = base[:, 0], base[:, 1], base[:, 2]
    la = np.arcsin(np.clip(by, -1, 1))
    lo = np.arctan2(bz, bx)

    ang = t * spin
    ca, sa = math.cos(ang), math.sin(ang)
    ct, st = math.cos(tilt), math.sin(tilt)
    x = bx * ca + bz * sa
    z0 = -bx * sa + bz * ca
    y = by * ct - z0 * st
    z = by * st + z0 * ct

    rim = np.exp(-((z0 / rim_w) ** 2))
    f = field_fn(la, lo, t, bx, by, bz)
    disp = 1.0 + disp_amp * f
    persp = persp_fov / (persp_fov - z * disp)
    sx = (cx + x * disp * radius * persp).astype(np.int32)
    sy = (cy - y * disp * radius * persp).astype(np.int32)

    f01 = np.clip((f - f.min()) / max(1e-6, (f.max() - f.min())), 0, 1)
    ridge = np.clip((f01 - ridge_lo) / max(1e-6, 1 - ridge_lo), 0, 1) ** ridge_pow

    # The rim and the membrane are INDEPENDENT effects. Applying the rim to the
    # whole shade term meant a point both near the limb AND in a ridge got lit
    # twice, and the limb swallowed the membrane. Suppress the rim where the
    # membrane is already bright so the two cannot stack into a white blob.
    rim_light = rim_boost * rim * (1.0 - 0.85 * ridge)
    shade = (1.0 - depth_dim * np.clip(z, 0, 1)) * (1.0 + rim_light)
    gold = ridge * peak
    R = (CREST[0] * gold + DARK[0] * (1 - gold) * dark_floor) * shade
    G = (CREST[1] * gold + DARK[1] * (1 - gold) * dark_floor) * shade
    B = (CREST[2] * gold + DARK[2] * (1 - gold) * dark_floor) * shade

    # density normalisation. norm_pow < 1 lets the dots come back up without
    # letting the whole interior bloom (which is what pow 1.0 over-corrected).
    k = 1.0 / max(1.0, len(base) / (math.pi * radius * radius)) ** norm_pow
    R, G, B = R * k, G * k, B * k

    buf = splat(w, h, sx, sy, R, G, B, DOT)
    solid = (ridge > fill_lo) | (rim > 0.6)
    if solid.any():
        bR = np.where(solid, R, 0.0) * fill_w
        bG = np.where(solid, G, 0.0) * fill_w
        bB = np.where(solid, B, 0.0) * fill_w
        for dx, dy, wt in K3:
            buf += splat(w, h, sx, sy, bR, bG, bB, ((dx, dy),), wt)

    img = np.clip(buf, 0, 255)
    lum = img.max(axis=2, keepdims=True)
    img = np.clip(lum * (1 - sat) + img * sat, 0, 255)
    surf.blit(pygame.surfarray.make_surface(img.astype(np.uint8)), (0, 0))
    return surf


def f_drape(la, lo, t, bx, by, bz):
    w = 2.4 * np.sin(1.1 * la + 0.45 * t) + 1.0 * np.sin(1.4 * lo - 0.5 * t)
    return 0.85 * np.sin(0.75 * lo + 0.95 * la + w + 0.6 * t) + \
           0.15 * np.cos(2.1 * la - 1.2 * lo - 0.4 * t)


def f_flow(la, lo, t, bx, by, bz):
    w1 = 2.2 * np.sin(1.0 * la + 0.45 * t)
    w2 = 1.8 * np.sin(1.2 * lo - 0.5 * t)
    return 0.62 * np.sin(0.8 * lo + 1.0 * la + w1 + 0.6 * t) + \
           0.38 * np.sin(0.9 * lo - 1.1 * la + w2 - 0.5 * t)


def f_flow2(la, lo, t, bx, by, bz):
    w1 = 2.4 * np.sin(1.0 * la + 0.45 * t)
    w2 = 2.0 * np.sin(1.2 * lo - 0.5 * t)
    return 0.58 * np.sin(0.8 * lo + 1.0 * la + w1 + 0.6 * t) + \
           0.34 * np.sin(0.9 * lo - 1.1 * la + w2 - 0.5 * t) + \
           0.08 * np.sin(3.0 * la + 2.0 * lo + 0.3 * t)


def f_curtain(la, lo, t, bx, by, bz):
    """Soft top-lit curtains, plus a little topo texture. Ref-like."""
    w = 2.5 * np.sin(1.0 * la + 0.45 * t) + 1.0 * np.sin(1.3 * lo - 0.5 * t)
    return 0.80 * np.sin(0.8 * lo + 0.95 * la + w + 0.6 * t) + \
           0.12 * np.sin(6.0 * la + 4.0 * lo + 0.4 * t) + \
           0.08 * np.cos(1.8 * la - 1.2 * lo - 0.4 * t)


def f_wide(la, lo, t, bx, by, bz):
    """Broader, smoother sheets that cross the visible face."""
    w = 1.8 * np.sin(0.9 * la + 0.45 * t) + 0.8 * np.sin(1.1 * lo - 0.5 * t)
    return 0.88 * np.sin(0.55 * lo + 0.70 * la + w + 0.6 * t) + \
           0.12 * np.cos(1.5 * la - 0.8 * lo - 0.4 * t)


def f_bands(la, lo, t, bx, by, bz):
    """Long diagonal bands sweeping across the whole ball."""
    w = 1.6 * np.sin(0.8 * la + 0.4 * t)
    return 0.75 * np.sin(0.5 * lo + 1.6 * la + w + 0.6 * t) + \
           0.25 * np.sin(0.4 * lo - 1.4 * la - 0.5 * t)


def main():
    pygame.init()
    screen = pygame.display.set_mode((TILE * COLS, TILE * ROWS))
    screen.fill((0, 0, 0))
    font = pygame.font.SysFont("consolas", 12, bold=True)

    ref = pygame.transform.smoothscale(pygame.image.load(REF), (TILE, TILE))
    screen.blit(ref, (0, 0))
    screen.blit(font.render("REFERENCE", True, (120, 230, 255)), (5, 5))

    g = latlon_uniform(900, 570)
    # norm_pow < 1 brings the dark dots back up toward the measured target
    variants = [
        ("A drape  r.72 p2.6", dict(field_fn=f_drape)),
        ("B flow   r.72 p2.6", dict(field_fn=f_flow)),
        ("C wide   r.72 p2.6", dict(field_fn=f_wide)),
        ("D bands  r.72 p2.6", dict(field_fn=f_bands)),
        ("E bands  r.66 p2.0", dict(field_fn=f_bands, ridge_lo=0.66,
                                    ridge_pow=2.0)),
        ("F bands  r.78 p3.2", dict(field_fn=f_bands, ridge_lo=0.78,
                                    ridge_pow=3.2)),
        ("G curtain r.70 p2.4", dict(field_fn=f_curtain, ridge_lo=0.70,
                                      ridge_pow=2.4)),
        ("H bands  r.72 spin.3", dict(field_fn=f_bands, spin=0.30)),
    ]
    for i, (name, kw) in enumerate(variants):
        slot = i + 1
        col, row = slot % COLS, slot // COLS
        t1 = time.time()
        screen.blit(render(g, TILE, TILE, 4.0, **kw), (col * TILE, row * TILE))
        screen.blit(font.render(name, True, (120, 230, 255)),
                    (col * TILE + 5, row * TILE + 5))
        print("rendered %-18s %.3fs" % (name, time.time() - t1))

    out = os.path.join(SCRATCH, "sphere_variants.png")
    pygame.image.save(screen, out)
    print("saved", out)


main()
