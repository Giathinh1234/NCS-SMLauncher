package com.giathinh.hashplay

import android.content.ComponentName
import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.MoreExecutors
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Thin wrapper around ExoPlayer exposing Compose-friendly state,
 * plus a fake FFT spectrum derived from playback time (Android's
 * Visualizer API needs RECORD_AUDIO; we animate from loudness estimate
 * of the track instead — see Visualizer.kt for rendering).
 */
class PlayerController(context: Context) {

    /**
     * The player lives in PlaybackService and this is a handle on it, created
     * asynchronously because binding to a service is not instant. Before it
     * arrives we buffer one action so the first tap on a track is not lost --
     * previously the UI owned a separate player that died with the activity.
     */
    private val appContext = context.applicationContext
    private var player: Player? = null
    private var pendingPlay: Track? = null

    init {
        val token = SessionToken(appContext, ComponentName(appContext, PlaybackService::class.java))
        val future = MediaController.Builder(appContext, token).buildAsync()
        future.addListener({
            val c = try { future.get() } catch (t: Throwable) { null }
            if (c != null) {
                player = c
                c.addListener(object : Player.Listener {
                    override fun onIsPlayingChanged(isPlaying: Boolean) { update() }
                    override fun onMediaItemTransition(item: MediaItem?, reason: Int) { update() }
                })
                pendingPlay?.let { play(it) }
                pendingPlay = null
                update()
            }
        }, MoreExecutors.directExecutor())
    }

    /** True once the service's player is reachable. */
    fun ready(): Boolean = player != null

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

    /** Energy in the lowest bins. Drives the sphere's warp amplitude. */
    fun bass(): Float {
        val m = spectrum.value
        if (m.isEmpty()) return 0f
        var sum = 0f
        val n = minOf(6, m.size)
        for (i in 0 until n) sum += m[i]
        return (sum / n).coerceIn(0f, 1f)
    }

    /** Overall RMS-ish level. The desktop found a separate radial scale term
     *  keeps the ball's response monotonic, which an amplitude-only warp does
     *  not: raising the warp reshuffles which limb points end up outermost. */
    fun level(): Float {
        val m = spectrum.value
        if (m.isEmpty()) return 0f
        var sum = 0f
        for (v in m) sum += v * v
        return kotlin.math.sqrt(sum / m.size).coerceIn(0f, 1f)
    }

    fun play(track: Track) {
        val p = player ?: run { pendingPlay = track; return }
        // Uri.fromFile() is both wrong and fatal here. Under scoped storage the
        // path is not readable, and a file:// Uri handed to another process
        // throws FileUriExposedException on Android 7+. Track.id is already
        // populated by LibraryScanner and was unused, so this costs nothing.
        val uri = if (track.id > 0)
            android.content.ContentUris.withAppendedId(
                android.provider.MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, track.id)
        else
            android.net.Uri.fromFile(java.io.File(track.path))
        p.setMediaItem(MediaItem.fromUri(uri))
        p.prepare()
        p.playWhenReady = true
        _state.value = _state.value.copy(title = track.title, artist = track.artist)
    }

    fun togglePause() {
        val p = player ?: return
        if (p.isPlaying) p.pause() else p.play()
    }

    fun seekTo(fraction: Float) {
        val p = player ?: return
        val d = p.duration
        if (d > 0) p.seekTo((d * fraction).toLong())
    }

    /**
     * Detach from the session. Deliberately does NOT release the player: the
     * service owns it, and releasing here would kill playback for the
     * notification and the media keys too.
     */
    fun release() {
        val c = player as? MediaController
        player = null
        c?.release()
    }

    /** Called each animation frame by the UI to refresh position + spectrum. */
    fun tick(timeSec: Float) {
        update()
        val p = player
        if (p != null && p.isPlaying) {
            spectrum.value = fakeSpectrum(timeSec)
        } else {
            decaySpectrum()
        }
    }

    private fun update() {
        // Before the session binds there is no player, and nothing is playing.
        // The UI reads this every frame, so it must not throw here.
        val p = player
        _state.value = _state.value.copy(
            isPlaying = p?.isPlaying == true,
            positionMs = p?.currentPosition?.coerceAtLeast(0) ?: 0,
            durationMs = p?.duration?.takeIf { it > 0 } ?: 0
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
