#!/usr/bin/env bash
# Build HashPlay as a double-clickable macOS .app bundle
set -e
cd "$(dirname "$0")"

# 1. build the single-file executable
python3 -m PyInstaller HashPlay.spec --noconfirm

# 2. wrap it in an .app bundle
APP="dist/HashPlay.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp dist/HashPlay "$APP/Contents/MacOS/HashPlay"

cat > "$APP/Contents/Info.plist" <<'PLIST'
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
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key>         <string>1</string>
    <key>LSMinimumSystemVersion</key>  <string>11.0</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>NSMicrophoneUsageDescription</key>
        <string>HashPlay uses the audio session for the visualizer.</string>
</dict>
</plist>
PLIST

codesign --force --deep -s - "$APP" 2>/dev/null || true
echo "Built: $APP"
