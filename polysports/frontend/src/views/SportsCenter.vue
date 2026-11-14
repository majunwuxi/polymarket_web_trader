<script setup lang="ts">
import { ref, watch, nextTick, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { session, logout, clearSession, ApiError } from '../auth'

type Team = { id: string; source_name: string; name_zh: string | null; translation_status: string; name_zh_source?: string | null; logo_url: string | null }
type Game = { id: string; provider: string; starts_at: string; status: string; home_team: Team; away_team: Team; home_score: number | null; away_score: number | null; league: {name:string} | null; last_synced_at:string | null; result_binding_state:string | null; has_matched_order:boolean }
const route=useRoute(), router=useRouter()
const games=ref<Game[]>([]), next=ref<string|null>(null), message=ref(''), loading=ref(false), menuOpen=ref(false)
const lastSync=ref<{status:string;error_code:string|null;started_at:string|null;scheduled_for:string}|null>(null)
const mappingBusy=ref(false)
const detail=ref<Record<string,any>|null>(null), editor=ref<Team|null>(null), chinese=ref(''), alias=ref(''), selectedRange=ref('upcoming')
const detailDialog=ref<HTMLDialogElement|null>(null), liveData=ref<any>(null), liveError=ref(''), liveLoading=ref(false), binding=ref<any>(null)
const marketGroups=computed(()=>{const defs=[{type:'moneyline',football:'全场胜平负',nba:'胜负'},{type:'spreads',football:'全场让球',nba:'让分'},{type:'totals',football:'全场总进球大小',nba:'总分大小'}];return defs.map(def=>({...def,label:sport()==='football'?def.football:def.nba,events:(liveData.value?.events||[]).map((event:any)=>({...event,markets:event.markets.filter((market:any)=>market.type===def.type)})).filter((event:any)=>event.markets.length)}))})
let detailRequest=0
const sport=()=>String(route.params.sport)
const title=()=>sport()==='football'?'足球赛事中心':'NBA 赛事中心'
const dateFormat=(value:string)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))
const name=(team:Team)=>team.name_zh || team.source_name
const bindingName=(state:string|null)=>({bound:'赛果已绑定',pending:'赛果匹配中',needs_review:'赛果待确认'} as Record<string,string>)[state || ''] || '赛果未绑定'
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
function closeDetail(){detailRequest++;detailDialog.value?.close();detail.value=null;liveData.value=null;binding.value=null}
async function showDetail(game:Game) {
  const request=++detailRequest
  detail.value={...game,recent_revisions:[]};liveData.value=null;liveError.value='';liveLoading.value=true;message.value=''
  await nextTick();if(request!==detailRequest)return
  if(detailDialog.value && !detailDialog.value.open) detailDialog.value.showModal()
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
async function saveTeam() {
  if(!editor.value) return
  try {
    await api('/teams/'+editor.value.id,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({name_zh:chinese.value.trim() || null,translation_status:chinese.value.trim()?'verified':'missing'})})
    if(alias.value.trim()) await api('/teams/'+editor.value.id+'/aliases',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({alias:alias.value.trim(),language:'zh-CN',verified:true})})
    editor.value=null; detail.value=null; await load()
  } catch {message.value='球队资料保存失败，译名可能已保存；请刷新后核对。'}
}
async function signOut() {try {await logout(); await router.replace('/login')} catch(e) {if(e instanceof ApiError && e.status===401) {clearSession(); await router.replace('/login')} else message.value='退出请求未确认，请重试。'}}
watch(()=>route.params.sport,()=>{menuOpen.value=false;closeDetail();selectedRange.value='upcoming';load()},{immediate:true})

</script>
<template>
<div class="app-layout">
  <header class="mobile-header"><div class="brand"><span class="brand-mark">P</span> PolySports</div><button class="menu-button" :aria-expanded="menuOpen" aria-controls="primary-nav" @click="menuOpen=!menuOpen">菜单</button></header>
  <aside id="primary-nav" class="sidebar" :class="{open:menuOpen}"><div class="brand sidebar-brand"><span class="brand-mark">P</span> PolySports</div><p class="nav-label">工作台</p><nav aria-label="主导航"><RouterLink to="/">总览</RouterLink><RouterLink to="/sports/football">足球赛事</RouterLink><RouterLink to="/sports/nba">NBA 赛事</RouterLink><RouterLink to="/combos">组合交易</RouterLink><RouterLink to="/orders">交易记录</RouterLink><RouterLink to="/history">历史与导出</RouterLink><RouterLink to="/wallets">钱包状态</RouterLink><RouterLink to="/system">系统管理</RouterLink></nav></aside>
  <main class="workspace"><div class="workspace-top"><span class="breadcrumb">工作台 / {{title()}}</span><div class="account"><span>{{session.username}}</span><button class="text-button" @click="signOut">退出</button></div></div>
    <div class="page-heading"><div><p class="eyebrow">MATCH CENTER · UTC+8</p><h1>{{title()}}</h1><p class="muted">时间显示为北京时间；赛事由配置的数据源提供。</p></div></div>
    <div class="game-tools"><label for="range">赛事范围</label><select id="range" v-model="selectedRange" @change="load()"><option value="upcoming">未来 36 小时</option><option value="history">历史赛事</option></select><button class="text-button" @click="load()">刷新</button></div>
    <p class="muted" v-if="lastSync">{{lastSync.started_at ? '最近同步' : '计划同步'}}：{{dateFormat(lastSync.started_at || lastSync.scheduled_for)}} UTC+8 · {{lastSync.status === 'succeeded' ? '成功' : lastSync.status === 'failed' ? '失败' : lastSync.status === 'queued' ? '排队中' : lastSync.status === 'running' ? '进行中' : '未知'}}<span v-if="lastSync.error_code">（{{lastSync.error_code}}）</span></p><p class="muted" v-else>尚无同步记录。</p><p v-if="!detail" role="status" class="form-message">{{message}}</p><p v-if="loading && !games.length">正在加载…</p><p v-else-if="!games.length && !message" class="muted">暂无已保存赛事。若尚未同步，请先配置数据源并运行同步命令。</p>
    <div class="games-list"><article v-for="game in games" :key="game.id" class="game-card"><div class="game-meta">{{game.league?.name || (sport()==='nba'?'NBA':'足球')}} · {{game.provider==='polymarket'?(sport()==='football'?'Polymarket 胜平负 · ':'Polymarket NBA · ')+bindingName(game.result_binding_state)+' · ':''}} {{dateFormat(game.starts_at)}} UTC+8 · {{statusName(game.status)}}</div><div class="matchup"><div v-for="team in [game.home_team,game.away_team]" :key="team.id" class="team"><img v-if="team.logo_url" :src="team.logo_url" alt="" @error="team.logo_url=null"><span v-else class="logo-placeholder">队徽待补充</span><strong>{{name(team)}}</strong><small v-if="sport()==='nba' && team.name_zh">{{team.source_name}}</small><small v-if="!team.name_zh">中文名待补充</small><small v-else-if="team.name_zh_source==='sportsmatch'">球队目录参考名，待核对</small><button class="text-button" @click="edit(team)">维护球队</button></div></div><p v-if="game.has_matched_order" class="game-filled">● 已成交</p><p v-if="game.home_score!==null && game.away_score!==null">{{game.home_score}} : {{game.away_score}}</p><button class="text-button" @click="showDetail(game)">查看详情</button><button v-if="game.provider!=='polymarket'" class="text-button" @click="followGame(game)">关注赛事</button><span v-else-if="!game.result_binding_state">确认订单后开始绑定赛果</span></article></div>
    <button v-if="next" class="primary more" :disabled="loading" @click="load(true)">加载更多</button>
    <dialog ref="detailDialog" class="game-dialog" aria-labelledby="game-dialog-title" @close="closeDetail">
      <div v-if="detail" class="dialog-content">
        <div class="detail-sticky">
        <header class="dialog-heading"><div><p class="eyebrow">MATCH CENTRE <span> / </span> UTC+8</p><h2 id="game-dialog-title">赛事详情</h2></div><button class="dialog-close" autofocus aria-label="关闭详情" @click="closeDetail">×</button></header>
        <p v-if="message" role="status" class="form-message">{{message}}</p>
        <section class="match-summary" aria-label="比赛信息">
          <div class="summary-meta"><span>{{detail.league?.name || (sport()==='nba'?'NBA':'足球')}}</span><span>·</span><span>{{detail.starts_at ? dateFormat(detail.starts_at)+' UTC+8' : '开赛时间未知'}}</span><span class="summary-status">{{statusName(detail.status)}}</span></div>
          <div v-if="detail.home_team && detail.away_team" class="summary-teams"><div class="summary-team"><img v-if="detail.home_team.logo_url" :src="detail.home_team.logo_url" alt=""><span v-else class="summary-logo">主</span><div><small>主队</small><strong>{{name(detail.home_team)}}</strong></div></div><span class="summary-versus">VS</span><div class="summary-team away"><div><small>客队</small><strong>{{name(detail.away_team)}}</strong></div><img v-if="detail.away_team.logo_url" :src="detail.away_team.logo_url" alt=""><span v-else class="summary-logo">客</span></div></div>
          <div class="summary-foot"><span>来源：{{detail.provider}} · 来源 ID：{{detail.external_id}}</span><span>最近同步：{{detail.last_synced_at ? dateFormat(detail.last_synced_at)+' UTC+8' : '未知'}}</span></div>
        </section>
        </div>
        <section v-if="binding" class="welcome-card detail"><h3>外部赛果绑定</h3><p>状态：{{binding.state}} <span v-if="binding.error_code">· {{binding.error_code}}</span></p><p v-for="candidate in binding.candidates" :key="candidate.id">{{candidate.home}} 对 {{candidate.away}} · {{dateFormat(candidate.starts_at)}} UTC+8 · {{candidate.league || 'NBA'}} <button v-if="binding.state==='needs_review'" class="text-button" @click="confirmBinding(candidate.id)">确认绑定</button></p></section>
        <aside v-if="detail.provider==='polymarket'" class="bet-notice"><div><strong>下单说明</strong><p>点击选项可单场下单，或点击“＋组合”加入组合单；订单确认后开始匹配外部赛果。</p></div><span>接受不等于成交</span></aside>
        <section class="market-panel" aria-label="Polymarket 市场信息"><div class="market-panel-heading"><div><p class="eyebrow">POLYMARKET</p><h3>市场信息</h3></div><span v-if="liveData" class="market-time">{{dateFormat(liveData.observed_at)}} UTC+8 更新</span></div>
          <p class="market-caption">参考价格以查询时来源数据为准，不保证成交。<span v-if="liveData?.match_status==='candidate'"> · 球队与时间匹配的候选，尚未确认。</span></p>
          <p v-if="liveLoading" role="status" class="market-empty">正在查询市场…</p><p v-else-if="liveError" role="alert" class="market-empty">{{liveError}}</p>
          <template v-else-if="liveData">
            <p v-if="!liveData.events.length" class="market-empty">未发现可核对的 Polymarket 市场。</p>
            <section v-for="group in marketGroups" :key="group.type" class="classified-markets"><div class="market-category-heading"><h4>{{group.label}}</h4><span>{{group.events.reduce((count:number,event:any)=>count+event.markets.length,0)}} 个市场</span></div><p v-if="!group.events.length" class="market-empty">暂无该类市场。</p><article v-for="event in group.events" :key="event.id" class="market-group"><div class="market-group-heading"><strong>{{event.title || '市场事件'}}</strong><a v-if="event.slug" :href="'https://polymarket.com/event/'+event.slug" target="_blank" rel="noopener noreferrer">在 Polymarket 查看 ↗</a></div><div class="moneyline-grid"><div v-for="market in event.markets" :key="market.id" class="market-tile"><h4>{{market.group || market.question || '市场'}}</h4><p v-if="market.group && market.question" class="market-question">{{market.question}}</p><span class="market-state">{{market.closed === true ? '已关闭' : market.accepting_orders === true ? '接收订单' : market.accepting_orders === false ? '暂不接单' : '状态未知'}}</span><div v-if="market.outcomes.length" class="outcome-list"><span v-for="(outcome,index) in market.outcomes" :key="index" class="outcome-choice"><button type="button" class="outcome-button" :disabled="mappingBusy || detail.provider!=='polymarket' || liveData?.match_status!=='source_identity' || market.closed!==false || market.accepting_orders!==true" @click="selectOutcome(event,market,outcome,index)">{{outcome.label}} <b>{{outcome.price === null ? '未知' : (Number(outcome.price)*100).toFixed(1)+'%'}}</b></button><button type="button" class="combo-add" :disabled="mappingBusy || detail.provider!=='polymarket' || liveData?.match_status!=='source_identity' || market.closed!==false || market.accepting_orders!==true" :aria-label="'将'+outcome.label+'加入组合'" @click="addComboOutcome(event,market,outcome,index)">＋组合</button></span></div><p v-else>选项数据暂不可用。</p></div></div></article></section>
          </template>
        </section>
<details class="match-history"><summary>赛事来源与变更记录</summary><p v-if="detail.watch">关注状态：{{detail.watch.state}}<span v-if="detail.watch.next_due_at"> · 下次查询：{{dateFormat(detail.watch.next_due_at)}} UTC+8</span></p><p v-if="!detail.recent_revisions.length">暂无变更记录</p><p v-for="(revision,index) in detail.recent_revisions" :key="index">{{dateFormat(revision.observed_at)}} · {{revision.old_status}} → {{revision.new_status}}<span v-if="revision.old_starts_at !== revision.new_starts_at"> · 时间已调整</span></p></details>
</div></dialog>
    <section v-if="editor" class="welcome-card detail"><h2>维护球队：{{editor.source_name}}</h2><label for="chinese">已确认中文名（留空表示待补充）</label><input id="chinese" v-model="chinese" maxlength="200"><label for="alias">中文别名（可选）</label><input id="alias" v-model="alias" maxlength="200"><div><button class="text-button" @click="editor=null">取消</button><button class="text-button" @click="saveTeam">保存</button></div></section>
  </main>
</div>
</template>
