"""Launcher integration: the 'video' mode really paints frames.

This exercises draw_visualizer() and the key/overlay plumbing in the real
ncs_launcher module, not ncs_video in isolation.
"""
import os
import subprocess
import sys
import tempfile

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import pygame
pygame.init()
screen = pygame.display.set_mode((1280, 800))

import ncs_launcher as nl
import ncs_video as nv

SCRATCH = os.path.join(os.path.expanduser("~"), ".hermes", "cache", "scratch")
CLIP = os.path.join(SCRATCH, "ncs_video_probe.mp4")
if not os.path.exists(CLIP):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", CLIP], check=True, capture_output=True)


class FakePlayer:
    """Just enough Player for the draw path: a spectrum and a position."""
    def spectrum(self):
        return [0.1] * 64

    def position(self):
        return 0.0

    track_path = "/tmp/x.mp3"
    paused = False
    muted = False


print("1) 'video' is a real mode in the launcher's mode list")
src = open("/Users/giathinh/ncs-music-launcher/src/ncs_launcher.py").read()
assert '"video"' in src, "video must be in the modes list"
assert "import ncs_video" in src
print("   ok")

print("2) draw_visualizer accepts a video_slot and the video mode")
import inspect
sig = inspect.signature(nl.draw_visualizer)
print("   signature:", sig)
assert "video_slot" in sig.parameters

print("3) an armed slot with no video draws a message, not a crash")
slot = nv.VideoSlot()
slot.enabled = True
nl.draw_visualizer(screen, FakePlayer(), 1280, 800, "video", 0.0, {},
                   video_slot=slot)
print("   ok, message was:", repr(slot.message))
slot.clear(by_user=False)

print("4) a live video paints real pixels into the window")
screen.fill((0, 0, 0))
slot = nv.VideoSlot()
slot.request(CLIP)
drew = 0
for i in range(6):
    nl.draw_visualizer(screen, FakePlayer(), 1280, 800, "video", float(i), {},
                       video_slot=slot)
    if slot.frames_drawn:
        drew = slot.frames_drawn
print("   frames drawn:", slot.frames_drawn)
assert slot.frames_drawn > 0, "draw_visualizer must actually paint frames"
arr = pygame.surfarray.array3d(screen)
print("   window mean brightness: %.1f" % arr.mean())
assert arr.mean() > 5.0, "the window should not be black; video did not paint"
slot.clear(by_user=False)

print("5) the other modes are untouched and still work")
for mode in ("bars", "mirror", "radial", "disc", "album"):
    screen.fill((0, 0, 0))
    nl.draw_visualizer(screen, FakePlayer(), 1280, 800, mode, 0.0, {})
    print("   ", mode, "ok")
print("6) video mode with slot=None must not raise")
nl.draw_visualizer(screen, FakePlayer(), 1280, 800, "video", 0.0, {},
                   video_slot=None)
print("   ok")

print("7) the video overlay renders without ffmpeg being consulted")
font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)
nl.draw_video_overlay(screen, font, 1280, 800, "/some/where/clip.mp4")
nl.draw_video_overlay(screen, font, 1280, 800, "")
print("   ok")

print("8) the V key is wired into the keydown chain")
assert "elif event.key == pygame.K_v:" in src, "V must be handled"
assert "video_overlay_open = True" in src
assert "video_slot.pin(want)" in src, "an explicit source must be pinned"
print("   ok")

print("9) the hint bar advertises V")
assert "V video" in src
print("   ok")

print("10) cleanup closes the video slot before pygame.quit()")
tail = src[src.rindex("# Cleanup"):]
assert "video_slot.clear(" in tail, "ffmpeg must be reaped on exit"
assert tail.index("video_slot.clear(") < tail.index("pygame.quit()"), \
    "the pipe must be closed BEFORE pygame quits"
print("   ok")

print("11) the visual area still gets the UI drawn on top of a video")
# draw_ui is called after draw_visualizer in the loop; confirm the order.
loop = src[src.index("while running:"):]
vi = loop.index("draw_visualizer(")
ui = loop.index("draw_ui(")
print("   draw_visualizer at", vi, "| draw_ui at", ui)
assert vi < ui, "the UI must be drawn after the video, so it stays readable"
print("   ok")

print("12) a sidecar in a real library layout is found and played")
with tempfile.TemporaryDirectory() as d:
    audio = os.path.join(d, "Song.mp3")
    open(audio, "wb").write(b"ID3")
    with open(CLIP, "rb") as s, open(os.path.join(d, "Song.mp4"), "wb") as t:
        t.write(s.read())
    slot = nv.VideoSlot()
    slot.request(nv.find_sidecar(audio))
    screen.fill((0, 0, 0))
    for _ in range(4):
        nl.draw_visualizer(screen, FakePlayer(), 1280, 800, "video", 0.0, {},
                           video_slot=slot)
    assert slot.frames_drawn > 0
    print("   sidecar painted", slot.frames_drawn, "frames")
    slot.clear(by_user=False)

print("LAUNCHER VIDEO INTEGRATION TESTS PASSED")
