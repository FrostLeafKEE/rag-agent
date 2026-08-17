# 安全备注（依赖漏洞跟踪）

> 关联：ROADMAP R3 / REAUDIT_FOLLOWUP R3-1
> 规则：已知漏洞均无修复版本时用 `uv audit || true` 不阻断 CI；**每月复核一次**是否有新修复版本，有则升级并在此更新。

## 当前已知漏洞（2026-08-17 扫描）

| 包 | 版本 | 漏洞 | 风险说明 | 处理 |
|---|---|---|---|---|
| diskcache | 5.6.3 | CVE-2025-69872（不安全 pickle 反序列化，GHSA-w8v5-vhqr-4h9v / PYSEC-2026-2447） | ragas 传递依赖；本项目未直接使用 diskcache 的持久化缓存路径 | 无修复版本，等待上游；每月复核 |
| ragas | 0.4.3 | CVE-2026-6587（Multi-Modal Faithfulness 模块 SSRF，GHSA-95ww-475f-pr4f / PYSEC-2026-3046） | 我们仅用文本指标（faithfulness/answer_relevancy/context_precision），**未使用多模态模块** | 无修复版本，等待上游；每月复核 |

**复核命令**：`uv audit`（CI 中 `uv audit || true` 不阻断，本表人工复核）。

**复核记录**：
- 2026-08-17：初始扫描，4 个已知漏洞（上述 2 包 × 2 来源），均无修复版本，非使用路径。
