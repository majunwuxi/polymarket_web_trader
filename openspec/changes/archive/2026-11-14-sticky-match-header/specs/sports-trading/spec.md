# Spec Delta

## ADDED Requirements

### Requirement: 赛事详情滚动保留比赛摘要
赛事详情弹窗 SHALL 在滚动市场内容时将标题、关闭按钮和对阵摘要保持在弹窗顶部；在窄屏或高度受限时 SHALL 保持固定区及其余内容可访问。

#### Scenario: 向下滚动市场
- **WHEN** 用户在赛事详情中向下滚动
- **THEN** 赛事标题、关闭按钮和比赛摘要仍停留在弹窗顶部

#### Scenario: 窄屏查看
- **WHEN** 用户在小屏幕滚动详情
- **THEN** 固定区域可见且市场内容仍可滚动查看
