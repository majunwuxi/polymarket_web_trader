import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Combos from '../src/views/Combos.vue'

// 全部为模拟数据，fetch 不连接真实交易服务。
const json=(body:unknown,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body}) as Response
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:Combos}]})
const wallet={id:'mock-wallet',label:'模拟钱包',wallet_address:'mock'}
const placed=(state:string,extra:Record<string,unknown>={})=>({id:'mock-rfq',state,direction:'BUY',quantity:'1',wallet_address:'mock',error_code:null,legs:[],quote:{id:'mock-quote',expires_at:new Date(Date.now()+60_000).toISOString(),price:'0.5',total_required:'1',net_receive:'2'},...extra})

beforeEach(async()=>{
  localStorage.clear()
  vi.stubGlobal('crypto',{randomUUID:()=> 'mock-id'})
  await router.push('/');await router.isReady()
})
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers()})

function twoLegs(){localStorage.setItem('polysports_combo_legs',JSON.stringify([{label:'模拟一',market_link_id:'m1',outcome_id:'o1'},{label:'模拟二',market_link_id:'m2',outcome_id:'o2'}]))}
function setup(response:()=>Promise<Response>,list:any[]=[]){
  const fetchMock=vi.fn((url:string,options?:RequestInit)=>{
    if(options?.method==='POST')return response()
    if(url.endsWith('/wallets'))return Promise.resolve(json({data:{items:[wallet]}}))
    return Promise.resolve(json({data:{items:list}}))
  })
  vi.stubGlobal('fetch',fetchMock)
  return {view:mount(Combos,{global:{plugins:[router]}}),fetchMock}
}
async function submit(view:any){
  await view.get('input[inputmode="decimal"]').setValue('1')
  await view.findAll('button').find(button=>button.text()==='按当前市价直接下单')!.trigger('click')
  await flushPromises()
}
const posts=(mock:any)=>mock.mock.calls.filter(([,options]:any[])=>options?.method==='POST')

describe('组合一键下单反馈（模拟接口）',()=>{
  it('填金额后按当前市场价一次提交，不再出现报价卡片和二次确认',async()=>{
    twoLegs()
    const {view,fetchMock}=setup(()=>Promise.resolve(json({data:placed('executing')})))
    await flushPromises()
    await submit(view)
    const [url,options]=posts(fetchMock)[0] as unknown as [string,RequestInit]
    expect(url).toBe('/api/v1/combo-rfqs')
    expect(JSON.parse(String(options.body))).toMatchObject({wallet_id:'mock-wallet',direction:'BUY',quantity:'1',auto_accept:true})
    expect(view.text()).toContain('已按当前市场价提交')
    expect(view.text()).not.toContain('确认接受真实资金报价')
    expect(view.find('[aria-label="最新组合报价"]').exists()).toBe(false)
    expect(fetchMock.mock.calls.filter(([path])=>String(path).endsWith('/accept'))).toHaveLength(0)
    expect(localStorage.getItem('polysports_combo_legs')).toBeNull()
    expect(view.text()).toContain('已清空待下单组合腿')
    view.unmount()
  })
  it('执行中的组合可以重新安排外部对账',async()=>{
    const combo=placed('executing')
    const {view,fetchMock}=setup(()=>Promise.resolve(json({data:{combo_id:'mock-rfq',status:'pending'}})),[combo])
    await flushPromises()
    const button=view.findAll('button').find(item=>item.text()==='重新核对外部状态')
    expect(button).toBeTruthy()
    await button!.trigger('click');await flushPromises()
    const reconcile=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/combo-rfqs/mock-rfq/reconcile'))!
    expect(reconcile[1]?.method).toBe('POST')
    expect(view.text()).toContain('已重新安排对账')
    view.unmount()
  })
  it('可以一次性清空全部待下单组合腿',async()=>{
    twoLegs()
    const {view}=setup(()=>Promise.resolve(json({data:placed('executing')})))
    await flushPromises()
    expect(view.text()).toContain('模拟一')
    expect(view.text()).toContain('模拟二')
    await view.findAll('button').find(button=>button.text()==='清空全部')!.trigger('click')
    expect(view.text()).not.toContain('模拟一')
    expect(view.text()).not.toContain('模拟二')
    expect(localStorage.getItem('polysports_combo_legs')).toBeNull()
    expect(view.text()).toContain('已清空全部待下单组合腿')
    view.unmount()
  })
  it('发送前校验失败显示具体原因，清除待查记录后允许重试',async()=>{
    twoLegs()
    const {view,fetchMock}=setup(()=>Promise.resolve(json({error:{code:'ALLOWANCE_INSUFFICIENT',message:'组合交易授权不足'}},503)))
    await flushPromises()
    await submit(view)
    expect(view.text()).toContain('组合交易授权不足（本次未发送下单请求）')
    expect(localStorage.getItem('polysports_combo_legs')).not.toBeNull()
    await submit(view)
    expect(posts(fetchMock)).toHaveLength(2)
    view.unmount()
  })
  it('下单结果未知时保留待查记录并阻止重复提交',async()=>{
    twoLegs()
    const {view,fetchMock}=setup(()=>Promise.resolve(json({data:placed('unknown')})))
    await flushPromises()
    await submit(view)
    expect(view.text()).toContain('下单结果未确认')
    expect(view.text()).toContain('查询原请求')
    await submit(view)
    expect(posts(fetchMock)).toHaveLength(1)
    view.unmount()
  })
  it('历史待确认报价不再提供接受入口',async()=>{
    twoLegs()
    const {view}=setup(()=>Promise.resolve(json({data:placed('executing')})),[placed('quoted',{id:'old-rfq'})])
    await flushPromises()
    expect(view.text()).toContain('旧流程的待确认报价')
    expect(view.text()).not.toContain('确认接受真实资金报价')
    view.unmount()
  })
  it('上游拒绝时显示具体原因，报价标签不再写成成交',async()=>{
    twoLegs()
    const reason='做市商拒绝本次接受（Last Look），报价方未确认成交'
    const {view}=setup(()=>Promise.resolve(json({data:placed('rejected',{error_code:'maker_declined',error_text:reason})})))
    await flushPromises()
    await submit(view)
    expect(view.text()).toContain('下单未成交：'+reason)
    expect(view.text()).not.toContain('请检查账户权限')
    expect(view.text()).toContain('组合报价')
    expect(view.text()).not.toContain('成交报价')
    view.unmount()
  })
})
