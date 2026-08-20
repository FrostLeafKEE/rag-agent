# 企业知识库问答系统 — 介绍 · 部署 · 使用手册

| 项目 | 内容 |
|---|---|
| 版本 | v0.2.0（2026-08-17） |
| 定位 | 企业内部 RAG Agent 知识问答平台（非 AI 客服） |
| 文档 | [PRD.md](./PRD.md)（需求）、[TECH_STACK.md](./TECH_STACK.md)（技术栈）、[P2_PLAN.md](./P2_PLAN.md)（阶段计划）、[RBAC_PLAN.md](./RBAC_PLAN.md)（权限模型） |

---

## 一、项目介绍

### 1.1 是什么

面向企业的知识库问答系统：把内部文档（制度、产品、研发规范等）统一摄入建索引，员工通过自然语言提问，系统从文档中检索依据并**带引用溯源**地作答。回答全部基于知识库内容，权限按部门隔离。

### 1.2 核心能力

| 能力 | 说明 |
|---|---|
| 文档摄入 | 8 种格式（PDF 含扫描件 OCR / DOCX / PPTX / Markdown / TXT / HTML），结构感知分块，幂等更新 |
| 混合检索 | Milvus 稠密向量 + BM25 稀疏 + RRF 融合 + Rerank 精排，部门权限 filter 下推 |
| Agent 问答 | 意图路由 / CRAG 反思 / 多跳拆解 / 主动改写 / 多查询扩展 / 术语表变体 / 工具调用 |
| 流式回答 | SSE 逐字输出，引用标注 [n] 可溯源，会话历史持久化 |
| 安全 | JWT 认证 + RBAC 三角色、登录限速、敏感信息脱敏、审计日志、只读 SQL 工具白名单 |
| 自动摄入 | Connector 定时扫描目录，新文件/变更自动入知识库 |
| 评估闭环 | 黄金集 30 条 + RAGAS 回归门禁；用户点踩自动沉淀评估用例 |
| 可观测 | Langfuse 全链路 trace、Prometheus/Grafana 指标面板 |

### 1.3 技术栈（简表）

Vue3 + Element Plus（前端）· FastAPI + LangGraph（后端）· Milvus（向量库）· PostgreSQL（元数据/用户）· Redis（队列/限速）· Docling/PyMuPDF/PaddleOCR（解析）· BGE-M3 / bge-reranker（嵌入/重排）· RAGAS（评估）· Langfuse（trace）· Docker Compose（部署）

---

## 二、部署手册

### 2.1 环境要求

- Windows / Linux / macOS（本手册以 Windows + Docker Desktop 为例）
- Docker Desktop（≥4.x）与 Docker Compose
- Python 3.12 + [uv](https://docs.astral.sh/uv/)（项目依赖管理）
- Node.js ≥18（仅前端改动时需要重新构建）

### 2.2 基础设施（11 个容器）

```bash
docker compose up -d
```

| 服务 | 端口 | 说明 |
|---|---|---|
| rag-milvus / rag-etcd / rag-milvus-minio | 19530 / 2379 / 9091 | 向量库（含内置 MinIO） |
| rag-postgres | 5432 | 业务库（用户/文档/会话/审计/AdminDepartment） |
| rag-redis | 6379 | 摄入任务队列、登录限速 |
| rag-minio | 9000 / 9001 | 对象存储（预留） |
| rag-langfuse / worker / clickhouse | 3000 | 全链路 trace 平台 |
| rag-prometheus / rag-grafana | 9090 / 3001 | 指标采集与面板 |

> 历史踩坑（详见部署记忆）：ClickHouse 需 named volume；Langfuse 需独立 worker 镜像与 S3 事件上传专用变量；健康检查用 127.0.0.1。

### 2.3 应用配置（.env）

复制 `.env.example` 为 `.env` 并填写：

```bash
# 模型网关（OpenAI 兼容）
LLM_BASE_URL=https://moyuu.cc/v1
LLM_API_KEY=你的密钥
LLM_MODEL=deepseek-v4-flash
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_API_KEY=你的密钥
EMBEDDING_MODEL=BAAI/bge-m3
# 重排（可选，缺 key 自动降级）
RERANK_API_KEY=你的密钥
# Langfuse（可选，缺 key 降级 noop）
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
# JWT（生产环境必须 ≥32 位随机值）
JWT_SECRET=...
```

### 2.4 启动应用

```bash
# 1) 安装依赖
uv sync

# 2) 启动 API 服务（端口 8000）
.venv/Scripts/uvicorn.exe app.main:app --port 8000

# 3) 启动摄入 worker（独立进程，消费上传队列）
.venv/Scripts/python.exe -m app.ingestion.worker

# 4)（可选）Connector 自动摄入常驻进程
.venv/Scripts/python.exe -m app.ingestion.connector
```

> 注意：`uv run` 与 uvicorn 在 Windows 上存在 websockets 文件锁冲突，请直接用 `.venv` 解释器启动服务进程。

### 2.5 首次初始化

```bash
# 建表（API 启动时自动执行 init_db；已由 Alembic 管理的库走迁移）
uv run python -c "import asyncio; from app.db import init_db; asyncio.run(init_db())"
# 或显式执行迁移：uv run alembic upgrade head

# 创建超级管理员（首次使用）
uv run python -m app.cli.create_admin --username admin --password '强密码'

# 验证
curl http://localhost:8000/healthz        # {"status":"ok",...}
```

> **注册开关**：默认开放注册（`AUTH_DISABLE_SIGNUP=false`，开发方便）。
> 生产环境必须设置 `AUTH_DISABLE_SIGNUP=true`（启动强校验，否则拒绝启动）——
> 关闭后注册接口返回 403，账号统一由超级管理员在管理台"用户管理"页创建。

### 2.6 前端

- 构建产物已挂载在 API 根路径：浏览器访问 `http://localhost:8000` 即管理台。
- 前端代码在 `web-ui/`，修改后需重新构建：

```bash
cd web-ui && npm install && npm run build
```

> 浏览器可能缓存旧页面，看到旧样式时 Ctrl+F5 强刷。

### 2.7 生产部署注意

- `APP_ENV=prod` 且 `DEBUG=false`（启动强校验，否则拒绝启动）；
- `JWT_SECRET` 必须 ≥32 位随机值（prod 强校验）；
- `AUTH_DISABLE_SIGNUP=true`（prod 强校验：关闭开放注册，账号由管理员创建）；
- **Schema 变更走 Alembic**：`uv run alembic revision --autogenerate -m "描述"` 生成迁移 → `uv run alembic upgrade head` 应用（初始迁移已覆盖 7 表，存量库已 stamp）；
- 建议 HTTPS 反向代理、定期备份 PostgreSQL 卷与 Milvus 数据；
- API keys 均存 `.env`（不入库），轮换后重启服务生效。

---

## 三、使用手册

### 3.1 角色与权限矩阵

| 能力 | super_admin 超级管理员 | admin 部门管理员 | user 普通用户 |
|---|---|---|---|
| 问答（检索范围） | 全部部门 | 负责部门 | 本部门 |
| 文档管理（上传/删除/列表） | 全部部门 | 仅负责部门 | ❌ 无入口（API 403） |
| 用户管理（创建/角色/部门分配） | ✅ | ❌ | ❌ |
| 审计日志查询 | ✅ | ❌ | ❌ |

### 3.2 普通用户（user）

1. **登录**：账号由管理员创建（开放注册关闭后注册接口 403；开发环境可临时开启 `AUTH_DISABLE_SIGNUP=false` 自注册）。
2. **问答**：在"问答工作台"输入问题，回答流式输出，`[n]` 为引用来源（点击可看文档来源）；支持多轮追问。
3. **反馈**：回答下方 👍/👎 反馈——点踩样本会被自动沉淀为评估用例（管理员可查）。
4. **会话**：左侧历史会话可继续/删除；新会话按钮开启新对话。

### 3.3 部门管理员（admin）

- 由超级管理员创建并**分配负责部门**（可多个）；只能管理负责部门内的文档。
- **上传文档**：文档管理页 → 选择负责部门（下拉限定）→ 上传；支持 PDF/DOCX/PPTX/MD/TXT/HTML，单文件 ≤50MB。
- **删除文档**：仅限负责部门；删除后检索数据立即失效。
- **质量报告**：文档行"质量"按钮 → 弹窗查看摄入清洗统计（总块数/规则过滤/内容去重/平均块长/空页数/丢弃原因分布/LLM 清洗统计）——用于发现低质量文档（过滤率高、空页多）。
- 问答范围与文档管理范围一致（只答负责部门内容）。

### 3.4 超级管理员（super_admin）

**用户管理页**：
- 创建用户：用户名/密码/角色（普通用户/部门管理员/超级管理员）；部门管理员必须指定负责部门；
- 编辑：切换角色、调整归属部门、重置密码、停用/启用；
- 防自锁保护：不能修改自己的角色或停用自己；系统必须保留至少一个超级管理员。

**文档管理**：全部门范围，上传时可指定任意部门。

**审计日志**：管理台"用户管理 → 审计日志"页签——按操作类型过滤、分页查看、一键导出 CSV（登录/上传/删除/越权/角色变更/脱敏等事件）；API 为 `GET /api/v1/admin/audit`（过滤/分页）与 `GET /api/v1/admin/audit/export`（CSV）。

### 3.5 知识库维护（配置文件）

| 配置 | 路径 | 作用 |
|---|---|---|
| Connector 目录监控 | `config/connectors.json` | `[{"path": "D:/目录", "department": "研发部"}]`，新文件/变更自动摄入（含隐藏/锁文件过滤） |
| 术语表 | `config/terms.json` | `{"发版": "发布"}`，口语别名自动生成检索变体 |
| 脱敏规则 | `config/redaction.json` | 内置手机号/身份证/邮箱/密钥等 PII 掩码 + 自定义敏感词 |
| 分块/检索参数 | `.env` | chunk 大小、top_k、CRAG 阈值、多查询变体数等 |
| 数据清洗 | `.env` | `ENABLE_LLM_CLEANING`（默认 false）：开启后对乱码/OCR 特征块做 LLM 清洗（结构化输出强约束，见 TECH_STACK §8.1）；规则清洗（噪声块过滤/内容去重）始终生效，无需配置 |

### 3.6 评估与监控

```bash
# 全量回归（对比基线，劣化 exit 1）
uv run python -m app.eval.regression
# 快速回归
uv run python -m app.eval.regression --limit 8
# 反馈回流：点踩样本 → 评估用例
uv run python -m app.eval.feedback_collect
```

- Grafana 面板：`http://localhost:3001`（请求量/延迟/检索命中率）
- Langfuse trace：`http://localhost:3000`（问答全链路 span，含改写前后、CRAG 重试、工具调用）

---

## 四、常见问题

| 现象 | 原因与处理 |
|---|---|
| 问答无输出 / 报"服务内部错误" | LLM 上游（moyuu.cc）临时故障，稍后重试；权限过滤在检索层已生效，不影响 |
| 页面样式是旧的 | 浏览器缓存，Ctrl+F5 强刷 |
| 上传中文部门名乱码 | 命令行 curl 传中文需 UTF-8（用 requests/前端上传） |
| 文档已删除又被摄入 | 已修复：worker 会丢弃 disabled 文档的任务（版本 v0.2 起） |
| 忘了 super_admin 密码 | 用 CLI 重置：`uv run python -m app.cli.create_admin --username admin --password 新密码`（对已存在用户生效） |

---

*本手册随版本持续更新；部署细节的踩坑记录见项目记忆（基础设施 / Langfuse / 依赖兼容）。*
