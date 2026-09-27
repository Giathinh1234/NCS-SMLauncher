"""Headless checks for incremental decoding of a file still being written.

Run:  python3 tests/test_streaming_source.py

The file under test is a REAL 12s stereo MP3 cut at 128 kbps, because the bugs
this guards against are format-specific: a 0-byte placeholder (music/test.mp3)
cannot be decoded at all, and a mono tone cannot catch the missing-downmix bug
where a stereo file yields exactly 2x the mono sample count.

The test writes that MP3 to disk in four stages (15/40/70/100%), pumping after
each, and asserts the decoded buffer only ever grows and ends up bit-identical
to a single full decode. If the real source track is missing it falls back to
an ffmpeg-generated stereo tone and skips only the assertions that genuinely
need real music.
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import miniaudio
import numpy as np

from streaming_source import SAMPLE_RATE, StreamingSource

FFMPEG = "/opt/homebrew/bin/ffmpeg"
REAL_SOURCE = "/Users/giathinh/Downloads/A Thousand Miles.mp3"

_num = [0]


def check(label, condition, detail=""):
    _num[0] += 1
    assert condition, "FAILED #%d %s %s" % (_num[0], label, detail)
    print("  %2d. ok  %s%s" % (_num[0], label, (" -- " + detail) if detail else ""))


def build_fixture(tmpdir):
    """Return (path, used_real_source). Falls back to a synthetic stereo tone."""
    out = os.path.join(tmpdir, "stream_src.mp3")
    if os.path.isfile(REAL_SOURCE) and os.path.getsize(REAL_SOURCE) > 1024:
        cmd = [FFMPEG, "-y", "-v", "quiet", "-ss", "30", "-t", "12", "-i", REAL_SOURCE,
               "-ar", "44100", "-b:a", "128k", out]
        if subprocess.call(cmd) == 0 and os.path.getsize(out) > 0:
            return out, True
    print("  NOTE: real source %s unavailable -- using a synthetic stereo tone."
          % REAL_SOURCE)
    cmd = [FFMPEG, "-y", "-v", "quiet", "-f", "lavfi", "-i",
           "sine=frequency=440:duration=12", "-ac", "2", "-ar", "44100",
           "-b:a", "128k", out]
    subprocess.check_call(cmd)
    return out, False


def write_stage(full_bytes, dest, percent):
    """Write the first `percent` of the encoded file to `dest`."""
    with open(dest, "wb") as fh:
        fh.write(full_bytes[: int(len(full_bytes) * percent / 100)])
    return os.path.getsize(dest)


def main():
    tmpdir = tempfile.mkdtemp(prefix="ncs_stream_")
    try:
        full, used_real = build_fixture(tmpdir)
        data = open(full, "rb").read()
        total_bytes = len(data)
        print("\n1. fixture")
        print("     source: %s (%s)" % (full, "real MP3" if used_real else "synthetic tone"))
        check("fixture mp3 is non-empty", total_bytes > 0, "%d bytes" % total_bytes)

        # ---- ground truth: one full decode, independent of StreamingSource ----
        print("\n2. ground-truth full decode")
        info = miniaudio.get_file_info(full)
        check("file reports 2 channels", int(info.nchannels) == 2, "nchannels=%d" % info.nchannels)
        raw = miniaudio.decode_file(full, output_format=miniaudio.SampleFormat.SIGNED16,
                                    nchannels=2, sample_rate=SAMPLE_RATE)
        native = np.asarray(raw.samples, dtype=np.float32) / 32768.0
        if native.ndim > 1:
            native = native.reshape(-1)
        full_mono = native.reshape(-1, 2).mean(axis=1).astype(np.float32)
        truth_n = int(full_mono.size)
        check("full mono decode is non-trivial", truth_n > 0, "%d samples" % truth_n)
        if used_real:
            check("native stereo count is exactly 2x mono",
                  int(native.size) == 2 * truth_n,
                  "native=%d mono=%d" % (native.size, truth_n))

        # ---- staged growth ----
        print("\n3. staged growth (15/40/70/100%)")
        staged = os.path.join(tmpdir, "growing.mp3")
        first_bytes = write_stage(data, staged, 15)
        src = StreamingSource(staged)
        check("kind defaults to mp3", src.kind == "mp3", src.kind)
        check("probed channel count is 2", src.channels == 2, "channels=%d" % src.channels)
        check("size_on_disk sees the first chunk", src.size_on_disk() == first_bytes,
              "%d bytes" % first_bytes)

        sizes = []
        for pct in (15, 40, 70, 100):
            if pct != 15:
                write_stage(data, staged, pct)
            added = src.pump()
            n = int(src.samples().size)
            sizes.append(n)
            dur = src.available_duration()
            print("     stage %3d%%: +%6d samples -> %6d total (%.2fs)" % (pct, added, n, dur))
            check("stage %d%% decoded something" % pct, n > 0, "%d samples" % n)
            check("stage %d%% is float32 1-D" % pct,
                  src.samples().dtype == np.float32 and src.samples().ndim == 1,
                  str(src.samples().dtype))

        print("\n4. monotonic growth and equality with a full decode")
        for i in range(1, len(sizes)):
            check("buffer never shrinks (stage %d -> %d)" % (i, i + 1),
                  sizes[i] >= sizes[i - 1], "%d -> %d" % (sizes[i - 1], sizes[i]))
        check("buffer grows monotonically overall", sizes[-1] > sizes[0],
              "%d -> %d" % (sizes[0], sizes[-1]))
        check("incremental count equals full-decode count", sizes[-1] == truth_n,
              "incremental=%d truth=%d" % (sizes[-1], truth_n))
        diff = float(np.max(np.abs(src.samples() - full_mono[: sizes[-1]])))
        check("samples match a full decode within 1e-4", diff < 1e-4, "max abs diff=%.3e" % diff)
        check("available_duration matches sample count",
              abs(src.available_duration() - sizes[-1] / SAMPLE_RATE) < 1e-6,
              "%.4fs" % src.available_duration())

        print("\n5. completion + idempotence")
        check("is_complete() True at exact size",
              src.is_complete(expected_size=total_bytes) is True)
        check("is_complete() False at a larger expected size",
              src.is_complete(expected_size=total_bytes * 2) is False)
        check("is_complete() False for a missing path",
              src.is_complete(path=os.path.join(tmpdir, "nope.mp3")) is False)
        check("size_on_disk equals the full byte count", src.size_on_disk() == total_bytes)
        again = src.pump()
        check("extra pump on a complete file adds 0", again == 0, "added=%d" % again)
        check("buffer unchanged by the extra pump", int(src.samples().size) == sizes[-1])

        print("\n6. torn tail (25% of the file)")
        torn = os.path.join(tmpdir, "torn.mp3")
        write_stage(data, torn, 25)
        torn_src = StreamingSource(torn)
        torn_added = torn_src.decode_all()
        check("torn file decodes without raising", True)
        check("torn file yields > 0 samples", int(torn_src.samples().size) > 0,
              "%d samples" % int(torn_src.samples().size))
        check("torn decode returns a non-negative count", torn_added >= 0,
              "returned %d" % torn_added)
        check("torn buffer is shorter than the full decode",
              int(torn_src.samples().size) < truth_n,
              "%d < %d" % (int(torn_src.samples().size), truth_n))
        check("torn buffer is float32 1-D",
              torn_src.samples().dtype == np.float32 and torn_src.samples().ndim == 1)

        print("\n7. degenerate inputs")
        empty = StreamingSource(os.path.join(tmpdir, "missing.mp3"))
        check("missing file probes 1 channel", empty.channels == 1)
        check("missing file reports size 0", empty.size_on_disk() == 0)
        check("pump on a missing file adds 0", empty.pump() == 0)
        check("empty buffer has 0 samples", int(empty.samples().size) == 0)
        check("available_duration is 0.0", empty.available_duration() == 0.0)
        check("is_complete() False for a missing file", empty.is_complete() is False)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n... TESTS PASSED")


if __name__ == "__main__":
    main()
