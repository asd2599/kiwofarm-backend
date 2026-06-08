"""공통 API 의존성 — 사용자 식별 (자체 JWT 또는 게스트).

식별 우선순위:
1. ``Authorization: Bearer <JWT>`` — /auth/signup·login 이 발급한 자체 토큰.
   검증 통과 시 "user:{id}" 를 식별자로 사용. 만료·위조면 401.
2. ``X-Device-Id`` 헤더 — 테스트·스크립트용 수동 식별.
3. 없음(비로그인) → 게스트. **게스트는 전원이 'demo' 공용 데이터를 공유**하며,
   영농캘린더·도감 시드(scripts/seed_demo_*)가 미리 들어있는 체험 계정이다.

식별자는 farm_plan/harvest_record 의 device_id 컬럼에 그대로 저장된다.

주의: <img src> 요청은 헤더를 못 보내므로 사진 바이트 서빙(memo-images,
/uploads)은 식별 검사를 하지 않는다.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.core.auth import verify_token

log = logging.getLogger(__name__)

# 게스트(비로그인) 공용 식별자 = 데모 시드 계정.
GUEST_DEVICE_ID = "demo"
DEMO_DEVICE_ID = GUEST_DEVICE_ID  # 하위 호환(시드 스크립트가 참조)


async def get_device_id(
    authorization: Annotated[str | None, Header()] = None,
    x_device_id: Annotated[str | None, Header()] = None,
) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        try:
            return verify_token(authorization.split(" ", 1)[1].strip())
        except Exception as e:  # noqa: BLE001 - 만료·위조 토큰을 한 응답으로 수렴
            log.info("토큰 검증 실패: %s", e)
            raise HTTPException(
                status_code=401, detail="로그인이 만료되었습니다. 다시 로그인해 주세요."
            ) from e
    if x_device_id and x_device_id.strip():
        return x_device_id.strip()[:64]
    return GUEST_DEVICE_ID


DeviceDep = Annotated[str, Depends(get_device_id)]
