// 本文件所有 API 返回值均为模拟测试数据，不访问真实服务。
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PnlCalendar from '../src/views/PnlCalendar.vue'

const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response
const router = createRouter({
  history: createMemoryHistory(),
  routes: [{ path: '/', component: PnlCalendar }, { path: '/login', component: PnlCalendar }],
})
beforeEach(async () => { await router.push('/'); await router.isReady() })
afterEach(() => vi.unstubAllGlobals())

function day(day: string, extra: Record<string, unknown> = {}) {
  return {
    day, empty: true, invested: '0', settled_invested: '0', unpriced_invested: '0', returned: '0',
    pnl: '0', tone: 'empty', settled_count: 0, unpriced_count: 0, pending_count: 0,
    has_estimate: false, by_sport: [], ...extra,
  }
}

function payload() {
  return {
    data: {
      month: '2026-11',
      first_day: '2026-11-01',
      last_day: '2026-11-03',
      days: [
        day('2026-11-01', {
          empty: false, invested: '136', settled_invested: '100', returned: '133', pnl: '33',
          tone: 'profit', settled_count: 1, pending_count: 1,
          by_sport: [
            { sport: 'football', name: '足球', invested: '136', settled_invested: '100', unpriced_invested: '0', returned: '133', pnl: '33', tone: 'profit', settled_count: 1, unpriced_count: 0, pending_count: 1, has_estimate: false },
          ],
        }),
        day('2026-11-02', {
          empty: false, invested: '50', settled_invested: '50', returned: '20', pnl: '-30',
          tone: 'loss', settled_count: 1,
          by_sport: [
            { sport: 'nba', name: '篮球', invested: '50', settled_invested: '50', unpriced_invested: '0', returned: '20', pnl: '-30', tone: 'loss', settled_count: 1, unpriced_count: 0, pending_count: 0, has_estimate: false },
            { sport: 'mixed', name: '跨运动组合', invested: '0', settled_invested: '0', unpriced_invested: '0', returned: '0', pnl: '0', tone: 'flat', settled_count: 0, unpriced_count: 0, pending_count: 0, has_estimate: false },
          ],
        }),
        day('2026-11-03', {
          // 赛果已确认但盘口口径无法推断：已结算、盈亏待核实，不计入盈亏合计
          empty: false, invested: '6.4974', settled_invested: '0', unpriced_invested: '6.4974',
          returned: '0', pnl: '0', tone: 'unpriced', settled_count: 0, unpriced_count: 1,
          has_estimate: true,
          by_sport: [{ sport: 'football', name: '足球', invested: '6.4974', settled_invested: '0', unpriced_invested: '6.4974', returned: '0', pnl: '0', tone: 'unpriced', settled_count: 0, unpriced_count: 1, pending_count: 0, has_estimate: true }],
        }),
      ],
      summary: { invested: '192.4974', settled_invested: '150', unpriced_invested: '6.4974', returned: '153', pnl: '3', tone: 'profit', settled_count: 2, unpriced_count: 1, pending_count: 1, has_estimate: false },
      scope: '归日按下单时间、北京时间 12:00 至次日 11:59 记为同一天；',
    },
  }
}

describe('每日盈亏日历（模拟接口）', () => {
  it('渲染月份标题、七天表头与按盈亏着色的日期格', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(json(payload()))))
    const view = mount(PnlCalendar, { global: { plugins: [router] } })
    await flushPromises()
    expect(view.text()).toContain('2026年11月')
    expect(view.text()).toContain('DAILY P&L')
    for (const name of ['日', '一', '二', '三', '四', '五', '六']) {
      expect(view.findAll('.calendar-weekday').map(x => x.text())).toContain(name)
    }
    const cells = view.findAll('button.calendar-cell')
    expect(cells).toHaveLength(3)
    expect(cells[0].classes()).toContain('calendar-profit')
    expect(cells[0].text()).toContain('投入 136')
    expect(cells[0].text()).toContain('返奖 133')
    expect(cells[0].text()).toContain('待结算 1 笔')
    expect(cells[1].classes()).toContain('calendar-loss')
    // 已结算但盈亏待核实：不能用红/黑冒充盈亏，也不能标成待结算。
    expect(cells[2].classes()).toContain('calendar-unpriced')
    expect(cells[2].text()).toContain('盈亏待核实 1 笔')
    expect(cells[2].text()).not.toContain('待结算')
    expect(view.get('.calendar-summary').text()).toContain('盈亏待核实 1 笔')
    view.unmount()
  })

  it('盈亏待核实计入已结算笔数，但不计入盈亏合计', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(json(payload()))))
    const view = mount(PnlCalendar, { global: { plugins: [router] } })
    await flushPromises()
    await view.findAll('button.calendar-cell')[2].trigger('click')
    await flushPromises()
    const detail = view.get('.calendar-detail')
    expect(detail.text()).toContain('1 笔已结算但盈亏待核实')
    expect(detail.text()).toContain('不计入盈亏合计')
    const row = detail.findAll('.calendar-sport-row')[0]
    expect(row.text()).toContain('盈亏待核实 1 笔')
    // 金额没有凭空计算，仍显示 0。
    expect(row.get('.pnl-badge').text()).toContain('0 pUSD')
    view.unmount()
  })

  it('点击日期展开足球与篮球明细，合计与日汇总一致', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(json(payload()))))
    const view = mount(PnlCalendar, { global: { plugins: [router] } })
    await flushPromises()
    expect(view.find('.calendar-detail').exists()).toBe(false)
    await view.findAll('button.calendar-cell')[1].trigger('click')
    await flushPromises()
    const detail = view.get('.calendar-detail')
    expect(detail.text()).toContain('2026-11-02 明细')
    const rows = detail.findAll('.calendar-sport-row')
    expect(rows.map(r => r.get('h3').text())).toEqual(['篮球', '跨运动组合'])
    expect(rows[0].text()).toContain('投入 50 pUSD')
    expect(rows[0].text()).toContain('-30 pUSD')
    // 再次点击同一格收起明细。
    await view.findAll('button.calendar-cell')[1].trigger('click')
    await flushPromises()
    expect(view.find('.calendar-detail').exists()).toBe(false)
    view.unmount()
  })

  it('请求当月接口并支持切换月份', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(json(payload())))
    vi.stubGlobal('fetch', fetchMock)
    const view = mount(PnlCalendar, { global: { plugins: [router] } })
    await flushPromises()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/pnl-calendar?month='))).toBe(true)
    await view.findAll('button').find(b => b.text() === '上个月')!.trigger('click')
    await flushPromises()
    const urls = fetchMock.mock.calls.map(([url]) => String(url))
    expect(urls.some(u => u.includes('/pnl-calendar?month=') && u !== urls[0])).toBe(true)
    view.unmount()
  })
})
