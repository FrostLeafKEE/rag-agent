"""健康检查与探针。"""

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter()

settings = get_settings()


@router.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "version": "0.1.0",
    }
