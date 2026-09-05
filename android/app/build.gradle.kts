plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.0"
}

android {
    namespace = "com.giathinh.hashplay"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.giathinh.hashplay"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
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
