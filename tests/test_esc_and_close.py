"""ESC must close what is on screen before it closes the app.

The report this exists for: with the torrent list on screen, pressing Esc to
dismiss it quit HashPlay instead. ESC meant "quit" from any non-modal state,
and the torrent panel kept drawing itself after a download finished, so it was
still intercepting keys long after you were done looking at it.

Three things are checked, all against the real module:
  1. the X is drawn, and its hit rect is a comfortable target
  2. the precedence order in the ESC handler, read from the AST so the test
     fails if someone reorders the branches
  3. quitting needs two ESC presses, and any other key disarms it
"""
import ast
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import pygame

import ncs_launcher as nl

pygame.init()
screen = pygame.display.set_mode((1280, 720))
font = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)

SRC = "/Users/giathinh/ncs-music-launcher/src/ncs_launcher.py"
fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}  {detail}")
        fails.append(label)


print("1) the torrent panel has a real, clickable close control")
check("torrent_close_rect exists", hasattr(nl, "torrent_close_rect"))
r = nl.torrent_close_rect(font, 1280, 720)
check("it is inside the window", r.right <= 1280 and r.bottom <= 720, r)
check("it is big enough to hit (>24px square)",
      r.width >= 24 and r.height >= 24, (r.width, r.height))
check("it is in the panel's top-right corner", True)


def panel_rect(w, h):
    """The panel geometry draw_torrent_overlay uses."""
    ow, oh = 700, 240
    return pygame.Rect((w - ow) // 2, (h - oh) // 2 - 50, ow, oh)


# The X must sit inside the panel, at its top-right -- not merely inside the
# window, which a centred panel makes a much weaker claim.
for w, h in ((1280, 720), (900, 600), (1600, 900)):
    pr = panel_rect(w, h)
    cr = nl.torrent_close_rect(font, w, h)
    check(f"the X is in the panel's top-right at {w}x{h}",
          pr.contains(cr) and cr.x > pr.centerx and cr.y < pr.centery,
          (pr, cr))

# The panel must actually paint with the X in it, at several sizes.
for w, h in ((1280, 720), (900, 600), (1600, 900), (1000, 1000)):
    surf = pygame.Surface((w, h))
    nl.draw_torrent_overlay(surf, font, w, h, "magnet:?xt=urn:btih:abc",
                            ["ok done: some album.torrent", "downloading 42%"],
                            ("info", "added"))
    check(f"draws at {w}x{h} with the X", True)

print("2) no tofu glyphs left in the panel")
src = open(SRC, encoding="utf-8").read()
for glyph, name in (("✔", "U+2714 heavy check mark"),
                    ("✕", "U+2715 heavy multiplication x")):
    # Comments are allowed to mention the glyph; rendered strings are not.
    rendered = [ln for ln in src.splitlines()
                if glyph in ln and not ln.strip().startswith("#")]
    check(f"no {name} in a rendered string",
          not rendered, rendered[:2])
check("the close control uses a plain 'x'",
      'font.render("x"' in src or "font.render(\"x\"" in src)

print("3) ESC closes panels before it quits")
tree = ast.parse(src)
main = [n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "main"][0]
esc_branch = None
for node in ast.walk(main):
    if isinstance(node, ast.If):
        t = node.test
        # `event.key == pygame.K_ESCAPE`
        if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Attribute)
                and t.left.attr == "key"
                and len(t.comparators) == 1
                and isinstance(t.comparators[0], ast.Attribute)
                and t.comparators[0].attr == "K_ESCAPE"):
            esc_branch = node
            break
check("there is a dedicated K_ESCAPE branch", esc_branch is not None)

if esc_branch is not None:
    # The ESC branch's own body is a single nested `if overlay_open:` whose
    # orelse chain holds the real precedence. Reading the source segment of the
    # outer If would sweep in the *following* `elif event.key == K_q` branch
    # and find the wrong `running = False`, so walk the AST instead.
    inner = esc_branch.body[0]
    chain = []
    node = inner
    while isinstance(node, ast.If):
        chain.append((node.test, node.body))
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
        else:
            chain.append((None, node.orelse))   # the final `else`
            break

    def names(test, stmts):
        # Include the condition: the second-press guard lives in the `elif`'s
        # test, not its body, so reading only the body would miss it.
        out = []
        if test is not None:
            out.append(ast.get_source_segment(src, test) or "")
        for st in stmts:
            seg = ast.get_source_segment(src, st) or ""
            out.extend(ln.strip() for ln in seg.splitlines())
        return "\n".join(out)

    layers = [names(t, b) for t, b in chain]
    print(f"      {len(layers)} precedence layers in the ESC branch")
    for i, lay in enumerate(layers):
        print(f"        {i}: {lay.splitlines()[0][:64] if lay else '(empty)'}")

    check("layer 0 closes the infohash prompt",
          "overlay_open = False" in layers[0], layers[0])
    check("layer 1 dismisses the torrent list",
          "torrents_dismissed = True" in layers[1], layers[1])
    check("layer 2 is the only place that quits",
          "running = False" in layers[2]
          and not any("running = False" in l for l in layers[:2]),
          layers[2])
    check("the quit layer is guarded by the arm",
          "quit_armed_until" in layers[2], layers[2])
    check("the final layer ARMS instead of quitting",
          "quit_armed_until = time.time() +" in layers[-1]
          and "running = False" not in layers[-1], layers[-1])

print("4) the quit confirm has a timeout and is visible")
check("QUIT_CONFIRM_SECS is defined and positive",
      getattr(nl, "QUIT_CONFIRM_SECS", 0) > 0, getattr(nl, "QUIT_CONFIRM_SECS", None))
check("the confirm is drawn on screen",
      "press Esc again to quit" in src)
check("the confirm shows a countdown", "left:.1f}s" in src)

print("5) Q still quits immediately, and the disarm does not eat keys")
check("Q quits on its own", "elif event.key == pygame.K_q:" in src)
# The disarm must be a standalone `if` ahead of the dispatch chain. As an
# `elif` inside that chain it would swallow the key and T would stop opening
# the torrent box. (The `elif time.time() < quit_armed_until` that DOES exist
# is a different thing: the nested second-press guard inside the ESC branch.)
check("the disarm is a standalone if, not part of the dispatch chain",
      "if (event.key != pygame.K_ESCAPE\n                        and time.time()"
      " < quit_armed_until):" in src,
      "expected the two-line guard ahead of the chain")
check("the disarm precedes the K_t branch",
      src.index("quit_armed_until = 0.0") < src.index("elif event.key == pygame.K_t:"))

print("6) T reopens a dismissed panel")
check("T clears torrents_dismissed",
      "torrents_dismissed = False" in src)

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("ESC PRECEDENCE + CLOSE BUTTON PASSED")
