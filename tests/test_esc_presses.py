"""Run the real ESC chain and count how many presses it takes to quit.

The existing test_esc_and_close.py inspects the AST, which is why it was
satisfied by code that needed THREE presses: the branch looked right in the
source while behaving wrongly at runtime. So this drives the actual branch
with the real local state, for the states that matter:

  at rest                 -> two presses
  torrent panel visible   -> press one dismisses, two more quit
  infohash prompt open    -> press one closes it, two more quit
  a notice showing        -> press one clears it, two more quit
"""
import os
import sys
import types

ROOT = "/Users/giathinh/ncs-music-launcher"
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import ncs_launcher                                  # noqa: E402

src = open(os.path.join(ROOT, "src", "ncs_launcher.py"), encoding="utf-8").read()
lines = src.splitlines()

# Pull the real ESC block out of the source so we exercise the shipped code,
# not a paraphrase of it.
# The app-wide ESC branch is the one immediately followed by the K_q quit.
# There are several K_ESCAPE branches (video overlay, infohash prompt, the
# app-wide one) and picking the first gets the wrong handler entirely.
end = next(i for i, l in enumerate(lines)
           if l.strip().startswith("elif event.key == pygame.K_q:") and i > 1600)
start = max(i for i in range(end)
            if "if event.key == pygame.K_ESCAPE:" in lines[i])
block = "\n".join(lines[start:end])
# de-indent by 16 (it lives inside the event loop inside main)
block = "\n".join(l[16:] if l.startswith(" " * 16) else l
                  for l in block.splitlines())
print("ESC branch under test:")
for l in block.splitlines():
    if l.strip():
        print("   " + l)
print()

fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


class FakeEvent:
    """Only .key matters to the ESC branch."""

    def __init__(self, key):
        self.key = key
        self.mod = 0
        self.unicode = ""


def presses_to_quit(torrents_present, notice=None, overlay_open=False,
                    torrents_dismissed=False, max_presses=6):
    """Feed ESC presses through the real branch until `running` goes False.

    The block is executed as module-level code in a plain dict, so its
    assignments land in that dict and can be read back. An earlier version
    rewrote the source text to redirect the names, and double-substituted its
    own output into nonsense -- executing the shipped block verbatim is both
    simpler and a stronger claim.
    """
    import time as _time
    import pygame as _pygame

    ns = {
        "event": FakeEvent(_pygame.K_ESCAPE),
        "pygame": _pygame,
        "time": _time,
        "QUIT_CONFIRM_SECS": ncs_launcher.QUIT_CONFIRM_SECS,
        "torrents": object() if torrents_present else None,
        "torrents_dismissed": torrents_dismissed,
        "overlay_open": overlay_open,
        "overlay_text": "abc" if overlay_open else "",
        "notice": notice,
        "quit_armed_until": 0.0,
        "running": True,
        "push_notice": lambda *a, **k: None,
    }
    code = compile(block, "<esc>", "exec")
    seen = []
    for i in range(1, max_presses + 1):
        ns["running"] = True
        # NOT reset between presses: quit_armed_until is the state that makes
        # the second press a quit, so clearing it every iteration meant press
        # two could never see the arm and nothing ever quit. That was the
        # harness lying, not the app.
        exec(code, ns)
        seen.append((i, bool(ns["running"]), ns["torrents_dismissed"],
                     ns["overlay_open"], ns["notice"]))
        if not ns["running"]:
            return i, seen
    return None, seen


print("AT REST (no panel, no prompt) -- libtorrent present")
n, seen = presses_to_quit(torrents_present=True)
check(f"two presses quit (took {n})", n == 2, seen)

print("AT REST -- libtorrent absent")
n, seen = presses_to_quit(torrents_present=False)
check(f"two presses quit (took {n})", n == 2, seen)

print("WITH A NOTICE SHOWING (so the torrent panel is on screen)")
n, seen = presses_to_quit(torrents_present=True, notice=("info", "hi"))
check(f"press 1 dismisses the panel, quit needs 3 total (took {n})", n == 3, seen)
check("press 1 really did dismiss it", seen[0][2] is True, seen[0])

print("WITH THE INFOHASH PROMPT OPEN")
n, seen = presses_to_quit(torrents_present=True, overlay_open=True)
check(f"press 1 closes the prompt, quit needs 3 total (took {n})", n == 3, seen)
check("press 1 really did close it", seen[0][3] is False, seen[0])

print("PANEL ALREADY DISMISSED")
n, seen = presses_to_quit(torrents_present=True, torrents_dismissed=True)
check(f"two presses quit (took {n})", n == 2, seen)

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("ESC PRESS-COUNT TEST PASSED")
