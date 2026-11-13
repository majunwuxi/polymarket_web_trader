# Proposal

## Why
HTTP 425 表示撮合引擎重启，现有界面误称凭证未就绪，且统一 SDK 的结构化重启信息被通用异常处理吞掉，会误导交易决策。

## What Changes
- 保留订单请求中明确的重启及 post-only 限制，并向买卖接口返回准确提示和服务端等待时间。
- 不自动重试或改变当前订单类型；不再根据异常文本猜测凭证问题。
- 验证错误分类、时间传播和普通失败不被误分类。

## Capabilities

### New Capabilities
- `trading-restrictions`: 将撮合引擎重启和限制交易状态准确反馈给用户。

### Modified Capabilities

## Impact
影响交易执行器及买卖接口的错误响应；不改变订单参数或钱包凭证。
