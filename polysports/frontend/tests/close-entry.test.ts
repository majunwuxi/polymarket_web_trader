import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import OrderEntry from '../src/views/OrderEntry.vue'
import Combos from '../src/views/Combos.vue'

// 所有响应均为模拟数据，测试禁止访问真实交易接口。
const json=(data:unknown)=>({ok:true,status:200,json:async()=>({data})}) as Response
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/orders/new/:link/:outcome',component:OrderEntry},{path:'/combos',component:Combos}]})
beforeEach(()=>{localStorage.clear()})
afterEach(()=>vi.unstubAllGlobals())

const closeContext={
  wallet_id:'mock-wallet',wallet_address:'0xmock',asset_id:'123',
  market_link_id:'mock-link',outcome_id:'mock-outcome',
  wallet_shares:'3',matched_shares:'2',candidate_shares:'2',
  market_price:'0.4',estimated_proceeds:'0.8',market_close_reason:null,
  bids:[{price:'0.4',size:'5'}],notice:'模拟共享份额'
}

it('单场平仓按市价自动取价，不显示开仓字段，不依赖赛事映射或公开盘口',async()=>{
  await router.push('/orders/new/mock-link/mock-outcome?close=mock-original');await router.isReady()
  const mock=vi.fn((url:string,options?:RequestInit)=>{
    if(url.includes('/close-context'))return Promise.resolve(json(closeContext))
    if(url.endsWith('/wallets'))return Promise.resolve(json({items:[{id:'mock-wallet',label:'模拟钱包',wallet_address:'mock'}]}))
    if(url.includes('/single-orders/preview'))return Promise.resolve(json({...JSON.parse(String(options?.body)),preview_token:'mock-token',expires_at:new Date(Date.now()+30000).toISOString(),estimated_notional:'0.8'}))
    if(url==='/api/v1/single-orders')return Promise.resolve(json({intent:{state:'pending',matched_size:null}}))
    throw Error('unexpected mock request: '+url)
  })
  vi.stubGlobal('fetch',mock)
  vi.stubGlobal('crypto',{randomUUID:()=> 'mock-request'})
  const view=mount(OrderEntry,{global:{plugins:[router]}});await flushPromises()
  const text=view.text()
  expect(text).toContain('立即平仓')
  expect(text).toContain('当前市价 0.4')
  expect(text).toContain('全部成交或不成交')
  expect(text).not.toContain('最低卖出价格')
  expect(text).not.toContain('购买金额上限')
  expect(text).not.toContain('输入方式')
  expect(text).not.toContain('方向：')
  expect(mock.mock.calls.some(([url])=>url.includes('/order-context/'))).toBe(false)
  expect(mock.mock.calls.some(([url])=>url.includes('/book'))).toBe(false)
  const preview=mock.mock.calls.find(([url])=>url.includes('/single-orders/preview'))!
  expect(JSON.parse(String(preview[1]?.body))).toMatchObject({side:'SELL',order_type:'FOK',parent_trade_id:'mock-original',size:'2',limit_price:'0.4'})
  expect(mock.mock.calls.some(([url])=>url==='/api/v1/single-orders')).toBe(false)
  await view.findAll('button').find(button=>button.text()==='确认按市价平仓')!.trigger('click');await flushPromises()
  const submitted=mock.mock.calls.find(([url])=>url==='/api/v1/single-orders')!
  expect(JSON.parse(String(submitted[1]?.body))).toMatchObject({side:'SELL',order_type:'FOK',parent_trade_id:'mock-original',preview_token:'mock-token'})
  view.unmount()
})

it('买盘深度不足时禁用平仓，不生成预览也不提交',async()=>{
  await router.push('/orders/new/mock-link/mock-outcome?close=mock-original');await router.isReady()
  const mock=vi.fn((url:string)=>{
    if(url.includes('/close-context'))return Promise.resolve(json({...closeContext,market_price:null,estimated_proceeds:null,market_close_reason:'当前买盘深度不足，无法一次全部平仓'}))
    if(url.endsWith('/wallets'))return Promise.resolve(json({items:[{id:'mock-wallet',label:'模拟钱包',wallet_address:'mock'}]}))
    throw Error('unexpected mock request: '+url)
  })
  vi.stubGlobal('fetch',mock)
  const view=mount(OrderEntry,{global:{plugins:[router]}});await flushPromises()
  expect(view.text()).toContain('当前买盘深度不足')
  expect(view.findAll('button').find(button=>button.text()==='确认按市价平仓')!.attributes('disabled')).toBeDefined()
  expect(mock.mock.calls.some(([url])=>url.includes('/single-orders'))).toBe(false)
  view.unmount()
})

it('组合平仓沿用原组合腿和钱包，明确关联原组合',async()=>{
  await router.push('/combos?close=mock-combo');await router.isReady()
  vi.stubGlobal('crypto',{randomUUID:()=> 'mock-request'})
  const origin={id:'mock-combo',state:'confirmed',direction:'BUY',wallet_id:'mock-wallet',quote:{net_receive:'3.450000'},legs:[{market_link_id:'l1',outcome_id:'o1',home:'甲',away:'乙',market_question:'胜负',outcome_label:'甲'},{market_link_id:'l2',outcome_id:'o2',home:'丙',away:'丁',market_question:'胜负',outcome_label:'丙'}]}
  const mock=vi.fn((url:string,options?:RequestInit)=>{
    if(url.endsWith('/wallets'))return Promise.resolve(json({items:[{id:'mock-wallet',label:'模拟钱包',wallet_address:'mock'}]}))
    if(url.endsWith('/combo-rfqs/mock-combo/close-context'))return Promise.resolve(json({wallet_id:'mock-wallet',position_id:null,wallet_shares:null,previously_closed_shares:'0',candidate_shares:'3.450000',approved:null,notice:'模拟共享份额'}))
    if(url.endsWith('/combo-rfqs/mock-combo'))return Promise.resolve(json(origin))
    if(url.endsWith('/combo-rfqs')&&options?.method==='POST')return Promise.resolve(json({state:'failed',error_code:'no_quote'}))
    if(url.endsWith('/combo-rfqs'))return Promise.resolve(json({items:[],next_cursor:null}))
    throw Error('unexpected mock request: '+url)
  })
  vi.stubGlobal('fetch',mock)
  const view=mount(Combos,{global:{plugins:[router]}});await flushPromises()
  expect(view.text()).toContain('组合平仓')
  await view.findAll('button').find(button=>button.text()==='请求真实卖出报价')!.trigger('click');await flushPromises()
  const call=mock.mock.calls.find(([url,options])=>url.endsWith('/combo-rfqs')&&options?.method==='POST')!
  expect(JSON.parse(String(call[1]?.body))).toMatchObject({wallet_id:'mock-wallet',direction:'SELL',quantity:'3.45',parent_combo_id:'mock-combo',legs:[{market_link_id:'l1',outcome_id:'o1'},{market_link_id:'l2',outcome_id:'o2'}]})
  view.unmount()
})
