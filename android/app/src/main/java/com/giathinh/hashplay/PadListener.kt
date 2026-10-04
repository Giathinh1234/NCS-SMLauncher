package com.giathinh.hashplay

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect

/**
 * Registers the calling screen with [Gamepad.sink] for as long as it is
 * composed.
 *
 * The sink is cleared on dispose rather than left dangling: a stale sink would
 * keep calling into a screen that is gone, which shows up as a crash on the
 * next analog nudge instead of here.
 */
@Composable
fun PadListener(onAction: (String) -> Unit) {
    DisposableEffect(Unit) {
        Gamepad.sink = Gamepad.Sink { action -> onAction(action) }
        onDispose { Gamepad.sink = null }
    }
}
