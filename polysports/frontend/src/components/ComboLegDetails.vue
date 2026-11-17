<script setup lang="ts">
import { formatDateTimeSeconds } from '../utils/format'
import { comboBuySource, legWinner, orderBindingName, resultBadge, watchStateName } from '../utils/labels'

defineProps<{ combo: any }>()
</script>

<template>
  <p class="detail-section-title">逐腿信息</p>
  <div class="mini-card-grid">
    <div v-for="leg in combo.legs" :key="leg.outcome_id" class="mini-card">
      <div class="mini-card-head"><strong>{{ leg.home }} 对 {{ leg.away }}</strong><span v-if="resultBadge(leg.result_won)" class="result-badge" :class="resultBadge(leg.result_won)?.cls">{{ resultBadge(leg.result_won)?.text }}</span><span v-else class="mini-tag mini-tag-neutral">无法判断</span></div>
      <p class="mini-card-meta">{{ leg.market_question }} · {{ leg.outcome_label }}</p>
      <div class="detail-rows">
        <div class="detail-row"><span class="detail-label">比赛时间：</span>{{ formatDateTimeSeconds(leg.starts_at) }} UTC+8</div>
        <div class="detail-row"><span class="detail-label">比分追踪：</span>{{ watchStateName(leg.watch_state) }} · 外部赛果：{{ orderBindingName(leg.binding_state) }}<span v-if="leg.home_score != null && leg.away_score != null"> · 当前比分：{{ leg.home_score }} : {{ leg.away_score }}</span><span v-else> · 比分待查询</span></div>
        <div v-if="leg.next_poll_at" class="detail-row"><span class="detail-label">下次查询：</span>{{ formatDateTimeSeconds(leg.next_poll_at) }} UTC+8</div>
        <div class="detail-row" :title="leg.result_basis ?? ''"><span class="detail-label">赛果判断：</span>{{ legWinner(leg.result_won, '无法判断') }}</div>
      </div>
      <RouterLink v-if="leg.sport === 'football' || leg.sport === 'nba'" class="text-button" :to="'/sports/' + leg.sport">前往赛事中心核对</RouterLink>
    </div>
  </div>
  <p class="detail-section-title">买入与报价</p>
  <div class="mini-card">
    <div class="detail-rows">
      <div class="detail-row"><span class="detail-label">金额来源：</span>{{ comboBuySource(combo) }}</div>
      <div class="detail-row"><span class="detail-label">预算 / 份额参数：</span>{{ combo.quantity }}（BUY 为预算，不等于份额）</div>
      <div v-if="combo.quote" class="detail-row"><span class="detail-label">签名报价：</span>每份 {{ combo.quote.price }} · 报价所需 {{ combo.quote.total_required }} · 预计{{ combo.direction === 'BUY' ? '份额' : '收入' }} {{ combo.quote.net_receive ?? '未知' }}</div>
      <div v-if="combo.fill" class="detail-row"><span class="detail-label">成交明细：</span>支出 {{ combo.fill.cost }} · 份额 {{ combo.fill.size }} · {{ combo.fill.source === 'signed_order' ? '签名接受订单' : '按签名报价重建' }}<span v-if="combo.fill.confirmed_at"> · {{ formatDateTimeSeconds(combo.fill.confirmed_at) }} UTC+8</span></div>
    </div>
  </div>
  <p class="detail-section-title">标识与状态</p>
  <div class="mini-card">
    <div class="detail-rows">
      <div class="detail-row id-text"><span class="detail-label">钱包：</span>{{ combo.wallet_address }}</div>
      <div class="detail-row id-text"><span class="detail-label">组合 RFQ：</span>{{ combo.external_rfq_id || '未知' }}</div>
      <div v-if="combo.tx_hash" class="detail-row id-text"><span class="detail-label">交易哈希：</span>{{ combo.tx_hash }}</div>
      <div v-if="combo.error_code" class="detail-row"><span class="detail-label">状态说明：</span>{{ combo.error_code }}</div>
    </div>
  </div>
</template>
