"""评估黄金数据集：30 条，覆盖 PRD / TECH_STACK 各章节 + 四类意图（FR-37）。

- intent=qa：知识查询，判定命中 = 检索结果存在 doc_id 匹配且 section_path 含前缀的块
- intent=chat：知识库外闲聊，期望拒答（生成评估校验回答不含编造内容）
- intent=summary：文档/主题总结
- reference：参考答案（RAGAS context_precision 等指标需要）
- 反馈回流样本（FR-40）：docs/feedback_cases.json，经 load_feedback_cases 合并，
  仅参与生成评估（无期望命中文档，不污染检索指标）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

FEEDBACK_CASES_PATH = Path("docs") / "feedback_cases.json"


@dataclass
class GoldenCase:
    question: str
    doc_id: str  # 期望命中的文档（chat 类为空；反馈回流样本为空）
    section_prefix: str  # 期望命中的章节路径前缀（空串表示该文档任意章节）
    intent: str = "qa"  # qa | summary | chat | clarify
    reference: str = ""
    source: str = ""  # 样本来源："" | feedback:<消息id>（FR-40）

    def matches(self, doc_id: str, section_path: str) -> bool:
        # section_path 带文档标题前缀（如 "...（PRD） > 4. 功能需求 > ..."），
        # 期望前缀为章节路径片段，用包含关系判定
        return doc_id == self.doc_id and self.section_prefix in section_path


GOLDEN_SET: list[GoldenCase] = [
    # ---------- PRD：目标与范围 ----------
    GoldenCase(
        "端到端支持哪些能力？",
        "PRD",
        "2. 项目目标与非目标 > 2.1 目标",
        reference="端到端支持上传文档、解析索引、自然语言问答、带引用溯源的回答，"
        "以及中文长文档问答、权限隔离和评估回归（G1-G6）。",
    ),
    GoldenCase(
        "本项目本期不做哪些事情？",
        "PRD",
        "2. 项目目标与非目标 > 2.2 非目标",
        reference="不做大模型训练/微调、通用对话助手、知识图谱自动构建、移动端客户端、多模态文档问答。",
    ),
    # ---------- PRD：用户角色 ----------
    GoldenCase(
        "知识库管理员的核心诉求是什么？",
        "PRD",
        "3. 用户角色与典型场景",
        reference="批量导入、增量更新、权限配置、质量监控。",
    ),
    # ---------- PRD：功能需求 ----------
    GoldenCase(
        "文档上传支持哪些文件格式？",
        "PRD",
        "4. 功能需求 > 4.1 文档接入与管理",
        reference="PDF（含扫描件 OCR）、DOCX、PPTX、Markdown、HTML、TXT、CSV/表格。",
    ),
    GoldenCase(
        "文档更新后旧版本怎么处理？",
        "PRD",
        "4. 功能需求 > 4.1 文档接入与管理",
        reference="同名文档上传视为新版本，旧版本自动失效（软删除，保留审计）。",
    ),
    GoldenCase(
        "分块策略有什么要求？",
        "PRD",
        "4. 功能需求 > 4.2 解析与索引",
        reference="结构感知分块：按标题层级切分，表格整块保留，块内保留文档结构与上下文。",
    ),
    GoldenCase(
        "摄入管道幂等是什么意思？",
        "PRD",
        "4. 功能需求 > 4.2 解析与索引",
        reference="重复摄入不产生重复块；失败任务可重跑。",
    ),
    GoldenCase(
        "混合检索由哪些部分组成？",
        "PRD",
        "4. 功能需求 > 4.3 检索",
        reference="稠密向量 + BM25 稀疏检索，RRF 融合，再加 Rerank 精排。",
    ),
    GoldenCase(
        "query 改写解决什么问题？",
        "PRD",
        "4. 功能需求 > 4.3 检索",
        reference="把口语化问题改写为适合检索的表述，提升召回质量。",
    ),
    GoldenCase(
        "意图路由识别哪几类？",
        "PRD",
        "4. 功能需求 > 4.4 Agent 能力",
        reference="知识库问答、文档总结、无关话题拒答、需澄清四类。",
    ),
    GoldenCase(
        "CRAG 反思机制是什么？",
        "PRD",
        "4. 功能需求 > 4.4 Agent 能力",
        reference="检索质量自评，低置信时自动重写查询、调整检索参数或切换检索源；两次仍低则如实说明。",
    ),
    GoldenCase(
        "回答必须带什么才能让企业用户采信？",
        "PRD",
        "4. 功能需求 > 4.5 问答交互",
        reference=(
            "引用溯源（FR-28）：引用标注（如 [1][2]）可跳转原文块、展示引用原文，回答可核实。"
        ),
    ),
    GoldenCase(
        "检索不到依据时系统应该怎么做？",
        "PRD",
        "4. 功能需求 > 4.5 问答交互",
        reference="明确告知知识库中未找到依据，而不是编造回答。",
    ),
    GoldenCase(
        "知识库的权限过滤应该在哪里执行？",
        "PRD",
        "4. 功能需求 > 4.6 权限与安全",
        reference="权限过滤在检索层执行：把文档级/目录级访问控制条件下推到向量库 filter，"
        "严禁检索结果返回后再过滤，避免越权数据泄露。",
    ),
    GoldenCase(
        "审计日志记录哪些事件？",
        "PRD",
        "4. 功能需求 > 4.6 权限与安全",
        reference="登录、上传、删除、问答中的权限拒绝事件。",
    ),
    GoldenCase(
        "黄金数据集评估要达到多少条起步？",
        "PRD",
        "4. 功能需求 > 4.7 评估体系",
        reference="黄金数据集 30~50 条起步（FR-37），每条含问题、参考答案、期望引用的文档范围。",
    ),
    GoldenCase(
        "自动化评估用哪些指标？",
        "PRD",
        "4. 功能需求 > 4.7 评估体系",
        reference="faithfulness、context precision、answer relevance，及检索端 recall@k / MRR。",
    ),
    GoldenCase(
        "回归门禁的作用是什么？",
        "PRD",
        "4. 功能需求 > 4.7 评估体系",
        reference="检索/分块/提示词改动后跑评估，指标劣化即阻断上线（FR-39）。",
    ),
    # ---------- PRD：非功能与验收 ----------
    GoldenCase(
        "文档被删除后多久应该从检索范围生效？",
        "PRD",
        "5. 非功能需求",
        reference="删除后 24 小时内从所有缓存生效，检索不再引用旧内容。",
    ),
    GoldenCase(
        "服务可用性目标是多少？",
        "PRD",
        "5. 非功能需求",
        reference="服务可用性 ≥ 99.5%；摄入失败不影响问答服务。",
    ),
    GoldenCase(
        "P0 验收对 faithfulness 的要求？",
        "PRD",
        "7. 验收标准",
        reference="黄金数据集评估 faithfulness ≥ 0.85，answer relevance ≥ 0.80。",
    ),
    # ---------- TECH_STACK：选型 ----------
    GoldenCase(
        "P0 阶段向量库选型是什么？",
        "TECH_STACK",
        "3. 技术选型",
        reference="Milvus（Docker 单机部署），稠密向量 + BM25 稀疏检索 + filter 权限下推一站式。",
    ),
    GoldenCase(
        "PostgreSQL 在系统中承担什么角色？",
        "TECH_STACK",
        "3. 技术选型 > 3.3 存储层",
        reference="文档元数据、用户、角色、会话、审计日志的关系存储。",
    ),
    GoldenCase(
        "嵌入模型为什么选 BGE-M3？",
        "TECH_STACK",
        "8. 关键技术决策记录",
        reference="BGE-M3 中文语义能力强，且稠密/稀疏双输出，与 Milvus 内置 BM25 天然互补。",
    ),
    GoldenCase(
        "RRF 融合相比权重加权有什么优势？",
        "TECH_STACK",
        "8. 关键技术决策记录",
        reference="无调参、稳健，避免稠密稀疏得分不可比问题（ADR-04）。",
    ),
    GoldenCase(
        "检索精度由哪些环节保证？",
        "TECH_STACK",
        "8. 关键技术决策记录",
        reference="混合检索（稠密+BM25）→ RRF 融合 → bge-reranker 精排 → CRAG 反思重写。",
    ),
    GoldenCase(
        "权限过滤的实现位置？",
        "TECH_STACK",
        "8. 关键技术决策记录",
        reference="检索层 filter 下推（ADR-05），向量库查询阶段执行，禁止查全量再过滤。",
    ),
    # ---------- 闲聊（chat 类：期望拒答） ----------
    GoldenCase(
        "今天天气怎么样？",
        "",
        "",
        intent="chat",
        reference="应拒答：只能回答企业知识库相关问题。",
    ),
    GoldenCase(
        "推荐一部好看的电影",
        "",
        "",
        intent="chat",
        reference="应拒答：不编造知识库外内容。",
    ),
    GoldenCase(
        "帮我写一首诗",
        "",
        "",
        intent="chat",
        reference="应拒答：礼貌告知只能回答知识库相关问题。",
    ),
]


def qa_cases() -> list[GoldenCase]:
    """检索/生成评估用：排除 chat 类（无检索目标）。"""
    return [c for c in GOLDEN_SET if c.intent != "chat"]


def chat_cases() -> list[GoldenCase]:
    return [c for c in GOLDEN_SET if c.intent == "chat"]


def load_feedback_cases() -> list[GoldenCase]:
    """反馈回流样本（FR-40）：docs/feedback_cases.json，不存在则空。"""
    if not FEEDBACK_CASES_PATH.exists():
        return []
    items = json.loads(FEEDBACK_CASES_PATH.read_text(encoding="utf-8"))
    return [
        GoldenCase(
            question=item["question"],
            doc_id=item.get("doc_id") or "",
            section_prefix=item.get("section_prefix") or "",
            intent=item.get("intent") or "qa",
            reference=item.get("reference") or "",
            source=item.get("source") or "feedback",
        )
        for item in items
    ]


def all_cases() -> list[GoldenCase]:
    """生成评估全集：黄金集非 chat 类 + 反馈回流样本（FR-40）。"""
    return qa_cases() + load_feedback_cases()


def hit_at_k(case: GoldenCase, results: list[tuple[str, str]]) -> int | None:
    """返回首个命中 case 的结果位置（0-based），未命中返回 None。"""
    for idx, (doc_id, section_path) in enumerate(results):
        if case.matches(doc_id, section_path):
            return idx
    return None
