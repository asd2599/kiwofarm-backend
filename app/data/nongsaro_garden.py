"""농사로 텃밭가꾸기(fildMnfct) 클라이언트.

서비스명: fildMnfct (텃밭가꾸기 조회). 인증키: settings.nongsaro_api_key (cropEbook 과 동일).
  - fildMnfctList : 텃밭가꾸기 목록(분류코드 335001) → cntntsNo·제목
  - fildMnfctView : 상세(cn = HTML 본문)

영농 캘린더 계획 생성 시 작목명으로 관련 텃밭 콘텐츠 본문을 회수해 RAG 컨텍스트로 쓴다.
라이브러리가 작아(~수십 건) 전체 목록을 받아 제목에 작목명이 든 항목만 골라 상세를 읽는다.
"""

from __future__ import annotations

import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

GARDEN_BASE = "http://api.nongsaro.go.kr/service/fildMnfct"
GARDEN_SE_CODE = "335001"  # 텃밭 분류코드 (활용가이드 샘플)
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class GardenError(RuntimeError):
    """텃밭가꾸기 API 호출 실패."""


async def _call(operation: str, params: dict[str, Any]) -> ET.Element:
    if not settings.nongsaro_api_key:
        raise GardenError("nongsaro_api_key 가 설정되지 않았습니다 (.env 확인)")
    merged = {"apiKey": settings.nongsaro_api_key, **params}
    url = f"{GARDEN_BASE}/{operation}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, params=merged)
    except httpx.HTTPError as e:
        raise GardenError(f"네트워크 오류: {e}") from e
    if resp.status_code != 200:
        raise GardenError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise GardenError(f"XML 파싱 실패: {e}") from e
    code = root.findtext(".//header/resultCode") or ""
    if code and code != "00":
        msg = root.findtext(".//header/resultMsg") or ""
        raise GardenError(f"농사로 응답 오류 resultCode={code} msg={msg} op={operation}")
    return root


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


@dataclass(frozen=True)
class GardenItem:
    cntnts_no: str
    title: str


async def fetch_list(
    s_text: str | None = None, *, num_of_rows: int = 100
) -> list[GardenItem]:
    """텃밭가꾸기 목록. s_text 지정 시 제목 검색(sType=sCntntsSj)."""
    params: dict[str, Any] = {"sSeCode": GARDEN_SE_CODE, "numOfRows": num_of_rows}
    if s_text:
        params["sType"] = "sCntntsSj"
        params["sText"] = s_text
    root = await _call("fildMnfctList", params)
    out: list[GardenItem] = []
    for it in root.findall(".//body/items/item"):
        no = _text(it.find("cntntsNo"))
        if not no:
            continue
        out.append(GardenItem(cntnts_no=no, title=_text(it.find("cntntsSj"))))
    return out


# 전체 목록 캐시(소규모·거의 불변). 작목마다 목록을 다시 받지 않도록 30분 TTL.
_LIST_TTL = 1800.0
_list_cache: tuple[float, list[GardenItem]] | None = None


async def fetch_full_list() -> list[GardenItem]:
    global _list_cache
    if _list_cache is not None and (time.monotonic() - _list_cache[0]) < _LIST_TTL:
        return _list_cache[1]
    items = await fetch_list()
    _list_cache = (time.monotonic(), items)
    return items


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(raw: str) -> str:
    """cn(HTML 본문) → 일반 텍스트. 태그 제거 + 엔티티 복원 + 공백 정규화."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("\xa0", " ").replace("​", " ")
    return _WS_RE.sub(" ", text).strip()


async def fetch_detail_text(cntnts_no: str) -> tuple[str, str]:
    """(제목, 본문텍스트). 본문은 HTML 제거."""
    root = await _call("fildMnfctView", {"cntntsNo": cntnts_no})
    item = root.find(".//body/item")
    if item is None:
        return "", ""
    return _text(item.find("cntntsSj")), _strip_html(_text(item.find("cn")))


# 상세 본문 1건당 RAG 로 넣을 최대 길이(임베딩 비용·잡설 컷).
MAX_BODY_CHARS = 1500
# 본문이 이보다 짧으면 "첨부파일 참고"식 안내문일 가능성이 커 RAG 에 넣지 않는다.
MIN_BODY_CHARS = 80


async def fetch_garden_texts(crop_name: str, *, limit: int = 6) -> list[str]:
    """작목명으로 텃밭가꾸기 본문 회수. 제목에 작목명이 든(본문이 충실한) 콘텐츠만.

    텃밭 라이브러리에 없는 작물이면 빈 리스트(호출자에서 graceful 처리).
    """
    crop_name = crop_name.strip()
    if len(crop_name) < 2:  # 1글자 작목명은 오매칭 위험이 커 건너뜀
        return []
    try:
        items = await fetch_full_list()
    except GardenError as e:
        log.info("텃밭가꾸기 목록 실패 crop=%s reason=%s", crop_name, e)
        return []

    named = [it for it in items if crop_name in it.title]
    texts: list[str] = []
    for it in named[:limit]:
        try:
            title, body = await fetch_detail_text(it.cntnts_no)
        except GardenError as e:
            log.info("텃밭가꾸기 상세 실패 no=%s reason=%s", it.cntnts_no, e)
            continue
        if len(body) < MIN_BODY_CHARS:  # 첨부파일 안내 등 빈약한 본문 제외
            continue
        texts.append(f"[텃밭가꾸기] {title}\n{body[:MAX_BODY_CHARS]}")
    return texts
