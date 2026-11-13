# PolySports（6A 基础阶段）

本目录为独立的 PolySports 项目。当前阶段仅提供 FastAPI 存活探针、PostgreSQL 配置/迁移基础、测试隔离和 Vue 占位页；**赛事、管理员登录、钱包及交易尚未实现**。不需要启动仓库原有 Flask Dashboard。

## 开发环境

- Python 3.11–3.14、[uv](https://docs.astral.sh/uv/)、PostgreSQL 15+；Node.js 20.19+ 或 22.12+（实际版本应满足锁定的 Vite 要求）与 npm。
- 在本目录执行 `uv sync --locked --group dev` 安装 Python 锁定依赖；在 `frontend/` 执行 `npm ci` 安装锁定前端依赖。
- 将 `.env.example` 复制为 `.env`，设置独立 PostgreSQL 连接 `POLYSPORTS_DATABASE_URL`（格式 `postgresql+psycopg://用户名:密码@主机:端口/数据库名`）。`.env` 已被忽略；不要把真实数据库口令或其他凭证提交到 Git。环境变量优先，命令应在本目录运行以加载 `.env`。

## 迁移与启动

在本目录执行：

```bash
uv run --locked alembic upgrade head
uv run --locked uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000/api/v1/health/live` 查看进程存活；此探针不代表 PostgreSQL 或外部服务已经就绪。缺少有效数据库连接配置时服务启动失败；6A 不自动创建生产数据库或管理员。

前端另开终端，在 `frontend/` 执行 `npm run dev`；开发代理将 `/api` 转到本地 8000 端口，生产构建使用 `npm run build`。页面仅显示开发阶段说明，没有虚构赛事和真实下单按钮。

## 测试

```bash
uv run --locked pytest -q
cd frontend
npm test
npm run build
```

后端测试通过自动启用的网络保护禁止连接非 loopback 地址；上游数据必须使用明确标记的模拟数据。未设置 `POLYSPORTS_TEST_DATABASE_URL` 时迁移测试会明确显示 skipped，不能称为完整验收。迁移测试只允许 `postgresql+psycopg`、loopback 主机及名称以 `polysports_test_` 开头的**全新空数据库**；测试执行 upgrade、约束检查、downgrade、再 upgrade，并对该临时数据库产生数据和迁移变更。每次重跑请由测试环境建立一个新的空数据库，禁止指向生产或已有数据。

PowerShell 示例（需先准备自己的隔离空库）：

```powershell
$env:POLYSPORTS_TEST_DATABASE_URL = 'postgresql+psycopg://测试用户:测试密码@127.0.0.1:端口/polysports_test_临时库'
uv run --locked pytest -q
```

实际部署、认证、后台任务和真实交易测试将在后续阶段实施；本阶段的模拟测试不证明真实 API 或交易可用。
