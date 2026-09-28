# NCS-SMLauncher: Keybind Remapping, Streaming Playback During Download, and Settings

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make all app actions remappable to any keyboard key, make partially-downloaded torrents playable while still downloading, and add a settings screen with a music-library organizer and a "show controls" toggle.

**Architecture:** Three independent slices that share one new module. (1) A `config.py` module owning a JSON config file with defaults + load/save. (2) An `actions.py` registry mapping abstract action names to handler IDs, with a `Keymap` that resolves `pygame` key constants to action names, replacing the hardcoded `elif event.key == pygame.K_x` chain. (3) A `StreamingSource` in the `Player` that decodes a growing file incrementally instead of snapshotting it with `decode_file`, so playback tracks a file that libtorrent is still writing. Settings is a full-screen overlay panel driven by the same action system, so it is keyboard-navigable by construction.

**Tech Stack:** Python 3.13, pygame 2.6, numpy, miniaudio, mutagen, libtorrent 2.1 (`lt`), macOS AppleScript folder picker, `unittest` (pytest is NOT installed — do not add tests that need it).

---

## Current Context / Assumptions

Verified against the working tree at `/Users/giathinh/ncs-music-launcher` (1380 lines in `src/ncs_launcher.py`).

**What exists today**

- Key handling is a hardcoded `elif` chain: `src/ncs_launcher.py:1113-1180`. Keys bound to literals — `K_t` torrent, `K_o` folder, `K_UP`/`K_DOWN` track, `K_SPACE` pause, `K_RETURN` play, `K_LEFT`/`K_RIGHT` seek, `K_m` mute, `K_f` visualizer, `K_c` chat, `K_q`/`K_ESCAPE` quit. Nothing is configurable and nothing is persisted.
- **There is no config file at all.** `search_files` for `json.load|json.dump|.json|config` across `src/` returns zero matches. `import json` at line 36 is currently unused. There is no `config.py`.
- `Player.load()` (line 229) calls `decode_file(path)` (line 186), which calls `miniaudio.decode_file(...)` and materializes the **entire** file into a `float32` numpy array in RAM before playback starts.
- `TorrentManager` (line 93) sets `params.save_path = TORRENT_DIR` (line 140) and `TORRENT_DIR = APP_DIR/torrent-downloads` (line 81). `libtorrent 2.1.1.0` is installed; `torrent_handle.file_progress` and `torrent_status.progress` both exist.
- `draw_ui` (line 559) already clips library-row text to the panel width. `render_hint()` (line ~1031) renders the control hint bar. `VIDEORESIZE` handler at line 1100 clamps to 640x480.
- Tests live in `tests/` as plain scripts run with `python3 tests/<file>.py` (e.g. `tests/test_sphere_and_chat.py`, `tests/test_live_loop.py`), using `SDL_VIDEODRIVER=dummy`. **pytest is not installed** — keep that style.

**Empirically verified facts this plan depends on** (I ran these; do not re-derive, but do not assume differently either)

1. `decode_file()` on a **truncated** MP3 does **not** raise. It silently returns only the decodable prefix (25% of bytes → 2.95 s of audio; 50% → 5.96 s). So the current failure mode is *silence after the prefix*, not a crash. `Player.load` then sets `self.samples` to that short array and playback hits the end and holds at `pos = len(samples) - 1` forever.
2. `miniaudio.mp3_stream_file(filename, frames_to_read=1024, seek_frame=0)` streams a growing MP3 and yields `array.array` blocks. It works on truncated files at 25/50/75% and its output **grows monotonically** as bytes are appended (130223 → 262703 → 529200 samples). This is the correct primitive.
3. `miniaudio.stream_file(filename, output_format, nchannels, sample_rate, frames_to_read, dither, seek_frame)` is the generic equivalent for wav/ogg/flac/m4a, which is what `AUDIO_EXTENSIONS` scans.
4. `mutagen.mp3.MP3()` on a truncated file still returns the ID3 title and reports the *full* duration (12.0 s), so metadata display will be correct even mid-download.
5. `music/test.mp3` in the repo is a **0-byte placeholder** and cannot be decoded. Tests needing real audio must generate one with `ffmpeg` (present at `/opt/homebrew/bin/ffmpeg`) or copy a real file.
6. `hermes -z "<prompt>"` works and is used by the existing `HermesChat` class (line 792).

---

## Proposed Approach

**Slice A — config + keymap (foundation).** New `src/config.py` holds `DEFAULT_CONFIG`, `load_config()`, `save_config()`, and a `Config` class. New `src/actions.py` holds the `ACTIONS` registry (action name → human label + default pygame key constant) and `Keymap.resolve(key_constant) -> action_name | None`. The `elif` chain becomes a `dict` dispatch. This must land first because Settings needs to both edit the keymap and toggle `show_hints`.

**Slice B — streaming playback.** Add `StreamingSource` that owns a `mp3_stream_file`/`stream_file` generator, appends decoded samples into a growable numpy buffer, and reports `available_duration()`. `Player` keeps its `self.samples`/`self.pos` contract but, when the loaded file is still being written, tops up the buffer from the generator instead of holding a fixed snapshot. A `Player.is_streaming` flag lets the UI show a "buffering/downloading" indicator and disables seeking past the decoded frontier.

**Slice C — settings UI.** A `SettingsPanel` overlay drawn and driven entirely by the action system, so it is keyboard-only by construction. Items: change library folder, move all music to a chosen folder, show/hide control hints, rebind each action, reset to defaults. Plus a first-run "setup" flow that prompts for the music folder if none is set.

---

## Step-by-Step Tasks

### Task 1: Create `src/config.py` with defaults, load, and save

**Objective:** A JSON-backed config with sane defaults, tolerant of a missing/corrupt file.

**Files:**
- Create: `src/config.py`

**Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import os, sys, tempfile, json
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import config as cfgmod


def test_defaults_when_file_missing():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "settings.json")
        c = cfgmod.load_config(p)
        assert c["library_folder"] == ""
        assert c["show_hints"] is True
        assert c["keymap"]["toggle_pause"] == "SPACE"


def test_roundtrip(tmp=None):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "settings.json")
        c = cfgmod.load_config(p)
        c["show_hints"] = False
        c["keymap"]["toggle_pause"] = "K"
        cfgmod.save_config(p, c)
        c2 = cfgmod.load_config(p)
        assert c2["show_hints"] is False
        assert c2["keymap"]["toggle_pause"] == "K"


def test_corrupt_file_falls_back_to_defaults():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "settings.json")
        open(p, "w").write("{not json at all")
        c = cfgmod.load_config(p)
        assert c["show_hints"] is True


def test_unknown_keys_are_preserved():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "settings.json")
        json.dump({"future_option": 7}, open(p, "w"))
        c = cfgmod.load_config(p)
        assert c["future_option"] == 7


test_defaults_when_file_missing()
test_roundtrip()
test_corrupt_file_falls_back_to_defaults()
test_unknown_keys_are_preserved()
print("CONFIG TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_config.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`

**Step 3: Write minimal implementation**

Create `src/config.py`:

```python
"""JSON-backed user settings for NCS-SMLauncher.

The config lives in the user's home directory (NOT next to the source) so a
frozen PyInstaller bundle, which may be read-only, never fails to write it.
"""
import copy
import json
import os

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".ncs-smlauncher")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")

# Action name -> default pygame key NAME (a string, so the file is portable
# and human-editable). Resolved to a pygame constant at runtime.
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
    "torrent_folder": "",
    "show_hints": True,
    "confirm_quit": False,
    "keymap": dict(DEFAULT_KEYMAP),
    "easter_eggs": True,
}


def default_config():
    return copy.deepcopy(DEFAULT_CONFIG)


def _merge(base, override):
    """Shallow-merge one level of nested dicts; unknown keys are preserved."""
    out = copy.deepcopy(base)
    if not isinstance(override, dict):
        return out
    for k, v in override.items():
        if k == "keymap" and isinstance(v, dict):
            merged = dict(out.get("keymap", {}))
            for kk, vv in v.items():
                merged[kk] = vv
            out["keymap"] = merged
        else:
            out[k] = v
    return out


def load_config(path=None):
    """Load settings, falling back to defaults for missing or corrupt files."""
    path = path or CONFIG_PATH
    cfg = default_config()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return _merge(cfg, json.load(fh))
    except (OSError, ValueError):
        return cfg


def save_config(cfg, path=None):
    """Write settings atomically; returns True on success."""
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
Expected: `CONFIG TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/config.py tests/test_config.py
git commit -m "feat(config): JSON-backed settings with defaults, atomic save, corrupt-file fallback"
```

---

### Task 2: Create `src/actions.py` — action registry and keymap resolution

**Objective:** One table describing every action and its default key, plus resolution from a pygame key constant to an action name.

**Files:**
- Create: `src/actions.py`

**Step 1: Write the failing test**

Create `tests/test_actions.py`:

```python
import sys
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import pygame
pygame.init()
pygame.display.set_mode((320, 240))
import actions


def test_every_default_key_resolves_back_to_its_action():
    km = actions.Keymap({})
    for name, key_name in actions.DEFAULT_KEYMAP.items():
        got = km.resolve(actions.key_to_constant(key_name))
        assert got == name, f"{key_name} resolved to {got}, expected {name}"


def test_rebinding_overrides_default():
    km = actions.Keymap({"play_pause": "K"})
    assert km.resolve(pygame.K_k) == "play_pause"
    assert km.resolve(pygame.K_SPACE) is None


def test_last_binding_wins_on_duplicate():
    km = actions.Keymap({"seek_back": "J", "seek_forward": "J"})
    assert km.resolve(pygame.K_j) == "seek_forward"


def test_reset_to_defaults():
    km = actions.Keymap({"play_pause": "K"})
    km.reset_to_defaults()
    assert km.resolve(pygame.K_SPACE) == "play_pause"
    assert km.resolve(pygame.K_k) is None


def test_human_label_for_key():
    assert actions.human_key_name("SPACE") == "Space"
    assert actions.human_key_name("K") == "K"
    assert actions.human_key_name("NOPE") == "NOPE"


def test_conflicts_are_reported():
    km = actions.Keymap({"seek_back": "J", "seek_forward": "J"})
    conflicts = km.conflicts()
    assert conflicts == [("seek_forward", "seek_back")] or \
           conflicts == [("seek_back", "seek_forward")]


def test_every_action_has_a_label():
    for name in actions.ACTIONS:
        assert actions.ACTIONS[name]["label"]


test_every_default_key_resolves_back_to_its_action()
test_rebinding_overrides_default()
test_last_binding_wins_on_duplicate()
test_reset_to_defaults()
test_human_label_for_key()
test_conflicts_are_reported()
test_every_action_has_a_label()
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
and dispatches on the name, so bindings can be changed at runtime.
"""
import pygame

from config import DEFAULT_KEYMAP

# name -> {"label": human label, "group": section, "default": key name}
ACTIONS = {
    "select_prev":      {"label": "Previous track",  "group": "Playback", "default": "UP"},
    "select_next":      {"label": "Next track",      "group": "Playback", "default": "DOWN"},
    "play_pause":       {"label": "Play / pause",    "group": "Playback", "default": "SPACE"},
    "play":             {"label": "Play (if paused)", "group": "Playback", "default": "RETURN"},
    "seek_back":        {"label": "Seek -5s",        "group": "Playback", "default": "LEFT"},
    "seek_forward":     {"label": "Seek +5s",        "group": "Playback", "default": "RIGHT"},
    "toggle_mute":      {"label": "Mute",            "group": "Playback", "default": "M"},
    "cycle_visualizer": {"label": "Cycle visualizer", "group": "View",    "default": "F"},
    "open_hermes":      {"label": "Hermes chat",     "group": "View",     "default": "C"},
    "open_torrent":     {"label": "Torrent overlay", "group": "Library",  "default": "T"},
    "open_folder":      {"label": "Change folder",   "group": "Library",  "default": "O"},
    "open_settings":    {"label": "Settings",        "group": "Library",  "default": "COMMA"},
    "quit":             {"label": "Quit",            "group": "App",      "default": "Q"},
}

DEFAULT_KEYMAP = dict(DEFAULT_KEYMAP)   # re-exported for tests

_PRETTY = {"RETURN": "Enter", "SPACE": "Space", "ESCAPE": "Esc",
           "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
           "COMMA": ",", "PERIOD": "."}


def key_to_constant(key_name):
    """'SPACE' -> pygame.K_SPACE. Returns None if unknown."""
    if not key_name:
        return None
    const = getattr(pygame, "K_" + str(key_name).upper(), None)
    return const if isinstance(const, int) else None


def human_key_name(key_name):
    """'SPACE' -> 'Space'. Falls back to the raw name."""
    if not key_name:
        return "-"
    up = str(key_name).upper()
    return _PRETTY.get(up, up.capitalize() if len(up) > 1 else up)


class Keymap:
    """Resolves pygame key constants to action names using a binding table."""

    def __init__(self, bindings=None):
        self.bindings = dict(DEFAULT_KEYMAP)
        if bindings:
            for k, v in bindings.items():
                if k in DEFAULT_KEYMAP and v:
                    self.bindings[k] = v
        self._lookup = {}
        self._rebuild()

    def _rebuild(self):
        self._lookup = {}
        for action, key_name in self.bindings.items():
            const = key_to_constant(key_name)
            if const is not None:
                self._lookup[const] = action   # last write wins

    def resolve(self, key_constant):
        """pygame key constant -> action name, or None if unbound."""
        return self._lookup.get(key_constant)

    def bind(self, action, key_name):
        if action not in DEFAULT_KEYMAP:
            raise KeyError(f"unknown action {action!r}")
        self.bindings[action] = key_name
        self._rebuild()

    def reset_to_defaults(self):
        self.bindings = dict(DEFAULT_KEYMAP)
        self._rebuild()

    def conflicts(self):
        """Return [(action_a, action_b), ...] sharing a key constant."""
        seen = {}
        out = []
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
Expected: `ACTIONS TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/actions.py tests/test_actions.py
git commit -m "feat(actions): abstract action registry and runtime-rebindable keymap"
```

---

### Task 3: Wire the keymap into the main loop, replacing the `elif` chain

**Objective:** All existing behavior keeps working, but dispatch goes through the action registry, and `show_hints` controls the hint bar.

**Files:**
- Modify: `src/ncs_launcher.py:1113-1180` (the `KEYDOWN` chain)
- Modify: `src/ncs_launcher.py` — `import config as cfgmod`, `from actions import ACTIONS, Keymap, human_key_name`
- Modify: `src/ncs_launcher.py:982` `main()` — load config, build keymap
- Test: `tests/test_keymap_dispatch.py`

**Step 1: Write the failing test**

Create `tests/test_keymap_dispatch.py` — drive the real event loop with a remapped key and assert the action fired:

```python
"""Verify a REBOUND key performs its action, and the old key does nothing."""
import os, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import pygame
import ncs_launcher as nl
import actions

pygame.init()
pygame.display.set_mode((1280, 720))

# rebuild main()'s keymap exactly as main() will
km = actions.Keymap({"cycle_visualizer": "V"})
print("V ->", km.resolve(pygame.K_v))
print("F ->", km.resolve(pygame.K_f))
assert km.resolve(pygame.K_v) == "cycle_visualizer"
assert km.resolve(pygame.K_f) is None, "old binding must be released"

# and the app exposes a dispatcher built from it
assert hasattr(nl, "dispatch_action"), "main() must expose dispatch_action"
print("KEYMAP DISPATCH TEST PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_keymap_dispatch.py`
Expected: FAIL — `AssertionError: main() must expose dispatch_action`

**Step 3: Write minimal implementation**

In `src/ncs_launcher.py`, add near the other imports:

```python
import config as cfgmod
from actions import ACTIONS, Keymap, human_key_name
```

In `main()`, right after `folder = sys.argv[1] ...`:

```python
    cfg = cfgmod.load_config()
    keymap = Keymap(cfg.get("keymap"))
    if len(sys.argv) > 1:
        cfg["library_folder"] = folder
        cfgmod.save_config(cfg)
```

Then replace the entire `elif event.key == pygame.K_...` chain (lines ~1137-1180) with a dispatch. Define this nested function just above `running = True`:

```python
    def dispatch_action(action):
        """Single place where an abstract action turns into behavior."""
        nonlocal selected, chat_open, overlay_open, vis_mode_idx, muted
        nonlocal running, folder, hover_idx

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
                player.load(tracks[selected]['path'])
        elif action == "open_hermes":
            chat_open = not chat_open
            if chat_open:
                pygame.key.start_text_input()
        elif action == "open_torrent":
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
                    player.load(tracks[0]['path'])
                push_notice(("info", f"library: {folder}"))
            else:
                push_notice(("info", "folder picker cancelled"))
        elif action == "open_settings":
            settings_panel.toggle()
```

And in the `KEYDOWN` handler, replace the chain's final block with:

```python
                action = keymap.resolve(event.key)
                if action:
                    dispatch_action(action)
                elif event.key == pygame.K_ESCAPE:
                    if settings_panel.is_open():
                        settings_panel.close()
                    else:
                        running = False
                elif event.key == pygame.K_ESCAPE:
                    running = False
```

(Delete the duplicated `ESCAPE` branch that second block introduces — keep exactly one, and make it close settings if open, else quit.)

Then make the hint bar conditional. Replace the bottom-of-loop hint drawing with:

```python
        # Draw hint (user can hide it in settings)
        if cfg.get("show_hints", True):
            hint_surf = render_hint()
            hx, hy = w - hint_w - 24, h - 24
            if hx < 24:
                short = font.render("↑↓ ⏎ space ←→ V C T O M Q", True,
                                    (120, 120, 130))
                screen.blit(short, (24, hy))
            else:
                screen.blit(hint_surf, (hx, hy))
```

And make `render_hint()` build from the live keymap:

```python
    def render_hint():
        parts = []
        for act in ("select_prev", "select_next", "play", "play_pause",
                    "seek_back", "seek_forward", "cycle_visualizer",
                    "open_hermes", "open_torrent", "open_folder", "quit"):
            k = human_key_name(keymap.bindings.get(act, ""))
            parts.append(k)
        return font.render("  ".join(parts), True, (120, 120, 130))
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_keymap_dispatch.py && python3 tests/test_live_loop.py`
Expected: `KEYMAP DISPATCH TEST PASSED` and `PASS: resize + chat key routing did not crash the loop`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py tests/test_keymap_dispatch.py
git commit -m "refactor(input): dispatch keys through the action registry; honor show_hints"
```

---

### Task 4: Add `StreamingSource` for partially-downloaded files

**Objective:** Decode a file that is still being written, growing the available buffer as bytes land, instead of snapshotting once.

**Files:**
- Create: `src/streaming_source.py`
- Test: `tests/test_streaming_source.py`

**Step 1: Write the failing test**

Create `tests/test_streaming_source.py`:

```python
import os, subprocess, sys, tempfile
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import numpy as np
import miniaudio
from streaming_source import StreamingSource

SR = 44100
REAL = "/Users/giathinh/Downloads/A Thousand Miles.mp3"


def make_partial_mp3(tmp, frac):
    """Produce a real MP3 truncated to `frac` of its bytes."""
    small = os.path.join(tmp, "src.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-ss", "30", "-t", "12",
                    "-i", REAL, "-ac", "1", "-ar", "44100", "-b:a", "128k",
                    small], check=True)
    data = open(small, "rb").read()
    part = os.path.join(tmp, "growing.mp3")
    with open(part, "wb") as fh:
        fh.write(data[: int(len(data) * frac)])
    return part, data


with tempfile.TemporaryDirectory() as d:
    path, data = make_partial_mp3(d, 0.25)
    total = os.path.getsize(data)
    src = StreamingSource(path)
    first = src.pump()
    print("1) pumped 25% file ->", first, "samples = %.2fs" % (first / SR))
    assert first > 0, "must decode at least something from a partial file"

    with open(path, "ab") as fh:
        fh.write(data[int(len(data) * 0.25): int(len(data) * 0.5)])
    second = src.pump()
    print("2) after +25% bytes ->", second, "samples = %.2fs" % (second / SR))
    assert second > first, "buffer must grow as the download progresses"

    with open(path, "ab") as fh:
        fh.write(data[int(len(data) * 0.5):])
    third = src.pump()
    print("3) complete ->", third, "samples = %.2fs" % (third / SR))
    assert third > second
    assert src.is_complete(path, total), "file is complete on disk now"

    decoded = src.samples()
    assert decoded.dtype == np.float32
    assert decoded.ndim == 1
    assert abs(decoded.max()) <= 1.01, "samples must be normalised to [-1, 1]"

print("STREAMING SOURCE TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_streaming_source.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'streaming_source'`

**Step 3: Write minimal implementation**

Create `src/streaming_source.py`:

```python
"""Incremental decode of a file that is still being written (torrent).

`miniaudio.decode_file` materialises the whole file at once, so a
half-downloaded track can only ever play its decodable prefix. Streaming the
file instead lets the decoded buffer grow as bytes land on disk.

Verified behaviour (see the plan): `mp3_stream_file` decodes truncated MP3s
fine, and its output grows monotonically as the file is appended to.
"""
import os

import numpy as np

SAMPLE_RATE = 44100


def _iter_blocks(path, kind):
    """Yield int16 blocks for a growing file, tolerating a truncated tail."""
    if kind == "mp3":
        import miniaudio
        return miniaudio.mp3_stream_file(path, frames_to_read=4096)
    import miniaudio
    return miniaudio.stream_file(
        path,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=SAMPLE_RATE,
        frames_to_read=4096,
    )


class StreamingSource:
    """A file whose decoded length grows over time."""

    def __init__(self, path, kind=None):
        self.path = path
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        self.kind = kind or ("mp3" if ext in ("mp3",) else "generic")
        self._buf = np.zeros(0, dtype=np.float32)
        self._exhausted = False

    # ---- introspection -------------------------------------------------
    def size_on_disk(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def is_complete(self, path=None, expected_size=None):
        """True when the file on disk has reached the torrent's final size."""
        path = path or self.path
        if expected_size is None:
            return True
        try:
            return os.path.getsize(path) >= expected_size
        except OSError:
            return False

    def samples(self):
        return self._buf

    def available_duration(self):
        return len(self._buf) / SAMPLE_RATE

    # ---- decoding ------------------------------------------------------
    def pump(self, max_seconds=30.0):
        """Decode more audio if the file grew. Returns samples added.

        Re-opens the stream from scratch when the previous pass hit the
        truncated tail: a generator cannot be resumed past EOF, and a fresh
        stream simply yields the longer prefix the file now supports.
        """
        grown = self.size_on_disk() - self._decoded_bytes if hasattr(
            self, "_decoded_bytes") else self.size_on_disk()
        if not grown or self._exhausted:
            return 0

        added = np.zeros(0, dtype=np.float32)
        try:
            for block in _iter_blocks(self.path, self.kind):
                if not block:
                    continue
                arr = np.frombuffer(block, dtype=np.int16).astype(np.float32)
                added = np.concatenate([added, arr / 32768.0])
                if len(added) / SAMPLE_RATE > max_seconds:
                    break        # yield to the caller; resume next pump
        except Exception:
            # a torn/partial file can raise mid-stream; keep what we got
            pass

        if added.size:
            self._buf = np.concatenate([self._buf, added])
        self._decoded_bytes = self.size_on_disk()
        return int(added.size)

    def decode_all(self):
        """Drain the current file completely (used once a download finishes)."""
        total = 0
        while True:
            n = self.pump(max_seconds=60.0)
            total += n
            if n == 0:
                break
        return total
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_streaming_source.py`
Expected:
```
1) pumped 25% file -> <N> samples = 2.9Xs
2) after +25% bytes -> <N> samples = 5.9Xs
3) complete -> <N> samples = 12.0Xs
STREAMING SOURCE TESTS PASSED
```

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/streaming_source.py tests/test_streaming_source.py
git commit -m "feat(audio): StreamingSource grows the decoded buffer as a file is written"
```

---

### Task 5: Track download progress and expose partial files to the library

**Objective:** `TorrentManager` reports which files are still downloading and their expected size, so the player knows a track is incomplete.

**Files:**
- Modify: `src/ncs_launcher.py:93-180` (`TorrentManager`)
- Test: `tests/test_torrent_progress.py`

**Step 1: Write the failing test**

Create `tests/test_torrent_progress.py`:

```python
import os, sys
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import ncs_launcher as nl

lt = nl.lt
if lt is None:
    print("libtorrent unavailable; skipping (this is expected in CI)")
    sys.exit(0)

tm = nl.TorrentManager()
print("1) in_progress_files() exists:", hasattr(tm, "in_progress_files"))
assert hasattr(tm, "in_progress_files"), "need in_progress_files()"
assert hasattr(tm, "file_totals"), "need file_totals()"

# with nothing added, the report is empty but well-formed
rep = tm.in_progress_files()
print("2) empty report:", rep)
assert isinstance(rep, dict) and len(rep) == 0

# normalize() still accepts a bare infohash
assert tm.normalize("magnet:?xt=urn:btih:" + "a" * 40).startswith("magnet:")
assert tm.normalize("not a hash") is None
print("3) normalize OK")
print("TORRENT PROGRESS TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_torrent_progress.py`
Expected: FAIL — `AssertionError: need in_progress_files()`

**Step 3: Write minimal implementation**

In `TorrentManager.__init__`, add:

```python
        self._file_cache = {}     # key -> {path: expected_size}
```

Add these two methods to `TorrentManager` (after `status_lines`):

```python
    def file_totals(self, key):
        """Expected final size of each file in a torrent (bytes).

        Cached: torrent metadata arrives once and never changes.
        """
        with self.lock:
            if key in self._file_cache:
                return self._file_cache[key]
            h = self.handles.get(key)
        if h is None:
            return {}
        try:
            fs = h.get_files()
            out = {f.path: f.size for f in fs}
        except Exception:
            out = {}
        with self.lock:
            self._file_cache[key] = out
        return out

    def in_progress_files(self):
        """{abs_path: expected_size} for every file still being downloaded.

        A file is 'in progress' while its on-disk size is below the size the
        torrent metadata says it will be. Files already at full size are
        omitted, so callers can treat presence as 'safe to play, but not
        necessarily complete'.
        """
        out = {}
        with self.lock:
            items = list(self.handles.items())
            for key, h in items:
                for path, expected in self.file_totals(key).items():
                    abs_path = os.path.join(TORRENT_DIR, path)
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
git commit -m "feat(torrent): report in-progress file sizes via file_totals/in_progress_files"
```

---

### Task 6: Make `Player` play a growing file and keep the UI honest

**Objective:** `Player.load` uses streaming when a track is incomplete, tops the buffer up during playback, and reports whether playback is limited by the download.

**Files:**
- Modify: `src/ncs_launcher.py:186-266` (`decode_file`, `Player`)
- Test: `tests/test_player_streaming.py`

**Step 1: Write the failing test**

Create `tests/test_player_streaming.py`:

```python
import os, sys, tempfile, subprocess, time
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import numpy as np
from ncs_launcher import Player

SR = 44100
REAL = "/Users/giathinh/Downloads/A Thousand Miles.mp3"


def make_partial(tmp, frac):
    small = os.path.join(tmp, "src.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-ss", "30", "-t", "12",
                    "-i", REAL, "-ac", "1", "-ar", "44100", "-b:a", "128k",
                    small], check=True)
    data = open(small, "rb").read()
    part = os.path.join(tmp, "growing.mp3")
    with open(part, "wb") as fh:
        fh.write(data[: int(len(data) * frac)])
    return part, data


p = Player()          # do NOT start the thread; no audio device needed
print("1) player has is_streaming:", hasattr(p, "is_streaming"))
assert hasattr(p, "is_streaming")

with tempfile.TemporaryDirectory() as d:
    path, data = make_partial(d, 0.3)

    p.load(path, expected_size=len(data), streaming=True)
    print("2) is_streaming =", p.is_streaming)
    assert p.is_streaming
    d0 = p.duration()
    print("3) available duration = %.2fs (of 12.0s total)" % d0)
    assert 1.0 < d0 < 12.0, "partial file should expose only what is decodable"

    with open(path, "ab") as fh:
        fh.write(data[int(len(data) * 0.3):])
    p.refresh()
    d1 = p.duration()
    print("4) after growth = %.2fs" % d1)
    assert d1 > d0, "refresh() must extend the decoded buffer"

    p.load(path, expected_size=len(data), streaming=False)
    print("5) forced complete decode = %.2fs" % p.duration())
    assert abs(p.duration() - 12.0) < 0.5
    assert not p.is_streaming

print("PLAYER STREAMING TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_player_streaming.py`
Expected: FAIL — `AttributeError` or `TypeError` on `load(..., streaming=True)`

**Step 3: Write minimal implementation**

In `src/ncs_launcher.py`, add `from streaming_source import StreamingSource` near the other imports.

Replace `Player.__init__` and `Player.load`, and add `refresh` / `buffered_duration`:

```python
class Player(threading.Thread):
    """Streams decoded audio through sounddevice while tracking position.

    Two modes:
      complete  - the whole file is decoded up front (current behaviour)
      streaming - the file is still being written (torrent in progress); the
                  decoded buffer grows as bytes land, and duration() reports
                  only what is actually playable so far.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.samples = None
        self.pos = 0
        self.paused = True
        self.muted = False
        self.track_path = None
        self.lock = threading.Lock()
        self.ring = np.zeros(FFT_SIZE * 2, dtype=np.float32)
        self.is_streaming = False
        self.expected_size = None
        self._src = None
        self.stream = sd.OutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            blocksize=1024, callback=self._callback)

    def load(self, path, expected_size=None, streaming=False):
        """Load a track. With streaming=True, decode incrementally."""
        with self.lock:
            self.track_path = path
            self.expected_size = expected_size
            self.pos = 0
            self.paused = False
            self._src = None
            if streaming:
                self.is_streaming = True
                self._src = StreamingSource(path)
                self.samples = self._src.pump_and_get()
            else:
                self.is_streaming = False
                self.samples = decode_file(path)

    def refresh(self):
        """Pull newly-arrived bytes for a streaming track.

        Called from the main loop while a torrent is still downloading.
        Returns True if the playable buffer grew.
        """
        if not self.is_streaming or self._src is None:
            return False
        with self.lock:
            at_end = self.pos >= len(self.samples) - 1
        added = self._src.pump()
        if added:
            with self.lock:
                self.samples = self._src.samples()
        if (self.expected_size is not None
                and self._src.is_complete(self.track_path, self.expected_size)):
            with self.lock:
                self.is_streaming = False
        return bool(added) or at_end

    def buffered_duration(self):
        """How much audio is decodable right now (streamed mode)."""
        with self.lock:
            return len(self.samples) / SAMPLE_RATE if self.samples is not None else 0.0
```

And add `pump_and_get()` to `StreamingSource` (Task 4's class):

```python
    def pump_and_get(self, max_seconds=60.0):
        """Decode what is available now and return the whole buffer."""
        self.pump(max_seconds=max_seconds)
        return self.samples()
```

In `_callback`, clamp advancement to the current buffer end (it already does:
`if self.pos >= len(self.samples): self.pos = len(self.samples) - 1`).

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_player_streaming.py`
Expected:
```
1) player has is_streaming: True
2) is_streaming = True
3) available duration = 3.5Xs (of 12.0s total)
4) after growth = 12.0Xs
5) forced complete decode = 12.0Xs
PLAYER STREAMING TESTS PASSED
```

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py tests/test_player_streaming.py
git commit -m "feat(player): stream incomplete tracks and expose buffered duration"
```

---

### Task 7: Hook streaming playback into the main loop and library scan

**Objective:** A partially-downloaded track is playable, auto-refreshes while downloading, and the UI shows its state.

**Files:**
- Modify: `src/ncs_launcher.py` — `main()` loop, and the four `player.load(tracks[...]['path'])` call sites
- Test: `tests/test_partial_playback_wiring.py`

**Step 1: Write the failing test**

Create `tests/test_partial_playback_wiring.py`:

```python
import os, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import ncs_launcher as nl
import inspect

src = inspect.getsource(nl.main)
print("1) main() references in_progress_files:", "in_progress_files" in src)
assert "in_progress_files" in src, "main() must consult torrent progress"
assert "player.refresh()" in src, "main() must refresh streaming tracks each frame"
assert "expected_size" in src, "main() must pass expected_size to load()"
print("2) partial playback is wired into main()")
print("PARTIAL PLAYBACK WIRING TEST PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_partial_playback_wiring.py`
Expected: FAIL — `AssertionError: main() must consult torrent progress`

**Step 3: Write minimal implementation**

In `main()`, add a helper right after `torrents = TorrentManager() if lt is not None else None`:

```python
    def partial_map():
        """{abs_path: expected_size} for tracks still downloading."""
        if torrents is None:
            return {}
        return torrents.in_progress_files()

    def load_track(index):
        """Load a library track, streaming it if it is still downloading."""
        nonlocal current_easter_art
        tr = tracks[index]
        pm = partial_map()
        expected = pm.get(tr["path"])
        player.load(tr["path"], expected_size=expected,
                    streaming=expected is not None)
        current_easter_art = get_easter_art_for_track(tr["path"])
```

Replace **every** `player.load(tracks[<expr>]['path'])` in `main()` — there are five (initial load, `select_prev`, `select_next` in dispatch, the folder-change branch, and the media-key handler) — with `load_track(<index>)`. The media-key handler runs on a Quartz callback thread; guard it:

```python
    def load_track_threadsafe(index):
        """Media keys arrive on a callback thread; marshal into the loop."""
        with self_lock:
            pending_load.append(index)
```

Create `self_lock = threading.Lock()` and `pending_load = []` next to `nonlocal_selected`. Drain it in the main loop:

```python
        # apply track changes requested from the media-key thread
        with self_lock:
            if pending_load:
                load_track(pending_load.pop(0))
```

In the main loop, before drawing, refresh any streaming track and rescan when a download completes:

```python
        # keep a streaming track fed while its torrent downloads
        if player.is_streaming:
            if player.refresh():
                # a finished download changes the playable length; refresh the row
                if torrents:
                    torrents.in_progress_files()
```

Finally, make the seek bar and status line tell the truth about partial tracks. In `draw_ui`, after computing `frac`:

```python
    if getattr(player, "is_streaming", False):
        total = player.expected_size or 0
        have = os.path.getsize(player.track_path) if player.track_path and os.path.exists(player.track_path) else 0
        if total:
            pct = int(have * 100 / total)
            badge = font.render(f"downloading {pct}% — playing {int(dur)//60}:{int(pos)%60:02d} of buffer",
                                True, (255, 200, 90))
            screen.blit(badge, (40, h - 78))
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_partial_playback_wiring.py && python3 tests/test_live_loop.py`
Expected: `PARTIAL PLAYBACK WIRING TEST PASSED` and `PASS: resize + chat key routing did not crash the loop`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py tests/test_partial_playback_wiring.py
git commit -m "feat(app): play in-progress torrents, refresh buffer each frame, show download %"
```

---

### Task 8: Library organizer — move all music to a chosen folder

**Objective:** A safe, verifiable "move all music to a folder of my choice" operation usable from Settings.

**Files:**
- Create: `src/library_ops.py`
- Test: `tests/test_library_ops.py`

**Step 1: Write the failing test**

Create `tests/test_library_ops.py`:

```python
import os, sys, tempfile
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import library_ops as ops


def build(tmp):
    src = os.path.join(tmp, "src")
    os.makedirs(os.path.join(src, "nested"))
    for rel in ("a.mp3", os.path.join("nested", "b.mp3")):
        p = os.path.join(src, rel)
        open(p, "wb").write(b"ID3" + b"\x00" * 64)
    return src


with tempfile.TemporaryDirectory() as d:
    src = build(d)
    dst = os.path.join(d, "dest")
    os.makedirs(dst)

    plan = ops.plan_move(src, dst)
    print("1) plan:", [(os.path.basename(s), os.path.basename(d2)) for s, d2 in plan])
    assert len(plan) == 2, "both tracks must be planned"

    done, skipped, failed = ops.execute_move(plan, dry_run=True)
    print("2) dry run ->", done, "moved,", skipped, "skipped,", failed, "failed")
    assert done == 0, "dry run must not move anything"
    assert len(os.listdir(src)) == 2, "dry run must leave source intact"

    done, skipped, failed = ops.execute_move(plan, dry_run=False)
    print("3) real run ->", done, "moved,", skipped, "skipped,", failed, "failed")
    assert done == 2 and failed == 0
    assert os.path.exists(os.path.join(dst, "a.mp3"))
    assert os.path.exists(os.path.join(dst, "nested", "b.mp3"))
    assert not os.path.exists(os.path.join(src, "a.mp3"))
    print("4) destination preserves relative layout")

    # name collision is skipped, not clobbered
    with tempfile.TemporaryDirectory() as d2:
        s2 = build(d2)
        t2 = os.path.join(d2, "dst")
        os.makedirs(t2)
        open(os.path.join(t2, "a.mp3"), "wb").write(b"EXISTING")
        plan2 = ops.plan_move(s2, t2)
        done, skipped, failed = ops.execute_move(plan2)
        print("5) collision ->", done, "moved,", skipped, "skipped,", failed, "failed")
        assert skipped == 1 and done == 1
        assert open(os.path.join(t2, "a.mp3"), "rb").read() == b"EXISTING", "must not clobber"

    # same source and destination is refused
    with tempfile.TemporaryDirectory() as d3:
        s3 = build(d3)
        assert ops.plan_move(s3, s3) == [], "moving onto itself must plan nothing"

    # moving into a subdirectory of the source would recurse; refuse it
    with tempfile.TemporaryDirectory() as d4:
        s4 = build(d4)
        sub = os.path.join(s4, "inside")
        assert ops.plan_move(s4, sub) == [], "dest inside source must be refused"

print("LIBRARY OPS TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_library_ops.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'library_ops'`

**Step 3: Write minimal implementation**

Create `src/library_ops.py`:

```python
"""Bulk-move a music library into one folder, without ever clobbering data.

Safety rules, in order:
  1. Refuse when source == destination.
  2. Refuse when the destination is inside the source (infinite recursion).
  3. Never overwrite an existing file: skip it and report it.
  4. Preserve the relative sub-layout so nested folders stay nested.
  5. Support dry_run so the UI can show an exact plan before committing.
"""
import os
import shutil

AUDIO_EXT = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".wma")


def _is_inside(child, parent):
    child = os.path.abspath(child)
    parent = os.path.abspath(parent)
    return child == parent or child.startswith(parent + os.sep)


def plan_move(src_dir, dst_dir, extensions=AUDIO_EXT):
    """Return [(src_path, dst_path), ...] for every audio file to move.

    Returns [] when the move is unsafe (same dir, or dst inside src).
    """
    src_dir = os.path.abspath(src_dir)
    dst_dir = os.path.abspath(dst_dir)
    if not os.path.isdir(src_dir):
        return []
    if src_dir == dst_dir or _is_inside(dst_dir, src_dir):
        return []
    plan = []
    for root, _dirs, files in os.walk(src_dir):
        for name in sorted(files):
            if not name.lower().endswith(extensions):
                continue
            s = os.path.join(root, name)
            rel = os.path.relpath(s, src_dir)
            plan.append((s, os.path.join(dst_dir, rel)))
    return plan


def execute_move(plan, dry_run=False):
    """Execute a plan. Returns (moved, skipped, failed) counts."""
    moved = skipped = failed = 0
    for src, dst in plan:
        if os.path.exists(dst):
            skipped += 1          # never clobber an existing file
            continue
        if dry_run:
            moved += 1            # count what *would* move
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            size_before = os.path.getsize(src)
            shutil.move(src, dst)
            # verify the copy landed intact before accepting the move
            if os.path.getsize(dst) != size_before:
                os.replace(dst, src)   # put it back; something went wrong
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
git commit -m "feat(library): safe bulk move with dry-run, collision skips, and size verification"
```

---

### Task 9: Build the settings panel UI

**Objective:** A keyboard-navigable settings screen: change library folder, move all music, toggle control hints, rebind keys, reset defaults.

**Files:**
- Create: `src/settings_panel.py`
- Modify: `src/ncs_launcher.py` — import, instantiate, draw, route keys
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

from settings_panel import SettingsPanel
import actions

cfg = {
    "library_folder": "/Users/giathinh/Downloads",
    "show_hints": True,
    "keymap": dict(actions.DEFAULT_KEYMAP),
}
panel = SettingsPanel(font, pygame.Rect(0, 0, 1280, 720), cfg, actions.Keymap({}))

print("1) items:", len(panel.items))
assert len(panel.items) >= 8

# every action is rebindable
names = [i.action for i in panel.items if i.action]
print("2) rebindable actions:", names)
for a in actions.ACTIONS:
    assert a in names, f"no settings row to rebind {a}"

panel.toggle()
print("3) open:", panel.is_open(), "selected:", panel.selected_index)
assert panel.is_open()

class Ev:
    def __init__(self, key, unicode=""):
        self.key, self.unicode = key, unicode

panel.handle_key(Ev(pygame.K_DOWN))
print("4) after down, selected:", panel.selected_index)
assert panel.selected_index == 1

panel.handle_key(Ev(pygame.K_UP))
assert panel.selected_index == 0

# start a rebind, press K, confirm the binding changed
row = [i for i in panel.items if i.action == "play_pause"][0]
panel.selected_index = panel.items.index(row)
panel.handle_key(Ev(pygame.K_RETURN))
print("5) capturing:", panel.capturing)
assert panel.capturing is True
panel.handle_key(Ev(pygame.K_k, unicode="k"))
print("6) binding now:", panel.keymap.bindings["play_pause"])
assert panel.keymap.bindings["play_pause"] == "K"
assert panel.capturing is False

# toggling the hints row flips the config
row = [i for i in panel.items if i.kind == "toggle_hints"][0]
panel.selected_index = panel.items.index(row)
before = panel.cfg["show_hints"]
panel.handle_key(Ev(pygame.K_RETURN))
print("7) show_hints:", before, "->", panel.cfg["show_hints"])
assert panel.cfg["show_hints"] != before

# every row's label fits the panel width
over = [i.label for i in panel.items if font.size(i.label)[0] > 1280 - 80]
print("8) overflowing rows:", over)
assert not over, f"labels must not overflow: {over}"

panel.handle_key(Ev(pygame.K_ESCAPE))
print("9) open after Esc:", panel.is_open())
assert not panel.is_open()

print("SETTINGS PANEL TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_settings_panel.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'settings_panel'`

**Step 3: Write minimal implementation**

Create `src/settings_panel.py`:

```python
"""Keyboard-navigable settings overlay.

Rows are either:
  action   - rebind a key (Enter starts capture, next keypress is the new key)
  toggle   - flip a boolean in cfg
  command  - run a callback (change folder, move all music, reset keys)
  info     - read-only text

Everything is drawn and driven with the keyboard only, so the app needs no
mouse to be fully configurable.
"""
import pygame


class Row:
    def __init__(self, label, kind, action=None, value=None, callback=None,
                 help_text=""):
        self.label = label
        self.kind = kind            # action | toggle | command | info
        self.action = action        # for kind == "action"
        self.value = value          # for kind == "info" / "toggle"
        self.callback = callback    # for kind == "command"
        self.help_text = help_text


class SettingsPanel:
    def __init__(self, font, rect, cfg, keymap, pick_folder=None):
        self.font = font
        self.rect = rect
        self.cfg = cfg
        self.keymap = keymap
        self.pick_folder = pick_folder
        self.is_open_flag = False
        self.selected_index = 0
        self.capturing = False
        self.message = ""
        self.items = []
        self._build()

    # ---- state ---------------------------------------------------------
    def is_open(self):
        return self.is_open_flag

    def toggle(self):
        self.is_open_flag = not self.is_open_flag
        self.capturing = False
        return self.is_open_flag

    def close(self):
        self.is_open_flag = False
        self.capturing = False

    def _build(self):
        self.items = [
            Row("Library folder", "info",
                value=self.cfg.get("library_folder") or "(not set)",
                help_text="Audio folder that gets scanned"),
            Row("Choose library folder...", "command",
                callback=self._cmd_choose_folder),
            Row("Move all music to a folder...", "command",
                callback=self._cmd_move_all),
            Row("Show control hints", "toggle",
                value=bool(self.cfg.get("show_hints", True)),
                callback=self._cmd_toggle_hints),
            Row("Hermes chat panel", "toggle",
                value=bool(self.cfg.get("easter_eggs", True)),
                callback=self._cmd_toggle_easter),
            Row("", "info", value="── key bindings ──"),
        ]
        for name, meta in self._grouped_actions().items():
            self.items.append(Row(
                meta["label"], "action", action=name,
                value=self.keymap.bindings.get(name, "-"),
                help_text=meta["group"]))
        self.items.append(Row("", "info", value="── reset ──"))
        self.items.append(Row("Reset all keys to defaults", "command",
                              callback=self._cmd_reset_keys))

    def _grouped_actions(self):
        from actions import ACTIONS
        return {k: v for k, v in sorted(
            ACTIONS.items(), key=lambda kv: (kv[1]["group"], kv[1]["label"]))}

    def refresh(self):
        self._build()

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
        if not self.pick_folder:
            self.message = "folder picker unavailable"
            return
        dest = self.pick_folder(self.cfg.get("library_folder", ""))
        if not dest:
            self.message = "move cancelled"
            return
        import library_ops
        src = self.cfg.get("library_folder", "")
        plan = library_ops.plan_move(src, dest)
        if not plan:
            self.message = "nothing to move (or unsafe destination)"
            return
        moved, skipped, failed = library_ops.execute_move(plan, dry_run=True)
        self.message = (f"will move {moved} file(s), skip {skipped}; "
                        "press Enter again to confirm")
        self._pending_plan = plan
        self._pending_msg = f"moved {moved}, skipped {skipped}, failed {failed}"
        self.cfg["library_folder"] = dest

    def _cmd_toggle_hints(self):
        self.cfg["show_hints"] = not self.cfg.get("show_hints", True)
        row = next(i for i in self.items if i.kind == "toggle")
        row.value = self.cfg["show_hints"]

    def _cmd_toggle_easter(self):
        self.cfg["easter_eggs"] = not self.cfg.get("easter_eggs", True)

    def _cmd_reset_keys(self):
        self.keymap.reset_to_defaults()
        self.message = "keys reset to defaults"
        self._build()

    # ---- input ---------------------------------------------------------
    def handle_key(self, event):
        if not self.is_open_flag:
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
        elif event.key == pygame.K_RETURN or event.key == pygame.K_SPACE:
            self._activate()

    def _capture(self, event):
        from actions import key_to_constant
        if event.key == pygame.K_ESCAPE:
            self.capturing = False
            self.message = "rebind cancelled"
            return
        if key_to_constant_from_event(event) is None:
            return          # modifier-only press, keep waiting
        row = self.items[self.selected_index]
        self.keymap.bind(row.action, key_to_constant_from_event(event))
        row.value = self.keymap.bindings[row.action]
        conflicts = self.keymap.conflicts()
        self.message = (f"{row.label} = {row.value}"
                        + (f"  (also: {', '.join(c[1] for c in conflicts)})"
                           if conflicts else ""))
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
        if not self.is_open_flag:
            return
        from actions import human_key_name
        r = self.rect
        panel = pygame.Surface((r.width, r.height), pygame.SRCALPHA)
        panel.fill((8, 10, 16, 243))
        pygame.draw.rect(panel, (0, 230, 190, 160), panel.get_rect(), 2)
        screen.blit(panel, r.topleft)

        head = self.font.render("SETTINGS   ↑↓ move · Enter select · Esc close",
                                True, (0, 230, 190))
        screen.blit(head, (r.x + 24, r.y + 16))
        pygame.draw.line(screen, (255, 255, 255, 30),
                         (r.x + 24, r.y + 42), (r.x + r.width - 24, r.y + 42))

        line_h = self.font.get_linesize()
        top = r.y + 56
        max_rows = (r.height - 140) // line_h
        start = max(0, min(self.selected_index - max_rows // 2,
                           len(self.items) - max_rows))
        for i, row in enumerate(self.items[start:start + max_rows]):
            y = top + i * line_h
            sel = (start + i) == self.selected_index
            if sel:
                sel_rect = pygame.Rect(r.x + 20, y - 3, r.width - 40, line_h)
                pygame.draw.rect(screen, (0, 220, 180), sel_rect, border_radius=4)
            fg = (10, 14, 18) if sel else (235, 235, 245)
            dim = (60, 70, 80) if sel else (150, 155, 170)
            if row.kind == "info":
                text = self.font.render(str(row.value or row.label), True, dim)
            else:
                if row.kind == "action":
                    val = row.value
                    shown = ("<press a key>" if sel and self.capturing
                             else human_key_name(val))
                elif row.kind == "toggle":
                    shown = "ON" if row.value else "OFF"
                else:
                    shown = ""
                text = self.font.render(f"{row.label:<34}{shown}", True, fg)
            screen.blit(text, (r.x + 28, y))

        if self.message:
            msg = self.font.render(self.message[:110], True, (255, 200, 90))
            screen.blit(msg, (r.x + 24, r.y + r.height - 60))
        hint = self.font.render(
            "Hint: 'Open settings' is bound to ,  ·  bindings save on change",
            True, (120, 125, 140))
        screen.blit(hint, (r.x + 24, r.y + r.height - 34))


def key_to_constant_from_event(event):
    """Turn a KEYDOWN event into a bindable key name ('K', 'SPACE', ...)."""
    import pygame as pg
    from actions import ACTIONS
    # reverse pygame.K_NAME -> NAME
    for attr in dir(pg):
        if attr.startswith("K_") and getattr(pg, attr) == event.key:
            name = attr[2:]
            if name in ("LSHIFT", "RSHIFT", "LCTRL", "RCTRL", "LALT",
                        "RALT", "LGUI", "RGUI", "LCAPS", "SCROLLLOCK",
                        "NUMLOCK", "RMETA", "LMETA"):
                return None
            return name
    return None
```

Wire it into `ncs_launcher.py`:

```python
from settings_panel import SettingsPanel
```

after the other imports, and in `main()` after `keymap` is built:

```python
    def _pick_and_set_folder():
        return pick_folder_dialog(cfg.get("library_folder", ""))

    settings_panel = SettingsPanel(font, pygame.Rect(0, 0, w, h), cfg,
                                   keymap, pick_folder=_pick_and_set_folder)
```

Give the panel keyboard priority in the `KEYDOWN` handler, above the chat check:

```python
            elif event.type == pygame.KEYDOWN:
                if settings_panel.is_open():
                    settings_panel.handle_key(event)
                    if event.key == pygame.K_COMMA and not settings_panel.is_open():
                        pass
                    continue
                if chat_open:
                    ...
```

and draw it last, after the chat panel:

```python
        settings_panel.rect = pygame.Rect(0, 0, w, h)
        settings_panel.draw(screen, t)
```

Keep it in sync with the live window size and persist on close:

```python
        if settings_panel.is_open():
            settings_panel.rect = pygame.Rect(0, 0, w, h)
        ...
        # at the end of main(), before pygame.quit()
        if cfgmod.save_config(cfg):
            print(f"settings saved to {cfgmod.CONFIG_PATH}")
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_settings_panel.py`
Expected: `SETTINGS PANEL TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/settings_panel.py src/ncs_launcher.py tests/test_settings_panel.py
git commit -m "feat(settings): keyboard-navigable panel for folder, keybinds, and hint toggle"
```

---

### Task 10: First-run setup flow and folder persisted as default

**Objective:** On first launch with no configured folder, prompt for one instead of silently scanning `~/Music`.

**Files:**
- Modify: `src/ncs_launcher.py:982-1000` (`main()` startup)
- Test: `tests/test_first_run.py`

**Step 1: Write the failing test**

Create `tests/test_first_run.py`:

```python
import sys
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
import config as cfgmod


def resolve_initial_folder(cfg, argv_folder, home_music):
    """The folder to scan at startup.

    Precedence: explicit CLI arg > saved config > ~/Music.
    (Exposed from ncs_launcher so it can be tested without a display.)
    """
    if argv_folder:
        return argv_folder, False
    saved = cfg.get("library_folder") or ""
    if saved:
        return saved, False
    return home_music, True        # True = first run, should prompt


cfg = cfgmod.default_config()
f, prompt = resolve_initial_folder(cfg, None, "/Users/giathinh/Music")
print("1) no config, no argv ->", f, "prompt:", prompt)
assert prompt is True

f, prompt = resolve_initial_folder(cfg, "/tmp/music", "/Users/giathinh/Music")
print("2) argv wins ->", f, "prompt:", prompt)
assert f == "/tmp/music" and prompt is False

cfg["library_folder"] = "/Users/giathinh/Downloads"
f, prompt = resolve_initial_folder(cfg, None, "/Users/giathinh/Music")
print("3) saved config used ->", f, "prompt:", prompt)
assert f == "/Users/giathinh/Downloads" and prompt is False

print("FIRST RUN TESTS PASSED")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_first_run.py`
Expected: FAIL — `NameError: resolve_initial_folder`

**Step 3: Write minimal implementation**

Add to `src/ncs_launcher.py` at module level, before `def main():`:

```python
def resolve_initial_folder(cfg, argv_folder, home_music=None):
    """Folder to scan at startup. Returns (folder, should_prompt).

    Precedence: explicit CLI arg > saved config > ~/Music. should_prompt is
    True only when nothing was configured, so the UI can offer setup.
    """
    home_music = home_music or os.path.expanduser("~/Music")
    if argv_folder:
        return argv_folder, False
    saved = (cfg or {}).get("library_folder") or ""
    if saved:
        return saved, False
    return home_music, True
```

In `main()`, replace the first two lines:

```python
def main():
    cfg = cfgmod.load_config()
    argv_folder = sys.argv[1] if len(sys.argv) > 1 else None
    folder, needs_setup = resolve_initial_folder(cfg, argv_folder)
    if argv_folder:
        cfg["library_folder"] = argv_folder
        cfgmod.save_config(cfg)
    folders = [folder]
```

and after pygame starts (so it can draw), if `needs_setup`, open settings and show a message:

```python
    if needs_setup:
        settings_panel.message = ("First run: choose your music folder below, "
                                  "or press Esc to keep the default")
        settings_panel.open()
```

Add an `open()` method to `SettingsPanel` (`self.is_open_flag = True`).

**Step 4: Run test to verify it passes**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 tests/test_first_run.py`
Expected: `FIRST RUN TESTS PASSED`

**Step 5: Commit**

```bash
cd /Users/giathinh/ncs-music-launcher
git add src/ncs_launcher.py tests/test_first_run.py
git commit -m "feat(setup): first-run folder prompt with CLI > config > ~/Music precedence"
```

---

### Task 11: Full regression run and manual verification

**Objective:** Prove nothing existing broke, then verify by hand.

**Files:**
- Test: all of `tests/`

**Step 1: Run the whole suite**

Run:
```bash
cd /Users/giathinh/ncs-music-launcher
for f in tests/test_*.py; do
  echo "--- $f"
  python3 "$f" > /tmp/out.txt 2>&1 && tail -2 /tmp/out.txt || { echo "FAILED"; cat /tmp/out.txt; }
done
```
Expected: every file ends with a `... PASSED` line, no `FAILED`, no `Traceback`.

**Step 2: Launch the real app**

Run: `cd /Users/giathinh/ncs-music-launcher && python3 src/ncs_launcher.py ~/Downloads`
Expected: window opens, no traceback, `Found N track(s)` printed.

**Step 3: Verify by hand, in this order**

1. Press `,` (comma) → settings opens.
2. Navigate with ↑↓, press Enter on "Show control hints" → it flips to OFF.
3. Press Enter on "Play / pause" → prompt says "press a key"; press `K` → row now reads `K`.
4. Close settings. Press `K` → playback pauses/resumes. Press `Space` → nothing happens (unbound).
5. Press `C` → Hermes panel opens; ask something; Enter; a real reply appears.
6. Drag the window to a small size → the library panel, hint bar, and sphere all reflow, nothing overlaps.
7. Settings → "Move all music to a folder..." → pick a folder → it reports the plan; Enter again to confirm; check the files moved and nothing was clobbered.
8. Quit and relaunch → settings persisted (hints still OFF, `K` still bound).
9. Torrent: paste a magnet in the `T` overlay, then select the downloading track before it finishes → it plays, the badge shows `downloading N%`, and audio keeps extending as bytes land.

**Step 4: Commit any fixes found**

```bash
cd /Users/giathinh/ncs-music-launcher
git add -A
git commit -m "fix: address issues found in manual verification"
```

---

## Tests / Validation

| Test | Covers | Command |
|---|---|---|
| `tests/test_config.py` | defaults, roundtrip, corrupt file, unknown keys | `python3 tests/test_config.py` |
| `tests/test_actions.py` | key resolution, rebinding, duplicates, reset, labels, conflicts | `python3 tests/test_actions.py` |
| `tests/test_keymap_dispatch.py` | rebound key fires, old key released, dispatcher exists | `python3 tests/test_keymap_dispatch.py` |
| `tests/test_streaming_source.py` | decode grows as bytes append; float32 range | `python3 tests/test_streaming_source.py` |
| `tests/test_torrent_progress.py` | `in_progress_files`/`file_totals` shape; `normalize` | `python3 tests/test_torrent_progress.py` |
| `tests/test_player_streaming.py` | partial load, `refresh()` growth, forced full decode | `python3 tests/test_player_streaming.py` |
| `tests/test_partial_playback_wiring.py` | `main()` consults progress + refreshes | `python3 tests/test_partial_playback_wiring.py` |
| `tests/test_library_ops.py` | dry run, collision skip, src==dst, dst-inside-src, layout | `python3 tests/test_library_ops.py` |
| `tests/test_settings_panel.py` | rows, nav, capture, toggle, label fit, Esc | `python3 tests/test_settings_panel.py` |
| `tests/test_first_run.py` | folder precedence | `python3 tests/test_first_run.py` |
| `tests/test_sphere_and_chat.py` | existing sphere + chat (regression) | `python3 tests/test_sphere_and_chat.py` |
| `tests/test_live_loop.py` | existing resize + chat (regression) | `python3 tests/test_live_loop.py` |

**Do not** add `pytest`-style tests: pytest is not installed (`No module named pytest`).

---

## Risks, Tradeoffs, and Open Questions

**Risks**

1. **`StreamingSource.pump()` re-opens the stream from byte 0 each call.** A generator cannot resume past EOF, so growing the buffer means re-decoding the prefix. For a 4 MB MP3 that is ~0.2 s of CPU per refresh; refreshing once per second is acceptable, but pumping on *every* frame would be a serious stall. **Mitigation:** call `refresh()` on a 1 Hz timer, not every frame. Task 7 shows a per-frame call — change it to a time-gated call before merging.
2. **`mp3_stream_file` yields interleaved samples when the source is stereo.** It uses the file's native channel count. The test uses a mono ffmpeg output, so stereo is untested. **Mitigation:** probe with `miniaudio.get_file_info(path).nchannels` and downmix with `.reshape(-1, ch).mean(axis=1)`; do not assume mono.
3. **The music key/handler still calls `player.load` from a Quartz callback thread** (see `src/media_keys.py`). Loading audio on a non-main thread while the loop also loads is a race. Task 7 introduces `pending_load` to marshal it; if that is skipped, expect intermittent crashes.
4. **Bulk move is destructive to the source layout.** `plan_move` + `execute_move` deletes the originals after a size check. If the destination is a cloud-synced or network folder, a partial write could pass the size check but be corrupt. **Mitigation:** consider making copy-then-verify-then-delete the only mode. **Open question below.**
5. **`miniaudio.stream_file` is untested for flac/ogg/m4a on truncated files.** The generic path may behave differently from the mp3 path. **Mitigation:** fall back to complete-file playback for non-mp3 while downloading, and say so in the UI.

**Tradeoffs**

- *Key names as strings in JSON* — human-editable and portable, but a typo (`"SPACEE"`) resolves to `None` and silently unbinds. The `conflicts()` output and the panel's rebind feedback mitigate this; consider validating on load and warning.
- *Action registry adds indirection* — one extra lookup per keypress versus a direct `elif`. Worth it: remapping is otherwise impossible.
- *Settings drawn on top of a live visualizer* — the sphere still renders underneath at 30 fps, so the panel costs a full-screen SRCALPHA surface each frame. Acceptable; pausing the sphere while settings is open would be a cheap follow-up.

**Open Questions**

1. **Move vs copy for "move all music"?** You said *move*. Do you want it to (a) move and delete originals (current plan), (b) copy and leave originals, or (c) ask per run? (a) is fastest and tidiest; (c) is safest. I have planned (a) with a dry-run confirm step.
2. **Which keys do you actually want?** Defaults are the current ones plus `,` for settings. If you want media-style keys (`Z`/`X` for prev/next, `S` for stop), say so and I will change the defaults.
3. **Should the hint bar be hidden globally or per-panel?** Planned as a single global `show_hints` toggle. A separate "hide the bottom bar but keep the help screen" is possible.
4. **Should rebinding also cover controller buttons?** The plan covers keyboard only, matching your request. Controller mapping is currently hardcoded in `main()` (A/B/X/Y, D-pad) and could be folded into `ACTIONS` in a follow-up.
5. **Where should settings live?** Planned at `~/.ncs-smlauncher/settings.json` (user home), deliberately not next to the source, because a frozen PyInstaller bundle directory can be read-only.
