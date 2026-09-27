#!/usr/bin/env bash
# Build the Linux x86_64 single-file executable and a versioned tarball.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(cat VERSION)"
BUILD="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"

# Prefer the pinned 3.14 venv so a local pyinstaller does not differ from CI's.
if [ -x ".venv/bin/python" ]; then
    PYI=(.venv/bin/python -m PyInstaller)
elif python3 -c "import PyInstaller" >/dev/null 2>&1; then
    echo "warning: .venv not found, using system python3; run ./scripts/build_venv.sh for a reproducible build" >&2
    PYI=(python3 -m PyInstaller)
else
    echo "error: PyInstaller not found. Run ./scripts/build_venv.sh first." >&2
    exit 1
fi

echo "Building HashPlay ${VERSION} (${BUILD})"
"${PYI[@]}" HashPlay.spec --noconfirm

tar -czf "dist/HashPlay-linux-x86_64-${VERSION}.tar.gz" dist/HashPlay

echo ""
echo "Built:"
echo "    dist/HashPlay"
echo "    dist/HashPlay-linux-x86_64-${VERSION}.tar.gz"
