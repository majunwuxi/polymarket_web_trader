# Design

## Context
官方文档定义 425 为撮合引擎重启，SDK 使用 RequestRejectedError 的 restriction 和 retry_after；重启后暂时进入仅限 post-only 的阶段。当前面板提交普通 GTC 订单。

## Goals / Non-Goals
**Goals:** 保留结构化限制与等待时间并如实报告，避免重复提交。
**Non-Goals:** 不自动改为 post-only，不自动重试普通订单，不改变钱包凭证。

## Decisions
识别 SDK 的结构化重启或 post-only 限制，以及来自 CLOB 订单簿的明确 HTTP 425；返回稳定错误代码和可选 retry_after。HTTP API 对暂时不可用返回 503 并按需返回 Retry-After。SDK 初始化和提交各阶段保留重启信号，普通网络超时保持原有处理。用户手动等待后重新操作，避免无明确拒绝证据时重复下单。

## Risks / Trade-offs
[无服务端等待时间] → 提示稍后重试，不虚构等待时间。 [引擎恢复后仍处于 post-only] → 明确提示限制，普通订单不自动重发。
