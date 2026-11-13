# Proposal

## Why
现有回退路径混用资产、condition 和 Gamma 市场 ID，并依赖未证实的接口；持仓获取还可能把请求失败当作空仓。官方 Data API v2 改用游标分页及新字段。

## What Changes
- 使用 Data API v2 分页读取并映射持仓，失败不覆盖已有缓存或伪装成空仓。
- 根据官方 Gamma 查询接口查找市场，区分市场 ID、condition ID 和资产 ID，验证结果匹配。
- 移除未经证实的事件、代币及余额回退，余额不可用时报告未知。

## Capabilities

### New Capabilities
- `data-retrieval`: 持仓、市场身份及余额数据的可靠读取。

### Modified Capabilities

## Impact
影响数据客户端、持仓面板及卖出，保留前端字段兼容；不改动交易签名。
