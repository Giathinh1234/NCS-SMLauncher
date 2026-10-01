"""What is still costing memory in the lite build, and what could go next?

Runs entirely headless (SDL_VIDEODRIVER=dummy, no window, no audio device) and
reports what each remaining feature costs to have present. It does not open
the app -- SDL is on the dummy driver and no display is initialised -- so it is
safe to run while the user has the real app closed.

Each feature is measured in its own subprocess: RSS is process-wide, so
measuring them in one process would attribute every earlier import to every
later feature. Numbers are peak RSS after the module is actually used, not
merely imported, because an unused import can be cheaper than a used one and
only the latter tells you whether the feature is worth cutting.
"""
import os
import subprocess
import sys
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")

FEATURES = {
    "baseline (nothing)": "pass",
    "pygame + a surface": "pygame.init(); s=pygame.Surface((1280,748))",
    "numpy (spectrum math)": "import numpy; numpy.linspace(0,1,64)",
    "the NCS ball": "import ncs_sphere",
    "control API (http)": "import control_api",
    "video (ffmpeg sidecar)": "import ncs_video",
    "torrents (libtorrent)": "import libtorrent",
}

MB = 1024.0 * 1024.0


def measure(body):
    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    env["HASHPLAY_CONFIG_DIR"] = "/tmp/hp_fuel_probe"
    script = textwrap.dedent(f"""
        import sys, resource
        sys.path.insert(0, {SRC!r})
        {body}
        # Touch it so a lazy module actually loads and allocates.
        import gc; gc.collect()
        print("RSS", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    """)
    out = subprocess.run([sys.executable, "-c", script], env=env,
                         capture_output=True, text=True, timeout=300)
    for line in (out.stdout or "").splitlines():
        if line.startswith("RSS"):
            return int(line.split()[1]) / MB
    return None


print("LITE BUILD: what is still in it  (headless, no window, no sound)\n")

results = {}
for label, body in FEATURES.items():
    mb = measure(body)
    if mb is None:
        print(f"  {label:26} (not installed / not importable)")
        continue
    results[label] = mb
    base = results.get("baseline (nothing)")
    delta = f"  (+{mb - base:5.1f} MB)" if base is not None else ""
    print(f"  {label:26} {mb:6.1f} MB{delta}")

print()
if "torrents (libtorrent)" in results and "baseline (nothing)" in results:
    t = results["torrents (libtorrent)"] - results["baseline (nothing)"]
    v = results.get("video (ffmpeg sidecar)", 0) - results["baseline (nothing)"]
    a = results.get("control API (http)", 0) - results["baseline (nothing)"]
    b = results.get("the NCS ball", 0) - results["baseline (nothing)"]
    print("  cost of each thing still in the lite build:")
    print(f"    NCS ball      {b:5.1f} MB   <- already removed")
    print(f"    control API   {a:5.1f} MB   <- already removed")
    print(f"    video         {v:5.1f} MB   <- still in")
    print(f"    torrents      {t:5.1f} MB   <- still in")
