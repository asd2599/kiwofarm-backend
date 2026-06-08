"""텃밭가꾸기(재배 정보) API (/api/v1/garden).

농사로 fildMnfct(텃밭가꾸기) 콘텐츠를 검색·상세 조회한다. KAMIS 코드 없이
작물명/키워드로 글을 찾고 본문을 그대로 보여주는 재배 정보 화면용.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.core.crops.summary import SummaryError
from app.core.garden_summary import summarize_garden
from app.data import nongsaro_garden as garden
from app.schemas.garden import (
    GardenDetailOut,
    GardenItemOut,
    GardenSourceOut,
    GardenSummaryOut,
)

router = APIRouter(prefix="/garden", tags=["garden"])


@router.get("/search", response_model=list[GardenItemOut])
async def search_garden(
    q: Annotated[str, Query(min_length=1, description="제목 검색어(작물명/키워드)")],
) -> list[GardenItemOut]:
    """제목에 검색어가 든 텃밭가꾸기 글 목록."""
    items = await garden.search_articles(q)
    return [
        GardenItemOut(
            cntntsNo=it.cntnts_no,
            title=it.title,
            seCode=it.se_code,
            seName=garden.SE_CODES.get(it.se_code, ""),
        )
        for it in items
    ]


@router.get("/summary", response_model=GardenSummaryOut)
async def garden_summary(
    q: Annotated[str, Query(min_length=1, description="작물명/키워드")],
) -> GardenSummaryOut:
    """텃밭가꾸기 본문을 GPT로 요약한 재배 핵심 정리 + 근거 원문 목록."""
    crop = q.strip()
    try:
        headline, points, mode = await summarize_garden(crop)
    except SummaryError as e:
        raise HTTPException(status_code=503, detail=f"요약 생성 실패: {e}") from e
    sources = await garden.search_articles(crop, limit=6)
    return GardenSummaryOut(
        crop=crop,
        headline=headline,
        keyPoints=points,
        sources=[
            GardenSourceOut(cntntsNo=s.cntnts_no, title=s.title) for s in sources
        ],
        mode=mode,
    )


@router.get("/{cntnts_no}", response_model=GardenDetailOut)
async def garden_detail(cntnts_no: str) -> GardenDetailOut:
    """텃밭가꾸기 글 상세(본문 + 첨부 링크)."""
    art = await garden.fetch_detail(cntnts_no)
    if art is None:
        raise HTTPException(status_code=404, detail="해당 텃밭가꾸기 글을 찾을 수 없습니다.")
    return GardenDetailOut(
        cntntsNo=art.cntnts_no,
        title=art.title,
        body=art.body,
        downUrl=art.down_url or None,
        fileName=art.file_name or None,
    )
