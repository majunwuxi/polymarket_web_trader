# Proposal

## Why
赛事详情可能显示没有本场选项的组合，用户无法辨别订单归属及选项是否缺失。

## What Changes
- 校验组合腿是否属于当前比赛，隔离不一致响应并提示排查。
- 本场组合的市场或选项缺失时明确提示。

## Capabilities

### New Capabilities

### Modified Capabilities
- `sports-trading`: 提升赛事订单明细身份与缺失字段的反馈。

## Impact
赛事详情只读展示和模拟测试，无资金操作。
