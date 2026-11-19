<script setup lang="ts">
import { ref, watch, nextTick, computed, onActivated } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { session, logout, clearSession, ApiError } from '../auth'
import AppSidebar from '../components/AppSidebar.vue'
import AppTopbar from '../components/AppTopbar.vue'
import ResultBindingSummary from '../components/ResultBindingSummary.vue'
import GameCard from '../components/GameCard.vue'
import { formatDateTime, formatDateTimeSeconds } from '../utils/format'
import { gameStatusName, orderStateName, resultBadge } from '../utils/labels'

type Team = { id: string; source_name: string; name_zh: string | null; translation_status: string; name_zh_source?: string | null; logo_url: string | null }
type Game = { id: string; provider: string; starts_at: string; status: string; home_team: Team; away_team: Team; home_score: number | null; away_score: number | null; league: {name:string} | null; last_synced_at:string | null; result_binding_state:string | null; result_detected_at:string | null; has_matched_order:boolean }
const route=useRoute(), router=useRouter()
const games=ref<Game[]>([]), next=ref<string|null>(null), message=ref(''), loading=ref(false), menuOpen=ref(false)
const lastSync=ref<{status:string;error_code:string|null;started_at:string|null;scheduled_for:string}|null>(null)
const mappingBusy=ref(false)
const detail=ref<Record<string,any>|null>(null), editor=ref<Team|null>(null), chinese=ref(''), alias=ref(''), selectedRange=ref('upcoming')
const detailDialog=ref<HTMLDialogElement|null>(null), liveData=ref<any>(null), liveError=ref(''), liveLoading=ref(false), binding=ref<any>(null)
const bindingSummary=ref<any>(null),bindingSummaryLoading=ref(false),bindingSummaryError=ref(''),bindingActionError=ref(''),bindingBusy=ref<string|null>(null),expandedBinding=ref<string|null>(null)
let summaryRequest=0
const matchSingle=ref<any[]>([]),matchCombo=ref<any[]>([]),singleCursor=ref<string|null>(null),comboCursor=ref<string|null>(null)
const singleError=ref(''),comboError=ref(''),singleLoading=ref(false),comboLoading=ref(false)
const totalLine=(market:any)=>{const text=String(market.group||market.question||''),match=text.match(/-?\d+(?:\.\d+)?/);return match?Number.parseFloat(match[0]):null}
const compareTotals=(a:any,b:any)=>{const left=totalLine(a),right=totalLine(b);if(left===null&&right===null)return String(a.id).localeCompare(String(b.id));if(left===null)return 1;if(right===null)return -1;return left-right||String(a.id).localeCompare(String(b.id))}
const marketGroups=computed(()=>{const defs=[{type:'moneyline',football:'全场胜平负',nba:'胜负'},{type:'totals',football:'全场总进球大小',nba:'总分大小'}];return defs.map(def=>({...def,label:sport()==='football'?def.football:def.nba,events:(liveData.value?.events||[]).map((event:any)=>{const markets=event.markets.filter((market:any)=>market.type===def.type);if(def.type==='totals')markets.sort(compareTotals);return {...event,markets}}).filter((event:any)=>event.markets.length)}))})
let detailRequest=0
const sport=()=>String(route.params.sport)
const title=()=>sport()==='football'?'足球赛事中心':'NBA 赛事中心'
const name=(team:Team)=>team.name_zh || team.source_name
const scoreLabel=(game:any)=>game.status==='finished'?'全场比分':game.status==='live'?'当前比分':'比分'
const beijingDate=(value:string|Date)=>{const date=value instanceof Date?value:new Date(value);return new Date(date.getTime()+8*3600e3).toISOString().slice(0,10)}
const beijingLabel=(key:string)=>{
  const now=Date.now(),today=beijingDate(new Date(now)),tomorrow=beijingDate(new Date(now+24*3600e3)),yesterday=beijingDate(new Date(now-24*3600e3))
  const [year,month,day]=key.split('-').map(Number)
  const text=`${month}月${day}日 · 周${'日一二三四五六'[new Date(Date.UTC(year,month-1,day)).getUTCDay()]}`
  return key===today?`今天 · ${text}`:key===tomorrow?`明天 · ${text}`:key===yesterday?`昨天 · ${text}`:text
}
const dayGroups=computed(()=>{
  const groups:{key:string;label:string;games:Game[]}[]=[]
  for(const game of games.value){
    const key=beijingDate(game.starts_at),last=groups[groups.length-1]
    if(last&&last.key===key)last.games.push(game)
    else groups.push({key,label:beijingLabel(key),games:[game]})
  }
  return groups
})
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
async function loadBindingSummary(){
  const request=++summaryRequest
  if(sport()!=='football'){bindingSummary.value=null;bindingSummaryError.value='';bindingActionError.value='';bindingSummaryLoading.value=false;expandedBinding.value=null;return}
  bindingSummaryLoading.value=true;bindingSummaryError.value='';bindingActionError.value=''
  try{
    const data=await api('/result-bindings/summary?sport=football&limit=20')
    if(!data||!data.totals||!Array.isArray(data.items))throw new Error('invalid summary')
    if(request===summaryRequest)bindingSummary.value=data
  }catch{if(request===summaryRequest){bindingSummary.value=null;bindingSummaryError.value='赛果绑定统计加载失败。'}}
  finally{if(request===summaryRequest)bindingSummaryLoading.value=false}
}
function openSummaryBinding(item:any){
  if(bindingBusy.value)return
  const candidates=Array.isArray(item.candidates)?item.candidates:[]
  if(candidates.length===1){void confirmSummaryBinding(item,candidates[0]);return}
  expandedBinding.value=expandedBinding.value===item.game_id?null:item.game_id
}
async function confirmSummaryBinding(item:any,candidate:any){
  if(bindingBusy.value)return
  const key=item.game_id+':'+candidate.id;bindingBusy.value=key;bindingActionError.value=''
  try{
    await api('/games/'+item.game_id+'/result-binding/confirm',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({fixture_id:candidate.id})})
    const text='已绑定 '+item.home+' 对 '+item.away+'：'+candidate.home+' 对 '+candidate.away
    if(expandedBinding.value===item.game_id)expandedBinding.value=null
    await Promise.all([loadBindingSummary(),load()])
    message.value=text
  }catch(e){bindingActionError.value=e instanceof Error?e.message:'确认绑定失败，请刷新后重试。'}
  finally{bindingBusy.value=null}
}
async function followGame(game:Game) {
  try {
    const data=await api('/games/'+game.id+'/watch',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:'{}'})
    message.value=data.state==='completed'?'赛事已完赛，已加入关注记录。':'已关注，赛果任务将按计划执行。'
    if(detail.value?.id===game.id) await showDetail(game)
  } catch(e) { message.value=e instanceof Error?e.message:'关注失败，请检查服务状态。' }
}
function closeDetail(){detailRequest++;detailDialog.value?.close();detail.value=null;liveData.value=null;binding.value=null;matchSingle.value=[];matchCombo.value=[]}
const polySlugs=new Map<string,string>(),polyBusy=ref<string|null>(null)
async function openPolymarket(game:Game){
  if(polyBusy.value)return
  polyBusy.value=game.id
  try{
    let slug=polySlugs.get(game.id)
    if(!slug){
      const data=await api('/games/'+game.id+'/live-markets')
      const events=Array.isArray(data?.events)?data.events:[]
      slug=events.map((event:any)=>event.slug).find((value:any)=>typeof value==='string'&&value)||null
      if(slug)polySlugs.set(game.id,slug)
    }
    if(!slug){message.value='未找到本场比赛的 Polymarket 页面。';return}
    window.open('https://polymarket.com/event/'+slug,'_blank','noopener,noreferrer')
  }catch(e){message.value=e instanceof Error?e.message:'Polymarket 页面查询失败，请稍后重试。'}
  finally{if(polyBusy.value===game.id)polyBusy.value=null}
}
async function loadMatchOrders(kind:'single'|'combo',gameId:string,request:number,append=false){
  const values=kind==='single'?matchSingle:matchCombo,cursor=kind==='single'?singleCursor:comboCursor,error=kind==='single'?singleError:comboError,busy=kind==='single'?singleLoading:comboLoading
  busy.value=true;error.value=''
  try{
    const query=new URLSearchParams({game_id:gameId,limit:'20'})
    if(kind==='combo')query.set('state','confirmed')
    if(append&&cursor.value)query.set('cursor',cursor.value)
    const result=await api((kind==='single'?'/trade-intents?':'/combo-rfqs?')+query)
    if(request!==detailRequest)return
    if(!Array.isArray(result.items))throw new Error('invalid response')
    const rows=kind==='combo'?result.items.filter((item:any)=>item.state==='confirmed' && Array.isArray(item.legs) && item.legs.some((leg:any)=>leg.game_id===gameId)):result.items
    if(kind==='combo' && rows.length!==result.items.length)error.value='组合记录与当前赛事不一致，已隐藏不匹配的记录；请确认后端已更新并重启。'
    values.value=append?[...values.value,...rows]:rows;cursor.value=result.next_cursor||null
  }catch{if(request===detailRequest)error.value=kind==='single'?'单场订单加载失败。':'组合订单加载失败。'}
  finally{if(request===detailRequest)busy.value=false}
}
async function showDetail(game:Game) {
  const request=++detailRequest
  detail.value={...game,recent_revisions:[]};liveData.value=null;liveError.value='';liveLoading.value=true;message.value=''
  matchSingle.value=[];matchCombo.value=[];singleCursor.value=null;comboCursor.value=null;singleError.value='';comboError.value='';singleLoading.value=true;comboLoading.value=true
  await nextTick();if(request!==detailRequest)return
  if(detailDialog.value && !detailDialog.value.open) detailDialog.value.showModal()
  void loadMatchOrders('single',game.id,request);void loadMatchOrders('combo',game.id,request)
  const [info,live,bindingResult]=await Promise.allSettled([api('/games/'+game.id),api('/games/'+game.id+'/live-markets'),api('/games/'+game.id+'/result-binding')])
  if(request!==detailRequest)return
  if(info.status==='fulfilled') detail.value={...game,...info.value}
  else message.value='赛事详情加载失败。'
  if(live.status==='fulfilled') liveData.value=live.value
  else liveError.value=live.reason instanceof Error?live.reason.message:'市场查询失败，请稍后重试。'
  binding.value=bindingResult.status==='fulfilled'?bindingResult.value:null
  liveLoading.value=false
}
async function confirmBinding(id:string){if(!detail.value)return;try{binding.value=await api('/games/'+detail.value.id+'/result-binding/confirm',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({fixture_id:id})});await load();message.value='已确认赛果来源，将按计划查询。'}catch(e){message.value=e instanceof Error?e.message:'绑定失败'}}
async function selectOutcome(event:any,market:any,outcome:any,index:string|number){if(!detail.value||mappingBusy.value)return;const selectionRequest=detailRequest;mappingBusy.value=true;message.value='正在核验市场和选项…';try{const data=await api('/games/'+detail.value.id+'/prepare-selection',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({event_id:event.id,market_id:market.id,outcome_index:Number(index),outcome_label:outcome.label})});if(selectionRequest!==detailRequest)return;await router.push({path:'/orders/new/'+data.market_link_id+'/'+data.outcome_id,query:outcome.price!==null?{reference:outcome.price}:{}})}catch(e){message.value=e instanceof Error?e.message:'市场核验失败，请刷新后重试。'}finally{mappingBusy.value=false}}

async function addComboOutcome(event:any,market:any,outcome:any,index:string|number){
  if(!detail.value||mappingBusy.value)return
  const selectionRequest=detailRequest;const game=detail.value;mappingBusy.value=true;message.value='正在检查组合市场…'
  try{
    const chosen=await api('/games/'+game.id+'/prepare-selection',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({event_id:event.id,market_id:market.id,outcome_index:Number(index),outcome_label:outcome.label})})
    await api('/combo-legs/'+chosen.market_link_id+'/'+chosen.outcome_id)
    if(selectionRequest!==detailRequest)return
    const key='polysports_combo_legs';const legs=JSON.parse(localStorage.getItem(key)||'[]')
    if(!Array.isArray(legs))throw new Error('组合单数据无法读取，请先清理组合单。')
    if(legs.some((leg:any)=>leg.market_link_id===chosen.market_link_id)){message.value='该市场已有选项在组合单中，请先移除原选项。';return}
    legs.push({...chosen,label:name(game.home_team)+' 对 '+name(game.away_team)+' · '+(market.group||market.question||'市场')+' · '+outcome.label})
    localStorage.setItem(key,JSON.stringify(legs));message.value='已加入组合，可继续选择其他比赛，或打开组合交易。'
  }catch(e){message.value=e instanceof Error?e.message:'无法验证组合市场，请稍后重试。'}finally{mappingBusy.value=false}
}
function edit(team:Team){editor.value=team; chinese.value=team.name_zh || ''; alias.value=''}
function editFromDetail(team:Team){closeDetail();edit(team)}
async function saveTeam() {
  if(!editor.value) return
  try {
    await api('/teams/'+editor.value.id,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({name_zh:chinese.value.trim() || null,translation_status:chinese.value.trim()?'verified':'missing'})})
    if(alias.value.trim()) await api('/teams/'+editor.value.id+'/aliases',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({alias:alias.value.trim(),language:'zh-CN',verified:true})})
    editor.value=null; detail.value=null; await load()
  } catch {message.value='球队资料保存失败，译名可能已保存；请刷新后核对。'}
}
async function signOut() {try {await logout(); await router.replace('/login')} catch(e) {if(e instanceof ApiError && e.status===401) {clearSession(); await router.replace('/login')} else message.value='退出请求未确认，请重试。'}}
watch(()=>route.params.sport,()=>{menuOpen.value=false;closeDetail();selectedRange.value='upcoming';load();loadBindingSummary()},{immediate:true})
let activated=false
onActivated(()=>{if(!activated){activated=true;return}menuOpen.value=false;closeDetail();load();loadBindingSummary()})

</script>
<template>
<div class="app-layout">
  <AppSidebar :open="menuOpen" @toggle="menuOpen=!menuOpen" />
  <main class="workspace"><AppTopbar :breadcrumb="'工作台 / '+title()" :username="session.username" @sign-out="signOut" />
    <div class="page-heading"><div><p class="eyebrow">MATCH CENTER · UTC+8</p><h1>{{title()}}</h1><p class="muted">时间显示为北京时间；赛事由配置的数据源提供。</p></div></div>
    <ResultBindingSummary v-if="sport()==='football'" :summary="bindingSummary" :loading="bindingSummaryLoading" :error="bindingSummaryError" :action-error="bindingActionError" :busy="bindingBusy" :expanded="expandedBinding" @refresh="loadBindingSummary" @open="openSummaryBinding" @confirm="confirmSummaryBinding" />
<div class="game-tools"><label for="range">赛事范围</label><select id="range" v-model="selectedRange" @change="load()"><option value="upcoming">{{sport()==='football'?'过去 2 小时 30 分至未来 12 小时':'过去 3 小时至未来 12 小时'}}</option><option value="history">历史赛事</option></select><button class="text-button" @click="load()">刷新</button></div>
    <p class="muted" v-if="lastSync">{{lastSync.started_at ? '最近同步' : '计划同步'}}：{{formatDateTime(lastSync.started_at || lastSync.scheduled_for)}} UTC+8 · {{lastSync.status === 'succeeded' ? '成功' : lastSync.status === 'failed' ? '失败' : lastSync.status === 'queued' ? '排队中' : lastSync.status === 'running' ? '进行中' : '未知'}}<span v-if="lastSync.error_code">（{{lastSync.error_code}}）</span></p><p class="muted" v-else>尚无同步记录。</p><p v-if="!detail" role="status" class="form-message">{{message}}</p><p v-if="loading && !games.length">正在加载…</p><p v-else-if="!games.length && !message" class="muted">暂无已保存赛事。若尚未同步，请先配置数据源并运行同步命令。</p>
    <div class="games-list">
      <template v-for="group in dayGroups" :key="group.key">
        <div class="day-divider" role="separator"><span>{{group.label}}</span></div>
        <GameCard v-for="game in group.games" :key="game.id" :game="game" :sport="sport()" :poly-loading="polyBusy===game.id" @detail="showDetail" @follow="followGame" @poly="openPolymarket" />
      </template>
    </div>
    <button v-if="next" class="primary more" :disabled="loading" @click="load(true)">加载更多</button>
    <dialog ref="detailDialog" class="game-dialog" aria-labelledby="game-dialog-title" @close="closeDetail">
      <div v-if="detail" class="dialog-content">
        <div class="detail-sticky">
        <header class="dialog-heading"><div><p class="eyebrow">MATCH CENTRE <span> / </span> UTC+8</p><h2 id="game-dialog-title">赛事详情</h2></div><button class="dialog-close" autofocus aria-label="关闭详情" @click="closeDetail">×</button></header>
        <p v-if="message" role="status" class="form-message">{{message}}</p>
        <section class="match-summary" aria-label="比赛信息">
          <div class="summary-meta"><span>{{detail.league?.name || (sport()==='nba'?'NBA':'足球')}}</span><span>·</span><span>{{detail.starts_at ? formatDateTime(detail.starts_at)+' UTC+8' : '开赛时间未知'}}</span><span class="summary-status">{{gameStatusName(detail.status)}}</span></div>
          <div v-if="detail.home_team && detail.away_team" class="summary-teams"><div class="summary-team"><img v-if="detail.home_team.logo_url" :src="detail.home_team.logo_url" alt=""><span v-else class="summary-logo">主</span><div><small>主队</small><strong>{{name(detail.home_team)}}</strong><small v-if="detail.home_team.name_zh" class="summary-source-name">{{detail.home_team.source_name}}</small><button class="text-button team-maintain" @click="editFromDetail(detail.home_team)">维护名称</button></div></div><div v-if="detail.home_score!==null && detail.away_score!==null" class="game-scoreboard summary-scoreboard" :aria-label="`${scoreLabel(detail)}：${name(detail.home_team)} ${detail.home_score} 比 ${detail.away_score} ${name(detail.away_team)}`"><span class="game-score-label">{{scoreLabel(detail)}}</span><div class="game-score-line"><strong :class="detail.home_score>detail.away_score?'score-winner':detail.home_score<detail.away_score?'score-loser':''">{{detail.home_score}}</strong><span class="game-score-sep">:</span><strong :class="detail.away_score>detail.home_score?'score-winner':detail.away_score<detail.home_score?'score-loser':''">{{detail.away_score}}</strong></div></div><span v-else class="summary-versus">VS</span><div class="summary-team away"><div><small>客队</small><strong>{{name(detail.away_team)}}</strong><small v-if="detail.away_team.name_zh" class="summary-source-name">{{detail.away_team.source_name}}</small><button class="text-button team-maintain" @click="editFromDetail(detail.away_team)">维护名称</button></div><img v-if="detail.away_team.logo_url" :src="detail.away_team.logo_url" alt=""><span v-else class="summary-logo">客</span></div></div>
          <div class="summary-foot"><span>来源：{{detail.provider}} · 来源 ID：{{detail.external_id}}</span><span v-if="detail.result_binding_state==='bound'">赛果首次检测：{{detail.result_detected_at ? formatDateTimeSeconds(detail.result_detected_at)+' UTC+8' : '尚未检测'}}</span><span>最近同步：{{detail.last_synced_at ? formatDateTime(detail.last_synced_at)+' UTC+8' : '未知'}}</span></div>
        </section>
        </div>
        <section class="welcome-card detail match-orders" aria-label="本场订单明细">
          <h3>本场订单</h3>
          <p v-if="singleLoading || comboLoading">正在加载订单…</p>
          <p v-if="singleError" role="alert">{{singleError}} <button class="text-button" @click="loadMatchOrders('single',detail.id,detailRequest)">重试</button></p>
          <p v-if="comboError" role="alert">{{comboError}} <button class="text-button" @click="loadMatchOrders('combo',detail.id,detailRequest)">重试</button></p>
          <p v-if="!singleLoading && !comboLoading && !singleError && !comboError && !matchSingle.length && !matchCombo.length">暂无本场订单。</p>
          <article v-for="order in matchSingle" :key="order.id" class="match-order-card">
            <strong>单场{{order.side==='BUY'?'买入':'卖出'}} · {{orderStateName(order.state)}}<span v-if="resultBadge(order.result_won)" class="result-badge" :class="resultBadge(order.result_won)?.cls">{{resultBadge(order.result_won)?.text}}</span></strong>
            <p>{{order.market_question}} · {{order.outcome_label}}</p>
            <p>限价 {{order.limit_price}} · 份额 {{order.size}} · 已成交 {{order.matched_size ?? '未知'}}</p>
            <p>创建时间：{{formatDateTime(order.created_at)}} UTC+8</p>
            <p>订单编号：{{order.external_order_id || '尚未取得'}}</p>
          </article>
          <button v-if="singleCursor" class="text-button" :disabled="singleLoading" @click="loadMatchOrders('single',detail.id,detailRequest,true)">加载更多单场订单</button>
          <article v-for="combo in matchCombo" :key="combo.id" class="match-order-card">
            <strong>组合{{combo.direction==='BUY'?'买入':'卖出'}} · 已确认</strong>
            <p v-for="leg in combo.legs.filter((item:any)=>item.game_id===detail?.id)" :key="leg.outcome_id">本场选项：{{leg.market_question || '市场信息未知'}} · {{leg.outcome_label || '选项信息未知'}}<span v-if="resultBadge(leg.result_won)" class="result-badge" :class="resultBadge(leg.result_won)?.cls">{{resultBadge(leg.result_won)?.text}}</span></p>
            <p>组合预算或份额：{{combo.quantity}}<span v-if="combo.quote"> · 组合报价：{{combo.quote.price}} · 报价所需：{{combo.quote.total_required}}</span></p>
            <p>创建时间：{{formatDateTime(combo.created_at)}} UTC+8</p>
            <p>组合 RFQ：{{combo.external_rfq_id || '未知'}}<span v-if="combo.tx_hash"> · 交易哈希：{{combo.tx_hash}}</span></p>
          </article>
          <button v-if="comboCursor" class="text-button" :disabled="comboLoading" @click="loadMatchOrders('combo',detail.id,detailRequest,true)">加载更多组合订单</button>
          <RouterLink v-if="matchSingle.length || matchCombo.length" to="/orders">查看全部交易记录</RouterLink>
        </section>
        <section v-if="binding" class="welcome-card detail"><h3>外部赛果绑定</h3><p>状态：{{binding.state}} <span v-if="binding.error_code">· {{binding.error_code}}</span></p><p v-for="candidate in binding.candidates" :key="candidate.id">{{candidate.home}} 对 {{candidate.away}} · {{formatDateTime(candidate.starts_at)}} UTC+8 · {{candidate.league || 'NBA'}} <button v-if="binding.state==='needs_review'" class="text-button" @click="confirmBinding(candidate.id)">确认绑定</button></p></section>
        <aside v-if="detail.provider==='polymarket'" class="bet-notice"><div><strong>下单说明</strong><p>点击选项可单场下单，或点击“＋组合”加入组合单；订单确认后开始匹配外部赛果。</p></div><span>接受不等于成交</span></aside>
        <section class="market-panel" aria-label="Polymarket 市场信息"><div class="market-panel-heading"><div><p class="eyebrow">POLYMARKET</p><h3>市场信息</h3></div><span v-if="liveData" class="market-time">{{formatDateTime(liveData.observed_at)}} UTC+8 更新</span></div>
          <p class="market-caption">参考价格以查询时来源数据为准，不保证成交。<span v-if="liveData?.match_status==='candidate'"> · 球队与时间匹配的候选，尚未确认。</span></p>
          <p v-if="liveLoading" role="status" class="market-empty">正在查询市场…</p><p v-else-if="liveError" role="alert" class="market-empty">{{liveError}}</p>
          <template v-else-if="liveData">
            <p v-if="!liveData.events.length" class="market-empty">未发现可核对的 Polymarket 市场。</p>
            <section v-for="group in marketGroups" :key="group.type" class="classified-markets"><div class="market-category-heading"><h4>{{group.label}}</h4><span>{{group.events.reduce((count:number,event:any)=>count+event.markets.length,0)}} 个市场</span></div><p v-if="!group.events.length" class="market-empty">暂无该类市场。</p><article v-for="event in group.events" :key="event.id" class="market-group"><div class="market-group-heading"><strong>{{event.title || '市场事件'}}</strong><a v-if="event.slug" :href="'https://polymarket.com/event/'+event.slug" target="_blank" rel="noopener noreferrer">在 Polymarket 查看 ↗</a></div><div class="moneyline-grid"><div v-for="market in event.markets" :key="market.id" class="market-tile"><h4>{{market.group || market.question || '市场'}}</h4><p v-if="market.group && market.question" class="market-question">{{market.question}}</p><span class="market-state">{{market.closed === true ? '已关闭' : market.accepting_orders === true ? '接收订单' : market.accepting_orders === false ? '暂不接单' : '状态未知'}}</span><div v-if="market.outcomes.length" class="outcome-list"><span v-for="(outcome,index) in market.outcomes" :key="index" class="outcome-choice"><button type="button" class="outcome-button" :disabled="mappingBusy || detail.provider!=='polymarket' || liveData?.match_status!=='source_identity' || market.closed!==false || market.accepting_orders!==true" @click="selectOutcome(event,market,outcome,index)">{{outcome.label}} <b>{{outcome.price === null ? '未知' : (Number(outcome.price)*100).toFixed(1)+'%'}}</b></button><button type="button" class="combo-add" :disabled="mappingBusy || detail.provider!=='polymarket' || liveData?.match_status!=='source_identity' || market.closed!==false || market.accepting_orders!==true" :aria-label="'将'+outcome.label+'加入组合'" @click="addComboOutcome(event,market,outcome,index)">＋组合</button></span></div><p v-else>选项数据暂不可用。</p></div></div></article></section>
          </template>
        </section>
<details class="match-history"><summary>赛事来源与变更记录</summary><p v-if="detail.watch">关注状态：{{detail.watch.state}}<span v-if="detail.watch.next_due_at"> · 下次查询：{{formatDateTime(detail.watch.next_due_at)}} UTC+8</span></p><p v-if="!detail.recent_revisions.length">暂无变更记录</p><p v-for="(revision,index) in detail.recent_revisions" :key="index">{{formatDateTime(revision.observed_at)}} · {{revision.old_status}} → {{revision.new_status}}<span v-if="revision.old_starts_at !== revision.new_starts_at"> · 时间已调整</span></p></details>
</div></dialog>
    <section v-if="editor" class="welcome-card detail"><h2>维护球队：{{editor.source_name}}</h2><label for="chinese">已确认中文名（留空表示待补充）</label><input id="chinese" v-model="chinese" maxlength="200"><label for="alias">中文别名（可选）</label><input id="alias" v-model="alias" maxlength="200"><div><button class="text-button" @click="editor=null">取消</button><button class="text-button" @click="saveTeam">保存</button></div></section>
  </main>
</div>
</template>
