import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Login from '../src/views/Login.vue'
import Dashboard from '../src/views/Dashboard.vue'
import { clearSession, session } from '../src/auth'

// 本文件所有 API 返回值均为模拟测试数据，不访问真实服务。
const json = (body: unknown, status = 200) => ({ status, ok: status >= 200 && status < 300, json: async () => body }) as Response
const router = createRouter({ history: createMemoryHistory(), routes: [
  { path: '/login', component: Login }, { path: '/', component: Dashboard },
] })

beforeEach(async () => { clearSession(); await router.push('/login'); await router.isReady() })
afterEach(() => vi.unstubAllGlobals())

describe('管理员页面（模拟接口）', () => {
  it('登录使用 CSRF，并在提交后清空密码', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ data: { csrf_token: 'mock-csrf' } }))
      .mockResolvedValueOnce(json({ data: { authenticated: true, csrf_token: 'mock-session-csrf' } }))
    vi.stubGlobal('fetch', fetchMock)
    const view = mount(Login, { global: { plugins: [router] } })
    await view.get('#username').setValue('mock-admin')
    await view.get('#password').setValue('mock-password')
    await view.get('form').trigger('submit.prevent'); await flushPromises()
    expect(fetchMock.mock.calls[1][1].headers['X-CSRF-Token']).toBe('mock-csrf')
    expect((view.get('#password').element as HTMLInputElement).value).toBe('')
    expect(router.currentRoute.value.path).toBe('/')
  })
  it('限流时显示提示且清空密码', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json({ data: { csrf_token: 'mock-csrf' } }))
      .mockResolvedValueOnce(json({ error: { retry_after_seconds: 30 } }, 429)))
    const view = mount(Login, { global: { plugins: [router] } })
    await view.get('#password').setValue('mock-password')
    await view.get('form').trigger('submit.prevent'); await flushPromises()
    expect(view.text()).toContain('30 秒')
    expect((view.get('#password').element as HTMLInputElement).value).toBe('')
  })
  it('退出失败保留会话，移动菜单可切换，未完成功能不可点击', async () => {
    session.username = 'mock-admin'; session.csrf = 'mock-session-csrf'; session.authenticated = true
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('mock network error')))
    const view = mount(Dashboard, { global: { plugins: [router] } })
    expect(view.findAll('[aria-disabled="true"]')).toHaveLength(1)
    expect(view.text()).toContain('不显示赛事')
    await view.get('.menu-button').trigger('click')
    expect(view.get('.menu-button').attributes('aria-expanded')).toBe('true')
    await view.get('.text-button').trigger('click'); await flushPromises()
    expect(view.text()).toContain('退出请求未确认')
    expect(session.csrf).toBe('mock-session-csrf')
  })
})
