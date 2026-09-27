# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

# libtorrent is optional at runtime and ships compiled extensions; the rest are
# pure-python modules reached through src/ (pathex below), listed explicitly
# because a few of them are imported lazily inside a function -- notably
# settings_panel, which would otherwise be missing from a frozen build.
hidden = (collect_submodules('libtorrent') + [
    'sounddevice', 'miniaudio',
    'ncs_sphere', 'ncs_video', 'ncs_disc_player', 'media_keys',
    'config', 'actions', 'migrations', 'version',
    'streaming_source', 'library_ops', 'updater', 'settings_panel',
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
    excludes=['tkinter'],
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