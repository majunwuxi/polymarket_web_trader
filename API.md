# PolySports 内部 API 设计

本文定义 PolySports 浏览器与 FastAPI 服务之间的内部接口契约，供后续实现和 UI 设计使用。本文包含已实现接口和后续阶段的设计；具体可用接口以运行中的 `/openapi.json` 及各阶段记录为准。下文 JSON、标识符和时间均为明确标注的模拟示例，不代表真实赛事、价格或成交。架构和持久化依据分别见 [ARCHITECTURE.md](ARCHITECTURE.md) 与 [DATABASE.md](DATABASE.md)。具体第三方数据字段、Combo 权限和交易能力以接入时的官方接口及实际账户为准。

## 1. 通用约定

### 1.1 地址、格式与认证

- 所有路径以前缀 `/api/v1` 开始；部署时 Nginx 将 HTTPS 同源请求转发给 FastAPI。响应采用 UTF-8 JSON，下载端点例外。
- 除 `GET /health/live`、`GET /auth/csrf`、`POST /auth/login` 外，所有端点须有有效管理员会话。只有一个管理员；不提供公开注册或多租户接口。
- 登录成功由服务端设置 `HttpOnly; Secure; SameSite=Strict; Path=/` 会话 Cookie；服务器仅保存随机令牌的摘要。会话过期或撤销后返回 `401`。代理只接受可信 Nginx 传入的客户端来源信息。
- 状态修改请求（`POST`、`PATCH`、`PUT`、`DELETE`）须校验同源 `Origin` 和 `X-CSRF-Token`。登录前先调用 `GET /auth/csrf` 获取短期匿名 CSRF 令牌及对应的 HttpOnly 关联 Cookie；登录成功后轮换为新会话及新的 CSRF 令牌。CSRF 令牌由服务端密钥和当前原始会话 Cookie 派生并绑定会话；数据库只保存摘要，`GET /auth/session` 可在当前会话内重新派生该令牌，不需要存储明文。匿名登录前的关联 Cookie 使用独立短期签名令牌。CSRF 令牌仅用于当前关联会话，状态变更失败不泄漏有效令牌。登录和认证失败按 IP 与账户等维度在共享存储限流，不能只依赖进程内计数。
- 内部对象 ID、客户端幂等键为 UUID 字符串；Gamma ID、condition ID、asset ID、订单 ID 和 RFQ ID 为不透明字符串，禁止 JavaScript 数值化。所有时间以 RFC 3339 UTC 字符串传输（如 `2026-01-01T00:00:00Z`），显示层另转固定 UTC+8；日期范围约定左闭右开 `[from, to)`。
- 价格、份额、金额、费用使用十进制字符串，例如 `"0.52"`、`"10.00"`；不能使用 JSON number 或科学计数法。服务端按 `numeric(38,18)` 的范围校验并按市场/SDK 规则进一步限制。未知值为 `null`，不能用 0 表示。参数超限报错，不静默舍入。时间、市场、订单簿与报价不满足确认条件时不得提交。
- 常规成功响应为 `{"data": ..., "request_id": "..."}`；无主体时允许 `204`。文件下载不使用 JSON 包装。所有响应返回 `X-Request-ID`；客户端可提供合法 UUID 作为关联 ID，但它不是幂等键。
- 列表响应 `data` 为 `{ "items": [...], "next_cursor": null }`，默认 `limit=50`、最大 100；游标不透明，查询条件不变地传入下一次请求。具体排序在各端点指定，按稳定唯一 ID 打破并列。游标过期或参数不匹配返回 `400`。数据源的外部分页与此客户端分页相互独立。
- 仅展示已验证、白名单化的数据字段；不得向浏览器、日志、异常或导出暴露私钥、API secret、passphrase、完整签名、Cookie 或未脱敏上游响应。标识和名称即使来自管理员或上游，也须经安全编码后展示。

### 1.2 错误响应

所有应用错误统一返回：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数不符合要求",
    "details": {"fields": [{"field": "limit_price", "reason": "INVALID_TICK"}]},
    "retry_after_seconds": null
  },
  "request_id": "00000000-0000-4000-8000-000000000001"
}
```

上述内容为模拟示例。`details` 只含安全的字段级原因；未知或不适用的 `retry_after_seconds` 为 `null`。当有可靠的非负重试时间时，响应同时设置 `Retry-After` 秒数（向上取整）。错误消息不得包含私钥、凭证或上游原始异常。

| HTTP | 代码示例 | 含义及客户端行为 |
| --- | --- | --- |
| 400 | `INVALID_CURSOR` | 游标、筛选或协议无效 |
| 401 | `UNAUTHENTICATED` | 未登录、会话过期或凭证无效；不得区分用户是否存在 |
| 403 | `CSRF_INVALID`, `TRADING_RESTRICTED` | CSRF、地区或账户权限不允许操作 |
| 404 | `NOT_FOUND` | 本地实体不存在或不可见 |
| 409 | `IDEMPOTENCY_CONFLICT`, `STATE_CONFLICT`, `MAPPING_UNCONFIRMED`, `QUOTE_EXPIRED`, `PREVIEW_STALE` | 请求与已保存状态不一致；按错误提示刷新，不能盲目重试 |
| 422 | `VALIDATION_ERROR`, `ORDER_RULES_INVALID` | 请求格式或市场规则不合法 |
| 429 | `RATE_LIMITED` | 已触发应用限制；按 `Retry-After` 等待 |
| 502 | `PROVIDER_ERROR` | 外部服务明确返回无法处理的错误；不等同于没有数据 |
| 503 | `PROVIDER_UNAVAILABLE`, `ENGINE_RESTARTING`, `POST_ONLY_MODE`, `SERVICE_UNAVAILABLE` | 服务暂不可用或交易受限；带上游等待时间时保留 |
| 500 | `INTERNAL_ERROR` | 内部错误；不暴露堆栈 |

若已发起可能产生资金结果的上游请求而发生超时、连接中断或进程重启，不返回暗示可安全重试的普通失败：返回 `202` 和本地意图的 `unknown` 状态（如能返回响应），客户端仅查询与对账。HTTP 超时没有收到本地响应时，通过原 `Idempotency-Key` 查询原意图，不用新键重发。

## 2. 主要资源类型

以下为约定字段；`id` 均为本地 UUID，字段未特别标明时为必填。`null` 明确表示未知或不适用。列表和详情可按最小公开字段投影，但同名字段的含义不变。

| 资源 | 主要字段 |
| --- | --- |
| `Team` | `id`, `sport` (`football` / `nba`), `source_name`, `name_zh`（可空）, `translation_status` (`verified` / `missing`), `logo_url`（可空） |
| `Game` | `id`, `sport`, `provider`, `external_id`, `league`（可空）, `home_team`, `away_team`, `starts_at`, `status`, `provider_status`（可空）, `home_score` / `away_score`（可空）, `watched`, `last_synced_at`（可空） |
| `Watch` | `id`, `game_id`, `state` (`active` / `completed` / `paused`), `first_due_at`, `next_due_at`（可空）, `last_checked_at`（可空）, `completed_at`（可空） |
| `MarketLink` | `id`, `game_id`, `market_id`（本地 UUID）, `state` (`candidate` / `confirmed` / `rejected` / `stale`), `match_method`, `evidence`, `confirmed_at`（可空） |
| `Market` | `id`（本地 UUID）, `gamma_market_id`, `condition_id`, `protocol_version` (`v1` / `v2`), `question`（可空）, `accepting_orders`（布尔或 null）, `combo_enabled`（布尔或 null）, `outcomes` |
| `Outcome` | `id`（本地 UUID）, `market_id`, `outcome_index`, `label`, `asset_id`, `asset_kind` (`ctf_token` / `position`), `combo_available`（布尔或 null；只基于已验证的真实上游能力） |
| `Wallet` | `id`, `label`, `wallet_address`, `signer_address`, `wallet_type`, `enabled`, `credentials_configured`（布尔）, `connection_status`（`ready` / `unverified` / `unavailable`）；不返回密钥或服务端凭证引用的实际值 |
| `TradeIntent` | `id`, `wallet_id`, `kind` (`single` / `combo`), `side`, `state`, `legs`, `limit_price`, `requested_size`, `budget`, `confirmed_at`, `submitted_at`, `created_at`, `updated_at`；未知数值/时间为 null |
| `TradeLeg` | `index`, `game_id`, `market_link_id`, `outcome_id`, `outcome_label_snapshot` |
| `Submission` | `id`, `intent_id`, `attempt_no`, `state`, `order_id`（可空）, `order_hash`（可空）, `started_at`, `resolved_at`（可空）, `filled_size`（可空）, `error_code`（可空） |
| `ComboRFQ` | `id`（本地 UUID）, `intent_id`, `external_rfq_id`（可空字符串）, `state`, `requested_at`, `resolved_at`（可空）, `quote`（可空 `ComboQuote`） |
| `ComboQuote` | `id`（本地 UUID）, `external_quote_id`, `price`, `size`, `fee_estimate`, `net_receive`（可空十进制字符串）, `expires_at`, `received_at`；仅返回上游实际提供且可确认的值 |
| `SyncRun` | `id`, `provider`, `sport`, `trigger_type`, `scheduled_for`, `started_at`（可空）, `finished_at`（可空）, `status`, `window_start` / `window_end`（可空）, `pages_fetched`, `items_received`, `items_upserted`, `error_code`（可空） |
| `Job` | `id`, `kind`, `due_at`, `status`, `attempt_count`, `last_error_code`（可空）, `updated_at`；不返回租约 token |

`TradeIntent.state` 使用数据库约定：`draft`, `confirmed`, `submitting`, `pending`, `partially_filled`, `filled`, `rejected`, `cancelled`, `unknown`。`ComboRFQ.state` 使用 `preparing`, `requesting`, `quoted`, `accepting`, `executing`, `confirmed`, `expired`, `rejected`, `failed`, `unknown`。`pending` 和 `executing` 都不代表已经成交；组合 `confirmed` 仅在上游提供相应已确认执行证据后使用，链上结算状态另行返回。

## 3. 认证与健康检查

| 方法与路径 | 请求 | 成功响应 | 约束 |
| --- | --- | --- | --- |
| `GET /health/live` | 无 | `200`：`{"data":{"status":"ok"},"request_id":"..."}` | 只表示进程存活，不访问第三方或泄露配置；生产部署可限制网络访问 |
| `GET /auth/csrf` | 无 | `200`：`data.csrf_token`；设置匿名关联 Cookie | 匿名令牌短期有效，不写日志 |
| `POST /auth/login` | `{ "username": "...", "password": "..." }`；须匿名 CSRF + Origin | `200`：`data.authenticated=true`、新 `csrf_token`；设置会话 Cookie | 认证失败为 `401`，触发限制为 `429`；成功轮换令牌 |
| `GET /auth/session` | 会话 Cookie | `200`：`authenticated`, `username`, `expires_at`, `csrf_token` | 不返回会话令牌；已撤销为 `401` |
| `POST /auth/logout` | 会话 + CSRF | `204`；撤销会话并清理 Cookie | 幂等撤销；不可通过 GET 退出 |

`GET /system/status`（第 8 节）是经过认证的详细就绪状态，与存活探针区分。

## 4. 赛事、球队与关注

| 方法与路径 | 请求 | 成功响应 | 业务规则 |
| --- | --- | --- | --- |
| `GET /games` | `sport`（必填 `football` / `nba`）；可选 `from`, `to`, `status`, `watched`, `limit`, `cursor` | `200` 分页 `Game` | 默认未来 36 小时；按 `starts_at,id` 升序；返回实际来源状态，不将获取失败伪装为空列表 |
| `GET /games/{game_id}` | 路径 UUID | `200`：`Game`，附 `watch`（可空）、`recent_revisions` | 展示已记录的状态/时间变更及数据更新时间 |
| `GET /teams` | 可选 `sport`, `translation_status`, `q`, `limit`, `cursor` | `200` 分页 `Team` | 按 `source_name,id` 升序；队徽缺失返回 null |
| `PATCH /teams/{team_id}` | `{ "name_zh": "...", "translation_status": "verified" }` | `200` 更新后的 `Team` | `verified` 必须提供非空中文名称；记录审计，未确认时设 `missing` 和 null |
| `POST /teams/{team_id}/aliases` | `{ "alias": "...", "language": "zh-CN", "verified": true }` | `201` 别名对象 | 唯一约束冲突返回 `409`，保留歧义候选 |
| `GET /watches` | 可选 `sport`, `state`, `limit`, `cursor` | `200` 分页 `Watch` | 按下一次到期时间、ID 排序；无下次任务的排最后 |
| `POST /games/{game_id}/watch` | `{}` | `200`：`Watch` | 幂等建立或返回已有关注；依据当前有效开赛时间计算首次截止时间；不重复建立任务 |
| `GET /games/{game_id}/markets` | 无 | `200`：`{ "items": [{ "link": MarketLink, "market": Market }], "refresh": {"status":"..."} }` | 只读已有数据；未确认或歧义候选仍展示但不可交易，未知或过期需明确标注 |
| `GET /games/{game_id}/live-markets` | 无 | `200`：`{ "events": [...], "match_status": "source_identity" / "candidate", "observed_at": "..." }` | 只读上游市场及参考价格；不同来源的候选不自动确认；分页失败或匹配歧义返回错误，缺失价格为 null |
| `POST /games/{game_id}/markets/refresh` | `{}` | `202`：`{ "game_id":"uuid", "status":"refreshing" }` | 经 CSRF 校验后执行候选更新；上游失败显式标记，不将旧候选冒充新数据 |
| `POST /market-links/{link_id}/confirm` | `{}` | `200`：确认后的 `MarketLink` | 检查来源身份、运动类型和冲突；写审计；已确认同一映射幂等 |
| `POST /market-links/{link_id}/reject` | `{}` | `200`：拒绝后的 `MarketLink` | 已被交易引用或状态不允许时返回 `409`；不能删除交易历史 |

关注任务由 worker 自动恢复：足球首次开赛后 120 分钟、随后每 10 分钟；NBA 首次开赛后 159 分钟、随后每 3 分钟。`next_due_at` 从持久化的待执行任务获取；暂停或完赛时为 null。仅上游明确完赛才设置 `completed`。

## 5. 行情、钱包和单场交易

### 5.1 查询与预览

| 方法与路径 | 请求 | 成功响应 | 业务规则 |
| --- | --- | --- | --- |
| `GET /wallets` | 无 | `200`：`{ "items": [Wallet] }` | 只列服务器预配置的钱包；不接收或返回私钥 |
| `GET /wallets/{wallet_id}/status` | 无 | `200`：`{ "wallet": Wallet, "collateral_balance": "..." / null, "allowance_status": "unknown", "availability": "allowed" / "blocked" / "unknown", "checked_at": ... / null, "diagnostic": null / { "code": "...", "message": "..." } }` | 失败返回固定安全诊断，不返回异常原文或密钥；地区与其他检查分开，授权和 Builder 不在此处验证；不触发授权或部署 |
| `GET /outcomes/{outcome_id}/book` | 无 | `200`：`{ "outcome_id": "...", "bids": [{"price":"...","size":"..."}], "asks": [...], "tick_size": "...", "min_order_size": "...", "observed_at": "..." }` | 来自真实上游订单簿，按实际可用性返回错误；价格和份额为字符串 |
| `POST /single-orders/preview` | `{ "wallet_id":"uuid", "market_link_id":"uuid", "outcome_id":"uuid", "side":"BUY", "limit_price":"0.52", "size":"10" }` | `200`：`{ "preview_token":"...", "expires_at":"...", "game_id":"...", "market_link_id":"...", "outcome_id":"...", "side":"BUY", "limit_price":"0.52", "size":"10", "estimated_notional":"5.20", "fee_estimate":null, "book_observed_at":"...", "warnings":[] }` | 不签名、不发送订单、不创建关注；确认映射、市场规则、钱包/可用性；预览 token 是短期、不可篡改、绑定上述规范化字段与过期时间的服务端认证令牌，不含秘密和上游签名 |

预览示例数值只是模拟，`estimated_notional` 是限价乘份额，并非已成交金额；服务费不可确认时为 null。预览 token 只表示先前校验和用户看到的订单参数，不能锁定盘口、余额或上游报价。确认时必须重新校验，失效返回 `409 PREVIEW_STALE` 并要求重新预览；不得悄然修改价格或份额。

### 5.2 提交、查询和撤单

`POST /single-orders` 要求 `Idempotency-Key: <UUID>`、会话和 CSRF。请求：

```json
{
  "preview_token": "<模拟预览令牌>",
  "wallet_id": "00000000-0000-4000-8000-000000000010",
  "market_link_id": "00000000-0000-4000-8000-000000000011",
  "outcome_id": "00000000-0000-4000-8000-000000000012",
  "side": "BUY",
  "limit_price": "0.52",
  "size": "10"
}
```

这是模拟请求。所有字段必须与预览令牌绑定的规范化值相同；幂等键映射 `trade_intents.client_request_id`，与钱包组成唯一键并计算不含 token 的业务参数指纹。同钱包、同键、同指纹重复请求返回原意图及当前状态，绝不再次发送；同键不同指纹返回 `409 IDEMPOTENCY_CONFLICT`。请求进入对外发送阶段前，以事务创建意图、唯一提交记录、关注和首次赛果任务；先提交事务再调用 SDK。若无法明确证明请求未发出，状态 `unknown` 并对账，不自动重试。

| 方法与路径 | 请求 | 成功响应 | 业务规则 |
| --- | --- | --- | --- |
| `POST /single-orders` | 上述请求 | 首次 `201`：已记录且上游明确接受或拒绝的 `{ "intent": TradeIntent, "submission": Submission }`；仍在处理或结果不明为 `202`；同键重复且结果明确为 `200` | 接受、拒绝与成交严格区分；`201` 不表示成交；有效拒绝 `state=rejected` 并包含脱敏错误。下游暂时不可用但能证明未发送时可返回相应 4xx/5xx |
| `GET /trade-intents/by-key/{idempotency_key}` | 查询参数 `wallet_id` 必填 | `200`：`TradeIntent`，不存在 `404` | 请求超时后用于查找同一个意图；只有在可证明从未创建意图时才能决定下一步 |
| `GET /trade-intents` | 可选 `kind=single`, `state`, `wallet_id`, `game_id`, `from`, `to`, `limit`, `cursor` | `200` 分页单场 `TradeIntent` | 按 `created_at DESC,id DESC`；组合记录见 `/combo-rfqs` 或 `/history/trades`；无隐式真实交易重试 |
| `GET /trade-intents/{intent_id}` | UUID | `200`：`{ "intent": TradeIntent, "submissions": [...], "rfqs": [...], "state_events": [...] }` | 成交仅以真实交易/订单记录为准，未知保持 null |
| `POST /single-orders/{intent_id}/cancel` | `{}` | `200`：撤单已确认；`202`：结果未知 | 只对已知订单提交撤单；检查 `canceled` 和 `not_canceled`；超时后对账，不误报成功，不影响已成交份额 |
| `POST /trade-intents/{intent_id}/reconcile` | `{}` | `202`：`{ "intent_id":"...", "job_id":"..." }` | 幂等调度只读对账，不重新发送下单或重新接受报价 |

`POST /single-orders` 的 `201` 资源是已持久化的意图，不隐含任何上游成交或获利。`202` 同样返回意图 ID，前端轮询详情；订单确认成功、失败及未知都保留审计历史。明确的 `425` 和 post-only 限制采用第 1.2 节错误形式，绝不改写成凭证问题或自动转换订单类型。

## 6. Combo 组合询价与接受

Combo 走独立的官方 RFQ 流程。当前官方 Builder SDK 文档以 `leg_position_ids` 请求组合，`BUY` 使用金额、`SELL` 使用份额，支持情况取决于真实账户与市场；不得将 CLOB asset ID 未验证地作为 Combo 腿标识，也不得把多笔单场订单假装为 Combo。报价可能没有返回、被撤销或在接受前过期。`expires_at` 使用上游报价实际截止时间，不假定固定 10 秒。

| 方法与路径 | 请求 | 成功响应 | 业务规则 |
| --- | --- | --- | --- |
| `POST /combo-rfqs` | 幂等头 `Idempotency-Key: <UUID>`；`{ "wallet_id":"uuid", "direction":"BUY", "quantity":"10.00", "legs":[{"market_link_id":"uuid","outcome_id":"uuid"},{"market_link_id":"uuid","outcome_id":"uuid"}] }`；BUY 的 `quantity` 为预算，SELL 的 `quantity` 为份额 | 首次 `201`：已得到报价或明确无报价的 `ComboRFQ` ；`202`：仍在请求或结果未知；同键重复且结果明确为 `200` | 创建 Combo 草稿与 RFQ 记录，不触发接受或资金成交；至少两腿、不能重复 outcome，所有映射已确认且真实 Combo 能力已核实；`quantity` 最多六位小数 |
| `GET /combo-rfqs` | `limit`, `cursor` | `200` 分页组合记录 | 按创建时间及 ID 倒序 |
| `GET /combo-rfqs/by-key/{key}` | 本地幂等键与 `wallet_id` | `200` 现有组合请求 | 用于查询不确定的本地请求，404 不证明外部未收到 |
| `GET /combo-legs/{link_id}/{outcome_id}` | 已确认映射和选项 ID | `200` 目录 position ID | 仅读取目录身份；不代表账户获交易授权 |
| `GET /combo-rfqs/{rfq_id}` | 本地 RFQ UUID | `200`：`ComboRFQ` | 返回本地状态和已核实的外部 RFQ ID；外部结果未知不能当作无报价或失败 |
| `POST /combo-rfqs/{rfq_id}/accept` | `{ "quote_id":"uuid" }`；会话 + CSRF | `200`：明确拒绝或已确认执行的 RFQ；`202`：执行中或未知的 RFQ | 此请求表示用户确认报价及全部腿。锁定 RFQ 与意图，核对有效期、报价、账户、市场和映射；先原子持久化确认、所有赛事关注及接受开始状态，再调用 SDK。重复请求只返回已存在状态，不再次接受 |
| `GET /combo-rfqs/{rfq_id}/events` | `limit`, `cursor` | `200` 分页脱敏 RFQ 状态事件 | 按 `created_at,id` 排序；`executing` 不等于成交 |

`POST /combo-rfqs` 的幂等键映射 `combo_rfqs.client_request_id`，指纹覆盖钱包、方向、金额/份额和规范化腿。相同键/相同参数返回原本地 RFQ；参数不同返回 `409 IDEMPOTENCY_CONFLICT`。若请求结果未知，只能查询/对账原 RFQ；不能通过更换幂等键绕过未知状态。服务端必须使用 `(wallet_id, fingerprint)` 的未解决状态唯一约束阻止相同参数换键并发创建额外 RFQ；冲突响应带可查询的原本地 RFQ ID，不得暴露秘密。该约束也适用于单场未知或正在提交的相同业务参数。

官方 SDK 接受报价需要原始报价对象；该对象可能含上游签名，不写入数据库、日志或浏览器。第一版部署限制 FastAPI 为**单进程**，将有效报价对象以受限内存保存并按上游 `expires_at` 及时销毁；API 响应只返回 `ComboQuote` 的安全字段。服务重启或内存对象丢失但尚未发送接受请求时，旧报价不可接受，返回 `409 QUOTE_EXPIRED` 或 `QUOTE_UNAVAILABLE`，用户可在旧报价过期后重新询价；**已开始接受或结果未知的请求则不可重新询价替代，必须先对账或人工处理**。如未来改为多进程，须另行设计安全共享的短期对象存储和跨进程锁，不能直接扩容后假定报价仍可用。

接受后使用 SDK 返回的 RFQ ID 异步查询执行状态，超时后按同一 RFQ ID 对账。请求期间或系统重启中如果无法确认接受请求是否已发送，状态保持 `unknown`，不自动重新接受或创建新 RFQ。未获得必要 Builder 凭证、签名钱包、授权或市场支持时返回明确不可用错误，不生成模拟报价。

## 7. 赛程同步与任务

| 方法与路径 | 请求 | 成功响应 | 业务规则 |
| --- | --- | --- | --- |
| `GET /sync-schedules` | 无 | `200`：`{ "items": [{"id":"uuid","provider":"polymarket","sport":"football","utc_time":"00:00:00","enabled":true}], "revision":1 }` | 示例为模拟；另有 NBA 及 UTC 08:00 默认项 |
| `PUT /sync-schedules` | `{ "revision":1, "items":[{"provider":"polymarket","sport":"football","utc_time":"00:00:00","enabled":true}, ...] }` | `200`：完整新配置及新 revision | 原子替换非秘密计划配置；每项唯一，要求足球/NBA 各至少一个启用时间；版本不匹配 `409`。仅调整未来未领取任务，不提前执行 |
| `POST /sync-runs` | `{ "sport":"football" }` | `202`：`{ "run_id":"uuid", "job_id":"uuid", "status":"queued" }` | 手动调度未来 36 小时来源同步；并发冲突返回已有运行或 `409`；不凭空生成数据 |
| `GET /sync-runs` | 可选 `sport`, `status`, `from`, `to`, `limit`, `cursor` | `200` 分页 `SyncRun` | 按 `started_at DESC,id DESC`，未开始项排最后；日期范围按 `scheduled_for` 筛选，保留失败信息 |
| `GET /sync-runs/{run_id}` | UUID | `200`：`SyncRun` | 失败详情为脱敏摘要，绝不包含 API key |
| `GET /jobs` | 可选 `kind`, `status`, `game_id`, `limit`, `cursor` | `200` 分页 `Job` | 按 `due_at,id`，只读，不暴露租约令牌 |
| `GET /jobs/{job_id}/runs` | `limit`, `cursor` | `200` 分页尝试摘要：`attempt_no`, `started_at`, `finished_at`, `result`, `error_code` | 任务重启与失败可观察；不提供提前执行未到期任务的接口 |

定时同步默认 UTC 00:00、08:00，两个来源分别执行；请求按分页完整性标记 `succeeded`、`partial` 或 `failed`。调度 API 的 `revision` 使用 `schedule_revisions` 中的非秘密版本值，计划行保存在 `sync_schedules`；更新两者的事务必须原子一致。

## 8. 历史、导出和运行状态

| 方法与路径 | 请求 | 成功响应 | 规则 |
| --- | --- | --- | --- |
| `GET /history/games` | `sport`, `from`, `to`, `status`, `limit`, `cursor` | `200` 分页历史 `Game` | 按 `starts_at DESC,id DESC`，包含历史状态和来源时间 |
| `GET /history/trades` | `sport`, `wallet_id`, `kind`, `state`, `from`, `to`, `limit`, `cursor` | `200` 分页交易及真实成交摘要 | 按 `created_at DESC,id DESC`；未成交、未知不得计入已实现盈亏 |
| `GET /statistics` | `sport`, `from`, `to`, `wallet_id`, `kind`, `state`, `status` | `200`：`{ "games_total":0, "finished_total":0, "trade_count":0, "confirmed_fill_count":0, "realized_pnl":null, "currency":"pUSD", "as_of":"..." }` | 示例不代表真实数据；赛事按开赛时间筛选，交易按创建时间筛选；单场只统计已保存的 CONFIRMED 成交记录，组合确认另计，收益证据不足为 null。钱包和交易类型筛选仅影响交易统计 |
| `GET /exports/games.csv` | 与历史赛事相同筛选（无分页） | `200 text/csv; charset=utf-8` | 导出全量过滤结果而非当前页面；大数据需流式处理 |
| `GET /exports/games.xlsx` | 同上 | `200 application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | 表头及时间时区清晰；Excel 需要限制单表行数 |
| `GET /exports/trades.csv` | 与历史交易相同筛选（无分页） | `200 text/csv; charset=utf-8` | 区分意图、成交及未知状态；防电子表格公式注入 |
| `GET /exports/trades.xlsx` | 同上 | `200 application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | 同 CSV 口径；禁止包含秘密 |
| `GET /system/status` | 无 | `200`：数据库、worker 心跳、各数据源最近同步、下次计划及非秘密配置摘要 | 数据源状态取已保存的同步记录，不表示实时探测；未知为 `unknown`；只暴露脱敏错误 |
| `GET /audit-events` | `action`, `entity_type`, `from`, `to`, `limit`, `cursor` | `200` 分页审计事件 | 按 `created_at DESC,id DESC`；只返回白名单化详情 |

导出端点需会话；响应包含安全文件名的 `Content-Disposition: attachment`，禁用缓存；固定 UTC+8 展示时间并在表头/文件说明标记，保留必要来源和状态字段。CSV/Excel 中以 `=`, `+`, `-`, `@` 等电子表格公式前缀开头的**文本字段**须按安全规则转义，不改变定点数值字段的数值语义。

## 9. 关键事务和时序

1. **单场预览 → 提交**：查询真实盘口 → 返回短期预览与规范化值 → 管理员确认 → 验证 token 和新盘口 → 在数据库事务内写意图、腿、关注、任务与提交状态 → 提交事务 → SDK 签名并发送 → 保存明确响应或未知状态 → 只读对账。禁止将上游 HTTP 200、订单 ID 或 `pending` 当作成交。
2. **Combo 询价 → 接受**：确认所有腿与能力 → 幂等创建草稿并请求 RFQ → 返回真实报价及有效期 → 管理员确认特定本地 quote ID → 行锁/租约和事务持久化接受意图与关注 → 接受原报价 → 记录 `executing` / 拒绝 / 未知 → 依据 RFQ ID 跟踪状态。不能保证外部事务和本地事务原子，故未知状态不可盲目重发。
3. **赛事同步和恢复**：API 只创建/更新配置或排队任务；worker 以数据库时间、到期条件、锁和租约领取。重启后未到期任务保持等待。足球与 NBA 的首次和后续时间如第 4 节，完赛才停止常规轮询。
4. **顺序与并发**：提交/接受操作在状态变更前锁定对应意图与唯一活动尝试；相同请求返回已存结果，参数改变导致冲突。异步回调、对账结果、审计事件及关注状态须在事务中更新，旧租约不可覆盖新结果。

## 10. 实现与契约测试要求

本文件是规范草案，后续 FastAPI 实现须生成 OpenAPI，并通过契约测试确保路径、请求响应、状态码、认证、CSRF、分页、数字字符串与此文档一致；如接口随真实 SDK 约束变化，先更新本文及关联 OpenSpec，再实现。

测试环境使用独立数据库、显式标记的模拟数据与 mock 上游，默认禁止真实资金下单。必须覆盖：

- 登录、会话轮换、CSRF、速率限制、过期/撤销及无权限访问；无秘密泄露。
- 两种运动的时间、日期边界、数据为空与失败、游标和中文名缺失；重复同步及任务重启不提前执行。
- 未确认/歧义映射、盘口变更、预览过期、精度与最小订单量、同键重放/冲突及并发。
- 单场提交与撤单的明确接受、拒绝、部分成交、网络超时和对账；不得重复下单或虚构成交。
- Combo 无报价、真实报价截止、对象丢失、重复接受、Last Look 拒绝、执行中、结果未知与 RFQ 对账；不得把单场批量下单当作 Combo。
- 425、post-only、第三方故障和授权缺失；日志、状态、统计、CSV/XLSX 口径和公式防护。

6F 已实现单场订单预览、提交、撤单、历史、只读对账、候选订单人工关联与成交证据查询；具体接口和配置见 [PolySports 运行说明](polysports/README.md) 与运行中的 OpenAPI。6G 增加组合询价、接受、状态和事件接口；6H 增加历史、统计、导出和系统状态接口。组合交易默认关闭，模拟测试不能证明真实资金交易已通过。官方参考：[单场下单](https://docs.polymarket.com/trading/place-orders)、[Combo Builder](https://docs.polymarket.com/trading/combos/builders)、[撮合重启](https://docs.polymarket.com/trading/matching-engine)。
