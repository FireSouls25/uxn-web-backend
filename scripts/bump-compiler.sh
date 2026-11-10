#!/bin/sh
# Bump the pinned etal compiler release everywhere prod reads it.
# Usage: sh scripts/bump-compiler.sh [tag]   (default: latest remote tag)
#
# Resolves the tag, downloads etal-linux-x86_64.tar.gz, smoke-tests
# the extracted binary (--list-targets), then records its sha256 in
# compiler-sha256.txt and points Dockerfile (ARG), docker-compose.yml
# and app/config.py at the new version. Future fetches (image build,
# CI, fetch-compiler.sh) verify against the recorded hash, so a bad
# bit never ships twice. Run from backend/.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
REPO="${GITHUB_REPO:-FireSouls25/uxn-dsl}"
ASSET="etal-linux-x86_64.tar.gz"

if [ $# -ge 1 ]; then
  TAG="$1"
else
  TAG=$(git ls-remote --tags "https://github.com/$REPO.git" \
    | awk -F/ '/refs\/tags\/v[0-9]/ {print $3}' | sort -V | tail -1)
  [ -n "$TAG" ] || { echo "bump-compiler: cannot resolve latest tag" >&2; exit 1; }
fi
echo "bump-compiler: target $TAG"

TMPD=$(mktemp -d)
trap 'rm -rf "$TMPD"' EXIT INT TERM
URL="https://github.com/$REPO/releases/download/$TAG/$ASSET"
curl -sSL --fail --retry 3 -o "$TMPD/$ASSET" "$URL" \
  || { echo "bump-compiler: download failed: $URL" >&2; exit 1; }
SHA=$(sha256sum "$TMPD/$ASSET" | cut -d' ' -f1)
mkdir -p "$TMPD/pkg"
tar xzf "$TMPD/$ASSET" -C "$TMPD/pkg"
BIN="$TMPD/pkg/etal"
[ -f "$BIN" ] || { echo "bump-compiler: no etal in $ASSET" >&2; exit 1; }
chmod +x "$BIN"
"$BIN" --list-targets >/dev/null 2>&1 \
  || { echo "bump-compiler: fetched etal fails --list-targets" >&2; exit 1; }
echo "bump-compiler: $TAG ok (sha256 $SHA)"

PIN="$ROOT/compiler-sha256.txt"
grep -v " $TAG $ASSET\$" "$PIN" > "$TMPD/pin" || true
printf '%s  %s %s\n' "$SHA" "$TAG" "$ASSET" >> "$TMPD/pin"
mv "$TMPD/pin" "$PIN"

sed -i "s/^ARG ETAL_VERSION=.*/ARG ETAL_VERSION=$TAG/" "$ROOT/Dockerfile"
sed -i "s/^\(\s*\)ETAL_VERSION: .*/\1ETAL_VERSION: \"$TAG\"/" "$ROOT/docker-compose.yml"
sed -i "s/^\(\s*\)ETAL_SOURCE: .*/\1ETAL_SOURCE: \"release:$TAG\"/" "$ROOT/docker-compose.yml"
sed -i "s/_env(\"ETAL_VERSION\", \"[^\"]*\")/_env(\"ETAL_VERSION\", \"$TAG\")/" "$ROOT/app/config.py"

echo "bump-compiler: pinned $TAG in compiler-sha256.txt, Dockerfile, docker-compose.yml, app/config.py"
grep " $TAG $ASSET\$" "$PIN"
