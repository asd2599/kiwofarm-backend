"""농사로 텃밭가꾸기(fildMnfct) 클라이언트.

서비스: http://api.nongsaro.go.kr/service/fildMnfct/...
인증키: settings.nongsaro_api_key (농사로 통합 키).

v3 의 1순위 재배지식 원천 — 도시 텃밭(베란다·옥상·노지 소규모) 눈높이 콘텐츠.
분류코드(sSeCode): 335001=채소·허브·텃밭일반, 335002=과수, 335003=특용작물.

오퍼레이션:
  fildMnfctList(sSeCode, pageNo, numOfRows)   목록 (cntntsNo, cntntsSj)
  fildMnfctView(cntntsNo)                     상세 (cn=본문 HTML)
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
_SP_RE = re.compile(r"[ \t ?]+")


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


__all__ = [
    "GardenError",
    "GardenArticleMeta",
    "GardenArticle",
    "SE_CODES",
    "fetch_list",
    "fetch_view",
]
