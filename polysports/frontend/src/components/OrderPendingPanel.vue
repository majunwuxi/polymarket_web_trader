<script setup lang="ts">
const acknowledged = defineModel<boolean>('acknowledged', { required: true })
defineProps<{ state: string | null; reconcilable: boolean }>()
const emit = defineEmits<{ lookup: []; clear: []; reconcile: [] }>()
</script>

<template>
  <section class="welcome-card">
    <h2>有一笔结果待核对</h2><p>系统保留了原幂等键。查询结果前请勿重复下单。</p>
    <p v-if="state">当前状态：{{ state }}</p>
    <button class="text-button" @click="emit('lookup')">按原键查询</button>
    <button v-if="reconcilable" class="text-button" @click="emit('reconcile')">重新安排对账</button>
    <p>仅在已到 Polymarket 独立核实、并理解重复下单风险后，才清除本机记录。</p>
    <label><input v-model="acknowledged" type="checkbox">我已独立核实订单状态</label>
    <button class="text-button" :disabled="!acknowledged" @click="emit('clear')">清除本机记录</button>
  </section>
</template>
