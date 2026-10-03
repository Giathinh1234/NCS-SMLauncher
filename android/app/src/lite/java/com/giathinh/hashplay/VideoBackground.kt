package com.giathinh.hashplay

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/**
 * Lite build: no video backgrounds.
 *
 * media3-ui plus the video decoders cost lite ~500 KB, taking it from 2.5 MB to
 * 3.0 MB -- a fifth more for a feature that is the single heaviest thing in the
 * app. Lite's whole contract is low resource use, so the code is not merely
 * disabled here, it is not in the APK at all.
 *
 * The NCS ball stays, per the same rule: lite reduces what it draws rather than
 * dropping the desktop's signature feature.
 *
 * Deliberately silent, unlike the lite torrent stub: there is no user action
 * here to refuse. The picker simply never offers a video, so nothing is
 * promised and nothing needs retracting.
 */
@Composable
fun VideoBackground(videoPath: String?, modifier: Modifier = Modifier) {
    // Intentionally empty.
}
