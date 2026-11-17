<script setup lang="ts">
import {onActivated,onMounted,ref} from 'vue'
import {useRouter} from 'vue-router'
import {session,logout,clearSession,ApiError} from '../auth'
import AppSidebar from '../components/AppSidebar.vue'
import AppTopbar from '../components/AppTopbar.vue'
import SingleResolution from '../components/SingleResolution.vue'
import ComboLegDetails from '../components/ComboLegDetails.vue'
import ComboResolution from '../components/ComboResolution.vue'
import {cancelStateName,comboResult,comboStateName,orderStateName,resultBadge} from '../utils/labels'
const router=useRouter(),menuOpen=ref(false),orders=ref<any[]>([]),nextCursor=ref<string|null>(null),loaded=ref(false),message=ref(''),pendingCancel=ref<string|null>(null),recoveryId=ref<string|null>(null),candidates=ref<any[]>([]),fillOrder=ref<string|null>(null),fills=ref<any[]>([]),fillsTruncated=ref(false),attachId=ref(''),ack=ref(false)
const combos=ref<any[]>([]),comboCursor=ref<string|null>(null),comboLoaded=ref(false),comboError=ref(''),comboBusy=ref(false)
const fullId=ref<string|null>(null)
const redeemMap=ref<Record<string,any>>({}),redeemEnabled=ref(false),redeemBusy=ref<string|null>(null),redeemErrors=ref<Record<string,string>>({})

async function api(path:string,options:RequestInit={}){const r=await fetch('/api/v1'+path,{credentials:'same-origin',cache:'no-store',...options});const b=await r.json();if(!r.ok)throw new Error(b?.error?.message||'请求失败');return b.data}

// 列表摘要只保留结论；题目里重复的对阵前缀、完整订单编号等证据放入明细。
const shortQuestion=(q:any)=>{const s=String(q??'');return /^[^:]*\bvs\.?\b[^:]*:\s*/i.test(s)?s.replace(/^[^:]*\bvs\.?\b[^:]*:\s*/i,''):s}
const shortId=(v:any)=>{const s=String(v??'');return s.length>18?s.slice(0,10)+'…'+s.slice(-6):s}
const sharesLine=(o:any)=>{
  const parts=[`限价 ${o.limit_price}`,`${o.matched_size??o.size} 份`]
  if(o.matched_size!=null&&String(o.matched_size)!==String(o.size))parts.push(`已成交 ${o.matched_size}`)
  return parts.join(' · ')
}
const canClose=(o:any)=>o.side==='BUY'&&Number(o.matched_size)>0&&!o.close_blocked
const closeHint=(o:any)=>o.close_blocked?(o.close_block_reason||'比赛结果已确认或市场已结算，不能平仓'):'该记录没有已确认的买入持仓可供平仓'
const confirmedAmount=(o:any)=>resolutionOrder.value===o.id&&resolution.value?.settlement?.agreement==='consistent'?resolution.value.settlement.settlement_amount:null
// 卡片盈亏由列表接口直接给出：黑底为亏损、红底为盈利；未确认时用中性样式，不用黑白/红白误导。
const pnlBadge=(o:any)=>{
  const p=o?.pnl
  if(p&&['profit','loss','flat'].includes(p.tone))return {cls:'pnl-'+p.tone,text:p.label,hint:p.detail||''}
  if(p?.tone==='na')return {cls:'pnl-pending',text:p.label||'平仓不适用',hint:p.detail||''}
  return {cls:'pnl-pending',text:p?.label||'盈亏待确认',hint:p?.detail||'赛果或成交数据未齐，暂不标注金额'}
}

async function loadCombos(append=false){if(comboBusy.value)return;comboBusy.value=true;comboError.value='';try{const q=new URLSearchParams({limit:'20',state:'confirmed',hide_settled:'true'});if(append&&comboCursor.value)q.set('cursor',comboCursor.value);const data=await api('/combo-rfqs?'+q);combos.value=append?[...combos.value,...data.items]:data.items;comboCursor.value=data.next_cursor||null;comboLoaded.value=true}catch{comboError.value='组合记录加载失败，请重试。'}finally{comboBusy.value=false}}
async function load(append=false){try{const q=new URLSearchParams({limit:'20',hide_settled:'true'});if(append && nextCursor.value)q.set('cursor',nextCursor.value);const data=await api('/trade-intents?'+q);orders.value=append?[...orders.value,...data.items]:data.items;nextCursor.value=data.next_cursor;loaded.value=true}catch{message.value='订单状态加载失败。'}}
async function action(id:string,kind:'cancel'|'reconcile'){try{await api(kind==='cancel'?'/single-orders/'+id+'/cancel':'/trade-intents/'+id+'/reconcile',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:'{}'});pendingCancel.value=null;message.value=kind==='cancel'?'撤单请求已处理，请查看状态。':'已安排只读对账。';await load()}catch{message.value='请求结果未确认，请刷新或安排只读对账。'}}
async function loadFills(id:string){try{const data=await api('/trade-intents/'+id+'/fills');fillOrder.value=id;fills.value=data.items;fillsTruncated.value=data.truncated;message.value='已查询交易所成交记录。'}catch(e){message.value=e instanceof Error?e.message:'成交记录查询失败。'}}
const redeemKey=(kind:'single'|'combo',id:string)=>kind+':'+id
const redeemInfo=(kind:'single'|'combo',id:string)=>redeemMap.value[redeemKey(kind,id)]
const canRedeem=(kind:'single'|'combo',id:string)=>redeemEnabled.value&&redeemInfo(kind,id)?.eligible===true
async function loadRedeemEligibility(){
  const intentIds=orders.value.map((row:any)=>row.id),comboIds=combos.value.map((row:any)=>row.id)
  if(!intentIds.length&&!comboIds.length){redeemMap.value={};redeemEnabled.value=false;return}
  try{
    const data=await api('/redemptions/eligibility',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({intent_ids:intentIds,combo_ids:comboIds})})
    const next:Record<string,any>={}
    for(const item of data.items||[])next[redeemKey(item.kind,item.id)]=item
    redeemMap.value=next;redeemEnabled.value=data.execution_enabled===true
  }catch{redeemMap.value={};redeemEnabled.value=false}
}
async function refreshAll(){await Promise.all([load(),loadCombos()]);await loadRedeemEligibility()}
async function redeem(kind:'single'|'combo',id:string){
  if(!canRedeem(kind,id)||redeemBusy.value)return
  const busyKey=redeemKey(kind,id);redeemBusy.value=busyKey;delete redeemErrors.value[busyKey]
  message.value=kind==='combo'?'正在提交组合整仓赎回…':'正在提交整仓赎回…'
  try{
    const key=crypto.randomUUID()
    const path=kind==='single'?'/trade-intents/'+id+'/redeem':'/combo-rfqs/'+id+'/redeem'
    const data=await api(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf,'Idempotency-Key':key},body:'{}'})
    if(data.state==='confirmed'){
      const credit=data.credited_amount!=null?'，现金余额增加 '+data.credited_amount+' pUSD':''
      const balance=data.collateral_balance!=null?'；当前现金余额 '+data.collateral_balance+' pUSD':''
      message.value='赎回已确认'+credit+balance
    }else if(data.state==='unknown')message.value=data.message||'赎回结果未知，请勿重复操作。'
    else message.value=data.message||'赎回请求已处理。'
    await refreshAll()
  }catch(e){const text=e instanceof Error?e.message:'赎回请求结果未确认，请勿重复操作。';message.value=text;redeemErrors.value[busyKey]=text}
  finally{redeemBusy.value=null}
}
const resolutionOrder=ref<string|null>(null), resolution=ref<any>(null)
async function loadResolution(id:string){try{const data=await api('/trade-intents/'+id+'/resolution');resolution.value=data;resolutionOrder.value=id;if(data?.state==='resolved'){const row=orders.value.find((o:any)=>o.id===id);if(row){row.close_blocked=true;row.close_block_reason='市场已最终结算，不能平仓'}}}catch(e){message.value=e instanceof Error?e.message:'结算查询失败'}}
async function findCandidates(id:string){recoveryId.value=id;candidates.value=[];attachId.value='';ack.value=false;try{const data=await api('/trade-intents/'+id+'/reconcile-candidates');candidates.value=data.items;message.value=data.truncated?'候选可能不完整，请到 Polymarket 独立核查。':'候选仅包括未完成订单；请独立核实。'}catch{message.value='候选订单查询失败，请稍后重试。'}}
async function attach(){if(!recoveryId.value||!ack.value)return;try{await api('/trade-intents/'+recoveryId.value+'/attach-order',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({order_id:attachId.value,acknowledge_uncertainty:true})});message.value='订单编号已关联，状态仍需只读对账。';recoveryId.value=null;await load()}catch(e){message.value=e instanceof Error?e.message:'订单编号关联失败。'}}
async function signOut(){try{await logout();await router.replace('/login')}catch(e){if(e instanceof ApiError&&e.status===401){clearSession();await router.replace('/login')}else message.value='退出请求未确认，请重试。'}}
const comboResolutionId=ref<string|null>(null),comboResolution=ref<any>(null)
async function loadComboResolution(id:string){try{const data=await api('/combo-rfqs/'+id+'/resolution');comboResolution.value=data;comboResolutionId.value=id;if(data?.resolved_legs===data?.leg_count&&data?.leg_count>0){const row=combos.value.find((c:any)=>c.id===id);if(row){row.close_blocked=true;row.close_block_reason='各腿市场均已终态结算，不能平仓'}}}catch(e){message.value=e instanceof Error?e.message:'组合胜负核对失败'}}
// 组合买入三项摘要只消费后端 buy_summary：不把 quantity（预算）当份额，也不把
// total_required（接受报价所需余额）当买入金额。缺数据时明确写「待核实」。
const buyMetrics=(c:any)=>{
  const s=c?.buy_summary
  return [
    {label:'份额总额',value:s?.size?s.size+' 份':'待核实'},
    {label:'买入价格',value:s?.price?s.price+' pUSD/份':'待核实'},
    {label:'买入金额',value:s?.cost?(s.estimated?'约':'')+s.cost+' pUSD':'待核实'}
  ]
}
const buyNote=(c:any)=>({quote_rebuilt:'金额按签名报价重建，标「约」为估算值。',quote_partial:'报价字段不完整，金额待核实。',unavailable:'缺少可核实数据，金额不做猜测。'} as Record<string,string>)[c?.buy_summary?.basis]||''
onMounted(refreshAll)
let activated=false
onActivated(()=>{if(!activated){activated=true;return}void refreshAll()})
</script>

<template>
<div class="app-layout">
  <AppSidebar :open="menuOpen" @toggle="menuOpen=!menuOpen" />
  <main class="workspace order-history">
    <AppTopbar breadcrumb="工作台 / 交易记录" :username="session.username" @sign-out="signOut" />
    <div class="page-heading"><div><p class="eyebrow">ORDER STATUS</p><h1>交易记录</h1><p class="muted">已接受不等于成交；未知状态请对账，不要重复提交。开赛超过 24 小时且已结算的历史记录不再显示。</p></div></div>
    <p role="status" class="form-message">{{message}}</p>
    <button class="text-button" @click="refreshAll">刷新订单</button>

    <div class="order-columns">
    <section class="welcome-card" aria-label="单场交易记录">
      <h2>单场订单</h2>
      <p class="scope-note">金额以赛果判断与 Polymarket 终态结算一致为准；粉色卡片可整仓赎回。</p>
      <p v-if="loaded && !orders.length">暂无已记录订单。</p>
      <article v-for="order in orders" :key="order.id" :class="['market-card','order-card',{'is-redeemable':canRedeem('single',order.id)}]">
        <h2 class="card-title"><span>{{order.side==='BUY'?'买入':'卖出'}} · {{orderStateName(order.state)}}<span v-if="resultBadge(order.result_won)" class="result-badge" :class="resultBadge(order.result_won)?.cls">{{resultBadge(order.result_won)?.text}}</span></span><span class="pnl-badge" :class="pnlBadge(order).cls" :title="pnlBadge(order).hint">{{pnlBadge(order).text}}</span></h2>
        <p>{{order.home}} 对 {{order.away}} · {{shortQuestion(order.market_question)}} · {{order.outcome_label}}</p>
        <p class="summary-line"><span>{{sharesLine(order)}}<template v-if="confirmedAmount(order)!=null"> · 结算 {{confirmedAmount(order)}} pUSD</template></span><span v-if="order.external_order_id" class="id-text id-compact">ID <template v-if="fullId===order.id">{{order.external_order_id}}</template><template v-else>{{shortId(order.external_order_id)}}</template><button class="text-button id-toggle" @click="fullId=fullId===order.id?null:order.id">{{fullId===order.id?'收起':'全文'}}</button></span></p>
        <p v-if="order.error_code">状态说明：{{order.error_code}}<span v-if="order.retry_after_seconds!==null"> · 建议等待 {{order.retry_after_seconds}} 秒</span></p>
        <p v-if="order.cancel_state">撤单状态：{{cancelStateName(order.cancel_state)}}</p>
        <div class="order-actions">
          <button v-if="canRedeem('single',order.id)" class="text-button redeem-button" :disabled="redeemBusy===redeemKey('single',order.id)" :title="'整仓赎回该钱包在 '+order.market_question+' 的全部可赎回份额'" @click="redeem('single',order.id)">赎回</button>
          <span v-else-if="redeemInfo('single',order.id)?.state==='confirmed'" class="redeem-state">已赎回</span>
          <span v-else-if="redeemInfo('single',order.id)?.state==='unknown'" class="redeem-state redeem-state-warn">赎回结果未知</span>
          <RouterLink v-if="canClose(order)" class="text-button" :to="{path:'/orders/new/'+order.market_link_id+'/'+order.outcome_id,query:{close:order.id}}">立即平仓</RouterLink>
          <button v-else class="text-button" disabled :title="closeHint(order)">{{order.close_blocked?'已结算 · 不可平仓':'立即平仓'}}</button>
          <button v-if="order.external_order_id" class="text-button" @click="loadFills(order.id)">查询成交记录</button>
          <button class="text-button" @click="loadResolution(order.id)">核对赛果和市场结算</button>
          <details class="more-actions"><summary>更多</summary><div>
            <button class="text-button" @click="action(order.id,'reconcile')">安排只读对账</button>
            <button v-if="order.state==='unknown' && !order.external_order_id" class="text-button" @click="findCandidates(order.id)">查找相似未完成订单</button>
            <button v-if="order.external_order_id && ['pending','partially_filled','unknown'].includes(order.state) && (!order.cancel_state || order.cancel_state==='rejected')" class="text-button" @click="pendingCancel=order.id">撤单</button>
          </div></details>
        </div>
        <p v-if="redeemErrors[redeemKey('single',order.id)]" class="redeem-error" role="alert">{{redeemErrors[redeemKey('single',order.id)]}}</p>
        <div v-if="fillOrder===order.id">
          <p>成交状态由交易所提供，未确认状态不等于结算完成。</p>
          <p v-if="!fills.length">当前查询未返回成交记录。</p>
          <p v-if="fillsTruncated">结果可能不完整，请到交易所核查。</p>
          <p v-for="fill in fills" :key="fill.trade_id" class="id-text">{{fill.trade_id}} · {{fill.size}} 份 · 价格 {{fill.price}} · {{fill.status}}</p>
        </div>
        <div v-if="recoveryId===order.id" class="mapping-confirm">
          <p>候选仅按参数筛选，不证明属于本次提交。请到 Polymarket 独立核对订单编号；未出现不代表订单不存在。</p>
          <p v-for="candidate in candidates" :key="candidate.order_id" class="id-text">{{candidate.order_id}} · {{candidate.created_at}} · {{candidate.status}}</p>
          <label :for="'attach-'+order.id">经独立核实的交易所订单 ID</label>
          <input :id="'attach-'+order.id" v-model="attachId" placeholder="0x…" maxlength="66">
          <label><input v-model="ack" type="checkbox">我已独立核实该编号属于本次订单</label>
          <button class="text-button" :disabled="!ack || !attachId" @click="attach">关联并继续只读对账</button>
        </div>
        <div v-if="pendingCancel===order.id">
          <p>确认向交易所撤销此订单？</p>
          <button class="text-button" @click="pendingCancel=null">返回</button>
          <button class="text-button" @click="action(order.id,'cancel')">确认撤单</button>
        </div>

        <SingleResolution v-if="resolutionOrder===order.id" :resolution="resolution" />
      </article>
      <button v-if="nextCursor" class="text-button" @click="load(true).then(loadRedeemEligibility)">加载更多</button>
    </section>

    <section class="welcome-card" aria-label="组合交易记录">
      <h2>组合交易</h2>
      <p class="scope-note">仅显示已确认组合；全部腿命中才算组合获胜；粉色卡片可整仓赎回。</p>
      <button class="text-button" :disabled="comboBusy" @click="loadCombos().then(loadRedeemEligibility)">刷新组合记录</button>
      <p v-if="comboError" role="alert">{{comboError}}</p>
      <p v-else-if="comboLoaded && !combos.length">暂无已确认组合交易。</p>
      <article v-for="combo in combos" :key="combo.id" :class="['market-card','order-card',{'is-redeemable':canRedeem('combo',combo.id)}]">
        <h3 class="card-title"><span>组合{{combo.direction==='BUY'?'买入':'卖出'}} · {{comboStateName(combo.state)}}</span><span class="pnl-badge" :class="pnlBadge(combo).cls" :title="pnlBadge(combo).hint">{{pnlBadge(combo).text}}</span></h3>
        <div v-if="combo.direction==='BUY'" class="buy-metrics" aria-label="组合买入摘要">
          <div v-for="m in buyMetrics(combo)" :key="m.label" class="buy-metric"><span class="buy-metric-label">{{m.label}}</span><strong class="buy-metric-value">{{m.value}}</strong></div>
        </div>
        <p v-if="buyNote(combo)" class="buy-note">{{buyNote(combo)}}</p>
        <p v-for="leg in combo.legs" :key="leg.outcome_id" class="leg-chip" :title="leg.result_basis ?? ''">{{leg.home}} 对 {{leg.away}} · {{shortQuestion(leg.market_question)}} · {{leg.outcome_label}}<span v-if="leg.home_score != null && leg.away_score != null"> · {{leg.home_score}} : {{leg.away_score}}</span><span v-if="resultBadge(leg.result_won)" class="result-badge" :class="resultBadge(leg.result_won)?.cls">{{resultBadge(leg.result_won)?.text}}</span><span v-else> · 无法判断</span></p>
        <p class="summary-line" :title="combo.score_reason || ''"><span>组合结果：{{comboResult(combo.by_score)}} · {{combo.judged_legs ?? 0}}/{{combo.leg_count ?? combo.legs.length}} 腿</span><span class="id-compact">{{shortId(combo.external_rfq_id)}}</span></p>
        <div class="order-actions">
          <button v-if="canRedeem('combo',combo.id)" class="text-button redeem-button" :disabled="redeemBusy===redeemKey('combo',combo.id)" title="整仓赎回该组合的全部可赎回份额" @click="redeem('combo',combo.id)">赎回</button>
          <span v-else-if="redeemInfo('combo',combo.id)?.state==='confirmed'" class="redeem-state">已赎回</span>
          <span v-else-if="redeemInfo('combo',combo.id)?.state==='unknown'" class="redeem-state redeem-state-warn">赎回结果未知</span>
          <RouterLink v-if="combo.direction==='BUY' && combo.state==='confirmed' && !combo.close_blocked" class="text-button" :to="{path:'/combos',query:{close:combo.id}}">立即平仓</RouterLink>
          <button v-else class="text-button" disabled :title="closeHint(combo)">{{combo.close_blocked?'已结算 · 不可平仓':'立即平仓'}}</button>
          <button class="text-button" @click="loadComboResolution(combo.id)">核对组合赛果和市场结算</button>
          <details class="more-actions"><summary>更多</summary><div>
            <details class="detail-box"><summary>逐腿与报价明细</summary>
              <ComboLegDetails :combo="combo" />
            </details>
            <RouterLink to="/combos">查看组合记录</RouterLink>
          </div></details>
        </div>
        <p v-if="redeemErrors[redeemKey('combo',combo.id)]" class="redeem-error" role="alert">{{redeemErrors[redeemKey('combo',combo.id)]}}</p>

        <ComboResolution v-if="comboResolutionId===combo.id" :resolution="comboResolution" />
      </article>
      <button v-if="comboCursor" class="text-button" :disabled="comboBusy" @click="loadCombos(true).then(loadRedeemEligibility)">加载更多组合</button>
    </section>
    </div>
  </main>
</div>
</template>
