<script setup lang="ts">
import {onMounted,ref,watch,computed} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {session,logout,clearSession,ApiError} from '../auth'
import AppSidebar from '../components/AppSidebar.vue'
import AppTopbar from '../components/AppTopbar.vue'
import OrderPendingPanel from '../components/OrderPendingPanel.vue'
import CloseOrderPanel from '../components/CloseOrderPanel.vue'
import OpenOrderForm from '../components/OpenOrderForm.vue'
import OrderPreviewPanel from '../components/OrderPreviewPanel.vue'
import OrderResultPanel from '../components/OrderResultPanel.vue'

const route=useRoute(),router=useRouter()
const menuOpen=ref(false),wallets=ref<any[]>([]),wallet=ref('')
const side=ref('BUY'),price=ref(''),size=ref(''),budget=ref(''),inputMode=ref<'amount'|'shares'>('amount')
const book=ref<any>(null),preview=ref<any>(null),context=ref<any>(null),closeContext=ref<any>(null)
const message=ref(''),busy=ref(false),result=ref<any>(null),pending=ref<any>(null),clearAcknowledged=ref(false)
const closing=computed(()=>typeof route.query.close==='string' && !!route.query.close)
const pendingKey='polysports_pending_order'
let draftRevision=0,loadRevision=0

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
const marketCloseReady=computed(()=>!!closeContext.value&&!closeContext.value.market_close_reason&&!!closeContext.value.market_price&&Number(closeContext.value.candidate_shares)>0)
const previewExpired=computed(()=>{
  if(!preview.value)return true
  const expiry=Date.parse(preview.value.expires_at)
  return !Number.isFinite(expiry)||expiry<=Date.now()
})
const closeWalletLabel=computed(()=>wallets.value.find((w:any)=>w.id===closeContext.value?.wallet_id)?.label)
const previewWallet=computed(()=>wallets.value.find((w:any)=>w.id===preview.value?.wallet_id))

async function api(path:string,options:RequestInit={}){
  const response=await fetch('/api/v1'+path,{credentials:'same-origin',cache:'no-store',...options})
  const body=await response.json()
  if(!response.ok)throw new Error(body?.error?.message||'请求失败')
  return body.data
}

// 平仓只依赖真实持仓上下文：不再请求开仓用的赛事映射与公开盘口接口。
async function load(){
  const generation=++loadRevision
  closeContext.value=null;preview.value=null;result.value=null;message.value=''
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
      if(marketCloseReady.value)await makePreview(generation)
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

// 平仓的价格、份额、方向全部由服务端从真实盘口和持仓给出，页面不提供任何输入项。
async function makePreview(generation?:number){
  if(closing.value&&!marketCloseReady.value){message.value=closeContext.value?.market_close_reason||'当前无法按市价平仓';return}
  if(!closing.value&&(!wallet.value||!book.value))return
  busy.value=true;message.value='';preview.value=null
  const revision=draftRevision
  const snapshot=context.value?{...context.value}:null
  const draft=closing.value
    ?{wallet_id:closeContext.value.wallet_id,market_link_id:route.params.link,outcome_id:route.params.outcome,
       side:'SELL',limit_price:closeContext.value.market_price,size:closeContext.value.candidate_shares,
       order_type:'FOK',parent_trade_id:String(route.query.close)}
    :{wallet_id:wallet.value,market_link_id:route.params.link,outcome_id:route.params.outcome,
       side:side.value,limit_price:price.value,size:orderSize.value}
  try{
    const data=await api('/single-orders/preview',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify(draft)})
    if(generation!==undefined&&generation!==loadRevision)return
    if(revision!==draftRevision){message.value='市价已变化，请重新获取。';return}
    preview.value={...data,context_snapshot:snapshot}
  }catch(e){message.value=e instanceof Error?e.message:'预览失败。'}
  finally{busy.value=false}
}

async function submit(){
  if(!preview.value||busy.value||pending.value||previewExpired.value)return
  busy.value=true;message.value=''
  const key=crypto.randomUUID()
  const payload={wallet_id:preview.value.wallet_id,market_link_id:preview.value.market_link_id,outcome_id:preview.value.outcome_id,side:preview.value.side,limit_price:preview.value.limit_price,size:preview.value.size,preview_token:preview.value.preview_token,...(closing.value?{order_type:'FOK',parent_trade_id:preview.value.parent_trade_id}:{})}
  pending.value={key,wallet_id:payload.wallet_id}
  try{localStorage.setItem(pendingKey,JSON.stringify(pending.value))}catch{pending.value=null;busy.value=false;message.value='无法保存查询凭据，订单未提交。';return}
  try{
    const data=await api('/single-orders',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf,'Idempotency-Key':key},body:JSON.stringify(payload)})
    result.value=data.intent
    if(!['unknown','submitting'].includes(data.intent.state)){localStorage.removeItem(pendingKey);pending.value=null}
    message.value=closing.value?'平仓订单已记录；请查看状态，接受不代表成交。':'订单已记录；请查看状态，接受不代表成交。'
  }catch{message.value='提交结果未确认，请使用原幂等键查询，不要重新下单。'}
  finally{busy.value=false}
}

async function lookup(){
  if(!pending.value)return
  try{
    result.value=await api('/trade-intents/by-key/'+pending.value.key+'?wallet_id='+encodeURIComponent(pending.value.wallet_id))
    if(!['unknown','submitting'].includes(result.value.state)){localStorage.removeItem(pendingKey);pending.value=null}
    message.value='已查询现有订单状态。'
  }catch{message.value='尚无法确认订单是否创建，请稍后再次查询。'}
}
function clearPending(){
  if(!clearAcknowledged.value)return
  localStorage.removeItem(pendingKey);pending.value=null;clearAcknowledged.value=false
  message.value='本机记录已清除，服务端订单状态不受影响。'
}
async function signOut(){
  try{await logout();await router.replace('/login')}
  catch(e){if(e instanceof ApiError&&e.status===401){clearSession();await router.replace('/login')}else message.value='退出请求未确认，请重试。'}
}
onMounted(()=>{
  const reference=route.query.reference
  if(!closing.value&&typeof reference==='string'&&/^(?:0?\.\d{1,18})$/.test(reference)&&Number(reference)>0&&Number(reference)<1)price.value=reference
  try{pending.value=JSON.parse(localStorage.getItem(pendingKey)||'null')}catch{pending.value=null}
})
watch([wallet,side,price,size,budget,inputMode],()=>{draftRevision++;preview.value=null},{flush:'sync'})
watch(()=>[route.params.link,route.params.outcome,route.query.close],()=>{draftRevision++;preview.value=null;context.value=null;book.value=null;result.value=null;load()},{immediate:true})
</script>

<template>
<div class="app-layout">
  <AppSidebar :open="menuOpen" @toggle="menuOpen=!menuOpen" />
  <main class="workspace">
    <AppTopbar :breadcrumb="'工作台 / '+(closing?'立即平仓':'单场订单')" :username="session.username" @sign-out="signOut" />
    <div class="page-heading">
      <div>
        <p class="eyebrow">REAL FUNDS</p>
        <h1>{{closing?'立即平仓':'单场订单预览'}}</h1>
        <p class="muted">{{closing?'按当前盘口市价一次卖出全部可平仓份额；全部成交或不成交。':'确认后将使用真实资金提交限价单；订单接受不等于成交。'}}</p>
      </div>
    </div>
    <p role="status" class="form-message">{{message}}</p>

    <OrderPendingPanel v-if="pending" v-model:acknowledged="clearAcknowledged" @lookup="lookup" @clear="clearPending" />

    <CloseOrderPanel v-if="closing" :context="closeContext" :wallet-label="closeWalletLabel" :market-close-ready="marketCloseReady" :busy="busy" :pending="pending" :preview="preview" :preview-expired="previewExpired" @refresh="makePreview()" @submit="submit" />

    <OpenOrderForm v-else v-model:wallet="wallet" v-model:side="side" v-model:price="price" v-model:size="size" v-model:budget="budget" v-model:input-mode="inputMode" :wallets="wallets" :book="book" :context="context" :order-size="orderSize" :busy="busy" @preview="makePreview()" />

    <OrderPreviewPanel v-if="preview && !closing" :preview="preview" :wallet="previewWallet" :busy="busy" :pending="!!pending" :preview-expired="previewExpired" @submit="submit" />

    <OrderResultPanel v-if="result" :result="result" />
  </main>
</div>
</template>
