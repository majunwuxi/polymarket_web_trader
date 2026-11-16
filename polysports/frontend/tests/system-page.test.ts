import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import SystemPage from '../src/views/SystemPage.vue'
import { session } from '../src/auth'

const json = (data: unknown) => ({ ok: true, status: 200, json: async () => ({ data }) }) as Response
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/system', component: SystemPage }] })

beforeEach(async () => {
  session.username = 'mock-admin'
  session.csrf = 'mock-csrf'
  await router.push('/system')
  await router.isReady()
})
afterEach(() => vi.unstubAllGlobals())

it('任务状态按前20小时至后24小时查询，并显示对应比赛', async () => {
  const targetTime = new Date().toISOString()
  const job = {
    id: 'job-1', kind: 'result_poll', status: 'pending', due_at: targetTime,
    attempt_count: 0, failure_count: 3, last_error_code: null, last_error_summary: null,
    target: { label: '主队 对 客队', starts_at: targetTime, game_status: 'scheduled' },
  }
  const fetchMock = vi.fn((url: string) => {
    if (url.startsWith('/api/v1/sync-schedules')) return Promise.resolve(json({ revision: 1, items: [] }))
    if (url.startsWith('/api/v1/jobs?')) return Promise.resolve(json({ items: [job], window: {} }))
    if (url.startsWith('/api/v1/worker-status')) return Promise.resolve(json({ workers: [] }))
    if (url.startsWith('/api/v1/system/status')) return Promise.resolve(json({ configuration: null, database: { status: 'ok', checked_at: targetTime }, sources: [] }))
    if (url.startsWith('/api/v1/sync-runs?')) return Promise.resolve(json({ items: [] }))
    if (url.startsWith('/api/v1/audit-events?')) return Promise.resolve(json({ items: [] }))
    return Promise.resolve(json({}))
  })
  vi.stubGlobal('fetch', fetchMock)
  const view = mount(SystemPage, { global: { plugins: [router] } })
  await flushPromises()
  const urls = fetchMock.mock.calls.map(([url]) => String(url))
  const jobsUrl = new URL(urls.find(url => url.startsWith('/api/v1/jobs?'))!, 'http://localhost')
  const from = new Date(jobsUrl.searchParams.get('from')!).getTime()
  const to = new Date(jobsUrl.searchParams.get('to')!).getTime()
  expect((to - from) / 3600000).toBe(44)
  for (const prefix of ['/api/v1/sync-runs?', '/api/v1/audit-events?']) {
    const url = new URL(urls.find(value => value.startsWith(prefix))!, 'http://localhost')
    const start = new Date(url.searchParams.get('from')!).getTime()
    const end = new Date(url.searchParams.get('to')!).getTime()
    expect((end - start) / 3600000).toBe(24)
  }
  expect(view.text()).toContain('过去 20 小时至未来 24 小时')
  expect(view.text()).toContain('目标：主队 对 客队')
  expect(view.text()).toContain('连续异常/未决 3 次')
  view.unmount()
})
