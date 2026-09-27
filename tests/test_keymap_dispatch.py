"""The keymap/config/action wiring in the real launcher.

Two things are checked here that unit tests of config.py and actions.py cannot
reach on their own:

  1. the integration contract -- every default binding in the real keymap
     resolves to the action the app expects, and a rebind actually changes
     what a keypress resolves to;
  2. that the launcher's dispatch table handles EVERY registered action.

The second is the one that catches real breakage. Adding an action to
ACTIONS.py without adding a branch to do_action() leaves a key that resolves
to a name the app then reports as "unbound", which is invisible until someone
presses the key.
"""
import ast
import os
import re as _re
import sys
import tempfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

SRC = "/Users/giathinh/ncs-music-launcher/src"
sys.path.insert(0, SRC)

import pygame
pygame.init()

import actions as A
import config as C
import migrations as M

LAUNCHER = os.path.join(SRC, "ncs_launcher.py")

print("KEYMAP / DISPATCH INTEGRATION")

# ---- 1. every registered action has a default that resolves --------------
print("1) every action has a resolvable default binding")
for name, meta in sorted(A.ACTIONS.items()):
    binding = C.DEFAULT_KEYMAP.get(name)
    assert binding is not None, f"{name} has no default binding"
    const = A.key_to_constant(binding)
    assert const is not None, f"{name} default {binding!r} is unbindable"
    got = A.Keymap().resolve(const)
    assert got == name, \
        f"{name} is bound to {binding!r} but that key resolves to {got!r}"
    print(f"   ok  {name:18} -> {A.human_key_name(binding)}")
print("   all %d actions resolve to themselves" % len(A.ACTIONS))

# ---- 2. a rebind changes what the key resolves to ------------------------
print("2) a rebind changes what a keypress resolves to")
km = A.Keymap()
assert km.resolve(A.key_to_constant("F")) == "cycle_visualizer"
km.bind("cycle_visualizer", "J")
assert km.resolve(A.key_to_constant("F")) is None, \
    "the old key must be released"
assert km.resolve(A.key_to_constant("J")) == "cycle_visualizer", \
    "the new key must resolve to the action"
print("   ok  F released, J now triggers cycle_visualizer")

print("3) a rebind survives a round trip through the config file")
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "settings.json")
    cfg = C.load_config(path)
    cfg = M.migrate(cfg)
    km2 = A.Keymap(cfg.get("keymap"))
    km2.bind("quit", "ESCAPE")
    cfg["keymap"] = dict(km2.bindings)
    C.save_config(cfg, path)
    reread = M.migrate(C.load_config(path))
    km3 = A.Keymap(reread.get("keymap"))
    assert km3.resolve(A.key_to_constant("ESCAPE")) == "quit", \
        "a persisted rebind did not survive reload"
    assert km3.resolve(A.key_to_constant("Q")) is None, \
        "the old quit key should be gone after rebinding"
    print("   ok  ESCAPE=quit persisted, Q released")

# ---- 4. the launcher dispatch handles every action -----------------------
print("4) the launcher's do_action handles EVERY registered action")
tree = ast.parse(open(LAUNCHER).read(), LAUNCHER)

dispatch = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "do_action":
        dispatch = node
        break
assert dispatch is not None, "do_action() is missing from ncs_launcher.py"

handled = set()
for node in ast.walk(dispatch):
    if isinstance(node, ast.If):
        test = node.test
        # `act == "name"` appears as a Compare on the `act` name
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name):
            if test.left.id == "act" and len(test.ops) == 1 and \
                    isinstance(test.ops[0], ast.Eq) and \
                    len(test.comparators) == 1 and \
                    isinstance(test.comparators[0], ast.Constant):
                handled.add(test.comparators[0].value)

missing = sorted(set(A.ACTIONS) - handled)
assert not missing, \
    f"do_action() does not handle: {missing} -- those keys resolve but do nothing"
print("   ok  all %d actions have a dispatch branch" % len(handled))

# ---- 5. the old literal key chain is gone -------------------------------
print("5) the hardcoded key chain is gone from the event loop")
src = open(LAUNCHER).read()
# These used to be literal branches. The launcher's structural keys (ESC,
# RETURN, BACKSPACE inside overlays, and UP/DOWN/LEFT/RIGHT/SPACE which are
# still literal) are allowed; what must be gone is the per-action literals
# that the keymap now owns.
for gone in ("pygame.K_c", "pygame.K_m", "pygame.K_f"):
    assert gone not in src, (
        f"{gone} is still bound literally; it must go through the keymap so a "
        f"rebind takes effect")
    print(f"   ok  {gone} is no longer hardcoded")

print("6) the settings panel is imported lazily, not at module load")
# Importing settings_panel at module scope would make the whole app depend on
# it; a missing file should only break the key that opens settings.
tree_top = [n for n in tree.body if isinstance(n, ast.Import)]
names = [a.name for n in tree_top for a in n.names]
assert "settings_panel" not in names, \
    "settings_panel must not be a module-level import"
print("   ok  no module-level settings_panel import")

# ---- 7. the launcher calls SettingsPanel the way it is defined ----------
print("7) the launcher's SettingsPanel calls match its real signature")
try:
    import inspect
    import settings_panel as sp
except ImportError:
    print("   settings_panel.py not present yet; skipping signature check")
    raise SystemExit(0)

init = inspect.signature(sp.SettingsPanel.__init__)
params = set(init.parameters)
# keywords the launcher passes
for kw in ("cfg", "keymap", "pick_folder", "font"):
    assert kw in params, \
        f"the launcher passes {kw}= to SettingsPanel but it is not a parameter"
    print(f"   ok  SettingsPanel accepts {kw}=")

draw = inspect.signature(sp.SettingsPanel.draw)
dp = set(draw.parameters)
for kw in ("screen", "font", "w", "h"):
    assert kw in dp, f"draw() is missing the {kw} parameter"
print("   ok  draw() takes screen, font, w, h")

# The launcher passes w/h by keyword. If they were positional, w would land
# in the `font` slot -- a silent, ugly failure, so pin it.
src_calls = open(LAUNCHER).read()
assert "settings_panel.draw(screen, w, h)" not in src_calls, \
    "draw(screen, w, h) passes w as the font argument"
assert "draw(screen, font=font, w=w, h=h)" in src_calls, \
    "the launcher should pass w/h to draw() by keyword"
print("   ok  w/h are passed to draw() by keyword, not positionally")

# handle_key's documented return values must all lead to a save, either via
# the close branch or the changed branch.
doc = inspect.getdoc(sp.SettingsPanel.handle_key) or ""
returns = {m.group(1) for m in _re.finditer(r'"([a-z]+)"', doc)}

handled = set()
handled |= set(_re.findall(r'verdict == "([a-z]+)"', src_calls))
for chunk in _re.findall(r'verdict in \(([^)]*)\)', src_calls):
    handled |= set(_re.findall(r'"([a-z]+)"', chunk))

missing = returns - handled
assert not missing, (
    f"handle_key can return {sorted(returns)} but the launcher only reacts to "
    f"{sorted(handled)}; a change would not be persisted")
print(f"   ok  launcher reacts to every value handle_key can return "
      f"({sorted(returns)})")

print("KEYMAP / DISPATCH INTEGRATION PASSED")
