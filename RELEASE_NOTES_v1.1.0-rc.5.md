# HashPlay 1.1.0-rc.5

A release candidate. **Marked prerelease on purpose** — the escape-hatch
behaviour below has not been manually confirmed on a real keyboard, and this
is the build you can try if you want to help check it.

**New in rc.4:**

- **The app no longer aborts a few seconds after opening.** rc.3 crashed
  every single time, seconds after launch. Two faults in the macOS media-key
  event tap: a constant name (`kCGEventTapDownOnMediaKey`) that does not exist
  in any pyobjc release, referenced bare in a tuple, so it raised
  `AttributeError` on *every* event and pyobjc turned that into an uncaught
  `NSException`; and the timeout handler calling `CGEventTapEnable(None, ...)`,
  which segfaults. If you could not see the app, this is why.
- **A lite build** on macOS: no control API, and the NCS ball redrawn from a
  third of its points so it costs half the frame time. Every visualizer full
  has, 13 MB less memory.
  Details and the measured numbers are in the lite section below.
- ESC fix and the Windows/Linux builds carried over from rc.3 unchanged.

**What changed from rc.3:** ESC could not quit the app when libtorrent was
installed. See the section below -- it is one of the two reasons this
candidate exists.

**What changed from rc.1:** rc.1 was macOS only; rc.2 and rc.3 have **Windows
and Linux** builds too. rc.1 was macOS only, because those platforms had never been
built. Both are now produced on every tag and verified on real runners. The
visualizer work also landed here: the non-NCS-ball visualizers were
overrunning the frame budget and are now 5-14x faster.

Nothing in the in-app updater will offer this to you automatically.
Prereleases are opt-in, so a build this unverified cannot be pushed at
someone who did not ask for it.

## The big one: your music now advances on its own

**The app could never advance a track by itself.** There was no end-of-track
check anywhere in the program. The comment on the relevant line claimed the
UI handled it; nothing did. You got one track, and then the app sat on the
final sample showing NOW PLAYING until you pressed next. Play an album now
and it plays through.


## Android

The Android port now ships two APKs alongside the desktop builds. They install
side by side: different application ids, so you can try one without
uninstalling the other.

| APK | size | torrents |
|---|---|---|
| `HashPlay-full-android-arm64.apk` | 14.8 MB | yes |
| `HashPlay-lite-android-arm64.apk` | 2.5 MB | no |

The lite APK is smaller than the full one by more than the torrent engine
accounts for: R8 shrinks both, and with no reachable reference to libtorrent4j
the 12.3 MB native library is dropped from lite entirely. Playback, the
library, the visualizer and the first-run setup are identical in both.

Torrents are the only thing lite gives up. Pasting a magnet into it says so
rather than failing silently.

Both are arm64, minSdk 26 (Android 8.0), and signed. They are distributed from
this release, not Google Play, so there is no Play listing and no auto-update
channel: install the APK and Android will ask once about installing from
unknown sources.

The lite APK is labelled "HashPlay Lite" on the home screen so the two icons
are tellable apart at a glance.

**Not verified:** neither APK has been run on a physical device or an emulator.
They build, sign, and are shaped and labelled correctly, but "it installs and
plays audio" has not been demonstrated. Treat the first install as a test.

## Playing a file that will not decode no longer kills the app

`load()` had 18 call sites with no error handling, and miniaudio raises on a
truncated or zero-length file — so one bad file in your library took the
whole thing down. The repository itself shipped a zero-byte `music/test.mp3`,
which made this reachable without doing anything unusual.

It now reports the file, names it, and moves on. The file has been removed.

## Video sidecars no longer flood the machine

If a sidecar video was shorter than its track, the playhead and the stored
seek target disagreed permanently, so the app re-seeked *every frame*:
measured at 60 ffmpeg process spawns and 14.6 seconds of work per second of
frames. It now seeks once.

## Bugs that were silently doing nothing

- **Media keys never worked at all.** The event mask referenced a constant
  that does not exist in pyobjc on this machine, and the failure was
  swallowed. Previous-track was separately dead — it was wired to a name the
  handler never matched.
- **The "O" key forgot your library folder.** It reimplemented the folder
  picker without the part that saves. Two copies of one action, and they
  disagreed.
- **Turning off the control hints needed a restart.**
- **Two of the six visualizer modes rendered nothing** — "disc" and "album"
  painted into a surface that was never composited, and burned a PIL decode
  per frame for a window nobody could see. Measured 0 non-black pixels;
  now ~118k and ~99k.
- **Every message in the app was invisible.** All 26 of them drew from
  inside the torrent panel, which requires libtorrent. With it absent you
  were told nothing — not about resizes, not about a missing library, not
  about a failed API bind. There is now a message bar that does not depend
  on it.
- **Escape needed three presses to quit**, in a fresh install. The first was
  spent dismissing a torrent panel that was never on screen. It is two now.

## The self-updater did not work at all, and was not even reachable

Not "unreliable" — it could not have worked for any release this project has
ever produced, for five independent reasons, all fixed:

1. It could not see our own macOS asset. The filter accepted only `.app.zip`
   or exactly `HashPlay`; releases publish `HashPlay-macos-arm64`. Every
   lookup returned nothing, so the app always reported "up to date".
2. A release with no installable artifact ended the whole scan, so an older
   release that *did* have one was never tried.
3. `self_update` never passed the current version, so nothing could ever
   qualify as newer.
4. The swap relaunched with `nohup "$TARGET"` — but on macOS that target is
   `HashPlay.app`, a directory. You quit, the swap happens, and the app never
   comes back. It uses `open` now.
5. Every request to GitHub failed TLS verification, because the system trust
   store on this machine is missing the issuer chain. That is the one worth
   dwelling on: the updater's own `except Exception: return None` turned the
   `SSLCertVerificationError` into a quiet "you are up to date", so the
   feature reported success while being completely broken. It now uses
   certifi, which is bundled.

And the fifth thing was the real one: **nothing in the app ever called it.**
The module shipped in the bundle and had tests, but no import, no dispatch
entry, no key — so there was no path from the interface to the updater at
all. Describing it as a "shipped headline feature" was not accurate.

There is now an `update_check` command that reports whether a newer release
exists. It is deliberately check-only: anything that can reach the loopback
API can ask, but nothing can replace your binary without you deciding to.
Installing is still a manual step, and that is a known gap, not an oversight.

A related caveat worth stating plainly: that `except Exception: return None`
is exactly the shape of code that hides bugs. It is the reason four of these
five went unnoticed. Some failures should be reported, not swallowed.

## Let your bots and agents drive it

`POST /webhook` — one flat endpoint, so a bot does not need to know the
command set:

```bash
curl -X POST "http://127.0.0.1:8777/webhook?token=$TOKEN" \
     -d "track=daft punk"
```

Accepts JSON or ordinary form encoding, so Discord, IFTTT, n8n and a plain
script all work without special handling. Aliases (`track`, `title`, `q`,
`song`, `name`) are normalised, and the response is plain text, because
webhook test screens display response bodies clearly. `hashplay-ctl hook`
prints the paste-ready URL.

This is **local only**. A bot on another machine still needs a relay, a
tunnel, or an exposed listener.

## First run

There is now a setup wizard: welcome, dependencies, library folder, summary.
It only appears when it should — not when you passed a folder on the command
line, not when one is already saved, and skipping is remembered.

## Also

- API and webhook authentication; the token lives in a `0600` file and is
  no longer printed in full on screen.
- `requirements.txt` now exists. CI referenced it in three places and it was
  in neither the repository nor `.gitignore`, so every build had been failing
  at the install step.
- A hostile-startup test suite: corrupt config, bad environment values,
  missing folders, a port that is already taken.
- ffmpeg processes are reaped properly instead of piling up as zombies.
- Long folder paths no longer hang the UI in an infinite ellipsis loop. This
  one is worth calling out: it silently froze the app, and it only showed up
  when testing the live API, because `/status` stops answering when the frame
  counter stops moving.

## Testing

38 test files, all passing. Every fix above is covered by a test that fails
without the fix — including the auto-advance, which is verified end to end
against the real app playing real audio, and the seek storm, which is
measured rather than asserted.

## Known gaps in this candidate

- **The escape-hatch behaviour is unverified on a real keyboard.** The logic
  is covered by a test that runs the actual code path, but synthetic
  keystrokes are blocked on this machine, so nobody has pressed it. That is
  the main reason this is an rc.
- The API and webhook are **loopback only**.
- The macOS binary is **unsigned**.
- Streaming playback and library organisation are written but not yet
  wired into the main UI.
- No Linux artifact.

## Windows and Linux builds

Both platforms now build on every tag, verified on real GitHub runners rather
than assumed:

- `HashPlay-windows-x64.exe` — a genuine PE32+ x86-64 binary
- `HashPlay-linux-x86_64` — built on ubuntu-latest

The Windows executable is **unsigned**. SmartScreen will warn on first run
and Defender may quarantine it. That is expected for an unsigned build, not
a broken one.

Media keys (rewind / play-pause / fast-forward) are macOS-only, since they go
through a Quartz event tap. On Windows the in-window keys work and the
hardware media keys are not intercepted. Torrent downloads need libtorrent;
if that wheel is unavailable for your platform the build still succeeds and
torrents are simply disabled, with everything else working.

## The visualizers were eating the frame

The non-NCS-ball visualizers were over budget, so the visualizer — not the
game logic — was what the main loop was waiting on. At a 1280x748 window,
against a 16.7 ms frame:

| mode | before | after |
|---|---|---|
| mirror | 21.2 ms | 1.5 ms |
| disc | 17.4 ms | 2.3 ms |
| bars | 13.9 ms | 1.9 ms |
| album | 10.0 ms | 3.1 ms |
| radial (NCS ball) | 13.4 ms | 12.3 ms |

Three causes, all found by profiling:

- A full-window 3.8 MB surface was allocated **every frame**, for every mode,
  including two that never touched it. Reused now.
- The entire album-art pipeline ran 60 times a second — PNG decode, LANCZOS
  resize, BICUBIC rotate, circular mask — for an image that does not change
  while a track plays. That was **49% of the disc frame**. Cached per file
  and size, with the rotation quantised to 3°, which is invisible at sticker
  size and cuts the misses fourfold.
- One `Surface` was allocated **per bar, per frame** — 64 for bars, 128 for
  mirror. The target surface is already alpha-capable, so those were pure
  waste.

Idling is cheaper too: a paused player stops the record spinning, so a still
player no longer pays for a rotation it cannot show.

The output was checked as well as the timings — a speedup this large is also
what a silently broken render looks like, so the tests assert every mode
still lights up, that the modes are visually distinct from each other, and
that a cold cache renders byte-identical to a warm one.

## ESC could not quit the app

Found by pressing the key for real, not by reading the code.

The ESC handler decided the torrent panel was on screen by testing
`notice or overlay_open`. But `notice` is shared state -- every one-line
message in the app goes through it, **including the "press Esc again to quit"
warning that the quit ladder itself raises**. So the first ESC armed the quit
and set a notice, and the second ESC read that notice, concluded a panel was
open, dismissed the panel, and never reached the quit.

With libtorrent installed, ESC could not quit HashPlay at all. Only Q worked.
Without libtorrent the branch is skipped, which is why it had gone unnoticed.

The fix asks whether the panel is genuinely on screen -- the infohash prompt
being up, or there being torrent status lines to show. A bare notice is a
banner drawn by `draw_notice()`, not a panel.

The branch tests could not have caught this. They set the modal flags directly
and never pressed ESC twice in a row, which is the only path that produces the
notice in the first place; their `torrents` stub had no `status_lines()`, and
their `push_notice` was a no-op, so the arming notice never existed as far as
the second press was concerned. One case was worse than silent: it passed a
`notice` and called it "the panel is on screen", encoding the bug as the
expected result.

There is now a check that presses ESC on the running app with real OS-level
key events and watches the process actually exit. Run it yourself:

    HASHPLAY_ESC_TARGET=source python3 tests/test_esc_os_level.py

Verified on the shipped `.app` bundle as well: one press arms, the second
quits, and Q still quits immediately.

## Low-fuel builds, if you want less of it

Two stripped builds, same codebase, no second copy to drift out of sync:

| build | what it drops | peak memory | download |
|---|---|---|---|
| full | nothing | 121 MB | 30 MB |
| **lite** | control API; ball at 1/3 density | 116 MB | 30 MB |
| **micro** | + video (ffmpeg), torrents | 98 MB | **25 MB** |

Measured at 1280x748 with each build drawing every mode it offers.

`micro` is the low-fuel one if you only want music. It keeps playback, your
library, the first-run wizard, settings and keymaps. You lose the control API
(bots, scripts, `hashplay-ctl`), video, and torrents.

Micro also drops the NCS ball entirely, which is why its bundle is 5 MB
smaller: lite keeps the ball and draws it from a smaller point cloud, while
micro leaves the renderer out of the bundle altogether.

**Both stripped builds were found broken and fixed, and the reason is worth
knowing if you build this yourself.**

The variant was originally an environment variable, `HASHPLAY_LITE=1`. PyInstaller
analyses *imports*, not the environment, so the flag never reached the binary:
the first "lite" build opened a listening socket on 8777 and wrote an
`api_token` file while advertising itself as lite. Every source-level test
passed, because the source was correct. Only running the finished bundle and
checking for a socket could have caught it.

Then micro crashed on startup, in the same family of bug — a per-frame
`api.publish(...)` and a shutdown `torrents.session.pause()` that assumed the
API and the torrent manager always exist. Making them optional means every
call site has to know, and four did not.

And the biggest one: `ncs_sphere` and `ncs_video` are *lazy* imports in the
source, so the `.spec` has to name them explicitly or a frozen build cannot
import them at all. Naming them unconditionally meant a micro build shipped,
extracted and **loaded** both — confirmed with `lsof` against a running micro
build, which had libtorrent's dylibs mapped in. A micro build has to exclude
them in the spec too, and PyInstaller's own analysis keeps finding libtorrent
regardless, because the `if BUILD_PROFILE != "micro":` around the import is not
something a static analyser evaluates.

So: the constant in `src/build_variant.py` governs *runtime behaviour*, and
`HASHPLAY_PROFILE` governs *what gets collected into the binary*. Both have to
agree, and both are now checked. `scripts/verify_lite_binary.sh` runs the
finished bundle and verifies the socket, the token file, the loaded modules
and that it stays alive —

    scripts/build_macos_stripped.sh micro
    scripts/verify_lite_binary.sh micro

## Install

Download the build for your platform and check `SHA256SUMS.txt` if you want to
verify it. On macOS, move `HashPlay-macos-arm64` to Applications. On Windows,
`HashPlay-windows-x64.exe` runs as-is.