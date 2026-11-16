import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import Orders from '../src/views/Orders.vue'
import { session } from '../src/auth'

const json=(data:unknown,status=200)=>({ok:status===200,status,json:async()=>status===200?{data}:{error:{message:'模拟错误'}}}) as Response
const order={id:'order-1',side:'BUY',state:'filled',limit_price:'0.4',size:'3',matched_size:'3',external_order_id:'mock-external',home:'模拟主队',away:'模拟客队',market_question:'模拟胜负',outcome_label:'模拟主队',market_link_id:'mock-link',outcome_id:'mock-outcome'}
const combo={id:'combo-1',state:'confirmed',direction:'BUY',quantity:'1',wallet_address:'0x'+'1'.repeat(40),external_rfq_id:'mock-rfq',by_score:null,judged_legs:0,leg_count:1,legs:[{game_id:'game',sport:'nba',market_link_id:'link',outcome_id:'outcome',home:'组合主队',away:'组合客队',starts_at:'2026-11-16T00:00:00Z',market_question:'组合胜负',outcome_label:'组合主队',watch_state:'active',binding_state:'bound',result_won:null}],quote:null,fill:null,pnl:null,buy_summary:{basis:'unavailable'}}
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:Orders},{path:'/orders/new/:link/:outcome',component:Orders},{path:'/combos',component:Orders}]})

beforeEach(async()=>{session.csrf='mock-csrf';await router.push('/');await router.isReady();vi.stubGlobal('crypto',{randomUUID:()=>'00000000-0000-4000-8000-000000000001'})})
afterEach(()=>vi.unstubAllGlobals())

it('标记可赎回卡片并提交整仓赎回',async()=>{
  let redeemed=false
  const requests:Array<{url:string,options?:RequestInit}>=[]
  vi.stubGlobal('fetch',vi.fn((url:string,options?:RequestInit)=>{
    requests.push({url,options})
    if(url.endsWith('/redemptions/eligibility'))return Promise.resolve(json({execution_enabled:true,items:[
      {kind:'single',id:'order-1',eligible:!redeemed,state:redeemed?'confirmed':null,shares:'3',expected_payout:'3',reason:redeemed?'该整仓已经赎回':null},
      {kind:'combo',id:'combo-1',eligible:true,state:null,shares:'4',expected_payout:'4',reason:null},
    ]}))
    if(url.endsWith('/trade-intents/order-1/redeem')){
      redeemed=true
      return Promise.resolve(json({state:'confirmed',credited_amount:'3',collateral_balance:'13',credited:true,already_redeemed:false,message:'赎回已确认'}))
    }
    if(url.startsWith('/api/v1/trade-intents?'))return Promise.resolve(json({items:[order],next_cursor:null}))
    if(url.startsWith('/api/v1/combo-rfqs?'))return Promise.resolve(json({items:[combo],next_cursor:null}))
    return Promise.resolve(json([]))
  }))
  const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
  const cards=view.findAll('article.order-card')
  expect(cards).toHaveLength(2)
  expect(cards[0].classes()).toContain('is-redeemable')
  expect(cards[1].classes()).toContain('is-redeemable')
  expect(view.findAll('button').filter(button=>button.text()==='赎回')).toHaveLength(2)

  await view.findAll('button').find(button=>button.text()==='赎回')!.trigger('click');await flushPromises()
  const redeemRequest=requests.find(item=>item.url.endsWith('/trade-intents/order-1/redeem'))
  expect(redeemRequest?.options?.method).toBe('POST')
  const headers=redeemRequest?.options?.headers as Record<string,string>
  expect(headers['X-CSRF-Token']).toBe('mock-csrf')
  expect(headers['Idempotency-Key']).toBe('00000000-0000-4000-8000-000000000001')
  expect(view.get('[role="status"]').text()).toContain('现金余额增加 3 pUSD')
  expect(view.findAll('article.order-card')[0].classes()).not.toContain('is-redeemable')
  expect(view.findAll('article.order-card')[0].text()).toContain('已赎回')
  expect(view.findAll('button').filter(button=>button.text()==='赎回')).toHaveLength(1)
  view.unmount()
})

it('功能关闭时不显示赎回按钮或粉色卡片',async()=>{
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/redemptions/eligibility'))return Promise.resolve(json({execution_enabled:false,items:[
      {kind:'single',id:'order-1',eligible:true,state:null,shares:'3',expected_payout:'3',reason:null},
      {kind:'combo',id:'combo-1',eligible:true,state:null,shares:'4',expected_payout:'4',reason:null},
    ]}))
    if(url.startsWith('/api/v1/trade-intents?'))return Promise.resolve(json({items:[order],next_cursor:null}))
    if(url.startsWith('/api/v1/combo-rfqs?'))return Promise.resolve(json({items:[combo],next_cursor:null}))
    return Promise.resolve(json([]))
  }))
  const view=mount(Orders,{global:{plugins:[router]}});await flushPromises()
  expect(view.findAll('article.order-card').every(card=>!card.classes().includes('is-redeemable'))).toBe(true)
  expect(view.findAll('button').some(button=>button.text()==='赎回')).toBe(false)
  view.unmount()
})
