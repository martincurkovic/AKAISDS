#!/usr/bin/env bash
set -e

# check_minos.sh MAX [APP_OR_DMG] - fails if any Mach-O file in a macOS build asks for a newer macOS than MAX.
#   scripts/check_minos.sh 12.0                                  the legacy build, src/AKAISDS.app
#   scripts/check_minos.sh 12.0 AKAISDS-macos-arm-legacy.dmg     a DMG (mounted read-only and detached again)
# Reads each slice's `minos` with vtool, so a universal binary is checked for both architectures. Why it matters:
# dev_docs/macos12-support-handoff.md. An old stamp inside a library macOS ignores (PySide6's 15.0) is still reported: judge those by hand.

MAX="${1:?usage: check_minos.sh MAX_VERSION [APP_OR_DMG]}"
TARGET="${2:-$(cd "$(dirname "$0")/.." && pwd)/src/AKAISDS.app}"

MOUNT=""
cleanup() { [ -n "$MOUNT" ] && hdiutil detach -quiet "$MOUNT" || true; }
trap cleanup EXIT
case "$TARGET" in
*.dmg)
    MOUNT="$(mktemp -d /tmp/akaisds_minos.XXXXXX)"
    hdiutil attach -readonly -nobrowse -quiet -mountpoint "$MOUNT" "$TARGET"
    APP="$MOUNT/AKAISDS.app"
    ;;
*) APP="$TARGET" ;;
esac
[ -d "$APP" ] || { echo "no app at $APP" >&2; exit 2; }

# newer A B: true if A is a later version than B
newer() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]; }

BAD=0
SUMMARY="$(mktemp)"
# `file` takes many paths per run: far faster than one process per file
find "$APP" -type f -print0 | xargs -0 file | grep "Mach-O" | sed 's/: *Mach-O.*//' | while IFS= read -r f; do
    for arch in $(lipo -archs "$f" 2>/dev/null); do
        # LC_BUILD_VERSION has `minos`; very old binaries have LC_VERSION_MIN_MACOSX with `version`
        v="$(vtool -arch "$arch" -show-build "$f" 2>/dev/null | awk '/minos/ {print $2; exit} /cmd LC_VERSION_MIN_MACOSX/ {old=1} old && /version/ {print $2; exit}')"
        [ -n "$v" ] || continue
        echo "$v" >>"$SUMMARY"
        if newer "$v" "$MAX"; then
            echo "TOO NEW  $v  $arch  ${f#$APP/Contents/}"
            echo x >>"$SUMMARY.bad"
        fi
    done
done

echo "minos of every slice in $APP (limit $MAX):"
sort -V "$SUMMARY" | uniq -c
if [ -e "$SUMMARY.bad" ]; then
    echo "FAIL: $(wc -l <"$SUMMARY.bad" | tr -d ' ') slice(s) need a newer macOS than $MAX"
    rm -f "$SUMMARY" "$SUMMARY.bad"
    exit 1
fi
rm -f "$SUMMARY"
echo "OK: nothing needs a newer macOS than $MAX"
