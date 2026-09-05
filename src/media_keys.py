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
        if event_type == Quartz.kCGEventTapDisabledByTimeout:
            # macOS re-enables us after timeout; restart tap
            Quartz.CGEventTapEnable(self.tap, True)
            return None
        if event_type != Quartz.kCGEventOtherKeyDown and \
           event_type != Quartz.kCGEventKeyDown and \
           event_type != Quartz.kCGEventTapDownOnMediaKey and \
           event_type != 14:   # NSSystemDefined / keyDown equivalent
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
            mask = (Quartz.CGEventMaskBit(14) |          # NSSystemDefined
                    Quartz.CGEventMaskBit(Quartz.kCGEventOtherKeyDown))
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
        except Exception:
            return False


def open_accessibility_settings():
    """Open System Settings → Accessibility so user can grant permission."""
    import subprocess as sp
    sp.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])
