<script setup lang="ts">
import { formatDateTime } from '../utils/format'

defineProps<{
  summary: any
  loading: boolean
  error: string
  actionError: string
  busy: string | null
  expanded: string | null
}>()
const emit = defineEmits<{
  refresh: []
  open: [item: any]
  confirm: [item: any, candidate: any]
}>()
</script>

<template>
  <section class="welcome-card binding-summary" aria-label="足球赛果绑定状态">
    <div class="binding-summary-head">
      <div><p class="eyebrow">RESULT BINDING</p><h2>足球赛果绑定状态</h2></div>
      <button class="text-button" :disabled="loading" @click="emit('refresh')">刷新绑定状态</button>
    </div>
    <p v-if="loading" role="status">正在加载赛果绑定状态…</p>
    <p v-else-if="error" role="alert" class="form-message">{{ error }}</p>
    <template v-else-if="summary">
      <div class="info-grid binding-metrics">
        <div><span>待确认</span><strong>{{ summary.totals.needs_review }}</strong></div>
        <div><span>自动匹配中</span><strong>{{ summary.totals.pending }}</strong></div>
        <div><span>已绑定</span><strong>{{ summary.totals.bound }}</strong></div>
      </div>
      <p class="muted">后台会自动查询 API-FOOTBALL，找到候选后进入待确认。</p>
      <p v-if="actionError" role="alert" class="form-message">{{ actionError }}</p>
      <div v-if="summary.items.length" class="binding-unbound">
        <span class="binding-unbound-label">未绑定球队</span>
        <div class="binding-team-links">
          <button v-for="item in summary.items" :key="item.game_id" class="binding-team-link" :disabled="!!busy" :title="item.candidates.length === 1 ? '点击直接绑定唯一的赛果候选' : '多个候选，点击展开选择'" @click="emit('open', item)">
            {{ item.home }} 对 {{ item.away }}
            <small v-if="item.candidates.length > 1">（{{ item.candidates.length }} 个候选）</small>
          </button>
        </div>
      </div>
      <p v-if="!summary.totals.total" class="muted">暂无需要绑定赛果的足球赛事。</p>
      <p v-else-if="!summary.items.length && summary.totals.pending" class="muted">当前没有可直接绑定的未绑定球队；自动匹配中的赛事找到候选后会显示为链接。</p>
      <p v-else-if="!summary.items.length" class="muted">当前没有可直接绑定的未绑定球队。</p>
      <div v-if="summary.items.length" class="binding-queue">
        <template v-for="item in summary.items" :key="item.game_id">
          <article v-if="expanded === item.game_id" class="binding-row">
            <div class="binding-row-head"><strong>{{ item.home }} 对 {{ item.away }} · 选择候选</strong><span>{{ item.league || '联赛未知' }} · {{ formatDateTime(item.starts_at) }} UTC+8</span></div>
            <p class="muted">Polymarket 来源 ID：{{ item.external_id }} · 必须选择与这场比赛对应的候选</p>
            <div class="binding-candidates">
              <div v-for="candidate in item.candidates" :key="candidate.id" class="binding-candidate">
                <span>{{ candidate.home }} 对 {{ candidate.away }} · {{ formatDateTime(candidate.starts_at) }} UTC+8 · {{ candidate.league || '联赛未知' }}</span>
                <button class="text-button" :disabled="!!busy" @click="emit('confirm', item, candidate)">{{ busy === item.game_id + ':' + candidate.id ? '绑定中…' : '确认绑定' }}</button>
              </div>
            </div>
          </article>
        </template>
      </div>
      <p v-if="summary.has_more" class="muted">仅显示前 20 条待确认赛事，处理后刷新可查看其余记录。</p>
    </template>
  </section>
</template>
