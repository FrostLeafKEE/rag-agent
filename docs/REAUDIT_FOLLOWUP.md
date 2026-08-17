# 复审后续报告（RECLOSE 待办清单）

> **复审日期**：2026-08-17
> **复审对象**：FIX_LIST 22 项 + ROADMAP R1-R11（git 提交 33c94f0 → 9f4a017，版本 0.2.0）
> **复审方式**：逐项代码实测 + 本地全量验证（非文档核验）
> **总评**：**改进实质达成，从"工程交付不合格"提升到"基本合格"。** 实测基线：pytest 173 全绿 / ruff check 0 错误 / mypy app/ 0 错误（54 文件）/ git 工作区干净。**剩余 2 个准阻塞项 + 3 个建议项 + 5 个待决策项**，见下。本清单每项自包含（位置/现状/修复建议/验收），可直接领取执行。

---

## 一、已完成并验证通过（**不要重复处理**，仅做回归保障）

| 项 | 验证结果 |
|---|---|
| FIX_LIST P0-1 部门注入 | `build_filter` 用 SAFE_DEPARTMENT 白名单逐项校验（app/retrieval/base.py:50）+ 注册 department 白名单 Field（app/api/routes/auth.py:95）双端校验 ✅ |
| FIX_LIST P0-2 SQL 多表绕过 | `_SQL_FROM_CLAUSE` 迭代 + 逗号 split 逐表校验（app/agent/tools.py:39-43）✅ |
| FIX_LIST P0-4 worker 可靠性 | `_recover_pending`（XPENDING）+ 失败落库 failed+error（app/ingestion/queue.py:69/153-189）✅ |
| FIX_LIST 其余（P1×7 + P2×6 + ruff 清零） | 含在 173 测试与 ruff 0 错误中 ✅ |
| R1 注册开关 | AUTH_DISABLE_SIGNUP 配置（app/config.py:60）+ prod 强制 true（config.py:94）+ 路由拦截（auth.py:117）+ 前端提示 ✅ |
| R2 安全头 | X-Content-Type-Options/X-Frame-Options/Referrer-Policy（app/main.py:43-45）✅ |
| R3 依赖漏洞扫描 | uv audit 纳入 CI（结论：diskcache/ragas 存在无修复版本 CVE-2026-6587，见下"建议项 R3-1"）⚠️ 见 2.2 |
| R4 Alembic | 七表初始迁移 alembic/versions/4b2634627afe + init_db 幂等引导（存在 alembic_version 表则跳过 create_all）✅ |
| R5 mypy | [tool.mypy] check_untyped_defs，app/ 0 错误 ✅ |
| R7 审计增强 | 分页 + action 过滤 + CSV 导出（app/api/routes/admin.py:251 export_audit）✅ |
| R8 CI 流水线 | .github/workflows/ci.yml 三 job（backend/frontend/evaluation）⚠️ 见"准阻塞 R6-1" |
| R9 pre-commit | ruff check/format + uv lock（.pre-commit-config.yaml）⚠️ format 未拦截住，见"准阻塞 R6-2" |
| R10 Runbook | docs/RUNBOOK.md 9 个故障模式 + 索引（超验收 ≥6）✅ |
| R11 CHANGELOG+版本 | CHANGELOG.md + 0.2.0 ✅ |

---

## 二、待办清单

### 准阻塞（必修，共 2 项）

**R6-1. CI 分支名不匹配——CI 从未真实运行过**
- 位置：`.github/workflows/ci.yml:5`
- 现状：workflow 监听 `on.push.branches: [main]`，但仓库分支是 **master**（git branch 实测）。push 到 master 永不触发 CI，意味着 R8"验收：push 触发全绿"从未验证过。
- 修复：`branches: [main, master]`（或按团队约定统一分支名后改对应值）；若代码托管平台允许，另建议在仓库设置里把 master 设为默认+受保护分支（配合后续 PR 评审）。
- 验收：push 到 master 真实触发 CI 且全绿。

**R6-2. ruff format 未达标——CI 的 format 检查当前必挂**
- 位置：`app/api/routes/admin.py:240` 附近（`ruff format --check .` 实测报 2 个文件需格式化，admin.py 为 R7 改动引入；R6 验收"format --check 通过"实际未达成）
- 现状：ROADMAP R6 宣称完成，但本地 `uv run ruff format --check .` 报 2 文件 unformatted；pre-commit 的 ruff-format 未拦住（疑似 --no-verify 或 hook 未安装）。因 R6-1 的 CI 从未运行，此问题未被暴露。
- 修复：`uv run ruff format .`（或仅格式化报错文件）后提交；确认 `uv run ruff format --check .` 0 文件待格式化；检查 pre-commit 是否已 `pre-commit install`。
- 验收：本机 `ruff format --check .` 全过；R6 才算真正关闭。

### 建议项（共 3 项）

**R3-1. uv audit 无失败信号**
- 位置：`.github/workflows/ci.yml`（`uv audit || true`）
- 现状：已知无修复版本漏洞（diskcache/ragas，CVE-2026-6587）用 `|| true` 不阻断，导致漏洞扫描不产生任何失败信号，"无未处理高危"无法自动验证。
- 建议：拆成独立 job（或保留 || true 但）：① 把当前已知漏洞+理由写进 docs/（如 ROADMAP R3 已注，另补 `docs/SECURITY_NOTES.md` 记录 CVE-2026-6587 详情与复核日期）；② 定期（如每月）人工复核"是否有新修复版本"；③ 可选：用 `uv audit --severity high` 加 gate——仅当**存在可修复的高危**时置红（需查 uv audit 是否支持按严重度退出码过滤，若支持用此实现自动化）。
- 验收：已知漏洞有文档化记录与复核节奏；新出现可修复高危时 CI 能红。

**R6-3. 跑一次真实 CI 全链路验证**
- 前置：R6-1 修复后，push 一次（或推送一个空提交 `git commit --allow-empty` + push）触发 CI，确认 backend（ruff/mypy/pytest/audit）+ frontend（vite build）全绿；evaluation job 为手动触发，本次如无真实模型 key 可跳过但需说明。
- 验收：GitHub Actions 页面有本次运行记录且 backend/frontend 全绿。

**H1-1. deptry 依赖声明检查（可选项，带误报处理说明）**
- 位置：pyproject.toml / CI
- 现状：`uvx deptry .` 实测报 17 项 DEP002（如 python-multipart/uvicorn/websockets 判定"声明未使用"）——多数为误报（框架动态使用、CLI 入口），deptry 无法识别。
- 建议（若做）：配置 deptry 的 `exclude`/`per-file-ignores` 排除已知误报后接入 CI；若不做，在 ROADMAP"明确不做"清单里补一行说明（当前 H1 既未做也未声明不做，属挂起）。最低要求：把 langgraph/langchain_openai 已声明的情况在 ROADMAP/IMPROVEMENT_PLAN 里勾掉，避免后续重复审计。
- 验收：二选一——deptry 0 问题进 CI，或 ROADMAP 记录"不做及理由"。

### 待决策项（5 项，建议补齐"做 or 不做+理由"，避免挂起）

以下为建议书（IMPROVEMENT_PLAN.md）中既未被 R1-R14 覆盖、也未写入 ROADMAP"明确不做"清单的条目，请逐条决定并更新 ROADMAP：

| # | 项 | 建议 | 工作量 |
|---|---|---|---|
| D-1 | F4/CONTRIBUTING 评审检查单 + 模块依赖方向 | **做**（成本最低，与 A1 提交规范配套）：docs/CONTRIBUTING.md 写 PR 检查单（安全影响/日志/测试/迁移/文档同步）+ 依赖方向（agent → retrieval/llm，禁反向） | S |
| D-2 | E3 外部依赖降级矩阵 | 做：把已有降级行为反向梳理成文档（LLM 超时120s/重排降 RRF/OCR 跳页/Docling 降级 PyMuPDF/队列重投≤3）入 TECH_STACK，补 Milvus 显式超时一行验证记录 | S-M |
| D-3 | G1 评估结果时间序列归档 | 试用期后做：每次回归结果带时间戳归档，对比取均值（RAGAS 方差 ±0.1）——可暂缓，进 ROADMAP 第三批 | S |
| D-4 | H2 升级窗口 | 试用期后做：季度 uv lock 刷新 + 全量测试 + 记录兼容结论；websockets==15.0.1 保留理由（uvicorn 兼容）需写进 ROADMAP/TECH_STACK 防后人误改 | S |
| D-5 | I3 安全评审周期 | 做：威胁模型一节 + 季度审计节奏入 ROADMAP（当前功能大改后建议最近一次审计） | S |

---

## 三、验收标准（本次清单全部关闭的判定）

1. `ruff format --check .` 0 文件待格式化；`ruff check .` 0 错误；`mypy app/` 0 错误；`pytest tests/ -q` 全绿（应 ≥173）；
2. GitHub Actions 有真实运行记录且 backend/frontend 全绿（R6-1/R6-3 验证）；
3. uv audit 已知漏洞在 docs/ 有记录与复核节奏（R3-1）；
4. ROADMAP.md 更新：准阻塞项勾掉、建议项处理完、待决策项全部"做/不做"闭环，无挂起；
5. 回归门禁 `uv run python -m app.eval.regression`（需真实模型 key）结果与基线对比无劣化——若本次无 key 无法验证，请在报告中明确说明。

---

*报告完。已完成项无需重复验证；准阻塞两项（R6-1/R6-2）是本次唯一"宣称完成但实测未达成"的部分，务必优先。*