<script setup lang="ts">
import { formatDateTime } from '../utils/format'

defineProps<{ wallets: any[]; book: any; context: any; orderSize: string; busy: boolean; estimatedNotional: string; pending: any }>()
const wallet = defineModel<string>('wallet', { required: true })
const side = defineModel<string>('side', { required: true })
const price = defineModel<string>('price', { required: true })
const size = defineModel<string>('size', { required: true })
const budget = defineModel<string>('budget', { required: true })
const inputMode = defineModel<'amount' | 'shares'>('inputMode', { required: true })
const emit = defineEmits<{ submit: [] }>()
</script>

<template>
  <section class="welcome-card detail">
    <p v-if="context">{{ context.home }} 对 {{ context.away }} · {{ context.market_question }} · 选项：{{ context.outcome_label }} · {{ formatDateTime(context.starts_at) }} UTC+8</p>
    <p v-if="context" class="id-text">资产 ID：{{ context.asset_id }} · 协议：{{ context.protocol_version }}</p>
    <p v-if="book">价格增量 {{ book.tick_size }} · 最小份额 {{ book.min_order_size }} · 盘口时间 {{ book.observed_at }}</p>
    <p v-else>真实盘口不可用。</p>
    <form @submit.prevent="emit('submit')">
      <label for="wallet">钱包</label>
      <select id="wallet" v-model="wallet" required><option v-for="w in wallets" :key="w.id" :value="w.id">{{ w.label }} · {{ w.wallet_address }}</option></select>
      <label for="side">方向</label>
      <select id="side" v-model="side"><option value="BUY">买入</option><option value="SELL">卖出</option></select>
      <label for="price">限价</label>
      <input id="price" v-model="price" inputmode="decimal" required>
      <template v-if="side === 'BUY'">
        <label for="input-mode">输入方式</label>
        <select id="input-mode" v-model="inputMode"><option value="amount">购买金额（pUSD）</option><option value="shares">购买份额</option></select>
      </template>
      <template v-if="side === 'BUY' && inputMode === 'amount'">
        <label for="budget">购买金额上限（pUSD）</label>
        <input id="budget" v-model="budget" inputmode="decimal" required>
        <p>按限价计算的份额：{{ orderSize || '请填写有效金额与限价' }}；份额向下取两位小数，手续费另计。</p>
      </template>
      <template v-else>
        <label for="size">份额</label>
        <input id="size" v-model="size" inputmode="decimal" required>
      </template>
      <p v-if="estimatedNotional">预计限价金额 {{ estimatedNotional }} pUSD；手续费未知，实际成交以交易所记录为准。</p>
      <p>点击「按当前价直接下单」将按当前盘口立即提交真实资金订单，不设二次确认；执行中不代表成交。</p>
      <button class="text-button" :disabled="busy || !!pending || !wallet || !book || !orderSize">按当前价直接下单</button>
    </form>
  </section>
</template>
