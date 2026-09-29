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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
import ncs_launcher as _L  # noqa: E402

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


class FakeTorrents:
    """Enough of the libtorrent manager for the ESC branch.

    status_lines() is what the branch consults to decide whether the download
    panel is on screen. Returning [] means "nothing to show", so the panel is
    not up and ESC belongs to the quit ladder.
    """

    def __init__(self, status_lines=()):
        self._status_lines = list(status_lines)

    def status_lines(self):
        return list(self._status_lines)


class FakeEvent:
    """Only .key matters to the ESC branch."""

    def __init__(self, key):
        self.key = key
        self.mod = 0
        self.unicode = ""


def _record_notice(ns, entry, duration=3.0):
    ns["notice"] = entry


def presses_to_quit(torrents_present, notice=None, overlay_open=False,
                    torrents_dismissed=False, max_presses=6,
                    torrent_status_lines=()):
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
        # A stub with status_lines(), because the branch now asks whether the
        # panel is genuinely on screen. A bare object() has no such method,
        # which is part of why this file never saw the bug: the old branch
        # only tested `notice`, so the stub never needed to be realistic.
        "torrents": (FakeTorrents(torrent_status_lines)
                     if torrents_present else None),
        "torrents_dismissed": torrents_dismissed,
        "overlay_open": overlay_open,
        "overlay_text": "abc" if overlay_open else "",
        "notice": notice,
        "quit_armed_until": 0.0,
        "running": True,
        # Replaced per-iteration below with one bound to this namespace. It
        # must record, exactly as the real push_notice writes `notice`: a
        # no-op meant the first ESC's warning never existed as far as the
        # second press was concerned, which is how this bug stayed hidden.
    }
    code = compile(block, "<esc>", "exec")
    seen = []
    for i in range(1, max_presses + 1):
        ns["running"] = True
        # NOT reset between presses: quit_armed_until is the state that makes
        # the second press a quit, so clearing it every iteration meant press
        # two could never see the arm and nothing ever quit. That was the
        # harness lying, not the app.
        # The branch calls _torrent_panel_up(); an exec'd snippet
        # has no access to module globals, so hand it in explicitly.

        ns['_torrent_panel_up'] = _L._torrent_panel_up
        ns['push_notice'] = lambda *a, **k: _record_notice(ns, *a, **k)

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

print("WITH THE TORRENT PANEL REALLY UP (a status line to show)")
# This case used to pass notice=("info", "hi") and call that "the panel is on
# screen". It is not. A notice is a one-line banner from draw_notice(); the
# panel is draw_torrent_overlay() and it appears for the infohash prompt or
# for torrent status lines. Treating a bare notice as the panel is the bug
# that made ESC unable to quit: the arming notice is itself a notice, so the
# confirm press always believed a panel was open.
n, seen = presses_to_quit(torrents_present=True,
                          torrent_status_lines=["name: x  50%  1.2 MB/s"])
check(f"press 1 dismisses the panel, quit needs 3 total (took {n})", n == 3, seen)
check("press 1 really did dismiss it", seen[0][2] is True, seen[0])

print("WITH A BARE NOTICE SHOWING (a banner, NOT the panel)")
# Must NOT be treated as the panel: ESC belongs to the quit ladder.
n, seen = presses_to_quit(torrents_present=True, notice=("info", "hi"))
check(f"a banner does not block the quit (took {n})", n == 2, seen)
check("and it did not get mistaken for a panel", seen[0][2] is False, seen[0])

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


# ---------------------------------------------------------------------------
# Why this file could not have caught the ESC-could-not-quit bug
# ---------------------------------------------------------------------------
# The branch used to read `(notice or overlay_open)` for "the panel is open".
# That is wrong because `notice` is shared state: the quit ladder's own
# "press Esc again to quit" warning is a notice, so the second ESC always
# believed a panel was up, dismissed it instead, and never quit. With
# libtorrent installed that made ESC unable to quit the app at all.
#
# The two cases above now cover both halves, against the real branch:
#   - a REAL torrent status list absorbs the first ESC, quit takes 3
#   - a BARE notice does not, and quit takes 2
#
# An earlier draft of this file also reimplemented the ladder as a local
# function so it could run the arm-then-confirm sequence. That was deleted on
# purpose: a second copy of the logic in a test drifts from the original and
# then passes while the app is broken. `presses_to_quit` above execs the
# shipped branch itself, so it cannot drift.
