<script setup lang="ts">
type Step = { name: string; state: string; elapsed_ms: number; attempts: number }
defineProps<{ steps?: Step[] }>()

const labels: Record<string, string> = {
  geography: '地区可用性',
  credentials: '钱包凭据',
  session: '交易会话',
  balance_allowance: '余额与授权读取',
}
</script>

<template>
  <ul v-if="steps?.length" aria-label="钱包诊断步骤">
    <li v-for="step in steps" :key="step.name">
      {{ labels[step.name] ?? step.name }} · {{ step.state === 'passed' ? '通过' : '失败' }}
      · {{ step.elapsed_ms }} ms<span v-if="step.attempts > 1"> · 已重试</span>
    </li>
  </ul>
</template>
