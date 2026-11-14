# Spec Delta

## ADDED Requirements

### Requirement: Polymarket 赛事组合选项及赛果绑定
系统 SHALL 允许用户从赛事市场选择经官方 Combo 目录确认的选项加入组合，询价和接受报价须保留现有资金、身份、权限、单次发送和幂等保护。Polymarket 组合订单得到上游确认后 SHALL 为关联赛事启动持久化赛果绑定；接受结果未知或执行中 MUST 不声称成交或重发。

#### Scenario: 市场不支持组合
- **WHEN** 用户选择不在官方 Combo 目录中的市场
- **THEN** 系统解释不可用原因，不加入可询价列表

#### Scenario: 交易执行中
- **WHEN** 组合报价已接受但外部交易仍在执行或结果未知
- **THEN** 系统只读对账，不声称成交，不启动赛果绑定

#### Scenario: 上游确认组合
- **WHEN** 来源确认组合交易成功
- **THEN** 系统为所有关联 Polymarket 赛事建立可恢复的赛果绑定请求
