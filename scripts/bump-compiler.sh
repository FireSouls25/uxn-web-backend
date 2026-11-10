#!/bin/sh
# Bump the pinned etal compiler release everywhere prod reads it.
# Usage: sh scripts/bump-compiler.sh [tag]   (default: latest remote tag)
#
# Resolves the tag, downloads etal-linux-x86_64.tar.gz (and
# etal-linux-aarch64.tar.gz when the tag has it), smoke-tests the one
# matching this machine (--list-targets), then records their sha256 in
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
PIN="$ROOT/compiler-sha256.txt"
HOST="etal-linux-$(uname -m).tar.gz"

# Pin every Linux asset the tag has: x86_64 (required) and aarch64
# (ARM servers; tags before v0.1.5 have none). Only the one matching
# this machine can be smoke-tested here.
for A in "$ASSET" etal-linux-aarch64.tar.gz; do
  URL="https://github.com/$REPO/releases/download/$TAG/$A"
  if ! curl -sSL --fail --retry 3 -o "$TMPD/$A" "$URL"; then
    [ "$A" = "$ASSET" ] && { echo "bump-compiler: download failed: $URL" >&2; exit 1; }
    echo "bump-compiler: $TAG has no $A (skipped)"
    continue
  fi
  SHA=$(sha256sum "$TMPD/$A" | cut -d' ' -f1)
  rm -rf "$TMPD/pkg" && mkdir -p "$TMPD/pkg"
  tar xzf "$TMPD/$A" -C "$TMPD/pkg"
  [ -f "$TMPD/pkg/etal" ] || { echo "bump-compiler: no etal in $A" >&2; exit 1; }
  if [ "$A" = "$HOST" ]; then
    chmod +x "$TMPD/pkg/etal"
    "$TMPD/pkg/etal" --list-targets >/dev/null 2>&1 \
      || { echo "bump-compiler: fetched etal fails --list-targets ($A)" >&2; exit 1; }
  fi
  echo "bump-compiler: $TAG $A ok (sha256 $SHA)"
  grep -v " $TAG $A\$" "$PIN" > "$TMPD/pin" || true
  printf '%s  %s %s\n' "$SHA" "$TAG" "$A" >> "$TMPD/pin"
  mv "$TMPD/pin" "$PIN"
done

sed -i "s/^ARG ETAL_VERSION=.*/ARG ETAL_VERSION=$TAG/" "$ROOT/Dockerfile"
sed -i "s/^\(\s*\)ETAL_VERSION: .*/\1ETAL_VERSION: \"$TAG\"/" "$ROOT/docker-compose.yml"
sed -i "s/^\(\s*\)ETAL_SOURCE: .*/\1ETAL_SOURCE: \"release:$TAG\"/" "$ROOT/docker-compose.yml"
sed -i "s/^\(\s*\)ETAL_VERSION: .*/\1ETAL_VERSION: \"$TAG\"/" "$ROOT/deploy/aws/compose.yml"
sed -i "s/_env(\"ETAL_VERSION\", \"[^\"]*\")/_env(\"ETAL_VERSION\", \"$TAG\")/" "$ROOT/app/config.py"

echo "bump-compiler: pinned $TAG in compiler-sha256.txt, Dockerfile, docker-compose.yml, app/config.py"
grep " $TAG " "$PIN"
