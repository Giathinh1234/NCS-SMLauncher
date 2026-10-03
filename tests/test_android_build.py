"""The Android build's contract, checked without a device or a Gradle run.

Every assertion here is about something that has actually gone wrong during
this work, or would go wrong silently:

  * a `packaging { }` block inside productFlavors is NOT variant-scoped, so
    excluding libtorrent4j there stripped it from the FULL build too -- and the
    build still went green
  * the release job's file list dropped HashPlay.app.zip when it was narrowed
    to explicit names, which would have quietly removed the main macOS
    download
  * TorrentManager.kt sat in src/main, so the lite stub redeclared it and lite
    stopped compiling

Checks are static. Building both APKs and inspecting them is
scripts/build_android_apks.sh's job, and it runs in CI.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRADLE = os.path.join(REPO, "android", "app", "build.gradle.kts")
WORKFLOW = os.path.join(REPO, ".github", "workflows", "build.yml")
PROGUARD = os.path.join(REPO, "android", "app", "proguard-rules.pro")
ANDROID_SRC = os.path.join(REPO, "android", "app", "src")
BUILD_SCRIPT = os.path.join(REPO, "scripts", "build_android_apks.sh")

FAILS = []


def check(cond, label, detail=""):
    if not isinstance(detail, str):
        detail = str(detail)
    print("   %s  %s%s" % ("ok " if cond else "FAIL", label,
                           "" if cond or not detail else "  -- " + str(detail)))
    if not cond:
        FAILS.append(label)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


gradle = read(GRADLE)
workflow = read(WORKFLOW)

print("ANDROID BUILD")
check(os.path.isfile(GRADLE), "app/build.gradle.kts exists")
check(os.path.isfile(PROGUARD), "proguard-rules.pro exists")
check(os.path.isfile(BUILD_SCRIPT), "scripts/build_android_apks.sh exists")

print("\n  both flavors exist")
check("productFlavors" in gradle, "product flavors are declared")
check(re.search(r'create\("full"\)', gradle) is not None, "a full flavor")
check(re.search(r'create\("lite"\)', gradle) is not None, "a lite flavor")

print("\n  they can be installed side by side")
check('applicationIdSuffix = ".lite"' in gradle,
      "lite has its own applicationId suffix")
# If both had the same id, installing one replaces the other, which defeats
# the point of offering a choice.
ids = re.findall(r'applicationId(?:Suffix)?\s*=\s*"([^"]+)"', gradle)
check(len(set(ids)) >= 2, "full and lite have different application ids", ids)

print("\n  lite really drops the torrent engine")
# The exclude must be variant-scoped. A `packaging { }` inside
# productFlavors applies to every variant and silently removed the engine from
# the FULL build, which still passed.
check("androidComponents" in gradle,
      "the jniLibs exclude is scoped via androidComponents, not productFlavors")
flavor_block = gradle[gradle.find("productFlavors"):gradle.find("androidComponents")]
check("jniLibs" not in flavor_block,
      "no unscoped jniLibs exclude inside productFlavors")
check('"**/libtorrent4j.so"' in gradle, "libtorrent4j.so is excluded by name")
check('flavors.contains("lite")' in gradle and '!flavors.contains("full")' in gradle,
      "the exclude applies to lite and NOT to full")

print("\n  the two TorrentManagers do not collide")
check(os.path.isfile(os.path.join(ANDROID_SRC, "full", "java", "com",
                                  "giathinh", "hashplay", "TorrentManager.kt")),
      "the real TorrentManager lives in src/full/")
check(os.path.isfile(os.path.join(ANDROID_SRC, "lite", "java", "com",
                                  "giathinh", "hashplay", "TorrentManager.kt")),
      "the lite stub lives in src/lite/")
check(not os.path.isfile(os.path.join(ANDROID_SRC, "main", "java", "com",
                                      "giathinh", "hashplay", "TorrentManager.kt")),
      "neither is in src/main, where both would compile at once")

# Both APKs sit on the same home screen, so the labels must differ or there is
# no way to tell the icons apart. The label is a resource, not a manifest
# literal, precisely so the lite flavor can override it.
manifest = read(os.path.join(REPO, "android", "app", "src", "main",
                             "AndroidManifest.xml"))
check('android:label="@string/app_name"' in manifest,
      "the manifest label is a resource, so a flavor can override it")
lite_strings = read(os.path.join(ANDROID_SRC, "lite", "res", "values", "strings.xml"))
check("HashPlay Lite" in lite_strings, "lite labels itself 'HashPlay Lite'")

lite_stub = read(os.path.join(ANDROID_SRC, "lite", "java", "com",
                              "giathinh", "hashplay", "TorrentManager.kt"))
real = read(os.path.join(ANDROID_SRC, "full", "java", "com",
                         "giathinh", "hashplay", "TorrentManager.kt"))
# Match an IMPORT, not the bare word: the stub's comment explains what it
# replaces and names the library, so a substring check fails on its own
# documentation.
check(not re.search(r"^\s*import\s+org\.libtorrent4j", lite_stub, re.M),
      "the lite stub does not import libtorrent4j (that is what drops the .so)")
# PlayerScreen.kt is shared and compiles against whichever file is present, so
# the two must expose the same surface. A drift here breaks the lite build.
for symbol in ("class TorrentManager", "data class Status", "val statuses",
               "val messages", "val downloadDir", "fun start(",
               "fun normalize("):
    in_lite = symbol in lite_stub
    in_real = symbol in real
    check(in_lite and in_real,
          f"both TorrentManagers expose {symbol!r}",
          f"lite={in_lite} full={in_real}")

# ---------------------------------------------------------------------
# Lite must REFUSE loudly. Verified on an emulator, not assumed.
#
# On the emulator the lite APK installed, launched, rendered and stayed
# alive -- but the "+ torrent" path could NOT be exercised: a headless
# emulator exposes no touch digitizer (no input device advertises
# ABS_MT_POSITION_X, so `input tap` injects events nothing consumes, silently).
# Keyboard events still work; touch does not. So the refusal path is checked
# against the source here instead of being claimed as device-verified.
# A bash-ism hid here: the script runs under /bin/sh and used ${FLAVOR^},
# which only fails when ONE flavor is requested. `both` took a separate branch
# and always worked, so every test that built both flavors passed while
# `build_android_apks.sh full` died with "bad substitution" on the line before
# Gradle was ever invoked.
print("\n  the APK build script runs under /bin/sh, not just bash")
script = read(REPO + "/scripts/build_android_apks.sh")
_code = "\n".join(l for l in script.splitlines()
                  if not l.lstrip().startswith("#"))
check("${FLAVOR^}" not in _code and "${FLAVOR^^}" not in _code,
      "the script uses no bash-only uppercase expansion",
      "it is invoked as ./scripts/build_android_apks.sh so the shebang picks the "
      "shell, and ${VAR^} is not POSIX -- under sh it is 'bad substitution'")
for flavor, task in (("full", "assembleFullRelease"),
                     ("lite", "assembleLiteRelease"),
                     ("both", "assembleFullRelease assembleLiteRelease")):
    check(task in script,
          f"building {flavor!r} names {task!r} explicitly",
          "each flavor must map to its own Gradle task, or one branch goes untested")

# Three bugs that made the app look alive while being inert. Found by an
# independent read of the code, then confirmed here by grep before fixing.
# Each one compiled, installed, launched, and rendered a screenshot -- and was
# still broken. None would ever have failed CI.
print("\n  the app is not inert (all three compiled, installed, and rendered)")
player = read(os.path.join(ANDROID_SRC, "main", "java", "com", "giathinh",
                          "hashplay", "PlayerScreen.kt"))

# 1. PlayerController.tick() existed but NOTHING called it. It is what advances
#    the seek bar and feeds the spectrum, so the slider sat at 0:00 and the
#    visualizer never moved even while audio played.
tick_def = [l for l in open(os.path.join(ANDROID_SRC, "main", "java", "com",
                                         "giathinh", "hashplay",
                                         "PlayerController.kt")).read()
            .splitlines() if "fun tick(" in l]
check(len(tick_def) == 1, "PlayerController declares tick()")
check("controller.tick(" in player,
      "PlayerScreen CALLS tick() every frame",
      "a tick() with no caller means a frozen slider and a frozen visualizer")
check("withFrameNanos" in player,
      "the frame clock is driven by the composition",
      "LaunchedEffect + withFrameNanos starts and stops with the screen")

# 2. The manifest declared the audio permissions, but nothing ever requested
#    them at runtime. Declaring makes the app installable; the user still has to
#    grant. Until then the library scan returns nothing and playback fails.
manifest = read(os.path.join(REPO, "android", "app", "src", "main",
                             "AndroidManifest.xml"))
check("READ_MEDIA_AUDIO" in manifest, "the manifest declares READ_MEDIA_AUDIO")
check("RequestMultiplePermissions" in player or
      "requestPermissions" in player,
      "the app ASKS for that permission at runtime",
      "a manifest entry alone is not a grant; without this the library is "
      "always empty on a fresh install")
check(read(os.path.join(ANDROID_SRC, "main", "java", "com", "giathinh",
                        "hashplay", "Permissions.kt")).count("READ_MEDIA_AUDIO") >= 1,
      "Permissions.kt picks the right read permission per API level",
      "READ_MEDIA_AUDIO only exists on API 33+; below that it is "
      "READ_EXTERNAL_STORAGE")

# 3. play() used Uri.fromFile(), which is both unreadable under scoped storage
#    and fatal when handed to another app (FileUriExposedException on 7+).
#    Track.id was already populated by LibraryScanner and never used.
ctrl = read(os.path.join(ANDROID_SRC, "main", "java", "com", "giathinh",
                         "hashplay", "PlayerController.kt"))
scanner = read(os.path.join(ANDROID_SRC, "main", "java", "com", "giathinh",
                            "hashplay", "LibraryScanner.kt"))
check("MediaStore.Audio.Media.EXTERNAL_CONTENT_URI" in scanner,
      "play() builds a content:// URI from the MediaStore id",
      "Uri.fromFile is not readable under scoped storage and throws when "
      "passed across a process boundary")
check("ContentUris.withAppendedId" in scanner,
      "the content URI is built with ContentUris",
      "the URI logic moved onto Track.uri() so the playlist can map over "
      "every item; assert it there, not in PlayerController")

# There must be exactly ONE player. Previously PlaybackService built its own
# ExoPlayer while PlayerController built a second, so playback died with the
# activity, there was no media notification, and hardware media keys had
# nothing to bind to -- four desktop features missing from one bug.
print("\n  exactly one player, owned by the service")
svc = read(os.path.join(ANDROID_SRC, "main", "java", "com", "giathinh",
                        "hashplay", "PlaybackService.kt"))
check(svc.count("ExoPlayer.Builder(") == 1,
      "PlaybackService builds the only ExoPlayer")
check("MediaSession.Builder(" in svc,
      "that player is published in a MediaSession",
      "without a session the lock screen, notification shade, Bluetooth, the "
      "watch and Assistant all have nothing to control")
check("setHandleAudioBecomingNoisy" in svc,
      "playback pauses when headphones are unplugged")
check("setAudioAttributes" in svc and "handleAudioFocus" not in svc.split(
      "setAudioAttributes")[1][:200],
      "audio attributes are declared")
check("MediaController" in ctrl and "SessionToken" in ctrl,
      "the UI connects to the service instead of owning a player",
      "two players means the notification and the screen show different things")
check(ctrl.count("ExoPlayer.Builder(") == 0,
      "PlayerController builds NO player of its own",
      "a second ExoPlayer here is the original bug")
check("MediaController.Builder" in ctrl,
      "PlayerController binds with MediaController.Builder")
check("pendingPlay" in ctrl,
      "a play pressed before the session binds is not lost")
check("c?.release()" in ctrl and "player.release()" not in ctrl.split(
      "fun release()")[1][:200],
      "release() detaches without releasing the service's player",
      "releasing here would kill playback for the notification too")

# Setting one track at a time meant NOTHING advanced when it ended: the player
# held the last sample and the UI still said NOW PLAYING. The desktop fixed the
# identical bug at ncs_launcher.py:1980-2013.
print("\n  playback advances like the desktop (ncs_launcher.py:1980-2013)")
check("setMediaItems(" in ctrl and "setMediaItem(" not in ctrl,
      "play() queues the WHOLE library, not one track",
      "a single-item queue cannot advance: the track ends and nothing happens")
check("fun setPlaylist(" in ctrl,
      "the queue is set from the visible track list")
check("setPlaylist(tracks)" in player,
      "the UI keeps the player's queue equal to the list on screen")
check("indexOfFirst" in ctrl,
      "the tapped track is located inside that queue")
check("skipBroken" in ctrl and "onPlayerError" in ctrl,
      "a track that fails to decode is stepped past",
      "ExoPlayer does NOT advance after a playback error, so one bad file "
      "would otherwise stop playback permanently")
check("seekToNextMediaItem" in ctrl,
      "skipping broken tracks walks the playlist")

# The desktop gates on setup_wizard.py's needs_setup() (:224-234): once setup is
# marked complete it never nags again, even if the user walked away. Android had
# no first run at all -- it went straight to the player.
print("\n  first run exists and never nags (setup_wizard.py:224-234)")
setup = read(os.path.join(ANDROID_SRC, "main", "java", "com", "giathinh",
                         "hashplay", "SetupState.kt"))
main_act = read(os.path.join(ANDROID_SRC, "main", "java", "com", "giathinh",
                             "hashplay", "MainActivity.kt"))
check("fun needsSetup()" in setup,
      "there is a first-run gate at all")
check("setup_complete" in setup,
      "completion is recorded under the desktop's own key name")
check("not bool(self.cfg.get(\"setup_complete\"))" not in setup and
      "!prefs.getBoolean(KEY_COMPLETE, false)" in setup,
      "setup is needed only while setup_complete is false")
check("fun complete()" in setup,
      "completion can be recorded")
check("SetupScreen" in main_act and "needsSetup()" in main_act,
      "MainActivity shows SetupScreen on first run")
# Escape is an answer: SetupScreen must complete() on BOTH paths, not just the
# grant path, or a user who backs out gets nagged on every launch.
setup_screen = read(os.path.join(ANDROID_SRC, "main", "java", "com",
                                 "giathinh", "hashplay", "SetupScreen.kt"))
check(setup_screen.count("setup.complete()") >= 2,
      "both continuing and skipping mark setup complete",
      "if only the grant path completes, every launch nags a user who backed "
      "out -- exactly what setup_wizard.py:229-231 refuses to do")
check("Not now" in setup_screen,
      "there is a visible way out that is not a grant")

# ncs_video.py is 757 desktop lines and most of it CANNOT port. Only the local
# video background is honest to bring across; the rest would be shipping a
# YouTube downloader inside a music player.
print("\n  video backgrounds: portable subset only (ncs_video.py)")
vid = read(os.path.join(ANDROID_SRC, "main", "java", "com", "giathinh",
                        "hashplay", "VideoBackground.kt"))
gradle = read(os.path.join(REPO, "android", "app", "build.gradle.kts"))
check("media3-ui" in gradle,
      "media3-ui is a dependency (PlayerView needs it, and exoplayer does "
      "not pull it in)")
# The flavor split is what stops lite CALLING video. It does NOT keep the
# library out of lite's dex -- R8 retains manifest-declared View subclasses
# regardless. Measured: lite classes.dex 2.54 MB with media3-ui present.
vid_full = read(os.path.join(REPO, "android", "app", "src", "full", "java",
                             "com", "giathinh", "hashplay",
                             "VideoBackground.kt"))
vid_lite = read(os.path.join(REPO, "android", "app", "src", "lite", "java",
                             "com", "giathinh", "hashplay",
                             "VideoBackground.kt"))
check("PlayerView" in vid_full,
      "the full flavor has the real video implementation")
check("PlayerView" not in vid_lite,
      "the lite flavor does not",
      "lite's contract is low resource use; it must not instantiate a player "
      "view behind the ball")
check("fullImplementation" not in gradle,
      "no unsupported fullImplementation dependency is used",
      "it does not resolve in this build script -- the dependencies block has "
      "no per-flavor configuration, and adding it fails the build")
for ext in ["mp4", "mkv", "webm", "avi", "mov", "m4v", "wmv", "flv", "3gp"]:
    check(f'"{ext}"' in vid,
          f"the desktop's .{ext} extension is recognised",
          "the extension list lives in the shared VideoSupport object in "
          "src/main, not in the per-flavor implementation")
check("REPEAT_MODE_ONE" in vid_full,
      "the background loops, as the desktop's does")
check("volume = 0f" in vid_full,
      "the background is MUTED",
      "the player owns the audio focus; a second unmuted stream fights it")
check("onDispose" in vid_full and ".release()" in vid_full,
      "the video player is released with the composable",
      "an ExoPlayer left running holds a decoder and a wake lock after the "
      "screen it was behind is gone")
check("onPlayerError" in vid_full,
      "an undecodable video is dropped rather than shown as a black rectangle")
# The parts deliberately NOT ported. These check for CODE, not for a mention:
# an earlier version of these two greps matched the file's own explanatory
# comment and failed the build for documenting what it was leaving out.
check(not re.search(r"""["']yt[-_]?dlp["']""", vid),
      "no yt-dlp source resolver was ported",
      "resolve_source() (ncs_video.py:93-123) pulls over the network; that is "
      "a different app with different legal exposure")
check("fun readStrm" not in vid and ".strm\"" not in vid,
      "no .strm stream support was ported",
      ".strm points at a remote URL (ncs_video.py:76); same objection")
check("UNSUPPORTED_NOTE" in vid,
      "the limit is stated rather than silently failing on some files")

# Found by static review of code that had never run on a device. DATA is
# deprecated in API 29 and the provider may omit it, which turns
# getColumnIndexOrThrow into an uncaught IllegalArgumentException on the IO
# dispatcher -- a crash on launch rather than an empty library.
print("\n  the library scan cannot crash the app")
check("MediaStore.Audio.Media.DATA" not in scanner,
      "DATA is not projected",
      "deprecated in API 29; the provider may omit it and "
      "getColumnIndexOrThrow then throws from an unguarded IO coroutine")
# Strip comments first. Two earlier checks in this file were satisfied by the
# very text explaining them, which is a trap worth encoding once here.
code = "\n".join(l.split("//")[0] for l in scanner.splitlines())
check("getColumnIndexOrThrow" not in code,
      "no getColumnIndexOrThrow in the scan's code",
      "one column the provider declines to return should cost one field, "
      "not the entire library. Comments are stripped first: an earlier "
      "version of this check matched the comment that explains the rule.")
check("getColumnIndex(" in code,
      "columns are looked up leniently")
check("SecurityException" in scanner,
      "a revoked permission yields an empty library, not a crash")
check("if (iId < 0) return" in scanner,
      "a missing _ID bails out cleanly",
      "_ID is the only column the scan cannot proceed without")
check("catch (t: Exception)" in player,
      "refresh() guards the scan it launches",
      "an uncaught throw on Dispatchers.IO takes the whole process down")

print("\n  lite refuses loudly rather than appearing to succeed")
# The stub's message must reach a Text, not merely sit in a StateFlow.
check("tMessage" in player, "PlayerScreen collects the torrent message")
check(re.search(r"tMessage\?\.let", player) is not None,
      "PlayerScreen RENDERS the torrent message",
      "a message that is set but never drawn is a silent no-op")
check("startsWith" in player,
      "the message is consumed (a completion check refreshes the library)")
# The stub must actually publish something, not just return false.
check(re.search(r"_messages\.value\s*=", lite_stub) is not None,
      "the lite stub publishes a message on start()")
check(re.search(r"fun start\([^)]*\)\s*:\s*Boolean", lite_stub) is not None,
      "the lite stub returns a Boolean like the real one")
# Known gap, recorded rather than glossed over: the sheet closes
# unconditionally after start(), so on lite the refusal only becomes visible
# after the user comes back to the screen. Not fixed here.

print("\n  R8 cannot strip what only the manifest references")
check("-keep class com.giathinh.hashplay.PlaybackService" in read(PROGUARD),
      "PlaybackService is kept (it is manifest-only, so R8 cannot see it)")
check("androidx.media3" in read(PROGUARD), "Media3 is kept")

print("\n  an unsigned release APK is refused, not shipped")
check("HASHPLAY_HAVE_SIGNING" in gradle, "signing is conditional on real key material")
check("isMinifyEnabled = true" in gradle, "R8 is on for release")

print("\n  CI builds and ships both")
check("name: Android (APK)" in workflow, "there is an android CI job")
check("build_android_apks.sh both" in workflow, "CI builds both flavors")
# android-actions/setup-android@v3 is deliberately NOT used: on the current
# runner image it runs `sdkmanager tools`, which no longer exists, and fails
# before any of this repo's steps. Two rc.5 runs died there identically.
_steps = workflow[workflow.find("  android:"):workflow.find("\n  release:")]
check("uses: android-actions" not in _steps,
      "CI does not use the setup-android action (it runs 'sdkmanager tools')")
check("sdkmanager" in workflow, "CI installs the SDK packages with sdkmanager")
check("platforms;android-34" in workflow and "build-tools;34.0.0" in workflow,
      "CI installs the exact platform and build-tools the app compiles against")
check("secrets.HASHPLAY_KEYSTORE_B64" in workflow, "CI gets the key from a secret")
check("jniLibs" in workflow or "libtorrent4j" in workflow,
      "CI asserts the lite APK really lost the engine")
check("needs: [macos, linux, windows, android, asset-names]" in workflow,
      "the release job waits for the APKs")
check("HashPlay-lite-android-arm64.apk" in workflow,
      "the lite APK is renamed to match the desktop naming before upload")
check("HashPlay-full-android-arm64.apk" in workflow,
      "the full APK is renamed too")
# The publish step must not narrow its globs. It did once and dropped
# HashPlay.app.zip, the main macOS download, with no error anywhere.
publish = workflow[workflow.find("name: Assemble the payload"):
                   workflow.find("name: Checksums")]
check("'*.zip'" in publish,
      "the publish step still matches zips broadly (it once dropped HashPlay.app.zip)")

print("\n  the verifier does not lie about signing")
bs = read(BUILD_SCRIPT)
check("apksigner" in bs,
      "signing is checked with apksigner, not by listing META-INF (v2/v3 signing "
      "stores no .RSA file, so a zip listing reports valid APKs as unsigned)")

print("\n" + ("ANDROID BUILD TESTS PASSED" if not FAILS
             else "%d FAILURES: %s" % (len(FAILS), FAILS)))
sys.exit(1 if FAILS else 0)
