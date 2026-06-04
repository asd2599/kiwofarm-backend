"""농사로 주간농사정보(weekFarmInfo) 클라이언트.

서비스: http://api.nongsaro.go.kr/service/weekFarmInfo/weekFarmInfoList
인증키: settings.nongsaro_api_key (cropEbook 키와 별개로 신청된 키).

주간농사정보는 매주 발간되는 "주간농사정보 제N호" 회보의 메타데이터 + 파일
다운로드 링크(hwpx/hwp/pdf)를 제공한다. cropEbook 의 PDF 다운로드는 막혀
있지만, 이 서비스의 contentsFileDownload 링크는 실제로 파일을 내려준다(pdf 포함)
→ 본문 텍스트 추출까지 가능.

응답 item 필드 (실측):
  cntntsNo, subject, regDt, writerNm, hitCt,
  fileName / downUrlList / fileSeCode  (각각 '|' 로 구분된 동순서 병렬 목록),
  downUrl (목록 중 첫 파일)
페이징: pageNo + numOfRows (공공데이터포털 표준). body/totalCount 에 전체 건수.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import httpx

from app.config import settings
from app.data.nongsaro import extract_pdf_text  # PDF 바이트 → 텍스트 (재사용)

log = logging.getLogger(__name__)

WEEKFARM_BASE = "http://api.nongsaro.go.kr/service/weekFarmInfo"
OPERATION = "weekFarmInfoList"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
FILE_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
MAX_RETRIES = 3
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50MB (hwpx 가 16MB+ 이므로 여유)


class WeekFarmError(RuntimeError):
    """주간농사정보 호출 실패 (네트워크·인증·resultCode 등)."""


@dataclass(frozen=True)
class WeekFarmFile:
    name: str
    url: str
    se_code: str

    @property
    def is_pdf(self) -> bool:
        return self.name.lower().endswith(".pdf")


@dataclass(frozen=True)
class WeekFarmInfo:
    cntnts_no: str
    subject: str
    reg_date: str  # YYYY-MM-DD (응답 원문 그대로)
    writer: str
    hit_count: int
    files: tuple[WeekFarmFile, ...]

    @property
    def pdf_url(self) -> str | None:
        """다운로드 파일 중 PDF 의 URL (없으면 None)."""
        return next((f.url for f in self.files if f.is_pdf and f.url), None)


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def _to_int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def _parse_files(item: ET.Element) -> tuple[WeekFarmFile, ...]:
    names = _text(item, "fileName").split("|")
    urls = _text(item, "downUrlList").split("|")
    codes = _text(item, "fileSeCode").split("|")
    files: list[WeekFarmFile] = []
    for i, name in enumerate(names):
        name = name.strip()
        if not name:
            continue
        url = urls[i].strip() if i < len(urls) else ""
        code = codes[i].strip() if i < len(codes) else ""
        files.append(WeekFarmFile(name=name, url=url, se_code=code))
    return tuple(files)


def _parse(xml_text: str) -> tuple[int, list[WeekFarmInfo]]:
    root = ET.fromstring(xml_text)
    code = root.findtext(".//header/resultCode") or ""
    if code and code != "00":
        msg = root.findtext(".//header/resultMsg") or ""
        raise WeekFarmError(f"resultCode={code} resultMsg={msg}")
    total = _to_int(root.findtext(".//body/totalCount") or root.findtext(".//totalCount") or "0")
    items = root.findall(".//body/items/item") or root.findall(".//item")
    out = [
        WeekFarmInfo(
            cntnts_no=_text(it, "cntntsNo"),
            subject=_text(it, "subject"),
            reg_date=_text(it, "regDt"),
            writer=_text(it, "writerNm"),
            hit_count=_to_int(_text(it, "hitCt")),
            files=_parse_files(it),
        )
        for it in items
        if _text(it, "cntntsNo")
    ]
    return total, out


async def fetch_page(
    page_no: int = 1,
    num_of_rows: int = 100,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[int, list[WeekFarmInfo]]:
    """주간농사정보 한 페이지. 반환: (전체 건수, 이 페이지 목록)."""
    if not settings.nongsaro_api_key:
        raise WeekFarmError("NONGSARO_API_KEY 미설정 (.env 확인)")
    params = {
        "apiKey": settings.nongsaro_api_key,
        "pageNo": str(page_no),
        "numOfRows": str(num_of_rows),
    }
    url = f"{WEEKFARM_BASE}/{OPERATION}"

    async def _req(c: httpx.AsyncClient) -> tuple[int, list[WeekFarmInfo]]:
        last: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = await c.get(url, params=params, timeout=TIMEOUT)
                resp.raise_for_status()
                return _parse(resp.text)
            except (httpx.HTTPError, ET.ParseError) as e:
                last = e
                await asyncio.sleep(0.5 * (2**attempt))
        raise WeekFarmError(f"네트워크/파싱 실패: {last}")

    if client is not None:
        return await _req(client)
    async with httpx.AsyncClient() as c:
        return await _req(c)


async def fetch_all(
    *, max_items: int | None = None, num_of_rows: int = 100
) -> list[WeekFarmInfo]:
    """전체(또는 max_items 까지) 페이지를 순회 수집. 최신(regDt 내림차순) 응답 순서 유지."""
    async with httpx.AsyncClient() as c:
        total, first = await fetch_page(1, num_of_rows, client=c)
        out = list(first)
        if max_items is not None:
            out = out[:max_items]
        target = total if max_items is None else min(total, max_items)
        page = 1
        while len(out) < target and len(first) == num_of_rows:
            page += 1
            _, items = await fetch_page(page, num_of_rows, client=c)
            if not items:
                break
            out.extend(items)
            if max_items is not None:
                out = out[:max_items]
        return out


async def download_file(url: str) -> bytes:
    """주간농사정보 파일(contentsFileDownload) 다운로드. 빈 URL·과대 용량은 오류."""
    if not url:
        raise WeekFarmError("빈 다운로드 URL")
    try:
        async with httpx.AsyncClient(timeout=FILE_TIMEOUT, follow_redirects=True) as c:
            resp = await c.get(url)
    except httpx.HTTPError as e:
        raise WeekFarmError(f"파일 다운로드 실패: {e}") from e
    if resp.status_code != 200:
        raise WeekFarmError(f"파일 HTTP {resp.status_code}")
    if len(resp.content) > MAX_FILE_BYTES:
        raise WeekFarmError(f"파일 용량 초과: {len(resp.content)} bytes")
    return resp.content


async def fetch_pdf_text(info: WeekFarmInfo) -> str:
    """회보의 PDF 를 받아 텍스트로. PDF 링크 없거나 추출 실패 시 빈 문자열."""
    url = info.pdf_url
    if not url:
        return ""
    try:
        data = await download_file(url)
        return extract_pdf_text(data)
    except Exception as e:  # noqa: BLE001 - 한 건 실패가 전체 수집을 막지 않게
        log.info("주간농사정보 PDF 텍스트 실패 cntnts=%s reason=%s", info.cntnts_no, e)
        return ""


__all__ = [
    "WeekFarmError",
    "WeekFarmFile",
    "WeekFarmInfo",
    "fetch_page",
    "fetch_all",
    "download_file",
    "fetch_pdf_text",
]
