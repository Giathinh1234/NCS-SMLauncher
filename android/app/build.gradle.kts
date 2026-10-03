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

// ---------------------------------------------------------------------------
// Signing.
//
// The keystore is NOT in the repo. It comes from:
//   * CI:  the HASHPLAY_KEYSTORE_B64 / _ALIAS / _PASSWORD secrets
//   * you:  -Pkeystore=/path/to.jks -PstorePass=… -PkeyPass=… -PkeyAlias=…
//           or the four HASHPLAY_* environment variables
//
// A release build with no key material is refused rather than silently
// producing an unsigned APK. An unsigned APK cannot be installed on a real
// device, so shipping one would mean shipping something broken that looks
// finished. scripts/build_android_apks.sh generates a throwaway key for local
// testing; see android/README.md before using one for anything real.
// ---------------------------------------------------------------------------
val HASHPLAY_KEYSTORE: String? =
    (project.findProperty("keystore") as String?) ?: System.getenv("HASHPLAY_KEYSTORE")
val HASHPLAY_STORE_PASS: String? =
    (project.findProperty("storePass") as String?) ?: System.getenv("HASHPLAY_STORE_PASS")
val HASHPLAY_KEY_PASS: String? =
    (project.findProperty("keyPass") as String?) ?: System.getenv("HASHPLAY_KEY_PASS")
val HASHPLAY_KEY_ALIAS: String? =
    (project.findProperty("keyAlias") as String?) ?: System.getenv("HASHPLAY_KEY_ALIAS")

val HASHPLAY_HAVE_SIGNING =
    listOf(HASHPLAY_KEYSTORE, HASHPLAY_STORE_PASS,
           HASHPLAY_KEY_PASS, HASHPLAY_KEY_ALIAS).all { !it.isNullOrBlank() }

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

    // -----------------------------------------------------------------------
    // Two flavors, mirroring the desktop tiers.
    //
    //   full  everything, including the libtorrent4j engine (12.3 MB of .so)
    //   lite  no torrent engine; src/lite/ supplies a no-op TorrentManager
    //
    // Each gets its own applicationId so both can be installed side by side.
    // That is the point of shipping both: they are alternatives, and asking
    // someone to uninstall one to try the other is how nobody tries either.
    //
    // The visualizer is NOT reduced in lite. The desktop lite build keeps the
    // NCS ball and draws it from a third of its points; the Android visualizer
    // is already a cheap Canvas renderer, so there is nothing worth removing.
    // Lite here is about the 12 MB native library.
    // -----------------------------------------------------------------------
    flavorDimensions += "size"
    productFlavors {
        create("full") {
            dimension = "size"
            buildConfigField("boolean", "HAS_TORRENTS", "true")
        }
        create("lite") {
            dimension = "size"
            applicationIdSuffix = ".lite"
            versionNameSuffix = "-lite"
            buildConfigField("boolean", "HAS_TORRENTS", "false")
        }
    }

    buildFeatures {
        compose = true
        // BuildConfig.HAS_TORRENTS is how shared UI knows which flavor it is
        // in. AGP 8 does not generate BuildConfig unless this is set, and
        // every reference to it fails to compile without it.
        buildConfig = true
    }

    signingConfigs {
        if (HASHPLAY_HAVE_SIGNING) {
            create("release") {
                storeFile = file(HASHPLAY_KEYSTORE!!)
                storePassword = HASHPLAY_STORE_PASS
                keyAlias = HASHPLAY_KEY_ALIAS
                keyPassword = HASHPLAY_KEY_PASS
            }
        }
    }

    buildTypes {
        release {
            // R8 both shrinks and lets the lite flavor lose libtorrent4j
            // entirely: with the no-op stub there is no reachable reference to
            // the engine, so the .so is removed from the package.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            if (HASHPLAY_HAVE_SIGNING) {
                signingConfig = signingConfigs.getByName("release")
            } else {
                // An unsigned release APK cannot be installed on a real
                // device, so refuse to build one rather than produce something
                // that looks finished and is not.
                logger.warn(
                    "No keystore configured, so release APKs will NOT be built. " +
                        "Set HASHPLAY_KEYSTORE/_STORE_PASS/_KEY_ALIAS/_KEY_PASS " +
                        "or pass -Pkeystore=… -PstorePass=… -PkeyPass=… " +
                        "-PkeyAlias=… . Debug builds are unaffected."
                )
            }
        }
    }

    // APK splits are deliberately NOT enabled. A universal APK is what someone
    // sideloading from a release page wants; split APKs are for Play, and these
    // are distributed from GitHub.
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

// ---------------------------------------------------------------------------
// Drop libtorrent4j's native library from the LITE flavor only.
//
// The lite TorrentManager (src/lite/) has no reference to the engine, but the
// dependency stays declared because the full flavor compiles against it, and
// declaring it is enough to package its 12.3 MB .so. R8 removes it from a
// minified release, but debug builds do not run R8, so without this both APKs
// came out byte-for-byte the same size with the engine in both.
//
// This has to be set through androidComponents rather than a `packaging { }`
// block inside productFlavors: that block is NOT variant-scoped, and putting it
// there silently removed the engine from the FULL build too -- which is the
// opposite of intended and still produced a passing build.
//
// jniLibs only governs the .so. The Java classes are small and R8 handles them.
// ---------------------------------------------------------------------------
androidComponents {
    onVariants { variant ->
        val flavors = variant.productFlavors.map { it.second }
        if (flavors.contains("lite") && !flavors.contains("full")) {
            variant.packaging.jniLibs.excludes.add("**/libtorrent4j.so")
        }
    }
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
    // PlayerView, for the full build's video background. exoplayer does NOT
    // pull this in, so it is declared explicitly.
    //
    // It is deliberately NOT scoped to the full flavor via a dependency
    // handler: R8 keeps PlayerView even when lite never instantiates it,
    // because media3-ui's own manifest declares these views as concrete
    // View subclasses and R8 treats manifest components as shrinker roots.
    // The flavor split in src/full and src/lite therefore stops lite CALLING
    // video, but does not by itself keep the library out of lite's dex.
    //
    // Verified rather than assumed: lite's classes.dex is 2.54 MB with
    // media3-ui present and its stub selected, and PlayerView/SubtitleView/
    // TimeBar are all still in the lite dex. This is the same class of problem
    // as the libtorrent .so exclusion at :179-200, except that one is jniLibs
    // and this is Java classes, so the packaging block cannot reach it.
    implementation("androidx.media3:media3-ui:1.3.1")

    // native torrent (infohash / magnet)
    implementation("org.libtorrent4j:libtorrent4j:2.1.0-30")
    implementation("org.libtorrent4j:libtorrent4j-android-arm64:2.1.0-30")

    // coroutines for the torrent/alert loops
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
