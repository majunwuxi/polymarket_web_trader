<script setup lang="ts">
import { formatDateTime } from '../utils/format'

defineProps<{ preview: any; wallet: any; busy: boolean; pending: boolean; previewExpired: boolean }>()
const emit = defineEmits<{ submit: [] }>()
</script>

<template>
  <section class="welcome-card detail">
    <h2>确认真实资金订单</h2>
    <p>钱包：{{ wallet?.label }} · {{ wallet?.wallet_address }}</p>
    <p v-if="preview.context_snapshot">{{ preview.context_snapshot.home }} 对 {{ preview.context_snapshot.away }} · {{ preview.context_snapshot.market_question }} · {{ preview.context_snapshot.outcome_label }} · {{ formatDateTime(preview.context_snapshot.starts_at) }} UTC+8</p>
    <p>方向：{{ preview.side }} · 价格：{{ preview.limit_price }} · 份额：{{ preview.size }}</p>
    <p>限价金额：{{ preview.estimated_notional }} pUSD；手续费未知，实际成交以交易所记录为准。</p>
    <p>预览有效期至 {{ preview.expires_at }}；提交时将再次检查盘口和钱包。</p>
    <button class="text-button" :disabled="busy || pending || previewExpired" @click="emit('submit')">确认并提交真实订单</button>
    <p v-if="previewExpired">预览已过期，请重新验证并预览。</p>
  </section>
</template>
