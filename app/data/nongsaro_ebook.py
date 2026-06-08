"""농사로 cropEbook 서비스 클라이언트.

매뉴얼: docs/OpenAPI 활용 매뉴얼 - (신)작목별 농업기술정보.hwp
베이스 URL: http://api.nongsaro.go.kr/service/cropEbook/{operation}
인증키 파라미터명: apiKey (settings.nongsaro_api_key)

응답은 UTF-8 XML. resultCode 분기:
  00 정상 (검색 결과 0건 포함)
  11 인증키 누락/미발급
  12 키 일시 중지
  13 미존재 서비스/오퍼레이션
  15 등록 도메인 외 호출 (AJAX 한정 — 백엔드 REST에서는 거의 안 뜸)
  91 시스템 오류
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import httpx

from app.config import settings

BASE_URL = "http://api.nongsaro.go.kr/service/cropEbook"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3


class NongsaroApiError(RuntimeError):
    """resultCode != 00 일 때 발생."""

    def __init__(self, code: str, msg: str, operation: str):
        super().__init__(f"[{operation}] resultCode={code} resultMsg={msg}")
        self.code = code
        self.msg = msg
        self.operation = operation


def _parse_items(xml_text: str, operation: str) -> list[dict[str, str]]:
    """response/body/items/item 또는 response/body/item 을 dict 리스트로.

    농사로는 페이징 응답(items 래퍼)과 단건 응답(item 직속) 두 포맷이 섞여 있다 (매뉴얼 3.3).
    """
    root = ET.fromstring(xml_text)

    header = root.find("header")
    if header is not None:
        code_el = header.find("resultCode")
        msg_el = header.find("resultMsg")
        code = (code_el.text or "").strip() if code_el is not None else ""
        msg = (msg_el.text or "").strip() if msg_el is not None else ""
        if code and code != "00":
            raise NongsaroApiError(code, msg, operation)

    body = root.find("body")
    if body is None:
        return []

    items_wrapper = body.find("items")
    item_elements = (
        items_wrapper.findall("item") if items_wrapper is not None else body.findall("item")
    )

    out: list[dict[str, str]] = []
    for item in item_elements:
        record: dict[str, str] = {}
        for child in item:
            record[child.tag] = (child.text or "").strip()
        out.append(record)
    return out


async def _get(
    operation: str,
    params: Mapping[str, str | int] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, str]]:
    if not settings.nongsaro_api_key:
        raise RuntimeError("NONGSARO_API_KEY 미설정 (.env 확인)")

    query: dict[str, str] = {"apiKey": settings.nongsaro_api_key}
    if params:
        query.update({k: str(v) for k, v in params.items()})

    url = f"{BASE_URL}/{operation}"

    async def _request(c: httpx.AsyncClient) -> list[dict[str, str]]:
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = await c.get(url, params=query, timeout=DEFAULT_TIMEOUT)
                resp.raise_for_status()
                return _parse_items(resp.text, operation)
            except NongsaroApiError as e:
                # 91(시스템 오류)만 재시도, 나머지(11/12/13/15)는 즉시 throw
                if e.code != "91":
                    raise
                last_err = e
            except (httpx.HTTPError, ET.ParseError) as e:
                last_err = e
            await asyncio.sleep(0.5 * (2**attempt))
        assert last_err is not None
        raise last_err

    if client is not None:
        return await _request(client)
    async with httpx.AsyncClient() as c:
        return await _request(c)


# ─────────────────────────── operations ───────────────────────────


async def main_category_list(
    *, client: httpx.AsyncClient | None = None
) -> list[dict[str, str]]:
    """대분류 카테고리. 응답: mainCategoryCode, mainCategoryNm."""
    return await _get("mainCategoryList", client=client)


async def middle_category_list(
    main_category_code: str, *, client: httpx.AsyncClient | None = None
) -> list[dict[str, str]]:
    """중분류 카테고리. 응답: middleCategoryCode, middleCategoryNm."""
    return await _get(
        "middleCategoryList", {"mainCategoryCode": main_category_code}, client=client
    )


async def sub_category_list(
    middle_category_code: str,
    *,
    sub_category_nm: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, str]]:
    """소분류 카테고리. 응답: subCategoryCode, subCategoryNm."""
    params: dict[str, str | int] = {"middleCategoryCode": middle_category_code}
    if sub_category_nm:
        params["subCategoryNm"] = sub_category_nm
    return await _get("subCategoryList", params, client=client)


async def ebook_list(
    sub_category_code: str, *, client: httpx.AsyncClient | None = None
) -> list[dict[str, str]]:
    """농업기술길잡이 목록.

    응답 항목 (매뉴얼 4.2.5):
      atchmnflGroupEsntlEbookCode/Nm, cropsEbookFile, cropsEbookFileNo, orginlFileNm,
      ebookCode, ebookName, stdItemCd, stdItemNm
    """
    return await _get("ebookList", {"subCategoryCode": sub_category_code}, client=client)


async def crop_index_list(
    ebook_code: str,
    crops_ebook_file_no: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, str]]:
    """농업기술길잡이 목차.

    응답 항목 (매뉴얼 4.2.8):
      ebookCode, cropsEbookFileNo, cropsEbookIndexNo, ebookUrl, ebookMobileUrl,
      indexBasePage, indexPage, indexLevel, indexName, indexOrder, indexSid,
      stdItemCd, stdItemNm
    """
    return await _get(
        "cropIndexList",
        {"ebookCode": ebook_code, "cropsEbookFileNo": crops_ebook_file_no},
        client=client,
    )


# ─────────────────── 작목 카탈로그 (대→중→소 순회, 캐시) ───────────────────


@dataclass(frozen=True)
class CropCatalogItem:
    code: str  # subCategoryCode (작목 코드)
    name: str  # subCategoryNm (작목명)
    category: str  # mainCategoryNm (대분류명)


_CATALOG_TTL = 3600.0
_catalog_cache: tuple[float, list[CropCatalogItem]] | None = None
_CONCURRENCY = 8
# 콜드 캐시일 때 동시에 들어온 요청들이 각자 트리를 순회(thundering herd)하지 않도록
# 단일화. 한 코루틴만 외부 API를 순회하고 나머지는 그 결과를 공유한다.
_catalog_lock = asyncio.Lock()


def _cached_catalog() -> list[CropCatalogItem] | None:
    if _catalog_cache is not None and (time.monotonic() - _catalog_cache[0]) < _CATALOG_TTL:
        return _catalog_cache[1]
    return None


async def fetch_crop_catalog() -> list[CropCatalogItem]:
    """전 작목(소분류) 목록을 대→중→소 순회로 모아 반환. 1시간 메모리 캐시.

    작목별농업기술정보(cropEbook) 카테고리 트리의 소분류 = 작목. 캘린더 작물 검색의
    소스로 쓴다(KAMIS 불필요). 중·소분류 호출은 동시성 제한으로 병렬 처리.
    """
    global _catalog_cache
    cached = _cached_catalog()
    if cached is not None:
        return cached

    async with _catalog_lock:
        # 락 대기 중 다른 코루틴이 이미 캐시를 채웠으면 그대로 반환(중복 순회 방지).
        cached = _cached_catalog()
        if cached is not None:
            return cached

        return await _build_catalog()


async def _build_catalog() -> list[CropCatalogItem]:
    global _catalog_cache
    sem = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        mains = await main_category_list(client=client)
        main_name = {
            m.get("mainCategoryCode", ""): m.get("mainCategoryNm", "") for m in mains
        }

        async def _middles(mcode: str) -> tuple[str, list[dict[str, str]]]:
            async with sem:
                return mcode, await middle_category_list(mcode, client=client)

        mid_lists = await asyncio.gather(
            *(_middles(c) for c in main_name if c), return_exceptions=True
        )
        # (mainCode, middleCode) 쌍
        pairs: list[tuple[str, str]] = []
        for r in mid_lists:
            if isinstance(r, BaseException):
                continue
            mcode, middles = r
            for md in middles:
                mid = md.get("middleCategoryCode")
                if mid:
                    pairs.append((mcode, mid))

        async def _subs(mcode: str, mid: str) -> tuple[str, list[dict[str, str]]]:
            async with sem:
                return mcode, await sub_category_list(mid, client=client)

        sub_lists = await asyncio.gather(
            *(_subs(mc, mid) for mc, mid in pairs), return_exceptions=True
        )

    seen: set[str] = set()
    out: list[CropCatalogItem] = []
    for r in sub_lists:
        if isinstance(r, BaseException):
            continue
        mcode, subs = r
        for s in subs:
            code = s.get("subCategoryCode")
            name = s.get("subCategoryNm", "")
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(CropCatalogItem(code=code, name=name, category=main_name.get(mcode, "")))
    out.sort(key=lambda c: c.name)
    _catalog_cache = (time.monotonic(), out)
    return out


async def search_crop_catalog(q: str, *, limit: int = 30) -> list[CropCatalogItem]:
    """작목명에 검색어가 포함된 작목 목록."""
    q = q.strip()
    if not q:
        return []
    items = await fetch_crop_catalog()
    return [c for c in items if q in c.name][:limit]


__all__ = [
    "NongsaroApiError",
    "CropCatalogItem",
    "main_category_list",
    "middle_category_list",
    "sub_category_list",
    "ebook_list",
    "crop_index_list",
    "fetch_crop_catalog",
    "search_crop_catalog",
]
