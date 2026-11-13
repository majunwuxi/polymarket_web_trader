# Proposal

## Why
订单接受与成交被混淆，可能误报交易结果；撤单也未核验服务端结果。

## What Changes
- 如实报告订单接受和已知成交量，核对撤单结果。
- 修正面板卖出和交易响应中的误导信息。

## Capabilities

### New Capabilities
- `order-lifecycle`: 订单提交、成交和撤单结果的可靠报告。

### Modified Capabilities

## Impact
影响订单执行器及面板交易接口，不改变下单类型。
