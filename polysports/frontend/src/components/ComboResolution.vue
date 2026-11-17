<script setup lang="ts">
import { formatDateTimeSeconds } from '../utils/format'
import { comboAgreement, comboWinner, legCheck, legWinner, resultBadge } from '../utils/labels'

defineProps<{ resolution: any }>()
</script>

<template>
  <div class="resolution-block">
    <p class="check-line" :class="resolution.agreement === 'consistent' ? 'tone-ok' : resolution.agreement === 'conflict' ? 'tone-warn' : 'tone-info'">{{ comboAgreement(resolution.agreement) }}</p>
    <details class="detail-box"><summary>详情</summary>
      <p class="detail-section-title">赛事终态核对：共 {{ resolution.leg_count }} 腿 · 已按赛果判断 {{ resolution.judged_legs }} 腿 · 已有市场终态 {{ resolution.resolved_legs }} 腿</p>
      <div class="mini-card-grid">
        <div v-for="leg in resolution.legs" :key="leg.leg_no" class="mini-card">
          <div class="mini-card-head"><strong>腿 {{ leg.leg_no }} · {{ leg.home }} 对 {{ leg.away }}</strong><span v-if="resultBadge(leg.result_won)" class="result-badge" :class="resultBadge(leg.result_won)?.cls">{{ resultBadge(leg.result_won)?.text }}</span><span v-else class="mini-tag mini-tag-neutral">无法判断</span></div>
          <p class="mini-card-meta">{{ leg.market_question }} · {{ leg.outcome_label }}<span v-if="leg.home_score != null && leg.away_score != null"> · {{ leg.home_score }} : {{ leg.away_score }}</span></p>
          <div class="detail-rows">
            <div class="detail-row"><span class="detail-label">赛事终态：</span><template v-if="leg.score_verified">比赛已结束，外部赛果已核实 {{ leg.home_score }} : {{ leg.away_score }}</template><template v-else>比赛尚未确认结束或外部赛果未核实（{{ leg.game_status }}）</template></div>
            <div class="detail-row"><span class="detail-label">结果一 · 按赛果判断：</span>{{ legWinner(leg.result_won, '无法判断') }}</div>
            <div class="detail-row detail-note"><span class="detail-label">判断依据：</span>{{ leg.result_judgment?.basis ?? '赛果未确认' }}</div>
            <div class="detail-row"><span class="detail-label">结果二 · 该腿 Polymarket 终态结算：</span><template v-if="leg.market?.state === 'resolved'">{{ legWinner(leg.market_won, '暂无终态结果') }} · 每份 {{ leg.market_won ? '1' : '0' }} pUSD · 已最终结算<template v-if="leg.market.resolved_at"> · {{ formatDateTimeSeconds(leg.market.resolved_at) }} UTC+8</template></template><template v-else>暂无终态结果（{{ leg.market?.market_status_text ?? leg.market?.market_error ?? '结算流程尚未完成' }}）<template v-if="leg.market?.market_status"> · 阶段 {{ leg.market.market_status }}</template></template></div>
            <div class="detail-row"><span class="detail-label">单腿核对：</span>{{ legCheck(leg.leg_agreement) }}</div>
          </div>
        </div>
      </div>
      <p class="detail-section-title">组合结论</p>
      <div class="mini-card">
        <div class="detail-rows">
          <div class="detail-row"><span class="detail-label">组合结果一 · 按赛果判断：</span>{{ comboWinner(resolution.by_score) }}</div>
          <div class="detail-row"><span class="detail-label">组合结果二 · 各腿 Polymarket 终态结算：</span>{{ comboWinner(resolution.by_market) }}</div>
          <div class="detail-row"><span class="detail-label">两个结果核对：</span>{{ comboAgreement(resolution.agreement) }}</div>
          <div v-if="resolution.reason" class="detail-row"><span class="detail-label">核对说明：</span>{{ resolution.reason }}</div>
          <div class="detail-row"><span class="detail-label">组合结算金额：</span>该只读核对不计算结算金额；卡片盈亏来自已落库成交明细，实际赔付以 Polymarket 组合仓位终态为准。</div>
        </div>
      </div>
      <p class="detail-section-title">口径说明</p>
      <div class="mini-card mini-card-note"><p>{{ resolution.notice }}</p></div>
    </details>
  </div>
</template>
