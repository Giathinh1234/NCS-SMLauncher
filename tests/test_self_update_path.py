"""The self-update path end to end, up to (but not including) the download.

`self_update` called fetch_latest without `current`, and parse_release(release,
None) returns None for every release -- so the whole flow reported "no update
available" for every release ever published. That was independent of the
asset-name bug: fix the asset names and it still returned None.

These checks drive the real functions with the real release-payload shape and
assert on what a 1.0.1 user would actually be offered.
"""
import json
import os
import sys

ROOT = "/Users/giathinh/ncs-music-launcher"
sys.path.insert(0, os.path.join(ROOT, "src"))
import updater                                        # noqa: E402
import version                                        # noqa: E402

fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


def serve(payload):
    """Stand in for the GitHub releases API. Returns an INSTANCE: handing back
    the class makes `with urlopen(...) as resp` raise TypeError, which
    fetch_latest's broad `except Exception` turns into a None -- a test that
    then reports a working fix as broken."""
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()
    return R()


def rel(tag, names, prerelease=False):
    return {"tag_name": tag, "prerelease": prerelease, "draft": False,
            "published_at": "2026-09-28T00:00:00Z",
            "assets": [{"name": n, "browser_download_url": "https://x/" + n,
                        "size": 4096} for n in names]}


RC = rel("v1.1.0-rc.1", ["HashPlay-macos-arm64"], prerelease=True)
FINAL = rel("v1.1.0", ["HashPlay-macos-arm64"])

print("SELF-UPDATE PATH")
print(f"  APP_VERSION on this machine: {version.APP_VERSION}")

real = updater.urllib.request.urlopen
try:
    # ------------------------------------ the defect: `current` was not passed
    print("1) fetch_latest needs the current version to judge 'newer'")
    # The rc is listed first here on purpose: GitHub returns newest-first,
    # so the first newer entry wins, and an rc created before the final is
    # genuinely older. The order is the API's to decide, not ours.
    updater.urllib.request.urlopen = lambda *a, **k: serve([RC, FINAL])
    got = updater.fetch_latest(current="1.0.1", include_prerelease=False)
    check("with current=1.0.1 it skips the rc and finds the final",
          got is not None and got.version == "1.1.0",
          None if got is None else got.version)
    got = updater.fetch_latest(current="1.0.1", include_prerelease=True)
    check("with prereleases allowed it takes the first newer entry",
          got is not None and got.version == "1.1.0-rc.1",
          None if got is None else got.version)
    got = updater.fetch_latest(current="2.0.0")
    check("with current=2.0.0 nothing qualifies, as it should",
          got is None, got)

    # ------------------------------------------- prereleases stay opt-in
    print("2) an unverified rc must not be pushed at users by default")
    updater.urllib.request.urlopen = lambda *a, **k: serve([RC])
    got = updater.fetch_latest(current="1.0.1", include_prerelease=False)
    check("include_prerelease=False hides the rc", got is None, got)
    got = updater.fetch_latest(current="1.0.1", include_prerelease=True)
    check("include_prerelease=True offers it to someone who asked",
          got is not None and got.version == "1.1.0-rc.1",
          None if got is None else got.version)

    # ------------------------------- self_update actually reaches the check
    print("3) self_update gets as far as the release (no download yet)")
    updater.urllib.request.urlopen = lambda *a, **k: serve([FINAL])

    calls = {"fetch": 0}
    real_fetch = updater.fetch_latest

    def spy(*a, **k):
        calls["fetch"] += 1
        # Did it pass `current`? That is the whole defect.
        calls["current"] = k.get("current", a[0] if a else None)
        return real_fetch(*a, **k)
    updater.fetch_latest = spy
    try:
        # Stop right after the lookup: a base_dir that is not writable keeps
        # us from downloading, and we only care about the check step.
        updater.self_update(base_dir="/nonexistent-hashplay-dir-xyz",
                            target="/nonexistent-hashplay-dir-xyz/HashPlay.app")
    finally:
        updater.fetch_latest = real_fetch
    check("self_update reaches fetch_latest", calls.get("fetch", 0) == 1, calls)
    check("self_update passes the current version",
          calls.get("current") is not None, calls.get("current"))

    # ------------------------------------------- the version string itself
    print("4) an rc must not outrank the release it precedes")
    check("1.1.0-rc.1 does not look newer than 1.1.0",
          version.is_newer("1.1.0-rc.1", "1.1.0") is False,
          version.parse("1.1.0-rc.1"))
    check("1.1.0-rc.1 does look newer than 1.0.1",
          version.is_newer("1.1.0-rc.1", "1.0.1") is True)
    check("the rc parses to its base version",
          version.parse("1.1.0-rc.1") == version.parse("1.1.0"),
          version.parse("1.1.0-rc.1"))
finally:
    updater.urllib.request.urlopen = real

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("SELF-UPDATE PATH TESTS PASSED")
