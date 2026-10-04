#!/usr/bin/env bash
# Build the Android APKs (full + lite + ncs), signed for release.
#
# The keystore is never in the repo. This script looks for one in this order:
#
#   1. $HASHPLAY_KEYSTORE (+ _STORE_PASS, _KEY_PASS, _KEY_ALIAS)
#   2. android/keystore/hashplay-release.jks  (gitignored, created on first run)
#   3. nothing -> a NEW key is generated
#
# On (3) it prints a warning worth reading: a key generated here is fine for
# sideloading and local testing, but if you ever publish to Google Play, keep
# that exact keystore forever. Play will refuse an update signed with a
# different key, and the only way out is to enrol in Play App Signing and
# request a key upgrade. Losing the key means you can never update that listing
# again.
#
# Usage:
#   scripts/build_android_apks.sh              # both flavors
#   scripts/build_android_apks.sh lite         # just lite
#   scripts/build_android_apks.sh full --debug # full, debug build (unsigned ok)

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO/android"

FLAVOR="${1:-both}"

# Debug task list, computed plainly. A nested `case` inside `$(...)` is what
# broke `sh -n` when this flavor was added, so it stays a simple if-chain.
DEBUG_TASKS="assembleFullDebug"
case "$FLAVOR" in
  lite) DEBUG_TASKS="assembleLiteDebug" ;;
  ncs)  DEBUG_TASKS="assembleNcsDebug" ;;
  both) DEBUG_TASKS="assembleFullDebug assembleLiteDebug" ;;
  all)  DEBUG_TASKS="assembleFullDebug assembleLiteDebug assembleNcsDebug" ;;
esac
MODE="${2:-release}"

die() { printf '  error: %s\n' "$1" >&2; exit 1; }

# --- keystore --------------------------------------------------------------
KS_PATH="${HASHPLAY_KEYSTORE:-}"
KS_PASS="${HASHPLAY_STORE_PASS:-}"
KEY_PASS="${HASHPLAY_KEY_PASS:-}"
KEY_ALIAS="${HASHPLAY_KEY_ALIAS:-}"

if [ "$MODE" = "debug" ]; then
  echo "  debug build: no keystore needed (APG signs with the debug key)"
  ./gradlew --console=plain \
    $DEBUG_TASKS \
    2>&1 | grep -viE "^Download|SDK processing"
  DEBUG_RC=$?
  # Same trap as the release path below: `|| true` here let a Kotlin compile
  # error exit 0 and the script went on as if the build had worked.
  if [ "$DEBUG_RC" -ne 0 ]; then
    echo "  BUILD FAILED (gradle exit $DEBUG_RC)."
    exit "$DEBUG_RC"
  fi
  exit 0
fi

if [ -z "$KS_PATH" ]; then
  KS_PATH="$REPO/android/keystore/hashplay-release.jks"
  mkdir -p "$(dirname "$KS_PATH")"
  if [ ! -f "$KS_PATH" ]; then
    # Write the password DOWN before using it. The first version of this script
    # generated one from the clock and only ever held it in a shell variable,
    # so the keystore it produced could not be opened again by anything --
    # including CI. An unrecoverable key is worse than no key.
    CRED_FILE="$REPO/android/keystore/CREDENTIALS.txt"
    KS_PASS="hashplay-$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 24)"
    KEY_PASS="$KS_PASS"
    KEY_ALIAS="hashplay"
    umask 077
    cat > "$CRED_FILE" <<EOF
HashPlay Android signing key
============================

Keystore : $KS_PATH
Alias    : $KEY_ALIAS
Password : $KS_PASS

KEEP BOTH FILES. If you ever publish to Google Play, this exact keystore is
required to update the listing: Play refuses an update signed with a different
key, and the only way out is Play App Signing plus a key upgrade request.
Losing the file means you can never update that listing again.

Neither file is in git. Back them up somewhere you will not lose them.
EOF
    chmod 600 "$CRED_FILE"
    keytool -genkeypair -v \
      -keystore "$KS_PATH" \
      -storepass "$KS_PASS" -keypass "$KEY_PASS" \
      -alias "$KEY_ALIAS" \
      -keyalg RSA -keysize 4096 -validity 10000 \
      -dname "CN=HashPlay, OU=HashPlay, O=HashPlay, L=-, ST=-, C=US" \
      >/dev/null 2>&1 || die "keytool failed"
    chmod 600 "$KS_PATH"
    echo
    echo "  Generated a signing key. Password written to:"
    echo "    $CRED_FILE"
    echo
    echo "  KEEP BOTH FILES. If you publish to Google Play, this exact keystore"
    echo "  is the only thing that can ever update that listing."
    echo
  fi
  # Reuse the recorded password if a key already exists, so a second run does
  # not invent a new one and then fail to open the old key.
  CRED_FILE="$REPO/android/keystore/CREDENTIALS.txt"
  if [ -f "$CRED_FILE" ]; then
    KS_PASS="${HASHPLAY_STORE_PASS:-$(sed -n 's/^Password : //p' "$CRED_FILE" | head -1)}"
    KEY_ALIAS="${HASHPLAY_KEY_ALIAS:-$(sed -n 's/^Alias    : //p' "$CRED_FILE" | head -1)}"
  fi
  KEY_PASS="${HASHPLAY_KEY_PASS:-$KS_PASS}"
  KEY_ALIAS="${KEY_ALIAS:-hashplay}"
fi

[ -f "$KS_PATH" ] || die "keystore not found at $KS_PATH"
# Prove the key actually opens before spending three minutes on a Gradle build
# that would fail at packaging time with an unhelpful message.
keytool -list -keystore "$KS_PATH" -storepass "$KS_PASS" -alias "$KEY_ALIAS" \
  >/dev/null 2>&1 || die "cannot open $KS_PATH with the given password (see $CRED_FILE)"

# --- build -----------------------------------------------------------------
case "$FLAVOR" in
  # Spelled out rather than using ${FLAVOR^}: that is a bash-ism and this
  # script runs under /bin/sh, where it dies with "bad substitution" only
  # when a single flavor is requested -- so `both` kept working and hid it.
  full)     TASKS="assembleFullRelease" ;;
  lite)     TASKS="assembleLiteRelease" ;;
  ncs)      TASKS="assembleNcsRelease" ;;
  both)     TASKS="assembleFullRelease assembleLiteRelease" ;;
  all)      TASKS="assembleFullRelease assembleLiteRelease assembleNcsRelease" ;;
  *)        die "flavor must be full, lite, ncs, both or all (got '$FLAVOR')" ;;
esac

echo "  building: $TASKS"
./gradlew --console=plain $TASKS \
  -Pkeystore="$KS_PATH" \
  -PstorePass="$KS_PASS" \
  -PkeyPass="$KEY_PASS" \
  -PkeyAlias="$KEY_ALIAS" \
  2>&1 | grep -viE "^Download|SDK processing"
GRADLE_RC=$?

# This used to end in `|| true`, which swallowed Gradle's exit status. A Kotlin
# compile error then still produced a signed APK out of a stale build/
# directory, and I measured and reported on a renderer that had never compiled.
# Twice. Gradle's exit code has to propagate.
if [ "$GRADLE_RC" -ne 0 ]; then
  echo
  echo "  BUILD FAILED (gradle exit $GRADLE_RC) -- refusing to ship a stale APK."
  exit 1
fi

# --- verify what came out --------------------------------------------------
echo
echo "  APKs:"
FAILED=0
# `all` builds every flavor; `both` means full+lite, the historical default.
for v in full lite ncs; do
  [ "$FLAVOR" != both ] && [ "$FLAVOR" != all ] && [ "$FLAVOR" != "$v" ] && continue
  APK=$(find "app/build/outputs/apk/$v/release" -name "*.apk" 2>/dev/null | head -1)
  if [ -z "$APK" ]; then
    echo "    $v: MISSING — build did not produce it"
    FAILED=1
    continue
  fi
  SZ=$(echo "scale=1; $(stat -f%z "$APK" 2>/dev/null || stat -c%s "$APK")/1048576" | bc)
  ENGINE=$(unzip -l "$APK" 2>/dev/null | grep -c libtorrent4j.so || true)
  case "$v" in
    full) WANT=present ;;
    lite) WANT=absent  ;;
    # ncs is the desktop-full equivalent: it carries the torrent engine like
    # `full`, and differs from it in UI and applicationId instead.
    ncs)  WANT=present ;;
    *)    WANT=present ;;
  esac
  # Each flavor must be a genuinely separate app, or "three versions on one
  # device" silently degrades into one app installed three times over.
  AAPT=$(ls "$(sed -n 's/^sdk\.dir=//p' "$REPO/android/local.properties" 2>/dev/null)"/build-tools/*/aapt 2>/dev/null | sort -V | tail -1)
  PKG=""
  if [ -n "$AAPT" ]; then
    # Capture just the quoted name. aapt emits
    #   package: name='com.x' versionCode=... versionName=...
    # so the name has to be delimited by the quotes, not by whitespace, or the
    # whole line comes back. Double-quoted to keep the single quotes out of the
    # shell's quoting rules -- an escaped quote in cut's argument broke `sh -n`.
    PKG=$("$AAPT" dump badging "$APK" 2>/dev/null \
          | sed -n "s/^package: name='\([^']*\)'.*/\1/p" | head -1)
  fi
  WANT_PKG="com.giathinh.hashplay"
  [ "$v" = lite ] && WANT_PKG="com.giathinh.hashplay.lite"
  [ "$v" = ncs ]  && WANT_PKG="com.giathinh.hashplay.ncs"
  GOT=$([ "$ENGINE" = "0" ] && echo absent || echo present)
  if [ "$GOT" = "$WANT" ]; then MARK="ok "; else MARK="BAD"; FAILED=1; fi
  if [ -n "$PKG" ] && [ "$PKG" != "$WANT_PKG" ]; then MARK="BAD"; FAILED=1
    echo "    expected package $WANT_PKG but the APK declares $PKG"
  fi
  # Signed? Ask apksigner, not the archive listing. Modern signing (v2/v3)
  # stores the signature in the APK Signing Block rather than as META-INF/*.RSA
  # files, so a zip listing reports "unsigned" for a perfectly valid APK --
  # which is what the first version of this script did.
  # apksigner lives in the SDK's build-tools. Find it without assuming
  # ANDROID_HOME is exported -- `set -u` aborts on an unset variable, and on
  # this machine the SDK path only ever existed in local.properties.
  BUILD_TOOLS=""
  for base in "${ANDROID_HOME:-}" "${ANDROID_SDK_ROOT:-}" \
              "$HOME/Library/Android/sdk" "$(sed -n 's/^sdk\.dir=//p' "$REPO/android/local.properties" 2>/dev/null)"; do
    [ -n "$base" ] || continue
    [ -d "$base/build-tools" ] || continue
    CAND=$(ls -d "$base"/build-tools/* 2>/dev/null | sort -V | tail -1)
    if [ -n "$CAND" ] && [ -x "$CAND/apksigner" ]; then
      BUILD_TOOLS="$CAND"
      break
    fi
  done
  if [ -n "$BUILD_TOOLS" ]; then
    "$BUILD_TOOLS/apksigner" verify --print-certs "$APK" >/dev/null 2>&1 \
      && SIG=signed || SIG="NOT SIGNED"
  else
    SIG="unknown (apksigner not found)"
  fi
  case "$SIG" in
    signed) ;;
    *) FAILED=1 ;;
  esac
  printf "    %s %-5s %-38s %6.1f MB  engine:%-8s %s\n" \
    "$MARK" "$v" "$(basename "$APK")" "$SZ" "$GOT" "$SIG"
done

echo
[ "$FAILED" = 0 ] || die "one or more APKs are wrong; see above"
echo "  All APKs built, signed and shaped as expected."
