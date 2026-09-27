#!/usr/bin/env bash
# Build HashPlay as a double-clickable macOS .app bundle
set -e
cd "$(dirname "$0")"

# 1. build the single-file executable
python3 -m PyInstaller HashPlay.spec --noconfirm

# 2. derive the minimum macOS version from the Python we just built against.
#    PyInstaller rewrites the Mach-O load command of the bundled interpreter,
#    so the framework's own deployment target is what actually governs whether
#    this bundle will launch. A hardcoded plist value that disagrees with the
#    binary yields a bundle that looks fine and refuses to start.
if [ -z "${MACOS_MIN:-}" ]; then
    BASE_PY="$(python3 -c 'import sys; print(getattr(sys, "_base_executable", None) or sys.executable)')"
    MACOS_MIN="$(otool -l "$BASE_PY" 2>/dev/null | awk '
        /LC_BUILD_VERSION/      { build = 1; next }
        /LC_VERSION_MIN_MACOSX/ { legacy = 1; next }
        build  && $1 == "minos" { print $2; exit }
        legacy && $1 == "version" { print $2; exit }
    ' || true)"
    [ -n "$MACOS_MIN" ] || MACOS_MIN="11.0"
    echo "LSMinimumSystemVersion: $MACOS_MIN (from $(basename "$BASE_PY"))"
else
    echo "LSMinimumSystemVersion: $MACOS_MIN (from MACOS_MIN)"
fi
CFBUNDLE_SHORT_VERSION="$(cat VERSION 2>/dev/null || echo 1.0.0)"
CFBUNDLE_VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo 1)"

# 3. wrap it in an .app bundle
APP="dist/HashPlay.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp dist/HashPlay "$APP/Contents/MacOS/HashPlay"

# The three interpolated values below come from VERSION, git and otool.
# Validate them BEFORE the heredoc runs: reject anything not version-shaped
# rather than letting a stray character corrupt the plist or inject markup.
for _v in "$CFBUNDLE_SHORT_VERSION" "$CFBUNDLE_VERSION" "$MACOS_MIN"; do
    case "$_v" in
        ''|*[!0-9A-Za-z.+-]*) echo "error: refusing to write a non-version value into Info.plist: '$_v'" >&2; exit 1 ;;
    esac
done

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>HashPlay</string>
    <key>CFBundleDisplayName</key>     <string>HashPlay</string>
    <key>CFBundleIdentifier</key>      <string>com.giathinh.hashplay</string>
    <key>CFBundleExecutable</key>      <string>HashPlay</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$CFBUNDLE_SHORT_VERSION</string>
    <key>CFBundleVersion</key>         <string>$CFBUNDLE_VERSION</string>
    <key>LSMinimumSystemVersion</key>  <string>$MACOS_MIN</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>NSMicrophoneUsageDescription</key>
        <string>HashPlay uses the audio session for the visualizer.</string>
</dict>
</plist>
PLIST

codesign --force --deep -s - "$APP" 2>/dev/null || true
echo "Built: $APP"
