#!/usr/bin/env bash
# Create .venv with Python 3.13 and install the build dependencies.
# Idempotent: safe to re-run after editing requirements-dev.txt.
#
# 3.13, not 3.14, and the reason is concrete rather than a preference:
# pygame publishes no 3.14 wheels, so pip falls back to building it from
# source and dies on "Unable to run sdl-config". Verified on CI -- macOS,
# Linux and Windows all fail identically on 3.14, and all three pass on 3.13.
# Bump this when pygame ships 3.14 wheels, and the test that pins the version
# (tests/test_ci_config.py) will need updating in the same commit.
set -euo pipefail
cd "$(dirname "$0")/.."

find_python() {
    if command -v python3.13 >/dev/null 2>&1; then
        command -v python3.13
        return 0
    fi
    if command -v brew >/dev/null 2>&1; then
        local prefix
        prefix="$(brew --prefix python@3.13 2>/dev/null || true)"
        if [ -n "$prefix" ] && [ -x "$prefix/bin/python3.13" ]; then
            echo "$prefix/bin/python3.13"
            return 0
        fi
    fi
    return 1
}

if ! PYTHON="$(find_python)"; then
    echo "error: python3.13 not found." >&2
    echo "" >&2
    echo "Install it with:" >&2
    echo "    brew install python@3.13" >&2
    echo "" >&2
    echo "3.13 is the newest version pygame ships wheels for. On 3.14 pip" >&2
    echo "tries to build pygame from source and fails on 'Unable to run" >&2
    echo "sdl-config', on every platform." >&2
    exit 1
fi

echo "Using: $PYTHON ($("$PYTHON" -V 2>&1))"

if [ ! -d .venv ]; then
    echo "Creating .venv..."
    "$PYTHON" -m venv .venv
fi

VENV_PY="$(pwd)/.venv/bin/python"
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r requirements-dev.txt

echo ""
echo "Done. Build with:"
echo "    ./build_macos_app.sh          # macOS arm64 .app"
echo "    ./scripts/build_linux.sh      # Linux x86_64 single file"
