<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const user = computed(() => {
  void route.path // 路由切换（含登录跳转）时重新读取 localStorage
  try {
    return JSON.parse(localStorage.getItem('rag_user') || 'null')
  } catch {
    return null
  }
})
// RBAC：super_admin → 全部菜单；admin → 问答+文档；user → 仅问答
const canManageDocs = computed(() => ['super_admin', 'admin'].includes(user.value?.role))
const isSuperAdmin = computed(() => user.value?.role === 'super_admin')

function logout() {
  localStorage.removeItem('rag_token')
  localStorage.removeItem('rag_user')
  location.hash = '#/login'
}
</script>

<template>
  <router-view v-if="route.path === '/login'" />
  <el-container v-else class="layout">
    <el-aside width="216px" class="sidebar">
      <div class="brand">
        <img src="/logo.svg" class="brand-logo" alt="logo" />
        <div class="brand-text">
          <div class="brand-name">企业知识库</div>
          <div class="brand-sub">内部知识问答平台</div>
        </div>
      </div>
      <el-menu :default-active="route.path" router class="side-menu">
        <el-menu-item index="/chat">
          <span class="menu-icon">💬</span> 问答工作台
        </el-menu-item>
        <el-menu-item v-if="canManageDocs" index="/docs">
          <span class="menu-icon">📄</span> 文档管理
        </el-menu-item>
        <el-menu-item v-if="isSuperAdmin" index="/users">
          <span class="menu-icon">👥</span> 用户管理
        </el-menu-item>
      </el-menu>
      <div class="user-box">
        <div class="avatar">{{ (user?.username || '?')[0].toUpperCase() }}</div>
        <div class="user-info">
          <span class="user-name">{{ user?.username || '' }}</span>
          <span class="user-dept">{{ user?.department || '' }}</span>
        </div>
        <el-button link class="logout-btn" @click="logout">退出</el-button>
      </div>
    </el-aside>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body, #app { height: 100%; }
.layout { height: 100%; }

.sidebar {
  background: linear-gradient(180deg, #0c0918 0%, #151026 60%, #1a1330 100%);
  display: flex;
  flex-direction: column;
  box-shadow: 2px 0 18px rgba(0, 0, 0, 0.45);
  z-index: 2;
  border-right: 1px solid #2b2447;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 16px;
  border-bottom: 1px solid rgba(139, 124, 248, 0.12);
}
.brand-logo {
  width: 38px;
  height: 38px;
  opacity: 0.92;
}
.brand-text { line-height: 1.3; }
.brand-name {
  color: #e8e4f7;
  font-size: 14.5px;
  font-weight: 600;
  letter-spacing: 1px;
}
.brand-sub { color: #6f669a; font-size: 11px; }

.side-menu { border-right: none; background: transparent; margin-top: 8px; }
.side-menu .el-menu-item {
  color: #9a92c0;
  border-radius: 8px;
  margin: 2px 10px;
  height: 42px;
}
.side-menu .el-menu-item:hover {
  background: rgba(139, 124, 248, 0.08);
  color: #cfc8f0;
}
.side-menu .el-menu-item.is-active {
  color: #b9affb;
  background: rgba(139, 124, 248, 0.14);
  box-shadow: inset 0 0 0 1px rgba(139, 124, 248, 0.22);
  font-weight: 500;
}
.menu-icon { margin-right: 8px; font-size: 15px; }

.user-box {
  margin-top: auto;
  padding: 14px 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  border-top: 1px solid rgba(139, 124, 248, 0.12);
}
.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: linear-gradient(135deg, #5b4bd0, #3b2f6e);
  color: #e8e4f7;
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.user-info { flex: 1; display: flex; flex-direction: column; line-height: 1.3; min-width: 0; }
.user-name { color: #cfc8f0; font-size: 13px; font-weight: 500; }
.user-dept { color: #6f669a; font-size: 11px; }
.logout-btn { color: #6f669a; font-size: 12px; }
.logout-btn:hover { color: #b9affb; }

.main { padding: 0; overflow: hidden; background: var(--prts-bg); }
</style>
