"""농사로 이달의 음식(monthFd) 클라이언트.

서비스: http://api.nongsaro.go.kr/service/monthFd/...
인증키: settings.nongsaro_api_key3 (이달의음식/음식 공통코드 신청 키).

수확인증 카드의 '관리(보관·손질)'와 '음식(섭취·영양·레시피)' 정보 원천.
식재료(monthFdmt*)는 월별 큐레이션이라 같은 식재료가 여러 연·월에 반복
등장한다 — cntntsNo 로 중복 제거하고 등장 월은 시즌 정보로 보존한다.

오퍼레이션:
  monthFdYearLst            연도 목록
  monthFdmtLst(년, 월)       식재료 목록 (cntntsNo, fdmtNm)
  monthFdmtDtl(no)          식재료 상세 (보관·손질/섭취/영양·효능 ...)
  monthNewFdLst(년, 월)      레시피 목록 (cntntsNo, fdNm, fdmtNm)
  monthNewFdDtl(no)         레시피 상세 (재료/조리법/영양수치)
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

import httpx

from app.config import settings

log = logging.getLogger(__name__)

BASE = "http://api.nongsaro.go.kr/service/monthFd"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_RETRIES = 3

_BLOCK_RE = re.compile(r"<\s*(?:br|/p|/div|/li|/tr)\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_SP_RE = re.compile(r"[ \t]+")


class MonthFdError(RuntimeError):
    """이달의 음식 호출 실패."""


@dataclass(frozen=True)
class FoodIngredientMeta:
    cntnts_no: str
    name: str  # fdmtNm


@dataclass(frozen=True)
class FoodIngredient:
    """식재료 상세 (monthFdmtDtl). 빈 섹션은 ""."""

    cntnts_no: str
    name: str
    origin: str  # ctvtIndcDtl 식재료유래
    buying: str  # prchCheatDtl 품종특성·구입요령
    storage: str  # cstdyMthDtl 보관방법·손질법
    eating: str  # ntkMthDtl 섭취방법
    nutrition: str  # ntrIrdntEfcyDtl 영양성분·효능
    etc: str  # etcInfoDtl 기타정보
    months: tuple[str, ...] = field(default=())  # "YYYY-MM" 등장 월(시즌)


@dataclass(frozen=True)
class FoodRecipeMeta:
    cntnts_no: str
    food_name: str  # fdmtNm (레시피 목록의 식재료/음식 제목)
    se_code: str  # fdSeCode 290001 음식 / 290002 가정식 / 290003 단체급식


@dataclass(frozen=True)
class FoodRecipe:
    """레시피 상세 (monthNewFdDtl)."""

    cntnts_no: str
    name: str  # fdNm
    list_title: str  # 목록의 fdmtNm (식재료 연결 단서)
    materials: str  # matrlInfo
    cooking: str  # ckngMthInfo
    nutrients: dict[str, str]  # 라벨 → 값 (비어있지 않은 것만)


def _text(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


def _plain(s: str) -> str:
    """HTML → 평문. 블록 태그는 줄바꿈으로 살려 섹션 구조를 보존한다."""
    s = _BLOCK_RE.sub("\n", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _SP_RE.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return _MULTI_NL_RE.sub("\n\n", s).strip()


async def _call(
    op: str, params: dict[str, str], *, client: httpx.AsyncClient
) -> ET.Element:
    if not settings.nongsaro_api_key3:
        raise MonthFdError("NONGSARO_API_KEY3 미설정 (.env 확인)")
    q = {"apiKey": settings.nongsaro_api_key3, **params}
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
                raise MonthFdError(f"[{op}] resultCode={code} resultMsg={msg}")
            return root
        except (httpx.HTTPError, ET.ParseError) as e:
            last = e
            await asyncio.sleep(0.5 * (2**attempt))
    raise MonthFdError(f"[{op}] 네트워크/파싱 실패: {last}")


def _items(root: ET.Element) -> list[ET.Element]:
    return root.findall(".//body/items/item") or root.findall(".//item")


async def fetch_years(*, client: httpx.AsyncClient) -> list[str]:
    root = await _call("monthFdYearLst", {}, client=client)
    years = [(e.text or "").strip() for e in root.iter("year")]
    return sorted({y for y in years if y})


async def fetch_ingredient_metas(
    year: str, month: str, *, client: httpx.AsyncClient
) -> list[FoodIngredientMeta]:
    """월별 식재료 목록. 이미지 행 중복은 cntntsNo 로 제거."""
    root = await _call(
        "monthFdmtLst", {"thisYear": year, "thisMonth": month}, client=client
    )
    seen: dict[str, FoodIngredientMeta] = {}
    for it in _items(root):
        no = _text(it, "cntntsNo")
        if not no or no in seen:
            continue
        seen[no] = FoodIngredientMeta(cntnts_no=no, name=_text(it, "fdmtNm"))
    return list(seen.values())


async def fetch_ingredient_detail(
    cntnts_no: str, *, client: httpx.AsyncClient, months: tuple[str, ...] = ()
) -> FoodIngredient:
    root = await _call("monthFdmtDtl", {"cntntsNo": cntnts_no}, client=client)
    it = root.find(".//body/item") or root.find(".//body/items/item") or root.find(".//item")
    return FoodIngredient(
        cntnts_no=cntnts_no,
        name=_text(it, "fdmtNm"),
        origin=_plain(_text(it, "ctvtIndcDtl")),
        buying=_plain(_text(it, "prchCheatDtl")),
        storage=_plain(_text(it, "cstdyMthDtl")),
        eating=_plain(_text(it, "ntkMthDtl")),
        nutrition=_plain(_text(it, "ntrIrdntEfcyDtl")),
        etc=_plain(_text(it, "etcInfoDtl")),
        months=months,
    )


async def fetch_recipe_metas(
    year: str, month: str, *, client: httpx.AsyncClient
) -> list[FoodRecipeMeta]:
    root = await _call(
        "monthNewFdLst", {"thisYear": year, "thisMonth": month}, client=client
    )
    seen: dict[str, FoodRecipeMeta] = {}
    for it in _items(root):
        no = _text(it, "cntntsNo")
        if not no or no in seen:
            continue
        seen[no] = FoodRecipeMeta(
            cntnts_no=no, food_name=_text(it, "fdmtNm"), se_code=_text(it, "fdSeCode")
        )
    return list(seen.values())


# 레시피 상세의 영양 응답변수 → 한글 라벨
_NUTRIENT_TAGS: tuple[tuple[str, str], ...] = (
    ("phphmntNm", "인분"),
    ("energyQy", "에너지(kcal)"),
    ("crbQy", "탄수화물(g)"),
    ("protQy", "단백질(g)"),
    ("ntrfsQy", "지질(g)"),
    ("edblfibrQy", "식이섬유(g)"),
    ("vtmaQy", "비타민A"),
    ("vtcQy", "비타민C"),
    ("vteQy", "비타민E"),
    ("thiaQy", "티아민"),
    ("niboplaQy", "리보플라빈"),
    ("clciQy", "칼슘"),
    ("naQy", "나트륨"),
    ("ptssQy", "칼륨"),
    ("irnQy", "철"),
)


async def fetch_recipe_detail(
    meta: FoodRecipeMeta, *, client: httpx.AsyncClient
) -> FoodRecipe:
    root = await _call("monthNewFdDtl", {"cntntsNo": meta.cntnts_no}, client=client)
    it = root.find(".//body/item") or root.find(".//body/items/item") or root.find(".//item")
    nutrients = {
        label: _text(it, tag) for tag, label in _NUTRIENT_TAGS if _text(it, tag)
    }
    return FoodRecipe(
        cntnts_no=meta.cntnts_no,
        name=_text(it, "fdNm") or meta.food_name,
        list_title=meta.food_name,
        materials=_plain(_text(it, "matrlInfo")),
        cooking=_plain(_text(it, "ckngMthInfo")),
        nutrients=nutrients,
    )


__all__ = [
    "MonthFdError",
    "FoodIngredientMeta",
    "FoodIngredient",
    "FoodRecipeMeta",
    "FoodRecipe",
    "fetch_years",
    "fetch_ingredient_metas",
    "fetch_ingredient_detail",
    "fetch_recipe_metas",
    "fetch_recipe_detail",
]
