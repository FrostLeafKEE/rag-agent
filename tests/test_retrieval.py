"""检索层单元测试：RRF 融合、权限 filter、Reranker、黄金集匹配。"""

import pytest

from app.eval.golden_set import GOLDEN_SET, GoldenCase, hit_at_k
from app.retrieval.base import RetrievedChunk, build_filter
from app.retrieval.hybrid import rrf_fuse
from app.retrieval.reranker import NoopReranker, RerankerError, SiliconFlowReranker


def _hit(chunk_id: int, doc_id: str = "doc", rank_score: float = 1.0) -> dict:
    return {
        "id": chunk_id,
        "distance": rank_score,
        "entity": {
            "id": chunk_id,
            "doc_id": doc_id,
            "chunk_index": chunk_id,
            "content": f"内容{chunk_id}",
            "section_path": f"章节{chunk_id}",
            "department": "研发部",
        },
    }


def test_rrf_fuses_and_ranks_by_reciprocal_rank() -> None:
    dense = [_hit(1), _hit(2), _hit(3)]
    sparse = [_hit(3), _hit(2), _hit(4)]  # 3 在两路都出现
    fused = rrf_fuse(dense, sparse, top_k=10)
    ids = [c.chunk_id for c in fused]
    # 3 在两路排名都靠前，RRF 分最高；其次 2、1、4
    assert ids[0] == 3
    assert set(ids) == {1, 2, 3, 4}
    assert fused[0].score > fused[1].score > fused[3].score


def test_rrf_respects_top_k() -> None:
    dense = [_hit(i) for i in range(1, 8)]
    fused = rrf_fuse(dense, [], top_k=5)
    assert len(fused) == 5


def test_rrf_single_route() -> None:
    fused = rrf_fuse([_hit(9)], [], top_k=10)
    assert len(fused) == 1
    assert fused[0].chunk_id == 9


def test_build_filter() -> None:
    assert build_filter(None) == ""  # admin 不限
    assert build_filter([]) == "1 == 0"  # 空部门用户零可见（安全修复）
    assert build_filter(["研发部"]) == 'department in ["研发部"]'
    assert build_filter(["a", "b"]) == 'department in ["a", "b"]'


def test_noop_reranker_keeps_order() -> None:
    chunks = [
        RetrievedChunk(chunk_id=i, doc_id="d", chunk_index=i, content=f"c{i}")
        for i in range(3)
    ]
    reranked = NoopReranker().rerank("q", chunks, top_k=2)
    assert [c.chunk_id for c in reranked] == [0, 1]


def test_siliconflow_reranker_reorders(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [
        RetrievedChunk(chunk_id=0, doc_id="d", chunk_index=0, content="低相关"),
        RetrievedChunk(chunk_id=1, doc_id="d", chunk_index=1, content="高相关"),
    ]

    class FakeResp:
        status_code = 200

        def json(self) -> dict:
            return {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ]
            }

    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: ANN001, ANN003
        captured["json"] = json
        assert url.endswith("/rerank")
        assert json["model"] == "test-model"
        return FakeResp()

    monkeypatch.setattr("app.retrieval.reranker.httpx.post", fake_post)
    reranker = SiliconFlowReranker("https://fake/v1", "k", "test-model")
    reranked = reranker.rerank("q", chunks)
    assert [c.chunk_id for c in reranked] == [1, 0]
    assert reranked[0].score == 0.9


def test_reranker_raises_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        status_code = 429
        text = "rate limited"

    monkeypatch.setattr(
        "app.retrieval.reranker.httpx.post",
        lambda *a, **k: FakeResp(),
    )
    reranker = SiliconFlowReranker("https://fake/v1", "k", "m")
    with pytest.raises(RerankerError):
        reranker.rerank("q", [RetrievedChunk(chunk_id=1, doc_id="d", chunk_index=0, content="c")])


def test_golden_case_matches_with_prefix() -> None:
    case = GoldenCase("q", "PRD", "4. 功能需求 > 4.6 权限与安全")
    full = "企业级 RAG Agent 项目需求书（PRD） > 4. 功能需求 > 4.6 权限与安全"
    assert case.matches("PRD", full)
    assert not case.matches("PRD", full.replace("4.6", "4.5"))
    assert not case.matches("OTHER", full)


def test_hit_at_k_returns_first_match_position() -> None:
    case = GoldenCase("q", "PRD", "4.6")
    results = [("PRD", "1. 项目背景"), ("PRD", "4. 功能需求 > 4.6 权限与安全"), ("PRD", "4.6 其他")]
    assert hit_at_k(case, results) == 1
    assert hit_at_k(case, [("X", "4.6")]) is None


def test_golden_set_is_non_empty() -> None:
    assert len(GOLDEN_SET) >= 30
    # 非闲聊用例须指向 PRD/TECH_STACK；闲聊用例 doc_id 为空
    assert all(c.doc_id in {"PRD", "TECH_STACK", ""} for c in GOLDEN_SET)
    assert all(c.doc_id for c in GOLDEN_SET if c.intent != "chat")
    assert sum(1 for c in GOLDEN_SET if c.intent == "chat") >= 3  # 拒答用例


def test_rerank_failure_falls_back_to_fusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """健壮性：精排失败必须降级为融合结果，不能中断检索。"""
    from app.retrieval import search as search_mod
    from app.retrieval.reranker import NoopReranker

    class FakeIndexer:
        def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
            pass

        def ensure_collection(self) -> None:
            pass

        def search_dense(self, vector, top_k, filter_expr=""):  # noqa: ANN001, ANN002, ANN003
            return [_hit(1)]

        def search_sparse(self, text, top_k, filter_expr=""):  # noqa: ANN001, ANN002, ANN003
            return [_hit(2)]

    class FakeEmbedder:
        dim = 1024

        def embed_one(self, text):  # noqa: ANN001
            return [0.1]

    class FakeSettings:
        milvus_uri = "x"
        milvus_collection = "c"

    def boom_rerank(query, chunks, top_k=None):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("rerank 服务不可用")

    fake_reranker = NoopReranker()
    fake_reranker.rerank = boom_rerank  # 覆盖为必失败

    monkeypatch.setattr(search_mod, "get_reranker", lambda: fake_reranker)
    monkeypatch.setattr(search_mod, "MilvusIndexer", FakeIndexer)
    monkeypatch.setattr(search_mod, "get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(search_mod, "get_settings", lambda: FakeSettings())

    results = search_mod.search("q", top_k=5, use_rerank=True)
    assert len(results) == 2  # 融合结果返回而非抛异常
