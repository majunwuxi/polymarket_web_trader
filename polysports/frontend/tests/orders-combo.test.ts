import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import Orders from '../src/views/Orders.vue'

// 模拟本地接口，绝不访问交易服务。
const json=(body:unknown,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body}) as Response
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:Orders}]})
const combo={id:'mock-rfq',state:'confirmed',direction:'BUY',quantity:'1',wallet_address:'mock-wallet',external_rfq_id:'mock-external',tx_hash:'mock-tx',quote:{price:'0.5',total_required:'1',net_receive:'2'},legs:[{outcome_id:'1',home:'模拟主队',away:'模拟客队',market_question:'模拟胜负',outcome_label:'模拟主队',starts_at:'2026-11-15T00:00:00Z',sport:'nba',watch_state:'active',binding_state:'bound',home_score:100,away_score:99}]}
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
    expect(fetchMock.mock.calls.some(([url])=>url.includes('state=confirmed'))).toBe(true)
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
    const view=await openResolution({state:'resolved',score_verified:true,home_score:100,away_score:90,outcome_won:true,matched_size:'3',side:'BUY',result_judgment:resultJudgment,settlement})
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
    const view=await openResolution({state:'resolved',score_verified:true,home_score:100,away_score:90,outcome_won:true,matched_size:'3',side:'BUY',result_judgment:{...resultJudgment,outcome_won:false,payout_per_share:'0'},settlement:conflict})
    expect(view.text()).toContain('不一致，请人工核实')
    expect(view.text()).toContain('推算应结算 0 pUSD')
    expect(view.text()).toContain('应结算 3 pUSD')
    expect(view.text()).not.toContain('结算金额：')
    view.unmount()
  })
  it('市场尚未终态时仅显示按赛果推算的预计金额，不当作已确认结算',async()=>{
    const provisional={...settlement,agreement:'result_only',settlement_amount:null,market_settlement_amount:null,market_outcome_won:null,cost_basis:null,realized_pnl:null,covered_shares:null,cost_verified:false,reason:'市场尚未进入终态；按赛果推算的金额仅为预计值，待 Polymarket 结算确认'}
    const view=await openResolution({state:'pending',score_verified:true,home_score:100,away_score:90,matched_size:'3',side:'BUY',result_judgment:resultJudgment,settlement:provisional})
    expect(view.text()).toContain('市场尚未进入终态')
    expect(view.text()).toContain('推算应结算 3 pUSD')
    expect(view.text()).toContain('金额仅为预计值')
    expect(view.text()).not.toContain('结算金额：')
    view.unmount()
  })
  it('大小分市场按总分判断输赢并显示计算依据，不再显示无法判断',async()=>{
    const provisional={...settlement,agreement:'result_only',settlement_amount:null,market_settlement_amount:null,market_outcome_won:null,result_settlement_amount:'0',result_outcome_won:false,cost_basis:null,realized_pnl:null,covered_shares:null,cost_verified:false,reason:'市场尚未进入终态；按赛果推算的金额仅为预计值，待 Polymarket 结算确认'}
    const judged={state:'judged',outcome_won:false,payout_per_share:'0',basis:'全场总分 127+117=244 高于 盘口 227.5，Under未获胜'}
    const view=await openResolution({state:'pending',score_verified:true,home_score:127,away_score:117,matched_size:'5.12',side:'BUY',result_judgment:judged,settlement:provisional})
    expect(view.text()).toContain('选项未获胜')
    expect(view.text()).toContain('判断依据：全场总分 127+117=244')
    expect(view.text()).toContain('推算应结算 0 pUSD')
    expect(view.text()).not.toContain('无法判断')
    view.unmount()
  })
})
