# Proposal

## Why
现有 Flask Dashboard 已有单场 CLOB 下单能力，但缺少独立的体育赛事同步、持久调度、组合交易与赛事管理。用户要求在同一仓库交付可独立部署的 PolySports 系统。

## What Changes
- 新增独立的 FastAPI、Vue 3、PostgreSQL 应用及 systemd/Nginx 部署方案，保留原 Dashboard。
- 实现足球与 NBA 数据同步、持久关注和赛果调度、中文队名与队徽、历史统计和导出。
- 实现 Polymarket 单场订单和 Combo RFQ 交易、钱包签名、实时状态核对及赛事关注。
- 按用户指定顺序完成架构、数据库、接口、界面、计划、代码测试与部署文档；每阶段验证后等待用户确认下一阶段。

## Capabilities

### New Capabilities
- `sports-events`: 体育赛事数据、身份关联、关注和持久调度。
- `sports-trading`: 基于赛事的单场和组合真实交易及状态核对。
- `polysports-administration`: 管理员访问、配置、日志、统计与独立部署。

### Modified Capabilities

## Impact
增加独立服务、数据迁移、前端和运维文件；现有交易规则和主规格仍适用。外部 API 凭证、Combo 权限及真实钱包测试环境需在运行前由用户提供。
