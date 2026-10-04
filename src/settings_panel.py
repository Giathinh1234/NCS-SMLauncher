"""Keyboard-driven settings overlay for NCS-SMLauncher.

Why a keyboard-only overlay rather than a real settings window: the player is
used the way people use a jukebox - leaning back, hands off the trackpad, music
playing. Anything that needs a mouse click is a feature most users will never
discover, and the one setting people actually want (key rebinding) is the one
you can only reach *because* you already own the keyboard. So the whole UI is
one list of rows: Up/Down to move, Enter to activate, Esc to back out. No
hit-testing, no hover state, nothing to learn.

Three failure modes this module is deliberately defensive about:

* Rebinding must never capture a bare modifier. The OS eats most modifier
  presses, and a "play/pause" bound to Shift alone leaves the app permanently
  muteable and inexplicable from this very screen - the place you would go to
  fix it. So a modifier press while capturing is dropped and the capture stays
  armed until a real key arrives.
* Drawing must never clip a row. The panel width follows the window, and every
  label is measured against the real font and ellipsized if it would overflow,
  because a settings list you cannot read is worse than one that scrolls.
* Writes go through config.save_config, which is atomic, so a crash during a
  settings edit can never truncate the user's keymap.

Public surface:
    Row             one line in the list (kind: action | toggle | command | info)
    BOOL_LABELS     friendlier names for the boolean settings
    SettingsPanel   open() / close() / handle_key() / draw()
"""

import os

# Headless is opt-in; see the note in actions.py. This module is imported
# lazily from the launcher's settings action, so an automatic dummy driver
# here would open a real window with no video the first time anyone pressed
# the settings key.
if os.environ.get("NCS_HEADLESS"):
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402  (env vars above must be set before SDL loads)

import config  # noqa: E402
from actions import ACTIONS, Keymap, constant_to_key_name, human_key_name  # noqa: E402

__all__ = ["BOOL_LABELS", "MODIFIER_CONSTANTS", "Row", "SettingsPanel"]

# Booleans in the config become toggle rows automatically; only the display
# name needs help. Anything without an entry here falls back to a
# de-snake-cased version of the key name, so a new boolean in config.py shows
# up in the panel with no change here.
BOOL_LABELS = {
    "show_hints": "Show control hints",
    "easter_eggs": "Easter eggs",
}

# Layout constants. The panel is a fraction of the window rather than a fixed
# size so it tracks a resized window instead of overflowing a small one.
SCREEN_MARGIN = 24
MIN_PANEL_W = 360
MAX_PANEL_W = 900
PANEL_W_RATIO = 0.66
PAD_X = 20
HEADER_H = 56
FOOTER_H = 92
GUTTER = 24
VALUE_MAX_W = 220
ELLIPSIS = "…"

# Modifier and lock keys, matched by substring over pygame's whole K_*
# namespace rather than trusted from a hand-listed set.
#
# actions.py already refuses most of these, but its list is spelled in terms of
# constants that moved between SDL versions (it names LCAPS, which pygame 2.6.1
# spells K_CAPSLOCK), so anything that slipped through would reach
# constant_to_key_name() as a perfectly bindable name. A binding of "play/
# pause" to CapsLock is unrecoverable from inside the panel, because Enter is
# how you get to the row - so the capture refuses these on its own.
#
# Substring, not suffix: K_CAPSLOCK does not end in "CAPS", and an earlier
# suffix version of this silently let CapsLock through. No ordinary key name
# contains any of these words, so the test has no false positives.
_MODIFIER_WORDS = (
    "SHIFT", "CTRL", "ALT", "SUPER", "META", "GUI", "MENU",
    "CAPS", "NUMLOCK", "SCROLLOCK", "MODE",
)


def _modifier_constants():
    """Every pygame key constant that is a modifier or a lock key."""
    values = set()
    for attr in dir(pygame):
        if not attr.startswith("K_"):
            continue
        name = attr[2:].upper()
        if not any(word in name for word in _MODIFIER_WORDS):
            continue
        value = getattr(pygame, attr)
        if isinstance(value, int):
            values.add(value)
    return frozenset(values)


MODIFIER_CONSTANTS = _modifier_constants()

# Palette lifted from draw_torrent_overlay() / draw_ui() so the overlay reads
# as part of the same app rather than a bolted-on dialog.
COL_BG = (8, 10, 16, 242)
COL_BORDER = (0, 230, 190, 150)
COL_ACCENT = (0, 230, 190)
COL_SELECT = (0, 220, 180)
COL_SELECT_FG = (10, 14, 18)
COL_FG = (235, 235, 245)
COL_DIM = (150, 155, 170)
COL_DIM_SEL = (60, 70, 80)
COL_MSG = (255, 200, 90)
COL_HINT = (125, 130, 145)


class Row:
    """One line of the settings list.

    `kind` decides what Enter does:
        "action"  arm a key capture and rebind an ACTIONS entry
        "toggle"  flip a boolean in cfg
        "slider"  adjust a float in cfg with Left/Right
        "command" run a callback
        "info"    read-only text, not selectable
    """

    def __init__(self, label, kind, action=None, value=None, callback=None,
                 help_text="", key=None, minimum=None, maximum=None,
                 step=0.05, format_string="{:.2f}"):
        self.label = label
        self.kind = kind
        self.action = action
        self.value = value
        self.callback = callback
        self.help_text = help_text
        self.key = key            # cfg key, for "toggle" and "slider" rows
        # Slider bounds live on the row rather than being inferred from the
        # value, so 0.0 is a legal middle setting and not "unset".
        self.minimum = minimum
        self.maximum = maximum
        self.step = step
        self.format_string = format_string

    def __repr__(self):
        return "<Row %s %r>" % (self.kind, self.label or self.value)


def _humanize(key):
    """'show_hints' -> 'Show hints', for boolean settings with no nicer name."""
    return key.replace("_", " ").capitalize()


def _ellipsize(text, max_w, font):
    """Trim `text` to `max_w` pixels, ending in a single ellipsis."""
    if max_w <= 0 or not text:
        return ""
    if font.size(text)[0] <= max_w:
        return text
    trimmed = text
    while trimmed and font.size(trimmed + ELLIPSIS)[0] > max_w:
        trimmed = trimmed[:-1]
    return trimmed + ELLIPSIS


def _ellipsize_left(text, max_w, font):
    """Keep the tail of `text` - the informative end of a path."""
    if max_w <= 0 or not text:
        return ""
    if font.size(text)[0] <= max_w:
        return text
    trimmed = text
    while trimmed and font.size(ELLIPSIS + trimmed)[0] > max_w:
        trimmed = trimmed[1:]
    return ELLIPSIS + trimmed


class SettingsPanel:
    """Modal settings list.

    `cfg` is the live config dict and `keymap` the live Keymap; the panel
    mutates both in place and (when `save_path` is given) writes them straight
    back, so it never holds a stale private copy that the launcher cannot see.
    `pick_folder(current)` is the launcher's native folder chooser - injected
    rather than implemented here so this module stays headless-testable and
    free of an OS dialog dependency.
    """

    def __init__(self, cfg=None, keymap=None, pick_folder=None, save_path=None,
                 rect=None, font=None):
        self.cfg = cfg if isinstance(cfg, dict) else config.default_config()
        self.keymap = keymap if keymap is not None else Keymap(self.cfg.get("keymap"))
        self.pick_folder = pick_folder
        self.save_path = save_path
        self.rect = rect            # optional, used when draw() gets no w/h
        self.font = font
        self.message = ""
        self._open = False
        self.capturing = False
        self.selected_index = 0
        self._first_row = 0
        self._pending_move = None   # (plan, dest) awaiting a second Enter
        self.items = []
        self._build()

    # ---- state ---------------------------------------------------------
    def is_open(self):
        return self._open

    def open(self):
        self._open = True
        self.capturing = False
        self._pending_move = None
        # Rebuild on open: the launcher may have changed cfg behind us (a
        # migration on load, say) and a stale value column would lie.
        self._build()
        return self

    def close(self):
        self._open = False
        self.capturing = False
        self._pending_move = None
        self._persist()

    def toggle(self):
        """Flip open/closed. Returns the new state."""
        if self._open:
            self.close()
        else:
            self.open()
        return self._open

    # ---- rows ----------------------------------------------------------
    def _ordered_actions(self):
        """Actions sorted by group, then label, for stable grouping."""
        return sorted(ACTIONS.items(),
                      key=lambda kv: (kv[1]["group"], kv[1]["label"]))

    def _bool_keys(self):
        """Every boolean in the config default, in declaration order."""
        return [key for key, value in config.DEFAULT_CONFIG.items()
                if isinstance(value, bool)]

    def _build(self):
        """(Re)create the row list, keeping the selection on the same row."""
        previous = self.current_action()
        rows = [
            Row("Library folder", "info",
                value=self.cfg.get("library_folder") or "(not set)"),
            Row("Choose library folder…", "command",
                callback=self._cmd_choose_folder,
                help_text="pick the folder the launcher scans"),
            Row("Move library into one folder…", "command",
                callback=self._cmd_move_all,
                help_text="flatten the tree, then repoint the launcher"),
        ]
        if self._bool_keys():
            rows.append(Row("", "info", value="── display ──"))

        # Lean comes before the display toggles: it changes what the user is
        # looking at, so it is the thing they most likely reached for, and
        # unlike a boolean it needs no Enter press -- Left/Right do it live.
        rows.append(Row("Lean visualizer", "slider",
                        key="visualizer_lean",
                        value=config.clamp_lean(
                            self.cfg.get("visualizer_lean", 0.0)),
                        minimum=config.LEAN_MIN, maximum=config.LEAN_MAX,
                        step=0.05, format_string="{:+.2f}",
                        help_text="Left/Right \u00b7 \u2190 full left, 0 centre, "
                                  "\u2192 full right"))
        for key in self._bool_keys():
            rows.append(Row(BOOL_LABELS.get(key, _humanize(key)), "toggle",
                            key=key,
                            value=bool(self.cfg.get(key, config.DEFAULT_CONFIG[key])),
                            help_text="saved to settings.json"))

        rows.append(Row("", "info", value="── key bindings ──"))
        group = None
        for name, meta in self._ordered_actions():
            if meta["group"] != group:
                group = meta["group"]
                rows.append(Row("", "info", value="── %s ──" % group.lower()))
            rows.append(Row(meta["label"], "action", action=name,
                            value=self.keymap.bindings.get(name, ""),
                            help_text=meta["group"]))

        rows.append(Row("", "info", value="── reset ──"))
        rows.append(Row("Reset all keys to defaults", "command",
                        callback=self._cmd_reset_keys))

        self.items = rows
        if previous:
            for index, row in enumerate(rows):
                if row.action == previous:
                    self.selected_index = index
                    break
        self.selected_index = max(0, min(self.selected_index, len(rows) - 1))

    def current_row(self):
        # None on an empty list: _build() asks for the current row to preserve
        # the selection, and it does that before the first rows exist.
        if not self.items:
            return None
        return self.items[min(self.selected_index, len(self.items) - 1)]

    def current_action(self):
        row = self.current_row()
        return row.action if row is not None else None

    def move_selection(self, delta):
        """Move the cursor, wrapping at both ends.

        Wrapping rather than clamping: a 20-row list is scannable, and a list
        that stops dead at the bottom is easy to lose your place in.
        """
        if not self.items:
            return self.selected_index
        self.selected_index = (self.selected_index + delta) % len(self.items)
        return self.selected_index

    def select(self, index):
        self.selected_index = max(0, min(int(index), len(self.items) - 1))
        return self.selected_index

    def select_action(self, action):
        """Put the cursor on `action`'s row. Returns the index, or -1."""
        for index, row in enumerate(self.items):
            if row.action == action:
                return self.select(index)
        return -1

    def select_kind(self, kind):
        """Put the cursor on the first row of `kind`. Returns index, or -1."""
        for index, row in enumerate(self.items):
            if row.kind == kind:
                return self.select(index)
        return -1

    # ---- commands ------------------------------------------------------
    def _cmd_choose_folder(self):
        if not self.pick_folder:
            self.message = "folder picker unavailable"
            return
        picked = self.pick_folder(self.cfg.get("library_folder", ""))
        if not picked:
            self.message = "library folder unchanged"
            return
        self.cfg["library_folder"] = picked
        self.message = "library set to %s" % picked

    def _cmd_move_all(self):
        # Imported lazily: library_ops is only needed for this one row, and
        # keeping it out of module scope means the settings screen has no
        # filesystem cost until the user actually asks for a move.
        import library_ops

        if not self.pick_folder:
            self.message = "folder picker unavailable"
            return
        dest = self.pick_folder(self.cfg.get("library_folder", ""))
        if not dest:
            self.message = "move cancelled"
            self._pending_move = None
            return
        plan = library_ops.plan_move(self.cfg.get("library_folder", ""), dest)
        if not plan:
            self.message = "nothing to move (or unsafe destination)"
            self._pending_move = None
            return
        if self._pending_move is not None and self._pending_move[0] == plan:
            # Second Enter on the identical plan. A bulk move of someone's
            # music is not something to do on a single unconfirmed keypress.
            moved, skipped, failed = library_ops.execute_move(plan)
            self.message = "moved %d, skipped %d, failed %d" % (moved, skipped, failed)
            self._pending_move = None
            if not failed:
                self.cfg["library_folder"] = dest
        else:
            would_move, _skipped, _failed = library_ops.execute_move(plan, dry_run=True)
            self.message = ("will move %d file(s) - press Enter again to confirm"
                            % would_move)
            self._pending_move = (plan, dest)

    def _cmd_reset_keys(self):
        self.keymap.reset_to_defaults()
        self.message = "keys reset to defaults"
        self._build()

    # ---- persistence ---------------------------------------------------
    def _persist(self):
        """Push the keymap into cfg and write it out when a path is set.

        The panel edits the caller's cfg and Keymap in place, so the copy
        here is one-way (keymap -> cfg) and the caller always sees the live
        objects it handed in.
        """
        if not isinstance(self.cfg.get("keymap"), dict):
            self.cfg["keymap"] = {}
        self.cfg["keymap"].update(self.keymap.bindings)
        if self.save_path:
            config.save_config(self.cfg, self.save_path)

    def save(self, path=None):
        """Write cfg (with the current keymap) to `path` or `save_path`."""
        self._persist()
        target = path or self.save_path
        if not target:
            return False
        return config.save_config(self.cfg, target)

    # ---- input ---------------------------------------------------------
    def handle_key(self, event):
        """Consume one key event.

        Returns "close", "rebind", "toggled", or None. The return is how the
        launcher learns something changed - the panel cannot import the
        launcher, so it reports instead of calling.
        """
        if not self._open:
            return None
        key = getattr(event, "key", None)
        if self.capturing:
            return self._capture(key)
        if key == pygame.K_ESCAPE:
            self.close()
            return "close"
        # Up/Down only, deliberately: the same row you are here to fix may
        # have just been bound to 'w' or 'j'.
        if key == pygame.K_UP:
            self.move_selection(-1)
        elif key == pygame.K_DOWN:
            self.move_selection(1)
        elif key == pygame.K_PAGEUP:
            self.move_selection(-8)
        elif key == pygame.K_PAGEDOWN:
            self.move_selection(8)
        elif key == pygame.K_HOME:
            self.select(0)
        elif key == pygame.K_END:
            self.select(len(self.items) - 1)
        elif key in (pygame.K_LEFT, pygame.K_RIGHT):
            return self._adjust(-1 if key == pygame.K_LEFT else 1)
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            return self._activate()
        return None

    def _adjust(self, direction):
        """Move the selected slider one step. Returns "adjusted" or None.

        Consumes the key ONLY for a slider row. A toggle or command row leaves
        Left/Right alone, because those keys are still the player's seek
        bindings and the panel must not eat them.
        """
        row = self.current_row()
        if row is None or row.kind != "slider":
            return None
        try:
            current = float(self.cfg.get(row.key, 0.0))
        except (TypeError, ValueError):
            current = 0.0
        if row.minimum is not None:
            current = max(row.minimum, current)
        if row.maximum is not None:
            current = min(row.maximum, current)
        updated = current + row.step * direction
        # Clamp AFTER stepping, so holding Right parks on the maximum exactly
        # instead of overshooting by a fraction every frame.
        if row.minimum is not None:
            updated = max(row.minimum, updated)
        if row.maximum is not None:
            updated = min(row.maximum, updated)
        # Snap to the step grid so repeated presses cannot accumulate float
        # drift (0.05 * 3 is 0.15000000000000002, which would print as noise).
        updated = round(updated, 4)
        self.cfg[row.key] = updated
        row.value = updated
        # No dirty flag: the panel already reports changes through its return
        # value, and the launcher persists from that.
        return "adjusted"

    def _capture(self, key):
        """Swallow the next real keypress and make it the new binding."""
        if key == pygame.K_ESCAPE:
            # Checked before anything else. Without this, Escape is a perfectly
            # good key name and the cancel gesture would bind Escape to the
            # action - after which there is no way to cancel a capture at all,
            # because the key you would press to cancel is now the thing being
            # changed. Back out without touching the binding.
            self.capturing = False
            self.message = "rebind cancelled"
            return None
        if key in MODIFIER_CONSTANTS:
            # Checked before constant_to_key_name() because that function
            # answers "is this a name I can print?", not "is this a sane
            # trigger?" - see MODIFIER_CONSTANTS. Returning here (rather than
            # disarming) is what stops a stray Shift from either binding
            # itself or silently cancelling a rebind the user is halfway
            # through: the capture simply waits for a real key.
            return None
        name = constant_to_key_name(key)
        if name is None:
            # An event with no usable key. Same reasoning: keep waiting.
            return None
        row = self.current_row()
        if row is None or row.kind != "action":
            self.capturing = False
            return None
        try:
            self.keymap.bind(row.action, name)
        except (KeyError, ValueError) as exc:
            self.capturing = False
            self.message = "cannot bind: %s" % (exc,)
            return None
        self.capturing = False
        row.value = self.keymap.bindings[row.action]
        self._persist()
        clashes = self.keymap.conflicts()
        self.message = "%s = %s" % (row.label, human_key_name(name))
        if clashes:
            self.message += "  (clashes with: " + ", ".join(
                ACTIONS.get(other, {}).get("label", other)
                for _first, other in clashes) + ")"
        return "rebind"

    def _activate(self):
        row = self.current_row()
        if row is None:
            return None
        if row.kind == "action":
            self.capturing = True
            self.message = "press a key for %s (Esc cancels)" % row.label
            return None
        if row.kind == "toggle":
            before = bool(self.cfg.get(row.key, config.DEFAULT_CONFIG.get(row.key, True)))
            self.cfg[row.key] = not before
            row.value = self.cfg[row.key]
            self._persist()
            self.message = "%s: %s" % (row.label, "ON" if row.value else "OFF")
            return "toggled"
        if row.kind == "command":
            row.callback()
            self._persist()
            self._build()
            return None
        return None  # info rows are not selectable

    # ---- layout / measurement -----------------------------------------
    def panel_size(self, w, h):
        """Panel (width, height) for a window of w x h.

        Width is a clamped fraction of the window so the panel follows a
        resize; height hugs the rows when they all fit and is capped by the
        window when they do not.
        """
        font = self.font
        line_h = font.get_linesize() if font else 22
        avail_w = max(200, int(w) - 2 * SCREEN_MARGIN)
        panel_w = min(max(MIN_PANEL_W, int(avail_w * PANEL_W_RATIO)),
                      MAX_PANEL_W, avail_w)
        wanted = HEADER_H + len(self.items) * line_h + FOOTER_H
        panel_h = max(120, min(wanted, int(h) - 2 * SCREEN_MARGIN))
        return panel_w, panel_h

    def panel_rect(self, w, h):
        r = self.rect
        if r is not None and w is None and h is None:
            return pygame.Rect(r)
        ww = int(r.width if r is not None and w is None else w)
        hh = int(r.height if r is not None and h is None else h)
        panel_w, panel_h = self.panel_size(ww, hh)
        return pygame.Rect((ww - panel_w) // 2, (hh - panel_h) // 2,
                           panel_w, panel_h)

    def label_width(self, row, w, font):
        """Pixels the row's label occupies as drawn - always <= label_max."""
        inner_w = self.panel_size(w, 0)[0] - 2 * PAD_X
        return font.size(self.row_label(row, w, font, inner_w))[0]

    def value_width(self, row, w, font):
        inner_w = self.panel_size(w, 0)[0] - 2 * PAD_X
        text = self.row_value(row)
        if not text:
            return 0
        return min(font.size(text)[0], self._value_max(inner_w))

    def row_display(self, row, w, font, capturing=False, selected=False):
        """The (label, value) pair exactly as draw() will blit it.

        Single source of truth on purpose: if the test measures row_value()
        while draw() shortens it somewhere else, the "no label overflows" claim
        is measuring a different string than the one on screen.
        """
        inner_w = self.panel_size(w, 0)[0] - 2 * PAD_X
        value_text = self._value_text(row, capturing, selected)
        label = self.row_label(row, w, font, inner_w, value_text=value_text)
        shown_value = ""
        if value_text:
            shown_value = _ellipsize(value_text, self._value_max(inner_w), font)
            if row.kind == "info" and len(shown_value) != len(value_text):
                # Paths read from the tail; everything else from the head.
                shown_value = _ellipsize_left(shown_value,
                                              self._value_max(inner_w), font)
        return label, shown_value

    def row_width(self, row, w, font):
        """Total pixels the row needs: label + gutter + value."""
        return (self.label_width(row, w, font) + GUTTER
                + self.value_width(row, w, font))

    @staticmethod
    def _value_max(inner_w):
        return max(40, min(VALUE_MAX_W, int(inner_w * 0.34)))

    def row_value(self, row):
        """The value-column text for a row, or '' when it has none."""
        if row.kind == "action":
            return human_key_name(row.value) if row.value else "-"
        if row.kind == "toggle":
            return "ON" if row.value else "OFF"
        if row.kind == "slider":
            return "%s %s" % (self._slider_bar(row), self._slider_num(row))
        if row.kind == "command":
            return ""
        return str(row.value or "")

    @staticmethod
    def _slider_num(row):
        try:
            return row.format_string.format(float(row.value or 0.0))
        except (TypeError, ValueError):
            return row.format_string.format(0.0)

    @staticmethod
    def _slider_bar(row, cells=10):
        """A 10-cell bar showing where the value sits between its bounds.

        Unicode blocks rather than ascii so it is legible in the default font.
        The centre cell is the zero position, which is what makes it obvious
        at a glance whether the ball is left, right, or centred.
        """
        try:
            value = float(row.value or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        low = row.minimum if row.minimum is not None else 0.0
        high = row.maximum if row.maximum is not None else 1.0
        span = (high - low) or 1.0
        fraction = max(0.0, min(1.0, (value - low) / span))
        filled = int(round(fraction * cells))
        filled = max(1, min(cells, filled))
        return "\u2501" * filled + "\u2508" * (cells - filled)

    def _value_text(self, row, capturing=False, selected=False):
        """The value-column text for a row, as drawn in this state.

        Shared by the label budget and draw() on purpose: while capturing, the
        value is "<press a key>" rather than the binding, and it is much wider.
        Budgeting the label against the *normal* width while drawing the wide
        one is how the two columns end up overlapping on a narrow window.
        """
        if capturing and selected and row.kind == "action":
            return "<press a key>"
        return self.row_value(row)

    def row_label(self, row, w, font, inner_w=None, value_text=None):
        """The label as it will be drawn, ellipsized to fit the label column."""
        if not row.label:
            return ""
        if inner_w is None:
            inner_w = self.panel_size(w, 0)[0] - 2 * PAD_X
        if value_text is None:
            value_text = self._value_text(row)
        budget = inner_w
        if value_text:
            budget -= min(font.size(value_text)[0], self._value_max(inner_w))
            budget -= GUTTER
        return _ellipsize(row.label, budget, font)

    def visible_range(self, w, h, font):
        """(first, last) row indices drawn at this size, so the cursor and its
        neighbours are always on screen."""
        line_h = font.get_linesize()
        _panel_w, panel_h = self.panel_size(w, h)
        max_rows = max(1, (panel_h - HEADER_H - FOOTER_H) // line_h)
        first = max(0, min(self.selected_index - max_rows // 2,
                           max(0, len(self.items) - max_rows)))
        self._first_row = first
        return first, min(len(self.items), first + max_rows)

    # ---- drawing -------------------------------------------------------
    def draw(self, screen, font=None, w=None, h=None):
        """Paint the overlay. `w`/`h` default to the window or stored rect."""
        if not self._open:
            return
        font = font or self.font
        if font is None:
            return
        if w is None or h is None:
            surface = screen.get_size()
            w = surface[0] if w is None else w
            h = surface[1] if h is None else h
        r = self.panel_rect(w, h)

        # Dim the launcher behind the panel: the same full-window treatment
        # draw_video_overlay() uses, so the overlay never competes with the
        # library list underneath it.
        dim = pygame.Surface((int(w), int(h)), pygame.SRCALPHA)
        dim.fill((2, 3, 6, 150))
        screen.blit(dim, (0, 0))

        panel = pygame.Surface(r.size, pygame.SRCALPHA)
        panel.fill(COL_BG)
        pygame.draw.rect(panel, COL_BORDER, panel.get_rect(), 2)
        screen.blit(panel, r.topleft)

        head = font.render("SETTINGS", True, COL_ACCENT)
        screen.blit(head, (r.x + PAD_X, r.y + 16))
        tip = font.render("Up/Down move  ·  Enter select  ·  Esc close",
                          True, COL_DIM)
        screen.blit(tip, (r.right - PAD_X - tip.get_width(), r.y + 18))
        pygame.draw.line(screen, (255, 255, 255, 30),
                         (r.x + PAD_X, r.y + 44), (r.right - PAD_X, r.y + 44))

        line_h = font.get_linesize()
        first, last = self.visible_range(w, h, font)
        for index in range(first, last):
            row = self.items[index]
            y = r.y + HEADER_H + (index - first) * line_h
            selected = index == self.selected_index
            if selected:
                sel_rect = pygame.Rect(r.x + PAD_X - 6, y - 3,
                                       r.width - 2 * PAD_X + 12, line_h)
                pygame.draw.rect(screen, COL_SELECT, sel_rect, border_radius=4)
            fg = COL_SELECT_FG if selected else COL_FG
            dim = COL_DIM_SEL if selected else COL_DIM
            label, value_text = self.row_display(
                row, w, font, capturing=self.capturing, selected=selected)
            screen.blit(font.render(label, True, fg), (r.x + PAD_X, y))
            if value_text:
                surf = font.render(value_text, True,
                                   fg if row.kind != "info" else dim)
                screen.blit(surf, (r.right - PAD_X - surf.get_width(), y))

        if self.message:
            msg = font.render(_ellipsize(self.message, r.width - 2 * PAD_X, font),
                              True, COL_MSG)
            screen.blit(msg, (r.x + PAD_X, r.bottom - 60))
        hint = font.render("Saved to ~/.ncs-smlauncher/settings.json",
                           True, COL_HINT)
        screen.blit(hint, (r.x + PAD_X, r.bottom - 34))
