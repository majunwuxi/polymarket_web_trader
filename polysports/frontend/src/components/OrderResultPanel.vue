<script setup lang="ts">
import { orderStateName, tradeErrorName } from '../utils/labels'

defineProps<{ result: any }>()
</script>

<template>
  <section class="welcome-card detail">
    <h2>订单状态</h2>
    <p>状态：{{ orderStateName(result.state) }} · 已成交份额：{{ result.matched_size ?? '未知' }}</p>
    <p v-if="result.error_code">状态说明：{{ tradeErrorName(result.error_code) }}</p>
    <p v-if="result.retry_after_seconds != null">建议等待：{{ result.retry_after_seconds }} 秒</p>
    <RouterLink to="/orders">查看交易记录</RouterLink>
  </section>
</template>
