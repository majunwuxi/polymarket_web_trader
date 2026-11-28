<script setup lang="ts">
import { onActivated, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { session, logout, clearSession, ApiError } from '../auth'
import { api } from '../api'
import { formatDateTime } from '../utils/format'
type Schedule = {sport:'football'|'nba';provider:'polymarket'|'balldontlie';utc_time:string;enabled:boolean}
type Job = {id:string;kind:string;status:string;due_at:string;attempt_count:number;failure_count:number;last_error_code:string|null;last_error_summary:string|null;target?:{label?:string;starts_at?:string;game_status?:string;state?:string;trade_state?:string}|null}
type Worker = {id:string;state:string;last_seen_at:string}
const router=useRouter(), menuOpen=ref(false), revision=ref(0), items=ref<Schedule[]>([]), jobs=ref<Job[]>([]), workers=ref<Worker[]>([]), message=ref(''), busy=ref(false), selected=ref<Job|null>(null), attempts=ref<any[]>([]),system=ref<any>(null),syncRuns=ref<any[]>([]),audit=ref<any[]>([])
const utc8=(v:string)=>{const [h,m]=v.split(':').map(Number);return String((h+8)%24).padStart(2,'0')+':'+String(m).padStart(2,'0')}
const jobKind=(kind:string)=>({sync:'赛程同步',result_poll:'赛果查询',trade_reconcile:'订单对账',combo_reconcile:'组合对账',cleanup:'数据清理',combo_watchdog:'组合持仓巡检'} as Record<string,string>)[kind]||kind
const jobStatus=(status:string)=>({pending:'待执行',leased:'执行中',done:'已完成',failed:'已停止',cancelled:'已取消'} as Record<string,string>)[status]||status
const format=formatDateTime
const footballOptions=ref<{id:string;label:string}[]>([])
const footballSelection=ref<string[]>([])
const footballDisplayMode=ref<'all'|'selected'>('all')
const footballDisplayBusy=ref(false), footballDisplayMessage=ref('')
async function loadFootballDisplay(){
  footballDisplayBusy.value=true
  footballDisplayMessage.value=''
  try{
    const data=await api('/system/football-display')
    if(!Array.isArray(data?.options))throw new Error('无法读取足球显示设置')
    footballOptions.value=data.options
    footballDisplayMode.value=data.competition_ids===null?'all':'selected'
    footballSelection.value=data.competition_ids ?? data.options.map((item:{id:string})=>item.id)
  }catch(e){footballDisplayMessage.value=e instanceof Error?e.message:'无法读取足球显示设置'}
  finally{footballDisplayBusy.value=false}
}
async function saveFootballDisplay(){
  footballDisplayBusy.value=true
  footballDisplayMessage.value=''
  try{
    await api('/system/football-display',{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},
      body:JSON.stringify({competition_ids:footballDisplayMode.value==='all'?null:footballSelection.value})})
    footballDisplayMessage.value='足球赛事显示设置已保存。'
  }catch(e){footballDisplayMessage.value=e instanceof Error?e.message:'保存足球显示设置失败'}
  finally{footballDisplayBusy.value=false}
}
const limits=ref({percent:'',floor:'1',daily:''})
const limitsReady=ref(false), limitsBusy=ref(false), limitsMessage=ref('')
async function loadLimits(){
  limitsBusy.value=true
  try{
    const data=await api('/system/trade-limits')
    if(typeof data?.order_limit_floor_usd!=='string')throw new Error('交易限额数据不可用')
    limits.value={percent:data.order_limit_percent??'',floor:data.order_limit_floor_usd,daily:data.max_daily_usd??''}
    limitsReady.value=true
  }catch(e){limitsMessage.value=e instanceof Error?e.message:'读取交易限额失败'}
  finally{limitsBusy.value=false}
}
async function saveLimits(){
  limitsBusy.value=true
  limitsMessage.value=''
  try{
    await api('/system/trade-limits',{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({
      order_limit_percent:limits.value.percent||null,
      order_limit_floor_usd:limits.value.floor,
      max_daily_usd:limits.value.daily||null,
    })})
    limitsMessage.value='交易限额已保存，下次提交时生效。'
  }catch(e){limitsMessage.value=e instanceof Error?e.message:'保存交易限额失败'}
  finally{limitsBusy.value=false}
}
async function load(){
  const now=Date.now()
  const jobWindow=new URLSearchParams({from:new Date(now-20*3600e3).toISOString(),to:new Date(now+24*3600e3).toISOString()})
  const recent=new URLSearchParams({from:new Date(now-24*3600e3).toISOString(),to:new Date(now).toISOString()})
  try {
    const [plan,task,status,info,runs,events]=await Promise.all([
      api('/sync-schedules'),api('/jobs?'+jobWindow),api('/worker-status'),api('/system/status'),
      api('/sync-runs?'+recent),api('/audit-events?'+recent),
    ])
    system.value=info;syncRuns.value=runs.items||[];audit.value=events.items||[]
    revision.value=plan.revision;items.value=plan.items.map((x:Schedule)=>({...x,utc_time:x.utc_time.slice(0,5)}))
    jobs.value=task.items;workers.value=status.workers
  }catch{message.value='加载失败，请检查服务状态。'}
}
function add(sport:'football'|'nba'){items.value.push({sport,provider:'polymarket',utc_time:'12:00',enabled:true})}
async function save(){busy.value=true;message.value='';try{await api('/sync-schedules',{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({revision:revision.value,items:items.value})});await load();message.value='同步计划已保存。'}catch(e){message.value=e instanceof Error?e.message:'保存失败。'}finally{busy.value=false}}
async function enqueue(sport:'football'|'nba'){try{await api('/sync-runs',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({sport})});message.value='已提交同步任务，请确认 worker 正在运行。';await load()}catch{message.value='提交同步任务失败。'}}
async function showRuns(job:Job){try{selected.value=job;attempts.value=(await api('/jobs/'+job.id+'/runs')).items}catch{message.value='任务记录加载失败。'}}
async function signOut(){try{await logout();await router.replace('/login')}catch(e){if(e instanceof ApiError&&e.status===401){clearSession();await router.replace('/login')}else message.value='退出请求未确认，请重试。'}}
onMounted(()=>{void load();void loadLimits();void loadFootballDisplay()})
let activated=false
onActivated(()=>{if(!activated){activated=true;return}void load()})
</script>
<template>
<div class="app-layout"><header class="mobile-header"><div class="brand"><span class="brand-mark">P</span> PolySports</div><button class="menu-button" :aria-expanded="menuOpen" aria-controls="primary-nav" @click="menuOpen=!menuOpen">菜单</button></header>
<aside id="primary-nav" class="sidebar" :class="{open:menuOpen}"><div class="brand sidebar-brand"><span class="brand-mark">P</span> PolySports</div><p class="nav-label">工作台</p><nav aria-label="主导航"><RouterLink to="/">总览</RouterLink><RouterLink to="/sports/football">足球赛事</RouterLink><RouterLink to="/sports/nba">NBA 赛事</RouterLink><RouterLink to="/combos">组合交易</RouterLink><RouterLink to="/orders">交易记录</RouterLink><RouterLink to="/pnl-calendar">每日盈亏</RouterLink><RouterLink to="/history">历史与导出</RouterLink><RouterLink to="/wallets">钱包状态</RouterLink><RouterLink to="/system">系统管理</RouterLink></nav></aside>
<main class="workspace"><div class="workspace-top"><span class="breadcrumb">工作台 / 系统管理</span><div class="account"><span>{{session.username}}</span><button class="text-button" @click="signOut">退出</button></div></div><div class="page-heading"><div><p class="eyebrow">SCHEDULE &amp; STATUS</p><h1>同步与任务</h1><p class="muted">同步时间使用 UTC；任务时间显示为 UTC+8。</p></div></div><p role="status" class="form-message">{{message}}</p>
<section class="welcome-card detail">
  <h2>足球赛事显示</h2>
  <p>设置适用于全站足球赛事名单。同步任务照常运行。</p>
  <form v-if="footballOptions.length" @submit.prevent="saveFootballDisplay">
    <label for="football-display-mode">显示范围</label>
    <select id="football-display-mode" v-model="footballDisplayMode">
      <option value="all">全部联赛</option>
      <option value="selected">仅勾选的联赛</option>
    </select>
    <fieldset v-if="footballDisplayMode==='selected'" class="football-competitions">
      <legend>显示的联赛</legend>
      <div class="football-selection-actions">
        <button type="button" class="text-button" @click="footballSelection=footballOptions.map(item=>item.id)">全选</button>
        <button type="button" class="text-button" @click="footballSelection=[]">清空</button>
      </div>
      <div class="football-option-grid">
        <label v-for="option in footballOptions" :key="option.id">
          <input v-model="footballSelection" type="checkbox" :value="option.id"> {{option.label}}
        </label>
      </div>
    </fieldset>
    <button class="text-button" :disabled="footballDisplayBusy">保存显示设置</button>
  </form>
  <button v-else type="button" class="text-button" :disabled="footballDisplayBusy" @click="loadFootballDisplay">读取显示设置</button>
  <p v-if="footballDisplayMessage" role="status">{{footballDisplayMessage}}</p>
</section>
<section class="welcome-card detail">
  <h2>交易限额</h2>
  <p>单场和组合共用每个钱包的买入额度。单笔上限按可用余额比例计算，最低按所填金额计算，最高为可用余额。</p>
  <form v-if="limitsReady" @submit.prevent="saveLimits">
    <label for="trade-limit-percent">单笔余额比例（%，留空关闭）</label>
    <input id="trade-limit-percent" v-model="limits.percent" inputmode="decimal" placeholder="6">
    <label for="trade-limit-floor">单笔上限最低值（pUSD）</label>
    <input id="trade-limit-floor" v-model="limits.floor" inputmode="decimal" required>
    <label for="trade-limit-daily">每钱包每日上限（pUSD，留空关闭）</label>
    <input id="trade-limit-daily" v-model="limits.daily" inputmode="decimal">
    <button class="text-button" :disabled="limitsBusy">保存交易限额</button>
  </form>
  <button v-else type="button" class="text-button" :disabled="limitsBusy" @click="loadLimits">读取交易限额</button>
  <p v-if="limitsMessage" role="status">{{limitsMessage}}</p>
</section>
<section class="welcome-card"><h2>每日同步计划</h2><p>足球和 NBA 赛程使用 Polymarket；每一行表示一个同步时间，默认每天两次。每种运动至少保留一个启用时间，修改仅影响未来尚未领取的任务。</p><div v-for="(item,index) in items" :key="index" class="schedule-row"><span>{{item.sport==='football'?'足球':'NBA'}}</span><input v-model="item.utc_time" type="time" :aria-label="`${item.sport} UTC 同步时间`" required><span>UTC · {{utc8(item.utc_time)}} UTC+8</span><button class="text-button" :aria-label="`删除${item.sport}时间`" @click="items.splice(index,1)">删除</button></div><div class="game-tools"><button class="text-button" @click="add('football')">添加足球时间</button><button class="text-button" @click="add('nba')">添加 NBA 时间</button><button class="text-button" :disabled="busy" @click="save">保存计划</button></div></section>
<section class="welcome-card detail"><h2>后台状态</h2><p v-if="!workers.length">未发现 worker 心跳；请启动独立 worker。</p><p v-for="worker in workers" :key="worker.id">{{worker.state==='running'?'运行中':worker.state==='stopping'?'正在停止':'心跳已过期'}} · 最近心跳 {{format(worker.last_seen_at)}} UTC+8</p><div class="game-tools"><button class="text-button" @click="enqueue('football')">手动同步足球</button><button class="text-button" @click="enqueue('nba')">手动同步 NBA</button><button class="text-button" @click="load">刷新状态</button></div></section>
<section v-if="system?.configuration" class="welcome-card detail"><h2>服务配置状态</h2><p>足球赛程来源：Polymarket（无需密钥） · API-Football 赛果绑定密钥：{{system.configuration.football_key_configured?'已配置':'未配置'}} · NBA 赛程来源：Polymarket · BallDontLie 赛果绑定密钥：{{system.configuration.nba_key_configured?'已配置':'未配置'}}</p><p>钱包数量：{{system.configuration.wallet_count}} · 组合交易：{{system.configuration.combo_enabled?'已启用':'未启用'}}</p><p>数据库：{{system.database.status}} · 检查时间 {{format(system.database.checked_at)}} UTC+8</p><p v-for="source in system.sources" :key="source.sport">{{source.sport==='football'?'足球':'NBA'}}最近同步：{{source.status}} · 下次计划：{{source.next_due_at ? format(source.next_due_at)+' UTC+8' : '未知'}}</p></section><section class="welcome-card detail"><h2>同步日志</h2><p class="muted">仅显示最近 24 小时。</p><p v-if="!syncRuns.length">最近 24 小时暂无同步记录。</p><article v-for="run in syncRuns" :key="run.id" class="market-card"><p>{{run.sport==='football'?'足球':'NBA'}} · {{run.provider}} · {{run.trigger_type || '未知'}} · {{run.status}}</p><p>计划：{{format(run.scheduled_for || run.started_at)}} UTC+8 · 完成：{{run.finished_at ? format(run.finished_at)+' UTC+8' : '尚未完成'}}</p><p v-if="run.window_start && run.window_end">时间窗：{{format(run.window_start)}} 至 {{format(run.window_end)}} UTC+8</p><p>页数 {{run.pages_fetched}} · 收到 {{run.items_received}} · 更新 {{run.items_upserted}} <span v-if="run.error_code">· {{run.error_code}}</span></p></article></section><section class="welcome-card detail"><h2>任务状态</h2><p class="muted">显示过去 20 小时至未来 24 小时的任务，并列出对应比赛或订单。</p><p v-if="!jobs.length">该时间范围内暂无任务。</p><div v-for="job in jobs" :key="job.id" class="schedule-row"><span>{{jobKind(job.kind)}}</span><span>{{jobStatus(job.status)}}</span><span>{{format(job.due_at)}} UTC+8</span><span :title="'累计领取次数，包含成功后的周期重排，不等于失败次数'">累计领取 {{job.attempt_count}} 次</span><span v-if="job.failure_count" :title="job.last_error_summary || ''">连续异常/未决 {{job.failure_count}} 次</span><span v-if="job.last_error_code">{{job.last_error_code==='retry_exhausted'||job.last_error_code==='reconcile_stale'?'已停止自动重试':job.last_error_code}}</span><button class="text-button" @click="showRuns(job)">查看记录</button><span v-if="job.target?.label" class="job-target">目标：{{job.target.label}}<template v-if="job.target.starts_at"> · {{format(job.target.starts_at)}} UTC+8</template><template v-if="job.target.game_status"> · {{job.target.game_status}}</template><template v-if="job.target.state"> · {{job.target.state}}</template></span></div><div v-if="selected"><h3>任务尝试记录</h3><p v-for="attempt in attempts" :key="attempt.attempt_no">第 {{attempt.attempt_no}} 次 · {{attempt.result}} · {{format(attempt.started_at)}}<span v-if="attempt.error_code"> · {{attempt.error_code}}</span></p></div></section><section class="welcome-card detail"><h2>审计记录</h2><p class="muted">仅显示最近 24 小时。</p><p v-if="!audit.length">最近 24 小时暂无审计记录。</p><p v-for="event in audit" :key="event.id">{{format(event.created_at)}} UTC+8 · {{event.action}} · {{event.entity_type || '未知'}}</p></section></main></div>
</template>

<style scoped>
.football-competitions { min-width: 0; margin: 12px 0; padding: 12px; border: 1px solid #dce8e0; border-radius: 8px; }
.football-selection-actions { display: flex; gap: 8px; margin-bottom: 6px; }
.football-option-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 6px 12px; }
.football-option-grid label { display: flex; align-items: center; gap: 5px; margin: 0; }
.football-option-grid input { width: auto; margin: 0; }
</style>
