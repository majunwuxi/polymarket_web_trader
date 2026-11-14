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

if [[ $# -ne 1 || ! -f "$1" || ! -r "$1" ]]; then
    printf '%s\n' '用法：restore-empty.sh 可信的备份文件' >&2; exit 2
fi
pg_restore --list "$1" >/dev/null
# 不允许恢复到已有对象、扩展或非系统 schema 的数据库。
objects=$(psql --no-password -X -A -t -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\') + (SELECT count(*) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\') + (SELECT count(*) FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE t.typrelid=0 AND t.typelem=0 AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\') + (SELECT count(*) FROM pg_catalog.pg_namespace WHERE nspname NOT IN ('public','pg_catalog','information_schema') AND nspname NOT LIKE 'pg\_%' ESCAPE '\') + (SELECT count(*) FROM pg_catalog.pg_extension WHERE extname <> 'plpgsql');")
[[ "$objects" == '0' ]] || { printf '%s\n' '目标数据库不是空库，拒绝恢复' >&2; exit 1; }
pg_restore --no-password --exit-on-error --single-transaction --no-owner --no-acl --dbname="$PGDATABASE" "$1"
printf '%s\n' '恢复完成；请检查迁移版本及关键数据后再进行人工切换。'
