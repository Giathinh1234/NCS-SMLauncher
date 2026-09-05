package com.giathinh.hashplay

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.State
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * NCS-style neon visualizer: rainbow bars with glow, mirror mode, and radial.
 */
enum class VizMode { BARS, MIRROR, RADIAL }

private fun hsv(hue: Float, s: Float = 0.95f, v: Float = 1f): Color {
    val h = ((hue % 1f) + 1f) % 1f
    val i = (h * 6).toInt()
    val f = h * 6 - i
    val p = v * (1 - s)
    val q = v * (1 - f * s)
    val t = v * (1 - (1 - f) * s)
    return when (i % 6) {
        0 -> Color(v, t, p); 1 -> Color(q, v, p)
        2 -> Color(p, v, t); 3 -> Color(p, q, v)
        4 -> Color(t, p, v); else -> Color(v, p, q)
    }
}

@Composable
fun Visualizer(
    spectrum: State<FloatArray>,
    mode: VizMode,
    modifier: Modifier = Modifier
) {
    Canvas(modifier.fillMaxSize()) {
        val mags = spectrum.value
        when (mode) {
            VizMode.BARS -> drawBars(mags, mirrored = false)
            VizMode.MIRROR -> drawBars(mags, mirrored = true)
            VizMode.RADIAL -> drawRadial(mags)
        }
    }
}

private fun DrawScope.baselineY() = size.height * 0.82f

private fun DrawScope.drawBars(mags: FloatArray, mirrored: Boolean) {
    val n = mags.size
    val gap = size.width * 0.0025f
    val barW = (size.width * 0.9f - gap * (n - 1)) / n
    var x = size.width * 0.05f
    val base = baselineY()

    for (i in 0 until n) {
        val col = hsv(i / n.toFloat())
        val bh = 6f + mags[i] * size.height * 0.55f

        // glow
        drawRoundRect(
            brush = Brush.verticalGradient(
                listOf(col.copy(alpha = 0.25f), Color.Transparent),
                startY = base - bh - 24f, endY = base
            ),
            topLeft = Offset(x - 4, base - bh - 20),
            size = Size(barW + 8, bh + 20),
            cornerRadius = CornerRadius(6f, 6f)
        )
        // bar
        drawRoundRect(
            color = col,
            topLeft = Offset(x, base - bh),
            size = Size(barW, bh),
            cornerRadius = CornerRadius(3f, 3f)
        )
        if (mirrored && bh > 10f) {
            drawRoundRect(
                color = col.copy(alpha = 0.35f),
                topLeft = Offset(x, base + gap),
                size = Size(barW, bh * 0.65f),
                cornerRadius = CornerRadius(3f, 3f)
            )
        }
        x += barW + gap
    }
}

private fun DrawScope.drawRadial(mags: FloatArray) {
    val cx = size.width / 2
    val cy = size.height / 2
    val rBase = minOf(size.width, size.height) * 0.22f

    for (i in mags.indices) {
        val ang = (2 * PI * i / mags.size - PI / 2).toFloat()
        val len = rBase * 0.15f + mags[i] * rBase * 0.9f
        val col = hsv(i / mags.size.toFloat())
        drawLine(
            color = col,
            start = Offset(cx + cos(ang) * rBase, cy + sin(ang) * rBase),
            end = Offset(cx + cos(ang) * (rBase + len),
                         cy + sin(ang) * (rBase + len)),
            strokeWidth = 7f
        )
    }
    // pulsing core
    val beat = mags.average().toFloat()
    val rCore = rBase * 0.35f * (1 + beat * 0.25f)
    val coreCol = hsv(0f)
    drawCircle(coreCol.copy(alpha = 0.22f), rCore, Offset(cx, cy))
    drawCircle(coreCol.copy(alpha = 0.75f), rCore * 0.55f, Offset(cx, cy))
}
