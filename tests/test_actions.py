"""Tests for the action registry and rebindable keymap (src/actions.py).

Run: python3 tests/test_actions.py
"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((320, 240))

import actions  # noqa: E402
from actions import ACTIONS, DEFAULT_KEYMAP, Keymap  # noqa: E402
from config import DEFAULT_KEYMAP as CONFIG_KEYMAP  # noqa: E402


def main():
    print("1) the registry covers exactly the default keymap")
    assert set(ACTIONS) == set(DEFAULT_KEYMAP), (set(ACTIONS) ^ set(DEFAULT_KEYMAP))
    assert DEFAULT_KEYMAP is CONFIG_KEYMAP
    for action, meta in ACTIONS.items():
        assert meta["label"], action
        assert meta["group"], action
    groups = {a: m["group"] for a, m in ACTIONS.items()}
    assert groups["play_pause"] == "Playback"
    assert groups["cycle_visualizer"] == "View"
    assert groups["open_torrent"] == "Library"
    assert groups["quit"] == "App"
    print("   %d actions, %d groups" % (len(ACTIONS), len(set(groups.values()))))

    print("2) every default binding resolves back to its action")
    km = Keymap()
    for action, key_name in DEFAULT_KEYMAP.items():
        const = actions.key_to_constant(key_name)
        assert const is not None, (action, key_name)
        assert km.resolve(const) == action, (action, key_name, km.resolve(const))
    assert km.resolve(pygame.K_SPACE) == "play_pause"
    assert km.resolve(pygame.K_UP) == "select_prev"
    assert km.resolve(pygame.K_COMMA) == "open_settings"
    print("   all %d defaults round-trip through resolve()" % len(DEFAULT_KEYMAP))

    print("3) rebinding releases the old key")
    km.bind("play_pause", "P")
    assert km.resolve(pygame.K_p) == "play_pause", km.resolve(pygame.K_p)
    assert km.resolve(pygame.K_SPACE) is None, "old key still resolves"
    assert km.bindings["play_pause"] == "P"
    print("   play_pause: SPACE -> P, K_SPACE now unbound")

    print("4) duplicate bindings: last write wins")
    km.bind("toggle_mute", "P")
    assert km.resolve(pygame.K_p) == "toggle_mute", km.resolve(pygame.K_p)
    assert km.conflicts(), "expected a conflict on P"
    assert ("play_pause", "toggle_mute") in km.conflicts(), km.conflicts()
    print("   conflicts() ->", km.conflicts())

    print("5) reset_to_defaults() restores the stock bindings")
    km.reset_to_defaults()
    assert km.bindings == DEFAULT_KEYMAP, km.bindings
    assert km.conflicts() == [], km.conflicts()
    assert km.resolve(pygame.K_SPACE) == "play_pause"
    assert km.resolve(pygame.K_p) is None
    print("   back to defaults, %d conflicts" % len(km.conflicts()))

    print("6) unknown actions and bad key names are ignored, defaults kept")
    km2 = Keymap({"quit": "W", "not_an_action": "Z", "play_pause": "NOPE",
                  "toggle_mute": "LSHIFT", "open_folder": ""})
    assert "not_an_action" not in km2.bindings, km2.bindings
    assert km2.bindings["quit"] == "W"
    assert km2.bindings["play_pause"] == DEFAULT_KEYMAP["play_pause"]
    assert km2.resolve(pygame.K_w) == "quit"
    assert km2.resolve(pygame.K_SPACE) == "play_pause"
    assert km2.resolve(pygame.K_LSHIFT) is None, "modifier bound as a trigger"
    assert km2.resolve(pygame.K_COMMA) == "open_settings"
    assert km2.conflicts() == [], km2.conflicts()
    print("   bad bindings dropped, defaults intact")

    print("6b) bind() rejects unknown actions and unbindable keys")
    for bad_action in ("nope", "", None):
        try:
            km2.bind(bad_action, "W")
        except KeyError:
            pass
        else:
            raise AssertionError("expected KeyError for %r" % (bad_action,))
    for bad_key in ("LSHIFT", "RMETA", "LCAPS", "NUMLOCK", "SCROLLOCK", "", None, "ZZZZ"):
        try:
            km2.bind("quit", bad_key)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for key %r" % (bad_key,))
    # Modifier aliases share constants with real modifiers; both are refused.
    assert actions.key_to_constant("LSUPER") is None
    assert actions.key_to_constant("RSUPER") is None
    assert actions.key_to_constant("NUMLOCKCLEAR") is None
    print("   KeyError/ValueError raised as documented")

    print("7) human_key_name()")
    assert actions.human_key_name("SPACE") == "Space"
    assert actions.human_key_name("K") == "K"
    assert actions.human_key_name("") == "-"
    assert actions.human_key_name(None) == "-"
    assert actions.human_key_name("RETURN") == "Enter"
    assert actions.human_key_name("ESCAPE") == "Esc"
    assert actions.human_key_name("UP") == "Up"
    assert actions.human_key_name("DOWN") == "Down"
    assert actions.human_key_name("COMMA") == ","
    assert actions.human_key_name("PERIOD") == "."
    print("   SPACE->Space, K->K, ''->'-'")

    print("8) constant <-> name conversion round-trips")
    for action, key_name in DEFAULT_KEYMAP.items():
        const = actions.key_to_constant(key_name)
        back = actions.constant_to_key_name(const)
        assert back == key_name, (key_name, back)
        assert actions.key_to_constant(back) == const
    assert actions.key_to_constant("") is None
    assert actions.key_to_constant(None) is None
    assert actions.key_to_constant("NOT_A_KEY") is None
    assert actions.constant_to_key_name(pygame.K_LCTRL) is None
    assert actions.constant_to_key_name(None) is None
    assert actions.constant_to_key_name(-999) is None
    print("   every default name round-trips both ways")

    print("9) a Keymap from stored bindings is independent of DEFAULT_KEYMAP")
    km3 = Keymap()
    km3.bind("quit", "ESCAPE")
    assert DEFAULT_KEYMAP["quit"] == "Q", DEFAULT_KEYMAP
    assert Keymap().bindings["quit"] == "Q"
    print("   shared defaults untouched")

    print("\nALL ACTIONS TESTS PASSED")


if __name__ == "__main__":
    main()
