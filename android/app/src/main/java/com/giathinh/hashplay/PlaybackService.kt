package com.giathinh.hashplay

import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService

/**
 * Background playback service so music keeps playing when the app is
 * backgrounded and shows lock-screen controls.
 */
class PlaybackService : MediaSessionService() {

    private var mediaSession: MediaSession? = null

    override fun onCreate() {
        super.onCreate()
        mediaSession = MediaSession.Builder(
            this, ExoPlayer.Builder(this).build()
        ).build()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo) =
        mediaSession

    override fun onDestroy() {
        mediaSession?.run {
            player.release()
            release()
            mediaSession = null
        }
        super.onDestroy()
    }
}
