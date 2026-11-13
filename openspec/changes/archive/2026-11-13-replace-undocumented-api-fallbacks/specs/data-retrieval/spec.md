# Spec Delta

## Purpose
确保面板使用官方数据接口完整读取持仓和准确解析市场身份，并在数据源不可用时保留不确定性，避免将错误展示为空仓或错误余额。

## ADDED Requirements

### Requirement: 完整读取用户持仓
系统 SHALL 使用 Data API v2 用户持仓接口逐页读取并映射当前持仓；任何分页失败、异常格式或循环游标 MUST 阻止部分结果被当作完整持仓。

#### Scenario: 多页持仓
- **WHEN** 持仓响应包含下一页游标
- **THEN** 系统携带同一用户和游标继续读取，直到最后一页并合并结果

#### Scenario: 刷新失败
- **WHEN** 任一页读取失败或响应结构无效
- **THEN** 系统保留旧缓存并标注过期；没有缓存时返回错误而非空持仓

#### Scenario: 空持仓
- **WHEN** 接口明确返回最后一页空列表
- **THEN** 系统可报告空持仓

### Requirement: 市场标识类型准确
系统 SHALL 使用 Gamma 市场 ID、condition ID、资产 ID 的对应官方查询方式并验证返回结果；无法唯一确定时不得猜测其他市场。

#### Scenario: 根据资产查询
- **WHEN** 资产 ID 匹配 Gamma 市场的 outcome 资产
- **THEN** 系统返回其 Gamma 市场 ID，而非 condition ID

#### Scenario: 多市场事件
- **WHEN** 事件包含多个市场且没有明确市场或资产
- **THEN** 系统不得选择第一个市场冒充确定结果

### Requirement: 余额不可用时保留未知
系统 SHALL 使用有效的已配置余额数据源；无法取得余额时返回未知，不使用未经证实的回退接口或把其他代币余额当作 pUSD。

#### Scenario: RPC 不可用
- **WHEN** 无法读取 pUSD 余额
- **THEN** 系统报告余额未知
