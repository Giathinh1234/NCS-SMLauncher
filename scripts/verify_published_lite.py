"""Verify the PUBLISHED lite bundle, not the local one.

Downloads the real asset from the release, unzips it, and decompresses the
frozen bytecode looking for the NCS sphere's renderer body. Local builds passing
prove nothing about what people actually download.
"""
import io
import re
import subprocess
import sys
import zipfile
import zlib

REPO = "Giathinh1234/NCS-SMLauncher"
TAG = "v1.1.0-rc.4"
ASSET = "HashPlay-lite-macos-arm64.app.zip"
MACOS = "https://github.com/%s/releases/download/%s/%s" % (REPO, TAG, ASSET)

# curl, not urllib: the local Python has no CA bundle, and silently disabling
# verification to work around that would defeat the point of checking a
# download over TLS.
print("downloading %s" % ASSET)
blob = subprocess.run(
    ["curl", "-sSL", "--fail", "--max-time", "300", MACOS],
    capture_output=True, check=True).stdout
print("  %.1f MB" % (len(blob) / 1048576))

zf = zipfile.ZipFile(io.BytesIO(blob))
names = zf.namelist()
exe = [n for n in names if n.endswith("/Contents/MacOS/HashPlay-lite")]
if not exe:
    print("  FAIL: no executable inside the zip")
    sys.exit(1)
raw = zf.read(exe[0])
print("  executable %.1f MB" % (len(raw) / 1048576))

# PyInstaller freezes modules as marshalled code inside zlib streams. The
# marker that distinguishes the real renderer from merely mentioning its name
# is the sphere's own geometry constant, which only ncs_sphere.py defines.
MARKERS = {
    "renderer body (_COSLAT_POINTS_FACTOR)": b"_COSLAT_POINTS_FACTOR",
    "renderer body (SPHERE_PPP_TARGET)": b"SPHERE_PPP_TARGET",
    "renderer body (draw_ncs_sphere)": b"draw_ncs_sphere",
    "API module (control_api)": b"ControlHandler",
    "ffmpeg module (ncs_video)": b"ffprobe",
    "torrents (libtorrent)": b"libtorrent",
}
found = {k: False for k in MARKERS}
scanned = 0
for m in re.finditer(b"\x78[\x01\x9c\xda\x5e]", raw):
    try:
        d = zlib.decompressobj().decompress(raw[m.start():m.start() + 8_000_000])
    except Exception:
        continue
    scanned += 1
    for k, v in MARKERS.items():
        if v in d:
            found[k] = True

print("  scanned %d compressed streams" % scanned)
for k in MARKERS:
    print("   %-42s %s" % (k, "present" if found[k] else "ABSENT"))

fails = []
if not found["renderer body (_COSLAT_POINTS_FACTOR)"]:
    fails.append("the NCS sphere is NOT in the published lite bundle")
if found["API module (control_api)"]:
    fails.append("the control API IS in the published lite bundle")
print()
if fails:
    for f in fails:
        print("  FAIL: %s" % f)
    sys.exit(1)
print("  OK: published lite has the NCS ball and no control API")
