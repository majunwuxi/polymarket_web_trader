# wallet-authentication Specification

## Purpose
确保交易会话使用用户明确配置的账户钱包、签名类型和凭证，并支持官方 SDK 所识别的钱包类型，同时避免隐式部署和错误降级。

## Requirements

### Requirement: 使用明确的钱包身份
系统 SHALL 使用官方统一 SDK 并核对签名者、账户地址和配置的签名类型；智能钱包须已部署，验证失败时不得提交订单或创建钱包。

#### Scenario: 支持的钱包类型
- **WHEN** 已部署钱包或 EOA 地址与签名类型及 SDK 识别结果一致
- **THEN** 系统使用该钱包建立交易会话并用于 CTF 和 V2 市场

#### Scenario: 钱包类型不一致
- **WHEN** SDK 识别的钱包类型与配置不同
- **THEN** 系统拒绝下单并提示配置不匹配

#### Scenario: 钱包未部署或无法验证
- **WHEN** 智能钱包没有合约代码或链上检查失败
- **THEN** 系统拒绝下单，不自动创建钱包

### Requirement: 正确处理交易凭证
系统 SHALL 使用完整提供的 CLOB 凭证，或在完全未提供时通过官方 SDK 获取凭证；部分凭证和初始化失败不得触发旧 SDK 回退。

#### Scenario: 凭证不完整
- **WHEN** 只配置部分 CLOB 凭证
- **THEN** 系统拒绝交易并提示补全配置

#### Scenario: SDK 不可用
- **WHEN** 官方 SDK 无法加载或初始化失败
- **THEN** 系统报告错误且不使用旧客户端提交
