package com.giathinh.hashplay

import android.content.Context
import android.provider.MediaStore

data class Track(
    val id: Long,
    val title: String,
    val artist: String,
    val path: String,
    val durationMs: Long
) {
    /**
     * A URI ExoPlayer can actually open.
     *
     * Uri.fromFile() is not readable under scoped storage, and a file:// Uri
     * passed to another process throws FileUriExposedException on Android 7+.
     * The MediaStore id is what we should be using; the raw path is only a
     * fallback for files that came from somewhere other than MediaStore.
     */
    fun uri(): android.net.Uri = if (id > 0)
        android.content.ContentUris.withAppendedId(
            MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, id)
    else
        android.net.Uri.fromFile(java.io.File(path))
}

object LibraryScanner {

    fun scan(context: Context): List<Track> {
        val out = mutableListOf<Track>()
        val proj = arrayOf(
            MediaStore.Audio.Media._ID,
            MediaStore.Audio.Media.TITLE,
            MediaStore.Audio.Media.ARTIST,
            MediaStore.Audio.Media.DATA,
            MediaStore.Audio.Media.DURATION
        )
        context.contentResolver.query(
            MediaStore.Audio.Media.EXTERNAL_CONTENT_URI,
            proj,
            "${MediaStore.Audio.Media.IS_MUSIC} != 0",
            null,
            "${MediaStore.Audio.Media.TITLE} COLLATE NOCASE ASC"
        )?.use { c ->
            val iId = c.getColumnIndexOrThrow(MediaStore.Audio.Media._ID)
            val iT = c.getColumnIndexOrThrow(MediaStore.Audio.Media.TITLE)
            val iA = c.getColumnIndexOrThrow(MediaStore.Audio.Media.ARTIST)
            val iP = c.getColumnIndexOrThrow(MediaStore.Audio.Media.DATA)
            val iD = c.getColumnIndexOrThrow(MediaStore.Audio.Media.DURATION)
            while (c.moveToNext()) {
                out += Track(
                    id = c.getLong(iId),
                    title = c.getString(iT) ?: "Unknown",
                    artist = c.getString(iA) ?: "",
                    path = c.getString(iP) ?: continue,
                    durationMs = c.getLong(iD)
                )
            }
        }
        return out
    }
}
