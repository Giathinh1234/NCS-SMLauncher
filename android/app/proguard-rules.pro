# HashPlay ProGuard/R8 rules
#
# R8 is fully enabled for release builds (isMinifyEnabled = true plus
# isShrinkResources = true). That is what lets the LITE flavor drop
# libtorrent4j's 12.3 MB native library: the lite TorrentManager is a no-op
# with no reference to the engine, so nothing keeps it alive.
#
# The rules below are only what R8 cannot work out on its own. Nothing here
# exists to "fix" a crash that has not happened; each line is a case where the
# default behaviour is provably wrong for this app.

# --- libtorrent4j ---------------------------------------------------------
# Its native layer is reached by JNI from Java, and JNI entry points have no
# Java caller for R8 to trace, so it would strip classes the native code calls
# back into. This matters for the FULL flavor only; lite has no engine.
-keep class org.libtorrent4j.** { *; }
-keep class org.libtorrent4j.libtorrent.** { *; }
-dontwarn org.libtorrent4j.**

# --- Media3 / ExoPlayer ----------------------------------------------------
# The player is driven through a MediaSession, and extensions are resolved
# reflectively by class name from the manifest and from META-INF service
# declarations. R8 cannot see either.
-keep class androidx.media3.** { *; }
-keep class androidx.media3.exoplayer.** { *; }
-keep class androidx.media3.session.** { *; }
-keep class * extends androidx.media3.exoplayer.ExoPlayer
-dontwarn androidx.media3.**

# --- PlaybackService ------------------------------------------------------
# Referenced from AndroidManifest.xml only, so there is no code reference for
# R8 to follow. Without this the service class is renamed or removed and
# playback dies the moment the app is backgrounded.
-keep class com.giathinh.hashplay.PlaybackService { *; }

# --- Compose --------------------------------------------------------------
# The Compose compiler already emits the rules it needs; these cover the
# runtime pieces it does not.
-keep class androidx.compose.runtime.** { *; }
-dontwarn androidx.compose.**

# --- Kotlin coroutines ----------------------------------------------------
-dontwarn kotlinx.coroutines.**

# Keep line numbers so a crash report from a user's device is readable, and
# hide the original file name so it does not leak local paths.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile
