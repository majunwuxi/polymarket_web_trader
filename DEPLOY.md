# PolySports 独立 VPS 部署与维护

本文针对专用 Ubuntu 24.04 LTS VPS，使用 Python 3.12、PostgreSQL 16、Nginx、systemd 和 Node.js 24。示例域名 `sports.example.com`、用户名与路径须在执行前核对；不要将本文件中的示例凭据当作真实凭据。以下操作由服务器管理员在目标 VPS 上执行，本文不会自动连接或修改服务器。

PolySports 与原 Flask Dashboard 使用独立进程和数据库。API 仅绑定 `127.0.0.1:8000`，由 Nginx 提供 HTTPS；worker 单独运行。Combo 报价对象保存在 API 进程内存，因此 API **必须为单进程**。不要在该设计下增加多个 API 实例或负载均衡。开启 Combo 时应用另有数据库会话锁，防止第二个 API 进程启动。

## 1. 前提与系统依赖

准备已解析到 VPS 的域名，允许外部访问 TCP 80/443，并确保 SSH 管理访问仍然可用；不要向公网开放 PostgreSQL 的 5432 或应用的 8000 端口。HTTP-01 证书验证需要从公网访问 80 端口；若域名配置 AAAA，IPv6 也须可达。若已存在其他网站，先检查现有 Nginx 站点和端口，避免覆盖配置。

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates xz-utils python3 python3-venv \
  postgresql postgresql-client nginx certbot openssl
sudo systemctl enable --now postgresql
python3 --version
```

前端构建需要 Node.js 24。下例从 Node.js 官方发行目录下载当前 24.x 二进制并核对其官方 SHA256 清单；支持 x86_64 或 aarch64。也可使用组织批准的 Node.js 安装方式。以下临时文件只在新建的临时目录中操作：

```bash
case "$(uname -m)" in
  x86_64) node_arch=linux-x64 ;;
  aarch64) node_arch=linux-arm64 ;;
  *) echo '不支持的架构，请按 Node.js 官方说明安装'; exit 1 ;;
esac
node_tmp=$(mktemp -d)
cd "$node_tmp"
curl -fsSLO https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt
node_archive=$(awk -v arch="$node_arch" '$2 ~ ("^node-v24\\.[0-9]+\\.[0-9]+-" arch "\\.tar\\.xz$") {print $2}' SHASUMS256.txt)
test -n "$node_archive" && test "$(printf '%s\n' "$node_archive" | wc -l)" -eq 1
curl -fsSLO "https://nodejs.org/dist/latest-v24.x/$node_archive"
grep -F " $node_archive" SHASUMS256.txt | sha256sum -c -
sudo tar -xJf "$node_archive" -C /opt
export PATH="/opt/${node_archive%.tar.xz}/bin:$PATH"
node --version && npm --version
```

在独立工具环境安装与项目锁文件兼容的 uv；实际 Python 依赖由仓库的 `uv.lock` 锁定。

```bash
sudo python3 -m venv /opt/polysports-tools
sudo /opt/polysports-tools/bin/pip install 'uv==0.11.22'
sudo /opt/polysports-tools/bin/uv --version
```

## 2. 代码、用户和数据库

以下目录是 systemd 模板中的固定路径。确认 `/opt/polysports-src` 不存在后再克隆；不要把已有目录覆盖为本项目。

```bash
sudo git clone https://github.com/majunwuxi/polymarket_web_trader.git /opt/polysports-src
sudo groupadd --system polysports
sudo useradd --system --gid polysports --home-dir /nonexistent \
  --shell /usr/sbin/nologin polysports
sudo /opt/polysports-tools/bin/uv sync --locked --no-dev --python /usr/bin/python3.12 \
  --directory /opt/polysports-src/polysports
cd /opt/polysports-src/polysports/frontend
sudo env "PATH=$PATH" npm ci --include=dev
sudo env "PATH=$PATH" npm run build
```

使用 PostgreSQL 交互式输入强密码。为避免 URL 与 `.pgpass` 转义差异，可使用随机十六进制密码；不要把密码写进命令行或 shell 历史。

```bash
sudo -u postgres createuser --no-superuser --no-createrole --no-createdb --pwprompt polysports
sudo -u postgres createdb --owner=polysports polysports
sudo install -d -m 0700 -o root -g root /etc/polysports
sudo install -m 0600 -o root -g root \
  /opt/polysports-src/polysports/.env.example /etc/polysports/polysports.env
sudoedit /etc/polysports/polysports.env
```

在受限编辑器中至少配置：

- `POLYSPORTS_ENVIRONMENT=production`。
- `POLYSPORTS_DATABASE_URL=postgresql+psycopg://polysports:<数据库密码>@127.0.0.1:5432/polysports`。密码若含 URL 保留字符，必须进行 URL 编码。
- `POLYSPORTS_PUBLIC_ORIGIN=https://sports.example.com`，与实际浏览器来源完全一致，不含路径或尾部斜线。
- `POLYSPORTS_AUTH_SECRET` 为独立生成、至少 32 字节的随机值；例如可通过 `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'` 生成后安全填写。
- 根据需要设置 API-Football、BallDontLie、钱包及 Builder 凭据。JSON 示例请见 [polysports/README.md](polysports/README.md)；包含 JSON 的 `.env` 值可用单引号包裹，文件必须为 UTF-8。Combo 默认关闭；真实启用条件见 README。

确认该文件为 `root:root`、权限 `0600`。生产服务通过 systemd `LoadCredential` 读取副本，密钥不通过 systemd `EnvironmentFile` 注入；设置了 systemd 凭据目录时，应用只读取凭据文件，不从环境变量或项目目录的 `.env` 回退。请单独安全备份该配置与密钥；数据库备份不包含它们。

## 3. 安装服务与初始化管理员

```bash
sudo install -m 0644 /opt/polysports-src/polysports/deploy/systemd/polysports-*.service /etc/systemd/system/
sudo install -m 0644 /opt/polysports-src/polysports/deploy/systemd/polysports-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start polysports-migrate.service
sudo systemctl status polysports-migrate.service --no-pager
```

迁移失败时不要启动 API/worker；查看 `journalctl -u polysports-migrate.service` 并先排查数据库连接和权限。初始化唯一管理员会在终端交互式读取密码，不把密码放在参数中：

```bash
sudo systemd-run --quiet --pty --collect --uid=polysports --gid=polysports \
  --working-directory=/opt/polysports-src/polysports \
  --property=LoadCredential=polysports.env:/etc/polysports/polysports.env \
  /opt/polysports-src/polysports/.venv/bin/python -m backend.init_admin --username admin
sudo systemctl enable --now polysports-api.service polysports-worker.service
sudo systemctl status polysports-api.service polysports-worker.service --no-pager
curl --fail http://127.0.0.1:8000/api/v1/health/live
```

初始化只允许创建一个管理员，已有管理员不会被覆盖。存活探针不代表数据库、外部数据源或交易权限就绪；登录后应在系统管理页面查看数据库、worker、同步日志及配置状态。更新凭据后需重启 API 和 worker，systemd 才会重新读取凭据副本。

## 4. Nginx、证书与 HTTPS

先安装 HTTP 验证站点，将其中 `sports.example.com` 替换为实际域名；不要直接安装需要尚不存在证书的 HTTPS 配置。

```bash
sudo install -d -m 0755 /var/lib/letsencrypt
sudo install -m 0644 /opt/polysports-src/polysports/deploy/nginx/polysports-acme.conf /etc/nginx/sites-available/polysports
sudoedit /etc/nginx/sites-available/polysports
sudo ln -s /etc/nginx/sites-available/polysports /etc/nginx/sites-enabled/polysports
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/lib/letsencrypt -d sports.example.com
```

证书成功签发后，保存当前引导配置，再安装 HTTPS 模板并将**所有** `sports.example.com` 替换为实际域名。证书路径必须与 Certbot 实际输出一致；如果 Certbot 产生不同证书目录，手工修改模板中的两个路径。

```bash
sudo cp -a /etc/nginx/sites-available/polysports /etc/nginx/sites-available/polysports.bootstrap
sudo install -m 0644 /opt/polysports-src/polysports/deploy/nginx/polysports.conf /etc/nginx/sites-available/polysports
sudoedit /etc/nginx/sites-available/polysports
sudo nginx -t
sudo systemctl reload nginx
sudo install -m 0755 /opt/polysports-src/polysports/deploy/scripts/certbot-reload-nginx.sh \
  /etc/letsencrypt/renewal-hooks/deploy/polysports-nginx
sudo certbot renew --dry-run
```

如果 `nginx -t` 失败，先修复配置，或恢复刚保存的 `polysports.bootstrap` 后重新测试；不要重载无效配置。模板固定覆盖客户端提供的 `X-Forwarded-For`，Uvicorn 只信任来自本机 Nginx 的转发头，并禁用 Nginx 向上游重试。模板假设 Nginx 直接面对客户端；如果前面还有 CDN 或其他代理，必须另行设计可信代理范围，不能直接信任任意转发头。

## 5. 备份与空库恢复

数据库备份含赛事、账户和交易历史，应保存在权限受限的本机及独立加密备份位置。备份脚本使用 PostgreSQL custom 格式、权限 `0600`，不在命令行中传递密码；它不自动删除旧备份，请自行设置保留策略并监控磁盘空间。定时备份默认每天 UTC 03:30，允许最多五分钟随机延迟。

```bash
sudo install -d -m 0700 -o polysports -g polysports /var/backups/polysports
sudo install -m 0600 -o root -g root /dev/null /etc/polysports/backup.pgpass
sudoedit /etc/polysports/backup.pgpass
```

在该文件中填写一行 `127.0.0.1:5432:*:polysports:<数据库密码>`；冒号和反斜杠需按 PostgreSQL `.pgpass` 规则转义。文件为 `root:root 0600`，由 systemd 作为凭据传给备份服务。

```bash
sudo systemctl start polysports-backup.service
sudo systemctl status polysports-backup.service --no-pager
sudo systemctl enable --now polysports-backup.timer
sudo systemctl list-timers polysports-backup.timer --all
sudo ls -l /var/backups/polysports
```

恢复只允许来自可信来源的备份，且只能写入**新建的空数据库**。下例把 `polysports_restore` 和备份路径替换为本次明确指定的目标；脚本检查目标不存在用户对象，失败则拒绝恢复，且不会执行 `--clean` 或删除数据库。

```bash
sudo -u postgres createdb --owner=polysports polysports_restore
sudo systemd-run --quiet --wait --pipe --collect --uid=polysports --gid=polysports \
  --property=LoadCredential=pgpass:/etc/polysports/backup.pgpass \
  --setenv=PGHOST=127.0.0.1 --setenv=PGPORT=5432 --setenv=PGUSER=polysports \
  --setenv=PGDATABASE=polysports_restore \
  /opt/polysports-src/polysports/deploy/scripts/restore-systemd.sh /var/backups/polysports/<实际备份文件>.dump
sudo -u postgres psql -d polysports_restore -c 'SELECT version_num FROM alembic_version;'
sudo -u postgres psql -d polysports_restore -c 'SELECT (SELECT count(*) FROM games) AS games, (SELECT count(*) FROM trade_intents) AS orders, (SELECT count(*) FROM jobs) AS jobs;'
```

定期在隔离数据库核对备份时点的赛事、订单、任务及迁移版本。正式切换前先停止 API 和 worker，核对恢复库和独立保存的配置；数据库快照之后可能发生过外部交易，必须先独立核对并对账，不能因恢复而重发订单。切换连接地址和重启服务由操作人员明确执行，不由恢复脚本自动替换现有库。

## 6. 日常维护与升级

```bash
sudo journalctl -u polysports-api.service -u polysports-worker.service --since '1 hour ago' --no-pager
sudo systemctl status polysports-api.service polysports-worker.service polysports-backup.timer --no-pager
```

升级前确认备份与恢复演练可用，记录当前 Git 提交并停止 API、worker。然后在 `/opt/polysports-src` 使用 `git pull --ff-only` 获取已审核版本，在项目目录重新运行 `uv sync --locked --no-dev`，在 `frontend/` 重新运行 `npm ci --include=dev` 和 `npm run build`。重新启动 `polysports-migrate.service` 后，再启动 API 和 worker，检查健康状态、日志及未到期任务。迁移可能不可逆；不要把 `git checkout` 当作数据库回滚，必要时恢复到新的空库并按上节核对后切换。

参考：[systemd 凭据](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)、[Nginx 反向代理](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)、[PostgreSQL pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html)、[Certbot Webroot](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot)。
