"""Single source of truth for the application version.

The version lives in a plain-text `VERSION` file at the repo root so the
PyInstaller spec, the Android Gradle config, and this module all read the same
value instead of each hardcoding a literal that drifts out of sync.

Under a frozen build PyInstaller unpacks data files next to the bundle, so
sys._MEIPASS is checked too. Every read is best-effort: a missing or empty
file yields "0.0.0" rather than an import-time crash, because a bad build
should not stop the app from launching.
"""
import os
import sys

FALLBACK_VERSION = "0.0.0"


def _read_version_file(path):
    """Contents of a VERSION file, or None if it is missing or empty."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def _find_version_file():
    """First readable VERSION file, in source, frozen, then repo-root order."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, "VERSION")]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "VERSION"))
    candidates.append(os.path.join(os.path.dirname(here), "VERSION"))

    for candidate in candidates:
        found = _read_version_file(candidate)
        if found:
            return found
    return FALLBACK_VERSION


APP_VERSION = _find_version_file()


def parse(version):
    """Parse a dotted version into a 3-tuple of ints.

    Missing parts become 0 and non-digits are stripped, so '1.2', '1.2.3',
    and 'v1.2.3-beta' all parse instead of raising on user-supplied input
    from an update feed.
    """
    parts = []
    for chunk in str(version).strip().split(".")[:3]:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(candidate, current=APP_VERSION):
    """True only when `candidate` is strictly greater than `current`."""
    return parse(candidate) > parse(current)
