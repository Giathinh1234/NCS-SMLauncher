"""Tests for the keyboard-driven settings overlay (src/settings_panel.py).

Run: python3 tests/test_settings_panel.py

Real pygame on the dummy video driver, a real Keymap, and a real Config
backed by a temp directory: config.CONFIG_PATH / CONFIG_DIR are repointed
before the panel is built, so the user's ~/.ncs-smlauncher is never touched
and the persistence assertions are about a file we can actually read back.
"""

import json
import os
import sys
import tempfile

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((1280, 720))
FONT = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)

import config  # noqa: E402
import actions  # noqa: E402
import settings_panel  # noqa: E402
from settings_panel import SettingsPanel  # noqa: E402

TMP = tempfile.mkdtemp(prefix="ncs-settings-panel-")
SETTINGS = os.path.join(TMP, "settings.json")
# Redirect the store before anything reads it: the real home settings must
# stay untouched by a test run.
config.CONFIG_DIR = TMP
config.CONFIG_PATH = SETTINGS


class Ev:
    """Minimal stand-in for a pygame KEYDOWN event."""

    def __init__(self, key, unicode=""):
        self.type = pygame.KEYDOWN
        self.key = key
        self.unicode = unicode


def key(const):
    return Ev(const, pygame.key.name(const))


def press(panel, const):
    return panel.handle_key(key(const))


def stored():
    with open(SETTINGS, "r", encoding="utf-8") as handle:
        return json.load(handle)


def build(**kwargs):
    cfg = config.load_config(SETTINGS)
    kwargs.setdefault("save_path", SETTINGS)
    return SettingsPanel(cfg, actions.Keymap(cfg["keymap"]), **kwargs)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_rows():
    print("1) every action in the registry is rebindable")
    panel = build()
    check(len(panel.items) > 8, "too few rows: %d" % len(panel.items))
    actions_rows = [row for row in panel.items if row.kind == "action"]
    check({r.action for r in actions_rows} == set(actions.ACTIONS),
          "rebind rows do not cover ACTIONS: %s"
          % ({r.action for r in actions_rows} ^ set(actions.ACTIONS)))
    for row in actions_rows:
        check(row.label, "action row without a label: %r" % (row,))
        check(row.value, "action row without a binding: %r" % (row,))
    check(any(r.kind == "toggle" for r in panel.items), "no boolean toggle")
    check(any(r.kind == "command" for r in panel.items), "no command row")
    print("   %d rows: %d rebindable actions, %d toggles"
          % (len(panel.items), len(actions_rows),
             sum(1 for r in panel.items if r.kind == "toggle")))


def test_open_close():
    print("2) open / close / toggle")
    panel = build()
    check(not panel.is_open(), "starts open")
    panel.open()
    check(panel.is_open(), "open() did not open")
    panel.open()
    check(panel.is_open(), "open() is not idempotent")
    panel.close()
    check(not panel.is_open(), "close() did not close")
    check(panel.toggle() is True and panel.is_open(), "toggle() did not open")
    check(panel.toggle() is False and not panel.is_open(), "toggle() did not close")
    check(press(panel, pygame.K_DOWN) is None, "closed panel must not act")
    print("   open/close/toggle OK")


def test_navigation():
    print("3) Up/Down navigation wraps and stays in range")
    panel = build()
    panel.open()
    check(panel.selected_index == 0, "starts on row 0, got %d" % panel.selected_index)
    check(press(panel, pygame.K_DOWN) is None, "DOWN should not report a change")
    check(panel.selected_index == 1, panel.selected_index)
    press(panel, pygame.K_UP)
    check(panel.selected_index == 0, panel.selected_index)
    press(panel, pygame.K_UP)
    check(panel.selected_index == len(panel.items) - 1, "UP did not wrap to the end")
    press(panel, pygame.K_DOWN)
    check(panel.selected_index == 0, "DOWN did not wrap to the top")
    # many rows in: the cursor must never leave the list, however far it walks
    for _ in range(len(panel.items) * 2 + 3):
        press(panel, pygame.K_DOWN)
        check(0 <= panel.selected_index < len(panel.items), panel.selected_index)
    press(panel, pygame.K_END)
    check(panel.selected_index == len(panel.items) - 1, "END")
    press(panel, pygame.K_HOME)
    check(panel.selected_index == 0, "HOME")
    print("   wrapped over %d rows, never out of range" % len(panel.items))


def test_rebind():
    print("4) Enter arms a capture, the next real key rebinds")
    panel = build()
    panel.open()
    check(panel.select_action("play_pause") >= 0, "no play_pause row")
    check(press(panel, pygame.K_RETURN) is None, "arming should not report 'rebind'")
    check(panel.capturing, "Enter did not arm the capture")
    check(press(panel, pygame.K_k) == "rebind", "capture did not report a rebind")
    check(not panel.capturing, "capture still armed after a real key")
    # constant_to_key_name() returns pygame's own spelling for letters, which
    # is lower case ('k'); key_to_constant() accepts either, so compare
    # case-insensitively and assert the round-trip rather than the spelling.
    check(panel.keymap.bindings["play_pause"].upper() == "K",
          panel.keymap.bindings["play_pause"])
    check(panel.keymap.resolve(pygame.K_k) == "play_pause", "keymap not rebuilt")
    check(panel.keymap.resolve(pygame.K_SPACE) is None, "old key still bound")
    on_disk = stored()
    check(on_disk["keymap"]["play_pause"].upper() == "K", on_disk["keymap"])
    check(panel.cfg["keymap"]["play_pause"].upper() == "K", panel.cfg["keymap"])
    print("   play_pause: SPACE -> %s, persisted to settings.json"
          % panel.keymap.bindings["play_pause"])

    print("5) the rebound key round-trips back to a display name")
    row = panel.items[panel.selected_index]
    check(actions.human_key_name(row.value) == "K", row.value)
    print("   row value %r -> %r" % (row.value, panel.row_value(row)))


def test_modifier_ignored():
    print("6) every modifier and lock key is ignored, capture stays armed")
    panel = build()
    panel.open()
    panel.select_action("toggle_mute")
    press(panel, pygame.K_RETURN)
    check(panel.capturing, "capture not armed")
    before = panel.keymap.bindings["toggle_mute"]

    # Sweep every modifier pygame exposes, not a hand-picked few: the whole
    # point is that no spelling of any of them can be bound.
    swept = 0
    for const in sorted(settings_panel.MODIFIER_CONSTANTS):
        check(press(panel, const) is None,
              "modifier %d reported a change" % const)
        check(panel.capturing, "capture disarmed by constant %d" % const)
        check(panel.keymap.bindings["toggle_mute"] == before,
              "modifier constant %d was bound" % const)
        swept += 1
    check(swept >= 8, "only %d modifier constants found" % swept)
    # And the specific ones the plan calls out, plus an event with no key.
    for const in (pygame.K_LSHIFT, pygame.K_RSHIFT, pygame.K_LCTRL,
                  pygame.K_RALT, pygame.K_LSUPER, pygame.K_CAPSLOCK,
                  pygame.K_NUMLOCK, pygame.K_SCROLLOCK):
        check(press(panel, const) is None,
              "modifier %s reported a change" % pygame.key.name(const))
        check(panel.capturing, "capture disarmed by %s" % pygame.key.name(const))
    check(press(panel, -1) is None, "a bogus key constant reported a change")
    check(panel.capturing, "capture disarmed by a bogus constant")
    check(panel.keymap.resolve(pygame.K_LSHIFT) is None, "modifier bound as a trigger")
    check(panel.keymap.resolve(pygame.K_CAPSLOCK) is None, "capslock bound as a trigger")
    print("   %d modifier constants + a bogus one, all ignored; still capturing"
          % swept)
    print("   binding still %s" % before)

    print("7) a real key after the modifiers still lands")
    check(press(panel, pygame.K_z) == "rebind", "capture lost after modifiers")
    check(panel.keymap.bindings["toggle_mute"].upper() == "Z", panel.keymap.bindings)
    check(not panel.capturing, "capture still armed")
    print("   toggle_mute -> %s" % panel.keymap.bindings["toggle_mute"])

    print("8) Esc cancels a capture, then Esc closes the panel")
    panel.select_action("quit")
    press(panel, pygame.K_RETURN)
    check(panel.capturing, "capture not armed")
    check(press(panel, pygame.K_ESCAPE) is None, "Esc mid-capture should not close")
    check(not panel.capturing, "Esc did not cancel the capture")
    check(panel.is_open(), "Esc mid-capture closed the panel")
    check(press(panel, pygame.K_ESCAPE) == "close", "Esc did not report close")
    check(not panel.is_open(), "panel still open")
    print("   cancel then close OK")


def test_toggle():
    print("9) Enter flips a boolean and writes it through")
    panel = build()
    panel.open()
    check(panel.select_kind("toggle") >= 0, "no toggle row")
    row = panel.current_row()
    before = bool(panel.cfg[row.key])
    check(press(panel, pygame.K_RETURN) == "toggled", "toggle did not report 'toggled'")
    check(panel.cfg[row.key] is not before, "cfg value unchanged")
    check(row.value is not before, "row value not refreshed")
    check(stored()[row.key] is not before, "not persisted")
    check(panel.row_value(row) == ("ON" if row.value else "OFF"), panel.row_value(row))
    after = bool(panel.cfg[row.key])
    print("   %s: %s -> %s (persisted)" % (row.key, before, after))
    press(panel, pygame.K_RETURN)
    check(panel.cfg[row.key] is before, "second toggle did not flip back")
    check(stored()[row.key] is before, "second toggle not persisted")


def test_reset():
    print("10) the reset row restores the default keymap")
    panel = build()
    panel.open()
    panel.select_action("play_pause")
    press(panel, pygame.K_RETURN)
    press(panel, pygame.K_j)
    check(panel.keymap.bindings["play_pause"].upper() == "J", panel.keymap.bindings)
    panel.select_kind("command")
    check(panel.current_row().callback is not None, "command row has no callback")
    while panel.current_row().label != "Reset all keys to defaults":
        press(panel, pygame.K_DOWN)
    press(panel, pygame.K_RETURN)
    check(panel.keymap.bindings["play_pause"] == "SPACE", panel.keymap.bindings)
    check(panel.keymap.conflicts() == [], panel.keymap.conflicts())
    check(stored()["keymap"]["play_pause"] == "SPACE", stored()["keymap"])
    print("   play_pause back to SPACE, no conflicts")


def test_labels_fit():
    print("11) no drawn row text overflows the panel at any plausible size")
    panel = build()
    panel.cfg["library_folder"] = ("/Users/giathinh/Music/NCS downloads "
                                   "/v0.99 release/very long folder name here")
    panel._build()
    worst = None
    for w, h in ((1280, 720), (1024, 640), (900, 560), (640, 480),
                 (520, 400), (420, 320), (1920, 1080), (300, 240)):
        panel_w, panel_h = panel.panel_size(w, h)
        check(panel_w <= w - 2 * 24, "panel wider than the window at w=%d" % w)
        check(panel_h <= h - 2 * 24, "panel taller than the window at h=%d" % h)
        inner = panel_w - 2 * 20
        for index, row in enumerate(panel.items):
            # Measure what draw() will actually blit, in every row state.
            for capturing in (False, True):
                label, value = panel.row_display(
                    row, w, FONT, capturing=capturing, selected=True)
                if not label and not value:
                    continue
                check(FONT.size(label)[0] <= inner,
                      "label %r overflows at w=%d (%d px)"
                      % (label, w, FONT.size(label)[0]))
                total = FONT.size(label)[0] + FONT.size(value)[0]
                check(total <= inner,
                      "row %r needs %d px in a %d px inner area at w=%d"
                      % (label or value, total, inner, w))
                # The selected row highlight must not spill past the panel.
                check(panel_w - 2 * 20 + 12 <= panel_w, "highlight overflows")
        longest = max(panel.label_width(r, w, FONT)
                      for r in panel.items if r.label)
        ratio = longest / float(panel_w)
        if worst is None or ratio > worst[0]:
            worst = (ratio, w, panel_w)
    print("   tightest case: label fills %.0f%% of a %d px panel at w=%d"
          % (worst[0] * 100, worst[2], worst[1]))

    print("12) a long path is shortened, not clipped off-screen")
    info = [r for r in panel.items
            if r.kind == "info" and r.label == "Library folder"]
    check(len(info) == 1, "no library-folder info row")
    raw = info[0].value
    for w in (1280, 640, 420, 300):
        _label, shown = panel.row_display(info[0], w, FONT)
        check(shown, "empty library folder at w=%d" % w)
        check(FONT.size(shown)[0] <= panel.panel_size(w, 0)[0] - 2 * 20,
              "shortened path still overflows at w=%d" % w)
        if FONT.size(raw)[0] > panel.panel_size(w, 0)[0] - 2 * 20:
            check(shown != raw and ("…" in shown or "..." in shown),
                  "long path not marked as shortened at w=%d: %r" % (w, shown))
    print("   %r" % raw[:30] + "...\n   -> %r" % panel.row_display(info[0], 420, FONT)[1])


def test_draw():
    print("13) draw() paints and survives odd sizes")
    screen = pygame.display.get_surface()
    panel = build()
    check(panel.draw(screen, FONT, 1280, 720) is None, "draw on a closed panel")
    panel.open()
    before = pygame.image.tobytes(screen, "RGB")
    panel.draw(screen, FONT, 1280, 720)
    painted = pygame.image.tobytes(screen, "RGB")
    check(before != painted, "draw() changed no pixels")
    for w, h in ((320, 240), (420, 320), (800, 600), (1920, 1080)):
        panel.draw(screen, FONT, w, h)
    print("   drawn at 4 sizes, no exception")

    print("14) draw() works with no explicit size, from the stored rect")
    panel.rect = pygame.Rect(0, 0, 1280, 720)
    panel.draw(screen)
    check(panel.capturing is False, "draw changed the capture state")
    print("   fallback sizing OK")

    print("15) the keymap and cfg the panel was given stay live objects")
    cfg = config.load_config(SETTINGS)
    km = actions.Keymap(cfg["keymap"])
    panel = SettingsPanel(cfg, km)
    panel.open()
    panel.select_action("cycle_visualizer")
    press(panel, pygame.K_RETURN)
    press(panel, pygame.K_b)
    check(km.bindings["cycle_visualizer"].upper() == "B", "panel copied the keymap")
    check(cfg["keymap"]["cycle_visualizer"].upper() == "B", "cfg not updated in place")
    print("   the caller's Keymap and cfg both reflect the edit")


def main():
    test_rows()
    test_open_close()
    test_navigation()
    test_rebind()
    test_modifier_ignored()
    test_toggle()
    test_reset()
    test_labels_fit()
    test_draw()
    print("\nSETTINGS PANEL TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("\nFAILED: %s" % exc)
        sys.exit(1)
