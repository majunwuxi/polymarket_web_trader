# Proposal

## Why
现有 CTF 和 Protocol V2 交易使用不同客户端，旧客户端存在静默降级，Deposit Wallet 在部分路径可能使用错误签名。统一官方 SDK 并核对钱包类型可减少错误配置和不兼容。

## What Changes
- 使用官方统一 SDK 处理两种协议的订单签名、提交、查询和撤单，移除旧 SDK 降级。
- 核对已配置的钱包类型、地址和 API 凭证；未配置完整凭证时明确拒绝。
- 保留已有钱包配置格式，说明 Deposit Wallet、授权和模拟模式的限制。

## Capabilities

### New Capabilities
- `wallet-authentication`: 使用明确的钱包身份和 CLOB 凭证建立交易会话。

### Modified Capabilities

## Impact
影响执行器、钱包状态、依赖、配置文档及测试；不自动创建钱包或执行链上授权。
