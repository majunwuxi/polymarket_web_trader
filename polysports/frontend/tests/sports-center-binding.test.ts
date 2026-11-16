import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import SportsCenter from '../src/views/SportsCenter.vue'
import { session } from '../src/auth'

const json=(data:unknown,status=200)=>({ok:status>=200&&status<300,status,json:async()=>status>=200&&status<300?{data}:{error:{message:'模拟错误'}}}) as Response
const team=(id:string)=>({id,source_name:id,name_zh:null,translation_status:'missing',logo_url:null})
const game={id:'g1',provider:'polymarket',external_id:'90108495',starts_at:'2026-11-17T03:45:00Z',status:'scheduled',home_team:team('瑞士'),away_team:team('苏格兰'),home_score:null,away_score:null,league:{name:'欧国联'},last_synced_at:null,result_binding_state:'needs_review',has_matched_order:false}
const summary={sport:'football',as_of:'2026-11-16T08:00:00Z',totals:{total:3,bound:1,pending:1,needs_review:1,unbound:2,ready_to_confirm:1},items:[{game_id:'g1',external_id:'90108495',starts_at:'2026-11-17T03:45:00Z',game_status:'scheduled',home:'瑞士',away:'苏格兰',league:'欧国联',state:'needs_review',provider:'api_football',fixture_id:null,candidates:[{id:'123456',starts_at:'2026-11-17T03:45:00Z',home:'Switzerland',away:'Scotland',league:'UEFA Nations League'}],error_code:null,updated_at:'2026-11-16T08:00:00Z',next_attempt_at:'2026-11-16T08:10:00Z',can_quick_confirm:true}],has_more:false}
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/sports/:sport',component:SportsCenter}]})

beforeEach(async()=>{session.csrf='mock-csrf';HTMLDialogElement.prototype.showModal=function(){this.open=true};HTMLDialogElement.prototype.close=function(){this.open=false};await router.push('/sports/football');await router.isReady()})
afterEach(()=>vi.unstubAllGlobals())

function setup(summaryResponse:()=>Promise<Response>=()=>Promise.resolve(json(summary))){
  const fetchMock=vi.fn((url:string,options?:RequestInit)=>{
    if(url.startsWith('/api/v1/result-bindings/summary'))return summaryResponse()
    if(url.endsWith('/result-binding/confirm'))return Promise.resolve(json({state:'bound'}))
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:[game],next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    return Promise.resolve(json(null))
  })
  vi.stubGlobal('fetch',fetchMock)
  return {view:mount(SportsCenter,{global:{plugins:[router]}}),fetchMock}
}

it('足球赛事中心显示未绑定统计并可直接确认候选',async()=>{
  const {view,fetchMock}=setup();await flushPromises()
  expect(view.get('[aria-label="足球赛果绑定状态"]').exists()).toBe(true)
  expect(view.findAll('.binding-metrics strong').map(item=>item.text())).toEqual(['2','1','1','1'])
  expect(view.text()).toContain('Switzerland 对 Scotland')
  const button=view.findAll('button').find(item=>item.text()==='确认绑定')
  expect(button).toBeTruthy()
  await button!.trigger('click');await flushPromises()
  const request=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/games/g1/result-binding/confirm'))
  expect(request).toBeTruthy()
  expect((request![1] as RequestInit).method).toBe('POST')
  expect(((request![1] as RequestInit).headers as Record<string,string>)['X-CSRF-Token']).toBe('mock-csrf')
  await vi.waitFor(()=>expect(view.text()).toContain('已绑定 瑞士 对 苏格兰'))
  view.unmount()
})

it('NBA 赛事中心不请求也不显示赛果绑定统计',async()=>{
  await router.push('/sports/nba')
  const {view,fetchMock}=setup();await flushPromises()
  expect(view.find('[aria-label="足球赛果绑定状态"]').exists()).toBe(false)
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/result-bindings/summary'))).toBe(false)
  view.unmount()
})

it('自动匹配中的赛事只显示数量，不提供确认按钮',async()=>{
  const pending={...summary,totals:{total:1,bound:0,pending:1,needs_review:0,unbound:1,ready_to_confirm:0},items:[]}
  const {view}=setup(()=>Promise.resolve(json(pending)));await flushPromises()
  expect(view.text()).toContain('等待后台自动匹配候选')
  expect(view.findAll('button').some(item=>item.text()==='确认绑定')).toBe(false)
  expect(view.findAll('.binding-metrics strong').map(item=>item.text())).toEqual(['1','0','1','0'])
  view.unmount()
})

it('绑定统计失败不阻塞赛事列表',async()=>{
  const {view}=setup(()=>Promise.resolve(json({error:{message:'mock'}},503)));await flushPromises()
  expect(view.text()).toContain('赛果绑定统计加载失败')
  expect(view.text()).toContain('瑞士')
  expect(view.text()).toContain('苏格兰')
  view.unmount()
})
