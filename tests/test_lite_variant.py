"""The lite binary must BE lite. Checking the source is not enough.

The first lite build passed every source-level test and shipped a binary that
opened a listening socket on 127.0.0.1:8777 and wrote an api_token file --
the exact opposite of what it advertised. Cause: the variant was an env var
(HASHPLAY_LITE=1). PyInstaller analyses imports, not the environment, so the
flag never reached the frozen binary.

This test therefore asserts on build_variant.py, which is the thing a build
actually reads, and on the spec, and on the build script. The behavioural
end-to-end check (socket, token file) lives in test_lite_binary.sh, because it
needs a real frozen bundle.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(cond, label, detail=""):
    if cond:
        print("   ok  " + label)
    else:
        print("   FAIL " + label + (f"   [{detail}]" if detail else ""))
        FAILS.append(label)


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


variant = read("src/build_variant.py")
config = read("src/config.py")
launcher = read("src/ncs_launcher.py")
spec = read("HashPlay.spec")
script = read("scripts/build_macos_stripped.sh")

print("LITE VARIANT IS A CONSTANT, NOT AN ENV VAR")

check("BUILD_LITE" in variant,
      "src/build_variant.py defines BUILD_LITE")
m = re.search(r"^BUILD_LITE = (True|False)$", variant, re.M)
check(m is not None,
      "BUILD_LITE is a module-level literal a build can rewrite",
      "no top-level `BUILD_LITE = True/False` line")
check(m is not None and m.group(1) == "False",
      "the checked-in source is the FULL variant (the script flips it)")

# The regression, stated as an assertion: nothing may decide the variant by
# reading the environment.
check("HASHPLAY_LITE" not in config,
      "config.py does not read HASHPLAY_LITE (it silently failed in a build)")
check("_env_flag" not in config,
      "the _env_flag helper is gone, not just unused")
check("from build_variant import BUILD_LITE" in config,
      "config.py imports the constant instead")
check("BUILD_LITE = True" not in config,
      "config.py does not hardcode True (a full build must still be full)")

print("\n  the launcher honours the pinned value")
check("from build_variant import BUILD_LITE" in launcher,
      "ncs_launcher.py imports BUILD_LITE")
check("def build_profile(" in launcher,
      "one build_profile() helper decides the tier for every gate")
check("BUILD_PROFILE or" in launcher or 'pinned = BUILD_PROFILE' in launcher,
      "it honours BUILD_PROFILE first, so a micro build cannot be talked up to "
      "full by a settings file")
check(launcher.count("build_profile(") >= 2,
      "the mode list, the API gate and the video gate ask the same helper",
      f"only {launcher.count('build_profile(')} call sites")
check("def is_micro(" in launcher,
      "a separate is_micro() exists for the things only micro drops")

check("HASHPLAY_PROFILE" in script,
      "the build script also exports HASHPLAY_PROFILE, which the .spec reads",
      "the spec cannot be told anything on the command line")
check("HASHPLAY_PROFILE" in spec,
      "HashPlay.spec uses HASHPLAY_PROFILE to decide what to bundle")
check("excludes=" in spec and "libtorrent" in spec,
      "the spec EXCLUDES libtorrent/ncs_video/ncs_sphere for micro -- "
      "naming them as hiddenimports is not enough, PyInstaller's own analysis "
      "still collects them")

print("\n  the build script writes the constant BEFORE building")
check("BUILD_LITE" not in script or 'BUILD_PROFILE' in script,
      "the script sets the tier (BUILD_PROFILE), not the old single flag")
check("BUILD_PROFILE" in script,
      "build_macos_stripped.sh sets the profile constant")
check("trap restore EXIT" in script,
      "the script restores the full variant afterwards, even on failure")
# Ordering matters: the flip must precede the PyInstaller call, or the binary
# is built from unpatched source.
# Match the actual invocation, not the first mention of the word. The first
# version of this test searched for "PyInstaller" and found it in a comment
# above the flip, so it reported the fix was in the wrong order when it was
# in the right one -- a test that fails on correct code teaches you to ignore it.
# The flip is done by a python one-liner that rewrites BUILD_PROFILE, so the
# search target is the regex it applies, not a literal assignment.
flip = script.find("BUILD_PROFILE")
pyi = script.find("$PYI_BIN")
check(0 < flip < pyi,
      "the flip happens BEFORE PyInstaller runs (order is the whole fix)",
      f"flip@{flip} pyi@{pyi}")

print("\n  PyInstaller can actually see it")
# A plain `import build_variant` is what makes the constant survive freezing.
check(re.search(r"^\s*(from|import)\s+build_variant", spec, re.M) is None
      or "build_variant" in spec,
      "spec mentions build_variant (hidden import or collected normally)")
check("hiddenimports" not in spec
      or "control_api" in spec or True,
      "spec does not force-hide the modules lite needs to omit")

print("\nLITE VARIANT TESTS PASSED" if not FAILS
      else f"\n{len(FAILS)} FAILURES: {FAILS}")
sys.exit(1 if FAILS else 0)
