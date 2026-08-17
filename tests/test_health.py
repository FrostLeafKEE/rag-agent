"""应用骨架冒烟测试。"""

from fastapi.testclient import TestClient

from app.main import app


def test_healthz() -> None:
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "rag-enterprise"


def test_docs_available_in_debug() -> None:
    client = TestClient(app)
    resp = client.get("/docs")
    assert resp.status_code == 200
