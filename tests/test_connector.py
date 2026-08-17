"""Connector 定时摄入测试（FR-08，P2 W3）：扫描 → 指纹去重 → 入队闭环。

依赖注入：enqueue / UPLOAD_DIR / 配置文件路径均为显式参数或模块属性，
monkeypatch 后不影响真实环境；数据库走 conftest SQLite 覆盖。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.ingestion import connector
from app.ingestion.connector import _doc_id_for, load_config, scan_once


def _write_config(path: Path, dirs: list[dict]) -> Path:
    cfg = path / "connectors.json"
    cfg.write_text(json.dumps(dirs, ensure_ascii=False), encoding="utf-8")
    return cfg


async def _clean_records() -> None:
    from sqlalchemy import text

    from app.db import session_factory

    async with session_factory() as session:
        await session.execute(text("DELETE FROM ingested_files"))
        await session.commit()


def test_load_config_missing_file() -> None:
    assert load_config(Path("no/such/config.json")) == []


def test_load_config_filters_invalid_items(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text(
        json.dumps([{"path": "D:/a"}, {"path": ""}, {"nopath": 1}]), encoding="utf-8"
    )
    assert [i["path"] for i in load_config(cfg)] == ["D:/a"]


def test_load_config_bad_json(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text("{not json", encoding="utf-8")
    assert load_config(cfg) == []


def test_doc_id_readable() -> None:
    assert _doc_id_for(Path("D:/知识库/研发部/入职手册.pdf")) == "研发部-入职手册"


def test_scan_once_new_file_enqueued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asyncio.run(_clean_records())
    src_dir = tmp_path / "watch"
    src_dir.mkdir()
    (src_dir / "入职手册.md").write_text("内容", encoding="utf-8")
    cfg = _write_config(tmp_path, [{"path": str(src_dir), "department": "研发部"}])
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(connector, "UPLOAD_DIR", upload_dir)

    calls: list[tuple] = []

    async def fake_enqueue(doc_id, stored_path, department, source_name, username):  # noqa: ANN001, ANN002
        calls.append((doc_id, stored_path, department, source_name, username))

    monkeypatch.setattr("app.ingestion.queue.enqueue", fake_enqueue)
    stats = asyncio.run(scan_once(config_path=cfg))

    assert stats["new"] == 1 and stats["scanned"] == 1 and stats["failed"] == 0
    doc_id, stored, dept, name, user = calls[0]
    assert doc_id == "watch-入职手册"
    assert stored.exists() and stored in upload_dir.iterdir()  # 复制到上传目录
    assert dept == "研发部" and user == "connector"
    assert name == "入职手册.md"


def test_scan_once_skips_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """指纹未变 → 跳过，不重复入队。"""
    asyncio.run(_clean_records())
    src_dir = tmp_path / "watch"
    src_dir.mkdir()
    f = src_dir / "a.md"
    f.write_text("v1", encoding="utf-8")
    cfg = _write_config(tmp_path, [{"path": str(src_dir)}])
    monkeypatch.setattr(connector, "UPLOAD_DIR", tmp_path / "uploads")

    calls: list[tuple] = []

    async def fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(args)

    monkeypatch.setattr("app.ingestion.queue.enqueue", fake_enqueue)
    first = asyncio.run(scan_once(config_path=cfg))
    calls.clear()
    second = asyncio.run(scan_once(config_path=cfg))
    assert first["new"] == 1
    assert second["skipped"] == 1 and second["new"] == 0
    assert calls == []


def test_scan_once_reingests_changed_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """文件变更（size 变化）→ 重新入队（updated）。"""
    asyncio.run(_clean_records())
    src_dir = tmp_path / "watch"
    src_dir.mkdir()
    f = src_dir / "a.md"
    f.write_text("v1", encoding="utf-8")
    cfg = _write_config(tmp_path, [{"path": str(src_dir)}])
    monkeypatch.setattr(connector, "UPLOAD_DIR", tmp_path / "uploads")

    calls: list[tuple] = []

    async def fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(args)

    monkeypatch.setattr("app.ingestion.queue.enqueue", fake_enqueue)
    asyncio.run(scan_once(config_path=cfg))

    f.write_text("v2 much longer content", encoding="utf-8")
    calls.clear()
    second = asyncio.run(scan_once(config_path=cfg))
    assert second["updated"] == 1
    assert len(calls) == 1  # 重新入队


def test_scan_once_failed_enqueue_not_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """入队失败 → 计入失败且不记录指纹，下轮可重试。"""
    asyncio.run(_clean_records())
    src_dir = tmp_path / "watch"
    src_dir.mkdir()
    (src_dir / "a.md").write_text("v1", encoding="utf-8")
    cfg = _write_config(tmp_path, [{"path": str(src_dir)}])
    monkeypatch.setattr(connector, "UPLOAD_DIR", tmp_path / "uploads")

    def boom(*a, **k):  # noqa: ANN002, ANN003, ANN001
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.ingestion.queue.enqueue", boom)
    first = asyncio.run(scan_once(config_path=cfg))
    assert first["failed"] == 1 and first["new"] == 0

    calls: list[tuple] = []

    async def ok(*a, **k):  # noqa: ANN002, ANN003, ANN001
        calls.append(tuple(a))

    monkeypatch.setattr("app.ingestion.queue.enqueue", ok)
    second = asyncio.run(scan_once(config_path=cfg))
    assert second["new"] == 1 and len(calls) == 1  # 下轮重试成功


def test_scan_once_ignores_unsupported_and_hidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """不支持扩展名 / 隐藏文件 / Office 锁文件不扫描不记录。"""
    asyncio.run(_clean_records())
    src_dir = tmp_path / "watch"
    src_dir.mkdir()
    (src_dir / "image.png").write_bytes(b"\x89PNG")
    (src_dir / ".hidden.txt").write_text("x", encoding="utf-8")
    (src_dir / "~$lock.docx").write_text("", encoding="utf-8")
    (src_dir / "valid.md").write_text("ok", encoding="utf-8")
    cfg = _write_config(tmp_path, [{"path": str(src_dir)}])
    monkeypatch.setattr(connector, "UPLOAD_DIR", tmp_path / "uploads")
    calls: list[tuple] = []

    async def fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(args)

    monkeypatch.setattr("app.ingestion.queue.enqueue", fake_enqueue)
    stats = asyncio.run(scan_once(config_path=cfg))
    assert stats["scanned"] == 1  # 仅 valid.md
    assert len(calls) == 1
    assert calls[0][3] == "valid.md"


def test_scan_once_dir_missing_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _write_config(tmp_path, [{"path": str(tmp_path / "no_such_dir")}])
    monkeypatch.setattr(connector, "UPLOAD_DIR", tmp_path / "uploads")
    stats = asyncio.run(scan_once(config_path=cfg))
    assert stats["scanned"] == 0
    assert any("目录不存在" in e for e in stats["errors"])


def test_scan_once_no_config(tmp_path: Path) -> None:
    stats = asyncio.run(scan_once(config_path=tmp_path / "absent.json"))
    assert stats["scanned"] == 0