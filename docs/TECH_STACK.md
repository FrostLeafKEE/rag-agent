# 技术栈与架构设计

| 项目 | 内容 |
|---|---|
| 文档版本 | v0.1（规划稿） |
| 编写日期 | 2026-08-16 |
| 关联文档 | [PRD.md](./PRD.md) |

---

## 1. 设计原则

1. **接口抽象、实现可换**：向量库、LLM、Embedding、解析器均以接口层隔离，P0 到 P2 演进时不改动上层代码。
2. **评估先行**：检索与生成链路从第一天就接评估脚本，改动有量化依据。
3. **检索层权限过滤**：权限过滤必须在向量库查询阶段执行（filter 下推），禁止"查全量再过滤"。
4. **异步管道**：文档摄入与问答服务完全解耦，摄入失败不影响在线服务。
5. **可观测性内建**：从 P0 开始全链路 trace，不后补。

## 2. 总体架构

```
┌───────────────────────── 客户端 ─────────────────────────┐
│   Web 管理台 (Vue3)        问答界面          OpenAPI 对接方   │
└────────────────────────────┬───────────────────────────────┘
                             │ HTTPS / SSE
┌────────────────────────────▼───────────────────────────────┐
│                    API 层 (FastAPI)                         │
│   认证/RBAC → 路由 → 会话管理 → 流式输出 → 限流              │
└──────┬──────────────────────────────────────┬──────────────┘
       │                                       │
┌──────▼──────────────┐            ┌───────────▼──────────────┐
│   Agent 编排 (LangGraph)        │   摄入管道 (Worker)        │
│  意图路由→改写→检索→重排→CRAG   │   解析→分块→嵌入→写库      │
│  →生成(带引用)                  │   (Celery/RQ 或独立进程)   │
└──────┬──────────────┘            └───────────┬──────────────┘
       │                                       │
┌──────▼───────────────────────────────────────▼──────────────┐
│                  检索/存储层（接口抽象）                       │
│   混合检索：向量(Milvus) + BM25 → RRF → Reranker             │
│   元数据/权限 filter 在此层执行                               │
└──────┬──────────────────────────────┬───────────────────────┘
       │                              │
┌──────▼──────────┐        ┌─────────▼──────────┐
│  模型网关        │        │  基础组件           │
│  LLM / Embed /  │        │  PostgreSQL(元数据) │
│  Reranker        │        │  Redis(缓存/队列)  │
│  (OpenAI 兼容层) │        │  MinIO(文件存储)   │
└─────────────────┘        └────────────────────┘
┌────────────────────────────────────────────────────────────┐
│ 横切：Langfuse(可观测) · RAGAS(评估) · 审计日志 · 配置中心    │
└────────────────────────────────────────────────────────────┘
```

## 3. 技术选型

### 3.1 运行时与工程

| 组件 | 首选 | 备选 | 理由 |
|---|---|---|---|
| 语言/运行时 | Python 3.12（uv 管理） | — | 生态最全；本机 Python 3.14 过新，兼容风险大，**用 uv 锁定 3.12** |
| 包管理 | uv | poetry | 快、锁文件可靠、Python 版本管理一体 |
| API 框架 | FastAPI + Pydantic v2 | — | 异步、OpenAPI 自动生成、流式 SSE 支持好 |
| 任务队列 | 独立进程 + Redis Stream | Celery | **已落地**：`app/ingestion/queue.py`（Stream + 消费组，失败重投 ≤3 次）+ `worker.py` 独立进程；任务状态存 documents 表跨进程可查 |
| Web 管理台 | Vue3 + Element Plus | React | 中文生态、后台管理组件齐全（P1 起） |

### 3.2 解析层

| 组件 | 首选 | 备选 | 理由 |
|---|---|---|---|
| 文档解析 | Docling (IBM) + PyMuPDF | Unstructured | 布局感知（标题/表格/多栏），输出结构化块，PDF/DOCX/PPTX 全覆盖；PyMuPDF 做文本型 PDF 快速路径 |
| OCR | PaddleOCR / Tesseract | — | **已落地**：PaddleOCR 3.7 + paddlepaddle，扫描件 PDF 自动降级 OCR（整页文本 <20 字符触发），中文识别已验证；注意 paddle 3.x 需 `enable_mkldnn=False` |
| 表格处理 | Docling 表格识别 → Markdown/JSON 保真 | — | 表格整块保留，不做单元格级拆分 |

### 3.3 存储层

| 组件 | 首选 | 备选 | 理由 |
|---|---|---|---|
| 向量库 | **Milvus**（Docker 单机起，后可集群） | Qdrant | 百万级 chunk、filter 下推性能好、企业级运维成熟；Docker 单机即可跑 P0 |
| 关系库 | PostgreSQL 16 | SQLite（仅 P0 开发） | 文档元数据、用户、角色（RBAC 三角色）、会话、审计日志、AdminDepartment（部门管理员负责部门） |
| 缓存 | Redis | — | 摄入任务队列（Stream）、登录限速计数、会话状态 |
| 对象存储 | MinIO | 本地磁盘 | 原始文档归档、可跳转原文（已部署，应用接入待做） |
| 稀疏检索 | Milvus 内置 BM25 | Elasticsearch | P0 已落地：BM25 function + 中文分词（jieba analyzer），稠密+稀疏+RRF 单库一站式 |

### 3.4 模型层（模型网关统一抽象）

| 组件 | 首选 | 备选 | 说明 |
|---|---|---|---|
| LLM | 云 API：DeepSeek / Qwen；本地：Qwen2.5-32B 系（vLLM/Ollama） | GPT/Claude | **P0 实际：OpenAI 兼容代理端点（moyuu.cc/v1，deepseek-v4-flash）**；网关抽象已就绪，切换只改 .env |
| Embedding | **BGE-M3**（中文强、支持 8192 token、可产出稀疏向量） | text-embedding-3 / bge-large-zh | **P0 实际：SiliconFlow API（BAAI/bge-m3，1024 维）**；本地部署位预留 |
| Reranker | **bge-reranker-v2-m3**（XLM-RoBERTa 系） | Cohere Rerank / Qwen rerank | **P0 实际：SiliconFlow API（BAAI/bge-reranker-v2-m3）**；未配置 key 时降级不重排 |

### 3.5 Agent 与检索编排

| 组件 | 首选 | 备选 | 理由 |
|---|---|---|---|
| Agent 框架 | **LangGraph** | LlamaIndex / 自研状态机 | **P1 已落地**：`app/agent/`（state/nodes/graph），意图路由 + CRAG 反思；SSE 契约保持兼容（新增 intent 事件） |
| 检索原语 | LlamaIndex 检索器（作为库引入） | 自研 | **P0 实际：自研（app/retrieval/，Milvus 稠密+BM25+RRF+Rerank 接口抽象）**，避免引入重型框架 |
| Query 改写/多查询 | LLM prompt 实现（自研轻量） | — | 无需框架，一个 prompt + 输出校验即可（P1：CRAG 重写已落地，多查询待做） |
| CRAG 反思 | LangGraph 条件边 + 检索得分阈值 | — | **已落地**：低置信（top1 < crag_min_score）→ 重写重检索，最多 crag_max_rewrites 轮后降级 |

### 3.6 评估与可观测

| 组件 | 首选 | 备选 | 说明 |
|---|---|---|---|
| 评估 | **RAGAS**（faithfulness / answer relevance / context precision）+ 检索端 recall@k、MRR | TruLens / 自研 judge | **已接入**：`app/eval/generation_eval.py`（黄金集走生产链路 → RAGAS 三指标，LLM judge 复用 LLM 网关）；基线见下 |
| Trace | **Langfuse**（Docker 自托管） | LangSmith（SaaS） | **已接入**：`app/observability/tracing.py`，问答链路 trace（qa→retrieval→llm generation 三级 span）；未配置 key 自动降级 noop |
| 日志 | structlog + Loki | — | 结构化日志（P1） |
| 指标 | Prometheus + Grafana | — | API 延迟分位、任务失败率、Token 消耗（P1） |

**评估基线（2026-08-16 二次更新，黄金集 30 条：27 问答 + 3 拒答，judge=deepseek-v4-flash）**：

| 指标 | 基线 | 说明 |
|---|---|---|
| recall@5 / MRR | 0.852 / 0.796 | 检索端 |
| faithfulness | 0.931 | 生成端忠实度 |
| answer_relevancy | 0.681 | 提示词聚焦优化后 +0.045（0.636→0.681） |
| context_precision | 0.918 | 优化后 +0.13 |
| chat_refusal | 1.0 | 闲聊拒答 3/3 |

> 注意：RAGAS judge 单次评估方差约 ±0.1（同回答两次评估可差 0.08~0.3），对比指标时建议多次取均值。
> 回归门禁：`uv run python -m app.eval.regression`（对比 docs/eval_baseline.json，容差 0.05，劣化退出码 1）；快速版 `--no-ragas` 或 `--limit N`。

### 3.7 安全与测试

| 组件 | 首选 | 说明 |
|---|---|---|
| 认证 | JWT + 自研用户表（P0）；OIDC 对接企业 SSO（P2） | PRD FR-32 |
| 鉴权 | RBAC；权限标签随 chunk 入库，filter 下推 | PRD FR-33 |
| 测试 | pytest + pytest-asyncio；评估脚本独立入口 | 单元/集成/评估三层 |
| 接口测试 | httpx + TestClient | API 契约测试 |

## 4. 数据模型

### 4.1 Milvus Collection：`chunks`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int64 (PK) | chunk 全局 ID（crc32 确定性生成，幂等） |
| vector | float vector[1024] | BGE-M3 稠密向量（1024 维） |
| sparse_vector | sparse float | **BM25 function 自动生成**（写入时基于 content 计算，不可手工写入/读取） |
| doc_id | varchar | 所属文档 ID（建索引） |
| chunk_index | int32 | 文档内块序号 |
| page | int32 | 页码 |
| section_path | varchar | 章节路径（如 "3.2 部署要求 > 3.2.1"） |
| tenant/department | varchar | 权限标签（**参与 filter**） |
| content | varchar | 块文本（enable_analyzer + 中文分词，BM25 前置条件） |
| updated_at | int64 | 更新时间（filter 支持时间范围） |

### 4.2 PostgreSQL 核心表

```
documents(id, title, source_type, file_path, size, status[uploading|parsing|indexed|failed|disabled],
          version, department, tags, uploaded_by, created_at, updated_at, content_hash)
users(id, username, password_hash, role, department, is_active)
sessions(id, user_id, title, created_at, updated_at)
messages(id, session_id, role, content, refs_json, feedback, created_at)
audit_logs(id, user_id, action, resource, detail, ip, created_at)
```

## 5. 项目目录结构

```
rag-enterprise/
├── pyproject.toml              # uv 管理，锁定 Python 3.12
├── docker-compose.yml          # milvus + postgres + redis + minio + langfuse
├── .env.example
├── app/
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # pydantic-settings 配置中心
│   ├── api/                    # 路由：问答、文档、会话、管理
│   │   ├── deps.py             # 认证/RBAC 依赖
│   │   └── schemas.py
│   ├── ingestion/              # 摄入管道
│   │   ├── parser.py           # Docling/PyMuPDF 解析
│   │   ├── chunker.py          # 结构感知分块
│   │   ├── embedder.py         # 嵌入
│   │   └── worker.py           # 队列消费者
│   ├── retrieval/              # 检索层（接口抽象）
│   │   ├── base.py             # VectorStore 接口
│   │   ├── milvus_store.py
│   │   ├── hybrid.py           # 混合检索 + RRF
│   │   ├── reranker.py
│   │   └── query_rewrite.py
│   ├── agent/                  # LangGraph 编排
│   │   ├── graph.py            # 图定义（路由/检索/CRAG/生成）
│   │   ├── nodes.py
│   │   └── state.py
│   ├── llm/                    # 模型网关
│   │   ├── gateway.py          # OpenAI 兼容抽象
│   │   └── prompts.py
│   ├── eval/                   # 评估
│   │   ├── golden_set.py       # 黄金数据集
│   │   ├── run_eval.py         # 评估入口
│   │   └── metrics.py
│   └── observability/          # Langfuse / 日志集成
├── web/                        # Vue3 管理台（P1）
└── tests/
```

## 6. 部署架构（P0，Docker Compose 单机）

| 服务 | 镜像 | 资源（建议） | 说明 |
|---|---|---|---|
| api | 自建 (uvicorn) | 2C / 4G | 问答 + 管理 API，可水平扩展 |
| worker | 自建 (同镜像) | 2C / 4G | 摄入任务消费者 |
| milvus-standalone | milvusdb/milvus | 4C / 8G | 向量库（含 etcd、minio 依赖容器） |
| postgres | postgres:16 | 1C / 1G | 元数据 |
| redis | redis:7 | 0.5C / 512M | 缓存 + 任务队列 |
| minio | minio/minio | 1C / 1G | 原始文档存储 |
| langfuse | langfuse/langfuse | 1C / 1G | 可观测性 |

> 单机总计约 11C / 19.5G（含系统），开发机内存 ≥ 16G 可跑 P0；Reranker/Embedding 初期可走云端 API 或 CPU 推理（速度下降），GPU 机器可选跑本地模型。

## 7. 本地开发环境

```bash
# 1. 创建项目（uv 锁定 Python 3.12）
uv init --python 3.12

# 2. 基础设施
docker compose up -d milvus postgres redis minio langfuse

# 3. 配置 .env（模型 API key、连接串）
cp .env.example .env

# 4. 启动 API（开发热重载）
uv run uvicorn app.main:app --reload

# 5. 启动摄入 worker
uv run python -m app.ingestion.worker

# 6. 跑评估
uv run python -m app.eval.run_eval
```

## 8. 关键技术决策记录（ADR 摘要）

| 决策 | 选择 | 理由 | 备注 |
|---|---|---|---|
| ADR-01 | Milvus 而非 ES/Qdrant | 稠密+稀疏+filter 一站式；企业级规模天花板高 | 接口层抽象，可换 |
| ADR-02 | BGE-M3 单一嵌入模型 | 中文强 + 稠密/稀疏双输出，避免维护两套嵌入 | P0 走 SiliconFlow API（稠密路）；稀疏路由 Milvus 内置 BM25 function 承担 |
| ADR-03 | LangGraph + LlamaIndex 库 | 控制流用图，检索原语复用现成 | **P0 修订：自研线性编排 + 检索接口抽象，P1 引入 LangGraph** |
| ADR-04 | RRF 融合而非权重加权 | 无调参、稳健，避免稠密稀疏得分不可比问题 | — |
| ADR-05 | 权限 filter 下推 | 安全要求，检索层强制执行 | PRD FR-33 |
| ADR-06 | Python 3.12 而非 3.14 | 生态兼容（milvus/langchain/docling 依赖树） | uv 管理 |

## 8.1 外部依赖降级矩阵（REAUDIT D-2 反向梳理）

| 依赖 | 故障表现 | 当前行为 | 恢复条件 |
|---|---|---|---|
| LLM（deepseek-v4-flash） | 超时 120s / 5xx / 返回非 JSON | JSON 解析失败兜底默认值（intent=qa 等）；流式异常经 SSE error 事件返回；`_complete_text` 失败返回空串（已记日志） | 上游恢复后重试；无自动重试（幂等场景可手工重试） |
| 嵌入（BGE-M3） | 5xx / 超时 | 摄入失败（异常冒泡 → worker 重投 ≤3 次 → documents=failed） | 上游恢复后重投/重传 |
| 重排（bge-reranker） | 失败/未配置 key | **自动降级 RRF 原始排序**（不阻断检索） | 无需处理 |
| OCR（PaddleOCR） | 依赖缺失/加载失败 | 懒加载失败仅影响扫描件；文本层 PDF 不受影响 | 安装依赖后重启 |
| Docling 解析 | 失败 | 降级 PyMuPDF（文本抽取） | 无需处理 |
| Milvus | 不可达 | 摄入/检索异常上抛（无显式超时，靠客户端默认） | 容器恢复后重试 |
| Redis | 不可达 | 登录限速/计数降级放行（记日志）；队列读写异常重试；上传入队失败 → 503 + 文件清理 | 容器恢复后自动 |
| Langfuse | 不可达/未配置 key | 降级 noop（零外部调用），不影响回答链路 | 无需处理 |

## 9. 成本估算（月度，仅供参考）

| 项目 | 云端 API 方案 | 本地部署方案 |
|---|---|---|
| LLM 推理 | DeepSeek/Qwen API 按量（约 ¥100~500/月，视用量） | 单卡 48G 显存机器（一次性 ~¥5 万+ 电费） |
| Embedding/Rerank | API 或本地 CPU 小模型 | 本地 CPU/GPU |
| 基础设施 | Docker 单机（开发机） | 服务器托管 |
| 可观测/评估 | Langfuse 自托管免费 | 同左 |

> 建议：P0~P1 用云 API 快速验证质量，量级上来后评估本地部署收益（待评审确认，PRD 开放问题 1）。

## 10. P0 实现顺序建议（进度：✅ 1-4 完成）

1. ✅ 项目骨架 + docker-compose + 配置中心（0.5 周）
2. ✅ 摄入管道：解析 → 分块 → 嵌入 → 写 Milvus（1.5 周）
3. ✅ 检索层：混合检索（稠密+BM25）+ RRF + Reranker，接口抽象 + 权限 filter 下推（1 周）
4. ✅ 问答 API：基础 LLM 问答 + 引用溯源 + SSE 流式（1 周）
5. ⏳ 认证与权限（JWT + RBAC，departments 改从 Token 取）
6. ⏳ Langfuse trace 接入 + 评估回归门禁（黄金集已建，基线 recall@5=0.833 / MRR=0.722）
7. ⏳ 收尾：上传 API/队列、文档、验收演示

---

*本文档为规划稿，随实现与技术选型验证持续修订。*
