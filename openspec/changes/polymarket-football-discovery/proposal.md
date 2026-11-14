# Proposal

## Why
API-Football 免费套餐无法可靠提供当前赛季；用户希望足球列表只展示 Polymarket 已开设胜平负市场的未来赛事，在投注时才绑定足球赛果来源。

## What Changes
- 以 Polymarket 真实足球胜平负市场发现未来 36 小时赛事，保留来源和历史。
- 投注前按赛事证据搜索 API-Football 候选，管理员确认绑定；不可用时明确提示。
- 修改同步调度、赛事展示、关注及赛果查询，保留交易安全检查和 NBA 行为。

## Capabilities

### New Capabilities

### Modified Capabilities
- `sports-events`: 足球赛程从 Polymarket 获取，API-Football 仅在需要时绑定及查询赛果。
- `sports-trading`: 投注前核验关联赛事，绑定不可确认时不推测或伪造。

## Impact
涉及赛事来源、持久化迁移、调度、API、界面及测试；真实 API-Football 套餐可能不能访问当前赛季。
