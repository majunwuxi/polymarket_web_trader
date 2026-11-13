# Proposal

## Why
固定精度舍入和价格钳制可能改变用户订单，且未依据市场最小份额校验。当前订单簿提供的交易约束必须在提交前生效。

## What Changes
- 读取订单簿的价格增量和最小份额，验证输入并拒绝无效订单。
- 按金额和百分比计算的份额向下取整至两位小数，在买入确认中展示实际份额。
- 模拟模式也执行相同市场约束校验。

## Capabilities

### New Capabilities
- `order-constraints`: 限价订单的市场约束与数量精度校验。

### Modified Capabilities

## Impact
影响订单执行器、交易面板、自动化测试和模拟模式；不改变订单类型或钱包配置。
