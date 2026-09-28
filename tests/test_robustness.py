"""Does the app survive hostile input?

Startup does a lot before anything is drawn: it loads a settings file it does
not control, resolves a library folder that may not exist, and writes a token.
Any of those can be wrong on a real machine -- a half-written settings.json
after a crash, a folder on an unmounted drive, a home directory the app cannot
write to. The failure that matters is a silent exit, because the user sees
nothing at all.

Each case launches the REAL launcher as a subprocess and asserts it is still
serving its API a few seconds later.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = "/Users/giathinh/ncs-music-launcher"
PORT = 8905
fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}  {detail}")
        fails.append(label)


def run_case(name, settings, folder_maker, env_extra=None, read_only=False):
    """Launch the app in a hostile environment; return (log, alive)."""
    tmp = tempfile.mkdtemp(prefix="hashplay-rob-")
    cfg = os.path.join(tmp, "cfg")
    os.makedirs(cfg, exist_ok=True)
    if settings is not None:
        with open(os.path.join(cfg, "settings.json"), "w",
                  encoding="utf-8") as fh:
            fh.write(settings)
    lib = folder_maker(tmp)
    if read_only:
        os.chmod(cfg, 0o500)          # readable, not writable

    env = dict(os.environ)
    env.update({
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
        "HASHPLAY_CONFIG_DIR": cfg,
        "HASHPLAY_API_PORT": str(PORT),
        "PYTHONPATH": os.path.join(ROOT, "src"),
    })
    if env_extra:
        env.update(env_extra)

    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "src", "ncs_launcher.py"),
         *([lib] if lib else [])],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    time.sleep(7)

    alive = proc.poll() is None
    served = False
    token = os.path.join(cfg, "api_token")
    if alive and os.path.exists(token):
        tok = open(token, encoding="utf-8").read().strip()
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/status")
        req.add_header("Authorization", f"Bearer {tok}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                served = r.status == 200
        except Exception:
            served = False

    # Read the log unconditionally. It used to be captured only when the app
    # had already exited, which meant a running-but-degraded app -- exactly
    # the case where the log matters -- reported nothing at all.
    log = ""
    try:
        if alive:
            proc.terminate()
        proc.wait(timeout=8)
        if proc.stdout:
            log = proc.stdout.read()[-1500:]
    except Exception:
        pass
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        os.chmod(cfg, 0o700)
        shutil.rmtree(tmp, ignore_errors=True)
    return alive, served, log


def with_audio(tmp):
    d = os.path.join(tmp, "music")
    os.makedirs(d, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "quiet", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=1", "-ac", "1",
                    "-ar", "44100", os.path.join(d, "A Track.wav")],
                   capture_output=True)
    return d


def empty_dir(tmp):
    d = os.path.join(tmp, "empty")
    os.makedirs(d, exist_ok=True)
    return d


def missing_dir(tmp):
    return os.path.join(tmp, "does", "not", "exist")


print("ROBUSTNESS: hostile startup")

print("1) a corrupt settings.json must not brick the app")
for label, blob in (
        ("truncated mid-write", '{"library_folder": "/Music", "keymap": {'),
        ("not json at all", "this is not json"),
        ("a bare list", '["a", "b"]'),
        ("a bare number", "42"),
        ("keymap of the wrong type", '{"keymap": "not a dict"}'),
        ("keymap values of the wrong type", '{"keymap": {"quit": 5}}'),
        ("a huge schema_version", '{"schema_version": 999999}'),
        ("a negative schema_version", '{"schema_version": -3}'),
        ("empty file", ""),
):
    alive, served, log = run_case(label, blob, with_audio)
    check(f"survives: {label}", alive and served, log[-400:])
    time.sleep(0.4)

print("2) a library folder that is not there")
alive, served, log = run_case("missing folder", None, missing_dir)
check("survives a folder that does not exist", alive and served, log[-400:])
time.sleep(0.4)

print("3) an empty library folder")
alive, served, log = run_case("empty folder", None, empty_dir)
check("survives an empty library", alive and served, log[-400:])
time.sleep(0.4)

print("4) a read-only config directory (cannot write the token)")
alive, served, log = run_case("read-only cfg", None, with_audio, read_only=True)
# It must not crash. It may legitimately fail to start the API if it cannot
# write a token, which is a correct, visible failure rather than a silent one.
check("does not crash when settings cannot be written", alive, log[-400:])
time.sleep(0.4)

print("5) the API port is already taken")
# Occupy the port, then launch: the app must still render and must say so.
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", PORT))
sock.listen(1)
try:
    alive, served, log = run_case("port taken", None, with_audio)
    # served will be False (that port is the *other* socket, not our app).
    # What matters is that the app is alive and told the user why.
    check("survives a port collision", alive, log[-400:])
    check("and reports the bind failure rather than hiding it",
          "control API" in log or "could not bind" in log, log[-400:])
finally:
    sock.close()
time.sleep(0.4)

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("ROBUSTNESS TESTS PASSED")
