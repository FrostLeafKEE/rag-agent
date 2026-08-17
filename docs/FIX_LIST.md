# 企业级修复清单（2026-08-17 评估产出）

> 用途：供修复 agent 按条目领取执行。每条自包含（位置 / 现状 / 修复建议 / 验证方式）。
> 优先级：P0 = 上线前必须；P1 = 尽快；P2 = 建议。
> 项目背景：FastAPI + LangGraph + Milvus + PostgreSQL + Redis 的企业级 RAG 问答平台。
> 约定：项目用 uv 管理依赖；测试 `uv run pytest tests/ -q`（当前 160 全绿）；lint `uv run ruff check .`（当前 35 错误）；所有修复必须保持测试全绿并补对应测试。

---

## P0-1 RBAC 部门过滤注入（安全，阻断性）

- **位置**：`app/retrieval/base.py:36-47`（`build_filter`）；`app/api/routes/auth.py:92`（`RegisterRequest.department` 定义）
- **现状**：`build_filter` 用 `f'"{d}"'` 把部门名直接拼进 Milvus filter 字符串，不转义；注册接口的 `department` 字段只有 `min_length=1, max_length=64`，**无字符集校验**。自注册用户（注册开放）填 `a"] or doc_id != "x" or department in ["a` 作为部门，生成的 filter 为永真表达式 `department in ["a"] or doc_id != "x" or department in ["a"]`，可绕过部门隔离检索全库文档。
- **修复建议**：给部门名加字符白名单，与 `app/ingestion/indexer.py:25` 的 `SAFE_DOC_ID`（`^[\w\-]{1,64}$`）同思路——在 `RegisterRequest.department` 和 `build_filter` 双端校验（或至少注册入口处加 `pattern` 正则 + `build_filter` 内对 `"` 等特殊字符拒绝/转义）。注意 `build_filter` 是唯一安全边界，不能只靠注册入口（部门还可能来自其他写入路径，如 `AdminDepartment` 表）。
- **验证**：在 `tests/test_security_regression.py` 新增用例：注册 department 含 `"] or ...` 的恶意值 → 断言被拒（422）或检索结果为空（filter 被转义）；另加单元测试直接调 `build_filter(["a\"] or doc_id != \"x\" or department in [\"a"])` 断言结果不含永真结构。`uv run pytest tests/ -q` 全绿。

## P0-2 工具 SQL 表白名单绕过（安全，阻断性）

- **位置**：`app/agent/tools.py:35-39`（`_safe_select`）
- **现状**：白名单用正则 `\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)` 只匹配 `FROM|JOIN` 后紧跟的单个表名。`SELECT * FROM documents, users` 中逗号分隔的 `users` 不会被检测（已实测复现：检测到 `{'documents'}`、白名单通过）。LLM 受提示注入影响时可取走 `users.password_hash`。
- **修复建议**：推荐引入 `sqlglot` 做词法/语法解析提取全部表名（`sqlglot.parse_one(sql).find_all(sqlglot.exp.Table)`），或对正则补充：逗号分隔的 FROM 列表逐个校验、`FROM ... JOIN ...` 链全部校验。保留现有拒绝逻辑（`;` 多语句、非 SELECT、未知表）。
- **验证**：`tests/test_tools.py` 新增用例：`SELECT * FROM documents, users` 必须被拒；`SELECT * FROM documents` 必须通过；`SELECT * FROM users JOIN documents` 必须被拒；带注释/换行的变体也覆盖。`uv run pytest tests/ -q` 全绿。

## P0-3 初始化版本控制（git init + 修正 .gitignore）

- **位置**：项目根目录（当前**不是 git 仓库**）
- **现状**：无 `.git`，无法回滚/评审/协作；`.gitignore` 内容正确（.env、volumes/、data/、.venv 等已忽略）但文件本身是 GBK 编码，中文注释乱码（如"鐜 澧冧笌瀵嗛挜"）。
- **修复建议**：`git init`；将 `.gitignore` 转为 UTF-8 编码并重写中文注释（内容项保持不变）；`.env`、`volumes/`、`data/`、`.venv/`、`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`、`web-ui/node_modules/`、`web-ui/dist/`（可保留，二选一，注意 main.py 挂载 dist 的路径约定）、`tests/_test.db` 必须被忽略；首次提交前 `git status` 核对无敏感文件（.env 含真实 API key）。
- **验证**：`git status` 只显示应跟踪的文件；`git log` 有首个提交。
- **注意**：remote 地址需用户提供，不要擅自配置远程。

## P0-4 worker 消息丢失 + 任务状态撒谎（可靠性）

- **位置**：`app/ingestion/queue.py:75-95`（消费与重投逻辑）；`app/ingestion/worker.py`（主循环）；`app/models.py:59-97`（Document.status）
- **现状**：① `XREADGROUP ... >` 只读新消息，从不处理 Pending Entries List——worker 在读到消息后、XACK 前崩溃，消息永久滞留 PEL，重启后也不重投（静默丢任务）；② 重试 >3 次后仅 XACK 丢弃（`queue.py:90-95`），不落库；③ `process_ingestion` 先把 status 置 `"processing"`，`ingest_document` 失败后异常冒泡，`documents.status` 永久停在 `"processing"`——`Document` 模型定义 `uploading|indexed|failed|disabled`，但 `"failed"` 全代码库无一处写入，`doc.error` 从不填充，任务状态对用户撒谎。
- **修复建议**：
  1. 消费循环加 PEL 恢复：启动时（或每次轮询）用 `XPENDING`/`XCLAIM`（`idle > 阈值`，如 60s）认领滞留消息重投；或至少对 PEL 中的旧消息重新入队。
  2. 重试耗尽时：写 `documents.status="failed"` + `doc.error`（异常消息截断 ≤512 字符），再 XACK。
  3. `ingest_document` 失败路径统一 try/except，把状态落库后再抛/记录，保证 `"processing"` 不会永久悬挂。
- **验证**：新增测试：模拟 ingest 抛异常 → 断言 documents.status=="failed" 且 error 非空（`tests/test_documents.py` 或新增 `tests/test_queue_recovery.py`）；PEL 恢复逻辑用 fakeredis/mock XREADGROUP 返回 PEL 消息断言被认领。`uv run pytest tests/ -q` 全绿。

## P1-5 worker/connector 优雅退出

- **位置**：`app/ingestion/worker.py:73`（`while True`）；`app/ingestion/connector.py:155`（常驻循环）
- **现状**：无信号处理，SIGTERM 直接杀进程，可能在 XACK 前中断（配合 P0-4 会丢任务）。
- **修复建议**：注册 `signal.signal(SIGTERM/SIGINT, ...)` 或 `asyncio` 事件循环的信号处理，设置退出标志，当前消息处理完后再 break；connector 同理。Windows 下注意 `signal.SIGTERM` 不可用（win32 只有 SIGINT/SIGBREAK），用 `signal.SIGBREAK` 或兼容写法。
- **验证**：手动验证：起 worker → 发送消息 → 杀进程（`taskkill`）→ 断言已读未 ACK 消息留在 PEL 且重启后恢复（配合 P0-4 测试）。

## P1-6 静默吞异常修复（可观测性）

- **位置**：`app/agent/nodes.py:121-128`（`_complete_text` 的 `except Exception: return ""`）；`app/api/routes/auth.py:85-86`（`clear_login_failures` 的 `except Exception: pass`）
- **现状**：LLM 故障被伪装成"改写失败"、Redis 故障被无声吞掉，故障不可观测。
- **修复建议**：`_complete_text` 的 except 加 `logger.warning("...", exc_info=True)`（或至少记录异常类型）；`clear_login_failures` 同理加 `logger.warning`。不要改变现有降级行为（返回空串/跳过清理）。
- **验证**：无行为变化，测试全绿即可；可用 caplog 断言日志被写出（可选）。

## P1-7 async 路由内同步写盘 + 孤儿文件

- **位置**：`app/api/routes/documents.py:96-140`（上传处理）
- **现状**：① `await file.read()` 把最大 50MB 文件整体读入内存（并发峰值）；② `stored_path.write_bytes(content)` 在 async 路由内同步写盘，阻塞事件循环（同文件 `_ensure_upload_dir` 反而正确用了 `asyncio.to_thread`，风格不一致）；③ 流程"文件落盘+DB 提交"先于 `enqueue`——Redis 故障时返回 500 但留下孤儿文件与永久 `"uploading"` 的 DB 行。
- **修复建议**：① 写盘改 `await asyncio.to_thread(...)`（或分块读流式写）；② 调整顺序：先入队（或先写 DB 行标记 pending）再落盘，失败时清理文件+回滚 DB 行；至少保证 enqueue 失败时删除已写文件并把 DB 行置 `failed` 或删除。
- **验证**：测试：mock `enqueue` 抛异常 → 断言 DB 无残留行、无孤儿文件；上传成功路径不受影响。`uv run pytest tests/ -q` 全绿。

## P1-8 Redis 连接超时统一

- **位置**：`app/api/routes/auth.py:39`（`_redis()`）；对比 `app/ingestion/queue.py:23-31`
- **现状**：`auth.py` 的 `_redis()` 用 `socket_timeout=None` 且无 connect timeout——Redis 假死时登录/爆破检查无限挂起；`queue.py` 反而正确设置了 `socket_connect_timeout=5`，两处不一致。
- **修复建议**：统一为 `redis.from_url(url, socket_connect_timeout=5, socket_timeout=5, decode_responses=...)`；同时考虑用模块级连接池（现在每次调用新建连接，高并发开销大）。
- **验证**：测试全绿；可 mock Redis 不可达验证快速失败（可选）。

## P1-9 未声明直接依赖移入 pyproject

- **位置**：`pyproject.toml` `[project].dependencies`
- **现状**：`langgraph`（`app/agent/graph.py:8`、`app/agent/service.py:12`）、`langchain_openai`（`app/eval/generation_eval.py:20-21`）直接 import 但未声明，靠 ragas→langchain 传递依赖存活——ragas 升级可能直接炸掉核心编排。
- **修复建议**：`uv add langgraph langchain-openai`（版本取当前锁定的兼容版本），然后 `uv lock` 更新锁文件，确认 `uv run python -c "import langgraph, langchain_openai"` 通过。
- **验证**：`uv run pytest tests/ -q` 全绿；`uv lock --check` 通过。

## P1-10 connector doc_id 超长导致摄入失败

- **位置**：`app/ingestion/connector.py:58`（`_doc_id_for`）；`app/ingestion/indexer.py:25`（`SAFE_DOC_ID` 上限 64）
- **现状**：`_doc_id_for` 生成的 doc_id 最长 128 字符，而 `SAFE_DOC_ID` 白名单上限 64——深层目录+长文件名的文件必然在 `indexer.upsert` 被拒，摄入失败且错误难定位。
- **修复建议**：`_doc_id_for` 输出截断到 ≤64 且保证唯一（如 `sha1(路径)[:32]` 或 前缀+哈希）；或放宽 `SAFE_DOC_ID` 上限（需同步检查 Milvus 侧与 API 校验）。推荐前者。
- **验证**：新增单元测试：构造超长路径 → 断言生成的 doc_id 匹配 `SAFE_DOC_ID` 且不冲突。

## P1-11 上传文件魔数校验

- **位置**：`app/api/routes/documents.py:85-98`（上传校验）
- **现状**：仅扩展名白名单，无魔数（magic bytes）校验、无病毒扫描（后者可留作运维项，若企业有 AV 网关）。
- **修复建议**：读文件头若干字节（如 512B）用 `magic`/`python-magic` 或手写常见格式魔数表（PDF `%PDF`、DOCX/PPTX 的 zip `PK`、PNG/JPEG 等）与扩展名交叉校验；不匹配返回 422。注意 zip 魔数下 DOCX/PPTX 需要进一步区分（可查 `[Content_Types].xml`）。
- **验证**：新增测试：改名成 .pdf 的文本文件上传被拒；真实 PDF/DOCX 通过。

## P2-12 清理死依赖

- **位置**：`pyproject.toml` `[project].dependencies`
- **现状**：三个声明但 0 import 的依赖：`langchain-community`（全项目无 import）、`minio`（MinIO 从未被应用代码使用，上传落本地 `data/uploads/`）、`structlog`（声明了但全项目 0 引用，实际用标准库 logging）。
- **修复建议**：`uv remove langchain-community minio structlog` 后 `uv lock`；**注意**：移除前先确认 `langchain-community` 没有被 ragas 间接需求（pyproject 里 ragas 是直接依赖，若 ragas 需要 community 会留在锁文件里，不用管）。若未来要真正落地 structlog 统一日志（当前 28 个模块全用标准库 logging，可作为独立改进项），则不删 structlog。
- **验证**：`uv run pytest tests/ -q` 全绿；`uv run ruff check .` 无新增错误。

## P2-13 解除死 pin

- **位置**：`pyproject.toml:27`（`websockets==15.0.1`）、`pyproject.toml:11`（`langchain-community<0.4`，若上一条未删则处理）
- **现状**：`websockets==15.0.1` 精确锁死无理由（uv.lock 已提供可复现锁定）；`langchain-community<0.4` 上限卡死（langchain 已 1.x）。
- **修复建议**：websockets 改 `>=15`（或直接删约束靠 lock 锁定，注意 uv 会按 lock 解析）；langchain-community 若保留则放宽为 `>=0.3` 或按实际需求。修改后 `uv lock` + 全量测试。
- **验证**：`uv run pytest tests/ -q` 全绿；`uv lock --check` 通过。

## P2-14 Prometheus 指标高基数

- **位置**：`app/observability/metrics.py:35`（指标 label 用 `request.url.path`）
- **现状**：动态路径（`/api/v1/sessions/123/messages`、`/api/v1/documents/xxx`）直接进 label，高基数会撑爆 Prometheus 存储。
- **修复建议**：改用路由模板（FastAPI 的 `request.scope["route"].path`，如 `/api/v1/sessions/{session_id}/messages`）；无路由的请求（404）归并到 `"unknown"`。
- **验证**：手动请求两个不同 id 的同一路由 → 指标 label 相同（模板路径）；`uv run pytest tests/ -q` 全绿。

## P2-15 测试基础设施改进

- **位置**：`tests/` 全目录
- **现状**：① `test_tools.py:38` 用 `DELETE FROM documents` 全表清空，与 `test_documents.py` 的 `like("itest%")` 清理策略混用，跨文件可能互相踩踏（当前靠执行顺序侥幸）；② `tests/_test.db` 残留文件；③ `time.sleep(0.2/0.3)` 十几处（test_documents/test_security_regression）时序脆弱；④ 模块级 `_client = TestClient(app)` 在 6 个文件共享同一 app 实例，`dependency_overrides` 异常中断会污染后续。
- **修复建议**：① 统一清理策略（统一前缀 `itest%` 或统一全表清空，二选一并抽成 fixture）；② `tests/_test.db` 加入 .gitignore（P0-3 已含）；③ sleep 换 `await asyncio.sleep` 配合轮询条件或 `pytest` 的 `wait_until` 风格事件等待；④ 至少为共享 app 实例的 override 加 try/finally 恢复。
- **验证**：`uv run pytest tests/ -q` 多次连续运行全绿（验证无状态残留）。

## P2-16 Alembic 数据库迁移

- **位置**：`app/db.py:32-38`（`init_db` 用 `create_all`）；`app/db.py:3` docstring 自注"升级为 Alembic 迁移（P1）"
- **现状**：schema 演进只能靠 `create_all`，已部署环境加列/改表无迁移路径。
- **修复建议**：`uv add alembic`；`alembic init` 生成迁移环境；配置 `alembic/env.py` 指向 `app.db.Base.metadata` 与 `postgres_dsn`（从 `app.config.get_settings()` 读）；用 `alembic revision --autogenerate` 生成初始迁移；`init_db` 改为"存在 alembic_version 表则跳过 create_all，否则提示跑迁移"或直接由启动脚本跑 `alembic upgrade head`。注意 async engine 需用 `async_engine_from_config` 或同步 URL 跑迁移（可临时用 `postgresql://` 同步 DSN）。
- **验证**：空库 `alembic upgrade head` 建出全部表；已有数据库（用当前 create_all 建的表）`alembic stamp head` 后增量迁移可用。

## P2-17 lifespan 关闭钩子

- **位置**：`app/main.py:22-25`（lifespan）
- **现状**：lifespan 只做 `init_db()`，无关闭钩子——不 dispose SQLAlchemy engine、不关 Redis/其他资源，优雅停机时连接泄漏。
- **修复建议**：`yield` 后加：`await _engine.dispose()`（从 `app.db` 暴露）、关闭 Redis 连接（若有全局客户端）、Langfuse client `flush()` 已有（tracing.py:128 在请求收尾做，可一并核对）。
- **验证**：起服务 → 关闭 → 日志无报错；测试全绿。

## P2-18 tracing 全局客户端并发初始化

- **位置**：`app/observability/tracing.py:28`（模块级 `_client` 惰性创建）
- **现状**：并发首请求可能重复创建 Langfuse 客户端（后果无害但冗余）；`app/ingestion/parser.py:79` `OcrEngine._instance` 同款问题。
- **修复建议**：`_client` 改 `asyncio.Lock` 包裹或模块级 `get_client()` 惰性初始化（简单加锁即可）；OCR 单例同理可接受现状（成本高不重复创建，加锁更稳）。
- **验证**：并发请求后无重复初始化日志；测试全绿。

## P2-19 日志中用户问题脱敏

- **位置**：`app/agent/nodes.py:117`（`_complete_json` 的 warning 日志含 `prompt[:40]`）
- **现状**：prompt 含用户问题原文（可能含 PII），截断 40 字符仍可能泄露敏感信息；Langfuse trace 会上传完整 question 与 messages（这是设计行为，需知悉第三方 PII 出口，不在本次修复范围）。
- **修复建议**：日志中 prompt 片段过 `app/security/redaction.py` 的脱敏函数（若可用）或改为只记 prompt 长度/节点名，不记内容。
- **验证**：测试全绿；手动触发含手机号的改写失败路径，日志无手机号明文。

## P2-20 ruff 存量 35 个错误

- **位置**：全仓（`uv run ruff check .`）
- **现状**：35 个错误，绝大多数 E501（行长 >100，含 `app/security/redaction.py` 的正则行长、`tests/` 多处）+ 少量 F401 未用导入、I001 导入排序、UP035/UP012 现代写法、ASYNC240（async 函数内同步 Path 方法，connector.py/queue.py 各几处——注意这条是真问题：`connector.py:89-97`、`queue.py:126` 在 async 里用 pathlib，涉及文件 IO 阻塞，若顺手修复需 `asyncio.to_thread` 包裹或标注豁免）。
- **修复建议**：`uv run ruff check . --fix` 自动修 7 处（F401/I001/UP012/UP035 等）；E501 手动折行或加 `# noqa: E501`（正则行建议折行或容忍）；ASYNC240 认真评估（阻塞 IO 需 to_thread，纯内存操作可 `# noqa: ASYNC240` 注释说明）。修复后 `uv run ruff check .` 0 错误。
- **验证**：`uv run ruff check .` 0 错误；`uv run pytest tests/ -q` 全绿。

## P2-21 CI/CD 流水线

- **位置**：新建 `.github/workflows/ci.yml`（项目当前无任何 CI 配置）
- **现状**：无版本控制（P0-3 先做）→ 无 CI。回归门禁 `python -m app.eval.regression` 是"CI 接入点"但从未接入。
- **修复建议**（git init 后）：`.github/workflows/ci.yml`：`uv sync` → `uv run ruff check .` → `uv run pytest tests/ -q` →（可选，需真实模型 key 的 job 单独标记，评估依赖外部 LLM，建议拆成手动触发 workflow_dispatch）`uv run python -m app.eval.regression`。Python 3.12、Windows/Ubuntu 均可（注意本项目依赖 paddlepaddle，CI 用 Ubuntu 或仅跑不需 GPU 的路径）。
- **验证**：push 后 CI 全绿。
- **注意**：依赖外部服务（Postgres/Milvus/LLM）的测试已 mock/skip，纯 CI 可跑通 160 测试（`test_indexer.py` 不可达自动跳过）。

## P2-22 应用容器化

- **位置**：新建 `Dockerfile`（app）、修改 `docker-compose.yml` 增加 api/worker 服务
- **现状**：只有基础设施 11 容器，应用靠手动两个终端（uvicorn + worker）启动，无镜像/无编排。
- **修复建议**：多阶段 Dockerfile（python:3.12-slim，装 paddleocr/paddlepaddle 体积大需注意镜像尺寸，可考虑文档标注或拆分：worker 镜像含 OCR，api 镜像可不含）；compose 增加 `api`（`uvicorn app.main:app`）与 `worker`（`python -m app.ingestion.worker`）两个服务，挂载 `config/`、`data/`、环境变量从 .env 注入，`depends_on` 基础设施全部 healthy；前端 dist 构建进镜像或挂载。**注意**：这是较大改动，建议单独一个 agent 专责，且验证本机 `docker compose up` 全链路。
- **验证**：`docker compose up -d` 后 `http://localhost:8000/health` 返回 ok；上传→摄入→问答全链路通。

---

## 领取建议（并行分组，避免文件冲突）

| 组 | 条目 | 冲突注意 |
|---|---|---|
| 安全组 | P0-1、P0-2、P1-11、P2-19 | 都动 tests/，先约定新增独立测试文件或分文件提交 |
| 摄入组 | P0-4、P1-5、P1-7、P1-10 | 都动 queue.py/worker.py/documents.py，建议同一 agent 顺序做 |
| 依赖组 | P1-9、P2-12、P2-13 | 都动 pyproject.toml，必须同一 agent 串行 |
| 观测组 | P1-6、P2-14、P2-17、P2-18 | 无冲突 |
| 工程组 | P0-3、P2-15、P2-16、P2-20 | 无冲突 |
| CI/部署组 | P2-21、P2-22 | 依赖 P0-3（git init）完成，放最后 |

**完成标准**：所有条目关闭 + `uv run pytest tests/ -q` 全绿 + `uv run ruff check .` 0 错误 + 回归门禁 `uv run python -m app.eval.regression` 不劣化。
