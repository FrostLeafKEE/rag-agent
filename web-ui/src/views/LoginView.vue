<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)

async function login() {
  if (!username.value || !password.value) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const data = await api.login(username.value, password.value)
    localStorage.setItem('rag_token', data.access_token)
    const me = await api.me()
    localStorage.setItem('rag_user', JSON.stringify(me))
    router.push('/chat')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <!-- 背景装饰：菱形网格 -->
    <div class="bg-decor" aria-hidden="true">
      <svg class="decor-svg" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
        <polygon points="50,2 98,50 50,98 2,50" fill="none" stroke="rgba(139,124,248,0.16)" stroke-width="1.2"/>
        <polygon points="50,18 82,50 50,82 18,50" fill="none" stroke="rgba(139,124,248,0.12)" stroke-width="1"/>
        <polygon points="50,34 66,50 50,66 34,50" fill="none" stroke="rgba(139,124,248,0.2)" stroke-width="1"/>
        <circle cx="50" cy="50" r="3.5" fill="rgba(139,124,248,0.5)"/>
      </svg>
      <svg class="decor-svg d2" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
        <polygon points="50,2 98,50 50,98 2,50" fill="none" stroke="rgba(91,75,208,0.16)" stroke-width="1.5"/>
      </svg>
      <svg class="decor-svg d3" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
        <polygon points="50,2 98,50 50,98 2,50" fill="none" stroke="rgba(107,92,224,0.16)" stroke-width="1"/>
      </svg>
    </div>

    <div class="login-card-wrap">
      <div class="logo-badge">
        <img src="/logo.svg" class="login-logo" alt="logo" />
      </div>
      <h1 class="title">企业知识库</h1>
      <p class="subtitle">内部知识问答平台</p>

      <el-card class="login-card prts-card">
        <el-form @submit.prevent="login">
          <el-form-item>
            <el-input v-model="username" placeholder="用户名" size="large" class="login-input">
              <template #prefix><span class="input-icon">👤</span></template>
            </el-input>
          </el-form-item>
          <el-form-item>
            <el-input
              v-model="password"
              type="password"
              placeholder="密码"
              size="large"
              show-password
              class="login-input"
              @keyup.enter="login"
            >
              <template #prefix><span class="input-icon">🔒</span></template>
            </el-input>
          </el-form-item>
          <el-button type="primary" size="large" class="login-btn" :loading="loading" @click="login">
            登录
          </el-button>
        </el-form>
      </el-card>
      <p class="tip">未注册？请先通过 /docs 的 register 接口创建账号</p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(1100px 560px at 15% -10%, rgba(91, 75, 208, 0.22), transparent 60%),
    radial-gradient(900px 640px at 110% 110%, rgba(59, 47, 110, 0.28), transparent 55%),
    linear-gradient(160deg, #0b0916 0%, #120e22 45%, #1a1330 100%);
}

/* 装饰菱形 */
.bg-decor { position: absolute; inset: 0; pointer-events: none; }
.decor-svg {
  position: absolute;
  width: 340px;
  opacity: 0.55;
  top: -60px;
  left: -80px;
  animation: float 9s ease-in-out infinite;
}
.decor-svg.d2 {
  width: 200px;
  right: 8%;
  left: auto;
  top: 14%;
  animation-delay: -3s;
}
.decor-svg.d3 {
  width: 130px;
  left: 12%;
  top: auto;
  bottom: 8%;
  animation-delay: -6s;
}
@keyframes float {
  0%, 100% { transform: translateY(0) rotate(0deg); }
  50% { transform: translateY(-16px) rotate(8deg); }
}

.login-card-wrap {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 400px;
}

.logo-badge {
  width: 80px;
  height: 80px;
  border-radius: 20px;
  background: rgba(139, 124, 248, 0.06);
  border: 1px solid rgba(139, 124, 248, 0.2);
  backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 18px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.45), inset 0 0 24px rgba(139, 124, 248, 0.08);
}
.login-logo { width: 54px; height: 54px; }

.title {
  color: #e8e4f7;
  font-size: 24px;
  font-weight: 600;
  letter-spacing: 3px;
}
.subtitle { color: #6f669a; font-size: 12.5px; margin: 8px 0 24px; letter-spacing: 1px; }

.login-card {
  width: 100%;
  padding: 8px;
  background: #ffffff;
  border-radius: 16px !important;
  border: 1px solid #ece9f7;
  backdrop-filter: blur(10px);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.45) !important;
}
.login-input :deep(.el-input__wrapper) {
  border-radius: 10px;
  padding: 2px 14px;
  background: #faf9fd;
  box-shadow: 0 0 0 1px #e2ddf2 inset;
}
.input-icon { font-size: 14px; margin-right: 2px; opacity: 0.8; }
.login-btn {
  width: 100%;
  height: 42px;
  border-radius: 10px;
  font-size: 15px;
  letter-spacing: 6px;
  font-weight: 500;
  background: linear-gradient(135deg, #7c6cf0, #6455cf);
  border: none;
  box-shadow: 0 6px 18px rgba(100, 85, 207, 0.4);
  color: #ffffff;
}
.login-btn:hover { filter: brightness(1.08); }

.tip { margin-top: 18px; font-size: 12px; color: #a89cfb; }
</style>
