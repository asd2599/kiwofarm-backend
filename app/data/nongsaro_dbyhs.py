"""농사로 병해충발생정보(dbyhsCccrrncInfo) 클라이언트.

서비스: http://api.nongsaro.go.kr/service/dbyhsCccrrncInfo/...
인증키: settings.nongsaro_api_key (농사로 통합 키).

전국 단위 주기 회보(예: "병해충발생정보 제3호 (2026.06.01~06.30)") 목록을 제공한다.
작목/지역 필터는 없고, 실제 내용은 첨부파일(downFile)에 있다. 위기 알림에서는 현재
시기(또는 최신) 회보를 골라 "이 시기 병해충 발생정보" 알림 + 원문 링크로 쓴다.

오퍼레이션:
  dbyhsCccrrncInfoList(sYear?, sText?, sType?)  회보 목록
  dbyhsCccrrncInfoYear()                          연도 콤보
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import date
from xml.etree import ElementTree as ET

import httpx
from pypdf import PdfReader

from app.config import settings

log = logging.getLogger(__name__)

BASE = "http://api.nongsaro.go.kr/service/dbyhsCccrrncInfo"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# 제목의 기간 표기 "(2026.06.01~06.30)" / "(2026.01.02 ~ 01.31)" 추출용.
_PERIOD_RE = re.compile(
    r"\((\d{4})\.(\d{1,2})\.(\d{1,2})\.?\s*~\s*(?:(\d{4})\.)?(\d{1,2})\.(\d{1,2})\.?\)"
)


class DbyhsError(RuntimeError):
    """병해충발생정보 호출 실패."""


@dataclass(frozen=True)
class OutbreakBulletin:
    cntnts_no: str
    year: str
    title: str  # cntntsSj
    regist_date: str  # svcDt (YYYY-MM-DD)
    down_url: str  # downFile (첨부 원문)
    file_name: str
    period_start: date | None
    period_end: date | None


def _text(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


def _parse_period(title: str) -> tuple[date | None, date | None]:
    m = _PERIOD_RE.search(title)
    if not m:
        return None, None
    y1, mo1, d1, y2, mo2, d2 = m.groups()
    try:
        start = date(int(y1), int(mo1), int(d1))
        end = date(int(y2 or y1), int(mo2), int(d2))
        return start, end
    except ValueError:
        return None, None


async def _call(op: str, params: dict[str, str], *, client: httpx.AsyncClient) -> ET.Element:
    if not settings.nongsaro_api_key:
        raise DbyhsError("NONGSARO_API_KEY 미설정 (.env 확인)")
    q = {"apiKey": settings.nongsaro_api_key, **params}
    try:
        resp = await client.get(f"{BASE}/{op}", params=q, timeout=TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except (httpx.HTTPError, ET.ParseError) as e:
        raise DbyhsError(f"[{op}] 네트워크/파싱 실패: {e}") from e
    code = root.findtext(".//header/resultCode") or ""
    if code and code != "00":
        msg = root.findtext(".//header/resultMsg") or ""
        raise DbyhsError(f"[{op}] resultCode={code} resultMsg={msg}")
    return root


async def fetch_list(
    *, s_year: str | None = None, num_of_rows: int = 20, client: httpx.AsyncClient
) -> list[OutbreakBulletin]:
    """병해충발생정보 회보 목록(최신순으로 제공됨)."""
    params: dict[str, str] = {"numOfRows": str(num_of_rows), "pageNo": "1"}
    if s_year:
        params["sYear"] = s_year
    root = await _call("dbyhsCccrrncInfoList", params, client=client)
    out: list[OutbreakBulletin] = []
    for it in root.findall(".//body/items/item") or root.findall(".//item"):
        no = _text(it, "cntntsNo")
        title = _text(it, "cntntsSj")
        if not no or not title:
            continue
        start, end = _parse_period(title)
        out.append(
            OutbreakBulletin(
                cntnts_no=no,
                year=_text(it, "pblicteYear"),
                title=title,
                regist_date=_text(it, "svcDt") or _text(it, "registDt"),
                down_url=_text(it, "downFile"),
                file_name=_text(it, "rtnOrginlFileNm"),
                period_start=start,
                period_end=end,
            )
        )
    return out


async def fetch_current(ref: date) -> OutbreakBulletin | None:
    """기준일이 속한 기간의 회보. 없으면 가장 최신 회보. 실패 시 None."""
    try:
        async with httpx.AsyncClient() as client:
            items = await fetch_list(s_year=str(ref.year), num_of_rows=30, client=client)
            if not items:  # 해당 연도 회보가 없으면 전체 최신
                items = await fetch_list(num_of_rows=30, client=client)
    except DbyhsError as e:
        log.info("병해충발생정보 조회 실패: %s", e)
        return None
    if not items:
        return None
    for b in items:
        if b.period_start and b.period_end and b.period_start <= ref <= b.period_end:
            return b
    return items[0]  # 목록은 최신순 → 첫 항목이 가장 최근


# 회보 PDF 본문 캐시(번호별·세션 메모리). 다운로드+파싱이 비싸 작물 간 공유.
_PDF_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_MAX_PDF_BYTES = 30 * 1024 * 1024
_text_cache: dict[str, str] = {}


async def fetch_bulletin_text(b: OutbreakBulletin, *, max_pages: int = 12) -> str:
    """회보 첨부 PDF를 받아 본문 텍스트로. 실패/비PDF면 빈 문자열. 번호별 캐시."""
    if not b.down_url:
        return ""
    if b.cntnts_no in _text_cache:
        return _text_cache[b.cntnts_no]
    try:
        async with httpx.AsyncClient(timeout=_PDF_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(b.down_url)
        resp.raise_for_status()
        data = resp.content
        if len(data) > _MAX_PDF_BYTES or not data[:4].startswith(b"%PDF"):
            return ""
        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages[:max_pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - 일부 페이지 추출 실패는 건너뜀
                continue
        text = " ".join("\n".join(parts).split())
    except Exception as e:  # noqa: BLE001 - 추출 실패해도 알림은 폴백으로 제공
        log.info("병해충발생정보 PDF 추출 실패 no=%s: %s", b.cntnts_no, e)
        return ""
    _text_cache[b.cntnts_no] = text
    return text


__all__ = [
    "DbyhsError",
    "OutbreakBulletin",
    "fetch_list",
    "fetch_current",
    "fetch_bulletin_text",
]
