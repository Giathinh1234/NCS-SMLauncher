"""Check that the asset names this project publishes are the ones the
in-app updater will actually match.

The updater's _asset_matches_platform is the single gate between a published
artifact and a user who can install it. For every release up to v1.1.0-rc.1
that gate was wrong or missing, so no update was ever offered. This runs in
CI, because the failure mode is silent: a perfectly good build that the app
cannot see, and nobody notices until a user reports "it says I'm up to date".

Run: python tools/check_asset_names.py
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import updater                                        # noqa: E402

# (asset name, platform that must find it) -- the names the workflows publish.
MUST_FIND = [
    ("HashPlay-macos-arm64", "darwin"),
    ("HashPlay.app.zip", "darwin"),
    ("HashPlay-linux-x86_64", "linux"),
    ("HashPlay-linux-x86_64.tar.gz", "linux"),
    ("HashPlay-windows-x64.exe", "win32"),
    ("HashPlay-windows-x64-win32.exe", "win32"),
]

# (asset name, platform that must NOT match) -- a cross-platform match here
# would hand someone the wrong binary.
MUST_NOT_FIND = [
    ("HashPlay-macos-arm64", "win32"),
    ("HashPlay-macos-arm64", "linux"),
    ("HashPlay-windows-x64.exe", "darwin"),
    ("HashPlay-windows-x64.exe", "linux"),
    ("HashPlay-linux-x86_64", "darwin"),
    ("HashPlay-android-arm64.apk", "darwin"),
    ("HashPlay-android-arm64.apk", "win32"),
    ("HashPlay-android-arm64.apk", "linux"),
    ("SHA256SUMS.txt", "darwin"),
    ("SHA256SUMS.txt", "win32"),
    ("", "darwin"),
]


def main():
    bad = []
    print("asset names the updater MUST find:")
    for name, plat in MUST_FIND:
        ok = updater._asset_matches_platform(name, plat)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:32} on {plat}")
        if not ok:
            bad.append(("must-find", name, plat))

    print("\nasset names the updater must NEVER offer:")
    for name, plat in MUST_NOT_FIND:
        hit = updater._asset_matches_platform(name, plat)
        print(f"  {'ok  ' if not hit else 'FAIL'} {name:32} on {plat}")
        if hit:
            bad.append(("must-not-find", name, plat))

    print()
    if bad:
        print(f"{len(bad)} PROBLEM(S):")
        for kind, name, plat in bad:
            print(f"  {kind}: {name!r} on {plat}")
        print("\nA release whose names fail this is invisible to the updater.")
        return 1
    print("all release asset names are discoverable, and nothing crosses platforms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
