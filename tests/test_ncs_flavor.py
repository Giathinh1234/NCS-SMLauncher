"""Static contracts for the `ncs` flavor -- the from-scratch Android player.

These are not unit tests. They assert that the flavor keeps the promises that
are easy to break silently and expensive to notice:

  * `ncs` exists, has its OWN applicationId, and therefore coexists with the
    existing full and lite builds rather than replacing one of them;
  * it keeps the torrent engine, because it is the desktop-`full` equivalent;
  * it selects the new player screen by flag, not by source-set override
    (Android cannot have two classes of the same name in one variant);
  * the lean behaves like the desktop's: a clamped fraction, snapped to a
    grid, that a dragged slider actually writes;
  * the build script knows about all three flavors and checks each package.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRADLE = ROOT / "android" / "app" / "build.gradle.kts"
MAIN = ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "giathinh" / "hashplay"
SCRIPT = ROOT / "scripts" / "build_android_apks.sh"


def strip_comments(text):
    """Remove // and /* */ comments.

    Without this the file's own explanatory comments match the assertions --
    an earlier version of this suite was defeated by exactly that.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


class NcsFlavorExists(unittest.TestCase):
    def setUp(self):
        self.gradle = strip_comments(GRADLE.read_text())

    def test_ncs_flavor_is_declared(self):
        self.assertIn('create("ncs")', self.gradle)

    def test_ncs_has_its_own_application_id(self):
        block = self.gradle.split('create("ncs")')[1].split("}")[0]
        self.assertIn('applicationIdSuffix = ".ncs"', block)

    def test_ncs_keeps_the_torrent_engine(self):
        block = self.gradle.split('create("ncs")')[1].split("}")[0]
        self.assertIn('"HAS_TORRENTS", "true"', block)

    def test_ncs_borrows_the_full_source_set(self):
        """A flavor only sees its own dir by default; ncs needs full's engine."""
        self.assertIn('getByName("ncs")', self.gradle)
        self.assertIn('java.srcDir("src/full/java")', self.gradle)

    def test_existing_flavors_still_use_the_old_screen(self):
        for flavor in ("full", "lite"):
            block = self.gradle.split('create("%s")' % flavor)[1].split("}")[0]
            self.assertIn('"NCS_UI", "false"', block,
                          "%s must keep the original player screen" % flavor)

    def test_ncs_selects_the_new_screen(self):
        block = self.gradle.split('create("ncs")')[1].split("}")[0]
        self.assertIn('"NCS_UI", "true"', block)


class NcsScreenRouting(unittest.TestCase):
    def setUp(self):
        self.activity = (MAIN / "MainActivity.kt").read_text()

    def test_routing_is_by_flag(self):
        self.assertIn("BuildConfig.NCS_UI", self.activity)

    def test_both_screens_are_referenced(self):
        self.assertIn("NcsPlayerScreen(", self.activity)
        self.assertIn("PlayerScreen(", self.activity)


class LeanBehaviour(unittest.TestCase):
    def setUp(self):
        self.prefs = (MAIN / "NcsPrefs.kt").read_text()
        self.screen = (MAIN / "NcsPlayerScreen.kt").read_text()

    def test_lean_is_a_clamped_fraction(self):
        self.assertIn("LEAN_MIN = -1f", self.prefs)
        self.assertIn("LEAN_MAX = 1f", self.prefs)
        self.assertIn("coerceIn(LEAN_MIN, LEAN_MAX)", self.prefs)

    def test_lean_survives_a_nan(self):
        """A NaN would otherwise make coerceIn throw or silently propagate."""
        self.assertIn("isNaN()", self.prefs)

    def test_lean_snaps_to_a_grid(self):
        """Repeated addition in Float drifts; values must not."""
        self.assertIn("roundToStep", self.prefs)

    def test_dragging_the_slider_writes_the_value(self):
        """Regression: the slider was wired to a 0-step nudge and did nothing.

        The lean slider moved from the player surface into the shared settings
        overlay, so this asserts on the file that owns it now. The old wiring
        must stay gone from the player screen too.
        """
        overlay = (MAIN / "SettingsOverlay.kt").read_text()
        self.assertNotIn("onValueChange = { onNudge(0) }", self.screen)
        self.assertNotIn("onValueChange = { onNudge(0) }", overlay)
        # It must write the dragged value, not step by zero.
        self.assertIn("onValueChange = {", overlay)
        self.assertIn("HashSettings.stepLean(lean, 0, it)", overlay)

    def test_renderer_accepts_a_lean(self):
        sphere = (MAIN / "NcsSphere.kt").read_text()
        self.assertIn("lean: Float = 0f", sphere)
        self.assertIn("coerceIn(-1f, 1f)", sphere)

    def test_renderer_clamps_against_the_real_radius(self):
        """Clamping against half the window instead clipped the sphere."""
        sphere = (MAIN / "NcsSphere.kt").read_text()
        self.assertIn("val travel = (width / 2f - radius)", sphere)

    def test_lean_does_not_push_the_ball_off_screen(self):
        sphere = (MAIN / "NcsSphere.kt").read_text()
        self.assertIn("coerceAtLeast(0f)", sphere)


class KeymapPort(unittest.TestCase):
    def setUp(self):
        self.prefs = (MAIN / "NcsPrefs.kt").read_text()

    def test_actions_are_declared(self):
        for action in ("A_PREV", "A_NEXT", "A_PLAY_PAUSE", "A_CYCLE_VIZ", "A_QUIT"):
            self.assertIn(action, self.prefs)

    def test_bindings_persist(self):
        self.assertIn("SharedPreferences", self.prefs)
        self.assertIn("fun resetBindings", self.prefs)

    def test_reserved_keys_are_refused(self):
        """Binding a key the OS eats first creates a binding that never fires."""
        self.assertIn("val RESERVED", self.prefs)
        self.assertIn("if (keyCode in RESERVED) return", self.prefs)

    def test_conflicts_are_reported(self):
        self.assertIn("fun conflicts", self.prefs)


class BuildScriptKnowsAboutNcs(unittest.TestCase):
    def setUp(self):
        self.text = SCRIPT.read_text()

    def test_syntax_is_checked_by_the_repo(self):
        self.assertTrue((ROOT / "tests" / "test_android_build.py").exists())

    def test_ncs_is_in_the_flavor_loop(self):
        self.assertIn("for v in full lite ncs; do", self.text)

    def test_ncs_task_is_available(self):
        self.assertIn("assembleNcsRelease", self.text)

    def test_each_apk_package_is_verified(self):
        """Three flavors that share a package are one app installed thrice."""
        self.assertIn("WANT_PKG", self.text)
        self.assertIn("com.giathinh.hashplay.ncs", self.text)

    def test_gradle_failures_are_not_swallowed(self):
        self.assertNotIn("|| true", self.text.split("assemble")[0])

class GamepadSupport(unittest.TestCase):
    """One table has to cover Xbox, DualSense and Switch Pro.

    That works because Android HID collapses all three onto the same
    KEYCODE_BUTTON_* / DPAD codes. The test pins that mapping so a change to
    one pad cannot quietly break the other two.
    """

    def setUp(self):
        self.pad = (MAIN / "Gamepad.kt").read_text()
        self.activity = (MAIN / "MainActivity.kt").read_text()

    def test_face_button_is_play_pause(self):
        """KEYCODE_BUTTON_A is the bottom face button on all three pads."""
        self.assertIn("KeyEvent.KEYCODE_BUTTON_A", self.pad)

    def test_dpad_is_navigation(self):
        for code in ("KEYCODE_DPAD_UP", "KEYCODE_DPAD_DOWN",
                     "KEYCODE_DPAD_LEFT", "KEYCODE_DPAD_RIGHT"):
            self.assertIn(code, self.pad)

    def test_triggers_are_read_from_axes(self):
        """L2/R2 are analog on all three; treating them as buttons is a no-op."""
        self.assertIn("MotionEvent.AXIS_LTRIGGER", self.pad)
        self.assertIn("MotionEvent.AXIS_RTRIGGER", self.pad)
        self.assertIn("TRIGGER_DEADZONE", self.pad)

    def test_triggers_have_a_digital_fallback(self):
        self.assertIn("KEYCODE_BUTTON_L2", self.pad)
        self.assertIn("KEYCODE_BUTTON_R2", self.pad)

    def test_pads_are_named_for_the_settings_screen(self):
        for name in ("DualSense", "Xbox", "Switch Pro"):
            self.assertIn(name, self.pad)

    def test_stick_has_a_deadzone(self):
        """A low stick threshold scrolls the library while you hold a direction."""
        self.assertIn("STICK_DEADZONE", self.pad)

    def test_generic_motion_is_dispatched_from_the_activity(self):
        """The only door: Compose pointer input cannot see pad axes here."""
        self.assertIn("dispatchGenericMotionEvent", self.activity)
        self.assertIn("Gamepad.fromGenericMotion", self.activity)

    def test_unclaimed_motion_is_not_swallowed(self):
        self.assertIn("super.dispatchGenericMotionEvent(ev)", self.activity)

    def test_the_sink_is_cleared_on_dispose(self):
        """A stale sink calls into a screen that is gone."""
        listener = (MAIN / "PadListener.kt").read_text()
        self.assertIn("onDispose { Gamepad.sink = null }", listener)


class ThemeModes(unittest.TestCase):
    def setUp(self):
        self.settings = (MAIN / "HashSettings.kt").read_text()
        self.overlay = (MAIN / "SettingsOverlay.kt").read_text()

    def test_both_themes_exist(self):
        self.assertIn("enum class Theme { TOUCH, KEYBOARD", self.settings)

    def test_theme_is_persisted(self):
        self.assertIn("fun setTheme", self.settings)

    def test_theme_bumps_the_control_size(self):
        """A theme that only changes colour is not the layout switch asked for.

        KEYBOARD is never named literally -- it is the `else` branch, the
        compact default -- so this asserts the TOUCH comparison and that it
        actually drives a measurement.
        """
        import re
        for screen in ("PlayerScreen.kt", "NcsPlayerScreen.kt"):
            src = (MAIN / screen).read_text()
            self.assertIn("HashSettings.Theme.TOUCH", src, screen)
            self.assertIn("= if (touch)", src, screen)

    def test_theme_defaults_to_keyboard(self):
        """The mode that still works with a keyboard or pad attached."""
        self.assertIn("?: KEYBOARD", self.settings)


class SettingsReachableEverywhere(unittest.TestCase):
    def setUp(self):
        self.overlay = (MAIN / "SettingsOverlay.kt").read_text()

    def test_the_overlay_is_shared(self):
        """One implementation, or the flavors drift apart."""
        self.assertIn("fun SettingsOverlay(", self.overlay)

    def test_every_screen_can_open_it(self):
        for screen in ("PlayerScreen.kt", "NcsPlayerScreen.kt"):
            src = (MAIN / screen).read_text()
            self.assertIn("SettingsOverlay(", screen and src, screen)

    def test_lean_and_gain_are_in_the_overlay(self):
        self.assertIn("LEAN VISUALIZER", self.overlay)
        self.assertIn("GAIN", self.overlay)
        self.assertIn("HashSettings.stepLean", self.overlay)
        self.assertIn("HashSettings.stepGain", self.overlay)

    def test_lean_and_gain_left_the_player_surface(self):
        """They were on the main screen before and crowded the player."""
        for screen in ("PlayerScreen.kt", "NcsPlayerScreen.kt"):
            src = (MAIN / screen).read_text()
            self.assertNotIn("fun LeanControl(", src, screen)
            self.assertNotIn("fun GainControl(", src, screen)

    def test_gain_is_written_through_to_the_player(self):
        """The service owns the volume; the slider must not assume it."""
        self.assertIn("onGain(gain)", self.overlay)



class PixelTypographyEverywhere(unittest.TestCase):
    """Every piece of text on every flavor renders in a pixel face.

    Two things can put system type back: a screen that names a font explicitly,
    and Material's default typography for a screen that names none. Both are
    pinned here, because a silent fallback is invisible until someone notices
    one label in the wrong typeface.
    """

    def setUp(self):
        self.main = MAIN
        self.type = (MAIN / "PixelType.kt").read_text()
        self.theme = (MAIN / "Theme.kt").read_text()

    def test_both_fonts_are_bundled(self):
        res = ROOT / "android" / "app" / "src" / "main" / "res" / "font"
        for f in ("silkscreen_regular.ttf", "silkscreen_bold.ttf", "pixelify_sans.ttf"):
            self.assertTrue((res / f).exists(), f)
            self.assertGreater((res / f).stat().st_size, 5000, f)

    def test_fonts_are_real_ttf_not_placeholders(self):
        """A 14-byte '404: Not Found' saved as a .ttf builds and renders nothing."""
        res = ROOT / "android" / "app" / "src" / "main" / "res" / "font"
        for f in res.glob("*.ttf"):
            head = f.read_bytes()[:4]
            self.assertIn(head, (b"\x00\x01\x00\x00", b"true", b"OTTO"),
                          "%s is not a real font" % f.name)

    def test_licences_travel_with_the_fonts(self):
        """SIL OFL requires it, and the res/font dir will not accept .txt."""
        lic = ROOT / "android" / "licenses"
        names = {p.name for p in lic.glob("OFL-*LICENSE.txt")}
        self.assertEqual(len(names), 2, names)
        for text in lic.glob("OFL-*LICENSE.txt"):
            self.assertIn("SIL OPEN FONT LICENSE", text.read_text().upper())

    def test_no_screen_asks_for_the_monospace_system_font(self):
        for kt in self.main.glob("*.kt"):
            self.assertNotIn("FontFamily.Monospace", kt.read_text(), kt.name)

    def test_material_typography_is_overridden(self):
        """This is what reaches PlayerScreen and SetupScreen, which name no font."""
        self.assertIn("typography = PixelTypography", self.theme)
        self.assertIn("MaterialTheme(", self.theme)

    def test_line_height_is_loosened_for_descenders(self):
        """Pixel faces clip g/y/p at Material's default leading."""
        self.assertIn("lineHeight", self.theme)

    def test_two_faces_with_distinct_jobs(self):
        self.assertIn("val Display", self.type)
        self.assertIn("val Body", self.type)

    def test_track_titles_keep_their_case(self):
        """Silkscreen is capitals-only, so a title in it loses its word shapes."""
        screen = (MAIN / "NcsPlayerScreen.kt").read_text()
        idx = screen.find("track.title.take(")
        self.assertGreater(idx, -1)
        window = screen[idx:idx + 220]
        self.assertIn("PixelType.Body", window)

    def test_the_wordmark_uses_the_display_face(self):
        screen = (MAIN / "NcsPlayerScreen.kt").read_text()
        idx = screen.find('"NCS", color =')
        self.assertGreater(idx, -1)
        self.assertIn("PixelType.Display", screen[idx:idx + 200])


if __name__ == "__main__":
    unittest.main(verbosity=2)
