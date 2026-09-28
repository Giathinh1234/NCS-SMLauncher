"""First-run setup flow for HashPlay.

Why a wizard at all: the launcher's one non-negotiable input is the music
folder, and it is also the one thing it cannot guess. Everything else it can
work out or degrade around - no ffmpeg means the V key explains itself, no
libtorrent means T is quietly inert. A user who starts the app with no folder
set gets an empty track list and no idea why, so the first run walks them
through the only two real decisions (where is your music, what can this
machine already do) and then gets out of the way.

Four constraints shaped this module:

* **No native folder dialog.** The folder is picked with Left/Right from a
  short candidate list, not with an OS chooser. A modal dialog cannot be
  driven by a test, cannot be driven over the control API, and blocks the
  render loop until someone clicks it. The candidate list is one keystroke
  and one blit, which makes the entire flow headless-testable end to end.

* **Esc skips, and skipping is final.** Someone with no music on disk yet has
  to be able to dismiss this and reach a usable app, or first run becomes a
  gate they cannot pass. So Esc sets `setup_complete` exactly the way
  finishing does - the wizard never comes back to nag - while leaving
  `library_folder` empty, because "I have not chosen one yet" and "I chose
  this one" are different answers and only the first is true after a skip.

* **Presence is not proof.** Probing is `shutil.which` and `import`, and that
  only answers "is it there", never "does it work". Every line here therefore
  says *found* or *not detected* and never claims a feature is functional.
  ffmpeg is the only piece that is genuinely required, and only for the video
  feature; the rest are optional extras, and a missing optional dependency is
  a smaller feature, not a broken install - so they are never dressed up as
  errors.

* **No writes.** The wizard edits the caller's cfg dict in place and hands
  back a summary via result(); persisting is the launcher's job, through
  config.save_config. Nothing in here touches the filesystem, so a test can
  run the whole flow against a temp directory and assert that nothing else
  moved.

Public surface:
    DEPENDENCIES / FEATURES   what is probed and what each piece unlocks
    probe_dependencies()      what this machine has, without claiming it works
    SetupWizard               needs_setup() / handle_key() / draw() / result()
"""

import os
import shutil

import pygame  # noqa: E402  (K_* constants only; SDL env is never touched here)

# The launcher's key-handling convention says a bare modifier press is not a
# choice, and settings_panel is where that rule already lives - including the
# CapsLock trap that a hand-listed set kept missing. Reusing the set is the
# point: two divergent definitions of "a modifier" is how a stray Shift ends
# up moving the folder cursor.
from settings_panel import MODIFIER_CONSTANTS

__all__ = [
    "DEPENDENCIES",
    "FEATURES",
    "REQUIRED_FOR_FEATURES",
    "STEPS",
    "SetupWizard",
    "probe_dependencies",
]

# The flow, in order. A tuple rather than an enum-ish class because the step
# doubles as a save file for the launcher and as a switch in draw().
STEPS = ("welcome", "dependencies", "folder", "finish")

# name -> (how it is probed, what it unlocks). Order is display order: the
# video pair first because it is the one that is actually required.
DEPENDENCIES = (
    ("ffmpeg", "binary", "video backgrounds and local video files (V)"),
    ("ffprobe", "binary", "reading video streams and durations"),
    ("libtorrent", "module", "torrent downloads (T)"),
    ("sounddevice", "module", "live audio output"),
    ("miniaudio", "module", "decoding audio files"),
)

# Features, as the user thinks of them rather than as packages: a feature is
# available only when every dependency it names is present.
FEATURES = (
    ("Video backgrounds", ("ffmpeg", "ffprobe"), "press V for a video layer"),
    ("Torrent downloads", ("libtorrent",), "press T to fetch a torrent"),
    ("Audio output", ("sounddevice",), "sound comes from your speakers"),
    ("Decoding", ("miniaudio",), "your files can be read at all"),
)

# Only ffmpeg is load-bearing, and only for video. Saying otherwise would
# turn a missing optional extra into a support ticket.
REQUIRED_FOR_FEATURES = {"ffmpeg": ("video",)}

# Palette lifted from draw_video_overlay() / draw_torrent_overlay().
COL_BG = (6, 8, 12, 236)
COL_CARD = (12, 14, 22, 240)
COL_BORDER = (0, 230, 190, 120)
COL_ACCENT = (0, 230, 190)
COL_OK = (0, 230, 190)
COL_FG = (235, 235, 245)
COL_DIM = (140, 145, 160)
COL_WARN = (255, 200, 90)
COL_HINT = (110, 115, 130)
COL_SELECT_FG = (10, 14, 18)

SCREEN_MARGIN = 24
# Card width is fixed; its height follows the body, so there is no
# CARD_H to tune here. See draw().
CARD_W = 820
PAD_X = 26
ELLIPSIS = "…"


def _display_path(path):
    """Home-relative form for the screen. What is stored stays absolute."""
    home = os.path.expanduser("~")
    if home and path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _module_importable(name):
    """True when `import name` succeeds. Never raises, never runs a version."""
    try:
        __import__(name)
        return True
    except Exception:
        # Bare Exception on purpose: a broken native extension can fail in
        # ways that are not ImportError, and "not detected" is the honest
        # answer for all of them.
        return False


def probe_dependencies():
    """What this machine has, as presence only.

    The result is deliberately shaped like control_api.probe_dependencies()
    so the wizard and the control API can hand each other the same dict. Each
    entry is {present, path, note, detail}: "present" is what was checked,
    "detail" is the honest description of that check, and neither is ever a
    claim that the feature runs.
    """
    out = {}
    for name, kind, note in DEPENDENCIES:
        if kind == "binary":
            path = shutil.which(name)
            present = bool(path)
            detail = ("found on PATH: %s" % path) if present else "not detected"
        else:
            present = _module_importable(name)
            # No version, no import-time probe beyond the import itself.
            detail = ("imports (not version-checked)" if present
                      else "not detected")
        out[name] = {"present": present,
                     "path": path if (kind == "binary" and present) else
                            (name if present else ""),
                     "note": note,
                     "detail": detail}
    return out


def _normalize_deps(deps):
    """Coerce whatever the caller injected into the probe_dependencies() shape.

    The launcher owns the probing (it may already have a report from
    control_api, and it must not pay for a second import sweep at first
    launch), so the wizard has to tolerate three shapes of the same fact:
    the full dict, a bare bool, or a path string.
    """
    out = {}
    for name, _kind, note in DEPENDENCIES:
        raw = (deps or {}).get(name)
        path = ""
        if isinstance(raw, bool) or raw is None:
            present = bool(raw)
        elif isinstance(raw, str):
            present = bool(raw)
            path = raw
        elif isinstance(raw, dict):
            present = bool(raw.get("present"))
            path = str(raw.get("path") or "")
        else:
            present = bool(raw)
        detail = raw.get("detail") if isinstance(raw, dict) else None
        if not detail:
            if present and path and path != name:
                detail = "found on PATH: %s" % path
            elif present:
                detail = "detected (existence only)"
            else:
                detail = "not detected"
        out[name] = {"present": present, "path": path,
                     "note": (raw.get("note") if isinstance(raw, dict) and
                              raw.get("note") else note),
                     "detail": detail}
    return out


class SetupWizard:
    """Modal first-run flow: welcome, dependencies, folder, summary.

    `cfg` is the launcher's live config dict and is edited in place, the same
    contract SettingsPanel uses - the launcher saves it. `deps` is an
    optional pre-built report (see probe_dependencies); pass one to keep the
    wizard from re-probing the system. `on_finish(summary)` is called exactly
    once, with result(), on either "done" or "skipped".
    """

    def __init__(self, font, cfg, deps=None, on_finish=None):
        self.font = font
        self.cfg = cfg if isinstance(cfg, dict) else {}
        self.on_finish = on_finish
        self.deps = (_normalize_deps(deps) if deps is not None
                     else probe_dependencies())
        self.step = 0
        self.candidates = self._build_candidates()
        self.selected = 0
        self.skipped = False
        self.active = True
        self._notified = False
        self.preselected = self._preselected()

    # ---- state ---------------------------------------------------------
    def needs_setup(self):
        """True while the wizard still has a reason to exist.

        A folder means the question is already answered. `setup_complete`
        means the user answered it by walking away, which is also an answer -
        re-opening this on someone who escaped the first run is exactly the
        nagging that makes people not install things.
        """
        if (self.cfg.get("library_folder") or "").strip():
            return False
        return not bool(self.cfg.get("setup_complete"))

    def is_active(self):
        return self.active

    def step_name(self):
        return STEPS[min(self.step, len(STEPS) - 1)]

    def _build_candidates(self):
        """Folder candidates, best guess first, de-duplicated.

        The configured folder leads because if it is set it is the answer the
        user already gave (or the one we are asking them to confirm).
        """
        seen = set()
        out = []
        for raw in (self.cfg.get("library_folder") or "",
                    os.path.join(os.path.expanduser("~"), "Music"),
                    os.path.join(os.path.expanduser("~"), "Downloads")):
            if not raw:
                continue
            path = os.path.abspath(os.path.expanduser(str(raw).strip()))
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
        if not out:  # no cfg, no home - still offer something enterable
            out = [os.path.expanduser("~/Music")]
        return out

    def _preselected(self):
        """True when the folder in settings is a real directory we can offer."""
        current = (self.cfg.get("library_folder") or "").strip()
        return bool(current) and os.path.isdir(os.path.expanduser(current))

    def _move(self, delta):
        """Step the folder cursor, wrapping. A no-op on every other step."""
        if self.step_name() != "folder" or not self.candidates:
            return self.selected
        self.selected = (self.selected + delta) % len(self.candidates)
        return self.selected

    def chosen_folder(self):
        """The folder the cursor is on, or '' when there is none."""
        if not self.candidates:
            return ""
        return self.candidates[min(self.selected, len(self.candidates) - 1)]

    # ---- input ---------------------------------------------------------
    def handle_key(self, event):
        """Consume one key event.

        Returns "done", "skipped", "changed", or None. "changed" means a
        folder was accepted and cfg now holds it; the launcher can rescan at
        that point instead of waiting for the final Enter.
        """
        if not self.active:
            return None
        key = getattr(event, "key", None)
        if key is None:
            # A mouse or window event. Not ours, and certainly not a choice.
            return None
        if key in MODIFIER_CONSTANTS:
            # Checked before everything that moves. A bare Shift/Alt arrives
            # as its own keydown, and on the folder step accepting that would
            # move the cursor - so the next Enter writes a folder nobody
            # chose. Ignored, never consumed as a selection.
            return None
        if key == pygame.K_ESCAPE:
            return self._skip()
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            return self._accept()
        if key in (pygame.K_LEFT, pygame.K_UP):
            self._move(-1)
        elif key in (pygame.K_RIGHT, pygame.K_DOWN):
            self._move(1)
        return None

    def _accept(self):
        name = self.step_name()
        if name == "folder":
            folder = self.chosen_folder()
            if not folder:
                return None
            self.cfg["library_folder"] = folder
            self.step += 1
            return "changed"
        if name == "finish":
            self.cfg["setup_complete"] = True
            self.active = False
            self._notify()
            return "done"
        self.step += 1
        return None

    def _skip(self):
        """Leave the app usable with no folder chosen, and never come back."""
        self.skipped = True
        self.cfg["setup_complete"] = True
        self.active = False
        self._notify()
        return "skipped"

    def _notify(self):
        if self._notified or self.on_finish is None:
            return
        self._notified = True
        self.on_finish(self.result())

    # ---- reporting -----------------------------------------------------
    def feature_status(self):
        """[(name, available, missing_dep_names)] for the summary screens."""
        out = []
        for name, needs, _hint in FEATURES:
            missing = [dep for dep in needs if not self.deps[dep]["present"]]
            out.append((name, not missing, missing))
        return out

    def result(self):
        """Summary of the run. Empty `library_folder` means it was skipped."""
        return {
            "library_folder": (self.cfg.get("library_folder") or ""),
            "skipped": bool(self.skipped),
            "setup_complete": bool(self.cfg.get("setup_complete")),
            "available": sorted(name for name, info in self.deps.items()
                                if info["present"]),
            "missing": sorted(name for name, info in self.deps.items()
                              if not info["present"]),
            "features": {name: available
                         for name, available, _missing in self.feature_status()},
        }

    def dep_lines(self):
        """[(mark, name, detail, note)] as the dependency step draws them.

        ASCII marks, deliberately. U+2714 and U+2713 are both absent from the
        fallback monospace face the launcher resolves, and a dependency report
        that opens with five tofu boxes is worse than one that says "ok".
        Colour still carries the ok/missing distinction.
        """
        rows = []
        for name, _kind, note in DEPENDENCIES:
            info = self.deps[name]
            mark = "ok" if info["present"] else "--"
            rows.append((mark, name, info["detail"], note))
        return rows

    def folder_note(self):
        """One line about the folder already in settings, or '' if unset.

        Deliberately does not quote the path: the candidate list right above
        it already shows the path, and a sentence with a path glued to the
        end is the first thing to be ellipsized away on a long one.
        """
        current = (self.cfg.get("library_folder") or "").strip()
        if not current:
            return "No music folder in settings yet."
        if self.preselected:
            return "Settings already point at this folder - Enter keeps it."
        return "Settings point at a folder that is not there - pick one."

    # ---- text ----------------------------------------------------------
    def _welcome_lines(self):
        return [
            ("gap", ""),
            ("text", "HashPlay is a keyboard-driven music player with a "
                     "beat visualiser."),
            ("text", "It scans one folder for audio, plays it, and draws it."),
            ("gap", ""),
            ("text", "Two things to decide: where your music is, and what this"),
            ("text", "machine can already do."),
            ("gap", ""),
            ("dim", "Change the folder any time with the O key."),
        ]

    def _dependency_lines(self):
        # Name + note on one line, the evidence on its own dimmer line. Both
        # fit a 1280-wide window uncut; cramming mark, path, note and unlock
        # onto one row is what pushed the note past the card edge.
        lines = [("gap", "")]
        for mark, name, detail, note in self.dep_lines():
            lines.append(("ok" if mark == "ok" else "dim",
                          "%-3s %-12s %s" % (mark, name, note)))
            lines.append(("dim", "     %s" % detail))
        lines.append(("gap", ""))
        lines.append(("dim", "Checked how: a binary on PATH, or a module that "
                            "imports."))
        lines.append(("dim", "That is presence, not a working feature - "
                            "nothing here was run."))
        lines.append(("gap", ""))
        lines.append(("text", "Only ffmpeg is required, and only for video. "
                              "The rest are extras."))
        missing = [name for name, info in self.deps.items()
                   if not info["present"]]
        if missing:
            lines.append(("dim", "Not detected: " + ", ".join(missing)))
        else:
            lines.append(("dim", "Everything is detected."))
        return lines

    def _folder_lines(self):
        lines = [("gap", ""), ("text", "Which folder should HashPlay scan?")]
        lines.append(("gap", ""))
        for index, path in enumerate(self.candidates):
            found = "folder found" if os.path.isdir(path) else "not there"
            style = "item" if index == self.selected else "dim"
            lines.append((style, "›   %s   (%s)"
                          % (_display_path(path), found)))
        lines.append(("gap", ""))
        note = self.folder_note()
        lines.append(("dim" if self.preselected else "warn", note))
        return lines

    def _finish_lines(self):
        folder = self.cfg.get("library_folder") or ""
        if self.skipped:
            lines = [("gap", ""), ("warn", "Setup skipped."), ("gap", "")]
            lines.append(("text", "No folder chosen - the library starts "
                                  "empty."))
            lines.append(("text", "Press O whenever you have music to point "
                                  "it at."))
        else:
            lines = [("gap", "")]
            if folder:
                # The path on its own line so it can keep its tail when the
                # home directory is deep enough to overflow the card.
                lines.append(("ok", "Music folder:"))
                lines.append(("path", _display_path(folder)))
            else:
                lines.append(("warn", "No music folder chosen."))
            lines.append(("gap", ""))
        for name, available, missing in self.feature_status():
            if available:
                lines.append(("ok", "ready     %s" % name))
            else:
                # Same wording as the dependency step on purpose: a feature
                # being off is a fact about detection, not a broken install.
                lines.append(("dim", "off       %s  (%s not detected)"
                              % (name, ", ".join(missing))))
        lines.append(("gap", ""))
        lines.append(("dim", "Anything marked off was simply not found "
                             "here. Nothing else is affected."))
        return lines

    def title(self):
        return {
            "welcome": "WELCOME TO HASHPLAY",
            "dependencies": "WHAT THIS MACHINE CAN DO",
            "folder": "CHOOSE A MUSIC FOLDER",
            "finish": "READY",
        }[self.step_name()]

    def hint(self):
        name = self.step_name()
        if name == "folder":
            return "Left/Right choose  ·  Enter use this  ·  Esc skip setup"
        if name == "finish":
            return "Enter start playing"
        return "Enter continue  ·  Esc skip setup"

    def body_lines(self):
        return {
            "welcome": self._welcome_lines,
            "dependencies": self._dependency_lines,
            "folder": self._folder_lines,
            "finish": self._finish_lines,
        }[self.step_name()]()

    # ---- drawing -------------------------------------------------------
    def _ellipsize(self, text, max_w, font):
        if max_w <= 0 or not text:
            return ""
        if font.size(text)[0] <= max_w:
            return text
        trimmed = text
        while trimmed and font.size(trimmed + ELLIPSIS)[0] > max_w:
            trimmed = trimmed[:-1]
        return trimmed + ELLIPSIS

    def _ellipsize_left(self, text, max_w, font):
        """Keep the tail - for a path that is the whole point of the line."""
        if max_w <= 0 or not text:
            return ""
        if font.size(text)[0] <= max_w:
            return text
        trimmed = text
        while trimmed and font.size(ELLIPSIS + trimmed)[0] > max_w:
            trimmed = trimmed[1:]
        return ELLIPSIS + trimmed

    def inner_width(self, w):
        inner_w = min(CARD_W, int(w) - 2 * SCREEN_MARGIN) - 2 * PAD_X
        return max(80, inner_w)

    def display_lines(self, w, font):
        """The (kind, text) pairs exactly as draw() will blit them at width w.

        Single source of truth for the same reason as
        settings_panel.row_display(): a test that measures body_lines() while
        draw() shortens a line somewhere else is measuring a different string
        than the one on screen. Paths keep their tail, everything else keeps
        its head.
        """
        inner_w = self.inner_width(w)
        out = []
        for kind, text in self.body_lines():
            if not text:
                out.append((kind, text))
                continue
            if kind in ("item", "path"):
                out.append((kind, self._ellipsize_left(text, inner_w, font)))
            else:
                out.append((kind, self._ellipsize(text, inner_w, font)))
        return out

    def draw(self, screen, font=None, w=None, h=None):
        """Paint the full-window modal. `w`/`h` fall back to the surface."""
        if not self.active:
            return
        font = font or self.font
        if font is None:
            return
        if w is None or h is None:
            size = screen.get_size()
            w = size[0] if w is None else w
            h = size[1] if h is None else h
        w, h = int(w), int(h)

        # Same treatment as draw_video_overlay(): cover the launcher outright
        # so nothing underneath competes with a first-run question.
        veil = pygame.Surface((max(1, w), max(1, h)), pygame.SRCALPHA)
        veil.fill(COL_BG)
        screen.blit(veil, (0, 0))

        line_h = font.get_linesize()
        body = self.display_lines(w, font)
        # The card hugs its content: a first-run box with 300 px of empty
        # panel under a three-line answer looks broken, not calm. Height is
        # measured from the same list that is about to be drawn, so the two
        # can never disagree.
        body_px = sum(line_h // 2 if kind == "gap" else line_h
                      for kind, _text in body)
        wanted = 24 + line_h + 22 + body_px + 58
        card_w = max(240, min(CARD_W, w - 2 * SCREEN_MARGIN))
        card_h = max(200, min(wanted, h - 2 * SCREEN_MARGIN))
        card = pygame.Rect((w - card_w) // 2, (h - card_h) // 2, card_w, card_h)
        panel = pygame.Surface(card.size, pygame.SRCALPHA)
        panel.fill(COL_CARD)
        pygame.draw.rect(panel, COL_BORDER, panel.get_rect(), 2)
        screen.blit(panel, card.topleft)

        inner_w = self.inner_width(w)
        y = card.y + 24
        screen.blit(font.render(self._ellipsize(self.title(), inner_w, font),
                                True, COL_ACCENT), (card.x + PAD_X, y))
        y += font.get_linesize() + 10
        pygame.draw.line(screen, (255, 255, 255, 30),
                         (card.x + PAD_X, y), (card.right - PAD_X, y))
        y += 12

        colors = {"text": COL_FG, "dim": COL_DIM, "ok": COL_OK,
                  "path": COL_OK, "warn": COL_WARN, "item": COL_FG}
        # Never draw into the hint strip: on a short window the summary just
        # stops, which is readable, rather than overprinting the controls the
        # user needs to get out of here.
        body_bottom = card.bottom - 58
        # Body lines are (kind, text) everywhere: the colour a line is drawn
        # in is a property of the wizard's state, not of the string, and the
        # two used to disagree in a way that painted the word "dim" instead
        # of the sentence it belonged to.
        item_index = -1
        for kind, text in body:
            if y + line_h > body_bottom:
                break
            if kind == "gap":
                y += line_h // 2
                continue
            if kind == "item":
                item_index += 1
                if item_index == self.selected:
                    bar = pygame.Rect(card.x + PAD_X - 6, y - 3,
                                      inner_w + 12, line_h)
                    pygame.draw.rect(screen, COL_ACCENT, bar, border_radius=4)
                    fg = COL_SELECT_FG
                else:
                    fg = colors["dim"]
            else:
                fg = colors.get(kind, COL_FG)
            screen.blit(font.render(text, True, fg), (card.x + PAD_X, y))
            y += line_h

        screen.blit(font.render(self._ellipsize(self.hint(), inner_w, font),
                                True, COL_HINT), (card.x + PAD_X, card.bottom - 44))
        marker = "step %d of %d  ·  %s" % (self.step + 1, len(STEPS),
                                          self.step_name())
        screen.blit(font.render(self._ellipsize(marker, inner_w, font),
                                True, COL_HINT), (card.x + PAD_X, card.bottom - 24))
