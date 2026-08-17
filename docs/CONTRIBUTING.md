# 贡献指南（CONTRIBUTING）

## 分支与提交

- 分支：`main`（受保护）/ `master`（当前默认）→ 新功能用 `feature/<编号>` 分支 + PR 评审后合入
- 提交信息遵循 Conventional Commits：`feat:` / `fix:` / `docs:` / `refactor:` / `chore:`，关联需求编号（FR-/R-）

## PR 检查单（五项必查）

1. **安全影响**：改动是否触及认证/RBAC/注入面/上传输入？需要的话更新 tests/test_security_regression.py 或 tests/test_rbac.py；
2. **日志**：异常路径是否可观测（禁止静默 except）？敏感信息（用户问题/密钥）不进日志；
3. **测试**：新逻辑带测试；全量 `uv run pytest tests/ -q` 全绿；
4. **迁移**：涉及表结构变更必须走 Alembic（`alembic revision --autogenerate`），禁止改 create_all 路径；
5. **文档同步**：PRD 附录 A / CHANGELOG / RUNBOOK 按影响更新。

## 质量门禁（合入前必须全过）

```bash
uv run ruff check .          # 0 错误
uv run ruff format --check . # 0 待格式化
uv run mypy app/             # 0 错误
uv run pytest tests/ -q      # 全绿
```

## 模块依赖方向

```
agent → retrieval / llm / tools（编排层依赖能力层）
api → agent / ingestion / observability
ingestion → parser / chunker / indexer（禁止反向）
```

**禁止**：retrieval/llm 反向 import agent；新依赖引入需在 PR 说明理由（防死依赖重现）。

## 新依赖引入

- 直接 import 的库必须声明在 `pyproject.toml [project].dependencies`（不允许靠传递依赖存活）；
- 加锁：`uv add <pkg>`（自动更新 uv.lock）；删除依赖用 `uv remove`；
- 兼容性约束（如 `websockets==15.0.1` 为 uvicorn 兼容、`langchain-community<0.4` 为 ragas 兼容）改动前先读 TECH_STACK 兼容性结论。
