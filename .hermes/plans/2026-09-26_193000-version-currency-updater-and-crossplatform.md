# NCS-SMLauncher — Version Currency, Self-Updater, Settings Migration, and Linux/macOS Builds

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.
> **Run AFTER** `.hermes/plans/2026-09-26_190000-remappable-keys-streaming-settings-and-publish.md` (tasks 1-13). This file is tasks 14-24.

**Goal:** Move the whole project onto Python 3.14, bring the Android toolchain current within AGP 8.x while fixing three latent Android bugs, make the app update itself from GitHub releases, migrate old user settings automatically, and ship Linux/Unix and macOS builds from a single version source of truth.

**Architecture:** One `VERSION` file at the repo root becomes the single source of truth, read by the app, the build scripts, the Android Gradle config, and the release workflow, so the version can no longer drift the way it did (`Info.plist` claimed `1.0` while the release was `0.99`). A new `src/updater.py` performs an opt-in, checksum-verified update that stages the new bundle and swaps it in from a detached helper after the current process exits — it never replaces the running binary in place. A `schema_version` in the settings file drives ordered migrations in `src/migrations.py`. GitHub Actions builds macOS arm64 and Linux binaries and opens an issue when dependencies go stale.

**Tech Stack:** Python 3.14.7 (Homebrew `python@3.14`, already installed), PyInstaller 6.22.2, AGP 8.13.0 / Kotlin 2.2.20 / Gradle 8.13 / compileSdk 34, JDK 17.0.7 (installed), GitHub Actions, `gh` CLI 2.100.0.

**Decisions locked in by the user:** Python → 3.14. Android → conservative, stay within AGP 8.x and keep compileSdk 34, **no SDK downloads on this machine**. Upgrade work → all three (repo currency, self-updater, settings migration), plus Linux/Unix and macOS builds.

---

## Current Context / Assumptions

**Environment as measured, not assumed**

| Thing | Current state | Target |
|---|---|---|
| Python on this machine | `python3` → 3.13.7, python.org framework at `/Library/Frameworks/Python.framework` | 3.14.7 |
| `python@3.14` (Homebrew) | **already installed**, 3.14.6, upgradeable to 3.14.7 | 3.14.7 |
| Python dependency pinning | **none** — no `requirements.txt`, no `pyproject.toml` | pinned `requirements.txt` |
| App version metadata | `Info.plist` says `1.0`; release tag is `v0.99`; Android `versionName` is `1.0` | one `VERSION` file |
| AGP / Kotlin / Gradle | 8.5.2 / 2.0.0 / 8.7 | 8.13.0 / 2.2.20 / 8.13 |
| compileSdk / targetSdk / minSdk | 34 / 34 / 26 | unchanged (no SDK download) |
| JDK | Temurin 17.0.7 installed | unchanged (AGP 8.x requires 17) |
| Android SDK present | `/opt/homebrew/share/android-commandlinetools`, only `android-34` + `build-tools 34.0.0` | unchanged |
| `gh` auth | authenticated as Giathinh1234 | unchanged |

**Three latent Android bugs, all confirmed by reading the files**

1. **`android/gradlew` cannot work.** It is a 3-line hand-written stub:
   ```sh
   #!/bin/sh
   exec java -classpath "$APP_HOME/gradle/wrapper/gradle-wrapper.jar" org.gradle.wrapper.GradleWrapperMain "$@"
   ```
   `$APP_HOME` is never set by this script, so the classpath is wrong and it fails. The real Gradle wrapper script is ~8 KB and computes `APP_HOME` itself. Git records the file mode as `100755` (executable) but the working copy is `644`.
2. **`android/local.properties` is committed to git.** It contains `sdk.dir=/opt/homebrew/share/android-commandlinetools` — a machine-specific absolute path. `git check-ignore` confirms it is **not** ignored. Every collaborator and every CI run gets this path.
3. **Android version metadata is a lie.** `versionName = "1.0"` / `versionCode = 1` in `android/app/build.gradle.kts` while the shipped release is `0.99`.

**Portability reality check on "update the linux/unix version"**

There is currently **no Linux build at all** — only `build_macos_app.sh` producing a macOS `.app`. Three things in the app are macOS-specific:

- `pick_folder_dialog()` shells out to `osascript`. It is wrapped in `try/except` so it degrades to "cancelled", but there is no Linux picker.
- `src/media_keys.py` imports Quartz; it is guarded by a `HAVE_QUARTZ` flag, so it no-ops off macOS.
- `libtorrent` via pip has poor manylinux wheel coverage. **The app already tolerates this**: `lt` may be `None` and the torrent overlay reports "libtorrent missing". A Linux build must therefore treat libtorrent as optional, not fatal.

**Verified in the previous planning session (carried forward, still valid)**

- `miniaudio.decode_file` does **not** raise on a truncated MP3; it returns only the decodable prefix. Design for the symptom.
- `mp3_stream_file` emits the file's native channel count (measured exactly 2.000× mono on stereo) — downmix is mandatory.
- Incremental cursor decode is bit-identical to a full decode (10,516,655 samples both ways, max diff 1.5e-05).
- `HashPlay.spec` uses `pathex=['src']`, so new sibling modules bundle with no spec change.

---

## Proposed Approach

Establish the version source of truth first, because every later task writes to it. Then make the build reproducible: a pinned `requirements.txt` plus a venv built on Python 3.14, so the bundle no longer depends on whatever happens to sit in the system framework. Then the self-updater, which is the only genuinely risky piece and therefore the only one that must be opt-in, checksum-verified, and staged rather than in-place. Then migrations, then the Android fixes and conservative bumps, then CI for macOS + Linux + a staleness check. Self-updater and migrations ship in the release that CI builds, so the very first release to contain the updater is the one that proves it.

---

## Step-by-Step Tasks

### Task 14: Single version source of truth

**Objective:** One `VERSION` file, read by everything, replacing the three places that currently disagree.

**Files:**
- Create: `VERSION`
- Create: `src/version.py`
- Modify: `build_macos_app.sh` (drop the hardcoded `1.0`)
- Modify: `android/app/build.gradle.kts` (read `VERSION`)
- Test: `tests/test_version.py`

**Step 1: Write the failing test**

Create `tests/test_version.py`:

```python
import os, re, subprocess, sys, tempfile
ROOT = "/Users/giathinh/ncs-music-launcher"
sys.path.insert(0, os.path.join(ROOT, "src"))

raw = open(os.path.join(ROOT, "VERSION")).read().strip()
print("1) VERSION =", repr(raw))
assert re.fullmatch(r"\d+\.\d+\.\d+", raw), f"VERSION must be semver, got {raw!r}"

import version
print("2) version.APP_VERSION =", version.APP_VERSION)
assert version.APP_VERSION == raw
assert version.parse(raw) > (0, 99, 0), "must be past the 0.99 line"
assert version.is_newer("0.99.1", "0.99.0") is True
assert version.is_newer("0.99.0", "0.99.1") is False
assert version.is_newer("0.99.1", "0.99.1") is False
print("3) is_newer OK")

# the build script must not hardcode a version any more
sh = open(os.path.join(ROOT, "build_macos_app.sh")).read()
assert 'VERSION="$(cat VERSION)"' in sh, "build script must read VERSION"
assert "<string>1.0</string>" not in sh, "no hardcoded 1.0 left in build script"
print("4) build_macos_app.sh reads VERSION")

# android must not hardcode versionName either
gk = open(os.path.join(ROOT, "android/app/build.gradle.kts")).read()
assert 'versionName = "1.0"' not in gk, "android still claims 1.0"
print("5) android versionName derived, not literal")
print("VERSION TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_version.py`
Expected: FAIL — `FileNotFoundError: .../VERSION`

**Step 3: Write minimal implementation**

Create `VERSION` containing exactly:

```
0.99.1
```

Create `src/version.py`:

```python
"""Single source of truth for the app version.

VERSION lives at the repo root. This module reads it and must work both from
source and from inside a frozen PyInstaller bundle, where the repo root is not
on disk next to us.
"""
import os
import sys


def _find_version_file():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, "VERSION")]
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(sys._MEIPASS, "VERSION"))
    # repo root is one level above src/
    candidates.append(os.path.join(os.path.dirname(here), "VERSION"))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read().strip()
            if text:
                return text
        except OSError:
            continue
    return "0.0.0"


APP_VERSION = _find_version_file()


def parse(v):
    """'0.99.1' -> (0, 99, 1). Missing parts become 0."""
    parts = []
    for chunk in str(v).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(candidate, current=APP_VERSION):
    """True when `candidate` is a strictly higher version than `current`."""
    return parse(candidate) > parse(current)
```

In `HashPlay.spec`, add the VERSION file to `datas` so it lands inside the bundle:

```python
    datas=[('VERSION', '.')],
```

In `build_macos_app.sh`, replace the `VERSION="${1:-0.99.1}"` line introduced in task 13 with a read from the file (keeping the argument as an override):

```bash
VERSION="${1:-$(cat VERSION)}"
BUILD=$(git rev-parse --short HEAD 2>/dev/null || echo nogit)
```

In `android/app/build.gradle.kts`, replace the hardcoded block:

```kotlin
    defaultConfig {
        applicationId = "com.giathinh.hashplay"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }
```

with a read of the root `VERSION` file (bumping `versionCode` by hand when releasing):

```kotlin
import java.util.Properties

val appVersion: String = rootProject.file("../VERSION")
    .takeIf { it.exists() }
    ?.readText()?.trim()
    ?: "0.0.0"

android {
    namespace = "com.giathinh.hashplay"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.giathinh.hashplay"
        minSdk = 26
        targetSdk = 34
        versionCode = 1          // bump manually on each release
        versionName = appVersion
    }
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_version.py`
Expected: five numbered lines then `VERSION TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add VERSION src/version.py HashPlay.spec build_macos_app.sh android/app/build.gradle.kts tests/test_version.py
git commit -m "feat(version): single VERSION source of truth for app, build, and Android"
```

---

### Task 15: Pin dependencies and build on Python 3.14

**Objective:** A reproducible `requirements.txt` and a venv on Python 3.14, so the bundle stops depending on the system framework.

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `scripts/build_venv.sh`
- Modify: `build_macos_app.sh` (use the venv's PyInstaller when it exists)
- Modify: `.gitignore`
- Test: `tests/test_requirements.py`

**Step 1: Write the failing test**

Create `tests/test_requirements.py`:

```python
import os, re, sys
ROOT = "/Users/giathinh/ncs-music-launcher"

req = open(os.path.join(ROOT, "requirements.txt")).read()
print("1) requirements.txt:\n" + req)

names = {}
for line in req.splitlines():
    line = line.split("#")[0].strip()
    if not line or line.startswith("-"):
        continue
    for sep in (">=", "==", "~=", "<", ">"):
        if sep in line:
            k, v = line.split(sep, 1)
            names[k.strip().lower()] = sep + v.strip()
            break
    else:
        names[line.lower()] = "UNPINNED"

required = ["pygame", "numpy", "miniaudio", "sounddevice",
            "mutagen", "pillow", "libtorrent"]
for r in required:
    assert r in names, f"{r} missing from requirements.txt"
print("2) all runtime deps declared:", sorted(names))

unpinned = [k for k, v in names.items() if v == "UNPINNED"]
assert not unpinned, f"these must carry a version floor: {unpinned}"
print("3) every dep has a version floor (no UNPINNED)")

dev = open(os.path.join(ROOT, "requirements-dev.txt")).read()
assert "pyinstaller" in dev.lower()
print("4) dev requirements include pyinstaller")

gi = open(os.path.join(ROOT, ".gitignore")).read()
for line in [".venv", "venv/", "requirements.lock"]:
    assert line in gi, f".gitignore must contain {line}"
print("5) .gitignore covers venv and lockfile")

print("REQUIREMENTS TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_requirements.py`
Expected: FAIL — `FileNotFoundError: .../requirements.txt`

**Step 3: Write minimal implementation**

Create `requirements.txt`, using version floors matching what is currently installed and working:

```
# Runtime dependencies for NCS-SMLauncher (NCS Music Launcher).
# Floors, not hard pins: the app is a desktop app, and a hard pin in a
# requirements.txt makes local dev need a lockfile instead.

pygame>=2.6.1,<3
numpy>=2.0,<3
miniaudio>=1.61
sounddevice>=0.4.6
mutagen>=1.47
pillow>=10.0
libtorrent>=2.0.9
```

Create `requirements-dev.txt`:

```
-r requirements.txt

pyinstaller>=6.10
```

Create `scripts/build_venv.sh`:

```bash
#!/usr/bin/env bash
# Create .venv on Python 3.14 and install the pinned dependencies.
# Idempotent: safe to re-run. Refuses to silently fall back to another Python.
set -euo pipefail
cd "$(dirname "$0")/.."

PY314="${PY314:-$(command -v python3.14 || true)}"
if [ -z "$PY314" ]; then
  PY314="$(brew --prefix python@3.14 2>/dev/null)/bin/python3.14" || true
fi
if [ ! -x "$PY314" ]; then
  echo "error: python3.14 not found. Install it with: brew install python@3.14" >&2
  exit 1
fi
echo "Using $PY314 ($("$PY314" --version 2>&1))"

[ -d .venv ] || "$PY314" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements-dev.txt
echo "venv ready: $(.venv/bin/python --version)"
```

Make it executable: `chmod +x scripts/build_venv.sh`.

In `build_macos_app.sh`, prefer the venv interpreter:

```bash
# 1. build the single-file executable
if [ -x .venv/bin/python ]; then
  PYI=(.venv/bin/python -m PyInstaller)
else
  echo "warning: .venv not found; falling back to system python3" >&2
  echo "         run ./scripts/build_venv.sh for a reproducible 3.14 build" >&2
  PYI=(python3 -m PyInstaller)
fi
"${PYI[@]}" HashPlay.spec --noconfirm
```

Append to `.gitignore`:

```
.venv
venv/
requirements.lock
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_requirements.py`
Expected: numbered lines then `REQUIREMENTS TESTS PASSED`

**Step 5: Build the venv for real and verify on 3.14**

Run:
```bash
cd /Users/giathinh/ncs-music-launcher
./scripts/build_venv.sh
.venv/bin/python --version
.venv/bin/python -c "import pygame, numpy, miniaudio, sounddevice, mutagen, PIL; print('imports ok')"
```
Expected: `Python 3.14.x` and `imports ok`.

Then confirm the app itself runs on 3.14 (this is the real test — wheels for 3.14 may be missing for some dep):

Run: `cd /Users/giathinh/ncs-music-launcher && .venv/bin/python src/ncs_launcher.py ~/Downloads`
Expected: window opens, `Found N track(s)`, no traceback. If a wheel is missing for 3.14, record which one and set that dep's floor accordingly rather than silently reverting to 3.13.

**Step 6: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add requirements.txt requirements-dev.txt scripts/build_venv.sh .gitignore tests/test_requirements.py
git commit -m "build: pinned requirements and a reproducible Python 3.14 venv"
```

---

### Task 16: Settings schema and ordered migrations

**Objective:** A `schema_version` in the settings file plus a migration chain, so an old config upgrades itself instead of silently reverting to defaults.

**Files:**
- Create: `src/migrations.py`
- Modify: `src/config.py` (add `schema_version`, run migrations on load)
- Test: `tests/test_migrations.py`

**Step 1: Write the failing test**

Create `tests/test_migrations.py`:

```python
import json, os, sys, tempfile
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import config as cfgmod
import migrations

print("1) SCHEMA_VERSION =", migrations.SCHEMA_VERSION)
assert migrations.SCHEMA_VERSION >= 1


def t(name, old, checks, expect_version=None):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "settings.json")
        if old is not None:
            json.dump(old, open(p, "w"))
        cfg = cfgmod.load_config(p)
        for desc, fn in checks:
            got = fn(cfg)
            print(f"   {name}: {desc} -> {got!r}")
            assert got, f"{name}: failed check: {desc}"
        if expect_version is not None:
            assert cfg["schema_version"] == expect_version, cfg["schema_version"]


# a v0 (today's) config: no schema_version, keymap values are pygame names
t("v0 legacy", {"library_folder": "/Music", "show_hints": True,
                "keymap": {"play_pause": "SPACE", "quit": "Q"}},
  [("library kept", lambda c: c["library_folder"] == "/Music"),
   ("play_pause kept", lambda c: c["keymap"]["play_pause"] == "SPACE"),
   ("hints kept", lambda c: c["show_hints"] is True),
   ("new keys filled", lambda c: c["keymap"].get("open_settings") == "COMMA")],
  expect_version=migrations.SCHEMA_VERSION)

# a v0 config that predates the easter-eggs flag
t("v0 minimal", {"keymap": {}},
  [("defaults filled", lambda c: c["keymap"].get("cycle_visualizer") == "F"),
   ("easter flag defaulted", lambda c: c.get("easter_eggs") is True)],
  expect_version=migrations.SCHEMA_VERSION)

# a file that is already current must not be touched
cur = cfgmod.default_config()
t("current", cur,
  [("still current", lambda c: c["library_folder"] == "" )],
  expect_version=migrations.SCHEMA_VERSION)

# migration never loses a user's library folder
t("preserve", {"library_folder": "/Volumes/Media/Music", "keymap": {}},
  [("folder preserved", lambda c: c["library_folder"] == "/Volumes/Media/Music")],
  expect_version=migrations.SCHEMA_VERSION)

# idempotent: migrating twice changes nothing
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "s.json")
    json.dump({"library_folder": "/X", "keymap": {}}, open(p, "w"))
    a = cfgmod.load_config(p)
    cfgmod.save_config(a, p)
    b = cfgmod.load_config(p)
    assert a == b, "migration must be idempotent"
    print("   idempotent OK")

# a stale keymap entry (removed action) is dropped, not fatal
t("stale key", {"keymap": {"ancient_action": "Z", "quit": "Q"}},
  [("quit survives", lambda c: c["keymap"]["quit"] == "Q"),
   ("ancient removed", lambda c: "ancient_action" not in c["keymap"])],
  expect_version=migrations.SCHEMA_VERSION)

print("MIGRATION TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_migrations.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'migrations'`

**Step 3: Write minimal implementation**

Create `src/migrations.py`:

```python
"""Ordered settings migrations.

A settings file written by an older build is upgraded in place on load. Every
migration takes (cfg) and returns cfg; migrations must be idempotent and must
never discard a user value they do not understand — unknown keys are kept.
"""
import copy

SCHEMA_VERSION = 1


def _migrate_v0_to_v1(cfg):
    """First release with a schema tag.

    - tag the file
    - ensure every known key exists
    - drop keymap entries for actions that no longer exist
    - keep unknown top-level keys (forward compatibility)
    """
    from config import DEFAULT_KEYMAP

    cfg = copy.deepcopy(cfg)
    cfg["schema_version"] = 1

    keymap = cfg.setdefault("keymap", {})
    if not isinstance(keymap, dict):
        keymap = {}
    for action, key_name in DEFAULT_KEYMAP.items():
        keymap.setdefault(action, key_name)
    for action in [k for k in keymap if k not in DEFAULT_KEYMAP]:
        del keymap[action]

    cfg.setdefault("library_folder", "")
    cfg.setdefault("show_hints", True)
    cfg.setdefault("easter_eggs", True)
    return cfg


MIGRATIONS = {
    0: _migrate_v0_to_v1,      # from untagged -> 1
}


def migrate(cfg):
    """Bring `cfg` up to SCHEMA_VERSION. Idempotent."""
    cfg = copy.deepcopy(cfg)
    current = 0
    try:
        current = int(cfg.get("schema_version") or 0)
    except (TypeError, ValueError):
        current = 0
    # step down if the file came from a future build
    while current < SCHEMA_VERSION:
        step = MIGRATIONS.get(current)
        if step is None:
            # no migration registered for this step: tag and move on
            cfg["schema_version"] = current + 1
            current += 1
            continue
        cfg = step(cfg)
        try:
            current = int(cfg.get("schema_version") or current + 1)
        except (TypeError, ValueError):
            current += 1
    if current > SCHEMA_VERSION:
        # written by a newer build; do not downgrade it
        return cfg
    cfg["schema_version"] = SCHEMA_VERSION
    return cfg
```

In `src/config.py`, add `"schema_version": 0` to `DEFAULT_CONFIG` and route `load_config` through `migrate`:

```python
def load_config(path=None):
    """Load settings, migrating older files forward."""
    path = path or CONFIG_PATH
    cfg = default_config()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError):
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    # migrate BEFORE merging, so migrations see the real stored shape
    from migrations import migrate
    return _merge(cfg, migrate(loaded))
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_migrations.py && python3 tests/test_config.py`
Expected: `MIGRATION TESTS PASSED` then `CONFIG TESTS PASSED`

Note: `test_config.py` asserts `c["keymap"]["play_pause"] == "SPACE"` on a fresh default — that still holds because `default_config()` fills the full keymap and `migrate` on an empty dict is a no-op beyond tagging.

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/migrations.py src/config.py tests/test_migrations.py
git commit -m "feat(settings): schema_version with ordered, idempotent migrations on load"
```

---

### Task 17: Self-updater — version check and staged download

**Objective:** Check GitHub for a newer release, download and verify it, and stage it for install on next launch. Opt-in, checksum-verified, never in-place.

**Files:**
- Create: `src/updater.py`
- Test: `tests/test_updater.py`

**Step 1: Write the failing test**

Create `tests/test_updater.py`:

```python
import hashlib, json, os, sys, tempfile
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
from updater import (UpdateInfo, parse_release, pick_asset,
                     verify_download, stage_pending, pending_path,
                     UPDATES_DIR, REPO)

print("1) repo:", REPO)
assert REPO == "Giathinh1234/NCS-SMLauncher"

# --- parse a GitHub releases payload ---
payload = {
    "tag_name": "v0.99.2",
    "name": "v0.99.2-beta",
    "prerelease": True,
    "draft": False,
    "assets": [
        {"name": "HashPlay", "size": 12345,
         "browser_download_url": "https://x/HashPlay",
         "digest": "sha256:" + "a" * 64},
        {"name": "HashPlay-linux-x86_64", "size": 999,
         "browser_download_url": "https://x/HashPlay-linux-x86_64",
         "digest": "sha256:" + "b" * 64},
    ],
}
info = parse_release(payload, current="0.99.1")
print("2) parsed:", info.version, "prerelease:", info.prerelease)
assert info.version == "0.99.2"
assert info.prerelease is True
assert info.digest and info.digest.startswith("sha256:")

# an older tag is not an update
old = parse_release({"tag_name": "v0.99.0", "prerelease": False, "assets": []},
                    current="0.99.1")
print("3) older release ->", old)
assert old is None

# equal version is not an update
same = parse_release({"tag_name": "v0.99.1", "prerelease": False, "assets": []},
                     current="0.99.1")
print("4) same version ->", same)
assert same is None

# drafts and non-matching names are ignored
assert parse_release({"tag_name": "v9.9.9", "draft": True, "assets": []},
                     current="0.99.1") is None
print("5) drafts ignored")

# asset choice follows the platform
mac = pick_asset(info, platform="darwin")
lin = pick_asset(info, platform="linux")
print("6) darwin ->", mac.name, "| linux ->", lin.name)
assert mac.name == "HashPlay" and lin.name == "HashPlay-linux-x86_64"

# --- checksum verification ---
with tempfile.TemporaryDirectory() as d:
    good = os.path.join(d, "good.bin")
    open(good, "wb").write(b"payload" * 100)
    digest = "sha256:" + hashlib.sha256(open(good, "rb").read()).hexdigest()
    ok, why = verify_download(good, digest, expected_size=os.path.getsize(good))
    print("7) good file ->", ok, why)
    assert ok is True

    bad = os.path.join(d, "bad.bin")
    open(bad, "wb").write(b"tampered")
    ok2, why2 = verify_download(bad, digest, expected_size=os.path.getsize(good))
    print("8) tampered file ->", ok2, why2)
    assert ok2 is False and "size" in why2.lower()

    ok3, why3 = verify_download(bad, "sha256:" + "c" * 64,
                                expected_size=len(b"tampered"))
    print("9) bad checksum ->", ok3, why3)
    assert ok3 is False and "checksum" in why3.lower()

# --- staging is atomic and lands under UPDATES_DIR ---
with tempfile.TemporaryDirectory() as d:
    src = os.path.join(d, "HashPlay")
    open(src, "wb").write(b"new binary")
    dest = stage_pending(src, "0.99.2", base_dir=d)
    print("10) staged at:", os.path.basename(dest))
    assert os.path.exists(dest)
    assert open(dest, "rb").read() == b"new binary"
    meta = os.path.join(os.path.dirname(dest), "pending.json")
    assert os.path.exists(meta), "must record metadata for the next launch"
    data = json.load(open(meta))
    print("11) pending meta:", data)
    assert data["version"] == "0.99.2" and data["staged_from"] == src
    assert not os.path.exists(str(dest) + ".tmp"), "no temp file left behind"

    # a corrupt/unreadable staged file must not be accepted later
    assert pending_path(d) == os.path.join(os.path.dirname(dest),
                                           "HashPlay-0.99.2.bin")

print("UPDATER TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_updater.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'updater'`

**Step 3: Write minimal implementation**

Create `src/updater.py`:

```python
"""Opt-in self-update from GitHub releases.

Design rules, all deliberate:
  * Never replace the running binary in place. The new build is downloaded,
    checksum-verified, and staged under ~/.ncs-smlauncher/updates/. The swap
    happens on the NEXT launch, from a detached helper, after this process has
    exited. A half-written bundle is the classic way to brick a desktop app.
  * Never automatic. The user has to choose to check, and to install.
  * Never unverified. Size and SHA-256 must both match the release metadata.
  * Never touches anything outside the app bundle it was launched from.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import version

REPO = "Giathinh1234/NCS-SMLauncher"
API = "https://api.github.com/repos/{repo}/releases"
UPDATES_DIR = os.path.join(os.path.expanduser("~"), ".ncs-smlauncher", "updates")
TIMEOUT = 30


class UpdateInfo:
    def __init__(self, version_, url, size, digest, prerelease=False, name=""):
        self.version = version_
        self.url = url
        self.size = size
        self.digest = digest
        self.prerelease = prerelease
        self.name = name

    def __repr__(self):
        return f"<UpdateInfo {self.version} {self.size}B prerelease={self.prerelease}>"


def _asset_matches_platform(name, platform):
    n = name.lower()
    if platform == "darwin":
        return n.endswith(".app.zip") or n == "hashplay"
    if platform == "linux":
        return "linux" in n
    return False


def pick_asset(info, platform=None):
    """Return the UpdateInfo for this platform, carrying the asset fields."""
    platform = platform or sys.platform
    for asset in info.assets:
        if _asset_matches_platform(asset["name"], platform):
            return UpdateInfo(info.version, asset["browser_download_url"],
                              asset.get("size"), asset.get("digest"),
                              info.prerelease, asset["name"])
    return None


def parse_release(payload, current=None):
    """A GitHub release payload -> UpdateInfo, or None if not an upgrade."""
    current = current or version.APP_VERSION
    if not isinstance(payload, dict) or payload.get("draft"):
        return None
    tag = str(payload.get("tag_name") or "").lstrip("vV")
    if not tag or not version.is_newer(tag, current):
        return None
    assets = []
    for a in payload.get("assets", []):
        assets.append({
            "name": a.get("name", ""),
            "size": a.get("size", 0),
            "browser_download_url": a.get("browser_download_url", ""),
            "digest": a.get("digest"),
        })
    return UpdateInfo(tag, None, 0, None,
                      bool(payload.get("prerelease")),
                      payload.get("name", tag)) | {"assets": assets} \
        if False else UpdateInfo.__new__(UpdateInfo) if False else _mk(tag, payload, assets)


def _mk(tag, payload, assets):
    info = UpdateInfo(tag, None, 0, None,
                      bool(payload.get("prerelease")),
                      payload.get("name", tag))
    info.assets = assets
    return info


def fetch_latest(current=None, include_prerelease=True, timeout=TIMEOUT):
    """Query the GitHub API. Returns UpdateInfo or None. Never raises."""
    req = urllib.request.Request(
        API.format(repo=REPO),
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"ncs-smlauncher/{version.APP_VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            releases = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"update check failed: {e}")
        return None
    for rel in releases or []:
        if rel.get("draft"):
            continue
        if rel.get("prerelease") and not include_prerelease:
            continue
        info = parse_release(rel, current=current)
        if info is not None:
            return info
    return None


def download(info, dest_dir=None, progress=None):
    """Download the platform asset to dest_dir. Returns the local path."""
    dest_dir = dest_dir or UPDATES_DIR
    os.makedirs(dest_dir, exist_ok=True)
    asset = pick_asset(info)
    if asset is None:
        return None
    out = os.path.join(dest_dir, f"{info.version}-{asset.name}")
    try:
        req = urllib.request.Request(asset.url,
                                     headers={"User-Agent": "ncs-smlauncher"})
        with urllib.request.urlopen(req, timeout=600) as resp, \
                open(out, "wb") as fh:
            shutil.copyfileobj(resp, fh, length=1 << 20)
    except Exception as e:
        print(f"download failed: {e}")
        return None
    ok, why = verify_download(out, asset.digest, asset.size)
    if not ok:
        os.remove(out)
        print(f"discarded download: {why}")
        return None
    return out


def verify_download(path, digest=None, expected_size=None):
    """(ok, reason). Both size and checksum must match when provided."""
    try:
        actual_size = os.path.getsize(path)
    except OSError as e:
        return False, f"cannot stat: {e}"
    if expected_size and actual_size != expected_size:
        return False, f"size mismatch: got {actual_size}, want {expected_size}"
    if digest:
        want = digest.split(":", 1)[-1].strip().lower()
        h = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError as e:
            return False, f"cannot read: {e}"
        if h.hexdigest() != want:
            return False, "checksum mismatch"
    return True, "ok"


def pending_path(base_dir=None, info=None):
    """Where a staged update is recorded, given its metadata."""
    meta_path = os.path.join(base_dir or UPDATES_DIR, "pending.json")
    if info is not None:
        return os.path.join(base_dir or UPDATES_DIR,
                            f"{info.version}-{info.name}")
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            return os.path.join(base_dir or UPDATES_DIR,
                                json.load(fh)["staged_as"])
    except (OSError, ValueError, KeyError):
        return None


def stage_pending(src_path, new_version, base_dir=None):
    """Copy a verified download into place and record it for next launch."""
    base_dir = base_dir or UPDATES_DIR
    os.makedirs(base_dir, exist_ok=True)
    name = os.path.basename(src_path)
    dest = os.path.join(base_dir, f"HashPlay-{new_version}.bin")
    fd, tmp = tempfile.mkstemp(dir=base_dir, suffix=".tmp")
    os.close(fd)
    try:
        shutil.copyfile(src_path, tmp)
        os.replace(tmp, dest)            # atomic within the same filesystem
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    with open(os.path.join(base_dir, "pending.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": new_version, "staged_from": src_path,
                   "staged_as": dest,
                   "from_version": version.APP_VERSION}, fh, indent=2)
    return dest


def consume_pending(base_dir=None, apply_fn=None):
    """Clear the staged update. Called AFTER the install has run."""
    base_dir = base_dir or UPDATES_DIR
    meta = os.path.join(base_dir, "pending.json")
    try:
        os.remove(meta)
    except OSError:
        pass
```

**Note on the `parse_release` above:** the version written in Task 17 Step 3 contains a deliberately awkward `| {...} if False else ...` expression left over from drafting. Replace it with the clean version before running the test:

```python
def parse_release(payload, current=None):
    """A GitHub release payload -> UpdateInfo, or None if not an upgrade."""
    current = current or version.APP_VERSION
    if not isinstance(payload, dict) or payload.get("draft"):
        return None
    tag = str(payload.get("tag_name") or "").lstrip("vV")
    if not tag or not version.is_newer(tag, current):
        return None
    assets = [{"name": a.get("name", ""), "size": a.get("size", 0),
               "browser_download_url": a.get("browser_download_url", ""),
               "digest": a.get("digest")}
              for a in payload.get("assets", [])]
    return _mk(tag, payload, assets)
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_updater.py`
Expected: eleven numbered lines then `UPDATER TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/updater.py tests/test_updater.py
git commit -m "feat(updater): opt-in, checksum-verified, staged GitHub release updates"
```

---

### Task 18: Apply a staged update on next launch

**Objective:** On startup, if an update is staged and verified, install it from a detached helper after this process exits, then relaunch.

**Files:**
- Modify: `src/updater.py` (add `apply_pending`)
- Modify: `src/ncs_launcher.py` (offer the update in Settings; call `apply_pending` at startup)
- Modify: `src/settings_panel.py` (add an "Check for updates" row)
- Test: `tests/test_apply_pending.py`

**Step 1: Write the failing test**

Create `tests/test_apply_pending.py`:

```python
import json, os, stat, sys, tempfile
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import updater

print("1) apply_pending exists:", hasattr(updater, "apply_pending"))
assert hasattr(updater, "apply_pending")

# no pending update -> nothing happens
with tempfile.TemporaryDirectory() as d:
    ran = []
    ok = updater.apply_pending(base_dir=d, spawn=lambda cmd: ran.append(cmd))
    print("2) nothing pending ->", ok, "spawned:", len(ran))
    assert ok is False and not ran

# a pending update spawns a detached helper and does NOT swap in place
with tempfile.TemporaryDirectory() as d:
    staged = os.path.join(d, "HashPlay-0.99.2.bin")
    open(staged, "wb").write(b"new build")
    json.dump({"version": "0.99.2", "staged_from": "/tmp/old",
               "staged_as": staged, "from_version": "0.99.1"},
              open(os.path.join(d, "pending.json"), "w"))
    ran = []
    ok = updater.apply_pending(base_dir=d, spawn=lambda cmd: ran.append(cmd),
                               target="/Applications/HashPlay.app")
    print("3) pending ->", ok, "commands:", len(ran))
    assert ok is True
    assert len(ran) == 1
    cmd = ran[0]
    print("4) helper command starts with:", cmd[0])
    assert cmd[0] in ("/bin/sh", "sh"), "must use a detached shell"
    joined = " ".join(cmd)
    assert staged in joined, "helper must reference the staged file"
    assert "/Applications/HashPlay.app" in joined
    # the ORIGINAL must still be untouched while we are running
    assert open(staged, "rb").read() == b"new build"

# a pending update whose staged file vanished must not spawn anything
with tempfile.TemporaryDirectory() as d:
    json.dump({"version": "0.99.2", "staged_from": "/tmp/x",
               "staged_as": os.path.join(d, "gone.bin"),
               "from_version": "0.99.1"},
              open(os.path.join(d, "pending.json"), "w"))
    ran = []
    ok = updater.apply_pending(base_dir=d, spawn=lambda cmd: ran.append(cmd))
    print("5) missing staged file ->", ok, "spawned:", len(ran))
    assert ok is False and not ran

# explicit opt-in: apply_pending must refuse unless told to
with tempfile.TemporaryDirectory() as d:
    staged = os.path.join(d, "HashPlay-0.99.2.bin")
    open(staged, "wb").write(b"x")
    json.dump({"version": "0.99.2", "staged_from": "/tmp/old",
               "staged_as": staged, "from_version": "0.99.1"},
              open(os.path.join(d, "pending.json"), "w"))
    ran = []
    ok = updater.apply_pending(base_dir=d, spawn=lambda cmd: ran.append(cmd),
                               confirmed=False)
    print("6) unconfirmed ->", ok, "spawned:", len(ran))
    assert ok is False and not ran, "must require explicit confirmation"

print("APPLY PENDING TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_apply_pending.py`
Expected: FAIL — `AssertionError: apply_pending exists: False`

**Step 3: Write minimal implementation**

Append to `src/updater.py`:

```python
def _helper_script(staged, target, app_dir):
    """A tiny POSIX sh script that waits for us to exit, then swaps.

    It waits on our PID so it can never race a still-running instance, moves
    the old bundle aside rather than deleting it (so a failed install can be
    rolled back by hand), and relaunches the new bundle.
    """
    return (
        "set -e\n"
        f"TARGET='{target}'\n"
        f"STAGED='{staged}'\n"
        f"APP='{app_dir}'\n"
        f"WAIT_PID={os.getpid()}\n"
        "while kill -0 \"$WAIT_PID\" 2>/dev/null; do sleep 1; done\n"
        "sleep 1\n"
        "if [ -e \"$TARGET\" ]; then mv \"$TARGET\" \"$TARGET.old\"; fi\n"
        "if [ -d \"$STAGED\" ]; then\n"
        "  mkdir -p \"$(dirname \"$TARGET\")\"\n"
        "  mv \"$STAGED\" \"$TARGET\"\n"
        "elif [ -f \"$STAGED\" ]; then\n"
        "  cp \"$STAGED\" \"$APP\" && chmod +x \"$APP\"\n"
        "fi\n"
        "open -a \"$TARGET\" 2>/dev/null || \"$TARGET\" &\n"
    )


def apply_pending(base_dir=None, spawn=None, target=None, confirmed=True):
    """Install a staged update after this process exits.

    Requires `confirmed=True`: the caller must have asked the user. Returns
    True if a helper was spawned, False if there was nothing to do.
    """
    base_dir = base_dir or UPDATES_DIR
    if not confirmed:
        return False
    staged = pending_path(base_dir)
    if not staged or not os.path.exists(staged):
        return False

    if target is None:
        exe = os.path.abspath(sys.argv[0])
        if os.path.basename(exe) == "HashPlay":
            target = exe
            app_dir = os.path.dirname(exe)
        else:
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            target = os.path.join(app_dir, "dist", "HashPlay")
    else:
        app_dir = os.path.dirname(target)

    script = _helper_script(staged, target, app_dir)
    if spawn is None:
        def spawn(cmd):
            subprocess.Popen(cmd, start_new_session=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    spawn(["/bin/sh", "-c", script])
    return True
```

In `src/ncs_launcher.py`, call it at startup, **only** if the user confirmed the install previously:

```python
    # a previously-confirmed update installs itself now that we can exit safely
    try:
        if cfg.get("update_confirmed", False):
            cfg["update_confirmed"] = False
            updater.apply_pending(target=None, confirmed=True)
    except Exception as e:
        print(f"update apply failed: {e}")
```

In `src/settings_panel.py`, add rows for checking and installing updates, above the key bindings:

```python
        self.items = [
            Row("Library folder", "info",
                value=self.cfg.get("library_folder") or "(not set)"),
            Row("Choose library folder...", "command",
                callback=self._cmd_choose_folder),
            Row("Move all music to a folder...", "command",
                callback=self._cmd_move_all),
            Row("Show control hints", "toggle",
                value=bool(self.cfg.get("show_hints", True)),
                callback=self._cmd_toggle_hints),
            Row("Check for updates", "command",
                callback=self._cmd_check_updates),
            Row("Install update on next launch", "command",
                callback=self._cmd_install_update,
                help_text="appears only after a verified download"),
            Row("", "info", value="── key bindings ──"),
        ]
```

and the two callbacks:

```python
    def _cmd_check_updates(self):
        import updater
        self.message = "checking for updates..."
        info = updater.fetch_latest(include_prerelease=True)
        if info is None:
            self.message = "you are on the latest version"
            return
        self._update_info = info
        self.message = (f"v{info.version} available "
                        f"({info.prerelease and 'pre-release' or 'stable'}) — "
                        "open 'Install update on next launch' to download")

    def _cmd_install_update(self):
        import updater
        info = getattr(self, "_update_info", None)
        if info is None:
            self.message = "check for updates first"
            return
        self.message = "downloading..."
        path = updater.download(info)
        if not path:
            self.message = "download failed or failed verification"
            return
        updater.stage_pending(path, info.version)
        self.cfg["update_confirmed"] = True
        self.message = (f"v{info.version} staged — restart HashPlay to "
                        "install it")
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_apply_pending.py && python3 tests/test_updater.py && python3 tests/test_settings_panel.py`
Expected: `APPLY PENDING TESTS PASSED`, `UPDATER TESTS PASSED`, `SETTINGS PANEL TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/updater.py src/ncs_launcher.py src/settings_panel.py tests/test_apply_pending.py
git commit -m "feat(updater): staged install on next launch, opt-in from Settings"
```

---

### Task 19: Fix the three Android bugs and bump within AGP 8.x

**Objective:** A gradlew that actually runs, an untracked `local.properties`, honest version metadata, and current-but-conservative toolchain versions.

**Files:**
- Modify: `android/gradlew` (real wrapper script)
- Modify: `android/build.gradle.kts` (AGP 8.5.2 → 8.13.0, Kotlin 2.0.0 → 2.2.20)
- Modify: `android/gradle/wrapper/gradle-wrapper.properties` (Gradle 8.7 → 8.13)
- Modify: `android/app/build.gradle.kts` (compose plugin version, done in task 14)
- Modify: `.gitignore` (ignore `android/local.properties`)
- Test: `tests/test_android_config.py`

**Step 1: Write the failing test**

Create `tests/test_android_config.py`:

```python
import os, re, stat, sys
ROOT = "/Users/giathinh/ncs-music-launcher"
AND = os.path.join(ROOT, "android")

# 1. gradlew must be the real wrapper, not a 3-line stub
gw = open(os.path.join(AND, "gradlew")).read()
print("1) gradlew lines:", len(gw.splitlines()))
assert len(gw.splitlines()) > 50, "gradlew is a stub; needs the real wrapper script"
assert "APP_HOME" in gw and "die()" in gw, "not the Gradle wrapper script"
mode = os.stat(os.path.join(AND, "gradlew")).st_mode
print("2) gradlew executable:", bool(mode & stat.S_IXUSR))
assert mode & stat.S_IXUSR, "gradlew must be executable"

# 2. local.properties must NOT be tracked
import subprocess
tracked = subprocess.run(["git", "ls-files", "android/local.properties"],
                          cwd=ROOT, capture_output=True, text=True).stdout.strip()
print("3) local.properties tracked:", repr(tracked))
assert not tracked, "machine-specific sdk.dir must not be in git"
gi = open(os.path.join(ROOT, ".gitignore")).read()
assert "android/local.properties" in gi
print("4) local.properties is ignored")

# 3. toolchain is current but stays on compileSdk 34 and AGP 8.x
top = open(os.path.join(AND, "build.gradle.kts")).read()
app = open(os.path.join(AND, "app/build.gradle.kts")).read()
wrap = open(os.path.join(AND, "gradle/wrapper/gradle-wrapper.properties")).read()
agp = re.search(r'com\.android\.application"\) version "([\d.]+)"', top).group(1)
kotlin = re.search(r'kotlin\.android"\) version "([\d.]+)"', top).group(1)
gradle = re.search(r'gradle-([\d.]+)-bin\.zip', wrap).group(1)
print("5) AGP=%s Kotlin=%s Gradle=%s" % (agp, kotlin, gradle))
assert agp.startswith("8."), "must stay on AGP 8.x per the decision"
assert tuple(map(int, agp.split("."))) >= (8, 13), "bump AGP to at least 8.13.0"
assert tuple(map(int, kotlin.split("."))) >= (2, 2), "bump Kotlin to at least 2.2"
assert tuple(map(int, gradle.split("."))) >= (8, 13)
assert 'compileSdk = 34' in app, "compileSdk stays 34 (no SDK download)"
assert 'targetSdk = 34' in app
assert 'versionName = "1.0"' not in app

print("ANDROID CONFIG TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_android_config.py`
Expected: FAIL — `AssertionError: gradlew is a stub; needs the real wrapper script`

**Step 3: Write minimal implementation**

Replace `android/gradlew` with the genuine Gradle 8.13 wrapper script, and restore the executable bit:

```bash
cd /Users/giathinh/ncs-music-launcher/android
curl -fsSL -o gradlew https://raw.githubusercontent.com/gradle/gradle/v8.13.0/gradlew
chmod +x gradlew
cd ..
git add android/gradlew
git update-index --chmod=+x android/gradlew
```

Verify it is the real thing: `wc -l android/gradlew` → expect well over 200 lines.

Untrack the machine-specific SDK path and ignore it:

```bash
cd /Users/giathinh/ncs-music-launcher
git rm --cached android/local.properties
printf '\n# Android: machine-specific SDK location, never commit\nandroid/local.properties\n' >> .gitignore
```

Keep the file on disk (the build still needs it locally) and replace the stub with a committed template:

```bash
cd /Users/giathinh/ncs-music-launcher
cat > android/local.properties.example <<'EOF'
# Copy to local.properties and point at your own SDK.
# macOS with Homebrew commandlinetools:
sdk.dir=/opt/homebrew/share/android-commandlinetools
# Android Studio default:
# sdk.dir=$HOME/Library/Android/sdk
EOF
```

In `android/build.gradle.kts`:

```kotlin
plugins {
    id("com.android.application") version "8.13.0" apply false
    id("org.jetbrains.kotlin.android") version "2.2.20" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.20" apply false
}
```

In `android/gradle/wrapper/gradle-wrapper.properties`:

```properties
distributionBase=GRADLE_USER_HOME
distributionPath=wrapper/dists
distributionUrl=https\://services.gradle.org/distributions/gradle-8.13-bin.zip
zipStoreBase=GRADLE_USER_HOME
zipStorePath=wrapper/dists
```

In `android/app/build.gradle.kts`, align the compose plugin with the Kotlin version:

```kotlin
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.20"
}
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_android_config.py`
Expected: five numbered lines then `ANDROID CONFIG TESTS PASSED`

**Step 5: Verify the Android project actually configures (needs the existing local SDK, no download of new platforms)**

Run:
```bash
cd /Users/giathinh/ncs-music-launcher/android
./gradlew --version 2>&1 | head -8
./gradlew :app:tasks --offline 2>&1 | tail -15
```
Expected: Gradle 8.13 banner; `:app:tasks` lists Android tasks. If Gradle needs to download its own 8.13 distribution, that is a first-use fetch of the wrapper only (not an SDK platform) — allow it. If AGP 8.13 refuses because it wants a newer build-tools than 34.0.0, fall back to the newest AGP 8.x that still accepts build-tools 34.0.0 and record the choice in the commit message; do NOT download SDK components.

**Step 6: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add -A android .gitignore tests/test_android_config.py
git commit -m "fix(android): real gradlew wrapper, untrack local.properties, bump to AGP 8.13/Kotlin 2.2.20/Gradle 8.13"
```

---

### Task 20: Linux/Unix support and build

**Objective:** The app runs and builds on Linux: a real folder picker, a documented build script, and no macOS-only crash paths.

**Files:**
- Modify: `src/ncs_launcher.py` (`pick_folder_dialog` — platform-aware)
- Create: `scripts/build_linux.sh`
- Modify: `src/media_keys.py` (confirm the no-op path is explicit)
- Test: `tests/test_crossplatform.py`

**Step 1: Write the failing test**

Create `tests/test_crossplatform.py`:

```python
import os, re, sys
ROOT = "/Users/giathinh/ncs-music-launcher"
src = open(os.path.join(ROOT, "src/ncs_launcher.py")).read()

i = src.index("def pick_folder_dialog(")
body = src[i:i + 2200]
print("1) pick_folder_dialog is platform-aware:",
      "sys.platform" in body or "platform" in body.lower())
assert "darwin" in body, "must branch on platform"
assert "zenity" in body or "kdialog" in body, "need a Linux folder picker"
assert "osascript" in body, "must keep the macOS path"

assert os.path.exists(os.path.join(ROOT, "scripts/build_linux.sh")), \
    "need a Linux build script"
sh = open(os.path.join(ROOT, "scripts/build_linux.sh")).read()
print("2) linux build script mentions PyInstaller:", "PyInstaller" in sh)
assert "ncs_launcher.py" in sh

# media keys must be a clean no-op off macOS, not an import crash
mk = open(os.path.join(ROOT, "src/media_keys.py")).read()
assert "HAVE_QUARTZ" in mk, "Quartz must stay guarded"
print("3) Quartz guarded behind HAVE_QUARTZ")

# libtorrent absence must be tolerated, not fatal
assert "lt is None" in src or "lt is not None" in src
print("4) libtorrent optional")

print("CROSSPLATFORM TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_crossplatform.py`
Expected: FAIL — `AssertionError: need a Linux folder picker`

**Step 3: Write minimal implementation**

In `src/ncs_launcher.py`, make the folder picker platform-aware:

```python
def pick_folder_dialog(current):
    """Native folder picker. Returns a path or None if cancelled.

    macOS uses AppleScript; Linux tries zenity, then kdialog; anything else
    (or a missing helper) reports cancelled rather than raising.
    """
    import subprocess as sp

    if sys.platform == "darwin":
        script = (
            'set init to POSIX file "%s"\n'
            'set p to choose folder with prompt "Choose a music folder" '
            'default location init\n'
            'return POSIX path of p' % (current or os.path.expanduser("~"))
        )
        try:
            r = sp.run(["osascript", "-e", script], capture_output=True,
                       text=True, timeout=300)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().rstrip("/") or "/"
        except Exception:
            pass
        return None

    for cmd in (["zenity", "--file-selection", "--directory",
                 "--title=Choose a music folder", current],
                ["kdialog", "--getexistingdirectory", current]):
        if shutil.which(cmd[0]) is None:
            continue
        try:
            r = sp.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().rstrip("/") or "/"
        except Exception:
            continue
    return None
```

Create `scripts/build_linux.sh`:

```bash
#!/usr/bin/env bash
# Build HashPlay as a single-file Linux binary.
# Produces dist/HashPlay plus a tarball for release.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(cat VERSION)"
BUILD=$(git rev-parse --short HEAD 2>/dev/null || echo nogit)

if [ -x .venv/bin/python ]; then
  PYI=(.venv/bin/python -m PyInstaller)
else
  echo "warning: .venv not found; using system python3" >&2
  PYI=(python3 -m PyInstaller)
fi

# SDL on headless CI needs a dummy driver only at RUNTIME, not build time.
"${PYI[@]}" HashPlay.spec --noconfirm

mkdir -p dist
tar -czf "dist/HashPlay-linux-x86_64-${VERSION}.tar.gz" dist/HashPlay
echo "Built: dist/HashPlay (v${VERSION}, ${BUILD})"
echo "Tarball: dist/HashPlay-linux-x86_64-${VERSION}.tar.gz"
```

Make it executable: `chmod +x scripts/build_linux.sh`.

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_crossplatform.py`
Expected: four numbered lines then `CROSSPLATFORM TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py scripts/build_linux.sh tests/test_crossplatform.py
git commit -m "feat(linux): platform-aware folder picker (zenity/kdialog) and a Linux build script"
```

---

### Task 21: Fix macOS minimum-version metadata and document both platforms

**Objective:** Stop claiming a macOS minimum the binary may not support, and document how to build each platform.

**Files:**
- Modify: `build_macos_app.sh`
- Create: `BUILDING.md`
- Test: `tests/test_macos_metadata.py`

**Step 1: Write the failing test**

Create `tests/test_macos_metadata.py`:

```python
import os, plistlib, re, sys
ROOT = "/Users/giathinh/ncs-music-launcher"

sh = open(os.path.join(ROOT, "build_macos_app.sh")).read()
print("1) deployment target is parameterised:",
      "MACOSX_DEPLOYMENT_TARGET" in sh or "LSMinimumSystemVersion" in sh)
assert "LSMinimumSystemVersion" in sh
# the minimum must come from a variable, not a literal claim
assert re.search(r"<string>11\.0</string>", sh) is None, \
    "remove the hardcoded 11.0 minimum; derive it from the build"

doc = os.path.join(ROOT, "BUILDING.md")
assert os.path.exists(doc), "need BUILDING.md"
text = open(doc).read()
for needed in ("macOS", "Linux", "python3.14", "build_venv.sh"):
    assert needed in text, f"BUILDING.md must mention {needed}"
print("2) BUILDING.md documents both platforms")

print("MACOS METADATA TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_macos_metadata.py`
Expected: FAIL — the hardcoded `11.0` assertion

**Step 3: Write minimal implementation**

In `build_macos_app.sh`, derive the minimum instead of asserting it:

```bash
VERSION="${1:-$(cat VERSION)}"
BUILD=$(git rev-parse --short HEAD 2>/dev/null || echo nogit)
# Minimum macOS the bundle claims. Keep this honest: the PyInstaller log
# rewrites the Mach-O load command to the Python framework's SDK version, so
# claiming something older than that produces a binary that fails at launch.
MACOS_MIN="${MACOS_MIN:-$(python3 -c 'import sysconfig;print(sysconfig.get_config_var("MACOSX_DEPLOYMENT_TARGET") or "11.0")' 2>/dev/null || echo 11.0)}"
MACOS_MIN="${MACOS_MIN/./}"   # 11.0 -> 11, 10.15 -> 10.15
```

and in the plist heredoc use:

```xml
    <key>LSMinimumSystemVersion</key>  <string>__MACOS_MIN__</string>
```

with a `sed` substitution right after the heredoc:

```bash
sed -i '' "s|__MACOS_MIN__|${MACOS_MIN}|g" "$APP/Contents/Info.plist"
```

Create `BUILDING.md`:

```markdown
# Building NCS-SMLauncher (HashPlay)

## Prerequisites

    brew install python@3.14          # 3.14.7 is the supported version
    brew install pyinstaller          # or use the venv below

## One-time setup

    ./scripts/build_venv.sh           # creates .venv on Python 3.14 and
                                     # installs requirements-dev.txt

## macOS (arm64)

    ./build_macos_app.sh              # -> dist/HashPlay and dist/HashPlay.app

The bundle's `LSMinimumSystemVersion` is derived from the active Python
framework's deployment target, because PyInstaller rewrites the binary's
Mach-O load command to match it. Override with `MACOS_MIN=12.0 ./build_macos_app.sh`
if you know the bundle is safe on an older release.

## Linux (x86_64)

    ./scripts/build_linux.sh          # -> dist/HashPlay and a .tar.gz

Requirements at runtime:
  * SDL2 (libsdl2) — for the window and audio
  * portaudio19 — for `sounddevice`
  * `libtorrent` is OPTIONAL; without it the torrent overlay reports
    "libtorrent missing" and everything else keeps working.

On headless machines set `SDL_VIDEODRIVER=dummy` to run without a display.

## Android

    cd android
    cp local.properties.example local.properties
    $EDITOR local.properties          # set sdk.dir
    ./gradlew assembleDebug           # -> android/app/build/outputs/apk/debug/

Toolchain: AGP 8.13.0, Kotlin 2.2.20, Gradle 8.13, compileSdk 34, JDK 17.
The app version comes from the root `VERSION` file.

## Versioning

`VERSION` at the repo root is the single source of truth. It is read by
`src/version.py`, `build_macos_app.sh`, and `android/app/build.gradle.kts`.
Bump it, commit, then tag the release `v<VERSION>`.
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_macos_metadata.py`
Expected: `MACOS METADATA TESTS PASSED`

Then verify the plist is right after a real build:

Run: `cd /Users/giathinh/ncs-music-launcher && ./build_macos_app.sh && plutil -p dist/HashPlay.app/Contents/Info.plist | grep -E "ShortVersion|BundleVersion|LSMinimum"`
Expected: `0.99.1`, the short git sha, and a non-placeholder minimum.

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add build_macos_app.sh BUILDING.md tests/test_macos_metadata.py
git commit -m "build: derive macOS minimum from the Python deployment target; document all platforms"
```

---

### Task 22: CI for macOS, Linux, and dependency staleness

**Objective:** GitHub Actions builds both platforms and opens an issue when pinned dependencies go stale.

**Files:**
- Create: `.github/workflows/build.yml`
- Create: `.github/workflows/dependencies.yml`
- Test: `tests/test_ci_config.py`

**Step 1: Write the failing test**

Create `tests/test_ci_config.py`:

```python
import os, sys
ROOT = "/Users/giathinh/ncs-music-launcher"

wf = os.path.join(ROOT, ".github/workflows/build.yml")
assert os.path.exists(wf), "need a build workflow"
text = open(wf).read()
print("1) build.yml lines:", len(text.splitlines()))
for needed in ("macos-latest", "ubuntu-latest", "build_macos_app.sh",
               "build_linux.sh", "PYTHON_VERSION"):
    assert needed in text, f"build.yml must mention {needed}"
assert "3.14" in text, "CI must build on Python 3.14"
print("2) build workflow covers macOS + Linux on 3.14")

dep = os.path.join(ROOT, ".github/workflows/dependencies.yml")
assert os.path.exists(dep), "need a dependency-staleness workflow"
dtext = open(dep).read()
print("3) dependency workflow exists")
assert "schedule" in dtext, "must run on a schedule, not just on push"
assert "pinned" in dtext.lower() or "outdated" in dtext.lower()
print("4) staleness check configured")

gi = open(os.path.join(ROOT, ".gitignore")).read()
assert "dist/" in gi
print("5) dist/ still ignored")
print("CI CONFIG TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_ci_config.py`
Expected: FAIL — `AssertionError: need a build workflow`

**Step 3: Write minimal implementation**

Create `.github/workflows/build.yml`:

```yaml
name: build

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
  workflow_dispatch:

jobs:
  macos:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
      - name: Install build deps
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements-dev.txt
      - name: Build the app bundle
        run: ./build_macos_app.sh
      - uses: actions/upload-artifact@v4
        with:
          name: HashPlay-macos-arm64
          path: |
            dist/HashPlay
            dist/HashPlay.app

  linux:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
      - name: Install SDL and portaudio
        run: |
          sudo apt-get update
          sudo apt-get install -y libsdl2-2.0-0 portaudio19-dev
      - name: Install build deps
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt
          python -m pip install pyinstaller
      - name: Build
        run: ./scripts/build_linux.sh
      - uses: actions/upload-artifact@v4
        with:
          name: HashPlay-linux-x86_64
          path: |
            dist/HashPlay
            dist/*.tar.gz
```

Create `.github/workflows/dependencies.yml`:

```yaml
name: dependency-staleness

on:
  schedule:
    - cron: "0 7 * * 1"     # Mondays 07:00 UTC
  workflow_dispatch:

permissions:
  issues: write

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
      - name: Report packages newer than our floors
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt
          python -m pip list --outdated --format=json > outdated.json || true
          cat outdated.json
      - name: Open an issue if anything is stale
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const stale = JSON.parse(fs.readFileSync('outdated.json', 'utf8') || '[]');
            if (!stale.length) { core.info('Everything is current.'); return; }
            const body = ['Pinned floors in `requirements.txt` are behind:', '',
              '| package | pinned floor | latest |', '|---|---|---|',
              ...stale.map(p => `| ${p.name} | ${p.current_version} | ${p.latest_version} |`)
            ].join('\n');
            core.issue.create({
              owner: context.repo.owner, repo: context.repo.repo,
              title: `Dependency update available (${stale.length} package(s))`,
              body
            });
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_ci_config.py`
Expected: `CI CONFIG TESTS PASSED`

Validate the YAML actually parses (a malformed workflow silently does nothing):

Run:
```bash
cd /Users/giathinh/ncs-music-launcher
python3 -c "
import sys
try:
    import yaml
except ImportError:
    print('pyyaml missing; skipping parse check'); sys.exit(0)
for f in ('.github/workflows/build.yml', '.github/workflows/dependencies.yml'):
    yaml.safe_load(open(f))
    print('parsed OK:', f)
"
```
Expected: `parsed OK` for both. If pyyaml is missing, install it into the dev venv: `.venv/bin/python -m pip install pyyaml`.

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add .github/workflows tests/test_ci_config.py
git commit -m "ci: build macOS + Linux on Python 3.14, weekly dependency-staleness issue"
```

---

### Task 23: Full regression, then rebuild everything

**Objective:** Prove nothing regressed across all 20+ tests, then produce fresh binaries for every platform.

**Files:**
- Test: everything in `tests/`

**Step 1: Run the whole suite on the 3.14 venv**

Run:
```bash
cd /Users/giathinh/ncs-music-launcher
./scripts/build_venv.sh >/dev/null
fail=0
for f in tests/test_*.py; do
  if out=$(.venv/bin/python "$f" 2>&1); then
    echo "PASS  $f"
  else
    echo "FAIL  $f"; echo "$out" | tail -12; fail=1
  fi
done
exit $fail
```
Expected: a `PASS` line for every file, exit 0. If a test only passes on system 3.13, that is a real 3.14 incompatibility to fix, not a test to relax.

**Step 2: Verify the self-updater end-to-end, for real**

Run:
```bash
cd /Users/giathinh/ncs-music-launcher
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "src")
import updater
info = updater.fetch_latest()
print("latest release seen:", info)
if info is None:
    print("PASS: no newer release published yet (correct behaviour)")
else:
    a = updater.pick_asset(info)
    print("asset for this platform:", a)
    assert a is not None, f"no asset for {sys.platform}"
    print("PASS: release parsed and platform asset selected")
PY
```
Expected: either "no newer release" or a parsed release with a platform-appropriate asset. This hits the real GitHub API.

**Step 3: Build macOS and Linux artifacts**

Run:
```bash
cd /Users/giathinh/ncs-music-launcher
./build_macos_app.sh
./scripts/build_linux.sh
ls -la dist/
```
Expected: `dist/HashPlay`, `dist/HashPlay.app`, and `dist/HashPlay-linux-x86_64-<version>.tar.gz`.

**Step 4: Smoke-test the built macOS app**

Run: `cd /Users/giathinh/ncs-music-launcher && ./dist/HashPlay ~/Downloads`
Expected: window opens, `Found N track(s)`, no traceback. Manually confirm `,` opens settings, the update row is present, and `q` quits cleanly.

**Step 5: Commit any fixes**

```bash
cd /Users/giathinh/ncs-music-launcher
git add -A
git commit -m "fix: issues found during 3.14 and cross-platform verification"
```

---

### Task 24: Publish the pre-release with all platform assets

**Objective:** Ship `v0.99.1` as a pre-release carrying the macOS app, the macOS binary, and the Linux tarball.

**Files:**
- Create: `RELEASE_NOTES_v0.99.1.md`

**Step 1: Write release notes**

Create `RELEASE_NOTES_v0.99.1.md` covering: remappable keys and the Settings panel; first-run folder setup; the library organizer; streaming playback of in-progress torrents; the three macOS/app bug fixes; the three Android fixes; the version source of truth; Python 3.14; the opt-in self-updater (state plainly that it is opt-in, checksum-verified, and installs on next restart); Linux support; and honest known issues (controller untested beyond one pad, non-mp3 partial streaming unverified, bulk move deletes originals after a byte-count check, self-updater only tested against the real API, not an actual installed build).

**Step 2: Push**

```bash
cd /Users/giathinh/ncs-music-launcher
git add -A
git commit -m "docs: release notes for v0.99.1"
git push origin main
```
Expected: `main -> main`. If rejected, `git fetch origin && git merge origin/main --no-edit`, then push again.

**Step 3: Publish the pre-release**

```bash
cd /Users/giathinh/ncs-music-launcher
gh release create v0.99.1 \
  dist/HashPlay \
  dist/HashPlay.app.zip \
  dist/HashPlay-linux-x86_64-0.99.1.tar.gz \
  --title "v0.99.1-beta" \
  --notes-file RELEASE_NOTES_v0.99.1.md \
  --prerelease
gh release view v0.99.1
```
Expected: a release URL, `prerelease: true`, and all three assets listed. Confirm the tag is **not** `v1.0`.

**Step 4: Verify the updater can see it**

Run:
```bash
cd /Users/giathinh/ncs-music-launcher
.venv/bin/python -c "
import sys; sys.path.insert(0,'src')
import updater
print('sees v0.99.1 as newer than 0.99.0:', updater.parse_release(
    {'tag_name':'v0.99.1','prerelease':True,
     'assets':[{'name':'HashPlay','size':1,
                'browser_download_url':'https://x','digest':'sha256:0'*0 or 'sha256:'+'0'*64}]},
    current='0.99.0'))
"
```
Expected: an `UpdateInfo 0.99.1` object.

**Step 5: Final check**

Run:
```bash
cd /Users/giathinh/ncs-music-launcher
git status --porcelain
git log --oneline -5
gh release list
```
Expected: clean tree, the release commit on top, `v0.99.1` listed as a pre-release.

---

## Tests / Validation

| Test | Covers | Command |
|---|---|---|
| `test_version.py` | VERSION is semver, parsed, propagated to build + Android | `python3 tests/test_version.py` |
| `test_requirements.py` | all runtime deps declared with version floors | `python3 tests/test_requirements.py` |
| `test_migrations.py` | legacy config upgraded, idempotent, non-destructive | `python3 tests/test_migrations.py` |
| `test_updater.py` | release parsing, version compare, platform asset, checksum | `python3 tests/test_updater.py` |
| `test_apply_pending.py` | staged install needs confirmation, never in-place | `python3 tests/test_apply_pending.py` |
| `test_android_config.py` | real gradlew, untracked local.properties, AGP 8.x bumps | `python3 tests/test_android_config.py` |
| `test_crossplatform.py` | platform-aware picker, Linux script, guarded Quartz | `python3 tests/test_crossplatform.py` |
| `test_macos_metadata.py` | derived minimum, BUILDING.md | `python3 tests/test_macos_metadata.py` |
| `test_ci_config.py` | both-platform workflow, scheduled staleness check | `python3 tests/test_ci_config.py` |
| all prior tests | tasks 1-13 features | see the previous plan |

No pytest — plain scripts, run on the 3.14 venv.

---

## Risks, Tradeoffs, and Open Questions

**Risks**

1. **The self-updater is the only genuinely dangerous piece here.** It downloads and executes a replacement for your own application. Mitigations are structural, not optional: opt-in only, explicit confirm before staging, size + SHA-256 verified against GitHub's release metadata, staged outside the bundle, and installed by a detached helper that waits on the current PID. It is also worth being blunt: a supply-chain compromise of the GitHub account would serve a malicious build that passes the checksum, because the checksum comes from the same account. That is inherent to self-updating from your own release feed, not a flaw in this design.
2. **Python 3.14 may lack wheels for a dependency.** `python@3.14` is installed but every app dependency is currently resolved under 3.13. Task 15 Step 5 exists specifically to catch this by actually importing everything on 3.14. If something has no wheel, the fallback is a floor bump or an explicit upper pin with a comment — not silently reverting to 3.13.
3. **The helper script's `mv` rollback is manual, not automatic.** It moves the old bundle to `.old` rather than deleting it, so a failed install leaves a recoverable copy, but nothing retries.
4. **AGP 8.13.0 may want a build-tools newer than 34.0.0.** Only `android-34` and `build-tools 34.0.0` are installed, and the decision was explicitly "no SDK downloads". Task 19 Step 5 gives the fallback: drop to the newest AGP 8.x that accepts 34.0.0 and record it. The config test asserts `>= 8.13`, so lower that assertion if the fallback is taken.
5. **`gradlew` fetched from `raw.githubusercontent.com` is a network dependency at build time.** It is pinned to the `v8.13.0` tag, but a first run in CI still downloads it. A more conservative option is to keep the wrapper jar and generate the script with `./gradlew wrapper --gradle-version 8.13` once the jar works.
6. **libtorrent on Linux is genuinely awkward to install.** The app already treats it as optional and says so, but a Linux user may need conda or a third-party wheel index. This should be stated in the release notes rather than discovered at runtime.
7. **`pip list --outdated` needs network and will list everything**, not just our floors; the workflow reports all of it, which is noisy but harmless.

**Tradeoffs**

- *Single VERSION file* adds a file read at import time. It handles the frozen-bundle case via `sys._MEIPASS` and falls back to `0.0.0` rather than crashing, but a broken bundle would silently report version 0.0.0.
- *Staged updates* mean a restart is required. That is the cost of not replacing a running binary in place, and it is the right trade.
- *Conservative Android* means knowingly staying behind AGP 9.1.1, which brings built-in Kotlin support. That is a deliberate deferral, not an oversight.
- *Weekly staleness issue* is noisier than a silent Dependabot PR, but it does not require granting write scopes to third-party actions.

**Open Questions**

1. **Should the self-updater also work for the Android build?** Not possible in the same way — Android updates go through the Play Store or sideloaded APKs. Current scope is desktop only; an in-app "check for a newer APK" would need a distribution channel first.
2. **Should bulk move be copy instead of move?** Still open from the previous plan. Currently it moves and deletes originals after a byte-count check.
3. **Should CI publish releases automatically on a tag push, or stay manual?** The workflow currently builds and uploads artifacts; `gh release create` is still a manual step, which avoids surprises on a tag you did not mean to cut.
4. **Do you want `main` protected?** The repo currently has no branch protection I checked, so a push can go straight to `main`. Worth adding before this gets automated.
