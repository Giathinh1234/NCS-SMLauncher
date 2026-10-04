package com.giathinh.hashplay

import android.view.MotionEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.pointer.pointerInput
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
    var vizMode by remember { mutableStateOf(VizMode.NCS_BALL) }
    var showTorrentSheet by remember { mutableStateOf(false) }
    var magnetInput by remember { mutableStateOf("") }

    // One settings store, shared with every flavor. Lean and gain live there
    // now rather than on this surface, so the player screen reads them and the
    // overlay is the only thing that writes them.
    val hashPrefs = remember { HashSettings.open(context) }
    var settingsOpen by remember { mutableStateOf(false) }
    var theme by remember { mutableStateOf(HashSettings.theme(hashPrefs)) }
    var lean by remember { mutableFloatStateOf(HashSettings.lean(hashPrefs)) }
    val listState = rememberLazyListState()

    // The stored gain is the starting level; the service owns it from then on.
    LaunchedEffect(Unit) { controller.setVolume(HashSettings.gain(hashPrefs)) }

    val now by controller.state.collectAsState()
    val spectrumState = controller.spectrum.collectAsState()

    // The frame clock. PlayerController.tick() refreshes position, spectrum and
    // decay, and NOTHING called it -- so the seek slider sat at 0:00 and the
    // visualizer never moved even while audio played. withFrameNanos is tied to
    // the composition, so it starts and stops with the screen for free.
    LaunchedEffect(Unit) {
        val start = withFrameNanos { it }
        var last = start
        while (true) {
            withFrameNanos { now ->
                // tick() takes elapsed seconds because the spectrum is a
                // function of playback time, not wall time.
                if (now - last >= 33_000_000L) {   // ~30 Hz is plenty
                    controller.tick((now - start) / 1_000_000_000f)
                    last = now
                }
            }
        }
    }
    val tStatuses by torrents.statuses.collectAsState()
    val tMessage by torrents.messages.collectAsState()

    // load library + rescan when a torrent finishes
    fun refresh() {
        scope.launch(kotlinx.coroutines.Dispatchers.IO) {
            // scan() handles its own provider failures, but a launch on IO with
            // no other guard means anything unexpected here is uncaught and
            // takes the process with it. An empty library is recoverable; a
            // crash on launch is not.
            try {
                tracks = LibraryScanner.scan(context)
            } catch (t: Exception) {
                tracks = emptyList()
            }
        }
    }

    // Keep the player's queue identical to the visible list, so "next" means
    // what the user sees and the notification's next button does the same.
    LaunchedEffect(tracks) {
        if (tracks.isNotEmpty()) controller.setPlaylist(tracks)
    }

    // Permissions are requested by SetupScreen on first run, behind an
    // explanation. PlayerScreen still re-asks ONLY if they were somehow never
    // granted, because the library is silently empty without them and there is
    // no error anywhere else to explain why.
    var permsAsked by remember { mutableStateOf(false) }
    val permissionLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts
            .RequestMultiplePermissions()
    ) { granted ->
        if (granted.values.any { it }) refresh()
    }

    // Folder picking, via the Storage Access Framework. The grant is persisted
    // so the folder is still readable after a reboot -- a bare content:// Uri is
    // not. MediaStore is then told to rescan, because audio copied in by hand
    // does not appear in the library until something asks the provider to look.
    var folderNote by remember { mutableStateOf<String?>(null) }
    val folderLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts
            .OpenDocumentTree()
    ) { tree ->
        if (tree != null) {
            val persisted = FolderPicker.persist(context, tree)
            FolderPicker.requestRescan(context, tree)
            SetupState(context).libraryFolder =
                (persisted ?: tree).toString()
            folderNote = "+ ${FolderPicker.describe(context, tree)}"
            refresh()
        }
    }
    LaunchedEffect(Unit) {
        if (!permsAsked) {
            permsAsked = true
            val missing = Permissions.needed()
                .filterNot { Permissions.granted(context, it) }
            if (missing.isNotEmpty()) permissionLauncher.launch(missing.toTypedArray())
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

    // --- gamepad + keyboard -------------------------------------------------
    // Both arrive as key events, so one handler covers a pad's D-pad, its
    // buttons and a physical keyboard. The pad table is tried FIRST so a pad
    // works even when the keymap has been rebound to something else.
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }

    fun moveSelection(delta: Int) {
        if (tracks.isEmpty()) return
        val next = (selected + delta).coerceIn(0, tracks.size - 1)
        selected = next
        // Selecting is not playing. Starting audio on a scroll is exactly the
        // accident this screen already had to be fixed for on touch.
        scope.launch { listState.scrollToItem(next) }
    }

    fun stepLean(direction: Int) {
        lean = HashSettings.stepLean(lean, direction)
        HashSettings.setLean(hashPrefs, lean)
    }

    fun padAction(action: String): Boolean = when (action) {
        Gamepad.A_PREV -> { controller.skipPrevious(); true }
        Gamepad.A_NEXT -> { controller.skipNext(); true }
        Gamepad.A_PLAY_PAUSE -> { controller.togglePause(); true }
        Gamepad.A_UP -> { moveSelection(-1); true }
        Gamepad.A_DOWN -> { moveSelection(1); true }
        Gamepad.A_LEAN_LEFT -> { stepLean(-1); true }
        Gamepad.A_LEAN_RIGHT -> { stepLean(1); true }
        Gamepad.A_SETTINGS -> { settingsOpen = !settingsOpen; true }
        else -> false
    }

    // Analog pad input (L2/R2 lean notches, stick direction). Registered here
    // because the Activity is the only thing that sees generic motion.
    PadListener { action -> padAction(action) }

    fun onKey(ev: android.view.KeyEvent): Boolean {
        if (ev.action != android.view.KeyEvent.ACTION_DOWN) return false
        val code = ev.keyCode
        Gamepad.resolve(code)?.let { return padAction(it) }
        Gamepad.triggerFromButton(code)?.let { return padAction(it) }
        // Stick axes are generic motion rather than key events, so they are
        // not reachable from here. See MainActivity.dispatchGenericMotionEvent.
        return false
    }

    // The theme is a layout decision, not a colour scheme: TOUCH means the
    // player is being driven by a thumb, so the transport controls get real
    // targets; KEYBOARD keeps the compact sizing that leaves room for the
    // library and matches what a pad or a keyboard gets.
    val touch = theme == HashSettings.Theme.TOUCH
    val ctrlPad = if (touch) 14.dp else 4.dp
    val ctrlFont = if (touch) 22.sp else 13.sp

    Column(
        Modifier.fillMaxSize().background(Color(0xFF0A0C12))
            .focusRequester(focus)
            .focusable()
            .onPreviewKeyEvent { ev -> onKey(ev.nativeKeyEvent) }
            // Analog triggers are NOT read here. L2/R2 arrive as generic
            // motion events, which Compose pointer input cannot see on this
            // version; MainActivity.dispatchGenericMotionEvent feeds
            // Gamepad.sink, and the PadListener below registers this screen
            // with it.
    ) {

        // header
        Row(Modifier.fillMaxWidth().padding(16.dp, 20.dp, 16.dp, 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text("hashplay", color = Color(0xFF00E6B8), fontSize = 22.sp)
            Spacer(Modifier.width(10.dp))
            Text("stream · download · listen", color = Color(0xFF8B91A5), fontSize = 12.sp)
            Spacer(Modifier.weight(1f))
            // Add a folder of the user's choosing. The desktop has this
            // (settings_panel.py:244) and Android only ever scanned all of
            // MediaStore, so there was no way to narrow the library.
            // OpenDocumentTree() takes no input and already requests a persistable read
            // grant, so there is no Intent to hand it. FolderPicker.intent() is
            // kept for callers that want to launch the picker themselves.
            TextButton(onClick = { folderLauncher.launch(null) }) {
                Text("+ folder", color = Color(0xFF8B91A5))
            }
            TextButton(onClick = { showTorrentSheet = true }) {
                Text("+ torrent", color = Color(0xFF00E6B8))
            }
            // Lean and gain used to live on this surface. They are in the
            // shared overlay now, so one button opens the one place they are
            // edited -- and the same overlay exists in every flavor.
            TextButton(onClick = { settingsOpen = true }) {
                Text("settings", color = Color(0xFF8B91A5))
            }
        }

        // visualizer (tap to cycle mode)
        Box(Modifier.fillMaxWidth().height(220.dp)
            .clickable {
                vizMode = VizMode.entries[(vizMode.ordinal + 1) % VizMode.entries.size]
            }) {
            // A local video can sit behind the ball, the way it does on the
            // desktop. Silent by design -- the player owns the audio focus.
            val currentTrack = tracks.getOrNull(selected)
            if (VideoSupport.isVideoFile(currentTrack?.path)) {
                VideoBackground(currentTrack?.path)
            }
            // NCS_BALL is the desktop's sphere renderer, ported. Lite keeps it
            // too -- the user was explicit that the lite build must still have
            // the ball, just drawn from fewer points.
            if (vizMode == VizMode.NCS_BALL) {
                val tier = if (BuildConfig.HAS_TORRENTS) NcsSphere.Tier.FULL
                           else NcsSphere.Tier.LITE
                // The lean is now a setting rather than a slider on this
                // surface, but the renderer still honours it -- otherwise
                // moving it in settings would do nothing visible at all.
                NcsSphereView(spectrumState.value, controller.bass(), controller.level(),
                              tier, Modifier.fillMaxSize(), lean = lean)
            } else {
                Visualizer(spectrumState, vizMode, Modifier.fillMaxSize())
            }
            if (!now.isPlaying && now.title.isEmpty()) {
                // Sits BELOW the ball, not on it. Centred over the sphere it
                // drew straight through the point cloud and made both look
                // broken -- caught on a Nokia T20 screenshot.
                Text("tap a track to play · +torrent for a magnet link",
                    color = Color(0xFF6E768C), fontSize = 11.sp,
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 10.dp))
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
                    // The desktop has these on K_UP / K_DOWN
                    // (ncs_launcher.py:2195-2204); Android had no way to move
                    // between tracks except tapping another row, which throws
                    // away your place in the queue.
                    FilledTonalButton(
                        onClick = { controller.skipPrevious() },
                        contentPadding = PaddingValues(ctrlPad)
                    ) {
                        Text("⏮", color = Color(0xFF00E6B8), fontSize = ctrlFont)
                    }
                    Spacer(Modifier.width(4.dp))
                    FilledTonalButton(onClick = { controller.togglePause() },
                        contentPadding = PaddingValues(ctrlPad)) {
                        Text(if (now.isPlaying) "❚❚" else "▶",
                            color = Color(0xFF00E6B8), fontSize = ctrlFont)
                    }
                    Spacer(Modifier.width(4.dp))
                    FilledTonalButton(
                        onClick = { controller.skipNext() },
                        contentPadding = PaddingValues(ctrlPad)
                    ) {
                        Text("⏭", color = Color(0xFF00E6B8), fontSize = ctrlFont)
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

        LazyColumn(Modifier.weight(1f).padding(horizontal = 8.dp), state = listState) {
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

    // The shared settings screen: theme, lean, gain, pads and key bindings.
    // hashPrefs, NOT the player prefs -- lean/gain/theme live in HashSettings,
    // and the overlay reads the keymap from whatever file it is handed.
    if (settingsOpen) {
        SettingsOverlay(
            prefs = hashPrefs,
            liveGain = controller.volume(),
            onGain = { controller.setVolume(it) },
            onClose = {
                settingsOpen = false
                // Re-read rather than assume: the theme chips write to disk and
                // the overlay can be closed with the keyboard, not just the
                // close button.
                theme = HashSettings.theme(hashPrefs)
                lean = HashSettings.lean(hashPrefs)
            },
        )
    }
}

private fun formatMs(ms: Long): String {
    val s = ms / 1000
    return "${s / 60}:${"%02d".format(s % 60)}"
}
