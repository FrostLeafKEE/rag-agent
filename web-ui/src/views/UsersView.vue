<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const activeTab = ref('users')
const users = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref(null) // null=新建；对象=编辑
const form = ref({ username: '', password: '', department: '', role: 'user', admin_departments: [] })

// 审计日志（R7）
const auditRows = ref([])
const auditLoading = ref(false)
const auditAction = ref('')
const auditPage = ref(1)
const auditTotal = ref(0)
const AUDIT_PAGE_SIZE = 20
const ACTION_LABELS = {
  register: '注册', login: '登录', login_failed: '登录失败',
  upload: '上传', delete: '删除', denied: '越权拒绝',
  user_admin: '用户管理', redact: '脱敏',
}

const ROLE_LABELS = {
  super_admin: '超级管理员',
  admin: '部门管理员',
  user: '普通用户',
}

async function refresh() {
  loading.value = true
  try {
    users.value = (await api.listUsers()).items
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.value = { username: '', password: '', department: '', role: 'user', admin_departments: [] }
  dialogVisible.value = true
}

function openEdit(user) {
  editing.value = user
  form.value = {
    username: user.username,
    password: '',
    department: user.department || '',
    role: user.role,
    admin_departments: user.admin_departments || [],
  }
  dialogVisible.value = true
}

async function save() {
  try {
    if (editing.value) {
      // 编辑：仅提交可变字段（密码留空不改）
      const payload = { role: form.value.role, department: form.value.department }
      if (form.value.password) payload.password = form.value.password
      await api.updateUser(editing.value.id, payload)
      if (form.value.role === 'admin') {
        await api.setAdminDepartments(editing.value.id, form.value.admin_departments)
      }
      ElMessage.success('已保存')
    } else {
      await api.createUser({
        username: form.value.username,
        password: form.value.password,
        department: form.value.department,
        role: form.value.role,
        admin_departments: form.value.role === 'admin' ? form.value.admin_departments : [],
      })
      ElMessage.success('用户已创建')
    }
    dialogVisible.value = false
    refresh()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function toggleActive(user) {
  try {
    await api.updateUser(user.id, { is_active: !user.is_active })
    ElMessage.success(user.is_active ? '已停用' : '已启用')
    refresh()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function resetPassword(user) {
  const { value } = await ElMessageBox.prompt(`为 ${user.username} 设置新密码`, '重置密码', {
    inputPattern: /^.{8,}$/,
    inputErrorMessage: '密码至少 8 位',
  })
  try {
    await api.updateUser(user.id, { password: value })
    ElMessage.success('密码已重置')
  } catch (e) {
    ElMessage.error(e.message)
  }
}

// ---- 审计日志（R7）----
async function loadAudit() {
  auditLoading.value = true
  try {
    const data = await api.listAudit({
      limit: AUDIT_PAGE_SIZE,
      offset: (auditPage.value - 1) * AUDIT_PAGE_SIZE,
      action: auditAction.value || undefined,
    })
    auditRows.value = data.items
    auditTotal.value = data.total
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    auditLoading.value = false
  }
}

function auditTime(row) {
  const t = row.created_at
  return t ? t.replace('T', ' ').slice(0, 19) : '—'
}

async function exportAudit() {
  try {
    const csv = await api.exportAudit()
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'audit_log.csv'
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(e.message)
  }
}

onMounted(refresh)
</script>

<template>
  <div class="users-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">👥 用户管理</h2>
        <p class="page-desc">超级管理员专属：创建用户、分配角色、指定部门管理员的负责部门、审计日志</p>
      </div>
      <el-button v-if="activeTab === 'users'" type="primary" class="create-btn" @click="openCreate">＋ 创建用户</el-button>
      <el-button v-else type="primary" class="create-btn" @click="exportAudit">⬇️ 导出 CSV</el-button>
    </div>

    <el-tabs v-model="activeTab" class="manage-tabs" @tab-change="activeTab === 'audit' && loadAudit()">
      <el-tab-pane label="用户管理" name="users">
        <el-card shadow="never" class="prts-card users-card">
          <el-table :data="users" v-loading="loading" class="users-table">
        <el-table-column prop="username" label="用户名" width="160" />
        <el-table-column label="角色" width="130">
          <template #default="{ row }">
            <span class="role-pill" :class="row.role">{{ ROLE_LABELS[row.role] || row.role }}</span>
          </template>
        </el-table-column>
        <el-table-column label="部门" width="150">
          <template #default="{ row }">
            <span v-if="row.role === 'super_admin'" class="dept-tag super">全部</span>
            <span v-else-if="row.role === 'admin'" class="dept-tag">
              {{ (row.admin_departments || []).join(' / ') || '未分配' }}
            </span>
            <span v-else class="dept-tag user">{{ row.department || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <span class="status-pill" :class="row.is_active ? 'active' : 'disabled'">
              {{ row.is_active ? '正常' : '停用' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="200">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link size="small" @click="resetPassword(row)">重置密码</el-button>
            <el-button link :type="row.is_active ? 'danger' : 'success'" size="small" @click="toggleActive(row)">
              {{ row.is_active ? '停用' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      </el-card>
      </el-tab-pane>

      <el-tab-pane label="审计日志" name="audit">
        <el-card shadow="never" class="prts-card users-card">
          <div class="audit-bar">
            <el-select v-model="auditAction" placeholder="全部操作类型" clearable style="width: 180px" @change="auditPage = 1; loadAudit()">
              <el-option v-for="(label, value) in ACTION_LABELS" :key="value" :label="label" :value="value" />
            </el-select>
            <span class="audit-total">共 {{ auditTotal }} 条</span>
          </div>
          <el-table :data="auditRows" v-loading="auditLoading" class="users-table" style="margin-top: 12px">
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <span class="role-pill" :class="'act-' + row.action">{{ ACTION_LABELS[row.action] || row.action }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="user" label="用户" width="120" />
            <el-table-column prop="resource" label="资源" min-width="180" show-overflow-tooltip />
            <el-table-column prop="detail" label="详情" min-width="200" show-overflow-tooltip />
            <el-table-column label="时间" width="170">
              <template #default="{ row }">{{ auditTime(row) }}</template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-model:current-page="auditPage"
            :page-size="AUDIT_PAGE_SIZE"
            :total="auditTotal"
            layout="prev, pager, next"
            style="margin-top: 14px; justify-content: flex-end"
            @current-change="loadAudit"
          />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dialogVisible" :title="editing ? `编辑用户：${editing.username}` : '创建用户'" width="480px">
      <el-form label-width="96px">
        <el-form-item label="用户名">
          <el-input v-model="form.username" :disabled="!!editing" placeholder="字母数字下划线" />
        </el-form-item>
        <el-form-item :label="editing ? '新密码' : '密码'">
          <el-input v-model="form.password" type="password" show-password :placeholder="editing ? '留空则不修改' : '至少 8 位'" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option label="普通用户" value="user" />
            <el-option label="部门管理员" value="admin" />
            <el-option label="超级管理员" value="super_admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="归属部门" v-if="form.role !== 'admin'">
          <el-input v-model="form.department" placeholder="普通用户的权限过滤部门" />
        </el-form-item>
        <el-form-item label="负责部门" v-if="form.role === 'admin'">
          <el-select
            v-model="form.admin_departments"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="输入部门名回车添加（至少一个）"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.users-page { padding: 22px 26px; overflow-y: auto; height: 100%; }

.page-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.page-title { font-size: 19px; color: #33304a; margin-bottom: 4px; }
.page-desc { font-size: 12.5px; color: #8b87a5; }
.create-btn {
  border-radius: 10px;
  background: linear-gradient(135deg, #7c6cf0, #6455cf);
  border: none;
  box-shadow: 0 4px 14px rgba(100, 85, 207, 0.3);
}

.users-card { border-radius: 14px !important; }

.role-pill {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 20px;
  display: inline-block;
}
.role-pill.super_admin { background: #f3e8ff; color: #9333ea; border: 1px solid #e9d5ff; }
.role-pill.admin { background: #eef2ff; color: #4f46e5; border: 1px solid #dbe3ff; }
.role-pill.user { background: #f4f3f8; color: #555170; border: 1px solid #e6e3f0; }

.dept-tag {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 20px;
  background: #f0edfb;
  color: #6455cf;
  border: 1px solid #ddd8f8;
}
.dept-tag.super {
  background: #f3e8ff;
  color: #9333ea;
  border-color: #e9d5ff;
}
.dept-tag.user {
  background: #f4f3f8;
  color: #555170;
  border-color: #e6e3f0;
}

.status-pill {
  font-size: 12px;
  padding: 2px 12px;
  border-radius: 20px;
  display: inline-block;
}
.status-pill.active { background: #e8f7ef; color: #16a34a; border: 1px solid #c6ecd9; }
.status-pill.disabled { background: #f4f3f8; color: #8b87a5; border: 1px solid #e6e3f0; }

.users-table :deep(.el-table__header th) {
  background: #faf9fd;
  color: #6455cf;
  font-weight: 600;
}
.users-table :deep(.el-table__row:hover > td) { background: #faf8ff !important; }

.manage-tabs { margin-bottom: 4px; }
.manage-tabs :deep(.el-tabs__item.is-active) { color: #6455cf; }
.manage-tabs :deep(.el-tabs__active-bar) { background: #7c6cf0; }

.audit-bar { display: flex; align-items: center; gap: 12px; }
.audit-total { font-size: 12.5px; color: #8b87a5; }

.role-pill.act-login { background: #eef2ff; color: #4f46e5; border: 1px solid #dbe3ff; }
.role-pill.act-login_failed { background: #fef2f2; color: #dc2626; border: 1px solid #fbcaca; }
.role-pill.act-upload { background: #f0edfb; color: #6455cf; border: 1px solid #ddd8f8; }
.role-pill.act-delete { background: #fef2f2; color: #dc2626; border: 1px solid #fbcaca; }
.role-pill.act-denied { background: #fff7ed; color: #ea580c; border: 1px solid #fed7aa; }
.role-pill.act-user_admin { background: #f3e8ff; color: #9333ea; border: 1px solid #e9d5ff; }
.role-pill.act-register { background: #f0fdf4; color: #16a34a; border: 1px solid #bbf7d0; }
.role-pill.act-redact { background: #f5f3ff; color: #6d28d9; border: 1px solid #ddd6fe; }
</style>
