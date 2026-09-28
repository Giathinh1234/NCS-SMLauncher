"""Regressions for bugs found in the overnight audit of 2026-09-28.

Every one of these was a real, reproducible defect. Where a bug was silent --
the defining characteristic of all of them -- the test asserts the specific
thing that used to go wrong, not merely that nothing crashed.
"""
import os
import subprocess
import sys
import tempfile

ROOT = "/Users/giathinh/ncs-music-launcher"
sys.path.insert(0, os.path.join(ROOT, "src"))
fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


print("AUDIT REGRESSIONS")

# ---------------------------------------------------------------- 1. media keys
print("1) media keys: the tap used a Quartz constant that does not exist")
import Quartz                                        # noqa: E402
import media_keys                                    # noqa: E402

check("kCGEventOtherKeyDown really is absent from this pyobjc",
      not hasattr(Quartz, "kCGEventOtherKeyDown"),
      "if this now exists, the getattr fallback is harmless but the bug was "
      "specific to this build")

src = open(os.path.join(ROOT, "src", "media_keys.py"), encoding="utf-8").read()
check("no bare Quartz.kCGEventOtherKeyDown attribute read is left",
      "Quartz.kCGEventOtherKeyDown" not in src,
      "still referenced by name somewhere")
check("the failure is now reported instead of silently returning False",
      "media keys unavailable" in src)

# A tap needs Accessibility permission, so start() legitimately returns False
# on this machine. What must not happen is an AttributeError being swallowed.
check("start() reports a real reason, not a bare False",
      "return False" in src and "except Exception as exc" in src)


# ------------------------------------------------- 2. the media-key rewind branch
print("2) the rewind media key did nothing, because the names disagreed")
launcher = open(os.path.join(ROOT, "src", "ncs_launcher.py"),
                encoding="utf-8").read()
check('the emitter sends "previous"', 'on_previous=lambda: media_key_handler("previous")'
      in launcher)
check('the handler now accepts both "prev" and "previous"',
      'action in ("prev", "previous")' in launcher)
check("so rewind is no longer a dead branch",
      'elif action == "prev":' not in launcher)


# ------------------------------------------------------- 3. pick_folder arity
print("3) Settings -> 'Choose library folder' raised TypeError")
check("do_pick_folder accepts the argument the settings panel passes",
      "def do_pick_folder(current=None):" in launcher)
check("it still opens the dialog",
      "pick_folder_dialog(current or folder)" in launcher)
panel = open(os.path.join(ROOT, "src", "settings_panel.py"),
             encoding="utf-8").read()
n_calls = panel.count("self.pick_folder(")
check("the settings panel still calls pick_folder(current)", n_calls >= 2, n_calls)

# Prove the arity actually matches now, without a GUI.
import ast                                         # noqa: E402
tree = ast.parse(launcher)
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "do_pick_folder")
n_total = len(fn.args.args)
n_required = n_total - len(fn.args.defaults)
check("do_pick_folder takes 0 required and at most 1 positional arg",
      n_required == 0 and n_total == 1, f"required={n_required} total={n_total}")


# ------------------------------------------------------ 4. config import crash
print("4) HASHPLAY_API_PORT=not-a-port killed the app at import")
for raw, want in (("not-a-port", 8777), ("99999", 8777), ("-5", 8777),
                  ("", 8777), ("8899", 8899), ("  8899  ", 8899)):
    env = dict(os.environ, HASHPLAY_API_PORT=raw, PYTHONPATH=os.path.join(ROOT, "src"))
    p = subprocess.run(
        [sys.executable, "-c",
         "import config; print(config.DEFAULT_CONFIG['api_port'])"],
        env=env, capture_output=True, text=True, timeout=30)
    got = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else f"CRASH: {p.stderr[-120:]}"
    check(f"HASHPLAY_API_PORT={raw!r} -> {want}", got == str(want), got)


# ------------------------------------------------------------- 5. disc player
print("5) ncs_disc_player: colorsys used but never imported, and a broken mode")
disc = open(os.path.join(ROOT, "src", "ncs_disc_player.py"),
            encoding="utf-8").read()
check("colorsys is imported", "\nimport colorsys" in disc)
# Import for real rather than exec'ing a source fragment: the module header
# references __file__, so a partial exec fails before reaching neon_color.
import ncs_disc_player                              # noqa: E402
try:
    r, g, b = ncs_disc_player.neon_color(0.5, 0.9, 0.8)
    # The contract is 0..255 ints, because the result goes straight into
    # pygame colours. (I first asserted 0..1 floats and it "failed" -- the
    # function was right and the test was wrong.)
    check("neon_color(0.5) returns three 0..255 ints",
          all(isinstance(v, int) and 0 <= v <= 255 for v in (r, g, b)),
          (r, g, b))
except NameError as e:
    check("neon_color(0.5) returns three 0..255 ints", False, e)

# Check the real call, not a grep: the 0o001 literal now appears only in the
# comment explaining why it went away, so grepping the file matched my own
# comment and reported a false failure.
main_tree = ast.parse(disc)
makedirs_calls = [n for n in ast.walk(main_tree)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "makedirs"]
check("every makedirs call uses exist_ok (or a real mode, never 1)",
      all(any(k.arg == "exist_ok" for k in n.keywords)
          for n in makedirs_calls),
      [ast.dump(n)[:90] for n in makedirs_calls])
check("no makedirs call passes a bare numeric mode",
      not any(len(n.args) > 1 and isinstance(n.args[1], ast.Constant)
              and isinstance(n.args[1].value, int)
              for n in makedirs_calls),
      [ast.dump(n)[:90] for n in makedirs_calls])


# ------------------------------------------------- 6. _resolve_web: -o - vs -g
print("6) every successful web resolve died on a UnicodeDecodeError")
video = open(os.path.join(ROOT, "src", "ncs_video.py"), encoding="utf-8").read()
check("yt-dlp is asked for a URL (-g), not for the media bytes (-o -)",
      '"-g", url' in video and '"-o", "-", url' not in video)

# Prove it end to end with a stand-in yt-dlp that behaves like the real one.
with tempfile.TemporaryDirectory() as d:
    fake = os.path.join(d, "yt-dlp")
    with open(fake, "w", encoding="utf-8") as fh:
        fh.write(
            "#!/bin/sh\n"
            # -g prints a URL; -o - would print the media, which is binary.
            "case \" $* \" in\n"
            "  *\" -g \"*) echo 'https://cdn.example/video-720p.mp4'; exit 0;;\n"
            "  *) printf '\\x00\\x93binaryjunk'; exit 0;;\n"
            "esac\n")
    os.chmod(fake, 0o755)
    import ncs_video                                # noqa: E402
    ncs_video.YTDLP = fake
    try:
        got, note = ncs_video._resolve_web("https://example/watch?v=abc")
        check("a resolving yt-dlp now yields a URL string, not a decode error",
              got == "https://cdn.example/video-720p.mp4", repr(got)[:120])
        check("and says where it came from", "resolved from" in note, note)
    except Exception as e:
        check("a resolving yt-dlp now yields a URL string, not a decode error",
              False, f"{type(e).__name__}: {e}")

    # A failing resolve must still raise a clear VideoError, not a decode error.
    failing = os.path.join(d, "yt-dlp-fail")
    with open(failing, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\necho 'ERROR: Video unavailable' >&2\nexit 1\n")
    os.chmod(failing, 0o755)
    ncs_video.YTDLP = failing
    try:
        ncs_video._resolve_web("https://example/nope")
        check("a failing resolve raises VideoError", False, "it returned")
    except ncs_video.VideoError as e:
        check("a failing resolve raises VideoError with the reason",
              "Video unavailable" in str(e), str(e))
    except Exception as e:
        check("a failing resolve raises VideoError", False,
              f"{type(e).__name__}: {e}")


# ------------------------------------------------ 7. _fail() leaked an ffmpeg
print("7) a failed video left a live ffmpeg running")
vtree = ast.parse(video)
fail_fn = next(n for n in ast.walk(vtree)
               if isinstance(n, ast.FunctionDef) and n.name == "_fail")
body = ast.dump(fail_fn)
check("_fail() closes the visual it is discarding", "'close'" in body,
      "it only drops the reference")
check("_fail() still marks the slot enabled=False",
      "enabled" in body and "False" in body)

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("AUDIT REGRESSION TESTS PASSED")
