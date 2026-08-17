# 企业级改进建议书

> **评估日期**：2026-08-17
> **评估对象**：D:\PRTSNote\RAG（企业级 RAG Agent 问答平台，P0~P2 功能全部落地）
> **文档定位**：面向"改进 agent"的实施依据。缺陷级问题（安全漏洞、可靠性 bug）见 [FIX_LIST.md](./FIX_LIST.md)，由修复 agent 先行处理；**本建议书只覆盖"不符合企业级标准"的工程化、架构与治理层面改进**，共 9 大主题域 30 项，附优先级路线图与验收标准。
> **协作约定**：项目用 uv 管理依赖；测试 `uv run pytest tests/ -q`（当前 160 用例）；lint `uv run ruff check .`；所有改进必须保持测试全绿，并尽可能补测试。

---

## 一、评估结论总览

**已达标（保持即可）**：

| 维度 | 现状 |
|---|---|
| 业务功能 | P0~P2 全部落地：Agent 编排、混合检索、RBAC、脱敏、审计、评估门禁、反馈回流 |
| 测试 | 160 用例全绿，覆盖单元/API/集成/安全回归/RBAC 矩阵，mock 策略分层清晰 |
| 安全实现 | JWT + 三角色 RBAC、防提权/防自锁、爆破锁定、PII 双环节脱敏、审计日志 |
| 可观测基础 | Langfuse 全链路 trace、Prometheus/Grafana 面板 |
| 文档 | PRD/计划/手册/ADR 齐全，与实现一致，零 TODO/FIXME 残留 |
| 配置管理 | pydantic-settings 配置中心、.env 模板、prod 强密钥启动校验 |

**不符合企业级（本建议书覆盖）**：

| 维度 | 核心缺口 |
|---|---|
| 软件工程基础设施 | 无 CI/CD、无提交规范/评审流程、无 pre-commit |
| 数据治理 | 无 schema 迁移（create_all）、无备份/恢复策略、上传文件落本地磁盘无归档 |
| 部署运维 | 应用未容器化（手动双终端）、无优雅停机、无环境分级、密钥无轮换机制 |
| 安全治理 | 生产开关未闭环（开放注册）、默认弱密码、无依赖漏洞扫描、无安全响应头 |
| 可观测性 | 指标高基数风险、structlog 声明未落地、无日志集中收集、无告警 |
| 代码质量 | 无类型检查、ruff 存量 35 错误、前端无 lint/测试 |
| 依赖治理 | 未声明直接依赖、死依赖、死 pin、无升级窗口机制 |
| 流程治理 | 无发布流程/版本策略、无 Runbook、无安全评审周期 |

---

## 二、改进建议（按主题域分组）

每条格式：**现状 → 建议 → 理由 → 实施要点 → 验收**。工作量：S（≤0.5 天）/ M（1~2 天）/ L（3 天以上）。

### 2.1 软件工程基础设施

**A1. 提交规范与分支策略**（M）
- 现状：git 刚初始化（master 分支），无提交规范、无分支策略、无评审流程。
- 建议：采用轻量工作流：`main`（受保护）+ `feature/<编号>` 分支 + PR/MR 评审；提交信息遵循 Conventional Commits（`feat:/fix:/docs:/refactor:` 前缀，关联 FR 编号）；`.gitignore` 中文乱码修正为 UTF-8。
- 理由：企业级的最低协作底线；可追溯的变更历史是回滚与审计的前提。
- 验收：仓库有规范文档（docs/CONTRIBUTING.md）；示例 PR 合入记录。

**A2. CI/CD 流水线**（L）
- 现状：无任何 CI 配置；回归门禁 `app.eval.regression` 标称"CI 接入点"但从未接入。
- 建议：新建 `.github/workflows/ci.yml`：`uv sync` → `ruff check`（0 错误门禁）→ `pytest tests/`（160 用例）→ 可选 job `app.eval.regression`（依赖真实 LLM，用 `workflow_dispatch` 手动触发，防止外部波动打断主干）；后续叠加 CD：镜像构建 + 推送（依赖 2.3 C1 容器化）。
- 理由：测试与评估门禁没有自动化执行点，等于没有门禁。
- 验收：push 触发 CI 全绿；评估 job 可手动触发且基线不劣化。

**A3. pre-commit 钩子**（S）
- 现状：无本地质量闸门。
- 建议：`pre-commit` 配置 ruff（check+format）、`uv lock --check`；可选加入 mypy（2.6 F1 落地后）。
- 理由：把 CI 反馈提前到提交前，降低返工。
- 验收：违反 lint 的提交被拦截。

### 2.2 数据与存储治理

**B1. 数据库迁移机制（Alembic）**（M）
- 现状：`app/db.py` `init_db()` 用 `create_all` 建表（db.py:3 自注"升级为 Alembic（P1）"但未做）；schema 无法演进。
- 建议：引入 Alembic，`env.py` 指向 `app.db.Base.metadata` 与配置 DSN；生成初始迁移（用户/文档/会话/消息/审计/admin_departments/ingested_files 七表）；`init_db` 改为幂等引导（有 alembic_version 则跳过，否则提示执行 `alembic upgrade head`）；发布流程固定"先迁移后发版"。
- 理由：生产 schema 变更（加列/改索引/数据回填）的唯一安全路径；create_all 无法处理已有数据的表结构变更。
- 验收：空库 `alembic upgrade head` 建全表；存量库 `stamp head` 后增量迁移可用；新增一列走一遍迁移流程成功。

**B2. 备份与恢复策略**（L）
- 现状：全部数据落 `./volumes/`（Postgres/Milvus/Redis/MinIO/ClickHouse），无任何备份机制；恢复无演练、无文档。
- 建议：制定并文档化备份策略：Postgres `pg_dump` 每日全量（保留 14 天）+ WAL 归档；Milvus 集合数据导出或卷快照；上传文件目录（data/uploads）同步到 MinIO/对象存储；ClickHouse（Langfuse 事件）按需备份；每季度做一次恢复演练并记录结果。
- 理由：企业数据资产的最低保障；无备份的系统在数据事故面前等于没有系统。
- 验收：docs/ 有备份手册；一次恢复演练成功（从备份重建库并验证问答链路）。

**B3. 原始文档对象存储化**（M）
- 现状：`minio` 声明在 pyproject 但 0 使用；上传文件落本地 `data/uploads/`（单机磁盘，无冗余、无生命周期）。
- 建议：将原始文档归档接入 MinIO（bucket 按部门/日期分层，对象键含 doc_id），本地仅作临时中转；或明确走企业已有对象存储。配套：删除流程同步删对象、对象元数据与 documents 表对齐、访问控制（管理端下载）。
- 理由：本地磁盘不是存储方案——无冗余、无灾备、无法横向扩展；且 MinIO 已在栈内，成本为零。
- 验收：上传→归档→删除全链路走对象存储；断网/重启后数据完整。

**B4. 数据生命周期与留存**（M）
- 现状：审计日志、会话消息无限增长；文档无版本保留策略；无数据留存合规说明。
- 建议：定义留存策略：审计日志保留 N 年（合规要求，当前建议 ≥1 年）、会话消息保留 M 月（如 6 个月，可配置）、文档版本保留最近 K 版；实现定时清理任务（worker 侧）；PRD 补充数据留存条款。
- 理由：无限增长的数据 = 存储成本 + 合规风险（PII 留存时长是监管关注点）。
- 验收：清理任务可配置运行；PRD 有留存条款；清理后检索与审计不受影响。

### 2.3 部署与运维

**C1. 应用容器化**（L）
- 现状：只有基础设施 11 容器；应用靠手动两个终端（uvicorn + worker），无镜像、无编排、无资源限制。
- 建议：多阶段 Dockerfile（api / worker 两个镜像或一镜像两命令）；compose 增加 `api`、`worker` 服务，`depends_on` 全部基础设施 healthy，环境变量从 .env 注入，挂载 config/ 与 data/；镜像 tag 与版本号绑定。**注意**：PaddleOCR/paddlepaddle 使镜像体积大（数 GB），评估拆分：api 镜像不含 OCR，worker 镜像含。
- 理由：部署可复现、环境一致、资源可控；手动双终端启动在生产环境不可接受。
- 验收：`docker compose up -d` 全链路起服；`/health` ok；上传→摄入→问答端到端通过。

**C2. 优雅停机与进程管理**（M）
- 现状：worker `while True` 无信号处理（FIX_LIST P1-5 修复中）；uvicorn 单进程手动运行。
- 建议：容器化后依赖 SIGTERM 优雅退出（worker 处理完当前消息再退出，uvicorn `--timeout-graceful-shutdown`）；API 按需 `--workers N`（注意 SSE 与连接池参数的配套调整）；compose 配置 `stop_grace_period`。
- 理由：部署发布/滚动升级时丢请求、丢任务不可接受。
- 验收：发布时存量 SSE 连接与摄入任务不中断（或明确降级提示）。

**C3. 环境分级与密钥管理**（L）
- 现状：单一 .env；LANGFUSE_NEXTAUTH_SECRET 等强密钥与普通配置混在 .env；无 dev/staging/prod 分级。
- 建议：环境分级 `APP_ENV=dev|staging|prod`（config 已支持，补完整校验矩阵）；密钥与配置分离——生产密钥放企业 secret 管理（或至少独立 .env.prod 且不入库、权限受控）；建立密钥轮换流程（JWT_SECRET / LANGFUSE_* / 模型 API key：轮换周期、影响面、双写过渡期）。
- 理由：密钥泄漏是企业安全事件的第一来源；无轮换机制=泄露后无法止损。
- 验收：三套环境配置模板；轮换流程文档化并演练过一次。

**C4. 资源与容量规划**（M）
- 现状：并发 50/50 压测通过（P1 记录），但无资源规划文档、无数据增长预估、连接池参数硬编码。
- 建议：连接池/队列参数（SQLAlchemy pool、Redis、SSE 并发上限）提为配置项；按文档量×分块数×向量维度估算 Milvus/Postgres 容量增长；制定扩容路径（Milvus 从 standalone → 分布式的前提条件）；压测基准留存（当前 50 并发数据作为基线）。
- 理由：容量问题是生产事故的常见根因，且 Milvus standalone→集群是迁移型改动，需提前规划。
- 验收：docs/TECH_STACK.md 增补容量规划章节；参数可配置化。

### 2.4 安全与合规治理

**D1. 依赖与供应链漏洞扫描**（M）
- 现状：无任何依赖漏洞扫描；uv.lock 已锁定但从未审计。
- 建议：接入 `uv audit`（或 pip-audit/osv-scanner）进 CI（A2 流水线加一步）；高危漏洞阻断合并；建立"锁文件定期刷新 + 漏洞修复"节奏（季度）。
- 理由：供应链攻击是当前安全威胁主流路径；232 个锁定包无一次审计不可接受。
- 验收：CI 含扫描步骤；最近一次扫描报告无未处理高危。

**D2. 生产安全开关闭环**（S）
- 现状：`AUTH_DISABLE_SIGNUP: "false"`（compose 注释已写"生产环境应关闭"但无强制机制）；默认弱密码（minioadmin / ragpass / langfuse / grafana admin）散落各处。
- 建议：compose 用 `${AUTH_DISABLE_SIGNUP:?必须显式设置}` 强制生产显式配置（同 LANGFUSE_NEXTAUTH_SECRET 的写法）；默认密码轮换清单（一次性脚本或文档清单：MinIO/Grafana/Langfuse/Postgres）；弱默认值在生产检测（启动时对 APP_ENV=prod 校验默认密码组合并警告/拒绝）。
- 理由："注释里说生产要关"不等于关了；默认凭据是扫描器第一攻击目标。
- 验收：prod 启动无任何默认凭据；注册开关强制显式配置。

**D3. 传输安全与响应头**（S）
- 现状：无 TLS 层（直接 HTTP）；FastAPI 无安全响应头；CORS 策略无文档（当前同源托管，尚可）。
- 建议：生产经反向代理（Nginx/Traefik/企业网关）终止 TLS（当前架构同源托管，配置简单）；中间件加安全头（`X-Content-Type-Options: nosniff`、`X-Frame-Options`、CSP 评估）；CORS 未来前后端分离时的策略文档化。
- 理由：传输加密是合规基线；响应头是低成本高收益的加固。
- 验收：生产访问全 HTTPS；响应含安全头；文档有 CORS 策略说明。

**D4. 审计与合规留存**（M）
- 现状：审计日志功能完备（登录/上传/删除/越权/角色变更），但无导出、无留存周期（见 B4）、管理端审计查询无过滤分页（仅 limit）。
- 建议：审计查询加分页/按操作类型过滤；提供导出（CSV）供合规审计；与 B4 留存策略联动；明确审计日志的完整性保护（追加写、防篡改说明——至少文档化"DB 层只读权限"约束）。
- 理由：审计要能被合规部门"用起来"，而非仅存数据。
- 验收：管理端可按 action 过滤+分页；可导出；留存策略生效。

### 2.5 可观测性与可靠性治理

**E1. 指标治理与告警**（L）
- 现状：指标采集齐全（请求量/延迟/检索命中/Token），但 label 用原始 path（高基数风险，FIX_LIST P2-14 修复中）；无告警规则、无 Alertmanager；无业务级指标（摄入失败率、队列积压、检索空结果率）。
- 建议：统一指标规范（label 用路由模板；补业务指标：队列深度、摄入失败率、CRAG 重写率、空检索率、P95 延迟）；配置 Prometheus 告警规则 + Alertmanager（Webhook/邮件）：服务存活、5xx 率、队列积压、错误率阈值；面板补 SLO 视图。
- 理由：可观测性的终点是告警——没有告警的面板只是事后考古。
- 验收：关键故障可触发告警；面板含业务指标；SLO 视图可用。

**E2. 日志治理（结构化 + 集中收集）**（L）
- 现状：structlog 声明在 pyproject 但 0 使用，28 个模块全用标准库 logging（格式不统一）；无集中收集（日志散在终端/容器 stdout）；`_complete_text` 等静默吞异常（FIX_LIST P1-6 修复中）。
- 建议：落地 structlog（或统一 JSON 日志格式）：request_id 贯穿（SSE 请求与 trace 关联）、结构化字段（route/status/duration/error）；容器化后日志走 stdout，由企业日志平台或 Loki+Promtail 收集；日志分级治理（info/warning/error 语义明确）。
- 理由：排障速度直接取决于日志质量；非结构化日志在分布式排查中几乎无用。
- 验收：日志统一 JSON 且含 request_id；查询链路日志可在收集端检索。

**E3. 外部依赖可靠性制度**（M）
- 现状：LLM/嵌入/重排调用有超时与降级（重排失败降 RRF 等），但无重试、无熔断；Milvus 无显式超时；策略未文档化。
- 建议：文档化外部依赖降级矩阵（依赖→故障表现→降级行为→恢复条件，已有实现的反向梳理）；对高价值路径（LLM 生成）加一次重试（幂等安全时）；Milvus 调用显式超时；评估 Langfuse 故障对主链路的影响隔离（当前 trace 失败是否影响回答——确认并测试）。
- 理由：多依赖系统的可用性 = 最弱依赖的降级质量；策略写下来才能被测试和评审。
- 验收：降级矩阵入 TECH_STACK；模拟各依赖故障的测试用例存在。

### 2.6 代码质量与工程规范

**F1. 类型检查接入**（M）
- 现状：无 mypy/pyright；空值问题靠运行时 except 兜底（如 gateway LLM 返回 null 时的链式 AttributeError）。
- 建议：mypy（`strict = false` 起步，`check_untyped_defs = true`，逐步收紧）+ pyproject 配置 + CI 门禁；先清零现有错误，再逐步打开 strict 选项；TypedDict（agent state）与 Pydantic 模型的边界是重点。
- 理由：这是把"测试能过但线上炸"类问题前移到开发期的最高性价比手段。
- 验收：`mypy app/` 0 错误；CI 含 mypy。

**F2. lint/format 门禁**（S）
- 现状：ruff 存量 35 错误（多为 E501，另有 ASYNC240 真问题：async 内同步 pathlib IO）；无 format 规范。
- 建议：`ruff check --fix` 清存量（FIX_LIST P2-20）+ `ruff format` 统一风格；pyproject 加 format 配置；CI 强制 0 错误；ASYNC240 逐处评估（真 IO 用 to_thread，纯内存加 noqa 注明）。
- 理由：风格一致性是代码评审效率的前提；存量不清理则门禁无法开启。
- 验收：`ruff check .` 0 错误；`ruff format --check` 通过。

**F3. 前端质量门禁**（M）
- 现状：web-ui 只有 dev/build/preview，无 ESLint、无测试、无类型检查。
- 建议：ESLint + Prettier（或 Vite 生态等价物）；Vitest + Vue Test Utils 补核心视图冒烟测试（Login/Chat 流式渲染/Docs 上传）；CI 集成构建（`vite build` 失败即阻断）。
- 理由：前端是用户直接面对的系统；无测试的管理台回归全靠人肉。
- 验收：`npm run lint` 与 `npm run test` 通过；CI 构建前端产物。

**F4. 代码评审与架构治理**（S）
- 现状：无评审制度；ADR 有基础但无"新依赖引入/架构变更"流程。
- 建议：PR 检查单文档化（安全影响、日志、测试、迁移、文档同步五项必查）；新依赖引入需在 PR 说明理由（防死依赖重现）；模块依赖方向（agent → retrieval/llm，禁止反向）写入 CONTRIBUTING。
- 理由：治理成本极低，防架构腐化效果显著。
- 验收：CONTRIBUTING.md 含检查单与依赖方向说明。

### 2.7 测试与质量保障

**G1. 评估门禁自动化**（M）
- 现状：回归门禁存在（基线对比、劣化 exit 1）但靠手动跑；RAGAS judge 单次方差 ±0.1 已知。
- 建议：CI 手动触发 job 接入（A2）；评估结果自动归档（时间序列化，便于看趋势）；方差对策制度化：对比取多次均值（≥3 次）。
- 理由：评估是"质量不劣化"的唯一证据，必须自动化、可追溯。
- 验收：触发评估 job 产出带时间戳的报告；基线对比逻辑不变。

**G2. 测试基建加固**（M）
- 现状：FIX_LIST P2-15 已列（共享 TestClient、time.sleep、清理策略混用、_test.db 残留）；另有覆盖率无门禁。
- 建议：完成 P2-15 后叠加：覆盖率统计（`pytest --cov`，目标 ≥80%，核心模块 agent/security 更高）；分层测试约定（单元不触外部、API 层 mock 网关、集成单独目录）；失败用例隔离（fixture 恢复保证）。
- 理由：测试体系的质量决定它能否长期守护回归。
- 验收：覆盖率达标并进 CI；连续 3 次全量运行全绿。

**G3. 性能与容量基准**（M）
- 现状：P1 做过 50 并发压测（关键修复已落地），但无基准脚本、无回归检测。
- 建议：压测脚本化（locust 或脚本即可）：并发问答（SSE）、上传摄入、混合负载三场景；基准数据入库（当前 50/50 全过为基线）；季度复测；P95 延迟与失败率阈值。
- 理由：性能回退是渐进式的，没有基准就无法发现。
- 验收：压测脚本在 scripts/ 可重复执行；基线与阈值文档化。

### 2.8 依赖与供应链治理

**H1. 依赖声明完整性**（S）
- 现状：langgraph、langchain_openai 直接 import 未声明（靠 ragas 传递依赖）；structlog/minio/langchain-community 死依赖（FIX_LIST P1-9/P2-12 修复中）。
- 建议：修复后建立机制：CI 增加 `import 检查`（直接依赖必须声明，可用 `pip-audit`/自定义脚本或用 `uvx deptry` 检测未声明/未使用）；新增 import 走 F4 评审流程。
- 理由：未声明依赖 = 升级定时炸弹（ragas 一升级核心编排可能直接炸）。
- 验收：deptry 检查 0 问题。

**H2. 升级策略与兼容窗口**（M）
- 现状：websockets==15.0.1 死 pin、langchain-community<0.4 卡死（FIX_LIST P2-13 修复中）；无升级节奏。
- 建议：季度依赖升级窗口（uv lock 刷新 + 全量测试 + 评估回归）；高风险依赖（ragas/langgraph/pymilvus）单独排期并记录兼容性结论到 TECH_STACK；锁文件变更必须随 PR 走。
- 理由：依赖不升级 = 漏洞不修复 + 生态掉队；升级不测试 = 事故。
- 验收：最近一个升级窗口有记录；TECH_STACK 有兼容性结论表。

### 2.9 流程与治理

**I1. 版本与发布流程**（M）
- 现状：pyproject version 0.1.0；无版本策略、无 CHANGELOG、无发布检查单。
- 建议：语义化版本（feature/minor、breaking/major、fix/patch 已由 A1 提交规范支撑）；CHANGELOG.md 维护；发布检查单：迁移执行 → 密钥核对 → 默认凭据轮换 → 评估回归 → 回滚预案（镜像 tag 即回滚点，依赖 C1）。
- 理由：可发布性 = 可回滚性 + 可追溯性，缺一即发布恐惧。
- 验收：一次完整发布流程走通并有记录。

**I2. 运维 Runbook**（M）
- 现状：MANUAL 有部署与 FAQ，但无故障处理手册。
- 建议：Runbook 覆盖已知故障模式（继承 memory 中的踩坑记录）：worker 多进程残留（旧 worker 抢消费）、uvicorn websockets 锁、Milvus/ClickHouse 挂起、Redis 假死、Langfuse 事件不落库（worker 镜像缺失）；每个模式：症状 → 诊断命令 → 处置 → 预防。
- 理由：项目已踩过的坑若不文档化，下一任运维会重踩。
- 验收：docs/RUNBOOK.md 覆盖 ≥6 个故障模式。

**I3. 安全评审周期**（S）
- 现状：做过一次全量安全审计（结果已修复），但无周期性机制。
- 建议：季度安全审计（对照 D 域清单）；每次重大功能变更附带安全影响自评（PR 检查单已有，I2 联动）；威胁模型文档化（当前：注册开放、管理员界面、文件上传、提示注入面）。
- 理由：安全是持续过程；单次审计的成果会随功能演进衰减。
- 验收：审计节奏入 CONTRIBUTING；最近一次审计记录在案。

---

## 三、优先级路线图

| 阶段 | 周期 | 内容 | 目标 |
|---|---|---|---|
| **Phase 1：工程化补齐** | 2~4 周 | A1、A2、A3、F1、F2、B1、C1、C2 | 版本控制/CI/迁移/容器化四大地基落地 |
| **Phase 2：运维与安全加固** | 1~2 月 | B2、B3、B4、C3、D1、D2、D3、E1、E2、G2、G3 | 可备份可恢复、密钥可轮换、漏洞可扫描、日志可检索 |
| **Phase 3：治理制度化** | 持续 | D4、E3、F3、F4、G1、H1、H2、I1、I2、I3 | 流程与制度形成闭环，防止退化 |

**依赖关系**：A2（CI）依赖 A1（git 规范）；C1（容器化）依赖 B1 迁移逻辑明确；E2（日志收集）依赖 C1（stdout 约定）；I1（发布流程）依赖 C1（镜像回滚点）；G1 依赖 A2。**建议 Phase 1 内 A/B/C/F 各组各派一个 agent 并行，Phase 2 起按组领取。**

## 四、验收标准（改进是否"达标"的可度量清单）

1. `git` 仓库含规范提交历史，`CONTRIBUTING.md` 有评审检查单；
2. CI 流水线：ruff 0 错误 + mypy 0 错误 + 160+ 测试全绿 + `vite build` 通过；
3. `alembic upgrade head` 可从空库建全 schema，存量库可演进；
4. `docker compose up -d` 全链路起服（api + worker 容器化），`/health` ok，端到端问答通过；
5. 备份手册 + 一次成功恢复演练记录；
6. `uv audit` 无未处理高危漏洞；prod 启动无默认凭据、注册开关显式配置；
7. 日志统一结构化且可集中检索；关键指标可触发告警；
8. 覆盖率 ≥80% 进 CI；压测基准与阈值文档化；
9. CHANGELOG 与版本策略生效，一次完整发布流程有记录；
10. RUNBOOK 覆盖 ≥6 个已知故障模式。

## 五、风险与注意事项

1. **镜像体积**：PaddleOCR/paddlepaddle 数 GB 级，容器化时按 api/worker 拆分镜像，否则构建与分发成本失控（C1 已标注）。
2. **Windows 开发 / Linux 生产差异**：本地是 Windows（uvicorn/信号处理/路径习惯），容器化后以 Linux 为准，注意 worker 信号处理（Windows 无 SIGTERM，FIX_LIST P1-5 已提示）与 `data/` 路径映射。
3. **外部模型依赖**：LLM/嵌入/重排是第三方服务，评估门禁依赖真实 LLM——CI 中评估 job 必须手动触发，避免外部波动阻断主干（A2）。
4. **RAGAS 评估方差**：judge 单次方差 ±0.1，任何"指标劣化"结论需多次取均值，防止误报（G1）。
5. **Langfuse 是第三方 PII 出口**：完整 question/messages 会上传 Langfuse，若企业有数据出境合规要求，需评估自托管隔离或脱敏后上传（D 域联动）。
6. **治理不落地 = 文档垃圾**：每条建议都带验收标准，实施时按"可度量"推进，不接受"已写文档"式交付。

---

*建议书完。实施顺序建议：Phase 1 优先完成 A2（CI）与 B1（Alembic），它们是后续所有质量门的执行载体。*
