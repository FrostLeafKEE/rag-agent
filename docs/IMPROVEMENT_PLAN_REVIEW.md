# IMPROVEMENT_PLAN.md 核验报告

> **核验日期**：2026-08-17
> **对象**：[IMPROVEMENT_PLAN.md](./IMPROVEMENT_PLAN.md)（9 大主题域 30 项改进建议）
> **方式**：逐条对照当前代码/配置/状态核实（部分建议书写于 FIX_LIST 修复之前，"现状"已被修复改变）
> **结论**：27 项属实（其中 6 项"现状"已被 FIX_LIST 修复、建议仍需落实后续机制）；3 项存在描述偏差（含 1 项误报）。

---

## 逐条核验

### 2.1 软件工程基础设施

| 条目 | 核验 | 说明 |
|---|---|---|
| A1 提交规范与分支策略 | ✅ 属实 | git 刚 init（master）；无提交规范/分支策略/评审流程。**附注**：".gitignore 中文乱码"为误报——实测 .gitignore 为 UTF-8 编码，内容正常 |
| A2 CI/CD 流水线 | ✅ 属实 | 无任何 CI 配置；regression 门禁仅手动运行 |
| A3 pre-commit 钩子 | ✅ 属实 | 无本地质量闸门 |

### 2.2 数据与存储治理

| 条目 | 核验 | 说明 |
|---|---|---|
| B1 Alembic 迁移 | ✅ 属实 | `init_db()` 用 create_all（db.py docstring 自注"升级为 Alembic（P1）"未做） |
| B2 备份与恢复 | ✅ 属实 | 数据全落 `./volumes/`，无备份机制/恢复演练/文档 |
| B3 原始文档对象存储化 | ⚠️ 部分属实 | "minio 声明在 pyproject 但 0 使用"已过时——**minio 依赖已删除**（FIX_LIST P2-12）；但"上传落本地 data/uploads 无归档/无冗余"仍属实，建议方向（MinIO 归档）成立 |
| B4 数据生命周期与留存 | ✅ 属实 | 审计/会话无限增长；无留存策略与清理任务 |

### 2.3 部署与运维

| 条目 | 核验 | 说明 |
|---|---|---|
| C1 应用容器化 | ✅ 属实 | 仅基础设施 11 容器；应用手动双终端 |
| C2 优雅停机与进程管理 | ⚠️ 部分属实 | worker/connector 信号处理**已修复**（FIX_LIST P1-5，SIGINT/SIGBREAK/SIGTERM）；uvicorn 单进程手动运行仍属实；容器化后的 stop_grace_period 配置未做 |
| C3 环境分级与密钥管理 | ✅ 属实 | 单一 .env；密钥与普通配置混存；无轮换流程。APP_ENV 已有 prod 强校验（JWT_SECRET/debug），但无三套环境模板 |
| C4 资源与容量规划 | ✅ 属实 | 并发 50 压测有记录，但无容量规划文档、连接池参数硬编码（SQLAlchemy pool_size 等未配置化） |

### 2.4 安全与合规治理

| 条目 | 核验 | 说明 |
|---|---|---|
| D1 依赖漏洞扫描 | ✅ 属实 | 无 uv audit/pip-audit；uv.lock 从未审计 |
| D2 生产安全开关闭环 | ⚠️ 描述偏差 | **"compose 注释已写 AUTH_DISABLE_SIGNUP 生产应关闭"为误报**——全仓（compose/.env.example/app）均无 AUTH_DISABLE_SIGNUP 变量与注释。但问题实质成立：**注册接口开放且无关闭开关**，生产需补注册开关 + 默认凭据轮换清单（minioadmin/ragpass 等确实散落 compose） |
| D3 传输安全与响应头 | ✅ 属实 | 无 TLS（直接 HTTP）；无安全响应头；CORS 未文档化 |
| D4 审计与合规留存 | ✅ 属实 | 审计查询仅 limit 无过滤分页；无导出；无留存周期 |

### 2.5 可观测性与可靠性治理

| 条目 | 核验 | 说明 |
|---|---|---|
| E1 指标治理与告警 | ⚠️ 部分属实 | label 高基数**已修复**（FIX_LIST P2-14，改路由模板 + unknown 归并）；无告警规则/Alertmanager/业务指标仍属实 |
| E2 日志治理 | ⚠️ 部分属实 | "structlog 声明但 0 使用"已过时——**structlog 已删除**（P2-12）；_complete_text 吞异常已修复（P1-6）；"28 模块标准库 logging 格式不统一、无集中收集"仍属实 |
| E3 外部依赖可靠性制度 | ✅ 属实 | LLM 调用有超时降级但无重试/熔断；Milvus 无显式超时；降级矩阵未文档化 |

### 2.6 代码质量与工程规范

| 条目 | 核验 | 说明 |
|---|---|---|
| F1 类型检查接入 | ✅ 属实 | 无 mypy/pyright |
| F2 lint/format 门禁 | ⚠️ 部分属实 | "ruff 存量 35 错误"已过时——**ruff 已清零**（P2-20，0 错误）；`ruff format` 未配置仍属实 |
| F3 前端质量门禁 | ✅ 属实 | web-ui 无 ESLint/测试/类型检查（package.json 仅 dev/build/preview） |
| F4 代码评审与架构治理 | ✅ 属实 | 无评审制度/检查单/依赖方向约束 |

### 2.7 测试与质量保障

| 条目 | 核验 | 说明 |
|---|---|---|
| G1 评估门禁自动化 | ✅ 属实 | 回归门禁手动运行；结果无时间序列归档 |
| G2 测试基建加固 | ✅ 属实 | 同 FIX_LIST P2-15（共享 TestClient、sleep、清理策略）；覆盖率无门禁 |
| G3 性能与容量基准 | ✅ 属实 | 50 并发压测无脚本化；无基准回归检测 |

### 2.8 依赖与供应链治理

| 条目 | 核验 | 说明 |
|---|---|---|
| H1 依赖声明完整性 | ⚠️ 部分属实 | langgraph/langchain-openai **已声明**（FIX_LIST P1-9）；minio/structlog 已删、langchain-community 保留（ragas 兼容）；deptry 检测机制未建仍属实 |
| H2 升级策略与兼容窗口 | ⚠️ 部分属实 | websockets==15.0.1 保留（uvicorn 兼容原因，非死 pin）；无升级窗口机制/兼容性结论表仍属实 |

### 2.9 流程与治理

| 条目 | 核验 | 说明 |
|---|---|---|
| I1 版本与发布流程 | ✅ 属实 | version=0.1.0；无 CHANGELOG/发布检查单 |
| I2 运维 Runbook | ✅ 属实 | MANUAL 有部署/FAQ，无故障 Runbook（memory 踩坑记录未文档化） |
| I3 安全评审周期 | ✅ 属实 | 做过一次全量审计（已修复），无周期性机制/威胁模型文档 |

---

## 汇总

- **完全属实**：21 项（A1~A3、B1、B2、B4、C1、C3、C4、D1、D3、D4、E3、F1、F3、F4、G1、G2、G3、I1、I2、I3）
- **部分属实（现状已被 FIX_LIST 修复，建议方向仍成立）**：9 项（B3、C2、E1、E2、F2、H1、H2 + D2 问题实质成立）
- **误报**：1 处（A1 的 .gitignore 乱码、D2 的 AUTH_DISABLE_SIGNUP 注释——均不存在）

## 与 FIX_LIST 的衔接

建议书中标注"修复中"的条目（P1-5 优雅退出、P1-6 吞异常、P1-9/P2-12 依赖、P2-13 pin、P2-14 指标、P2-20 ruff）**已全部修复**（FIX_LIST 处理完毕，169 测试全绿，ruff 0 错误）。本建议书剩余未落地项以 **Phase 1 的四大地基**为最高优先级：A2（CI）、B1（Alembic）、C1（容器化）、F1（mypy）。
