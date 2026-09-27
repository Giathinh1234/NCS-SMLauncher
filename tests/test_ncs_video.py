"""Video visual: real ffmpeg pipes, real .strm resolution, real surfaces.

Every check here runs against an actual ffmpeg process on a real generated
file. Nothing is mocked, because the whole point of this module is that the
pipe hands back correctly-sized raw frames.
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
pygame.display.set_mode((1280, 800))

import ncs_video as nv

SCRATCH = os.path.join(os.path.expanduser("~"), ".hermes", "cache", "scratch")
os.makedirs(SCRATCH, exist_ok=True)
MP4 = os.path.join(SCRATCH, "ncs_video_probe.mp4")


def build_clip(path, seconds=3, w=320, h=180):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", path],
        check=True, capture_output=True)
    return path


print("1) ffmpeg available:", nv.have_ffmpeg())
assert nv.have_ffmpeg(), "ffmpeg must be on PATH for this module to work"

if not os.path.exists(MP4):
    build_clip(MP4)
print("   fixture:", os.path.basename(MP4), os.path.getsize(MP4), "bytes")

# ---- extension recognition -------------------------------------------------
print("2) extension detection")
for ext in (".mp4", ".MP4", ".mkv", ".webm", ".strm", ".avi"):
    assert nv.is_video_file("x" + ext), f"{ext} should be recognised"
for ext in (".mp3", ".flac", ".txt", ".jpg", ""):
    assert not nv.is_video_file("x" + ext), f"{ext} must not be video"
assert not nv.is_video_file(None)
print("   ok")

# ---- ffprobe ----------------------------------------------------------------
print("3) probe a real file")
info = nv.probe(MP4)
print("   ", info)
assert info is not None, "ffprobe must describe a real mp4"
src_w, src_h, duration, has_audio = info
assert src_w == 320 and src_h == 180, f"got {src_w}x{src_h}"
assert duration > 1.5, f"duration {duration}"
assert has_audio is True, "the fixture does carry an audio track"
print("4) probe a bogus source returns None, not an exception")
assert nv.probe("/definitely/not/here.mp4") is None
print("   ok")

# ---- letterbox geometry -----------------------------------------------------
print("5) fit_rect keeps aspect ratio")
x, y, w, h = nv.fit_rect(1920, 1080, 800, 600)
assert (w, h) == (800, 450), f"got {w}x{h}"
assert (x, y) == (0, 75), f"centred wrong: {x},{y}"
x, y, w, h = nv.fit_rect(1080, 1920, 800, 600)
assert (w, h) == (337, 600), f"portrait got {w}x{h}"
assert (x, y) == (231, 0), f"portrait not centred: {x},{y}"
_x2, _y2, w2, h2 = nv.fit_rect(640, 480, 800, 600)
assert (w2, h2) == (800, 600), f"must fill the box, got {w2}x{h2}"
_x3, _y3, w3, h3 = nv.fit_rect(640, 480, 800, 600, allow_upscale=False)
assert (w3, h3) == (640, 480), f"no-upscale mode must fit inside, got {w3}x{h3}"
# an absurd upscale is capped rather than followed to 4K
_x4, _y4, w4, h4 = nv.fit_rect(32, 32, 4000, 4000)
assert (w4, h4) == (128, 128), f"upscale cap not applied: {w4}x{h4}"
print("   ok")

# ---- .strm resolution -------------------------------------------------------
print("6) .strm holds a single source line")
with tempfile.TemporaryDirectory() as d:
    strm = os.path.join(d, "clip.strm")
    with open(strm, "w") as fh:
        fh.write("\n\n   " + MP4 + "  \n")
    assert nv.read_strm(strm).strip() == MP4
    resolved, note = nv.resolve_source(strm)
    print("   resolved:", os.path.basename(resolved))
    assert os.path.samefile(resolved, MP4), "strm must resolve to the real file"
    assert note == ""

    empty = os.path.join(d, "empty.strm")
    open(empty, "w").write("   \n\n")
    try:
        nv.read_strm(empty)
        raise AssertionError("empty .strm must raise")
    except nv.VideoError as e:
        print("   empty .strm ->", e)
print("7) missing file raises VideoError with a readable message")
try:
    nv.resolve_source(os.path.join(SCRATCH, "nope.mp4"))
    raise AssertionError("missing file must raise")
except nv.VideoError as e:
    print("   ", e)
    assert "no such file" in str(e)
print("8) a bare URL with web disabled is refused, not fetched")
try:
    nv.resolve_source("https://example.com/clip.mp4", allow_web=False)
    raise AssertionError("web must be refused when disabled")
except nv.VideoError as e:
    print("   ", e)

# ---- the actual pipe --------------------------------------------------------
print("9) build_visual opens a real pipe and returns sized frames")
box = pygame.Rect(0, 0, 1280, 800)
with nv.build_visual(MP4, box) as vis:
    print("   source size:", vis.width, "x", vis.height,
          "frame_bytes:", vis.frame_bytes, "duration:", vis.duration)
    assert vis.is_alive(), "ffmpeg must be running"
    got = 0
    first = None
    for _ in range(12):
        result = vis.next_surface(box)
        assert result is not None, f"frame {got} came back None early"
        surf, rect = result
        if first is None:
            first = (surf, rect)
        assert isinstance(surf, pygame.Surface)
        assert surf.get_width() == vis.width
        assert surf.get_height() == vis.height
        assert rect.width == vis.width and rect.height == vis.height
        got += 1
    print("   frames read:", got, "surface:", first[0].get_size(),
          "placed at:", tuple(first[1].topleft))
    assert got == 12
    assert vis.frame_index == 12
    # the test pattern is not black, so the surface must carry real pixels
    arr = pygame.surfarray.array3d(first[0])
    assert float(arr.mean()) > 8.0, f"surface looks blank (mean {arr.mean()})"
    print("   surface mean brightness: %.1f" % arr.mean())
    assert not vis.eof

print("10) a short clip reaches EOF and then reports eof, never a crash")
short = os.path.join(SCRATCH, "ncs_video_short.mp4")
if not os.path.exists(short):
    build_clip(short, seconds=1)
with nv.build_visual(short, box) as vis:
    frames = 0
    t0 = time.time()
    while time.time() - t0 < 12:
        if vis.next_surface(box) is None:
            break
        frames += 1
    print("   frames before EOF:", frames, "eof:", vis.eof)
    assert vis.eof, "a 1s clip must reach EOF"
    assert vis.next_surface(box) is None, "after EOF must stay None"
print("11) close() is idempotent and safe on a dead pipe")
vis = nv.build_visual(MP4, box)
vis.close()
vis.close()
assert not vis.is_alive()
assert vis.next_surface(box) is None
print("12) seek restarts the pipe at a new offset")
with nv.build_visual(MP4, box) as vis:
    vis.next_surface(box)
    vis.seek(0.5)          # the clip is 2.0s, so 0.5 leaves a frame to read
    assert vis.frame_index == 0, "seek must reset the frame counter"
    assert vis.is_alive(), "seek must leave a live pipe"
    assert vis.next_surface(box) is not None
    print("   ok, frame after seek:", vis.frame_index)
print("12b) seeking past the end reports eof rather than hanging")
with nv.build_visual(MP4, box) as vis:
    vis.seek(1.99)
    saw = None
    for _ in range(400):
        saw = vis.next_surface(box)
        if saw is None:
            break
    print("   eof after seeking to the very end:", vis.eof)
    assert vis.eof, "a seek to the end must terminate"

# ---- a real .strm driving the pipe -----------------------------------------
print("13) a .strm file drives the pipe end to end")
with tempfile.TemporaryDirectory() as d:
    strm = os.path.join(d, "movie.strm")
    open(strm, "w").write(MP4 + "\n")
    with nv.build_visual(strm, box) as vis:
        assert vis.next_surface(box) is not None
        print("   ok, frames from .strm:", vis.frame_index)
    print("14) sync_to seeks the pipe to the playhead")
with nv.build_visual(MP4, box, sync_to=1.0) as vis:
    assert vis.is_alive()
    assert vis.next_surface(box) is not None
    print("   ok")

print("VIDEO TESTS PASSED")
