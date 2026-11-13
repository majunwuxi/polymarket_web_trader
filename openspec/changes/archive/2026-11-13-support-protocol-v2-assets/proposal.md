# Proposal

## Why
现有市场解析只读取 CTF 的 clobTokenIds，且旧交易客户端无法按 Protocol V2 订单簿选择签名域，导致新版市场无法可靠交易。

## What Changes
- 按市场版本选择 outcome 资产标识，保留旧接口字段兼容。
- 按订单簿版本选择能够处理对应签名域的客户端，未知版本时阻止提交。
- 覆盖市场解析、盘口和下单路径，增加模拟测试。

## Capabilities

### New Capabilities
- `market-assets`: 根据市场协议识别结果资产及交易签名路径。

### Modified Capabilities

## Impact
影响市场数据客户端、交易执行器、依赖及自动测试。旧市场保持原有交易路径。
