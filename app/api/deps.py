"""공통 API 의존성 — 사용자 식별 (Supabase Auth JWT 또는 게스트).

식별 우선순위:
1. ``Authorization: Bearer <Supabase JWT>`` — JWKS(ES256)로 서명 검증 후
   user id(sub)를 식별자로 사용. 만료·위조면 401.
2. ``X-Device-Id`` 헤더 — 테스트·스크립트용 수동 식별.
3. 없음(비로그인) → 게스트. **게스트는 전원이 'demo' 공용 데이터를 공유**하며,
   영농캘린더·도감 시드(scripts/seed_demo_*)가 미리 들어있는 체험 계정이다.

식별자는 farm_plan/harvest_record 의 device_id 컬럼에 그대로 저장된다
(로그인 사용자는 Supabase user UUID).

주의: <img src> 요청은 헤더를 못 보내므로 사진 바이트 서빙(memo-images,
/uploads)은 식별 검사를 하지 않는다.
"""

from __future__ import annotations

import logging
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient

from app.config import settings

log = logging.getLogger(__name__)

# 게스트(비로그인) 공용 식별자 = 데모 시드 계정.
GUEST_DEVICE_ID = "demo"
DEMO_DEVICE_ID = GUEST_DEVICE_ID  # 하위 호환(시드 스크립트가 참조)

_jwks_client: PyJWKClient | None = None


def _jwks() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            f"{settings.supabase_url}/auth/v1/.well-known/jwks.json",
            cache_keys=True,
        )
    return _jwks_client


def _verify_supabase_jwt(token: str) -> str:
    """Supabase JWT 서명·만료 검증 → user id(sub). 실패 시 HTTPException(401)."""
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
        sub = str(claims.get("sub", "")).strip()
        if not sub:
            raise ValueError("sub 클레임 없음")
        return sub[:64]
    except HTTPException:
        raise
    except Exception as e:
        log.info("JWT 검증 실패: %s", e)
        raise HTTPException(
            status_code=401, detail="로그인이 만료되었습니다. 다시 로그인해 주세요."
        ) from e


async def get_device_id(
    authorization: Annotated[str | None, Header()] = None,
    x_device_id: Annotated[str | None, Header()] = None,
) -> str:
    if (
        authorization
        and authorization.lower().startswith("bearer ")
        and settings.supabase_url
    ):
        return _verify_supabase_jwt(authorization.split(" ", 1)[1].strip())
    if x_device_id and x_device_id.strip():
        return x_device_id.strip()[:64]
    return GUEST_DEVICE_ID


DeviceDep = Annotated[str, Depends(get_device_id)]
