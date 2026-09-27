"""VideoSlot: sidecar discovery, lifecycle, and draw-path failure behaviour.

The regression that matters here is the resize path. An earlier version
compared the live frame size against the *box* size, which never match for a
letterboxed source -- so every single frame tore down and re-spawned ffmpeg.
These tests count process spawns to prove that does not happen.
"""
import os
import subprocess
import sys
import tempfile
import time

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import pygame
pygame.init()
screen = pygame.display.set_mode((1280, 800))

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

BOX = pygame.Rect(0, 0, 1280, 800)

# Count real ffmpeg spawns so the resize assertion means something.
_real_open = nv.VideoVisual._open
SPAWNS = []


def counting_open(self, start_at=0.0):
    SPAWNS.append(1)
    return _real_open(self, start_at=start_at)


nv.VideoVisual._open = counting_open

# ---- sidecar discovery ------------------------------------------------------
print("1) sidecar: same basename, video extension, same folder")
with tempfile.TemporaryDirectory() as d:
    audio = os.path.join(d, "Song.mp3")
    open(audio, "wb").write(b"ID3")
    assert nv.find_sidecar(audio) is None, "no sidecar yet"

    for ext in (".mkv", ".mp4"):                 # both present, mp4 must win
        open(os.path.join(d, "Song" + ext), "wb").write(b"x")
    got = nv.find_sidecar(audio)
    print("   ->", os.path.basename(got))
    assert got.endswith("Song.mp4"), f"preference order wrong: {got}"

    print("2) sidecar: a videos/ subfolder is searched too")
    nested = os.path.join(d, "Other.mp3")
    open(nested, "wb").write(b"ID3")
    os.makedirs(os.path.join(d, "videos"))
    open(os.path.join(d, "videos", "Other.mkv"), "wb").write(b"x")
    got = nv.find_sidecar(nested)
    print("   ->", os.path.relpath(got, d))
    assert got == os.path.join(d, "videos", "Other.mkv")

    print("3) sidecar: a track with no video returns None")
    lonely = os.path.join(d, "NoVideo.mp3")
    open(lonely, "wb").write(b"ID3")
    assert nv.find_sidecar(lonely) is None
    assert nv.find_sidecar("") is None
    assert nv.find_sidecar(None) is None
print("4) an extension with no sidecar is not a false positive")
with tempfile.TemporaryDirectory() as d:
    a = os.path.join(d, "X.mp3")
    open(a, "wb").write(b"ID3")
    open(os.path.join(d, "X.txt"), "wb").write(b"x")   # not a video ext
    assert nv.find_sidecar(a) is None
print("   ok")

# ---- slot lifecycle ---------------------------------------------------------
print("5) a disabled slot draws nothing and never opens a process")
slot = nv.VideoSlot()
SPAWNS.clear()
for _ in range(5):
    assert slot.draw(screen, BOX) is False
assert SPAWNS == [], f"a disabled slot must not spawn ffmpeg: {len(SPAWNS)}"
assert slot.is_active() is False
print("   ok, spawns:", len(SPAWNS))

print("6) request() defers the open to the draw path")
SPAWNS.clear()
slot.request(CLIP)
assert SPAWNS == [], "request must not spawn; the key handler must stay quick"
assert slot.enabled is True
drawn = slot.draw(screen, BOX)
print("   first draw ->", drawn, "spawns:", len(SPAWNS))
assert drawn is True
assert len(SPAWNS) == 1, f"expected exactly one spawn, got {len(SPAWNS)}"
assert slot.is_active() is True

print("7) a steady frame does not respawn the pipe")
SPAWNS.clear()
for _ in range(20):
    assert slot.draw(screen, BOX) is True
print("   spawns across 20 frames:", len(SPAWNS))
assert len(SPAWNS) == 0, f"steady state must not respawn, got {len(SPAWNS)}"
assert slot.frames_drawn >= 20

print("8a) resizing WITHIN the same cap tier does not rebuild the pipe")
# The pipe is capped at MAX_PIPE_WIDTH, so a 1280x800 box and an 800x600 box
# both decode to the same 640x360 frame. Rebuilding ffmpeg for that would be
# pure waste, so it must not happen.
slot.request(CLIP)
slot.draw(screen, BOX)
pipe_before = (slot.visual.width, slot.visual.height)
print("   pipe at 1280x800:", pipe_before)
assert pipe_before[0] <= nv.MAX_PIPE_WIDTH
SPAWNS.clear()
smaller = pygame.Rect(0, 0, 800, 600)
for _ in range(10):
    assert slot.draw(screen, smaller) is True
print("   spawns after resizing to 800x600:", len(SPAWNS))
assert len(SPAWNS) == 0, \
    f"same pipe size must not respawn ffmpeg, got {len(SPAWNS)}"
assert (slot.visual.width, slot.visual.height) == pipe_before

print("8b) resizing ACROSS the cap tier rebuilds exactly ONCE, then settles")
SPAWNS.clear()
tiny = pygame.Rect(0, 0, 480, 360)      # below the cap -> a genuinely new size
assert slot.draw(screen, tiny) is True
print("   pipe at 480x360:", (slot.visual.width, slot.visual.height))
assert (slot.visual.width, slot.visual.height) != pipe_before, \
    "a size below the cap should decode at its own size"
print("   spawns on first frame at the new size:", len(SPAWNS))
assert len(SPAWNS) == 1, f"a real resize must rebuild once, got {len(SPAWNS)}"
SPAWNS.clear()
for _ in range(15):
    assert slot.draw(screen, tiny) is True
print("   spawns across 15 more frames at the new size:", len(SPAWNS))
assert len(SPAWNS) == 0, "must settle after one rebuild"
# 16:9 source into a 480x360 box -> 480x270, letterboxed vertically
assert (slot.visual.width, slot.visual.height) == (480, 270), \
    f"expected a 16:9 pipe, got {slot.visual.width}x{slot.visual.height}"
slot.close()

# ---- failure behaviour ------------------------------------------------------
print("9) a bad source disables the slot and reports, never raises")
errors = []
slot2 = nv.VideoSlot(on_error=errors.append)
slot2.request("/definitely/not/here.mp4")
drawn = slot2.draw(screen, BOX)
print("   draw ->", drawn, "| message:", slot2.message, "| errors:", errors)
assert drawn is False
assert slot2.enabled is False
assert slot2.message, "a failure must leave a readable message"
assert errors and "no such file" in errors[0]

print("10) end of stream disables the slot instead of spinning")
short = os.path.join(SCRATCH, "ncs_video_short.mp4")
if not os.path.exists(short):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x90:rate=30:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", short],
        check=True, capture_output=True)
slot3 = nv.VideoSlot()
slot3.request(short)
t0 = time.time()
drew = 0
while time.time() - t0 < 12:
    if not slot3.draw(screen, BOX):
        break
    drew += 1
print("   frames:", drew, "| message:", slot3.message)
assert drew > 0, "should draw some frames first"
assert slot3.enabled is False
assert "end" in slot3.message or "stop" in slot3.message

print("11) toggle turns a live video off and reports it")
slot4 = nv.VideoSlot()
slot4.request(CLIP)
slot4.draw(screen, BOX)
assert slot4.is_active()
assert slot4.toggle() is False
assert slot4.is_active() is False
assert slot4.message == "video off"
assert slot4.toggle(CLIP) is True
assert slot4.enabled is True
slot4.clear()
print("   ok")

print("12) an explicit pin survives track changes; a sidecar does not fight it")
with tempfile.TemporaryDirectory() as d:
    # REAL video bytes, not 1-byte stubs: a stub ffmpeg cannot decode would
    # fail the slot for the wrong reason and prove nothing.
    a1 = os.path.join(d, "A.mp3"); open(a1, "wb").write(b"ID3")
    a2 = os.path.join(d, "B.mp3"); open(a2, "wb").write(b"ID3")
    for name in ("A", "B"):
        with open(CLIP, "rb") as _s, open(os.path.join(d, name + ".mp4"), "wb") as _d:
            _d.write(_s.read())

    slot5 = nv.VideoSlot()
    slot5.request(nv.find_sidecar(a1))
    slot5.draw(screen, BOX)
    print("   following A ->", os.path.basename(slot5.source))
    assert slot5.source.endswith("A.mp4")

    slot5.follow_track(a2)
    slot5.draw(screen, BOX)
    print("   following B ->", os.path.basename(slot5.source))
    assert slot5.source.endswith("B.mp4"), "a sidecar must follow the track"

    slot5.pin(CLIP)
    slot5.draw(screen, BOX)
    assert slot5.source == CLIP
    slot5.follow_track(a1)
    slot5.draw(screen, BOX)
    print("   after pin, following A ->", os.path.basename(slot5.source))
    assert slot5.source == CLIP, "a pinned source must not be overridden"
    slot5.clear()

print("12b) a slot that FAILED on one track recovers on the next good one")
with tempfile.TemporaryDirectory() as d:
    good = os.path.join(d, "Good.mp3"); open(good, "wb").write(b"ID3")
    with open(CLIP, "rb") as _s, open(os.path.join(d, "Good.mp4"), "wb") as _d:
        _d.write(_s.read())
    broken = os.path.join(d, "Broken.mp3"); open(broken, "wb").write(b"ID3")
    open(os.path.join(d, "Broken.mp4"), "wb").write(b"x")   # undecodable

    slot7 = nv.VideoSlot()
    slot7.request(nv.find_sidecar(broken))
    assert slot7.draw(screen, BOX) is False
    print("   broken sidecar ->", repr(slot7.message), "| enabled:", slot7.enabled)
    assert slot7.enabled is False
    assert slot7.follow_track(good) is True, \
        "an automatic failure must not permanently disable the slot"
    assert slot7.draw(screen, BOX) is True, "and the good track must recover"
    print("   recovered on", os.path.basename(slot7.source))
    slot7.clear()

print("12c) a user turning video off is respected across track changes")
with tempfile.TemporaryDirectory() as d:
    t = os.path.join(d, "T.mp3"); open(t, "wb").write(b"ID3")
    with open(CLIP, "rb") as _s, open(os.path.join(d, "T.mp4"), "wb") as _d:
        _d.write(_s.read())
    slot8 = nv.VideoSlot()
    assert slot8.toggle() is True          # arm it on
    assert slot8.enabled is True
    assert slot8.toggle() is False         # then explicitly off
    assert slot8._user_off is True
    assert slot8.follow_track(t) is False, "an explicit off must be respected"
    assert slot8.draw(screen, BOX) is False
    print("   ok, stayed off")

print("12d) arming the slot with no source adopts the next track's sidecar")
with tempfile.TemporaryDirectory() as d:
    t = os.path.join(d, "U.mp3"); open(t, "wb").write(b"ID3")
    with open(CLIP, "rb") as _s, open(os.path.join(d, "U.mp4"), "wb") as _d:
        _d.write(_s.read())
    slot9 = nv.VideoSlot()
    slot9.toggle()                        # arm, no source
    assert slot9.draw(screen, BOX) is False, "nothing to draw yet"
    assert slot9.enabled is True, "must still be armed"
    assert slot9.follow_track(t) is True
    assert slot9.draw(screen, BOX) is True, "the sidecar should now be live"
    print("   picked up", os.path.basename(slot9.source))
    slot9.clear()
print("13) a slot with no sidecar for the current track reports False")
slot6 = nv.VideoSlot()
with tempfile.TemporaryDirectory() as d:
    a = os.path.join(d, "C.mp3")
    open(a, "wb").write(b"ID3")
    slot6.request(nv.find_sidecar(a))     # None -> stays off
    assert slot6.follow_track(a) is False
    assert slot6.draw(screen, BOX) is False
print("   ok")

print("14) no ffmpeg process is left running after the slot is closed")
import subprocess as _sp
before = _sp.run(["pgrep", "-f", "ncs_video_probe"], capture_output=True,
                 text=True).stdout.strip()
slot.close()
slot2.clear()
slot3.clear()
slot4.clear()
slot5.clear()
slot6.clear()
time.sleep(0.6)
after = _sp.run(["pgrep", "-f", "ncs_video_probe"], capture_output=True,
                text=True).stdout.strip()
print("   pgrep before:", repr(before), "after:", repr(after))
assert (len(after.split()) if after else 0) <= (len(before.split()) if before else 0), \
    "closing the slot must reap its ffmpeg processes"

nv.VideoVisual._open = _real_open
print("VIDEO SLOT TESTS PASSED")
