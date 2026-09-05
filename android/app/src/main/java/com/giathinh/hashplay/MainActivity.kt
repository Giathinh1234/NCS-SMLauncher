package com.giathinh.hashplay

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
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
                PlayerScreen(
                    pendingMagnet = pendingMagnet,
                    onMagnetConsumed = { pendingMagnet = null }
                )
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
