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
                if (pendingMagnet != null) {
                    PlayerScreen(
                        pendingMagnet = pendingMagnet,
                        onMagnetConsumed = { pendingMagnet = null }
                    )
                } else {
                    val s = setup ?: SetupState(this).also { setup = it }
                    if (s.needsSetup()) {
                        SetupScreen(onDone = { setup = SetupState(this).also { it.complete() } })
                    } else {
                        PlayerScreen(
                            pendingMagnet = null,
                            onMagnetConsumed = { pendingMagnet = null }
                        )
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
