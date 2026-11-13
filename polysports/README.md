# PolySports（6D 持久调度阶段）

本目录是独立的 PolySports 项目，提供管理员认证、足球和 NBA 赛程同步、赛事中心、球队译名维护、关注和独立后台调度。钱包和交易尚未实现，界面不显示虚构业务数据。不需要启动仓库原有 Flask Dashboard。

## 环境与配置

需要 Python 3.11–3.14、uv、PostgreSQL 15+、Node.js 22.12+（22.x）、24.x 或 26+ 和 npm（测试工具链要求）。在本目录运行 `uv sync --locked --group dev`，在 `frontend/` 运行 `npm ci`。

复制 `.env.example` 为 `.env`，设置 `POLYSPORTS_DATABASE_URL`（`postgresql+psycopg://用户名:密码@主机:端口/数据库名`）、`POLYSPORTS_PUBLIC_ORIGIN`（浏览器访问的 HTTPS 源，如 `https://sports.example.com`，不带末尾斜线或路径）和 `POLYSPORTS_AUTH_SECRET`。密钥必须是至少 32 字节的随机值，可以用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成；替换示例占位符并妥善保存。`.env` 已被 Git 忽略，不要提交真实凭据。更换密钥会使现有会话失效。环境变量优先于 `.env`；以下后端命令应在本目录运行。

## 迁移、初始化及运行

```bash
uv run --locked alembic upgrade head
uv run --locked python -m backend.init_admin --username 管理员用户名
uv run --locked uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

初始化命令交互式要求输入两次至少 12 个字符的密码，不通过命令行参数传递密码。系统只允许一个管理员。健康探针为 `http://127.0.0.1:8000/api/v1/health/live`，仅表示进程存活，不代表数据库就绪。

前端在 `frontend/` 执行 `npm run dev`，开发代理将 `/api` 转至本地 8000 端口；生产构建执行 `npm run build`。登录 Cookie 设置了 Secure，访问登录页面须通过可信 HTTPS 反向代理，并且浏览器实际源须与 `POLYSPORTS_PUBLIC_ORIGIN` 完全一致。反向代理应只向应用传递经过可信代理验证的客户端地址，不能信任任意客户端自带的转发头，否则按 IP 限流会失准。正式 Nginx 和 systemd 配置属于后续部署阶段。

## 赛事数据同步

在 `.env` 中设置 `POLYSPORTS_API_FOOTBALL_KEY` 和 `POLYSPORTS_BALLDONTLIE_KEY`，密钥只从本机环境加载，不写入数据库或 Git。根据供应商账户套餐与数据覆盖范围配置。迁移完成后，可手动同步：

```bash
uv run --locked python -m backend.sync_cli football
uv run --locked python -m backend.sync_cli nba
```

每次调用以实际开始时间为起点，获取并精确过滤未来 36 小时；分页失败、来源限流、无权限或数据字段异常会明确记为失败，不将已有赛事删除或伪造结果。来源状态和同步时间可在赛事中心查看；中文名称由管理员核实维护，缺失队徽以占位显示。赛事和修改历史持续保留。也可在系统管理页面提交同步任务。自动同步需要启动独立 worker，默认按 UTC 00:00、08:00 分别同步两项运动，可在页面修改。NBA 同步会额外请求默认不包含的季前赛；NBA 来源可能不提供队徽，界面会标记待补充。真实接口联调依赖用户提供有效 API key 和套餐，本阶段自动测试均使用模拟数据。

接口依据：[API-Football fixtures](https://www.api-football.com/documentation-v3) 和 [BALLDONTLIE NBA games](https://docs.balldontlie.io/#get-all-games)。

## 后台任务和关注

完成数据库迁移后，在本目录的独立终端运行：

```bash
uv run --locked python -m backend.worker
```

worker 通过 PostgreSQL 生成和领取持久任务，允许多个实例并发运行；进程退出或机器重启后重新启动 worker 即可恢复到期任务。任务只会在数据库时间达到截止时间后被领取，过期租约的旧结果不会写入。系统管理页面提供计划修改、worker 心跳、任务和尝试记录；实际 VPS 的 systemd 服务配置在部署阶段提供。

在赛事中心选择“关注赛事”后，足球首次赛果查询为开赛后 120 分钟，未完赛每 10 分钟查询；NBA 首次为开赛后 159 分钟，未完赛每 3 分钟查询。来源确认完赛后停止常规查询。延期、来源失败和限流将保留关注与任务记录；若来源提供 `Retry-After`，任务不会早于其指定时间重试。用户下单后自动关注将在交易阶段接入。

## 测试

```bash
uv run --locked pytest -q
cd frontend
npm test
npm run build
npm run test:browser
```

浏览器测试需要 Playwright Chromium（`npx playwright install chromium`），也可以通过 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指向已安装的 Chromium。浏览器测试拦截所有 API 并使用明确标识的模拟数据，不访问真实服务。

后端测试自动禁止访问非 loopback 网络。未设置 `POLYSPORTS_TEST_DATABASE_URL` 时数据库集成测试会跳过，不能称为完整验收。集成测试只允许 `postgresql+psycopg`、loopback 主机及名称以 `polysports_test_` 开头的**全新空数据库**；测试执行迁移、约束和认证测试，会修改该临时数据库。每次重跑应先建立新的空测试库，禁止指向生产或已有数据。

```powershell
$env:POLYSPORTS_TEST_DATABASE_URL = 'postgresql+psycopg://测试用户:测试密码@127.0.0.1:端口/polysports_test_临时库'
uv run --locked pytest -q
```

本阶段测试不证明外部赛事 API 或真实交易可用；独立 VPS 部署和真实交易将在后续阶段实施。
