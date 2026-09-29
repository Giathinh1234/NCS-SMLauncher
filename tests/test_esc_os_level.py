"""Press ESC on the real app, for real, at the OS level.

Why this file exists
--------------------
The ESC behaviour has a documented promise -- at rest, two presses quit --
and it had a real bug: the torrent panel consumed the first press even when
no panel was on screen, so it took three. That was caught by extracted
branch tests. Extracted branch tests are not a keyboard.

This drives the actual shipped binary with actual CGEvents and watches the
actual process exit. Nothing is stubbed.

A note on what counts as proof
------------------------------
CGEventPost raising no exception proves nothing. Without Accessibility
permission the HID event tap accepts a post and silently discards it, so
"the call worked" and "the key arrived" are different claims. The only
honest test is the one that checks whether the process died.

Because of that, every case here is expected to FAIL LOUDLY. If the events
are being dropped, this reports that plainly instead of quietly passing.
"""
import os
import subprocess
import sys
import time

import Quartz

try:
    import AppKit
except ImportError:  # pragma: no cover - AppKit ships with the pyobjc set
    AppKit = None

# 0x35 = Escape, 0x0C = q  (macOS virtual keycodes)
VK_ESC = 0x35
VK_Q = 0x0C

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The .app BUNDLE, not dist/HashPlay. The bare onefile executable is what
# PyInstaller emits, but it is not the shipped artifact and it is not focusable:
# with no bundle identifier the window server has no application to activate,
# so CGEventPost events have nowhere to land. Testing it produced a window
# count of 0 and a focus() that returned None, which made the first ESC
# "pass" for the wrong reason -- the app was simply not listening.
APP_BUNDLE = os.path.join(REPO, "dist", "HashPlay.app")
APP = os.path.join(APP_BUNDLE, "Contents", "MacOS", "HashPlay")

# How to start the app. "source" runs src/ncs_launcher.py, which executes the
# same main() and the same event loop as the frozen binary but as a normal
# process that the window server will focus. "bundle" launches the shipped
# .app through `open`, so Launch Services registers it properly.
MODE = os.environ.get("HASHPLAY_ESC_TARGET", "source")
USE_OPEN = MODE == "bundle"
ENV = dict(os.environ)
# A real window, real audio, no headless shortcuts. This is the whole point.
ENV["SDL_VIDEODRIVER"] = "cocoa"
# There is no "skip the wizard" env var, and pretending there was sent ESC
# into a first-run profile, where the FIRST press is consumed by the wizard's
# _skip(). That made two presses look insufficient when three is the correct
# answer from a fresh profile. So drive it to a real library instead.
ENV["HASHPLAY_API_PORT"] = "8791"
ENV.pop("SDL_AUDIODRIVER", None)
ENV.pop("SDL_VIDEODRIVER_DUMMY", None)


def log(msg):
    print("   " + msg, flush=True)


def press(keycode, times=1, gap=0.35):
    """Press and release a key `times` times, at the OS event layer."""
    for _ in range(times):
        down = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.05)
        up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(gap)


def _named_windows(pid):
    """The app's own on-screen window titles, via the window server."""
    try:
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
    except Exception:
        return []
    return [w for w in wins
            if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowName")
            and w.get("kCGWindowBounds", {}).get("Width", 0) > 200]


def focus(pid):
    """Make the app frontmost, and confirm it actually got there.

    activateWithOptions_IgnoringOtherApps is asynchronous and this process
    has no GUI session of its own, so a single call routinely returns before
    the window server has switched. Retrying until both isActive() and a
    real on-screen window agree is the only way to know the keys will land --
    a False here means the next test result would be meaningless.
    """
    app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
        pid)
    if app is None:
        log(f"focus pid {pid}: no NSApplication object yet")
        return False
    flags = (AppKit.NSApplicationActivateIgnoringOtherApps
             | AppKit.NSApplicationActivateAllWindows)
    for attempt in range(1, 13):
        app.activateWithOptions_(flags)
        time.sleep(0.75)
        if app.isActive() and _named_windows(pid):
            log(f"focus pid {pid}: active on attempt {attempt}, window "
                f"{_named_windows(pid)[0]['kCGWindowName']!r}")
            return True
    log(f"focus pid {pid}: gave up after 12 attempts "
        f"(isActive={app.isActive()}, windows={len(_named_windows(pid))})")
    return False


def ensure_real_profile():
    """Leave the app in the state a returning user is actually in.

    A first-run profile opens the modal setup wizard, and the wizard eats the
    first ESC to mean "skip". That is correct behaviour, not the bug this
    file exists to catch -- but testing on a fresh profile measures the
    wizard, not the quit ladder. So point the config at a folder that has
    music in it, so needs_setup() is False and ESC is ESC again.

    The path comes from the config module. Hardcoding config.json made this
    return None in silence -- the real file is settings.json -- and the run
    then measured the wizard and reported ESC as broken.
    """
    import glob
    import json
    import os as _os
    import sys as _sys
    _sys.path.insert(0, os.path.join(REPO, "src"))
    import config as _config
    cfg_path = _config.CONFIG_PATH
    log(f"config path: {cfg_path}")
    if not _os.path.exists(cfg_path):
        log("!! no config on disk -- the wizard will be open and this test "
            "cannot measure the quit ladder")
        return None
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    home = _os.path.expanduser("~")
    cands = []
    for pat in ("Music/*.mp3", "Music/*.wav", "Music/*.flac",
                "Music/*.m4a", "Music/*.ogg", "Downloads/*.mp3"):
        cands.extend(glob.glob(_os.path.join(home, pat)))
    if not cands:
        log("!! no audio under ~/Music or ~/Downloads -- cannot set up a "
            "returning-user profile")
        return None
    folder = _os.path.dirname(cands[0])
    cfg["library_folder"] = folder
    cfg["setup_complete"] = True
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    log(f"profile: library_folder={folder} ({len(cands)} files), "
        "setup_complete=True -> wizard closed")
    return folder


def launch():
    if USE_OPEN and not os.path.isdir(APP_BUNDLE):
        sys.exit(f"no app bundle at {APP_BUNDLE}; run ./build_macos_app.sh first")
    if MODE == "source":
        cmd = [sys.executable, "-u", os.path.join(REPO, "src", "ncs_launcher.py")]
        log(f"target: SOURCE app ({' '.join(cmd[1:])}) -- same main() as the binary")
    else:
        cmd = [APP]
        log("target: shipped .app bundle via `open` (Launch Services)")

    if USE_OPEN:
        subprocess.run(["open", "-a", APP_BUNDLE], env=ENV, check=True)
        # `open` returns immediately; find the pid it actually started.
        # `open` starts a onefile launcher that FORKS the real app: a stub of
        # ~1.8 MB at 0% CPU, and a child of ~200 MB doing the work. Focusing
        # the stub returns isActive=False and the keys go nowhere, which looks
        # exactly like broken ESC.
        #
        # RSS is the reliable discriminator, not NSWorkspace: mid-launch the
        # stub is still the only process NSWorkspace knows about, so asking it
        # returns the stub and the run fails on a healthy app. Wait until a
        # process clears a size threshold that only the real app reaches.
        #
        # And it must be allowed to FINISH loading. Scanning for "a process
        # over 60 MB" caught the real child at 76 MB while it was still
        # indexing a 230 MB library: no NSApplication object yet, so focus
        # returned None and the keys went nowhere. Wait for the RSS to stop
        # growing and settle, which is when the window actually exists.
        deadline = time.time() + 60
        real, last, stable = None, -1, 0
        while time.time() < deadline:
            pids = [int(x) for x in subprocess.run(
                ["pgrep", "-f", "HashPlay"],
                capture_output=True, text=True).stdout.split()
                if _pid_alive(int(x))]
            big = [p for p in pids if _rss(p) > 40_000]
            if big:
                real = max(big, key=_rss)
                rss = _rss(real)
                stable = stable + 1 if abs(rss - last) < 2_000 else 0
                last = rss
                if stable >= 3:
                    log(f"real app pid {real}, rss settled at {rss} KB "
                        "(stub is ~1.8 MB)")
                    time.sleep(3)
                    if _pid_alive(real):
                        return _reap_open_pid(real)
                    real = None
            time.sleep(1)
        sys.exit("the .app never appeared in the process table")
    return subprocess.Popen(cmd, env=ENV, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _reap_open_pid(bundle_pid):
    """`open` starts a launcher that forks the real app. Wrap the child."""
    class Child:
        def __init__(self, pid):
            self.pid = pid

        def poll(self):
            return None if _pid_alive(self.pid) else 0

        def kill(self):
            try:
                os.kill(self.pid, 9)
            except OSError:
                pass

        def wait(self, timeout=None):
            time.sleep(0.2)
    return Child(bundle_pid)


def _rss(pid):
    """Resident size in KB, for telling the onefile stub from the real app."""
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True).stdout.strip()
        return int(out or 0)
    except (ValueError, OSError):
        return 0


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def alive(p):
    return p.poll() is None


def case_escape_quits_at_rest():
    """The documented promise: two ESC presses at rest quit."""
    print("\n  ESC at rest -- expect QUIT on the 2nd press")
    p = launch()
    time.sleep(7)
    if not alive(p):
        sys.exit("FAIL: the app exited before any key was pressed")
    try:
        if not focus(p.pid):
            # No focus means no key delivery, which means the presses below
            # prove nothing. Reporting this as a pass is how the first run of
            # this file "passed" the first ESC on an app that was never
            # listening. It is neither a pass nor a failure: it is
            # inconclusive, and it must say so.
            log("SKIP: could not focus the app, so no key can be delivered")
            return None
        time.sleep(1.0)

        press(VK_ESC, times=1, gap=0.6)
        time.sleep(0.6)
        if not alive(p):
            log("FAIL: quit on ONE press. ESC must arm first, not quit.")
            return False
        log("ok: 1 press did not quit (it armed)")

        press(VK_ESC, times=1, gap=0.0)
        deadline = time.time() + 12
        while alive(p) and time.time() < deadline:
            time.sleep(0.25)
        if alive(p):
            log("FAIL: still running after 2 presses.")
            return False
        log("ok: quit on the 2nd press, as documented")
        return True
    finally:
        if alive(p):
            p.kill()
        p.wait(timeout=15)


def case_q_quits_immediately():
    print("\n  Q at rest -- expect QUIT on the 1st press, no arming")
    p = launch()
    time.sleep(7)
    try:
        if not focus(p.pid):
            log("SKIP: could not focus the app, so no key can be delivered")
            return None
        time.sleep(1.0)
        press(VK_Q, times=1, gap=0.0)
        deadline = time.time() + 12
        while alive(p) and time.time() < deadline:
            time.sleep(0.25)
        if alive(p):
            log("FAIL: Q did not quit.")
            return False
        log("ok: Q quit immediately")
        return True
    finally:
        if alive(p):
            p.kill()
        p.wait(timeout=15)


def _reap_all():
    """Make sure no HashPlay is left running between cases."""
    out = subprocess.run(["pkill", "-f", "HashPlay"],
                         capture_output=True, text=True).stdout
    time.sleep(1.5)
    left = [x for x in subprocess.run(["pgrep", "-f", "HashPlay"],
                                      capture_output=True,
                                      text=True).stdout.split()
            if _pid_alive(int(x))]
    if left:
        for pid in left:
            try:
                os.kill(int(pid), 9)
            except OSError:
                pass
        time.sleep(1.0)
    log(f"reaped any leftover app processes (was {len(left)})")


def main():
    print("=" * 68)
    print("OS-LEVEL ESC CHECK -- real app, real CGEvents, real process exit")
    print("=" * 68)
    ensure_real_profile()
    if Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateHIDSystemState, VK_ESC):
        log("warning: ESC reads as held down already")

    results = [case_escape_quits_at_rest()]  # True / False / None=inconclusive
    # Between cases, make sure the previous app is really gone. Back-to-back
    # `open` calls otherwise raced: the second launch happened while the first
    # bundle's onefile stub was still tearing down, and the lookup found
    # nothing. That looked like a broken Q key; it was a slow quit.
    _reap_all()
    time.sleep(2)
    results.append(case_q_quits_immediately())
    passed = sum(1 for r in results if r is True)
    skipped = sum(1 for r in results if r is None)

    print("\n" + "=" * 68)
    if skipped:
        print(f"{skipped} case(s) INCONCLUSIVE -- the app could not be brought "
              "\nto the front, so the keys never reached it. That is a limit of "
              "\nthis harness in a non-GUI session, not a result about ESC.")
    if passed == len(results):
        print(f"ALL {passed} OS-LEVEL CHECKS PASSED")
    else:
        print(f"{passed}/{len(results)} OS-LEVEL CHECKS PASSED -- the rest are"
              "\nreal failures, not flakes. Read which case failed above: if"
              "\nthe app never saw the key, the events are being dropped and"
              "\nthe branch tests remain the only evidence.")
    print("=" * 68)
    if skipped and not passed:
        return 2          # inconclusive: nothing was actually measured
    return 0 if passed == len(results) else 1


# Run it by hand, not from the suite:
#     HASHPLAY_ESC_TARGET=source python3 tests/test_esc_os_level.py
#
# Deliberately excluded from the normal sweep. It takes ~30s, it steals
# keyboard focus from whatever the user is doing, and it needs a real Aqua
# session -- none of which belong in an automated run. Exit codes:
#     0  every case passed
#     1  a case ran and failed (a real finding)
#     2  inconclusive -- the app could not be focused, so nothing was measured
if __name__ == "__main__":
    sys.exit(main())
