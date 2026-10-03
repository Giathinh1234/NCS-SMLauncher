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
        // DATA is deliberately NOT projected.
        //
        // It was deprecated in API 29 and the provider is allowed to omit it
        // from the result entirely. getColumnIndexOrThrow then throws
        // IllegalArgumentException -- from inside a coroutine on Dispatchers.IO,
        // where nothing catches it, so a scan failure takes the app down rather
        // than showing an empty library.
        //
        // It is also no longer needed. Track.uri() builds a content:// Uri from
        // the MediaStore _ID, which is the supported way to address media under
        // scoped storage; the raw path was only ever a fallback for tracks that
        // did not come from MediaStore, and every Track here does.
        val proj = arrayOf(
            MediaStore.Audio.Media._ID,
            MediaStore.Audio.Media.TITLE,
            MediaStore.Audio.Media.ARTIST,
            MediaStore.Audio.Media.DURATION
        )
        try {
            context.contentResolver.query(
                MediaStore.Audio.Media.EXTERNAL_CONTENT_URI,
                proj,
                "${MediaStore.Audio.Media.IS_MUSIC} != 0",
                null,
                "${MediaStore.Audio.Media.TITLE} COLLATE NOCASE ASC"
            )?.use { c ->
                // getColumnIndex, not OrThrow: a column the provider chooses not
                // to return should cost us one field, not the whole library.
                val iId = c.getColumnIndex(MediaStore.Audio.Media._ID)
                val iT = c.getColumnIndex(MediaStore.Audio.Media.TITLE)
                val iA = c.getColumnIndex(MediaStore.Audio.Media.ARTIST)
                val iD = c.getColumnIndex(MediaStore.Audio.Media.DURATION)
                if (iId < 0) return out
                while (c.moveToNext()) {
                    val id = c.getLong(iId)
                    if (id <= 0) continue
                    out += Track(
                        id = id,
                        title = if (iT >= 0) c.getString(iT) ?: "Unknown" else "Unknown",
                        artist = if (iA >= 0) c.getString(iA) ?: "" else "",
                        // Empty is honest: uri() uses the id, and an empty path
                        // only matters for the non-MediaStore fallback.
                        path = "",
                        durationMs = if (iD >= 0) c.getLong(iD) else 0L
                    )
                }
            }
        } catch (t: SecurityException) {
            // Permission revoked mid-scan, or never granted. An empty library
            // with the UI's existing explanation beats a crash.
            return emptyList()
        } catch (t: IllegalArgumentException) {
            return emptyList()
        }
        return out
    }
}
