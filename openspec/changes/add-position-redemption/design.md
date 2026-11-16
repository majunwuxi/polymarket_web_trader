# Design

## Context

Polymarket 官方赎回没有“按订单部分赎回”参数。标准单场按 `condition_id` 一次处理钱包全部结果份额；组合按官方 `position_id` 处理组合仓位。可赎回资格只能以官方持仓状态为准，不能从本地比分、订单成交份额或 `close_blocked` 推断。

## Goals / Non-Goals

**Goals:** 准确识别可赎回的单场和组合卡片；整仓幂等赎回；真实交易证据和余额结果；未知状态禁止重发。

**Non-Goals:** 部分份额赎回；自动链上授权；自动部署钱包；自动重试结果未知的交易；把赎回伪装成 CLOB 订单。

## Decisions

- 资格批量从 Data API `/v2/positions?status=REDEEMABLE` 和 `/v2/positions/combos?status=REDEEMABLE` 获取；按 `token_id` / `combo_position_id` 匹配本地卡片。
- `redeem_requests` 的唯一键为 `(wallet_id,target_type,target_ref)`，`target_ref` 为单场 condition ID 或组合 position ID。
- 执行顺序：只读资格与授权检查 → 短事务写 `submitting` 和审计 → 事务外调用 SDK 并等待 → 新事务写 `confirmed` 或 `unknown`。
- 单场调用 `redeem_positions(condition_id=...)`；组合调用 `redeem_positions(position_id=...)`。
- 发送后任何异常视为 `unknown` 并返回 `202`，不自动重发；只有 `confirmed` 可幂等返回 `200`。
- 赎回前后现金余额的可靠正差值作为 `payout`；余额不可读取时为 `null`。
- 授权只读检查，缺失时返回 `409 REDEEM_APPROVAL_REQUIRED`；由用户在外部完成授权。
- 智能钱包优先使用完整 Builder 凭据，否则使用官方 Relayer API Key；两者都没有时发送前返回 `503`。

## Risks / Trade-offs

- [Router/Adapter 未授权] → 在发送前拒绝，不自动授权。
- [智能钱包 Builder 凭据缺失] → 发送前返回 `503`；不隐式使用其他钱包凭据。
- [请求已广播但回执未知] → 保持 `unknown`，卡片禁止重复点击，要求外部核对。
- [列表资格查询失败] → 卡片不显示可赎回状态，不阻塞订单列表，也不把未知当作可赎回。
- [同一钱包多张订单共享仓位] → 整仓赎回后其他关联卡片同步变为已赎回。
