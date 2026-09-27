# Building HashPlay (NCS-SMLauncher)

HashPlay is a pygame music player. It ships as a single-file executable
wrapped in a macOS `.app` bundle, with an Android port built from the same
Python sources.

---

## Prerequisites

The project targets **Python 3.14**.

```bash
brew install python@3.14
```

PyInstaller and the runtime dependencies come from `requirements-dev.txt`
(build) and `requirements.txt` (runtime). Both are installed by the venv
script below.

### One-time setup

```bash
./scripts/build_venv.sh
```

This is idempotent. It finds `python3.14` on `PATH`, falling back to
`brew --prefix python@3.14`, creates `.venv` if it is missing, upgrades pip,
and installs `requirements-dev.txt`. Re-run it any time the requirements
change.

---

## macOS (arm64)

```bash
./build_macos_app.sh
```

Produces `dist/HashPlay` (the PyInstaller one-file executable) and
`dist/HashPlay.app` (the double-clickable bundle).

### A note on `LSMinimumSystemVersion`

The bundle's minimum-system version is **derived from the active Python
framework's deployment target**, not hardcoded. PyInstaller rewrites the
Mach-O load command of the bundled interpreter, so the version the framework
was built against is what actually determines whether the bundle will launch
on an older macOS. A hardcoded plist value that disagrees with the binary
produces a bundle that looks fine and refuses to start.

Override it when you need to:

```bash
MACOS_MIN=12.0 ./build_macos_app.sh
```

---

## Linux (x86_64)

```bash
./scripts/build_linux.sh
```

Produces `dist/HashPlay` and `dist/HashPlay-linux-x86_64-<VERSION>.tar.gz`.

### Runtime dependencies

```bash
sudo apt-get install -y libsdl2-2.0-0 portaudio19-dev
```

`libsdl2` is what pygame's video and audio go through; `portaudio` backs the
`sounddevice` input used for the audio-reactive visualizer.

### libtorrent is OPTIONAL

`libtorrent` is **not** required. Without it, the torrent overlay reports
`libtorrent missing` and streaming falls back to direct HTTP; every other
feature — playback, the NCS sphere, chat, keybindings, album art — works
exactly the same. Install it (`pip install libtorrent`) if you want the
torrent overlay, and omit it if you want a smaller, more portable binary.

### Headless machines

There is no display to open on a server, so point SDL at a dummy driver:

```bash
SDL_VIDEODRIVER=dummy ./dist/HashPlay
```

The audio path still works with `SDL_AUDIODRIVER=dummy` if you only care
about the code paths and not the output.

---

## Android

```bash
cd android
cp local.properties.example local.properties
./gradlew assembleDebug
```

The APK lands in `android/app/build/outputs/apk/debug/`.

Toolchain versions: **AGP 8.13.0 / Kotlin 2.2.20 / Gradle 8.13 /
compileSdk 34 / JDK 17**. Use a JDK 17 toolchain — newer JDKs trip AGP's
bytecode version check.

`local.properties` points at your SDK and NDK; the example file is a template,
so copy it and edit the paths.

---

## Versioning

The root **`VERSION`** file is the single source of truth. It is read by:

- `src/version.py` (reported by the app and compared by the updater)
- the build scripts (`scripts/build_linux.sh` for the tarball name)
- the Android Gradle config

To cut a release:

```bash
echo "1.0.0" > VERSION
git add VERSION
git commit -m "chore: 1.0.0"
git tag v1.0.0
git push origin main --tags
```

Bump the file, commit, then tag `v<VERSION>`. Pushing the `v*` tag is what
triggers the release build in `.github/workflows/build.yml`, which is where
the assets the in-app updater downloads come from.

---

## Continuous integration

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `build.yml` | push to `main`, tag `v*`, PR, manual | Builds macOS arm64 and Linux x86_64 artifacts on every change |
| `dependencies.yml` | weekly (Mon 07:00 UTC), manual | Opens one tracking issue listing outdated pinned packages |

The updater verifies downloaded releases against the size and SHA-256 digest
published with the release, so a CI-built asset is only installable if it is
byte-identical to what GitHub says it is.

---

## Tests

Plain scripts, no pytest required:

```bash
python3 tests/test_updater.py        # release parsing + checksum verification
python3 tests/test_apply_pending.py   # the next-launch swap
python3 tests/test_ci_config.py       # workflows, scripts, docs
```
