<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

// 当前用户（安全解析：localStorage 中可能为 'null' 字符串，直接 parse 会抛错拖垮渲染）
const currentUser = computed(() => {
  try {
    return JSON.parse(localStorage.getItem('rag_user') || 'null')
  } catch {
    return null
  }
})

const sessions = ref([])
const currentSessionId = ref(null)
const messages = ref([])
const question = ref('')
const sending = ref(false)
const msgListRef = ref(null)

async function refreshSessions() {
  sessions.value = (await api.listSessions()).items
}

async function openSession(session) {
  currentSessionId.value = session.id
  const data = await api.listMessages(session.id)
  messages.value = data.items.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    refs: m.refs ? JSON.parse(m.refs) : [],
    feedback: m.feedback || '',
  }))
  scrollToBottom()
}

function newSession() {
  currentSessionId.value = null
  messages.value = []
}

async function removeSession(session) {
  await ElMessageBox.confirm('删除该会话及其全部消息？', '删除会话', { type: 'warning' })
  await api.deleteSession(session.id)
  if (currentSessionId.value === session.id) newSession()
  refreshSessions()
}

async function send() {
  const q = question.value.trim()
  if (!q || sending.value) return
  question.value = ''
  sending.value = true

  messages.value.push({ role: 'user', content: q, refs: [], feedback: '' })
  const assistant = { role: 'assistant', content: '', refs: [], feedback: '' }
  messages.value.push(assistant)
  scrollToBottom()

  const history = messages.value
    .filter((m) => m.role !== 'assistant' || m.content)
    .slice(-8)
    .map((m) => ({ role: m.role, content: m.content }))
    .filter((m) => m.role === 'user' || m.content)

  try {
    await api.ask(q, {
      sessionId: currentSessionId.value,
      history,
      onDelta: (text) => {
        assistant.content += text
        scrollToBottom()
      },
      onCitations: (cits) => {
        assistant.refs = cits
      },
      onSession: (sid) => {
        currentSessionId.value = sid
        refreshSessions()
      },
      onError: (msg) => {
        assistant.content = '⚠️ ' + msg
      },
    })
  } catch (e) {
    assistant.content = '⚠️ ' + e.message
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

async function setFeedback(msg, feedback) {
  if (!currentSessionId.value) return
  try {
    await api.feedback(currentSessionId.value, msg.id, feedback)
    msg.feedback = feedback
    ElMessage.success('已记录反馈')
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (msgListRef.value) msgListRef.value.scrollTop = msgListRef.value.scrollHeight
  })
}

onMounted(refreshSessions)
</script>

<template>
  <div class="chat-layout">
    <!-- 会话列表 -->
    <aside class="session-panel">
      <el-button class="new-session-btn" type="primary" style="width: 100%" @click="newSession">＋ 新会话</el-button>
      <div class="session-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === currentSessionId }"
          @click="openSession(s)"
        >
          <span class="title">{{ s.title }}</span>
          <el-button link size="small" type="danger" @click.stop="removeSession(s)">删</el-button>
        </div>
        <el-empty v-if="!sessions.length" description="暂无会话" :image-size="60" />
      </div>
    </aside>

    <!-- 消息区 -->
    <main class="chat-panel">
      <div ref="msgListRef" class="msg-list">
        <el-empty v-if="!messages.length" description="开始提问吧" :image-size="72" class="empty-tip" />
        <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
          <div v-if="m.role === 'assistant'" class="role-avatar bot">
            <img src="/logo.svg" alt="PRTS" />
          </div>
          <div class="bubble">
            <div class="content">{{ m.content || '…' }}</div>
            <div v-if="m.refs && m.refs.length" class="cites">
              <span v-for="c in m.refs" :key="c.index" class="cite-tag" :title="c.content">
                [{{ c.index }}] {{ c.doc_id }}
              </span>
            </div>
            <div v-if="m.role === 'assistant' && m.id" class="actions">
              <el-button
                link
                size="small"
                :type="m.feedback === 'up' ? 'success' : 'info'"
                @click="setFeedback(m, 'up')"
              >
                👍
              </el-button>
              <el-button
                link
                size="small"
                :type="m.feedback === 'down' ? 'danger' : 'info'"
                @click="setFeedback(m, 'down')"
              >
                👎
              </el-button>
            </div>
          </div>
          <div v-if="m.role === 'user'" class="role-avatar user">
            {{ (currentUser?.username || '我')[0].toUpperCase() }}
          </div>
        </div>
      </div>
      <div class="input-row">
        <el-input
          v-model="question"
          placeholder="输入问题，回车发送"
          size="large"
          :disabled="sending"
          class="chat-input"
          @keyup.enter="send"
        />
        <el-button type="primary" size="large" class="send-btn" :loading="sending" @click="send">
          发送
        </el-button>
      </div>
    </main>
  </div>
</template>

<style scoped>
.chat-layout { display: flex; height: 100%; }

/* 会话面板（浅色） */
.session-panel {
  width: 248px;
  border-right: 1px solid #e9e4f4;
  padding: 14px 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: #faf9fd;
}
.new-session-btn {
  border-radius: 10px;
  background: linear-gradient(135deg, #7c6cf0, #6455cf);
  border: none;
  box-shadow: 0 4px 14px rgba(100, 85, 207, 0.3);
}
.session-list { flex: 1; overflow-y: auto; }
.session-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 9px 12px;
  border-radius: 10px;
  cursor: pointer;
  font-size: 13px;
  margin-bottom: 4px;
  color: #555170;
  border: 1px solid transparent;
  transition: all 0.15s ease;
}
.session-item:hover { background: #f0edfb; }
.session-item.active {
  background: linear-gradient(135deg, rgba(124, 108, 240, 0.12), rgba(124, 108, 240, 0.07));
  color: #6455cf;
  border-color: rgba(124, 108, 240, 0.28);
  font-weight: 500;
}
.session-item .title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

/* 消息区（浅色） */
.chat-panel { flex: 1; display: flex; flex-direction: column; background: #f6f4fb; }
.msg-list { flex: 1; overflow-y: auto; padding: 22px 28px; }
.empty-tip :deep(.el-empty__description p) { color: #8b87a5; }

.msg { display: flex; margin-bottom: 18px; align-items: flex-start; gap: 10px; }
.msg.user { justify-content: flex-end; }
.msg.assistant { justify-content: flex-start; }

.role-avatar {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  margin-top: 2px;
}
.role-avatar.bot {
  background: linear-gradient(135deg, #7c6cf0, #4f3fb8);
  box-shadow: 0 3px 10px rgba(100, 85, 207, 0.35);
  padding: 5px;
}
.role-avatar.bot img { width: 100%; height: 100%; }
.role-avatar.user {
  background: linear-gradient(135deg, #8b7cf8, #5b4bd0);
  color: #fff;
  box-shadow: 0 3px 10px rgba(91, 75, 208, 0.3);
}

.bubble {
  max-width: 72%;
  padding: 12px 16px;
  border-radius: 14px;
  line-height: 1.7;
  font-size: 14px;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg.user .bubble {
  background: linear-gradient(135deg, #7c6cf0, #6455cf);
  color: #fff;
  border-bottom-right-radius: 4px;
  box-shadow: 0 4px 14px rgba(100, 85, 207, 0.28);
}
.msg.assistant .bubble {
  background: #ffffff;
  border: 1px solid #ece9f7;
  border-bottom-left-radius: 4px;
  box-shadow: 0 4px 16px rgba(76, 29, 149, 0.07);
  color: #33304a;
}

.cites { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
.cite-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 20px;
  background: #f0edfb;
  color: #6455cf;
  border: 1px solid #ddd8f8;
  cursor: help;
  transition: all 0.15s;
}
.cite-tag:hover { background: #e8e4fa; box-shadow: 0 2px 8px rgba(100, 85, 207, 0.18); }

.actions { margin-top: 8px; opacity: 0.55; transition: opacity 0.2s; }
.bubble:hover .actions { opacity: 1; }

/* 输入区（浅色） */
.input-row {
  display: flex;
  gap: 10px;
  padding: 14px 28px 18px;
  border-top: 1px solid #e9e4f4;
  background: #faf9fd;
}
.chat-input :deep(.el-input__wrapper) {
  border-radius: 12px;
  padding: 4px 16px;
  background: #ffffff;
  box-shadow: 0 0 0 1px #e2ddf2 inset;
}
.chat-input :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1.5px #9d91f5 inset;
}
.send-btn {
  border-radius: 12px;
  padding: 0 26px;
  background: linear-gradient(135deg, #7c6cf0, #6455cf);
  border: none;
  box-shadow: 0 4px 14px rgba(100, 85, 207, 0.3);
}
</style>
