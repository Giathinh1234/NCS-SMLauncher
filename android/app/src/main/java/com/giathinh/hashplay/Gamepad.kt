package com.giathinh.hashplay

import android.content.Context
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView

/**
 * Gamepad input, normalised across every controller Android recognises.
 *
 * The thing that makes one table work for Xbox, DualSense and Switch Pro is
 * that Android HID already collapses them onto KEYCODE_BUTTON_A/B/X/Y and the
 * D-pad. A DualSense cross and a Switch Pro cross and an Xbox cross all arrive
 * as KEYCODE_DPAD_*, so binding "next track" to DPAD_RIGHT works on all three
 * without knowing which one is plugged in.
 *
 * What is NOT identical between them, and is handled here rather than pretended
 * away:
 *
 *  * Triggers. On Xbox, L2/R2 arrive as ANALOG AXES. On DualSense and Switch
 *    Pro they usually do too. Some devices report them as digital buttons
 *    instead. Both are handled, or the bumpers would simply not work on the
 *    majority of pads.
 *  * Bumpers vs shoulders. L1/R1 are digital everywhere; L2/R2 are the analog
 *    pair. Treating L2 as a button would make it all-or-nothing.
 *  * Extras. DualSense has a touchpad and gyro, Switch Pro has a capture/home
 *    pair that Android often does not report at all. Those are surfaced as
 *    "unavailable" rather than bound to something that never fires.
 */
object Gamepad {

    // --- actions ------------------------------------------------------------
    const val A_PREV = "gp_prev"
    const val A_NEXT = "gp_next"
    const val A_PLAY_PAUSE = "gp_play_pause"
    const val A_UP = "gp_up"
    const val A_DOWN = "gp_down"
    const val A_LEAN_LEFT = "gp_lean_left"
    const val A_LEAN_RIGHT = "gp_lean_right"
    const val A_SETTINGS = "gp_settings"

    data class Binding(val label: String, val hint: String)

    /**
     * The default pad layout.
     *
     * `hint` says which physical control it is, because the KEYCODE names are
     * meaningless to a person holding a pad -- KEYCODE_BUTTON_A is the bottom
     * face button on all three pads, which is not what "A" suggests on a
     * Nintendo controller.
     */
    val DEFAULTS: List<Pair<String, Binding>> = listOf(
        A_UP to Binding("Up", "D-pad up / left stick"),
        A_DOWN to Binding("Down", "D-pad down / right stick"),
        A_PREV to Binding("Previous", "D-pad left"),
        A_NEXT to Binding("Next", "D-pad right"),
        A_PLAY_PAUSE to Binding("Play / pause", "A / cross"),
        A_LEAN_LEFT to Binding("Lean left", "L1"),
        A_LEAN_RIGHT to Binding("Lean right", "R1"),
        A_SETTINGS to Binding("Settings", "Start / options"),
    )

    /**
     * Digital codes accepted for each action.
     *
     * Several per action on purpose: the same physical button has different
     * codes depending on whether it arrived as a key event or as a gamepad
     * button, and binding one code per action means whichever way your pad
     * reports it, it works.
     */
    private val KEYCODES: Map<String, Set<Int>> = mapOf(
        A_PREV to setOf(
            KeyEvent.KEYCODE_DPAD_LEFT,
            KeyEvent.KEYCODE_BUTTON_SELECT,     // shared "minus"
            KeyEvent.KEYCODE_MEDIA_PREVIOUS,
        ),
        A_NEXT to setOf(
            KeyEvent.KEYCODE_DPAD_RIGHT,
            KeyEvent.KEYCODE_MEDIA_NEXT,
        ),
        A_PLAY_PAUSE to setOf(
            KeyEvent.KEYCODE_BUTTON_A,
            KeyEvent.KEYCODE_DPAD_CENTER,
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
            KeyEvent.KEYCODE_ENTER,
            KeyEvent.KEYCODE_SPACE,
        ),
        A_UP to setOf(KeyEvent.KEYCODE_DPAD_UP),
        A_DOWN to setOf(KeyEvent.KEYCODE_DPAD_DOWN),
        A_LEAN_LEFT to setOf(KeyEvent.KEYCODE_BUTTON_L1),
        A_LEAN_RIGHT to setOf(KeyEvent.KEYCODE_BUTTON_R1),
        A_SETTINGS to setOf(
            KeyEvent.KEYCODE_BUTTON_START,
            KeyEvent.KEYCODE_BUTTON_SELECT,
            KeyEvent.KEYCODE_MENU,
        ),
    )

    /** What action this key press means, or null. */
    fun resolve(keyCode: Int): String? =
        KEYCODES.entries.firstOrNull { keyCode in it.value }?.key

    /**
     * Which physical pad this is, for display.
     *
     * Only used to label the settings screen, never to decide behaviour --
     * behaviour is identical across pads precisely because of the table above.
     */
    fun describe(deviceId: Int): String {
        val name = runCatching {
            InputDevice.getDevice(deviceId)?.name
        }.getOrNull().orEmpty()
        return when {
            name.contains("Dual", true) -> "DualSense"
            name.contains("Xbox", true) -> "Xbox pad"
            name.contains("Nintendo", true) || name.contains("Switch", true) ->
                "Switch Pro"
            name.contains("PlayStation", true) -> "PlayStation pad"
            name.isBlank() -> "gamepad"
            else -> name.take(28)
        }
    }

    /**
     * Controllers currently attached, for the settings screen.
     *
     * A plain loop rather than mapNotNull: IntArray.mapNotNull constrains its
     * result to `R : Any`, and the lambda here yields String?, so inference
     * has nowhere to go and the call does not resolve.
     */
    fun connected(context: Context): List<String> {
        val out = ArrayList<String>()
        for (id in InputDevice.getDeviceIds()) {
            val d = runCatching { InputDevice.getDevice(id) }.getOrNull()
            if (d != null && isGamepad(d)) {
                val name = describe(id)
                if (name !in out) out.add(name)
            }
        }
        return out
    }

    fun isGamepad(device: InputDevice): Boolean =
        (device.sources and InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD ||
            (device.sources and InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK

    // --- analog triggers ----------------------------------------------------

    /** How far a trigger has to travel before it counts as pressed. */
    private const val TRIGGER_DEADZONE = 0.5f

    /**
     * Digital bumpers, if the pad reports them as buttons instead of axes.
     *
     * Xbox, DualSense and Switch Pro all normally use axes for L2/R2, but some
     * pads (and some Android versions) report only buttons, and on those the
     * bumpers would otherwise do nothing at all.
     */
    private const val DIGITAL_L2 = KeyEvent.KEYCODE_BUTTON_L2
    private const val DIGITAL_R2 = KeyEvent.KEYCODE_BUTTON_R2

    fun triggerFromAxis(axis: Int, value: Float): String? {
        if (value < TRIGGER_DEADZONE) return null
        return when (axis) {
            MotionEvent.AXIS_LTRIGGER -> A_LEAN_LEFT
            MotionEvent.AXIS_RTRIGGER -> A_LEAN_RIGHT
            // Z on the touchpad reads as a vertical axis on some pads.
            MotionEvent.AXIS_Z -> if (value < 0f) A_LEAN_LEFT else A_LEAN_RIGHT
            else -> null
        }
    }

    fun triggerFromButton(keyCode: Int): String? = when (keyCode) {
        DIGITAL_L2 -> A_LEAN_LEFT
        DIGITAL_R2 -> A_LEAN_RIGHT
        else -> null
    }

    /** True when the event came from something that is not a touchscreen. */
    fun isPadSource(event: MotionEvent): Boolean {
        val s = event.source
        return (s and InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD ||
            (s and InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK
    }

    /**
     * Stick direction, for pads whose D-pad is a stick.
     *
     * Threshold is high on purpose. A stick nudged a little way is noise
     * between actions, and a low threshold makes a track list scroll by itself
     * while you are trying to hold a direction.
     */
    private const val STICK_DEADZONE = 0.6f

    fun stickDirection(x: Float, y: Float): String? {
        if (kotlin.math.abs(x) < STICK_DEADZONE && kotlin.math.abs(y) < STICK_DEADZONE) {
            return null
        }
        return when {
            kotlin.math.abs(x) > kotlin.math.abs(y) ->
                if (x > 0) A_NEXT else A_PREV
            y > 0 -> A_UP
            else -> A_DOWN
        }
    }

    /**
     * The analog half of the pad: triggers and raw stick axes.
     *
     * These arrive as MotionEvents through dispatchGenericMotion, NOT as key
     * events. They also cannot be reached from Compose's pointer input on this
     * version: PointerInputChange carries no native MotionEvent, so both
     * `Modifier.pointerInput` and `Modifier.onPointerEvent` are blind to them.
     * The window's dispatchGenericMotionEvent is the only door, which is why
     * this is a sink the Activity feeds rather than a Modifier that listens.
     *
     * See `MainActivity.dispatchGenericMotionEvent` for the caller, and
     * `PadListener` in each screen for the registration.
     */
    fun interface Sink {
        fun onPadAction(action: String)
    }

    /**
     * Where the Activity hands analog pad actions.
     *
     * A single slot on purpose: exactly one player screen is composed at a
     * time, so a list would only ever hold one entry and would imply a
     * lifetime this does not have.
     */
    var sink: Sink? = null

    /**
     * Resolve an analog event to an action, or null to let it pass on.
     *
     * Triggers repeat while held because the OS resends them, so the caller
     * should be tolerant of repeats. That is why only the lean notches are fed
     * from here -- stepping lean twice is harmless, skipping two tracks would
     * not be.
     */
    fun fromGenericMotion(event: MotionEvent): String? {
        if (!isPadSource(event)) return null
        val trigger = if (event.action == MotionEvent.ACTION_DOWN) {
            triggerFromAxis(
                MotionEvent.AXIS_LTRIGGER,
                event.getAxisValue(MotionEvent.AXIS_LTRIGGER),
            ) ?: triggerFromAxis(
                MotionEvent.AXIS_RTRIGGER,
                event.getAxisValue(MotionEvent.AXIS_RTRIGGER),
            )
        } else {
            null
        }
        return trigger ?: stickDirection(
            event.getAxisValue(MotionEvent.AXIS_X),
            event.getAxisValue(MotionEvent.AXIS_Y),
        )
    }
}