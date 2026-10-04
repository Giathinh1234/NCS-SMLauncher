package com.giathinh.hashplay

import android.view.MotionEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.input.pointer.pointerInput
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
    // Two files, two jobs. NcsPrefs keeps the keymap and the visualizer mode;
    // HashSettings keeps what every flavor shares -- theme, lean and gain.
    // Lean and gain left the player surface and live in settings now.
    val prefs = remember { NcsPrefs.open(context) }
    val hashPrefs = remember { HashSettings.open(context) }

    val controller = remember { PlayerController(context) }
    val torrents = remember { TorrentManager(context) }

    var tracks by remember { mutableStateOf<List<Track>>(emptyList()) }
    var selected by remember { mutableIntStateOf(-1) }
    var folder by remember { mutableStateOf(prefs.getString("folder", null)) }
    var scanError by remember { mutableStateOf<String?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }
    var settingsOpen by remember { mutableStateOf(false) }
    var capturing by remember { mutableStateOf<String?>(null) }

    var lean by remember { mutableFloatStateOf(HashSettings.lean(hashPrefs)) }
    var theme by remember { mutableStateOf(HashSettings.theme(hashPrefs)) }
    var vizMode by remember { mutableIntStateOf(NcsPrefs.vizMode(prefs)) }
    val listState = rememberLazyListState()

    // The stored gain is only the starting level; the service owns it after.
    LaunchedEffect(Unit) { controller.setVolume(HashSettings.gain(hashPrefs)) }

    // TOUCH means big targets under the thumb; KEYBOARD stays compact, which is
    // also what a pad gets. Sizes below all derive from this one flag.
    val touch = theme == HashSettings.Theme.TOUCH
    val ctrlFont = if (touch) 20.sp else 11.sp
    val ctrlPad = if (touch) 12.dp else 4.dp
    val rowFont = if (touch) 14.sp else 11.sp

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

    // --- gamepad, on top of the keyboard keymap ----------------------------
    // A pad's buttons arrive as ordinary KeyEvents, so this is the same door.
    // The pad table is tried FIRST: a gamepad has its own physical layout, and
    // it should keep working even if the keymap has been rebound to something
    // else. A stick with no D-pad reports as a pair of axes on the same event.
    fun moveSelection(delta: Int) {
        if (tracks.isEmpty()) return
        val next = (selected + delta).coerceIn(0, tracks.size - 1)
        selected = next
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

    // Analog pad input, via the Activity -- see PadListener.
    PadListener { action -> padAction(action) }

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

            Gamepad.resolve(code)?.let { return padAction(it) }
            Gamepad.triggerFromButton(code)?.let { return padAction(it) }

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
            // Analog triggers. Xbox, DualSense and Switch Pro all report L2/R2
            // as AXES rather than keycodes, and a key event never arrives for
            // them -- so without this the bumpers do nothing on most pads.
            // Analog triggers arrive as generic motion events, which Compose
            // pointer input cannot see on this version. They come through
            // MainActivity.dispatchGenericMotionEvent into Gamepad.sink.
    ) {
        Row(Modifier.fillMaxSize()) {

            // --- left: library ------------------------------------------------
            Column(Modifier.weight(1f).fillMaxHeight().padding(12.dp)) {
                PixelHeader(
                    folder, tracks.size, BuildConfig.HAS_TORRENTS,
                    onAdd = { folderLauncher.launch(null) },
                    onSettings = { settingsOpen = true },
                    big = touch,
                )
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
                        LazyColumn(Modifier.fillMaxSize(), state = listState) {
                            itemsIndexed(tracks) { index, track ->
                                TrackRow(
                                    track, index == selected,
                                    fontSize = rowFont,
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
                // Lean and gain used to sit here. They are in the shared
                // settings overlay now, which is the one place every flavor
                // edits them -- and the pad bumpers still reach the lean.
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
                Transport(now, controller, Modifier.fillMaxWidth(),
                          fontSize = ctrlFont, pad = ctrlPad)
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
            // hashPrefs, not prefs: theme/lean/gain live in HashSettings. The
            // overlay reads the keymap from whatever file it is handed, and the
            // binding keys do not collide with those.
            SettingsOverlay(
                prefs = hashPrefs,
                liveGain = controller.volume(),
                onGain = { controller.setVolume(it) },
                onClose = {
                    settingsOpen = false
                    capturing = null
                    // Re-read on close rather than trusting the close button:
                    // the overlay can also be dismissed from the keyboard.
                    theme = HashSettings.theme(hashPrefs)
                    lean = HashSettings.lean(hashPrefs)
                },
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
private fun PixelTag(
    text: String,
    modifier: Modifier = Modifier,
    fontSize: androidx.compose.ui.unit.TextUnit = 10.sp,
) {
    Box(
        modifier.clip(RoundedCornerShape(2.dp))
            .background(Px07090D.copy(alpha = 0.82f))
            .border(1.dp, Px1E2430, RoundedCornerShape(2.dp))
            .padding(horizontal = 6.dp, vertical = 3.dp),
    ) {
        Text(text, color = PxTEAL, fontSize = fontSize, fontFamily = FontFamily.Monospace)
    }
}

@Composable
private fun PixelHeader(
    folder: String?,
    count: Int,
    hasTorrents: Boolean,
    onAdd: () -> Unit,
    onSettings: () -> Unit,
    big: Boolean = false,
) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(
            "NCS", color = PxTEAL, fontSize = if (big) 24.sp else 18.sp,
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
        Spacer(Modifier.width(6.dp))
        // The one settings door. Lean, gain, theme and every binding are in
        // there now, so the header does not carry its own sliders.
        PixelTag(
            "settings",
            Modifier.clickable { onSettings() },
            fontSize = if (big) 13.sp else 10.sp,
        )
    }
}

@Composable
private fun TrackRow(
    track: Track,
    isSelected: Boolean,
    fontSize: androidx.compose.ui.unit.TextUnit = 11.sp,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier.fillMaxWidth().padding(vertical = 3.dp)
            .clip(RoundedCornerShape(2.dp))
            .background(if (isSelected) Px1E2430 else Color.Transparent)
            .padding(horizontal = 6.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            track.title.take(38), color = if (isSelected) PxGOLD else PxD8DEE9,
            fontSize = fontSize, fontFamily = FontFamily.Monospace,
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
private fun Transport(
    now: PlayerController.NowPlaying,
    controller: PlayerController,
    modifier: Modifier = Modifier,
    fontSize: androidx.compose.ui.unit.TextUnit = 11.sp,
    pad: androidx.compose.ui.unit.Dp = 6.dp,
) {
    // The theme changes the transport for real: under TOUCH the buttons get a
    // font and a padding a thumb can hit, under KEYBOARD they stay compact so
    // the library keeps the space.
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(pad)) {
        PixelTag("⏮", Modifier.clickable { controller.skipPrevious() }, fontSize)
        PixelTag(if (now.isPlaying) "⏸" else "▶",
                 Modifier.clickable { controller.togglePause() }, fontSize)
        PixelTag("⏭", Modifier.clickable { controller.skipNext() }, fontSize)
        Spacer(Modifier.weight(1f))
        if (now.durationMs > 0) {
            Text(
                "${fmt(now.positionMs)} / ${fmt(now.durationMs)}",
                color = Px6E768C, fontSize = 10.sp, fontFamily = FontFamily.Monospace,
            )
        }
    }
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
