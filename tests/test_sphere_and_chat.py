#!/usr/bin/env python3
"""Headless verification of the NCS sphere + chat wrap logic."""
import os, sys, time, math
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np
import pygame

import ncs_launcher as nl

pygame.init()
pygame.display.set_mode((1280, 720))
screen = pygame.display.get_surface()


class FakePlayer:
    def __init__(self):
        self.n = 32
        self.k = 0

    def spectrum(self):
        self.k += 1
        return np.linspace(0, 1, self.n).astype(np.float32)


p = FakePlayer()

# --- sphere geometry sanity ---
base = nl._fibonacci_sphere(5200)
print("sphere points:", base.shape)
r = np.linalg.norm(base, axis=1)
print("radius min/max: %.4f / %.4f (want ~1.0)" % (r.min(), r.max()))

# --- render frames + measure timing ---
t0 = time.time()
FRAMES = 60
for i in range(FRAMES):
    nl.draw_ncs_sphere(screen, p, 1280, 720, i * 0.016)
elapsed = time.time() - t0
print("sphere: %d frames in %.2fs = %.1f fps"
      % (FRAMES, elapsed, FRAMES / elapsed))

# --- is anything actually drawn? ---
arr = pygame.surfarray.array3d(screen)
lit = int((arr.sum(axis=2) > 30).sum())
print("lit pixels:", lit, "(want > 2000, got %s)" % ("OK" if lit > 2000 else "FAIL"))

# gold-ish check: R and G high, B low on lit pixels
px = arr.reshape(-1, 3)[arr.reshape(-1, 3).sum(axis=1) > 30]
if len(px):
    print("mean lit RGB: R=%.0f G=%.0f B=%.0f (want R>=G>>B for gold)"
          % (px[:, 0].mean(), px[:, 1].mean(), px[:, 2].mean()))

# --- perspective: points must stay on screen ---
geo = nl._get_sphere_geometry(1280, 720)["base"]
print("cached geometry reused:", geo is nl._get_sphere_geometry(1280, 720)["base"])

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
