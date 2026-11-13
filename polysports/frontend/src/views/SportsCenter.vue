<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { session, logout, clearSession, ApiError } from '../auth'

type Team = { id: string; source_name: string; name_zh: string | null; translation_status: string; logo_url: string | null }
type Game = { id: string; starts_at: string; status: string; home_team: Team; away_team: Team; home_score: number | null; away_score: number | null; league: {name:string} | null; last_synced_at:string | null }
const route=useRoute(), router=useRouter()
const games=ref<Game[]>([]), next=ref<string|null>(null), message=ref(''), loading=ref(false), menuOpen=ref(false)
const lastSync=ref<{status:string;error_code:string|null;started_at:string}|null>(null)
const detail=ref<Record<string,any>|null>(null), editor=ref<Team|null>(null), chinese=ref(''), alias=ref(''), selectedRange=ref('upcoming')
const sport=()=>String(route.params.sport)
const title=()=>sport()==='football'?'足球赛事中心':'NBA 赛事中心'
const dateFormat=(value:string)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))
const name=(team:Team)=>team.name_zh || team.source_name
const statusName=(status:string)=>({scheduled:'未开始',live:'进行中',finished:'已完赛',postponed:'已延期',cancelled:'已取消',interrupted:'已中断',unknown:'状态未知'} as Record<string,string>)[status] || '状态未知'
async function api(path:string, options:RequestInit={}) {
  const response=await fetch('/api/v1'+path,{credentials:'same-origin',cache:'no-store',...options})
  const payload=await response.json()
  if(!response.ok) throw new Error(payload?.error?.message || '服务暂不可用')
  return payload.data
}
async function load(append=false) {
  loading.value=true; message.value=''
  try {
    const query=new URLSearchParams({sport:sport(),limit:'20'})
    if(selectedRange.value==='history') { const now=new Date(); query.set('from','2000-01-01T00:00:00Z'); query.set('to',now.toISOString()) }
    if(append && next.value) query.set('cursor',next.value)
    const [result, runs]=await Promise.all([api('/games?'+query),api('/sync-runs?sport='+sport()+'&limit=1')])
    lastSync.value=runs.items[0] || null
    games.value=append?[...games.value,...result.items]:result.items; next.value=result.next_cursor
  } catch { message.value='赛事加载失败，请检查服务状态。'; if(!append) games.value=[] }
  finally { loading.value=false }
}
async function showDetail(game:Game) { try { detail.value=await api('/games/'+game.id) } catch {message.value='赛事详情加载失败。'} }
function edit(team:Team){editor.value=team; chinese.value=team.name_zh || ''; alias.value=''}
async function saveTeam() {
  if(!editor.value) return
  try {
    await api('/teams/'+editor.value.id,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({name_zh:chinese.value.trim() || null,translation_status:chinese.value.trim()?'verified':'missing'})})
    if(alias.value.trim()) await api('/teams/'+editor.value.id+'/aliases',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({alias:alias.value.trim(),language:'zh-CN',verified:true})})
    editor.value=null; detail.value=null; await load()
  } catch {message.value='球队资料保存失败，译名可能已保存；请刷新后核对。'}
}
async function signOut() {try {await logout(); await router.replace('/login')} catch(e) {if(e instanceof ApiError && e.status===401) {clearSession(); await router.replace('/login')} else message.value='退出请求未确认，请重试。'}}
watch(()=>route.params.sport,()=>{detail.value=null;selectedRange.value='upcoming';load()},{immediate:true})
</script>
<template>
<div class="app-layout">
  <header class="mobile-header"><div class="brand"><span class="brand-mark">P</span> PolySports</div><button class="menu-button" :aria-expanded="menuOpen" aria-controls="primary-nav" @click="menuOpen=!menuOpen">菜单</button></header>
  <aside id="primary-nav" class="sidebar" :class="{open:menuOpen}"><div class="brand sidebar-brand"><span class="brand-mark">P</span> PolySports</div><p class="nav-label">工作台</p><nav aria-label="主导航"><RouterLink to="/">总览</RouterLink><RouterLink to="/sports/football">足球赛事</RouterLink><RouterLink to="/sports/nba">NBA 赛事</RouterLink><span aria-disabled="true">组合交易 <small>即将开放</small></span><span aria-disabled="true">交易记录 <small>即将开放</small></span><span aria-disabled="true">系统管理 <small>即将开放</small></span></nav></aside>
  <main class="workspace"><div class="workspace-top"><span class="breadcrumb">工作台 / {{title()}}</span><div class="account"><span>{{session.username}}</span><button class="text-button" @click="signOut">退出</button></div></div>
    <div class="page-heading"><div><p class="eyebrow">MATCH CENTER · UTC+8</p><h1>{{title()}}</h1><p class="muted">时间显示为北京时间；赛事由配置的数据源提供。</p></div></div>
    <div class="game-tools"><label for="range">赛事范围</label><select id="range" v-model="selectedRange" @change="load()"><option value="upcoming">未来 36 小时</option><option value="history">历史赛事</option></select><button class="text-button" @click="load()">刷新</button></div>
    <p class="muted" v-if="lastSync">最近同步：{{dateFormat(lastSync.started_at)}} UTC+8 · {{lastSync.status === 'succeeded' ? '成功' : lastSync.status === 'failed' ? '失败' : '进行中'}}<span v-if="lastSync.error_code">（{{lastSync.error_code}}）</span></p><p class="muted" v-else>尚无同步记录。</p><p role="status" class="form-message">{{message}}</p><p v-if="loading && !games.length">正在加载…</p><p v-else-if="!games.length && !message" class="muted">暂无已保存赛事。若尚未同步，请先配置数据源并运行同步命令。</p>
    <div class="games-list"><article v-for="game in games" :key="game.id" class="game-card"><div class="game-meta">{{game.league?.name || (sport()==='nba'?'NBA':'联赛未知')}} · {{dateFormat(game.starts_at)}} UTC+8 · {{statusName(game.status)}}</div><div class="matchup"><div v-for="team in [game.home_team,game.away_team]" :key="team.id" class="team"><img v-if="team.logo_url" :src="team.logo_url" alt="" @error="team.logo_url=null"><span v-else class="logo-placeholder">队徽待补充</span><strong>{{name(team)}}</strong><small v-if="!team.name_zh">中文名待补充</small><button class="text-button" @click="edit(team)">维护球队</button></div></div><p v-if="game.home_score!==null && game.away_score!==null">{{game.home_score}} : {{game.away_score}}</p><button class="text-button" @click="showDetail(game)">查看详情</button></article></div>
    <button v-if="next" class="primary more" :disabled="loading" @click="load(true)">加载更多</button>
    <section v-if="detail" class="welcome-card detail"><button class="text-button" @click="detail=null">关闭详情</button><h2>赛事详情</h2><p>来源：{{detail.provider}} · 来源 ID：{{detail.external_id}}</p><p>最近同步：{{detail.last_synced_at ? dateFormat(detail.last_synced_at)+' UTC+8' : '未知'}}</p><h3>变更记录</h3><p v-if="!detail.recent_revisions.length">暂无变更记录</p><p v-for="(revision,index) in detail.recent_revisions" :key="index">{{dateFormat(revision.observed_at)}} · {{revision.old_status}} → {{revision.new_status}}<span v-if="revision.old_starts_at !== revision.new_starts_at"> · 时间已调整</span></p></section>
    <section v-if="editor" class="welcome-card detail"><h2>维护球队：{{editor.source_name}}</h2><label for="chinese">已确认中文名（留空表示待补充）</label><input id="chinese" v-model="chinese" maxlength="200"><label for="alias">中文别名（可选）</label><input id="alias" v-model="alias" maxlength="200"><div><button class="text-button" @click="editor=null">取消</button><button class="text-button" @click="saveTeam">保存</button></div></section>
  </main>
</div>
</template>
