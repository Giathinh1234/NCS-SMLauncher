#!/usr/bin/env python3
"""Headless verification of the Hermes chat wrap and input logic.

The sphere itself is verified by tests/test_ncs_sphere.py, which owns its
geometry, palette and frame-time checks against the reference image. This file
keeps only the chat half, which is unchanged.
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

# --- HermesChat input handling ---
chat = nl.HermesChat(font, pygame.Rect(0, 0, 1280, 720))
chat.context = "ctx"


class Ev:
    def __init__(self, key, unicode=""):
        self.key = key
        self.unicode = unicode


for ch in "hi":
    chat.handle_key(Ev(0, ch))
print("input buffer after typing 'hi':", repr(chat.input_buf))
assert chat.input_buf == "hi"
chat.handle_key(Ev(pygame.K_BACKSPACE))
assert chat.input_buf == "h", chat.input_buf
print("backspace OK ->", repr(chat.input_buf))
assert chat.handle_key(Ev(pygame.K_ESCAPE)) == "close"
print("esc returns close OK")

print("\nALL CHECKS PASSED")
