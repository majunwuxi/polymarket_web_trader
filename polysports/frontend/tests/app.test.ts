import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import App from '../src/App.vue'

describe('系统基础页面（模拟测试）', () => {
  it('明确标示尚未实现交易且无虚构行情', () => {
    const wrapper = mount(App)
    expect(wrapper.text()).toContain('PolySports 正在建设')
    expect(wrapper.text()).toContain('不包含真实赛事、报价或交易功能')
    expect(wrapper.find('button').exists()).toBe(false)
  })
})
