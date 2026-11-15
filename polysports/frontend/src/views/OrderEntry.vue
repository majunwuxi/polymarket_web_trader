<script setup lang="ts">
import {onMounted,ref,watch,computed} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {session,logout,clearSession,ApiError} from '../auth'

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
const stateName=(s:string)=>({submitting:'提交中',pending:'已接受，待核对',partially_filled:'部分成交',filled:'全部成交',rejected:'已拒绝',cancelled:'已撤单',unknown:'结果未知'} as Record<string,string>)[s]||'未知'
const errorName=(s:string)=>({engine_restarting:'撮合引擎正在重启，系统未自动重发',post_only_mode:'市场处于 post-only 模式，系统未自动改变或重发订单',signed_order_mismatch:'签名参数不一致，订单未发送'} as Record<string,string>)[s]||s
const time=(v:string)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(v))

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
  <header class="mobile-header"><div class="brand"><span class="brand-mark">P</span> PolySports</div><button class="menu-button" :aria-expanded="menuOpen" aria-controls="primary-nav" @click="menuOpen=!menuOpen">菜单</button></header>
  <aside id="primary-nav" class="sidebar" :class="{open:menuOpen}">
    <div class="brand sidebar-brand"><span class="brand-mark">P</span> PolySports</div>
    <p class="nav-label">工作台</p>
    <nav aria-label="主导航">
      <RouterLink to="/">总览</RouterLink><RouterLink to="/sports/football">足球赛事</RouterLink><RouterLink to="/sports/nba">NBA 赛事</RouterLink><RouterLink to="/combos">组合交易</RouterLink><RouterLink to="/orders">交易记录</RouterLink><RouterLink to="/history">历史与导出</RouterLink><RouterLink to="/wallets">钱包状态</RouterLink><RouterLink to="/system">系统管理</RouterLink>
    </nav>
  </aside>
  <main class="workspace">
    <div class="workspace-top"><span class="breadcrumb">工作台 / {{closing?'立即平仓':'单场订单'}}</span><div class="account"><span>{{session.username}}</span><button class="text-button" @click="signOut">退出</button></div></div>
    <div class="page-heading">
      <div>
        <p class="eyebrow">REAL FUNDS</p>
        <h1>{{closing?'立即平仓':'单场订单预览'}}</h1>
        <p class="muted">{{closing?'按当前盘口市价一次卖出全部可平仓份额；全部成交或不成交。':'确认后将使用真实资金提交限价单；订单接受不等于成交。'}}</p>
      </div>
    </div>
    <p role="status" class="form-message">{{message}}</p>

    <section v-if="pending" class="welcome-card">
      <h2>有一笔结果待核对</h2><p>系统保留了原幂等键。查询结果前请勿重复下单。</p>
      <button class="text-button" @click="lookup">按原键查询</button>
      <p>仅在已到 Polymarket 独立核实、并理解重复下单风险后，才清除本机记录。</p>
      <label><input v-model="clearAcknowledged" type="checkbox">我已独立核实订单状态</label>
      <button class="text-button" :disabled="!clearAcknowledged" @click="clearPending">清除本机记录</button>
    </section>

    <section v-if="closing" class="welcome-card detail">
      <template v-if="closeContext">
        <p>钱包：{{wallets.find(w=>w.id===closeContext.wallet_id)?.label}} · {{closeContext.wallet_address}}</p>
        <p>原订单已成交 {{closeContext.matched_shares}} · 钱包持仓 {{closeContext.wallet_shares}} · 本次平仓 {{closeContext.candidate_shares}} 份</p>
        <p v-if="marketCloseReady">当前市价 {{closeContext.market_price}} · 预计收入约 {{closeContext.estimated_proceeds}} pUSD</p>
        <p v-if="closeContext.market_close_reason" class="form-message">{{closeContext.market_close_reason}}</p>
        <p>FOK 卖出：全部成交或不成交；盘口变化需重新获取市价，接受不代表成交。</p>
        <p>{{closeContext.notice}}</p>
        <button class="text-button" :disabled="busy || !marketCloseReady" @click="makePreview()">刷新市价</button>
        <button class="text-button" :disabled="busy || !!pending || !preview || previewExpired" @click="submit">确认按市价平仓</button>
        <p v-if="preview && !previewExpired">市价有效期至 {{preview.expires_at}}；提交时再次核验盘口与钱包。</p>
        <p v-else-if="preview">市价已过期，请点击「刷新市价」重新获取。</p>
      </template>
      <p v-else>正在核实真实持仓与盘口…</p>
    </section>

    <section v-else class="welcome-card detail">
      <p v-if="context">{{context.home}} 对 {{context.away}} · {{context.market_question}} · 选项：{{context.outcome_label}} · {{time(context.starts_at)}} UTC+8</p>
      <p v-if="context" class="id-text">资产 ID：{{context.asset_id}} · 协议：{{context.protocol_version}}</p>
      <p v-if="book">价格增量 {{book.tick_size}} · 最小份额 {{book.min_order_size}} · 盘口时间 {{book.observed_at}}</p>
      <p v-else>真实盘口不可用。</p>
      <form @submit.prevent="makePreview()">
        <label for="wallet">钱包</label>
        <select id="wallet" v-model="wallet" required @change="preview=null"><option v-for="w in wallets" :key="w.id" :value="w.id">{{w.label}} · {{w.wallet_address}}</option></select>
        <label for="side">方向</label>
        <select id="side" v-model="side" @change="preview=null"><option value="BUY">买入</option><option value="SELL">卖出</option></select>
        <label for="price">限价</label>
        <input id="price" v-model="price" inputmode="decimal" required @input="preview=null">
        <template v-if="side==='BUY'">
          <label for="input-mode">输入方式</label>
          <select id="input-mode" v-model="inputMode"><option value="amount">购买金额（pUSD）</option><option value="shares">购买份额</option></select>
        </template>
        <template v-if="side==='BUY' && inputMode==='amount'">
          <label for="budget">购买金额上限（pUSD）</label>
          <input id="budget" v-model="budget" inputmode="decimal" required>
          <p>按限价计算的份额：{{orderSize || '请填写有效金额与限价'}}；份额向下取两位小数，手续费另计。</p>
        </template>
        <template v-else>
          <label for="size">份额</label>
          <input id="size" v-model="size" inputmode="decimal" required @input="preview=null">
        </template>
        <button class="text-button" :disabled="busy || !wallet || !book">验证并预览</button>
      </form>
    </section>

    <section v-if="preview && !closing" class="welcome-card detail">
      <h2>确认真实资金订单</h2>
      <p>钱包：{{wallets.find(w=>w.id===preview.wallet_id)?.label}} · {{wallets.find(w=>w.id===preview.wallet_id)?.wallet_address}}</p>
      <p v-if="preview.context_snapshot">{{preview.context_snapshot.home}} 对 {{preview.context_snapshot.away}} · {{preview.context_snapshot.market_question}} · {{preview.context_snapshot.outcome_label}} · {{time(preview.context_snapshot.starts_at)}} UTC+8</p>
      <p>方向：{{preview.side}} · 价格：{{preview.limit_price}} · 份额：{{preview.size}}</p>
      <p>限价金额：{{preview.estimated_notional}} pUSD；手续费未知，实际成交以交易所记录为准。</p>
      <p>预览有效期至 {{preview.expires_at}}；提交时将再次检查盘口和钱包。</p>
      <button class="text-button" :disabled="busy || !!pending || previewExpired" @click="submit">确认并提交真实订单</button>
      <p v-if="previewExpired">预览已过期，请重新验证并预览。</p>
    </section>

    <section v-if="result" class="welcome-card detail">
      <h2>订单状态</h2>
      <p>状态：{{stateName(result.state)}} · 已成交份额：{{result.matched_size ?? '未知'}}</p>
      <p v-if="result.error_code">状态说明：{{errorName(result.error_code)}}</p>
      <p v-if="result.retry_after_seconds!=null">建议等待：{{result.retry_after_seconds}} 秒</p>
      <RouterLink to="/orders">查看交易记录</RouterLink>
    </section>
  </main>
</div>
</template>
