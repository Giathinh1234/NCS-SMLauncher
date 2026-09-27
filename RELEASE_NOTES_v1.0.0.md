# HashPlay 1.0.0

The first stable release. Video backgrounds, a real settings system with
remappable keys, a 44x faster sphere, and a self-updater.

---

## The two headline changes

### The sphere is 44x faster, and finally looks right

The NCS ball was taking **372 ms per frame** at 1280x800 — about 3 FPS. It is
now **8.4 ms**, inside a 16.67 ms budget for 60 FPS.

The cause was not what it looked like. The old notes blamed the nine-tap
membrane splat. The profile said the real cost was `np.bincount` allocating a
window-sized float64 accumulator — 1,024,000 bins, 8.2 MB, three times per
splat, ten splats per frame. That cost is fixed by the *buffer size*, not by
the point count, which is why every earlier attempt to fix it by lowering
density failed: cutting the cloud from 111k points to 27k moved the frame time
by 6%.

The fixes, in order of impact:

1. Accumulate into the point cloud's **bounding box**, not the whole window.
   A sphere is a circle covering about half of it.
2. Evaluate the splat kernel as one `(k, npts)` plane instead of `k` passes —
   36 small gathers became 4 large ones.
3. Drop non-solid points before the nine-tap fill. They were multiplied by
   zero anyway.
4. Render at half internal resolution, with the density target raised from
   3.5 to 2.0 px/point to compensate for each dot now covering 2x2 pixels.

Quality was decided by rendering the variants and looking at them, not by
guessing: the shipped setting carries **more** lit pixels than the old
full-resolution render (373,918 vs 251,928) with no visible row striping.

### Video backgrounds (`V`)

The visualizer slot can now play video. It is a **background layer**, not a
video player — the UI stays responsive and the audio is untouched.

- **MP4 / MKV / MOV / WebM** files, **`.strm`** files, and web URLs
- **Automatic sidecar discovery**: put `song.mp4` next to `song.mp3` and it
  plays on that track by itself
- An explicit source you pick yourself is **pinned** and outranks sidecars
- Follows the playhead; a track change resyncs
- Falls back to the next visualizer if the video fails, and says why

It pipes raw RGB24 out of `ffmpeg` rather than using OpenCV, because
OpenCV's bundled SDL2 collides with pygame's and takes the window down.

Frame reads are non-blocking: the first frame waits briefly so something
appears, and every frame after that uses `select()` + `read1()` and reuses the
last surface when nothing is ready. A plain `BufferedReader.read(n)` was
measured stalling the draw path for **268 ms** even after `select()` reported
the pipe ready.

**Requirement:** `ffmpeg` and `ffprobe` on your `PATH` (`brew install ffmpeg`).
The `V` key refuses with a clear message if they are missing.

---

## Settings and remappable keys (`,`)

- **Every action is rebindable.** Rebinding takes effect immediately — the
  hardcoded per-key branches are gone from the event loop.
- Settings persist to `~/.ncs-smlauncher/settings.json`, written atomically.
- **Ordered, idempotent schema migrations** run on load, so an old settings
  file upgrades itself. A file written by a *newer* build is never downgraded.
- A corrupt or unreadable settings file is reported and ignored — it can never
  stop the app from starting.
- `show_hints` actually hides the hint bar.
- The library folder you choose is remembered across restarts.

Defaults: `↑↓` select, `⏎` play, `Space` pause, `←→` seek, `F` visualizer,
`V` video, `C` Hermes, `T` torrents, `O` folder, `M` mute, `,` settings,
`Q` quit.

## Self-updater

- Checks GitHub Releases for a newer tag, picks the right asset for your
  platform, and verifies the published **SHA-256** before accepting it.
- Downloads are **staged**; nothing is swapped while the app is running.
- The swap happens on next launch through a detached helper, and only with
  explicit opt-in.
- A staged file that went missing or fails its checksum is refused, not
  installed.
- If the old process is still alive when the swap is attempted, the update
  **aborts and leaves the bundle staged** rather than replacing a running app.

## Other fixes

- Media keys and absent `libtorrent` are clean no-ops off macOS instead of
  import crashes.
- Android: `gradlew` is the real wrapper (committed with mode 755),
  `local.properties` is no longer tracked, and `versionName` reads the root
  `VERSION` file instead of claiming `1.0`.
- One version source of truth: the root `VERSION` file drives the app, both
  build scripts, and Gradle.
- macOS `LSMinimumSystemVersion` is derived from the actual Python framework
  rather than being a hand-typed claim that made the binary fail to launch.

---

## Known gaps in 1.0.0

Stated plainly so nobody is surprised:

- **Streaming playback during a download is not wired up yet.**
  `src/streaming_source.py` is complete and tested (incremental decode of a
  growing file, bit-exact against a full decode, torn-tail safe), but the
  `Player` and main loop do not consume it yet. Partial downloads still play
  as far as they have decoded, which is the old behaviour.
- **The library "move all music" organizer is not in the UI.**
  `src/library_ops.py` is complete and tested, including refusing a move into
  a subdirectory of the source and never clobbering an existing destination,
  but no key triggers it yet.
- **Android was not built or run.** The SDK is not installed on the build
  machine, so the fixes are committed and unverified by an actual Gradle
  build. `gradlew` may need network access on first run.
- **Release assets are macOS arm64 only.** The Linux build script exists and
  is tested, but no Linux runner produced a binary for this release.
- The `V` overlay takes a path, a `.strm`, or a URL. There is no video file
  browser.

## Requirements

- macOS (arm64) for the bundled app
- `ffmpeg` + `ffprobe` on `PATH` for video backgrounds (everything else works
  without them)
- `libtorrent` and `sounddevice` are optional; the app reports what is missing
  instead of failing to start
