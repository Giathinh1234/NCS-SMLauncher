package com.giathinh.hashplay

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat

/**
 * Runtime permissions, and why they are not optional.
 *
 * The manifest already declared READ_MEDIA_AUDIO, READ_EXTERNAL_STORAGE and
 * POST_NOTIFICATIONS, and NOTHING ever asked for them at runtime. Declaring a
 * permission only makes it installable; the user still has to grant it, and
 * until they do the library scan comes back empty and playback fails on a path
 * the app cannot open. So this is the difference between an app that works and
 * one that silently sees no music at all.
 */
object Permissions {

    /**
     * The audio-library read permission for this API level.
     *
     * READ_MEDIA_AUDIO only exists from API 33. Below that the equivalent is
     * READ_EXTERNAL_STORAGE, which Android 13 auto-grants on upgrade but a
     * fresh install still has to request.
     */
    fun audioRead(): String =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
            Manifest.permission.READ_MEDIA_AUDIO
        else
            Manifest.permission.READ_EXTERNAL_STORAGE

    /** The full set worth asking for on first run, most important first. */
    fun needed(): List<String> = buildList {
        add(audioRead())
        // Only meaningful on Android 13+, and only for the media notification.
        // Asking earlier is a no-op that the system silently denies.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            add(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    fun granted(context: Context, permission: String): Boolean =
        ContextCompat.checkSelfPermission(context, permission) ==
            PackageManager.PERMISSION_GRANTED

    /** True when nothing is left to ask for. */
    fun allGranted(context: Context): Boolean =
        needed().all { granted(context, it) }
}
