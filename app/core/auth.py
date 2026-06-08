"""자체 인증 — scrypt 비밀번호 해시 + HS256 JWT 발급/검증.

베타 정책: 아이디 1~32자, 비밀번호 제약 없음(1자 이상). 토큰 유효 30일.
시크릿은 settings.auth_secret (운영은 fly secret AUTH_SECRET 로 교체).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from app.config import settings

TOKEN_DAYS = 30
_ISS = "kiwofarm"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt.encode(), n=2**14, r=8, p=1
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
        cand = hashlib.scrypt(
            password.encode(), salt=salt.encode(), n=2**14, r=8, p=1
        ).hex()
        return secrets.compare_digest(cand, digest)
    except Exception:  # noqa: BLE001 - 형식 불량 해시는 불일치로 처리
        return False


def issue_token(user_id: int, username: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": _ISS,
            "sub": f"user:{user_id}",
            "username": username,
            "iat": now,
            "exp": now + timedelta(days=TOKEN_DAYS),
        },
        settings.auth_secret,
        algorithm="HS256",
    )


def verify_token(token: str) -> str:
    """토큰 → 식별자("user:{id}"). 만료·위조면 jwt 예외."""
    claims = jwt.decode(
        token, settings.auth_secret, algorithms=["HS256"], issuer=_ISS
    )
    return str(claims["sub"])[:64]
