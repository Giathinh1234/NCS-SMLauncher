package com.giathinh.hashplay

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts

/**
 * First run, Android-shaped.
 *
 * The desktop's wizard asks for a library folder and probes whether ffmpeg is
 * installed. Neither question exists on Android -- the media permission covers
 * the library, and there is no PATH to probe -- so this is a two-step flow:
 * explain, then ask.
 *
 * Explaining first is the part that matters. A cold system permission dialog
 * with no context is the single most common reason people deny a media
 * permission and then wonder why the app shows an empty library forever.
 */
@Composable
fun SetupScreen(onDone: () -> Unit) {

    val context = LocalContext.current
    val setup = remember { SetupState(context) }

    var asked by remember { mutableStateOf(false) }
    var denied by remember { mutableStateOf(false) }

    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { granted ->
        // Denying is allowed and final: the app still works, the library is
        // just empty, and the UI says so rather than silently showing nothing.
        setup.complete()
        onDone()
    }

    fun finish() {
        setup.complete()
        onDone()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(28.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text("NCS Player", style = MaterialTheme.typography.headlineMedium,
             fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(10.dp))
        Text(
            "Your music stays on this device. NCS Player reads your audio library " +
                "and plays it -- nothing is uploaded.",
            style = MaterialTheme.typography.bodyMedium
        )

        Spacer(Modifier.height(26.dp))

        Text("NCS Player needs permission to read your music so it can list it " +
            "in your library. Without it there is nothing to show.",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.fillMaxWidth()
        )

        Spacer(Modifier.height(24.dp))

        Button(
            onClick = {
                asked = true
                val missing = Permissions.needed()
                    .filterNot { Permissions.granted(context, it) }
                if (missing.isEmpty()) finish()
                else launcher.launch(missing.toTypedArray())
            },
            modifier = Modifier.fillMaxWidth()
        ) { Text(if (asked && denied) "Ask again" else "Continue") }

        Spacer(Modifier.height(10.dp))

        // Escape is an answer -- setup_wizard.py:224-234 never reopens once
        // marked complete. The user can grant from system settings later.
        OutlinedButton(onClick = ::finish, modifier = Modifier.fillMaxWidth()) {
            Text("Not now")
        }

        if (asked && denied) {
            Spacer(Modifier.height(18.dp))
            Text(
                "No problem -- the library will be empty until you allow " +
                    "access. You can turn it on later in Settings.",
                style = MaterialTheme.typography.bodySmall
            )
        }

        Spacer(Modifier.height(18.dp))
        Text(
            if (Permissions.allGranted(context))
                "Library access granted. Enjoy."
            else
                "Library access not granted yet.",
            style = MaterialTheme.typography.bodySmall
        )
    }
}
