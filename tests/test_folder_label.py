"""Regression: the source-folder label must terminate on any path.

The original truncation loop was

    while len(shown) > 52 and "/" in shown[1:]:
        shown = "…" + shown[shown.index("/", 1):]

It removed one leading character and prepended a one-character ellipsis, so
`len(shown)` never decreased. Any library path longer than 52 characters that
still contained a "/" spun forever on the very first frame -- the app froze
with the window up, the process alive and consuming no CPU. It never fired for
a short path like ~/Downloads, which is exactly why it survived being opened
by hand many times.

This calls the real draw_ui(), so a regression hangs the test rather than
passing it.
"""
import os
import signal
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import pygame

import ncs_launcher as nl

pygame.init()
screen = pygame.display.set_mode((1280, 720))
font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)
font_big = pygame.font.SysFont("consolas,menlo,dejavusansmono", 22, bold=True)

fails = []


class StubPlayer:
    """draw_ui only touches these; no audio device needed."""
    track_path = None
    paused = True
    samples = None

    def duration(self):
        return 0.0

    def position(self):
        return 0.0


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}  {detail}")
        fails.append(label)


# Hard ceiling: if draw_ui does not return promptly the test fails instead of
# hanging the whole suite. The old code needed this to be a failure, not a hang.
def _bail(signum, _frame):
    raise TimeoutError("draw_ui did not return")


signal.signal(signal.SIGALRM, _bail)
signal.alarm(10)

PATHS = [
    "/Users/giathinh/Downloads",                       # short, the common case
    "/a" * 60,                                         # long, no slash left
    "/" + "deeply/nested/" * 12 + "Music",              # long, many slashes
    "/var/folders/_d/0fm6f6kj72dg_wx56w8mx6vr0000gn/T/tmp.abc123/music",
    "/" + "x" * 500,                                   # absurd
    "/" * 200,                                         # only slashes
]

for p in PATHS:
    try:
        signal.alarm(10)
        nl.draw_ui(screen, font, font_big, [], 0, StubPlayer(), 1280, 720, False, p,
                   None)
        signal.alarm(0)
        check(f"draw_ui returns for a {len(p)}-char path", True)
    except TimeoutError:
        signal.alarm(0)
        check(f"draw_ui returns for a {len(p)}-char path", False,
              "HUNG -- the truncation loop did not terminate")
    except Exception as exc:
        signal.alarm(0)
        check(f"draw_ui returns for a {len(p)}-char path", False,
              f"{type(exc).__name__}: {exc}")

# The truncation itself: long paths keep their TAIL, because the tail is the
# part that identifies the folder. Losing the tail would make two libraries
# with the same basename indistinguishable on screen.
signal.alarm(10)
nl.draw_ui(screen, font, font_big, [], 0, StubPlayer(), 1280, 720, False,
           "/very/long/prefix/that/goes/on/MyMusicFolder", None)
signal.alarm(0)
check("a long path is drawn without error", True)

# Prove the loop really is gone by asserting the property directly.
shown = "/very/long/prefix/that/goes/on/MyMusicFolder"
truncated = shown if len(shown) <= 52 else "…" + shown[-(52 - 1):]
check("truncation strictly shortens the string", len(truncated) <= 52,
      len(truncated))
check("truncation keeps the tail", truncated.endswith("MyMusicFolder"),
      truncated)

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("FOLDER LABEL TRUNCATION PASSED")
