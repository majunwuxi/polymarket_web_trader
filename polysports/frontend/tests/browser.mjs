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
    await context.route('**/*', async route => {
      const url = new URL(route.request().url())
      if (url.origin !== base) return route.abort()
      if (!url.pathname.startsWith('/api/')) return route.continue()
      const endpoint = url.pathname.split('/').pop()
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
    assert.equal(await page.getByText('当前页面不包含真实赛事、报价或交易数据。').count(), 1)
    if (viewport.width < 600) {
      const menu = page.getByRole('button', { name: '菜单' })
      await menu.click()
      assert.equal(await menu.getAttribute('aria-expanded'), 'true')
    }
    await page.getByRole('button', { name: '退出' }).click()
    await page.getByRole('heading', { name: '欢迎回来' }).waitFor()
    console.log(`模拟浏览器测试通过: ${viewport.width}x${viewport.height}`)
    await context.close()
  }
} finally {
  if (browser) await browser.close()
  server.kill()
}
