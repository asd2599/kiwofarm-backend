"""농사로 텃밭가꾸기(fildMnfct) 클라이언트.

서비스: http://api.nongsaro.go.kr/service/fildMnfct/...
인증키: settings.nongsaro_api_key (농사로 통합 키).

v3 의 1순위 재배지식 원천 — 도시 텃밭(베란다·옥상·노지 소규모) 눈높이 콘텐츠.
분류코드(sSeCode): 335001=채소·허브·텃밭일반, 335002=과수, 335003=특용작물.

두 가지 사용처를 모두 지원한다:
  1. 배치 수집 (scripts/sync_garden.py): fetch_list(se_code) + fetch_view()
     → 전체 글을 작물 매칭해 {슬러그}.garden / _common.garden 임베딩.
  2. 온디맨드 인제스트 (core/rag/ingest._ingest_garden): fetch_garden_texts(작목명)
     → 영농 캘린더 계획 생성 시 제목에 작목명이 든 본문만 회수해 RAG 보강.
     라이브러리가 작아(~150건) 전체 목록을 TTL 캐시하고 제목으로 거른다.

오퍼레이션:
  fildMnfctList(sSeCode, pageNo, numOfRows)   목록 (cntntsNo, cntntsSj)
  fildMnfctView(cntntsNo)                     상세 (cn=본문 HTML)
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import httpx

from app.config import settings

log = logging.getLogger(__name__)

BASE = "http://api.nongsaro.go.kr/service/fildMnfct"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_RETRIES = 3

SE_CODES: dict[str, str] = {
    "335001": "채소·허브·텃밭일반",
    "335002": "과수",
    "335003": "특용작물",
}

_BLOCK_RE = re.compile(r"<\s*(?:br|/p|/div|/li|/tr|/h[1-6])\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_SP_RE = re.compile(r"[ \t ?]+")


class GardenError(RuntimeError):
    """텃밭가꾸기 호출 실패."""


@dataclass(frozen=True)
class GardenArticleMeta:
    cntnts_no: str
    title: str  # cntntsSj
    se_code: str


@dataclass(frozen=True)
class GardenArticle:
    cntnts_no: str
    title: str
    se_code: str
    body: str  # cn 평문
    down_url: str
    file_name: str


def _text(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


def _plain(s: str) -> str:
    """본문 HTML → 평문 (블록 태그는 줄바꿈 유지)."""
    s = _BLOCK_RE.sub("\n", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ").replace("​", " ")
    s = _SP_RE.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return _MULTI_NL_RE.sub("\n\n", s).strip()


async def _call(
    op: str, params: dict[str, str], *, client: httpx.AsyncClient
) -> ET.Element:
    if not settings.nongsaro_api_key:
        raise GardenError("NONGSARO_API_KEY 미설정 (.env 확인)")
    q = {"apiKey": settings.nongsaro_api_key, **params}
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.get(f"{BASE}/{op}", params=q, timeout=TIMEOUT)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            code = root.findtext(".//header/resultCode") or ""
            if code and code != "00":
                msg = root.findtext(".//header/resultMsg") or ""
                raise GardenError(f"[{op}] resultCode={code} resultMsg={msg}")
            return root
        except (httpx.HTTPError, ET.ParseError) as e:
            last = e
            await asyncio.sleep(0.5 * (2**attempt))
    raise GardenError(f"[{op}] 네트워크/파싱 실패: {last}")


async def fetch_list(
    se_code: str, *, client: httpx.AsyncClient, num_of_rows: int = 100
) -> list[GardenArticleMeta]:
    """분류코드의 전체 목록 (페이지 순회, cntntsNo 중복 제거)."""
    out: dict[str, GardenArticleMeta] = {}
    page = 1
    while True:
        root = await _call(
            "fildMnfctList",
            {"sSeCode": se_code, "pageNo": str(page), "numOfRows": str(num_of_rows)},
            client=client,
        )
        items = root.findall(".//item")
        for it in items:
            no = _text(it, "cntntsNo")
            if not no or no in out:
                continue
            out[no] = GardenArticleMeta(
                cntnts_no=no, title=_text(it, "cntntsSj"), se_code=se_code
            )
        total = int(root.findtext(".//totalCount") or "0")
        if page * num_of_rows >= total or not items:
            break
        page += 1
    return list(out.values())


async def fetch_view(
    meta: GardenArticleMeta, *, client: httpx.AsyncClient
) -> GardenArticle:
    root = await _call("fildMnfctView", {"cntntsNo": meta.cntnts_no}, client=client)
    it = root.find(".//body/item") or root.find(".//item")
    # cn 은 CDATA 가 여러 개로 쪼개질 수 있어 itertext 로 합친다.
    cn_el = it.find("cn") if it is not None else None
    raw = "".join(cn_el.itertext()) if cn_el is not None else ""
    return GardenArticle(
        cntnts_no=meta.cntnts_no,
        title=_text(it, "cntntsSj") or meta.title,
        se_code=meta.se_code,
        body=_plain(raw),
        down_url=_text(it, "downUrl"),
        file_name=_text(it, "fileName"),
    )


# ───────────── 온디맨드 인제스트용 (영농 캘린더 RAG 보강) ─────────────

# 전체 목록 캐시(소규모·거의 불변). 작목마다 목록을 다시 받지 않도록 30분 TTL.
_LIST_TTL = 1800.0
_list_cache: tuple[float, list[GardenArticleMeta]] | None = None

# 상세 본문 1건당 RAG 로 넣을 최대 길이(임베딩 비용·잡설 컷).
MAX_BODY_CHARS = 1500
# 본문이 이보다 짧으면 "첨부파일 참고"식 안내문일 가능성이 커 RAG 에 넣지 않는다.
MIN_BODY_CHARS = 80


async def fetch_full_list() -> list[GardenArticleMeta]:
    """전 분류코드 목록 (TTL 캐시)."""
    global _list_cache
    if _list_cache is not None and (time.monotonic() - _list_cache[0]) < _LIST_TTL:
        return _list_cache[1]
    items: list[GardenArticleMeta] = []
    async with httpx.AsyncClient() as client:
        for se in SE_CODES:
            items.extend(await fetch_list(se, client=client))
    _list_cache = (time.monotonic(), items)
    return items


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
    async with httpx.AsyncClient() as client:
        for it in named[:limit]:
            try:
                art = await fetch_view(it, client=client)
            except GardenError as e:
                log.info("텃밭가꾸기 상세 실패 no=%s reason=%s", it.cntnts_no, e)
                continue
            if len(art.body) < MIN_BODY_CHARS:  # 첨부파일 안내 등 빈약한 본문 제외
                continue
            texts.append(f"[텃밭가꾸기] {art.title}\n{art.body[:MAX_BODY_CHARS]}")
    return texts


__all__ = [
    "GardenError",
    "GardenArticleMeta",
    "GardenArticle",
    "SE_CODES",
    "fetch_list",
    "fetch_view",
    "fetch_full_list",
    "fetch_garden_texts",
    "MAX_BODY_CHARS",
    "MIN_BODY_CHARS",
]
