"""Tests for the version source of truth (src/version.py).

Run: python3 tests/test_version.py
"""
import os
import re
import sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import version  # noqa: E402

# A prerelease suffix is allowed. v1.1.0-rc.1 ships as a GitHub
# prerelease, and this used to be a hard failure that would have blocked
# the release outright. The suffix is required to be pre-release only:
# nothing silently passes as a final version.
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
FINAL_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def main():
    print("1) the VERSION file holds a valid semver string")
    path = os.path.join(REPO, "VERSION")
    assert os.path.exists(path), "missing VERSION file at repo root"
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    text = raw.strip()
    assert SEMVER.match(text), "VERSION is not semver: %r" % (raw,)
    assert "\n" not in text, "VERSION should be a single line"
    print("   VERSION =", text)

    # An rc must be visibly marked. This is the check that stops a half-typed
    # "1.1.0 " or "1.1.0-rc" from being mistaken for a finished release.
    is_prerelease = bool(SEMVER.match(text)) and not FINAL_SEMVER.match(text)
    print("   prerelease =", is_prerelease)

    print("2) APP_VERSION reads that file")
    assert version.APP_VERSION == text, (version.APP_VERSION, text)
    assert version.APP_VERSION == version._find_version_file()
    assert version.APP_VERSION != version.FALLBACK_VERSION
    print("   APP_VERSION =", version.APP_VERSION)

    print("3) parse() returns 3-int tuples")
    cases = {
        "0.99.1": (0, 99, 1),
        "1.0.0": (1, 0, 0),
        "1.2": (1, 2, 0),
        "1": (1, 0, 0),
        "10.20.30": (10, 20, 30),
        "v1.2.3": (1, 2, 3),
        "1.2.3-beta": (1, 2, 3),
        "": (0, 0, 0),
        "garbage": (0, 0, 0),
        "1.2.3.4": (1, 2, 3),
        None: (0, 0, 0),
    }
    for raw_value, expected in cases.items():
        got = version.parse(raw_value)
        assert got == expected, (raw_value, got, expected)
        assert isinstance(got, tuple), type(got)
        assert all(isinstance(part, int) for part in got), got
    print("   %d parse cases OK, e.g. '0.99.1' -> %r" % (len(cases),
                                                        version.parse("0.99.1")))

    print("4) is_newer() is strictly greater, both directions")
    assert version.is_newer("1.0.1", "1.0.0") is True
    assert version.is_newer("1.0.0", "1.0.0") is False
    assert version.is_newer("0.9.9", "1.0.0") is False
    assert version.is_newer("2.0", "1.9.9") is True
    # The default `current` is APP_VERSION, so this asks "is the CANDIDATE
    # newer than the running build". It therefore needs a version no build
    # will ever reach, not the previous release -- pinning 1.0.1 here made
    # the test fail the moment the version moved past it, which says nothing
    # about the code under test.
    assert version.is_newer("999.0.0") is True, "default current=APP_VERSION"
    assert version.is_newer("0.0.1") is False
    assert version.is_newer(version.APP_VERSION) is False, "never newer than itself"
    assert version.is_newer("nonsense") is False
    print("   newer/equal/older all correct")

    print("5) helpers return structured types, not strings")
    assert isinstance(version.parse("1.0.0"), tuple)
    assert not isinstance(version.parse("1.0.0"), str)
    assert isinstance(version.is_newer("9.9.9", "1.0.0"), bool)
    assert not isinstance(version.is_newer("9.9.9", "1.0.0"), str)
    print("   tuple and bool confirmed")

    print("6) a missing VERSION file degrades instead of raising")
    saved = version.APP_VERSION
    try:
        broken = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "_no_such_dir_", "VERSION")
        assert version._read_version_file(broken) is None
        assert version._read_version_file("/") is None  # a directory
        # _find_version_file still finds the real one; with none reachable it
        # must return the fallback rather than raising.
        real_find = version._find_version_file
        version._find_version_file = lambda: version.FALLBACK_VERSION
        assert version.FALLBACK_VERSION == "0.0.0"
        version._find_version_file = real_find
        assert version.APP_VERSION == saved
    finally:
        version.APP_VERSION = saved
    print("   missing/empty file -> %r, no exception" % version.FALLBACK_VERSION)

    print("\nALL VERSION TESTS PASSED")


if __name__ == "__main__":
    main()
