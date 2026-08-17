<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const users = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref(null) // null=新建；对象=编辑
const form = ref({ username: '', password: '', department: '', role: 'user', admin_departments: [] })

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

onMounted(refresh)
</script>

<template>
  <div class="users-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">👥 用户管理</h2>
        <p class="page-desc">超级管理员专属：创建用户、分配角色、指定部门管理员的负责部门</p>
      </div>
      <el-button type="primary" class="create-btn" @click="openCreate">＋ 创建用户</el-button>
    </div>

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
</style>
