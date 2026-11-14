# Proposal

## Why
钱包状态失败时统一显示未知，管理员无法区分配置、地区、钱包身份和上游服务问题。

## What Changes
- 为钱包状态提供安全错误代码和固定提示，独立展示地区检查结果。
- 页面显示诊断与当前实现的授权、Builder 检查边界。

## Capabilities

### New Capabilities

### Modified Capabilities
- `polysports-administration`: 提供不泄露敏感信息的钱包状态诊断。

## Impact
影响钱包只读接口、页面和测试，不改变交易限制及真实资金操作。
