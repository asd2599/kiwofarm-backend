"""앱 사용자 — 아이디+비밀번호 자체 인증 (마이그레이션 0009).

비밀번호는 scrypt(stdlib) 해시로 저장. 식별자는 "user:{id}" 형태로
farm_plan/harvest_record.device_id 컬럼에 들어간다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    # 커뮤니티 피드 표시 이름. 가입 시 입력(필수). 구 계정 보호를 위해 nullable —
    # 비면 username 으로 폴백한다.
    nickname: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 주소(선택). 시·군·구까지만 "경기도 성남시" 형태. 작목 추천 지역 프리필용.
    address_sigungu: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
