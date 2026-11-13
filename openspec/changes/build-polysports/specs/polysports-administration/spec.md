# Spec Delta

## Purpose
为个人赛事交易系统提供可管理、安全访问、可追踪的运行环境和数据导出能力，并支持独立安装、维护与备份。

## ADDED Requirements

### Requirement: 管理与导出
系统 SHALL 提供单一管理员登录、HTTPS 访问、集中配置、同步日志、运行状态、历史统计以及 Excel 和 CSV 导出；密钥不得进入 Git 仓库或日志。

#### Scenario: 导出历史数据
- **WHEN** 管理员选择历史数据并请求 Excel 或 CSV 导出
- **THEN** 系统导出所选真实记录和明确字段

#### Scenario: 外部服务失败
- **WHEN** 第三方 API 不可用或缺少必要权限
- **THEN** 系统报告失败或不可用，不使用虚构数据冒充真实数据

### Requirement: 独立部署
系统 SHALL 提供 PostgreSQL 数据库迁移、自动化测试、Nginx、systemd、维护和备份说明，且不依赖原有 Dashboard 运行。

#### Scenario: 独立安装
- **WHEN** 管理员按部署说明安装并配置所需凭证
- **THEN** 可独立启动应用和后台任务，并执行迁移与备份恢复流程
