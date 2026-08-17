# 运维 Runbook — 已知故障模式处置手册

> **编写**：2026-08-17（ROADMAP R10）
> **范围**：项目实际踩过的坑 + 常见故障。每条：症状 → 诊断 → 处置 → 预防。
> **关联**：[MANUAL.md](./MANUAL.md)（部署/使用）、[TECH_STACK.md](./TECH_STACK.md)

---

## 1. worker 多进程残留（旧代码 worker 抢消费）

- **症状**：改动 worker 逻辑后"不生效"——任务被处理但行为是旧的；`tasklist | grep python` 出现多个 `app.ingestion.worker` 进程。
- **原因**：Redis Stream 消费组允许多 consumer 共存；历史会话遗留的旧 worker 进程仍持有连接，新消息被旧进程（旧代码）抢走。
- **诊断**：
  ```bash
  powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { \$_.CommandLine -match 'worker' } | Select ProcessId,CommandLine"
  ```
- **处置**：全部杀掉只留一个最新进程：
  ```bash
  taskkill //F //PID <每个旧 PID>
  .venv/Scripts/python.exe -m app.ingestion.worker   # 重启唯一实例
  ```
- **预防**：改 worker 行为后先清进程再重启；部署脚本固定 consumer 名并单实例运行。

## 2. uvicorn 启动失败：websockets pyd 文件锁

- **症状**：`uv run uvicorn ...` 报 `PermissionError`/pyd 文件被占用，或服务起不来。
- **原因**：Windows 上 `uv run` 与运行中的 uvicorn 争用 `.venv` 中 websockets 的 pyd 锁文件。
- **处置**：直接调用解释器，不用 `uv run`：
  ```bash
  .venv/Scripts/uvicorn.exe app.main:app --port 8000
  ```
- **预防**：服务进程一律用 `.venv/Scripts/*.exe` 启动；`uv run` 仅用于一次性命令。

## 3. Redis 假死 / 连接挂起

- **症状**：登录/上传/worker 长时间无响应；日志停在 Redis 操作。
- **原因**：Redis 容器假死或网络异常；早期代码无连接超时（`socket_connect_timeout` 缺失）会无限挂起。
- **诊断**：`docker ps` 看 rag-redis 状态；`docker exec rag-redis redis-cli ping`。
- **处置**：重启 Redis 容器；代码已统一 `socket_connect_timeout=5`（2026-08 修复），超时会快速失败并降级（登录限速放行、队列报错重试）。
- **预防**：保持超时配置一致；监控容器健康状态。

## 4. Milvus 检索异常 / collection 未加载

- **症状**：问答检索 0 条或报错；`docker ps` 中 rag-milvus 状态异常。
- **原因**：Milvus 重启后 collection 需重新加载；容器健康检查未覆盖 Milvus（无 healthcheck）。
- **诊断**：
  ```bash
  docker ps --format '{{.Names}} {{.Status}}' | grep milvus
  # 检查 collection 状态（应用侧）：
  uv run python -c "from pymilvus import MilvusClient; from app.config import get_settings; c=MilvusClient(uri=get_settings().milvus_uri); print(c.get_collection_stats(get_settings().milvus_collection))"
  ```
- **处置**：`docker restart rag-milvus`；应用侧 `ensure_collection` 会在摄入时自动 load。若检索仍空，确认摄入任务是否完成（documents 表 status）。
- **预防**：摄入/检索前确认 collection loaded；监控 Milvus 指标。

## 5. ClickHouse / Langfuse 事件不落库

- **症状**：Langfuse 控制台看不到 trace；或 Langfuse 页面异常。
- **原因**（历史踩坑）：Langfuse v3 需**独立 worker 镜像**处理事件上传；ClickHouse 需 named volume（Windows bind mount 重命名权限问题）；事件上传需专用 S3 前缀变量。
- **诊断**：
  ```bash
  docker ps --format '{{.Names}} {{.Status}}' | grep -E "langfuse|clickhouse"
  docker logs rag-langfuse-worker --tail 20
  ```
- **处置**：确认 compose 中 langfuse-worker 服务在跑；ClickHouse 卷挂载为 named volume；检查 `LANGFUSE_S3_EVENT_UPLOAD_*` 变量（如用 S3 存储事件）。
- **预防**：部署时严格按 docker-compose 配置（含 worker 服务），不删改 ClickHouse 卷类型。

## 6. 上游 LLM 服务故障（moyuu.cc 渠道不可用）

- **症状**：问答报"服务内部错误"或回答为空；API 日志出现 `LLM 接口返回 500 ... get_channel_failed`；所有 LLM JSON 调用解析失败。
- **原因**：第三方 LLM 网关（moyuu.cc）渠道临时故障，非本项目问题。
- **诊断**：
  ```bash
  uv run python -c "import asyncio; from app.llm.gateway import get_llm; asyncio.run(asyncio.to_thread(get_llm().complete, [{'role':'user','content':'只回复OK'}], temperature=0))"
  ```
- **处置**：等待上游恢复后重试；权限过滤与检索在 LLM 故障期间仍正常（检索层独立）。
- **预防**：CI 评估 job 手动触发（外部波动不阻断主干）；降级矩阵见 TECH_STACK（重排失败降 RRF 等）。

## 7. 摄入任务滞留 PEL（崩溃后不重投）

- **症状**：上传后 documents 状态长期 `uploading`/`processing`；worker 重启也不消费。
- **原因**：worker 读到消息后、XACK 前崩溃 → 消息进 Pending Entries List；旧版只读 `>` 新消息。
- **处置**：重启 worker（2026-08 修复后启动时自动 XCLAIM 认领 idle>60s 的 PEL 消息重新入队）。旧版本需手动：
  ```bash
  docker exec rag-redis redis-cli XPENDING rag:ingestion ingestion-workers
  docker exec rag-redis redis-cli XCLAIM rag:ingestion ingestion-workers recovery 60000 <msg-id>
  ```
- **预防**：使用含 PEL 恢复的 worker 版本（≥ commit bbd894a）。

## 8. 浏览器显示旧版前端

- **症状**：改了前端但页面样式/功能不变。
- **原因**：浏览器 HTTP 缓存了 index.html/CSS（同 URL 的 goto/reload 可能不重新加载）。
- **处置**：Ctrl+F5 强刷；或 URL 加 cache-busting 参数（`?_cb=N`）。
- **预防**：前端构建产物带 hash 文件名；发布后提示用户强刷。

## 9. Git Bash 中文参数乱码（开发环境）

- **症状**：curl 传中文 department/JSON body 报 "error parsing the body" 或存库为乱码（`ÑÐ·¢²¿`）。
- **原因**：Windows Git Bash 下 curl 发送中文按 GBK 编码，服务端按 UTF-8 解析。
- **处置**：用 Python requests（UTF-8 安全）或把 JSON 写入文件后 `curl --data @file.json`。
- **预防**：脚本/测试统一用 requests；命令行避免直接传中文。

---

## 故障模式索引

| # | 模式 | 关键信号 | 处置入口 |
|---|---|---|---|
| 1 | worker 残留 | 多个 python worker 进程 | §1 |
| 2 | uvicorn 锁 | pyd PermissionError | §2 |
| 3 | Redis 假死 | 操作挂起 | §3 |
| 4 | Milvus 未加载 | 检索 0 条 | §4 |
| 5 | Langfuse 无 trace | 控制台空白 | §5 |
| 6 | 上游 LLM 故障 | 500 get_channel_failed | §6 |
| 7 | PEL 滞留 | 状态卡 uploading | §7 |
| 8 | 前端缓存 | 页面不更新 | §8 |
| 9 | 中文乱码 | body parsing error | §9 |

*本手册持续更新：新增踩坑请按"症状→诊断→处置→预防"格式补充。*
