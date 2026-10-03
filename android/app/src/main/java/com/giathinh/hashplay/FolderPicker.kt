package com.giathinh.hashplay

import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import androidx.documentfile.provider.DocumentFile

/**
 * Let the user point the library at a folder of their choosing.
 *
 * The desktop has this and Android did not: it only ever scanned all of
 * MediaStore, so a phone with a few hundred tracks had no way to narrow the
 * library to one album, one artist, or one folder of downloads.
 *
 * Android's answer is the Storage Access Framework. The user picks a real
 * folder through the system picker, the app gets a persistable read grant for
 * it, and that grant survives reboots -- which a raw content:// Uri does not.
 *
 * Two honest limits:
 *   - A SAF folder is NOT the same as a MediaStore row. MediaStore indexes on
 *     its own schedule, so audio copied in by hand often does not appear until
 *     the provider rescans. That is why adding a folder calls
 *     MediaScannerConnection rather than expecting MediaStore to already know.
 *   - MediaStore.Audio.Media.DATA is never queried here. It is deprecated in
 *     API 29 and the provider may omit it; see LibraryScanner for what that
 *     costs when assumed present.
 */
object FolderPicker {

    /**
     * Persist the read grant for [tree] and return the persisted Uri, or null
     * if the platform would not grant it.
     */
    fun persist(context: Context, tree: Uri): Uri? {
        val flags = android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION
        return try {
            context.contentResolver.takePersistableUriPermission(tree, flags)
            tree
        } catch (t: SecurityException) {
            // The provider refused a persistable grant. A one-shot read still
            // works for this session, so use it rather than failing outright.
            null
        }
    }

    /** True when a previously granted folder is still readable after reboot. */
    fun stillGranted(context: Context, tree: Uri): Boolean {
        val persisted = context.contentResolver.persistedUriPermissions.any {
            it.uri == tree && it.isReadPermission
        }
        return persisted || canRead(context, tree)
    }

    private fun canRead(context: Context, tree: Uri): Boolean = try {
        DocumentFile.fromTreeUri(context, tree)?.exists() ?: false
    } catch (t: SecurityException) {
        false
    }

    /**
     * Ask MediaStore to index what was just written.
     *
     * Audio added by USB, a file manager, or adb does not appear in the library
     * until something tells the provider to look. Without this, pushing files
     * onto the device and expecting them in the app shows an empty list.
     */
    fun requestRescan(context: Context, tree: Uri) {
        val docs = try {
            DocumentFile.fromTreeUri(context, tree)
        } catch (t: SecurityException) {
            null
        } ?: return
        val paths = mutableListOf<String>()
        collectAudio(docs, paths, depth = 0)
        if (paths.isEmpty()) return
        android.media.MediaScannerConnection.scanFile(
            context.applicationContext,
            paths.toTypedArray(),
            arrayOf("audio/*"),
            null
        )
    }

    private val AUDIO_EXT = setOf(
        "mp3", "m4a", "aac", "flac", "ogg", "opus", "wav", "wma", "alac", "aiff"
    )

    private fun collectAudio(dir: DocumentFile?, out: MutableList<String>, depth: Int) {
        // Bounded so a user who picks / cannot hang the app walking the whole
        // filesystem. Six levels is deeper than any sane music folder.
        if (dir == null || depth > 6) return
        if (dir.isFile) {
            val name = dir.name ?: return
            if (name.substringAfterLast('.', "").lowercase() in AUDIO_EXT) {
                out += dir.uri.toString()
            }
            return
        }
        for (child in dir.listFiles()) collectAudio(child, out, depth + 1)
    }

    /** Human label for a stored folder, for the UI. */
    fun describe(context: Context, tree: Uri): String {
        val name = try {
            DocumentFile.fromTreeUri(context, tree)?.name
        } catch (t: SecurityException) {
            null
        }
        return name ?: tree.lastPathSegment?.substringAfterLast(':') ?: "folder"
    }

    /** The intent the UI launches. */
    fun intent(): android.content.Intent =
        android.content.Intent(android.content.Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
            addFlags(
                android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION or
                    android.content.Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
            )
        }
}