"""텃밭가꾸기(재배 정보) 스키마."""

from __future__ import annotations

from pydantic import BaseModel


class GardenItemOut(BaseModel):
    cntntsNo: str
    title: str
    seCode: str
    seName: str  # 분류명(채소·허브·텃밭일반 / 과수 / 특용작물)


class GardenDetailOut(BaseModel):
    cntntsNo: str
    title: str
    body: str  # 본문 평문
    downUrl: str | None = None  # 첨부 원문 다운로드 URL
    fileName: str | None = None
