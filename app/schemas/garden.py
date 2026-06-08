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


class GardenSourceOut(BaseModel):
    cntntsNo: str
    title: str


class GardenSummaryOut(BaseModel):
    """텃밭가꾸기 본문을 GPT로 요약한 재배 핵심 정리."""

    crop: str  # 매칭된 작물명(또는 검색어)
    headline: str  # 한 줄 요약
    keyPoints: list[str]  # 핵심 포인트 6~8개
    sources: list[GardenSourceOut]  # 요약 근거가 된 원문 글
    mode: str  # 'garden'(텃밭 본문 기반) | 'general'(작물명 일반지식 폴백)
