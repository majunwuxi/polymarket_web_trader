import { beforeEach, vi } from 'vitest'

beforeEach(() => {
  // 此阶段所有前端测试都是模拟；不得发起真实上游请求。
  vi.stubGlobal('fetch', vi.fn(() => { throw new Error('测试禁止网络请求') }))
})
