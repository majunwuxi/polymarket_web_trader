import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Combos from '../src/views/Combos.vue'

// 全部为模拟数据，fetch 不连接真实交易服务。
const json=(body:unknown,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body}) as Response
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:Combos}]})
const row=(expires:number)=>({id:'mock-rfq',state:'quoted',direction:'BUY',quantity:'1',wallet_address:'mock',legs:[],quote:{id:'mock-quote',expires_at:new Date(expires).toISOString(),price:'0.5',total_required:'1',net_receive:'2'}})

beforeEach(async()=>{localStorage.clear();await router.push('/');await router.isReady()})
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers()})

function setup(response:()=>Promise<Response>,expires=Date.now()+60_000){
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/accept'))return response()
    if(url.endsWith('/wallets'))return Promise.resolve(json({data:{items:[]}}))
    return Promise.resolve(json({data:{items:[row(expires)]}}))
  })
  vi.stubGlobal('fetch',fetchMock)
  return {view:mount(Combos,{global:{plugins:[router]}}),fetchMock}
}

describe('组合接受反馈（模拟接口）',()=>{
  it('询价返回后立即显示报价，无需等待历史记录刷新',async()=>{
    localStorage.setItem('polysports_combo_legs',JSON.stringify([{label:'模拟一',market_link_id:'m1',outcome_id:'o1'},{label:'模拟二',market_link_id:'m2',outcome_id:'o2'}]))
    const quote=row(Date.now()+60_000);quote.legs=[]
    let recordReads=0
    vi.stubGlobal('crypto',{randomUUID:()=> 'mock-id'})
    vi.stubGlobal('fetch',vi.fn((url:string,options?:RequestInit)=>{
      if(url.endsWith('/wallets'))return Promise.resolve(json({data:{items:[{id:'mock-wallet',label:'模拟钱包',wallet_address:'mock'}]}}))
      if(options?.method==='POST')return Promise.resolve(json({data:quote}))
      recordReads++
      return Promise.resolve(json({data:{items:[]}}))
    }))
    const view=mount(Combos,{global:{plugins:[router]}})
    await flushPromises()
    await view.get('input[inputmode="decimal"]').setValue('1')
    await view.findAll('button').find(button=>button.text()==='请求真实报价')!.trigger('click')
    await flushPromises()
    expect(view.get('[aria-label="最新组合报价"]').text()).toContain('确认接受真实资金报价')
    expect(recordReads).toBe(1)
    view.unmount()
  })
  it('明确的发送前失败显示具体原因',async()=>{
    const {view}=setup(()=>Promise.resolve(json({error:{code:'ALLOWANCE_INSUFFICIENT',message:'组合交易授权不足'}},503)))
    await flushPromises();await view.get('.market-card button').trigger('click');await flushPromises()
    expect(view.text()).toContain('组合交易授权不足（本次未发送接受请求）')
    view.unmount()
  })
  it('网络异常后禁止对同一报价重复接受',async()=>{
    const {view,fetchMock}=setup(()=>Promise.reject(new Error('mock network error')))
    await flushPromises();await view.get('.market-card button').trigger('click');await flushPromises()
    expect(view.text()).toContain('确认结果未核实')
    expect((view.get('.market-card button').element as HTMLButtonElement).disabled).toBe(true)
    expect(fetchMock.mock.calls.filter(([url])=>url.endsWith('/accept'))).toHaveLength(1)
    view.unmount()
  })
  it('过期报价不能接受',async()=>{
    const {view,fetchMock}=setup(()=>Promise.resolve(json({data:{}})),Date.now()-1000)
    await flushPromises()
    expect(view.text()).toContain('报价已过期')
    expect((view.get('.market-card button').element as HTMLButtonElement).disabled).toBe(true)
    expect(fetchMock.mock.calls.filter(([url])=>url.endsWith('/accept'))).toHaveLength(0)
    view.unmount()
  })
})
