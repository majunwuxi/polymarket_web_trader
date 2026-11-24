import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import SportsCenter from '../src/views/SportsCenter.vue'
import { session } from '../src/auth'

const json=(data:unknown,status=200)=>({ok:status>=200&&status<300,status,json:async()=>status>=200&&status<300?{data}:{error:{message:'模拟错误'}}}) as Response
const team=(id:string)=>({id,source_name:id,name_zh:null,translation_status:'missing',logo_url:null})
const game={id:'g1',provider:'polymarket',external_id:'90108495',starts_at:'2026-11-17T03:45:00Z',status:'scheduled',home_team:team('瑞士'),away_team:team('苏格兰'),home_score:null,away_score:null,league:{name:'欧国联'},last_synced_at:null,result_binding_state:'needs_review',has_matched_order:false,recent_revisions:[]}
const summary={sport:'football',as_of:'2026-11-16T08:00:00Z',totals:{total:3,bound:1,pending:1,needs_review:1,unbound:2,ready_to_confirm:1},items:[{game_id:'g1',external_id:'90108495',starts_at:'2026-11-17T03:45:00Z',game_status:'scheduled',home:'瑞士',away:'苏格兰',league:'欧国联',state:'needs_review',provider:'api_football',fixture_id:null,candidates:[{id:'123456',starts_at:'2026-11-17T03:45:00Z',home:'Switzerland',away:'Scotland',league:'UEFA Nations League'}],error_code:null,updated_at:'2026-11-16T08:00:00Z',next_attempt_at:'2026-11-16T08:10:00Z',can_quick_confirm:true}],has_more:false}
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/sports/:sport',component:SportsCenter}]})

beforeEach(async()=>{session.csrf='mock-csrf';HTMLDialogElement.prototype.showModal=function(){this.open=true};HTMLDialogElement.prototype.close=function(){this.open=false};await router.push('/sports/football');await router.isReady()})
afterEach(()=>vi.unstubAllGlobals())

function setup(summaryResponse:()=>Promise<Response>=()=>Promise.resolve(json(summary)),games=[game]){
  const fetchMock=vi.fn((url:string,options?:RequestInit)=>{
    if(url.startsWith('/api/v1/result-bindings/summary'))return summaryResponse()
    if(url.endsWith('/result-binding/confirm'))return Promise.resolve(json({state:'bound'}))
    if(url.startsWith('/api/v1/games?'))return Promise.resolve(json({items:games,next_cursor:null}))
    if(url.includes('/sync-runs'))return Promise.resolve(json({items:[]}))
    return Promise.resolve(json(null))
  })
  vi.stubGlobal('fetch',fetchMock)
  return {view:mount(SportsCenter,{global:{plugins:[router]}}),fetchMock}
}

it('足球赛事中心用球队链接展示未绑定，单候选点击直接绑定',async()=>{
  const {view,fetchMock}=setup();await flushPromises()
  expect(view.get('[aria-label="足球赛果绑定状态"]').exists()).toBe(true)
  expect(view.findAll('.binding-metrics strong').map(item=>item.text())).toEqual(['1','1','1'])
  expect(view.text()).toContain('未绑定球队')
  expect(view.text()).not.toContain('Switzerland 对 Scotland')
  const link=view.findAll('button').find(item=>item.text().includes('瑞士 对 苏格兰'))
  expect(link).toBeTruthy()
  await link!.trigger('click');await flushPromises()
  const request=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/games/g1/result-binding/confirm'))
  expect(request).toBeTruthy()
  expect((request![1] as RequestInit).method).toBe('POST')
  expect(((request![1] as RequestInit).headers as Record<string,string>)['X-CSRF-Token']).toBe('mock-csrf')
  expect(JSON.parse(String((request![1] as RequestInit).body))).toEqual({fixture_id:'123456'})
  await vi.waitFor(()=>expect(view.text()).toContain('已绑定 瑞士 对 苏格兰'))
  view.unmount()
})

it('多候选球队链接先展开，再由用户选择具体候选',async()=>{
  const multi={...summary,items:[{...summary.items[0],candidates:[
    summary.items[0].candidates[0],
    {id:'654321',starts_at:'2026-11-17T03:45:00Z',home:'Macedonia','away':'Slovenia',league:'UEFA Nations League'},
  ]}],totals:{...summary.totals,ready_to_confirm:1}}
  const {view,fetchMock}=setup(()=>Promise.resolve(json(multi)));await flushPromises()
  const link=view.findAll('button').find(item=>item.text().includes('瑞士 对 苏格兰'))
  expect(link?.text()).toContain('2 个候选')
  await link!.trigger('click');await flushPromises()
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/result-binding/confirm'))).toBe(false)
  const candidates=view.findAll('button').filter(item=>item.text()==='确认绑定')
  expect(candidates).toHaveLength(2)
  await candidates[1].trigger('click');await flushPromises()
  const request=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/games/g1/result-binding/confirm'))
  expect(JSON.parse(String((request![1] as RequestInit).body))).toEqual({fixture_id:'654321'})
  view.unmount()
})

it('赛事卡合并重复的队名提示，并把球队维护移入详情',async()=>{
  const {view}=setup();await flushPromises()
  const card=view.get('.game-card')
  expect(view.findAll('.game-name-note')).toHaveLength(1)
  expect(view.get('.game-name-note').text()).toBe('中文名待补充')
  expect(card.text()).not.toContain('维护球队')
  expect(card.text()).not.toContain('确认订单后开始绑定赛果')
  expect(card.get('.game-meta').text()).not.toContain('UTC+8')
  expect(card.get('.game-meta').text().match(/赛果待确认/g)).toHaveLength(1)
  await card.findAll('button').find(button=>button.text()==='查看详情')!.trigger('click');await flushPromises()
  const summaryInfo=view.get('[aria-label="比赛信息"]')
  expect(summaryInfo.findAll('button').filter(button=>button.text()==='维护名称')).toHaveLength(2)
  await summaryInfo.findAll('button').find(button=>button.text()==='维护名称')!.trigger('click');await flushPromises()
  const teamDialog=view.get('.team-dialog')
  expect(teamDialog.text()).toContain('维护球队：瑞士')
  // 维护窗体是叠在详情之上的独立弹窗，详情仍然打开。
  expect(teamDialog.attributes('open')).toBeDefined()
  expect(view.get('[aria-label="比赛信息"]').exists()).toBe(true)
  view.unmount()
})

it('球队维护弹窗保存后留在详情，并就地更新队名',async()=>{
  const {view,fetchMock}=setup();await flushPromises()
  await view.get('.game-card').findAll('button').find(button=>button.text()==='查看详情')!.trigger('click');await flushPromises()
  await view.get('[aria-label="比赛信息"]').findAll('button').find(button=>button.text()==='维护名称')!.trigger('click');await flushPromises()
  await view.get('.team-dialog input#chinese').setValue('瑞士队')
  await view.get('.team-dialog form').trigger('submit')
  await flushPromises()
  const request=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/teams/瑞士'))
  expect((request![1] as RequestInit).method).toBe('PATCH')
  // 详情没有被关闭，队名就地更新。
  expect(view.get('[aria-label="比赛信息"]').exists()).toBe(true)
  expect(view.get('[aria-label="比赛信息"]').text()).toContain('瑞士队')
  view.unmount()
})

it('完赛卡片用比分板显示全场比分并突出获胜方',async()=>{
  const finished={...game,status:'finished',home_score:120,away_score:136}
  const {view}=setup(undefined,[finished]);await flushPromises()
  const board=view.get('.game-scoreboard')
  expect(board.text()).toContain('全场比分')
  expect(board.findAll('strong').map(item=>item.text())).toEqual(['120','136'])
  expect(board.get('.score-winner').text()).toBe('136')
  expect(board.attributes('aria-label')).toBe('全场比分：瑞士 120 比 136 苏格兰')
  view.unmount()
})

it('有中文名时不因目录来源标记队名待核对',async()=>{
  const directoryTeam=(id:string,zh:string)=>({...team(id),name_zh:zh,name_zh_source:'sportsmatch'})
  const directoryGame={...game,home_team:directoryTeam('Lithuania','立陶宛'),away_team:directoryTeam('Liechtenstein','列支敦士登')}
  const {view}=setup(undefined,[directoryGame]);await flushPromises()
  expect(view.text()).toContain('立陶宛')
  expect(view.text()).toContain('列支敦士登')
  expect(view.find('.game-name-note').exists()).toBe(false)
  expect(view.text()).not.toContain('队名待核对')
  view.unmount()
})

it('NBA 赛事中心不请求也不显示赛果绑定统计',async()=>{
  await router.push('/sports/nba')
  const {view,fetchMock}=setup();await flushPromises()
  expect(view.find('[aria-label="足球赛果绑定状态"]').exists()).toBe(false)
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/result-bindings/summary'))).toBe(false)
  view.unmount()
})

it('自动匹配中的赛事显示比赛、原因和重试时间，不提供确认按钮',async()=>{
  const pending={...summary,totals:{total:1,bound:0,pending:1,needs_review:0,unbound:1,ready_to_confirm:0},items:[],pending_items:[{...summary.items[0],state:'pending',candidates:[],error_code:'no_matching_fixture'}]}
  const {view}=setup(()=>Promise.resolve(json(pending)));await flushPromises()
  const queue=view.get('[aria-label="自动匹配中的赛事"]')
  expect(queue.get('a').text()).toBe('瑞士 对 苏格兰')
  expect(queue.get('a').attributes('href')).toContain('game=g1')
  expect(queue.text()).toContain('未找到匹配赛程')
  expect(queue.text()).toContain('下次查询')
  expect(view.findAll('button').some(item=>item.text()==='确认绑定')).toBe(false)
  expect(view.findAll('button').some(item=>item.classes().includes('binding-team-link'))).toBe(false)
  expect(view.findAll('.binding-metrics strong').map(item=>item.text())).toEqual(['0','1','0'])
  view.unmount()
})

it('绑定统计失败不阻塞赛事列表',async()=>{
  const {view}=setup(()=>Promise.resolve(json({error:{message:'mock'}},503)));await flushPromises()
  expect(view.text()).toContain('赛果绑定统计加载失败')
  expect(view.text()).toContain('瑞士')
  expect(view.text()).toContain('苏格兰')
  view.unmount()
})
