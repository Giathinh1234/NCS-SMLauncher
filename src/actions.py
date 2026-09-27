"""Action registry and rebindable keymap.

Keystrokes are described by *action names* ("play_pause"), never by pygame
constants, at every layer above this module: the launcher asks the keymap
"what did the user just press?" and gets an action back. That indirection is
what makes remapping possible without hunting for K_* constants in gameplay
code.

Resolution goes the other way too, for the settings screen: a printable name
("Space", "Enter", ",") is what gets shown and written to settings.json.

Public surface:
    ACTIONS               action -> {"label", "group"}
    DEFAULT_KEYMAP        re-exported from config
    key_to_constant(name) name -> pygame constant, or None
    constant_to_key_name(c)  pygame constant -> name, or None for modifiers
    human_key_name(name)  name -> display string
    Keymap                live binding table with resolve()/bind()
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402  (env vars above must be set before SDL loads)

from config import DEFAULT_KEYMAP  # noqa: E402  re-exported for callers

__all__ = [
    "ACTIONS",
    "DEFAULT_KEYMAP",
    "Keymap",
    "constant_to_key_name",
    "human_key_name",
    "key_to_constant",
]

# Every action the launcher understands. Adding one here plus a DEFAULT_KEYMAP
# entry is all it takes to make it bindable; the settings screen groups these
# rows into sections.
ACTIONS = {
    "select_prev": {"label": "Previous track", "group": "Playback"},
    "select_next": {"label": "Next track", "group": "Playback"},
    "play_pause": {"label": "Play / Pause", "group": "Playback"},
    "play": {"label": "Play (force)", "group": "Playback"},
    "seek_back": {"label": "Seek backward", "group": "Playback"},
    "seek_forward": {"label": "Seek forward", "group": "Playback"},
    "toggle_mute": {"label": "Toggle mute", "group": "Playback"},
    "cycle_visualizer": {"label": "Cycle visualizer", "group": "View"},
    "open_hermes": {"label": "Open Hermes", "group": "View"},
    "open_video": {"label": "Open video / visual", "group": "View"},
    "open_torrent": {"label": "Torrent downloads", "group": "Library"},
    "open_folder": {"label": "Open library folder", "group": "Library"},
    "open_settings": {"label": "Settings", "group": "Library"},
    "quit": {"label": "Quit", "group": "App"},
}

# Display names for keys whose name is not simply a single word.
_PRETTY = {
    "RETURN": "Enter",
    "SPACE": "Space",
    "ESCAPE": "Esc",
    "UP": "Up",
    "DOWN": "Down",
    "LEFT": "Left",
    "RIGHT": "Right",
    "COMMA": ",",
    "PERIOD": ".",
}

# Modifiers and lock keys: never bindable as a trigger on their own, because
# the OS eats most of them and a stuck modifier would strand the app in a
# confusing state. They are still accepted as chord *modifiers* elsewhere.
_UNBINDABLE = {
    "LSHIFT", "RSHIFT", "LCTRL", "RCTRL", "LALT", "RALT",
    "LGUI", "RGUI", "LCAPS", "SCROLLOCK", "NUMLOCK", "RMETA", "LMETA",
}


def _unbindable_constants():
    """Constant values of the unbindable names, plus their pygame aliases.

    Several modifiers share a constant with an unbindable name (LSUPER ==
    LMETA, RSUPER == RMETA, NUMLOCKCLEAR == NUMLOCK). Matching on values rather
    than attribute names means the aliases are rejected too, so no config edit
    can smuggle one in as a trigger.
    """
    values = set()
    for name in _UNBINDABLE:
        const = getattr(pygame, "K_" + name, None)
        if isinstance(const, int):
            values.add(const)
    for attr in dir(pygame):
        if attr.startswith("K_"):
            value = getattr(pygame, attr)
            if value in values and attr[2:] not in _UNBINDABLE:
                values.add(value)
    return frozenset(values)


_UNBINDABLE_CONSTANTS = _unbindable_constants()


def key_to_constant(key_name):
    """pygame constant for a key name, or None if there isn't a usable one.

    Returns None for unknown names, modifier/lock keys, and empty input. SDL
    spells letters as K_a..K_z and digits as K_0..K_9, while the canonical
    names are upper case, so both spellings are tried.
    """
    if not key_name or not isinstance(key_name, str):
        return None
    name = key_name.strip().upper()
    if not name or name in _UNBINDABLE:
        return None
    for candidate in (name, name.lower()):
        value = getattr(pygame, "K_" + candidate, None)
        if isinstance(value, int):
            return None if value in _UNBINDABLE_CONSTANTS else value
    return None


def constant_to_key_name(const):
    """Canonical key name for a pygame constant, or None if there isn't one.

    Modifiers return None, as do constants SDL exposes that no sensible
    binding should use.
    """
    if const is None or not isinstance(const, int):
        return None
    if const in _UNBINDABLE_CONSTANTS:
        return None
    return _NAME_BY_CONSTANT.get(const)


def human_key_name(key_name):
    """Display string for a key name: 'SPACE' -> 'Space', 'K' -> 'K', '' -> '-'."""
    if not key_name or not isinstance(key_name, str):
        return "-"
    name = key_name.strip().upper()
    if not name:
        return "-"
    if name in _PRETTY:
        return _PRETTY[name]
    if len(name) <= 2:
        # Single keys and short codes ("K", "F1", "1") read better verbatim.
        return name
    return name.replace("_", " ").capitalize()


def _build_constant_index():
    """Map constant -> canonical name once, with deterministic alias choices.

    SDL exposes aliases for the same constant (KP1/KP_1, PRINT/PRINTSCREEN).
    When several names share a value, prefer one already used by
    DEFAULT_KEYMAP so display round-trips cleanly, otherwise take the
    alphabetically first so the result is stable across runs.
    """
    names_by_constant = {}
    for attr in dir(pygame):
        if not attr.startswith("K_"):
            continue
        value = getattr(pygame, attr)
        if not isinstance(value, int) or value in _UNBINDABLE_CONSTANTS:
            continue
        names_by_constant.setdefault(value, []).append(attr[2:])

    index = {}
    for value, names in names_by_constant.items():
        names.sort()
        preferred = _preferred_name(value, names)
        index[value] = preferred or names[0]
    return index


def _preferred_name(value, names):
    """The DEFAULT_KEYMAP name for `value`, or '' when none of them apply.

    The match is case-insensitive because SDL spells letters K_a..K_z while
    DEFAULT_KEYMAP uses the canonical upper-case form ('M', not 'm').
    """
    by_upper = {name.upper(): name for name in names}
    for name in DEFAULT_KEYMAP.values():
        actual = by_upper.get(name.upper())
        if actual is not None and key_to_constant(actual) == value:
            return name
    return ""


_NAME_BY_CONSTANT = _build_constant_index()


class Keymap:
    """A live action -> key binding table.

    Unknown actions and unresolvable key names in `bindings` are dropped
    rather than applied, so a hand-edited or forward-version settings file
    degrades to "that binding is not active" instead of breaking every key.
    """

    def __init__(self, bindings=None):
        self.bindings = dict(DEFAULT_KEYMAP)
        if bindings:
            for action, key_name in dict(bindings).items():
                if action not in self.bindings:
                    continue  # unknown action: ignore, keep the default
                if key_to_constant(key_name) is None:
                    continue  # unbindable or bogus key: keep the default
                self.bindings[action] = key_name
        self._rebuild()

    def _rebuild(self):
        """Recompute the constant -> action lookup from `bindings`."""
        self._actions = {}
        for action, key_name in self.bindings.items():
            const = key_to_constant(key_name)
            if const is None:
                continue
            # Last write wins when two actions share a key.
            self._actions[const] = action

    def resolve(self, key_constant):
        """Action bound to a pygame key constant, or None."""
        return self._actions.get(key_constant)

    def bind(self, action, key_name):
        """Bind `action` to `key_name` and rebuild the lookup.

        Raises KeyError for an unknown action and ValueError for a key that
        cannot be bound (modifier, lock key, or unknown name). The old binding
        is released automatically.
        """
        if action not in self.bindings:
            raise KeyError("unknown action: %r" % (action,))
        if key_to_constant(key_name) is None:
            raise ValueError("cannot bind %r to key %r" % (action, key_name))
        self.bindings[action] = key_name
        self._rebuild()

    def reset_to_defaults(self):
        """Drop every custom binding."""
        self.bindings = dict(DEFAULT_KEYMAP)
        self._rebuild()

    def conflicts(self):
        """Pairs of actions currently sharing one key constant."""
        by_constant = {}
        for action, key_name in self.bindings.items():
            const = key_to_constant(key_name)
            if const is None:
                continue
            by_constant.setdefault(const, []).append(action)
        pairs = []
        for actions in by_constant.values():
            for index, first in enumerate(actions):
                for second in actions[index + 1:]:
                    pairs.append((first, second))
        return pairs
