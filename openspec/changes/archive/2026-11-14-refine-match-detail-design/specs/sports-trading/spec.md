# Spec Delta

## ADDED Requirements

### Requirement: 清晰展示赛事下注状态
赛事详情 SHALL 以紧凑、适配手机的布局展示真实市场与下注状态；未满足站内交易前置条件时 MUST 明确原因，不能展示伪造的可执行下单入口。

#### Scenario: 足球赛果尚未绑定
- **WHEN** Polymarket 足球赛事尚未完成 API-Football 绑定
- **THEN** 弹窗说明站内暂不能下单，并展示可访问的官方市场链接

#### Scenario: 已确认交易映射
- **WHEN** 已存在有效可用的原有映射
- **THEN** 保留订单预览入口并继续执行现有校验

#### Scenario: 移动设备与键盘操作
- **WHEN** 用户在窄屏查看详情或按 Escape
- **THEN** 信息可读、内容可滚动且弹窗可关闭
