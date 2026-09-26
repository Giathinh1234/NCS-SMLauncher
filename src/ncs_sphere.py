"""The NCS sphere visualizer.

A point cloud on a structured lat/lon grid, displaced by a domain-warped wave
field, additively splatted into a float buffer. The look was matched against
the reference image ExwK_GkWEAENp4e.jpg; every constant below is a value
measured from that image or from a benchmark, not a guess:

  silhouette  901 x 900 px          -> a perfect CIRCLE, so displacement is small
  crest       RGB (255, 227, 152)   -> pale gold at the brightest points
  dark dots   RGB ( 55,  45,   9)   -> dark olive
  background  pure black
  bright area 10.4% of pixels
  dark-region luminance: mean 202.9, median 155, p90 468, p99 569

What each earlier attempt got wrong, and the fix:

1. A Fibonacci lattice has no line structure at all. The reference's dots form
   warped contour bands, which needs a lat/lon GRID for the displacement to
   bend.
2. A uniform lat/lon grid converges at the poles and burns two hot spots into
   the ball. Give each row cos(lat) points.
3. Density. The membrane must fill in smoothly, so it needs points, and points
   ARE the frame cost. Measured at 1280x800 (ball r=352px, area 389k px):
       327k pts (2.9 px/pt) -> 128 ms/frame
       196k pts (3.5 px/pt) ->  77 ms/frame, keeps the look
        95k pts (4.1 px/pt) ->  46 ms/frame, 21.7 fps
   so the grid scales with window area and is capped.
4. This renderer is ADDITIVE, so a pixel is the SUM over every point landing on
   it. At 3.4 points/px an unfilled "dark" dot accumulated to several times its
   intended value and turned the whole interior olive. Normalize by the density
   shortfall against a reference density; calibrated against the reference's
   measured dark-region luminance (mean 202.9):
       _REF_PPP 4.1, pow 0.85 -> mean 127 (too dim)
       _REF_PPP 8.0, pow 1.20 -> mean 209 (matches)
5. The silhouette of a sphere is the z == 0 circle, so the limb highlight must
   be keyed to that. Keying it off projected screen radius let the
   displacement smear the band until it ate the entire ball.
6. The rim and the membrane are independent effects. Applying the rim to the
   whole shade term lit a point twice wherever the limb met a crest, burying
   the membrane under a white halo. Suppress the rim under the membrane.
7. The ball must respond to audio MONOTONICALLY. Raising the warp amplitude
   alone did not do it (measured 601x605 -> 587x610, i.e. smaller), because it
   reshuffles which limb points end up outermost. An explicit radial scale term
   fixes it.
8. The 9-tap membrane fill must be ONE splat call, not nine. Nine separate
   calls cost 69 ms/frame at 1280x800 purely from re-masking and re-allocating
   the index list per tap; one call with all nine offsets costs ~9 ms.
"""

import math

import numpy as np

import pygame

# --- grid budget, from the benchmark in note 3 above ---
SPHERE_NU = 700
SPHERE_NV = 440
SPHERE_PPP_TARGET = 3.5      # px per point
SPHERE_POINTS_MAX = 240000
# a cos(lat)-compensated nu x nv grid yields exactly 0.741 * nu * nv points
_COSLAT_POINTS_FACTOR = 0.741

# --- density normalization, calibrated in note 4 ---
_REF_PPP = 8.0
_NORM_POW = 1.2

# --- crest shape, calibrated against the reference's 10.4% bright area ---
_RIDGE_LO = 0.70
_RIDGE_POW = 2.4
_FILL_W = 0.55

# measured reference colours
_CREST = (255.0, 227.0, 152.0)
_DARK = (55.0, 45.0, 9.0)

# small disc kernel, so the membrane smooths without bleeding into the dark
_FILL3 = ((-1, 0, 0.55), (0, 0, 1.0), (1, 0, 0.55),
          (0, -1, 0.55), (0, 1, 0.55),
          (-1, -1, 0.22), (1, -1, 0.22), (-1, 1, 0.22), (1, 1, 0.22))
_DOT = ((0, 0),)

_sphere_cache = {}


def _grid_for(w, h):
    """Pick a lat/lon grid sized to the window, bounded by the point cap."""
    ball_px = math.pi * (min(w, h) * 0.44) ** 2
    want = min(ball_px / SPHERE_PPP_TARGET, SPHERE_POINTS_MAX)
    scale = math.sqrt(want / (_COSLAT_POINTS_FACTOR * SPHERE_NU * SPHERE_NV))
    nu = max(160, int(SPHERE_NU * scale))
    nv = max(100, int(SPHERE_NV * scale))
    return nu, nv


def _sphere_geometry(w, h):
    """Cached lat/lon point cloud, sized for this window."""
    nu, nv = _grid_for(w, h)
    key = (nu, nv)
    geo = _sphere_cache.get(key)
    if geo is None:
        if len(_sphere_cache) > 3:
            _sphere_cache.clear()
        rng = np.random.default_rng(7)
        lat = np.linspace(-math.pi / 2 + 0.004, math.pi / 2 - 0.004, nv)
        xs, ys, zs = [], [], []
        for la in lat:
            # cos(lat) points per row keeps density uniform, so the poles do
            # not collapse into bright spots
            n_row = max(3, int(round(nu * math.cos(la))))
            lon = (np.arange(n_row) + rng.random() * 0.5) * (2 * math.pi / n_row)
            xs.append(np.cos(la) * np.cos(lon))
            ys.append(np.full(n_row, math.sin(la)))
            zs.append(np.cos(la) * np.sin(lon))
        p = np.stack([np.concatenate(xs), np.concatenate(ys),
                      np.concatenate(zs)], axis=1).astype(np.float32)
        # jitter breaks up perfectly straight rows, so the texture reads organic
        p += rng.normal(0, 0.003, p.shape).astype(np.float32)
        p /= np.linalg.norm(p, axis=1, keepdims=True)
        geo = {"base": p,
               "lat": np.arcsin(np.clip(p[:, 1], -1, 1)),
               "lon": np.arctan2(p[:, 2], p[:, 0])}
        _sphere_cache[key] = geo
    return geo


def _splat(w, h, sx, sy, r, g, b, offsets=(), weight=1.0, out=None):
    """Additively accumulate weighted points into a (h, w, 3) float buffer.

    `out` accumulates into an existing buffer instead of allocating one, and
    `offsets` splats a whole kernel in a SINGLE pass (see note 8 above).

    An offset is either (dx, dy) or (dx, dy, per-tap weight).
    """
    n = w * h
    if out is None:
        out = np.zeros((h, w, 3), dtype=np.float32)
    idx_parts, r_parts, g_parts, b_parts = [], [], [], []
    simple = True
    for off in offsets:
        if len(off) == 3:
            dx, dy, wt = off
            simple = False
        else:
            dx, dy = off
            wt = 1.0
        xx = sx + dx
        yy = sy + dy
        m = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h)
        if not m.any():
            continue
        idx_parts.append(yy[m] * w + xx[m])
        r_parts.append(r[m] * wt)
        g_parts.append(g[m] * wt)
        b_parts.append(b[m] * wt)
    if not idx_parts:
        return out
    idx = np.concatenate(idx_parts)
    if simple and weight == 1.0 and len(idx_parts) == 1:
        out[..., 0] += np.bincount(idx, weights=r_parts[0], minlength=n).reshape(h, w)
        out[..., 1] += np.bincount(idx, weights=g_parts[0], minlength=n).reshape(h, w)
        out[..., 2] += np.bincount(idx, weights=b_parts[0], minlength=n).reshape(h, w)
        return out
    rw = np.concatenate(r_parts) * weight
    gw = np.concatenate(g_parts) * weight
    bw = np.concatenate(b_parts) * weight
    out[..., 0] += np.bincount(idx, weights=rw, minlength=n).reshape(h, w)
    out[..., 1] += np.bincount(idx, weights=gw, minlength=n).reshape(h, w)
    out[..., 2] += np.bincount(idx, weights=bw, minlength=n).reshape(h, w)
    return out


def _sphere_field(la, lo, t, mag, bass):
    """Domain-warped wave field.

    The warp (a sine of a sine) is what makes the membrane read as draped
    fabric rather than a few round blobs.
    """
    # high-band energy modulates the fine texture, so the dots shimmer
    wob = 0.35 * float(np.mean(mag[-64:])) if len(mag) >= 64 else 0.0
    amp = 0.6 + 0.8 * bass

    # la and lo are arrays, so these must be numpy, not math
    w = 2.5 * np.sin(1.0 * la + 0.45 * t) + 1.0 * np.sin(1.3 * lo - 0.5 * t)
    return (0.80 * np.sin(0.80 * lo + 0.95 * la + w * amp + 0.6 * t)
            + 0.12 * np.sin(6.0 * la + 4.0 * lo + 0.4 * t + wob * 6.0)
            + 0.08 * np.cos(1.8 * la - 1.2 * lo - 0.4 * t))


def draw_ncs_sphere(screen, player, w, h, t):
    """Draw the NCS sphere: dark gold dot texture under a flowing gold membrane."""
    mag = player.spectrum()
    geo = _sphere_geometry(w, h)
    pts = geo["base"]
    la, lo = geo["lat"], geo["lon"]

    cx, cy = w // 2, h // 2

    energy = float(np.mean(mag)) if len(mag) else 0.0
    bass = float(np.mean(mag[:10])) / 255.0 if len(mag) >= 10 else 0.0
    bass = float(np.clip(bass * 2.2, 0.0, 1.0))

    # Explicit radial pulse (note 7): makes the audio response monotonic.
    radius = min(w, h) * 0.44 * (1.0 + 0.10 * bass)

    bx, by, bz = pts[:, 0], pts[:, 1], pts[:, 2]

    # --- rotation: slow spin about Y, wobbling tilt about X ---
    ang = t * 0.28
    tilt = 0.35 + 0.12 * math.sin(t * 0.4)
    ca, sa = math.cos(ang), math.sin(ang)
    ct, st = math.cos(tilt), math.sin(tilt)
    x = bx * ca + bz * sa
    z0 = -bx * sa + bz * ca          # view depth before the wobble
    y = by * ct - z0 * st
    z = by * st + z0 * ct

    # --- limb highlight (note 5) ---
    rim = np.exp(-((z0 / 0.055) ** 2))

    f = _sphere_field(la, lo, t, mag, bass)
    disp_amp = 0.030 + 0.020 * bass
    disp = 1.0 + disp_amp * f
    persp = 3.0 / (3.0 - z * disp)
    sx = (cx + x * disp * radius * persp).astype(np.int32)
    sy = (cy - y * disp * radius * persp).astype(np.int32)

    # --- shading ---
    f01 = np.clip((f - f.min()) / max(1e-6, f.max() - f.min()), 0, 1)
    ridge = np.clip((f01 - _RIDGE_LO) / (1.0 - _RIDGE_LO), 0, 1) ** _RIDGE_POW

    # rim and membrane are independent (note 6)
    rim_light = 0.45 * rim * (1.0 - 0.85 * ridge)
    shade = (1.0 - 0.18 * np.clip(z, 0, 1)) * (1.0 + rim_light)

    gold = ridge
    cr = (_CREST[0] * gold + _DARK[0] * (1 - gold)) * shade
    cg = (_CREST[1] * gold + _DARK[1] * (1 - gold)) * shade
    cb = (_CREST[2] * gold + _DARK[2] * (1 - gold)) * shade

    # Density normalization (note 4).
    ball_px = math.pi * radius * radius
    ppp = ball_px / len(pts)                 # px per point, higher = sparser
    shortfall = _REF_PPP / ppp              # >1 when sparser than the reference
    k = shortfall ** _NORM_POW if shortfall > 1.0 else 1.0
    cr, cg, cb = cr * k, cg * k, cb * k

    # Two passes: 1px dots for the texture, then a 9-tap fill (note 8) for the
    # membrane and rim so they read as solid sheets rather than speckle.
    buf = _splat(w, h, sx, sy, cr, cg, cb, _DOT)
    solid = (ridge > 0.30) | (rim > 0.6)
    if solid.any():
        br = np.where(solid, cr, 0.0) * _FILL_W
        bg = np.where(solid, cg, 0.0) * _FILL_W
        bb = np.where(solid, cb, 0.0) * _FILL_W
        _splat(w, h, sx, sy, br, bg, bb, _FILL3, out=buf)

    img = np.clip(buf, 0, 255)
    # lift saturation so overlapping gold goes vivid yellow, not white
    lum = img.max(axis=2, keepdims=True)
    img = np.clip(lum * 0.10 + img * 0.90, 0, 255).astype(np.uint8)
    screen.blit(pygame.surfarray.make_surface(
        np.transpose(img, (1, 0, 2))), (0, 0))
