# NCS-SMLauncher v0.99.x — Remappable Keys, Streaming Playback While Downloading, Settings, and Publish

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make every app action rebindable to any key, make a still-downloading torrent playable while its bytes arrive, add a settings screen with a music-library organizer and a control-hints toggle, fix three latent bugs found during investigation, then rebuild the macOS app and publish a new pre-release.

**Architecture:** Three new modules — `src/config.py` (JSON settings), `src/actions.py` (action registry + keymap), `src/streaming_source.py` (incremental decode) — plus `src/library_ops.py` (safe bulk move) and `src/settings_panel.py` (keyboard-driven overlay). The hardcoded `elif event.key == pygame.K_x` chain in `main()` collapses into a `dispatch_action(name)` table so rebinding is possible at all. `Player` gains a streaming mode that appends newly-arrived audio via a decode cursor instead of snapshotting the file. The app already bundles via PyInstaller with `pathex=['src']`, so new sibling modules are picked up with no spec change.

**Tech Stack:** Python 3.13, pygame 2.6.1, numpy, miniaudio, mutagen, libtorrent 2.1.1.0, PyInstaller 6.22.2, `gh` CLI 2.100.0 (authenticated as Giathinh1234). Tests are **plain scripts run with `python3 tests/<file>.py`** — pytest is NOT installed and must not be assumed.

---

## Current Context / Assumptions

Working tree `/Users/giathinh/ncs-music-launcher`, 1379 lines in `src/ncs_launcher.py`, HEAD `73a7864`, app not currently running, working tree clean except the untracked plan from the previous session.

**What exists today**

- Key handling is a hardcoded `elif` chain at `src/ncs_launcher.py:1137-1189`, comparing raw pygame constants. Nothing is configurable or persisted.
- **No config file exists anywhere.** A search for `json.load`/`json.dump`/`.json` across `src/` returns nothing; `import json` (line 36) is unused. There is no `src/config.py`.
- `Player.load()` (line 229) calls `decode_file()` (line 186) → `miniaudio.decode_file()`, materialising the whole file into a float32 array before playback.
- `player.load(...)` appears at **15 call sites** (lines 1022, 1070, 1076, 1151, 1159, 1164, 1202, 1207, 1211, 1227, 1235, 1240, 1275, 1295, 1312) plus a sync site at 1317. Every one would need the streaming path — a strong argument for collapsing them into one helper.
- `src/media_keys.py:110-114` runs the Quartz tap on `threading.Thread(target=run, daemon=True)`, and `media_key_handler` (line 1056) calls `player.load()` directly from that thread — a genuine race with the main loop.
- `TorrentManager` (line 93) sets `params.save_path = TORRENT_DIR` (line 140); `TORRENT_DIR = APP_DIR/torrent-downloads` (line 81).
- `HashPlay.spec` uses `pathex=['src']` and `hiddenimports=collect_submodules('libtorrent') + ['sounddevice','miniaudio']`. New modules in `src/` are bundled automatically — **no spec edit needed**.
- `build_macos_app.sh` builds `dist/HashPlay` then wraps it into `dist/HashPlay.app` and ad-hoc codesigns. Note `Info.plist` hardcodes `CFBundleShortVersionString` = `1.0`, which is now wrong for a pre-release.
- Release `v0.99` exists as a **pre-release** (tag `v0.99`, title `v0.99-beta`). `gh` is authenticated.

**Empirically verified findings — I ran these; the plan depends on them**

1. **`decode_file()` on a truncated MP3 does NOT raise.** It silently returns only the decodable prefix (25% of bytes → 2.95 s; 50% → 5.96 s). So the current mid-download failure is *silence after the prefix*, not a crash — `Player` holds a short array, hits the end, and parks at `pos = len(samples) - 1` forever. Design for the symptom, not for an exception.
2. **`mp3_stream_file(filename, frames_to_read=1024, seek_frame=0)`** (note: no `output_format`/`nchannels` kwargs — passing them raises `TypeError`, which cost me a probe) streams a growing MP3 and grows monotonically as bytes are appended.
3. **Stereo files yield interleaved samples.** Measured on a real 2-channel file: `stream ints / (2 × decode_file mono samples) = 1.000`. `mp3_stream_file` emits the file's native channel count, so **a downmix is mandatory** or playback is double-speed noise.
4. **The cursor design is sound and bit-identical to a full decode.** Simulating a download arriving in 4 stages (15% → 40% → 70% → 100%) and appending via `seek_frame=<frames already held>` reproduced the full `decode_file` mono output exactly: `10516655` vs `10516655` samples, max abs difference `1.5e-05` (float32 rounding). This is the core of Task 4 and it is now proven, not assumed.
5. **Cost is not the problem I feared.** Re-decoding from byte 0 vs. cursor-seek: `0.093 s` vs `0.089 s` on a 2 MB half-file — a 1.0× speedup, i.e. no benefit. The generator has to scan headers from the start regardless. So **use the simple cursor design, refreshed on a timer**; do not build a seek-optimised incremental decoder. Simpler and equally fast at this scale.
6. `mutagen.mp3.MP3()` on a truncated file still returns the ID3 title and reports the **full** duration, so metadata display is correct mid-download.
7. `music/test.mp3` in the repo is a **0-byte placeholder** and cannot be decoded. Tests needing real audio must build one with `ffmpeg` (at `/opt/homebrew/bin/ffmpeg`) from `/Users/giathinh/Downloads/A Thousand Miles.mp3` (2-channel, 4.1 MB, 238.5 s).

**Three latent bugs found while investigating (Tasks 1-3)**

8. **Real bug — `start_text_input()` is called twice (lines 1141, 1185) and `stop_text_input()` never.** Text-input mode leaks on permanently. I tested whether this breaks normal key delivery: it does **not** — `KEYDOWN` still arrives with `unicode` set while IME is active. So this is a resource/cleanliness leak, not a functional break, and must not be described as breaking key remapping. It still needs fixing because with a rebinding system the app must be able to hand keys back to the app cleanly.
9. **Real bug — cross-thread `player.load()`.** The media-key handler loads audio from the Quartz thread while the main loop may also load. This must be marshalled through a queue.
10. **Real (minor) bug — dead code in the sphere.** `energy = float(np.mean(mag))` at line 334 is computed and never used; `amp` uses `bass` only. `draw_visualizer` also computes `mag` at line 402 and then discards it for the `radial` branch. Both should be removed.
11. I checked and the app bundle's `Resources` dir happens to be writable today, but that is an accident of this checkout. Config still belongs in `~/.ncs-smlauncher/` so a frozen, moved, or read-only install never fails to save.

---

## Proposed Approach

Land the foundation first (`config`, `actions`), because Settings needs to both edit the keymap and toggle `show_hints`. Then the bug fixes, which shrink the `player.load` call sites from 15 to 1 and make the streaming path trivial to wire everywhere. Then `streaming_source` + the `Player` streaming mode, which the library-organizer work does not depend on. Then the organizer and settings UI. Finally rebuild and publish, because shipping a broken binary is worse than shipping late.

---

## Step-by-Step Tasks

### Task 1: Fix the dead `energy` variable and the leaked text-input mode

**Objective:** Remove the unused `energy` computation and balance `start_text_input()` with `stop_text_input()`, so the app hands keyboard control back cleanly.

**Files:**
- Modify: `src/ncs_launcher.py:334` (drop `energy`)
- Modify: `src/ncs_launcher.py:402` (`draw_visualizer`: skip `player.spectrum()` for the sphere branch)
- Modify: `src/ncs_launcher.py:1141,1185` plus the overlay/chat close paths
- Test: `tests/test_text_input_balance.py`

**Step 1: Write the failing test**

Create `tests/test_text_input_balance.py`:

```python
"""Text input must be started and stopped in balance, and the sphere must not
compute an unused variable."""
import os, re, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

SRC = open("/Users/giathinh/ncs-music-launcher/src/ncs_launcher.py").read()

starts = len(re.findall(r"pygame\.key\.start_text_input\(\)", SRC))
stops = len(re.findall(r"pygame\.key\.stop_text_input\(\)", SRC))
print("1) start=%d stop=%d" % (starts, stops))
assert starts > 0, "sanity: app should start text input somewhere"
assert starts == stops, "start/stop text input must be balanced (%d != %d)" % (starts, stops)

# 'energy' must not linger as a dead assignment
dead = re.search(r"^\s*energy\s*=\s*float\(np\.mean\(mag\)\)", SRC, re.M)
print("2) dead 'energy' assignment present:", bool(dead))
assert not dead, "remove the unused energy assignment"

print("TEXT INPUT BALANCE TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_text_input_balance.py`
Expected: FAIL — `AssertionError: start/stop text input must be balanced (2 != 0)`

**Step 3: Write minimal implementation**

In `src/ncs_launcher.py`, delete line 334 entirely:

```python
    bass = float(np.mean(mag[:10])) if len(mag) >= 10 else 0.0
    amp = 0.06 + 0.20 * bass
```

(replacing the existing `energy = ...` + `bass = ...` pair; `amp` already only uses `bass`.)

In `draw_visualizer`, only pay for the FFT when a mode actually uses it:

```python
def draw_visualizer(screen, player, w, h, mode, t, current_track_metadata):
    if mode == "radial":
        # The real NCS ball draws itself; it runs its own spectrum pass
        draw_ncs_sphere(screen, player, w, h, t)
        return
    mag = player.spectrum()
    cx, cy = w // 2, h // 2
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    base_y = h - 90

    if mode == "disc":
```

and delete the old `if mode == "radial":` block that used to call `draw_ncs_sphere` followed by `elif mode == "disc":` (now `if mode == "disc":`).

Balance text input at the four open/close sites:

```python
                elif event.key == pygame.K_t:
                    if not overlay_open:
                        overlay_open = True
                        pygame.key.start_text_input()
```

```python
                if overlay_open:
                    if event.key == pygame.K_ESCAPE:
                        overlay_open = False
                        overlay_text = ""
                        pygame.key.stop_text_input()
                    elif event.key == pygame.K_RETURN and overlay_text.strip():
                        ok = torrents.start_download(overlay_text.strip()) \
                            if torrents else False
                        if not torrents:
                            push_notice(("error",
                                         "libtorrent missing: pip install libtorrent"))
                        elif ok:
                            overlay_text = ""
                            overlay_open = False
                            pygame.key.stop_text_input()
                        # keep the overlay open on failure so the error shows
```

```python
                elif event.key == pygame.K_c:
                    if chat_open:
                        chat_open = False
                        pygame.key.stop_text_input()
                    else:
                        chat_open = True
                        pygame.key.start_text_input()
```

```python
                if chat_open:
                    if chat_panel.handle_key(event) == "close":
                        chat_open = False
                        pygame.key.stop_text_input()
                    continue
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_text_input_balance.py`
Expected: `TEXT INPUT BALANCE TESTS PASSED`

Then confirm no visual regression: `python3 tests/test_sphere_and_chat.py` → expect `ALL CHECKS PASSED`, and `python3 tests/test_live_loop.py` → expect `PASS: resize + chat key routing did not crash the loop`.

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py tests/test_text_input_balance.py
git commit -m "fix: remove dead energy var, skip redundant spectrum pass, balance start/stop_text_input"
```

---

### Task 2: Create `src/config.py`

**Objective:** JSON settings with sane defaults, tolerant of missing/corrupt files, stored in the user's home.

**Files:**
- Create: `src/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import copy, json, os, sys, tempfile
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import config as cfgmod


def test_defaults_when_missing():
    with tempfile.TemporaryDirectory() as d:
        c = cfgmod.load_config(os.path.join(d, "s.json"))
        assert c["library_folder"] == ""
        assert c["show_hints"] is True
        assert c["keymap"]["play_pause"] == "SPACE"
    print("1) defaults OK")


def test_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        c = cfgmod.load_config(p)
        c["show_hints"] = False
        c["keymap"]["play_pause"] = "K"
        assert cfgmod.save_config(c, p) is True
        c2 = cfgmod.load_config(p)
        assert c2["show_hints"] is False
        assert c2["keymap"]["play_pause"] == "K"
    print("2) roundtrip OK")


def test_corrupt_falls_back():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        open(p, "w").write("{not json")
        c = cfgmod.load_config(p)
        assert c["show_hints"] is True
        assert c["keymap"]["play_pause"] == "SPACE"
    print("3) corrupt fallback OK")


def test_unknown_keys_preserved():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        json.dump({"future": 7}, open(p, "w"))
        assert cfgmod.load_config(p)["future"] == 7
    print("4) unknown keys preserved OK")


def test_save_is_atomic_no_tmp_left():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        cfgmod.save_config(cfgmod.default_config(), p)
        assert not os.path.exists(p + ".tmp")
        assert os.path.exists(p)
    print("5) atomic save leaves no .tmp OK")


def test_defaults_are_not_shared():
    a = cfgmod.default_config()
    a["keymap"]["play_pause"] = "Z"
    b = cfgmod.default_config()
    assert b["keymap"]["play_pause"] == "SPACE", "mutable default leaked"
    print("6) defaults not shared OK")


for f in (test_defaults_when_missing, test_roundtrip, test_corrupt_falls_back,
          test_unknown_keys_preserved, test_save_is_atomic_no_tmp_left,
          test_defaults_are_not_shared):
    f()
print("CONFIG TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_config.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`

**Step 3: Write minimal implementation**

Create `src/config.py`:

```python
"""JSON-backed user settings for NCS-SMLauncher.

Stored under the user's home, NOT next to the source: a frozen PyInstaller
bundle can be moved, read-only, or unwritable, and settings must always save.
"""
import copy
import json
import os

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".ncs-smlauncher")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")

# action name -> default pygame key NAME (a string, so the file stays
# human-editable and portable). Resolved to a pygame constant at runtime.
DEFAULT_KEYMAP = {
    "select_prev": "UP",
    "select_next": "DOWN",
    "play_pause": "SPACE",
    "play": "RETURN",
    "seek_back": "LEFT",
    "seek_forward": "RIGHT",
    "toggle_mute": "M",
    "cycle_visualizer": "F",
    "open_hermes": "C",
    "open_torrent": "T",
    "open_folder": "O",
    "open_settings": "COMMA",
    "quit": "Q",
}

DEFAULT_CONFIG = {
    "library_folder": "",
    "show_hints": True,
    "easter_eggs": True,
    "keymap": {},          # filled from DEFAULT_KEYMAP in default_config()
}


def default_config():
    """A fresh deep copy — never hand out the shared DEFAULT_CONFIG."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["keymap"] = dict(DEFAULT_KEYMAP)
    return cfg


def _merge(base, override):
    """Merge one nested level (keymap); pass through any unknown keys."""
    out = copy.deepcopy(base)
    if not isinstance(override, dict):
        return out
    for k, v in override.items():
        if k == "keymap" and isinstance(v, dict):
            merged = dict(out.get("keymap", {}))
            merged.update(v)
            out["keymap"] = merged
        else:
            out[k] = v
    return out


def load_config(path=None):
    """Load settings; fall back to defaults on missing or corrupt files."""
    path = path or CONFIG_PATH
    cfg = default_config()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return _merge(cfg, json.load(fh))
    except (OSError, ValueError):
        return cfg


def save_config(cfg, path=None):
    """Write settings atomically. Returns True on success."""
    path = path or CONFIG_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print(f"could not save config to {path}: {e}")
        return False
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_config.py`
Expected: six `OK` lines then `CONFIG TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/config.py tests/test_config.py
git commit -m "feat(config): JSON settings in ~/.ncs-smlauncher with atomic save and corrupt fallback"
```

---

### Task 3: Create `src/actions.py` — action registry and keymap

**Objective:** One table of actions, and a `Keymap` that resolves a pygame key constant to an action name.

**Files:**
- Create: `src/actions.py`
- Test: `tests/test_actions.py`

**Step 1: Write the failing test**

Create `tests/test_actions.py`:

```python
import os, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import pygame
pygame.init()
pygame.display.set_mode((320, 240))
import actions


def test_defaults_roundtrip():
    km = actions.Keymap({})
    bad = []
    for name, key_name in actions.DEFAULT_KEYMAP.items():
        got = km.resolve(actions.key_to_constant(key_name))
        if got != name:
            bad.append((key_name, got, name))
    print("1) mismatched defaults:", bad)
    assert not bad
    print("1) every default key resolves to its action")


def test_rebind_releases_old_key():
    km = actions.Keymap({"play_pause": "K"})
    assert km.resolve(pygame.K_k) == "play_pause"
    assert km.resolve(pygame.K_SPACE) is None
    print("2) rebind releases the old key")


def test_duplicate_last_wins():
    km = actions.Keymap({"seek_back": "J", "seek_forward": "J"})
    assert km.resolve(pygame.K_j) == "seek_forward"
    print("3) duplicate key: last binding wins")


def test_reset():
    km = actions.Keymap({"play_pause": "K"})
    km.reset_to_defaults()
    assert km.resolve(pygame.K_SPACE) == "play_pause"
    assert km.resolve(pygame.K_k) is None
    print("4) reset restores defaults")


def test_unknown_key_name_ignored():
    km = actions.Keymap({"play_pause": "TOTALLY_NOT_A_KEY"})
    assert km.resolve(pygame.K_SPACE) is None
    assert km.bindings["play_pause"] == "SPACE", "bad name must not clobber"
    print("5) unknown key name is ignored, default kept")


def test_conflicts():
    km = actions.Keymap({"seek_back": "J", "seek_forward": "J"})
    c = km.conflicts()
    print("6) conflicts:", c)
    assert len(c) == 1
    assert set(c[0]) == {"seek_back", "seek_forward"}


def test_labels():
    for a in actions.ACTIONS.values():
        assert a["label"] and a["group"]
    assert actions.human_key_name("SPACE") == "Space"
    assert actions.human_key_name("K") == "K"
    assert actions.human_key_name("") == "-"
    print("7) labels + human key names OK")


def test_every_action_in_default_keymap():
    assert set(actions.ACTIONS) == set(actions.DEFAULT_KEYMAP), \
        "ACTIONS and DEFAULT_KEYMAP must cover the same names"
    print("8) registry and keymap cover identical actions")


for f in (test_defaults_roundtrip, test_rebind_releases_old_key,
          test_duplicate_last_wins, test_reset, test_unknown_key_name_ignored,
          test_conflicts, test_labels, test_every_action_in_default_keymap):
    f()
print("ACTIONS TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_actions.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions'`

**Step 3: Write minimal implementation**

Create `src/actions.py`:

```python
"""Abstract action registry + keyboard mapping.

The app never compares raw key constants. It resolves a key to an action name
and dispatches on that name, which is what makes rebinding possible at all.
"""
import pygame

from config import DEFAULT_KEYMAP

# action name -> {"label": human label, "group": section}
ACTIONS = {
    "select_prev":      {"label": "Previous track",   "group": "Playback"},
    "select_next":      {"label": "Next track",       "group": "Playback"},
    "play_pause":       {"label": "Play / pause",     "group": "Playback"},
    "play":             {"label": "Play (if paused)", "group": "Playback"},
    "seek_back":        {"label": "Seek -5s",         "group": "Playback"},
    "seek_forward":     {"label": "Seek +5s",         "group": "Playback"},
    "toggle_mute":      {"label": "Mute",             "group": "Playback"},
    "cycle_visualizer": {"label": "Cycle visualizer", "group": "View"},
    "open_hermes":      {"label": "Hermes chat",      "group": "View"},
    "open_torrent":     {"label": "Torrent overlay",  "group": "Library"},
    "open_folder":      {"label": "Change folder",    "group": "Library"},
    "open_settings":    {"label": "Settings",         "group": "Library"},
    "quit":             {"label": "Quit",             "group": "App"},
}

DEFAULT_KEYMAP = dict(DEFAULT_KEYMAP)     # re-exported for tests

_PRETTY = {"RETURN": "Enter", "SPACE": "Space", "ESCAPE": "Esc",
           "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
           "COMMA": ",", "PERIOD": "."}

# keys we refuse to bind (they are handled structurally, not as actions)
_UNBINDABLE = {"LSHIFT", "RSHIFT", "LCTRL", "RCTRL", "LALT", "RALT",
               "LGUI", "RGUI", "LCAPS", "SCROLLOCK", "NUMLOCK",
               "RMETA", "LMETA"}


def key_to_constant(key_name):
    """'SPACE' -> pygame.K_SPACE, or None if unknown/unbindable."""
    if not key_name:
        return None
    up = str(key_name).upper()
    if up in _UNBINDABLE:
        return None
    const = getattr(pygame, "K_" + up, None)
    return const if isinstance(const, int) else None


def constant_to_key_name(const):
    """pygame.K_k -> 'K'. Returns None for modifiers."""
    for attr in dir(pygame):
        if attr.startswith("K_") and getattr(pygame, attr) == const:
            name = attr[2:]
            return None if name in _UNBINDABLE else name
    return None


def human_key_name(key_name):
    """'SPACE' -> 'Space'; falls back to a tidy form."""
    if not key_name:
        return "-"
    up = str(key_name).upper()
    return _PRETTY.get(up, up.capitalize() if len(up) > 1 else up)


class Keymap:
    """Resolves pygame key constants to action names."""

    def __init__(self, bindings=None):
        self.bindings = dict(DEFAULT_KEYMAP)
        if bindings:
            for action, key_name in bindings.items():
                # ignore unknown actions AND unresolvable key names
                if action in DEFAULT_KEYMAP and key_to_constant(key_name):
                    self.bindings[action] = key_name
        self._lookup = {}
        self._rebuild()

    def _rebuild(self):
        self._lookup = {}
        for action, key_name in self.bindings.items():
            const = key_to_constant(key_name)
            if const is not None:
                self._lookup[const] = action        # last write wins

    def resolve(self, key_constant):
        """pygame key constant -> action name, or None if unbound."""
        return self._lookup.get(key_constant)

    def bind(self, action, key_name):
        if action not in DEFAULT_KEYMAP:
            raise KeyError(f"unknown action {action!r}")
        if key_to_constant(key_name) is None:
            raise ValueError(f"cannot bind {action} to {key_name!r}")
        self.bindings[action] = key_name
        self._rebuild()

    def reset_to_defaults(self):
        self.bindings = dict(DEFAULT_KEYMAP)
        self._rebuild()

    def conflicts(self):
        """[(action_a, action_b), ...] pairs sharing one key constant."""
        seen, out = {}, []
        for action, key_name in self.bindings.items():
            const = key_to_constant(key_name)
            if const is None:
                continue
            if const in seen:
                out.append((seen[const], action))
            else:
                seen[const] = action
        return out
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_actions.py`
Expected: eight numbered `OK` lines then `ACTIONS TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/actions.py tests/test_actions.py
git commit -m "feat(actions): abstract action registry and runtime-rebindable keymap"
```

---

### Task 4: Create `src/streaming_source.py` — incremental decode of a growing file

**Objective:** Decode a file that is still being written, appending only newly-available audio via a frame cursor, with a mandatory stereo downmix.

**Files:**
- Create: `src/streaming_source.py`
- Test: `tests/test_streaming_source.py`

**Step 1: Write the failing test**

Create `tests/test_streaming_source.py`:

```python
import os, shutil, subprocess, sys, tempfile
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import numpy as np
import miniaudio
from streaming_source import StreamingSource, SAMPLE_RATE

REAL = "/Users/giathinh/Downloads/A Thousand Miles.mp3"   # 2-channel, 4.1MB


def make_source(tmp):
    """A real short stereo mp3, plus its bytes."""
    small = os.path.join(tmp, "src.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-ss", "30", "-t", "12",
                    "-i", REAL, "-ar", "44100", "-b:a", "128k", small],
                   check=True)
    return small, open(small, "rb").read()


with tempfile.TemporaryDirectory() as tmp:
    small, data = make_source(tmp)
    channels = miniaudio.get_file_info(small).nchannels
    print("1) source channels:", channels)
    assert channels == 2, "this test is designed around a stereo file"

    # ---- ground truth: full mono decode of the complete file ----
    truth = miniaudio.decode_file(small, nchannels=1, sample_rate=SAMPLE_RATE,
            output_format=miniaudio.SampleFormat.SIGNED16)
    truth_mono = np.frombuffer(truth.samples, dtype=np.int16).astype(np.float32) / 32768.0
    print("2) ground truth mono samples:", len(truth_mono))

    # ---- incremental: file arrives in 4 stages ----
    part = os.path.join(tmp, "growing.mp3")
    src = StreamingSource(part)
    prev = 0
    for frac in (0.15, 0.4, 0.7, 1.0):
        with open(part, "wb") as fh:
            fh.write(data[: int(len(data) * frac)])
        added = src.pump()
        total = len(src.samples())
        print("3) %3d%% -> +%8d samples, total=%8d (%.2fs)"
              % (int(frac * 100), added, total, total / SAMPLE_RATE))
        assert total >= prev, "buffer must never shrink"
        prev = total

    got = src.samples()
    print("4) incremental=%d vs truth=%d" % (len(got), len(truth_mono)))
    n = min(len(got), len(truth_mono))
    diff = float(np.abs(got[:n] - truth_mono[:n]).max())
    print("5) max abs sample diff:", diff)
    assert len(got) == len(truth_mono), "sample count must match exactly"
    assert diff < 1e-4, "incremental decode diverged from full decode"
    assert got.dtype == np.float32 and got.ndim == 1

    # ---- completion detection ----
    assert src.is_complete(expected_size=len(data))
    assert not src.is_complete(expected_size=len(data) * 2)
    print("6) is_complete OK")

    # ---- pumping a complete file is a no-op ----
    before = len(src.samples())
    again = src.pump()
    print("7) extra pump added:", again)
    assert again == 0 and len(src.samples()) == before

    # ---- a truncated mp3 must not raise ----
    part2 = os.path.join(tmp, "part.mp3")
    with open(part2, "wb") as fh:
        fh.write(data[: len(data) // 4])
    s2 = StreamingSource(part2)
    assert s2.pump() > 0
    print("8) truncated file decodes without raising:", len(s2.samples()))

print("STREAMING SOURCE TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_streaming_source.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'streaming_source'`

**Step 3: Write minimal implementation**

Create `src/streaming_source.py`:

```python
"""Incremental decode of a file that is still being written (torrent).

Why this exists: `miniaudio.decode_file` returns only the decodable prefix of
a partial file (verified: no exception, just truncated audio), so a
half-downloaded track plays its first few seconds and then goes silent.

`mp3_stream_file` yields the file's NATIVE channel count (measured: 2x the mono
sample count on a stereo file), so a downmix is mandatory. We keep a cursor of
how many PCM frames we already hold and pass it as `seek_frame`, so each pump
appends only the new tail. Verified bit-identical to a full decode
(max abs diff 1.5e-05, float32 rounding).
"""
import os

import numpy as np

SAMPLE_RATE = 44100


class StreamingSource:
    """A file whose decodable length grows over time."""

    def __init__(self, path, kind=None):
        self.path = path
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        self.kind = kind or ("mp3" if ext == "mp3" else "generic")
        self.channels = self._probe_channels()
        self._buf = np.zeros(0, dtype=np.float32)
        self._decoded_bytes = 0

    # ---- introspection -------------------------------------------------
    def _probe_channels(self):
        """Native channel count; defaults to 1 if the file is unreadable."""
        try:
            import miniaudio
            info = miniaudio.get_file_info(self.path)
            return max(1, int(info.nchannels))
        except Exception:
            return 1

    def size_on_disk(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def is_complete(self, expected_size=None, path=None):
        """True once the file on disk has reached the torrent's final size."""
        if expected_size is None:
            return True
        try:
            return os.path.getsize(path or self.path) >= expected_size
        except OSError:
            return False

    def samples(self):
        return self._buf

    def available_duration(self):
        return len(self._buf) / SAMPLE_RATE

    # ---- decoding ------------------------------------------------------
    def _iter_mono(self, seek_frame):
        """Yield mono float32 blocks starting at `seek_frame` PCM frames."""
        import miniaudio
        if self.kind == "mp3":
            stream = miniaudio.mp3_stream_file(self.path, frames_to_read=8192,
                                               seek_frame=seek_frame)
        else:
            stream = miniaudio.stream_file(
                self.path,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=self.channels, sample_rate=SAMPLE_RATE,
                frames_to_read=8192, seek_frame=seek_frame)
        for block in stream:
            if not block:
                continue
            arr = np.frombuffer(block, dtype=np.int16).astype(np.float32) / 32768.0
            if self.channels > 1 and arr.size % self.channels == 0:
                arr = arr.reshape(-1, self.channels).mean(axis=1)
            yield arr

    def pump(self, max_seconds=300.0):
        """Append newly-available audio. Returns samples added."""
        have = self.size_on_disk()
        if have <= self._decoded_bytes:
            return 0
        cursor_frames = len(self._buf)          # mono frames already held
        added = 0
        try:
            for arr in self._iter_mono(cursor_frames):
                if arr.size:
                    self._buf = np.concatenate([self._buf, arr])
                    added += arr.size
                if added / SAMPLE_RATE > max_seconds:
                    break
        except Exception:
            # a torn tail can raise mid-stream; keep whatever decoded cleanly
            pass
        self._decoded_bytes = have
        return added

    def decode_all(self):
        total = 0
        while True:
            n = self.pump()
            total += n
            if n == 0:
                return total
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_streaming_source.py`
Expected:
```
1) source channels: 2
2) ground truth mono samples: 529200
3)  15% -> ... total=...
...
4) incremental=529200 vs truth=529200
5) max abs sample diff: 1.5e-05-ish
6) is_complete OK
7) extra pump added: 0
8) truncated file decodes without raising: ...
STREAMING SOURCE TESTS PASSED
```

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/streaming_source.py tests/test_streaming_source.py
git commit -m "feat(audio): StreamingSource appends via decode cursor with stereo downmix"
```

---

### Task 5: Collapse the 15 `player.load` sites and add `Player` streaming mode

**Objective:** One `load_track()` helper, the media-key thread marshalled through a queue, and a `Player` that can stream an incomplete file.

**Files:**
- Modify: `src/ncs_launcher.py:186-266` (`decode_file`, `Player`)
- Modify: `src/ncs_launcher.py` — import `StreamingSource`; replace the 15 `player.load(...)` call sites
- Test: `tests/test_player_streaming.py`
- Test: `tests/test_load_sites.py`

**Step 1: Write the failing tests**

Create `tests/test_player_streaming.py`:

```python
import os, subprocess, sys, tempfile
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
from ncs_launcher import Player, decode_file

SAMPLE_RATE = 44100
REAL = "/Users/giathinh/Downloads/A Thousand Miles.mp3"


def make_partial(tmp, frac):
    small = os.path.join(tmp, "src.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-ss", "30", "-t", "12",
                    "-i", REAL, "-ar", "44100", "-b:a", "128k", small],
                   check=True)
    data = open(small, "rb").read()
    part = os.path.join(tmp, "growing.mp3")
    with open(part, "wb") as fh:
        fh.write(data[: int(len(data) * frac)])
    return part, data


p = Player()          # never start the thread; no audio device required
print("1) is_streaming attr:", hasattr(p, "is_streaming"))
assert hasattr(p, "is_streaming")

with tempfile.TemporaryDirectory() as d:
    path, data = make_partial(d, 0.3)

    p.load(path, expected_size=len(data), streaming=True)
    print("2) is_streaming:", p.is_streaming)
    assert p.is_streaming
    d0 = p.duration()
    print("3) playable duration %.2fs of 12.00s total" % d0)
    assert 1.0 < d0 < 12.0, "partial file exposes only what is decodable"

    with open(path, "ab") as fh:
        fh.write(data[int(len(data) * 0.3):])
    grew = p.refresh()
    d1 = p.duration()
    print("4) after growth: grew=%s duration=%.2fs" % (grew, d1))
    assert grew and d1 > d0, "refresh() must extend the playable buffer"

    with open(path, "ab") as fh:
        fh.write(data[int(len(data) * 0.3):])
    p.refresh()
    print("5) complete: %.2fs, is_streaming=%s" % (p.duration(), p.is_streaming))
    assert abs(p.duration() - 12.0) < 0.5
    assert not p.is_streaming, "must stop streaming once the file is complete"

    # full decode still works for ordinary complete files
    small = os.path.join(d, "src.mp3")
    p.load(small)
    print("6) complete-file load: %.2fs, is_streaming=%s" % (p.duration(), p.is_streaming))
    assert not p.is_streaming
    assert abs(p.duration() - 12.0) < 0.5

# a file that cannot be decoded at all must raise, not silently do nothing
try:
    decode_file("/Users/giathinh/ncs-music-launcher/music/test.mp3")
    print("7) FAIL: 0-byte file decoded without error")
    raise SystemExit(1)
except Exception as e:
    print("7) 0-byte file raises:", type(e).__name__)

print("PLAYER STREAMING TESTS PASSED")
```

Create `tests/test_load_sites.py`:

```python
"""Every track load must go through the single load_track() helper."""
import re, sys

SRC = open("/Users/giathinh/ncs-music-launcher/src/ncs_launcher.py").read()
main_src = SRC[SRC.index("def main():"]

# exactly one raw player.load outside the helper's definition
calls = re.findall(r"player\.load\(", SRC)
print("1) raw player.load( occurrences:", len(calls))
assert len(calls) == 1, f"collapse to a single call site, found {len(calls)}"

# no load() may happen on the media-key thread
assert "pending_load" in SRC, "media-key loads must be marshalled via pending_load"
print("2) media-key loads are marshalled")

# the helper must consult torrent progress
assert "expected_size" in SRC and "in_progress_files" in SRC
print("3) load path consults torrent progress")

print("LOAD SITE TESTS PASSED")
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_player_streaming.py; python3 tests/test_load_sites.py`
Expected: FAIL — `hasattr(p, "is_streaming")` is False, and `15 raw player.load( occurrences`

**Step 3: Write minimal implementation**

In `src/ncs_launcher.py`, add `from streaming_source import StreamingSource` near the imports, and add to `Player`:

```python
    def load(self, path, expected_size=None, streaming=False):
        """Load a track.

        streaming=True decodes incrementally and keeps extending as the file
        grows (torrent in progress). Otherwise the whole file is decoded now.
        """
        with self.lock:
            self.track_path = path
            self.expected_size = expected_size
            self.pos = 0
            self.paused = False
            self._src = None
            if streaming:
                self.is_streaming = True
                self._src = StreamingSource(path)
                self._src.pump()
                self.samples = self._src.samples()
            else:
                self.is_streaming = False
                self.samples = decode_file(path)

    def refresh(self):
        """Extend a streaming track with newly-arrived bytes.

        Returns True when the playable buffer changed. Call on a ~1 Hz timer:
        the decoder must rescan headers from the start, so a per-frame call
        would burn CPU for nothing.
        """
        if not self.is_streaming or self._src is None:
            return False
        added = self._src.pump()
        if added:
            with self.lock:
                self.samples = self._src.samples()
        if self._src.is_complete(self.expected_size):
            with self.lock:
                self.is_streaming = False
        return bool(added)

    def buffered_duration(self):
        with self.lock:
            return len(self.samples) / SAMPLE_RATE if self.samples is not None else 0.0
```

Add to `Player.__init__`: `self.is_streaming = False`, `self.expected_size = None`, `self._src = None`.

In `main()`, add immediately after `torrents = ...`:

```python
    state_lock = threading.Lock()
    pending_load = []          # track indices requested off-thread
    last_refresh = 0.0

    def partial_map():
        """{abs_path: expected_size} for tracks still downloading."""
        if torrents is None:
            return {}
        try:
            return torrents.in_progress_files()
        except Exception:
            return {}

    def load_track(index):
        """The ONE place a track gets loaded, from any thread."""
        if not tracks:
            return
        index = index % len(tracks)
        path = tracks[index]['path']
        expected = partial_map().get(path)
        try:
            player.load(path, expected_size=expected,
                        streaming=expected is not None)
        except Exception as e:
            push_notice(("error", f"cannot play: {os.path.basename(path)} ({e})"))
            return
        if tracks[index].get('art_path') and cfg.get("easter_eggs", True):
            pass    # hook kept for Task 9; harmless no-op here
```

Then replace **all 15** `player.load(tracks[...]['path'])` call sites with `load_track(<index>)`:

- line 1022 → `load_track(0)`
- lines 1070, 1076 (inside `media_key_handler`, which runs on the Quartz thread) → replace with a queue append:
  ```python
            with state_lock:
                pending_load.append(selected)
  ```
  leaving the existing `nonlocal_selected[0] = selected` sync in place
- lines 1151, 1159, 1164, 1202, 1207, 1211, 1227, 1235, 1240, 1275, 1295 → `load_track(<index>)`
- line 1312 (mouse wheel) → `load_track(selected)`
- line 1317 (media-key sync block) → drain the queue instead:
  ```python
        with state_lock:
            if pending_load:
                load_track(pending_load.pop(0))
  ```

Add the throttled refresh at the top of the main loop body, after `t = time.time() - start_time`:

```python
        # extend a streaming track at ~1Hz (decoder rescans headers; don't spam it)
        now = time.time()
        if player.is_streaming and now - last_refresh > 1.0:
            last_refresh = now
            player.refresh()
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_player_streaming.py && python3 tests/test_load_sites.py`
Expected: `PLAYER STREAMING TESTS PASSED` then `LOAD SITE TESTS PASSED`

Then regressions: `python3 tests/test_live_loop.py` → `PASS: ...`; `python3 tests/test_sphere_and_chat.py` → `ALL CHECKS PASSED`.

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py tests/test_player_streaming.py tests/test_load_sites.py
git commit -m "feat(player): streaming load path, single load_track helper, marshal media-key loads off-thread"
```

---

### Task 6: Report in-progress torrent files

**Objective:** `TorrentManager` exposes which files are still downloading and their expected size.

**Files:**
- Modify: `src/ncs_launcher.py:93-180` (`TorrentManager`)
- Test: `tests/test_torrent_progress.py`

**Step 1: Write the failing test**

Create `tests/test_torrent_progress.py`:

```python
import os, sys
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import ncs_launcher as nl

if nl.lt is None:
    print("libtorrent unavailable; skipping")
    sys.exit(0)

tm = nl.TorrentManager()
print("1) has in_progress_files:", hasattr(tm, "in_progress_files"))
print("2) has file_totals:", hasattr(tm, "file_totals"))
assert hasattr(tm, "in_progress_files")
assert hasattr(tm, "file_totals")

rep = tm.in_progress_files()
print("3) empty report:", rep, type(rep))
assert isinstance(rep, dict) and rep == {}

# normalize still behaves
assert tm.normalize("magnet:?xt=urn:btih:" + "a" * 40).startswith("magnet:")
assert tm.normalize("a" * 40).startswith("magnet:")
assert tm.normalize("A" * 32).startswith("magnet:")
assert tm.normalize("nonsense") is None
assert tm.normalize("") is None
print("4) normalize OK")

# file_totals on an unknown key is empty, not an exception
print("5) file_totals(unknown):", tm.file_totals("nope"))
assert tm.file_totals("nope") == {}

print("TORRENT PROGRESS TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_torrent_progress.py`
Expected: FAIL — `AssertionError` on `in_progress_files`

**Step 3: Write minimal implementation**

In `TorrentManager.__init__`, add `self._file_cache = {}` next to `self.handles`.

Add after `status_lines`:

```python
    def file_totals(self, key):
        """Expected final size of each file in a torrent.

        Cached: torrent metadata arrives once and never changes.
        """
        with self.lock:
            if key in self._file_cache:
                return self._file_cache[key]
            h = self.handles.get(key)
        if h is None:
            return {}
        try:
            out = {f.path: f.size for f in h.get_files()}
        except Exception:
            out = {}
        with self.lock:
            self._file_cache[key] = out
        return out

    def in_progress_files(self):
        """{abs_path: expected_size} for files still being downloaded.

        A file counts as in progress while its on-disk size is below the size
        the torrent metadata says it will reach.
        """
        out = {}
        with self.lock:
            items = list(self.handles.items())
        for key, h in items:
            for rel, expected in self.file_totals(key).items():
                abs_path = os.path.join(TORRENT_DIR, rel)
                try:
                    have = os.path.getsize(abs_path)
                except OSError:
                    have = 0
                if have < expected:
                    out[abs_path] = expected
        return out
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_torrent_progress.py`
Expected: `TORRENT PROGRESS TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py tests/test_torrent_progress.py
git commit -m "feat(torrent): expose in_progress_files/file_totals for streaming playback"
```

---

### Task 7: Show download progress in the UI

**Objective:** The seek bar and status line tell the truth about a partially-downloaded track.

**Files:**
- Modify: `src/ncs_launcher.py` — `draw_ui` (line 559)
- Test: `tests/test_partial_badge.py`

**Step 1: Write the failing test**

Create `tests/test_partial_badge.py`:

```python
import os, re, sys
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

SRC = open("/Users/giathinh/ncs-music-launcher/src/ncs_launcher.py").read()
ui = SRC[SRC.index("def draw_ui("):SRC.index("def extract_metadata_and_art(")]

print("1) draw_ui knows about is_streaming:", "is_streaming" in ui)
assert "is_streaming" in ui, "UI must special-case a streaming track"
assert "downloading" in ui.lower(), "UI must label a partial track"
print("2) partial-track badge present")
print("PARTIAL BADGE TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_partial_badge.py`
Expected: FAIL — `draw_ui knows about is_streaming: False`

**Step 3: Write minimal implementation**

In `draw_ui`, after the seek bar and time readout are drawn, insert:

```python
        # a track that is still downloading: say so, and show buffer position
        if getattr(player, "is_streaming", False) and player.track_path:
            expected = getattr(player, "expected_size", None)
            try:
                have = os.path.getsize(player.track_path)
            except OSError:
                have = 0
            if expected:
                pct = min(100, int(have * 100 / expected))
                col = (255, 200, 90) if pct < 100 else (120, 235, 160)
                badge = font.render(
                    f"downloading {pct}%  ·  playing {int(pos)//60}:{int(pos)%60:02d} of buffer",
                    True, col)
                screen.blit(badge, (40, h - 78))
            # a buffering spinner while the playhead sits at the buffer end
            if pos >= player.buffered_duration() - 0.25:
                dots = "." * (1 + int(time.time() * 2) % 3)
                tip = font.render(f"buffering{dots} — waiting for more data",
                                  True, (150, 155, 170))
                screen.blit(tip, (40, h - 100))
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_partial_badge.py`
Expected: `PARTIAL BADGE TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py tests/test_partial_badge.py
git commit -m "feat(ui): show download percentage and buffering state for partial tracks"
```

---

### Task 8: Create `src/library_ops.py` — safe bulk move

**Objective:** "Move all music to a folder of my choice" that never clobbers data and can be previewed first.

**Files:**
- Create: `src/library_ops.py`
- Test: `tests/test_library_ops.py`

**Step 1: Write the failing test**

Create `tests/test_library_ops.py`:

```python
import os, sys, tempfile
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import library_ops as ops


def build(root):
    src = os.path.join(root, "src")
    os.makedirs(os.path.join(src, "nested"))
    for rel in ("a.mp3", os.path.join("nested", "b.mp3"), "notes.txt"):
        p = os.path.join(src, rel)
        open(p, "wb").write(b"ID3" + os.path.basename(rel).encode() * 8)
    return src


with tempfile.TemporaryDirectory() as d:
    src = build(d)
    dst = os.path.join(d, "dest"); os.makedirs(dst)

    plan = ops.plan_move(src, dst)
    print("1) planned:", sorted(os.path.basename(s) for s, _ in plan))
    assert len(plan) == 2, "only audio files, not notes.txt"

    m, s, f = ops.execute_move(plan, dry_run=True)
    print("2) dry run -> moved=%d skipped=%d failed=%d" % (m, s, f))
    assert m == 2 and s == 0 and f == 0
    assert os.path.exists(os.path.join(src, "a.mp3")), "dry run moves nothing"
    assert not os.path.exists(os.path.join(dst, "a.mp3"))

    m, s, f = ops.execute_move(plan, dry_run=False)
    print("3) real run -> moved=%d skipped=%d failed=%d" % (m, s, f))
    assert (m, s, f) == (2, 0, 0)
    assert os.path.exists(os.path.join(dst, "a.mp3"))
    assert os.path.exists(os.path.join(dst, "nested", "b.mp3")), "keeps layout"
    assert not os.path.exists(os.path.join(src, "a.mp3"))
    assert os.path.exists(os.path.join(src, "notes.txt")), "non-audio untouched"

with tempfile.TemporaryDirectory() as d:
    src = build(d)
    dst = os.path.join(d, "dest"); os.makedirs(dst)
    open(os.path.join(dst, "a.mp3"), "wb").write(b"EXISTING")
    m, s, f = ops.execute_move(ops.plan_move(src, dst))
    print("4) collision -> moved=%d skipped=%d failed=%d" % (m, s, f))
    assert (m, s, f) == (1, 1, 0)
    assert open(os.path.join(dst, "a.mp3"), "rb").read() == b"EXISTING", "never clobber"

with tempfile.TemporaryDirectory() as d:
    src = build(d)
    assert ops.plan_move(src, src) == []
    assert ops.plan_move(src, os.path.join(src, "inside")) == [], "refuse dst in src"
    assert ops.plan_move(os.path.join(d, "missing"), d) == []
print("5) unsafe plans refused")

print("LIBRARY OPS TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_library_ops.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'library_ops'`

**Step 3: Write minimal implementation**

Create `src/library_ops.py`:

```python
"""Bulk-move a music library into one folder, without ever losing data.

Safety rules, in order:
  1. Refuse source == destination.
  2. Refuse a destination inside the source (would recurse forever).
  3. Never overwrite: an existing destination file is skipped and reported.
  4. Preserve relative sub-layout, so nested folders stay nested.
  5. Support dry_run, so the UI can show the exact plan before committing.
  6. Verify the byte count after the move; roll back if it does not match.
"""
import os
import shutil

AUDIO_EXT = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".wma")


def _is_inside(child, parent):
    child = os.path.abspath(child)
    parent = os.path.abspath(parent)
    return child == parent or child.startswith(parent + os.sep)


def plan_move(src_dir, dst_dir, extensions=AUDIO_EXT):
    """[(src, dst), ...] for every audio file. [] when the move is unsafe."""
    src_dir = os.path.abspath(src_dir)
    dst_dir = os.path.abspath(dst_dir)
    if not os.path.isdir(src_dir) or not src_dir or not dst_dir:
        return []
    if src_dir == dst_dir or _is_inside(dst_dir, src_dir):
        return []
    plan = []
    for root, _dirs, files in os.walk(src_dir):
        for name in sorted(files):
            if not name.lower().endswith(extensions):
                continue
            s = os.path.join(root, name)
            plan.append((s, os.path.join(dst_dir, os.path.relpath(s, src_dir))))
    return plan


def execute_move(plan, dry_run=False):
    """Run a plan. Returns (moved, skipped, failed)."""
    moved = skipped = failed = 0
    for src, dst in plan:
        if os.path.exists(dst):
            skipped += 1
            continue
        if dry_run:
            moved += 1
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            size_before = os.path.getsize(src)
            shutil.move(src, dst)
            if os.path.getsize(dst) != size_before:
                os.replace(dst, src)      # roll back a bad move
                failed += 1
            else:
                moved += 1
        except OSError:
            failed += 1
    return moved, skipped, failed
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_library_ops.py`
Expected: `LIBRARY OPS TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/library_ops.py tests/test_library_ops.py
git commit -m "feat(library): safe bulk move with dry-run preview, collision skips, size rollback"
```

---

### Task 9: Create `src/settings_panel.py` — keyboard-driven settings UI

**Objective:** Settings for library folder, bulk move, hint toggle, key rebinding, and reset — all navigable by keyboard alone.

**Files:**
- Create: `src/settings_panel.py`
- Test: `tests/test_settings_panel.py`

**Step 1: Write the failing test**

Create `tests/test_settings_panel.py`:

```python
import os, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import pygame
pygame.init()
pygame.display.set_mode((1280, 720))
font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)

import actions
import config as cfgmod
from settings_panel import SettingsPanel


class Ev:
    def __init__(self, key, unicode=""):
        self.key, self.unicode = key, unicode


cfg = cfgmod.default_config()
cfg["library_folder"] = "/Users/giathinh/Downloads"
panel = SettingsPanel(font, pygame.Rect(0, 0, 1280, 720), cfg,
                      actions.Keymap({}), pick_folder=lambda cur: None)

print("1) rows:", len(panel.items))
assert len(panel.items) > 8

names = [i.action for i in panel.items if i.action]
print("2) rebindable actions:", len(names))
assert set(names) == set(actions.ACTIONS), "every action needs a rebind row"
print("3) every action is rebindable")

panel.open()
assert panel.is_open()
panel.handle_key(Ev(pygame.K_DOWN))
assert panel.selected_index == 1
panel.handle_key(Ev(pygame.K_UP))
assert panel.selected_index == 0
print("4) arrow navigation OK")

row = next(i for i in panel.items if i.action == "play_pause")
panel.selected_index = panel.items.index(row)
panel.handle_key(Ev(pygame.K_RETURN))
print("5) capturing:", panel.capturing)
assert panel.capturing
panel.handle_key(Ev(pygame.K_k, unicode="k"))
print("6) rebound to:", panel.keymap.bindings["play_pause"])
assert panel.keymap.bindings["play_pause"] == "K"
assert not panel.capturing
print("7) rebind OK")

# capturing a modifier must be ignored, capture stays armed
row = next(i for i in panel.items if i.action == "toggle_mute")
panel.selected_index = panel.items.index(row)
panel.handle_key(Ev(pygame.K_RETURN))
assert panel.capturing
panel.handle_key(Ev(pygame.K_LSHIFT))
print("8) modifier ignored, still capturing:", panel.capturing)
assert panel.capturing
panel.handle_key(Ev(pygame.K_ESCAPE))
print("9) esc cancels capture, panel still open:", panel.is_open())
assert not panel.capturing and panel.is_open()
panel.handle_key(Ev(pygame.K_ESCAPE))
print("10) esc closes panel:", panel.is_open())
assert not panel.is_open()

panel.open()
trow = next(i for i in panel.items if i.kind == "toggle_hints")
panel.selected_index = panel.items.index(trow)
before = panel.cfg["show_hints"]
panel.handle_key(Ev(pygame.K_RETURN))
print("11) show_hints:", before, "->", panel.cfg["show_hints"])
assert panel.cfg["show_hints"] != before

panel.selected_index = next(i for i, r in
                            enumerate(panel.items) if r.kind == "reset_keys")
panel.handle_key(Ev(pygame.K_RETURN))
print("12) after reset, play_pause =", panel.keymap.bindings["play_pause"])
assert panel.keymap.bindings["play_pause"] == "SPACE"

over = [i.label for i in panel.items
        if font.size(i.label)[0] > 1280 - 100]
print("13) overflowing labels:", over)
assert not over, f"labels must fit: {over}"

panel.draw(pygame.display.get_surface(), 1.0)
print("SETTINGS PANEL TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_settings_panel.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'settings_panel'`

**Step 3: Write minimal implementation**

Create `src/settings_panel.py`:

```python
"""Keyboard-navigable settings overlay.

Row kinds:
  action  - rebind a key (Enter arms capture; the next keypress is the new key)
  toggle  - flip a boolean in cfg
  command - run a callback
  info    - read-only text

Everything is keyboard-driven, so the app stays fully configurable with no
mouse. Modifiers are refused as bindings.
"""
import pygame

from actions import ACTIONS, constant_to_key_name, human_key_name


class Row:
    def __init__(self, label, kind, action=None, value=None, callback=None,
                 help_text=""):
        self.label = label
        self.kind = kind          # action | toggle | command | info
        self.action = action
        self.value = value
        self.callback = callback
        self.help_text = help_text


class SettingsPanel:
    def __init__(self, font, rect, cfg, keymap, pick_folder=None):
        self.font = font
        self.rect = rect
        self.cfg = cfg
        self.keymap = keymap
        self.pick_folder = pick_folder
        self._open = False
        self.selected_index = 0
        self.capturing = False
        self.message = ""
        self._pending_plan = None
        self.items = []
        self._build()

    # ---- state ---------------------------------------------------------
    def is_open(self):
        return self._open

    def open(self):
        self._open = True
        self.capturing = False

    def close(self):
        self._open = False
        self.capturing = False

    def toggle(self):
        self._open = not self._open
        self.capturing = False
        return self._open

    def _ordered_actions(self):
        return sorted(ACTIONS.items(), key=lambda kv: (kv[1]["group"], kv[1]["label"]))

    def _build(self):
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
            Row("", "info", value="── key bindings ──"),
        ]
        for name, meta in self._ordered_actions():
            self.items.append(Row(meta["label"], "action", action=name,
                                  value=self.keymap.bindings.get(name, "-"),
                                  help_text=meta["group"]))
        self.items.append(Row("", "info", value="── reset ──"))
        self.items.append(Row("Reset all keys to defaults", "command",
                              callback=self._cmd_reset_keys))
        self.selected_index = min(self.selected_index, len(self.items) - 1)

    # ---- commands ------------------------------------------------------
    def _cmd_choose_folder(self):
        if not self.pick_folder:
            self.message = "folder picker unavailable"
            return
        picked = self.pick_folder(self.cfg.get("library_folder", ""))
        if picked:
            self.cfg["library_folder"] = picked
            self.message = f"library set to {picked}"

    def _cmd_move_all(self):
        import library_ops
        if not self.pick_folder:
            self.message = "folder picker unavailable"
            return
        dest = self.pick_folder(self.cfg.get("library_folder", ""))
        if not dest:
            self.message = "move cancelled"
            return
        plan = library_ops.plan_move(self.cfg.get("library_folder", ""), dest)
        if not plan:
            self.message = "nothing to move (or unsafe destination)"
            self._pending_plan = None
            return
        if self._pending_plan == plan:
            # second Enter confirms the identical plan
            moved, skipped, failed = library_ops.execute_move(plan)
            self.message = f"moved {moved}, skipped {skipped}, failed {failed}"
            self._pending_plan = None
            self.cfg["library_folder"] = dest
        else:
            moved, _skipped, _failed = library_ops.execute_move(plan, dry_run=True)
            self.message = (f"will move {moved} file(s) to {dest} — "
                            "press Enter again to confirm")
            self._pending_plan = plan

    def _cmd_toggle_hints(self):
        self.cfg["show_hints"] = not self.cfg.get("show_hints", True)
        for row in self.items:
            if row.kind == "toggle":
                row.value = self.cfg["show_hints"]
                break

    def _cmd_reset_keys(self):
        self.keymap.reset_to_defaults()
        self.message = "keys reset to defaults"
        self._build()

    # ---- input ---------------------------------------------------------
    def handle_key(self, event):
        if not self._open:
            return
        if self.capturing:
            self._capture(event)
            return
        if event.key == pygame.K_ESCAPE:
            self.close()
        elif event.key in (pygame.K_UP, pygame.K_w):
            self.selected_index = (self.selected_index - 1) % len(self.items)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.selected_index = (self.selected_index + 1) % len(self.items)
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
            self._activate()

    def _capture(self, event):
        if event.key == pygame.K_ESCAPE:
            self.capturing = False
            self.message = "rebind cancelled"
            return
        name = constant_to_key_name(event.key)
        if name is None:
            return          # modifier press; keep waiting
        row = self.items[self.selected_index]
        try:
            self.keymap.bind(row.action, name)
        except (KeyError, ValueError) as e:
            self.capturing = False
            self.message = f"cannot bind: {e}"
            return
        row.value = self.keymap.bindings[row.action]
        clashes = self.keymap.conflicts()
        self.message = f"{row.label} = {human_key_name(name)}"
        if clashes:
            self.message += "  (also: " + ", ".join(c[1] for c in clashes) + ")"
        self.capturing = False

    def _activate(self):
        row = self.items[self.selected_index]
        if row.kind == "action":
            self.capturing = True
            self.message = f"press a key for: {row.label}"
        elif row.callback:
            row.callback()
            self._build()

    # ---- drawing -------------------------------------------------------
    def draw(self, screen, t):
        if not self._open:
            return
        r = self.rect
        panel = pygame.Surface((r.width, r.height), pygame.SRCALPHA)
        panel.fill((8, 10, 16, 243))
        pygame.draw.rect(panel, (0, 230, 190, 160), panel.get_rect(), 2)
        screen.blit(panel, r.topleft)

        head = self.font.render("SETTINGS   Up/Down move · Enter select · Esc close",
                                True, (0, 230, 190))
        screen.blit(head, (r.x + 24, r.y + 16))
        pygame.draw.line(screen, (255, 255, 255, 30),
                         (r.x + 24, r.y + 42), (r.x + r.width - 24, r.y + 42))

        line_h = self.font.get_linesize()
        top = r.y + 56
        max_rows = max(1, (r.height - 150) // line_h)
        start = max(0, min(self.selected_index - max_rows // 2,
                           max(0, len(self.items) - max_rows)))
        for i, row in enumerate(self.items[start:start + max_rows]):
            y = top + i * line_h
            sel = (start + i) == self.selected_index
            if sel:
                sel_rect = pygame.Rect(r.x + 20, y - 3, r.width - 40, line_h)
                pygame.draw.rect(screen, (0, 220, 180), sel_rect, border_radius=4)
            fg = (10, 14, 18) if sel else (235, 235, 245)
            dim = (60, 70, 80) if sel else (150, 155, 170)
            if row.kind == "info":
                text = self.font.render(str(row.value or ""), True, dim)
            else:
                if row.kind == "action":
                    shown = ("<press a key>" if sel and self.capturing
                             else human_key_name(row.value))
                elif row.kind == "toggle":
                    shown = "ON" if row.value else "OFF"
                else:
                    shown = ""
                text = self.font.render(f"{row.label:<36}{shown}", True, fg)
            screen.blit(text, (r.x + 28, y))

        if self.message:
            msg = self.font.render(self.message[:110], True, (255, 200, 90))
            screen.blit(msg, (r.x + 24, r.y + r.height - 62))
        hint = self.font.render(
            "Keys are saved to ~/.ncs-smlauncher/settings.json", True, (120, 125, 140))
        screen.blit(hint, (r.x + 24, r.y + r.height - 36))
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_settings_panel.py`
Expected: numbered lines then `SETTINGS PANEL TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/settings_panel.py tests/test_settings_panel.py
git commit -m "feat(settings): keyboard-navigable panel for folder, bulk move, hint toggle, keybinds"
```

---

### Task 10: Wire the action dispatch, settings, and config into `main()`

**Objective:** Replace the `elif` key chain with `dispatch_action`, route settings input first, honor `show_hints`, and persist on exit.

**Files:**
- Modify: `src/ncs_launcher.py` — imports, `main()` startup, key chain, hint bar, cleanup
- Test: `tests/test_dispatch_wiring.py`

**Step 1: Write the failing test**

Create `tests/test_dispatch_wiring.py`:

```python
import os, re, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import pygame
pygame.init(); pygame.display.set_mode((1280, 720))
import ncs_launcher as nl
import actions

SRC = open("/Users/giathinh/ncs-music-launcher/src/ncs_launcher.py").read()
main_src = SRC[SRC.index("def main():"]

print("1) dispatch_action exists:", "def dispatch_action" in main_src)
assert "def dispatch_action" in main_src
assert "keymap.resolve(event.key)" in main_src, "keys must resolve via the keymap"
assert "action == \"open_settings\"" in main_src
assert "action == \"cycle_visualizer\"" in main_src
print("2) dispatch table wired")

# the old literal chain must be gone
literals = re.findall(r"event\.key == pygame\.K_[a-z]", main_src)
print("3) remaining literal key comparisons:", literals)
assert not literals, f"raw key comparisons left in main(): {literals}"

# settings gets keyboard priority and its rect follows the window
assert "settings_panel.is_open()" in main_src
assert "settings_panel.rect = pygame.Rect(0, 0, w, h)" in main_src
print("4) settings wired with live rect")

# config is loaded and saved
assert "cfgmod.load_config()" in main_src
assert "cfgmod.save_config(cfg)" in main_src
print("5) config load + save wired")

# a live dispatch smoke test through the real keymap
km = actions.Keymap({"cycle_visualizer": "V"})
assert km.resolve(pygame.K_v) == "cycle_visualizer"
assert km.resolve(pygame.K_f) is None
print("6) rebind visible through the keymap")

print("DISPATCH WIRING TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_dispatch_wiring.py`
Expected: FAIL — `AssertionError: dispatch_action exists: False`

**Step 3: Write minimal implementation**

Add imports near the top of `src/ncs_launcher.py`:

```python
import config as cfgmod
from actions import ACTIONS, Keymap, human_key_name
from settings_panel import SettingsPanel
```

In `main()`, replace the folder setup at the very top:

```python
def main():
    cfg = cfgmod.load_config()
    argv_folder = sys.argv[1] if len(sys.argv) > 1 else None
    folder = argv_folder or cfg.get("library_folder") or os.path.expanduser("~/Music")
    if argv_folder:
        cfg["library_folder"] = argv_folder
    folders = [folder]
    keymap = Keymap(cfg.get("keymap"))
    settings_panel = SettingsPanel(font if (font := None) else None,
                                   pygame.Rect(0, 0, 1280, 720), cfg, keymap)
```

then, immediately after `font` and `font_big` are created, rebuild the panel with the real font and folder picker:

```python
    settings_panel = SettingsPanel(
        font, pygame.Rect(0, 0, w, h), cfg, keymap,
        pick_folder=lambda cur: pick_folder_dialog(cur))
```

Delete the placeholder line above.

Define `dispatch_action` just before `running = True`:

```python
    def dispatch_action(action):
        """The single place an abstract action becomes behaviour."""
        nonlocal selected, chat_open, overlay_open, vis_mode_idx, muted
        nonlocal running, folder, current_easter_art

        if action == "quit":
            running = False
        elif action == "cycle_visualizer":
            vis_mode_idx = (vis_mode_idx + 1) % len(modes)
            push_notice(("info", f"visual: {modes[vis_mode_idx]}"))
        elif action == "toggle_mute":
            muted = not muted
            player.muted = muted
            push_notice(("info", "muted" if muted else "unmuted"))
        elif action == "play_pause":
            if player.track_path:
                player.toggle_pause()
        elif action == "play":
            if player.track_path and player.paused:
                player.toggle_pause()
        elif action == "seek_back":
            if not player.paused and player.track_path:
                player.seek(-5)
        elif action == "seek_forward":
            if not player.paused and player.track_path:
                player.seek(5)
        elif action in ("select_prev", "select_next"):
            if tracks:
                step = -1 if action == "select_prev" else 1
                selected = (selected + step) % len(tracks)
                nonlocal_selected[0] = selected
                load_track(selected)
        elif action == "open_hermes":
            if chat_open:
                chat_open = False
                pygame.key.stop_text_input()
            else:
                chat_open = True
                pygame.key.start_text_input()
        elif action == "open_torrent":
            if not overlay_open:
                overlay_open = True
                pygame.key.start_text_input()
        elif action == "open_folder":
            picked = pick_folder_dialog(folder)
            if picked and os.path.isdir(picked):
                folder = picked
                cfg["library_folder"] = picked
                cfgmod.save_config(cfg)
                folders[0] = folder
                tracks.clear()
                scan_library(folders, tracks)
                selected = 0
                if tracks:
                    load_track(0)
                push_notice(("info", f"library: {folder}"))
            else:
                push_notice(("info", "folder picker cancelled"))
        elif action == "open_settings":
            settings_panel.toggle()
```

Replace the whole literal key chain with dispatch, keeping the overlay/chat text-entry branches above it:

```python
                action = keymap.resolve(event.key)
                if action:
                    dispatch_action(action)
                elif event.key == pygame.K_ESCAPE:
                    running = False
```

Give settings keyboard priority, above the chat check:

```python
            elif event.type == pygame.KEYDOWN:
                if settings_panel.is_open():
                    settings_panel.handle_key(event)
                    continue
                if chat_open:
                    ...
```

Make the hint bar conditional and built from the live keymap:

```python
    def render_hint():
        keys = [human_key_name(keymap.bindings.get(a, ""))
                for a in ("select_prev", "select_next", "play", "play_pause",
                          "seek_back", "seek_forward", "cycle_visualizer",
                          "open_hermes", "open_torrent", "open_settings", "quit")]
        return font.render("  ".join(keys), True, (120, 120, 130))
```

```python
        if cfg.get("show_hints", True):
            hint_surf = render_hint()
            hx, hy = w - hint_surf.get_width() - 24, h - 24
            if hx < 24:
                screen.blit(font.render("Up Down Enter Space L R F C T , Q",
                                        True, (120, 120, 130)), (24, hy))
            else:
                screen.blit(hint_surf, (hx, hy))
```

Keep the panel sized to the window, and draw it last:

```python
        settings_panel.rect = pygame.Rect(0, 0, w, h)
        settings_panel.draw(screen, t)
```

In the `VIDEORESIZE` handler, add `settings_panel.rect = pygame.Rect(0, 0, w, h)`.

Before `pygame.quit()` in the cleanup block:

```python
    if cfgmod.save_config(cfg):
        print(f"settings saved to {cfgmod.CONFIG_PATH}")
    if torrents and torrents.session:
        torrents.session.pause()
    pygame.quit()
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_dispatch_wiring.py && python3 tests/test_live_loop.py`
Expected: `DISPATCH WIRING TESTS PASSED` and `PASS: resize + chat key routing did not crash the loop`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py tests/test_dispatch_wiring.py
git commit -m "refactor(input): dispatch through action registry, add settings overlay, persist config"
```

---

### Task 11: First-run setup prompt

**Objective:** On a fresh install with no configured folder, open straight into setup instead of silently scanning `~/Music`.

**Files:**
- Modify: `src/ncs_launcher.py` — module-level `resolve_initial_folder`, plus `main()` startup
- Test: `tests/test_first_run.py`

**Step 1: Write the failing test**

Create `tests/test_first_run.py`:

```python
import os, sys
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import config as cfgmod
from ncs_launcher import resolve_initial_folder


def t(cli, saved, expect, prompt):
    cfg = cfgmod.default_config()
    if saved is not None:
        cfg["library_folder"] = saved
    got, got_prompt = resolve_initial_folder(cfg, cli, "/Users/giathinh/Music")
    print("  cli=%r saved=%r -> %r prompt=%s" % (cli, saved, got, got_prompt))
    assert got == expect, (got, expect)
    assert got_prompt is prompt


t(None, None, "/Users/giathinh/Music", True)
t("/tmp/music", None, "/tmp/music", False)
t(None, "/Users/giathinh/Downloads", "/Users/giathinh/Downloads", False)
t("/tmp/a", "/Users/giathinh/Downloads", "/tmp/a", False)
print("FIRST RUN TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_first_run.py`
Expected: FAIL — `ImportError: cannot import name 'resolve_initial_folder'`

**Step 3: Write minimal implementation**

Add at module level, just before `def main():`:

```python
def resolve_initial_folder(cfg, argv_folder, home_music=None):
    """Folder to scan at startup. Returns (folder, should_prompt).

    Precedence: explicit CLI arg > saved config > ~/Music. should_prompt is
    True only when nothing has been configured, so the UI can offer setup.
    """
    home_music = home_music or os.path.expanduser("~/Music")
    if argv_folder:
        return argv_folder, False
    saved = (cfg or {}).get("library_folder") or ""
    if saved:
        return saved, False
    return home_music, True
```

In `main()`, use it:

```python
    cfg = cfgmod.load_config()
    argv_folder = sys.argv[1] if len(sys.argv) > 1 else None
    folder, needs_setup = resolve_initial_folder(cfg, argv_folder)
    if argv_folder:
        cfg["library_folder"] = argv_folder
        cfgmod.save_config(cfg)
```

and after `settings_panel` is created with the real font:

```python
    if needs_setup:
        settings_panel.message = ("First run: choose your music folder below, "
                                  "or press Esc to keep the default")
        settings_panel.open()
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_first_run.py`
Expected: four indented lines then `FIRST RUN TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py tests/test_first_run.py
git commit -m "feat(setup): first-run folder prompt with CLI > config > ~/Music precedence"
```

---

### Task 12: Full regression suite

**Objective:** Prove nothing that worked before is broken.

**Files:**
- Test: everything in `tests/`

**Step 1: Run the whole suite**

Run:
```bash
cd /Users/giathinh/ncs-music-launcher
fail=0
for f in tests/test_*.py; do
  if out=$(python3 "$f" 2>&1); then
    echo "PASS  $f"
  else
    echo "FAIL  $f"
    echo "$out" | tail -15
    fail=1
  fi
done
exit $fail
```
Expected: a `PASS` line for every file, no `FAIL`, exit code 0. Twelve files: `test_actions`, `test_chat_e2e` (skip if slow), `test_config`, `test_dispatch_wiring`, `test_first_run`, `test_live_loop`, `test_library_ops`, `test_load_sites`, `test_partial_badge`, `test_player_streaming`, `test_settings_panel`, `test_sphere_and_chat`, `test_streaming_source`, `test_text_input_balance`, `test_torrent_progress`.

**Step 2: Launch the real app and verify by hand, in order**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 src/ncs_launcher.py ~/Downloads`
Expected: window opens, no traceback, `Found N track(s)`.

1. `,` opens settings (first run: it should already be open).
2. ↑↓ navigates; Enter on "Show control hints" flips it OFF; the hint bar disappears.
3. Enter on "Play / pause" → "press a key"; press `K`; row reads `K`.
4. Esc. Press `K` → playback pauses/resumes. Press Space → nothing (unbound).
5. Press `C` → Hermes panel; ask something; Enter; a real reply appears. Esc closes.
6. `T` → torrent overlay accepts text; Esc closes; then Space still works (text input balanced).
7. Resize the window small → panel, hint bar, and sphere all reflow, nothing overlaps.
8. Settings → "Move all music to a folder..." → pick a folder → "press Enter again to confirm" → Enter again → files moved, nothing clobbered.
9. Quit, relaunch → hints still OFF, `K` still bound (config persisted).
10. Paste a magnet in `T`, then select the downloading track before it finishes → it plays, the badge shows `downloading N%`, audio keeps extending.

**Step 3: Fix anything found, then re-run Task 12 Step 1**

```bash
cd /Users/giathinh/ncs-music-launcher
git add -A
git commit -m "fix: issues found during manual verification"
```

---

### Task 13: Update version metadata, rebuild, and publish

**Objective:** Ship the new build as a pre-release with correct version metadata.

**Files:**
- Modify: `build_macos_app.sh` (version string)
- Modify: `HashPlay.spec` only if the build reports missing modules
- Create: `RELEASE_NOTES_v0.99.1.md`
- Test: verify the built binary actually starts

**Step 1: Correct the version in the build script**

`build_macos_app.sh` hardcodes `<key>CFBundleShortVersionString</key>  <string>1.0</string>`, which is wrong for a pre-release. Replace it with a variable at the top of the script and interpolate it:

```bash
#!/usr/bin/env bash
# Build HashPlay as a double-clickable macOS .app bundle
set -e
cd "$(dirname "$0")"
VERSION="${1:-0.99.1}"
BUILD=$(git rev-parse --short HEAD 2>/dev/null || echo nogit)
```

and inside the heredoc, use `__VERSION__` / `__BUILD__` placeholders and substitute:

```bash
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>HashPlay</string>
    <key>CFBundleDisplayName</key>     <string>HashPlay</string>
    <key>CFBundleIdentifier</key>      <string>com.giathinh.hashplay</string>
    <key>CFBundleExecutable</key>      <string>HashPlay</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key>         <string>$BUILD</string>
    <key>LSMinimumSystemVersion</key>  <string>11.0</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>NSMicrophoneUsageDescription</key>
        <string>HashPlay uses the audio session for the visualizer.</string>
</dict>
</plist>
PLIST
```

Change the last line from `echo "Built: $APP"` to `echo "Built: $APP (v$VERSION, $BUILD)"`.

**Step 2: Commit the version change before building, so `$BUILD` resolves**

```bash
cd /Users/giathinh/ncs-music-launcher
git add build_macos_app.sh
git commit -m "build: parameterize bundle version, stop claiming 1.0 in a pre-release"
```

**Step 3: Build**

Run: `cd /Users/giathinh/ncs-music-launcher && ./build_macos_app.sh 0.99.1`
Expected: PyInstaller logs ending in `Build complete!`, then `Built: dist/HashPlay.app (v0.99.1, <sha>)`.
If PyInstaller reports `ModuleNotFoundError` for `config`, `actions`, `streaming_source`, `library_ops`, or `settings_panel`, add them to `hiddenimports` in `HashPlay.spec` and rebuild.

**Step 4: Verify the built binary starts and is not stale**

Run:
```bash
cd /Users/giathinh/ncs-music-launcher
stat -f "%Sm %N" -t "%H:%M:%S" src/ncs_launcher.py dist/HashPlay dist/HashPlay.app/Contents/MacOS/HashPlay
plutil -p dist/HashPlay.app/Contents/Info.plist | grep -E "ShortVersion|BundleVersion"
```
Expected: the two binaries are newer than `src/ncs_launcher.py`, and the plist shows `0.99.1`.

Then a real launch, not just a version string:
```bash
cd /Users/giathinh/ncs-music-launcher
rm -rf ~/Library/Caches/HashPlay 2>/dev/null
./dist/HashPlay ~/Downloads
```
Expected: a window opens, `Found N track(s)` prints, no traceback. Press `q` to quit. Confirm the new features work from the bundle: `,` opens settings, `C` opens Hermes.

**Step 5: Write release notes**

Create `RELEASE_NOTES_v0.99.1.md` describing: remappable keys via Settings, the new settings panel, first-run folder setup, the library organizer, streaming playback of in-progress torrents, the three bug fixes (dead `energy` var, redundant spectrum pass, unbalanced `start/stop_text_input`), and honest known-issues (controller support is untested beyond one pad; non-mp3 formats are not verified for partial-file streaming; bulk move deletes originals after a byte-count check).

**Step 6: Push, then publish the pre-release**

```bash
cd /Users/giathinh/ncs-music-launcher
git add -A
git commit -m "docs: release notes for v0.99.1"
git push origin main
```

Expected: `main -> main` with no rejected push. If the remote moved, `git fetch origin && git merge origin/main --no-edit` first, then push again.

```bash
cd /Users/giathinh/ncs-music-launcher
rm -f dist/HashPlay.app.zip
zip -qr dist/HashPlay.app.zip dist/HashPlay.app
gh release create v0.99.1 \
  dist/HashPlay dist/HashPlay.app.zip \
  --title "v0.99.1-beta" \
  --notes-file RELEASE_NOTES_v0.99.1.md \
  --prerelease
gh release view v0.99.1
```
Expected: a release URL, then `prerelease: true` and both assets listed. Verify the tag is **not** `v1.0` — the user asked for a pre-release, and `v1.0` must remain unused.

**Step 7: Commit any last fixes**

```bash
cd /Users/giathinh/ncs-music-launcher
git status --porcelain
git log --oneline -3
```
Expected: clean tree, with the release commit on top.

---

## Tests / Validation

| Test | Covers | Command |
|---|---|---|
| `test_text_input_balance.py` | balanced start/stop, no dead `energy` | `python3 tests/test_text_input_balance.py` |
| `test_config.py` | defaults, roundtrip, corrupt, unknown keys, atomic save | `python3 tests/test_config.py` |
| `test_actions.py` | key resolution, rebind, duplicates, modifiers, conflicts | `python3 tests/test_actions.py` |
| `test_streaming_source.py` | incremental == full decode (bit-level), stereo downmix, truncation | `python3 tests/test_streaming_source.py` |
| `test_player_streaming.py` | partial load, `refresh()` growth, completion, bad-file error | `python3 tests/test_player_streaming.py` |
| `test_load_sites.py` | exactly one `player.load`, off-thread marshalling | `python3 tests/test_load_sites.py` |
| `test_torrent_progress.py` | `in_progress_files`, `file_totals`, `normalize` | `python3 tests/test_torrent_progress.py` |
| `test_partial_badge.py` | UI labels a partial track | `python3 tests/test_partial_badge.py` |
| `test_library_ops.py` | dry run, collisions, layout, unsafe refusals | `python3 tests/test_library_ops.py` |
| `test_settings_panel.py` | rows, nav, capture, modifiers, toggle, reset, label fit | `python3 tests/test_settings_panel.py` |
| `test_dispatch_wiring.py` | dispatch table, no literal key comparisons, persistence | `python3 tests/test_dispatch_wiring.py` |
| `test_first_run.py` | folder precedence | `python3 tests/test_first_run.py` |
| `test_sphere_and_chat.py` | sphere + chat (regression) | `python3 tests/test_sphere_and_chat.py` |
| `test_live_loop.py` | resize + chat (regression) | `python3 tests/test_live_loop.py` |
| `test_chat_e2e.py` | real Hermes round trip (regression, slow) | `python3 tests/test_chat_e2e.py` |

Do **not** add pytest-style tests: pytest is not installed.

---

## Risks, Tradeoffs, and Open Questions

**Risks, with what I already verified**

1. **Seek-cursor decoding is not a speedup.** Measured 0.093 s vs 0.089 s for cursor vs full re-decode — a 1.0× speedup, because the generator must rescan headers from byte 0 regardless. The plan therefore uses a ~1 Hz `refresh()` gate and the simple cursor design. Calling `refresh()` per frame would burn a visible slice of CPU every frame for no benefit.
2. **Stereo must be downmixed or playback is garbage.** Verified: streamed ints are exactly 2.000× the mono decode on a 2-channel file. `mp3_stream_file` emits the native channel count and rejects `output_format`/`nchannels` kwargs.
3. **`decode_file` does not raise on partial files** (it returns a short prefix), so a naive "catch the error" design would ship a silent bug. Every test asserts the *sample count*, not just "no exception".
4. **Cross-thread `player.load` is a real race** — the Quartz tap calls it from its own thread. Task 5 collapses 15 call sites into one helper and marshals off-thread requests through `pending_load`. Skipping that is the most likely source of intermittent crashes.
5. **Bulk move deletes originals after a byte-count check.** Correct for a local disk; on a network or cloud-synced target a short write could pass the size check yet be corrupt. Option (b) below avoids this entirely.
6. **`miniaudio.stream_file` is unverified for flac/ogg/m4a on truncated files.** The generic path may behave differently from mp3. Streaming is therefore best-effort per format; the UI shows a buffering state rather than failing silently.
7. **A bad key name in a hand-edited JSON silently unbinds a key.** `Keymap` drops unresolvable names and keeps the default, so it cannot crash, but the user gets no warning. `conflicts()` output in the settings panel is the mitigation.
8. **`build_macos_app.sh` was claiming version 1.0** while shipping a pre-release. Task 13 fixes it to take the version as an argument, so the plist can never again disagree with the release tag.

**Tradeoffs**

- *Action registry* adds one dict lookup per keypress in exchange for being the only way rebinding can work. Worth it.
- *Settings drawn over a live visualizer* costs a full-screen SRCALPHA surface per frame. The sphere still renders at ~30 fps underneath. Pausing the sphere while settings is open is a cheap follow-up if it bothers you.
- *Config as strings* (`"SPACE"`) keeps the file editable but allows typos; the mitigation is defaults-preserved-on-invalid plus the panel showing conflicts.
- *Bulk move as two-step confirm* costs one extra Enter press to prevent an accidental mass move. Deliberate.

**Open Questions**

1. **Move or copy?** You said *move*. The plan moves and deletes originals after verifying byte count. If you'd rather keep originals (safer on cloud-synced folders), say so and I'll add a copy-then-verify-then-delete mode — a small change to `execute_move`.
2. **Which keys do you want as defaults?** Currently unchanged from the app's existing bindings, plus `,` for settings. If you prefer media-player muscle memory (`Z`/`X` prev/next, `S` stop), that is a one-line change to `DEFAULT_KEYMAP`.
3. **Hide hints globally or keep a help screen?** Planned as a single `show_hints` toggle. A "compact bar / full help" choice is easy to add if you want both.
4. **Release version number.** I plan `v0.99.1` as a new pre-release tag, leaving `v0.99` and `v1.0` untouched. Tell me if you'd rather overwrite the existing `v0.99` release instead.
5. **Should the controller buttons be rebindable too?** Out of scope here (you asked for keyboard). The controller mapping is hardcoded in `main()` and could be folded into `ACTIONS` in a follow-up.
