<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const kbs = ref([])
const loading = ref(false)
const keyword = ref('')
const dialogVisible = ref(false)
const editing = ref(null)
const form = ref({ name: '', description: '', department: '' })
const SUPER = (() => {
  try {
    return JSON.parse(localStorage.getItem('rag_user') || 'null')?.role === 'super_admin'
  } catch {
    return false
  }
})()

const filteredKbs = computed(() =>
  kbs.value.filter(
    (k) =>
      !keyword.value ||
      k.name.toLowerCase().includes(keyword.value.toLowerCase()) ||
      (k.description || '').toLowerCase().includes(keyword.value.toLowerCase())
  )
)

async function refresh() {
  loading.value = true
  try {
    kbs.value = (await api.listKbs()).items
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.value = { name: '', description: '', department: '' }
  dialogVisible.value = true
}

function openEdit(kb) {
  editing.value = kb
  form.value = { name: kb.name, description: kb.description, department: kb.department }
  dialogVisible.value = true
}

async function save() {
  try {
    if (editing.value) {
      await api.updateKb(editing.value.id, {
        name: form.value.name,
        description: form.value.description,
      })
      ElMessage.success('已保存')
    } else {
      await api.createKb(form.value)
      ElMessage.success('知识库已创建')
    }
    dialogVisible.value = false
    refresh()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function removeKb(kb) {
  try {
    await ElMessageBox.confirm(
      `删除知识库「${kb.name}」？其下文档不受影响，将变为未分组。`,
      '删除知识库',
      { type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await api.deleteKb(kb.id)
    ElMessage.success('已删除')
    refresh()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

onMounted(refresh)
</script>

<template>
  <div class="kbs-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">📚 知识库管理</h2>
        <p class="page-desc">知识库是文档的内容分组层，文档权限继承所属知识库的部门</p>
      </div>
      <el-button v-if="SUPER" type="primary" class="create-btn" @click="openCreate">＋ 新增知识库</el-button>
    </div>

    <el-card shadow="never" class="prts-card kbs-card">
      <div class="bar">
        <el-input
          v-model="keyword"
          placeholder="🔍 搜索知识库名称"
          clearable
          style="width: 280px"
          class="dept-input"
        />
        <span class="count">共 {{ filteredKbs.length }} 个</span>
      </div>
      <el-table :data="filteredKbs" v-loading="loading" class="kbs-table" style="margin-top: 14px">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="知识库名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
        <el-table-column label="部门" width="110">
          <template #default="{ row }">
            <span class="dept-tag">{{ row.department || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="doc_count" label="文档数" width="80" align="center" />
        <el-table-column prop="created_by" label="创建者" width="110" />
        <el-table-column label="操作" width="140" align="center">
          <template #default="{ row }">
            <el-button v-if="SUPER" link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="SUPER" link type="danger" size="small" @click="removeKb(row)">删除</el-button>
            <span v-if="!SUPER" class="readonly-hint">只读</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editing ? `编辑知识库：${editing.name}` : '新增知识库'" width="480px">
      <el-form label-width="96px">
        <el-form-item label="知识库名称">
          <el-input v-model="form.name" maxlength="64" placeholder="唯一名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="256" placeholder="知识库用途说明" />
        </el-form-item>
        <el-form-item label="归属部门">
          <el-input v-model="form.department" :disabled="!!editing" placeholder="权限归属部门（文档继承）" />
        </el-form-item>
        <p v-if="!editing" class="form-hint">提示：编辑时可改名称与描述；变更归属部门会同步迁移其下文档的部门权限。</p>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.kbs-page { padding: 22px 26px; overflow-y: auto; height: 100%; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.page-title { font-size: 19px; color: #33304a; margin-bottom: 4px; }
.page-desc { font-size: 12.5px; color: #8b87a5; }
.create-btn {
  border-radius: 10px;
  background: linear-gradient(135deg, #7c6cf0, #6455cf);
  border: none;
  box-shadow: 0 4px 14px rgba(100, 85, 207, 0.3);
}
.kbs-card { border-radius: 14px !important; }
.bar { display: flex; align-items: center; gap: 12px; }
.count { margin-left: auto; font-size: 12.5px; color: #8b87a5; }
.dept-tag {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 20px;
  background: #f0edfb;
  color: #6455cf;
  border: 1px solid #ddd8f8;
}
.readonly-hint { font-size: 12px; color: #b0accc; }
.form-hint { font-size: 12px; color: #8b87a5; margin: 0 0 8px; }
.kbs-table :deep(.el-table__header th) {
  background: #faf9fd;
  color: #6455cf;
  font-weight: 600;
}
.kbs-table :deep(.el-table__row:hover > td) { background: #faf8ff !important; }
</style>
