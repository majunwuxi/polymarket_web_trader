import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import OrderEntry from '../src/views/OrderEntry.vue'

const json=(data:unknown)=>({ok:true,status:200,json:async()=>({data})}) as Response
const fail=(code:string,message:string)=>({ok:false,status:503,json:async()=>({error:{code,message}})}) as Response
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/orders/new/:link/:outcome',component:OrderEntry}]})
beforeEach(()=>{localStorage.clear()})
afterEach(()=>vi.unstubAllGlobals())

function baseMock(extra:(url:string,options?:RequestInit)=>Response|undefined){
  return vi.fn((url:string,options?:RequestInit)=>{
    const custom=extra(url,options)
    if(custom)return custom
    if(url.endsWith('/wallets'))return Promise.resolve(json({items:[{id:'mock-wallet',label:'模拟钱包',wallet_address:'0x'+'1'.repeat(40)}]}))
    if(url.includes('/outcomes/mock-outcome/book'))return Promise.resolve(json({tick_size:'0.01',min_order_size:'5',observed_at:'2026-11-16T00:00:00Z',bids:[],asks:[]}))
    if(url.includes('/order-context/'))return Promise.resolve(json({home:'甲',away:'乙',market_question:'模拟市场',outcome_label:'甲',starts_at:'2026-11-16T00:00:00Z',asset_id:'123',protocol_version:'v1'}))
    throw Error('unexpected mock request: '+url)
  })
}

it('页面加载时自动用原键查询，已成交的旧标记不再挡住新订单',async()=>{
  await router.push('/orders/new/mock-link/mock-outcome');await router.isReady()
  localStorage.setItem('polysports_pending_order',JSON.stringify({key:'old-key',wallet_id:'mock-wallet'}))
  const mock=baseMock((url)=>{
    if(url.includes('/trade-intents/by-key/'))return Promise.resolve(json({id:'mock-intent',state:'filled'}))
    return undefined
  })
  vi.stubGlobal('fetch',mock)
  const view=mount(OrderEntry,{global:{plugins:[router]}});await flushPromises()
  expect(view.text()).not.toContain('有一笔结果待核对')
  expect(view.text()).toContain('已查询现有订单状态：已成交')
  expect(view.text()).toContain('按当前价直接下单')
  expect(localStorage.getItem('polysports_pending_order')).toBeNull()
  view.unmount()
})

it('结果未知的下单失败保留本机标记，不提示可安全重试',async()=>{
  await router.push('/orders/new/mock-link/mock-outcome');await router.isReady()
  const mock=baseMock((url)=>{
    if(url==='/api/v1/single-orders')return Promise.resolve(fail('SUBMISSION_UNKNOWN','提交结果未确认'))
    return undefined
  })
  vi.stubGlobal('fetch',mock)
  vi.stubGlobal('crypto',{randomUUID:()=> 'new-request'})
  const view=mount(OrderEntry,{global:{plugins:[router]}});await flushPromises()
  await view.get('#input-mode').setValue('shares')
  await view.get('#price').setValue('0.5')
  await view.get('#size').setValue('5')
  await view.get('form').trigger('submit.prevent');await flushPromises()
  expect(view.text()).toContain('有一笔结果待核对')
  expect(view.text()).toContain('下单结果未核实，请按原键查询，不要重复提交')
  expect(localStorage.getItem('polysports_pending_order')).toContain('new-request')
  view.unmount()
})

it('发送前即可确定的错误安全清除标记并说明未发送下单请求',async()=>{
  await router.push('/orders/new/mock-link/mock-outcome');await router.isReady()
  const mock=baseMock((url)=>{
    if(url==='/api/v1/single-orders')return Promise.resolve(fail('BALANCE_INSUFFICIENT','余额不足'))
    return undefined
  })
  vi.stubGlobal('fetch',mock)
  vi.stubGlobal('crypto',{randomUUID:()=> 'new-request'})
  const view=mount(OrderEntry,{global:{plugins:[router]}});await flushPromises()
  await view.get('#input-mode').setValue('shares')
  await view.get('#price').setValue('0.5')
  await view.get('#size').setValue('5')
  await view.get('form').trigger('submit.prevent');await flushPromises()
  expect(view.text()).not.toContain('有一笔结果待核对')
  expect(view.text()).toContain('本次未发送下单请求')
  expect(localStorage.getItem('polysports_pending_order')).toBeNull()
  view.unmount()
})
