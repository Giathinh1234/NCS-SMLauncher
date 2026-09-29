"""The update check, driven through the real running app.

The updater module shipped in the bundle but nothing in the launcher ever
called it: no import, no dispatch entry, no key. So the in-app updater was
not a feature, it was dead code with tests. This drives `update_check`
through the real loopback API against the real GitHub releases API.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = "/Users/giathinh/ncs-music-launcher"
PORT = int(os.environ.get("HASHPLAY_TEST_PORT", "8797"))

fails = []

# Read VERSION rather than hardcoding it. A literal version in this file made
# it fail on every version bump for no reason -- the point is that the app
# reports whatever it was built as.
with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as _fh:
    BUILT_VERSION = _fh.read().strip()


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


def call(action, params=None, token=None):
    """The API is per-action paths, not one /command with an action field."""
    body = json.dumps(dict(params or {})).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/{action}", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise AssertionError(
            f"{action} -> HTTP {e.code}: {body}") from None


tmp = tempfile.mkdtemp(prefix="hp_upd_")
env = dict(os.environ, HASHPLAY_CONFIG_DIR=tmp, HASHPLAY_API_PORT=str(PORT))
proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "src", "ncs_launcher.py")],
                        env=env, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
try:
    token = None
    for _ in range(80):
        time.sleep(0.5)
        try:
            with open(os.path.join(tmp, "api_token"), encoding="utf-8") as fh:
                token = fh.read().strip()
            if token:
                break
        except OSError:
            pass
    check("the app came up and wrote a token", bool(token))

    print("1) the default path must not surface an unverified prerelease")
    res = call("update_check", token=token)
    result = res.get("result", res)
    check(f"it reports the version it was built as ({BUILT_VERSION})",
          result.get("running") == BUILT_VERSION, result.get("running"))
    check("and reports no update by default",
          result.get("update") is None, result.get("update"))

    print("2) the running build is the rc, so the rc is not an update for it")
    # This app IS v1.1.0-rc.1, and the newest published release is
    # v1.1.0-rc.1. "Newer than what is running" must be false, or an rc
    # would offer itself to the people running it. Asserting the opposite
    # here would have been a test that only passes if releases are broken.
    res = call("update_check", {"prerelease": True}, token=token)
    result = res.get("result", res)
    check("opting in still reports no update for itself",
          result.get("update") is None, result.get("update"))

    print("2b) but a 1.0.1 user WOULD be offered it -- verified directly,")
    print("    since this process is already running the rc")
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import updater

    # Ask the live API what the newest prerelease actually is, rather than
    # hardcoding a version. Hardcoding meant this test broke every time a new
    # candidate was cut, which trains people to ignore it.
    # A sentinel OLDER than anything published, so "newest" really is newest.
    # (Using a high version here returns None for the opposite reason: nothing
    # is newer than it, which is the same None that means "no prerelease".)
    probe = updater.fetch_latest(current="0.0.1", include_prerelease=True)
    newest_pre = None if probe is None else probe.version
    check("the published prerelease can be discovered",
          newest_pre is not None, newest_pre)

    for flags, want in ((False, None), (True, newest_pre)):
        info = updater.fetch_latest(current="1.0.1",
                                    include_prerelease=flags)
        got = None if info is None else info.version
        check(f"a 1.0.1 user, include_prerelease={flags}, gets {want}",
              got == want, got)

    print("3) a check must change nothing on disk")
    staged = [n for n in os.listdir(tmp) if "download" in n or "HashPlay-" in n]
    check("nothing was downloaded or staged", not staged, staged)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("UPDATE CHECK TESTS PASSED")
