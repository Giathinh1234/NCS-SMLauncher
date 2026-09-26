#!/usr/bin/env python3
"""Render the NCS sphere to a PNG so it can be eyeballed."""
import os, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import numpy as np
import pygame
import ncs_launcher as nl

pygame.init()
W, H = 900, 900
pygame.display.set_mode((W, H))
screen = pygame.display.get_surface()
screen.fill((6, 8, 14))


class FakePlayer:
    def spectrum(self):
        t = np.linspace(0, 1, 32)
        return (0.35 + 0.55 * np.sin(t * 3.0) ** 2).astype(np.float32)


for f in range(3):
    nl.draw_ncs_sphere(screen, FakePlayer(), W, H, 1.2 + f * 0.9)

out = "/tmp/ncs_sphere_preview.png"
pygame.image.save(screen, out)
print("saved", out)
