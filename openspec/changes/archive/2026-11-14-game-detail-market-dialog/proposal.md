# Proposal

## Why
赛事详情当前显示在列表下方，新足球赛事没有可见市场信息；用户需要在弹窗中查看 Polymarket 市场。

## What Changes
- 每场赛事打开可关闭的详情弹窗，自动加载只读 Polymarket 市场信息。
- 按来源身份验证关联，展示价格、状态及不可用原因，保持交易授权和绑定限制。

## Capabilities

### New Capabilities

### Modified Capabilities
- `sports-trading`: 以只读方式展示比赛关联的市场及来源价格。

## Impact
影响赛事前端、只读后端接口和自动化测试，不改变订单或资金操作。
