#!/usr/bin/env bash
set -Eeuo pipefail
: "${CREDENTIALS_DIRECTORY:?缺少 systemd 凭据目录}"
umask 077
PGPASSFILE=$(mktemp "${TMPDIR:-/tmp}/polysports-pgpass.XXXXXX")
trap 'rm -f -- "$PGPASSFILE"' EXIT
cat -- "$CREDENTIALS_DIRECTORY/pgpass" > "$PGPASSFILE"
chmod 600 "$PGPASSFILE"
export PGPASSFILE
/opt/polysports-src/polysports/deploy/scripts/restore-empty.sh "$@"
