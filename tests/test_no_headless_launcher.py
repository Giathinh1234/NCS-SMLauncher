"""The launcher must never force a headless video driver.

This is a regression test for a bug that shipped in 1.0.0: `actions.py` set
`SDL_VIDEODRIVER=dummy` unconditionally at import time, and `ncs_launcher.py`
imports it at module scope. SDL reads that variable when `set_mode()` runs, so
the real launcher opened with NO WINDOW -- it drew a perfect 60 FPS frame
loop into a surface nobody could see, and the process looked completely
healthy (busy CPU, no errors, no window).

Every test in this repo runs with the dummy driver set by the test file
itself, so the suite could never have caught it: the tests were already
headless, which is exactly why the module-level setdefault was both redundant
and dangerous.

The check below imports the launcher the way the app does -- with no SDL
variables preset -- and asserts nothing in the process got forced to dummy.
"""
import ast
import os
import sys

SRC = "/Users/giathinh/ncs-music-launcher/src"
sys.path.insert(0, SRC)

# Prove the precondition: this process starts with a real/absent driver.
# If the harness already forced dummy we cannot test anything meaningful.
os.environ.pop("SDL_VIDEODRIVER", None)
os.environ.pop("SDL_AUDIODRIVER", None)

print("LAUNCHER MUST NOT FORCE A HEADLESS DRIVER")
print("1) the driver is genuinely unset before the import")
assert "SDL_VIDEODRIVER" not in os.environ, \
    "the harness forced a dummy driver; this test cannot detect the bug"
print("   ok  SDL_VIDEODRIVER is unset")

print("2) importing the launcher (as the app does) must not set it")
import ncs_launcher  # noqa: E402

for var in ("SDL_VIDEODRIVER", "SDL_AUDIODRIVER"):
    got = os.environ.get(var)
    assert got != "dummy", (
        f"{var} was forced to 'dummy' just by importing ncs_launcher. "
        f"The real app would open with no window.")
    print(f"   ok  {var} = {got!r} (not forced to dummy)")

print("3) no module sets a dummy driver unless it is inside a guard")


def is_sdl_setdefault(node):
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if not (isinstance(fn, ast.Attribute) and fn.attr == "setdefault"):
        return False
    args = node.args
    if not args or not isinstance(args[0], ast.Constant):
        return False
    key = args[0].value
    return isinstance(key, str) and key.startswith("SDL_")


def collect(body, guarded):
    """Every SDL setdefault under `body`, flagging whether an `if` guards it."""
    found = []
    for node in body:
        if is_sdl_setdefault(node):
            found.append((node, guarded))
        for field, value in ast.iter_fields(node):
            inner_guarded = guarded or isinstance(node, ast.If)
            if isinstance(value, list):
                # Only AST elements; a list field may also hold plain strings
                # (e.g. an f-string's literal parts).
                found.extend(collect([v for v in value
                                      if isinstance(v, ast.AST)], inner_guarded))
            elif isinstance(value, ast.AST):
                found.extend(collect([value], inner_guarded))
    return found


suspicious = []
for name in sorted(os.listdir(SRC)):
    if not name.endswith(".py"):
        continue
    path = os.path.join(SRC, name)
    src_text = open(path, encoding="utf-8").read()
    lines = src_text.splitlines()
    tree = ast.parse(src_text, path)
    for node, guarded in collect(tree.body, False):
        if guarded:
            continue
        line = lines[node.lineno - 1].strip()
        suspicious.append(f"{name}:{node.lineno}  {line}")

assert not suspicious, (
    "these force an SDL dummy driver unconditionally at import time:\n  "
    + "\n  ".join(suspicious)
    + "\nGuard them behind `if os.environ.get('NCS_HEADLESS'):` -- a module "
      "imported by the launcher cannot set a dummy driver, or the shipped "
      "app opens with no window.")
print("   ok  no unguarded SDL setdefault in any module under src/")

print("4) headless still works when explicitly requested")
import subprocess
probe = subprocess.run(
    [sys.executable, "-c",
     "import os,sys; sys.path.insert(0,%r); os.environ['NCS_HEADLESS']='1';"
     "import actions; print(os.environ.get('SDL_VIDEODRIVER'))" % SRC],
    capture_output=True, text=True, timeout=120)
out = probe.stdout.strip().splitlines()[-1] if probe.stdout.strip() else ""
print(f"   NCS_HEADLESS=1 -> SDL_VIDEODRIVER={out!r}")
assert out == "dummy", f"opt-in headless broke: rc={probe.returncode} {probe.stderr[-300:]}"
print("   ok  the test suite can still ask for a headless driver")

print("LAUNCHER MUST NOT FORCE A HEADLESS DRIVER PASSED")
