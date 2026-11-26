<script setup lang="ts">
import {onActivated,onMounted,ref} from 'vue'
import {useRouter} from 'vue-router'
import {session,logout,clearSession,ApiError} from '../auth'
import AppSidebar from '../components/AppSidebar.vue'
import AppTopbar from '../components/AppTopbar.vue'

const router=useRouter()
const menuOpen=ref(false)
const month=ref(currentMonth())
const days=ref<any[]>([])
const summary=ref<any>(null)
const scope=ref('')
const selected=ref<string|null>(null)
const message=ref('')
const loading=ref(false)
const loaded=ref(false)

function currentMonth(){const now=new Date(Date.now()+8*3600*1000);return now.getUTCFullYear()+'-'+String(now.getUTCMonth()+1).padStart(2,'0')}

async function api(path:string){const response=await fetch('/api/v1'+path,{credentials:'same-origin',cache:'no-store'});const body=await response.json();if(!response.ok)throw new Error(body?.error?.message||'请求失败');return body.data}

function shift(delta:number){
  const [year,mon]=month.value.split('-').map(Number)
  const next=new Date(Date.UTC(year,mon-1+delta,1))
  month.value=next.getUTCFullYear()+'-'+String(next.getUTCMonth()+1).padStart(2,'0')
  void load()
}

// 月份标题直接由 YYYY-MM 推出，避免浏览器时区影响。
const monthLabel=()=>{const [year,mon]=month.value.split('-');return year+'年'+Number(mon)+'月'}

// 每月 1 号是周几，用于在月首补齐空格；以日历日（date 字符串）为准，不做时区换算。
function leadingBlanks(){
  if(!days.value.length)return 0
  const first=days.value[0].day
  const [year,mon,day]=first.split('-').map(Number)
  return new Date(Date.UTC(year,mon-1,day)).getUTCDay()
}

const weekdayNames=['日','一','二','三','四','五','六']

const dayNumber=(value:string)=>Number(value.split('-')[2])

const toneClass=(entry:any)=>{
  if(entry.empty)return 'calendar-cell calendar-empty'
  if(entry.tone==='profit')return 'calendar-cell calendar-profit'
  if(entry.tone==='loss')return 'calendar-cell calendar-loss'
  if(entry.tone==='flat')return 'calendar-cell calendar-flat'
  if(entry.tone==='unpriced')return 'calendar-cell calendar-unpriced'
  return 'calendar-cell calendar-pending'
}

const signed=(value:string)=>value.startsWith('-')?'':(Number(value)>0?'+':'')
const amount=(value:string)=>signed(value)+value

const money=(value:string)=>value+' pUSD'

// 已结算与盈亏待核实是两件事：前者计入盈亏，后者只说明比赛已结算但金额无法核实。
const settledText=(entry:any)=>{
  if(entry.empty)return ''
  const parts=[]
  if(entry.settled_count)parts.push('已结算 '+entry.settled_count+' 笔')
  if(entry.unpriced_count)parts.push('盈亏待核实 '+entry.unpriced_count+' 笔')
  if(entry.pending_count)parts.push('待结算 '+entry.pending_count+' 笔')
  return parts.join(' · ')
}

const selectedDay=()=>days.value.find(x=>x.day===selected.value)||null

const toggle=(entry:any)=>{
  if(entry.empty)return
  selected.value=selected.value===entry.day?null:entry.day
}

async function load(){
  loading.value=true
  message.value=''
  try{
    const data=await api('/pnl-calendar?month='+encodeURIComponent(month.value))
    days.value=data.days
    summary.value=data.summary
    scope.value=data.scope||''
    selected.value=null
    loaded.value=true
  }catch(e){
    message.value=e instanceof Error?e.message:'盈亏日历加载失败。'
    days.value=[]
  }finally{loading.value=false}
}

async function signOut(){
  try{await logout();await router.replace('/login')}
  catch(e){if(e instanceof ApiError&&e.status===401){clearSession();await router.replace('/login')}else message.value='退出请求未确认，请重试。'}
}

onMounted(load)
let activated=false
onActivated(()=>{if(!activated){activated=true;return}void load()})
</script>

<template>
  <div class="app-layout">
    <AppSidebar :open="menuOpen" @toggle="menuOpen=!menuOpen" />
    <main class="workspace combo-page">
      <AppTopbar breadcrumb="工作台 / 每日盈亏" :username="session.username" @sign-out="signOut" />
      <div class="page-heading">
        <div>
          <p class="eyebrow">DAILY P&L</p>
          <h1>每日盈亏日历</h1>
          <p class="muted">归日按下单时间，北京时间 12:00 至次日 11:59 记为同一天。投入含未结算；盈亏只计入已结算且金额可严格核实的投注，已结算但盘口口径无法按比分推断的标为「盈亏待核实」，不计入合计。金额单位 pUSD。</p>
        </div>
      </div>
      <p role="status" class="form-message">{{message}}</p>

      <section class="welcome-card calendar-card">
        <header class="calendar-header">
          <button class="text-button" :disabled="loading" @click="shift(-1)">上个月</button>
          <h2>{{monthLabel()}}</h2>
          <button class="text-button" :disabled="loading" @click="shift(1)">下个月</button>
        </header>
        <p v-if="summary" class="calendar-summary">
          本月投入 {{money(summary.invested)}} · 返奖 {{money(summary.returned)}} · 已结算盈亏 {{amount(summary.pnl)}} pUSD
          <span v-if="summary.unpriced_count"> · 盈亏待核实 {{summary.unpriced_count}} 笔</span>
          <span v-if="summary.pending_count"> · 待结算 {{summary.pending_count}} 笔</span>
        </p>
        <p v-if="scope" class="scope-note">{{scope}}</p>
        <p v-if="loading && !days.length">正在加载盈亏日历…</p>

        <div class="calendar-grid" role="grid" aria-label="每日盈亏">
          <span v-for="name in weekdayNames" :key="name" class="calendar-weekday" role="columnheader">{{name}}</span>
          <span v-for="n in leadingBlanks()" :key="'blank-'+n" class="calendar-cell calendar-empty" aria-hidden="true"></span>
          <button
            v-for="entry in days"
            :key="entry.day"
            type="button"
            role="gridcell"
            :class="[toneClass(entry),{selected:selected===entry.day}]"
            :disabled="entry.empty"
            :aria-pressed="selected===entry.day"
            @click="toggle(entry)"
          >
            <b class="calendar-day">{{dayNumber(entry.day)}}</b>
            <template v-if="!entry.empty">
              <span class="calendar-line">投入 {{entry.invested}}</span>
              <span class="calendar-line">返奖 {{entry.returned}}</span>
              <span class="calendar-line calendar-pnl">{{signed(entry.pnl)}}{{entry.pnl}}</span>
              <span class="calendar-state">{{settledText(entry)}}{{entry.has_estimate?' · 含估算':''}}</span>
            </template>
          </button>
        </div>
      </section>

      <section v-if="selectedDay()" class="welcome-card calendar-detail" aria-label="当日明细">
        <h2>{{selectedDay()!.day}} 明细</h2>
        <p class="muted">总计：投入 {{money(selectedDay()!.invested)}} · 返奖 {{money(selectedDay()!.returned)}} · 已结算盈亏 {{amount(selectedDay()!.pnl)}} pUSD</p>
        <p v-if="selectedDay()!.unpriced_count" class="check-line tone-info">
          当日有 {{selectedDay()!.unpriced_count}} 笔已结算但盈亏待核实：比赛已结束且赛果已确认，但该盘口口径无法按比分严格推断，金额不计入盈亏合计。
        </p>
        <div class="calendar-sport-rows">
          <article v-for="row in selectedDay()!.by_sport" :key="row.sport" class="market-card calendar-sport-row">
            <header>
              <h3>{{row.name}}</h3>
              <span class="pnl-badge" :class="'pnl-'+row.tone">{{signed(row.pnl)}}{{row.pnl}} pUSD</span>
            </header>
            <p>
              投入 {{money(row.invested)}} · 返奖 {{money(row.returned)}} · 已结算 {{row.settled_count}} 笔
              <span v-if="row.unpriced_count"> · 盈亏待核实 {{row.unpriced_count}} 笔</span>
              <span v-if="row.pending_count"> · 待结算 {{row.pending_count}} 笔</span>
            </p>
            <p v-if="row.has_estimate" class="check-line tone-info">该运动包含成交明细未齐、按限价估算的成本。</p>
          </article>
          <p v-if="!selectedDay()!.by_sport.length">当日没有可归入运动的投注。</p>
        </div>
      </section>
      <p v-else-if="loaded" class="muted">点击有投注的日期查看明细。</p>
    </main>
  </div>
</template>
