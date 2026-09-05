package com.giathinh.hashplay

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

@Composable
fun PlayerScreen(
    pendingMagnet: String?,
    onMagnetConsumed: () -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val controller = remember { PlayerController(context) }
    val torrents = remember { TorrentManager(context) }

    var tracks by remember { mutableStateOf<List<Track>>(emptyList()) }
    var selected by remember { mutableStateOf(-1) }
    var vizMode by remember { mutableStateOf(VizMode.BARS) }
    var showTorrentSheet by remember { mutableStateOf(false) }
    var magnetInput by remember { mutableStateOf("") }

    val now by controller.state.collectAsState()
    val spectrumState = controller.spectrum.collectAsState()
    val tStatuses by torrents.statuses.collectAsState()
    val tMessage by torrents.messages.collectAsState()

    // load library + rescan when a torrent finishes
    fun refresh() {
        scope.launch(kotlinx.coroutines.Dispatchers.IO) {
            tracks = LibraryScanner.scan(context)
        }
    }
    LaunchedEffect(Unit) { refresh() }
    LaunchedEffect(tMessage) {
        if (tMessage?.startsWith("✔") == true) refresh()
    }
    // handle magnet shared into the app
    LaunchedEffect(pendingMagnet) {
        pendingMagnet?.let {
            torrents.start(it)
            onMagnetConsumed()
        }
    }

    DisposableEffect(Unit) { onDispose { controller.release() } }

    Column(Modifier.fillMaxSize().background(Color(0xFF0A0C12))) {

        // header
        Row(Modifier.fillMaxWidth().padding(16.dp, 20.dp, 16.dp, 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text("hashplay", color = Color(0xFF00E6B8), fontSize = 22.sp)
            Spacer(Modifier.width(10.dp))
            Text("stream · download · listen", color = Color(0xFF8B91A5), fontSize = 12.sp)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = { showTorrentSheet = true }) {
                Text("+ torrent", color = Color(0xFF00E6B8))
            }
        }

        // visualizer (tap to cycle mode)
        Box(Modifier.fillMaxWidth().height(220.dp)
            .clickable {
                vizMode = VizMode.entries[(vizMode.ordinal + 1) % VizMode.entries.size]
            }) {
            Visualizer(spectrumState, vizMode, Modifier.fillMaxSize())
            if (!now.isPlaying && now.title.isEmpty()) {
                Text("tap +torrent to paste an infohash",
                    color = Color(0xFF555C70), fontSize = 12.sp,
                    modifier = Modifier.align(Alignment.Center))
            }
        }

        // now playing bar
        if (now.title.isNotEmpty()) {
            Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                Text(
                    (if (now.isPlaying) "NOW PLAYING ▸ " else "PAUSED ▸ ") + now.title,
                    color = Color.White, fontSize = 13.sp, maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Slider(
                        value = if (now.durationMs > 0)
                            now.positionMs.toFloat() / now.durationMs else 0f,
                        onValueChange = { controller.seekTo(it) },
                        modifier = Modifier.weight(1f),
                        colors = SliderDefaults.colors(
                            thumbColor = Color(0xFF00E6B8),
                            activeTrackColor = Color(0xFF00E6B8),
                            inactiveTrackColor = Color(0xFF242A3A)
                        )
                    )
                    Text(formatMs(now.positionMs) + " / " + formatMs(now.durationMs),
                        color = Color(0xFF8B91A5), fontSize = 11.sp)
                    Spacer(Modifier.width(6.dp))
                    FilledTonalButton(onClick = { controller.togglePause() },
                        contentPadding = PaddingValues(4.dp)) {
                        Text(if (now.isPlaying) "❚❚" else "▶", color = Color(0xFF00E6B8))
                    }
                }
            }
        }

        // torrent status strip
        tStatuses.forEach { s ->
            Text(
                (if (s.done) "✔ done: " else "↓ ${"%.0f".format(s.progress * 100)}% " +
                        "${s.peers}p ${s.downSpeedKBs}kB/s ") + s.name,
                color = if (s.done) Color(0xFF00E6B8) else Color(0xFFAAC8E6),
                fontSize = 11.sp, maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(horizontal = 16.dp)
            )
        }
        tMessage?.let {
            Text(it, color = Color(0xFF96D2FF), fontSize = 11.sp,
                 modifier = Modifier.padding(horizontal = 16.dp), maxLines = 1)
        }

        // library
        Text("LIBRARY (${tracks.size})",
            color = Color(0xFF00E6B8), fontSize = 12.sp,
            modifier = Modifier.padding(16.dp, 12.dp, 16.dp, 4.dp))

        LazyColumn(Modifier.weight(1f).padding(horizontal = 8.dp)) {
            items(tracks.size) { i ->
                val t = tracks[i]
                val isSel = i == selected
                Row(
                    Modifier.fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .background(if (isSel) Color(0xFF00E6B8) else Color.Transparent)
                        .clickable {
                            selected = i
                            controller.play(t)
                        }
                        .padding(horizontal = 12.dp, vertical = 10.dp)
                ) {
                    Text(
                        t.title,
                        color = if (isSel) Color(0xFF0A0C12) else Color(0xFFE8EAF2),
                        fontSize = 14.sp, maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f)
                    )
                    Text(
                        formatMs(t.durationMs),
                        color = if (isSel) Color(0xFF0A0C12) else Color(0xFF8B91A5),
                        fontSize = 11.sp
                    )
                }
            }
        }
    }

    // torrent input sheet
    if (showTorrentSheet) {
        @OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
        ModalBottomSheet(onDismissRequest = { showTorrentSheet = false }) {
            Column(Modifier.padding(20.dp)) {
                Text("Paste infohash or magnet link",
                    color = Color(0xFF00E6B8), fontSize = 15.sp)
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = magnetInput,
                    onValueChange = { magnetInput = it },
                    placeholder = { Text("magnet:?xt=… or bare 40-char hash") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(Modifier.height(10.dp))
                Button(onClick = {
                    if (magnetInput.isNotBlank()) {
                        torrents.start(magnetInput)
                        magnetInput = ""
                        showTorrentSheet = false
                    }
                }, modifier = Modifier.align(Alignment.End)) {
                    Text("Download")
                }
                Spacer(Modifier.height(24.dp))
            }
        }
    }
}

private fun formatMs(ms: Long): String {
    val s = ms / 1000
    return "${s / 60}:${"%02d".format(s % 60)}"
}
