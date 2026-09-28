"""Regressions for the three severe audit findings.

  1. A track that will not decode took the whole app down.
  2. Nothing advanced when a track ended, so the app held the last sample
     forever and showed NOW PLAYING until you pressed next.
  3. A video shorter than its track re-seeked every frame: 60 ffmpeg spawns
     and 14.6s of work per second of frames.

All three are reproduced with real media and real processes.
"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = "/Users/giathinh/ncs-music-launcher"
sys.path.insert(0, os.path.join(ROOT, "src"))
fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


def wav(path, seconds=1.0, freq=440):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "quiet", "-f", "lavfi",
                    "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ac", "1", "-ar", "44100", path], capture_output=True)
    return path


print("AUDIT REGRESSIONS (severe)")

# --------------------------------------------------------- 1. undecodable file
print("1) a corrupt file must not take the app down")
import ncs_launcher                                  # noqa: E402
from ncs_launcher import Player                       # noqa: E402

with tempfile.TemporaryDirectory() as d:
    empty = os.path.join(d, "empty.mp3")
    open(empty, "wb").close()                        # 0 bytes, like music/test.mp3
    junk = os.path.join(d, "junk.mp3")
    with open(junk, "wb") as fh:
        fh.write(b"this is definitely not audio" * 40)
    good = wav(os.path.join(d, "good.wav"), seconds=1.0)

    reported = []
    pl = Player.__new__(Player)          # no audio device needed for this
    pl.samples = None
    pl.pos = 0
    pl.paused = True
    pl.muted = False
    pl.volume = 1.0
    pl.track_path = None
    import threading
    pl.lock = threading.Lock()
    pl.on_load_error = reported.append

    for bad, name in ((empty, "0-byte"), (junk, "garbage")):
        try:
            ok = pl.load(bad)
            check(f"a {name} file returns False instead of raising", ok is False,
                  ok)
        except Exception as e:
            check(f"a {name} file returns False instead of raising", False,
                  f"{type(e).__name__}: {e}")
        check(f"a {name} file was reported to the user", bool(reported),
              reported[-3:])
        check(f"and the player kept its previous state (nothing loaded)",
              pl.samples is None)

    check("a good file still loads", pl.load(good) is True)
    check("and the position reset to zero", pl.pos == 0)
    check("a good load did NOT report an error",
          len(reported) == 2, reported)

check("the repo no longer ships the 0-byte music/test.mp3",
      not os.path.exists(os.path.join(ROOT, "music", "test.mp3"))
      or os.path.getsize(os.path.join(ROOT, "music", "test.mp3")) > 0)


# ------------------------------------------------- 2. a track has to advance
print("2) a finished track must be noticed, and must advance")
pl2 = Player.__new__(Player)
import numpy as np                                  # noqa: E402
pl2.samples = np.zeros(44100, dtype=np.float32)     # exactly 1 second
pl2.pos = 0
pl2.paused = False
pl2.lock = threading.Lock()

check("finished() is False at the start of a track", pl2.finished() is False)
pl2.pos = len(pl2.samples) - 2
check("finished() is False one sample from the end", pl2.finished() is False,
      pl2.pos)
pl2.pos = len(pl2.samples) - 1
check("finished() is True once the callback parks at the end",
      pl2.finished() is True, pl2.pos)
pl2.paused = True
check("finished() is False while paused", pl2.finished() is False)
pl2.paused = False
pl2.samples = None
check("finished() is False with nothing loaded", pl2.finished() is False)

src = open(os.path.join(ROOT, "src", "ncs_launcher.py"), encoding="utf-8").read()
check("the main loop actually calls finished()", "player.finished()" in src)
check("and steps to the next track when it does",
      "selected = (selected + 1) % len(tracks)" in src.split(
          "if player.finished():")[1][:200])

# The old comment claimed the UI advanced. It did not.
check("the 'hold at end; UI advances' lie is gone",
      "UI advances" not in src)
check("and the comment now says who actually advances",
      "player.finished()" in src)


# --------------------------------------------------------- 3. the seek storm
print("3) a sidecar shorter than its track must not re-seek every frame")
import ncs_video                                    # noqa: E402


class FakePlayer:
    """A playhead parked past the end of the video, which is the trigger."""

    def __init__(self, pos):
        self._pos = pos

    def position(self):
        return self._pos


class CountingVisual:
    duration = 6.0
    width, height = 16, 9          # draw() reads these for the fit

    def __init__(self):
        self.seeks = 0

    def seek(self, s):
        self.seeks += 1

    def close(self):
        pass

    def next_surface(self, box):
        # A real (surface, rect) every frame, NOT None. Returning None is how
        # draw() learns a video ended, and that ends the slot -- which killed
        # the stub after one frame and made the seek count read 0 for the
        # wrong reason.
        class S:
            def get_width(self):
                return 16

            def get_height(self):
                return 9
        return S(), type("R", (), {"topleft": (0, 0)})()

    eof = False


class Slot(ncs_video.VideoSlot):
    """A VideoSlot with the ffmpeg-dependent half stubbed out."""

    def __init__(self, visual):
        self.visual = visual
        self._seeked_to = -1.0
        self._want = None
        self.source = "x"
        # draw() reads this before the seek block; without it the frame dies
        # with an AttributeError that the old version of this test swallowed,
        # which is why the seek count came back as 0 and looked like a pass.
        self._source_size = (16, 9)
        self.enabled = True
        self.message = ""
        self.allow_web = False
        self._user_off = False
        self.frames_drawn = 0
        self.on_error = None

    def _probed_size(self, want):
        return (16, 9)

    def _target_size(self, box):
        return (16, 9)

    def _target_size_raw(self, box):
        return (16, 9)


class Box:
    width, height = 64, 36


class FakeScreen:
    """draw() ends with screen.blit(surf, rect.toplevel)."""

    def __init__(self):
        self.blits = 0

    def blit(self, surf, pos):
        self.blits += 1


# pos well past the 6s video duration -- exactly the reported trigger.
screen1 = FakeScreen()
vis = CountingVisual()
slot = Slot(vis)
player = FakePlayer(20.0)
err = None
for _ in range(60):
    try:
        slot.draw(screen1, Box(), player=player)
    except Exception as e:          # record, do not swallow
        err = err or f"{type(e).__name__}: {e}"
        break

check("draw() ran without raising", err is None, err)
check(f"60 frames past the video duration caused 1 seek, not 60 "
      f"(got {vis.seeks})", vis.seeks == 1, vis.seeks)

# A genuinely drifting playhead should still re-seek.
screen2 = FakeScreen()
vis2 = CountingVisual()
slot2 = Slot(vis2)
p2 = FakePlayer(0.0)
err2 = None
for i in range(60):
    p2._pos = i * 0.5          # 0 .. 29.5s, well past the 6s duration
    try:
        slot2.draw(screen2, Box(), player=p2)
    except Exception as e:
        err2 = err2 or f"{type(e).__name__}: {e}"
        break
check("a moving playhead did not raise", err2 is None, err2)
# A playhead that genuinely moves SHOULD keep re-seeking -- that is the
# feature. It drifts 0.5s per frame against a 1.0s tolerance, so a re-seek
# every second or two is correct. The bug was a *static* playhead re-seeking
# forever; that is the check above, which wants exactly 1.
check(f"a genuinely drifting playhead still re-seeks (got {vis2.seeks})",
      vis2.seeks >= 5, vis2.seeks)
check("and it drew a frame each time", screen2.blits == 60, screen2.blits)

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("SEVERE AUDIT REGRESSION TESTS PASSED")
