package com.giathinh.hashplay

import android.content.Context

/**
 * First-run state, ported from the desktop's setup_wizard.py.
 *
 * The desktop shows a four-step wizard (welcome, dependencies, folder, finish)
 * until it has a reason to exist. Two of those steps do not apply to Android:
 * `probe_dependencies()` (setup_wizard.py:134-159) asks whether ffmpeg is on
 * PATH and whether libtorrent imports, and neither question exists here.
 *
 * What does apply is the permission step, and it matters more than the desktop's
 * equivalent: without READ_MEDIA_AUDIO the library is simply empty and there is
 * no error anywhere to explain why. That is step one on Android.
 *
 * The rule that carries over unchanged is `needs_setup()` (setup_wizard.py:
 * 224-234): once setup is marked complete it never nags again, even if the user
 * walked away without finishing. Re-opening it on someone who escaped the first
 * run is exactly the nagging that makes people not install things. Escape is an
 * answer.
 *
 * Nothing here uses SharedPreferences' "first launch" flag, which fires even
 * when the process is killed mid-setup.
 */
class SetupState(context: Context) {

    private val prefs = context.applicationContext
        .getSharedPreferences("hashplay_setup", Context.MODE_PRIVATE)

    /** True while the wizard still has a reason to exist. */
    fun needsSetup(): Boolean = !prefs.getBoolean(KEY_COMPLETE, false)

    /**
     * Mark setup finished. Called when the user finishes the flow AND when
     * they explicitly skip it -- walking away is an answer, per the desktop.
     */
    fun complete() {
        prefs.edit().putBoolean(KEY_COMPLETE, true).apply()
    }

    /** The user picked a library folder, which answers the question outright. */
    var libraryFolder: String?
        get() = prefs.getString(KEY_FOLDER, null)
        set(value) = prefs.edit().putString(KEY_FOLDER, value).apply()

    private companion object {
        const val KEY_COMPLETE = "setup_complete"
        const val KEY_FOLDER = "library_folder"
    }
}
