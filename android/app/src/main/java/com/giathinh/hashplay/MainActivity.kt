package com.giathinh.hashplay

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

class MainActivity : ComponentActivity() {

    var pendingMagnet by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // launched via a magnet:?xt=… link from a browser
        intent?.dataString?.takeIf { it.startsWith("magnet:") }?.let {
            pendingMagnet = it
        }

        setContent {
            HashPlayTheme {
                // First run gets an explanation before the cold system dialog.
                // The magnet path skips it: a user arriving from a browser to
                // play a specific magnet has already chosen to do the thing
                // and does not need onboarding in the way.
                var setup by remember { mutableStateOf<SetupState?>(null) }

                // The ncs flavor ships its own player screen. Routed by flag
                // rather than by flavor-specific source sets, because Android
                // cannot have two classes of the same name in a variant and
                // source-set overriding is not something it supports.
                @androidx.compose.runtime.Composable
                fun Route(pending: String?, consumed: () -> Unit) {
                    if (BuildConfig.NCS_UI) {
                        NcsPlayerScreen(pendingMagnet = pending, onMagnetConsumed = consumed)
                    } else {
                        PlayerScreen(pendingMagnet = pending, onMagnetConsumed = consumed)
                    }
                }

                if (pendingMagnet != null) {
                    Route(pendingMagnet) { pendingMagnet = null }
                } else {
                    val s = setup ?: SetupState(this).also { setup = it }
                    if (s.needsSetup()) {
                        SetupScreen(onDone = { setup = SetupState(this).also { it.complete() } })
                    } else {
                        Route(null) { pendingMagnet = null }
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        intent.dataString?.takeIf { it.startsWith("magnet:") }?.let {
            pendingMagnet = it
        }
    }
}
