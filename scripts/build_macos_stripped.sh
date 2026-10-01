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

# Which stripped tier to build. "lite" drops the control API and the NCS ball;
# "micro" drops those plus video (ffmpeg) and torrents (libtorrent).
PROFILE="${1:-lite}"
case "$PROFILE" in
  lite|micro) ;;
  *) echo "usage: $0 [lite|micro]" >&2; exit 2 ;;
esac

VERSION="$(cat VERSION 2>/dev/null || echo 1.1.0)"
MACOS_MIN="10.13"
PYI_BIN="python3 -m PyInstaller"
[[ -x ".venv/bin/python" ]] && PYI_BIN=".venv/bin/python -m PyInstaller"

echo "== HashPlay $VERSION ($PROFILE) =="

# Clean only what this build makes. The first version of this ran
# `rm -rf build dist`, which is fine standalone but DESTROYS the full build's
# output when CI runs it a second time for the other tier: it deleted
# dist/HashPlay and dist/HashPlay.app, and the collect step then failed with
# "cp: dist/HashPlay: No such file or directory". It now removes only the
# bundle it is about to create, plus the PyInstaller work dir.
rm -rf build "dist/HashPlay-$PROFILE.app" "dist/HashPlay-$PROFILE-macos-arm64.app.zip"
# The flag has to be set for the BUILD as well as the app: PyInstaller reads
# the modules in to bundle them, and a module never imported at build time is
# a module a frozen build cannot import at run time.
# The variant is a CONSTANT in src/build_variant.py, not an env var. This is
# the part that matters: HASHPLAY_LITE=1 was set here originally and produced a
# binary that still opened a listening socket and wrote a token file, because
# PyInstaller analyses imports, not the environment. Overwriting the constant
# is something it can see.
#
# HASHPLAY_PROFILE below is a SEPARATE, second signal, and it exists only
# because a .spec is executed by PyInstaller and cannot be told anything on the
# command line. HashPlay.spec uses it to decide whether to bundle ncs_sphere,
# ncs_video and libtorrent at all. Both have to agree: the constant governs
# runtime behaviour, the env var governs what gets collected into the binary.
export HASHPLAY_PROFILE="$PROFILE"
VARIANT="src/build_variant.py"
cp "$VARIANT" "$VARIANT.full-backup"
restore() { mv "$VARIANT.full-backup" "$VARIANT"; }
trap restore EXIT

python3 - "$VARIANT" "$PROFILE" <<'PYEOF'
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
import sys
prof = sys.argv[2]
s2 = re.sub(r'^BUILD_PROFILE = "full"$', f'BUILD_PROFILE = "{prof}"', s, flags=re.M)
if s2 == s:   # already switched: build a second tier from the same tree
    s2 = re.sub(r'^BUILD_PROFILE = "(?:lite|micro)"$',
                f'BUILD_PROFILE = "{prof}"', s, flags=re.M)
assert s2 != s, "BUILD_PROFILE assignment not found -- refusing to build a full binary"
open(p, "w", encoding="utf-8").write(s2)
PYEOF


# PyInstaller writes dist/HashPlay, which the step below renames into the tier
# bundle. That is what ate the full build's bare executable when CI ran the
# tiers after build_macos_app.sh: the mv consumed it and nothing put it back.
# Stash it first and restore afterwards, so all three artifacts coexist.
STASH="dist/.full-build-keep"
mkdir -p "$STASH"
[ -e dist/HashPlay ] && mv dist/HashPlay "$STASH/HashPlay"
[ -e dist/HashPlay.app ] && cp -R dist/HashPlay.app "$STASH/HashPlay.app" 2>/dev/null || true

$PYI_BIN --clean --noconfirm HashPlay.spec >/dev/null 2>&1 \
  || $PYI_BIN --clean --noconfirm HashPlay.spec

# Verify the flip reached the source that is about to be frozen. The first
# lite build shipped the full modules while advertising itself as lite,
# because the variant was an env var PyInstaller could not see.
grep -q "BUILD_PROFILE = \"$PROFILE\"" "$VARIANT" \
  || { echo "  FATAL: variant not set to $PROFILE; aborting"; exit 1; }
echo "  variant: BUILD_PROFILE = \"$PROFILE\""

APP="dist/HashPlay-$PROFILE.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
mv dist/HashPlay "$APP/Contents/MacOS/HashPlay-$PROFILE"

# The bundle's Info.plist. Identifier gets its own suffix so the lite and full
# builds can sit side by side without Launch Services treating them as one app.
INFOPLIST="$APP/Contents/Info.plist"
{
  echo '<?xml version="1.0" encoding="UTF-8"?>'
  echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
  echo ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
  echo '<plist version="1.0">'
  echo '<dict>'
  echo '  <key>CFBundleName</key><string>HashPlay $PROFILE</string>'
  echo '  <key>CFBundleDisplayName</key><string>HashPlay $PROFILE</string>'
  echo '  <key>CFBundleExecutable</key><string>HashPlay-$PROFILE</string>'
  echo '  <key>CFBundleIdentifier</key><string>com.giathinh.hashplay.$PROFILE</string>'
  echo '  <key>CFBundleShortVersionString</key><string>'"$VERSION"'</string>'
  echo '  <key>CFBundleVersion</key><string>'"$(git rev-parse --short HEAD 2>/dev/null || echo 1)"'</string>'
  echo '  <key>CFBundlePackageType</key><string>APPL</string>'
  echo '  <key>LSMinimumSystemVersion</key><string>'"$MACOS_MIN"'</string>'
  echo '  <key>NSHighResolutionCapable</key><true/>'
  echo '</dict>'
  echo '</plist>'
} > "$INFOPLIST"

printf 'APPL????' > "$APP/Contents/PkgInfo"

# Put the full build back: this script must not consume it.
if [ -e "$STASH/HashPlay" ]; then
  mv "$STASH/HashPlay" dist/HashPlay
fi
rm -rf "$STASH"

# Prove the flag survived into the binary. A lite build that reports itself as
# full would be worse than useless -- it would look right and listen anyway.
BUILT="$("$APP/Contents/MacOS/HashPlay-$PROFILE" --version 2>&1 | tail -1)"
echo "  built: $BUILT"
[[ "$BUILT" == *"lite"* ]] || echo "  NOTE: --version does not report lite; the flag is verified by tests/test_lite_mode.py"

echo "  app:  $APP"
du -sh "$APP"
echo "  to run: open '$APP'   (or build_variant edited, then python3 src/ncs_launcher.py)"
