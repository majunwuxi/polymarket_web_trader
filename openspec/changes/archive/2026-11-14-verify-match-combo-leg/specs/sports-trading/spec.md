# Spec Delta

## ADDED Requirements

### Requirement: 校验赛事组合订单归属
赛事详情 SHALL 校验组合订单至少包含当前赛事的腿；MUST 不展示不属于当前赛事的组合作为本场订单。市场或选项信息缺失时 SHALL 显示未知提示。

#### Scenario: 返回不属于本场的组合
- **WHEN** 服务端返回组合没有当前赛事的腿
- **THEN** 系统隐藏该组合并提示接口结果不一致

#### Scenario: 本场组合选项缺失
- **WHEN** 当前赛事组合腿的市场或选项名称缺失
- **THEN** 系统显示明确的未知提示，不推测选项
