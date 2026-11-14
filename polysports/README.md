# PolySports

本目录是独立的 PolySports 项目，提供管理员认证、足球和 NBA 赛程同步、赛事中心、球队译名维护、关注、独立后台调度、Polymarket 市场映射、只读钱包状态、单场限价订单及 Combo RFQ 组合交易。界面不显示虚构业务数据。不需要启动仓库原有 Flask Dashboard。测试记录和真实环境验证边界见 [ACCEPTANCE.md](../ACCEPTANCE.md)。

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

前端在 `frontend/` 执行 `npm run dev`，开发代理将 `/api` 转至本地 8000 端口；生产构建执行 `npm run build`。登录 Cookie 设置了 Secure，访问登录页面须通过可信 HTTPS 反向代理，并且浏览器实际源须与 `POLYSPORTS_PUBLIC_ORIGIN` 完全一致。反向代理应只向应用传递经过可信代理验证的客户端地址，不能信任任意客户端自带的转发头，否则按 IP 限流会失准。正式 Nginx、systemd、凭据和备份恢复步骤见 [DEPLOY.md](../DEPLOY.md)。

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

在赛事中心选择“关注赛事”后，足球首次赛果查询为开赛后 120 分钟，未完赛每 10 分钟查询；NBA 首次为开赛后 159 分钟，未完赛每 3 分钟查询。来源确认完赛后停止常规查询。延期、来源失败和限流将保留关注与任务记录；若来源提供 `Retry-After`，任务不会早于其指定时间重试。单场提交意图与关注任务在同一数据库事务中创建。

## Polymarket 市场与钱包

在赛事中心打开详情，选择“搜索候选市场”，然后使用“检查搜索状态”查看结果。系统查询 Polymarket Gamma 的体育事件，按运动、球队、主客顺序和相近开赛时间形成候选；即使名称相似也不会自动确认。请逐项核对来源球队、开赛时间和市场问题，确认或拒绝映射。来源错误、赛事改期或数据超过 15 分钟会使映射不可用于交易；需重新搜索核对。已确认映射可进入单场订单预览；预览和提交时仍需重新核验来源、钱包和盘口。

市场 ID、condition ID、资产 ID 分别保存；对于来源标识为 v1 的市场使用 `clobTokenIds`，v2 使用 `positionIds`。协议版本、组合能力或资产未确认时保持未知。资产变化会使刷新失败并保留旧记录供核查。自动测试只使用明确标记的模拟上游；实际下单仅在管理员确认后执行。

`POLYSPORTS_WALLETS` 是 `.env` 中的 JSON 数组，只接收 `label`、`wallet_address`、`signer_address`、`wallet_type` 和可选的 `credential_ref`，禁止包含私钥；未配置时为 `[]`。可使用如下**模拟配置格式**，部署时必须替换为自己核实的钱包地址：

```dotenv
POLYSPORTS_WALLETS=[{"label":"示例","wallet_address":"0x1111111111111111111111111111111111111111","signer_address":"0x2222222222222222222222222222222222222222","wallet_type":"POLY_PROXY","credential_ref":"primary"}]
```

钱包页面只读展示配置；配置有效凭据时可只读查询 pUSD 余额，未能确认的信息保持未知。每次下单会按选定资产和交易所核验余额与授权，不会自动设置授权或部署钱包。地区受限或无法核验时拒绝下单。

官方参考：[Gamma 事件游标分页](https://docs.polymarket.com/api-reference/events/list-events-keyset-pagination)、[市场字段](https://docs.polymarket.com/api-reference/markets/list-markets)、[Protocol V2 迁移](https://docs.polymarket.com/migrate/polymarket-v2/api-integrations)。

## 单场订单与凭据

真实资金单场交易需要使用独立的受限 `.env` 或环境变量配置 `POLYSPORTS_WALLET_CREDENTIALS` JSON 对象，其键对应钱包的 `credential_ref`。每项包含 `private_key`；CLOB 的 `clob_api_key`、`clob_api_secret`、`clob_api_passphrase` 必须全部提供或全部省略，省略时由官方 SDK 获取。智能钱包还须配置 `POLYSPORTS_POLYGON_RPC_URL`，且钱包必须已部署在 Polygon 主网。**不要把真实私钥、凭证或带密钥的 RPC 地址提交到 Git，也不要在浏览器页面输入私钥。**

格式示意（尖括号为占位符，不是可用凭据）：

```dotenv
POLYSPORTS_WALLET_CREDENTIALS={"primary":{"private_key":"<仅在本机安全配置>","clob_api_key":"<可选>","clob_api_secret":"<可选>","clob_api_passphrase":"<可选>"}}
POLYSPORTS_POLYGON_RPC_URL=<仅在本机安全配置>
```

在赛事详情确认映射后选择某个结果进入订单预览，输入方向、限价和份额。确认页展示钱包、赛事、选项、金额与价格；点击确认会提交**真实资金订单**。系统在提交前重新核验地区、钱包类型、盘口、余额与授权；价格或盘口变化须重新预览。订单被接受并不代表成交，订单状态和逐笔成交以交易所返回的证据为准。

订单提交或撤单发生超时时，不要重复提交；在交易记录中按原幂等键查询并安排只读对账。若交易所订单编号未知，可只读查询相似的未完成订单；相似并不证明其属于本次提交，只有独立核实后才能手动关联编号。未找到候选不代表订单不存在。后台对账需要运行独立 worker。

本阶段自动化测试使用模拟交易所和测试专用私钥，绝不发送真实订单。真实钱包、额度、签名和部署环境仍需在用户自己的目标环境中单独核验。

## 组合交易

组合交易默认关闭。使用前须取得官方批准的 Builder 凭证，并在钱包 `credential_ref` 对应的 `POLYSPORTS_WALLET_CREDENTIALS` 项中同时配置 `builder_api_key`、`builder_api_secret`、`builder_api_passphrase`。钱包必须为已部署的 Deposit Wallet，并配置 Polygon 主网 RPC；在服务器上完成相应余额和 Exchange V3 授权后，通过受限环境变量设置 `POLYSPORTS_COMBO_EXECUTION_ENABLED=true`。不要提交任何真实凭证。FastAPI 必须单进程运行；启用组合功能时应用使用数据库会话锁拒绝第二个 API 进程。后台对账仍由独立 worker 执行。

在赛事中心确认映射并将不同市场的结果加入组合；系统从官方 Combo 市场目录核对每条腿，至少两腿。先请求真实报价，核对全部赛事、钱包、价格、金额和实际上游截止时间，再主动接受。系统按报价重新核验余额、授权、凭证及映射；不会自动部署钱包、授权或重发接受请求。报价对象仅驻留 API 进程内存，不写入数据库或浏览器；重启后未接受的报价不可再接受，应等待过期后重新询价。执行中不代表成交，只有上游返回确认或成交状态才标为已确认。创建或接受结果未知时不要另换请求键重试；系统保留未知状态并对已知 RFQ ID 只读对账。

组合功能的自动化测试使用模拟上游，不发送真实订单；Builder 权限、账户及真实资金端到端交易尚未验证。官方文档：[Combos for Builders](https://docs.polymarket.com/trading/combos/builders)。

## 历史、统计和导出

“历史与导出”按运动、UTC+8 日期、赛事状态、钱包、交易类型及交易状态筛选；结束日期不含当天。赛事按开赛时间筛选，交易按创建时间筛选。导出采用最近一次应用的筛选条件，覆盖所有匹配记录而不是当前页面；CSV 和 Excel 的时间列标注 UTC+8，文本字段防电子表格公式注入。Excel 将标识作为文本保存，避免大整数精度丢失。

统计的单场成交只包括本地已保存、上游状态为 CONFIRMED 的成交记录；组合确认数单独列出，不等于单场成交数。当前没有完整的成本、费用和结算证据，因此已实现收益显示未知，导出文件中的收益空白表示未知。钱包、交易类型和交易状态筛选只影响交易统计，不影响赛事总数。系统管理页提供数据源非秘密配置状态、同步日志、worker 心跳、任务与审计记录。

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

自动测试不证明外部赛事 API、真实钱包权限或真实资金交易可用；部署与恢复说明见 [DEPLOY.md](../DEPLOY.md)。
