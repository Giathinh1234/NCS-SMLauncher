package com.giathinh.hashplay

import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight

/**
 * The app's typefaces.
 *
 * Two pixel fonts, deliberately, because one was not enough:
 *
 *  Display   Silkscreen. A dot-matrix face that has no lowercase -- it renders
 *            everything as capitals. That is authentic 8-bit and it reads
 *            beautifully for short, shouty things: the wordmark, section
 *            headers, a button label. It is the wrong face for a track title,
 *            because "Never Gonna Give You Up" becomes an undifferentiated
 *            block of capitals and you lose the shape of the words.
 *
 *  Body      Pixelify Sans. A pixel face that keeps its case, so a track title
 *            stays legible at the small sizes a track list actually uses while
 *            still looking like it came out of a cartridge.
 *
 * Both are SIL Open Font License 1.1. The licence texts sit next to the font
 * files in res/font/ because OFL requires the licence to travel with the
 * fonts; the licences are why these files are in the repository at all.
 *
 * Note the desktop does NOT use a pixel font -- it asks pygame for
 * "consolas,menlo,dejavusansmono", which is a smooth monospace. The retro
 * feel on macOS comes from the sphere, not the typeface. Android therefore
 * ends up more 8-bit than the Mac app rather than matching it, which is what
 * was asked for, but it is worth being honest that the two are not the same
 * look.
 */
object PixelType {

    /** Dot-matrix, capitals only. Headers, the wordmark, button labels. */
    val Display = FontFamily(
        Font(R.font.silkscreen_regular, FontWeight.Normal),
        Font(R.font.silkscreen_bold, FontWeight.Bold),
    )

    /**
     * Pixel face with case intact. Track titles, values, anything a person
     * actually reads.
     *
     * One file serves every weight: it is a variable font, and Android only
     * synthesises a bold instance from it reliably. Real emphasis comes from
     * switching to [Display] at Bold rather than from faking weight here.
     */
    val Body = FontFamily(
        Font(R.font.pixelify_sans, FontWeight.Normal),
        Font(R.font.pixelify_sans, FontWeight.Medium),
        Font(R.font.pixelify_sans, FontWeight.Bold),
    )
}