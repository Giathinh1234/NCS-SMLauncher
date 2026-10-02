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
9. THE BALL WAS FROZEN. The audio was read as `mean(mag[:10]) / 255.0 * 2.2`,
   but Player.spectrum() already ends with `np.clip(mag, 0, 1)` -- it returns
   0..1, not 0..255. Dividing by 255 flattened the entire signal to
   0.001-0.008 and the *2.2 then clipped it, so quiet and loud rendered
   byte-identical frames (measured pixel diff between mid and loud: 0.000).
   With the correct range the same audio spans 0.153 .. 0.936. This is why the
   sphere appeared to be a still image.
10. The amplitudes were far too timid even once the range was fixed: the
   radius swung only 28px on a 640px window and disp_amp only 25%. Now the
   radius swings 286..345px and disp_amp 0.04..0.17, and the field drifts 3-5x
   faster (the old 0.6 rad/s meant a 10s pattern cycle).
"""

import math

import numpy as np

import pygame

# --- grid budget, from the benchmark in note 3 above ---
SPHERE_NU = 700
SPHERE_NV = 440
SPHERE_PPP_TARGET = 2.0      # px per point
SPHERE_POINTS_MAX = 240000

# Internal render scale.
#
# The original profile blamed the membrane splat, but the real cost was
# `np.bincount` allocating a w*h-sized float64 accumulator -- 1,024,000 bins,
# 8.2 MB, three times per splat, ten splats per frame. Measured at 1280x800,
# the frame was 372 ms; it is now 8.4 ms.
#
# Three changes, in order of impact:
#   1. accumulate into the point cloud's BOUNDING BOX, not the whole window
#      (a sphere is a circle covering about half of it) -- this is what made
#      native resolution affordable at all
#   2. evaluate the whole kernel as one (k, npts) plane instead of k passes
#   3. drop non-solid points before the 9-tap fill, where they contributed
#      exactly zero anyway
#
# SPHERE_PPP_TARGET also moved 3.5 -> 2.0. Halving the internal resolution
# makes each dot cover 2x2 screen pixels, so the density target has to rise to
# keep the surface looking filled. Verified by rendering both and comparing:
# at ppp 2.0 the ball carries MORE lit pixels than the old full-resolution
# render (373,918 vs 251,928) with no visible row striping. Native resolution
# at ppp 7.0 is also in budget (14.7 ms) but reads sparse and thin.
# --- per-build cost knobs -------------------------------------------------
# The ball stays in every build, including lite. What lite changes is how many
# dots it is made of and the internal resolution it is drawn at, which is where
# the frame cost actually is. At 700x440 the cloud is ~240k points and each one
# is splatted into a (h, w, 3) float accumulator, so cost is very close to
# linear in point count.
#
#   full  1.00  240k points at 0.50 scale
#   lite  0.34  ~28k points at 0.34 scale
#
# 0.34 is not arbitrary: the accumulator is rw*rh*3 floats, and 0.34^2 is 8.7x
# less of it, so the draw path falls to roughly a ninth while the ball still
# reads as the same sphere. The brightness compensation further down already
# handles a sparser cloud -- it divides by the shortfall so fewer, wider-looking
# points do not come out dim -- so this does not need a colour change.
def _density():
    """(point scale, render scale) for the build this binary was made as."""
    try:
        from build_variant import BUILD_PROFILE
    except Exception:
        return 1.0, SPHERE_RENDER_SCALE
    if BUILD_PROFILE == "lite":
        return 0.34, 0.34
    return 1.0, SPHERE_RENDER_SCALE


SPHERE_RENDER_SCALE = 0.5
# a cos(lat)-compensated nu x nv grid yields exactly 0.741 * nu * nv points
_COSLAT_POINTS_FACTOR = 0.741

# --- geometry ---
# The ball pulses with the bass, so its radius varies between
# _R_BASE*(1-_R_SWING) and _R_BASE. Density must NOT be normalized against the
# CURRENT radius: doing so makes the brightness correction swing with volume
# (measured k = 2.23 at full bass vs 5.25 at silence, so the ball pumped 2.5x
# brighter when quiet, mean luminance 508 vs the reference's 203). The grid
# and the normalization both key off a FIXED reference radius of _R_BASE, and
# the pulse only scales the projection.
_R_BASE = 0.44
_R_SWING = 0.30

# --- density normalization, calibrated in note 4 ---
# Recalibrated after the audio fix: with the ball now pulsing and correctly
# sized, 1.2/0.55 washed the interior out (mean 336 vs the reference's 203).
# Measured sweep of (_FILL_W, _NORM_POW) -> mean luminance at 640x640:
#     0.55 / 1.2 -> 336      0.25 / 0.9 -> 241
#     0.35 / 1.0 -> 271      0.18 / 0.8 -> 226   <- chosen
#     0.12 / 0.7 -> 218
# The floor is set by the base dark-dot level rather than the fill, so this is
# as dim as the crest can go without losing the gold.
_REF_PPP = 8.0
_NORM_POW = 0.50

# --- crest shape ---
# These are the MEASURED radial statistics of the reference still
# (Downloads/ExwK_GkWEAENp4e.jpg, 1080x1080, ball R=447px), and of a real NCS
# video, both of which turned out to be the same design in different colours:
# a hollow shell with a thick glowing rim, NOT a solid dot ball.
#
#   band (r/R)   reference mean / %bright     real video mean / %bright
#   core 0.00-0.60     45 /  7.2                   18 /  0.0
#   inner 0.60-0.85    83 / 11.8                   49 /  1.4
#   rim   0.85-1.02   227 / 39.1                  114 /  9.1
#
# The peak is at r/R 0.95-1.00 (mean 357, 64.9% bright); outside r/R 1.05 it
# is pure black. So the rim is the dominant feature and the core is dark with
# a sparse gold stipple.
#
# Earlier versions had this backwards: a uniformly dim ball (core 35, rim 92)
# with a gold membrane smeared across the face. Raising _RIDGE_LO to 0.70
# starved the core of the ~7% bright stipple the reference has, and a narrow
# rim Gaussian (0.055) left the limb at 1/3 of its correct brightness.
# A 0.30 sigma with a 3.2x boost, and a lower ridge threshold so the stipple
# actually covers ~7% of the core, reproduces the measured profile.
_RIDGE_LO = 0.45
_RIDGE_POW = 1.40
_FILL_W = 0.30

# measured reference colours
_CREST = (255.0, 227.0, 152.0)
_DARK = (55.0, 45.0, 9.0)

# small disc kernel, so the membrane smooths without bleeding into the dark
_FILL3 = ((-1, 0, 0.55), (0, 0, 1.0), (1, 0, 0.55),
          (0, -1, 0.55), (0, 1, 0.55),
          (-1, -1, 0.22), (1, -1, 0.22), (-1, 1, 0.22), (1, 1, 0.22))
_DOT = ((0, 0),)

_sphere_cache = {}


# Cached kernel arrays, keyed by the offsets tuple. Building the (k, npts)
# coordinate planes is the expensive part of a splat, and the kernels never
# change, so the 1-tuple and 9-tuple forms are hoisted out of the hot path.
_kernel_cache = {}


def _kernel(offsets):
    """(dx, dy, wt) as arrays, cached per kernel shape."""
    n = len(offsets)
    key = (n, tuple(len(o) for o in offsets))
    k = _kernel_cache.get(key)
    if k is None:
        if len(offsets) == 1 and len(offsets[0]) == 2:
            k = (np.zeros(1, np.int32), np.zeros(1, np.int32),
                 np.ones(1, np.float32))
        else:
            k = (np.array([o[0] for o in offsets], dtype=np.int32),
                 np.array([o[1] for o in offsets], dtype=np.int32),
                 np.array([o[2] if len(o) == 3 else 1.0 for o in offsets],
                          dtype=np.float32))
        if len(_kernel_cache) > 8:
            _kernel_cache.clear()
        _kernel_cache[key] = k
    return k


def _grid_for(w, h):
    """Pick a lat/lon grid sized to the window, bounded by the point cap.

    Keyed to the FIXED _R_BASE, not the pulsing radius, so the point budget
    does not swing with volume (see the _R_BASE comment).
    """
    ball_px = math.pi * (min(w, h) * _R_BASE) ** 2
    want = min(ball_px / SPHERE_PPP_TARGET, SPHERE_POINTS_MAX)
    want *= _density()[0]
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

    The whole kernel is evaluated as one (k, npts) plane rather than k
    separate Python-level passes. The previous per-offset version ran 4 fancy
    index operations per tap -- 36 for the 9-tap membrane kernel -- and those
    small gathers, not the accumulation, were what the profile charged to this
    function. Two big masked gathers now replace them.
    """
    if out is None:
        out = np.zeros((h, w, 3), dtype=np.float32)
    if not len(offsets) or sx.size == 0:
        return out

    dx, dy, wt = _kernel(offsets)

    # (k, npts) coordinate planes for every tap at once
    xx = sx[None, :] + dx[:, None]
    yy = sy[None, :] + dy[:, None]
    m = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h)
    if not m.any():
        return out

    gx = xx[m]
    gy = yy[m]

    # Accumulate into the points' BOUNDING BOX, not the whole window.
    # np.bincount always allocates minlength bins, so passing w*h forces a
    # 1,024,000-bin float64 array (8.2 MB) per channel even though the ball is
    # a circle covering roughly half the window -- and a quarter of it once the
    # render is scaled down. Shifting the indices by the box origin shrinks the
    # accumulator to the ball, with no change to the result.
    x0, x1 = int(gx.min()), int(gx.max()) + 1
    y0, y1 = int(gy.min()), int(gy.max()) + 1
    bw, bh = x1 - x0, y1 - y0
    area = bw * bh

    # Re-index into the box's OWN row-major width, not the window's. Subtracting
    # a linear origin is not enough: (gh - y0) * bw + (gx - x0), because a
    # bbox narrower than the window has a different stride.
    idx = (gy - y0) * bw + (gx - x0)

    wts = np.broadcast_to(wt[:, None], m.shape)
    rw = (r[None, :] * wts)[m] * weight
    gw = (g[None, :] * wts)[m] * weight
    bwv = (b[None, :] * wts)[m] * weight

    sub = out[y0:y1, x0:x1, :]
    sub[..., 0] += np.bincount(idx, weights=rw, minlength=area).reshape(bh, bw)
    sub[..., 1] += np.bincount(idx, weights=gw, minlength=area).reshape(bh, bw)
    sub[..., 2] += np.bincount(idx, weights=bwv, minlength=area).reshape(bh, bw)
    return out


def _bands(mag):
    """Split the spectrum into (bass, mid, high), each 0..1.

    Player.spectrum() returns values already normalized to 0..1 (it ends with
    np.clip(mag, 0, 1)), so these must NOT be divided by 255. The previous
    code did `mean(mag[:10]) / 255.0 * 2.2`, which put the entire signal at
    0.001-0.008 and then clipped at 1.0 -- measured against realistic music:
        level 0.15 -> 0.001        level 0.75 -> 0.007
    i.e. the ball was effectively frozen and identical for quiet and loud.
    With the correct range the same audio spans 0.153 .. 0.936, which is what
    makes it visibly pulse.
    """
    n = len(mag)
    if n == 0:
        return 0.0, 0.0, 0.0
    def band(lo, hi):
        hi = min(hi, n)
        if lo >= hi:
            return 0.0
        return float(np.clip(np.mean(mag[lo:hi]), 0.0, 1.0))
    return band(0, 12), band(12, 120), band(120, 420)


def _smooth(prev, target, rate, dt):
    """Frame-rate independent exponential smoothing.

    A raw FFT frame is jittery, and feeding it straight into a radius makes the
    ball twitch. Smoothing gives the punchy-but-fluid feel of the reference.
    """
    if prev is None:
        return target
    a = 1.0 - math.exp(-rate * dt)
    return prev + (target - prev) * a


# animation state, per running instance
_anim = {"bass": None, "mid": None, "high": None, "t_prev": None}


def _reset_anim():
    """Clear the smoothing state (call between tracks for an instant reset)."""
    _anim["bass"] = _anim["mid"] = _anim["high"] = _anim["t_prev"] = None


def _sphere_field(la, lo, t, bass, mid, high, dt):
    """Domain-warped wave field, driven by the audio bands.

    The warp (a sine of a sine) is what makes the membrane read as draped
    fabric rather than a few round blobs. Each band drives a different part of
    the shape so the motion reads as a response to the music, not a generic
    idle wobble:

        bass  -> warp depth and the crest sharpness (the punch)
        mid   -> the large-scale sheet drift
        high  -> the fine surface texture, which shimmers
    """
    # Speeds are 3-5x the previous values. The old field drifted at 0.6 rad/s,
    # so a full pattern cycle took ~10s and looked static next to real music.
    warp = (2.6 * np.sin(1.0 * la + 1.9 * t)
            + 1.1 * np.sin(1.3 * lo - 1.6 * t))
    warp = warp * (0.55 + 0.95 * bass)

    return (0.78 * np.sin(0.80 * lo + 0.95 * la + warp + 2.1 * t + mid * 1.4)
            + 0.14 * np.sin(6.0 * la + 4.0 * lo + 3.2 * t + high * 5.0)
            + 0.08 * np.cos(1.8 * la - 1.2 * lo - 1.3 * t))


def draw_ncs_sphere(screen, player, w, h, t):
    """Draw the NCS sphere: dark gold dot texture under a flowing gold membrane.

    Animated and audio-reactive. The sphere pulses with the bass, ripples on
    the mids, and shimmers on the highs.
    """
    mag = player.spectrum()

    # Everything below happens at the INTERNAL resolution, not the window's.
    # The accumulator is the dominant cost and it scales with rw*rh, so this is
    # where the frame budget is actually won. The result is scaled back up to
    # the window at the end.
    scale = _density()[1]
    rw = max(2, int(w * scale))
    rh = max(2, int(h * scale))
    if (rw, rh) == (w, h):
        rw, rh = w, h

    geo = _sphere_geometry(rw, rh)
    pts = geo["base"]
    la, lo = geo["lat"], geo["lon"]

    cx, cy = rw // 2, rh // 2

    # --- smoothed audio bands ---
    raw_bass, raw_mid, raw_high = _bands(mag)
    tp = _anim["t_prev"]
    dt = 0.033 if tp is None else max(0.0, min(0.1, t - tp))
    _anim["t_prev"] = t
    bass = _smooth(_anim["bass"], raw_bass, 9.0, dt)
    mid = _smooth(_anim["mid"], raw_mid, 7.0, dt)
    high = _smooth(_anim["high"], raw_high, 14.0, dt)
    _anim["bass"], _anim["mid"], _anim["high"] = bass, mid, high

    # Radially scaled by the smoothed bass, with the MAXIMUM fitted to _R_BASE
    # so a loud passage never pushes the ball past the window (at a 0.44 base
    # the rim was clipped on all four edges). The swing is
    # _R_BASE*_R_SWING = 0.132 of the short side, i.e. 35px on a 640px window.
    radius = min(rw, rh) * _R_BASE * (1.0 - _R_SWING + _R_SWING * bass)

    bx, by, bz = pts[:, 0], pts[:, 1], pts[:, 2]

    # --- rotation: spin about Y, with only a slight tilt about X ---
    # The tilt is deliberately small. A sphere's silhouette is only circular at
    # tilt 0; a 0.5 rad (29 deg) tilt made the bbox 507x553, i.e. 1.09 aspect,
    # against the reference's 901x900 circle. Keep it under ~0.12 rad (7 deg)
    # so the silhouette reads as a ball, and get the visible motion from the
    # Y spin and the field instead.
    ang = t * 0.30
    tilt = 0.10 + 0.06 * math.sin(t * 0.5) + 0.03 * math.sin(t * 1.7)
    ca, sa = math.cos(ang), math.sin(ang)
    ct, st = math.cos(tilt), math.sin(tilt)
    x = bx * ca + bz * sa
    z0 = -bx * sa + bz * ca          # view depth before the wobble
    y = by * ct - z0 * st
    z = by * st + z0 * ct

    # --- limb highlight: the silhouette of the UNIT sphere is the z0 == 0
    #     circle. The real NCS ball is a hollow shell: a dark core with a
    #     thick glowing rim, and its radial profile (measured off the
    #     reference still) rises from mean 100 at r/R 0.85 to 357 at r/R 0.97.
    #     A wide Gaussian (sigma 0.40) washed the whole outer half into a
    #     uniform shell; the real edge is tighter, so pull it in and let the
    #     fill pass build the thickness instead.
    rim = np.exp(-((z0 / 0.30) ** 2))

    f = _sphere_field(la, lo, t, bass, mid, high, dt)
    # Bass drives the ripple depth: 0.04 at rest up to 0.17 on a loud hit.
    disp_amp = 0.040 + 0.130 * bass
    disp = 1.0 + disp_amp * f
    persp = 3.0 / (3.0 - z * disp)
    sx = (cx + x * disp * radius * persp).astype(np.int32)
    sy = (cy - y * disp * radius * persp).astype(np.int32)

    # --- shading ---
    f01 = np.clip((f - f.min()) / max(1e-6, f.max() - f.min()), 0, 1)
    ridge = np.clip((f01 - _RIDGE_LO) / (1.0 - _RIDGE_LO), 0, 1) ** _RIDGE_POW

    # rim and membrane are independent (note 6)
    rim_light = 3.20 * rim * (1.0 - 0.85 * ridge)
    shade = (1.0 - 0.18 * np.clip(z, 0, 1)) * (1.0 + rim_light)

    gold = ridge
    cr = (_CREST[0] * gold + _DARK[0] * (1 - gold)) * shade
    cg = (_CREST[1] * gold + _DARK[1] * (1 - gold)) * shade
    cb = (_CREST[2] * gold + _DARK[2] * (1 - gold)) * shade

    # Density normalization (note 4). Keyed to the FIXED _R_BASE reference
    # ball, not the pulsing radius, so brightness does not pump with volume.
    ref_ball_px = math.pi * (min(rw, rh) * _R_BASE) ** 2
    ppp = ref_ball_px / len(pts)             # px per point, higher = sparser
    shortfall = _REF_PPP / ppp              # >1 when sparser than the reference
    k = shortfall ** _NORM_POW if shortfall > 1.0 else 1.0
    cr, cg, cb = cr * k, cg * k, cb * k

    # Two passes: 1px dots for the texture, then a 9-tap fill (note 8) for the
    # membrane and rim so they read as solid sheets rather than speckle.
    buf = _splat(rw, rh, sx, sy, cr, cg, cb, _DOT)
    solid = (ridge > 0.30) | (rim > 0.15)
    if solid.any():
        # Drop the non-solid points before the 9-tap pass. The fill weight is
        # already np.where(solid, ..., 0), so those points contribute exactly
        # nothing -- but they still cost a full 9x multiply and gather each.
        # Filtering is identical output for less work, and the membrane is a
        # minority of the cloud, so this is the single biggest saving left.
        fsx, fsy = sx[solid], sy[solid]
        br = cr[solid] * _FILL_W
        bg = cg[solid] * _FILL_W
        bb = cb[solid] * _FILL_W
        _splat(rw, rh, fsx, fsy, br, bg, bb, _FILL3, out=buf)

    # Tone-map and saturate. The first version used np.max(axis=2) and a
    # second full-size product, which cost 18.3 ms/frame at 1280x800. The
    # "optimization" that replaced it with a single global buf.max() was
    # WRONG: that added a constant to every pixel, lifting the black
    # background to luminance 22 instead of 0.
    # This is correct and still cheaper: clip in place, then scale the two
    # cool channels down relative to R, which is what makes overlapping gold
    # read as vivid yellow rather than white.
    np.clip(buf, 0, 255, out=buf)
    buf[..., 1] *= 0.90
    buf[..., 2] *= 0.80
    img = buf.astype(np.uint8)
    surf = pygame.surfarray.make_surface(np.transpose(img, (1, 0, 2)))
    if surf.get_width() != w or surf.get_height() != h:
        # smoothscale, not scale: the ball is a soft cloud of small dots, and
        # a nearest-neighbour upscale turns each dot into a visible block.
        surf = pygame.transform.smoothscale(surf, (w, h))
    screen.blit(surf, (0, 0))
