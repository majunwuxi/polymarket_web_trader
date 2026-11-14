# Proposal

## Why
用户希望足球和 NBA 赛程均来自 Polymarket；足球仅展示已开设胜平负市场的指定赛事。订单被交易所确认接受后，再绑定外部数据源核验赛果和订单输赢。

## What Changes
- 以 Polymarket 真实足球胜平负市场发现未来 36 小时赛事，保留来源和历史。
- 以 Polymarket 真实 NBA 比赛发现未来 36 小时赛程，保留既有 NBA 历史和订单。
- 先提供安全的足球下单入口；交易所确认接受订单后，按赛事证据搜索 API-Football 候选并确认绑定，不可用时明确提示。
- 订单确认后分别绑定 API-Football 和 BallDontLie，通过外部来源核实最终比分，并结合对应市场规则核对订单输赢。
- 修改同步调度、赛事展示、关注及赛果查询，保留交易安全检查和历史数据。

## Capabilities

### New Capabilities

### Modified Capabilities
- `sports-events`: 足球和 NBA 赛程从 Polymarket 获取，API-Football 与 BallDontLie 在订单确认后绑定并查询赛果。
- `sports-trading`: 投注前核验 Polymarket 市场身份，订单确认接受后启动赛果绑定；绑定不可确认时不推测或伪造。

## Impact
涉及赛事来源、持久化迁移、调度、API、界面及测试；真实 API-Football 套餐可能不能访问当前赛季。
