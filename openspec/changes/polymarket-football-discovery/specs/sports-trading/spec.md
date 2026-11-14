# Spec Delta

## ADDED Requirements

### Requirement: 足球交易前按需关联赛果
Polymarket 足球赛事进入投注流程时 SHALL 提供按需核验 API-Football 赛事绑定的操作，并清楚说明绑定状态和来源限制。

#### Scenario: 绑定不可验证
- **WHEN** 足球赛果来源不可用或候选不明确
- **THEN** 系统显示未绑定及原因，不声称完成赛事或赛果验证
