package com.giathinh.hashplay

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * The one settings screen. Every flavor shows this, so a setting added here
 * exists in all of them and none can drift out of step.
 *
 * Lean and gain live HERE rather than on the player screen. They were on the
 * main surface before, which crowded the player and meant the two bars were
 * always on screen whether or not you cared about them.
 */
@Composable
fun SettingsOverlay(
    prefs: android.content.SharedPreferences,
    /** Live gain, because the service owns it and this must not assume. */
    liveGain: Float,
    onGain: (Float) -> Unit,
    onClose: () -> Unit,
) {
    val context = LocalContext.current

    var theme by remember { mutableStateOf(HashSettings.theme(prefs)) }
    var bgVis by remember { mutableStateOf(HashSettings.backgroundVisualizer(prefs)) }
    var lean by remember { mutableFloatStateOf(HashSettings.lean(prefs)) }
    var gain by remember { mutableFloatStateOf(liveGain) }

    // Keyboard and pad bindings are read live, not cached, so rebinding takes
    // effect on the next press instead of the next relaunch.
    val pads = remember { Gamepad.connected(context) }
    val keys = remember { NcsPrefs.bindings(prefs) }
    var capturing by remember { mutableStateOf<String?>(null) }

    val teal = Color(0xFF00E6B8)
    val gold = Color(0xFF8B91A5)
    val dim = Color(0xFF12151F)
    val edge = Color(0xFF1E2430)

    @Composable
    fun Chip(text: String, active: Boolean = false, onClick: () -> Unit) {
        Box(
            Modifier.clip(RoundedCornerShape(2.dp))
                .background(if (active) Color(0xFF1E2430) else dim)
                .border(1.dp, if (active) teal else edge, RoundedCornerShape(2.dp))
                .clickable { onClick() }
                .padding(horizontal = 10.dp, vertical = 6.dp),
        ) {
            Text(
                text, color = if (active) teal else Color(0xFFD8DEE9),
                fontSize = 11.sp, fontFamily = PixelType.Display,
            )
        }
    }

    // Back closes this overlay rather than leaving the app. Without the
    // handler, BACK from settings dropped straight out of the player and threw
    // away whatever was open -- the same rule the desktop's ESC follows, where
    // a visible panel closes first and only a second press goes on to quit.
    androidx.activity.compose.BackHandler(enabled = true) {
        capturing = null
        onClose()
    }

    Box(
        Modifier.fillMaxSize()
            .background(Color(0xCC05070B))
            // Swallow taps so a click behind the panel cannot hit a track and
            // start playing it while the user is closing settings.
            .clickable(enabled = false) {},
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier
                .fillMaxWidth(0.88f)
                .fillMaxHeight(0.9f)
                .clip(RoundedCornerShape(2.dp))
                .border(2.dp, teal, RoundedCornerShape(2.dp))
                .background(Color(0xFF0E1116))
                .padding(14.dp)
                .verticalScroll(rememberScrollState()),
        ) {
            Text(
                if (capturing != null) "press a key for $capturing" else "SETTINGS",
                color = if (capturing != null) Color(0xFFFF5C7A) else teal,
                fontSize = 14.sp, fontFamily = PixelType.Display,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(10.dp))

            // --- theme ------------------------------------------------------
            Section("INTERFACE")
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Chip("TOUCH", theme == HashSettings.Theme.TOUCH) {
                    theme = HashSettings.Theme.TOUCH
                    HashSettings.setTheme(prefs, theme)
                }
                Chip("KEYBOARD", theme == HashSettings.Theme.KEYBOARD) {
                    theme = HashSettings.Theme.KEYBOARD
                    HashSettings.setTheme(prefs, theme)
                }
            }
            Text(
                when (theme) {
                    HashSettings.Theme.TOUCH ->
                        "Big targets, controls under the thumb, library leads."
                    HashSettings.Theme.KEYBOARD ->
                        "Compact layout, library stays visible, controls show " +
                            "their keys. Also what a gamepad gets."
                },
                color = gold, fontSize = 10.sp, fontFamily = PixelType.Body,
                modifier = Modifier.padding(top = 4.dp),
            )

            // Only offered on the keyboard layout: a full-bleed reactive
            // background behind a thumb-driven layout puts moving light under
            // the controls you are trying to hit.
            if (theme == HashSettings.Theme.KEYBOARD) {
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Switch(
                        checked = bgVis,
                        onCheckedChange = {
                            bgVis = it
                            HashSettings.setBackgroundVisualizer(prefs, it)
                        },
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        "Visualizer as background",
                        color = Color(0xFFD8DEE9), fontSize = 11.sp,
                        fontFamily = PixelType.Body,
                    )
                }
                Text(
                    "Fills the whole screen edge to edge, no box. The app " +
                        "background takes its near-black so the sphere still reads.",
                    color = gold, fontSize = 10.sp, fontFamily = PixelType.Body,
                )
            }

            // --- lean -------------------------------------------------------
            Spacer(Modifier.height(12.dp))
            Section("LEAN VISUALIZER")
            Row(verticalAlignment = Alignment.CenterVertically) {
                Chip("«") { lean = HashSettings.stepLean(lean, -1); HashSettings.setLean(prefs, lean) }
                Slider(
                    value = lean,
                    onValueChange = {
                        lean = HashSettings.stepLean(lean, 0, it)
                        HashSettings.setLean(prefs, lean)
                    },
                    valueRange = HashSettings.LEAN_MIN..HashSettings.LEAN_MAX,
                    modifier = Modifier.weight(1f).padding(horizontal = 6.dp).height(24.dp),
                    colors = SliderDefaults.colors(
                        thumbColor = Color(0xFFFFC64A), activeTrackColor = Color(0xFFFFC64A),
                        inactiveTrackColor = edge,
                    ),
                )
                Chip("»") { lean = HashSettings.stepLean(lean, 1); HashSettings.setLean(prefs, lean) }
                Text(
                    "%+.2f".format(lean), color = Color(0xFFFFC64A), fontSize = 10.sp,
                    fontFamily = PixelType.Body,
                    modifier = Modifier.padding(start = 6.dp).width(44.dp),
                )
            }
            Text(
                "−1 hard left · 0 centred · +1 hard right",
                color = gold, fontSize = 10.sp, fontFamily = PixelType.Body,
            )

            // --- gain -------------------------------------------------------
            Spacer(Modifier.height(12.dp))
            Section("GAIN")
            Row(verticalAlignment = Alignment.CenterVertically) {
                Chip("−") {
                    gain = HashSettings.stepGain(gain, -1)
                    HashSettings.setGain(prefs, gain); onGain(gain)
                }
                Slider(
                    value = gain,
                    onValueChange = {
                        gain = HashSettings.stepGain(gain, 0, it)
                        HashSettings.setGain(prefs, gain); onGain(gain)
                    },
                    valueRange = 0f..1f,
                    modifier = Modifier.weight(1f).padding(horizontal = 6.dp).height(24.dp),
                    colors = SliderDefaults.colors(
                        thumbColor = teal, activeTrackColor = teal, inactiveTrackColor = edge,
                    ),
                )
                Chip("+") {
                    gain = HashSettings.stepGain(gain, 1)
                    HashSettings.setGain(prefs, gain); onGain(gain)
                }
                Text(
                    "${(gain * 100).toInt()}%", color = teal, fontSize = 10.sp,
                    fontFamily = PixelType.Body,
                    modifier = Modifier.padding(start = 6.dp).width(44.dp),
                )
            }
            Text(
                "Player volume. This is a level, not a promise about audibility.",
                color = gold, fontSize = 10.sp, fontFamily = PixelType.Body,
            )

            // --- gamepad ----------------------------------------------------
            Spacer(Modifier.height(12.dp))
            Section("GAMEPAD")
            Text(
                if (pads.isEmpty()) "none connected — plug one in to use it"
                else "connected: ${pads.joinToString(", ")}",
                color = if (pads.isEmpty()) gold else teal,
                fontSize = 10.sp, fontFamily = PixelType.Body,
            )
            Spacer(Modifier.height(4.dp))
            for ((action, binding) in Gamepad.DEFAULTS) {
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 2.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(dim)
                        .padding(horizontal = 6.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        binding.label, color = Color(0xFFD8DEE9), fontSize = 11.sp,
                        fontFamily = PixelType.Body,
                        modifier = Modifier.weight(1f),
                    )
                    Text(
                        binding.hint, color = gold, fontSize = 10.sp,
                        fontFamily = PixelType.Body,
                    )
                }
            }
            Text(
                "Xbox, DualSense and Switch Pro all report the same buttons on " +
                    "Android, so one layout covers all three.",
                color = gold, fontSize = 9.sp, fontFamily = PixelType.Body,
                modifier = Modifier.padding(top = 4.dp),
            )

            // --- key bindings -----------------------------------------------
            Spacer(Modifier.height(12.dp))
            Section("KEY BINDINGS")
            var group by remember { mutableStateOf("") }
            for ((action, binding) in NcsPrefs.DEFAULTS) {
                if (binding.group != group) {
                    group = binding.group
                    Text(
                        "── $group ──", color = gold, fontSize = 9.sp,
                        fontFamily = PixelType.Body,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
                val armed = capturing == NcsPrefs.label(action)
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 2.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(if (armed) Color(0xFF1E2430) else dim)
                        .clickable {
                            capturing =
                                if (armed) null else NcsPrefs.label(action)
                        }
                        .padding(horizontal = 6.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        binding.label, color = Color(0xFFD8DEE9), fontSize = 11.sp,
                        fontFamily = PixelType.Body,
                        modifier = Modifier.weight(1f),
                    )
                    Text(
                        if (armed) "press a key…" else keyLabel(keys[action]),
                        color = if (armed) Color(0xFFFF5C7A) else teal,
                        fontSize = 10.sp, fontFamily = PixelType.Body,
                    )
                }
            }
            val clashes = NcsPrefs.conflicts(prefs)
            if (clashes.isNotEmpty()) {
                Text(
                    "conflict: ${clashes.joinToString("; ")}", color = Color(0xFFFF5C7A),
                    fontSize = 10.sp, fontFamily = PixelType.Body,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
            Text(
                "Volume keys are reserved: Android consumes them for system " +
                    "volume before the app sees them.",
                color = gold, fontSize = 9.sp, fontFamily = PixelType.Body,
                modifier = Modifier.padding(top = 4.dp),
            )

            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Chip("reset keys") { NcsPrefs.resetBindings(prefs) }
                Chip("reset lean") { lean = 0f; HashSettings.setLean(prefs, 0f) }
                Chip("close") { onClose() }
            }
            Spacer(Modifier.height(4.dp))
        }
    }
}

@Composable
private fun Section(text: String) {
    Text(
        text, color = Color(0xFF6E768C), fontSize = 10.sp,
        fontFamily = PixelType.Display, fontWeight = FontWeight.Bold,
    )
}

private fun keyLabel(code: Int?): String = when (code) {
    null -> "-"
    66 -> "ENTER"
    85 -> "MENU"
    112 -> "SETTINGS"
    113 -> "ESC"
    273 -> "MEDIA PREV"
    275 -> "MEDIA NEXT"
    20 -> "+5s"
    21 -> "-5s"
    else -> "KEY $code"
}