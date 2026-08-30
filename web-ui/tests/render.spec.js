import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import ChatView from '../src/views/ChatView.vue'
import DocsView from '../src/views/DocsView.vue'
import UsersView from '../src/views/UsersView.vue'
import LoginView from '../src/views/LoginView.vue'
import OverviewView from '../src/views/OverviewView.vue'
import KbsView from '../src/views/KbsView.vue'

// 渲染冒烟测试：用真实 Element Plus 组件挂载，捕获 setup/模板运行时错误
const VIEWS = {
  LoginView,
  OverviewView,
  ChatView,
  DocsView,
  UsersView,
  KbsView,
}

describe('视图组件可挂载（渲染无运行时错误）', () => {
  for (const [name, comp] of Object.entries(VIEWS)) {
    it(name, () => {
      const wrapper = mount(comp, {
        global: { plugins: [ElementPlus] },
      })
      expect(wrapper.exists()).toBe(true)
      wrapper.unmount()
    })
  }
})
