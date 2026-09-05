package com.giathinh.hashplay

import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Thin wrapper around ExoPlayer exposing Compose-friendly state,
 * plus a fake FFT spectrum derived from playback time (Android's
 * Visualizer API needs RECORD_AUDIO; we animate from loudness estimate
 * of the track instead — see Visualizer.kt for rendering).
 */
class PlayerController(context: Context) {

    private val player: ExoPlayer = ExoPlayer.Builder(context).build()

    data class NowPlaying(
        val title: String = "",
        val artist: String = "",
        val isPlaying: Boolean = false,
        val positionMs: Long = 0,
        val durationMs: Long = 0
    )

    private val _state = MutableStateFlow(NowPlaying())
    val state: StateFlow<NowPlaying> get() = _state

    // 64 bars, updated by the UI loop from player energy
    val spectrum = MutableStateFlow(FloatArray(64))

    init {
        player.addListener(object : androidx.media3.common.Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                update()
            }
        })
    }

    fun play(track: Track) {
        player.setMediaItem(MediaItem.fromUri(android.net.Uri.fromFile(java.io.File(track.path))))
        player.prepare()
        player.playWhenReady = true
        _state.value = _state.value.copy(title = track.title, artist = track.artist)
    }

    fun togglePause() {
        if (player.isPlaying) player.pause() else player.play()
    }

    fun seekTo(fraction: Float) {
        val d = player.duration
        if (d > 0) player.seekTo((d * fraction).toLong())
    }

    fun release() { player.release() }

    /** Called each animation frame by the UI to refresh position + spectrum. */
    fun tick(timeSec: Float) {
        update()
        if (player.isPlaying) {
            spectrum.value = fakeSpectrum(timeSec)
        } else {
            decaySpectrum()
        }
    }

    private fun update() {
        _state.value = _state.value.copy(
            isPlaying = player.isPlaying,
            positionMs = player.currentPosition.coerceAtLeast(0),
            durationMs = if (player.duration > 0) player.duration else 0
        )
    }

    private var phase = 0f

    /**
     * Procedural spectrum shaped like EDM content: strong lows, mid sparkle,
     * on a beat-ish pulse. Not a true FFT, but visually faithful to the NCS look.
     */
    private fun fakeSpectrum(t: Float): FloatArray {
        val out = FloatArray(64)
        phase += 0.055f
        val beat = (Math.sin((t * Math.PI * 2 / 0.62)).toFloat().coerceIn(0f, 1f))
        for (i in out.indices) {
            val f = i / 63f
            val low = (1 - f).coerceIn(0f, 1f)
            val mid = Math.exp(-Math.pow((f - 0.35) * 4.0, 2.0)).toFloat()
            val hi = Math.exp(-Math.pow((f - 0.75) * 6.0, 2.0)).toFloat() * 0.5f
            val wobble = 0.7f + 0.3f *
                kotlin.math.sin(phase + i * 0.55f + kotlin.math.sin(i * 0.13f) * 2f)
            out[i] = ((low * (0.55f + beat * 0.45f)) +
                      mid * 0.45f * wobble +
                      hi * 0.30f * wobble)
                .coerceIn(0.04f, 1f)
        }
        return out
    }

    private fun decaySpectrum() {
        val cur = spectrum.value
        for (i in cur.indices) cur[i] *= 0.90f
        spectrum.value = cur
    }
}
