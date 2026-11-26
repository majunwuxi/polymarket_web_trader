import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import assert from 'node:assert/strict'

// 所有认证响应为明确的模拟数据；此验收不访问生产服务或执行交易。
const probe = createServer()
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve))
const port = probe.address().port
await new Promise(resolve => probe.close(resolve))
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { windowsHide: true, stdio: 'ignore' })
const base = `http://127.0.0.1:${port}`
let browser
try {
  for (let attempt = 0; ; attempt++) {
    try { if ((await fetch(base)).ok) break } catch {}
    if (attempt >= 100) throw new Error('Vite 测试服务未启动')
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) })
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport })
    let authenticated = false
    let mockMappingState = 'candidate'
    let mockCombos = []
    let mockLiveCalls = 0
    let mockProvider = 'mock-test-provider'
    let prepared = 0
    let footballDisplay = null
    let tradeLimits = {order_limit_percent:null,order_limit_floor_usd:'1',max_daily_usd:null}
    await context.route('**/*', async route => {
      const url = new URL(route.request().url())
      if (url.origin !== base) return route.abort()
      if (!url.pathname.startsWith('/api/')) return route.continue()
      const endpoint = url.pathname.split('/').pop()
      if (url.pathname.startsWith('/api/v1/exports/')) return route.fulfill({status:200,contentType:'text/csv',body:'模拟导出\n'})
      if (url.pathname.startsWith('/api/v1/history/')) return route.fulfill({status:200,json:{data:{items:[],next_cursor:null}}})
      if (endpoint === 'statistics') return route.fulfill({status:200,json:{data:{games_total:0,finished_total:0,trade_count:0,confirmed_fill_count:0,confirmed_combo_count:0,realized_pnl:null,scope:'模拟统计口径'}}})
      if (endpoint === 'pnl-calendar') return route.fulfill({status:200,json:{data:{month:'2026-11',first_day:'2026-11-01',last_day:'2026-11-30',days:[
        {day:'2026-11-01',empty:false,invested:'116',settled_invested:'116',returned:'133.7',pnl:'17.7',tone:'profit',settled_count:1,pending_count:0,has_estimate:false,by_sport:[{sport:'football',name:'足球',invested:'116',settled_invested:'116',returned:'133.7',pnl:'17.7',tone:'profit',settled_count:1,pending_count:0,has_estimate:false}]},
        {day:'2026-11-02',empty:false,invested:'136',settled_invested:'0',returned:'0',pnl:'0',tone:'pending',settled_count:0,pending_count:1,has_estimate:false,by_sport:[{sport:'nba',name:'篮球',invested:'136',settled_invested:'0',returned:'0',pnl:'0',tone:'pending',settled_count:0,pending_count:1,has_estimate:false}]},
        {day:'2026-11-03',empty:true,invested:'0',settled_invested:'0',returned:'0',pnl:'0',tone:'empty',settled_count:0,pending_count:0,has_estimate:false,by_sport:[]},
      ],summary:{invested:'252',settled_invested:'116',returned:'133.7',pnl:'17.7',tone:'profit',settled_count:1,pending_count:1,has_estimate:false},scope:'模拟盈亏日历口径',currency:'pUSD'}}})
      if (url.pathname==='/api/v1/system/status') return route.fulfill({status:200,json:{data:{database:{status:'ok',checked_at:'2026-01-01T00:00:00Z'},configuration:{football_key_configured:false,nba_key_configured:false,wallet_count:0,combo_enabled:false}}}})
      if (endpoint === 'audit-events') return route.fulfill({status:200,json:{data:{items:[]}}})
      if (url.pathname==='/api/v1/system/football-display') {
        if(route.request().method()==='PUT')footballDisplay=route.request().postDataJSON().competition_ids
        return route.fulfill({status:200,json:{data:{competition_ids:footballDisplay,options:[{id:'1234',label:'欧冠'},{id:'306',label:'英超'}]}}})
      }
      if (url.pathname==='/api/v1/system/trade-limits') {
        if(route.request().method()==='PUT')tradeLimits=route.request().postDataJSON()
        return route.fulfill({status:200,json:{data:tradeLimits}})
      }
      if (endpoint==='trade-limits') return route.fulfill({status:200,json:{data:{order_limit_percent:'6',order_limit_floor_usd:'1',remaining_daily_usd:'20'}}})
      if (endpoint === 'trade-intents') return route.fulfill({status:200,json:{data:{items:[],next_cursor:null}}})
      if (url.pathname.includes('/combo-legs/')) return route.fulfill({status:200,json:{data:{accepting_orders:true}}})
      if (endpoint === 'prepare-selection') {
        const id=++prepared%2===1?'1':'2'
        return route.fulfill({status:200,json:{data:{market_link_id:'link-'+id,outcome_id:'o'+id}}})
      }
      if (endpoint === 'book') return route.fulfill({status:200,json:{data:{tick_size:'0.01',min_order_size:'5',observed_at:'2026-01-01T00:00:00Z',bids:[{price:'0.40',size:'10'}],asks:[{price:'0.42',size:'15'}]}}})
      if (url.pathname.includes('/order-context/')) return route.fulfill({status:200,json:{data:{home:'Mock Home',away:'Mock Away',market_question:'模拟市场问题',outcome_label:'Yes',starts_at:'2026-01-01T18:00:00Z',asset_id:'1'.repeat(77),protocol_version:'v1'}}})
      if (endpoint === 'single-orders' && route.request().method()==='POST') return route.fulfill({status:201,json:{data:{intent:{id:'mock-order',state:'pending',matched_size:null,retry_after_seconds:null}}}})
      if (endpoint === 'combo-rfqs' && route.request().method()==='POST') {mockCombos=[{id:'mock-combo',state:'executing',direction:'BUY',quantity:'10',wallet_address:'mock-wallet',external_rfq_id:'mock-rfq',error_code:null,legs:[{outcome_id:'o1',home:'Mock Home',away:'Mock Away',market_question:'模拟市场问题',outcome_label:'Yes',starts_at:'2026-01-01T18:00:00Z'}],quote:{id:'mock-quote',price:'0.5',total_required:'10',net_receive:'18',expires_at:'2099-01-01T00:00:00Z'}}];return route.fulfill({status:200,json:{data:mockCombos[0]}})}
      if (endpoint === 'combo-rfqs') return route.fulfill({status:200,json:{data:{items:mockCombos}}})
      if (endpoint === 'accept' && url.pathname.includes('/combo-rfqs/')) {mockCombos[0].state='executing';return route.fulfill({status:200,json:{data:mockCombos[0]}})}
      if (endpoint === 'deposit-address') return route.fulfill({status:200,json:{data:{
        address:'0x'+'3'.repeat(40),wallet_address:'0x'+'1'.repeat(40),chain_id:137,
        assets:[{symbol:'USDC',contract:'0x'+'4'.repeat(40),minimum_usd:'2'}],checked_at:'2026-01-01T00:00:00Z',
      }}})
      if (endpoint === 'wallets') return route.fulfill({status:200,json:{data:{items:[{id:'mock-wallet',label:'模拟钱包',wallet_address:'0x'+'1'.repeat(40),signer_address:'0x'+'2'.repeat(40),wallet_type:'EOA'}]}}})
      if (endpoint === 'status' && url.pathname.includes('/wallets/')) return route.fulfill({status:200,json:{data:{wallet:{id:'mock-wallet'},collateral_balance:null,allowance_status:'unknown',availability:'allowed',diagnostic:{code:'signer_mismatch',message:'签名私钥与配置的签名地址不一致。'}}}})
      if (endpoint === 'live-markets') {const call=++mockLiveCalls;if(call===1)await new Promise(resolve=>setTimeout(resolve,1000));return route.fulfill({status:200,json:{data:{match_status:mockProvider==='polymarket'?'source_identity':'candidate',observed_at:'2026-01-01T18:00:00Z',events:[{id:'mock-event',title:'模拟赛事市场',slug:'mock-event',markets:[{id:'mock-market',question:'模拟胜负问题',group:null,type:'moneyline',closed:false,accepting_orders:true,outcomes:[{label:'Yes',price:call===1?'0.17':'0.42'},{label:'No',price:null}]}]}]}}})}
      if (endpoint === 'markets') return route.fulfill({status:200,json:{data:{refresh:{status:'succeeded'},items:[{link:{id:'mock-link',state:mockMappingState,can_confirm:mockMappingState==='candidate',evidence:{ordered:true}},market:{question:'模拟市场问题',gamma_market_id:'101',condition_id:'0x'+'a'.repeat(64),protocol_version:'v1',accepting_orders:true,combo_enabled:null,outcomes:[{id:'o1',label:'Yes',asset_kind:'ctf_token',asset_id:'1'.repeat(77)}]},event:{home:'Mock Home',away:'Mock Away',starts_at:'2026-01-01T18:00:00Z'}},{link:{id:'mock-link-2',state:mockMappingState,can_confirm:mockMappingState==='candidate',evidence:{ordered:true}},market:{question:'模拟第二市场',gamma_market_id:'102',condition_id:'0x'+'b'.repeat(64),protocol_version:'v1',accepting_orders:true,combo_enabled:null,outcomes:[{id:'o2',label:'No',asset_kind:'ctf_token',asset_id:'2'.repeat(77)}]},event:{home:'Mock Home',away:'Mock Away',starts_at:'2026-01-01T18:00:00Z'}}]}}})
      if (endpoint === 'confirm' && url.pathname.includes('/market-links/')) {mockMappingState='confirmed';return route.fulfill({status:200,json:{data:{}}})}
      if (endpoint === 'sync-schedules') return route.fulfill({status:200,json:{data:{revision:1,items:[{sport:'football',provider:'polymarket',utc_time:'00:00:00',enabled:true},{sport:'nba',provider:'balldontlie',utc_time:'08:00:00',enabled:true}]}}})
      if (endpoint === 'jobs') return route.fulfill({status:200,json:{data:{items:[]}}})
      if (endpoint === 'worker-status') return route.fulfill({status:200,json:{data:{workers:[]}}})
      if (endpoint === 'watch') return route.fulfill({status:200,json:{data:{state:'active'}}})
      if (endpoint === 'sync-runs') return route.fulfill({status:200,json:{data:{items:[]}}})
      if (endpoint === 'summary' && url.pathname.includes('/result-bindings/')) return route.fulfill({status:200,json:{data:{sport:'football',as_of:'2026-01-01T00:00:00Z',totals:{total:0,bound:0,pending:0,needs_review:0,unbound:0,ready_to_confirm:0},items:[],has_more:false}}})
      if (endpoint === 'games') {
        if (url.searchParams.get('watched')==='true') {
          const game = {id:'mock-followed',starts_at:new Date(Date.now()+3600000).toISOString(),
            home_team:{source_name:'关注主队'},away_team:{source_name:'关注客队'}}
          return route.fulfill({status:200,json:{data:{items:url.searchParams.get('sport')==='football'?[game]:[],next_cursor:null}}})
        }
        const team = (id, name) => ({ id, source_name:name, name_zh:null, translation_status:'missing', logo_url:null })
        const game = { id:'mock-game', starts_at:'2026-01-01T18:00:00Z', status:'scheduled', home_team:team('mock-home','Mock Home'), away_team:team('mock-away','Mock Away'), home_score:null, away_score:null, league:null, last_synced_at:null }
        return route.fulfill({status:200,json:{data:{items:[game],next_cursor:null}}})
      }
      if (endpoint === 'mock-game') return route.fulfill({status:200,json:{data:{id:'mock-game',provider:mockProvider,external_id:'mock-id',last_synced_at:null,recent_revisions:[]}}})
      const data = endpoint === 'csrf' ? { csrf_token: 'mock-login-csrf' } : endpoint === 'login' ? { authenticated: true, csrf_token: 'mock-session-csrf' } : { username: 'mock-admin', csrf_token: 'mock-session-csrf', expires_at: '2099-01-01T00:00:00Z' }
      if (endpoint === 'session' && !authenticated) return route.fulfill({ status: 401, json: { error: { message: 'mock unauthorized' } } })
      if (endpoint === 'login') authenticated = true
      if (endpoint === 'logout') { authenticated = false; return route.fulfill({ status: 204 }) }
      return route.fulfill({ status: 200, json: { data } })
    })
    const page = await context.newPage()
    await page.goto(base)
    await page.getByLabel('用户名').fill('mock-admin')
    await page.getByLabel('密码', { exact: true }).fill('mock-password')
    await page.getByRole('button', { name: /登录工作台/ }).click()
    await page.getByRole('heading', { name: '赛事工作台' }).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `viewport ${viewport.width} 横向溢出`)
    await page.getByRole('heading', { name: /即将开赛的关注赛事/ }).waitFor()
    const followed=page.getByRole('link',{name:/关注主队 对 关注客队/})
    await followed.waitFor()
    assert.match(await followed.getAttribute('href'), /watched=1&game=mock-followed/)
    assert.equal(await followed.locator('.schedule-time strong').count(),1)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true)
    if (viewport.width < 600) {
      const menu = page.getByRole('button', { name: '菜单' })
      await menu.click()
      assert.equal(await menu.getAttribute('aria-expanded'), 'true')
    }
    await page.getByRole('navigation', {name:'赛事与交易入口'}).getByRole('link', {name:'足球赛事',exact:true}).click()
    await page.getByRole('heading', { name: '足球赛事中心' }).waitFor()
    await page.getByRole('heading', { name: '足球赛果绑定状态' }).waitFor()
    await page.getByText('Mock Home').waitFor()
    assert.equal(await page.getByText('中文名待补充').count(), 1)
    assert.equal(await page.getByText('队徽待补充').count(), 2)
    assert.match(await page.locator('.game-meta').innerText(), /2026\/01\/02.*02:00/)
    await page.getByRole('button', {name:'关注赛事'}).click()
    await page.getByText('已关注，赛果任务将按计划执行。').waitFor()
    await page.getByRole('button', {name:'查看详情'}).click()
    await page.getByRole('dialog', {name:'赛事详情'}).waitFor()
    await page.getByText('正在查询市场…').waitFor()
    await page.keyboard.press('Escape')
    assert.equal(await page.getByRole('dialog', {name:'赛事详情'}).count(),0)
    mockProvider='mock-test-provider'
    await page.getByRole('button', {name:'查看详情'}).click()
    await page.locator('.outcome-button').first().waitFor()
    assert.equal(await page.locator('.outcome-button').first().isDisabled(), true)
    assert.equal(await page.getByRole('link',{name:'订单预览'}).count(),0)
    assert.equal(await page.getByRole('link',{name:/在 Polymarket 查看/}).count(),1)
    await page.getByRole('button',{name:'关闭详情'}).click()
    mockProvider='polymarket'
    await page.getByRole('button', {name:'查看详情'}).click()
    await page.getByText('模拟胜负问题').waitFor()
    await page.locator('.market-tile').getByText('42.0%').waitFor()
    await page.locator('.market-tile').getByText('未知').waitFor()
    await page.waitForTimeout(1100)
    assert.equal(await page.locator('.market-tile').getByText('17.0%').count(),0)
    await page.getByText('来源：polymarket · 来源 ID：mock-id').waitFor()
    await page.getByRole('button',{name:'将Yes加入组合'}).click()
    await page.getByRole('button',{name:'查看已选 1 腿'}).waitFor()
    await page.getByRole('button',{name:'将No加入组合'}).click()
    await page.getByRole('button',{name:'查看已选 2 腿'}).waitFor()
    await page.locator('.outcome-button').first().click()
    await page.getByRole('heading',{name:'单场订单',exact:true}).waitFor()
    await page.getByLabel('限价').fill('0.50')
    await page.getByLabel('输入方式').selectOption('shares')
    await page.getByLabel('份额').fill('5')
    await page.getByRole('button',{name:'刷新盘口'}).click()
    await page.getByText(/限价内可见深度 15 份/).waitFor()
    await page.getByText(/预估均价 0.420000 pUSD/).waitFor()
    await page.getByText(/提交后预计剩余 17.5 pUSD/).waitFor()
    assert.equal(await page.getByLabel('份额').inputValue(),'5')
    assert.equal(new URL(page.url()).pathname,'/sports/football')
    await page.getByRole('button',{name:'按当前价直接下单'}).click()
    await page.getByText('订单已记录；挂单中，请查看状态。接受不代表成交。').waitFor()
    assert.equal(await page.getByRole('heading',{name:'确认真实资金订单'}).count(),0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `viewport ${viewport.width} 订单页横向溢出`)
    await page.getByRole('button',{name:'关闭单场交易'}).click()
    await page.getByRole('button',{name:'查看已选 2 腿'}).click()
    await page.getByRole('link',{name:'查看组合交易'}).click()
    await page.getByRole('heading', {name:'组合交易'}).waitFor()
    await page.getByText('Mock Home 对 Mock Away · 模拟胜负问题 · Yes').waitFor()
    await page.getByLabel('最高预算（含费用）').fill('10')
    await page.getByRole('button',{name:'按当前市价直接下单'}).click()
    await page.getByText('已按当前市场价提交，执行中不代表成交。').waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),true)
    if (viewport.width < 600) await page.getByRole('button', {name:'菜单'}).click()
    await page.getByRole('link', {name:'NBA 赛事',exact:true}).click()
    await page.getByRole('heading', {name:'NBA 赛事中心'}).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `viewport ${viewport.width} 赛事页横向溢出`)
    if (viewport.width < 600) await page.getByRole('button', {name:'菜单'}).click()
    await page.getByRole('link', {name:'系统管理'}).click()
    await page.getByRole('heading', {name:'同步与任务'}).waitFor()
    await page.getByText('未发现 worker 心跳；请启动独立 worker。').waitFor()
    await page.getByLabel('单笔余额比例（%，留空关闭）').fill('6')
    await page.getByLabel('单笔上限最低值（pUSD）').fill('1')
    await page.getByLabel('每钱包每日上限（pUSD，留空关闭）').fill('20')
    await page.getByRole('button',{name:'保存交易限额'}).click()
    await page.getByLabel('显示范围').selectOption('selected')
    await page.getByLabel('英超',{exact:true}).uncheck()
    await page.getByRole('button',{name:'保存显示设置'}).click()
    await page.getByText('足球赛事显示设置已保存。').waitFor()
    assert.deepEqual(footballDisplay,['1234'])
    await page.getByText('交易限额已保存，下次提交时生效。').waitFor()
    assert.deepEqual(tradeLimits,{order_limit_percent:'6',order_limit_floor_usd:'1',max_daily_usd:'20'})
    assert.equal(await page.getByLabel('football UTC 同步时间').inputValue(),'00:00')
    await page.getByRole('button', {name:'保存计划'}).click()
    await page.getByText('同步计划已保存。').waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `viewport ${viewport.width} 状态页横向溢出`)
    if (viewport.width < 600) await page.getByRole('button', {name:'菜单'}).click()
    await page.getByRole('link', {name:'历史与导出'}).click()
    await page.getByRole('heading', {name:'历史与导出'}).waitFor()
    await page.getByText('模拟统计口径').waitFor()
    await page.getByText('没有符合条件的赛事。').waitFor()
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'导出赛事 CSV'}).click()])
    assert.equal(download.suggestedFilename(),'polysports-games.csv')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),true)
    if (viewport.width < 600) await page.getByRole('button', {name:'菜单'}).click()
    await page.getByRole('link', {name:'每日盈亏'}).click()
    await page.getByRole('heading',{name:'每日盈亏日历'}).waitFor()
    await page.getByText(/归日按下单时间/).waitFor()
    await page.getByText(/本月投入 252 pUSD/).waitFor()
    const profitCell=page.locator('.calendar-cell.calendar-profit').first()
    await profitCell.waitFor()
    assert.match(await profitCell.innerText(),/投入 116/)
    await profitCell.click()
    await page.getByRole('heading',{name:/明细/}).waitFor()
    await page.getByText('足球',{exact:true}).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `viewport ${viewport.width} 盈亏日历横向溢出`)
    if (viewport.width < 600) await page.getByRole('button', {name:'菜单'}).click()
    await page.getByRole('link', {name:'钱包状态'}).click()
    await page.getByRole('heading', {name:'钱包状态'}).waitFor()
    await page.getByRole('button', {name:'查看只读状态'}).click()
    await page.getByText('可用余额').waitFor()
    await page.getByText('地区状态：本次检查允许', {exact:false}).waitFor()
    await page.getByText('签名私钥与配置的签名地址不一致。', {exact:false}).waitFor()
    await page.getByText('授权状态：未检查').waitFor()
    await page.getByRole('button', {name:'查询 Polygon 充值地址'}).click()
    const depositPanel=page.getByRole('region',{name:'Polygon 充值'})
    await depositPanel.getByText(/Polygon PoS.*Chain ID 137/).waitFor()
    await depositPanel.getByText('最低 2 USD 等值', {exact:true}).waitFor()
    const qr=depositPanel.getByRole('img',{name:'Polygon 充值地址二维码'})
    await qr.waitFor()
    assert.match(await qr.getAttribute('src'), /^data:image\/png;base64,/)
    await page.waitForFunction(()=>document.querySelector('.deposit-qr')?.naturalWidth===240)
    await depositPanel.locator('summary').filter({hasText:'USDC'}).click()
    await depositPanel.getByText('0x'+'4'.repeat(40), {exact:true}).waitFor()
    await page.getByRole('button',{name:'关闭充值弹窗'}).click()
    await page.getByRole('dialog',{name:'Polygon 充值地址'}).waitFor({state:'hidden'})
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `viewport ${viewport.width} 钱包页横向溢出`)
    await page.getByRole('button', { name: '退出' }).click()
    await page.getByRole('heading', { name: '欢迎回来' }).waitFor()
    console.log(`模拟浏览器测试通过: ${viewport.width}x${viewport.height}`)
    await context.close()
  }
} finally {
  if (browser) await browser.close()
  server.kill()
}
