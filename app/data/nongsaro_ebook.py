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
from collections.abc import Mapping
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


__all__ = [
    "NongsaroApiError",
    "main_category_list",
    "middle_category_list",
    "sub_category_list",
    "ebook_list",
    "crop_index_list",
]
