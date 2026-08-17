"""脱敏引擎与接入测试（FR-36，P2 W4）：内置 PII 规则 / 词表 / 开关 / 跨帧 / 管道 / 审计。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.security.redaction import RedactionEngine

DEFAULT = RedactionEngine()


# ---- 内置 PII 规则 ----


def test_phone_masked() -> None:
    text, hits = DEFAULT.redact("联系电话：13812345678，请惠存。")
    assert "138****5678" in text
    assert "13812345678" not in text
    assert hits == ["phone"]


def test_id_card_masked() -> None:
    text, hits = DEFAULT.redact("身份证 110101199003074512。")
    assert "110101199003074512" not in text
    assert "1101**********4512" in text  # 前4 + 10* + 后4
    assert hits == ["id_card"]


def test_email_masked() -> None:
    text, hits = DEFAULT.redact("联系 a.zhang@company.com 获取。")
    assert "a.zhang@company.com" not in text
    assert "a****@company.com" in text  # 本地部分掩码、保留域名
    assert hits == ["email"]


def test_bank_card_masked() -> None:
    text, hits = DEFAULT.redact("收款卡号 6222020200112233445。")
    assert "6222020200112233445" not in text
    assert "6222***********3445" in text  # 前4 + 11* + 后4（19 位）
    assert hits == ["bank_card"]


def test_api_key_masked() -> None:
    text, hits = DEFAULT.redact("token=sk-abcdefghijklmnopqrstuvwxyz123")
    assert "sk-abcdefghijklmnopqrstuvwxyz123" not in text
    assert "sk-a****" in text and text.endswith("z123")  # 前4+掩码+后4
    assert hits == ["api_key"]


def test_ipv4_masked() -> None:
    text, hits = DEFAULT.redact("部署在 192.168.10.25 上")
    assert "192.168.10.25" not in text
    assert hits == ["ipv4"]


def test_multi_hits() -> None:
    text, hits = DEFAULT.redact("电话 13812345678，邮箱 a@b.com")
    assert set(hits) == {"phone", "email"}


def test_short_numbers_not_false_positive() -> None:
    text, hits = DEFAULT.redact("版本 1.2.3，共 100 元，编号 20260817")
    assert hits == []


# ---- 词表与开关 ----


def test_word_rules() -> None:
    engine = RedactionEngine(
        rules=[{"type": "word", "name": "内部代号", "words": ["北极星项目", "深海计划"]}]
    )
    text, hits = engine.redact("北极星项目已完成，深海计划暂停。")
    assert "北极星项目" not in text and "深海计划" not in text
    assert hits == sorted(["内部代号", "内部代号"]) or hits == ["内部代号"]


def test_disabled_engine_passthrough() -> None:
    engine = RedactionEngine(enabled=False)
    assert engine.redact("13812345678") == ("13812345678", [])


def test_empty_text() -> None:
    assert DEFAULT.redact("") == ("", [])


# ---- 流式出口：跨帧命中 ----


def test_stream_redacted_cross_frame() -> None:
    """手机号被切在帧边界时仍能完整脱敏（尾部缓冲）。"""
    from app.agent import service

    async def source():
        for part in ["联系 1381", "234567", "8，请惠存"]:
            yield part

    async def run() -> str:
        parts = []
        async for p in service._stream_redacted(source(), DEFAULT):  # noqa: SLF001
            parts.append(p)
        return "".join(parts)

    out = asyncio.run(run())
    assert "13812345678" not in out
    assert "138****5678" in out


def test_stream_redacted_disabled_passthrough() -> None:
    from app.agent import service

    async def source():
        yield "13812345678"

    async def run() -> str:
        parts = []
        async for p in service._stream_redacted(source(), RedactionEngine(enabled=False)):  # noqa: SLF001
            parts.append(p)
        return "".join(parts)

    assert asyncio.run(run()) == "13812345678"


# ---- 摄入环节：分块脱敏 + 审计 ----


def test_pipeline_redacts_chunks_before_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """写入索引/嵌入的内容必须已脱敏；命中回调上报规则名。"""
    from app.ingestion import pipeline
    from app.retrieval.base import RetrievedChunk

    sent: dict = {}

    class FakeEmbedder:
        dim = 3

        def embed(self, contents):  # noqa: ANN001
            sent["embedded"] = list(contents)
            return [[0.0] * 3] * len(contents)

    class FakeIndexer:
        def __init__(self, *a, **k):  # noqa: ANN002, ANN003
            pass

        def ensure_collection(self) -> None:
            pass

        def delete_by_doc(self, doc_id):  # noqa: ANN001
            sent["deleted"] = doc_id

        def upsert(self, chunks, vectors):  # noqa: ANN001, ANN002
            sent["upserted"] = [c.content for c in chunks]

    monkeypatch.setattr(pipeline, "parse_document", lambda p: [])
    monkeypatch.setattr(
        pipeline,
        "chunk_document",
        lambda *a, **k: [
            RetrievedChunk(
                chunk_id=1,
                doc_id="D",
                chunk_index=0,
                content="联系人：13812345678",
                section_path="",
                department="",
                score=0.0,
            )
        ],
    )
    monkeypatch.setattr(pipeline, "get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(pipeline, "MilvusIndexer", FakeIndexer)
    monkeypatch.setattr(pipeline, "get_engine", lambda path: RedactionEngine())  # noqa: ARG005

    hits: list[list[str]] = []
    count = pipeline.ingest_document_sync(Path("x.md"), "D", on_redact_hit=hits.append)
    assert count == 1
    assert "138****5678" in sent["upserted"][0]
    assert "13812345678" not in sent["embedded"][0]
    assert hits == [["phone"]]


def test_ingest_audits_redact_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """脱敏命中应写入 audit_logs（action=redact，资源=doc_id）。"""
    from sqlalchemy import select

    from app.ingestion import pipeline
    from app.models import AuditLog

    def fake_sync(path, doc_id, **kw):  # noqa: ANN001, ANN002
        kw["on_redact_hit"](["phone", "email"])
        return 2

    monkeypatch.setattr(pipeline, "ingest_document_sync", fake_sync)

    async def run():
        from app.db import session_factory

        async with session_factory() as session:
            await pipeline.ingest_document(session, Path("p.md"), "DOCP", uploaded_by="tester")
            rows = (
                (await session.execute(select(AuditLog).where(AuditLog.action == "redact")))
                .scalars()
                .all()
            )
            return rows

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0].resource == "DOCP"
    assert "phone" in rows[0].detail and "email" in rows[0].detail
    assert rows[0].user == "tester"
