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
    await context.route('**/*', async route => {
      const url = new URL(route.request().url())
      if (url.origin !== base) return route.abort()
      if (!url.pathname.startsWith('/api/')) return route.continue()
      const endpoint = url.pathname.split('/').pop()
      if (url.pathname.startsWith('/api/v1/exports/')) return route.fulfill({status:200,contentType:'text/csv',body:'模拟导出\n'})
      if (url.pathname.startsWith('/api/v1/history/')) return route.fulfill({status:200,json:{data:{items:[],next_cursor:null}}})
      if (endpoint === 'statistics') return route.fulfill({status:200,json:{data:{games_total:0,finished_total:0,trade_count:0,confirmed_fill_count:0,confirmed_combo_count:0,realized_pnl:null,scope:'模拟统计口径'}}})
      if (url.pathname==='/api/v1/system/status') return route.fulfill({status:200,json:{data:{database:{status:'ok',checked_at:'2026-01-01T00:00:00Z'},configuration:{football_key_configured:false,nba_key_configured:false,wallet_count:0,combo_enabled:false}}}})
      if (endpoint === 'audit-events') return route.fulfill({status:200,json:{data:{items:[]}}})
      if (endpoint === 'book') return route.fulfill({status:200,json:{data:{tick_size:'0.01',min_order_size:'5',observed_at:'2026-01-01T00:00:00Z',bids:[],asks:[]}}})
      if (url.pathname.includes('/order-context/')) return route.fulfill({status:200,json:{data:{home:'Mock Home',away:'Mock Away',market_question:'模拟市场问题',outcome_label:'Yes',starts_at:'2026-01-01T18:00:00Z',asset_id:'1'.repeat(77),protocol_version:'v1'}}})
      if (endpoint === 'preview') {const order=route.request().postDataJSON();await new Promise(resolve => setTimeout(resolve,250));return route.fulfill({status:200,json:{data:{...order,preview_token:'mock-preview',estimated_notional:'2.5',expires_at:'2099-01-01T00:00:00Z'}}})}
      if (endpoint === 'single-orders' && route.request().method()==='POST') return route.fulfill({status:201,json:{data:{intent:{id:'mock-order',state:'pending',matched_size:null,retry_after_seconds:null}}}})
      if (endpoint === 'combo-rfqs' && route.request().method()==='POST') {mockCombos=[{id:'mock-combo',state:'quoted',direction:'BUY',quantity:'10',wallet_address:'mock-wallet',external_rfq_id:'mock-rfq',error_code:null,legs:[{outcome_id:'o1',home:'Mock Home',away:'Mock Away',market_question:'模拟市场问题',outcome_label:'Yes',starts_at:'2026-01-01T18:00:00Z'}],quote:{id:'mock-quote',price:'0.5',total_required:'10',net_receive:'18',expires_at:'2099-01-01T00:00:00Z'}}];return route.fulfill({status:200,json:{data:mockCombos[0]}})}
      if (endpoint === 'combo-rfqs') return route.fulfill({status:200,json:{data:{items:mockCombos}}})
      if (endpoint === 'accept' && url.pathname.includes('/combo-rfqs/')) {mockCombos[0].state='executing';return route.fulfill({status:200,json:{data:mockCombos[0]}})}
      if (endpoint === 'wallets') return route.fulfill({status:200,json:{data:{items:[{id:'mock-wallet',label:'模拟钱包',wallet_address:'0x'+'1'.repeat(40),signer_address:'0x'+'2'.repeat(40),wallet_type:'EOA'}]}}})
      if (endpoint === 'status' && url.pathname.includes('/wallets/')) return route.fulfill({status:200,json:{data:{wallet:{id:'mock-wallet'},collateral_balance:null,allowance_status:'unknown',availability:'allowed',diagnostic:{code:'signer_mismatch',message:'签名私钥与配置的签名地址不一致。'}}}})
      if (endpoint === 'live-markets') {const call=++mockLiveCalls;if(call===1)await new Promise(resolve=>setTimeout(resolve,1000));return route.fulfill({status:200,json:{data:{match_status:'candidate',observed_at:'2026-01-01T18:00:00Z',events:[{id:'mock-event',title:'模拟赛事市场',slug:'mock-event',markets:[{id:'mock-market',question:'模拟胜负问题',group:null,type:'moneyline',closed:false,accepting_orders:true,outcomes:[{label:'Yes',price:call===1?'0.17':'0.42'},{label:'No',price:null}]}]}]}}})}
      if (endpoint === 'markets') return route.fulfill({status:200,json:{data:{refresh:{status:'succeeded'},items:[{link:{id:'mock-link',state:mockMappingState,can_confirm:mockMappingState==='candidate',evidence:{ordered:true}},market:{question:'模拟市场问题',gamma_market_id:'101',condition_id:'0x'+'a'.repeat(64),protocol_version:'v1',accepting_orders:true,combo_enabled:null,outcomes:[{id:'o1',label:'Yes',asset_kind:'ctf_token',asset_id:'1'.repeat(77)}]},event:{home:'Mock Home',away:'Mock Away',starts_at:'2026-01-01T18:00:00Z'}},{link:{id:'mock-link-2',state:mockMappingState,can_confirm:mockMappingState==='candidate',evidence:{ordered:true}},market:{question:'模拟第二市场',gamma_market_id:'102',condition_id:'0x'+'b'.repeat(64),protocol_version:'v1',accepting_orders:true,combo_enabled:null,outcomes:[{id:'o2',label:'No',asset_kind:'ctf_token',asset_id:'2'.repeat(77)}]},event:{home:'Mock Home',away:'Mock Away',starts_at:'2026-01-01T18:00:00Z'}}]}}})
      if (endpoint === 'confirm' && url.pathname.includes('/market-links/')) {mockMappingState='confirmed';return route.fulfill({status:200,json:{data:{}}})}
      if (endpoint === 'sync-schedules') return route.fulfill({status:200,json:{data:{revision:1,items:[{sport:'football',provider:'polymarket',utc_time:'00:00:00',enabled:true},{sport:'nba',provider:'balldontlie',utc_time:'08:00:00',enabled:true}]}}})
      if (endpoint === 'jobs') return route.fulfill({status:200,json:{data:{items:[]}}})
      if (endpoint === 'worker-status') return route.fulfill({status:200,json:{data:{workers:[]}}})
      if (endpoint === 'watch') return route.fulfill({status:200,json:{data:{state:'active'}}})
      if (endpoint === 'sync-runs') return route.fulfill({status:200,json:{data:{items:[]}}})
      if (endpoint === 'games') {
        const team = (id, name) => ({ id, source_name:name, name_zh:null, translation_status:'missing', logo_url:null })
        const game = { id:'mock-game', starts_at:'2026-01-01T18:00:00Z', status:'scheduled', home_team:team('mock-home','Mock Home'), away_team:team('mock-away','Mock Away'), home_score:null, away_score:null, league:null, last_synced_at:null }
        return route.fulfill({status:200,json:{data:{items:[game],next_cursor:null}}})
      }
      if (endpoint === 'mock-game') return route.fulfill({status:200,json:{data:{id:'mock-game',provider:'mock-test-provider',external_id:'mock-id',last_synced_at:null,recent_revisions:[]}}})
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
    await page.getByRole('heading', { name: '赛事总览' }).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `viewport ${viewport.width} 横向溢出`)
    assert.equal(await page.getByText('总览页面不显示赛事、报价或交易数据。').count(), 1)
    if (viewport.width < 600) {
      const menu = page.getByRole('button', { name: '菜单' })
      await menu.click()
      assert.equal(await menu.getAttribute('aria-expanded'), 'true')
    }
    await page.getByRole('link', { name: '足球赛事' }).click()
    await page.getByRole('heading', { name: '足球赛事中心' }).waitFor()
    await page.getByText('Mock Home').waitFor()
    assert.equal(await page.getByText('中文名待补充').count(), 2)
    assert.equal(await page.getByText('队徽待补充').count(), 2)
    assert.match(await page.locator('.game-meta').innerText(), /2026\/01\/02.*02:00/)
    await page.getByRole('button', {name:'关注赛事'}).click()
    await page.getByText('已关注，赛果任务将按计划执行。').waitFor()
    await page.getByRole('button', {name:'查看详情'}).click()
    await page.getByRole('dialog', {name:'赛事详情'}).waitFor()
    await page.getByText('正在查询市场…').waitFor()
    await page.keyboard.press('Escape')
    assert.equal(await page.getByRole('dialog', {name:'赛事详情'}).count(),0)
    await page.getByRole('button', {name:'查看详情'}).click()
    await page.getByText('模拟胜负问题').waitFor()
    await page.getByText('Yes：42.0%').waitFor()
    await page.getByText('No：未知').waitFor()
    await page.waitForTimeout(1100)
    assert.equal(await page.getByText('Yes：17.0%').count(),0)
    await page.getByText('来源：mock-test-provider · 来源 ID：mock-id').waitFor()
    await page.getByText('模拟市场问题').waitFor()
    await page.getByRole('button',{name:'核对并确认'}).first().click()
    await page.getByRole('button',{name:'我已核对，确认映射'}).click()
    await page.getByText('映射已确认，可进行订单预览。').waitFor()
    await page.getByRole('button',{name:'加入组合'}).nth(0).click()
    await page.getByRole('button',{name:'加入组合'}).nth(1).click()
    await page.getByText('已加入组合，请在组合交易页面核对。').waitFor()
    await page.getByRole('link',{name:'订单预览'}).first().click()
    await page.getByRole('heading',{name:'单场订单预览'}).waitFor()
    await page.getByLabel('限价').fill('0.50')
    await page.getByLabel('份额').fill('5')
    await page.getByRole('button',{name:'验证并预览'}).click()
    await page.getByLabel('份额').fill('6')
    await page.getByText('参数已变化，请重新预览。').waitFor()
    assert.equal(await page.getByRole('heading',{name:'确认真实资金订单'}).count(),0)
    await page.getByLabel('份额').fill('5')
    await page.getByRole('button',{name:'验证并预览'}).click()
    await page.getByRole('heading',{name:'确认真实资金订单'}).waitFor()
    await page.getByRole('button',{name:'确认并提交真实订单'}).click()
    await page.getByText('订单已记录；请查看状态，接受不代表成交。').waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `viewport ${viewport.width} 订单页横向溢出`)
    if (viewport.width < 600) await page.getByRole('button', {name:'菜单'}).click()
    await page.getByRole('link', {name:'组合交易'}).click()
    await page.getByRole('heading', {name:'组合询价'}).waitFor()
    await page.getByText('Mock Home 对 Mock Away · 模拟市场问题 · Yes').waitFor()
    await page.getByLabel('最高预算（含费用）').fill('10')
    await page.getByRole('button',{name:'请求真实报价'}).click()
    await page.getByText('已记录组合询价，请核对报价和状态。').waitFor()
    await page.getByRole('button',{name:'确认接受真实资金报价'}).click()
    await page.getByText('已处理确认请求；执行中不代表成交。').waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),true)
    if (viewport.width < 600) await page.getByRole('button', {name:'菜单'}).click()
    await page.getByRole('link', {name:'NBA 赛事'}).click()
    await page.getByRole('heading', {name:'NBA 赛事中心'}).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `viewport ${viewport.width} 赛事页横向溢出`)
    if (viewport.width < 600) await page.getByRole('button', {name:'菜单'}).click()
    await page.getByRole('link', {name:'系统管理'}).click()
    await page.getByRole('heading', {name:'同步与任务'}).waitFor()
    await page.getByText('未发现 worker 心跳；请启动独立 worker。').waitFor()
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
    await page.getByRole('link', {name:'钱包状态'}).click()
    await page.getByRole('heading', {name:'钱包状态'}).waitFor()
    await page.getByRole('button', {name:'查看只读状态'}).click()
    await page.getByText('余额：未知').waitFor()
    await page.getByText('地区状态：本次检查允许', {exact:false}).waitFor()
    await page.getByText('签名私钥与配置的签名地址不一致。', {exact:false}).waitFor()
    await page.getByText('授权状态：未检查').waitFor()
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
