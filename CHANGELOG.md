# Changelog

本项目采用语义化版本（SemVer）：`主版本.次版本.补丁`

- **主版本**：破坏性变更（API 不兼容、数据迁移不可逆）
- **次版本**：新功能（向后兼容）
- **补丁**：缺陷修复

发布检查单（docs/ROADMAP.md I1）：迁移执行 → 密钥核对 → 默认凭据轮换 → 评估回归 → 回滚预案。变更记录按 [Conventional Commits](https://www.conventionalcommits.org/) 归类（feat/fix/docs/chore/refactor）。

---

## [0.2.0] - 2026-08-17

### 新增（feat）
- RBAC 三角色：超级管理员 / 部门管理员（负责部门集合 AdminDepartment）/ 普通用户；文档管理管理员化；用户管理页（角色/部门分配/重置密码/停用）
- 注册开关 `AUTH_DISABLE_SIGNUP`（生产强制关闭开放注册，账号由管理员创建）
- 安全响应头中间件（X-Content-Type-Options / X-Frame-Options / Referrer-Policy）
- 审计日志查询增强：action 过滤 + 分页 + CSV 导出（管理台"审计日志"页签）
- Alembic 数据库迁移（初始迁移覆盖 7 表，存量库已 stamp；init_db 改为 Alembic 感知）
- mypy 类型检查接入（app/ 0 错误）、ruff format 统一格式
- CI 流水线（ruff/mypy/pytest/vite build + 手动触发的评估回归 job）
- pre-commit 配置（ruff + uv lock check）
- 运维 Runbook（docs/RUNBOOK.md，9 个故障模式）

### 修复（fix）
- **安全**：部门名 filter 注入（SAFE_DEPARTMENT 白名单 + 三入口校验）；工具 SQL 表名单逗号分隔绕过（FROM/JOIN 子句全校验）；doc_id 冲突归属校验收紧（仅 super_admin 可覆盖）
- **可靠性**：worker PEL 滞留消息恢复（启动 XCLAIM 重投）；摄入失败落库 failed+error（不再悬挂 processing）；worker/connector 优雅退出（信号处理）
- **健壮性**：connector doc_id 截断 ≤64+sha1；上传魔数校验（假 PDF 拒收）；写盘移线程池 + 入队失败清理；Redis 连接超时统一；指标 label 改路由模板；日志片段脱敏；Langfuse 客户端双检锁
- **依赖**：声明 langgraph/langchain-openai；移除 minio/structlog 死依赖

### 工程（chore）
- git 仓库初始化（此前无版本控制）；.gitignore 补齐前端产物/测试残留
- 文档体系完善：MANUAL（部署/使用）、RUNBOOK（故障处置）、ROADMAP（改进路线）、FIX_LIST（缺陷修复清单）、IMPROVEMENT_PLAN_REVIEW（建议书核验）

---

## [0.1.0] - 2026-08-16

初始版本（P0 + P1 全量）：

- 文档摄入（PDF/DOCX/PPTX/MD/TXT/HTML + OCR）、混合检索（Milvus 稠密+BM25+RRF+重排）、LangGraph Agent（意图路由/CRAG/多跳/改写/多查询）、SSE 流式问答 + 引用溯源
- Redis Stream 摄入队列 + 独立 worker、审计日志、admin 用户管理
- 评估体系：黄金集 30 条、RAGAS 三指标、回归门禁、反馈回流
- Langfuse trace、Prometheus/Grafana 指标面板
- Vue3 管理台（问答工作台 / 文档管理）
