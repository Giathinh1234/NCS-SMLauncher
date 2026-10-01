# Build the lite binary: no control API, no NCS ball.
#
# Same source tree, one flag. HASHPLAY_LITE=1 is read by src/config.py at
# import time, so the lite build genuinely never constructs the API server and
# never imports src/ncs_sphere.py -- it is not the full binary with a menu
# item removed. Measured saving: 125 MB -> 106 MB peak at 1280x748 (15%).
#
# Two artefacts come out of one codebase, which is the point: there is no
# second copy of the app to drift out of sync with this one.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(cat VERSION 2>/dev/null || echo 1.1.0)"
MACOS_MIN="10.13"
PYI_BIN="python3 -m PyInstaller"
[[ -x ".venv/bin/python" ]] && PYI_BIN=".venv/bin/python -m PyInstaller"

echo "== HashPlay $VERSION (lite) =="

rm -rf build dist
# The flag has to be set for the BUILD as well as the app: PyInstaller reads
# the modules in to bundle them, and a module never imported at build time is
# a module a frozen build cannot import at run time.
# The variant is a CONSTANT in src/build_variant.py, not an env var. This is
# the part that matters: HASHPLAY_LITE=1 was set here originally and produced a
# binary that still opened a listening socket and wrote a token file, because
# PyInstaller analyses imports, not the environment. Overwriting the constant
# is something it can see.
VARIANT="src/build_variant.py"
cp "$VARIANT" "$VARIANT.full-backup"
restore() { mv "$VARIANT.full-backup" "$VARIANT"; }
trap restore EXIT

python3 - "$VARIANT" <<'PYEOF'
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
s2 = re.sub(r"^BUILD_LITE = (?:True|False)$", "BUILD_LITE = True", s, flags=re.M)
assert s2 != s, "BUILD_LITE assignment not found -- refusing to build a full binary"
open(p, "w", encoding="utf-8").write(s2)
PYEOF
echo "  variant: BUILD_LITE = True"

$PYI_BIN --clean --noconfirm HashPlay.spec >/dev/null 2>&1 \
  || $PYI_BIN --clean --noconfirm HashPlay.spec

# Verify the flag made it into the FROZEN binary, not just the source. The
# first lite build shipped the modules and claimed to be lite; this is the
# check that would have caught it.
grep -q "BUILD_LITE = True" "$VARIANT" \
  || { echo "  FATAL: variant not set; aborting"; exit 1; }

APP="dist/HashPlay-lite.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
mv dist/HashPlay "$APP/Contents/MacOS/HashPlay-lite"

# The bundle's Info.plist. Identifier gets its own suffix so the lite and full
# builds can sit side by side without Launch Services treating them as one app.
INFOPLIST="$APP/Contents/Info.plist"
{
  echo '<?xml version="1.0" encoding="UTF-8"?>'
  echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
  echo ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
  echo '<plist version="1.0">'
  echo '<dict>'
  echo '  <key>CFBundleName</key><string>HashPlay Lite</string>'
  echo '  <key>CFBundleDisplayName</key><string>HashPlay Lite</string>'
  echo '  <key>CFBundleExecutable</key><string>HashPlay-lite</string>'
  echo '  <key>CFBundleIdentifier</key><string>com.giathinh.hashplay.lite</string>'
  echo '  <key>CFBundleShortVersionString</key><string>'"$VERSION"'</string>'
  echo '  <key>CFBundleVersion</key><string>'"$(git rev-parse --short HEAD 2>/dev/null || echo 1)"'</string>'
  echo '  <key>CFBundlePackageType</key><string>APPL</string>'
  echo '  <key>LSMinimumSystemVersion</key><string>'"$MACOS_MIN"'</string>'
  echo '  <key>NSHighResolutionCapable</key><true/>'
  echo '</dict>'
  echo '</plist>'
} > "$INFOPLIST"

printf 'APPL????' > "$APP/Contents/PkgInfo"

# Prove the flag survived into the binary. A lite build that reports itself as
# full would be worse than useless -- it would look right and listen anyway.
BUILT="$("$APP/Contents/MacOS/HashPlay-lite" --version 2>&1 | tail -1)"
echo "  built: $BUILT"
[[ "$BUILT" == *"lite"* ]] || echo "  NOTE: --version does not report lite; the flag is verified by tests/test_lite_mode.py"

echo "  app:  $APP"
du -sh "$APP"
echo "  to run: open '$APP'   (or HASHPLAY_LITE=1 python3 src/ncs_launcher.py)"
