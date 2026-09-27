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

print("KEYMAP / DISPATCH INTEGRATION PASSED")
