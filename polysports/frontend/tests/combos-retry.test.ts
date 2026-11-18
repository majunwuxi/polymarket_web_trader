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

function setup(postResponses:Response[]){
  const posts:{key:string;body:any}[]=[]
  const fetchMock=vi.fn((url:string,options?:RequestInit)=>{
    if(url.startsWith('/api/v1/wallets'))return Promise.resolve(ok({items:[wallet]}))
    if(url.startsWith('/api/v1/combo-rfqs')&&(!options||options.method!=='POST'))return Promise.resolve(ok({items:[],next_cursor:null}))
    if(url==='/api/v1/combo-rfqs'&&options?.method==='POST'){
      posts.push({key:String((options.headers as Record<string,string>)['Idempotency-Key']),body:JSON.parse(String(options.body))})
      return Promise.resolve(postResponses[posts.length-1])
    }
    return Promise.resolve(ok(null))
  })
  vi.stubGlobal('fetch',fetchMock)
  return {view:mount(Combos,{global:{plugins:[router]}}),posts}
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
