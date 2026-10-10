package com.giathinh.hashplay

import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import android.content.Context
import android.content.SharedPreferences

/**
 * Settings shared by every flavor, and the theme/keyboard split.
 *
 * All three builds -- full, lite and ncs -- read and write this one store, so
 * a setting added here appears in all of them with no per-flavor work. Each app
 * still keeps its OWN copy: they are separate packages with separate
 * sandboxes, so "shared" means shared source, not a synced database. Making
 * them actually sync would need a shared userId or a ContentProvider, which is
 * a much larger change than it sounds.
 */
object HashSettings {

    private const val FILE = "hashplay_settings"

    /**
     * How the app presents itself.
     *
     * These are not colour schemes. They are different assumptions about how
     * the player is being driven, and that changes the layout:
     *
     *  TOUCH    everything is a big target, controls sit under the thumb, the
     *           library leads. The phone assumption.
     *  KEYBOARD everything is compact, the library stays visible because you
     *           are navigating by key, and the controls advertise the keys
     *           that drive them. Also what a controller should get.
     *
     * KEYBOARD is the default here because it is the one that still works when
     * a keyboard or controller is attached, and it degrades to something
     * usable on a small screen.
     */
    enum class Theme { TOUCH, KEYBOARD;

        val isTouch: Boolean get() = this == TOUCH

        companion object {
            fun parse(raw: String?): Theme =
                entries.firstOrNull { it.name == raw } ?: KEYBOARD
        }
    }

    fun open(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    // --- theme --------------------------------------------------------------
    fun theme(p: SharedPreferences): Theme = Theme.parse(p.getString(KEY_THEME, null))

    fun setTheme(p: SharedPreferences, theme: Theme) {
        p.edit().putString(KEY_THEME, theme.name).apply()
    }

    // --- visualizer as background -------------------------------------------
    /**
     * Draw the visualizer full-bleed behind everything, instead of inside a
     * bordered box on one side.
     *
     * Only offered on the KEYBOARD theme, and the UI only *shows* the toggle
     * there. A full-bleed reactive background behind a thumb-driven layout is
     * unreadable -- the light moves under the controls you are trying to hit.
     * On a keyboard or pad layout nothing moves under your fingers.
     */
    fun backgroundVisualizer(p: SharedPreferences): Boolean =
        p.getBoolean(KEY_BG_VIS, false)

    fun setBackgroundVisualizer(p: SharedPreferences, on: Boolean) {
        p.edit().putBoolean(KEY_BG_VIS, on).apply()
    }

    /** The near-black the visualizer box used to be filled with. */
    val VisualizerInk = androidx.compose.ui.graphics.Color(0xFF07090D)

    // --- lean ---------------------------------------------------------------
    const val LEAN_MIN = -1f
    const val LEAN_MAX = 1f
    const val LEAN_STEP = 0.05f

    /**
     * Read the lean without assuming the stored type.
     *
     * SharedPreferences can hold anything, including a String if the file was
     * written by something else, and getFloat throws on a type mismatch rather
     * than returning a default.
     */
    fun lean(p: SharedPreferences): Float =
        clampLean(runCatching { p.getFloat(KEY_LEAN, 0f) }.getOrElse { 0f })

    fun setLean(p: SharedPreferences, value: Float) {
        p.edit().putFloat(KEY_LEAN, clampLean(value)).apply()
    }

    /**
     * Step the lean, or take a dragged value outright.
     *
     * The snap is not cosmetic: 0.05f added three times in Float is not 0.15f,
     * so an unsnapped value drifts on every press and lands on disk as
     * 0.15000001. Dragging snaps to the same grid the notches use, so the two
     * cannot disagree about where the stops are.
     */
    fun stepLean(current: Float, direction: Int, dragValue: Float? = null): Float {
        val raw = if (dragValue != null) dragValue else current + LEAN_STEP * direction
        return clampLean(roundToStep(raw))
    }

    private fun roundToStep(value: Float): Float =
        Math.round(value / LEAN_STEP) * LEAN_STEP

    /** A fraction, always usable. NaN would make coerceIn throw. */
    fun clampLean(value: Float): Float {
        if (value.isNaN() || value.isInfinite()) return 0f
        return value.coerceIn(LEAN_MIN, LEAN_MAX)
    }

    // --- gain ---------------------------------------------------------------
    const val GAIN_STEP = 0.05f

    fun gain(p: SharedPreferences): Float =
        clampGain(runCatching { p.getFloat(KEY_GAIN, 1f) }.getOrElse { 1f })

    fun setGain(p: SharedPreferences, value: Float) {
        p.edit().putFloat(KEY_GAIN, clampGain(value)).apply()
    }

    fun stepGain(current: Float, direction: Int, dragValue: Float? = null): Float {
        val raw = if (dragValue != null) dragValue else current + GAIN_STEP * direction
        return clampGain(Math.round(raw / GAIN_STEP) * GAIN_STEP)
    }

    fun clampGain(value: Float): Float {
        if (value.isNaN() || value.isInfinite()) return 1f
        return value.coerceIn(0f, 1f)
    }

    // --- generic slider, for the settings overlay ---------------------------
    /** Live state for a settings slider while it is being dragged. */
    class SliderState(initial: Float) {
        var value: Float by androidx.compose.runtime.mutableFloatStateOf(initial)
    }

    private const val KEY_THEME = "ui_theme"
    private const val KEY_BG_VIS = "background_visualizer"
    private const val KEY_LEAN = "visualizer_lean"
    private const val KEY_GAIN = "player_gain"
}