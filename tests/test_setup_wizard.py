"""Tests for the first-run setup flow (src/setup_wizard.py).

Run: python3 tests/test_setup_wizard.py

Real pygame on the dummy video driver, a real temp directory standing in for
the music folder, and an injected `deps` report so the dependency screen is
deterministic. The wizard writes no files at all, so the temp directory is
snapshotted before and after every flow and asserted to be untouched - as is
the real ~/.ncs-smlauncher, which is redirected into the temp dir and must
have no settings.json in it afterwards.
"""

import os
import sys
import tempfile

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((1280, 720))
FONT = pygame.font.SysFont("consolas,menlo,dejavusansmono", 17, bold=True)

import config  # noqa: E402
import settings_panel  # noqa: E402
import setup_wizard  # noqa: E402
from setup_wizard import SetupWizard, probe_dependencies  # noqa: E402

TMP = tempfile.mkdtemp(prefix="ncs-setup-wizard-")
MUSIC = os.path.join(TMP, "Music")
os.makedirs(MUSIC)

# The wizard never writes settings itself, but if that ever changes the write
# must land in the temp dir and nowhere near the user's real home.
config.CONFIG_DIR = TMP
config.CONFIG_PATH = os.path.join(TMP, "settings.json")
REAL_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".ncs-smlauncher")
REAL_STATE = (os.path.exists(REAL_CONFIG_DIR),
              os.path.getmtime(REAL_CONFIG_DIR) if os.path.exists(REAL_CONFIG_DIR) else 0)

# Deterministic dependency report. One present, one present, the rest absent -
# deliberately mixed so the screens have to render both states.
FAKE_DEPS = {
    "ffmpeg": {"present": True, "path": "/usr/local/bin/ffmpeg",
               "note": "video backgrounds and local video files (V)"},
    "ffprobe": {"present": True, "path": "/usr/local/bin/ffprobe",
                "note": "reading video streams and durations"},
    "libtorrent": {"present": False, "path": "",
                   "note": "torrent downloads (T)"},
    "sounddevice": {"present": True, "path": "sounddevice",
                    "note": "live audio output"},
    "miniaudio": {"present": False, "path": "",
                  "note": "decoding audio files"},
}


class Ev:
    """Minimal stand-in for a pygame KEYDOWN event."""

    def __init__(self, key, unicode=""):
        self.type = pygame.KEYDOWN
        self.key = key
        self.unicode = unicode


class SpyFont:
    """A real font that also remembers every string it was asked to draw.

    Counting blits proves the screen is not empty; it cannot tell a correct
    line from the wrong one. This can.
    """

    def __init__(self, font):
        self.font = font
        self.rendered = []

    def size(self, text):
        return self.font.size(text)

    def get_linesize(self):
        return self.font.get_linesize()

    def render(self, text, antialias, color):
        self.rendered.append((text, color))
        return self.font.render(text, antialias, color)


def key(const):
    return Ev(const, pygame.key.name(const))


def press(wiz, const):
    return wiz.handle_key(key(const))


def snapshot(root):
    """Every path under `root` with its mtime, for a no-writes assertion."""
    out = {}
    for base, dirs, files in os.walk(root):
        for name in list(dirs) + list(files):
            path = os.path.join(base, name)
            try:
                out[path] = os.path.getmtime(path)
            except OSError:
                out[path] = None
    return out


def build(folder="", deps=FAKE_DEPS, on_finish=None, run_setup=False):
    """A wizard over a fresh default config with the temp folder as the answer."""
    cfg = config.default_config()
    if run_setup or folder:
        cfg["setup_complete"] = True
    if folder:
        cfg["library_folder"] = folder
    return SetupWizard(FONT, cfg, deps=deps, on_finish=on_finish), cfg


def walk_to(wiz, name, limit=8):
    """Enter until the named step. Returns how many Enters it took."""
    for count in range(limit):
        if wiz.step_name() == name:
            return count
        press(wiz, pygame.K_RETURN)
    raise AssertionError("never reached step %r (stuck on %r)"
                         % (name, wiz.step_name()))


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def all_text(wiz):
    """Every string the wizard would draw, for wording assertions."""
    out = [wiz.title(), wiz.hint(), wiz.folder_note()]
    for step in setup_wizard.STEPS:
        saved = wiz.step
        wiz.step = setup_wizard.STEPS.index(step)
        out.extend(text for _kind, text in wiz.body_lines())
        wiz.step = saved
    out.extend(name for name, _available, _missing in wiz.feature_status())
    return out


def test_needs_setup():
    print("1) needs_setup() tracks the real question: is a folder chosen?")
    before = snapshot(TMP)
    wiz, cfg = build()
    check(wiz.needs_setup() is True,
          "a fresh config with no folder must need setup, got %r"
          % wiz.needs_setup())
    check(cfg["library_folder"] == "", "build() polluted the config")

    cfg["library_folder"] = MUSIC
    check(wiz.needs_setup() is False,
          "a chosen folder must end the wizard, got %r" % wiz.needs_setup())

    cfg["library_folder"] = ""
    check(wiz.needs_setup() is True,
          "clearing the folder must bring the wizard back")
    check(snapshot(TMP) == before, "needs_setup() wrote to the filesystem")
    print("   True with no folder, False once a folder is set")


def test_steps_advance():
    print("2) each step advances on Enter")
    wiz, cfg = build()
    check(wiz.step == 0 and wiz.step_name() == "welcome", "does not start welcome")
    press(wiz, pygame.K_RETURN)
    check(wiz.step_name() == "dependencies", "Enter did not reach dependencies")
    press(wiz, pygame.K_RETURN)
    check(wiz.step_name() == "folder", "Enter did not reach the folder step")
    press(wiz, pygame.K_RETURN)
    check(wiz.step_name() == "finish", "Enter did not reach the finish step")
    check(wiz.is_active(), "the wizard closed before it was finished")
    # Reaching the summary screen is not the same as finishing it: only the
    # final Enter writes setup_complete, so a crash here still re-opens setup.
    check(not cfg.get("setup_complete"),
          "setup_complete set before the user finished")
    print("   welcome -> dependencies -> folder -> finish")


def test_folder_choice():
    print("3) ENTER on the folder step returns a result with that folder")
    before = snapshot(TMP)
    wiz, cfg = build(folder=MUSIC)
    check(wiz.preselected is True, "an existing folder was not pre-selected")
    check(wiz.candidates[0] == MUSIC,
          "the configured folder should lead the candidates, got %r"
          % wiz.candidates)
    check(MUSIC in wiz.candidates, "the temp folder is not a candidate")
    walk_to(wiz, "folder")

    verdict = press(wiz, pygame.K_RETURN)
    check(verdict == "changed",
          "accepting a folder should report 'changed', got %r" % (verdict,))
    check(cfg["library_folder"] == MUSIC,
          "cfg holds %r, expected %r" % (cfg["library_folder"], MUSIC))
    check(wiz.result()["library_folder"] == MUSIC,
          "result() lost the folder: %r" % wiz.result())
    check(wiz.step_name() == "finish", "accepting did not move on")
    check(snapshot(TMP) == before, "choosing a folder wrote to the filesystem")

    verdict = press(wiz, pygame.K_RETURN)
    check(verdict == "done", "the final Enter should report 'done', got %r"
          % (verdict,))
    check(wiz.is_active() is False, "a finished wizard is still active")
    check(cfg["setup_complete"] is True, "setup_complete was not persisted")
    check(wiz.needs_setup() is False, "a finished wizard still needs setup")
    summary = wiz.result()
    check(summary["skipped"] is False, "a completed run reported as skipped")
    check(summary["features"]["Video backgrounds"] is True,
          "ffmpeg+ffprobe present but video reads as unavailable")
    check(summary["features"]["Torrent downloads"] is False,
          "libtorrent absent but torrents read as available")
    check("libtorrent" in summary["missing"], "missing list omits libtorrent")
    check(snapshot(TMP) == before, "finishing wrote to the filesystem")
    print("   result(): %r" % (summary,))


def test_left_right():
    print("4) Left/Right walk the candidates and wrap")
    wiz, _cfg = build(folder=MUSIC)
    walk_to(wiz, "folder")
    check(wiz.selected == 0, "does not start on the pre-selected folder")
    press(wiz, pygame.K_RIGHT)
    check(wiz.selected == 1, "RIGHT did not move: %d" % wiz.selected)
    press(wiz, pygame.K_DOWN)
    check(wiz.selected == 2, "DOWN should also move: %d" % wiz.selected)
    press(wiz, pygame.K_LEFT)
    check(wiz.selected == 1, "LEFT did not move back: %d" % wiz.selected)
    press(wiz, pygame.K_UP)
    check(wiz.selected == 0, "UP should also move back: %d" % wiz.selected)
    press(wiz, pygame.K_LEFT)
    check(wiz.selected == len(wiz.candidates) - 1, "LEFT did not wrap")
    press(wiz, pygame.K_RIGHT)
    check(wiz.selected == 0, "RIGHT did not wrap")
    check(wiz.chosen_folder() == MUSIC, "cursor is not on the chosen folder")
    print("   %d candidates: %s" % (len(wiz.candidates), wiz.candidates))


def test_escape_skips():
    print("5) Esc skips from any step, and the app stays usable")
    before = snapshot(TMP)
    for stop in ("welcome", "dependencies", "folder"):
        wiz, cfg = build()
        walk_to(wiz, stop)
        verdict = press(wiz, pygame.K_ESCAPE)
        check(verdict == "skipped",
              "Esc on %r returned %r" % (stop, verdict))
        check(wiz.is_active() is False, "Esc on %r left it active" % stop)
        check(cfg["setup_complete"] is True,
              "Esc on %r did not persist setup_complete - it would nag" % stop)
        check(cfg["library_folder"] == "",
              "Esc must not invent a folder: %r" % cfg["library_folder"])
        check(wiz.needs_setup() is False,
              "a skipped wizard still asks for setup on the next launch")
        check(wiz.result()["skipped"] is True, "result() lost the skip flag")
        check(press(wiz, pygame.K_RETURN) is None,
              "a finished wizard still answers keys on %r" % stop)
    check(snapshot(TMP) == before, "skipping wrote to the filesystem")
    print("   skips from all 3 steps, setup_complete persisted each time")


def test_modifier_ignored():
    print("6) a modifier-only press is ignored, not consumed as a choice")
    wiz, cfg = build(folder=MUSIC)
    walk_to(wiz, "folder")
    step = wiz.step
    picked = wiz.selected
    ignored = 0
    for const in sorted(settings_panel.MODIFIER_CONSTANTS):
        verdict = press(wiz, const)
        if verdict is None and wiz.step == step and wiz.selected == picked:
            ignored += 1
    check(ignored >= 5, "only %d modifier constants were ignored" % ignored)
    check(wiz.step == step, "a modifier advanced the wizard")
    check(wiz.selected == picked, "a modifier moved the folder cursor")
    check(cfg.get("library_folder") == MUSIC,
          "a modifier changed the config: %r" % cfg.get("library_folder"))
    # A real key still works immediately afterwards - the press was dropped,
    # not swallowed into some armed state.
    check(press(wiz, pygame.K_RETURN) == "changed",
          "the real key after a modifier was lost")
    print("   %d modifier constants dropped, step and cursor unmoved" % ignored)


def test_honesty():
    print("7) the dependency screen never over-claims")
    wiz, _cfg = build()
    walk_to(wiz, "dependencies")
    rows = dict((name, detail) for _mark, name, detail, _note in wiz.dep_lines())
    check(rows["ffmpeg"].startswith("found on PATH"),
          "a present binary should say where it was found: %r" % rows["ffmpeg"])
    check(rows["libtorrent"] == "not detected",
          "an absent module should read 'not detected': %r" % rows["libtorrent"])
    for name, info in wiz.deps.items():
        check(isinstance(info["present"], bool), "%s present is not a bool" % name)
        check(info["detail"], "%s has no detail line" % name)

    walk_to(wiz, "finish")
    for text in all_text(wiz):
        low = text.lower()
        for banned in ("works", "working feature is", "error", "failed to",
                       "broken", "required to run"):
            check(banned not in low,
                  "the wizard over-claims: %r contains %r" % (text, banned))
    check(any("not detected" in t for t in all_text(wiz)),
          "the summary never says what was not found")
    print("   presence wording only, no claim that anything runs")


def test_optional_is_not_an_error():
    print("8) a missing optional dependency is a smaller feature, not an error")
    empty = {name: False for name, _kind, _note in setup_wizard.DEPENDENCIES}
    wiz, _cfg = build(deps=empty)
    walk_to(wiz, "finish")
    status = wiz.result()["features"]
    check(status["Video backgrounds"] is False,
          "video is 'ready' with no ffmpeg at all")
    for name, available, missing in wiz.feature_status():
        check(missing, "%s claims to be off with nothing missing" % name)
    lines = " ".join(text for _kind, text in wiz.body_lines()).lower()
    check("not detected" in lines, "the summary hides what is absent")
    check("error" not in lines, "a missing extra is being framed as an error")
    print("   all-off machine reports every feature off, no errors raised")


def test_probe_shape():
    print("9) probe_dependencies() reports presence and nothing more")
    report = probe_dependencies()
    for name, _kind, _note in setup_wizard.DEPENDENCIES:
        check(name in report, "probe missed %r" % name)
        info = report[name]
        check(isinstance(info["present"], bool), "%s present is not a bool" % name)
        check(info["detail"], "%s has no detail" % name)
        check(info["note"], "%s has no note" % name)
    # An injected report must be honoured, and normalised into the same shape.
    wiz, _cfg = build(deps={"ffmpeg": True, "ffprobe": False,
                            "libtorrent": "/opt/lt.py", "sounddevice": None})
    check(wiz.deps["ffmpeg"]["present"] is True, "a bare True was dropped")
    check(wiz.deps["libtorrent"]["detail"].startswith("found on PATH"),
          "a path string was not read as a find: %r" % wiz.deps["libtorrent"])
    check(wiz.deps["sounddevice"]["present"] is False, "None became present")
    check(wiz.result()["features"]["Video backgrounds"] is False,
          "ffprobe reported present but video is 'ready'")
    print("   %s" % {n: report[n]["present"] for n in sorted(report)})


def test_on_finish():
    print("10) on_finish fires exactly once, on both endings")
    seen = []
    wiz, _cfg = build(folder=MUSIC, on_finish=seen.append)
    walk_to(wiz, "folder")
    press(wiz, pygame.K_RETURN)
    check(not seen, "on_finish fired mid-flow")
    press(wiz, pygame.K_RETURN)
    check(len(seen) == 1, "on_finish did not fire on done: %d" % len(seen))
    check(seen[0]["library_folder"] == MUSIC, "on_finish got %r" % (seen[0],))
    press(wiz, pygame.K_ESCAPE)
    check(len(seen) == 1, "on_finish fired again after the wizard closed")

    seen = []
    wiz, _cfg = build(on_finish=seen.append)
    press(wiz, pygame.K_ESCAPE)
    check(len(seen) == 1, "on_finish did not fire on skip")
    check(seen[0]["skipped"] is True, "on_finish got a non-skip summary")
    print("   once per run, with the same result() dict the caller gets")


def test_missing_folder_is_marked():
    print("11) a candidate that is not there says so, and can still be accepted")
    wiz, cfg = build()
    absent = os.path.join(TMP, "nope")
    wiz.cfg["library_folder"] = absent
    wiz.candidates = wiz._build_candidates()
    wiz.preselected = wiz._preselected()
    check(wiz.preselected is False,
          "a folder that does not exist was pre-selected")
    wiz.step = setup_wizard.STEPS.index("folder")
    body = " ".join(text for _kind, text in wiz.body_lines())
    check("not there" in body, "a missing folder is not marked: %r" % body)
    check("not there" in wiz.folder_note(), "the note does not admit it")
    check(press(wiz, pygame.K_RETURN) == "changed",
          "a user should still be allowed to choose it")
    check(cfg["library_folder"] == absent, "the choice was not recorded")
    check(not os.path.isdir(absent), "the wizard created a directory")
    print("   marked, not created, still choosable")


def test_draw():
    print("12) draw() paints every step, draws the right text, survives odd sizes")
    screen = pygame.display.get_surface()
    wiz, _cfg = build(folder=MUSIC)
    for index, name in enumerate(setup_wizard.STEPS):
        wiz.step = index
        wiz.active = True
        screen.fill((0, 0, 0))
        before = pygame.image.tobytes(screen, "RGB")
        spy = SpyFont(FONT)
        wiz.draw(screen, spy, 1280, 720)
        painted = pygame.image.tobytes(screen, "RGB")
        check(before != painted, "draw() on step %r changed no pixels" % name)
        # The pixels prove something was drawn; the spy proves it was the
        # right thing. A step whose (kind, text) order is swapped still
        # "paints" - it just paints the word "dim" instead of the sentence.
        drawn = [text for text, _colour in spy.rendered]
        # display_lines() is the same list draw() blits, so this compares the
        # on-screen strings, not a parallel idea of them.
        expected = [text for kind, text in wiz.display_lines(1280, FONT)
                    if kind != "gap" and text]
        for line in expected:
            check(line in drawn, "step %r never drew %r" % (name, line))
        check(wiz.title() in drawn, "step %r drew no title" % name)
        # Nothing may be cut off at the default window size - a dependency
        # report whose notes are ellipsized away says nothing.
        body = [text for kind, text in wiz.body_lines() if kind != "gap" and text]
        shown = [text for kind, text in wiz.display_lines(1280, FONT)
                 if kind != "gap" and text]
        for raw, clipped in zip(body, shown):
            # A path may lose its head - the tail is the useful end - but a
            # sentence must never be right-cut: "That is presence, not a
            # working featur…" is worse than no line at all.
            ok = (raw == clipped or clipped.startswith("…")
                  or raw.endswith(clipped.lstrip("…")))
            check(ok, "line clipped at 1280x720: %r -> %r" % (raw, clipped))
    for w, h in ((320, 240), (420, 320), (640, 480), (1920, 1080)):
        wiz.draw(screen, FONT, w, h)
    wiz.draw(screen)  # sizes itself from the surface
    wiz.active = False
    screen.fill((0, 0, 0))
    before = pygame.image.tobytes(screen, "RGB")
    wiz.draw(screen, FONT, 1280, 720)
    check(before == pygame.image.tobytes(screen, "RGB"),
          "a finished wizard still painted")
    print("   4 steps x 5 sizes, every body line drawn, no exception")


def test_writes_nothing():
    print("13) a full flow writes nothing outside the temp directory")
    before = snapshot(TMP)
    wiz, cfg = build(folder=MUSIC)
    for _ in range(8):
        press(wiz, pygame.K_RETURN)
    check(wiz.step_name() == "finish", "the flow did not reach the end")
    after = snapshot(TMP)
    check(after == before,
          "the wizard changed the filesystem: %r"
          % sorted(set(after) ^ set(before)))
    check(not os.path.exists(config.CONFIG_PATH),
          "the wizard wrote %s itself" % config.CONFIG_PATH)
    now = (os.path.exists(REAL_CONFIG_DIR),
           os.path.getmtime(REAL_CONFIG_DIR) if os.path.exists(REAL_CONFIG_DIR) else 0)
    check(now == REAL_STATE,
          "the real %s was touched: %r -> %r" % (REAL_CONFIG_DIR, REAL_STATE, now))
    print("   %d entries in %s, all mtimes unchanged" % (len(after), TMP))


def main():
    test_needs_setup()
    test_steps_advance()
    test_folder_choice()
    test_left_right()
    test_escape_skips()
    test_modifier_ignored()
    test_honesty()
    test_optional_is_not_an_error()
    test_probe_shape()
    test_on_finish()
    test_missing_folder_is_marked()
    test_draw()
    test_writes_nothing()
    print("\nSETUP WIZARD TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("\nFAILED: %s" % exc)
        sys.exit(1)
