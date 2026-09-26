#!/usr/bin/env python3
"""End-to-end: does the chat panel actually get a reply from hermes?"""
import os, sys, time
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import pygame
import ncs_launcher as nl

pygame.init()
pygame.display.set_mode((1280, 720))
font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)

print("hermes binary:", nl.HERMES_BIN)
assert os.path.exists(nl.HERMES_BIN), "hermes binary not found!"

chat = nl.HermesChat(font, pygame.Rect(0, 0, 1280, 720))
chat.context = "You are embedded in the NCS Music Launcher. Be extremely brief."
chat.input_buf = "reply with exactly: PONG from the panel"
chat.submit(chat.context)

print("busy:", chat.busy)
deadline = time.time() + 180
while chat.busy and time.time() < deadline:
    time.sleep(0.5)
    chat.poll()
    if not chat.busy:
        break

chat.poll()
print("messages:", len(chat.messages))
for role, text in chat.messages:
    print(f"  [{role}] {text[:120]}")

assert not chat.busy, "chat stayed busy -> hermes never replied"
assert "PONG" in chat.messages[-1][1], "unexpected reply: %r" % chat.messages[-1][1]
print("\nPASS: real hermes reply received through the panel")
