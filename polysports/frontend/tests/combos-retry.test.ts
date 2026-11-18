import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import Combos from '../src/views/Combos.vue'
import { session } from '../src/auth'

const ok=(data:unknown,status=200)=>({ok:true,status,json:async()=>({data})}) as Response
const fail=(code:string,message:string,status=409)=>({ok:false,status,json:async()=>({error:{code,message}})}) as Response
const wallet={id:'w1',label:'主钱包',wallet_address:'0x'+'1'.repeat(40)}
const record=(overrides:Record<string,unknown>={})=>({id:'r1',wallet_id:'w1',wallet_address:wallet.wallet_address,direction:'BUY',quantity:'1',state:'executing',external_rfq_id:'rfq-1',error_code:null,error_text:null,tx_hash:null,accept_order_hash:null,parent_combo_id:null,combo_position_id:null,created_at:'2026-11-18T14:30:00Z',updated_at:'2026-11-18T14:30:01Z',legs:[{outcome_id:'o1',home:'甲队',away:'乙队',market_question:'市价',outcome_label:'Over',starts_at:'2026-11-19T00:30:00Z'},{outcome_id:'o2',home:'丙队',away:'丁队',market_question:'市价',outcome_label:'Over',starts_at:'2026-11-19T00:30:00Z'}],quote:null,fill:null,pnl:null,buy_summary:null,...overrides})
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/combos',component:Combos}]})

beforeEach(async()=>{
  session.csrf='mock-csrf'
  localStorage.clear()
  localStorage.setItem('polysports_combo_legs',JSON.stringify([
    {market_link_id:'l1',outcome_id:'o1',label:'甲队 对 乙队 · 市价 · Over'},
    {market_link_id:'l2',outcome_id:'o2',label:'丙队 对 丁队 · 市价 · Over'},
  ]))
  await router.push('/combos');await router.isReady()
})
afterEach(()=>vi.unstubAllGlobals())

function setup(postResponses:Response[]=[],options:{records?:any[];byKey?:()=>Response}={}){
  const posts:{key:string;body:any}[]=[]
  const deletes:string[]=[]
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.startsWith('/api/v1/wallets'))return Promise.resolve(ok({items:[wallet]}))
    if(url.startsWith('/api/v1/combo-rfqs/by-key/'))return Promise.resolve(options.byKey?options.byKey():ok(null))
    if(url.startsWith('/api/v1/combo-rfqs/')&&init?.method==='DELETE'){deletes.push(url.split('/').pop()!);return Promise.resolve(ok({deleted:'r1'}))}
    if(url==='/api/v1/combo-rfqs'&&init?.method==='POST'){
      posts.push({key:String((init.headers as Record<string,string>)['Idempotency-Key']),body:JSON.parse(String(init.body))})
      return Promise.resolve(postResponses[posts.length-1])
    }
    if(url.startsWith('/api/v1/combo-rfqs')&&(!init||init.method!=='POST'))return Promise.resolve(ok({items:options.records??[],next_cursor:null}))
    return Promise.resolve(ok(null))
  })
  vi.stubGlobal('fetch',fetchMock)
  return {view:mount(Combos,{global:{plugins:[router]}}),posts,deletes}
}

async function submit(view:ReturnType<typeof mount>){
  await flushPromises()
  await view.find('input[inputmode="decimal"]').setValue('1')
  const button=view.findAll('button').find(item=>item.text()==='按当前市价直接下单')
  expect(button).toBeTruthy()
  await button!.trigger('click');await flushPromises()
}

it('报价过期时自动重新询价一次并按新报价下单',async()=>{
  const {view,posts}=setup([fail('COMBO_QUOTE_EXPIRED','组合报价已过期'),ok(record(),201)])
  await submit(view)
  expect(posts).toHaveLength(2)
  expect(posts[0].key).not.toBe(posts[1].key)
  expect(posts[1].body.auto_accept).toBe(true)
  expect(view.text()).toContain('已按当前市场价提交，执行中不代表成交。')
  expect(view.text()).toContain('此前报价已过期，已自动重新询价一次')
  expect(localStorage.getItem('polysports_combo_pending')).toBeNull()
  view.unmount()
})

it('报价在服务端已失效（QUOTE_UNAVAILABLE）同样重询一次',async()=>{
  const {view,posts}=setup([fail('QUOTE_UNAVAILABLE','报价已失效或服务重启，请重新询价'),ok(record(),201)])
  await submit(view)
  expect(posts).toHaveLength(2)
  expect(view.text()).toContain('已按当前市场价提交')
  view.unmount()
})

it('重询一次后仍过期不再重试，提示未发送下单请求',async()=>{
  const {view,posts}=setup([fail('COMBO_QUOTE_EXPIRED','组合报价已过期'),fail('COMBO_QUOTE_EXPIRED','组合报价已过期')])
  await submit(view)
  expect(posts).toHaveLength(2)
  expect(view.text()).toContain('组合报价已过期（本次未发送下单请求）')
  expect(localStorage.getItem('polysports_combo_pending')).toBeNull()
  view.unmount()
})

it('余额不足等其它错误不会自动重试',async()=>{
  const {view,posts}=setup([fail('BALANCE_INSUFFICIENT','组合交易余额不足')])
  await submit(view)
  expect(posts).toHaveLength(1)
  expect(view.text()).toContain('组合交易余额不足')
  view.unmount()
})

it('组合记录显示下单时间',async()=>{
  const {view}=setup([],{records:[record({state:'unknown',error_code:'rfq_not_found',created_at:'2026-11-18T14:30:00Z'})]})
  await flushPromises()
  expect(view.text()).toContain('下单时间：2026/11/18 22:30:00 UTC+8')
  view.unmount()
})

it('删除未成交记录需先勾选确认，再调用删除接口',async()=>{
  const {view,deletes}=setup([],{records:[record({state:'unknown',error_code:'rfq_not_found'})]})
  await flushPromises()
  const ask=view.findAll('button').find(item=>item.text()==='确认 Polymarket 无此订单并删除')
  expect(ask).toBeTruthy()
  await ask!.trigger('click')
  const checkbox=view.find('input[type="checkbox"]')
  expect(checkbox.exists()).toBe(true)
  const confirm=view.findAll('button').find(item=>item.text()==='确认删除')!
  expect(confirm.attributes('disabled')).toBeDefined()
  await checkbox.setValue(true)
  await confirm.trigger('click');await flushPromises()
  expect(deletes).toEqual(['r1'])
  expect(view.text()).toContain('已删除该组合记录。')
  view.unmount()
})

it('已确认成交的记录不显示删除按钮',async()=>{
  const {view}=setup([],{records:[record({state:'confirmed'})]})
  await flushPromises()
  expect(view.findAll('button').some(item=>item.text()==='确认 Polymarket 无此订单并删除')).toBe(false)
  view.unmount()
})

it('查询原请求返回 404 时自动清除本机待查标记',async()=>{
  localStorage.setItem('polysports_combo_pending',JSON.stringify({key:'k1',wallet_id:'w1'}))
  const {view}=setup([],{byKey:()=>fail('NOT_FOUND','询价记录不存在，不能据此推断外部请求状态',404)})
  await flushPromises()
  const lookup=view.findAll('button').find(item=>item.text()==='查询原请求')
  expect(lookup).toBeTruthy()
  await lookup!.trigger('click');await flushPromises()
  expect(localStorage.getItem('polysports_combo_pending')).toBeNull()
  expect(view.text()).toContain('本机待查标记已清除')
  view.unmount()
})
