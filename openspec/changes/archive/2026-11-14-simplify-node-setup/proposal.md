# Proposal

## Why
部署说明中的 Node.js 安装命令过长，容易漏掉步骤。提供单脚本入口简化操作。

## What Changes
- 提供 Linux Node.js 24 安装脚本，校验下载并安全处理已有目录。
- 更新 VPS 部署文档，说明单条安装命令和 PATH。

## Capabilities

### New Capabilities

### Modified Capabilities
- `polysports-administration`: 增加前端构建依赖的简化安装入口。

## Impact
影响部署脚本和文档，不改变应用运行及交易逻辑。
