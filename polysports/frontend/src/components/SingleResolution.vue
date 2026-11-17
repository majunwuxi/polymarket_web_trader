<script setup lang="ts">
import { formatDateTimeSeconds } from '../utils/format'

defineProps<{ resolution: any }>()

function checkLine(r: any) {
  const s = r?.settlement ?? {}
  const won = r?.result_judgment?.outcome_won ?? r?.outcome_won
  const verdict = won === true ? '选项获胜' : won === false ? '选项未获胜' : '结果待确认'
  if (s.agreement === 'consistent') return { tone: 'ok', text: `✓ 两个结果一致 · ${verdict} · 结算 ${s.settlement_amount ?? '未知'} pUSD` }
  if (s.agreement === 'conflict') return { tone: 'warn', text: `⚠ 两个结果不一致，需人工核实 · 按赛果 ${s.result_settlement_amount ?? '未知'} pUSD / 按市场 ${s.market_settlement_amount ?? '未知'} pUSD` }
  if (s.agreement === 'market_only') return { tone: 'warn', text: `◐ 仅有市场终态 · 按市场结算 ${s.market_settlement_amount ?? '未知'} pUSD，建议人工核实` }
  if (s.agreement === 'result_only') return { tone: 'info', text: `◐ 仅有赛果判断 · 预计 ${s.result_settlement_amount ?? '未知'} pUSD，市场未终态` }
  return { tone: 'muted', text: '— 两个结果均不可用，暂不能确认金额' }
}
</script>

<template>
  <div class="resolution-block">
    <p class="check-line" :class="'tone-' + checkLine(resolution).tone">{{ checkLine(resolution).text }}</p>
    <details class="detail-box"><summary>详情</summary>
      <p>赛事终态：<template v-if="resolution?.score_verified">比赛已结束，外部赛果已核实 {{ resolution.home_score }} : {{ resolution.away_score }}</template><template v-else-if="resolution?.game_finished">比赛已结束，但外部赛果尚未绑定核实</template><template v-else>比赛尚未确认结束（赛事状态 {{ resolution?.game_status ?? '未知' }}）</template></p>
      <p>结果一 · 按赛果判断：<template v-if="resolution?.result_judgment?.state === 'judged'">{{ resolution.result_judgment.outcome_won ? '选项获胜' : '选项未获胜' }} · 每份 {{ resolution.result_judgment.payout_per_share }} pUSD · 推算应结算 {{ resolution.settlement?.result_settlement_amount ?? '未知' }} pUSD</template><template v-else>无法判断（{{ resolution?.result_judgment?.basis ?? '赛果未确认' }}）</template></p>
      <p v-if="resolution?.result_judgment?.state === 'judged'" class="muted">判断依据：{{ resolution.result_judgment.basis }}</p>
      <p>结果二 · Polymarket 终态结算：<template v-if="resolution?.state === 'resolved'">{{ resolution.outcome_won ? '选项获胜' : '选项未获胜' }} · 每份 {{ resolution.outcome_won ? '1' : '0' }} pUSD · 应结算 {{ resolution.settlement?.market_settlement_amount ?? '未知' }} pUSD</template><template v-else>暂无市场结果（{{ resolution?.market_status_text ?? resolution?.market_error ?? (resolution?.state === 'unavailable' ? '结算上游暂不可用' : '市场尚未进入终态') }}）</template></p>
      <p v-if="resolution?.state === 'resolved'" class="muted">Polymarket 结算状态：已最终结算<template v-if="resolution?.resolved_at"> · {{ formatDateTimeSeconds(resolution.resolved_at) }} UTC+8</template></p>
      <p v-else-if="resolution?.market_status" class="muted">Polymarket 结算阶段：{{ resolution.market_status }} · {{ resolution.market_status_text }}</p>
      <p>两个结果核对：{{ resolution?.settlement?.agreement === 'consistent' ? '一致，结算金额已确认' : resolution?.settlement?.agreement === 'conflict' ? '不一致，请人工核实，暂不确认金额' : resolution?.settlement?.agreement === 'market_only' ? '仅有市场结果，按市场终态结算，建议人工核实' : resolution?.settlement?.agreement === 'result_only' ? '仅有赛果判断，市场终态未确认，金额仅为预计值' : '两个结果均不可用，暂不能确认金额' }}</p>
      <p v-if="resolution?.settlement?.settlement_amount != null">结算金额：<strong>{{ resolution.settlement.settlement_amount }} pUSD</strong> · 成交成本：{{ resolution.settlement.cost_basis ?? '待核实' }} · 已实现盈亏：{{ resolution.settlement.realized_pnl ?? '待核实' }} · 已核实成交 {{ resolution.settlement.covered_shares ?? '未知' }} 份</p>
      <p v-if="resolution?.settlement?.reason">{{ resolution.settlement.reason }}</p>
      <p v-if="resolution?.settlement?.notice" class="muted">{{ resolution.settlement.notice }}</p>
    </details>
  </div>
</template>
