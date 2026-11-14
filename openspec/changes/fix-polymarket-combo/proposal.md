# Proposal

## Why
赛事中心不再提供组合选项入口，且后端阻止 Polymarket 来源赛事参与组合，导致现有组合功能无法使用。

## What Changes
- 在赛事市场提供加入组合入口，使用来源身份和官方组合目录核验。
- 按官方 Combo RFQ 流程询价、确认和只读对账，明确权限不足和报价失败。
- 组合交易确认后为相关 Polymarket 比赛启动外部赛果绑定，结果未知不得认定成交。

## Capabilities

### New Capabilities

### Modified Capabilities
- `sports-trading`: 支持 Polymarket 来源赛事安全选择组合腿及确认后绑定。

## Impact
涉及赛事详情、组合状态机、外部赛果绑定和测试；需要已配置的官方 Builder 凭证及支持组合的市场。
