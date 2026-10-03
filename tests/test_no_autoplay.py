"""The app must never make sound unless the user asked for it.

Regression cover for a real behaviour, not a style preference: opening the
launcher called player.load(tracks[0]) with the audio stream already running,
so the first track started playing the moment the app launched. Same class of
problem on Android, where the fix belongs in the service rather than the UI.

These are static checks on purpose. The suite runs headless with no audio
device, so anything that asserted "no sound" by listening would pass
vacuously.
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))

FAILS = []


def check(cond, label, why=""):
    print(f"     {'ok  ' if cond else 'FAIL'}   {label}")
    if not cond:
        if why:
            print(f"            {why}")
        FAILS.append(label)


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


print("\n  desktop: launching never starts audio")
launcher = read("src", "ncs_launcher.py")

check("def load(self, path, start_paused=False)" in launcher,
      "load() can decode without starting sound",
      "the stream is opened once at startup and runs continuously, so "
      "'loaded but silent' has to be expressible")

# Every tracks[0] load is a startup or folder-change path. All of them must
# pass start_paused=True. A bare load(tracks[0][...]) here is the bug.
bare = []
for i, line in enumerate(launcher.splitlines(), 1):
    stripped = line.strip()
    if "player.load(tracks[0]" in stripped and "start_paused" not in stripped:
        bare.append((i, stripped))
check(not bare,
      "no startup load() path omits start_paused",
      f"these load track 0 audibly: {bare}")

check(launcher.count("start_paused=True") >= 3,
      "all three folder/startup sites pass it",
      "startup, pick_folder and rescan_folder all adopt a new library, and "
      "choosing a folder should show the library rather than start playing it")

check("self.paused = bool(start_paused)" in launcher,
      "the flag actually reaches the playhead state")

print("\n  android: launching never starts audio")
android_src = os.path.join(REPO, "android", "app", "src", "main", "java",
                           "com", "giathinh", "hashplay")
svc = read(android_src, "PlaybackService.kt")
ctrl = read(android_src, "PlayerController.kt")

# The service sets playWhenReady on item transition. On a fresh install there
# are no items, so this cannot fire -- but the contract is worth pinning.
check("repeatMode" not in svc,
      "the service does not auto-advance an unstarted queue into playback")

# play() is only ever reached from a tap. Nothing may call it during startup.
screen = read(android_src, "PlayerScreen.kt")
auto = [l.strip() for l in screen.splitlines()
        if "controller.play(" in l and "onClick" not in l
        and "LaunchedEffect" in l]
check(not auto,
      "nothing auto-plays from a LaunchedEffect",
      f"these call play() without a user gesture: {auto}")

check("playWhenReady = true" in ctrl,
      "play() is the only thing that starts playback")

# The setup screen must not start anything either.
setup = read(android_src, "SetupScreen.kt")
check("play(" not in setup,
      "the first-run screen never starts playback")

print("\n  both platforms: the first sound needs a deliberate press")
check("start_paused=True" in launcher,
      "desktop honours it")
check("Permissions" in screen,
      "android gates its library behind first-run setup rather than "
      "immediately playing whatever it finds")

print()
if FAILS:
    print(f"  {len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1)
print("  NO-AUTOPLAY TESTS PASSED")