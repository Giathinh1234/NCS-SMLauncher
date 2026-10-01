# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_submodules

# Which stripped tier is this build? scripts/build_macos_stripped.sh sets
# HASHPLAY_PROFILE before invoking PyInstaller, because a .spec is executed by
# PyInstaller itself and cannot be told anything on the command line.
#
# This matters: ncs_sphere and ncs_video are LAZY imports in the source, so
# PyInstaller's own analysis never sees them and they have to be listed
# explicitly or a frozen build cannot import them at all. But listing them
# unconditionally means a micro build ships, extracts and LOADS both -- the
# exact ffmpeg and splat-kernel memory the tier exists to avoid. Verified with
# lsof against a running micro build, which had libtorrent's dylibs mapped in.
PROFILE = os.environ.get("HASHPLAY_PROFILE", "full")
MICRO = (PROFILE == "micro")

# libtorrent is OPTIONAL at runtime -- ncs_launcher.py imports it in a
# try/except and sets lt = None when it is missing, disabling torrents and
# nothing else. collect_submodules has to be guarded the same way, or a
# machine without the wheel fails the entire build rather than producing an
# app that simply cannot download torrents. The first platform to need this
# was Windows, where a fresh runner may not get a working libtorrent wheel.
if MICRO:
    # Micro does not import libtorrent at runtime, so bundling it would only
    # make the app larger and would load its dylibs for nothing.
    _lt = []
    print("micro build: leaving libtorrent out")
else:
    try:
        _lt = collect_submodules('libtorrent')
    except Exception:                  # noqa: BLE001 - see above
        _lt = []
        print("warning: libtorrent not available; building without torrent support")

# The rest are pure-python modules reached through src/ (pathex below), listed
# explicitly because a few of them are imported lazily inside a function --
# notably settings_panel, which would otherwise be missing from a frozen build.
hidden = (_lt + [
    'sounddevice', 'miniaudio',
    # ncs_sphere and ncs_video are lazy imports, so they must be named here or
    # a frozen build cannot import them -- except in micro, which must not
    # carry them at all.
    *( [] if MICRO else ['ncs_sphere', 'ncs_video'] ),
    'ncs_disc_player', 'media_keys',
    'config', 'actions', 'migrations', 'version',
    'streaming_source', 'library_ops', 'updater', 'settings_panel',
    # certifi is imported inside updater._ssl_context() and its CA bundle is
    # data, not code, so neither the import nor cacert.pem is collected
    # automatically. Without it a frozen build cannot verify api.github.com
    # and the updater reports "no update available" forever, silently.
    'certifi',
])

# version.py reads this at runtime, and a frozen bundle has no repo root to
# fall back on. Without it the shipped app reports 0.0.0 and the updater can
# never tell it is out of date.
datas = [('VERSION', '.')]

a = Analysis(
    ['src/ncs_launcher.py'],
    pathex=['src'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # PyInstaller's own analysis finds libtorrent even in a micro build:
    # ncs_launcher.py wraps that import in `if BUILD_PROFILE != "micro":`, but
    # the static analyser does not evaluate a constant that comes from another
    # module, so the import still looks reachable and gets collected. Excluding
    # it here is the only thing that actually removes it.
    excludes=(['tkinter'] + (['libtorrent', 'ncs_video', 'ncs_sphere']
                             if MICRO else [])),
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='HashPlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,          # keeps terminal logs; set False for silent app
    icon=None,
)