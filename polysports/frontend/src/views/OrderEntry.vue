<script setup lang="ts">
import {onMounted,ref,watch,computed} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {session,logout,clearSession,ApiError} from '../auth'
import AppSidebar from '../components/AppSidebar.vue'
import AppTopbar from '../components/AppTopbar.vue'
import OrderPendingPanel from '../components/OrderPendingPanel.vue'
import CloseOrderPanel from '../components/CloseOrderPanel.vue'
import OpenOrderForm from '../components/OpenOrderForm.vue'
import OrderResultPanel from '../components/OrderResultPanel.vue'

const route=useRoute(),router=useRouter()
const menuOpen=ref(false),wallets=ref<any[]>([]),wallet=ref('')
const side=ref('BUY'),price=ref(''),size=ref(''),budget=ref(''),inputMode=ref<'amount'|'shares'>('amount')
const book=ref<any>(null),context=ref<any>(null),closeContext=ref<any>(null)
const message=ref(''),busy=ref(false),result=ref<any>(null),pending=ref<any>(null),clearAcknowledged=ref(false)
const closing=computed(()=>typeof route.query.close==='string' && !!route.query.close)
const pendingKey='polysports_pending_order'
let loadRevision=0

// 与组合订单一致：只有能确定发生在发送下单请求之前的错误，才可安全清除本机待查记录并允许重试。
// 其余错误（超时、网络中断、交易所未知响应）一律保留标记，避免重复真实下单。
const SAFE_CODES=[
  'PREVIEW_STALE','MAPPING_INVALID','MARKET_UNAVAILABLE','IDEMPOTENCY_CONFLICT','POSITION_SETTLED',
  'INVALID_AMOUNT','PRICE_TICK_INVALID','SIZE_INVALID','GEOBLOCK_BLOCKED','GEOBLOCK_UNKNOWN',
  'WALLET_NOT_FOUND','WALLET_MISMATCH','SIGNER_MISMATCH','WALLET_UNDEPLOYED','WALLET_DEPLOYMENT_UNKNOWN',
  'WALLET_INVALID','RPC_WRONG_CHAIN','CREDENTIALS_MISSING','BALANCE_INSUFFICIENT','ALLOWANCE_INSUFFICIENT',
  'BALANCE_UNAVAILABLE','BOOK_UNAVAILABLE','BOOK_OR_BALANCE_UNAVAILABLE','BOOK_MISMATCH','ENGINE_RESTARTING',
  'ASSET_VERSION_MISMATCH','POSITION_UNAVAILABLE','POSITION_MISMATCH','SIGNED_ORDER_MISMATCH'
]
const stateName=(state:string)=>({submitting:'提交中',pending:'挂单中',partially_filled:'部分成交',filled:'已成交',rejected:'已拒绝',cancelled:'已取消',unknown:'结果未知'} as Record<string,string>)[state]||state
const reconcilable=computed(()=>!!result.value?.id && result.value.state==='unknown')

function amountToShares(amount:string,limit:string):string{
  const parse=(x:string)=>{
    if(!/^(?:0|[1-9]\d{0,19})(?:\.\d{1,18})?$/.test(x))return null
    const [whole,fraction='']=x.split('.')
    return {n:BigInt(whole+fraction),scale:fraction.length}
  }
  const a=parse(amount),p=parse(limit)
  if(!a||!p||p.n===0n)return ''
  const cents=(a.n*10n**BigInt(p.scale)*100n)/(p.n*10n**BigInt(a.scale))
  if(cents<=0n)return ''
  return (cents/100n).toString()+'.'+(cents%100n).toString().padStart(2,'0')
}
const orderSize=computed(()=>side.value==='BUY'&&inputMode.value==='amount'?amountToShares(budget.value,price.value):size.value)
const orderNotional=computed(()=>{
  if(side.value!=='BUY'||!price.value||!orderSize.value)return ''
  const value=Number(price.value)*Number(orderSize.value)
  return Number.isFinite(value)?value.toFixed(4):''
})
const marketCloseReady=computed(()=>!!closeContext.value&&!closeContext.value.market_close_reason&&!!closeContext.value.market_price&&Number(closeContext.value.candidate_shares)>0)
const closeWalletLabel=computed(()=>wallets.value.find((w:any)=>w.id===closeContext.value?.wallet_id)?.label)

async function api(path:string,options:RequestInit={}){
  const response=await fetch('/api/v1'+path,{credentials:'same-origin',cache:'no-store',...options})
  const body=await response.json()
  if(!response.ok){
    const error=new Error(body?.error?.message||'请求失败') as Error & {code?:string}
    error.code=body?.error?.code
    throw error
  }
  return body.data
}

// 平仓只依赖真实持仓上下文：不再请求开仓用的赛事映射与公开盘口接口。
async function load(){
  const generation=++loadRevision
  closeContext.value=null;result.value=null;message.value=''
  if(closing.value){
    try{
      const [w,close]=await Promise.all([
        api('/wallets'),
        api('/trade-intents/'+encodeURIComponent(String(route.query.close))+'/close-context')
      ])
      if(generation!==loadRevision)return
      wallets.value=w.items
      if(!w.items.some((item:any)=>item.id===close.wallet_id))throw new Error('原订单钱包不在已配置钱包中')
      if(close.market_link_id!==route.params.link||close.outcome_id!==route.params.outcome)throw new Error('平仓资产与当前页面不一致')
      closeContext.value=close
      wallet.value=close.wallet_id;side.value='SELL';size.value=close.candidate_shares;price.value=close.market_price||''
    }catch(e){
      if(generation===loadRevision)message.value=e instanceof Error?e.message:'无法核实真实持仓与盘口，不能平仓。'
    }
    return
  }
  const link=route.params.link,outcome=route.params.outcome
  try{
    const [w,b,c]=await Promise.all([api('/wallets'),api('/outcomes/'+outcome+'/book'),api('/order-context/'+link+'/'+outcome)])
    if(generation!==loadRevision)return
    wallets.value=w.items;book.value=b;context.value=c
    if(wallets.value.length)wallet.value=wallets.value[0].id
  }catch(e){
    if(generation===loadRevision){book.value=null;message.value=e instanceof Error?e.message:'钱包或真实盘口不可用，请检查配置后重试。'}
  }
}

// 一步式下单：与组合订单相同，前端只发一次幂等请求，服务端在本次请求内核对实时盘口、签名并提交。
async function submit(){
  if(busy.value||pending.value)return
  const draft=closing.value
    ?{wallet_id:closeContext.value?.wallet_id,market_link_id:route.params.link,outcome_id:route.params.outcome,
      side:'SELL',limit_price:closeContext.value?.market_price,size:closeContext.value?.candidate_shares,
      order_type:'FOK',parent_trade_id:String(route.query.close)}
    :{wallet_id:wallet.value,market_link_id:route.params.link,outcome_id:route.params.outcome,
      side:side.value,limit_price:price.value,size:orderSize.value}
  if(!draft.wallet_id||!draft.limit_price||!draft.size){message.value='请核对钱包、限价与份额后再下单。';return}
  busy.value=true;message.value=''
  const key=crypto.randomUUID()
  pending.value={key,wallet_id:draft.wallet_id}
  try{localStorage.setItem(pendingKey,JSON.stringify(pending.value))}
  catch{pending.value=null;busy.value=false;message.value='无法保存查询凭据，订单未提交。';return}
  try{
    const data=await api('/single-orders',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf,'Idempotency-Key':key},body:JSON.stringify(draft)})
    result.value=data.intent
    if(!['unknown','submitting'].includes(data.intent.state)){localStorage.removeItem(pendingKey);pending.value=null}
    message.value=(closing.value?'平仓订单已记录':'订单已记录')+'；'+stateName(data.intent.state)+'，请查看状态。接受不代表成交。'
  }catch(e){
    const error=e as Error & {code?:string}
    const known=SAFE_CODES.includes(error.code||'')
    if(known){localStorage.removeItem(pendingKey);pending.value=null}
    message.value=known?(error.message+'（本次未发送下单请求）'):'下单结果未核实，请按原键查询，不要重复提交。'
  }finally{busy.value=false}
}

async function lookup(){
  if(!pending.value)return
  try{
    result.value=await api('/trade-intents/by-key/'+pending.value.key+'?wallet_id='+encodeURIComponent(pending.value.wallet_id))
    if(!['unknown','submitting'].includes(result.value.state)){localStorage.removeItem(pendingKey);pending.value=null}
    message.value='已查询现有订单状态：'+stateName(result.value.state)
  }catch{message.value='尚无法确认订单是否创建，请稍后再次查询。'}
}
async function reconcile(){
  if(!result.value?.id)return
  try{
    await api('/trade-intents/'+result.value.id+'/reconcile',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:'{}'})
    message.value='已重新安排对账，请稍后按原键查询。'
  }catch(e){message.value=e instanceof Error?e.message:'重新安排对账失败。'}
}
function clearPending(){
  if(!clearAcknowledged.value)return
  localStorage.removeItem(pendingKey);pending.value=null;result.value=null;clearAcknowledged.value=false
  message.value='本机记录已清除，服务端订单状态不受影响。'
}
async function restorePending(){
  try{pending.value=JSON.parse(localStorage.getItem(pendingKey)||'null')}catch{pending.value=null}
  // 页面加载时自动查询一次服务端：订单可能早已被 worker 对账为终态，本机标记不该一直挡住新订单。
  if(pending.value)await lookup()
}
async function signOut(){
  try{await logout();await router.replace('/login')}
  catch(e){if(e instanceof ApiError&&e.status===401){clearSession();await router.replace('/login')}else message.value='退出请求未确认，请重试。'}
}
onMounted(()=>{
  const reference=route.query.reference
  if(!closing.value&&typeof reference==='string'&&/^(?:0?\.\d{1,18})$/.test(reference)&&Number(reference)>0&&Number(reference)<1)price.value=reference
  void restorePending()
})
watch(()=>[route.params.link,route.params.outcome,route.query.close],()=>{context.value=null;book.value=null;result.value=null;load()},{immediate:true})
</script>

<template>
<div class="app-layout">
  <AppSidebar :open="menuOpen" @toggle="menuOpen=!menuOpen" />
  <main class="workspace">
    <AppTopbar :breadcrumb="'工作台 / '+(closing?'立即平仓':'单场订单')" :username="session.username" @sign-out="signOut" />
    <div class="page-heading">
      <div>
        <p class="eyebrow">REAL FUNDS</p>
        <h1>{{closing?'立即平仓':'单场订单'}}</h1>
        <p class="muted">{{closing?'按当前盘口市价一次卖出全部可平仓份额；全部成交或不成交。':'按当前盘口提交限价单；一次点击即提交真实资金订单，执行中不代表成交。'}}</p>
      </div>
    </div>
    <p role="status" class="form-message">{{message}}</p>

    <OrderPendingPanel v-if="pending" v-model:acknowledged="clearAcknowledged"
      :state="result?.state||null" :reconcilable="reconcilable"
      @lookup="lookup" @reconcile="reconcile" @clear="clearPending" />

    <CloseOrderPanel v-if="closing" :context="closeContext" :wallet-label="closeWalletLabel"
      :market-close-ready="marketCloseReady" :busy="busy" :pending="pending" @refresh="load()" @submit="submit" />

    <OpenOrderForm v-else :wallets="wallets" :book="book" :context="context" :order-size="orderSize"
      :busy="busy" :pending="pending" :estimated-notional="orderNotional"
      v-model:wallet="wallet" v-model:side="side" v-model:price="price" v-model:size="size"
      v-model:budget="budget" v-model:input-mode="inputMode" @submit="submit" />

    <OrderResultPanel v-if="result" :result="result" />
  </main>
</div>
</template>
