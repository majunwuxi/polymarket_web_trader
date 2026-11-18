<script setup lang="ts">
import {ref,onActivated,onMounted,onUnmounted,watch} from 'vue'
import {session,logout} from '../auth'
import {useRoute,useRouter} from 'vue-router'
import {formatDateTimeSeconds} from '../utils/format'
const router=useRouter(),route=useRoute(),legs=ref<any[]>([]),wallets=ref<any[]>([]),wallet=ref(''),direction=ref('BUY'),quantity=ref(''),items=ref<any[]>([]),message=ref(''),busy=ref(false),menuOpen=ref(false),closeOriginal=ref<any>(null),closeInfo=ref<any>(null)
const stateName=(state:string)=>({requesting:'询价中',quoted:'已报价',accepting:'提交中',executing:'执行中，未确认成交',confirmed:'已确认',expired:'已过期',rejected:'已拒绝',failed:'未成交',unknown:'结果未知'} as Record<string,string>)[state]||'未知'
const nextCursor=ref<string|null>(null)
const recordsMessage=ref('')
const key='polysports_combo_legs',pendingKey='polysports_combo_pending';const pending=ref<any>(null),ack=ref(false)
async function api(path:string,options:RequestInit={}){const r=await fetch('/api/v1'+path,{credentials:'same-origin',cache:'no-store',...options});const body=await r.json();if(!r.ok){const e=new Error(body.error?.message||'请求失败');(e as any).code=body.error?.code;throw e}return body.data}
async function load(more=false){try{const [w,r]=await Promise.all([api('/wallets'),api('/combo-rfqs'+(more&&nextCursor.value?'?cursor='+encodeURIComponent(nextCursor.value):''))]);wallets.value=w.items;items.value=more?[...items.value,...r.items]:r.items;nextCursor.value=r.next_cursor||null;if(!wallet.value&&wallets.value.length)wallet.value=wallets.value[0].id}catch{recordsMessage.value='读取钱包或组合记录失败。'}}
async function reconcileCombo(id:string){try{await api('/combo-rfqs/'+id+'/reconcile',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:'{}'});recordsMessage.value='已重新安排对账，请稍后刷新。';await load()}catch(e){recordsMessage.value=e instanceof Error?e.message:'重新安排对账失败。'}}
function remove(i:number){legs.value.splice(i,1);localStorage.setItem(key,JSON.stringify(legs.value))}
function clearLegs(notify=true){legs.value=[];localStorage.removeItem(key);if(notify)message.value='已清空全部待下单组合腿。'}
// 这些错误在发送下单请求之前就已确定，可以安全地清除本机待查记录并允许重试。
const safeCodes=['COMBO_DISABLED','COMBO_CLOSE_INVALID','MAPPING_INVALID','INVALID_AMOUNT','COMBO_LEGS_INVALID','COMBO_LEGS_DUPLICATE','COMBO_MARKET_UNAVAILABLE','COMBO_CATALOG_UNAVAILABLE','COMBO_CATALOG_INCOMPLETE','COMBO_MARKET_MISMATCH','COMBO_OUTCOME_UNSUPPORTED','COMBO_OUTCOME_MISMATCH','COMBO_POSITION_INVALID','CREDENTIALS_MISSING','BUILDER_CREDENTIALS_MISSING','COMBO_WALLET_UNSUPPORTED','WALLET_NOT_FOUND','WALLET_MISMATCH','SIGNER_MISMATCH','GEOBLOCK_BLOCKED','GEOBLOCK_UNKNOWN','WALLET_UNDEPLOYED','WALLET_DEPLOYMENT_UNKNOWN','BALANCE_INSUFFICIENT','ALLOWANCE_INSUFFICIENT','COMBO_FUNDING_UNKNOWN','QUOTE_UNAVAILABLE','COMBO_QUOTE_EXPIRED','COMBO_QUOTE_MISMATCH']
async function place(){
  if(busy.value)return
  busy.value=true;message.value=''
  if(localStorage.getItem(pendingKey)){message.value='上次下单结果未确认，请先查询记录，勿重复下单。';busy.value=false;return}
  // 官方组合报价的有效期只有数秒（由上游报价的 expires_at 决定），而一步下单
  // 在服务端做完资金与目录校验后才发出接受请求。这些错误在发出接受之前即已确定、
  // 从未真正下单，因此可以安全地重新询价一次；其余错误绝不自动重试。
  const quoteExpiryCodes=['COMBO_QUOTE_EXPIRED','QUOTE_UNAVAILABLE']
  const post=(id:string)=>api('/combo-rfqs',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf,'Idempotency-Key':id},body:JSON.stringify({wallet_id:wallet.value,direction:direction.value,quantity:quantity.value,auto_accept:true,legs:legs.value.map(x=>({market_link_id:x.market_link_id,outcome_id:x.outcome_id})),...(closeOriginal.value?{parent_combo_id:closeOriginal.value.id}:{})})})
  let retried=false
  try{
    for(;;){
      const id=crypto.randomUUID()
      try{localStorage.setItem(pendingKey,JSON.stringify({key:id,wallet_id:wallet.value}));pending.value={key:id,wallet_id:wallet.value}}
      catch{message.value='无法保存请求标识，未发送下单请求。';break}
      try{
        const data=await post(id)
        if(!['requesting','unknown'].includes(data.state)){localStorage.removeItem(pendingKey);pending.value=null}
        const expired=data.state==='expired'||data.error_code==='quote_expired'
        if(expired&&!retried){retried=true;items.value=[data,...items.value.filter((x:any)=>x.id!==data.id)];continue}
        items.value=[data,...items.value.filter((x:any)=>x.id!==data.id)]
        const submitted=['accepting','executing','confirmed'].includes(data.state)
        if(submitted&&!closeOriginal.value)clearLegs(false)
        message.value=(['requesting','unknown'].includes(data.state)?'下单结果未确认，请查询原记录，不要重复提交。':submitted?'已按当前市场价提交，执行中不代表成交。':data.error_code==='no_quote'?'本次没有获得市场报价，未成交。':data.error_text?'下单未成交：'+data.error_text:data.state==='rejected'?'上游拒绝本次下单，未能成交。':'组合下单未成交，请查看记录。')+(submitted&&!closeOriginal.value?' 已清空待下单组合腿。':'')+(retried?'（此前报价已过期，已自动重新询价一次）':'')
        break
      }catch(e){
        const known=safeCodes.includes((e as any)?.code)
        if(known){localStorage.removeItem(pendingKey);pending.value=null}
        if(known&&quoteExpiryCodes.includes((e as any)?.code)&&!retried){retried=true;continue}
        await load()
        message.value=known?(e instanceof Error?e.message:'下单前检查未通过。')+'（本次未发送下单请求）':'下单结果未核实，请查询记录，不要重复提交。'
        break
      }
    }
  }finally{busy.value=false}
}
async function lookup(){if(!pending.value)return;try{const data=await api('/combo-rfqs/by-key/'+pending.value.key+'?wallet_id='+encodeURIComponent(pending.value.wallet_id));if(!['requesting','unknown'].includes(data.state)){localStorage.removeItem(pendingKey);pending.value=null}await load();message.value='已找到记录，状态：'+stateName(data.state)}catch{message.value='未能找到记录，请独立核对，不要重新下单。'}}
function clearPending(){if(!ack.value)return;localStorage.removeItem(pendingKey);pending.value=null;ack.value=false}
let closeRevision=0
async function initialize(){
  const revision=++closeRevision,closeId=route.query.close
  closeOriginal.value=null;closeInfo.value=null
  await load()
  if(revision!==closeRevision)return
  if(typeof closeId!=='string'){
    direction.value='BUY';quantity.value=''
    try{const saved=JSON.parse(localStorage.getItem(key)||'[]');legs.value=Array.isArray(saved)?saved:[]}catch{legs.value=[]}
    return
  }
  try{
    const [original,position]=await Promise.all([api('/combo-rfqs/'+encodeURIComponent(closeId)),api('/combo-rfqs/'+encodeURIComponent(closeId)+'/close-context')])
    if(revision!==closeRevision)return
    if(original.state!=='confirmed'||original.direction!=='BUY'||!original.quote?.net_receive||!Array.isArray(original.legs)||original.legs.length<2||!wallets.value.some((item:any)=>item.id===original.wallet_id))throw new Error('原组合成交或钱包无法核实')
    if(position.wallet_id!==original.wallet_id || (original.combo_position_id && position.position_id!==original.combo_position_id))throw new Error('原组合钱包或持仓身份不一致')
    closeOriginal.value=original;closeInfo.value=position;wallet.value=original.wallet_id;direction.value='SELL'
    quantity.value=String(position.candidate_shares).replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'')
    legs.value=original.legs.map((leg:any)=>({market_link_id:leg.market_link_id,outcome_id:leg.outcome_id,label:leg.home+' 对 '+leg.away+' · '+leg.market_question+' · '+leg.outcome_label}))
    message.value='组合平仓：请核对原组合及卖出份额，再按当前市价直接平仓。'
  }catch(e){if(revision===closeRevision){legs.value=[];message.value=e instanceof Error?e.message:'无法读取原组合，不能平仓。'}}
}
onMounted(()=>{try{pending.value=JSON.parse(localStorage.getItem(pendingKey)||'null')}catch{pending.value=null}void initialize()})
watch(()=>route.query.close,()=>{void initialize()})
let activated=false
onActivated(()=>{if(!activated){activated=true;return}try{pending.value=JSON.parse(localStorage.getItem(pendingKey)||'null')}catch{pending.value=null}void initialize()})
onUnmounted(()=>{closeRevision++})
</script>
<template><div class="app-layout"><header class="mobile-header"><div class="brand">PolySports</div><button class="menu-button" :aria-expanded="menuOpen" aria-controls="primary-nav" @click="menuOpen=!menuOpen">菜单</button></header><aside id="primary-nav" class="sidebar" :class="{open:menuOpen}"><div class="brand sidebar-brand">PolySports</div><nav><RouterLink to="/">总览</RouterLink><RouterLink to="/sports/football">足球赛事</RouterLink><RouterLink to="/sports/nba">NBA 赛事</RouterLink><RouterLink to="/combos">组合交易</RouterLink><RouterLink to="/orders">交易记录</RouterLink><RouterLink to="/history">历史与导出</RouterLink><RouterLink to="/wallets">钱包状态</RouterLink><RouterLink to="/system">系统管理</RouterLink></nav></aside><main class="workspace combo-page"><div class="workspace-top"><span>工作台 / 组合交易</span><button class="text-button" @click="logout().then(()=>router.replace('/login'))">退出</button></div><h1>{{closeOriginal ? '组合平仓' : '组合交易'}}</h1><p>从赛事市场中选择至少两条腿。按当前市场报价直接下单，可能使用真实资金；执行中不代表成交。</p><section class="welcome-card"><p v-if="!legs.length">请先在赛事中心选择已确认的市场选项。</p><p v-for="(leg,index) in legs" :key="leg.outcome_id">{{leg.label}} <button v-if="!closeOriginal" class="text-button" @click="remove(index)">移除</button></p><div v-if="legs.length && !closeOriginal" class="leg-tools"><button class="text-button" @click="clearLegs()">清空全部</button></div><label>钱包 <select v-model="wallet" :disabled="!!closeOriginal"><option v-for="w in wallets" :key="w.id" :value="w.id">{{w.label}} · {{w.wallet_address}}</option></select></label><label>方向 <select v-model="direction" :disabled="!!closeOriginal"><option value="BUY">买入</option><option value="SELL">卖出</option></select></label><label>{{direction==='BUY'?'最高预算（含费用）':'出售份额'}} <input v-model="quantity" inputmode="decimal"></label><div v-if="closeInfo"><p>原组合候选可卖份额：{{closeInfo.candidate_shares}} · 钱包当前份额：{{closeInfo.wallet_shares ?? '未知（报价后核验）'}} · 已平仓：{{closeInfo.previously_closed_shares}} · 授权：{{closeInfo.approved === true ? '已授权' : closeInfo.approved === false ? '未授权' : '待核验'}}</p><p>{{closeInfo.notice}}</p><p>卖出按当前市场报价一次成交；提交前会核验钱包余额和授权。</p></div><button class="text-button" :disabled="busy||legs.length<2||!wallet||!quantity || (!!route.query.close && (!closeOriginal || closeInfo?.approved===false || Number(closeInfo?.candidate_shares)<=0))" @click="place">{{closeOriginal ? '按当前市价直接平仓' : '按当前市价直接下单'}}</button><p v-if="busy" role="status" class="combo-submit-status">正在按当前市价提交，请勿重复点击…</p><p v-else-if="message" role="status" class="combo-submit-status">{{message}}</p><div v-if="pending" class="combo-pending-block" role="alert"><p>上次下单结果未确认，本次未发送新请求。请先用原请求标识查询。</p><button class="text-button" @click="lookup">查询原请求</button><label><input type="checkbox" v-model="ack">我已独立核实外部状态，理解重复下单风险</label><button class="text-button" :disabled="!ack" @click="clearPending">清除本机待查记录</button></div></section><section class="welcome-card"><h2>组合记录</h2><button class="text-button" @click="load()">刷新状态</button><p v-if="recordsMessage" role="status" class="form-message">{{recordsMessage}}</p><p v-if="!items.length">暂无组合记录。</p><article v-for="row in items" :key="row.id" class="market-card"><p>状态：{{stateName(row.state)}} · 方向：{{row.direction}} · 数量：{{row.quantity}} · 钱包：{{row.wallet_address}}</p><p v-for="leg in row.legs" :key="leg.outcome_id">{{leg.home}} 对 {{leg.away}} · {{leg.market_question}} · {{leg.outcome_label}} · {{formatDateTimeSeconds(leg.starts_at)}} UTC+8</p><p>RFQ：{{row.external_rfq_id || '未知'}} · {{row.error_text || row.error_code || ''}}</p><p v-if="row.quote">组合报价：{{row.quote.price}} · 实际需求：{{row.quote.total_required}} · {{row.direction==='BUY'?'预计组合份额':'扣费后收入'}}：{{row.quote.net_receive ?? '未知'}}</p><p v-if="row.state==='quoted'">旧流程的待确认报价，不再提供接受入口。</p><p v-if="row.state==='unknown'">结果未知；请勿重复操作。</p><button v-if="row.state==='executing'||row.state==='unknown'" class="text-button" @click="reconcileCombo(row.id)">重新核对外部状态</button></article><button v-if="nextCursor" class="text-button" @click="load(true)">加载更多</button></section></main></div></template>
