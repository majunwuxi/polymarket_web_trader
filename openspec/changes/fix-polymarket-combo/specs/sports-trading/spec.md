# Spec Delta

## MODIFIED Requirements

### Requirement: 核验组合市场目录
系统 SHALL 从官方组合市场目录或按市场 ID 查询的官方市场详情取得组合腿 position ID，使用 condition ID、市场 ID、选项序号及标签核对已确认赛事映射；使用市场详情时 MUST 明确显示组合状态为 enabled 且市场处于有效状态。来源身份不一致、组合状态不明确或选项不完整时 MUST 拒绝该腿，不能用单场 CLOB 标识代替。

#### Scenario: 按市场查询成功
- **WHEN** 官方市场详情确认组合已启用，且市场、condition、选项和 position ID 均一致
- **THEN** 系统可使用该官方 position ID，不必扫描完整目录

#### Scenario: 目录查询不完整
- **WHEN** 来源错误或市场身份不唯一、不完整
- **THEN** 系统报告组合能力不可验证，不提供真实询价入口

#### Scenario: 钱包能力未知
- **WHEN** 市场信息匹配成功但 Builder 凭证或账户权限没有核实
- **THEN** 系统仅报告市场匹配，不声称用户可交易

## ADDED Requirements

### Requirement: Polymarket 赛事组合选项及赛果绑定
系统 SHALL 允许用户从赛事市场选择经官方 Combo 市场信息确认的选项加入组合，询价和接受报价须保留现有资金、身份、权限、单次发送和幂等保护。Polymarket 组合订单得到上游确认后 SHALL 为关联赛事启动持久化赛果绑定；接受结果未知或执行中 MUST 不声称成交或重发。

#### Scenario: 市场不支持组合
- **WHEN** 用户选择未经官方数据确认支持 Combo 的市场
- **THEN** 系统解释不可用原因，不加入可询价列表

#### Scenario: 交易执行中
- **WHEN** 组合报价已接受但外部交易仍在执行或结果未知
- **THEN** 系统只读对账，不声称成交，不启动赛果绑定

#### Scenario: 上游确认组合
- **WHEN** 来源确认组合交易成功
- **THEN** 系统为所有关联 Polymarket 赛事建立可恢复的赛果绑定请求
