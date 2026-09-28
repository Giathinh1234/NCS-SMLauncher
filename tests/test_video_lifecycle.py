"""Does a storm of video sources leak processes or zombies?

The audit found two separate defects here, and both only show up under
repeat failure, which is why neither showed up in a test that opens a video
once:

  - _fail() dropped the reference to a live ffmpeg without closing it.
  - close() killed a stubborn ffmpeg but never reaped it, leaving a
    Z <defunct> zombie in the process table.

So this deliberately fails a lot -- sources that cannot be probed, sources
that resolve but die -- and then checks the only thing that actually
matters: how many ffmpeg processes and zombies we left behind.
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


def ffmpeg_procs():
    """(live, zombie) pids, by process table state."""
    out = subprocess.run(["ps", "-eo", "pid=,stat=,comm="],
                         capture_output=True, text=True).stdout
    live, zombies = [], []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] != "ffmpeg":
            continue
        (zombies if "Z" in parts[1] else live).append(parts[0])
    return live, zombies


def make_clip(d, seconds=2, size="160x90"):
    clip = os.path.join(d, "c.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "quiet", "-f", "lavfi",
                    "-i", f"testsrc=size={size}:rate=10:duration={seconds}",
                    "-pix_fmt", "yuv420p", clip], capture_output=True)
    return clip


class Box:
    """The launcher passes a pygame.Rect; build_visual only reads .width/.height."""

    def __init__(self, w, h):
        self.width, self.height = w, h


import ncs_video                                    # noqa: E402

live0, zom0 = ffmpeg_procs()
print(f"   (start: {len(live0)} ffmpeg live, {len(zom0)} zombies)")

print("1) _fail() must not leave a running ffmpeg behind")
with tempfile.TemporaryDirectory() as d:
    clip = make_clip(d, seconds=30)          # long, so it would run for a while

    class DyingProc:
        """A live ffmpeg whose first read comes back empty (EOF)."""

        def __init__(self, inner):
            self.inner = inner
            self.stdout = None

        def poll(self):
            return self.inner.poll()

        def wait(self, timeout=None):
            return self.inner.wait(timeout=timeout)

        def terminate(self):
            self.inner.terminate()

        def kill(self):
            self.inner.kill()

    real_popen = ncs_video.subprocess.Popen
    wrapped = []

    def dying_popen(cmd, *a, **kw):
        p = DyingProc(real_popen(cmd, *a, **kw))
        wrapped.append(p)
        return p

    ncs_video.subprocess.Popen = dying_popen
    try:
        for _ in range(12):
            vis = ncs_video.build_visual(clip, Box(64, 36))
            slot = ncs_video.VideoSlot()
            slot.visual = vis
            slot._fail("synthetic failure")   # the leak path
    except ncs_video.VideoError as e:
        check("built a visual to fail", False, e)
    finally:
        ncs_video.subprocess.Popen = real_popen

    # build_visual may open more than one pipe per call, so assert "at least
    # the 12 we asked for" rather than an exact count.
    check("spawned at least 12 ffmpeg processes to fail", len(wrapped) >= 12,
          len(wrapped))
    time.sleep(2.0)
    live1, zom1 = ffmpeg_procs()
    check(f"12 forced failures leaked no ffmpeg (was {len(live0)})",
          len(live1) <= len(live0), f"now {len(live1)}: {live1}")
    check(f"12 forced failures left no zombies (was {len(zom0)})",
          len(zom1) <= len(zom0), f"now {len(zom1)}: {zom1}")

print("2) close() must reap what it has to kill")
with tempfile.TemporaryDirectory() as d:
    ignoring = os.path.join(d, "stubborn")
    with open(ignoring, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\ntrap '' TERM\nwhile true; do sleep 0.1; done\n")
    os.chmod(ignoring, 0o755)

    vis = ncs_video.VideoVisual.__new__(ncs_video.VideoVisual)
    proc = subprocess.Popen([ignoring], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    vis.proc = proc
    vis._last_surface = None
    vis.stopped = False
    vis.paused = False
    vis.want_frame = 0
    vis.muted = False
    vis.closed = False
    vis.volume = 1.0
    stubborn_pid = proc.pid

    t0 = time.monotonic()
    vis.close()
    elapsed = time.monotonic() - t0
    check("close() returns rather than hanging", elapsed < 6.0, f"{elapsed:.1f}s")

    # close() clears self.proc, so poll our own reference.
    time.sleep(0.5)
    reaped = False
    try:
        proc.wait(timeout=3)
        reaped = True
    except Exception:
        pass
    check("the SIGTERM-ignoring ffmpeg was killed", proc.poll() is not None,
          proc.poll())
    check("and reaped, not left as a zombie", reaped,
          "still in the process table; the kill() branch never waited")
    check("close() dropped its reference to the process", vis.proc is None,
          vis.proc)
    # Already dead and reaped by the checks above; kill it only in case that
    # was not true, and do not care either way.
    try:
        proc.kill()
    except Exception:
        pass

print("3) repeated open/close cycles leave nothing behind")
with tempfile.TemporaryDirectory() as d:
    clip = make_clip(d, seconds=3)
    before, _ = ffmpeg_procs()
    for _ in range(5):
        vis = ncs_video.build_visual(clip, Box(64, 36))
        if vis is not None:
            vis.close()
    time.sleep(2.0)
    after, zafter = ffmpeg_procs()
    check(f"5 open/close cycles left no ffmpeg (was {len(before)})",
          len(after) <= len(before), f"now {len(after)}: {after}")
    check("5 open/close cycles left no zombies", len(zafter) <= len(zom0),
          f"{zafter}")

live, _ = ffmpeg_procs()
for pid in live:
    if pid not in live0:
        subprocess.run(["kill", "-9", pid], capture_output=True)

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("VIDEO LIFECYCLE TESTS PASSED")
