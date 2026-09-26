#!/usr/bin/env python3
"""Drive the real main() headlessly: resize events + chat open/close."""
import os, sys, threading, time
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher")

import pygame
import ncs_launcher as nl

real_init = pygame.init
state = {"frames": 0}


def fake_init():
    real_init()
    pygame.display.set_mode((1280, 720))


pygame.init = fake_init

# keep the loop short: quit after N frames
orig_get = pygame.event.get
count = {"n": 0}


def counting_get(*a, **k):
    count["n"] += 1
    if count["n"] == 2:
        # simulate a window resize
        pygame.event.post(pygame.event.Event(
            pygame.VIDEORESIZE, w=900, h=600, size=(900, 600)))
    if count["n"] == 4:
        # open the chat panel
        pygame.event.post(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_c, unicode="c", mod=0, scancode=0))
    if count["n"] == 6:
        # type a message
        for ch in "hi":
            pygame.event.post(pygame.event.Event(
                pygame.KEYDOWN, key=ord(ch.upper()), unicode=ch, mod=0,
                scancode=0))
    if count["n"] == 8:
        # close chat with Esc
        pygame.event.post(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode="\x1b", mod=0,
            scancode=0))
    if count["n"] >= 12:
        pygame.event.post(pygame.event.Event(pygame.QUIT))
    return orig_get(*a, **k)


pygame.event.get = counting_get

sys.argv = ["ncs_launcher.py", "/Users/giathinh/ncs-music-launcher/music"]

t0 = time.time()
try:
    nl.main()
    print("main() returned cleanly after %.1fs" % (time.time() - t0))
    print("PASS: resize + chat key routing did not crash the loop")
except SystemExit as e:
    print("SystemExit:", e)
except Exception as e:
    import traceback
    traceback.print_exc()
    print("FAIL: exception escaped main()")
    sys.exit(1)
