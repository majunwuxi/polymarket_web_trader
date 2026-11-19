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

it('完赛详情显示最终比分，本场订单使用卡片和胜负徽标',async()=>{
  const finished={...game,status:'finished',home_score:120,away_score:136}
  const single=(id:string,won:boolean|null)=>({id,side:'BUY',state:'filled',market_question:'Mock '+id,outcome_label:'Mock',limit_price:'0.5',size:'2',matched_size:'2',created_at:'2026-11-15T00:00:00Z',external_order_id:'0x'+id,result_won:won})
  const combo={id:'combo',state:'confirmed',direction:'BUY',quantity:'2',created_at:'2026-11-15T00:00:00Z',external_rfq_id:'rfq',legs:[{game_id:'mock-game',outcome_id:'a',market_question:'本场胜负',outcome_label:'主队',result_won:true}]}
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:[finished],next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    if(url.includes('/live-markets'))return Promise.resolve(json({events:[],observed_at:'2026-11-14T00:00:00Z'}))
    if(url.includes('/result-binding'))return Promise.resolve(json(null))
    if(url.includes('/trade-intents?'))return Promise.resolve(json({items:[single('win',true),single('loss',false)],next_cursor:null}))
    if(url.includes('/combo-rfqs?'))return Promise.resolve(json({items:[combo],next_cursor:null}))
    return Promise.resolve(json(finished))
  }))
  const view=mount(SportsCenter,{global:{plugins:[router]}});await flushPromises()
  await view.findAll('button').find(button=>button.text()==='查看详情')!.trigger('click');await flushPromises()
  const score=view.get('.summary-scoreboard')
  expect(score.text()).toContain('全场比分')
  expect(score.text()).toContain('120')
  expect(score.text()).toContain('136')
  expect(score.get('.score-winner').text()).toBe('136')
  expect(view.findAll('.match-order-card')).toHaveLength(3)
  expect(view.findAll('.result-badge.result-win').map(item=>item.text())).toContain('选项获胜')
  expect(view.findAll('.result-badge.result-loss').map(item=>item.text())).toContain('选项失败')
  view.unmount()
})

it('赛事详情在主客队中文名下方显示 Polymarket 英文队名',async()=>{
  const named={...game,home_team:{...team('Spurs'),source_name:'Spurs',name_zh:'圣安东尼奥马刺'},away_team:{...team('Pistons'),source_name:'Pistons',name_zh:'底特律活塞'}}
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:[named],next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    if(url.includes('/live-markets'))return Promise.resolve(json({events:[],observed_at:'2026-11-14T00:00:00Z'}))
    if(url.includes('/result-binding'))return Promise.resolve(json(null))
    if(url.includes('/trade-intents?'))return Promise.resolve(json({items:[],next_cursor:null}))
    if(url.includes('/combo-rfqs?'))return Promise.resolve(json({items:[],next_cursor:null}))
    return Promise.resolve(json(named))
  }))
  const view=mount(SportsCenter,{global:{plugins:[router]}});await flushPromises()
  await view.findAll('button').find(button=>button.text()==='查看详情')!.trigger('click');await flushPromises()
  const sourceNames=view.findAll('.summary-source-name').map(item=>item.text())
  expect(sourceNames).toEqual(['Spurs','Pistons'])
  const summary=view.get('.match-summary').text()
  expect(summary).toContain('圣安东尼奥马刺')
  expect(summary).toContain('底特律活塞')
  view.unmount()
})

it('赛事详情不展示 spread 让分市场，大小分按盘口升序排列',async()=>{
  const live={events:[{id:'event-1',title:'Mock Event',slug:'mock-event',markets:[
    {id:'money',type:'moneyline',question:'Mock Home vs Mock Away',group:'胜负',closed:false,accepting_orders:true,outcomes:[{label:'Mock Home',price:'0.55'}]},
    {id:'spread',type:'spreads',question:'Mock Home spread',group:'让分 -1.5',closed:false,accepting_orders:true,outcomes:[{label:'Mock Home -1.5',price:'0.5'}]},
    {id:'total-high',type:'totals',question:'Mock total high',group:'总分 230.5',closed:false,accepting_orders:true,outcomes:[{label:'Over',price:'0.5'}]},
    {id:'total-low',type:'totals',question:'Mock total low',group:'总分 218.5',closed:false,accepting_orders:true,outcomes:[{label:'Over',price:'0.5'}]},
    {id:'total-mid',type:'totals',question:'Mock total mid',group:'总分 225.5',closed:false,accepting_orders:true,outcomes:[{label:'Over',price:'0.5'}]},
  ]}],observed_at:'2026-11-14T00:00:00Z',match_status:'source_identity'}
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:[game],next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    if(url.includes('/live-markets'))return Promise.resolve(json(live))
    if(url.includes('/result-binding'))return Promise.resolve(json(null))
    if(url.includes('/trade-intents?')||url.includes('/combo-rfqs?'))return Promise.resolve(json({items:[],next_cursor:null}))
    return Promise.resolve(json(game))
  }))
  const view=mount(SportsCenter,{global:{plugins:[router]}});await flushPromises()
  await view.findAll('button').find(button=>button.text()==='查看详情')!.trigger('click');await flushPromises()
  const panel=view.get('[aria-label="Polymarket 市场信息"]')
  expect(panel.findAll('.market-category-heading h4').map(item=>item.text())).toEqual(['胜负','总分大小'])
  expect(panel.findAll('.market-tile h4').map(item=>item.text())).toEqual(['胜负','总分 218.5','总分 225.5','总分 230.5'])
  expect(panel.text()).not.toContain('让分')
  expect(panel.text()).not.toContain('Mock Home spread')
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

it('按运动类型显示默认回溯范围',async()=>{
  const fetchMock=vi.fn((url:string)=>{
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:[],next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    return Promise.resolve(json(null))
  })
  vi.stubGlobal('fetch',fetchMock)
  const view=mount(SportsCenter,{global:{plugins:[router]}});await flushPromises()
  expect(view.get('#range').text()).toContain('过去 3 小时至未来 12 小时')
  await router.push('/sports/football');await flushPromises()
  expect(view.get('#range').text()).toContain('过去 2 小时 30 分至未来 12 小时')
  const requests=fetchMock.mock.calls.map(([url])=>url).filter(url=>url.startsWith('/api/v1/games?'))
  expect(requests).toHaveLength(2)
  expect(requests.every(url=>!url.includes('from=')&&!url.includes('to='))).toBe(true)
  view.unmount()
})

it('按北京时间把当天和第二天赛事分隔显示',async()=>{
  const key=(offset:number)=>{const date=new Date(Date.now()+offset*86400e3+8*3600e3);return date.toISOString().slice(0,10)}
  const todayGame={...game,id:'today-game',starts_at:key(0)+'T12:00:00+08:00'}
  const tomorrowGame={...game,id:'tomorrow-game',starts_at:key(1)+'T12:00:00+08:00'}
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:[todayGame,tomorrowGame],next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    return Promise.resolve(json(null))
  }))
  const view=mount(SportsCenter,{global:{plugins:[router]}});await flushPromises()
  const dividers=view.findAll('.day-divider')
  expect(dividers).toHaveLength(2)
  expect(dividers[0].text()).toContain('今天')
  expect(dividers[1].text()).toContain('明天')
  view.unmount()
})
