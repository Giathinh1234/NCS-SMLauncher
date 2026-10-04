package com.giathinh.hashplay

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The NCS build's player screen, written from scratch against the desktop app.
 *
 * What this carries over from the desktop, and why:
 *
 *   lean slider      the desktop's visualizer_lean, same -1..1 fraction and the
 *                    same "never clipped" edge mapping
 *   rebindable keys  the desktop's Keymap, with the same conflict rule and a
 *                    reset. A phone has no keyboard, but a tablet with a case
 *                    keyboard or an Android TV box does, and this is a desktop
 *                    class player.
 *   persisted mode   the desktop persists its visualizer choice. The existing
 *                    Android build did not, so one stray tap could move you
 *                    off the NCS ball and lose it on relaunch.
 *   disc mode        the desktop's rotating vinyl disc visualizer
 *   honest refusals  lite has no torrent engine and says so rather than
 *                    silently doing nothing
 *
 * Layout follows the desktop's shape rather than the phone one: the sphere is
 * the point, so it gets the right half at full height and the library sits
 * beside it, instead of the ball being a banner above a list.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun NcsPlayerScreen(
    pendingMagnet: String?,
    onMagnetConsumed: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val prefs = remember { NcsPrefs.open(context) }

    val controller = remember { PlayerController(context) }
    val torrents = remember { TorrentManager(context) }

    var tracks by remember { mutableStateOf<List<Track>>(emptyList()) }
    var selected by remember { mutableIntStateOf(-1) }
    var folder by remember { mutableStateOf(prefs.getString("folder", null)) }
    var scanError by remember { mutableStateOf<String?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }
    var settingsOpen by remember { mutableStateOf(false) }
    var capturing by remember { mutableStateOf<String?>(null) }

    var lean by remember { mutableFloatStateOf(NcsPrefs.lean(prefs)) }
    var vizMode by remember { mutableIntStateOf(NcsPrefs.vizMode(prefs)) }

    val now by controller.state.collectAsState()
    val spectrumState = controller.spectrum.collectAsState()
    val statuses by torrents.statuses.collectAsState()

    // One scan, off the main thread, and a failure is shown rather than thrown:
    // an uncaught scan used to be able to take the whole app down on launch.
    suspend fun refresh() = withContext(Dispatchers.IO) {
        try {
            tracks = LibraryScanner.scan(context)
            scanError = null
        } catch (e: Exception) {
            scanError = e.message ?: "could not read the library"
        }
    }

    LaunchedEffect(Unit) { refresh() }

    var folderNote by remember { mutableStateOf<String?>(null) }
    val folderLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts
            .OpenDocumentTree()
    ) { tree ->
        if (tree != null) {
            val persisted = FolderPicker.persist(context, tree)
            FolderPicker.requestRescan(context, tree)
            SetupState(context).libraryFolder = (persisted ?: tree).toString()
            folderNote = "+ ${FolderPicker.describe(context, tree)}"
            scope.launch { refresh() }
        }
    }

    LaunchedEffect(pendingMagnet) {
        val magnet = pendingMagnet ?: return@LaunchedEffect
        onMagnetConsumed()
        if (BuildConfig.HAS_TORRENTS) {
            torrents.start(magnet)
            notice = "torrent added"
        } else {
            notice = "torrents are not in this build"
        }
    }

    // The frame clock. PlayerController.tick() refreshes position, spectrum and
    // bass; without being called the visualizer never moved even while audio
    // played. Copied from the existing screen because the desktop drives this
    // from pygame's event loop and Compose has no equivalent.
    LaunchedEffect(Unit) {
        val start = withFrameNanos { it }
        while (true) {
            withFrameNanos { now ->
                controller.tick((now - start) / 1_000_000_000f)
            }
        }
    }


    // --- keyboard, the port of the desktop's keymap -------------------------
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }

    fun onKey(ev: android.view.KeyEvent): Boolean {
            if (ev.action != android.view.KeyEvent.ACTION_DOWN) return false
            val code = ev.keyCode

            // An armed capture swallows the next key and becomes the binding.
            val armed = capturing
            if (armed != null) {
                capturing = null
                NcsPrefs.bind(prefs, armed, code)
                return true
            }

            val target = tracks.getOrNull(selected)
            val handled = when (NcsPrefs.resolve(prefs, code)) {
                NcsPrefs.A_PREV -> { controller.skipPrevious(); true }
                NcsPrefs.A_NEXT -> { controller.skipNext(); true }
                NcsPrefs.A_PLAY_PAUSE -> { controller.togglePause(); true }
                NcsPrefs.A_PLAY -> {
                    if (target != null) { controller.play(target); true } else false
                }
                NcsPrefs.A_SEEK_BACK -> { nudgeSeek(controller, now, -5f); true }
                NcsPrefs.A_SEEK_FWD -> { nudgeSeek(controller, now, 5f); true }
                NcsPrefs.A_VOL_UP -> {
                    controller.setVolume((controller.volume() + 0.05f).coerceIn(0f, 1f))
                    true
                }
                NcsPrefs.A_VOL_DOWN -> {
                    controller.setVolume((controller.volume() - 0.05f).coerceIn(0f, 1f))
                    true
                }
                NcsPrefs.A_CYCLE_VIZ -> {
                    vizMode = (vizMode + 1) % (NcsPrefs.LAST_VIZ_MODE + 1)
                    NcsPrefs.setVizMode(prefs, vizMode)
                    true
                }
                NcsPrefs.A_SETTINGS -> { settingsOpen = !settingsOpen; true }
                // There is no hard quit on Android: the desktop has a window to
                // close, here there is nothing to quit TO. Pause is the closest
                // honest equivalent, so that is what the binding does.
                NcsPrefs.A_QUIT -> { controller.togglePause(); true }
                else -> false
            }
            return handled
        }

    androidx.compose.foundation.layout.Box(
        Modifier.fillMaxSize().background(Px0E1116)
            // Without this the LEAN slider and GAIN slider render underneath the
            // system navigation bar and cannot actually be dragged.
            .systemBarsPadding()
            .focusRequester(focus)
            .focusable()
            .onPreviewKeyEvent { ev -> onKey(ev.nativeKeyEvent) }
    ) {
        Row(Modifier.fillMaxSize()) {

            // --- left: library ------------------------------------------------
            Column(Modifier.weight(1f).fillMaxHeight().padding(12.dp)) {
                PixelHeader(folder, tracks.size, BuildConfig.HAS_TORRENTS) {
                    folderLauncher.launch(null)
                }
                Spacer(Modifier.height(8.dp))

                if (tracks.isEmpty()) {
                    PixelBox(Modifier.fillMaxWidth().weight(1f)) {
                        Text(
                            if (scanError != null) "library: $scanError"
                            else "no tracks yet · + folder to add one",
                            color = Px6E768C, fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                        )
                    }
                } else {
                    PixelBox(Modifier.fillMaxWidth().weight(1f)) {
                        LazyColumn(Modifier.fillMaxSize()) {
                            itemsIndexed(tracks) { index, track ->
                                TrackRow(
                                    track, index == selected,
                                    modifier = Modifier.clickable {
                                        selected = index
                                        // Never autoplay on a tap that was
                                        // meant to select. Starting playback
                                        // is a separate, explicit action.
                                    },
                                )
                            }
                        }
                    }
                }

                Spacer(Modifier.height(8.dp))
                LeanControl(lean) { v -> NcsPrefs.setLean(prefs, v); lean = v }
            }

            // --- right: the sphere, full height ------------------------------
            Column(
                Modifier.weight(1f).fillMaxHeight()
                    .padding(end = 12.dp, top = 12.dp, bottom = 12.dp)
            ) {
                Box(
                    Modifier.fillMaxWidth().weight(1f)
                        .clip(RoundedCornerShape(2.dp))
                        .border(2.dp, Px1E2430, RoundedCornerShape(2.dp))
                        .background(Px07090D)
                        .clickable {
                            vizMode = (vizMode + 1) % (NcsPrefs.LAST_VIZ_MODE + 1)
                            NcsPrefs.setVizMode(prefs, vizMode)
                        },
                ) {
                    when (vizMode) {
                        3 -> NcsSphereView(
                            spectrumState.value, controller.bass(), controller.level(),
                            NcsSphere.Tier.FULL, lean = lean,
                        )
                        else -> Visualizer(
                            spectrumState, VizMode.entries[vizMode],
                            Modifier.fillMaxSize(),
                        )
                    }
                    // The mode name is always visible. Cycling modes by tapping
                    // with no label is how the previous build lost its ball
                    // without the user noticing.
                    PixelTag(
                        vizName(vizMode),
                        Modifier.align(Alignment.TopEnd).padding(8.dp),
                    )
                }

                Spacer(Modifier.height(10.dp))
                Transport(now, controller, Modifier.fillMaxWidth())
                Spacer(Modifier.height(6.dp))
                GainControl(controller, Modifier.fillMaxWidth())
            }
        }

        if (now.title.isNotEmpty()) {
            PixelBox(
                Modifier.align(Alignment.BottomCenter).padding(bottom = 10.dp)
            ) {
                Text(now.title, color = PxD8DEE9, fontSize = 12.sp,
                     fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
            }
        }

        notice?.let {
            PixelTag(it, Modifier.align(Alignment.TopCenter).padding(top = 10.dp))
        }

        if (settingsOpen) {
            SettingsOverlay(
                prefs = prefs,
                capturing = capturing,
                onCapture = { capturing = it },
                lean = lean,
                onLean = { NcsPrefs.setLean(prefs, it); lean = it },
                onClose = { settingsOpen = false; capturing = null },
            )
        }
    }
}

// --- small building blocks, deliberately plain and square ---------------------

private val Px0E1116 = Color(0xFF0E1116)
private val Px07090D = Color(0xFF07090D)
private val Px12151F = Color(0xFF12151F)
private val Px1E2430 = Color(0xFF1E2430)
private val Px6E768C = Color(0xFF6E768C)
private val PxD8DEE9 = Color(0xFFD8DEE9)
private val PxGOLD = Color(0xFFFFC64A)
private val PxTEAL = Color(0xFF00E6B8)
private val PxRED = Color(0xFFFF5C7A)

private fun vizName(mode: Int) = when (mode) {
    0 -> "BARS"
    1 -> "MIRROR"
    2 -> "DISC"
    3 -> "NCS BALL"
    else -> "?"
}

@Composable
private fun PixelBox(modifier: Modifier, content: @Composable () -> Unit) {
    Box(
        modifier.clip(RoundedCornerShape(2.dp))
            .border(1.dp, Px1E2430, RoundedCornerShape(2.dp))
            .background(Px12151F)
            .padding(8.dp),
    ) { content() }
}

@Composable
private fun PixelTag(text: String, modifier: Modifier = Modifier) {
    Box(
        modifier.clip(RoundedCornerShape(2.dp))
            .background(Px07090D.copy(alpha = 0.82f))
            .border(1.dp, Px1E2430, RoundedCornerShape(2.dp))
            .padding(horizontal = 6.dp, vertical = 3.dp),
    ) {
        Text(text, color = PxTEAL, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
    }
}

@Composable
private fun PixelHeader(folder: String?, count: Int, hasTorrents: Boolean, onAdd: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(
            "NCS", color = PxTEAL, fontSize = 18.sp,
            fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Black,
        )
        Spacer(Modifier.width(8.dp))
        Text(
            "player", color = Px6E768C, fontSize = 11.sp,
            fontFamily = FontFamily.Monospace,
        )
        Spacer(Modifier.weight(1f))
        PixelTag(if (hasTorrents) "+ torrent" else "lite")
        Spacer(Modifier.width(6.dp))
        PixelTag("${count} tracks")
        Spacer(Modifier.width(6.dp))
        PixelTag("+ folder", Modifier.clickable { onAdd() })
    }
}

@Composable
private fun TrackRow(track: Track, isSelected: Boolean, modifier: Modifier = Modifier) {
    Row(
        modifier.fillMaxWidth().padding(vertical = 3.dp)
            .clip(RoundedCornerShape(2.dp))
            .background(if (isSelected) Px1E2430 else Color.Transparent)
            .padding(horizontal = 6.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            track.title.take(38), color = if (isSelected) PxGOLD else PxD8DEE9,
            fontSize = 11.sp, fontFamily = FontFamily.Monospace,
            maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        Text(
            fmt(track.durationMs), color = Px6E768C, fontSize = 10.sp,
            fontFamily = FontFamily.Monospace,
        )
    }
}

@Composable
private fun LeanControl(lean: Float, onSet: (Float) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text("LEAN", color = Px6E768C, fontSize = 10.sp,
             fontFamily = FontFamily.Monospace)
        TextButton("«") { onSet(NcsPrefs.stepLean(lean, -1)) }

        // The slider has to WRITE the dragged value. Wiring it to the same
        // step-nudge the buttons use made dragging a no-op: the thumb followed
        // the finger, then the state snapped straight back to where it was.
        Slider(
            value = lean,
            onValueChange = { onSet(NcsPrefs.stepLean(lean, 0, it)) },
            valueRange = NcsPrefs.LEAN_MIN..NcsPrefs.LEAN_MAX,
            modifier = Modifier.weight(1f).height(24.dp),
            colors = SliderDefaults.colors(
                thumbColor = PxGOLD, activeTrackColor = PxGOLD,
                inactiveTrackColor = Px1E2430,
            ),
        )
        TextButton("»") { onSet(NcsPrefs.stepLean(lean, 1)) }
        Text(
            "%+.2f".format(lean), color = PxGOLD, fontSize = 10.sp,
            fontFamily = FontFamily.Monospace,
        )
    }
}

@Composable
private fun TextButton(label: String, onClick: () -> Unit) {
    Box(
        Modifier.clip(RoundedCornerShape(2.dp))
            .border(1.dp, Px1E2430, RoundedCornerShape(2.dp))
            .background(Px12151F)
            .clickable { onClick() }
            .padding(horizontal = 8.dp, vertical = 4.dp),
    ) {
        Text(label, color = PxGOLD, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
    }
}

@Composable
private fun Transport(
    now: PlayerController.NowPlaying,
    controller: PlayerController,
    modifier: Modifier = Modifier,
) {
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        PixelTag("⏮", Modifier.clickable { controller.skipPrevious() })
        PixelTag(if (now.isPlaying) "⏸" else "▶",
                 Modifier.clickable { controller.togglePause() })
        PixelTag("⏭", Modifier.clickable { controller.skipNext() })
        Spacer(Modifier.weight(1f))
        if (now.durationMs > 0) {
            Text(
                "${fmt(now.positionMs)} / ${fmt(now.durationMs)}",
                color = Px6E768C, fontSize = 10.sp, fontFamily = FontFamily.Monospace,
            )
        }
    }
}

@Composable
private fun GainControl(controller: PlayerController, modifier: Modifier = Modifier) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        Text("GAIN", color = Px6E768C, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
        Slider(
            value = controller.volume(),
            onValueChange = { controller.setVolume(it) },
            valueRange = 0f..1f,
            modifier = Modifier.weight(1f).height(24.dp),
            colors = SliderDefaults.colors(
                thumbColor = PxTEAL, activeTrackColor = PxTEAL,
                inactiveTrackColor = Px1E2430,
            ),
        )
        Text(
            "${(controller.volume() * 100).toInt()}%",
            color = PxTEAL, fontSize = 10.sp, fontFamily = FontFamily.Monospace,
        )
    }
}

@Composable
private fun SettingsOverlay(
    prefs: android.content.SharedPreferences,
    capturing: String?,
    onCapture: (String?) -> Unit,
    lean: Float,
    onLean: (Float) -> Unit,
    onClose: () -> Unit,
) {
    Box(
        Modifier.fillMaxSize().background(Color(0xCC05070B))
            .clickable(enabled = false) {},
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier.fillMaxWidth(0.86f).clip(RoundedCornerShape(2.dp))
                .border(2.dp, PxGOLD, RoundedCornerShape(2.dp))
                .background(Px0E1116).padding(14.dp),
        ) {
            Text(
                if (capturing != null) "press a key for ${NcsPrefs.label(capturing)} (Esc cancels)"
                else "SETTINGS",
                color = if (capturing != null) PxRED else PxGOLD,
                fontSize = 14.sp, fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(10.dp))

            Text("LEAN", color = Px6E768C, fontSize = 10.sp,
                 fontFamily = FontFamily.Monospace)
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton("«") { onLean(NcsPrefs.stepLean(lean, -1)) }
                Text(
                    "%+.2f   (← full left, → full right)".format(lean),
                    color = PxGOLD, fontSize = 10.sp, fontFamily = FontFamily.Monospace,
                    modifier = Modifier.padding(horizontal = 8.dp),
                )
                TextButton("»") { onLean(NcsPrefs.stepLean(lean, 1)) }
            }

            Spacer(Modifier.height(12.dp))
            Text("KEY BINDINGS", color = Px6E768C, fontSize = 10.sp,
                 fontFamily = FontFamily.Monospace)
            Spacer(Modifier.height(4.dp))

            val bindings = NcsPrefs.bindings(prefs)
            var group by remember { mutableStateOf("") }
            for ((action, binding) in NcsPrefs.DEFAULTS) {
                if (binding.group != group) {
                    group = binding.group
                    Spacer(Modifier.height(6.dp))
                    Text("── $group ──", color = Px6E768C, fontSize = 9.sp,
                         fontFamily = FontFamily.Monospace)
                }
                val armed = capturing == action
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 2.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(if (armed) Px1E2430 else Color.Transparent)
                        .clickable { onCapture(action) }
                        .padding(horizontal = 6.dp, vertical = 3.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        binding.label, color = PxD8DEE9, fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier.weight(1f),
                    )
                    Text(
                        keyLabel(bindings[action]), color = if (armed) PxRED else PxTEAL,
                        fontSize = 11.sp, fontFamily = FontFamily.Monospace,
                    )
                }
            }

            val clashes = NcsPrefs.conflicts(prefs)
            if (clashes.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    "conflict: ${clashes.joinToString("; ")}", color = PxRED,
                    fontSize = 10.sp, fontFamily = FontFamily.Monospace,
                )
            }

            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                TextButton("reset keys") { NcsPrefs.resetBindings(prefs) }
                TextButton("close") { onClose() }
            }
        }
    }
}

private fun keyLabel(code: Int?): String = when (code) {
    null -> "-"
    66 -> "ENTER"
    85 -> "MENU"
    112 -> "SETTINGS"
    113 -> "ESC"
    273 -> "MEDIA PREV"
    275 -> "MEDIA NEXT"
    85 -> "PLAY/PAUSE"
    20 -> "+5s"
    21 -> "-5s"
    else -> "KEY $code"
}

/** Seek by a whole number of seconds, as a fraction of the track. */
private fun nudgeSeek(
    controller: PlayerController,
    now: PlayerController.NowPlaying,
    seconds: Float,
) {
    if (now.durationMs <= 0) return
    val target = (now.positionMs + seconds * 1000f)
        .coerceIn(0f, now.durationMs.toFloat())
    controller.seekTo(target / now.durationMs.toFloat())
}

private fun fmt(ms: Long): String {
    val total = ms / 1000
    return "%d:%02d".format(total / 60, total % 60)
}
