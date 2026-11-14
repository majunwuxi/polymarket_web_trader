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
