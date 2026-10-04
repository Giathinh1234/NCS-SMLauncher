package com.giathinh.hashplay

import android.content.Context
import android.content.SharedPreferences

/**
 * Android port of the desktop's `settings.json`, restricted to what this build
 * actually uses.
 *
 * The desktop keeps three separate mechanisms and Android folds them into one
 * file: the desktop's `config.py` (booleans and the lean), its `actions.py`
 * Keymap (the rebindable keymap), and its `settings_panel.py` (the UI). Here
 * the Keymap is the interesting port, because a phone has no keyboard by
 * default -- but a tablet with a case keyboard, a TV remote, or an Android TV
 * box absolutely does, and a desktop-class player with no shortcuts on those
 * is a worse player than the one it is porting from.
 *
 * Bindings are stored as Android `KeyEvent` keycodes, not pygame names, because
 * that is what `KeyEvent.keyCode` reports on the platform this runs on.
 */
object NcsPrefs {

    private const val FILE = "ncs_player"

    // --- action ids. Stable strings, because they are persisted. -------------
    const val A_PREV = "select_prev"
    const val A_NEXT = "select_next"
    const val A_PLAY_PAUSE = "play_pause"
    const val A_PLAY = "play"
    const val A_SEEK_BACK = "seek_back"
    const val A_SEEK_FWD = "seek_forward"
    const val A_VOL_UP = "volume_up"
    const val A_VOL_DOWN = "volume_down"
    const val A_CYCLE_VIZ = "cycle_viz"
    const val A_TORRENT = "open_torrent"
    const val A_SETTINGS = "open_settings"
    const val A_QUIT = "quit"

    data class Binding(val label: String, val keyCode: Int, val group: String)

    /** Default bindings, chosen to match the desktop's. */
    val DEFAULTS: List<Pair<String, Binding>> = listOf(
        A_PREV to Binding("Previous track", 273, "transport"),      // KEYCODE_MEDIA_PREVIOUS
        A_NEXT to Binding("Next track", 275, "transport"),          // KEYCODE_MEDIA_NEXT
        A_PLAY_PAUSE to Binding("Play / pause", 85, "transport"),   // KEYCODE_MEDIA_PLAY_PAUSE
        A_PLAY to Binding("Play selected", 66, "transport"),        // KEYCODE_ENTER
        A_SEEK_BACK to Binding("Seek back 5s", 21, "transport"),    // KEYCODE_MINUS
        A_SEEK_FWD to Binding("Seek forward 5s", 20, "transport"),  // KEYCODE_EQUALS
        A_VOL_UP to Binding("Volume up", 24, "mix"),                // KEYCODE_VOLUME_UP
        A_VOL_DOWN to Binding("Volume down", 25, "mix"),            // KEYCODE_VOLUME_DOWN
        A_CYCLE_VIZ to Binding("Cycle visualizer", 82, "view"),     // KEYCODE_MENU
        A_TORRENT to Binding("Torrent overlay", 84, "view"),        // KEYCODE_SEARCH
        A_SETTINGS to Binding("Settings", 112, "view"),             // KEYCODE_SETTINGS
        A_QUIT to Binding("Quit", 113, "view"),                     // KEYCODE_ESCAPE
    )

    /**
     * Keys that must never be bound to a user action.
     *
     * Volume keys are excluded because the OS intercepts them for system
     * volume before the app ever sees them -- binding them here would create
     * a binding that silently does nothing, which is worse than not offering
     * it. Back/home are excluded because they are the app's only exits.
     */
    val RESERVED = setOf(4, 3, 24, 25, 164, 166, 187)

    fun open(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    // --- lean ---------------------------------------------------------------
    const val LEAN_MIN = -1f
    const val LEAN_MAX = 1f
    const val LEAN_STEP = 0.05f

    /**
     * Read the lean, tolerating anything a corrupt or hand-edited value can be.
     *
     * SharedPreferences values can be any type, including a String if the file
     * was written by something else, so this cannot assume a Float.
     */
    fun lean(p: SharedPreferences): Float {
        val raw = runCatching { p.getFloat(KEY_LEAN, 0f) }.getOrElse { 0f }
        return clampLean(raw)
    }

    fun setLean(p: SharedPreferences, value: Float) {
        p.edit().putFloat(KEY_LEAN, clampLean(value)).apply()
    }

    /** Mirrors `config.clamp_lean` on the desktop: a fraction, always usable. */
    fun clampLean(value: Float): Float {
        if (value.isNaN() || value.isInfinite()) return 0f
        return value.coerceIn(LEAN_MIN, LEAN_MAX)
    }

    /**
     * Step the lean by whole notches, or set it outright when `dragValue` is
     * supplied.
     *
     * The snap matters: 0.05f added three times in Float is not 0.15f, and a
     * value that drifts per press ends up written to disk as 0.15000001. A
     * dragged slider is snapped to the same grid so the notches and the drag
     * cannot disagree about where the stops are.
     */
    fun stepLean(current: Float, direction: Int, dragValue: Float? = null): Float {
        val raw = if (dragValue != null) dragValue else current + LEAN_STEP * direction
        return clampLean(roundToStep(raw))
    }

    /** Persist and return the nudged value. */
    fun nudgeLean(p: SharedPreferences, direction: Int): Float {
        val next = stepLean(lean(p), direction)
        setLean(p, next)
        return next
    }

    private fun roundToStep(value: Float): Float =
        Math.round(value / LEAN_STEP) * LEAN_STEP

    // --- visualizer mode ----------------------------------------------------
    // Persisted so the mode survives a relaunch. It did not before: a stray tap
    // could silently move you off the NCS ball and you came back to it only by
    // tapping your way round again.
    /** The sphere is the point of this build, so it is the default. */
    const val DEFAULT_VIZ_MODE = 3

    fun vizMode(p: SharedPreferences): Int =
        p.getInt(KEY_VIZ_MODE, DEFAULT_VIZ_MODE).coerceIn(0, LAST_VIZ_MODE)

    fun setVizMode(p: SharedPreferences, value: Int) {
        p.edit().putInt(KEY_VIZ_MODE, value.coerceIn(0, LAST_VIZ_MODE)).apply()
    }

    const val LAST_VIZ_MODE = 3          // VizMode.NCS_BALL

    // --- keymap -------------------------------------------------------------
    /**
     * action id -> keycode, merged over the defaults.
     *
     * Read per-call rather than cached: the settings screen rebinds live, and a
     * cached copy is exactly how "I rebound it and nothing changed" happens.
     */
    fun bindings(p: SharedPreferences): Map<String, Int> {
        val out = LinkedHashMap<String, Int>()
        for ((action, binding) in DEFAULTS) out[action] = binding.keyCode
        for (action in out.keys.toList()) {
            val stored = p.getInt(keyOf(action), Int.MIN_VALUE)
            if (stored != Int.MIN_VALUE) out[action] = stored
        }
        return out
    }

    fun bind(p: SharedPreferences, action: String, keyCode: Int) {
        if (action !in DEFAULTS.map { it.first }) return
        if (keyCode in RESERVED) return
        p.edit().putInt(keyOf(action), keyCode).apply()
    }

    /** Drop every override so the defaults apply again. */
    fun resetBindings(p: SharedPreferences) {
        val editor = p.edit()
        for (action in DEFAULTS.map { it.first }) editor.remove(keyOf(action))
        editor.apply()
    }

    /**
     * Which action this key press means, or null.
     *
     * Conflicts resolve to the FIRST action in DEFAULTS order, so a conflicting
     * rebind is predictable rather than dependent on map iteration order.
     */
    fun resolve(p: SharedPreferences, keyCode: Int): String? {
        val map = bindings(p)
        for ((action, _) in DEFAULTS) {
            if (map[action] == keyCode) return action
        }
        return null
    }

    /** Human-readable actions sharing a key, for the conflict warning. */
    fun conflicts(p: SharedPreferences): List<String> {
        val map = bindings(p)
        val seen = HashMap<Int, MutableList<String>>()
        for ((action, _) in DEFAULTS) {
            seen.getOrPut(map[action] ?: 0) { ArrayList() }.add(action)
        }
        return seen.values.filter { it.size > 1 }.map { it.joinToString(" + ") }
    }

    fun label(action: String): String =
        DEFAULTS.firstOrNull { it.first == action }?.second?.label ?: action

    fun group(action: String): String =
        DEFAULTS.firstOrNull { it.first == action }?.second?.group ?: ""

    private const val KEY_LEAN = "visualizer_lean"
    private const val KEY_VIZ_MODE = "viz_mode"
    private fun keyOf(action: String) = "key_$action"
}
