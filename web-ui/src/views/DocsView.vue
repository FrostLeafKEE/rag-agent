<script setup>
import { ref, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const docs = ref([])
const loading = ref(false)
const uploading = ref(false)
const department = ref('')
const myDepts = ref(null) // null=不限（super_admin）；数组=负责部门（admin）
const keyword = ref('') // 文档搜索：按标题 / 文档 ID 模糊匹配（防抖自动搜索）

let searchTimer = null
watch(keyword, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(refresh, 300)
})

async function refresh() {
  loading.value = true
  try {
    docs.value = (await api.listDocuments({ keyword: keyword.value || undefined })).items
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function loadMyDepartments() {
  try {
    const data = await api.myDepartments()
    myDepts.value = data.departments // null 或数组
  } catch {
    myDepts.value = []
  }
}

// 文档管理页仅 super_admin / admin 可进入（路由守卫 + 后端 403 双保险）
async function handleUpload(file) {
  if (myDepts.value && !myDepts.value.includes(department.value)) {
    ElMessage.warning('请选择负责部门后再上传')
    return false
  }
  uploading.value = true
  try {
    const task = await api.uploadDocument(file, department.value)
    ElMessage.success('已提交上传，正在解析…')
    // 轮询任务状态
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      const status = await api.taskStatus(task.task_id)
      if (status.status === 'done') {
        ElMessage.success(`摄入完成：${status.chunk_count} 个分块`)
        break
      }
      if (status.status === 'failed') {
        ElMessage.error('摄入失败：' + status.error)
        break
      }
    }
    refresh()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    uploading.value = false
  }
  return false // 阻止 el-upload 默认提交
}

async function removeDoc(doc) {
  await ElMessageBox.confirm(`删除文档「${doc.title}」？其检索数据将立即失效。`, '删除文档', {
    type: 'warning',
  })
  try {
    await api.deleteDocument(doc.doc_id)
    ElMessage.success('已删除')
    refresh()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

// 摄入质量报告（数据清洗与质量门禁）
const reportVisible = ref(false)
const report = ref(null)
const reportLoading = ref(false)

async function viewReport(doc) {
  reportLoading.value = true
  reportVisible.value = true
  report.value = null
  try {
    report.value = await api.getIngestionReport(doc.doc_id)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    reportLoading.value = false
  }
}

const REASON_LABELS = {
  too_short: '过短（<10 字符）',
  no_language: '纯符号/编号',
  header_footer: '页眉页脚',
  toc: '目录条目',
}

function statusTag(status) {
  const map = {
    uploading: { text: '上传中', type: 'info' },
    indexed: { text: '已索引', type: 'success' },
    failed: { text: '失败', type: 'danger' },
    disabled: { text: '已停用', type: 'warning' },
  }
  return map[status] || { text: status, type: 'info' }
}

onMounted(() => {
  refresh()
  loadMyDepartments()
})
</script>

<template>
  <div class="docs-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">📄 文档管理</h2>
        <p class="page-desc">上传、检索与维护知识库文档，部门标签用于权限隔离</p>
      </div>
    </div>
    <el-card shadow="never" class="prts-card docs-card">
      <div class="upload-bar">
        <el-input
          v-model="keyword"
          placeholder="🔍 搜索文档（标题 / 文档 ID），回车确认"
          clearable
          style="width: 280px"
          class="dept-input search-input"
        />
        <el-select
          v-if="myDepts"
          v-model="department"
          placeholder="选择负责部门"
          clearable
          style="width: 250px"
          class="dept-input"
        >
          <el-option v-for="d in myDepts" :key="d" :label="d" :value="d" />
        </el-select>
        <el-input
          v-else
          v-model="department"
          placeholder="部门标签（权限过滤用，如：研发部）"
          style="width: 250px"
          clearable
          class="dept-input"
        >
          <template #prefix><span class="input-icon">🏷️</span></template>
        </el-input>
        <el-upload :show-file-list="false" :before-upload="handleUpload" accept=".pdf,.docx,.pptx,.md,.txt,.html">
          <el-button type="primary" :loading="uploading" class="upload-btn">📤 上传文档</el-button>
        </el-upload>
      </div>
      <p class="hint">支持 PDF（含扫描件 OCR）、DOCX、PPTX、Markdown、TXT、HTML；单文件 ≤ 50MB</p>

      <el-table :data="docs" v-loading="loading" style="margin-top: 18px" class="docs-table">
        <el-table-column prop="doc_id" label="文档 ID" width="210" show-overflow-tooltip />
        <el-table-column prop="title" label="标题" min-width="170" show-overflow-tooltip />
        <el-table-column label="部门" width="110">
          <template #default="{ row }">
            <span class="dept-tag">{{ row.department || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <span class="status-pill" :class="row.status">
              {{ statusTag(row.status).text }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="分块" width="70" align="center" />
        <el-table-column prop="uploaded_by" label="上传人" width="100" />
        <el-table-column label="操作" width="140" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="viewReport(row)">质量</el-button>
            <el-button link type="danger" size="small" @click="removeDoc(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="reportVisible" title="摄入质量报告" width="520px">
      <div v-loading="reportLoading">
        <template v-if="report">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="文档 ID">{{ report.doc_id }}</el-descriptions-item>
            <el-descriptions-item label="清洗前块数">{{ report.total_blocks }}</el-descriptions-item>
            <el-descriptions-item label="规则过滤">{{ report.filtered_blocks }}</el-descriptions-item>
            <el-descriptions-item label="内容去重">{{ report.dedup_skipped }}</el-descriptions-item>
            <el-descriptions-item label="平均块长">{{ report.avg_chunk_length }}</el-descriptions-item>
            <el-descriptions-item label="空页数">{{ report.empty_pages }}</el-descriptions-item>
            <el-descriptions-item label="LLM 清洗">{{ report.llm_cleaned }}</el-descriptions-item>
            <el-descriptions-item label="LLM 回退">{{ report.llm_fallback }}</el-descriptions-item>
          </el-descriptions>
          <div v-if="Object.keys(report.noise_reasons || {}).length" class="report-reasons">
            <span class="report-title">丢弃原因分布：</span>
            <el-tag v-for="(count, reason) in report.noise_reasons" :key="reason" size="small" class="reason-tag">
              {{ REASON_LABELS[reason] || reason }} × {{ count }}
            </el-tag>
          </div>
        </template>
        <el-empty v-else-if="!reportLoading" description="该文档暂无质量报告" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.docs-page { padding: 22px 26px; overflow-y: auto; height: 100%; }

.page-title { font-size: 19px; color: #33304a; margin-bottom: 4px; }
.page-desc { font-size: 12.5px; color: #8b87a5; margin-bottom: 16px; }

.docs-card { border-radius: 14px !important; }

.upload-bar { display: flex; gap: 12px; align-items: center; }
.dept-input :deep(.el-input__wrapper) { border-radius: 10px; background: #ffffff; }
.input-icon { font-size: 13px; margin-right: 2px; opacity: 0.8; }
.upload-btn {
  border-radius: 10px;
  background: linear-gradient(135deg, #7c6cf0, #6455cf);
  border: none;
  box-shadow: 0 4px 14px rgba(100, 85, 207, 0.3);
  color: #fff;
}
.hint { margin-top: 10px; font-size: 12px; color: #8b87a5; }

.dept-tag {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 20px;
  background: #f0edfb;
  color: #6455cf;
  border: 1px solid #ddd8f8;
}

.status-pill {
  font-size: 12px;
  padding: 2px 12px;
  border-radius: 20px;
  display: inline-block;
}
.status-pill.indexed { background: #e8f7ef; color: #16a34a; border: 1px solid #c6ecd9; }
.status-pill.uploading { background: #eef2ff; color: #4f46e5; border: 1px solid #dbe3ff; }
.status-pill.failed { background: #fef2f2; color: #dc2626; border: 1px solid #fbcaca; }
.status-pill.disabled { background: #f4f3f8; color: #8b87a5; border: 1px solid #e6e3f0; }

.docs-table :deep(.el-table__header th) {
  background: #faf9fd;
  color: #6455cf;
  font-weight: 600;
}
.docs-table :deep(.el-table__row:hover > td) { background: #faf8ff !important; }

.report-reasons { margin-top: 14px; }
.report-title { font-size: 13px; color: #555170; margin-right: 8px; }
.reason-tag { margin: 2px 4px; }
</style>
