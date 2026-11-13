# Spec Delta

## Purpose
确保交易系统准确传达撮合引擎重启或交易限制，不将临时服务状态误报为钱包凭证问题，并避免未经确认的重复下单。

## ADDED Requirements

### Requirement: 报告撮合引擎重启
系统 SHALL 在 CLOB 返回明确 HTTP 425 或 SDK 报告 restarting 时说明撮合引擎暂时重启，并保留有效的服务端重试延迟；系统 MUST NOT 将其称为凭证错误或自动重复提交普通订单。

#### Scenario: 订单提交被重启拒绝
- **WHEN** 下单请求返回 425 或 SDK 识别为 restarting
- **THEN** 返回重启提示和可用的等待时间，且不清除凭证或自动重新下单

#### Scenario: 订单簿或客户端初始化遇到重启
- **WHEN** 获取订单簿或初始化客户端时收到明确的重启信号
- **THEN** 返回重启提示而不报告凭证错误

#### Scenario: 网络错误结果未知
- **WHEN** 请求超时或发生非重启错误
- **THEN** 系统不得按重启自动重试或错误标记为 425

### Requirement: 报告 post-only 限制
系统 SHALL 在 SDK 明确返回 post_only 或 post_only_mode 时提示该限制，并保留可用的重试延迟，不得擅自改变订单类型。

#### Scenario: 普通订单被限制
- **WHEN** 普通限价订单因 post-only 模式被拒绝
- **THEN** 系统提示交易限制，并且不自动重发或改变订单
