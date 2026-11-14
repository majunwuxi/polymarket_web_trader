#!/usr/bin/env bash
set -Eeuo pipefail
: "${CREDENTIALS_DIRECTORY:?缺少 systemd 凭据目录}"
export PGPASSFILE="$CREDENTIALS_DIRECTORY/pgpass"
exec /opt/polysports-src/polysports/deploy/scripts/backup.sh /var/backups/polysports
