package com.giathinh.hashplay

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

private val DarkColors = darkColorScheme(
    primary = androidx.compose.ui.graphics.Color(0xFF00E6B8),
    background = androidx.compose.ui.graphics.Color(0xFF0A0C12),
    surface = androidx.compose.ui.graphics.Color(0xFF12151F),
    onBackground = androidx.compose.ui.graphics.Color(0xFFE8EAF2),
    onSurface = androidx.compose.ui.graphics.Color(0xFFE8EAF2)
)

/**
 * Every text style the Material components default to, moved onto the pixel
 * faces.
 *
 * This is the load-bearing part of the 8-bit change. PlayerScreen and
 * SetupScreen never name a font at all -- they ask Material for
 * `typography.bodyLarge` and whatever the theme supplies is what renders.
 * Overriding typography here reaches those screens AND every Material widget
 * (TextButton, Slider, dialogs) without touching either file, which is why a
 * new screen cannot quietly come out in the system typeface again.
 *
 * Line height is loosened a little on the body styles. Pixel faces are drawn
 * on a coarse grid and sit tight at Material's default leading, which clips
 * the descenders of g/y/p -- exactly the letters a track title is full of.
 */
private fun style(
    size: Int,
    weight: FontWeight = FontWeight.Normal,
    lineHeight: Int = (size * 1.45f).toInt(),
    family: FontFamily = PixelType.Body,
) = TextStyle(fontFamily = family, fontWeight = weight, fontSize = size.sp, lineHeight = lineHeight.sp)

private val PixelTypography = androidx.compose.material3.Typography(
    displayLarge = style(40, FontWeight.Bold, 46, PixelType.Display),
    displayMedium = style(34, FontWeight.Bold, 40, PixelType.Display),
    displaySmall = style(28, FontWeight.Bold, 34, PixelType.Display),

    headlineLarge = style(30, FontWeight.Bold, 36, PixelType.Display),
    headlineMedium = style(26, FontWeight.Bold, 32, PixelType.Display),
    headlineSmall = style(22, FontWeight.Bold, 28, PixelType.Display),

    titleLarge = style(20, FontWeight.Bold, 26, PixelType.Display),
    titleMedium = style(16, FontWeight.Normal, 22),
    titleSmall = style(14, FontWeight.Normal, 20),

    bodyLarge = style(16, lineHeight = 24),
    bodyMedium = style(14, lineHeight = 20),
    bodySmall = style(12, lineHeight = 18),

    labelLarge = style(14, FontWeight.Bold, 20, PixelType.Display),
    labelMedium = style(12, FontWeight.Normal, 16),
    labelSmall = style(11, FontWeight.Normal, 14),
)

@Composable
fun HashPlayTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColors,
        typography = PixelTypography,
        content = content
    )
}