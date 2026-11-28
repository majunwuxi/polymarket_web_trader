# PolySports 独立 VPS 部署与维护

本文针对专用 Ubuntu 24.04 LTS VPS，使用 Python 3.12、PostgreSQL 16、Nginx、systemd 和 Node.js 24。示例域名 `sports.example.com`、用户名与路径须在执行前核对；不要将本文件中的示例凭据当作真实凭据。以下操作由服务器管理员在目标 VPS 上执行，本文不会自动连接或修改服务器。

隔离验收已在 [Ubuntu 24.04 CI](https://github.com/majunwuxi/polymarket_web_trader/actions/runs/43538150858) 完成：使用明确标识的模拟凭据和临时数据库，验证 110 项后端测试、数据库迁移、systemd 启动与任务重启、实际备份和空库恢复、Nginx HTTPS 与反向代理。CI 使用自签名测试证书和预装依赖；实际 VPS、域名解析及 Let's Encrypt 证书签发仍须在目标环境按本文操作并验收。

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

前端构建需要 Node.js 24。克隆项目后，下一节提供一条脚本命令安装并校验 Node.js；支持 Linux x86_64 和 aarch64。也可以使用已安装的兼容版本。

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
sudo bash /opt/polysports-src/polysports/deploy/scripts/install-node.sh
export PATH="/opt/polysports-node/bin:$PATH"
node --version && npm --version
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
- 赛程同步不依赖第三方赛事密钥；仅在需要订单成交后的赛果绑定与比分核验时设置 `POLYSPORTS_API_FOOTBALL_KEY` 和 `POLYSPORTS_BALLDONTLIE_KEY`。另按需设置钱包凭据 `POLYSPORTS_WALLETS` 与 `POLYSPORTS_WALLET_CREDENTIALS`。JSON 示例请见 [polysports/README.md](polysports/README.md)；包含 JSON 的 `.env` 值可用单引号包裹，文件必须为 UTF-8。Combo 使用官方 Requester Gateway，不需要 Builder 凭据，且默认关闭；真实启用条件见 README。
- 已结算仓位赎回默认关闭。确认钱包已具备 Router/Adapter 的 ERC-1155 operator 授权、Polygon RPC 可访问，且智能钱包已配置完整 Builder 凭据或 `relayer_api_key` + `relayer_api_key_address` 后，才设置 `POLYSPORTS_REDEEM_EXECUTION_ENABLED=true`。系统不会自动授权、部署钱包或重发结果未知的赎回请求。
- 可选买入限额：`POLYSPORTS_ORDER_LIMIT_PERCENT`（单笔按可用余额百分比封顶，>0 且 ≤100）、`POLYSPORTS_ORDER_LIMIT_FLOOR_USD`（最低上限，默认 1）、`POLYSPORTS_MAX_DAILY_USD`（每个钱包每日买入总额）。单场与组合共用日额度，按 UTC+8 午夜重置；留空表示不启用对应限制。
- 可选只读 Agent：`POLYSPORTS_AGENT_READ_TOKEN`（至少 32 字符）。仅当需要 MCP/Codex 读取项目赛事、钱包余额和限额时设置；令牌校验失败会按来源 IP 限流并记录结构化日志。修改后重启 API 生效。

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
curl --retry 15 --retry-delay 1 --retry-connrefused --retry-all-errors --connect-timeout 2 --fail http://127.0.0.1:8000/api/v1/health/live
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

数据库备份含赛事、账户和交易历史，应保存在权限受限的本机及独立加密备份位置。备份脚本使用 PostgreSQL custom 格式、权限 `0600`，不在命令行中传递密码；成功备份后会自动删除超过保留期的 `polysports-*.dump`，默认 30 天，可用 `BACKUP_RETENTION_DAYS` 调整为 14–30 天，仍应监控磁盘空间并保留独立异地备份。定时备份默认每天 UTC 03:30，允许最多五分钟随机延迟。

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

旧版仓库根目录的 Dashboard 入口默认只监听 127.0.0.1。若仍通过 HTTPS 反向代理使用旧版，在其运行环境中设置 `DASHBOARD_PUBLIC_ORIGIN=https://实际域名`，并在代理中透传原始 Host。旧版登录限流按 Flask 收到的连接地址计数。PolySports 使用独立的 `POLYSPORTS_PUBLIC_ORIGIN` 配置。

升级前确认备份与恢复演练可用，记录当前 Git 提交并停止 API、worker。然后在 `/opt/polysports-src` 使用 `git pull --ff-only` 获取已审核版本，在项目目录重新运行 `uv sync --locked --no-dev`，在 `frontend/` 重新运行 `npm ci --include=dev` 和 `npm run build`。用 `systemctl restart polysports-migrate.service`（**不是 `start`**）重新执行迁移后，再启动 API 和 worker，检查健康状态、日志及未到期任务。迁移可能不可逆；不要把 `git checkout` 当作数据库回滚，必要时恢复到新的空库并按上节核对后切换。

**关键：`frontend/dist/` 被 `.gitignore` 排除，`git pull` 只更新源码，不会更新浏览器实际加载的静态文件。** 每次拉取后都必须在 `frontend/` 重新执行 `npm run build`；否则页面仍是旧版本，看起来像「更新没生效」。`npm run build` 由 `vue-tsc --noEmit && vite build` 组成，两者以及 TypeScript 都在 `devDependencies` 中，所以 `npm ci --include=dev` 不能省略；Node 24 安装在 `/opt/polysports-node/bin`，新 SSH 会话必须先设置 PATH，否则 `npm: command not found` 且旧 `dist` 会被原样保留。完整升级命令：

```bash
cd /opt/polysports-src
sudo git rev-parse HEAD                          # 记录升级前版本
sudo systemctl start polysports-backup.service    # 升级前备份
sudo systemctl stop polysports-api.service polysports-worker.service
sudo git pull --ff-only origin main
sudo git rev-parse HEAD                          # 必须等于已审核的目标提交

export PATH="/opt/polysports-node/bin:$PATH"     # Node 24 不在系统默认 PATH
node --version && npm --version

sudo /opt/polysports-tools/bin/uv sync --locked --no-dev --python /usr/bin/python3.12 \
  --directory /opt/polysports-src/polysports
cd /opt/polysports-src/polysports/frontend
sudo env "PATH=$PATH" npm ci --include=dev       # vue-tsc / vite 只在 devDependencies
sudo env "PATH=$PATH" npm run build              # 必须成功；失败时旧 dist 保留，页面不变

# 构建校验：index.html 引用的 JS 必须存在，且产物包含当前版本特征字符串
sudo ls -l --time-style=long-iso /opt/polysports-src/polysports/frontend/dist/assets/
sudo grep -o 'assets/index-[A-Za-z0-9_-]*\.js' /opt/polysports-src/polysports/frontend/dist/index.html
sudo grep -rl '按赛果判断' /opt/polysports-src/polysports/frontend/dist/assets/*.js

cd /opt/polysports-src
sudo systemctl restart polysports-migrate.service  # 必须用 restart；start 对已 active(exited) 的 oneshot 服务是 no-op
sudo systemctl status polysports-migrate.service --no-pager   # Active 时间必须是本次，日志含 Running upgrade ... -> 目标 revision
sudo -u postgres psql -d polysports -c 'SELECT version_num FROM alembic_version;'   # 应与仓库迁移 head 一致
sudo -u postgres psql -d polysports -c "SELECT to_regclass('public.combo_fills');"  # 含新增表时必须非 NULL
sudo systemctl restart polysports-api.service polysports-worker.service
curl --retry 15 --retry-delay 1 --retry-connrefused --retry-all-errors --connect-timeout 2 --fail http://127.0.0.1:8000/api/v1/health/live
sudo journalctl -u polysports-api.service -u polysports-worker.service --since '10 min ago' --no-pager
```

本次安全与事务修复版本还需额外执行以下步骤（无新增数据库迁移）：

```bash
cd /opt/polysports-src
# systemd 单元新增了 AGENT_READ_TOKEN 与交易限额变量的 UnsetEnvironment，必须重新安装并重载
sudo install -m 0644 polysports/deploy/systemd/polysports-*.service /etc/systemd/system/
sudo systemctl daemon-reload
# Nginx 模板新增 Content-Security-Policy，但模板里的 sports.example.com 只是占位符。
# 已有站点不要直接覆盖：先备份，装入后必须替换为实际域名，再校验和重载。
sudo cp -a /etc/nginx/sites-available/polysports /etc/nginx/sites-available/polysports.bak
sudo install -m 0644 polysports/deploy/nginx/polysports.conf /etc/nginx/sites-available/polysports
sudo ls -1 /etc/letsencrypt/live/          # 确认真实证书目录名
sudo sed -i 's/sports.example.com/<实际域名>/g' /etc/nginx/sites-available/polysports
sudo nginx -t
sudo systemctl reload nginx
```

如果 `nginx -t` 报 `cannot load certificate "/etc/letsencrypt/live/sports.example.com/fullchain.pem"`，说明忘记了把占位域名替换成实际域名：此时磁盘上的配置无效，但正在运行的 Nginx 仍使用旧配置；修正域名或从 `polysports.bak` 恢复后再重载，不要重启 Nginx。

Nginx 模板 CSP 的 `img-src` 已放行队徽实际使用的 `media.api-sports.io`、`cdn.nba.com`、`polymarket-upload.s3.*.amazonaws.com`、`upload.wikimedia.org` 与 `thumb.wikimedia.org`。新增其他图片来源时必须同步扩充该列表，否则图片会被浏览器拦截并显示为“队徽待补充”。

生产环境中，直接访问 API 的 `/docs`、`/redoc` 与 `/openapi.json` 会返回 404，这是预期行为；开发/测试环境仍可访问。注意经 Nginx 的 `https://域名/docs` 属于前端 SPA 路由（Nginx 只把 `/api/` 代理给 FastAPI），会返回 `index.html` 的 200，不能据此判断 API 文档是否暴露；应直接检查 `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/docs`。MCP 分发方式也有变化：仓库不再提供 `PolySports-MCP-Windows.zip`，客户端需从源码运行并设置 `POLYSPORTS_BASE_URL`（HTTPS 站点地址），详见 [polysports/POLYSPORTS-MCP.md](polysports/POLYSPORTS-MCP.md)。该域名曾出现在公开仓库历史中，建议在 Nginx 前叠加来源 IP 白名单或 Zero Trust，并轮换 `POLYSPORTS_AGENT_READ_TOKEN`。

**远程历史被重写时**：若上游执行过 `git push --force`（例如清除已泄露信息），VPS 上的 `git pull --ff-only` 会因本地与远程历史分叉而失败。确认 `/opt/polysports-src` 没有本地改动后，改用下面的命令对齐到新历史，不要再 merge/rebase 把旧提交带回：

```bash
cd /opt/polysports-src
sudo git fetch origin
sudo git reset --hard origin/main
```

对齐后仍须按上方流程重新构建前端、重新安装 systemd/Nginx 配置并重启服务。

后端 `.py` 改动必须重启 `polysports-api.service` 与 `polysports-worker.service` 才会生效；仅 `git pull` 不会重载已加载的模块。若该版本包含新增表（例如 `combo_fills`），迁移未成功就启动 API，列表接口会直接报 `relation "combo_fills" does not exist`；此时先修复迁移，不要靠重启 API 重试。Nginx 的 `index.html` 应设为不缓存（见 `deploy/nginx/polysports.conf` 的 `location = /index.html`）；更新该配置后需重新安装并执行 `sudo nginx -t && sudo systemctl reload nginx`。浏览器仍显示旧界面时，先用无痕窗口或强制刷新（Ctrl+Shift+R）确认；带 hash 的 JS 可长期缓存，入口 `index.html` 不可缓存。

**升级时 `systemctl start` 不会重跑迁移**：`polysports-migrate.service` 为 `Type=oneshot` + `RemainAfterExit=yes`，首次成功后长期保持 `active (exited)`；此时 `systemctl start` 被 systemd 视为已启动而直接返回，`ExecStart` 不会再次执行。典型症状是 `systemctl status` 的 Active 时间仍是几小时前、日志停在上一版 `Running upgrade ...`、`SELECT version_num FROM alembic_version` 不前进，随后 `SELECT count(*) FROM combo_fills` 报 `relation "combo_fills" does not exist`，API 的列表接口 500。含新迁移的升级必须用 `systemctl restart polysports-migrate.service`（或先 `stop` 再 `start`），并逐项核对：status 的 Active 时间是本次、日志出现 `Running upgrade ... -> <目标 revision>`、数据库 `version_num` 等于仓库 `alembic heads`、新表存在。首次安装时服务处于 inactive，`start` 才有效（见第 3 节）。未确认迁移成功前不要启动 API，也不要靠反复重启 API 来绕过缺表错误。

**健康检查不要用裸 `curl`**：`polysports-api.service` 是 `Type=simple`，systemd 打印 `Started` 只表示进程已 exec，真正监听要等 uvicorn 完成模块导入与 startup（实测约 5 秒以上，迁移后首次启动可能更久）。此时裸 `curl --fail` 会得到 `curl: (7) Failed to connect`，容易被误判为部署失败。用带 `--retry-connrefused` 的重试，或先确认 `journalctl -u polysports-api.service` 出现 `Application startup complete` 与 `Uvicorn running on http://127.0.0.1:8000`；只有状态为 `failed`/`auto-restart` 且反复出现 `Scheduled restart job` 才是真正的启动失败，需按日志里的 traceback 排查。

参考：[systemd 凭据](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)、[Nginx 反向代理](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)、[PostgreSQL pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html)、[Certbot Webroot](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot)。
