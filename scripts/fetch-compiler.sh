#!/bin/sh
# Fetch a pinned etal compiler release for this backend.
# Usage: fetch-compiler.sh [release:<tag> | release:latest]
#   ETAL_SOURCE     default strategy when no arg is given (default: local)
#   COMPILER_DIR    where to unpack (default: /opt/etal)
#   GITHUB_REPO     owner/repo (default: FireSouls25/uxn-dsl)
# Strategies:
#   local            use ETAL_BIN as-is (dev checkout); just smoke-tests it
#   release:<tag>    download etal-linux-x86_64.tar.gz for <tag>, verify
#                    sha256 against compiler-sha256.txt, unpack, smoke-test
#   release:latest   resolve the newest GitHub release (CI uses this so it
#                    always tests what users will get). A pinned hash
#                    wins when present; otherwise the asset digest that
#                    GitHub publishes for the release is verified.
# Prints the resulting binary path on stdout (for ETAL_BIN).
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMPILER_DIR="${COMPILER_DIR:-/opt/etal}"
GITHUB_REPO="${GITHUB_REPO:-FireSouls25/uxn-dsl}"
STRATEGY="${1:-${ETAL_SOURCE:-local}}"

fail() { echo "fetch-compiler: $1" >&2; exit 1; }

case "$STRATEGY" in
  local)
    BIN="${ETAL_BIN:-$ROOT/../uxn-dsl/build/linux-x86/etal}"
    [ -x "$BIN" ] || fail "local compiler not executable: $BIN"
    "$BIN" --list-targets >/dev/null 2>&1 \
      || fail "local compiler failed --list-targets: $BIN"
    echo "$BIN"
    ;;
  release:*)
    TAG="${STRATEGY#release:}"
    [ -n "$TAG" ] || fail "empty tag in release:<tag>"
    ASSET="etal-linux-x86_64.tar.gz"
    command -v curl >/dev/null 2>&1 || fail "curl is required for release fetching"
    PIN="$ROOT/compiler-sha256.txt"
    [ -f "$PIN" ] || fail "pin file missing: $PIN"
    DIGEST=""
    if [ "$TAG" = "latest" ]; then
      API="https://api.github.com/repos/$GITHUB_REPO/releases/latest"
      META=$(curl -sSL --fail --retry 3 ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} "$API") \
        || fail "cannot query $API"
      TAG=$(printf '%s' "$META" | python3 -c 'import sys,json; print(json.load(sys.stdin)["tag_name"])') \
        || fail "no tag_name in latest release"
      DIGEST=$(printf '%s' "$META" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print(next((a.get("digest") or "" for a in d["assets"] if a["name"] == sys.argv[1]), "").removeprefix("sha256:"))' "$ASSET")
      echo "fetch-compiler: latest release is $TAG" >&2
    fi
    WANT=$(awk -v tag="$TAG" -v asset="$ASSET" '$2 == tag && $3 == asset {print $1}' "$PIN")
    [ -n "$WANT" ] || WANT="$DIGEST"
    [ -n "$WANT" ] || fail "no pinned or published hash for $TAG $ASSET (refusing unverified download)"
    TMPD=$(mktemp -d)
    trap 'rm -rf "$TMPD"' EXIT INT TERM
    URL="https://github.com/$GITHUB_REPO/releases/download/$TAG/$ASSET"
    curl -sSL --fail --retry 3 -o "$TMPD/$ASSET" "$URL" \
      || fail "download failed: $URL"
    GOT=$(sha256sum "$TMPD/$ASSET" | cut -d' ' -f1)
    [ "$GOT" = "$WANT" ] || fail "hash mismatch for $TAG $ASSET (got $GOT)"
    mkdir -p "$COMPILER_DIR"
    tar xzf "$TMPD/$ASSET" -C "$COMPILER_DIR"
    BIN="$COMPILER_DIR/etal"
    chmod +x "$BIN"
    "$BIN" --list-targets >/dev/null 2>&1 \
      || fail "fetched compiler failed --list-targets: $BIN"
    echo "$BIN"
    ;;
  *)
    fail "unknown strategy '$STRATEGY' (want local, release:<tag> or release:latest)"
    ;;
esac
