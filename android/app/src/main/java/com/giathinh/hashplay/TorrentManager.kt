package com.giathinh.hashplay

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.libtorrent4j.AlertListener
import org.libtorrent4j.SessionManager
import org.libtorrent4j.alerts.Alert
import org.libtorrent4j.alerts.TorrentFinishedAlert
import java.io.File

/**
 * Native BitTorrent downloads via libtorrent4j.
 * Mirrors the desktop TorrentManager: paste infohash/magnet, get files in
 * app-specific storage, which we hand to MediaStore so they show up as music.
 */
class TorrentManager(private val context: Context) {

    data class Status(
        val name: String,
        val progress: Float,
        val peers: Int,
        val downSpeedKBs: Int,
        val done: Boolean
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _statuses = MutableStateFlow<List<Status>>(emptyList())
    val statuses: StateFlow<List<Status>> get() = _statuses

    private val _messages = MutableStateFlow<String?>(null)
    val messages: StateFlow<String?> get() = _messages

    private var session: SessionManager? = null

    val downloadDir: File
        get() = File(context.getExternalFilesDir(null), "torrent-downloads")
            .apply { mkdirs() }

    @Synchronized
    fun start(uriRaw: String): Boolean {
        val magnet = normalize(uriRaw) ?: run {
            _messages.value = "Not a valid infohash / magnet link"
            return false
        }
        return try {
            val sm = session ?: SessionManager().also {
                it.addListener(object : AlertListener {
                    override fun types(): IntArray? = null   // receive all alerts
                    override fun alert(alert: Alert<*>) {
                        when (alert) {
                            is TorrentFinishedAlert -> {
                                _messages.value = "✔ Finished: ${alert.torrentName()}"
                                scanDownloaded()
                            }
                            else -> {}
                        }
                        publishStatuses()
                    }
                })
                it.start()
                session = it
            }
            sm.download(magnet, downloadDir, null)
            _messages.value = "Downloading…"
            true
        } catch (e: Exception) {
            _messages.value = "Could not start: ${e.message}"
            false
        }
    }

    private fun publishStatuses() {
        val sm = session ?: return
        if (!sm.isRunning) return
        val list = mutableListOf<Status>()
        try {
            val vec = sm.swig().get_torrents()
            for (i in 0 until vec.size) {
                val h = org.libtorrent4j.TorrentHandle(vec.get(i))
                val st = h.status()
                list += Status(
                    name = st.name(),
                    progress = st.progress(),
                    peers = st.numPeers(),
                    downSpeedKBs = (st.downloadPayloadRate() / 1000).toInt(),
                    done = st.progress() >= 1f
                )
            }
        } catch (_: Exception) {}
        _statuses.value = list.takeLast(3)
    }

    init {
        scope.launch {
            while (isActive) {
                publishStatuses()
                delay(1000)
            }
        }
    }

    private fun scanDownloaded() {
        val paths = downloadDir.listFiles { f ->
            f.extension.lowercase() in setOf("mp3", "wav", "ogg", "flac", "m4a")
        } ?: return
        for (f in paths) {
            android.media.MediaScannerConnection.scanFile(
                context, arrayOf(f.absolutePath), null, null)
        }
    }

    companion object {
        fun normalize(raw: String): String? {
            val u = raw.trim()
            if (u.isEmpty()) return null
            if (u.startsWith("magnet:") || u.startsWith("http")) return u
            return if (u.length == 40 && u.all { it in "0123456789abcdefABCDEF" })
                "magnet:?xt=urn:btih:" + u.lowercase()
            else if (u.length == 32 &&
                u.all { it in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" })
                "magnet:?xt=urn:btih:" + u
            else null
        }
    }
}
