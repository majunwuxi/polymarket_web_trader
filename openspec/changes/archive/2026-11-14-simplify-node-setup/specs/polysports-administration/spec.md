# Spec Delta

## ADDED Requirements

### Requirement: 简化前端构建依赖安装
部署材料 SHALL 提供单脚本方式安装 Node.js 24，校验下载内容，且不覆盖不兼容的已有目标目录。

#### Scenario: 新安装
- **WHEN** 管理员在支持的 Linux 架构运行脚本且目标目录不存在
- **THEN** 脚本校验官方下载内容并安装 Node.js 24，显示后续 PATH 设置方式

#### Scenario: 已有目标
- **WHEN** 目标目录已包含可运行的 Node.js 24
- **THEN** 脚本复用现有安装；其他已有目标不被覆盖

#### Scenario: 下载校验失败
- **WHEN** 下载文件与校验清单不一致
- **THEN** 脚本失败且不创建目标安装目录
