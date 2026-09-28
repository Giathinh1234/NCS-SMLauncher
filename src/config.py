"""JSON-backed settings store for NCS-SMLauncher.

Settings live in the user's home directory rather than next to the source
because the PyInstaller bundle ships inside a read-only application folder on
macOS: a settings.json written beside the code would silently fail to persist
(and, on a signed bundle, break code signing on every run that touched it).

Public surface:
    CONFIG_DIR / CONFIG_PATH   where settings.json lives
    DEFAULT_KEYMAP            action -> pygame key name
    DEFAULT_CONFIG            template written on first run
    default_config()          fresh, unshared defaults
    load_config(path=None)    defaults merged with the stored file
    save_config(cfg, path=None)  atomic write, True/False
"""
import copy
import json
import os

# Home, not CWD: the frozen bundle's directory is read-only.
# HASHPLAY_CONFIG_DIR overrides it so a test -- or someone who wants a
# throwaway profile -- can point the whole app somewhere disposable instead of
# writing into the real one.
CONFIG_DIR = os.environ.get("HASHPLAY_CONFIG_DIR") or os.path.join(
    os.path.expanduser("~"), ".ncs-smlauncher")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")

# Canonical default bindings. Names are pygame K_* suffixes ("UP", "SPACE"),
# not constants, so the file stays human-editable and survives pygame versions
# that renumber constants.
DEFAULT_KEYMAP = {
    "select_prev": "UP",
    "select_next": "DOWN",
    "play_pause": "SPACE",
    "play": "RETURN",
    "seek_back": "LEFT",
    "seek_forward": "RIGHT",
    "toggle_mute": "M",
    "cycle_visualizer": "F",
    "open_api": "C",
    "open_torrent": "T",
    "open_folder": "O",
    "open_settings": "COMMA",
    "open_video": "V",
    "quit": "Q",
}

def _env_port(name="HASHPLAY_API_PORT", fallback=8777):
    """Read a port from the environment, ignoring anything unusable.

    This used to be a bare int() inside the dict literal below, evaluated at
    import time, so HASHPLAY_API_PORT=not-a-port raised ValueError and took
    the whole module -- and therefore the whole app -- down on startup. Every
    other failure mode in this file is carefully defended ("a corrupt settings
    file must never be able to stop the player from starting"); this one path
    was the odd one out. A typo in a test script or a stale export should cost
    you the custom port, not the application.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return fallback
    try:
        port = int(raw)
    except ValueError:
        print(f"{name}={raw!r} is not a port; using {fallback}")
        return fallback
    if not 0 <= port <= 65535:
        print(f"{name}={raw!r} is out of range; using {fallback}")
        return fallback
    return port


# schema_version 0 = pre-migration format; see migrations.py.
DEFAULT_CONFIG = {
    "library_folder": "",
    "show_hints": True,
    "easter_eggs": True,
    "schema_version": 0,
    "keymap": {},
    # Control API (src/control_api.py). The host is deliberately not
    # configurable to anything but loopback: this endpoint can change what the
    # speakers do, so exposing it on a LAN interface would hand volume control
    # to anyone on the network. The port is free to move if 8777 is taken.
    "api_enabled": True,
    "api_host": "127.0.0.1",
    "api_port": _env_port(),
    "setup_complete": False,
}


def default_config():
    """A complete default config, freshly allocated on every call.

    Never hand out DEFAULT_CONFIG itself: the settings UI mutates the dict it
    is given, and a shared template would let one window's edits leak into the
    next load.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["keymap"] = copy.deepcopy(DEFAULT_KEYMAP)
    return cfg


def _merge(base, override):
    """Overlay `override` onto `base`, one nested level deep for "keymap".

    Unknown keys in `override` are kept so a settings file written by a newer
    build survives a round-trip through an older one (forward compat).
    """
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key == "keymap" and isinstance(value, dict):
            existing = merged.get("keymap")
            nested = dict(existing) if isinstance(existing, dict) else {}
            nested.update(value)
            merged["keymap"] = nested
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_config(path=None):
    """Load settings, falling back to defaults for anything unusable.

    A missing file, invalid JSON, or JSON that is not an object all yield
    defaults rather than raising: a corrupt settings file must never be able
    to stop the player from starting.
    """
    target = CONFIG_PATH if path is None else path
    defaults = default_config()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return defaults
    if not isinstance(stored, dict):
        return defaults
    return _merge(defaults, stored)


def save_config(cfg, path=None):
    """Write `cfg` as JSON atomically. Returns True on success.

    Writes to "<path>.tmp" and os.replace()s it into place so an interrupted
    write (crash, full disk) can never leave a half-written settings file.
    """
    target = CONFIG_PATH if path is None else path
    tmp = target + ".tmp"
    try:
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        return True
    except OSError as exc:
        print("config: could not save settings to %s (%s)" % (target, exc))
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False
