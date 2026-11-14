import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import SportsCenter from '../src/views/SportsCenter.vue'

// 仅使用模拟接口响应。
const json=(data:unknown,status=200)=>({ok:status===200,status,json:async()=>status===200?{data}:{error:{message:'模拟错误'}}}) as Response
const team=(id:string)=>({id,source_name:id,name_zh:null,translation_status:'missing',logo_url:null})
const game={id:'mock-game',provider:'polymarket',starts_at:'2026-11-15T00:00:00Z',status:'scheduled',home_team:team('模拟主队'),away_team:team('模拟客队'),home_score:null,away_score:null,league:null,last_synced_at:null,result_binding_state:null,has_matched_order:true,recent_revisions:[]}
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/sports/:sport',component:SportsCenter},{path:'/orders',component:SportsCenter}]})
beforeEach(async()=>{await router.push('/sports/nba');await router.isReady();HTMLDialogElement.prototype.showModal=function(){this.open=true};HTMLDialogElement.prototype.close=function(){this.open=false}})
afterEach(()=>vi.unstubAllGlobals())

it('只显示本场单场订单和组合腿',async()=>{
  const fetchMock=vi.fn((url:string)=>{
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:[game],next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    if(url.includes('/live-markets'))return Promise.resolve(json({events:[],observed_at:'2026-11-14T00:00:00Z'}))
    if(url.includes('/result-binding'))return Promise.resolve(json(null))
    if(url.includes('/trade-intents?'))return Promise.resolve(json({items:[{id:'one',side:'BUY',state:'partially_filled',market_question:'模拟市场',outcome_label:'模拟主队',limit_price:'0.5',size:'2',matched_size:'1',created_at:'2026-11-14T00:00:00Z'}],next_cursor:null}))
    if(url.includes('/combo-rfqs?'))return Promise.resolve(json({items:[{id:'combo',state:'confirmed',direction:'BUY',quantity:'2',created_at:'2026-11-14T00:00:00Z',legs:[{game_id:'mock-game',outcome_id:'a',market_question:'本场胜负',outcome_label:'模拟主队'},{game_id:'other',outcome_id:'b',market_question:'其他比赛',outcome_label:'不应显示'}]}],next_cursor:null}))
    return Promise.resolve(json(game))
  })
  vi.stubGlobal('fetch',fetchMock)
  const view=mount(SportsCenter,{global:{plugins:[router]}});await flushPromises()
  await view.findAll('button').find(button=>button.text()==='查看详情')!.trigger('click');await flushPromises()
  const section=view.get('[aria-label="本场订单明细"]')
  expect(section.text()).toContain('单场买入 · 部分成交')
  expect(section.text()).toContain('已成交 1')
  expect(section.text()).toContain('组合买入 · 已确认')
  expect(section.text()).toContain('本场胜负')
  expect(section.text()).not.toContain('其他比赛')
  expect(fetchMock.mock.calls.some(([url])=>url.includes('/combo-rfqs?')&&url.includes('game_id=mock-game')&&url.includes('state=confirmed'))).toBe(true)
  view.unmount()
})

it('接口失败不会被显示为暂无订单',async()=>{
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:[game],next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    if(url.includes('/live-markets'))return Promise.resolve(json({events:[],observed_at:'2026-11-14T00:00:00Z'}))
    if(url.includes('/result-binding'))return Promise.resolve(json(null))
    if(url.includes('/trade-intents?'))return Promise.resolve(json(null,503))
    if(url.includes('/combo-rfqs?'))return Promise.resolve(json({items:[],next_cursor:null}))
    return Promise.resolve(json(game))
  }))
  const view=mount(SportsCenter,{global:{plugins:[router]}});await flushPromises()
  await view.findAll('button').find(button=>button.text()==='查看详情')!.trigger('click');await flushPromises()
  const section=view.get('[aria-label="本场订单明细"]')
  expect(section.text()).toContain('单场订单加载失败')
  expect(section.text()).not.toContain('暂无本场订单')
  view.unmount()
})

it('隐藏其他赛事组合并提示缺失的本场选项，后续页继续核验',async()=>{
  const matched={id:'correct',state:'confirmed',direction:'BUY',quantity:'1',created_at:'2026-11-14T00:00:00Z',legs:[{game_id:'mock-game',outcome_id:'a',market_question:null,outcome_label:null}]}
  const unrelated={...matched,id:'wrong',legs:[{game_id:'other-game',outcome_id:'b',market_question:'其他场次',outcome_label:'其他选项'}]}
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:[game],next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    if(url.includes('/live-markets'))return Promise.resolve(json({events:[],observed_at:'2026-11-14T00:00:00Z'}))
    if(url.includes('/result-binding'))return Promise.resolve(json(null))
    if(url.includes('/trade-intents?'))return Promise.resolve(json({items:[],next_cursor:null}))
    if(url.includes('/combo-rfqs?'))return Promise.resolve(json(url.includes('cursor=')?{items:[unrelated],next_cursor:null}:{items:[matched,unrelated],next_cursor:'mock-next'}))
    return Promise.resolve(json(game))
  }))
  const view=mount(SportsCenter,{global:{plugins:[router]}});await flushPromises()
  await view.findAll('button').find(button=>button.text()==='查看详情')!.trigger('click');await flushPromises()
  const section=view.get('[aria-label="本场订单明细"]')
  expect(section.text()).toContain('市场信息未知 · 选项信息未知')
  expect(section.text()).toContain('组合记录与当前赛事不一致')
  expect(section.text()).not.toContain('其他场次')
  expect(section.findAll('article')).toHaveLength(1)
  await section.findAll('button').find(button=>button.text()==='加载更多组合订单')!.trigger('click');await flushPromises()
  expect(section.text()).toContain('组合记录与当前赛事不一致')
  expect(section.findAll('article')).toHaveLength(1)
  view.unmount()
})

it('切换比赛后旧订单响应不能覆盖当前比赛',async()=>{
  const second={...game,id:'second-game',home_team:team('第二主队')}
  let resolveOld:(value:Response)=>void=()=>{}
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:[game,second],next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    if(url.includes('/live-markets'))return Promise.resolve(json({events:[],observed_at:'2026-11-14T00:00:00Z'}))
    if(url.includes('/result-binding'))return Promise.resolve(json(null))
    if(url.includes('/trade-intents?')&&url.includes('mock-game'))return new Promise<Response>(resolve=>{resolveOld=resolve})
    if(url.includes('/trade-intents?')||url.includes('/combo-rfqs?'))return Promise.resolve(json({items:[],next_cursor:null}))
    return Promise.resolve(json(url.includes('second-game')?second:game))
  }))
  const view=mount(SportsCenter,{global:{plugins:[router]}});await flushPromises()
  await view.findAll('button').filter(button=>button.text()==='查看详情')[0].trigger('click');await flushPromises()
  await view.get('[aria-label="关闭详情"]').trigger('click')
  await view.findAll('button').filter(button=>button.text()==='查看详情')[1].trigger('click');await flushPromises()
  resolveOld(json({items:[{id:'stale',side:'BUY',state:'filled',market_question:'旧比赛订单',created_at:'2026-11-14T00:00:00Z'}],next_cursor:null}));await flushPromises()
  expect(view.get('[aria-label="本场订单明细"]').text()).not.toContain('旧比赛订单')
  expect(view.get('[aria-label="本场订单明细"]').text()).toContain('暂无本场订单')
  view.unmount()
})
