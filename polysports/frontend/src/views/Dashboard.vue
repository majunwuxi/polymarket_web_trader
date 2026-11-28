<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError, clearSession, logout, session } from '../auth'
import { api } from '../api'
import AppSidebar from '../components/AppSidebar.vue'
import AppTopbar from '../components/AppTopbar.vue'
import { formatDateTime } from '../utils/format'

const router = useRouter()
const menuOpen = ref(false)
const errorMessage = ref('')
const loading = ref(true)
const sports = ['football', 'nba'] as const
const sportName = { football: '足球', nba: 'NBA' }
type DashboardLink = { label:string; to:string; time?:string; category?:string }
type DashboardGroup = { title:string; links:DashboardLink[]; count:string; failed:boolean }
const groups = ref<DashboardGroup[]>([])
const shortcuts = [
  { label:'足球赛事', to:'/sports/football' },
  { label:'NBA赛事', to:'/sports/nba' },
  { label:'交易记录', to:'/orders' },
]
const emptyMessages = ['暂无即将开赛的关注赛事。','暂无待确认的赛事映射。','暂无提交结果待核实的订单。']
const teamName = (team:any) => team?.name_zh || team?.source_name || '未知球队'

async function read(path:string) {
  const data=await api<any>(path)
  if(!Array.isArray(data?.items))throw new Error('数据格式无效')
  return data
}

async function load() {
  loading.value=true
  const now=Date.now()
  const definitions=[
    {title:'即将开赛的关注赛事', paths:sports.map(sport=>'/games?watched=true&limit=4&sport='+sport+'&status=scheduled'),
      rows:(data:any,i:number)=>data.items.filter((game:any)=>Date.parse(game.starts_at)>=now).map((game:any)=>({
        label:teamName(game.home_team)+' 对 '+teamName(game.away_team),
        time:game.starts_at,
        category:sportName[sports[i]!],
        to:'/sports/'+sports[i]+'?watched=1&game='+game.id,
      })),
      count:(data:any)=>data.items.filter((game:any)=>Date.parse(game.starts_at)>=now).length, more:(data:any)=>!!data.next_cursor},
    {title:'待确认的赛事映射', paths:['/result-bindings/summary?sport=football&limit=4'],
      rows:(data:any)=>data.items.map((game:any)=>({label:game.home+' 对 '+game.away,time:game.starts_at,to:'/sports/football#binding-summary'})),
      count:(data:any)=>data.totals.needs_review, more:()=>false},
    {title:'结果未知的交易', paths:['/trade-intents?state=unknown&limit=4&hide_settled=true','/combo-rfqs?state=unknown&limit=4&hide_settled=true'],
      rows:(data:any,i:number)=>data.items.map((order:any)=>({
        label:(i?'组合订单':'单场订单')+' · '+String(order.id).slice(0,8),
        time:order.created_at,
        to:'/orders?state=unknown#trade-'+order.id,
      })),
      count:(data:any)=>data.items.length, more:(data:any)=>!!data.next_cursor},
  ]
  groups.value=await Promise.all(definitions.map(async group=>{
    const responses=await Promise.allSettled(group.paths.map(read))
    const links:DashboardLink[]=[]
    let count=0
    let more=false
    for(const [index,response] of responses.entries()){
      if(response.status!=='fulfilled')continue
      links.push(...group.rows(response.value,index))
      count+=group.count(response.value)
      more ||= group.more(response.value)
    }
    const failed=responses.some(response=>response.status==='rejected')
    if(group===definitions[0])links.sort((a,b)=>Date.parse(a.time ?? '')-Date.parse(b.time ?? ''))
    return {title:group.title,links,count:failed?'':String(count)+(more?'+':''),failed}
  }))
  loading.value=false
}

async function signOut() {
  try { await logout(); await router.replace('/login') }
  catch (error) { if (error instanceof ApiError && error.status === 401) { clearSession(); await router.replace('/login') } else { errorMessage.value = '退出请求未确认，请重试。' } }
}
onMounted(load)
</script>

<template>
  <div class="app-layout dashboard-layout">
    <AppSidebar :open="menuOpen" @toggle="menuOpen=!menuOpen" />
    <main class="workspace dashboard">
      <AppTopbar breadcrumb="工作台 / 总览" :username="session.username" @sign-out="signOut" />
      <p v-if="errorMessage" class="form-message" role="alert">{{errorMessage}}</p>
      <header class="dashboard-header">
        <h1>赛事工作台</h1>
        <button type="button" class="dashboard-refresh" :disabled="loading" @click="load">
          <span aria-hidden="true">↻</span> {{loading ? '刷新中…' : '刷新'}}
        </button>
      </header>
      <nav class="dashboard-shortcuts" aria-label="赛事与交易入口">
        <RouterLink v-for="shortcut in shortcuts" :key="shortcut.to" :to="shortcut.to">
          {{shortcut.label}} <span aria-hidden="true">↗</span>
        </RouterLink>
      </nav>
      <p v-if="loading && !groups.length" class="dashboard-loading" role="status">正在读取赛事和待办…</p>
      <div v-if="groups.length" class="overview-grid" :aria-busy="loading">
        <section v-for="(group,index) in groups" :key="group.title" class="overview-card" :class="{'overview-schedule':index===0}">
          <header class="overview-card-header">
            <div>
              <h2>{{group.title}}</h2>
              <span v-if="index===0" class="schedule-zone">开赛时间 · UTC+8</span>
            </div>
            <span v-if="group.count" class="overview-count" :class="{'count-active':group.count!=='0'}">{{group.count}}</span>
          </header>
          <ul v-if="group.links.length" class="overview-rows">
            <li v-for="link in group.links" :key="link.to + link.label">
              <RouterLink :to="link.to" class="overview-row" :class="{'schedule-row-link':index===0}">
                <time v-if="index===0 && link.time" class="schedule-time" :datetime="link.time">
                  <strong>{{formatDateTime(link.time).slice(-5)}}</strong>
                  <small>{{formatDateTime(link.time).slice(5,10)}}</small>
                </time>
                <span class="overview-row-content">
                  <strong>{{link.label}}</strong>
                  <small v-if="index!==0 && link.time">{{formatDateTime(link.time)}} UTC+8</small>
                  <small v-if="link.category">{{link.category}}</small>
                </span>
                <span class="overview-arrow" aria-hidden="true">›</span>
              </RouterLink>
            </li>
          </ul>
          <p v-if="group.failed" class="overview-status overview-error" role="status">部分数据暂不可用，请刷新重试。</p>
          <p v-else-if="!group.links.length" class="overview-status">{{emptyMessages[index]}}</p>
        </section>
      </div>
    </main>
  </div>
</template>

<style scoped>
.dashboard {
  min-width: 0;
  --overview-ink: #19342c;
  --overview-muted: #536d62;
  --overview-line: #dce8e0;
}
.dashboard-layout :deep(.sidebar nav a) { color: #35594c; }
.dashboard-layout :deep(.sidebar nav a.router-link-exact-active) { background: #e4f2eb; color: #176348; font-weight: 700; }
.dashboard-header {
  margin: 24px 0 12px;
  padding: clamp(17px, 4vw, 32px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-radius: 18px;
  background: linear-gradient(115deg, #143b31, #235445);
  color: #fff;
}
.dashboard-header h1 { margin: 0; font-size: clamp(24px, 3.5vw, 38px); letter-spacing: -.035em; }
.dashboard-refresh {
  flex: none;
  padding: 8px 12px;
  color: #fff;
  background: #ffffff12;
  border: 1px solid #ffffff4d;
  border-radius: 8px;
  font-weight: 650;
}
.dashboard-refresh:hover:not(:disabled) { background: #ffffff26; }
.dashboard-refresh:disabled { opacity: .7; cursor: default; }
.dashboard-shortcuts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 18px; }
.dashboard-shortcuts a {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 5px;
  padding: 10px 13px;
  border: 1px solid var(--overview-line);
  border-radius: 9px;
  background: #fff;
  color: var(--overview-ink);
  font-size: 14px;
  font-weight: 650;
  text-decoration: none;
}
.dashboard-shortcuts a:hover { border-color: #82aa98; background: #f0f8f3; }
.dashboard-shortcuts span, .overview-arrow { color: #33725a; }
.dashboard-loading { color: var(--overview-muted); }
.overview-grid { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(0, 1fr); align-items: start; gap: 14px; }
.overview-card { min-width: 0; border: 1px solid var(--overview-line); border-radius: 14px; background: #fff; overflow: hidden; }
.overview-schedule { grid-row: span 2; }
.overview-card-header { display: flex; align-items: start; justify-content: space-between; gap: 10px; padding: 15px 16px; }
.overview-card-header h2 { margin: 0; color: var(--overview-ink); font-size: 17px; line-height: 1.4; }
.schedule-zone { display: block; margin-top: 5px; color: var(--overview-muted); font-size: 12px; }
.overview-count { flex: none; min-width: 30px; border-radius: 20px; padding: 4px 8px; background: #edf3ef; color: #4b6659; text-align: center; font-size: 13px; font-weight: 700; }
.overview-count.count-active { background: #dff0e6; color: #156345; }
.overview-rows { list-style: none; margin: 0; padding: 0; }
.overview-rows li { border-top: 1px solid #e8efea; }
.overview-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 12px; min-height: 52px; padding: 10px 16px; color: var(--overview-ink); text-decoration: none; }
.overview-row:hover { background: #f1f8f4; }
.overview-row:focus-visible { outline-offset: -3px; }
.schedule-row-link { grid-template-columns: 57px minmax(0, 1fr) auto; min-height: 61px; }
.schedule-time { font-variant-numeric: tabular-nums; }
.schedule-time strong { display: block; color: #22684e; font-size: 16px; }
.overview-row-content { overflow-wrap: anywhere; }
.overview-row-content strong { display: block; font-size: 14px; line-height: 1.45; }
.overview-row small { display: block; margin-top: 3px; color: var(--overview-muted); font-size: 12px; }
.overview-arrow { font-size: 21px; line-height: 1; }
.overview-status { margin: 0 16px 16px; padding: 9px 11px; border-radius: 7px; background: #f3f7f4; color: var(--overview-muted); font-size: 13px; }
.overview-rows + .overview-status { margin-top: 10px; }
.overview-error { background: #fff2e4; color: #844e24; }
@media (max-width: 920px) {
  .overview-grid { grid-template-columns: minmax(0, 1fr); }
  .overview-schedule { grid-row: auto; }
}
@media (max-width: 380px) {
  .dashboard-shortcuts a { padding-inline: 7px; font-size: 13px; }
}
</style>
