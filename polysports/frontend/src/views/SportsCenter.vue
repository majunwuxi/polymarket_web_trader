<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { session, logout, clearSession, ApiError } from '../auth'

type Team = { id: string; source_name: string; name_zh: string | null; translation_status: string; name_zh_source?: string | null; logo_url: string | null }
type Game = { id: string; provider: string; starts_at: string; status: string; home_team: Team; away_team: Team; home_score: number | null; away_score: number | null; league: {name:string} | null; last_synced_at:string | null }
const route=useRoute(), router=useRouter()
const games=ref<Game[]>([]), next=ref<string|null>(null), message=ref(''), loading=ref(false), menuOpen=ref(false)
const lastSync=ref<{status:string;error_code:string|null;started_at:string|null;scheduled_for:string}|null>(null)
const markets=ref<any[]>([]), refreshState=ref<any>(null), mappingBusy=ref(false), pendingLink=ref<string|null>(null)
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
async function followGame(game:Game) {
  try {
    const data=await api('/games/'+game.id+'/watch',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:'{}'})
    message.value=data.state==='completed'?'赛事已完赛，已加入关注记录。':'已关注，赛果任务将按计划执行。'
    if(detail.value?.id===game.id) await showDetail(game)
  } catch(e) { message.value=e instanceof Error?e.message:'关注失败，请检查服务状态。' }
}
async function showDetail(game:Game) { try { detail.value=await api('/games/'+game.id); await loadMarkets(game.id) } catch {message.value='赛事详情加载失败。'} }
async function loadMarkets(gameId:string) { try { const data=await api('/games/'+gameId+'/markets'); markets.value=data.items; refreshState.value=data.refresh } catch { message.value='市场记录加载失败。' } }
async function refreshMarkets() { if(!detail.value || mappingBusy.value) return; mappingBusy.value=true; message.value=''; try { await api('/games/'+detail.value.id+'/markets/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:'{}'}); message.value='已开始搜索；稍后点击检查搜索状态。'; await loadMarkets(detail.value.id) } catch { message.value='市场搜索未能启动。' } finally {mappingBusy.value=false} }
async function decide(linkId:string, decision:'confirm'|'reject') { if(!detail.value) return; try {await api('/market-links/'+linkId+'/'+decision,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:'{}'}); pendingLink.value=null; await loadMarkets(detail.value.id); message.value=decision==='confirm'?'映射已确认，可进行订单预览。':'映射已拒绝。'} catch(e){message.value=e instanceof Error?e.message:'映射操作失败。'} }

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
watch(()=>route.params.sport,()=>{menuOpen.value=false;detail.value=null;markets.value=[];refreshState.value=null;pendingLink.value=null;selectedRange.value='upcoming';load()},{immediate:true})
function addCombo(item:any,outcome:any){
  try {const key='polysports_combo_legs';const legs=JSON.parse(localStorage.getItem(key)||'[]');if(legs.some((x:any)=>x.market_link_id===item.link.id)){message.value='同一市场不能重复加入组合。';return}legs.push({market_link_id:item.link.id,outcome_id:outcome.id,label:item.event.home+' 对 '+item.event.away+' · '+item.market.question+' · '+outcome.label});localStorage.setItem(key,JSON.stringify(legs));message.value='已加入组合，请在组合交易页面核对。'}catch{message.value='无法保存组合腿。'}
}
</script>
<template>
<div class="app-layout">
  <header class="mobile-header"><div class="brand"><span class="brand-mark">P</span> PolySports</div><button class="menu-button" :aria-expanded="menuOpen" aria-controls="primary-nav" @click="menuOpen=!menuOpen">菜单</button></header>
  <aside id="primary-nav" class="sidebar" :class="{open:menuOpen}"><div class="brand sidebar-brand"><span class="brand-mark">P</span> PolySports</div><p class="nav-label">工作台</p><nav aria-label="主导航"><RouterLink to="/">总览</RouterLink><RouterLink to="/sports/football">足球赛事</RouterLink><RouterLink to="/sports/nba">NBA 赛事</RouterLink><RouterLink to="/combos">组合交易</RouterLink><RouterLink to="/orders">交易记录</RouterLink><RouterLink to="/history">历史与导出</RouterLink><RouterLink to="/wallets">钱包状态</RouterLink><RouterLink to="/system">系统管理</RouterLink></nav></aside>
  <main class="workspace"><div class="workspace-top"><span class="breadcrumb">工作台 / {{title()}}</span><div class="account"><span>{{session.username}}</span><button class="text-button" @click="signOut">退出</button></div></div>
    <div class="page-heading"><div><p class="eyebrow">MATCH CENTER · UTC+8</p><h1>{{title()}}</h1><p class="muted">时间显示为北京时间；赛事由配置的数据源提供。</p></div></div>
    <div class="game-tools"><label for="range">赛事范围</label><select id="range" v-model="selectedRange" @change="load()"><option value="upcoming">未来 36 小时</option><option value="history">历史赛事</option></select><button class="text-button" @click="load()">刷新</button></div>
    <p class="muted" v-if="lastSync">{{lastSync.started_at ? '最近同步' : '计划同步'}}：{{dateFormat(lastSync.started_at || lastSync.scheduled_for)}} UTC+8 · {{lastSync.status === 'succeeded' ? '成功' : lastSync.status === 'failed' ? '失败' : lastSync.status === 'queued' ? '排队中' : lastSync.status === 'running' ? '进行中' : '未知'}}<span v-if="lastSync.error_code">（{{lastSync.error_code}}）</span></p><p class="muted" v-else>尚无同步记录。</p><p role="status" class="form-message">{{message}}</p><p v-if="loading && !games.length">正在加载…</p><p v-else-if="!games.length && !message" class="muted">暂无已保存赛事。若尚未同步，请先配置数据源并运行同步命令。</p>
    <div class="games-list"><article v-for="game in games" :key="game.id" class="game-card"><div class="game-meta">{{game.league?.name || (sport()==='nba'?'NBA':'足球')}} · {{game.provider==='polymarket'?'Polymarket 胜平负 · 赛果待绑定 · ':''}} {{dateFormat(game.starts_at)}} UTC+8 · {{statusName(game.status)}}</div><div class="matchup"><div v-for="team in [game.home_team,game.away_team]" :key="team.id" class="team"><img v-if="team.logo_url" :src="team.logo_url" alt="" @error="team.logo_url=null"><span v-else class="logo-placeholder">队徽待补充</span><strong>{{name(team)}}</strong><small v-if="!team.name_zh">中文名待补充</small><small v-else-if="team.name_zh_source==='sportsmatch'">球队目录参考名，待核对</small><button class="text-button" @click="edit(team)">维护球队</button></div></div><p v-if="game.home_score!==null && game.away_score!==null">{{game.home_score}} : {{game.away_score}}</p><button class="text-button" @click="showDetail(game)">查看详情</button><button v-if="game.provider!=='polymarket'" class="text-button" @click="followGame(game)">关注赛事</button><span v-else>赛果来源待绑定，暂不可关注或下单</span></article></div>
    <button v-if="next" class="primary more" :disabled="loading" @click="load(true)">加载更多</button>
    <section v-if="detail" class="welcome-card detail"><button class="text-button" @click="detail=null">关闭详情</button><h2>赛事详情</h2><p>来源：{{detail.provider}} · 来源 ID：{{detail.external_id}}</p><p v-if="detail.home_team && detail.away_team">赛事主队：{{name(detail.home_team)}} · 客队：{{name(detail.away_team)}} · 开赛：{{dateFormat(detail.starts_at)}} UTC+8</p><p>最近同步：{{detail.last_synced_at ? dateFormat(detail.last_synced_at)+' UTC+8' : '未知'}}</p><p v-if="detail.watch">关注状态：{{detail.watch.state}}<span v-if="detail.watch.next_due_at"> · 下次查询：{{dateFormat(detail.watch.next_due_at)}} UTC+8</span></p><h3>变更记录</h3><p v-if="!detail.recent_revisions.length">暂无变更记录</p><p v-for="(revision,index) in detail.recent_revisions" :key="index">{{dateFormat(revision.observed_at)}} · {{revision.old_status}} → {{revision.new_status}}<span v-if="revision.old_starts_at !== revision.new_starts_at"> · 时间已调整</span></p></section>
    <section v-if="detail && detail.provider==='polymarket'" class="welcome-card detail"><h2>投注与赛果</h2><p>API-Football 赛事绑定将在下一阶段提供；当前无法关注或下单。</p></section><section v-if="detail && detail.provider!=='polymarket'" class="welcome-card detail"><h2>Polymarket 市场映射</h2><p>对照主客队和开赛时间后人工确认；下单前仍需核验账户、地区、余额和盘口。</p><div class="game-tools"><button class="text-button" :disabled="mappingBusy" @click="refreshMarkets">搜索候选市场</button><button class="text-button" @click="loadMarkets(detail.id)">检查搜索状态</button></div><p v-if="refreshState">搜索状态：{{refreshState.status==='succeeded'?'已完成':refreshState.status==='running'?'进行中':'失败'}}<span v-if="refreshState.error_code"> · {{refreshState.error_code}}</span></p><p v-else>尚未搜索该赛事的市场。</p><p v-if="refreshState?.status==='succeeded' && !markets.length">未发现匹配的候选市场。</p>
    <article v-for="item in markets" :key="item.link.id" class="market-card"><h3>{{item.market.question || '市场名称未知'}}</h3><p>映射状态：{{item.link.state}} · 主客顺序：{{item.link.evidence.ordered?'一致':'不一致，不能确认'}}</p><p v-if="item.link.evidence.home_name_ambiguous || item.link.evidence.away_name_ambiguous">球队名称存在歧义，请特别核对赛事来源与时间。</p><p>市场主队：{{item.event.home}} · 客队：{{item.event.away}} · 开赛：{{dateFormat(item.event.starts_at)}} UTC+8</p><p>Gamma 市场 ID：{{item.market.gamma_market_id}} · 协议：{{item.market.protocol_version || '未知'}}</p><p class="id-text">Condition ID：{{item.market.condition_id}}</p><p>接单：{{item.market.accepting_orders === true ? '来源显示可接单' : item.market.accepting_orders === false ? '来源显示不接单' : '未知'}} · 组合能力：{{item.market.combo_enabled === null ? '未知' : item.market.combo_enabled ? '可用' : '不可用'}}</p><ul><li v-for="outcome in item.market.outcomes" :key="outcome.id" class="id-text">{{outcome.label}} · {{outcome.asset_kind}}：{{outcome.asset_id}} <RouterLink v-if="item.link.state==='confirmed' && item.market.accepting_orders === true" :to="'/orders/new/'+item.link.id+'/'+outcome.id">订单预览</RouterLink> <button v-if="item.link.state==='confirmed'" class="text-button" @click="addCombo(item,outcome)">加入组合</button></li></ul><p>确认映射不表示可以交易；订单须重新核验。</p><button v-if="item.link.can_confirm && item.link.state!=='confirmed'" class="text-button" @click="pendingLink=item.link.id">核对并确认</button><button v-if="item.link.state!=='rejected'" class="text-button" @click="decide(item.link.id,'reject')">拒绝映射</button><div v-if="pendingLink===item.link.id" class="mapping-confirm"><p>请核实上述赛事、主客队、开赛时间及市场问题。</p><button class="text-button" @click="pendingLink=null">取消</button><button class="text-button" @click="decide(item.link.id,'confirm')">我已核对，确认映射</button></div></article></section>
    <section v-if="editor" class="welcome-card detail"><h2>维护球队：{{editor.source_name}}</h2><label for="chinese">已确认中文名（留空表示待补充）</label><input id="chinese" v-model="chinese" maxlength="200"><label for="alias">中文别名（可选）</label><input id="alias" v-model="alias" maxlength="200"><div><button class="text-button" @click="editor=null">取消</button><button class="text-button" @click="saveTeam">保存</button></div></section>
  </main>
</div>
</template>
