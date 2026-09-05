# NCS Music Launcher — Android

Native Android port of the desktop NCS-style music launcher.
Kotlin + Java, Jetpack Compose UI, Media3/ExoPlayer playback, libtorrent4j for
infohash/magnet downloads, custom Canvas visualizer.

## Build

Requires Android Studio (Hedgehog or newer) with SDK 34.

```bash
cd ncs-music-launcher/android
./gradlew assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk
adb install app/build/outputs/apk/debug/app-debug.apk
```

Or open the `android/` folder in Android Studio and press Run.

## Features

- Same neon FFT bar / mirror / radial visualizer (custom Compose Canvas)
- Library list from device Music folder (MediaStore) + Downloads
- Tap to play, tap again row to restart, swipe list scrolls
- Torrent overlay: paste infohash or magnet → native download via libtorrent4j
- Now-playing bar with seek slider, play/pause, next

## Structure

- `app/src/main/java/com/giathinh/hashplay/`
  - `MainActivity.kt` — single-activity Compose host
  - `PlayerScreen.kt` — library + now playing UI
  - `Visualizer.kt` — Canvas spectrum renderer
  - `PlayerController.kt` — Media3 player wrapper + FFT capture
  - `TorrentManager.kt` — libtorrent4j session (Kotlin)
  - `LibraryScanner.kt` — MediaStore audio query
