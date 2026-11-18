<script setup lang="ts">
import { formatDateTime } from '../utils/format'
import { gameBindingName, gameStatusName } from '../utils/labels'

type Team = { id: string; source_name: string; name_zh: string | null; translation_status: string; name_zh_source?: string | null; logo_url: string | null }
type Game = { id: string; provider: string; starts_at: string; status: string; home_team: Team; away_team: Team; home_score: number | null; away_score: number | null; league: { name: string } | null; last_synced_at: string | null; result_binding_state: string | null; has_matched_order: boolean }

defineProps<{ game: Game; sport: string; polyLoading?: boolean }>()
const emit = defineEmits<{ detail: [game: Game]; follow: [game: Game]; poly: [game: Game] }>()

const name = (team: Team) => team.name_zh || team.source_name
const teamNameNote = (game: Game) => [game.home_team, game.away_team].some(team => !team.name_zh) ? '中文名待补充' : ''
const scoreLabel = (game: Game) => game.status === 'finished' ? '全场比分' : game.status === 'live' ? '当前比分' : '比分'
const googleSearchUrl = (game: Game) => {
  const parts = [game.home_team.source_name, 'vs', game.away_team.source_name]
  if (game.league?.name) parts.push(game.league.name)
  return 'https://www.google.com/search?q=' + encodeURIComponent(parts.join(' '))
}
</script>

<template>
  <article class="game-card" :class="{ 'has-matched-order': game.has_matched_order }">
    <div class="game-meta">{{ game.league?.name || (sport === 'nba' ? 'NBA' : '足球') }} · <template v-if="game.provider === 'polymarket'">{{ sport === 'football' ? 'Polymarket 胜平负' : 'Polymarket NBA' }} · {{ gameBindingName(game.result_binding_state) }} · </template>{{ formatDateTime(game.starts_at) }} · {{ gameStatusName(game.status) }}<span v-if="teamNameNote(game)" class="game-name-note">{{ teamNameNote(game) }}</span></div>
    <div class="matchup">
      <div v-for="team in [game.home_team, game.away_team]" :key="team.id" class="team">
        <img v-if="team.logo_url" :src="team.logo_url" alt="" @error="team.logo_url = null">
        <span v-else class="logo-placeholder">队徽待补充</span>
        <strong>{{ name(team) }}</strong>
        <small v-if="sport === 'nba' && team.name_zh">{{ team.source_name }}</small>
      </div>
    </div>
    <div v-if="game.home_score !== null && game.away_score !== null" class="game-scoreboard" :aria-label="`${scoreLabel(game)}：${name(game.home_team)} ${game.home_score} 比 ${game.away_score} ${name(game.away_team)}`">
      <span class="game-score-label">{{ scoreLabel(game) }}</span>
      <div class="game-score-line">
        <strong :class="game.home_score > game.away_score ? 'score-winner' : game.home_score < game.away_score ? 'score-loser' : ''">{{ game.home_score }}</strong>
        <span class="game-score-sep">:</span>
        <strong :class="game.away_score > game.home_score ? 'score-winner' : game.away_score < game.home_score ? 'score-loser' : ''">{{ game.away_score }}</strong>
      </div>
    </div>
    <p v-if="game.has_matched_order" class="game-filled">● 已成交</p>
    <div class="game-actions">
      <button class="text-button" @click="emit('detail', game)">查看详情</button>
      <button v-if="game.provider !== 'polymarket'" class="text-button" @click="emit('follow', game)">关注赛事</button>
      <button class="text-button" :disabled="polyLoading" @click="emit('poly', game)">在 Polymarket 查看 ↗</button>
      <a class="text-button" :href="googleSearchUrl(game)" target="_blank" rel="noopener noreferrer">Google 搜索 ↗</a>
    </div>
  </article>
</template>
