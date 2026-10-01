"""
Media-key support for HashPlay on macOS.

Captures the MacBook keyboard's media keys (rewind ⏪ F7, play/pause ⏯ F8,
fast-forward ⏩ F9) system-wide via a Quartz event tap and translates them
into actions on a callback dict.

Falls back gracefully: if Quartz isn't available or Accessibility permission
is missing, the app keeps working with in-window keys only.

macOS will prompt once for Accessibility (System Settings → Privacy & Security
→ Accessibility) the first time this runs — needed to intercept media keys.
"""

import threading

try:
    import Quartz
    from Foundation import NSMakeRect
    HAVE_QUARTZ = True
except ImportError:
    HAVE_QUARTZ = False

# NX_KEYTYPE values from IOKit hidsystem/ev_keymap.h
NX_KEYTYPE_PLAY = 20       # play/pause ⏯  (F8)
NX_KEYTYPE_NEXT = 19       # fast  ⏩       (F9)
NX_KEYTYPE_PREVIOUS = 18   # rewind ⏪     (F7)
NX_KEYTYPE_FAST = 101      # alternate fast-forward code
NX_KEYTYPE_REWIND = 100    # alternate rewind code


class MediaKeyTap:
    """
    Listens globally for media key presses and calls:

        on_play_pause()   when ⏯ pressed
        on_next()         when ⏩ pressed
        on_previous()     when ⏪ pressed

    The app decides semantics:
      - paused:            next/previous switch track
      - playing:           next/previous seek ±5 s
    """

    def __init__(self, on_play_pause, on_next, on_previous):
        self.callbacks = {
            NX_KEYTYPE_PLAY: on_play_pause,
            NX_KEYTYPE_NEXT: on_next,
            NX_KEYTYPE_FAST: on_next,
            NX_KEYTYPE_PREVIOUS: on_previous,
            NX_KEYTYPE_REWIND: on_previous,
        }
        self.tap = None
        self.running = False
        self.available = HAVE_QUARTZ

    # ---- internals -------------------------------------------------------

    def _callback(self, proxy, event_type, event, refcon):
        # Quartz is only bound when the import at the top of this module
        # succeeded. A tap can only exist if it did, so this is belt and
        # braces -- but referencing an unbound Quartz here would be an
        # UnboundLocalError, and an exception escaping this callback aborts
        # the process. Never let anything propagate out of here.
        if not HAVE_QUARTZ:
            return event
        try:
            return self._dispatch(event_type, event)
        except Exception:
            # A media key is not worth crashing the player over. The tap
            # swallows the event and the app carries on.
            return event

    def _dispatch(self, event_type, event):
        if event_type == Quartz.kCGEventTapDisabledByTimeout:
            # macOS re-enables us after a timeout; restart the tap.
            # Guard on self.tap: CGEventTapEnable() is a C function that
            # dereferences the port without a NULL check, so calling it before
            # start() succeeded segfaults the interpreter outright. Reached
            # here whenever a timeout event arrives for a tap that was never
            # established.
            if getattr(self, "tap", None) is not None:
                Quartz.CGEventTapEnable(self.tap, True)
            return None
        # These two constants are ABSENT from some pyobjc builds (verified on
        # this machine: both hasattr() checks are False). Naming them directly
        # raised AttributeError on every single event, and an exception
        # escaping a CGEventTap callback is converted by pyobjc into an
        # uncaught NSException -- which aborts the whole app. The app died a
        # few seconds after opening, with SIGABRT and this frame on top:
        #     PyObjCErr_ToObjCWithGILState
        #     m_CGPatternDrawPatternCallback
        #     processEventTapData
        # The comment above used to claim this was handled. It was not: the
        # getattr() covered kCGEventOtherKeyDown but the very next element was
        # a bare Quartz.kCGEventTapDownOnMediaKey, and the tuple is evaluated
        # before `in` ever runs.
        other_key_down = getattr(Quartz, "kCGEventOtherKeyDown", 14)
        media_key_down = getattr(Quartz, "kCGEventTapDownOnMediaKey", 14)
        if event_type not in (14, other_key_down, media_key_down,
                              Quartz.kCGEventKeyDown):
            return event

        keycode = Quartz.CGEventGetIntegerValueField(event,
                                                     Quartz.kCGKeyboardEventKeycode)
        # media keys arrive as NSSystemDefined events; data1 holds the key type
        data1 = int(Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGMouseEventClickState)) or 0

        # For NSSystemDefined (type 14): bits 16-31 of data1 = subtype/keycode.
        # But via CGEventTap, media keys surface with keycode field directly.
        action = self.callbacks.get(keycode)
        if action is None:
            # try extracting from data1 high bits (NSSystemDefined layout)
            key = (data1 >> 16) & 0xFFFF
            action = self.callbacks.get(key)
        if action is not None:
            # swallow the event so iTunes/Music doesn't also react
            action()
            return None
        return event

    def start(self):
        if not HAVE_QUARTZ or self.running:
            return False
        try:
            # kCGEventOtherKeyDown does not exist in this pyobjc build
            # (verified: hasattr(Quartz, 'kCGEventOtherKeyDown') is False), so
            # naming it raised AttributeError on EVERY call to start(). The
            # broad `except` below turned that into a silent False, which is
            # why media keys looked like they had never worked rather than
            # crashing. The callback already special-cases event type 14
            # (NSSystemDefined) by hand, so the mask does not need it.
            other_key_down = getattr(Quartz, "kCGEventOtherKeyDown", 14)
            mask = (Quartz.CGEventMaskBit(14) |          # NSSystemDefined
                    Quartz.CGEventMaskBit(other_key_down))
            self.tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,   # must be "default" to filter
                mask,
                self._callback,
                None)
            if self.tap is None:
                # no accessibility permission yet
                return False
            source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
            loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(self.tap, True)

            def run():
                self.running = True
                Quartz.CFRunLoopRun()

            t = threading.Thread(target=run, daemon=True)
            t.start()
            return True
        except Exception as exc:
            # This used to be a bare `return False`, which is how a missing
            # Quartz constant and a genuine missing-permission failure looked
            # identical. Media keys would simply never work and the only clue
            # was that nothing happened when you pressed them.
            print(f"media keys unavailable: {exc}")
            return False


def open_accessibility_settings():
    """Open System Settings → Accessibility so user can grant permission."""
    import subprocess as sp
    sp.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])
