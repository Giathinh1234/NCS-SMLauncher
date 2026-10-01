#!/bin/bash
# Prove a FROZEN lite bundle is actually lite.
#
# This exists because the first lite binary shipped while being the opposite
# of what it claimed: it listened on 127.0.0.1:8777 and wrote an api_token
# file, because the variant was an env var that PyInstaller never captured.
# Source-level tests all passed, because source was correct. Only running the
# finished binary could have caught it.
#
#   1. nothing listens on the API port
#   2. no token file is written
#   3. the app stays alive (a lite build should not be a broken build)
#
# SDL_AUDIODRIVER=dummy throughout: this is a memory/behaviour check and it
# must not make a sound.
set -uo pipefail
cd "$(dirname "$0")/.."

APP="dist/HashPlay-lite.app"
PORT="${HASHPLAY_API_PORT:-8777}"
PROBE="$(mktemp -d /tmp/hp_lite_bin.XXXXXX)"
fails=0

ok()   { echo "   ok  $1"; }
bad()  { echo "   FAIL $1"; fails=$((fails+1)); }

if [ ! -d "$APP" ]; then
  echo "   SKIP no $APP -- run scripts/build_macos_lite.sh first"
  exit 0
fi

pkill -9 -f "HashPlay" 2>/dev/null
sleep 1

echo "LITE BINARY BEHAVIOUR (frozen bundle, silent)"

HASHPLAY_CONFIG_DIR="$PROBE" SDL_AUDIODRIVER=dummy \
  open -a "$PWD/$APP" 2>/dev/null

# Wait for the real child to settle rather than a fixed sleep: the onefile
# stub starts long before the app is up.
pid=""
for _ in $(seq 1 40); do
  pid=$(pgrep -f "HashPlay-lite.app/Contents/MacOS/HashPlay-lite" \
        | while read -r p; do
            r=$(ps -o rss= -p "$p" 2>/dev/null | tr -d ' ')
            [ -n "$r" ] && [ "$r" -gt 40000 ] && echo "$p"
          done | head -1)
  [ -n "$pid" ] && break
  sleep 2
done

if [ -z "$pid" ]; then
  bad "the lite bundle did not start"
  pkill -9 -f "HashPlay" 2>/dev/null
  exit 1
fi
ok "the lite bundle started (pid $pid)"

# 1. the socket
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | grep -q .; then
  bad "something is listening on $PORT -- a lite build must not serve the API"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | tail -n +2 | sed 's/^/        /'
else
  ok "nothing is listening on $PORT"
fi

# 2. the token file
if [ -f "$PROBE/api_token" ]; then
  bad "a lite build wrote $PROBE/api_token -- it constructed the API anyway"
else
  ok "no api_token file was written"
fi

# 3. it should still be a working app
sleep 6
if ps -p "$pid" >/dev/null 2>&1; then
  ok "still running after ~20s (a lite build is not a broken build)"
else
  bad "it died within ~20s"
fi

pkill -9 -f "HashPlay" 2>/dev/null
rm -rf "$PROBE"

if [ "$fails" -eq 0 ]; then
  echo
  echo "LITE BINARY TESTS PASSED"
else
  echo
  echo "$fails FAILURES"
fi
exit $((fails > 0))
