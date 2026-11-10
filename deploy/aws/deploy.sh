#!/bin/sh
# Ship the backend to the VM and (re)start it.
# Usage (from backend/): sh deploy/aws/deploy.sh <host-ip> [ssh-key]
# Copies the committed backend tree plus backend/.env (as-is), builds
# the image there (it fetches the pinned compiler release) and
# restarts the stack. With UXN_DSL=<checkout> it also ships that
# uxn-dsl tree, for compose files pointed at Dockerfile.source.
set -eu
HOST="$1"
KEY="${2:-$HOME/.ssh/uxn-backend.pem}"
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DSL="${UXN_DSL:-}"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new ubuntu@$HOST"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/backend" "$TMP/uxn-dsl"
git -C "$ROOT" archive HEAD | tar -x -C "$TMP/backend"
[ -z "$DSL" ] || git -C "$DSL" archive HEAD | tar -x -C "$TMP/uxn-dsl"
tar -czf "$TMP/src.tgz" -C "$TMP" backend uxn-dsl
scp -i "$KEY" -q "$TMP/src.tgz" "ubuntu@$HOST:/tmp/src.tgz"
scp -i "$KEY" -q "$ROOT/.env" "ubuntu@$HOST:/tmp/backend.env"
$SSH 'set -eu
sudo mkdir -p /opt/uxn && sudo chown ubuntu /opt/uxn && cd /opt/uxn
rm -rf backend uxn-dsl && tar -xzf /tmp/src.tgz && rm /tmp/src.tgz
cd backend/deploy/aws
mkdir -p /opt/uxn/state && mv /tmp/backend.env /opt/uxn/state/backend.env && chmod 600 /opt/uxn/state/backend.env
ln -sf /opt/uxn/state/backend.env backend.env
if [ ! -f /opt/uxn/state/prod.env ]; then
  IP=$(curl -s http://checkip.amazonaws.com); DOMAIN=$(echo "$IP" | tr . -).sslip.io
  PW=$(openssl rand -hex 24)
  umask 077; cat > /opt/uxn/state/prod.env <<ENV
DOMAIN=$DOMAIN
PUBLIC_BASE_URL=https://$DOMAIN
POSTGRES_PASSWORD=$PW
DATABASE_URL=postgresql+psycopg://uxn:$PW@db:5432/uxnweb
JWT_SECRET=$(openssl rand -hex 32)
ENV
fi
ln -sf /opt/uxn/state/prod.env prod.env
# The bundled Postgres only runs when the app is pointed at it.
if grep -q "^DATABASE_URL=.*@db:" prod.env; then PROFILE="--profile localdb"; else PROFILE=""; fi
docker compose $PROFILE up -d --build --remove-orphans
docker image prune -f >/dev/null'
echo "deployed: https://$($SSH "grep ^DOMAIN= /opt/uxn/state/prod.env | cut -d= -f2")"
