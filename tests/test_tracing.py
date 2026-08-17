"""Trace 模块单元测试：无 key 降级 noop；有 key 时创建 Langfuse trace。"""

import asyncio

import pytest

from app.config import Settings
from app.observability import tracing


class FakeLLM:
    def __init__(self, pieces: list[str]) -> None:
        self._pieces = pieces

    async def stream(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
        assert messages
        for p in self._pieces:
            yield p


def test_noop_trace_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_client", None)
    settings = Settings(
        langfuse_public_key="", langfuse_secret_key="", langfuse_host="http://localhost:3000"
    )
    monkeypatch.setattr("app.observability.tracing.get_settings", lambda: settings)

    async def run() -> list[str]:
        async with tracing.trace_qa("问题", None, 5) as t:
            assert t.trace_id is None
            llm = FakeLLM(["a", "b"])
            return [p async for p in t.llm_stream(llm, [{"role": "user", "content": "hi"}])]

    assert asyncio.run(run()) == ["a", "b"]


def test_trace_with_key_creates_langfuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_client", None)
    settings = Settings(
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        langfuse_host="http://localhost:3000",
    )
    monkeypatch.setattr("app.observability.tracing.get_settings", lambda: settings)

    calls: dict = {}

    class FakeSpan:
        def __init__(self, name: str) -> None:
            self.id = f"span-{name}"

        def end(self) -> None:
            calls.setdefault("ended", []).append(self.id)

        def update(self, **kwargs) -> None:  # noqa: ANN003
            calls.setdefault("updated", []).append(kwargs)

    class FakeLangfuse:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            calls["init"] = kwargs

        def create_trace_id(self) -> str:
            return "trace-123"

        def start_observation(self, **kwargs):  # noqa: ANN003
            calls["observation"] = kwargs
            return FakeSpan(kwargs["name"])

        def flush(self) -> None:
            calls["flushed"] = True

    monkeypatch.setattr(tracing, "Langfuse", FakeLangfuse)

    async def run() -> None:
        async with tracing.trace_qa("问题", ["研发部"], 3) as t:
            assert t.trace_id == "trace-123"
            assert isinstance(t, tracing._LangfuseQATrace)  # noqa: SLF001

    asyncio.run(run())
    assert calls["init"]["public_key"] == "pk-test"
    assert calls["flushed"] is True
    obs = calls["observation"]
    assert obs["name"] == "qa"
    assert obs["input"]["question"] == "问题"
    assert obs["trace_context"]["trace_id"] == "trace-123"
    assert calls["ended"] == ["span-qa"]  # qa span 正常关闭
