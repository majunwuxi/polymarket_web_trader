// 本文件所有数据均为模拟测试数据，不访问真实服务。
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import BackToTop from '../src/components/BackToTop.vue'

const Page = defineComponent({ render: () => h('div', 'mock page') })
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: Page }] })

function setScrollY(value: number) {
  Object.defineProperty(window, 'scrollY', { value, writable: true, configurable: true })
}

// 组件用 requestAnimationFrame 合并滚动事件；测试里手动跑完一帧。
async function scrollTo(value: number) {
  setScrollY(value)
  window.dispatchEvent(new Event('scroll'))
  await new Promise(resolve => requestAnimationFrame(() => resolve(null)))
  await flushPromises()
}

beforeEach(async () => {
  setScrollY(0)
  await router.push('/')
  await router.isReady()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('回到顶部浮动按钮（模拟滚动）', () => {
  it('在页面顶部时不渲染，向下滚动超过阈值后才出现', async () => {
    const view = mount(BackToTop, { global: { plugins: [router] } })
    await flushPromises()
    expect(view.find('.back-to-top').exists()).toBe(false)

    await scrollTo(1200)
    const button = view.get('.back-to-top')
    expect(button.attributes('aria-label')).toBe('回到页面顶部')
    expect(button.attributes('title')).toBe('回到页面顶部')
    view.unmount()
  })

  it('滚动回顶部后按钮消失', async () => {
    setScrollY(1200)
    const view = mount(BackToTop, { global: { plugins: [router] } })
    await flushPromises()
    expect(view.find('.back-to-top').exists()).toBe(true)

    await scrollTo(10)
    expect(view.find('.back-to-top').exists()).toBe(false)
    view.unmount()
  })

  it('点击后把窗口滚回顶部', async () => {
    setScrollY(1500)
    const scrollToSpy = vi.fn()
    vi.stubGlobal('scrollTo', scrollToSpy)
    const view = mount(BackToTop, { global: { plugins: [router] } })
    await flushPromises()
    await view.get('.back-to-top').trigger('click')
    // 未声明 reduce-motion 时使用平滑滚动。
    expect(scrollToSpy).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' })
    view.unmount()
  })

  it('赛事中心有组合草稿条时避让其高度', async () => {
    setScrollY(1500)
    const tray = document.createElement('aside')
    tray.className = 'combo-tray'
    tray.getBoundingClientRect = () => ({ top: window.innerHeight - 100 } as DOMRect)
    document.body.appendChild(tray)
    try {
      const view = mount(BackToTop, { global: { plugins: [router] } })
      await flushPromises()
      const style = view.get('.back-to-top').attributes('style') || ''
      expect(style).toContain('--tray-clearance: 112px')
      view.unmount()
    } finally {
      tray.remove()
    }
  })
})
