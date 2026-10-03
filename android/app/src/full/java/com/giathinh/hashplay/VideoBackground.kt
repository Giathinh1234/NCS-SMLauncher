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

/** Full build: real video backgrounds. See the shared VideoSupport. */
@Composable
fun VideoBackground(videoPath: String?, modifier: Modifier = Modifier) {

    if (videoPath == null || !VideoSupport.isVideoFile(videoPath)) return

    val context = LocalContext.current
    var player by remember { mutableStateOf<ExoPlayer?>(null) }
    var failed by remember { mutableStateOf(false) }

    LaunchedEffect(videoPath) {
        failed = false
        val p = ExoPlayer.Builder(context).build()
        p.setMediaItem(MediaItem.fromUri(Uri.parse(
            if (videoPath.startsWith("content://") || videoPath.startsWith("file://"))
                videoPath else "file://$videoPath")))
        p.repeatMode = Player.REPEAT_MODE_ONE
        p.volume = 0f              // music owns the audio focus
        p.playWhenReady = true
        p.play()
        // A codec the device cannot decode must not take the screen down with
        // it. Show nothing rather than a black rectangle over the visualizer.
        p.addListener(object : Player.Listener {
            override fun onPlayerError(error: androidx.media3.common.PlaybackException) {
                failed = true
            }
        })
        player = p
    }

    DisposableEffect(Unit) {
        onDispose {
            player?.let {
                it.stop()
                it.release()
            }
            player = null
        }
    }

    val p = player
    if (p != null && !failed) {
        Box(modifier = modifier.fillMaxSize()) {
            AndroidView(
                factory = { ctx ->
                    PlayerView(ctx).apply {
                        this.player = p
                        useController = false        // no transport UI over the ball
                        // A video background must never be mistaken for a
                        // tappable surface.
                        isClickable = false
                        setShutterBackgroundColor(android.graphics.Color.TRANSPARENT)
                    }
                },
                modifier = Modifier.fillMaxSize(),
                update = { it.player = p }
            )
        }
    }
}
