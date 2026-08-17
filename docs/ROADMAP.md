# 改进路线图（基于 IMPROVEMENT_PLAN 核验）

> **制定日期**：2026-08-17
> **依据**：[IMPROVEMENT_PLAN_REVIEW.md](./IMPROVEMENT_PLAN_REVIEW.md)（30 项核验：21 属实 / 9 部分属实 / 2 误报）
> **原则**：只做对"当前阶段（本地开发 → 小范围试用）"真实有价值的事；每条带工作量与验收；做不动的明确写"不做及理由"。
> **前置状态**：FIX_LIST 缺陷级问题已全部修复（169 测试全绿，ruff 0 错误，git 已 init）。

---

## 第一批：上线前必做（P0，小改动高价值）

| # | 项目 | 来源 | 工作量 | 内容与验收 |
|---|---|---|---|---|
| R1 | **注册开关** ✅ | | D2 实质 | S | 新增 `AUTH_DISABLE_SIGNUP` 配置（默认 false 保持开发可用）；prod 强制 true（config 校验）；前端注册提示随开关。验收：prod 配置下注册接口 403，用户由 super_admin 创建 |
| R2 | **安全响应头** ✅ | | D3 | S | 中间件：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy`；CORS 策略说明入 TECH_STACK。验收：响应含安全头，测试覆盖 |
| R3 | **依赖漏洞扫描** ✅ | | D1 | S | `uv audit` 纳入常规检查（记录当前结果）；无未处理高危。验收：audit 报告 0 高危 |
| R4 | **Alembic 迁移** ✅ | | B1 | M | `alembic init` + env.py 指向 Base.metadata；生成七表初始迁移；`init_db` 幂等引导（有 alembic_version 则跳过）。验收：空库 upgrade head 建全表；存量库 stamp head 可用 |
| R5 | **mypy 起步** ✅ | | F1 | M | mypy（check_untyped_defs，非 strict）配置 + app/ 清零。验收：`mypy app/` 0 错误 |
| R6 | **ruff format** ✅ | | F2 剩余 | S | 统一格式 + CI 检查。验收：`ruff format --check` 通过 |

## 第二批：试用期工程化（小范围试用后按需）

| # | 项目 | 来源 | 工作量 | 内容与验收 |
|---|---|---|---|---|
| R7 | **审计查询增强** ✅ | | D4 | M | 分页 + 按 action 过滤 + CSV 导出。验收：管理端可按类型查询并导出 |
| R8 | **CI 流水线** ✅ | | A2 | M | `.github/workflows/ci.yml`：ruff(0) → pytest(全绿) → vite build；评估 job 手动触发。验收：push 触发全绿 |
| R9 | **pre-commit** ✅ | | A3 | S | ruff check/format + uv lock --check。验收：违规提交被拦截 |
| R10 | **Runbook** ✅ | | I2 | S | docs/RUNBOOK.md：≥6 个已知故障模式（worker 残留/websockets 锁/Milvus 挂起/Redis 假死/Langfuse 事件缺失/上游 LLM 故障），症状→诊断→处置→预防。验收：覆盖 ≥6 模式 |
| R11 | **CHANGELOG + 版本策略** ✅ | | I1 | S | 语义化版本 + CHANGELOG.md + 发布检查单。验收：最近变更已记录 |

## 第三批：数据治理（试用期暴露真实数据量后）

| # | 项目 | 来源 | 工作量 | 内容与验收 |
|---|---|---|---|---|
| R12 | **备份策略文档** | B2 | S（文档） | pg_dump 每日 + 卷快照说明 + 恢复演练步骤。验收：docs/BACKUP.md 有可执行步骤 |
| R13 | **数据留存清理** | B4 | M | 会话消息/审计日志可配置留存期 + worker 定时清理任务。验收：清理任务可配置运行且不影响检索 |
| R14 | **MinIO 文档归档** | B3 | M | 原始文档上传 MinIO（bucket 按部门），本地仅临时中转；删除联动。验收：上传→归档→删除全链路 |

## 明确不做（及理由）

| 项目 | 来源 | 理由 |
|---|---|---|
| 容器化 api/worker | C1 | 当前单机 Windows 部署 + 手动双终端可维护；PaddleOCR 镜像数 GB，成本大于收益。**等真正上服务器时再做**（届时按建议书拆分 api/worker 镜像） |
| 告警 Alertmanager | E1 | 无真实监控值班需求前过度建设；面板 + 日志已可排障 |
| 日志集中收集 / structlog | E2 | 单机 stdout 足够；structlog 刚删除，标准库 logging 配 request_id 即可（R2 阶段若做日志增强仅加 request_id） |
| 压测脚本化 | G3 | 已有 50 并发基线；无真实业务量前阈值无意义 |
| 覆盖率门禁 | G2 | 低 ROI；核心路径已有安全/RBAC 回归覆盖 |
| 前端 ESLint/Vitest | F3 | 管理台功能稳定；补 lint 可做但优先级低，列入 backlog |
| 密钥轮换流程 | C3 | 文档化进入 Runbook（R10）即可；单机部署轮换成本高 |
| 容量规划文档 | C4 | 试用期数据量小；Milvus standalone 够用，集群路径在 R14 评估时顺带记录 |
| Langfuse PII 出口 | D 域注意 | 内部部署（自托管 Langfuse）无出境问题，知悉即可 |

---

## 实施节奏建议

1. **第一批（R1~R6）**：一次性做完（约 3~4 天工作量），每项补测试，全量回归 + 更新 FIX_LIST 式勾选；
2. **第二批（R7~R11）**：试用开始后按反馈插入（R10 Runbook 建议最先，成本最低收益直接）；
3. **第三批（R12~R14）**：试用期真实数据量出现后启动；
4. 每批结束跑一次回归门禁（`uv run python -m app.eval.regression`），保证评估基线不劣化。

---

## 复审后续（REAUDIT_FOLLOWUP 处理记录，2026-08-17）

**准阻塞（已修复）**：
- R6-1 ✅ CI 分支改 `[main, master]`（仓库默认分支为 master，此前 CI 永不触发）
- R6-2 ✅ `ruff format` 补跑（R7 改动后遗漏的 2 个文件），`format --check` 0 待格式化

**建议项**：
- R3-1 ✅ `docs/SECURITY_NOTES.md` 记录 CVE-2026-6587/CVE-2025-69872 详情 + 每月复核节奏
- R6-3 ⏸ 真实 CI 运行**需要 GitHub remote**（用户提供后 push 验证）；evaluation job 需真实模型 key
- H1-1 ✅ 决策：**不做 deptry**（17 项 DEP002 多为框架动态使用/CLI 入口误报，治理收益低）；langgraph/langchain-openai 已声明（FIX_LIST P1-9）

**待决策项**：
- D-1 ✅ 做：`docs/CONTRIBUTING.md`（PR 五项检查单 + 模块依赖方向 + 质量门禁）
- D-2 ✅ 做：TECH_STACK §8.1 外部依赖降级矩阵（8 项反向梳理）
- D-3 ⏸ 评估结果时间序列归档：进第三批（试用期后，配合 G1）
- D-4 ⏸ 季度升级窗口：进第三批；websockets==15.0.1 / langchain-community<0.4 保留理由已记录于 TECH_STACK/记忆
- D-5 ⏸ 安全评审周期：季度审计节奏入 CONTRIBUTING（已含"安全影响"检查项）；威胁模型待试用期后补充

**状态**：REAUDIT 验收标准 1（本地四门禁）✅；标准 2/5 需 remote+真实 key；标准 3 ✅（SECURITY_NOTES）；标准 4 ✅（本记录）。
