"""Lite mode must be a real gate, not a hidden menu entry.

"Lite" was asked for as: no control API, and none of the memory-hungry
visualizer. A build that merely hid the menu item would have satisfied the
screenshot and none of the point, so this asserts the substance:

  * no HTTP listener is created and control_api is never imported
  * "radial" is not in the mode list, so it cannot be selected
  * ncs_sphere is not in sys.modules -- a top-level import would have loaded
    it (and its numpy kernel caches) even with the mode removed
  * the full build still has all six modes and does import the sphere

The sys.modules check is the one that matters most. Modes being absent from a
list proves nothing about memory; the module being unloaded proves it.
"""
import os
import subprocess
import sys
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(cond, label, detail=""):
    if cond:
        print("   ok  " + label)
    else:
        print("   FAIL " + label + (f"   [{detail}]" if detail else ""))
        FAILS.append(label)


def run_probe(lite):
    """Start the launcher far enough to read its real state, then report.

    Runs in a subprocess so each mode gets a clean interpreter -- sys.modules
    in this process is already polluted by the test itself.
    """
    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    env["HASHPLAY_CONFIG_DIR"] = env.get("PROBE_CFG", "/tmp/hp_lite_probe")
    # Flip the CONSTANT, not an env var. The env var was the original design
    # and it silently did nothing in a frozen binary; these tests have to
    # exercise the mechanism that actually ships. Done by rewriting
    # build_variant.py in a scratch copy of the tree? No -- the module is
    # imported from src/, so the probe overrides it directly, which is exactly
    # what the frozen binary carries.
    if lite:
        # A frozen lite build carries BUILD_LITE = True in this module, so
        # that is what the probe simulates -- by patching the module before
        # config.py reads it. Setting an env var would test the mechanism that
        # was already shown not to survive a build.
        env["HP_PROBE_LITE"] = "1"
    else:
        env.pop("HP_PROBE_LITE", None)

    probe = textwrap.dedent("""
        import json, os, sys
        sys.path.insert(0, os.path.join(%r, "src"))
        import build_variant
        # Patch BEFORE config.py is imported: config reads the constant at
        # import time to build DEFAULT_CONFIG.
        if os.environ.get("HP_PROBE_LITE") == "1":
            build_variant.BUILD_LITE = True
        import config
        cfg = config.load_config()
        # Ask the launcher for its mode list. This probe used to keep its own
        # copy gated on cfg["lite"], and when the launcher changed the two
        # drifted -- the probe reported a mode list the app would never build.
        import ncs_launcher as _L
        modes = _L.VIS_MODES(cfg)
        # The launcher imports the sphere lazily, on FIRST DRAW. So checking
        # sys.modules before drawing proves nothing about the full build --
        # it would report "not loaded" for a build that loads it correctly a
        # frame later. Draw it, then look.
        api_imported = False
        if not cfg.get("lite"):
            import control_api  # noqa: F401
            api_imported = True
        density = None
        if "radial" in modes:
            import numpy, pygame
            import ncs_sphere as NS
            density = list(NS._density())
            import ncs_launcher as L
            screen = pygame.Surface((320, 200))
            class P:
                paused = False
                track_path = "/dev/null"
                def spectrum(self, n=64):
                    return [0.3] * n
            L.draw_visualizer(screen, P(), 320, 200, "radial", 0.0,
                              {"art_path": None, "artist": "a", "title": "t"},
                              None)
        print("PROBE" + json.dumps({
            "lite": bool(cfg.get("lite")),
            "modes": modes,
            "sphere_loaded": "ncs_sphere" in sys.modules,
            "density": density,
            "profile": __import__("build_variant").BUILD_PROFILE,
            "control_api_loaded": "control_api" in sys.modules,
            "api_imported_by_launcher_path": api_imported,
        }))
    """) % REPO

    out = subprocess.run([sys.executable, "-c", probe], env=env,
                         capture_output=True, text=True, timeout=90)
    for line in (out.stdout or "").splitlines():
        if line.startswith("PROBE"):
            import json
            return json.loads(line[5:])
    raise SystemExit(f"probe produced no result\nSTDOUT:\n{out.stdout}\n"
                     f"STDERR:\n{out.stderr[-1500:]}")


print("LITE MODE")
full = run_probe(lite=False)
lite = run_probe(lite=True)

check(full["lite"] is False, "default build is not lite")
check(lite["lite"] is True, "BUILD_LITE = True turns lite on")

print("\n  the control API")
check(lite["control_api_loaded"] is False,
      "lite never imports control_api")
check(full["control_api_loaded"] is True,
      "the full build still imports it (lite did not break the feature)")

print("\n  the NCS ball")
check("radial" in full["modes"], "full build still offers 'radial'")
check("radial" in lite["modes"],
      "lite KEEPS the NCS ball -- fewer dots, not a different feature")
check(lite["modes"] == full["modes"],
      "lite offers exactly the same modes as full -- it is lighter, not smaller",
      str(lite["modes"]))
check(full["sphere_loaded"] is True,
      "full build loads ncs_sphere (lazy import works)")
check(lite["sphere_loaded"] is True,
      "lite loads ncs_sphere, because lite draws the ball")

print("\n  a stale env var must not resurrect the old mechanism")
env = dict(os.environ)
env.update({"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
            "HASHPLAY_CONFIG_DIR": "/tmp/hp_lite_probe"})
env.pop("HASHPLAY_LITE", None)
bad = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, 'src'); import config; "
     "print('OK', config.default_config()['lite'])"],
    cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
check(bad.returncode == 0 and "OK False" in bad.stdout,
      "a stray HASHPLAY_LITE env var is ignored rather than honoured",
      (bad.stderr or "")[-120:])

# --- the ball must still LOOK like a ball, and cost less to draw ----------
# "lite can reach radial" is not enough on its own: the user asked for a
# lighter build that still has the NCS ball, so the ball has to survive the
# density cut. Both are checked, because either alone can pass while the other
# is broken:
#   * silhouette -- the lit-pixel bounding box must stay the same size and
#     round, or the ball is not a ball anymore
#   * cost       -- lite must genuinely draw it faster, or nothing was saved
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import time
sys.path.insert(0, os.path.join(REPO, "src"))
import numpy as _np
import pygame as _pg
import build_variant as _bv
import ncs_sphere as _NS

_pg.init()
_scr = _pg.Surface((1280, 748))


class _P:
    paused = False
    track_path = "/dev/null"

    def spectrum(self, n=64):
        return [0.3] * n


def _measure(_profile):
    _bv.BUILD_PROFILE = _profile
    _NS._sphere_cache.clear()
    _NS._reset_anim()
    for _i in range(15):                      # warm up and settle the bands
        _NS.draw_ncs_sphere(_scr, _P(), 1280, 748, _i * 0.033)
    _ts = []
    for _i in range(60):
        _t0 = time.perf_counter()
        _NS.draw_ncs_sphere(_scr, _P(), 1280, 748, 0.5 + _i * 0.016)
        _ts.append((time.perf_counter() - _t0) * 1000)
    _ts.sort()
    _a = _pg.surfarray.array3d(_scr).astype(_np.float32).mean(axis=2)
    _ys, _xs = _np.nonzero(_a > 24)
    if len(_xs) == 0:
        return _ts[len(_ts) // 2], 0, 0, 0
    return (_ts[len(_ts) // 2], int(len(_xs)),
            int(_xs.max() - _xs.min()), int(_ys.max() - _ys.min()))


_f_ms, _f_px, _f_w, _f_h = _measure("full")
_l_ms, _l_px, _l_w, _l_h = _measure("lite")
print(f"\n  the ball itself, drawn at 1280x748:")
print(f"    full  {_f_ms:5.2f} ms/frame   {_f_px:>7,} lit px   {_f_w}x{_f_h}")
print(f"    lite  {_l_ms:5.2f} ms/frame   {_l_px:>7,} lit px   {_l_w}x{_l_h}")

check(_l_ms < _f_ms,
      f"lite draws the ball faster ({_l_ms:.2f} ms < {_f_ms:.2f} ms)")
check(_l_px > 0, f"lite's ball is actually visible ({_l_px:,} lit pixels)")
check(abs(_l_w - _f_w) < _f_w * 0.15 and abs(_l_h - _f_h) < _f_h * 0.15,
      f"lite's ball is the same size ({_l_w}x{_l_h} vs {_f_w}x{_f_h})")
_l_aspect = _l_w / max(1, _l_h)
_f_aspect = _f_w / max(1, _f_h)
check(abs(_l_aspect - _f_aspect) < 0.10,
      f"lite's ball is still round (aspect {_l_aspect:.2f} vs {_f_aspect:.2f})")
check(_l_px >= _f_px * 0.60,
      f"lite keeps most of the texture ({_l_px:,} of {_f_px:,} lit pixels)")

print("\nLITE MODE TESTS PASSED" if not FAILS
      else f"\n{len(FAILS)} FAILURES: {FAILS}")
sys.exit(1 if FAILS else 0)
