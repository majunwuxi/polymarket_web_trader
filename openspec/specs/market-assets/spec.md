# market-assets Specification

## Purpose
确保面板发现不同协议版本市场时选择正确的结果资产，并使用与该市场订单簿匹配的交易签名路径，以避免错误识别和错误签名。

## Requirements

### Requirement: 根据协议选择资产
系统 SHALL 根据市场版本使用对应 outcome 的资产标识，V2 使用 positionIds，CTF 使用 clobTokenIds。

#### Scenario: V2 市场
- **WHEN** 市场版本为 v2 且含有 positionIds
- **THEN** 系统按 outcome 顺序返回对应 position ID 并可用它查询盘口

#### Scenario: CTF 市场
- **WHEN** 市场为 CTF 且包含 clobTokenIds
- **THEN** 系统按 outcome 顺序返回对应 token ID

#### Scenario: 缺失标识
- **WHEN** 指定版本缺少对应资产标识或列表长度不一致
- **THEN** 系统不得使用其他协议的标识代替，也不得错配 outcome

### Requirement: 交易签名与订单簿版本一致
系统 SHALL 在提交前确定订单簿版本并采用对应的签名和提交路径。

#### Scenario: V2 下单
- **WHEN** 订单簿版本为 v2
- **THEN** 系统使用支持 V2 签名域的客户端提交所选 position ID

#### Scenario: 无法确认订单簿版本
- **WHEN** 订单簿不可用或版本无法识别
- **THEN** 系统拒绝提交订单并提示错误
