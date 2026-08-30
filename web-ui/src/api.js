// 后端 API 封装：认证、问答 SSE、会话、文档管理

const API = '' // 同源部署（FastAPI 挂载）；dev 模式由 vite proxy 转发

function token() {
  return localStorage.getItem('rag_token') || ''
}

function authHeaders(extra = {}) {
  return { ...extra, Authorization: 'Bearer ' + token() }
}

async function handle(resp) {
  if (resp.status === 401) {
    localStorage.removeItem('rag_token')
    location.hash = '#/login'
    throw new Error('登录已过期，请重新登录')
  }
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detail.detail || '请求失败（' + resp.status + '）')
  }
  return resp.json()
}

export const api = {
  // 认证
  login: (username, password) =>
    fetch(API + '/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }).then(handle),
  me: () => fetch(API + '/api/v1/auth/me', { headers: authHeaders() }).then(handle),

  // 问答（SSE 流式）
  ask: async (question, { sessionId = null, history = [], onDelta, onCitations, onSession, onError } = {}) => {
    const resp = await fetch(API + '/api/v1/qa/ask', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ question, session_id: sessionId, history }),
    })
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}))
      throw new Error(detail.detail || '请求失败')
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop()
      for (const block of blocks) {
        const line = block.split('\n').find((l) => l.startsWith('data: '))
        if (!line) continue
        let data
        try {
          data = JSON.parse(line.slice(6))
        } catch {
          continue
        }
        if (data.type === 'delta') onDelta?.(data.text)
        else if (data.type === 'citations') onCitations?.(data.citations)
        else if (data.type === 'session') onSession?.(data.session_id)
        else if (data.type === 'error') onError?.(data.message)
      }
    }
  },

  // 会话
  listSessions: () => fetch(API + '/api/v1/sessions', { headers: authHeaders() }).then(handle),
  createSession: (title) =>
    fetch(API + '/api/v1/sessions', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ title }),
    }).then(handle),
  listMessages: (sessionId) =>
    fetch(API + `/api/v1/sessions/${sessionId}/messages`, { headers: authHeaders() }).then(handle),
  deleteSession: (sessionId) =>
    fetch(API + `/api/v1/sessions/${sessionId}`, { method: 'DELETE', headers: authHeaders() }).then(handle),
  feedback: (sessionId, messageId, feedback) =>
    fetch(API + `/api/v1/sessions/${sessionId}/messages/${messageId}/feedback`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ feedback }),
    }).then(handle),

  // 文档
  listDocuments: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== '' && v !== null)
    ).toString()
    return fetch(API + `/api/v1/documents?${q}`, { headers: authHeaders() }).then(handle)
  },
  myDepartments: () => fetch(API + '/api/v1/documents/departments', { headers: authHeaders() }).then(handle),
  uploadDocument: async (file, department, kbId = null) => {
    const form = new FormData()
    form.append('file', file)
    form.append('department', department)
    if (kbId) form.append('kb_id', kbId)
    const resp = await fetch(API + '/api/v1/documents/upload', {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    })
    return handle(resp)
  },
  taskStatus: (taskId) =>
    fetch(API + `/api/v1/documents/tasks/${taskId}`, { headers: authHeaders() }).then(handle),
  deleteDocument: (docId) =>
    fetch(API + `/api/v1/documents/${docId}`, { method: 'DELETE', headers: authHeaders() }).then(handle),
  getIngestionReport: (docId) =>
    fetch(API + `/api/v1/ingestion/report/${docId}`, { headers: authHeaders() }).then(handle),
  getOverview: () => fetch(API + '/api/v1/stats/overview', { headers: authHeaders() }).then(handle),
  listKbs: (keyword = '') =>
    fetch(API + `/api/v1/kbs?keyword=${encodeURIComponent(keyword)}`, { headers: authHeaders() }).then(handle),
  createKb: (payload) =>
    fetch(API + '/api/v1/kbs', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    }).then(handle),
  updateKb: (kbId, payload) =>
    fetch(API + `/api/v1/kbs/${kbId}`, {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    }).then(handle),
  deleteKb: (kbId) =>
    fetch(API + `/api/v1/kbs/${kbId}`, { method: 'DELETE', headers: authHeaders() }).then(handle),

  // 用户管理（super_admin，RBAC）
  listUsers: () => fetch(API + '/api/v1/admin/users', { headers: authHeaders() }).then(handle),
  createUser: (payload) =>
    fetch(API + '/api/v1/admin/users', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    }).then(handle),
  updateUser: (userId, payload) =>
    fetch(API + `/api/v1/admin/users/${userId}`, {
      method: 'PATCH',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    }).then(handle),
  setAdminDepartments: (userId, departments) =>
    fetch(API + `/api/v1/admin/users/${userId}/departments`, {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ departments }),
    }).then(handle),
  listAudit: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
    ).toString()
    return fetch(API + `/api/v1/admin/audit?${q}`, { headers: authHeaders() }).then(handle)
  },
  exportAudit: () => fetch(API + '/api/v1/admin/audit/export', { headers: authHeaders() }).then((r) => r.text()),
}
