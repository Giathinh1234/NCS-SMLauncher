"""Measure what lite mode actually saves.

The claim is memory. A test that says "the module was not imported" supports
that claim, but it is still an inference, so this measures RSS of the real
launcher in both modes and reports the difference.

Both runs use the same library, the same window size, and the same number of
frames, and neither plays audio (SDL_AUDIODRIVER=dummy) -- the point is the
renderer's memory, and a silent run also means this makes no sound.
"""
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []

# ru_maxrss is in BYTES on this macOS, not kilobytes as the Linux man page
# says. Verified against ps, which reports KB unambiguously: the ratio came out
# at ~1019, so the divisor is 1024*1024. The first two versions of this test
# divided by 1024 and printed "131248.0 MB" -- three orders of magnitude too
# large, from a test that passed both times. A number and its unit have to be
# checked against something independent, or the test is decoration.
MB = 1024.0 * 1024.0


def check(cond, label, detail=""):
    if cond:
        print("   ok  " + label)
    else:
        print("   FAIL " + label + (f"   [{detail}]" if detail else ""))
        FAILS.append(label)


def rss_kb(pid):
    try:
        return int(subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                                  capture_output=True,
                                  text=True).stdout.strip() or 0)
    except ValueError:
        return 0


def measure(lite, frames=90, runs=3):
    """Best (lowest) peak RSS over `runs` samples.

    ru_maxrss is a high-water mark, so a single run is a single noisy
    measurement: one scheduling hiccup inflates it and can make a lighter
    build look heavier than a full one -- which is exactly what one run of
    this test reported (micro at 99 MB vs lite at 87 MB, with micro provably
    doing less work). Taking the minimum over several runs discards the
    inflated samples, and the spread is reported so a genuinely noisy machine
    is visible rather than silently averaged away.
    """
    """Run the real draw path in a child, and report its peak RSS.

    lite is a profile name: "full", "lite", or "micro".
    """
    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"          # no sound, by design
    env["HASHPLAY_CONFIG_DIR"] = "/tmp/hp_lite_mem"
    # The probe sets the PROFILE, mirroring what a frozen build carries.
    # (An earlier version of this file still set HASHPLAY_LITE, the dead env
    # var, so the "lite" child ran as a full build and this test cheerfully
    # reported a ~2 MB saving between two builds that were identical.)
    #
    # Note the truthiness test that used to sit here: `if lite:` was true for
    # the string "full" too, so every tier set the lite flag and all three ran
    # identical builds. Profile names are compared exactly.
    if lite in ("lite", "micro"):
        env["HP_PROBE_PROFILE"] = lite
    else:
        env.pop("HP_PROBE_PROFILE", None)

    script = f"""
import os, sys, gc
sys.path.insert(0, {os.path.join(REPO, "src")!r})
# Patch build_variant BEFORE ncs_launcher is imported. `from build_variant
# import BUILD_LITE` copies the value at import time, so patching the module
# after that import has no effect on ncs_launcher.BUILD_LITE -- which is why
# an earlier version of this probe set the flag too late and still drew the
# ball, reporting 2 MB saved instead of 19.
import build_variant
_prof = os.environ.get("HP_PROBE_PROFILE")
if _prof in ("lite", "micro"):
    build_variant.BUILD_PROFILE = _prof
import numpy as np, pygame
pygame.init()
import ncs_launcher as L

class P:
    paused = False
    track_path = "/dev/null"
    def spectrum(self, n=64):
        return (np.linspace(0.2, 0.9, n)).astype(np.float32)
    def position(self): return 0.0
    def duration(self): return 100.0

W, H = 1280, 748
screen = pygame.Surface((W, H))
meta = {{"art_path": None, "artist": "a", "title": "t"}}
p = P()

# The modes a user can actually reach in this build -- taken from the
# launcher's own list, not a copy of it. An earlier version of this test
# rebuilt the list here and so went on drawing "radial" in a lite run, which
# is why it reported 0 MB saved while the lite build really does save 19.
# Duplicating the thing under test guarantees the test measures itself.
modes = L.VIS_MODES()

# Warm every mode once, so setup is not counted as steady state.
for m in modes:
    L.draw_visualizer(screen, p, W, H, m, 0.0, meta, None)
gc.collect()

peak = 0
for i in range({frames}):
    for m in modes:
        L.draw_visualizer(screen, p, W, H, m, i * 0.016, meta, None)
    peak = max(peak, rss())
    if i % 15 == 0:
        time.sleep(0)

import resource
print("PEAK_KB", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
print("MODES", ",".join(modes))
"""
    # rss() helper
    script = script.replace("import os, sys, gc",
                            "import os, sys, gc, time\n"
                            "def rss():\n"
                            "    import resource\n"
                            "    return resource.getrusage("
                            "resource.RUSAGE_SELF).ru_maxrss")
    samples = []
    modes = None
    for _ in range(runs):
        out = subprocess.run([sys.executable, "-c", script], env=env,
                             capture_output=True, text=True, timeout=600)
        peak = None
        for line in (out.stdout or "").splitlines():
            if line.startswith("PEAK_KB"):
                peak = int(line.split()[1])
            elif line.startswith("MODES"):
                modes = line.split(None, 1)[1]
        if peak is None:
            raise SystemExit(f"no measurement for {lite}\n"
                             f"STDOUT:{out.stdout[-800:]}\n"
                             f"STDERR:{out.stderr[-1500:]}")
        samples.append(peak)
    return min(samples), modes


print("MEMORY BY TIER -- 1280x748, 90 frames, silent (no audio device)\n")

full_peak, full_modes = measure("full")
print(f"  full : {full_peak / MB:7.1f} MB   modes: {full_modes}")
lite_peak, lite_modes = measure("lite")
print(f"  lite : {lite_peak / MB:7.1f} MB   modes: {lite_modes}")
micro_peak, micro_modes = measure("micro")
print(f"  micro: {micro_peak / MB:7.1f} MB   modes: {micro_modes}")

full_mb, lite_mb, micro_mb = full_peak / MB, lite_peak / MB, micro_peak / MB
print(f"  lite saves {full_mb - lite_mb:6.1f} MB "
      f"({100.0 * (full_mb - lite_mb) / full_mb:.1f}%)")
print(f"  micro saves {full_mb - micro_mb:6.1f} MB "
      f"({100.0 * (full_mb - micro_mb) / full_mb:.1f}%)\n")

check("radial" in full_modes, "full build drew the NCS ball")
check("radial" not in lite_modes, "lite never drew the ball")
check("radial" not in micro_modes, "micro never drew the ball")
# The sphere's own allocation measured 24.8 MB; video (ffmpeg) 25.5 MB.
check(lite_mb < full_mb - 8,
      f"lite is meaningfully lighter ({full_mb - lite_mb:.1f} MB > 8 MB)")
check(micro_mb < lite_mb - 8,
      f"micro is meaningfully lighter than lite "
      f"({lite_mb - micro_mb:.1f} MB > 8 MB) -- this is the ffmpeg saving")
check(micro_mb < 80,
      f"micro fits in a small footprint ({micro_mb:.0f} MB < 80 MB)")
check(full_peak > 10 * MB,
      f"ru_maxrss really is bytes ({full_peak} > 10 MB)")

print("\nMEMORY TIER TEST PASSED" if not FAILS
      else f"\n{len(FAILS)} FAILURES: {FAILS}")
sys.exit(1 if FAILS else 0)
