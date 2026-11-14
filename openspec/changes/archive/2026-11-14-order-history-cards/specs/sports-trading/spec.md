# Spec Delta

## ADDED Requirements

### Requirement: 交易记录显示独立订单卡片
交易记录页面 SHALL 将每笔单场订单和已确认组合分别显示为独立卡片，以阴影和间距区分；窄屏时内容 SHALL 保持可阅读。

#### Scenario: 多条订单
- **WHEN** 交易记录存在多笔订单
- **THEN** 每笔订单分别显示为带阴影的卡片

#### Scenario: 窄屏长编号
- **WHEN** 订单包含长编号且页面宽度较窄
- **THEN** 编号换行且页面不出现水平溢出
