#!/usr/bin/env bash
# Build the self-host release tarball: dist/lean-asset-tracker-<version>.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:-$(sed -nE 's/^VERSION = "([^"]+)"/\1/p' app/main.py)}"
[ -n "$VERSION" ] || { echo "could not read VERSION from app/main.py" >&2; exit 1; }
[ -e "dist/lean-asset-tracker-${VERSION}.tar.gz" ] && [ -z "${LAT_FORCE:-}" ] && { echo "dist/lean-asset-tracker-${VERSION}.tar.gz exists; bump VERSION or set LAT_FORCE=1" >&2; exit 1; }
STAGE="$(mktemp -d)/lean-asset-tracker"
mkdir -p "$STAGE" dist

rsync -a --exclude '__pycache__' --exclude '*.bak*' --exclude '*.pyc' app "$STAGE/"
cp Dockerfile requirements.txt LICENSE "$STAGE/"
cp selfhost/docker-compose.yml selfhost/README.md selfhost/latctl "$STAGE/"
chmod +x "$STAGE/latctl"
echo "$VERSION" > "$STAGE/VERSION"
printf '%s\n' '.env' 'backups/' '**/__pycache__' '**/*.bak*' > "$STAGE/.dockerignore"

OUT="dist/lean-asset-tracker-${VERSION}.tar.gz"
tar -C "$(dirname "$STAGE")" -czf "$OUT" lean-asset-tracker
sha256sum "$OUT" | tee "$OUT.sha256"
tar -tzf "$OUT"
