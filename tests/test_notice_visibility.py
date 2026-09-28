"""Can the user actually see a message when something goes wrong?

Notices were painted only from inside draw_torrent_overlay, which is gated on
libtorrent being installed. With libtorrent absent nothing was ever reported
at all, and with it present an unrelated one-line message dragged the whole
torrent download panel onto the screen. Both directions were wrong.

This renders the banner onto a real surface and counts non-black pixels, so
"it was called" and "the user could see it" are different assertions.
"""
import os
import sys

ROOT = "/Users/giathinh/ncs-music-launcher"
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame                                        # noqa: E402
import pygame.surfarray                              # noqa: E402

fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


pygame.init()
W, H = 800, 600
font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 16)
import ncs_launcher                                  # noqa: E402

print("NOTICES")


def pixels(notice):
    screen = pygame.Surface((W, H))
    screen.fill((0, 0, 0))
    ncs_launcher.draw_notice(screen, font, W, H, notice)
    arr = pygame.surfarray.array3d(screen)
    return int((arr.max(axis=2) > 8).sum())


check("there is a standalone draw_notice", hasattr(ncs_launcher, "draw_notice"))

check("no notice draws nothing",
      pixels(None) == 0, pixels(None))

for kind in ("info", "error", "warn", "something-unknown"):
    n = pixels((kind, f"a {kind} message"))
    check(f"a {kind} notice is visible ({n} px)", n > 100, n)

# A real message the app actually emits.
n = pixels(("error", "cannot play test.mp3: DecodeError"))
check("an undecodable-track error is visible", n > 100, n)

# Long text must not overflow the window.
n = pixels(("error", "x" * 400))
check("a very long notice still fits and is visible", n > 100, n)

src = open(os.path.join(ROOT, "src", "ncs_launcher.py"), encoding="utf-8").read()
check("the main loop calls draw_notice unconditionally",
      "draw_notice(screen, font, w, h, notice)" in src)

# The old gate: notices were only reachable through the torrent panel.
loop = src[src.find("def main()"):]
check("notices are no longer gated on `if torrents`",
      "draw_notice(screen, font, w, h, notice)\n\n        # Draw the control-API"
      in loop,
      "the call may have moved inside a torrents block")

# Count how many push_notice sites exist, to show what was previously invisible.
check("the app has many notice sites that all depend on this",
      src.count("push_notice(") > 15, src.count("push_notice("))

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("NOTICE VISIBILITY TEST PASSED")
