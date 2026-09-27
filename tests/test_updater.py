"""Plain-script checks for src/updater.py: release parsing, platform asset
selection, and checksum verification.

Run:  python3 tests/test_updater.py

These are the paths that decide whether a user's app is allowed to replace
itself. A test that only checks "no exception" would let a compare bug ship,
so each case below asserts on the actual value.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

import updater
from updater import (UpdateInfo, _asset_matches_platform, is_newer,
                     parse_release, pick_asset, stage_pending, verify_download)

# A realistic release payload, trimmed only where it would bloat the file.
# Digest format matches what the GitHub releases API returns: "sha256:<hex>".
_MAC_DIGEST = "b" * 64
_LINUX_DIGEST = "c" * 64

PAYLOAD = {
    "url": "https://api.github.com/repos/Giathinh1234/NCS-SMLauncher/releases/12345",
    "html_url": "https://github.com/Giathinh1234/NCS-SMLauncher/releases/tag/v0.99.2",
    "tag_name": "v0.99.2",
    "name": "v0.99.2-beta",
    "draft": False,
    "prerelease": True,
    "published_at": "2026-09-20T10:00:00Z",
    "assets": [
        {
            "name": "HashPlay-linux-x86_64",
            "size": 43210000,
            "digest": "sha256:" + _LINUX_DIGEST,
            "browser_download_url":
                "https://github.com/Giathinh1234/NCS-SMLauncher/releases/download/v0.99.2/HashPlay-linux-x86_64",
        },
        {
            "name": "HashPlay.app.zip",
            "size": 38820000,
            "digest": "sha256:" + _MAC_DIGEST,
            "browser_download_url":
                "https://github.com/Giathinh1234/NCS-SMLauncher/releases/download/v0.99.2/HashPlay.app.zip",
        },
    ],
}

n = 0
def ok(msg):
    global n
    n += 1
    print("%2d. %s" % (n, msg))


# -- 1. constants ----------------------------------------------------------
assert updater.REPO == "Giathinh1234/NCS-SMLauncher", updater.REPO
assert updater.API == "https://api.github.com/repos/{repo}/releases", updater.API
assert updater.TIMEOUT == 30, updater.TIMEOUT
assert updater.UPDATES_DIR.endswith(os.path.join(".ncs-smlauncher", "updates")), updater.UPDATES_DIR
ok("REPO/API/TIMEOUT/UPDATES_DIR constants are correct")

# -- 2. realistic payload --------------------------------------------------
info = parse_release(json.loads(json.dumps(PAYLOAD)), current="0.99.0")
assert info is not None, "v0.99.2 vs 0.99.0 should be an update"
assert info.version == "0.99.2", info.version
assert info.prerelease is True, info.prerelease
assert info.name == "v0.99.2-beta", info.name
assert len(info.assets) == 2, info.assets
ok("realistic payload parsed: version=%s prerelease=%s name=%s"
   % (info.version, info.prerelease, info.name))

# -- 3. repr ---------------------------------------------------------------
r = repr(UpdateInfo("0.99.2", size=1234, prerelease=False))
assert r == "<UpdateInfo 0.99.2 1234B prerelease=False>", r
ok("repr is '<UpdateInfo 0.99.2 1234B prerelease=False>': %s" % r)

# -- 4. digest is carried through pick_asset -------------------------------
mac = pick_asset(info, platform="darwin")
assert mac is not None, "darwin should match an asset"
assert mac.digest == _MAC_DIGEST, mac.digest
assert mac.size == 38820000, mac.size
assert mac.name == "HashPlay.app.zip", mac.name
ok("pick_asset(darwin) -> %s size=%d digest=%s..." % (mac.name, mac.size, mac.digest[:12]))

# -- 5. older tag -> None --------------------------------------------------
old = dict(PAYLOAD, tag_name="v0.98.0", assets=[])
assert parse_release(old, current="0.99.2") is None, "0.98.0 must not beat 0.99.2"
assert parse_release(dict(PAYLOAD, tag_name="v0.50.0", assets=[]), current="1.0.0") is None
ok("an OLDER tag returns None")

# -- 6. equal tag -> None --------------------------------------------------
assert parse_release(dict(PAYLOAD, tag_name="v0.99.2", assets=[]), current="0.99.2") is None
assert parse_release(dict(PAYLOAD, tag_name="0.99.2", assets=[]), current="v0.99.2") is None
ok("an EQUAL tag returns None (strictly-newer only)")

# -- 7. draft / junk / empty tag -> None ----------------------------------
assert parse_release(dict(PAYLOAD, draft=True), current="0.99.0") is None
assert parse_release(dict(PAYLOAD, tag_name=""), current="0.99.0") is None
assert parse_release("not a dict", current="0.99.0") is None
assert parse_release(None, current="0.99.0") is None
ok("draft / empty tag / non-dict payloads all return None")

# -- 8. platform asset selection ------------------------------------------
assert _asset_matches_platform("HashPlay.app.zip", "darwin") is True
assert _asset_matches_platform("HashPlay", "darwin") is True
assert _asset_matches_platform("HashPlay-linux-x86_64", "darwin") is False
assert _asset_matches_platform("HashPlay-linux-x86_64", "linux") is True
assert _asset_matches_platform("LINUX-build", "linux") is True
assert _asset_matches_platform("HashPlay.app.zip", "win32") is False
assert _asset_matches_platform("HashPlay.app.zip", None) is False
ok("_asset_matches_platform is correct for darwin/linux/other")

assert pick_asset(info, platform="darwin").name == "HashPlay.app.zip"
assert pick_asset(info, platform="linux").name == "HashPlay-linux-x86_64"
assert pick_asset(info, platform="win32") is None
bare = parse_release({"tag_name": "v1.0.0", "assets": [{"name": "HashPlay", "size": 7}]}, current="0.99")
assert pick_asset(bare, platform="darwin").name == "HashPlay"
ok("pick_asset: darwin -> HashPlay(.app.zip), linux -> HashPlay-linux-x86_64, win32 -> None")

# -- 9. is_newer -----------------------------------------------------------
assert is_newer("1.0.0", "0.99.2") is True
assert is_newer("1.0.0", "1.0.0") is False
assert is_newer("0.99.2", "1.0.0") is False
assert is_newer("garbage", "1.0.0") is False
ok("is_newer handles new/equal/old/unparseable tags")

# -- 10. verify_download ---------------------------------------------------
tmp = tempfile.mkdtemp(prefix="ncs-upd-")
try:
    good = os.path.join(tmp, "payload.bin")
    data = os.urandom(1024 * 64)
    with open(good, "wb") as fh:
        fh.write(data)
    digest = hashlib.sha256(data).hexdigest()

    good_ok, reason = verify_download(good, digest=digest, expected_size=len(data))
    assert good_ok is True, reason
    assert reason == "ok", reason
    ok("verify_download accepts a correct file (size %d, sha256 %s...)" % (len(data), digest[:12]))

    bad_size_ok, bad_size_reason = verify_download(good, digest=digest, expected_size=len(data) + 1)
    assert bad_size_ok is False, "size mismatch must fail"
    assert "size" in bad_size_reason, bad_size_reason
    ok("size mismatch fails: %s" % bad_size_reason)

    other = hashlib.sha256(b"different bytes entirely").hexdigest()
    bad_sum_ok, bad_sum_reason = verify_download(good, digest=other, expected_size=len(data))
    assert bad_sum_ok is False, "checksum mismatch must fail"
    assert "checksum" in bad_sum_reason, bad_sum_reason
    ok("checksum mismatch fails: %s" % bad_sum_reason)

    missing_ok, missing_reason = verify_download(os.path.join(tmp, "nope.bin"), digest=digest)
    assert missing_ok is False and "missing" in missing_reason
    ok("a missing file fails verification without raising: %s" % missing_reason)

    # No metadata at all: still fine, this is the "release published without
    # a digest" case. self_update() is what refuses those, not verify.
    assert verify_download(good) == (True, "ok")
    ok("verify_download with no metadata returns (True, 'ok')")

    # -- 11. stage_pending -------------------------------------------------
    base = os.path.join(tmp, "updates")
    dest = stage_pending(good, "0.99.2", base_dir=base)
    assert dest is not None, "staging a real file must succeed"
    assert os.path.isfile(dest), dest
    with open(dest, "rb") as fh:
        assert fh.read() == data, "staged bytes must match the source exactly"
    assert os.path.dirname(dest) == base, dest
    ok("stage_pending wrote the payload byte-for-byte to %s" % os.path.basename(dest))

    meta_path = os.path.join(base, "pending.json")
    assert os.path.isfile(meta_path), "pending.json must exist"
    with open(meta_path) as fh:
        meta = json.load(fh)
    assert meta["version"] == "0.99.2", meta
    assert meta["staged_from"] == os.path.abspath(good), meta
    assert meta["staged_as"] == dest, meta
    assert "from_version" in meta, meta
    ok("pending.json has version/staged_from/staged_as/from_version: %s" % sorted(meta))

    leftovers = [f for f in os.listdir(base) if f.endswith(".tmp")]
    assert leftovers == [], "temp files left behind: %s" % leftovers
    ok("no .tmp leftovers in the staging dir: %s" % sorted(os.listdir(base)))

    assert updater.pending_path(base) == dest
    assert updater.pending_meta(base)["version"] == "0.99.2"
    assert stage_pending(os.path.join(tmp, "ghost.bin"), "1.0.0", base_dir=base) is None
    ok("pending_path/pending_meta round-trip; staging a missing source returns None")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("... TESTS PASSED")
