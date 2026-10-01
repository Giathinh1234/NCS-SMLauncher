"""The media-key tap must never raise out of its callback.

Found from a real crash, not from reading code. The app opened, showed its
window, and then died a few seconds later with SIGABRT. The crash report's
`lastExceptionBacktrace` named the culprit:

    PyObjCErr_ToObjCWithGILState
    m_CGPatternDrawPatternCallback
    processEventTapData

so an AttributeError was escaping the CGEventTap callback, pyobjc was
converting it into an uncaught NSException, and the process aborted.

The cause was `Quartz.kCGEventTapDownOnMediaKey`, which does not exist in
this pyobjc build. The comment in the file already said the constant was
absent and had to be read with getattr() -- and then named it bare in the
very next tuple element, which Python evaluates before `in` ever runs.

The invariant tested here is the one that matters: for ANY event type, the
callback returns normally. An exception escaping this function is not a
missing feature, it is a process abort.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

FAILS = []


def check(cond, label):
    if cond:
        print("   ok  " + label)
    else:
        print("   FAIL " + label)
        FAILS.append(label)


try:
    import Quartz
    HAVE_Q = True
except ImportError:
    HAVE_Q = False
    print("   (no Quartz on this platform -- checking the no-tap path only)")

if HAVE_Q:
    import media_keys
    from media_keys import MediaKeyTap

    print("MEDIA-KEY TAP CALLBACK SAFETY")

    fired = []
    tap = MediaKeyTap(lambda: fired.append("play"),
                      lambda: fired.append("next"),
                      lambda: fired.append("prev"))

    # 1. The constants the callback needs must be read defensively.
    check(not hasattr(Quartz, "kCGEventTapDownOnMediaKey"),
          "kCGEventTapDownOnMediaKey is genuinely absent here "
          "(so this test is exercising the real condition)")
    check(not hasattr(Quartz, "kCGEventOtherKeyDown"),
          "kCGEventOtherKeyDown is genuinely absent here too")

    # 2. Every plausible event type must return without raising. This is the
    #    assertion that failed before: the tuple was built eagerly, so even an
    #    event type that should be ignored blew up while building it.
    # A real CGEvent, not None. The callback calls
    # CGEventGetIntegerValueField(event, ...), and passing None into the Core
    # Graphics API segfaults the interpreter -- my first version of this test
    # died with SIGSEGV for that reason, which says nothing about the bug
    # under test.
    real_event = Quartz.CGEventCreate(None)
    Quartz.CGEventSetType(real_event, Quartz.kCGEventKeyDown)
    Quartz.CGEventSetIntegerValueField(
        real_event, Quartz.kCGKeyboardEventKeycode, 32)   # space
    Quartz.CGEventSetIntegerValueField(
        real_event, Quartz.kCGMouseEventClickState, (18 << 16) | 20)

    types_to_try = [0, 1, 2, 10, 12, 13, 14, 15, 29, 30, 31,
                    Quartz.kCGEventKeyDown,
                    Quartz.kCGEventTapDisabledByTimeout]
    raised = []
    for t in types_to_try:
        try:
            tap._callback(None, t, real_event, None)
        except BaseException as e:      # noqa: BLE001 - that is the point
            raised.append((t, type(e).__name__, str(e)[:60]))
    check(not raised,
          f"callback survives all {len(types_to_try)} event types "
          f"(no exception escapes)")
    for t, name, msg in raised:
        print(f"        event_type={t} raised {name}: {msg}")

    # 3. A callback that cannot fail at all, even if dispatch misbehaves.
    #    Simulate the inner dispatch blowing up.
    original = tap._dispatch

    def boom(event_type, event):
        raise RuntimeError("simulated failure inside dispatch")

    tap._dispatch = boom
    try:
        tap._callback(None, Quartz.kCGEventKeyDown, real_event, None)
        check(True, "a failure inside dispatch is contained, not propagated")
    except BaseException as e:          # noqa: BLE001
        check(False, f"a failure inside dispatch escaped: {type(e).__name__}")
    finally:
        tap._dispatch = original

    # 4. And the ordinary path still works -- containment must not have
    #    turned the tap into a no-op that ignores real media keys.
    # A genuine media keycode must still reach its action, or the containment
    # above has quietly turned the tap into a no-op.
    fired.clear()
    play_event = Quartz.CGEventCreate(None)
    Quartz.CGEventSetType(play_event, Quartz.kCGEventKeyDown)
    Quartz.CGEventSetIntegerValueField(
        play_event, Quartz.kCGKeyboardEventKeycode, media_keys.NX_KEYTYPE_PLAY)
    tap._callback(None, 14, play_event, None)
    check(fired == ["play"],
          f"a real play/pause keycode still fires its action (got {fired})")
else:
    import media_keys
    from media_keys import MediaKeyTap
    tap = MediaKeyTap(lambda: None, lambda: None, lambda: None)
    try:
        tap._callback(None, 14, None, None)
        check(True, "callback is a no-op without Quartz, does not raise")
    except BaseException as e:          # noqa: BLE001
        check(False, f"raised without Quartz: {type(e).__name__}: {e}")

print("\nMEDIA-KEY CALLBACK TESTS PASSED" if not FAILS
      else f"\n{len(FAILS)} FAILURES: {FAILS}")
sys.exit(1 if FAILS else 0)
