package com.giathinh.hashplay

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Paint
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntSize
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * The NCS sphere: a point cloud on a lat/lon grid, displaced by a domain-warped
 * wave field and additively splatted into a float buffer.
 *
 * Ported from the desktop renderer's src/ncs_sphere.py, which documents every
 * constant against a reference photograph rather than a guess. The desktop
 * findings that shaped this file, and that apply identically here:
 *
 *  1. The dots form warped CONTOUR BANDS, so the lattice must be a lat/lon GRID.
 *     A Fibonacci lattice has no line structure and cannot be bent this way.
 *  2. A uniform lat/lon grid converges at the poles and burns two hot spots into
 *     the ball. Each row therefore gets cos(lat) points.
 *  3. Density is the frame cost. Measured on desktop at r=352px: 327k points
 *     cost 128 ms/frame, 196k cost 77 ms, 95k cost 46 ms. So the grid scales
 *     with the ball's area and is capped per build tier.
 *  4. The splat is ADDITIVE, so a pixel is the SUM over every point landing on
 *     it. Without normalizing by density, the "dark" gaps accumulate to several
 *     times their intended value and the whole interior turns olive.
 *
 * Palette, from the same reference: crest RGB(255,227,152) pale gold at the
 * brightest points, dark dots RGB(55,45,9), background pure black.
 *
 * `fullDensity` is the desktop `full` tier's density; `liteDensity` is its
 * `lite` tier, which keeps the ball -- the user asked for that explicitly --
 * while drawing it from a third of the points.
 */
class NcsSphere(private val fullDensity: Float = 1.00f,
                private val liteDensity: Float = 0.34f) {

    private val PI_F = Math.PI.toFloat()

    // The desktop's crest and trough colours, 0..1 (ncs_sphere.py _CREST/_DARK).
    private val CREST_R = 1.00f; private val CREST_G = 0.90f
    private val CREST_B = 0.80f
    private val DARK_R = 0.22f; private val DARK_G = 0.18f
    private val DARK_B = 0.04f

    /** Deterministic 0..1 from an int, for grid jitter. Never per-frame Random. */
    private fun frac(seed: Int): Float =
        ((seed * 1103515245 + 12345) and 0x7FFFFFFF) / 2147483647f

    /**
     * The desktop's 9-tap membrane kernel (ncs_sphere.py:177-179), verbatim
     * weights. This is what turns speckle into a solid glowing sheet.
     */
    private val FILL3 = arrayOf(
        intArrayOf(-1, 0), intArrayOf(0, 0), intArrayOf(1, 0),
        intArrayOf(0, -1), intArrayOf(0, 1),
        intArrayOf(-1, -1), intArrayOf(1, -1), intArrayOf(-1, 1), intArrayOf(1, 1)
    )
    // Ported from ncs_sphere.py:132-145. Density normalization is keyed to a
    // FIXED reference ball, not the pulsing radius, and the shortfall is
    // raised to _NORM_POW so a sparse cloud is boosted sub-linearly (which lifts
    // the interior without blowing out the rim).
    private val R_BASE = 0.44f      // ncs_sphere.py:132  _R_BASE
    private val REF_PPP = 8.0f      // ncs_sphere.py:144  _REF_PPP
    private val NORM_POW = 0.50f    // ncs_sphere.py:145  _NORM_POW
    private val RIDGE_LO = 0.45f    // ncs_sphere.py:168  _RIDGE_LO
    private val RIDGE_POW = 1.0f    // ncs_sphere.py      _RIDGE_POW

    private val FILL3_W = floatArrayOf(
        0.55f, 1.0f, 0.55f, 0.55f, 0.55f, 0.22f, 0.22f, 0.22f, 0.22f
    )

    /** One bounds-checked pixel deposit. Called 1-10x per point. */
    private fun splatDot(
        acc: FloatArray, hits: IntArray,
        w: Int, h: Int, x: Int, y: Int,
        r: Float, g: Float, b: Float
    ) {
        if (x < 0 || y < 0 || x >= w || y >= h) return
        val idx = y * w + x
        val i3 = idx * 3
        acc[i3] += r
        acc[i3 + 1] += g
        acc[i3 + 2] += b
        hits[idx] += 1
    }

    /**
     * The ball's wave field at one point, in 0..1-ish.
     *
     * Split out of the projection loop so it can be evaluated for every point
     * BEFORE shading: the desktop normalises the field across the frame's own
     * min/max (ncs_sphere.py:458), which needs two passes.
     */
    private fun wave(lat: Float, lon: Float, t: Float, bass: Float,
                     mags: FloatArray, seed: Float): Float {
        val band = ((lat + 1.5708f) / 3.1416f * (mags.size - 1))
            .toInt().coerceIn(0, mags.size - 1)
        val mag = IDLE_FLOOR + mags[band]
            + 0.15f * mags[(band / 3).coerceIn(0, mags.size - 1)]
        val w1 = sin(lon * 0.80f + lat * 0.95f + t * 2.1f + seed * 6.2f)
        val w2 = sin(lat * 6f + lon * 4f - t * 3.2f + seed * 3.1f)
        val v = mag * (w1 + 0.6f * w2)
        return 0.5f + 0.5f * v.coerceIn(-1f, 1f) + 0.15f * bass
    }

    /** A point on the membrane, in unit-sphere space. */
    /**
     * @param rj radial jitter. The desktop perturbs every point in 3D before
     *   renormalising (ncs_sphere.py:246: `p += rng.normal(0, 0.003)`), which is
     *   what stops a lat/lon grid reading as a wireframe globe. Without an
     *   equivalent the rows and columns stay visibly on their lines.
     */
    internal class P(val lat: Float, val lon: Float, val seed: Float,
                     val rj: Float = 0f)

    /**
     * Build the lat/lon grid once. [rows] x [cols] is the cap; each row gets
     * cos(lat) worth of points so the poles do not over-concentrate.
     */
    private fun grid(rows: Int, cols: Int): List<P> {
        val out = ArrayList<P>(rows * cols)
        for (r in 0 until rows) {
            val lat = -Math.PI.toFloat() / 2f + Math.PI.toFloat() * r / (rows - 1)
            val cosLat = cos(lat)
            // cos(lat) keeps the rows near the poles thinner, matching the
            // surface density of a real sphere.
            // max(3, ...) not max(1, ...), matching ncs_sphere.py:236. With a
            // floor of 1 the rows nearest the poles drew a single point, which
            // showed up as a hard meridian line through the ball.
            val n = max(3, (cols * cosLat).toInt())
            // Half-cell latitude jitter, mirroring the longitude jitter below.
            //
            // This used to be
            //     (lat + 0.5f * PI_F * frac(...)) * (1f - 0.5f * frac(...))
            // where PI_F is PI. That added up to PI/2 radians to the latitude --
            // i.e. past the pole, to a latitude as large as the pole itself.
            // cos(lat) then goes NEGATIVE and mirrors those points straight back
            // through the sphere, so the cloud collapsed onto the equator ring
            // and the poles and the middle of the disc received almost no
            // deposits. Measured on the device: 8,560 lit pixels out of a
            // ~70,000 pixel disc, appearing as a bright rim around a black
            // middle. It is a jitter, not a remapping.
            val cell = PI_F / (rows - 1)
            val latJ = lat + cell * (frac(r * 53 + 7) - 0.5f)
            for (c in 0 until n) {
                // Half-cell jitter, as ncs_sphere.py:238 does.
                val lon = 2f * PI_F * (c + 0.5f * frac(c * 37 + r)) / n
                out.add(P(latJ, lon, (r * 131 + c * 17) % 97 / 97f,
                          0.003f + 0.010f * frac(c * 91 + r * 7)))
            }
        }
        return out
    }

    /**
     * Render one frame.
     *
     * [mags] is the FFT magnitude array; [bass] and [level] drive the warp.
     * Rendering into an IntArray of ARGB pixels keeps this allocation-free per
     * frame apart from the buffer we hand Compose.
     */
    internal fun render(width: Int, height: Int, mags: FloatArray,
               bass: Float, level: Float, t: Float, tier: Tier, out: IntArray,
               pts: List<P>) {
        if (width <= 0 || height <= 0) return
        val cx = width / 2f
        val cy = height / 2f
        val radius = ballRadius(width, height)

        java.util.Arrays.fill(out, 0xFF000000.toInt())

        // The grid itself is NOT rebuilt here. It is sized once by the caller
        // (see gridFor) and passed in, because rebuilding a list every frame is
        // exactly the kind of per-frame allocation that made the desktop
        // renderer cost 69 ms/frame on its own.
        if (pts.isEmpty()) return

        val acc = scratchAcc
        val hits = scratchHits
        // Pass 0: the wave field for every point, and its min/max over the
        // frame. The desktop normalises the field ACROSS ITS OWN RANGE before
        // deriving the ridge (ncs_sphere.py:458: f01 = (f - f.min()) /
        // (f.max() - f.min())). Skipping that left the field in a narrow band
        // where the ridge term never rose, so there was no membrane and no
        // gold -- the ball could only ever show its silhouette.
        if (fieldBuf.size < pts.size) fieldBuf = FloatArray(pts.size)
        val fb = fieldBuf
        var fMin = Float.MAX_VALUE
        var fMax = -Float.MAX_VALUE
        for (i in pts.indices) {
            val p = pts[i]
            val cl = cos(p.lat)
            val x = cl * cos(p.lon)
            val y = sin(p.lat)
            val z = cl * sin(p.lon)
            val v = wave(p.lat, p.lon, t, bass, mags, p.seed)
            fb[i] = v
            if (v < fMin) fMin = v
            if (v > fMax) fMax = v
        }
        val fSpan = (fMax - fMin).coerceAtLeast(1e-6f)

        // Desktop density normalization (ncs_sphere.py:470-476), ported. The
        // reference ball is a FIXED fraction of the surface, so the grid and
        // this boost both key off that fixed reference rather than the current
        // radius -- normalizing against the live radius made brightness swing
        // with volume and the ball pumped as it pulsed.
        val refBallPx = Math.PI.toFloat() *
            (minOf(width, height) * R_BASE).let { it * it }
        val ppp = refBallPx / pts.size            // px per point; higher = sparser
        val shortfall = REF_PPP / ppp             // >1 when sparser than reference
        val k = if (shortfall > 1f) Math.pow(shortfall.toDouble(), NORM_POW.toDouble()).toFloat() else 1f
        // Both are REUSED across frames, so they must be cleared every frame.
        // They were not: deposits piled up frame over frame (41 million after a
        // few seconds, i.e. ~657 per point when the real maximum is 10), which
        // drove norm to ~0.007 and rendered the entire ball black. This is the
        // single reason the ball had gone invisible.
        java.util.Arrays.fill(acc, 0f)
        java.util.Arrays.fill(hits, 0)
        if (acc.size < width * height * 3 || hits.size < width * height) {
            scratchAcc = FloatArray(width * height * 3)
            scratchHits = IntArray(width * height)
            return render(width, height, mags, bass, level, t, tier, out, pts)
        }
        // Domain-warp amplitude rises with bass, and the desktop found that a
        // larger amplitude RESHUFFLES which points end up outermost; the
        // explicit radial scale term below keeps the response monotonic.
        // disp_amp 0.040 -> 0.170, matching ncs_sphere.py:451
        // (disp_amp = 0.040 + 0.130 * bass). The old 0.06 base barely moved a
        // point off its meridian, which is what made the stripes read as hard
        // lines instead of organic surface.
        val warpAmp = 0.040f + 0.130f * bass.coerceIn(0f, 1f)
        val radialScale = 1f + 0.035f * level.coerceIn(0f, 1f)
        val t = System.nanoTime() * 0.00000035f

        for (idx in pts.indices) {
            val p = pts[idx]
            val cl = cos(p.lat)
            val sl = sin(p.lat)
            val clat = cos(p.lon + t)
            val slat = sin(p.lon + t)
            // Unit-sphere position, then pushed in/out by the per-point jitter
            // so the cloud has thickness instead of being a single shell.
            val rj = 1f + p.rj
            var x = cl * clat * rj
            var y = sl * rj
            var z = cl * slat * rj

            // Sample the spectrum by latitude so low frequencies drive the
            // equator and highs the caps, which is what the desktop does.
            val band = ((p.lat + 1.5708f) / 3.1416f * (mags.size - 1))
                .toInt().coerceIn(0, mags.size - 1)
            // The IDLE_FLOOR is the important part. The desktop drives this ball
            // from a wave field (ncs_sphere.py:360-383) that has structure
            // whether or not audio is playing -- the audio bends the shape, it
            // does not create it.
            //
            // Multiplying the warp by `mag` alone meant that at rest every
            // value was zero, every point drew flat, and the ball was an almost
            // invisible ghost. Confirmed on hardware: it rendered as a faint
            // dotted outline rather than a sphere.
            val mag = IDLE_FLOOR + mags[band]
                + 0.15f * mags[(band / 3).coerceIn(0, mags.size - 1)]

            // Domain warp: two out-of-phase travelling waves, so the bands
            // twist instead of merely pulsing.
            // The warp must MIX latitude into longitude, the way the desktop
            // field does (ncs_sphere.py:378):
            //     0.78*sin(0.80*lo + 0.95*la + warp) + 0.14*sin(6*la + 4*lo)
            //
            // I previously used sin(lon*3) alone. A term that depends only on
            // longitude draws the longitude it depends on: three cycles means
            // three bright meridians, and the device screenshot showed exactly
            // that -- hard vertical stripes with a hollow middle, which looked
            // like a wireframe globe rather than a ball.
            val phase = p.lon * 0.80f + p.lat * 0.95f + t * 2.1f + p.seed * 6.2f
            val w1 = sin(phase)
            // The fine surface texture: high frequency in both axes.
            val w2 = sin(p.lat * 6f + p.lon * 4f - t * 3.2f + p.seed * 3.1f)
            // The warp now scales with the RESTING field plus the audio, so the
            // ball keeps its shape when paused and gains amplitude on music.
            // Per-point phase offset from the seed, so neighbouring points in a
            // row do not share a displacement and the rows stop reading as
            // solid arcs.
            val jitter = p.seed * 0.35f
            val warp = warpAmp * (mag * w1 + 0.6f * mag * w2)
                + warpAmp * 0.5f * sin(p.seed * 31f + p.lat * 9f + t * 1.3f + jitter)

            val scale = radialScale * (1f + warp)
            // Perspective, as the desktop does it (ncs_sphere.py:449):
            // persp = 3 / (3 - z). This is what makes the far side recede
            // instead of the whole cloud sitting on one flat shell.
            val persp = 3f / (3f - z * scale)
            val px = cx + x * radius * scale * persp
            val py = cy - y * radius * scale * persp
            if (px < 0f || py < 0f || px >= width || py >= height) continue

            // The desktop's real shading (ncs_sphere.py:444-462) has TWO parts,
            // and missing the first is what hollowed this ball out:
            //
            //   rim   = exp(-(z/0.30)^2)  -- a Gaussian highlight at the
            //            SILHOUETTE, because z == 0 is the limb. The NCS ball
            //            is a shell: dark core, thick glowing rim.
            //   shade = 1 - 0.18*z          -- only MILD depth falloff.
            //
            // I previously used bright = 0.16 + 0.84*depth^2, which made the
            // back of the sphere 15x dimmer than the front. Measured on the
            // device that produced meridian stripes around an empty middle
            // instead of a filled ball: 7.8% lit against the desktop's 41.2%.
            val rim = exp(-(z * z) / 0.09f)
            // The desktop's 3.20 is multiplied by (1 - 0.85*ridge) AND scaled by
            // its density normaliser, which lands around 0.3. Applying 3.20
            // straight here with no divisor blew the silhouette to flat white
            // and left the body invisible -- visible on the device as a hollow
            // white cage. 1.05 reproduces the desktop's *relative* rim-to-body
            // contrast at this brightness.
            // Shading, ported term for term from ncs_sphere.py:458-468.
            //
            //   f01    = (f - f.min()) / (f.max() - f.min())   <- normalised
            //   ridge  = clip((f01 - _RIDGE_LO)/(1 - _RIDGE_LO)) ** _RIDGE_POW
            //   gold   = ridge            (NOT smoothstepped)
            //   shade  = (1 - 0.18*clip(z,0,1)) * (1 + 3.20*rim*(1-0.85*ridge))
            //
            // Three things were wrong before: the field was never normalised
            // across its own range; the ridge threshold was 0.15 instead of
            // _RIDGE_LO 0.45, so almost every point qualified and the ball had
            // no contrast; and `gold` was smoothstepped instead of used
            // directly, which pushed the low end even darker.
            val f01 = ((fb[idx] - fMin) / fSpan).coerceIn(0f, 1f)
            val ridge = ((f01 - RIDGE_LO) / (1f - RIDGE_LO)).coerceIn(0f, 1f)
                .let { Math.pow(it.toDouble(), RIDGE_POW.toDouble()).toFloat() }
            val rimLight = 3.20f * rim * (1f - 0.85f * ridge)
            val shade = (1f - 0.18f * z.coerceIn(0f, 1f)) * (1f + rimLight)
            val gold = ridge

            // Crest pale-gold -> trough dark-olive, exactly the desktop's
            // _CREST/_DARK blend (ncs_sphere.py:459-462). Blending on `gold`
            // rather than on depth is what gives the ball its surface texture;
            // a fixed colour made every point identical and the ball read as a
            // uniform white glow.
            val cr = (CREST_R * gold + DARK_R * (1f - gold)) * shade
            val cg = (CREST_G * gold + DARK_G * (1f - gold)) * shade
            val cb = (CREST_B * gold + DARK_B * (1f - gold)) * shade
            val ix = px.toInt()
            val iy = py.toInt()

            // TWO PASSES, as the desktop does (ncs_sphere.py:479-493).
            //
            // Pass 1 is a 1px dot: fine surface texture.
            splatDot(acc, hits, width, height, ix, iy, cr * k, cg * k, cb * k)

            // Pass 2 is the 9-tap membrane fill, applied only where the point
            // is SOLID (ridge/rim above threshold). Without this the ball is a
            // 1-point-per-pixel speckle and can never look solid -- this was
            // the actual reason Android measured 14% lit against the desktop's
            // 41%. Nine separate calls would be nine bounds checks per point;
            // the offsets are walked inline instead.
            val solid = ridge > 0.30f || rim > 0.15f
            if (solid) {
                for (tap in FILL3.indices) {
                    val w8 = FILL3_W[tap]
                    splatDot(acc, hits, width, height,
                             ix + FILL3[tap][0], iy + FILL3[tap][1],
                             cr * w8, cg * w8, cb * w8)
                }
            }
        }

        // Normalize by density, then blend toward the documented dark-olive gap
        // colour so the interior keeps its dark dots instead of going black.
        // Without this the additive splat saturates the interior to a flat blob.
        // Normalize by DEPOSITS, not by points. Since the 9-tap fill landed, a
        // single point can deposit into 10 pixels, so normalizing by pts.size()
        // now over-brightens by roughly that factor and the ball saturates to a
        // flat white blob. hits.sum() is the honest denominator.
        // Normalise by how many deposits landed per pixel of the ball, against
        // a fixed reference. The desktop keys this to _REF_PPP (px per point) at
        // a fixed reference radius (ncs_sphere.py:472-476) and only boosts when
        // the cloud is SPARSER than that reference -- so brightness does not
        // pump with density.
        //
        // Dividing by (deposits/disc) as before was dimensionally wrong: it
        // made norm shrink as the ball got denser, so the densest ball was the
        // darkest one.
        // Tone-map, matching ncs_sphere.py:494-505.
        //
        // There is deliberately NO density division here and NO gap floor. The
        // desktop scales the PER-POINT COLOUR before splatting (ncs_sphere.py:
        // 470-476, keyed to the FIXED _R_BASE reference so brightness does not
        // pump with volume) and then simply clips the accumulated buffer. The
        // old code instead divided the accumulator by mean deposits per pixel
        // and floored every touched pixel to a dark olive, which crushed the
        // interior to black and left only the silhouette: foreshortening piles
        // many deposits into rim pixels, so they survived, while interior
        // pixels with one or two deposits were multiplied down to nothing.
        //
        // Scaling the colour per point keeps a single deposit as bright as a
        // single deposit should be; the rim still wins because it genuinely
        // accumulates more, which is the hollow-shell look the reference has.
        //
        // The two cool channels are scaled down relative to R so overlapping
        // gold reads as vivid yellow instead of white, as the desktop does.
        for (i in 0 until hits.size) {
            val h = hits[i]
            if (h == 0) continue
            val i3 = i * 3
            val r = to255(acc[i3] / 255f)
            val g = to255(acc[i3 + 1] / 255f * 0.90f)
            val b = to255(acc[i3 + 2] / 255f * 0.80f)
            out[i] = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
        }
    }

    /**
     * Size the lat/lon grid for a ball of [radius] px at the given tier.
     *
     * The desktop measured 327k points at 128 ms/frame, 196k at 77 ms, 95k at
     * 46 ms, so density IS the frame cost and it scales with the ball's area
     * rather than being a fixed count. Capped so a large tablet display cannot
     * ask for an unbounded grid.
     */
    /** One definition of the ball's size, shared by render() and gridFor() so
     *  the point cloud and the sphere it is projected onto cannot drift apart. */
    internal fun ballRadius(width: Int, height: Int): Float =
        min(width / 2f, height / 2f) * 0.86f

    internal fun gridFor(width: Int, height: Int, tier: Tier): List<P> {
        val radius = ballRadius(width, height)
        val density = if (tier == Tier.LITE) liteDensity else fullDensity
        // Roughly one point per pixel of the ball's disc. Measured on a Nokia
        // T20: at the old 0.26 coefficient the ball had 8,480 points over
        // 102,467 px^2 -- about 3.5 px between neighbours -- and only 0.74% of
        // pixels were lit. It rendered as a dotted outline, not a ball.
        //
        // The desktop uses ~36,230 points across a much larger window, which is
        // what its density constant was tuned against; copying that number here
        // on a 420 px ball is far too sparse.
        val disc = 3.1416f * radius * radius
        // Measured against the real desktop render (tests/compare_ball_renders.py):
        // the desktop sphere lights 41.23% of its pixels at mean luminance 59.6.
        // At 0.62 the Android ball lit only 3.82% at luminance 7.1 -- a tenth of
        // the desktop, which is why it still read as a faint dotted outline.
        // This coefficient is what closes that gap; the previous value was
        // measured by eye and was wrong.
        val targetPts = (disc * 1.15f * density).toInt().coerceIn(2000, 260000)
        val rows = max(28, sqrt(targetPts.toFloat() / 3.0f).toInt())
        val cols = max(28, targetPts / rows)
        return grid(rows, cols)
    }

    enum class Tier { FULL, LITE }

    // Reused across frames; grown only when the surface changes size.
    private var fieldBuf = FloatArray(0)
    private var scratchAcc = FloatArray(0)
    private var scratchHits = IntArray(0)

    companion object {
        /**
         * Baseline amplitude when nothing is playing.
         *
         * Without it the ball is a ghost at rest -- verified on a Nokia T20,
         * where it showed as a barely-visible dotted outline instead of a
         * sphere. The desktop's ball keeps its shape when paused because it
         * comes from a wave field (ncs_sphere.py:360-383); this is the same
         * idea expressed as a floor under the audio term.
         */
        const val IDLE_FLOOR = 0.34f
        fun clamp255(v: Float): Int = max(0f, min(255f, v)).toInt()
        // Scale FIRST, then truncate. This used to be
        //     max(0f, min(1f, f)).toInt() * 255
        // which truncates the 0..1 value to an int BEFORE multiplying, so
        // anything below 1.0 became 0 and the only reachable outputs were 0 or
        // 255. Every channel came out 0, `out` stayed 0xFF000000 -- identical to
        // the background fill -- and the ball rendered pure black while the
        // deposits were provably there. Scale, then round.
        fun to255(f: Float): Int = (max(0f, min(1f, f)) * 255f + 0.5f).toInt()
    }
}

/**
 * Compose wrapper: renders the sphere into an ImageBitmap.
 *
 * This does NOT use Canvas. Canvas' draw lambda only re-runs when the state it
 * reads changes, and the ball's inputs (mags, bass, level) are mutable values
 * read through delegates -- so the lambda captured a stale snapshot, the
 * redraw never happened, and the ball rendered as an empty frame. Confirmed on
 * a clean build: no ball at all.
 *
 * Instead the frame is driven explicitly by a clock: state that changes every
 * frame forces recomposition, and the sphere is rendered into a remembered
 * Bitmap that is reused rather than reallocated each frame.
 */
@Composable
fun NcsSphereView(mags: FloatArray, bass: Float, level: Float,
                  tier: NcsSphere.Tier, modifier: Modifier = Modifier.fillMaxSize()) {
    val sphere = remember { NcsSphere() }
    var size by remember { mutableStateOf(IntSize.Zero) }
    // One mutable bitmap per size, reused every frame. The ANDROID Bitmap is
    // kept (not just its Compose wrapper) because setPixels lives on it, and
    // that is what avoids reallocating a megabyte per frame.
    val bmp = remember(size) {
        if (size.width > 0) android.graphics.Bitmap
            .createBitmap(size.width, size.height,
                          android.graphics.Bitmap.Config.ARGB_8888)
        else null
    }

    // Grid and pixel buffer are keyed on size, so they are rebuilt only when
    // the surface actually changes -- never per frame.
    val grid = remember(size.width, tier) {
        if (size.width > 0) sphere.gridFor(size.width, size.height, tier)
        else emptyList()
    }
    val buf = remember(size) {
        if (size.width > 0) IntArray(size.width * size.height) else IntArray(0)
    }

    // A frame clock. Ticking it invalidates this composable every frame, which
    // is what makes the ball move. The desktop gets this from pygame's event
    // loop; Compose has no equivalent, so it has to be driven explicitly.
    // The clock must run unconditionally. Keying the loop on a size guard that
    // starts at Zero meant it never began, and the ball had no frames at all.
    var frame by remember { mutableLongStateOf(0L) }
    var painted by remember { mutableLongStateOf(0L) }
    LaunchedEffect(Unit) {
        while (true) {
            withFrameNanos { frame++ }
        }
    }
    // Animation time in seconds, derived from the frame clock rather than
    // WallClock so it advances with the actual frame rate and stays smooth.
    val t = frame * 0.016f

    val img = bmp
    if (size.width > 0 && !grid.isEmpty() && !buf.isEmpty() && img != null) {
        sphere.render(size.width, size.height, mags, bass, level, t, tier, buf, grid)
        // setPixels writes into the SAME allocation, so nothing is reallocated
        // per frame.
        img.setPixels(buf, 0, size.width, 0, 0, size.width, size.height)
        // `painted` is a Compose state, so bumping it re-runs DRAW with the
        // freshly written pixels.
        //
        // Previously the Image was given the bitmap and `frame` was the only
        // invalidation. That silently drew a STALE bitmap: Compose skipped the
        // draw because the ImageBitmap instance was unchanged, so the surface
        // kept whatever the bitmap held before setPixels. The ball rendered for
        // minutes and the screenshot stayed black.
        painted++
    }

    // onSizeChanged MUST stay on whatever we draw with. It is the only thing
    // that ever set `size`; when Canvas was replaced by Image the modifier was
    // dropped, size stayed 0x0, and the render guard skipped every frame.
    androidx.compose.foundation.Image(
        bitmap = img?.asImageBitmap()
            ?: androidx.compose.ui.graphics.ImageBitmap(
                size.width.coerceAtLeast(1), size.height.coerceAtLeast(1)),
        contentDescription = null,
        modifier = modifier.onSizeChanged { size = it }
    )
    // Reading `painted` here is what ties draw-phase invalidation to the
    // pixels just written. Without this read the value is write-only and the
    // draw is still skipped.
    @Suppress("UNUSED_EXPRESSION")
    painted
}
