"""FastAPI 应用入口。

启动：uv run uvicorn app.main:app --reload
- API 文档：/docs
- 问答页面：/ui
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import admin, auth, documents, health, qa, sessions
from app.config import get_settings
from app.db import init_db
from app.observability.metrics import metrics_middleware, metrics_response

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="RAG Enterprise API",
    version="0.1.0",
    debug=settings.debug,
    docs_url="/docs" if settings.debug else None,
    lifespan=lifespan,
)

app.middleware("http")(metrics_middleware)


@app.middleware("http")
async def security_headers(request, call_next):
    """安全响应头（R2/ROADMAP）：防 MIME 嗅探 / 点击劫持 / 泄露来源。"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


app.include_router(health.router, tags=["health"])
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(sessions.router)
app.include_router(qa.router, tags=["qa"])


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return metrics_response()


# Vue3 管理台（构建产物；hash 路由无需 SPA fallback）
_frontend = Path(__file__).parent.parent / "web-ui" / "dist"
if _frontend.exists():
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
    app.mount("/ui", StaticFiles(directory=_frontend, html=True), name="ui")
