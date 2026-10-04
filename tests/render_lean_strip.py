"""Render the same sphere at three lean positions, side by side.

Visual proof that the setting does what it says: one image, three leans, so
the movement is visible rather than asserted. Writes /tmp/lean_strip.png.

Run:  python3 tests/render_lean_strip.py
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import ncs_sphere


class SilentPlayer:
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


pygame.font.init()

W, H = 460, 300
LEANS = [(-1.0, "LEFT"), (0.0, "CENTRE"), (1.0, "RIGHT")]
strip = pygame.Surface((W * len(LEANS), H + 26))
strip.fill((10, 12, 18))
font = pygame.font.SysFont("monospace", 15)

for i, (lean, name) in enumerate(LEANS):
    panel = pygame.Surface((W, H))
    panel.fill((10, 12, 18))
    ncs_sphere.draw_ncs_sphere(panel, SilentPlayer(), W, H, t=1.0,
                               lean_value=lean)
    strip.blit(panel, (i * W, 0))
    pygame.draw.line(strip, (40, 44, 58), ((i + 1) * W, 0), ((i + 1) * W, H))
    label = font.render("lean %+.0f  %s" % (lean, name), True, (200, 205, 220))
    strip.blit(label, (i * W + 10, H + 5))

out = "/tmp/lean_strip.png"
pygame.image.save(strip, out)
print("  wrote %s (%d bytes)" % (out, os.path.getsize(out)))
