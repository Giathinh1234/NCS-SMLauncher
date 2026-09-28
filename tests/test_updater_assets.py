"""The self-updater, against the asset names the project actually publishes.

Three defects here meant the in-app updater -- a shipped v1.0.0 headline
feature -- could not have worked, end to end, for any release ever made:

  1. It could not see our own macOS asset. The v1.0.1 release publishes
     `HashPlay-macos-arm64`, but the filter accepted only `.app.zip` or
     exactly `HashPlay`, so every lookup returned None.

  2. fetch_latest returned on the FIRST newer release, even if that release
     had nothing installable for this platform. A notes-only v9.9.9 ahead of
     a usable v1.5.0 made the updater report "no update".

  3. The swap script relaunched with `nohup "$TARGET"`, where TARGET is
     HashPlay.app -- a DIRECTORY. That is rc=126, Permission denied: you
     quit, the swap happens, and the app never comes back.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = "/Users/giathinh/ncs-music-launcher"
sys.path.insert(0, os.path.join(ROOT, "src"))
import updater                                        # noqa: E402

fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


print("SELF-UPDATER")

# ------------------------------------------------------- 1. asset recognition
print("1) the names actually published on a real release")
# Taken from `gh release view v1.0.1 --json assets`, not invented.
published = ["HashPlay-macos-arm64", "HashPlay-android-arm64.apk",
             "SHA256SUMS.txt"]
check("the v1.0.1 macOS asset is recognised on darwin",
      updater._asset_matches_platform("HashPlay-macos-arm64", "darwin") is True)
check("the APK is NOT taken on darwin",
      updater._asset_matches_platform("HashPlay-android-arm64.apk", "darwin")
      is False)
check("the checksum file is not mistaken for an artifact",
      updater._asset_matches_platform("SHA256SUMS.txt", "darwin") is False)

print("   and the alternates the build scripts produce")
for name, plat, want in (
        ("HashPlay.app.zip", "darwin", True),
        ("HashPlay", "darwin", True),
        ("HashPlay-macos-arm64.zip", "darwin", True),
        ("HashPlay-darwin-arm64.zip", "darwin", True),
        ("HashPlay-linux-x86_64.tar.gz", "linux", True),
        ("HashPlay-linux-arm64", "linux", True),
        ("HashPlay-macos-arm64", "linux", False),
        ("SHA256SUMS.txt", "linux", False),
        ("", "darwin", False),
        ("SomeOtherApp-macos", "darwin", False),
        # Android owns its own update path, so it is not a self-update target.
        ("HashPlay-android-arm64.apk", "android", False)):
    got = updater._asset_matches_platform(name, plat)
    check(f"{plat:8} {name or '(empty)':28} -> {want}", got is want, got)


# --------------------------------------------------------- 2. scanning past it
print("2) a newer release with no usable asset must not end the scan")


def rel(tag, assets):
    return {"tag_name": tag, "prerelease": False, "draft": False,
            "published_at": "2026-01-01T00:00:00Z",
            "assets": [{"name": n, "browser_download_url": "http://x/" + n,
                        "size": 10} for n in assets]}


def with_payload(payload):
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()
    # An INSTANCE. Returning the bare class made `with urlopen(...) as resp`
    # raise TypeError, which fetch_latest's broad `except Exception` swallowed
    # into a None -- so the test was really asserting against a swallowed
    # error, and reported the fix as broken when it was working.
    return R()


real_urlopen = updater.urllib.request.urlopen
try:
    payload = [rel("v9.9.9", ["notes.txt"]),
               rel("v1.5.0", ["HashPlay-macos-arm64"])]
    updater.urllib.request.urlopen = lambda *a, **k: with_payload(payload)
    got = updater.fetch_latest(current="1.0.0")
    # Work out which step gave up, so a failure is diagnosable rather than
    # just "None".
    why = [f"platform={sys.platform}"]
    for r_ in payload:
        info = updater.parse_release(r_, "1.0.0")
        why.append(f"{r_['tag_name']}:info={None if info is None else info.version}")
        if info is not None:
            why.append(f"  pick={updater.pick_asset(info)}")
            why.append(f"  assets={[a.get('name') for a in (info.assets or [])]}")
    check("it scans past the notes-only v9.9.9 and finds v1.5.0",
          got is not None and got.version == "1.5.0",
          f"got={got} | " + " ".join(why))

    # A genuinely empty scan is still "no update".
    updater.urllib.request.urlopen = lambda *a, **k: with_payload(
        [rel("v9.9.9", ["notes.txt"])])
    got = updater.fetch_latest(current="1.0.0")
    check("with nothing installable anywhere, it reports no update",
          got is None, got)

    # An older-than-current release is not an update.
    updater.urllib.request.urlopen = lambda *a, **k: with_payload(
        [rel("v0.1.0", ["HashPlay-macos-arm64"])])
    got = updater.fetch_latest(current="1.0.0")
    check("an older release is correctly not an update", got is None, got)
finally:
    updater.urllib.request.urlopen = real_urlopen


# --------------------------------------------------------- 3. the relaunch
print("3) the swap script must be able to relaunch a .app")
# Returns the script as a STRING, not a list of lines. Joining it iterated
# one character at a time, so "nohup" was trivially absent and the whole
# section was testing nothing.
script = updater._swap_script("/tmp/staged", "/tmp/HashPlay.app", 123)
# Match the command, not the word: the explanatory comment above it
# legitimately says "not nohup/exec".
check("it does not nohup-exec the bundle directory",
      'nohup "$TARGET"' not in script,
      [l for l in script.splitlines() if "nohup" in l])
check("it uses `open`, which is what the Finder would do",
      'open "$TARGET"' in script)

# Prove the claim rather than trusting it: exec'ing a directory really fails,
# and `open` really does not.
with tempfile.TemporaryDirectory() as d:
    bundle = os.path.join(d, "HashPlay.app")
    os.makedirs(bundle)
    rc = subprocess.run(["/bin/sh", "-c", f'nohup "{bundle}"'],
                        capture_output=True, text=True)
    check("executing the .app directory fails, as the audit reported",
          rc.returncode != 0, f"rc={rc.returncode} {rc.stderr.strip()[:80]}")
    which = subprocess.run(["/bin/sh", "-c", "command -v open"],
                           capture_output=True, text=True)
    check("`open` exists on this platform, so the replacement is valid",
          which.returncode == 0 and which.stdout.strip().endswith("/open"),
          which.stdout.strip())

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("SELF-UPDATER TESTS PASSED")
