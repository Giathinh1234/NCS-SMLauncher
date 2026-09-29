plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.0"
}

// ---------------------------------------------------------------------------
// App version — single source of truth is the repo-root VERSION file.
//
// The desktop build scripts and src/version.py read the same file, so the
// Android app can never advertise a different version than the desktop build.
// This block must stay *after* the `plugins {}` block: Gradle's Kotlin DSL
// requires `plugins {}` to be the first block in a build script.
//
// versionName is the file contents verbatim (e.g. "1.0.0").
// versionCode is derived as major * 10000 + minor * 100 + patch, which is
// monotonically increasing for 0.0.0 <= version < 1.0.0 with minor,patch < 100
// (e.g. 0.99 -> 9900, 1.0.0 -> 10000, 1.2.3 -> 10203). Google Play requires
// only that versionCode strictly increase between uploads, so keeping minor and
// patch below 100 preserves that ordering.
// -----------------------------------------------------------------------
// A missing, blank or malformed VERSION file must fail the build loudly, so
// every step below is guarded. `require` throws, which aborts configuration.
// -----------------------------------------------------------------------
val versionFile = rootProject.file("../VERSION")
require(versionFile.isFile) {
    "VERSION file not found at ${versionFile.absolutePath}. " +
        "It is the single source of truth for the app version and must be " +
        "committed at the repo root."
}
val appVersionName = versionFile.readText().trim()
require(appVersionName.isNotEmpty()) {
    "VERSION file at ${versionFile.absolutePath} is empty. " +
        "Put a version like '1.0.0' in it (major.minor.patch)."
}
// A prerelease suffix is allowed in the NAME: '1.1.0-rc.1' ships as a
// prerelease and must not abort the Android build the way a bare
// three-digit require did. The suffix is carried in versionName only; the
// versionCode is still derived from the three leading integers, so an rc and
// its final share a code. That is fine here because these APKs are
// distributed from GitHub releases, not Google Play. If Play ever becomes a
// channel, the final must be uploaded with a higher versionCode than its rc.
val versionCore = appVersionName.substringBefore('-')
val versionParts = versionCore.split(".")
require(versionParts.size == 3 && versionParts.all { it.isNotEmpty() && it.all(Char::isDigit) }) {
    "VERSION file at ${versionFile.absolutePath} contains '$appVersionName', " +
        "which is not a major.minor.patch triple with an optional -suffix. " +
        "The Android build derives versionCode from those three integers and " +
        "cannot continue."
}
val (versionMajor, versionMinor, versionPatch) = versionParts.map(String::toInt)
val appVersionCode = versionMajor * 10_000 + versionMinor * 100 + versionPatch

android {
    namespace = "com.giathinh.hashplay"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.giathinh.hashplay"
        minSdk = 26
        targetSdk = 34
        versionCode = appVersionCode
        versionName = appVersionName
    }

    buildFeatures { compose = true }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.06.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.2")

    // audio playback + visualizer
    implementation("androidx.media3:media3-exoplayer:1.3.1")
    implementation("androidx.media3:media3-session:1.3.1")

    // native torrent (infohash / magnet)
    implementation("org.libtorrent4j:libtorrent4j:2.1.0-30")
    implementation("org.libtorrent4j:libtorrent4j-android-arm64:2.1.0-30")

    // coroutines for the torrent/alert loops
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
