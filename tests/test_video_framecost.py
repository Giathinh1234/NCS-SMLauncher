"""Honest frame-cost measurement for the video visual.

The claim that matters is whether adding video keeps the app at 60 FPS. This
measures the real per-frame cost of: the pipe read, the numpy transpose, the
surface build, and the blit -- at the real default window size.
"""
import os
import subprocess
import sys
import time

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import pygame
pygame.init()
screen = pygame.display.set_mode((1280, 800))
font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)
import ncs_video as nv
import ncs_launcher as nl

SCRATCH = os.path.join(os.path.expanduser("~"), ".hermes", "cache", "scratch")
CLIP = os.path.join(SCRATCH, "ncs_video_probe.mp4")
BIG = os.path.join(SCRATCH, "ncs_video_1080.mp4")
if not os.path.exists(BIG):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=6",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
         BIG], check=True, capture_output=True)


class FakePlayer:
    def spectrum(self):
        return [0.1] * 64

    def position(self):
        return 0.0

    track_path = None
    paused = True
    muted = False


def bench(label, clip, w, h, frames=40, draw_ui=True):
    slot = nv.VideoSlot()
    slot.request(clip)
    screen.fill((0, 0, 0))
    # warm up: let the pipe produce its first frame and the font cache settle
    for _ in range(3):
        nl.draw_visualizer(screen, FakePlayer(), w, h, "video", 0.0, {},
                           video_slot=slot)
    if draw_ui:
        nl.draw_ui(screen, font, font, [], 0, FakePlayer(), w, h, False,
                   "/tmp", None)

    t0 = time.perf_counter()
    for i in range(frames):
        nl.draw_visualizer(screen, FakePlayer(), w, h, "video", float(i), {},
                           video_slot=slot)
        if draw_ui:
            nl.draw_ui(screen, font, font, [], 0, FakePlayer(), w, h, False,
                       "/tmp", None)
    dt = time.perf_counter() - t0
    per = dt / frames
    slot.clear(by_user=False)
    ok = "OK  " if per < 0.0167 else "SLOW"
    print(f"   {ok} {label:30} {per*1000:6.2f} ms of CPU per draw call"
          f"   (16.67 ms budget for 60 FPS)")
    return per


print("VIDEO FRAME COST @ 1280x800")
print("   This is CPU cost per draw call, not the displayed frame rate.")
print(f"   The video itself updates at {nv.TARGET_FPS} FPS by design; between")
print("   frames the draw path reuses the last surface without blocking.")
print("1) 320x180 source upscaled to fill the window")
bench("320x180 source", CLIP, 1280, 800)

print("2) a real 1080p source downscaled to the window")
bench("1920x1080 source", BIG, 1280, 800)

print("3) the same, without draw_ui, to separate the two costs")
bench("1080p, visualizer only", BIG, 1280, 800, draw_ui=False)

print("4) the sphere for comparison (the heaviest existing mode)")
screen.fill((0, 0, 0))
t0 = time.perf_counter()
for i in range(10):
    nl.draw_visualizer(screen, FakePlayer(), 1280, 800, "radial", float(i), {},
                       video_slot=None)
dt = (time.perf_counter() - t0) / 10
verdict = "OK  " if dt < 0.0167 else "SLOW"
print(f"   {verdict} {'radial / NCS sphere':30} {dt*1000:6.2f} ms of CPU per frame")

print("5) bars, the cheap baseline")
t0 = time.perf_counter()
for i in range(20):
    nl.draw_visualizer(screen, FakePlayer(), 1280, 800, "bars", float(i), {},
                       video_slot=None)
dt = (time.perf_counter() - t0) / 20
print(f"   {'OK  ' if dt < 0.0167 else 'SLOW'} {'bars':30} {dt*1000:6.2f} ms of CPU per frame")

print("FRAME COST MEASURED")
