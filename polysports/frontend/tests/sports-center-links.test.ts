import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import SportsCenter from '../src/views/SportsCenter.vue'
import { session } from '../src/auth'

const json=(data:unknown,status=200)=>({ok:status>=200&&status<300,status,json:async()=>status>=200&&status<300?{data}:{error:{message:'模拟错误'}}}) as Response
const team=(id:string)=>({id,source_name:id,name_zh:null,translation_status:'missing',logo_url:null})
const game={id:'g1',provider:'polymarket',external_id:'90108495',starts_at:'2026-11-17T03:45:00Z',status:'scheduled',home_team:team('Switzerland'),away_team:team('Scotland'),home_score:null,away_score:null,league:{name:'欧国联'},last_synced_at:null,result_binding_state:null,has_matched_order:false}
const liveMarkets={match_status:'source_identity',observed_at:'2026-11-17T00:00:00Z',events:[{id:'e1',title:'模拟赛事',slug:'switzerland-scotland',markets:[]}]}
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/sports/:sport',component:SportsCenter}]})

beforeEach(async()=>{session.csrf='mock-csrf';HTMLDialogElement.prototype.showModal=function(){this.open=true};HTMLDialogElement.prototype.close=function(){this.open=false};await router.push('/sports/football');await router.isReady()})
afterEach(()=>vi.unstubAllGlobals())

function setup(liveResponse:()=>Promise<Response>=()=>Promise.resolve(json(liveMarkets)),games=[game]){
  const fetchMock=vi.fn((url:string)=>{
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:games,next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    if(url.startsWith('/api/v1/result-bindings/summary'))return Promise.resolve(json({sport:'football',as_of:'2026-11-16T08:00:00Z',totals:{total:0,bound:0,pending:0,needs_review:0,unbound:0,ready_to_confirm:0},items:[],has_more:false}))
    if(url.includes('/live-markets'))return liveResponse()
    return Promise.resolve(json(null))
  })
  vi.stubGlobal('fetch',fetchMock)
  return {view:mount(SportsCenter,{global:{plugins:[router]}}),fetchMock}
}

it('比赛卡片提供 Polymarket 与 Google 搜索入口',async()=>{
  const {view}=setup();await flushPromises()
  const card=view.get('.game-card')
  expect(card.findAll('button').some(button=>button.text()==='在 Polymarket 查看 ↗')).toBe(true)
  const google=card.findAll('a').find(anchor=>anchor.text()==='Google 搜索 ↗')
  expect(google).toBeTruthy()
  expect(google!.attributes('href')).toBe('https://www.google.com/search?q='+encodeURIComponent('Switzerland vs Scotland 欧国联'))
  expect(google!.attributes('target')).toBe('_blank')
  expect(google!.attributes('rel')).toContain('noopener')
  view.unmount()
})

it('点击 Polymarket 按钮解析 slug 后新窗口打开并复用结果',async()=>{
  let resolveLive!: (value:Response)=>void
  const deferred=new Promise<Response>(resolve=>{resolveLive=resolve})
  const {view,fetchMock}=setup(()=>deferred);await flushPromises()
  const openSpy=vi.spyOn(window,'open').mockImplementation(()=>null)
  const button=view.findAll('button').find(button=>button.text()==='在 Polymarket 查看 ↗')!
  await button.trigger('click');await flushPromises()
  expect(button.attributes('disabled')).toBeDefined()
  expect(fetchMock.mock.calls.filter(([url])=>String(url).includes('/live-markets'))).toHaveLength(1)
  resolveLive(json(liveMarkets));await flushPromises()
  expect(openSpy).toHaveBeenCalledWith('https://polymarket.com/event/switzerland-scotland','_blank','noopener,noreferrer')
  expect(button.attributes('disabled')).toBeUndefined()
  await button.trigger('click');await flushPromises()
  expect(openSpy).toHaveBeenCalledTimes(2)
  expect(fetchMock.mock.calls.filter(([url])=>String(url).includes('/live-markets'))).toHaveLength(1)
  openSpy.mockRestore();view.unmount()
})

it('未找到 Polymarket 页面时给出提示',async()=>{
  const {view}=setup(()=>Promise.resolve(json({match_status:'candidate',observed_at:'2026-11-17T00:00:00Z',events:[]})));await flushPromises()
  vi.spyOn(window,'open').mockImplementation(()=>null)
  await view.findAll('button').find(button=>button.text()==='在 Polymarket 查看 ↗')!.trigger('click');await flushPromises()
  expect(view.text()).toContain('未找到本场比赛的 Polymarket 页面。')
  vi.restoreAllMocks();view.unmount()
})

it('Polymarket 页面查询失败时显示服务端原因',async()=>{
  const {view}=setup(()=>Promise.resolve(json(null,503)));await flushPromises()
  await view.findAll('button').find(button=>button.text()==='在 Polymarket 查看 ↗')!.trigger('click');await flushPromises()
  expect(view.text()).toContain('模拟错误')
  view.unmount()
})
