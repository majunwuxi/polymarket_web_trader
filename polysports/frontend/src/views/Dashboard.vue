<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError, clearSession, logout, session } from '../auth'
import AppSidebar from '../components/AppSidebar.vue'
import AppTopbar from '../components/AppTopbar.vue'
import { formatDateTime } from '../utils/format'

const router = useRouter()
const menuOpen = ref(false)
const errorMessage = ref('')
const loading = ref(true)
const sports = ['football', 'nba'] as const
const sportName = { football: '足球', nba: 'NBA' }
const groups = ref<{ title:string; links:{ label:string; to:string }[]; count:string; failed:boolean; description?:string }[]>([])
const teamName = (team:any) => team?.name_zh || team?.source_name || '未知球队'

async function read(path:string) {
  const response = await fetch('/api/v1'+path,{credentials:'same-origin',cache:'no-store'})
  if(!response.ok)throw new Error('读取失败')
  const data=(await response.json()).data
  if(!Array.isArray(data?.items))throw new Error('数据格式无效')
  return data
}

async function load() {
  loading.value=true
  const now=Date.now()
  const definitions=[
    {title:'即将开赛的关注赛事', paths:sports.map(sport=>'/games?watched=true&limit=4&sport='+sport+'&status=scheduled'),
      rows:(data:any,i:number)=>data.items.filter((game:any)=>Date.parse(game.starts_at)>=now).map((game:any)=>({
        label:formatDateTime(game.starts_at)+' · '+teamName(game.home_team)+' 对 '+teamName(game.away_team),
        to:'/sports/'+sports[i]+'?watched=1&game='+game.id,
      })),
      count:(data:any)=>data.items.filter((game:any)=>Date.parse(game.starts_at)>=now).length, more:(data:any)=>!!data.next_cursor},
    {title:'待确认的赛事映射', paths:['/result-bindings/summary?sport=football&limit=4'],
      rows:(data:any)=>data.items.map((game:any)=>({label:game.home+' 对 '+game.away,to:'/sports/football#binding-summary'})),
      count:(data:any)=>data.totals.needs_review, more:()=>false},
    {title:'结果未知的交易', description:'提交结果待核实的订单。', paths:['/trade-intents?state=unknown&limit=4&hide_settled=true','/combo-rfqs?state=unknown&limit=4&hide_settled=true'],
      rows:(data:any,i:number)=>data.items.map((order:any)=>({
        label:(i?'组合':'单场')+' · '+formatDateTime(order.created_at)+' · '+String(order.id).slice(0,8),
        to:'/orders?state=unknown#trade-'+order.id,
      })),
      count:(data:any)=>data.items.length, more:(data:any)=>!!data.next_cursor},
  ]
  groups.value=await Promise.all(definitions.map(async group=>{
    const responses=await Promise.allSettled(group.paths.map(read))
    const links:{label:string;to:string}[]=[]
    let count=0
    let more=false
    for(const [index,response] of responses.entries()){
      if(response.status!=='fulfilled')continue
      links.push(...group.rows(response.value,index))
      count+=group.count(response.value)
      more ||= group.more(response.value)
    }
    const failed=responses.some(response=>response.status==='rejected')
    return {title:group.title,description:group.description,links,count:failed?'':String(count)+(more?'+':''),failed}
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
  <div class="app-layout">
    <AppSidebar :open="menuOpen" @toggle="menuOpen=!menuOpen" />
    <main class="workspace">
      <AppTopbar breadcrumb="工作台 / 总览" :username="session.username" @sign-out="signOut" />
      <p v-if="errorMessage" class="form-message" role="alert">{{errorMessage}}</p>
      <div class="page-heading">
        <h1>赛事工作台</h1>
        <button class="text-button" :disabled="loading" @click="load">刷新</button>
      </div>
      <p v-if="loading" role="status">正在读取待办…</p>
      <div class="dashboard-tasks">
        <section v-for="group in groups" :key="group.title" class="welcome-card">
          <h2>{{group.title}} <span v-if="group.count">{{group.count}}</span></h2>
          <p v-if="group.description">{{group.description}}</p>
          <p v-if="group.failed" role="status">部分数据暂不可用，请刷新重试。</p>
          <p v-else-if="!group.links.length">暂无记录。</p>
          <ul v-if="group.links.length">
            <li v-for="link in group.links" :key="link.label"><RouterLink :to="link.to">{{link.label}}</RouterLink></li>
          </ul>
        </section>
      </div>
      <nav class="info-grid" aria-label="赛事与交易入口">
        <RouterLink v-for="sport in sports" :key="sport" class="home-entry" :to="'/sports/'+sport">{{sportName[sport]}}赛事</RouterLink>
        <RouterLink class="home-entry" to="/orders">交易记录</RouterLink>
      </nav>
    </main>
  </div>
</template>
