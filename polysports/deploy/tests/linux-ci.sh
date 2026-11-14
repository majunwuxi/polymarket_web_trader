#!/usr/bin/env bash
# 仅供 GitHub 托管临时 runner：测试数据和凭据均为模拟值。
set -Eeuo pipefail
[[ "${GITHUB_ACTIONS:-}" == 'true' && "${RUNNER_OS:-}" == 'Linux' && -n "${RUNNER_TEMP:-}" ]] || { echo '仅允许在隔离的 GitHub Linux runner 执行' >&2; exit 2; }
repo="$GITHUB_WORKSPACE/polysports"
cd "$repo"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx postgresql postgresql-client openssl
sudo systemctl start postgresql
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE ci_test LOGIN CREATEDB PASSWORD 'mock-ci-password';"
sudo -u postgres createdb --owner=ci_test polysports_test_ci
export POLYSPORTS_TEST_DATABASE_URL='postgresql+psycopg://ci_test:mock-ci-password@127.0.0.1:5432/polysports_test_ci'
uv sync --locked --group dev
uv run --locked pytest -q

# 全新实例：安装到模板指定路径，不接触真实 VPS 或真实凭据。
if getent passwd polysports >/dev/null || [[ -e /opt/polysports-src ]]; then echo 'runner 不是全新环境' >&2; exit 1; fi
sudo groupadd --system polysports
sudo useradd --system --gid polysports --home-dir /nonexistent --shell /usr/sbin/nologin polysports
sudo mkdir -p /opt/polysports-src /etc/polysports
sudo rsync -a --exclude=.venv --exclude=node_modules --exclude=dist --exclude=__pycache__ "$repo" /opt/polysports-src/
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE polysports LOGIN PASSWORD 'mock-app-password';"
sudo -u postgres createdb --owner=polysports polysports
cat <<'EOF' | sudo tee /etc/polysports/polysports.env >/dev/null
POLYSPORTS_DATABASE_URL=postgresql+psycopg://polysports:mock-app-password@127.0.0.1:5432/polysports
POLYSPORTS_AUTH_SECRET=mock-only-ci-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
POLYSPORTS_PUBLIC_ORIGIN=https://example.test:18443
POLYSPORTS_ENVIRONMENT=test
POLYSPORTS_COMBO_EXECUTION_ENABLED=false
EOF
sudo chmod 0600 /etc/polysports/polysports.env
printf '%s\n' '127.0.0.1:5432:*:polysports:mock-app-password' | sudo tee /etc/polysports/backup.pgpass >/dev/null
sudo chmod 0600 /etc/polysports/backup.pgpass
sudo install -d -m 0700 -o polysports -g polysports /var/backups/polysports
uv_bin=$(command -v uv)
sudo "$uv_bin" sync --locked --no-dev --python /usr/bin/python3.12 --directory /opt/polysports-src/polysports
(cd frontend && npm ci --include=dev && npm run build)
sudo mkdir -p /opt/polysports-src/polysports/frontend
sudo cp -a frontend/dist /opt/polysports-src/polysports/frontend/dist
sudo chown -R root:root /opt/polysports-src
sudo chmod -R a+rX /opt/polysports-src
sudo install -m 0644 deploy/systemd/polysports-*.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/polysports-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/polysports-migrate.service /etc/systemd/system/polysports-api.service /etc/systemd/system/polysports-worker.service /etc/systemd/system/polysports-backup.service /etc/systemd/system/polysports-backup.timer
sudo systemctl start polysports-api.service polysports-worker.service
curl --fail --silent --show-error --retry 20 --retry-delay 1 --retry-connrefused http://127.0.0.1:8000/api/v1/health/live >/dev/null
[[ "$(sudo stat -c %a /etc/polysports/polysports.env)" == '600' ]]
if sudo systemctl show polysports-api.service --property=Environment --value | grep -Fq 'mock-only-ci-'; then exit 1; fi

# 模拟未来关注任务，重启 worker 后不得提前领取。
sudo -u postgres psql -v ON_ERROR_STOP=1 -d polysports <<'SQL'
INSERT INTO teams (id,sport,source_name) VALUES ('00000000-0000-4000-8000-000000000001','football','Mock Home'),('00000000-0000-4000-8000-000000000002','football','Mock Away');
INSERT INTO games (id,provider,external_id,sport,home_team_id,away_team_id,starts_at,status) VALUES ('00000000-0000-4000-8000-000000000003','api_football','mock-fixture','football','00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000002',now()+interval '1 day','scheduled');
INSERT INTO watched_games (id,game_id,state,first_due_at) VALUES ('00000000-0000-4000-8000-000000000004','00000000-0000-4000-8000-000000000003','active',now()+interval '26 hours');
INSERT INTO jobs (id,kind,dedupe_key,watched_game_id,due_at,status) VALUES ('00000000-0000-4000-8000-000000000005','result_poll','mock-future','00000000-0000-4000-8000-000000000004',now()+interval '26 hours','pending');
SQL
sudo systemctl restart polysports-worker.service
sleep 3
[[ "$(sudo -u postgres psql -At -d polysports -c "SELECT status || ':' || attempt_count FROM jobs WHERE dedupe_key='mock-future'")" == 'pending:0' ]]

# 实际 systemd 凭据、备份服务与空库恢复。
sudo systemctl stop polysports-worker.service
sudo systemctl start polysports-backup.service
backup=$(sudo find /var/backups/polysports -maxdepth 1 -name '*.dump' -type f -print -quit)
[[ -n "$backup" ]]
sudo -u postgres createdb --owner=polysports polysports_restore
sudo systemd-run --quiet --wait --collect --uid=polysports --gid=polysports --property=LoadCredential=pgpass:/etc/polysports/backup.pgpass --setenv=PGHOST=127.0.0.1 --setenv=PGPORT=5432 --setenv=PGUSER=polysports --setenv=PGDATABASE=polysports_restore /opt/polysports-src/polysports/deploy/scripts/restore-systemd.sh "$backup"
for table in games trade_intents jobs alembic_version; do
    source_count=$(sudo -u postgres psql -At -d polysports -c "SELECT count(*) FROM $table")
    restore_count=$(sudo -u postgres psql -At -d polysports_restore -c "SELECT count(*) FROM $table")
    [[ "$source_count" == "$restore_count" ]]
done
sudo systemctl start polysports-worker.service

# 临时自签证书与高位端口只用于验证 Nginx，不向外发布服务。
mkdir -p "$RUNNER_TEMP/nginx/logs"
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$RUNNER_TEMP/nginx/key.pem" -out "$RUNNER_TEMP/nginx/cert.pem" -days 1 -subj '/CN=example.test' >/dev/null 2>&1
sed -e "s|/etc/letsencrypt/live/sports.example.com/fullchain.pem|$RUNNER_TEMP/nginx/cert.pem|g" -e "s|/etc/letsencrypt/live/sports.example.com/privkey.pem|$RUNNER_TEMP/nginx/key.pem|g" -e 's/sports.example.com/example.test/g' -e 's/listen 80;/listen 18080;/' -e 's/listen \[::\]:80;/listen [::]:18080;/' -e 's/listen 443 ssl;/listen 18443 ssl;/' -e 's/listen \[::\]:443 ssl;/listen [::]:18443 ssl;/' deploy/nginx/polysports.conf > "$RUNNER_TEMP/nginx/site.conf"
printf 'pid %s/nginx.pid; events { worker_connections 128; } http { include /etc/nginx/mime.types; include %s/site.conf; }\n' "$RUNNER_TEMP/nginx" "$RUNNER_TEMP/nginx" > "$RUNNER_TEMP/nginx/nginx.conf"
sudo nginx -t -p "$RUNNER_TEMP/nginx/" -c "$RUNNER_TEMP/nginx/nginx.conf"
sudo nginx -p "$RUNNER_TEMP/nginx/" -c "$RUNNER_TEMP/nginx/nginx.conf"
trap 'sudo nginx -p "$RUNNER_TEMP/nginx/" -c "$RUNNER_TEMP/nginx/nginx.conf" -s quit || true' EXIT
curl --fail --silent --show-error --insecure --noproxy '*' --resolve example.test:18443:127.0.0.1 https://example.test:18443/api/v1/health/live >/dev/null
curl --fail --silent --show-error --insecure --noproxy '*' --resolve example.test:18443:127.0.0.1 https://example.test:18443/history | grep -qi '<!doctype html>'
# 验证伪造的 X-Forwarded-For 被覆盖：限流记录按 127.0.0.1 生成。
curl --fail --silent --show-error --insecure --noproxy '*' --resolve example.test:18443:127.0.0.1 -c "$RUNNER_TEMP/cookies" https://example.test:18443/api/v1/auth/csrf > "$RUNNER_TEMP/csrf.json"
csrf=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["csrf_token"])' "$RUNNER_TEMP/csrf.json")
status=$(curl --silent --show-error --insecure --noproxy '*' --resolve example.test:18443:127.0.0.1 -b "$RUNNER_TEMP/cookies" -H 'Origin: https://example.test:18443' -H "X-CSRF-Token: $csrf" -H 'X-Forwarded-For: 203.0.113.44' -H 'Content-Type: application/json' -d '{"username":"mock-user","password":"mock-password"}' -o /dev/null -w '%{http_code}' https://example.test:18443/api/v1/auth/login)
[[ "$status" == '401' ]]
hash=$(python -c "import hashlib,hmac; print(hmac.new(b'mock-only-ci-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',b'rate-ip:127.0.0.1',hashlib.sha256).hexdigest())")
[[ "$(sudo -u postgres psql -At -d polysports -c "SELECT count(*) FROM auth_rate_limits WHERE bucket_key='$hash'")" == '1' ]]
echo 'Linux 部署隔离验收通过（全部凭据与数据均为模拟）。'
