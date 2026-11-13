# Design

## Context
统一 SDK 按账户钱包识别 EOA、Proxy、Safe 和 Deposit Wallet，自动为市场选择签名域。SDK 公共工厂可能部署尚未部署的 Deposit Wallet。

## Goals / Non-Goals
**Goals:** 两种市场共享同一会话，避免旧客户端降级并保留订单状态约定。
**Non-Goals:** 不实现钱包创建、授权交易、Session Keys 或自定义 CLOB 环境。

## Decisions
惰性创建官方客户端，配置智能钱包必须先通过 Polygon RPC 验证合约已部署；SDK 识别的钱包类型必须与配置签名类型一致。显式完整 CLOB 凭证传给 SDK，不完整配置直接报错；未配置时由 SDK 派生。签名和提交分开调用，避免自动授权恢复。结果适配原有 API，所有账户独立缓存客户端。

## Risks / Trade-offs
[RPC 不可用或合约尚未部署] → 拒绝交易，提示检查地址及 RPC，不发起部署。 [凭证或钱包不匹配] → 拒绝交易，提示修正配置。 [线上差异] → 模拟测试覆盖各钱包类型，明确未做真实下单。
