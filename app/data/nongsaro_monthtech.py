"""농사로 이달의 농업기술(monthFarmTech) 클라이언트.

서비스: http://api.nongsaro.go.kr/service/monthFarmTech/...
인증키: settings.nongsaro_api_key2 (주간농사정보와 같은 신규 신청 키).

이달의 농업기술은 "큐레이션" 단위 기사(이달의 핵심 농업기술)로, cropEbook/주간과
달리 **본문 HTML 을 API 로 직접** 준다(파일 다운로드 불필요). 작물 태깅(prdlstCode)도
있어 작목별 정리에 가장 적합하다.

4단계 오퍼레이션:
  monthFarmTechLst                  큐레이션 목록 (curationNo, curationNm, clCodeNm, ...)
  monthFarmTechDtlDefaultInfo(no)   기본정보 (prdlstCode=품목코드, linkUrl, ...)
  monthFarmTechDtlGuideLst(no)      하위 콘텐츠 목록 (cntntsSnn, cntntsNm)
  monthFarmTechDtl(no, snn)         하위 콘텐츠 본문 (cntntsInfoHtml)
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import httpx

from app.config import settings

log = logging.getLogger(__name__)

BASE = "http://api.nongsaro.go.kr/service/monthFarmTech"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_RETRIES = 3

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class MonthTechError(RuntimeError):
    """이달의 농업기술 호출 실패."""


@dataclass(frozen=True)
class MonthTechArticle:
    curation_no: str
    title: str
    category: str  # clCodeNm
    summary: str  # curationSumryDtl
    svc_date: str  # svcDt
    prdlst_code: str  # 농사로 품목코드 (없으면 "")
    body: str  # 하위 콘텐츠 본문(HTML 제거·결합)


def _text(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


def _strip_html(s: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", s))).strip()


async def _call(
    op: str, params: dict[str, str], *, client: httpx.AsyncClient
) -> ET.Element:
    if not settings.nongsaro_api_key2:
        raise MonthTechError("NONGSARO_API_KEY2 미설정 (.env 확인)")
    q = {"apiKey": settings.nongsaro_api_key2, **params}
    url = f"{BASE}/{op}"
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.get(url, params=q, timeout=TIMEOUT)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            code = root.findtext(".//header/resultCode") or ""
            if code and code != "00":
                msg = root.findtext(".//header/resultMsg") or ""
                raise MonthTechError(f"[{op}] resultCode={code} resultMsg={msg}")
            return root
        except (httpx.HTTPError, ET.ParseError) as e:
            last = e
            await asyncio.sleep(0.5 * (2**attempt))
    raise MonthTechError(f"[{op}] 네트워크/파싱 실패: {last}")


def _items(root: ET.Element) -> list[ET.Element]:
    return root.findall(".//body/items/item") or root.findall(".//item")


async def fetch_list_page(
    page_no: int, num_of_rows: int, *, client: httpx.AsyncClient
) -> list[dict[str, str]]:
    root = await _call(
        "monthFarmTechLst",
        {"pageNo": str(page_no), "numOfRows": str(num_of_rows)},
        client=client,
    )
    out: list[dict[str, str]] = []
    for it in _items(root):
        no = _text(it, "curationNo")
        if not no:
            continue
        out.append(
            {
                "curationNo": no,
                "curationNm": _text(it, "curationNm"),
                "clCodeNm": _text(it, "clCodeNm"),
                "curationSumryDtl": _text(it, "curationSumryDtl"),
                "svcDt": _text(it, "svcDt"),
            }
        )
    return out


async def _fetch_prdlst_code(curation_no: str, *, client: httpx.AsyncClient) -> str:
    root = await _call(
        "monthFarmTechDtlDefaultInfo", {"srchCurationNo": curation_no}, client=client
    )
    it = root.find(".//body/items/item") or root.find(".//item")
    return _text(it, "prdlstCode")


async def _fetch_body(curation_no: str, *, client: httpx.AsyncClient) -> str:
    """하위 콘텐츠(GuideLst) 전부의 본문(Dtl)을 결합한 평문."""
    guide = await _call(
        "monthFarmTechDtlGuideLst", {"srchCurationNo": curation_no}, client=client
    )
    snns = [_text(it, "cntntsSnn") for it in _items(guide)]
    snns = [s for s in snns if s]
    if not snns:
        snns = ["1"]  # 가이드 목록이 비어도 1번 본문 시도
    parts: list[str] = []
    for snn in snns:
        dtl = await _call(
            "monthFarmTechDtl",
            {"srchCurationNo": curation_no, "srchCntntsSnn": snn},
            client=client,
        )
        it = dtl.find(".//body/items/item") or dtl.find(".//item")
        plain = _strip_html(_text(it, "cntntsInfoHtml"))
        if plain:
            parts.append(plain)
    return "\n\n".join(parts)


async def fetch_article(
    curation_no: str, meta: dict[str, str], *, client: httpx.AsyncClient
) -> MonthTechArticle:
    prdlst = await _fetch_prdlst_code(curation_no, client=client)
    body = await _fetch_body(curation_no, client=client)
    return MonthTechArticle(
        curation_no=curation_no,
        title=meta.get("curationNm", ""),
        category=meta.get("clCodeNm", ""),
        summary=meta.get("curationSumryDtl", ""),
        svc_date=meta.get("svcDt", ""),
        prdlst_code=prdlst,
        body=body,
    )


async def fetch_all_articles(
    *, max_items: int | None = None, num_of_rows: int = 50, concurrency: int = 6
) -> list[MonthTechArticle]:
    """전체(또는 max_items) 큐레이션의 본문까지 수집."""
    async with httpx.AsyncClient() as client:
        metas: list[dict[str, str]] = []
        page = 1
        while True:
            batch = await fetch_list_page(page, num_of_rows, client=client)
            if not batch:
                break
            metas.extend(batch)
            if max_items is not None and len(metas) >= max_items:
                metas = metas[:max_items]
                break
            if len(batch) < num_of_rows:
                break
            page += 1

        sem = asyncio.Semaphore(concurrency)

        async def _one(m: dict[str, str]) -> MonthTechArticle | None:
            async with sem:
                try:
                    return await fetch_article(m["curationNo"], m, client=client)
                except MonthTechError as e:
                    log.info("monthtech 기사 실패 no=%s reason=%s", m["curationNo"], e)
                    return None

        results = await asyncio.gather(*(_one(m) for m in metas))
        return [a for a in results if a is not None]


__all__ = [
    "MonthTechError",
    "MonthTechArticle",
    "fetch_list_page",
    "fetch_article",
    "fetch_all_articles",
]
