<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  summary?: { size?: string | null; price?: string | null; cost?: string | null; estimated?: boolean } | null
}>()

const metrics = computed(() => {
  const summary = props.summary
  return [
    { label: '份额总额', value: summary?.size ? summary.size + ' 份' : '待核实' },
    { label: '买入价格', value: summary?.price ? summary.price + ' pUSD/份' : '待核实' },
    { label: '买入金额', value: summary?.cost ? (summary.estimated ? '约' : '') + summary.cost + ' pUSD' : '待核实' },
  ]
})
</script>

<template>
  <div class="buy-metrics" aria-label="组合买入摘要">
    <div v-for="metric in metrics" :key="metric.label" class="buy-metric">
      <span class="buy-metric-label">{{ metric.label }}</span>
      <strong class="buy-metric-value">{{ metric.value }}</strong>
    </div>
  </div>
</template>
