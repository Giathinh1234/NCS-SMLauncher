package com.giathinh.hashplay

import android.content.Intent
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService

/**
 * Owns the ONE real player.
 *
 * This previously built its own ExoPlayer while PlayerController built a second
 * one, so there were two players and no connection between them: playback died
 * when the activity went away, there was no media notification, and hardware
 * keys had nothing to talk to. The desktop gets all three from its macOS
 * media-key tap.
 *
 * Android's equivalent is strictly better than that: a MediaSession published
 * here is picked up by the system and routed to the lock screen, the
 * notification shade, Bluetooth headsets, the watch, car head units and
 * Assistant -- with no Accessibility grant, which the macOS tap required.
 *
 * The UI does not own a player either. It binds through MediaController, so
 * what the notification shows and what the screen shows are the same thing by
 * construction rather than by luck.
 */
class PlaybackService : MediaSessionService() {

    private var mediaSession: MediaSession? = null

    override fun onCreate() {
        super.onCreate()
        val player = ExoPlayer.Builder(this)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                    .setUsage(C.USAGE_MEDIA)
                    .build(),
                /* handleAudioFocus = */ true
            )
            .setHandleAudioBecomingNoisy(true)
            .build()

        mediaSession = MediaSession.Builder(this, player)
            .setSessionActivity(pendingIntentToMainActivity())
            .build()

        // The desktop's media-key semantics, ported as-is: when paused, the
        // play key starts the next track; when playing, it pauses.
        // (ncs_launcher.py:1932-1961.)
        player.addListener(object : androidx.media3.common.Player.Listener {
            override fun onMediaItemTransition(item: androidx.media3.common.MediaItem?, reason: Int) {
                if (!player.playWhenReady && player.mediaItemCount > 0) {
                    player.prepare()
                    player.play()
                }
            }
        })
    }

    private fun pendingIntentToMainActivity() = android.app.PendingIntent.getActivity(
        this,
        0,
        Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP
        },
        android.app.PendingIntent.FLAG_IMMUTABLE or
            android.app.PendingIntent.FLAG_UPDATE_CURRENT
    )

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo) = mediaSession

    /** True once the session exists, so the UI can wait for it before binding. */
    fun sessionAvailable(): Boolean = mediaSession != null

    override fun onTaskRemoved(rootIntent: Intent?) {
        // Match the desktop: leaving the app entirely stops playback, rather
        // than leaving a notification the user cannot get rid of.
        val player = mediaSession?.player
        if (player?.isPlaying != true) stopSelf()
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        mediaSession?.run {
            player.release()
            release()
            mediaSession = null
        }
        super.onDestroy()
    }
}