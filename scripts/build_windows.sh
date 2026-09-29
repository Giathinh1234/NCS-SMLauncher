# Build the Windows x64 single-file executable and a versioned zip.
#
# PyInstaller cannot cross-compile, so this only runs on Windows -- CI does it
# on a windows-latest runner. Run it from Git Bash or WSL-with-Windows-python.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(cat VERSION)"
BUILD="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"

# Prefer the pinned venv so a local build does not differ from CI's.
if [ -x ".venv/Scripts/python.exe" ]; then
    PYI=(.venv/Scripts/python.exe -m PyInstaller)
elif python -c "import PyInstaller" >/dev/null 2>&1; then
    echo "warning: .venv not found, using the ambient python; run scripts/build_venv.ps1 for a reproducible build" >&2
    PYI=(python -m PyInstaller)
else
    echo "error: PyInstaller not found. Run scripts/build_venv.ps1 first." >&2
    exit 1
fi

echo "Building HashPlay ${VERSION} (${BUILD}) for Windows x64"

# No --windowed here: PyInstaller refuses makespec options ("option(s) not
# allowed ... makespec options not valid when a .spec file is given"), and
# HashPlay.spec already sets console=True. That is deliberate on our side --
# --version and the API bind diagnostics both write to stdout, and CI checks
# the built binary reports the right version. A release build that wants a
# silent double-click experience should set console=False in the spec instead,
# and give up the version self-check.
"${PYI[@]}" --clean --noconfirm HashPlay.spec

# The spec is a onefile build, so Windows produces dist/HashPlay.exe and
# macOS produces dist/HashPlay. Handle both layouts anyway: a onefile ->
# onedir switch in the spec would otherwise fail here with a confusing
# "not produced" message rather than saying which path was looked for.
EXE=""
for candidate in dist/HashPlay.exe dist/HashPlay/HashPlay.exe; do
    if [ -f "$candidate" ]; then
        EXE="$candidate"
        break
    fi
done
if [ -z "$EXE" ]; then
    echo "error: no built executable found. Looked for:" >&2
    echo "         dist/HashPlay.exe" >&2
    echo "         dist/HashPlay/HashPlay.exe" >&2
    echo "         what dist/ actually contains:" >&2
    ls -R dist 2>/dev/null | head -20 >&2
    exit 1
fi
echo "built $EXE"

# Sanity check: the binary must report the version we built, not the 0.0.0
# fallback. A frozen bundle that lost VERSION is a shipped app that can never
# tell it is out of date, and it fails silently.
GOT="$("$EXE" --version 2>/dev/null | tr -d '\r' | tail -1 || true)"
case "$GOT" in
    *"$VERSION"*) echo "version check ok: $GOT" ;;
    *) echo "error: binary reports '$GOT', expected it to contain '$VERSION'" >&2
       exit 1 ;;
esac

# Name the artifact the way the in-app updater expects to find it.
cp "$EXE" "dist/HashPlay-windows-x64.exe"
cp VERSION "dist/VERSION"
(cd dist && zip -qry "HashPlay-windows-x64-${VERSION}.zip" \
    HashPlay-windows-x64.exe VERSION)

echo "Built:"
ls -l dist/HashPlay-windows-x64.exe "dist/HashPlay-windows-x64-${VERSION}.zip"
echo
echo "Note: this binary is UNSIGNED. Windows SmartScreen will warn on first"
echo "run, and Defender may quarantine it. That is expected, not a broken build."
