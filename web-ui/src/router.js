import { createRouter, createWebHashHistory } from 'vue-router'

// hash 路由：避免 SPA fallback（服务端只需托管 index.html）
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/home' },
    { path: '/login', component: () => import('./views/LoginView.vue') },
    { path: '/home', component: () => import('./views/OverviewView.vue') },
    { path: '/chat', component: () => import('./views/ChatView.vue') },
    { path: '/docs', component: () => import('./views/DocsView.vue') },
    { path: '/users', component: () => import('./views/UsersView.vue') },
  ],
})

function currentUser() {
  try {
    return JSON.parse(localStorage.getItem('rag_user') || 'null')
  } catch {
    return null
  }
}

router.beforeEach((to) => {
  if (to.path !== '/login' && !localStorage.getItem('rag_token')) {
    return '/login'
  }
  // RBAC 前端守卫（后端仍有 403 兜底）：普通用户无管理页
  const user = currentUser()
  if (['/docs', '/home'].includes(to.path) && !['super_admin', 'admin'].includes(user?.role)) {
    return '/chat'
  }
  if (to.path === '/users' && user?.role !== 'super_admin') {
    return '/chat'
  }
})

export default router
