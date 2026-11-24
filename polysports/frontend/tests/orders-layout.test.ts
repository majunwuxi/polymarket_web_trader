import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import Orders from '../src/views/Orders.vue'

const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: Orders }] })
beforeEach(async () => { await router.push('/'); await router.isReady() })
afterEach(() => vi.unstubAllGlobals())

describe('交易记录左右两列与隐藏规则（模拟接口）', () => {
  it('单场与组合是同一容器的两个并列区块，不上下堆叠', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(
      url.includes('/combo-rfqs') ? json({ data: { items: [], next_cursor: null } }) : json({ data: { items: [], next_cursor: null } }))))
    const view = mount(Orders, { global: { plugins: [router] } })
    await flushPromises()
    const columns = view.get('.order-columns')
    const sections = Array.from(columns.element.children).filter(
      el => el.tagName === 'SECTION' && el.classList.contains('welcome-card'))
    expect(sections).toHaveLength(2)
    expect(sections[0].getAttribute('aria-label')).toBe('单场交易记录')
    expect(sections[1].getAttribute('aria-label')).toBe('组合交易记录')
    expect(sections[0].textContent).toContain('单场订单')
    expect(view.text()).toContain('开赛超过 24 小时且已结算的历史记录不再显示')
    view.unmount()
  })

  it('两个列表都请求后端隐藏老旧已结算记录，翻页时同样带上', async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(
      url.includes('/combo-rfqs')
        ? json({ data: { items: [], next_cursor: 'mock-combo-cursor' } })
        : json({ data: { items: [], next_cursor: 'mock-order-cursor' } })))
    vi.stubGlobal('fetch', fetchMock)
    const view = mount(Orders, { global: { plugins: [router] } })
    await flushPromises()
    const urls = fetchMock.mock.calls.map(([url]) => String(url))
    expect(urls.some(u => u.startsWith('/api/v1/trade-intents?') && u.includes('hide_settled=true'))).toBe(true)
    expect(urls.some(u => u.startsWith('/api/v1/combo-rfqs?') && u.includes('hide_settled=true') && u.includes('state=confirmed'))).toBe(true)
    await view.findAll('button').find(b => b.text() === '加载更多')!.trigger('click')
    await view.findAll('button').find(b => b.text() === '加载更多组合')!.trigger('click')
    await flushPromises()
    const paged = fetchMock.mock.calls.map(([url]) => String(url))
    expect(paged.some(u => u.includes('cursor=mock-order-cursor') && u.includes('hide_settled=true'))).toBe(true)
    expect(paged.some(u => u.includes('cursor=mock-combo-cursor') && u.includes('hide_settled=true'))).toBe(true)
    view.unmount()
  })
})


it('结果未知的订单列表仍隐藏已结算旧记录', async () => {
  await router.push('/?state=unknown')
  const fetchMock = vi.fn((url: string) => Promise.resolve(json({ data: { items: [], next_cursor: null } })))
  vi.stubGlobal('fetch', fetchMock)
  const view = mount(Orders, { global: { plugins: [router] } })
  await flushPromises()
  for (const path of ['/api/v1/trade-intents?', '/api/v1/combo-rfqs?']) {
    expect(fetchMock.mock.calls.some(([url]) => url.startsWith(path) && url.includes('state=unknown') && url.includes('hide_settled=true'))).toBe(true)
  }
  expect(view.text()).toContain('显示提交结果待核实的组合')
  view.unmount()
})
