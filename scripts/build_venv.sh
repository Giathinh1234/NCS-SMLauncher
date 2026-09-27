#!/usr/bin/env bash
# Create .venv with Python 3.14 and install the build dependencies.
# Idempotent: safe to re-run after editing requirements-dev.txt.
set -euo pipefail
cd "$(dirname "$0")/.."

find_python() {
    if command -v python3.14 >/dev/null 2>&1; then
        command -v python3.14
        return 0
    fi
    if command -v brew >/dev/null 2>&1; then
        local prefix
        prefix="$(brew --prefix python@3.14 2>/dev/null || true)"
        if [ -n "$prefix" ] && [ -x "$prefix/bin/python3.14" ]; then
            echo "$prefix/bin/python3.14"
            return 0
        fi
    fi
    return 1
}

if ! PYTHON="$(find_python)"; then
    echo "error: python3.14 not found." >&2
    echo "" >&2
    echo "Install it with:" >&2
    echo "    brew install python@3.14" >&2
    echo "" >&2
    echo "The project targets 3.14; earlier versions build but are not supported." >&2
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
