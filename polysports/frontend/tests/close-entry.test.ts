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

it('单场平仓使用原钱包、SELL、FOK 和原订单，不自动提交',async()=>{
  await router.push('/orders/new/mock-link/mock-outcome?close=mock-original');await router.isReady()
  const mock=vi.fn((url:string,options?:RequestInit)=>{
    if(url.includes('/close-context'))return Promise.resolve(json({wallet_id:'mock-wallet',asset_id:'123',wallet_shares:'3',matched_shares:'2',candidate_shares:'2',bids:[{price:'0.4',size:'5'}],notice:'模拟共享份额'}))
    if(url.endsWith('/wallets'))return Promise.resolve(json({items:[{id:'mock-wallet',label:'模拟钱包',wallet_address:'mock'}]}))
    if(url.includes('/book'))return Promise.resolve(json({tick_size:'0.01',min_order_size:'1',observed_at:'模拟'}))
    if(url.includes('/order-context/'))return Promise.resolve(json({home:'主',away:'客',market_question:'模拟市场',outcome_label:'模拟选项',starts_at:'2026-11-15T00:00:00Z',asset_id:'123'}))
    if(url.includes('/single-orders/preview'))return Promise.resolve(json({...JSON.parse(String(options?.body)),preview_token:'mock-token',expires_at:'mock',estimated_notional:'0.8'}))
    if(url==='/api/v1/single-orders')return Promise.resolve(json({intent:{state:'pending',matched_size:null}}))
    throw Error('unexpected mock request')
  })
  vi.stubGlobal('fetch',mock)
  const view=mount(OrderEntry,{global:{plugins:[router]}});await flushPromises()
  expect(view.text()).toContain('全部成交或不成交')
  expect((view.get('#wallet').element as HTMLSelectElement).value).toBe('mock-wallet')
  await view.get('form').trigger('submit.prevent');await flushPromises()
  const preview=mock.mock.calls.find(([url])=>url.includes('/single-orders/preview'))!
  expect(JSON.parse(String(preview[1]?.body))).toMatchObject({side:'SELL',order_type:'FOK',parent_trade_id:'mock-original',size:'2',limit_price:'0.4'})
  expect(mock.mock.calls.some(([url])=>url==='/api/v1/single-orders')).toBe(false)
  vi.stubGlobal('crypto',{randomUUID:()=> 'mock-request'})
  await view.findAll('button').find(button=>button.text()==='确认并提交真实订单')!.trigger('click');await flushPromises()
  const submitted=mock.mock.calls.find(([url])=>url==='/api/v1/single-orders')!
  expect(JSON.parse(String(submitted[1]?.body))).toMatchObject({side:'SELL',order_type:'FOK',parent_trade_id:'mock-original',preview_token:'mock-token'})
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
    throw Error('unexpected mock request')
  })
  vi.stubGlobal('fetch',mock)
  const view=mount(Combos,{global:{plugins:[router]}});await flushPromises()
  expect(view.text()).toContain('组合平仓')
  await view.findAll('button').find(button=>button.text()==='请求真实卖出报价')!.trigger('click');await flushPromises()
  const call=mock.mock.calls.find(([url,options])=>url.endsWith('/combo-rfqs')&&options?.method==='POST')!
  expect(JSON.parse(String(call[1]?.body))).toMatchObject({wallet_id:'mock-wallet',direction:'SELL',quantity:'3.45',parent_combo_id:'mock-combo',legs:[{market_link_id:'l1',outcome_id:'o1'},{market_link_id:'l2',outcome_id:'o2'}]})
  view.unmount()
})
