<script setup lang="ts">
defineProps<{
  context: any
  walletLabel: string | undefined
  marketCloseReady: boolean
  busy: boolean
  pending: any
}>()
const emit = defineEmits<{ refresh: []; submit: [] }>()
</script>

<template>
  <section class="welcome-card detail">
    <template v-if="context">
      <p>钱包：{{ walletLabel }} · {{ context.wallet_address }}</p>
      <p>原订单已成交 {{ context.matched_shares }} · 钱包持仓 {{ context.wallet_shares }} · 本次平仓 {{ context.candidate_shares }} 份</p>
      <p v-if="marketCloseReady">当前市价 {{ context.market_price }} · 预计收入约 {{ context.estimated_proceeds }} pUSD</p>
      <p v-if="context.market_close_reason" class="form-message">{{ context.market_close_reason }}</p>
      <p>FOK 卖出：全部成交或不成交；盘口变化需重新获取市价，接受不代表成交。</p>
      <p>{{ context.notice }}</p>
      <button class="text-button" :disabled="busy" @click="emit('refresh')">刷新市价</button>
      <button class="text-button" :disabled="busy || !!pending || !marketCloseReady" @click="emit('submit')">按当前市价直接平仓</button>
      <p>当前市价来自服务端读取的真实盘口，点击即按该市价提交 FOK 卖单；盘口变化请先刷新市价。全部成交或不成交，执行中不代表成交。</p>
    </template>
    <p v-else>正在核实真实持仓与盘口…</p>
  </section>
</template>
