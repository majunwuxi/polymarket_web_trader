# Design

## Context
Data API v2 的 /v2/positions 使用 data 与 pagination 封装和游标；CLOB 订单簿的 market 为 condition ID，不能用于 Gamma 市场详情路径。

## Goals / Non-Goals
**Goals:** 全部分页后再更新缓存，使用官方接口并验证标识一致性。
**Non-Goals:** 不迁移无关活动和行情接口，不增加未授权的交易。

## Decisions
持仓接口沿游标拉取并映射已验证字段，缺页或异常抛错保留旧缓存；无缓存时返回服务不可用。Gamma 通过资产、condition 或 slug 对应查询，核实返回的标识；多市场事件不猜测第一个市场。余额只使用已有 Polygon RPC 查询，不调用未证实 Gamma 钱包接口。

## Risks / Trade-offs
[分页中途失败] → 放弃部分结果并返回错误；[API 暂不可用] → 旧缓存明确标注过期。 [链上余额不可用] → 返回未知而非伪造数值。
