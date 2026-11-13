# PolySports（6B 管理员认证阶段）

本目录是独立的 PolySports 项目，提供单管理员登录、服务端会话、CSRF 防护和工作台基础界面。赛事、钱包和交易尚未实现，界面不显示虚构业务数据。不需要启动仓库原有 Flask Dashboard。

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

本阶段测试不证明外部赛事 API 或真实交易可用；部署、后台任务和真实交易将在后续阶段实施。
