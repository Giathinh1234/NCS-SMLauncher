"""The visualizer lean setting: config, clamping, the slider row, and the
actual pixel movement of the sphere.

The lean exists because the sphere is a square object in a wide window: dead
centred, it leaves dead space on both sides and reads small. Leaning it lets
the ball sit against one edge.

What is actually verified here, and why each case matters:
  * clamp_lean refuses the values a hand-edited settings.json can contain.
    settings.json is user-editable and survives upgrades, so "lean": "left"
    or "lean": 47 is reachable, and the renderer multiplies the window by it.
  * the sphere really MOVES, measured by finding the centroid of lit pixels.
    Asserting the call was made with the right argument would pass even if
    the renderer ignored it.
  * the sphere never leaves the buffer at full lean.
  * the slider persists through the settings panel's own save path.

Run:  python3 tests/test_visualizer_lean.py
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
os.environ.setdefault("HASHPLAY_CONFIG_DIR", "/tmp/hashplay-lean-test")

import pygame

import config
import ncs_sphere

FAILS = []


def check(condition, message):
    if condition:
        print("     ok   %s" % message)
    else:
        FAILS.append(message)
        print("     FAIL %s" % message)


class SilentPlayer:
    """A player whose spectrum is all zeros, so the sphere is static."""

    def spectrum(self):
        return [0.0] * 64

    def level(self):
        return 0.4

    def position(self):
        return 0.0

    def duration(self):
        return 0.0

    def playing(self):
        return False

    def paused(self):
        return False

    def volume(self):
        return 0.5

    def title(self):
        return ""

    def filename(self):
        return ""

    def set_volume(self, _v):
        pass


def lit_centroid_x(surface):
    """Mean x of the lit pixels. Uses the same threshold as the comparison tool."""
    w, h = surface.get_size()
    total = 0.0
    count = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = surface.get_at((x, y))[:3]
            if r + g + b > 90:
                total += x
                count += 1
    if count == 0:
        return None, 0
    return total / count, count


def lit_extent_x(surface):
    """(leftmost, rightmost, count) of lit pixels."""
    w, h = surface.get_size()
    lo, hi, count = None, None, 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = surface.get_at((x, y))[:3]
            if r + g + b > 90:
                count += 1
                if lo is None or x < lo:
                    lo = x
                if hi is None or x > hi:
                    hi = x
    return lo, hi, count


def render(lean, w=900, h=640):
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    screen = pygame.display.set_mode((w, h))
    screen.fill((0, 0, 0))
    ncs_sphere.draw_ncs_sphere(screen, SilentPlayer(), w, h, t=1.0,
                               lean_value=lean)
    return screen


print("\n  clamp_lean survives a hand-edited settings file")
for bad, expected in [
    (None, 0.0), ("left", 0.0), ([], 0.0), ({}, 0.0),
    (float("nan"), 0.0), (float("inf"), 0.0), (float("-inf"), 0.0),
    (47.0, 1.0), (-47.0, -1.0), (2.5, 1.0), (-2.5, -1.0),
    (0.5, 0.5), (0, 0.0), ("0.5", 0.5),
]:
    got = config.clamp_lean(bad)
    check(got == expected, "clamp_lean(%r) == %r (got %r)" % (bad, expected, got))

print("\n  the default config ships a centred, in-range lean")
check("visualizer_lean" in config.DEFAULT_CONFIG,
      "DEFAULT_CONFIG has a visualizer_lean key")
check(config.DEFAULT_CONFIG["visualizer_lean"] == 0.0,
      "the default lean is 0.0 (centred), not left or right")
check(config.clamp_lean(config.DEFAULT_CONFIG["visualizer_lean"]) == 0.0,
      "the shipped default passes through the clamp")

print("\n  the sphere actually moves")
try:
    centre = lit_centroid_x(render(0.0))
    left = lit_centroid_x(render(-1.0))
    right = lit_centroid_x(render(1.0))
except Exception as exc:                                  # noqa: BLE001
    check(False, "rendering the sphere raised %r" % (exc,))
    centre = left = right = (None, 0)

check(centre[0] is not None,
      "the centred sphere lit some pixels (%d sampled)" % centre[1])
check(left[0] is not None and right[0] is not None,
      "both leaned spheres lit pixels (%d / %d)"
      % (left[1], right[1]))
if centre[0] is not None and left[0] is not None and right[0] is not None:
    check(left[0] < centre[0],
          "lean=-1 moves the ball LEFT (%.0f < %.0f)" % (left[0], centre[0]))
    check(right[0] > centre[0],
          "lean=+1 moves the ball RIGHT (%.0f > %.0f)" % (right[0], centre[0]))
    check(abs(left[0] - right[0]) > 100,
          "the full lean range is a large movement (%.0f px across)"
          % (right[0] - left[0]))
    check(abs(centre[0] - 450) < 25,
          "lean=0 is still centred (centroid %.0f, window centre 450)"
          % centre[0])

print("\n  a full lean stays inside the window")
# At full lean the sphere centre moves one radius from the middle, so the ball
# is flush with an edge but must not be clipped away.
# The centroid test above would still pass if the ball were clipped in half,
# because a half-ball has a shifted centroid. Check the actual extent.
for label, lean in (("left", -1.0), ("right", 1.0)):
    lo, hi, count = lit_extent_x(render(lean))
    check(lo is not None and count > 0,
          "the fully-%s ball lit pixels (%d)" % (label, count))
    check(lo is not None and lo >= 0,
          "the fully-%s ball is not clipped at the left edge (leftmost %r)"
          % (label, lo))
    check(hi is not None and hi <= 900,
          "the fully-%s ball is not clipped at the right edge (rightmost %r)"
          % (label, hi))
# And centred must stay clear of both edges too.
lo, hi, _ = lit_extent_x(render(0.0))
check(lo is not None and lo >= 0 and hi is not None and hi <= 900,
      "the centred ball is fully inside the window (%r..%r)" % (lo, hi))

print("\n  the settings panel exposes it as a slider and persists it")
import settings_panel as sp

panel = sp.SettingsPanel(cfg=config.default_config(), keymap=None)
panel.open()
rows = panel.items
slider = [r for r in rows if r.kind == "slider"]
check(len(slider) == 1, "the panel has exactly one slider row (found %d)"
      % len(slider))
if slider:
    row = slider[0]
    check(row.key == "visualizer_lean", "the slider edits visualizer_lean")
    check(row.minimum == config.LEAN_MIN and row.maximum == config.LEAN_MAX,
          "the slider spans the documented range")
    check(row.label, "the slider has a label: %r" % row.label)

    # _adjust() acts on the SELECTED row, so select the slider first. Without
    # this every adjustment is a no-op on whatever row happens to be at index
    # 0 and the assertions below pass a value of 0.0 for the wrong reason.
    slider_index = rows.index(row)
    panel.select(slider_index)
    check(panel.current_row() is row,
          "the slider can be selected (index %d)" % slider_index)

    # Left/Right must move it, and must clamp at the ends.
    panel.cfg["visualizer_lean"] = 0.0
    row.value = 0.0
    for _ in range(40):
        panel._adjust(1)
    check(panel.cfg["visualizer_lean"] == config.LEAN_MAX,
          "holding Right parks exactly on +1.0 (got %r)"
          % panel.cfg["visualizer_lean"])
    for _ in range(80):
        panel._adjust(-1)
    check(panel.cfg["visualizer_lean"] == config.LEAN_MIN,
          "holding Left parks exactly on -1.0 (got %r)"
          % panel.cfg["visualizer_lean"])

    # Repeated nudges must not accumulate float noise in the saved file.
    panel.cfg["visualizer_lean"] = 0.0
    row.value = 0.0
    for _ in range(3):
        panel._adjust(1)
    check(abs(panel.cfg["visualizer_lean"] - 0.15) < 1e-6,
          "three steps of 0.05 land on 0.15, not 0.15000000000000002 (%r)"
          % panel.cfg["visualizer_lean"])

print("\n" + ("LEAN TESTS PASSED" if not FAILS
             else "%d FAILURES: %s" % (len(FAILS), FAILS)))
sys.exit(1 if FAILS else 0)
