"""공통 API 의존성 — 익명 디바이스 ID.

계정 체계 도입 전 사용자 구분: 프론트(lib/deviceId.ts)가 localStorage UUID 를
``X-Device-Id`` 헤더로 보낸다. 헤더가 없으면(curl·스크립트) 'anonymous'.
'demo' 는 시연 계정 — 시드 데이터(scripts/seed_demo_*)가 이 ID 로 들어간다.

주의: <img src> 요청은 헤더를 못 보내므로 사진 바이트 서빙(memo-images,
/uploads)은 디바이스 검사를 하지 않는다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

DEMO_DEVICE_ID = "demo"


async def get_device_id(
    x_device_id: Annotated[str | None, Header()] = None,
) -> str:
    return (x_device_id or "").strip()[:64] or "anonymous"


DeviceDep = Annotated[str, Depends(get_device_id)]
