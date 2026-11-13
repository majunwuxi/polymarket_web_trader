# Design

## Context
Gamma 的 Protocol V2 市场提供 positionIds；CTF 市场提供 clobTokenIds。订单簿 version 决定交易签名域。

## Goals / Non-Goals
**Goals:** 保持现有 token_id API 兼容，并确保 V2 订单使用官方统一 SDK。
**Non-Goals:** 不处理价格精度及钱包迁移的其他问题。

## Decisions
从 Gamma 市场记录按版本读取资产，不交叉回退。下单前请求订单簿并按版本路由；V2 使用惰性创建的官方统一客户端，分别签名及提交以避免隐式自动授权。保留 CTF 的原执行路径。统一客户端要求现有钱包地址且仅支持生产 CLOB 主机，避免误连其他环境。

## Risks / Trade-offs
[订单簿不可访问] → 拒绝提交以避免使用错误签名域；[真实账户兼容性] → 用模拟测试并在结果中说明未进行真实下单。
