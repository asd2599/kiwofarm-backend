from pydantic import BaseModel


class CropOption(BaseModel):
    """KAMIS 부류·품목·품종 코드 단위 검색 결과."""

    group_code: str
    group_name: str
    item_code: str
    item_name: str
    kind_code: str
    kind_name: str
    label: str


class CropCatalogOption(BaseModel):
    """작목별농업기술정보(cropEbook) 소분류 = 작목. 캘린더 작물 검색 결과."""

    code: str  # subCategoryCode
    name: str  # subCategoryNm (작목명)
    category: str  # mainCategoryNm (대분류명)


class EbookIndex(BaseModel):
    """농업기술길잡이 e-book 의 한 목차 항목."""

    name: str
    page: int
    base_page: int = 0
    level: int = 0
    order: int = 0


class EbookEntry(BaseModel):
    """농업기술길잡이 e-book PDF 메타."""

    ebook_code: str
    ebook_name: str
    file_no: str
    file_url: str | None = None
    orginl_file_nm: str | None = None
    thumbnail_code: str | None = None
    thumbnail_name: str | None = None
    std_item_code: str | None = None
    std_item_name: str | None = None
    ebook_url: str | None = None
    ebook_mobile_url: str | None = None
    indices: list[EbookIndex] = []


class CultivationGuide(BaseModel):
    """KAMIS 코드 → 농사로 (신)작목별농업기술정보 정규화 응답.

    농사로는 텍스트 본문 대신 e-book PDF 메타와 목차를 제공합니다.
    """

    item_code: str
    kind_code: str
    crop_name: str
    sub_category_name: str | None = None
    ebooks: list[EbookEntry] = []
    source: str | None = None
    updated_at: str | None = None


class CropSummary(BaseModel):
    """농업기술길잡이 PDF → GPT 키포인트 요약 (PDF 막힌 경우 일반 지식 fallback)."""

    item_code: str
    kind_code: str
    crop_name: str
    headline: str
    key_points: list[str]
    source_ebook_code: str | None = None
    source_ebook_name: str | None = None
    source_file_url: str | None = None
    text_chars: int = 0
    mode: str = "pdf"  # "pdf" | "general"
