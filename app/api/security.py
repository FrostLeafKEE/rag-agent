"""安全模块：密码哈希（bcrypt）与 JWT 签发/校验（pyjwt）。

Token payload：sub=用户ID、username、role、department、exp。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings


class SecurityError(Exception):
    """认证/授权失败（凭据错误、token 无效等）。"""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user: dict) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "department": user["department"],
        "exp": datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """解析并校验 token；无效/过期抛 SecurityError。"""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as e:
        raise SecurityError("token 已过期") from e
    except jwt.InvalidTokenError as e:
        raise SecurityError("token 无效") from e
    return payload
