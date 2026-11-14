#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
# 显式连接参数避免误用本机 PostgreSQL 的默认数据库。
: "${PGHOST:?必须设置 PGHOST}" "${PGPORT:?必须设置 PGPORT}" "${PGUSER:?必须设置 PGUSER}" "${PGDATABASE:?必须设置 PGDATABASE}" "${PGPASSFILE:?必须设置 PGPASSFILE}"
[[ "$PGPORT" =~ ^[0-9]{1,5}$ ]] && ((10#$PGPORT>=1 && 10#$PGPORT<=65535)) || { printf '%s\n' '无效的 PGPORT' >&2; exit 2; }
[[ "$PGDATABASE" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || { printf '%s\n' '数据库名必须为普通标识符' >&2; exit 2; }
[[ -f "$PGPASSFILE" && -r "$PGPASSFILE" ]] || { printf '%s\n' '无法读取 PGPASSFILE' >&2; exit 2; }
unset PGSERVICE PGSERVICEFILE PGHOSTADDR PGOPTIONS PGPASSWORD
export PGCONNECT_TIMEOUT=10

if [[ $# -ne 1 || ! -d "$1" || ! -w "$1" ]]; then
    printf '%s\n' '用法：backup.sh 已存在且可写的备份目录' >&2; exit 2
fi
backup_dir=$(cd -- "$1" && pwd -P)
tmp=$(mktemp "$backup_dir/polysports-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
trap 'rm -f -- "$tmp"' EXIT
pg_dump --no-password --format=custom --no-owner --no-acl --file="$tmp"
pg_restore --list "$tmp" >/dev/null
final="$tmp.dump"
[[ ! -e "$final" ]] || { printf '%s\n' '备份目标已存在' >&2; exit 1; }
mv -- "$tmp" "$final"
trap - EXIT
printf '备份完成：%s\n' "$final"
