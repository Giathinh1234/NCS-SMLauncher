package com.giathinh.hashplay

import android.net.Uri
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView

/**
 * Video backgrounds, ported from the desktop's ncs_video.py.
 *
 * This is deliberately the PORTABLE subset only. The desktop module is 757
 * lines and most of it cannot exist on Android:
 *
 *   - resolve_source() / _resolve_web() pull sources over the network with a
 *     downloader binary (ncs_video.py:93-123). Shipping that inside a music
 *     player is a different app with different legal exposure, so it is not
 *     coming.
 *   - .strm files (read_strm, :76) point at a remote URL. Same objection.
 *   - ffmpeg/ffprobe transcode paths exist because desktop codecs are limited.
 *     Android ships H.264/VP9 in the platform decoder and ExoPlayer handles
 *     container/stream differences itself.
 *
 * What remains is the part the user actually sees: a muted, looping local video
 * playing behind the visualizer.
 *
 * Muted is not a shortcut. The desktop mutes it too, and it is the only correct
 * behaviour: the music player owns the audio focus, and a second unmuted stream
 * would fight it.
 */
object VideoSupport {

    /** Same extension list as ncs_video.py:33-34. */
    private val EXTENSIONS = setOf(
        "mp4", "mkv", "webm", "avi", "mov", "m4v", "mpg",
        "mpeg", "wmv", "flv", "ts", "m2ts", "ogv", "3gp"
    )

    /** True when this path is something we should try to play as video. */
    fun isVideoFile(path: String?): Boolean {
        if (path.isNullOrBlank()) return false
        val ext = path.substringAfterLast('.', "").lowercase()
        return ext in EXTENSIONS
    }

    /**
     * What the desktop cannot do here, stated once so the UI can say it.
     * A video feature that silently does nothing on some files is worse than
     * one that admits the limit.
     */
    const val UNSUPPORTED_NOTE =
        "Local video only -- streams and web sources are not supported on Android."
}

/**
 * Plays [videoPath] full-bleed behind whatever is drawn over it.
 *
 * The player is created and destroyed with the composable. Leaking one is the
 * classic bug here: an ExoPlayer left running holds a decoder and a wake lock
 * that survive the screen it was supposed to be behind.
 */
