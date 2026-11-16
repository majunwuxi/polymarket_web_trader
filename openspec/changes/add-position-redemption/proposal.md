# Add Position Redemption

## Why

已结算的赢家仓位目前只能显示“应结算金额”，用户仍需到 Polymarket 官方界面手动赎回。交易记录页需要在卡片上直接标识可赎回仓位，并通过官方 SDK 将资金领取回 Polymarket 现金余额。

## What Changes

- 通过 Polymarket Data API v2 的 `REDEEMABLE` 持仓批量判断单场与组合卡片资格。
- 可赎回订单卡片显示粉色背景和“赎回”按钮。
- 新增 `POST /redemptions/eligibility`、`POST /trade-intents/{id}/redeem`、`POST /combo-rfqs/{id}/redeem`。
- 新增 `redeem_requests`，以钱包 + 整仓目标做幂等，保存交易哈希、实际余额增量和错误状态。
- 使用官方 `SecureClient.redeem_positions`：单场传 `condition_id`，组合传 `position_id`。
- 新增 `POLYSPORTS_REDEEM_EXECUTION_ENABLED`，默认关闭。
- 只读检查 Router/Adapter operator 授权，不自动授权、不自动部署钱包、不重发未知结果。

## Impact

- 后端：`redeem.py`、`redeem_gateway.py`、`redeem_api.py`、模型、迁移、配置。
- 前端：交易记录卡片样式与交互。
- 数据：新增 `redeem_requests` 表；不修改既有订单表语义。
- 运维：需要迁移、前端重建和显式启用赎回开关。
