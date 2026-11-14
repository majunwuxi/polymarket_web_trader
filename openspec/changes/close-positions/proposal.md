# Proposal

## Why
交易记录中没有直接退出单场及组合持仓的入口，用户难以核对当前可卖份额和及时发起卖出。

## What Changes
- 为可核实的单场及组合持仓提供平仓入口、实时核验和明确确认。
- 追踪卖出请求及结果，不以订单历史数量替代实际钱包余额。

## Capabilities

### New Capabilities

### Modified Capabilities
- `sports-trading`: 从交易记录发起已验证持仓的平仓。

## Impact
交易记录、单场和组合交易及状态持久化，涉及真实资金和外部交易接口。
