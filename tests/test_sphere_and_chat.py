#!/usr/bin/env python3
"""Headless verification of wrap_text and the control-API panel.

The sphere itself is verified by tests/test_ncs_sphere.py, which owns its
geometry, palette and frame-time checks against the reference image.

This file used to also cover HermesChat's text input. That panel is gone as
of 1.1.0 -- it shelled out to the `hermes` CLI, and the app now exposes a
loopback control API instead (src/control_api.py, tests/test_control_api.py).
What remains here is the shared text layout, which the API panel still uses.
"""
import os, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import pygame

import ncs_launcher as nl

pygame.init()
pygame.display.set_mode((1280, 720))
screen = pygame.display.get_surface()

# --- wrap_text ---
font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)
long_text = "word " * 200
lines = nl.wrap_text(font, long_text, 400)
print("wrap lines:", len(lines), "max width:",
      max(font.size(l)[0] for l in lines), "(must be <= 400)")
assert all(font.size(l)[0] <= 400 for l in lines), "wrap overflowed"

# long unbreakable token
tok = "x" * 900
lines2 = nl.wrap_text(font, tok, 300)
print("hard-wrap of long token -> max width:",
      max(font.size(l)[0] for l in lines2))
assert all(font.size(l)[0] <= 300 for l in lines2), "hard wrap overflowed"

# --- the API panel replaced the chat panel ---
assert hasattr(nl, "ApiPanel"), "ApiPanel is missing"
assert not hasattr(nl, "HermesChat"), \
    "HermesChat is gone from the launcher; this test needs updating"
assert not hasattr(nl, "HERMES_BIN"), \
    "the launcher must not resolve the hermes CLI any more"

src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                        "ncs_launcher.py"), encoding="utf-8").read()
for bad in ("HERMES_BIN", "subprocess.Popen([HERMES_BIN"):
    assert bad not in src, f"launcher still references {bad}"


class Ev:
    def __init__(self, key, unicode=""):
        self.key = key
        self.unicode = unicode


panel = nl.ApiPanel(font, pygame.Rect(0, 0, 1280, 720),
                    lambda: "http://127.0.0.1:8777  (token: abc123)")
# the panel is read-only, so esc/q close it and nothing else does
assert panel.handle_key(Ev(pygame.K_ESCAPE)) == "close"
assert panel.handle_key(Ev(pygame.K_q)) == "close"
assert panel.handle_key(Ev(pygame.K_a)) is None, "a random key must not close it"
# typing into it does nothing: there is no text buffer any more
panel.handle_key(Ev(0, "h"))
print("API panel closes on esc/q, ignores everything else")

# it must actually paint without touching anything that no longer exists
panel.draw(screen, 1.0, 1280, 720)
print("API panel drew cleanly")

print("\nALL CHECKS PASSED")
