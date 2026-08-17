"""模型网关单元测试：mock HTTP 层，不实际调用外部 API。"""


import pytest

from app.llm.gateway import LLMError, LLMGateway


@pytest.fixture()
def gateway() -> LLMGateway:
    return LLMGateway("https://fake.example/v1", "test-key", "test-model", 0.1)


class FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self) -> dict:
        return self._json or {}


def test_complete_parses_content(gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*args, **kwargs):  # noqa: ANN001, ARG001
        payload = kwargs["json"]
        assert payload["stream"] is False
        assert payload["messages"] == [{"role": "user", "content": "hi"}]
        return FakeResponse(
            200,
            {"choices": [{"message": {"content": "你好"}}]},
        )

    monkeypatch.setattr("app.llm.gateway.httpx.post", fake_post)
    assert gateway.complete([{"role": "user", "content": "hi"}]) == "你好"


def test_complete_raises_on_error(gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.llm.gateway.httpx.post",
        lambda *a, **k: FakeResponse(401, text="unauthorized"),
    )
    with pytest.raises(LLMError):
        gateway.complete([{"role": "user", "content": "hi"}])


def test_stream_yields_deltas(gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.status_code = 200

        async def __aenter__(self) -> "FakeStream":
            return self

        async def __aexit__(self, *args) -> None:  # noqa: ANN002
            return None

        def aiter_lines(self):  # noqa: ANN201
            chunks = [
                'data: {"choices":[{"delta":{"content":"你"}}]}',
                'data: {"choices":[{"delta":{"content":"好"}}]}',
                "data: [DONE]",
            ]

            async def gen():
                for c in chunks:
                    yield c

            return gen()

        async def aread(self) -> bytes:
            return b""

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args) -> None:  # noqa: ANN002
            return None

        def stream(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            return FakeStream()

    monkeypatch.setattr("app.llm.gateway.httpx.AsyncClient", FakeClient)

    async def run() -> str:
        return "".join([p async for p in gateway.stream([{"role": "user", "content": "hi"}])])

    import asyncio

    assert asyncio.run(run()) == "你好"


def test_llm_error_contains_status(gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.llm.gateway.httpx.post",
        lambda *a, **k: FakeResponse(500, text="boom"),
    )
    with pytest.raises(LLMError, match="500"):
        gateway.complete([{"role": "user", "content": "hi"}])
