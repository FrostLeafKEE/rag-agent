# 企业级 RAG Agent — 知识库问答平台

> 把内部文档变成可问答的知识库：混合检索 + Agent 编排 + 引用溯源 + RBAC 权限 + 评估回归 + 全链路可观测。
> 当前版本 **0.2.0**（[CHANGELOG](CHANGELOG.md)）· 173 测试全绿 · ruff/mypy 0 错误

---

## 目录

- [1. 项目是什么](#1-项目是什么)
- [2. 核心特性](#2-核心特性)
- [3. 架构总览](#3-架构总览)
- [4. 技术栈](#4-技术栈)
- [5. 快速开始](#5-快速开始)
- [6. 角色与权限](#6-角色与权限)
- [7. 目录结构](#7-目录结构)
- [8. 质量与评估](#8-质量与评估)
- [9. 安全设计](#9-安全设计)
- [10. 文档索引](#10-文档索引)

---

## 1. 项目是什么

**定位**：面向企业内部的知识库问答系统（不是 AI 客服）。员工用自然语言提问，系统从内部文档中检索依据，生成**带引用溯源**的回答，回答内容严格限定在知识库范围内。

**解决的问题**：企业文档散落在 PDF/Word/Markdown 中，员工检索困难、回答无依据、权限难隔离。本系统将文档统一摄入建索引，提供：

- 一句话问出制度/产品/研发规范等问题的准确答案（附原文出处）；
- 部门级权限隔离（不同部门只能看到自己部门的文档与回答）；
- 知识库内容由管理员统一维护（含目录自动摄入），普通员工只负责提问。

**核心链路**：上传/扫描文档 → 解析（OCR）→ 结构感知分块 → 向量化 → 混合检索 → Agent 编排（路由/改写/反思/拆解）→ 流式回答 + 引用。

---

## 2. 核心特性

### 2.1 文档摄入
- **8 种格式**：PDF（含扫描件 OCR，PaddleOCR 自动识别）、DOCX、PPTX、Markdown、TXT、HTML；
- **结构感知分块**：按标题层级切块，表格整块保留，块内保留章节路径；
- **幂等更新**：同名文档重传即新版本，旧分块自动清理（先删后写）；
- **异步队列**：上传 → Redis Stream → 独立 worker 消费，失败自动重投（≤3 次）；
- **Connector 自动摄入**：配置目录后"丢文件进文件夹就进库"，文件变更自动重新摄入（指纹去重，支持隐藏/锁文件过滤）。

### 2.2 检索质量
- **混合检索**：BGE-M3 稠密向量 + Milvus 内置 BM25（中文分词）+ RRF 融合，再经 bge-reranker 精排；
- **权限下推**：部门过滤在向量库查询阶段执行（filter 下推），禁止"查全量再过滤"；
- **查询增强**：主动改写（口语→检索式）、多查询扩展（3 变体）、术语表变体（"发版"→"发布"）、多跳拆解（对比/兼容类问题拆子问题）。

### 2.3 Agent 问答（LangGraph 编排）
- **意图路由**：知识问答 / 文档总结 / 闲聊拒答 / 需澄清，四类识别；
- **CRAG 反思**：检索置信度低时自动改写查询重检索（≤2 轮），仍低则明确拒答；
- **工具调用**：Agent 可按需调用只读 SQL 工具（如"知识库有多少文档"），白名单 + LIMIT + 值截断；
- **流式输出**：SSE 逐字生成，引用 `[n]` 标注可溯源，多轮会话持久化。

### 2.4 安全与合规
- **RBAC 三角色**：超级管理员（全权）/ 部门管理员（负责 N 个部门）/ 普通用户（仅问答）；
- **认证加固**：JWT（prod 强制 ≥32 位密钥）、登录 5 次失败锁定 5 分钟、注册防提权、bcrypt 72 字节上限；
- **敏感信息脱敏**：手机号/身份证/银行卡/邮箱/密钥/IP 六类 PII 在摄入与回答双环节掩码，命中审计；
- **审计日志**：登录/上传/删除/越权/角色变更全程记录，super_admin 可查；
- **防注入**：Milvus filter doc_id 白名单、只读 SQL 工具多重建模约束。

### 2.5 评估与可观测
- **黄金集 30 条** + 检索评估（recall@k/MRR）+ RAGAS 生成评估（faithfulness / answer_relevancy / context_precision）；
- **回归门禁**：对比基线，指标劣化 >5% 即失败退出（CI 接入点）；
- **反馈回流**：用户点踩自动沉淀为评估用例（LLM 分类意图 + 生成参考答案）；
- **全链路 trace**：Langfuse（改写前后、CRAG 重试、工具调用）；
- **指标面板**：Prometheus + Grafana（请求量/延迟/检索命中率/Token 消耗）。

### 2.6 Web 管理台
Vue3 + Element Plus（暗紫渐变侧边栏 + 亮色内容区）：问答工作台（流式/引用/会话/反馈）、文档管理（按角色）、用户管理（super_admin：创建/角色/部门分配/重置密码/停用）。

---

## 3. 架构总览

```
┌──────────────────────────────┐        ┌──────────────────────────────┐
│  Web 管理台 (Vue3)            │        │  摄入链路                     │
│  问答 / 文档管理 / 用户管理    │        │  上传 API / Connector 扫描     │
└──────────────┬───────────────┘        │  → Redis Stream 队列          │
               │ SSE / REST             │  → worker：解析(OCR)→分块     │
┌──────────────▼───────────────┐        │    →脱敏→嵌入→写 Milvus       │
│  API 层 (FastAPI)             │        └──────────────┬───────────────┘
│  认证(RBAC) → 文档 → 问答      │                       │
│  SSE 流式 · 审计 · 限速        │                       ▼
└──────────────┬───────────────┘        ┌──────────────────────────────┐
               │ 问题 + 部门可见范围      │  存储层                       │
┌──────────────▼───────────────┐        │  Milvus：向量+BM25+权限标签   │
│  Agent 编排 (LangGraph)       │        │  PostgreSQL：用户/文档/审计   │
│  路由→工具→拆解→改写→多查询    │        │  Redis：队列/限速            │
│  →检索→CRAG反思→生成          │        └──────────────────────────────┘
└──────────────────────────────┘
               │
               ▼
      LLM 网关（OpenAI 兼容：deepseek / bge-m3 / bge-reranker）
      可观测：Langfuse trace · Prometheus 指标
```

**关键设计原则**：
1. **权限过滤在检索层**：部门 filter 随查询下推到 Milvus，杜绝越权读取；
2. **摄入与问答解耦**：上传只入队，worker 独立进程消费，互不阻塞；
3. **图只做决策**：LangGraph 负责路由/改写/反思，LLM 流式生成由服务层驱动，SSE 契约稳定；
4. **评估先行**：基线 + 回归门禁保证每次改动可度量、劣化即阻断。

---

## 4. 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 前端 | Vue3 + Element Plus + Vite | 暗紫主题管理台，构建产物挂 FastAPI |
| API | FastAPI + SQLAlchemy(async) + Pydantic | SSE 流式、RBAC 依赖注入 |
| Agent | LangGraph | 状态机编排：8 个节点 |
| 向量库 | Milvus 2.5 | 稠密 + BM25 稀疏 + RRF + filter 下推 |
| 关系库 | PostgreSQL 16 | 用户/文档/会话/审计/AdminDepartment |
| 缓存/队列 | Redis | Stream 摄入队列、登录限速计数 |
| 解析 | Docling + PyMuPDF + PaddleOCR | 布局感知 + 扫描件 OCR |
| 嵌入/重排 | BGE-M3 / bge-reranker-v2-m3（SiliconFlow） | 中文优化 |
| LLM | deepseek-v4-flash（OpenAI 兼容网关） | 路由/改写/生成/工具判断 |
| 评估 | RAGAS + 自研 recall@k/MRR | 黄金集 30 条 + 回归门禁 |
| 可观测 | Langfuse + Prometheus + Grafana | trace / 指标 / 面板 |
| 部署 | Docker Compose（11 容器） | Milvus/PG/Redis/MinIO/Langfuse/监控 |

---

## 5. 快速开始

> 完整部署与使用细节见 [📖 MANUAL.md](docs/MANUAL.md)（部署手册 + 使用手册 + 常见问题）。

```bash
# 1. 基础设施（11 容器：Milvus/PG/Redis/MinIO/Langfuse/ClickHouse/监控）
docker compose up -d

# 2. 配置模型密钥
cp .env.example .env   # 填 LLM_API_KEY / EMBEDDING_API_KEY / LANGFUSE_* 等

# 3. 依赖与建表
uv sync
uv run python -c "import asyncio; from app.db import init_db; asyncio.run(init_db())"

# 4. 创建超级管理员
uv run python -m app.cli.create_admin --username admin --password '强密码' --department 研发部

# 5. 启动服务（两个终端；uvicorn 运行时勿用 uv run，见 MANUAL 2.4）
.venv/Scripts/uvicorn.exe app.main:app --port 8000      # API + Web 管理台
.venv/Scripts/python.exe -m app.ingestion.worker        # 摄入 worker

# 6. 使用
#   Web 管理台：http://localhost:8000
#   Swagger：   http://localhost:8000/docs
#   （可选）Connector 目录自动摄入：.venv/Scripts/python.exe -m app.ingestion.connector
```

---

## 6. 角色与权限

| 能力 | super_admin 超级管理员 | admin 部门管理员 | user 普通用户 |
|---|---|---|---|
| 问答（检索范围） | 全部部门 | 负责部门（可多个） | 本部门 |
| 文档管理（上传/删除/列表） | 全部部门 | 仅负责部门 | ❌ 无入口（API 403） |
| 用户管理（创建/角色/部门分配） | ✅ | ❌ | ❌ |
| 审计日志 | ✅ | ❌ | ❌ |

- 部门管理员由超级管理员创建并分配负责部门（`AdminDepartment` 表，覆盖式更新）；
- 普通用户通过注册接口或管理员创建，注册固定 role=user（防提权）；
- 防自锁：不能修改自己的角色/停用自己；系统必须保留至少一个超级管理员。

---

## 7. 目录结构

```
├── app/
│   ├── agent/            # LangGraph 图：nodes（8 节点）/ graph / service / tools / state
│   ├── api/              # FastAPI 路由：auth / documents / qa(SSE) / sessions / admin
│   │   ├── deps.py       # JWT 认证 + RBAC 可见范围统一入口
│   │   ├── security.py   # 密码哈希 / JWT 编解码
│   │   └── audit.py      # 审计日志
│   ├── eval/             # 黄金集 / 检索评估 / RAGAS 生成评估 / 回归门禁 / 反馈回流
│   ├── ingestion/        # parser(OCR) / chunker / embedder / indexer / pipeline / queue / worker / connector
│   ├── llm/              # 模型网关（OpenAI 兼容）/ 提示词
│   ├── observability/    # Langfuse trace / Prometheus 指标
│   ├── retrieval/        # 混合检索 / RRF / 重排 / 术语表 / 权限 filter
│   └── security/         # PII 脱敏引擎
├── web-ui/               # Vue3 管理台（src/views：Chat/Docs/Users/Login）
├── config/               # connectors.json（目录监控）/ terms.json（术语）/ redaction.json（脱敏）
├── infra/                # docker-compose / prometheus / grafana
├── docs/                 # 全部文档（见 §10）
└── tests/                # 173 个测试（含安全回归 + RBAC 矩阵 + 审查回归）
```

---

## 8. 质量与评估

**测试**：173 个用例全绿（pytest）——单元/API/集成/安全回归/RBAC 矩阵/审查回归。

**质量门禁（本地 + CI 一致）**：

```bash
uv run ruff check .          # 0 错误
uv run ruff format --check . # 0 待格式化
uv run mypy app/             # 0 错误（54 文件）
uv run pytest tests/ -q      # 173 全绿
```

- **CI**：`.github/workflows/ci.yml`（backend 四门禁 + frontend vite build + 手动触发的评估回归）
- **迁移**：Alembic（`uv run alembic upgrade head`，初始迁移覆盖 7 表；`init_db` 自动感知）
- **pre-commit**：提交前自动 ruff + lock 检查
- **版本**：0.2.0（语义化版本，见 CHANGELOG.md）

```bash
uv run pytest tests/ -q

# 评估与回归
uv run python -m app.eval.retrieval_eval        # 检索评估 recall@k/MRR
uv run python -m app.eval.generation_eval       # RAGAS 三指标
uv run python -m app.eval.regression            # 回归门禁（对比基线，劣化 exit 1）
uv run python -m app.eval.feedback_collect      # 点踩样本 → 评估用例
```

**当前基线**（docs/eval_baseline.json）：recall@5=0.852 / MRR=0.796 / faithfulness=0.931 / answer_relevancy=0.681 / context_precision=0.918 / 拒答 1.0。RAGAS judge 单次评估方差 ±0.1，对比需多次取均值。

---

## 9. 安全设计

- **认证**：JWT HS256 + exp 校验；prod 强制 ≥32 位密钥与 debug=False（启动校验）；
- **RBAC**：三角色 + 部门 filter 下推；文档管理仅管理员（前端隐藏 + 后端 403 双保险）；
- **注入防护**：Milvus filter doc_id 白名单 `^[\w\-]{1,64}$`；只读 SQL 工具（仅 SELECT/单语句/表白名单/LIMIT）；
- **脱敏**：摄入与回答双环节 PII 掩码，命中审计；
- **爆破防护**：登录 5 次失败锁 5 分钟（Redis 计数，服务不可用时降级放行）；
- **审计**：登录/上传/删除/越权/角色变更全程记录；
- **已知边界**：上传的 doc_id 冲突归属校验（仅 super_admin 可覆盖）；空部门用户零可见。

---

## 10. 文档索引

| 文档 | 内容 |
|---|---|
| **[MANUAL.md](docs/MANUAL.md)** | 📖 介绍 · 部署手册 · 使用手册 · 常见问题 |
| **[CHANGELOG.md](CHANGELOG.md)** | 版本记录（当前 0.2.0，语义化版本策略） |
| [PRD.md](docs/PRD.md) | 需求（FR-01~46）+ 里程碑 + 附录（实现核对/安全审计记录） |
| [TECH_STACK.md](docs/TECH_STACK.md) | 选型理由、架构图、数据模型、ADR、评估基线、外部依赖降级矩阵 |
| [ROADMAP.md](docs/ROADMAP.md) | 改进路线图（三批 14 项 + 复审闭环，前两批已完成） |
| [RUNBOOK.md](docs/RUNBOOK.md) | 运维故障处置手册（9 个故障模式） |
| [CONTRIBUTING.md](docs/CONTRIBUTING.md) | 贡献指南：PR 检查单、质量门禁、模块依赖方向 |
| [SECURITY_NOTES.md](docs/SECURITY_NOTES.md) | 依赖漏洞跟踪（CVE 记录 + 每月复核节奏） |
| [FIX_LIST.md](docs/FIX_LIST.md) | 缺陷级审查清单（22 项已修复并勾选） |
| [IMPROVEMENT_PLAN.md](docs/IMPROVEMENT_PLAN.md) | 治理级改进建议书（30 项）+ [核验报告](docs/IMPROVEMENT_PLAN_REVIEW.md) |
| [REAUDIT_FOLLOWUP.md](docs/REAUDIT_FOLLOWUP.md) | 复审报告（准阻塞已修复，处理记录见 ROADMAP） |
| [P1_PLAN.md](docs/P1_PLAN.md) | 完善阶段 W1~W6（Agent 化/检索增强/管理台/工程化/评估） |
| [P2_PLAN.md](docs/P2_PLAN.md) | 企业化阶段（工具/反馈/Connector/脱敏/术语表）+ RBAC 扩展 |
| [RBAC_PLAN.md](docs/RBAC_PLAN.md) | 三角色权限模型设计 |
| [eval_baseline.json](docs/eval_baseline.json) | 回归基线数据 |
| [feedback_cases.json](docs/feedback_cases.json) | 反馈回流沉淀的评估用例 |
