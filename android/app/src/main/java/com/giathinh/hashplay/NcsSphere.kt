package com.giathinh.hashplay

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
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

    /** A point on the membrane, in unit-sphere space. */
    internal class P(val lat: Float, val lon: Float, val seed: Float)

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
            val n = max(1, (cols * cosLat).toInt())
            for (c in 0 until n) {
                val lon = 2f * Math.PI.toFloat() * c / n
                out.add(P(lat, lon, (r * 131 + c * 17) % 97 / 97f))
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
               bass: Float, level: Float, tier: Tier, out: IntArray,
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
        if (acc.size < width * height * 3 || hits.size < width * height) {
            scratchAcc = FloatArray(width * height * 3)
            scratchHits = IntArray(width * height)
            return render(width, height, mags, bass, level, tier, out, pts)
        }
        // Domain-warp amplitude rises with bass, and the desktop found that a
        // larger amplitude RESHUFFLES which points end up outermost; the
        // explicit radial scale term below keeps the response monotonic.
        val warpAmp = 0.06f + 0.16f * bass.coerceIn(0f, 1f)
        val radialScale = 1f + 0.035f * level.coerceIn(0f, 1f)
        val t = System.nanoTime() * 0.00000035f

        for (p in pts) {
            val cl = cos(p.lat)
            val sl = sin(p.lat)
            val clat = cos(p.lon + t)
            val slat = sin(p.lon + t)
            // Unit-sphere position.
            var x = cl * clat
            var y = sl
            var z = cl * slat

            // Sample the spectrum by latitude so low frequencies drive the
            // equator and highs the caps, which is what the desktop does.
            val band = ((p.lat + 1.5708f) / 3.1416f * (mags.size - 1))
                .toInt().coerceIn(0, mags.size - 1)
            val mag = mags[band] + 0.15f * mags[(band / 3).coerceIn(0, mags.size - 1)]

            // Domain warp: two out-of-phase travelling waves, so the bands
            // twist instead of merely pulsing.
            val w1 = sin(p.lon * 3f + t * 2.1f + p.seed * 6.2f)
            val w2 = cos(p.lat * 4f - t * 1.7f + p.seed * 3.1f)
            val warp = warpAmp * (mag * w1 + 0.6f * mag * w2)

            val scale = radialScale * (1f + warp)
            val px = cx + x * radius * scale
            val py = cy - y * radius * scale
            if (px < 0f || py < 0f || px >= width || py >= height) continue

            // z drives brightness: front of the sphere is lit, back is dim.
            val depth = (z + 1f) * 0.5f
            val bright = 0.16f + 0.84f * depth * depth
            val vv = bright * (0.55f + 0.9f * mag.coerceIn(0f, 1.4f))

            val idx = ((py.toInt()) * width + px.toInt())
            val i3 = idx * 3
            // Pale gold crest, dark olive trough, from the reference.
            acc[i3] += 255f * vv
            acc[i3 + 1] += 227f * vv
            acc[i3 + 2] += 152f * vv * (0.35f + 0.65f * depth)
            hits[idx] += 1
        }

        // Normalize by density, then blend toward the documented dark-olive gap
        // colour so the interior keeps its dark dots instead of going black.
        // Without this the additive splat saturates the interior to a flat blob.
        val norm = 1f / max(1f, 0.30f * (pts.size.toFloat() / (radius * radius * 3.14f)))
        for (i in 0 until hits.size) {
            val h = hits[i]
            if (h == 0) continue
            val f = norm * (0.55f + 0.45f * min(1f, h / 3f))
            val i3 = i * 3
            val gapR = 55 / 255f   // dark-olive dot, from the reference
            val gapG = 45 / 255f
            val gapB = 9 / 255f
            val r = to255(max(acc[i3] * f / 255f, gapR))
            val g = to255(max(acc[i3 + 1] * f / 255f, gapG))
            val b = to255(max(acc[i3 + 2] * f / 255f, gapB))
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
        val targetPts = (radius * radius * 0.26f * density).toInt().coerceIn(1500, 42000)
        val rows = max(24, sqrt(targetPts.toFloat() / 3.0f).toInt())
        val cols = max(24, targetPts / rows)
        return grid(rows, cols)
    }

    enum class Tier { FULL, LITE }

    // Reused across frames; grown only when the surface changes size.
    private var scratchAcc = FloatArray(0)
    private var scratchHits = IntArray(0)

    companion object {
        fun clamp255(v: Float): Int = max(0f, min(255f, v)).toInt()
        fun to255(f: Float): Int = max(0f, min(1f, f)).toInt() * 255
    }
}

/** Compose wrapper: renders the sphere into a Canvas via an ImageBitmap. */
@Composable
fun NcsSphereView(mags: FloatArray, bass: Float, level: Float,
                  tier: NcsSphere.Tier, modifier: Modifier = Modifier.fillMaxSize()) {
    val sphere = remember { NcsSphere() }
    // Both are keyed on size so they are rebuilt only when the ball actually
    // changes, never per frame. remember() must NOT be called inside Canvas's
    // draw lambda -- that is a draw-scope, not a composition scope.
    var size by remember { mutableStateOf(IntSize.Zero) }
    val grid = remember(size.width, tier) {
        if (size.width > 0) sphere.gridFor(size.width, size.height, tier) else emptyList()
    }
    val buf = remember(size) {
        if (size.width > 0) IntArray(size.width * size.height) else IntArray(0)
    }
    Canvas(modifier = modifier.onSizeChanged { size = it }) {
        val w = size.width
        val h = size.height
        if (w <= 0 || h <= 0 || buf.isEmpty() || grid.isEmpty()) return@Canvas
        sphere.render(w, h, mags, bass, level, tier, buf, grid)
        val bmp = android.graphics.Bitmap
            .createBitmap(buf, w, h, android.graphics.Bitmap.Config.ARGB_8888)
            .asImageBitmap()
        drawImage(bmp, Offset.Zero)
    }
}
