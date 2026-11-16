import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import Orders from '../src/views/Orders.vue'

// 模拟本地接口，绝不访问交易服务。
const json=(body:unknown,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body}) as Response
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:Orders}]})
const combo={id:'mock-rfq',state:'confirmed',direction:'BUY',quantity:'1',wallet_address:'mock-wallet',external_rfq_id:'mock-external',tx_hash:'mock-tx',by_score:true,judged_legs:1,leg_count:1,score_reason:null,quote:{price:'0.5',total_required:'1',net_receive:'2'},buy_summary:{size:'5.12',price:'0.41',cost:'2.0992',basis:'combo_fill',estimated:false,source:'signed_order',detail:'份额与金额取自已确认组合成交明细'},legs:[{outcome_id:'1',home:'模拟主队',away:'模拟客队',market_question:'模拟胜负',outcome_label:'模拟主队',starts_at:'2026-11-15T00:00:00Z',sport:'nba',watch_state:'active',binding_state:'bound',home_score:100,away_score:99,result_won:true,result_basis:'胜负命题：模拟主队 100:99 模拟客队，模拟主队 获胜'}]}
beforeEach(async()=>{await router.push('/');await router.isReady()})
afterEach(()=>vi.unstubAllGlobals())

describe('交易记录中的组合（模拟接口）',()=>{
  it('显示已确认组合和各腿，并保留单场区域',async()=>{
    const fetchMock=vi.fn((url:string)=>Promise.resolve(url.includes('/combo-rfqs')?json({data:{items:[combo],next_cursor:null}}):json({data:{items:[],next_cursor:null}})))
    vi.stubGlobal('fetch',fetchMock)
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    expect(view.text()).toContain('单场订单')
    expect(view.get('[aria-label="组合交易记录"]').text()).toContain('组合买入 · 已确认')
    expect(view.text()).toContain('模拟主队 对 模拟客队')
    expect(view.text()).toContain('mock-external')
    expect(view.text()).toContain('比分追踪：已关注 · 外部赛果：已绑定 · 当前比分：100 : 99')
    expect(view.text()).toContain('赛果判断：选项获胜')
    const resultBadge=view.get('[aria-label="组合交易记录"] .result-badge.result-win')
    expect(resultBadge.text()).toBe('选项获胜')
    expect(view.text()).toContain('组合结果：获胜 · 1/1 腿')
    expect(fetchMock.mock.calls.some(([url])=>url.includes('state=confirmed'))).toBe(true)
    view.unmount()
  })
  it('未完成组合可手动重新安排对账',async()=>{
    const retryCombo={...combo,state:'unknown'}
    const fetchMock=vi.fn((url:string)=>Promise.resolve(url.includes('/combo-rfqs')?json({data:{items:[retryCombo],next_cursor:null}}):json({data:{items:[],next_cursor:null}})))
    vi.stubGlobal('fetch',fetchMock)
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const retry=view.findAll('button').find(button=>button.text()==='重新安排对账')
    expect(retry).toBeTruthy()
    await retry!.trigger('click');await flushPromises()
    const request=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/combo-rfqs/mock-rfq/reconcile'))
    expect(request).toBeTruthy()
    expect((request![1] as RequestInit).method).toBe('POST')
    view.unmount()
  })
  it('组合请求失败单独显示错误，不显示空记录',async()=>{
    vi.stubGlobal('fetch',vi.fn((url:string)=>Promise.resolve(url.includes('/combo-rfqs')?json({error:{}},503):json({data:{items:[],next_cursor:null}}))))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    expect(view.get('[aria-label="组合交易记录"]').text()).toContain('组合记录加载失败')
    expect(view.get('[aria-label="组合交易记录"]').text()).not.toContain('暂无已确认组合交易')
    expect(view.text()).toContain('暂无已记录订单')
    view.unmount()
  })
  it('组合分页不改变单场列表',async()=>{
    vi.stubGlobal('fetch',vi.fn((url:string)=>Promise.resolve(url.includes('/combo-rfqs')?json({data:url.includes('cursor=')?{items:[{...combo,id:'mock-second'}],next_cursor:null}:{items:[combo],next_cursor:'mock-cursor'}}):json({data:{items:[],next_cursor:null}}))))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    await view.findAll('button').find(button=>button.text()==='加载更多组合')!.trigger('click');await flushPromises()
    expect(view.get('[aria-label="组合交易记录"]').findAll('article')).toHaveLength(2)
    expect(view.text()).toContain('暂无已记录订单')
    view.unmount()
  })
})

const order={id:'mock-order',side:'BUY',state:'filled',limit_price:'0.4',size:'3',matched_size:'3',external_order_id:'mock-external',home:'模拟主队',away:'模拟客队',market_question:'模拟胜负',outcome_label:'模拟主队',market_link_id:'mock-link',outcome_id:'mock-outcome'}
const settlement={settlement_amount:'3',market_settlement_amount:'3',result_settlement_amount:'3',market_outcome_won:true,result_outcome_won:true,agreement:'consistent',cost_basis:'1.2',realized_pnl:'1.8',covered_shares:'3',confirmed_fill_count:1,cost_verified:true,currency:'pUSD',reason:null,notice:'赛果判断与 Polymarket 终态结算一致；按市场终态赔付与已确认成交份额计算，不等于链上赎回或实际到账。'}
const resultJudgment={state:'judged',outcome_won:true,payout_per_share:'1',basis:'已绑定外部赛果比分 + 严格识别的市场胜负命题'}
function resolutionFetch(payload:unknown){return vi.fn((url:string)=>{
  if(url.includes('/resolution'))return Promise.resolve(json({data:payload}))
  if(url.includes('/combo-rfqs'))return Promise.resolve(json({data:{items:[],next_cursor:null}}))
  return Promise.resolve(json({data:{items:[order],next_cursor:null}}))
})}
async function openResolution(payload:unknown){vi.stubGlobal('fetch',resolutionFetch(payload));const view=mount(Orders,{global:{plugins:[router]}});await flushPromises();await view.findAll('button').find(button=>button.text()==='核对赛果和市场结算')!.trigger('click');await flushPromises();return view}

describe('交易记录中的两个结算结果（模拟接口）',()=>{
  it('赛果判断与市场结算一致时确认结算金额、成本与盈亏',async()=>{
    const view=await openResolution({state:'resolved',score_verified:true,game_finished:true,game_status:'finished',home_score:100,away_score:90,outcome_won:true,matched_size:'3',side:'BUY',result_judgment:resultJudgment,settlement})
    expect(view.text()).toContain('结果一 · 按赛果判断')
    expect(view.text()).toContain('结果二 · Polymarket 终态结算')
    expect(view.text()).toContain('两个结果核对：一致，结算金额已确认')
    expect(view.text()).toContain('结算金额：3 pUSD')
    expect(view.text()).toContain('成交成本：1.2')
    expect(view.text()).toContain('已实现盈亏：1.8')
    expect(view.text()).toContain('不等于链上赎回或实际到账')
    view.unmount()
  })
  it('两个结果冲突时并列展示金额但不确认结算金额',async()=>{
    const conflict={...settlement,agreement:'conflict',settlement_amount:null,result_outcome_won:false,result_settlement_amount:'0',cost_basis:null,realized_pnl:null,covered_shares:null,cost_verified:false,reason:'两个结算结果不一致，需人工核实，不确认结算金额'}
    const view=await openResolution({state:'resolved',score_verified:true,game_finished:true,game_status:'finished',home_score:100,away_score:90,outcome_won:true,matched_size:'3',side:'BUY',result_judgment:{...resultJudgment,outcome_won:false,payout_per_share:'0'},settlement:conflict})
    expect(view.text()).toContain('不一致，请人工核实')
    expect(view.text()).toContain('推算应结算 0 pUSD')
    expect(view.text()).toContain('应结算 3 pUSD')
    expect(view.text()).not.toContain('结算金额：')
    view.unmount()
  })
  it('市场尚未终态时仅显示按赛果推算的预计金额，不当作已确认结算',async()=>{
    const provisional={...settlement,agreement:'result_only',settlement_amount:null,market_settlement_amount:null,market_outcome_won:null,cost_basis:null,realized_pnl:null,covered_shares:null,cost_verified:false,reason:'市场尚未进入终态；按赛果推算的金额仅为预计值，待 Polymarket 结算确认'}
    const view=await openResolution({state:'pending',score_verified:true,game_finished:true,game_status:'finished',home_score:100,away_score:90,matched_size:'3',side:'BUY',result_judgment:resultJudgment,settlement:provisional})
    expect(view.text()).toContain('市场尚未进入终态')
    expect(view.text()).toContain('推算应结算 3 pUSD')
    expect(view.text()).toContain('金额仅为预计值')
    expect(view.text()).not.toContain('结算金额：')
    view.unmount()
  })
  it('大小分市场按总分判断输赢并显示计算依据，不再显示无法判断',async()=>{
    const provisional={...settlement,agreement:'result_only',settlement_amount:null,market_settlement_amount:null,market_outcome_won:null,result_settlement_amount:'0',result_outcome_won:false,cost_basis:null,realized_pnl:null,covered_shares:null,cost_verified:false,reason:'市场尚未进入终态；按赛果推算的金额仅为预计值，待 Polymarket 结算确认'}
    const judged={state:'judged',outcome_won:false,payout_per_share:'0',basis:'全场总分 127+117=244 高于 盘口 227.5，Under未获胜'}
    const view=await openResolution({state:'pending',score_verified:true,game_finished:true,game_status:'finished',home_score:127,away_score:117,matched_size:'5.12',side:'BUY',result_judgment:judged,settlement:provisional})
    expect(view.text()).toContain('选项未获胜')
    expect(view.text()).toContain('判断依据：全场总分 127+117=244')
    expect(view.text()).toContain('推算应结算 0 pUSD')
    expect(view.text()).not.toContain('无法判断')
    view.unmount()
  })
  it('赛事终态与 Polymarket 结算分别确认，市场已最终结算时确认结算金额',async()=>{
    const confirmed={...settlement,agreement:'consistent',settlement_amount:'0',market_settlement_amount:'0',result_settlement_amount:'0',market_outcome_won:false,result_outcome_won:false,cost_basis:'2.0992',realized_pnl:'-2.0992',covered_shares:'5.12',confirmed_fill_count:1,cost_verified:true,reason:null}
    const judged={state:'judged',outcome_won:false,payout_per_share:'0',basis:'全场总分 127+117=244 高于 盘口 227.5，Under未获胜'}
    const view=await openResolution({state:'resolved',score_verified:true,game_finished:true,game_status:'finished',home_score:127,away_score:117,outcome_won:false,resolved_at:'2026-11-15T02:00:00Z',market_status:'resolved',market_status_text:'已最终结算',payouts:[1000000,0],matched_size:'5.12',side:'BUY',result_judgment:judged,settlement:confirmed})
    expect(view.text()).toContain('赛事终态：比赛已结束，外部赛果已核实 127 : 117')
    expect(view.text()).toContain('Polymarket 结算状态：已最终结算')
    expect(view.text()).toContain('两个结果核对：一致，结算金额已确认')
    expect(view.text()).toContain('结算金额：0 pUSD')
    view.unmount()
  })
  it('市场结算流程中显示具体阶段，赛事终态单独确认',async()=>{
    const provisional={...settlement,agreement:'result_only',settlement_amount:null,market_settlement_amount:null,market_outcome_won:null,result_settlement_amount:'0',result_outcome_won:false,cost_basis:null,realized_pnl:null,covered_shares:null,cost_verified:false,reason:'市场尚未进入终态；按赛果推算的金额仅为预计值，待 Polymarket 结算确认'}
    const judged={state:'judged',outcome_won:false,payout_per_share:'0',basis:'全场总分 127+117=244 高于 盘口 227.5，Under未获胜'}
    const view=await openResolution({state:'pending',score_verified:true,game_finished:true,game_status:'finished',home_score:127,away_score:117,matched_size:'5.12',side:'BUY',market_status:'proposed',market_status_text:'已提议结果，处于无争议期，尚未最终结算',result_judgment:judged,settlement:provisional})
    expect(view.text()).toContain('赛事终态：比赛已结束')
    expect(view.text()).toContain('Polymarket 结算阶段：proposed')
    expect(view.text()).toContain('已提议结果，处于无争议期，尚未最终结算')
    view.unmount()
  })
})

const comboLegOne={leg_no:1,home:'Grizzlies',away:'Nets',market_question:'Nets vs. Grizzlies',outcome_label:'Grizzlies',home_score:129,away_score:110,score_verified:true,game_status:'finished',result_judgment:{state:'judged',outcome_won:true,basis:'胜负命题：Grizzlies 129:110 Nets，Grizzlies 获胜'},market:{state:'resolved',outcome_won:true,market_status:'resolved',market_status_text:'已最终结算',resolved_at:'2026-11-15T02:00:00Z'},result_won:true,market_won:true,leg_agreement:'consistent'}
const comboLegTwo={leg_no:2,home:'Timberwolves',away:'Bulls',market_question:'Bulls vs. Timberwolves: O/U 244.5',outcome_label:'Under',home_score:106,away_score:117,score_verified:true,game_status:'finished',result_judgment:{state:'judged',outcome_won:true,basis:'全场总分 106+117=223 低于 盘口 244.5，Under获胜'},market:{state:'resolved',outcome_won:true,market_status:'resolved',market_status_text:'已最终结算',resolved_at:'2026-11-15T02:10:00Z'},result_won:true,market_won:true,leg_agreement:'consistent'}
const comboResolutionPayload={id:'mock-rfq',direction:'BUY',state:'confirmed',leg_count:2,judged_legs:2,resolved_legs:2,by_score:true,by_market:true,agreement:'consistent',reason:'两个结果一致确认组合获胜',notice:'组合按全中口径核对：全部腿命中才算获胜；该只读核对不计算组合结算金额，卡片盈亏来自已落库成交明细，实际赔付以 Polymarket 组合仓位终态为准。',legs:[comboLegOne,comboLegTwo]}
function comboResolutionFetch(payload:unknown){return vi.fn((url:string)=>{
  if(url.includes('/combo-rfqs/mock-rfq/resolution'))return Promise.resolve(json({data:payload}))
  if(url.includes('/combo-rfqs'))return Promise.resolve(json({data:{items:[combo],next_cursor:null}}))
  return Promise.resolve(json({data:{items:[],next_cursor:null}}))
})}
async function openComboResolution(payload:unknown){vi.stubGlobal('fetch',comboResolutionFetch(payload));const view=mount(Orders,{global:{plugins:[router]}});await flushPromises();await view.findAll('button').find(button=>button.text()==='核对组合赛果和市场结算')!.trigger('click');await flushPromises();return view}

describe('组合交易记录的胜负核对（模拟接口）',()=>{
  it('逐腿显示赛事终态、两个结果与组合层一致结论，不虚构结算金额',async()=>{
    const view=await openComboResolution(comboResolutionPayload)
    const text=view.get('[aria-label="组合交易记录"]').text()
    expect(text).toContain('赛事终态核对：共 2 腿')
    expect(text).toContain('比赛已结束，外部赛果已核实 129 : 110')
    expect(text).toContain('判断依据：全场总分 106+117=223')
    expect(text).toContain('单腿核对：两个结果一致')
    expect(text).toContain('组合结果一 · 按赛果判断：全部腿命中，组合获胜')
    expect(text).toContain('组合结果二 · 各腿 Polymarket 终态结算：全部腿命中，组合获胜')
    expect(text).toContain('两个结果核对：一致，组合胜负已确认')
    expect(text).toContain('组合结算金额：该只读核对不计算结算金额')
    expect(text).not.toContain('无法判断')
    view.unmount()
  })
  it('仍有腿未进入市场终态时，组合结果只标为预计值并显示结算阶段',async()=>{
    const pending={...comboLegTwo,market:{state:'pending',outcome_won:null,market_status:'proposed',market_status_text:'已提议结果，处于无争议期，尚未最终结算',resolved_at:null},market_won:null,leg_agreement:'partial'}
    const payload={...comboResolutionPayload,resolved_legs:1,by_market:null,agreement:'result_only',reason:'各腿 Polymarket 终态尚未全部返回；按赛果推算的组合结果仅为预计值，待市场结算确认',legs:[comboLegOne,pending]}
    const view=await openComboResolution(payload)
    const text=view.get('[aria-label="组合交易记录"]').text()
    expect(text).toContain('阶段 proposed')
    expect(text).toContain('已提议结果，处于无争议期，尚未最终结算')
    expect(text).toContain('组合结果二 · 各腿 Polymarket 终态结算：暂不能确认组合胜负')
    expect(text).toContain('两个结果核对：仅有赛果判断，市场终态未齐，组合结果仅为预计值')
    expect(text).toContain('单腿核对：仅有一个结果，暂不确认')
    view.unmount()
  })
  it('组合卖出明确标注不适用终态胜负核对',async()=>{
    const view=await openComboResolution({...comboResolutionPayload,direction:'SELL',agreement:'not_applicable',by_score:null,by_market:null,reason:'组合卖出按报价成交，不适用终态胜负核对'})
    expect(view.get('[aria-label="组合交易记录"]').text()).toContain('组合卖出不适用终态胜负核对')
    view.unmount()
  })
})

const longId='0x1ba2d214a058182d02120801075244461aaf2784ea80901668aebbe12fc2a47c'
const compactOrder={...order,market_question:'Mavericks vs. Cavaliers: O/U 227.5',outcome_label:'Under',external_order_id:longId}
function compactFetch(rows:unknown[]){return vi.fn((url:string)=>{
  if(url.includes('/resolution'))return Promise.resolve(json({data:{state:'resolved',score_verified:true,game_finished:true,game_status:'finished',home_score:127,away_score:117,outcome_won:false,matched_size:'3',side:'BUY',result_judgment:{state:'judged',outcome_won:false,payout_per_share:'0',basis:'全场总分 127+117=244 高于 盘口 227.5，Under未获胜'},settlement}}))
  if(url.includes('/combo-rfqs'))return Promise.resolve(json({data:{items:[],next_cursor:null}}))
  return Promise.resolve(json({data:{items:rows,next_cursor:null}}))
})}

describe('交易记录精简显示与平仓准入（模拟接口）',()=>{
  it('摘要去掉重复队名与完整订单编号，不重复显示份额',async()=>{
    vi.stubGlobal('fetch',compactFetch([compactOrder]))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const card=view.get('.order-card')
    expect(card.text()).toContain('O/U 227.5 · Under')
    expect(card.text()).not.toContain('Mavericks vs. Cavaliers:')
    expect(card.text()).toContain('0x1ba2d214…c2a47c')
    expect(card.text()).not.toContain('交易所订单 ID：')
    expect(card.text()).toContain('限价 0.4 · 3 份')
    expect(card.text()).not.toContain('已成交 3')
    expect(card.find('details.detail-box').exists()).toBe(false)
    view.unmount()
  })
  it('市场已最终结算后，核对结论只占一行且立即平仓自动失效',async()=>{
    vi.stubGlobal('fetch',compactFetch([compactOrder]))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    expect(view.findAll('a').some(a=>a.text()==='立即平仓')).toBe(true)
    await view.findAll('button').find(b=>b.text()==='核对赛果和市场结算')!.trigger('click');await flushPromises()
    expect(view.text()).toContain('✓ 两个结果一致 · 选项未获胜 · 结算 3 pUSD')
    expect(view.find('details.detail-box').attributes('open')).toBeUndefined()
    expect(view.text()).toContain('判断依据：全场总分 127+117=244')
    const disabled=view.findAll('button').find(b=>b.text()==='已结算 · 不可平仓')!
    expect(disabled.attributes('disabled')).toBeDefined()
    expect(disabled.attributes('title')).toContain('不能平仓')
    expect(view.findAll('a').some(a=>a.text()==='立即平仓')).toBe(false)
    view.unmount()
  })
  it('列表返回赛果已确认时，无需核对即禁用平仓并显示黑色失败徽标',async()=>{
    vi.stubGlobal('fetch',compactFetch([{...compactOrder,close_blocked:true,close_block_reason:'比赛结果已确认，持仓已进入结算，不能平仓',result_won:false}]))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    expect(view.text()).toContain('买入 · 全部成交')
    const badge=view.get('.result-badge.result-loss')
    expect(badge.text()).toBe('选项失败')
    expect(view.text()).toContain('已结算 · 不可平仓')
    expect(view.findAll('a').some(a=>a.text()==='立即平仓')).toBe(false)
    view.unmount()
  })
  it('组合失败腿使用黑色选项失败徽标',async()=>{
    const failedLeg={...combo.legs[0],outcome_id:'failed',home:'Mock Loss',away:'Mock Win',result_won:false}
    const failedCombo={...combo,by_score:false,legs:[failedLeg]}
    vi.stubGlobal('fetch',vi.fn((url:string)=>Promise.resolve(url.includes('/combo-rfqs')?json({data:{items:[failedCombo],next_cursor:null}}):json({data:{items:[],next_cursor:null}}))))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const badge=view.get('[aria-label="组合交易记录"] .result-badge.result-loss')
    expect(badge.text()).toBe('选项失败')
    view.unmount()
  })
})

const cardLoss={amount:'2.0992',tone:'loss',label:'约亏损 2.0992 pUSD',basis:'limit_estimate',estimated:true,detail:'成本按限价 0.41 × 5.12 份估算',cost:'2.0992',payout:'0'}
const cardProfit={amount:'3.072',tone:'profit',label:'盈利 3.072 pUSD',basis:'confirmed_fills',estimated:false,detail:'CONFIRMED 成交明细已覆盖 5.12 份',cost:'2.048',payout:'5.12'}
function comboWithPnl(p:unknown){return vi.fn((url:string)=>{
  if(url.includes('/combo-rfqs'))return Promise.resolve(json({data:{items:[{...combo,pnl:p}],next_cursor:null}}))
  return Promise.resolve(json({data:{items:[],next_cursor:null}}))
})}

describe('订单卡片直接标注盈亏金额（模拟接口）',()=>{
  it('单场亏损使用黑底白字徽标并直接写出金额',async()=>{
    vi.stubGlobal('fetch',compactFetch([{...compactOrder,pnl:cardLoss}]))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const badge=view.find('.pnl-badge')
    expect(badge.classes()).toContain('pnl-loss')
    expect(badge.text()).toContain('亏损 2.0992 pUSD')
    expect(badge.attributes('title')).toContain('限价')
    view.unmount()
  })
  it('单场盈利使用红底白字徽标，已核实成本不加「约」',async()=>{
    vi.stubGlobal('fetch',compactFetch([{...compactOrder,pnl:cardProfit}]))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const badge=view.find('.pnl-badge')
    expect(badge.classes()).toContain('pnl-profit')
    expect(badge.text()).toContain('盈利 3.072 pUSD')
    expect(badge.text()).not.toContain('约盈利')
    view.unmount()
  })
  it('组合卡片也直接标注盈亏金额并注明按报价估算',async()=>{
    vi.stubGlobal('fetch',comboWithPnl({...cardProfit,label:'约盈利 1 pUSD',amount:'1',basis:'quote_estimate',estimated:true,detail:'组合未落库成交明细，按已确认报价估算'}))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const badge=view.get('[aria-label="组合交易记录"]').find('.pnl-badge')
    expect(badge.classes()).toContain('pnl-profit')
    expect(badge.text()).toContain('约盈利 1 pUSD')
    expect(badge.attributes('title')).toContain('报价')
    view.unmount()
  })
  it('组合有 CONFIRMED 成交明细时，亏损金额不带「约」并用黑底白字',async()=>{
    vi.stubGlobal('fetch',comboWithPnl({amount:'2.0992',tone:'loss',label:'亏损 2.0992 pUSD',basis:'combo_fill',estimated:false,detail:'成交成本 2.0992 pUSD（官方签名报价按 micro-USDC 精度重建的成交金额）· 实际份额 5.12 份',cost:'2.0992',payout:'0'}))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const badge=view.get('[aria-label="组合交易记录"]').find('.pnl-badge')
    expect(badge.classes()).toContain('pnl-loss')
    expect(badge.text()).toBe('亏损 2.0992 pUSD')
    expect(badge.text()).not.toContain('约')
    expect(badge.attributes('title')).toContain('实际份额')
    view.unmount()
  })
  it('组合有 CONFIRMED 成交明细时，盈利金额不带「约」并用红底白字',async()=>{
    vi.stubGlobal('fetch',comboWithPnl({amount:'3.62',tone:'profit',label:'盈利 3.62 pUSD',basis:'combo_fill',estimated:false,detail:'成交成本 1.5 pUSD（签名接受订单的 makerAmount/takerAmount）· 实际份额 5.12 份',cost:'1.5',payout:'5.12'}))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const badge=view.get('[aria-label="组合交易记录"]').find('.pnl-badge')
    expect(badge.classes()).toContain('pnl-profit')
    expect(badge.text()).toBe('盈利 3.62 pUSD')
    expect(badge.text()).not.toContain('约')
    view.unmount()
  })
  it('盈亏未确认时用中性样式，不误用黑底或红底',async()=>{
    vi.stubGlobal('fetch',compactFetch([compactOrder]))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const badge=view.find('.pnl-badge')
    expect(badge.classes()).toContain('pnl-pending')
    expect(badge.text()).toContain('盈亏待确认')
    expect(badge.classes()).not.toContain('pnl-loss')
    expect(badge.classes()).not.toContain('pnl-profit')
    view.unmount()
  })
})

describe('组合买入摘要与详情分层（模拟接口）',()=>{
  function comboRows(rows:unknown[]){return vi.fn((url:string)=>{
    if(url.includes('/combo-rfqs'))return Promise.resolve(json({data:{items:rows,next_cursor:null}}))
    return Promise.resolve(json({data:{items:[],next_cursor:null}}))
  })}
  it('卡片直接显示份额总额、买入价格与买入金额，不再把预算误标为份额',async()=>{
    vi.stubGlobal('fetch',comboRows([combo]))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const card=view.get('[aria-label="组合交易记录"]').find('.order-card')
    const metrics=card.findAll('.buy-metric')
    expect(metrics).toHaveLength(3)
    expect(metrics[0].text()).toContain('份额总额')
    expect(metrics[0].text()).toContain('5.12 份')
    expect(metrics[1].text()).toContain('买入价格')
    expect(metrics[1].text()).toContain('0.41 pUSD/份')
    expect(metrics[2].text()).toContain('买入金额')
    expect(metrics[2].text()).toContain('2.0992 pUSD')
    expect(metrics[2].text()).not.toContain('约')
    expect(card.findAll('.buy-metric-hint')).toHaveLength(0)
    expect(card.findAll('.buy-metric').every(metric=>metric.attributes('title')===undefined)).toBe(true)
    expect(card.find('.buy-note').exists()).toBe(false)
    expect(card.find('.summary-line').text()).not.toContain('1 份')
    expect(card.findAll('.leg-chip')).toHaveLength(1)
    view.unmount()
  })
  it('按报价重建时金额标「约」，缺数据时三项都写待核实',async()=>{
    vi.stubGlobal('fetch',comboRows([
      {...combo,buy_summary:{size:'2',price:'0.5',cost:'1',basis:'quote_rebuilt',estimated:true,source:'quote_rebuilt',detail:'按签名报价重建'}},
      {...combo,id:'mock-missing',buy_summary:{size:null,price:null,cost:null,basis:'unavailable',estimated:false,source:null,detail:'缺少可核实数据'}}
    ]))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const cards=view.get('[aria-label="组合交易记录"]').findAll('.order-card')
    expect(cards[0].findAll('.buy-metric')[2].text()).toContain('约1 pUSD')
    expect(cards[0].find('.buy-note').text()).toContain('金额按签名报价重建')
    expect(cards[0].findAll('.buy-metric-hint')).toHaveLength(0)
    expect(cards[1].findAll('.buy-metric').every(m=>m.text().includes('待核实'))).toBe(true)
    expect(cards[1].find('.buy-note').text()).toContain('缺少可核实数据')
    view.unmount()
  })
  it('组合卖出不显示买入摘要',async()=>{
    vi.stubGlobal('fetch',comboRows([{...combo,direction:'SELL'}]))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    expect(view.get('[aria-label="组合交易记录"]').find('.buy-metrics').exists()).toBe(false)
    view.unmount()
  })
  it('核对详情按逐腿 / 组合结论 / 口径说明分层，每腿各占一张小卡片',async()=>{
    const view=await openComboResolution(comboResolutionPayload)
    const detail=view.get('[aria-label="组合交易记录"]').find('.resolution-block .detail-box')
    const titles=detail.findAll('.detail-section-title').map(s=>s.text())
    expect(titles[0]).toContain('赛事终态核对：共 2 腿')
    expect(titles).toContain('组合结论')
    expect(titles).toContain('口径说明')
    expect(detail.findAll('.mini-card-grid .mini-card')).toHaveLength(2)
    expect(detail.findAll('.mini-card').length).toBeGreaterThanOrEqual(4)
    expect(detail.find('.mini-card-grid').text()).toContain('腿 1 · Grizzlies 对 Nets')
    expect(detail.findAll('.detail-row').length).toBeGreaterThanOrEqual(10)
    view.unmount()
  })
  it('逐腿与报价明细同样分层，并保留比分追踪与赛果判断证据',async()=>{
    vi.stubGlobal('fetch',comboRows([combo]))
    const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
    const more=view.get('[aria-label="组合交易记录"]').find('.more-actions')
    expect(more.findAll('.mini-card').length).toBeGreaterThanOrEqual(3)
    expect(more.text()).toContain('比分追踪：已关注 · 外部赛果：已绑定 · 当前比分：100 : 99')
    expect(more.text()).toContain('赛果判断：选项获胜')
    expect(more.text()).toContain('mock-external')
    view.unmount()
  })
})
