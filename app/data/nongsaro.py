"""농사로 (신)작목별 농업기술정보 클라이언트.

서비스명: cropEbook (REST/XML).
인증키: settings.nongsaro_api_key.

이 API는 텍스트 본문을 직접 주지 않고 5단계 카테고리 트리를 순회한 끝에
"농업기술길잡이 e-book PDF" 메타데이터(파일 URL·웹뷰어 URL·목차)를 반환합니다.

흐름 (작목명 → 길잡이 목록):
  1) mainCategoryList               대분류 (식량작물·채소·과수·…)
  2) middleCategoryList(mainCode)   중분류 (벼·맥류·…)
  3) subCategoryList(middleCode)    소분류 = 작목 (감자·고구마·…)  ← 작목명 매칭 지점
  4) ebookList(subCode)             길잡이 e-book 목록
  5) cropIndexList(ebookCode,fileNo) 길잡이 목차

KAMIS groupName 으로 1단계(mainCategory)를 좁히고, KAMIS itemName 으로
3단계(subCategory)를 매칭합니다.
"""

from __future__ import annotations

import io
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx
from pypdf import PdfReader

from app.config import settings

log = logging.getLogger(__name__)

NONGSARO_BASE = "http://api.nongsaro.go.kr/service/cropEbook"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class NongsaroError(RuntimeError):
    """농사로 API 호출 실패 (네트워크·인증·응답 파싱·결과코드 어느 단계든)."""


# KAMIS groupName → 농사로 mainCategoryNm 사전 매핑.
# 농사로 대분류: 식량작물·특용작물·채소·과수·화훼·축산·녹비작물·곤충·작물보호·토양비료·기타
KAMIS_TO_NONGSARO_MAIN: dict[str, str] = {
    "식량작물": "식량작물",
    "특용작물": "특용작물",
    "채소류": "채소",
    "과일류": "과수",
    "축산물": "축산",
}


# ─────────────────────── 병해충 (placeholder 유지) ───────────────────────


@dataclass(frozen=True)
class PestRisk:
    crop_id: str
    pest_name: str
    month: int
    severity: str
    control_note: str


async def fetch_pest_risks(crop_id: str) -> list[PestRisk]:
    """디지털 트윈 위기 알림용. 농사로 별도 서비스 (병해충) 연결 전 placeholder."""
    del crop_id
    return []


# ─────────────────────────── HTTP 헬퍼 ───────────────────────────


async def _call(operation: str, params: dict[str, Any]) -> ET.Element:
    if not settings.nongsaro_api_key:
        raise NongsaroError("nongsaro_api_key 가 설정되지 않았습니다 (.env 확인)")

    merged = {"apiKey": settings.nongsaro_api_key, **params}
    url = f"{NONGSARO_BASE}/{operation}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, params=merged)
    except httpx.HTTPError as e:
        raise NongsaroError(f"네트워크 오류: {e}") from e

    if resp.status_code != 200:
        raise NongsaroError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise NongsaroError(f"XML 파싱 실패: {e}") from e

    result_code = root.findtext(".//header/resultCode") or ""
    if result_code and result_code != "00":
        msg = root.findtext(".//header/resultMsg") or ""
        raise NongsaroError(
            f"농사로 응답 오류 resultCode={result_code} resultMsg={msg} op={operation}"
        )
    return root


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _items(root: ET.Element) -> list[ET.Element]:
    return root.findall(".//body/items/item")


def _to_int(s: str, default: int = 0) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


# ─────────────────────────── 캐시 (모듈 레벨, 1시간 TTL) ───────────────────────────
#
# 카테고리 트리는 거의 변하지 않지만, 장시간 떠 있는 프로세스가 영구히 stale 한
# 값을 들고 있지 않도록 1시간 TTL 을 둔다. 값은 (적재시각, payload) 튜플로 보관.

CATEGORY_TTL = 3600.0  # 초

_main_cache: tuple[float, list[tuple[str, str]]] | None = None
_middle_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}
_sub_cache: dict[tuple[str, str | None], tuple[float, list[tuple[str, str]]]] = {}


def _fresh(stamped: tuple[float, Any] | None) -> bool:
    return stamped is not None and (time.monotonic() - stamped[0]) < CATEGORY_TTL


# ─────────────────────────── 카테고리 호출 ───────────────────────────


async def fetch_main_categories() -> list[tuple[str, str]]:
    global _main_cache
    if _fresh(_main_cache):
        return _main_cache[1]  # type: ignore[index]
    root = await _call("mainCategoryList", {})
    out = [
        (_text(it.find("mainCategoryCode")), _text(it.find("mainCategoryNm")))
        for it in _items(root)
    ]
    fresh = [(c, n) for c, n in out if c]
    _main_cache = (time.monotonic(), fresh)
    return fresh


async def fetch_middle_categories(main_code: str) -> list[tuple[str, str]]:
    if _fresh(_middle_cache.get(main_code)):
        return _middle_cache[main_code][1]
    root = await _call("middleCategoryList", {"mainCategoryCode": main_code})
    out = [
        (_text(it.find("middleCategoryCode")), _text(it.find("middleCategoryNm")))
        for it in _items(root)
    ]
    out = [(c, n) for c, n in out if c]
    _middle_cache[main_code] = (time.monotonic(), out)
    return out


async def fetch_sub_categories(
    middle_code: str, name: str | None = None
) -> list[tuple[str, str]]:
    key = (middle_code, name)
    if _fresh(_sub_cache.get(key)):
        return _sub_cache[key][1]
    params: dict[str, Any] = {"middleCategoryCode": middle_code}
    if name:
        params["subCategoryNm"] = name
    root = await _call("subCategoryList", params)
    out = [
        (_text(it.find("subCategoryCode")), _text(it.find("subCategoryNm")))
        for it in _items(root)
    ]
    out = [(c, n) for c, n in out if c]
    _sub_cache[key] = (time.monotonic(), out)
    return out


# ─────────────────────────── 길잡이/목차 ───────────────────────────


@dataclass(frozen=True)
class EbookEntry:
    ebook_code: str
    ebook_name: str
    file_no: str
    file_url: str | None  # cropsEbookFile (다운로드 URL)
    orginl_file_nm: str | None
    thumbnail_code: str | None
    thumbnail_name: str | None
    std_item_code: str | None
    std_item_name: str | None


@dataclass(frozen=True)
class EbookIndexEntry:
    name: str
    base_page: int
    page: int
    level: int
    order: int
    ebook_url: str | None  # 첫 행에서 추출 (모든 row 동일)
    ebook_mobile_url: str | None


async def fetch_ebook_list(sub_code: str) -> list[EbookEntry]:
    root = await _call("ebookList", {"subCategoryCode": sub_code})
    out: list[EbookEntry] = []
    for it in _items(root):
        ebook_code = _text(it.find("ebookCode"))
        if not ebook_code:
            continue
        out.append(
            EbookEntry(
                ebook_code=ebook_code,
                ebook_name=_text(it.find("ebookName")),
                file_no=_text(it.find("cropsEbookFileNo")),
                file_url=_text(it.find("cropsEbookFile")) or None,
                orginl_file_nm=_text(it.find("orginlFileNm")) or None,
                thumbnail_code=_text(it.find("atchmnflGroupEsntlEbookCode")) or None,
                thumbnail_name=_text(it.find("atchmnflGroupEsntlEbookNm")) or None,
                std_item_code=_text(it.find("stdItemCd")) or None,
                std_item_name=_text(it.find("stdItemNm")) or None,
            )
        )
    return out


async def fetch_crop_index_list(
    ebook_code: str, file_no: str
) -> list[EbookIndexEntry]:
    root = await _call(
        "cropIndexList",
        {"ebookCode": ebook_code, "cropsEbookFileNo": file_no},
    )
    out: list[EbookIndexEntry] = []
    for it in _items(root):
        name = _text(it.find("indexName"))
        if not name:
            continue
        out.append(
            EbookIndexEntry(
                name=name,
                base_page=_to_int(_text(it.find("indexBasePage"))),
                page=_to_int(_text(it.find("indexPage"))),
                level=_to_int(_text(it.find("indexLevel"))),
                order=_to_int(_text(it.find("indexOrder"))),
                ebook_url=_text(it.find("ebookUrl")) or None,
                ebook_mobile_url=_text(it.find("ebookMobileUrl")) or None,
            )
        )
    return out


# ─────────────────────────── 작목 매칭 ───────────────────────────


@dataclass(frozen=True)
class SubCategoryMatch:
    main_code: str
    main_name: str
    middle_code: str
    middle_name: str
    sub_code: str
    sub_name: str


async def find_sub_category(
    crop_name: str, kamis_group_name: str | None = None
) -> SubCategoryMatch | None:
    """KAMIS itemName 으로 농사로 subCategory 찾기.

    설계 결정:
      - 농사로 subCategoryNm 파라미터로 서버측 필터링이 작동하지 않으므로
        각 middle 의 전체 sub 를 가져온 뒤 메모리에서 매칭.
      - kamis_group_name 매핑이 있는 경우 그 main 만 시도 (호출 폭증 방지).
      - 매핑 미정의 작목 (예: 화훼·곤충·녹비) 은 전체 main 폴백.
    """
    crop_name = crop_name.strip()
    if not crop_name:
        return None

    mains = await fetch_main_categories()
    if not mains:
        return None

    preferred_main_name = (
        KAMIS_TO_NONGSARO_MAIN.get(kamis_group_name, "") if kamis_group_name else ""
    )
    if preferred_main_name:
        # 매핑된 main 하나만 시도
        candidates_main = [mc for mc in mains if mc[1] == preferred_main_name]
    else:
        candidates_main = list(mains)

    for main_code, main_name in candidates_main:
        middles = await fetch_middle_categories(main_code)
        for middle_code, middle_name in middles:
            subs = await fetch_sub_categories(middle_code, name=None)
            match = _pick_best_match(subs, crop_name)
            if match:
                return SubCategoryMatch(
                    main_code, main_name, middle_code, middle_name, *match
                )
    return None


def _pick_best_match(
    candidates: list[tuple[str, str]], crop_name: str
) -> tuple[str, str] | None:
    if not candidates:
        return None
    exact = [c for c in candidates if c[1] == crop_name]
    if exact:
        return exact[0]
    prefix = [c for c in candidates if c[1].startswith(crop_name)]
    if prefix:
        return prefix[0]
    partial = [c for c in candidates if crop_name in c[1]]
    if partial:
        return partial[0]
    return None


# ─────────────────────────── PDF 다운로드 + 텍스트 추출 ───────────────────────────

PDF_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
MAX_PDF_BYTES = 30 * 1024 * 1024  # 30MB 상한 (큰 PDF 메모리 폭주 방지)


async def download_pdf(url: str) -> bytes:
    """농사로 cropsEbookFile URL 에서 PDF 바이트를 받아옴.

    응답이 PDF 매직 넘버 (%PDF) 로 시작하지 않으면 농사로가 HTML 에러/안내
    페이지를 반환한 경우이므로 명시적 NongsaroError 로 실패시켜 호출자가
    fallback 경로로 분기할 수 있게 합니다.
    """
    try:
        async with httpx.AsyncClient(timeout=PDF_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError as e:
        raise NongsaroError(f"PDF 다운로드 실패: {e}") from e
    if resp.status_code != 200:
        raise NongsaroError(f"PDF HTTP {resp.status_code}")
    if len(resp.content) > MAX_PDF_BYTES:
        raise NongsaroError(f"PDF 용량 초과: {len(resp.content)} bytes")
    if not resp.content[:4].startswith(b"%PDF"):
        ctype = resp.headers.get("content-type", "")
        raise NongsaroError(
            f"PDF 가 아닌 응답 (content-type={ctype}, {len(resp.content)} bytes). "
            "농사로 다운로드 endpoint 가 폐쇄됐을 가능성."
        )
    return resp.content


def extract_pdf_text(data: bytes, max_pages: int = 60) -> str:
    """PDF 바이트 → 일반 텍스트. 너무 큰 PDF 는 앞 max_pages 까지만."""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise NongsaroError(f"PDF 파싱 실패: {e}") from e
    chunks: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(chunks).strip()
