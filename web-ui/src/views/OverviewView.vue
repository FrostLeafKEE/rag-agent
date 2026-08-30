<script setup>
import { ref, onMounted } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { ElMessage } from 'element-plus'
import { api } from '../api'

echarts.use([LineChart, PieChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const loading = ref(true)
const stats = ref({
  user_count: 0, dept_count: 0, doc_count: 0, chunk_count: 0,
  today_questions: 0, trend_7d: [], doc_by_department: [],
})

const CARDS = [
  { key: 'user_count', label: '用户总数', icon: '👤', color: '#4f7cf0' },
  { key: 'dept_count', label: '部门数量', icon: '📁', color: '#67c23a' },
  { key: 'doc_count', label: '文档总数', icon: '📄', color: '#e6a23c' },
  { key: 'today_questions', label: '今日提问', icon: '💬', color: '#f56c6c' },
]

let trendChart = null
let pieChart = null

function renderTrend(trend) {
  const el = document.getElementById('trend-chart')
  if (!el) return
  trendChart = trendChart || echarts.init(el)
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 24, top: 24, bottom: 30 },
    xAxis: { type: 'category', data: trend.map((d) => d.date), boundaryGap: false },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      {
        name: '提问数',
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 7,
        data: trend.map((d) => d.count),
        lineStyle: { color: '#4f7cf0', width: 2.5 },
        itemStyle: { color: '#4f7cf0' },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(79,124,240,0.28)' },
              { offset: 1, color: 'rgba(79,124,240,0.02)' },
            ],
          },
        },
      },
    ],
  })
}

function renderPie(byDept) {
  const el = document.getElementById('pie-chart')
  if (!el) return
  pieChart = pieChart || echarts.init(el)
  pieChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} 篇（{d}%）' },
    legend: { orient: 'vertical', right: 10, top: 'center', textStyle: { color: '#555170' } },
    series: [
      {
        type: 'pie',
        radius: ['45%', '72%'],
        center: ['38%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
        label: { show: false },
        data: byDept.map((d) => ({ name: d.department, value: d.count })),
      },
    ],
    color: ['#4f7cf0', '#a3d977', '#4a4a68', '#e6a23c', '#f56c6c'],
  })
}

function handleResize() {
  trendChart?.resize()
  pieChart?.resize()
}

async function load() {
  loading.value = true
  try {
    stats.value = await api.getOverview()
    // 等 DOM 渲染卡片后再初始化图表
    setTimeout(() => {
      renderTrend(stats.value.trend_7d || [])
      renderPie(stats.value.doc_by_department || [])
      handleResize()
    }, 50)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  window.addEventListener('resize', handleResize)
  await load()
})
</script>

<template>
  <div class="overview-page" v-loading="loading">
    <div class="page-head">
      <div>
        <h2 class="page-title">📊 数据概览</h2>
        <p class="page-desc">知识库运营数据一览（统计范围随角色权限）</p>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div class="stat-cards">
      <div v-for="c in CARDS" :key="c.key" class="stat-card">
        <div class="stat-info">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-value">{{ stats[c.key] }}</div>
        </div>
        <div class="stat-icon" :style="{ background: c.color + '1a', color: c.color }">
          {{ c.icon }}
        </div>
      </div>
    </div>

    <!-- 图表区 -->
    <div class="charts-row">
      <el-card shadow="never" class="prts-card chart-card wide">
        <template #header><span class="chart-title">近 7 天提问趋势</span></template>
        <div id="trend-chart" class="chart-box"></div>
      </el-card>
      <el-card shadow="never" class="prts-card chart-card">
        <template #header><span class="chart-title">部门文档占比</span></template>
        <div id="pie-chart" class="chart-box"></div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.overview-page { padding: 22px 26px; overflow-y: auto; height: 100%; }
.page-title { font-size: 19px; color: #33304a; margin-bottom: 4px; }
.page-desc { font-size: 12.5px; color: #8b87a5; margin-bottom: 16px; }

.stat-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.stat-card {
  background: #fff;
  border: 1px solid #ece9f7;
  border-radius: 14px;
  padding: 20px 22px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  box-shadow: 0 6px 24px rgba(76, 29, 149, 0.06);
}
.stat-label { font-size: 13px; color: #8b87a5; margin-bottom: 6px; }
.stat-value { font-size: 30px; font-weight: 700; color: #33304a; line-height: 1; }
.stat-icon {
  width: 46px;
  height: 46px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
}

.charts-row { display: flex; gap: 16px; }
.chart-card { flex: 1; border-radius: 14px !important; }
.chart-card.wide { flex: 1.6; }
.chart-title { font-size: 14px; font-weight: 600; color: #33304a; }
.chart-box { height: 300px; width: 100%; }
</style>
