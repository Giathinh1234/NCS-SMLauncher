"""Does the app now advance to the next track by itself?

Before the fix there was no end-of-track check anywhere in the program: the
audio callback parked the playhead on the final sample, the UI said
NOW PLAYING, and the only way forward was to press next yourself. So this
launches the real app against a library of 3-second tracks and watches the
track index change on its own, with no input at all.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = "/Users/giathinh/ncs-music-launcher"
PORT = 8791

tmp = tempfile.mkdtemp(prefix="hp-adv-")
lib = os.path.join(tmp, "lib")
cfg = os.path.join(tmp, "cfg")
os.makedirs(lib)
os.makedirs(cfg)
for name in ("one", "two", "three"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "quiet", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=3", "-ac", "1",
                    "-ar", "44100", os.path.join(lib, f"{name}.wav")],
                   capture_output=True)

# Only the VIDEO driver is faked. Faking the audio driver means PortAudio
# never calls _callback, so the playhead never moves, nothing ever ends and
# there is nothing to observe -- the first version of this test passed a
# silent app and called it a failure of the feature.
env = dict(os.environ,
           HASHPLAY_CONFIG_DIR=cfg, HASHPLAY_API_PORT=str(PORT),
           SDL_VIDEODRIVER="dummy")
env.pop("SDL_AUDIODRIVER", None)
proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "src", "ncs_launcher.py"), lib],
                        cwd=ROOT, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

fails = []
try:
    token = None
    for _ in range(40):
        time.sleep(0.5)
        p = os.path.join(cfg, "api_token")
        if os.path.exists(p):
            token = open(p, encoding="utf-8").read().strip()
            break
    if not token:
        print("   FAIL the app never wrote a token")
        sys.exit(1)

    def status():
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/status")
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode())
        # The envelope is {"ok": true, "result": {...}}; the state lives in
        # result. The first version of this test read top-level keys, got
        # None for everything, and reported a failure that was really just
        # its own bug.
        return body.get("result", body)

    time.sleep(2.0)
    seen = []
    print("   watching a 3-track, 3-second-each library with no input:")
    deadline = time.time() + 14
    while time.time() < deadline:
        try:
            s = status()
        except Exception:
            time.sleep(0.4)
            continue
        row = (s.get("index"), s.get("track"), round(s.get("position", 0.0), 1))
        if not seen or seen[-1][0] != row[0]:
            seen.append(row)
            print(f"     -> index {row[0]}  {row[1]}  (pos {row[2]}s)")
        time.sleep(0.4)

    indices = [r[0] for r in seen]
    print(f"   observed track indices: {indices}")
    if len(set(indices)) > 1:
        print("   ok  the track changed with no input -- it auto-advanced")
    else:
        print("   FAIL it never left the first track on its own")
        fails.append("auto-advance")

    s = status()
    if s.get("index") in indices and s.get("position", 0) < 3.2:
        print("   ok  position stays inside the track duration")
    else:
        print("   FAIL position ran past the end of the track",
              s.get("position"))
        fails.append("position overrun")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()
    log = proc.stdout.read() if proc.stdout else ""
    shutil.rmtree(tmp, ignore_errors=True)

if "Traceback" in log:
    print("   FAIL the app logged a traceback")
    print("   " + log[-900:].replace("\n", "\n   "))
    fails.append("traceback")

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("AUTO-ADVANCE TEST PASSED")
