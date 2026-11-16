<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {useRouter} from 'vue-router'
import {session,logout,clearSession,ApiError} from '../auth'
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
const resultBadge=(won:any)=>won===true?{cls:'result-win',text:'选项获胜'}:won===false?{cls:'result-loss',text:'选项失败'}:null
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

function checkLine(r:any){
  const s=r?.settlement??{}
  const won=r?.result_judgment?.outcome_won??r?.outcome_won
  const verdict=won===true?'选项获胜':won===false?'选项未获胜':'结果待确认'
  if(s.agreement==='consistent')return {tone:'ok',text:`✓ 两个结果一致 · ${verdict} · 结算 ${s.settlement_amount??'未知'} pUSD`}
  if(s.agreement==='conflict')return {tone:'warn',text:`⚠ 两个结果不一致，需人工核实 · 按赛果 ${s.result_settlement_amount??'未知'} pUSD / 按市场 ${s.market_settlement_amount??'未知'} pUSD`}
  if(s.agreement==='market_only')return {tone:'warn',text:`◐ 仅有市场终态 · 按市场结算 ${s.market_settlement_amount??'未知'} pUSD，建议人工核实`}
  if(s.agreement==='result_only')return {tone:'info',text:`◐ 仅有赛果判断 · 预计 ${s.result_settlement_amount??'未知'} pUSD，市场未终态`}
  return {tone:'muted',text:'— 两个结果均不可用，暂不能确认金额'}
}

async function loadCombos(append=false){if(comboBusy.value)return;comboBusy.value=true;comboError.value='';try{const q=new URLSearchParams({limit:'20',state:'confirmed',hide_settled:'true'});if(append&&comboCursor.value)q.set('cursor',comboCursor.value);const data=await api('/combo-rfqs?'+q);combos.value=append?[...combos.value,...data.items]:data.items;comboCursor.value=data.next_cursor||null;comboLoaded.value=true}catch{comboError.value='组合记录加载失败，请重试。'}finally{comboBusy.value=false}}
const comboState=(s:string)=>({requesting:'询价中',quoted:'已报价，未确认成交',accepting:'确认中',executing:'执行中，未确认成交',confirmed:'已确认',expired:'报价已过期',rejected:'已拒绝',failed:'未成交',unknown:'结果未知'} as Record<string,string>)[s]||'状态未知'
const watchName=(s:string|null)=>({active:'已关注',completed:'已完赛，停止查询'} as Record<string,string>)[s||'']||'未关注'
const bindingName=(s:string|null)=>({pending:'等待匹配',needs_review:'待确认绑定',bound:'已绑定',failed:'绑定失败'} as Record<string,string>)[s||'']||'未绑定'
async function load(append=false){try{const q=new URLSearchParams({limit:'20',hide_settled:'true'});if(append && nextCursor.value)q.set('cursor',nextCursor.value);const data=await api('/trade-intents?'+q);orders.value=append?[...orders.value,...data.items]:data.items;nextCursor.value=data.next_cursor;loaded.value=true}catch{message.value='订单状态加载失败。'}}
const stateName=(s:string)=>({submitting:'提交中',pending:'已接受，待核对',partially_filled:'部分成交',filled:'全部成交',rejected:'已拒绝',cancelled:'已撤单',unknown:'结果未知'} as Record<string,string>)[s]||'未知'
const cancelName=(s:string)=>({submitting:'撤单中',unknown:'撤单结果未知',confirmed:'已确认撤单',rejected:'撤单被拒绝'} as Record<string,string>)[s]||'未知'
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
async function retryComboReconcile(id:string){try{await api('/combo-rfqs/'+id+'/reconcile',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:'{}'});message.value='已安排组合对账。'}catch(e){message.value=e instanceof Error?e.message:'组合对账安排失败'}}
const legWinner=(won:any,fallback:string)=>won===true?'选项获胜':won===false?'选项未获胜':fallback
const comboWinner=(won:any)=>won===true?'全部腿命中，组合获胜':won===false?'已有腿未命中，组合未获胜':'暂不能确认组合胜负'
const comboResult=(won:any)=>won===true?'获胜':won===false?'未获胜':'未确认'
const comboAgreement=(a:string)=>({consistent:'一致，组合胜负已确认',conflict:'不一致，请人工核实，不确认组合胜负',market_only:'仅有各腿市场终态，按市场结果核对，建议人工核实',result_only:'仅有赛果判断，市场终态未齐，组合结果仅为预计值',unavailable:'两个结果均不可用，暂不能确认',not_applicable:'组合卖出不适用终态胜负核对'} as Record<string,string>)[a]||'暂不能确认'
const legCheck=(a:string)=>({consistent:'两个结果一致',conflict:'两个结果不一致，需人工核实',partial:'仅有一个结果，暂不确认',unavailable:'两个结果均不可用'} as Record<string,string>)[a]||'暂不确认'
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
const buySource=(c:any)=>({combo_fill:'已落库 CONFIRMED 成交明细（签名接受订单的 makerAmount / takerAmount）',quote_rebuilt:'官方签名报价按 micro-USDC 精度重建',quote_partial:'签名报价字段不完整，金额未核实',unavailable:'暂无可核实的成交明细或报价'} as Record<string,string>)[c?.buy_summary?.basis]||'暂无可核实的成交明细或报价'
const legTone=(leg:any)=>leg.leg_agreement==='conflict'?'mini-tag-warn':leg.result_won===true?'mini-tag-ok':'mini-tag-neutral'
onMounted(refreshAll)
</script>

<template>
<div class="app-layout">
  <header class="mobile-header"><div class="brand"><span class="brand-mark">P</span> PolySports</div><button class="menu-button" :aria-expanded="menuOpen" aria-controls="primary-nav" @click="menuOpen=!menuOpen">菜单</button></header>
  <aside id="primary-nav" class="sidebar" :class="{open:menuOpen}"><div class="brand sidebar-brand"><span class="brand-mark">P</span> PolySports</div><p class="nav-label">工作台</p><nav aria-label="主导航"><RouterLink to="/">总览</RouterLink><RouterLink to="/sports/football">足球赛事</RouterLink><RouterLink to="/sports/nba">NBA 赛事</RouterLink><RouterLink to="/combos">组合交易</RouterLink><RouterLink to="/orders">交易记录</RouterLink><RouterLink to="/history">历史与导出</RouterLink><RouterLink to="/wallets">钱包状态</RouterLink><RouterLink to="/system">系统管理</RouterLink></nav></aside>
  <main class="workspace order-history">
    <div class="workspace-top"><span class="breadcrumb">工作台 / 交易记录</span><div class="account"><span>{{session.username}}</span><button class="text-button" @click="signOut">退出</button></div></div>
    <div class="page-heading"><div><p class="eyebrow">ORDER STATUS</p><h1>交易记录</h1><p class="muted">已接受不等于成交；未知状态请对账，不要重复提交。开赛超过 24 小时且已结算的历史记录不再显示。</p></div></div>
    <p role="status" class="form-message">{{message}}</p>
    <button class="text-button" @click="refreshAll">刷新订单</button>

    <div class="order-columns">
    <section class="welcome-card" aria-label="单场交易记录">
      <h2>单场订单</h2>
      <p class="scope-note">金额以赛果判断与 Polymarket 终态结算一致为准；粉色卡片可整仓赎回。</p>
      <p v-if="loaded && !orders.length">暂无已记录订单。</p>
      <article v-for="order in orders" :key="order.id" :class="['market-card','order-card',{'is-redeemable':canRedeem('single',order.id)}]">
        <h2 class="card-title"><span>{{order.side==='BUY'?'买入':'卖出'}} · {{stateName(order.state)}}<span v-if="resultBadge(order.result_won)" class="result-badge" :class="resultBadge(order.result_won)?.cls">{{resultBadge(order.result_won)?.text}}</span></span><span class="pnl-badge" :class="pnlBadge(order).cls" :title="pnlBadge(order).hint">{{pnlBadge(order).text}}</span></h2>
        <p>{{order.home}} 对 {{order.away}} · {{shortQuestion(order.market_question)}} · {{order.outcome_label}}</p>
        <p class="summary-line"><span>{{sharesLine(order)}}<template v-if="confirmedAmount(order)!=null"> · 结算 {{confirmedAmount(order)}} pUSD</template></span><span v-if="order.external_order_id" class="id-text id-compact">ID <template v-if="fullId===order.id">{{order.external_order_id}}</template><template v-else>{{shortId(order.external_order_id)}}</template><button class="text-button id-toggle" @click="fullId=fullId===order.id?null:order.id">{{fullId===order.id?'收起':'全文'}}</button></span></p>
        <p v-if="order.error_code">状态说明：{{order.error_code}}<span v-if="order.retry_after_seconds!==null"> · 建议等待 {{order.retry_after_seconds}} 秒</span></p>
        <p v-if="order.cancel_state">撤单状态：{{cancelName(order.cancel_state)}}</p>
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

        <div v-if="resolutionOrder===order.id" class="resolution-block">
          <p class="check-line" :class="'tone-'+checkLine(resolution).tone">{{checkLine(resolution).text}}</p>
          <details class="detail-box"><summary>详情</summary>
            <p>赛事终态：<template v-if="resolution?.score_verified">比赛已结束，外部赛果已核实 {{resolution.home_score}} : {{resolution.away_score}}</template><template v-else-if="resolution?.game_finished">比赛已结束，但外部赛果尚未绑定核实</template><template v-else>比赛尚未确认结束（赛事状态 {{resolution?.game_status ?? '未知'}}）</template></p>
            <p>结果一 · 按赛果判断：<template v-if="resolution?.result_judgment?.state==='judged'">{{resolution.result_judgment.outcome_won ? '选项获胜' : '选项未获胜'}} · 每份 {{resolution.result_judgment.payout_per_share}} pUSD · 推算应结算 {{resolution.settlement?.result_settlement_amount ?? '未知'}} pUSD</template><template v-else>无法判断（{{resolution?.result_judgment?.basis ?? '赛果未确认'}}）</template></p>
            <p v-if="resolution?.result_judgment?.state==='judged'" class="muted">判断依据：{{resolution.result_judgment.basis}}</p>
            <p>结果二 · Polymarket 终态结算：<template v-if="resolution?.state==='resolved'">{{resolution.outcome_won ? '选项获胜' : '选项未获胜'}} · 每份 {{resolution.outcome_won ? '1' : '0'}} pUSD · 应结算 {{resolution.settlement?.market_settlement_amount ?? '未知'}} pUSD</template><template v-else>暂无市场结果（{{resolution?.market_status_text ?? resolution?.market_error ?? (resolution?.state==='unavailable' ? '结算上游暂不可用' : '市场尚未进入终态')}}）</template></p>
            <p v-if="resolution?.state==='resolved'" class="muted">Polymarket 结算状态：已最终结算<template v-if="resolution?.resolved_at"> · {{new Date(resolution.resolved_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}} UTC+8</template></p>
            <p v-else-if="resolution?.market_status" class="muted">Polymarket 结算阶段：{{resolution.market_status}} · {{resolution.market_status_text}}</p>
            <p>两个结果核对：{{resolution?.settlement?.agreement==='consistent' ? '一致，结算金额已确认' : resolution?.settlement?.agreement==='conflict' ? '不一致，请人工核实，暂不确认金额' : resolution?.settlement?.agreement==='market_only' ? '仅有市场结果，按市场终态结算，建议人工核实' : resolution?.settlement?.agreement==='result_only' ? '仅有赛果判断，市场终态未确认，金额仅为预计值' : '两个结果均不可用，暂不能确认金额'}}</p>
            <p v-if="resolution?.settlement?.settlement_amount!=null">结算金额：<strong>{{resolution.settlement.settlement_amount}} pUSD</strong> · 成交成本：{{resolution.settlement.cost_basis ?? '待核实'}} · 已实现盈亏：{{resolution.settlement.realized_pnl ?? '待核实'}} · 已核实成交 {{resolution.settlement.covered_shares ?? '未知'}} 份</p>
            <p v-if="resolution?.settlement?.reason">{{resolution.settlement.reason}}</p>
            <p v-if="resolution?.settlement?.notice" class="muted">{{resolution.settlement.notice}}</p>
          </details>
        </div>
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
        <h3 class="card-title"><span>组合{{combo.direction==='BUY'?'买入':'卖出'}} · {{comboState(combo.state)}}</span><span class="pnl-badge" :class="pnlBadge(combo).cls" :title="pnlBadge(combo).hint">{{pnlBadge(combo).text}}</span></h3>
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
              <p class="detail-section-title">逐腿信息</p>
              <div class="mini-card-grid">
                <div v-for="leg in combo.legs" :key="leg.outcome_id" class="mini-card">
                  <div class="mini-card-head"><strong>{{leg.home}} 对 {{leg.away}}</strong><span v-if="resultBadge(leg.result_won)" class="result-badge" :class="resultBadge(leg.result_won)?.cls">{{resultBadge(leg.result_won)?.text}}</span><span v-else class="mini-tag mini-tag-neutral">无法判断</span></div>
                  <p class="mini-card-meta">{{leg.market_question}} · {{leg.outcome_label}}</p>
                  <div class="detail-rows">
                    <div class="detail-row"><span class="detail-label">比赛时间：</span>{{new Date(leg.starts_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}} UTC+8</div>
                    <div class="detail-row"><span class="detail-label">比分追踪：</span>{{watchName(leg.watch_state)}} · 外部赛果：{{bindingName(leg.binding_state)}}<span v-if="leg.home_score != null && leg.away_score != null"> · 当前比分：{{leg.home_score}} : {{leg.away_score}}</span><span v-else> · 比分待查询</span></div>
                    <div v-if="leg.next_poll_at" class="detail-row"><span class="detail-label">下次查询：</span>{{new Date(leg.next_poll_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}} UTC+8</div>
                    <div class="detail-row" :title="leg.result_basis ?? ''"><span class="detail-label">赛果判断：</span>{{legWinner(leg.result_won,'无法判断')}}</div>
                  </div>
                  <RouterLink v-if="leg.sport==='football' || leg.sport==='nba'" class="text-button" :to="'/sports/'+leg.sport">前往赛事中心核对</RouterLink>
                </div>
              </div>
              <p class="detail-section-title">买入与报价</p>
              <div class="mini-card">
                <div class="detail-rows">
                  <div class="detail-row"><span class="detail-label">金额来源：</span>{{buySource(combo)}}</div>
                  <div class="detail-row"><span class="detail-label">预算 / 份额参数：</span>{{combo.quantity}}（BUY 为预算，不等于份额）</div>
                  <div v-if="combo.quote" class="detail-row"><span class="detail-label">签名报价：</span>每份 {{combo.quote.price}} · 报价所需 {{combo.quote.total_required}} · 预计{{combo.direction==='BUY'?'份额':'收入'}} {{combo.quote.net_receive ?? '未知'}}</div>
                  <div v-if="combo.fill" class="detail-row"><span class="detail-label">成交明细：</span>支出 {{combo.fill.cost}} · 份额 {{combo.fill.size}} · {{combo.fill.source==='signed_order'?'签名接受订单':'按签名报价重建'}}<span v-if="combo.fill.confirmed_at"> · {{new Date(combo.fill.confirmed_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}} UTC+8</span></div>
                </div>
              </div>
              <p class="detail-section-title">标识与状态</p>
              <div class="mini-card">
                <div class="detail-rows">
                  <div class="detail-row id-text"><span class="detail-label">钱包：</span>{{combo.wallet_address}}</div>
                  <div class="detail-row id-text"><span class="detail-label">组合 RFQ：</span>{{combo.external_rfq_id || '未知'}}</div>
                  <div v-if="combo.tx_hash" class="detail-row id-text"><span class="detail-label">交易哈希：</span>{{combo.tx_hash}}</div>
                  <div v-if="combo.error_code" class="detail-row"><span class="detail-label">状态说明：</span>{{combo.error_code}}</div>
                </div>
              </div>
            </details>
            <button v-if="['requesting','quoted','accepting','executing','unknown'].includes(combo.state)" class="text-button" @click="retryComboReconcile(combo.id)">重新安排对账</button>
            <RouterLink to="/combos">查看组合记录</RouterLink>
          </div></details>
        </div>
        <p v-if="redeemErrors[redeemKey('combo',combo.id)]" class="redeem-error" role="alert">{{redeemErrors[redeemKey('combo',combo.id)]}}</p>

        <div v-if="comboResolutionId===combo.id" class="resolution-block">
          <p class="check-line" :class="comboResolution.agreement==='consistent'?'tone-ok':comboResolution.agreement==='conflict'?'tone-warn':'tone-info'">{{comboAgreement(comboResolution.agreement)}}</p>
          <details class="detail-box"><summary>详情</summary>
            <p class="detail-section-title">赛事终态核对：共 {{comboResolution.leg_count}} 腿 · 已按赛果判断 {{comboResolution.judged_legs}} 腿 · 已有市场终态 {{comboResolution.resolved_legs}} 腿</p>
            <div class="mini-card-grid">
              <div v-for="leg in comboResolution.legs" :key="leg.leg_no" class="mini-card">
                <div class="mini-card-head"><strong>腿 {{leg.leg_no}} · {{leg.home}} 对 {{leg.away}}</strong><span v-if="resultBadge(leg.result_won)" class="result-badge" :class="resultBadge(leg.result_won)?.cls">{{resultBadge(leg.result_won)?.text}}</span><span v-else class="mini-tag mini-tag-neutral">无法判断</span></div>
                <p class="mini-card-meta">{{leg.market_question}} · {{leg.outcome_label}}<span v-if="leg.home_score != null && leg.away_score != null"> · {{leg.home_score}} : {{leg.away_score}}</span></p>
                <div class="detail-rows">
                  <div class="detail-row"><span class="detail-label">赛事终态：</span><template v-if="leg.score_verified">比赛已结束，外部赛果已核实 {{leg.home_score}} : {{leg.away_score}}</template><template v-else>比赛尚未确认结束或外部赛果未核实（{{leg.game_status}}）</template></div>
                  <div class="detail-row"><span class="detail-label">结果一 · 按赛果判断：</span>{{legWinner(leg.result_won,'无法判断')}}</div>
                  <div class="detail-row detail-note"><span class="detail-label">判断依据：</span>{{leg.result_judgment?.basis ?? '赛果未确认'}}</div>
                  <div class="detail-row"><span class="detail-label">结果二 · 该腿 Polymarket 终态结算：</span><template v-if="leg.market?.state==='resolved'">{{legWinner(leg.market_won,'暂无终态结果')}} · 每份 {{leg.market_won?'1':'0'}} pUSD · 已最终结算<template v-if="leg.market.resolved_at"> · {{new Date(leg.market.resolved_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}} UTC+8</template></template><template v-else>暂无终态结果（{{leg.market?.market_status_text ?? leg.market?.market_error ?? '结算流程尚未完成'}}）<template v-if="leg.market?.market_status"> · 阶段 {{leg.market.market_status}}</template></template></div>
                  <div class="detail-row"><span class="detail-label">单腿核对：</span>{{legCheck(leg.leg_agreement)}}</div>
                </div>
              </div>
            </div>
            <p class="detail-section-title">组合结论</p>
            <div class="mini-card">
              <div class="detail-rows">
                <div class="detail-row"><span class="detail-label">组合结果一 · 按赛果判断：</span>{{comboWinner(comboResolution.by_score)}}</div>
                <div class="detail-row"><span class="detail-label">组合结果二 · 各腿 Polymarket 终态结算：</span>{{comboWinner(comboResolution.by_market)}}</div>
                <div class="detail-row"><span class="detail-label">两个结果核对：</span>{{comboAgreement(comboResolution.agreement)}}</div>
                <div v-if="comboResolution.reason" class="detail-row"><span class="detail-label">核对说明：</span>{{comboResolution.reason}}</div>
                <div class="detail-row"><span class="detail-label">组合结算金额：</span>该只读核对不计算结算金额；卡片盈亏来自已落库成交明细，实际赔付以 Polymarket 组合仓位终态为准。</div>
              </div>
            </div>
            <p class="detail-section-title">口径说明</p>
            <div class="mini-card mini-card-note"><p>{{comboResolution.notice}}</p></div>
          </details>
        </div>
      </article>
      <button v-if="comboCursor" class="text-button" :disabled="comboBusy" @click="loadCombos(true).then(loadRedeemEligibility)">加载更多组合</button>
    </section>
    </div>
  </main>
</div>
</template>
