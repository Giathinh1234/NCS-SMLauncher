// ---------------------------------------------------------------------------
// A drop-in replacement for the real TorrentManager, used by the LITE flavor.
//
// The real one wraps libtorrent4j, whose native library is 12.3 MB of the APK --
// the single largest item in the build, and the same trade the desktop `lite`
// tier makes with libtorrent on macOS. The desktop build gates the feature
// behind a build-time constant and keeps the module out of the bundle; this is
// the Android equivalent.
//
// This file must expose EXACTLY the same surface as the real one -- the nested
// `Status` type, the two StateFlows, `downloadDir`, `start()` and the
// companion `normalize()` -- because PlayerScreen.kt is shared between both
// flavors and compiles against this file instead of the real one. When the real
// class changes shape, this has to change with it or the lite flavor stops
// compiling; that is deliberate, so the divergence is caught by the build rather
// than at runtime on a phone.
//
// Nothing here imports libtorrent4j, which is the entire point: with no
// reachable reference the dependency is dropped from the lite APK.
// ---------------------------------------------------------------------------
package com.giathinh.hashplay

import android.content.Context
import java.io.File
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

class TorrentManager(@Suppress("UNUSED_PARAMETER") private val context: Context) {

    /** Identical shape to the real one, so shared UI code compiles unchanged. */
    data class Status(
        val name: String,
        val progress: Float,
        val peers: Int,
        val downSpeedKBs: Int,
        val done: Boolean
    )

    private val _statuses = MutableStateFlow<List<Status>>(emptyList())
    val statuses: StateFlow<List<Status>> get() = _statuses

    private val _messages = MutableStateFlow<String?>(null)
    val messages: StateFlow<String?> get() = _messages

    /** Kept so the download path exists, but nothing is ever written to it. */
    val downloadDir: File
        get() = File(context.getExternalFilesDir(null), "torrent-downloads")
            .apply { mkdirs() }

    /** Always false, and never throws. Says why, so the UI is not a dead end. */
    fun start(uriRaw: String): Boolean {
        _messages.value =
            "Torents are not in the lite build — use the full APK for that."
        return false
    }

    companion object {
        /** Lite accepts nothing, exactly as the real one rejects a bad hash. */
        @Suppress("UNUSED_PARAMETER")
        fun normalize(raw: String): String? {
            if (raw.isBlank()) return null
            return null
        }
    }
}
