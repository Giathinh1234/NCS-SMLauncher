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
        """Regression: the slider was wired to a 0-step nudge and did nothing."""
        self.assertNotIn("onValueChange = { onNudge(0) }", self.screen)
        self.assertIn("onValueChange = { onSet(NcsPrefs.stepLean(lean, 0, it)) }",
                      self.screen)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)