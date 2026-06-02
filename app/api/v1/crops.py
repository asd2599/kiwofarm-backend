from fastapi import APIRouter, HTTPException, Query

from app.core.crops import summary as crop_summary
from app.data import kamis_crops, nongsaro
from app.schemas.crops import (
    CropOption,
    CropSummary,
    CultivationGuide,
    EbookEntry,
    EbookIndex,
)

router = APIRouter(prefix="/crops", tags=["crops"])


def _to_option(row: kamis_crops.CropRecord) -> CropOption:
    return CropOption(
        group_code=row["groupCode"],
        group_name=row["groupName"],
        item_code=row["itemCode"],
        item_name=row["itemName"],
        kind_code=row["kindCode"],
        kind_name=row["kindName"],
        label=row["label"],
    )


@router.get("/search", response_model=list[CropOption])
async def search_crops(
    q: str = Query(..., min_length=1, description="품목/품종/부류명 부분일치"),
    limit: int = Query(10, ge=1, le=50),
) -> list[CropOption]:
    return [_to_option(r) for r in kamis_crops.search(q, limit=limit)]


@router.get("/{item_code}/{kind_code}/cultivation", response_model=CultivationGuide)
async def get_cultivation(item_code: str, kind_code: str) -> CultivationGuide:
    """KAMIS 코드 → 농사로 (신)작목별농업기술정보 (cropEbook 서비스).

    흐름:
      1. KAMIS 시드에서 itemName/groupName 조회
      2. 농사로 카테고리 트리 순회로 subCategory 매칭
      3. ebookList 로 농업기술길잡이 목록 수집
      4. cropIndexList 로 각 길잡이의 목차 + EBOOK URL 수집
      5. CultivationGuide 로 정규화 (텍스트 본문 X, e-book 메타만)
    """
    row = kamis_crops.get_by_codes(item_code, kind_code)
    if row is None:
        raise HTTPException(status_code=404, detail="해당 KAMIS 작목 코드가 없습니다.")

    crop_name = row["itemName"]
    group_name = row["groupName"]

    try:
        match = await nongsaro.find_sub_category(crop_name, kamis_group_name=group_name)
    except nongsaro.NongsaroError as e:
        raise HTTPException(status_code=503, detail=f"농사로 카테고리 조회 실패: {e}") from e

    if not match:
        return CultivationGuide(
            item_code=item_code,
            kind_code=kind_code,
            crop_name=row["label"],
            sub_category_name=None,
            ebooks=[],
            source=f"농사로에서 작목명 '{crop_name}' 매칭 결과 없음 (group={group_name})",
        )

    try:
        ebook_entries = await nongsaro.fetch_ebook_list(match.sub_code)
    except nongsaro.NongsaroError as e:
        raise HTTPException(status_code=503, detail=f"농사로 길잡이 목록 실패: {e}") from e

    ebooks: list[EbookEntry] = []
    for e in ebook_entries:
        try:
            idx_entries = await nongsaro.fetch_crop_index_list(e.ebook_code, e.file_no)
        except nongsaro.NongsaroError:
            # 목차 실패해도 책 자체는 표시
            idx_entries = []

        ebook_url = next((i.ebook_url for i in idx_entries if i.ebook_url), None)
        ebook_mobile_url = next(
            (i.ebook_mobile_url for i in idx_entries if i.ebook_mobile_url), None
        )
        indices = [
            EbookIndex(
                name=i.name,
                page=i.page,
                base_page=i.base_page,
                level=i.level,
                order=i.order,
            )
            for i in idx_entries
        ]
        ebooks.append(
            EbookEntry(
                ebook_code=e.ebook_code,
                ebook_name=e.ebook_name,
                file_no=e.file_no,
                file_url=e.file_url,
                orginl_file_nm=e.orginl_file_nm,
                thumbnail_code=e.thumbnail_code,
                thumbnail_name=e.thumbnail_name,
                std_item_code=e.std_item_code,
                std_item_name=e.std_item_name,
                ebook_url=ebook_url,
                ebook_mobile_url=ebook_mobile_url,
                indices=indices,
            )
        )

    return CultivationGuide(
        item_code=item_code,
        kind_code=kind_code,
        crop_name=row["label"],
        sub_category_name=match.sub_name,
        ebooks=ebooks,
        source=(
            f"농사로 cropEbook / {match.main_name} > {match.middle_name} > {match.sub_name}"
        ),
    )


@router.get("/{item_code}/{kind_code}/summary", response_model=CropSummary)
async def get_crop_summary(item_code: str, kind_code: str) -> CropSummary:
    """농업기술길잡이 → RAG 기반 GPT 키포인트 요약.

    계획 생성과 같은 RAG 파이프라인(농사로 PDF → 청크 → 임베딩 → pgvector 검색)으로
    요약에 필요한 청크만 추려 GPT 로 보냅니다. 첫 호출은 PDF 다운+임베딩+GPT 로
    길어지고(10~30초), 결과는 메모리 캐시. e-book 메타는 출처 표시용으로 함께 조회.
    """
    row = kamis_crops.get_by_codes(item_code, kind_code)
    if row is None:
        raise HTTPException(status_code=404, detail="해당 KAMIS 작목 코드가 없습니다.")

    try:
        match = await nongsaro.find_sub_category(
            row["itemName"], kamis_group_name=row["groupName"]
        )
    except nongsaro.NongsaroError as e:
        raise HTTPException(status_code=503, detail=f"농사로 카테고리 조회 실패: {e}") from e

    if not match:
        raise HTTPException(
            status_code=404,
            detail=f"농사로에서 작목 '{row['itemName']}' 매칭 결과 없음",
        )

    try:
        ebook_entries = await nongsaro.fetch_ebook_list(match.sub_code)
    except nongsaro.NongsaroError as e:
        raise HTTPException(status_code=503, detail=f"농사로 길잡이 목록 실패: {e}") from e

    # PDF 가 있는 첫 길잡이 우선, 없으면 첫 길잡이 (메타만 표시), 둘 다 없으면 None.
    first = next((e for e in ebook_entries if e.file_url), None)
    if first is None and ebook_entries:
        first = ebook_entries[0]

    try:
        result = await crop_summary.build_summary(
            item_code=item_code,
            kind_code=kind_code,
            crop_name=row["itemName"],  # 농사로 소분류 매칭·RAG 인제스트 기준명
            sub_category_name=match.sub_name,
            ebook_code=first.ebook_code if first else None,
            ebook_name=first.ebook_name if first else None,
            file_url=first.file_url if first else None,
            group_name=row["groupName"],
        )
    except crop_summary.SummaryError as e:
        raise HTTPException(status_code=503, detail=f"요약 생성 실패: {e}") from e

    return CropSummary(
        item_code=result.item_code,
        kind_code=result.kind_code,
        crop_name=result.crop_name,
        headline=result.headline,
        key_points=result.key_points,
        source_ebook_code=result.source_ebook_code,
        source_ebook_name=result.source_ebook_name,
        source_file_url=result.source_file_url,
        text_chars=result.text_chars,
        mode=result.mode,
    )
