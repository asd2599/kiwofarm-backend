"""KAMIS (도매가격) 클라이언트.

엔드포인트: http://www.kamis.or.kr/service/price/xml.do?action=periodProductList
인증: settings.kamis_cert_key + settings.kamis_cert_id

응답(JSON, p_returntype=json) 예시:
    {"data": {"error_code": "000", "item": [
        {"countyname": "평균", "regday": "05/15", "price": "13,620", ...},
        {"countyname": "평년", "regday": "05/15", "price": "14,381", ...},
        {"countyname": "서울", "marketname": "가락도매",
         "regday": "05/15", "price": "10,800", ...},
        ...
    ]}}

특수 county:
  - "평균" : 전국 평균 (현재 응답일 기준)
  - "평년" : 예년 동기 평균
  - 그 외 : 도매시장이 있는 도시명 (서울/부산/대구/광주/대전 등)

캐시:
  (item_code, kind_code, start, end, rank) 키로 1시간 TTL 메모리 캐시.
  worker 다중화 시 Redis 전환.
"""

from __future__ import annotations

import ssl
import time
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

from app.config import settings
from app.data import kamis_productno

KAMIS_URL = "https://www.kamis.or.kr/service/price/xml.do"
CACHE_TTL_SEC = 3600


def _legacy_ssl_context() -> ssl.SSLContext:
    """KAMIS 서버는 오래된 cipher suite 만 협상해서 OpenSSL 3.x 기본값으로는
    handshake 가 실패한다. SECLEVEL=0 + legacy renegotiation 허용으로 우회."""
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=0")
    ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
    return ctx


_SSL_CTX = _legacy_ssl_context()


@dataclass(frozen=True)
class WholesalePoint:
    """KAMIS 가격 한 행."""

    county: str  # "서울", "평균", "평년" 등
    market: str  # "가락도매" 등 (평균/평년은 빈 문자열)
    item_name: str
    kind_name: str
    obs_date: date  # regday + 응답 yyyy 결합
    price: int  # 원/단위
    price_cls: str = "02"  # "02"=도매, "01"=소매(도매 자료 없을 때 폴백)


_cache: dict[tuple[str, str, str, str, str], tuple[float, list[WholesalePoint]]] = {}


def _parse_price(raw: str) -> int | None:
    try:
        return int(raw.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_str(raw: object) -> str:
    # KAMIS는 빈 필드를 [] 로 직렬화한다.
    if isinstance(raw, list):
        return ""
    return str(raw or "").strip()


def _parse_date(yyyy: str, regday: str) -> date | None:
    try:
        mm, dd = regday.split("/")
        return date(int(yyyy), int(mm), int(dd))
    except (ValueError, AttributeError):
        return None


async def _fetch_points(
    *,
    product_cls_code: str,
    category_code: str,
    item_code: str,
    kind_code: str,
    start: date,
    end: date,
    rank_code: str,
) -> list[WholesalePoint]:
    """단일 productclscode(02 도매 / 01 소매) 기간 가격 조회."""
    params = {
        "action": "periodProductList",
        "p_productclscode": product_cls_code,
        "p_startday": start.isoformat(),
        "p_endday": end.isoformat(),
        "p_itemcategorycode": category_code,
        "p_itemcode": item_code,
        "p_kindcode": kind_code,
        "p_productrankcode": rank_code,
        "p_cert_key": settings.kamis_cert_key,
        "p_cert_id": settings.kamis_cert_id,
        "p_returntype": "json",
    }

    async with httpx.AsyncClient(timeout=20.0, verify=_SSL_CTX) as client:
        resp = await client.get(KAMIS_URL, params=params)
        resp.raise_for_status()
        body = resp.json()

    # KAMIS는 성공 시 data 를 dict({"error_code", "item": [...]}) 로,
    # 자료 없음/에러 시 list(["001"] 등) 로 직렬화한다.
    data = body.get("data")
    if not isinstance(data, dict):
        return []
    if data.get("error_code") not in (None, "000"):
        return []

    items = data.get("item")
    if not isinstance(items, list):
        return []

    points: list[WholesalePoint] = []
    for row in items:
        county = _parse_str(row.get("countyname"))
        if not county:
            continue
        obs = _parse_date(_parse_str(row.get("yyyy")), _parse_str(row.get("regday")))
        price = _parse_price(_parse_str(row.get("price")))
        if obs is None or price is None:
            continue
        points.append(
            WholesalePoint(
                county=county,
                market=_parse_str(row.get("marketname")),
                item_name=_parse_str(row.get("itemname")),
                kind_name=_parse_str(row.get("kindname")),
                obs_date=obs,
                price=price,
                price_cls=product_cls_code,
            )
        )
    return points


async def fetch_wholesale_period(
    category_code: str,
    item_code: str,
    kind_code: str,
    start: date,
    end: date,
    rank_code: str = "04",
) -> list[WholesalePoint]:
    """기간 가격 조회. category_code 는 KAMIS 부류코드(예: 200=채소류).

    도매(02) 를 우선 조회하고, 해당 품목에 도매 자료가 없으면 소매(01) 로 폴백한다.
    (방울토마토 등 일부 품목은 KAMIS 에 도매가가 없고 소매가만 존재.)
    """
    cache_key = (item_code, kind_code, start.isoformat(), end.isoformat(), rank_code)
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SEC:
        return cached[1]

    if not settings.kamis_cert_key or not settings.kamis_cert_id:
        return []

    points: list[WholesalePoint] = []
    for cls_code in ("02", "01"):
        points = await _fetch_points(
            product_cls_code=cls_code,
            category_code=category_code,
            item_code=item_code,
            kind_code=kind_code,
            start=start,
            end=end,
            rank_code=rank_code,
        )
        if points:
            break

    _cache[cache_key] = (now, points)
    return points


def group_by_county(points: list[WholesalePoint]) -> dict[str, list[WholesalePoint]]:
    out: dict[str, list[WholesalePoint]] = {}
    for p in points:
        out.setdefault(p.county, []).append(p)
    for series in out.values():
        series.sort(key=lambda x: x.obs_date)
    return out


# ───────────────────── 최근일자 가격정보 ─────────────────────
# action=dailyPriceByCategoryList: 부류(category) 전체 품목의 최근일자 도/소매가.
# p_regday(조회일자)가 필수. 응답 item 의 dpr1=당일, dpr2=1일전, dpr3=1주일전,
# dpr5=1개월전. item_code 로 매칭하면 시드 품종코드 불일치를 피할 수 있다.


@dataclass(frozen=True)
class RecentPrice:
    item_code: str
    item_name: str
    kind_code: str
    kind_name: str
    rank: str            # "상품"/"중품" 등
    unit: str            # "5kg" 등
    product_cls_code: str  # "02"=도매, "01"=소매
    obs_date: date         # 가격 기준일
    price: int             # 기준일 도매가 (원/unit)
    prev_price: int | None  # 1일전
    week_ago: int | None    # 1주일전
    month_ago: int | None   # 1개월전


async def _fetch_daily_category(
    product_cls_code: str, category_code: str, regday: date
) -> list[dict]:
    params = {
        "action": "dailyPriceByCategoryList",
        "p_product_cls_code": product_cls_code,
        "p_item_category_code": category_code,
        "p_regday": regday.isoformat(),
        "p_convert_kg_yn": "N",
        "p_cert_key": settings.kamis_cert_key,
        "p_cert_id": settings.kamis_cert_id,
        "p_returntype": "json",
    }
    async with httpx.AsyncClient(timeout=20.0, verify=_SSL_CTX) as client:
        resp = await client.get(KAMIS_URL, params=params)
        resp.raise_for_status()
        body = resp.json()

    data = body.get("data")
    if not isinstance(data, dict):
        return []
    if data.get("error_code") not in (None, "000"):
        return []
    items = data.get("item")
    return items if isinstance(items, list) else []


async def fetch_recent_price(
    category_code: str,
    item_code: str,
    kind_code: str = "",
    product_cls_code: str = "02",
    regday: date | None = None,
) -> RecentPrice | None:
    """검색한 작물의 최근일자 도매가(기본) 1건.

    category 전체 목록에서 item_code 로 매칭 → 품종코드 일치 우선 → 상품(04) 우선.
    당일가(dpr1)가 '-' 면 직전 거래일가로 폴백. 도매 목록에 없으면 None.
    """
    if not settings.kamis_cert_key or not settings.kamis_cert_id:
        return None

    base = regday or date.today()
    items: list[dict] = []
    used_day = base
    for back in range(4):  # 주말/휴일 대비 며칠 뒤로
        used_day = base - timedelta(days=back)
        items = await _fetch_daily_category(product_cls_code, category_code, used_day)
        if items:
            break
    if not items:
        return None

    rows = [r for r in items if _parse_str(r.get("item_code")) == item_code]
    if not rows:
        return None

    def pr(r: dict, key: str) -> int | None:
        return _parse_price(_parse_str(r.get(key)))

    pool = [r for r in rows if pr(r, "dpr1") is not None] or rows
    if kind_code:
        kmatch = [r for r in pool if _parse_str(r.get("kind_code")) == kind_code]
        pool = kmatch or pool
    rank04 = [r for r in pool if _parse_str(r.get("rank_code")) == "04"]
    chosen = (rank04 or pool)[0]

    price = pr(chosen, "dpr1") or pr(chosen, "dpr2") or pr(chosen, "dpr3")
    if price is None:
        return None

    return RecentPrice(
        item_code=item_code,
        item_name=_parse_str(chosen.get("item_name")),
        kind_code=_parse_str(chosen.get("kind_code")),
        kind_name=_parse_str(chosen.get("kind_name")),
        rank=_parse_str(chosen.get("rank")),
        unit=_parse_str(chosen.get("unit")),
        product_cls_code=product_cls_code,
        obs_date=used_day,
        price=price,
        prev_price=pr(chosen, "dpr2"),
        week_ago=pr(chosen, "dpr3"),
        month_ago=pr(chosen, "dpr5"),
    )


# ───────────────────── 최근 가격추이 ─────────────────────
# action=recentlyPriceTrendList: 한 상품(p_productno)의 올해/작년/평년 추이.
# item_code 대신 KAMIS 내부 productno 필요 → seed/kamis_productno.json 매핑 사용.
# 응답 price[].{yyyy, d0(최근)~d40(약 한 달 전), mx, mn}.


_TREND_LABELS = ("4주 전", "3주 전", "2주 전", "1주 전", "최근")


@dataclass(frozen=True)
class PriceTrend:
    productno: int
    item_code: str
    item_name: str
    kind_name: str
    rank: str
    unit: str
    latest: int               # 올해 d0 (최근 도매가)
    points: list[dict]        # [{label, price}] 과거→최근 (None 시점 제외)
    year_ago: int | None      # 작년 동기 d0
    normal: int | None        # 평년 동기 d0
    month_high: int | None    # 올해 기간 최고
    month_low: int | None     # 올해 기간 최저


async def fetch_price_trend(
    item_code: str,
    kind_code: str = "",
    regday: date | None = None,
) -> PriceTrend | None:
    """검색 작물의 최근 도매가 추이(올해/작년/평년). productno 매핑 없으면 None."""
    if not settings.kamis_cert_key or not settings.kamis_cert_id:
        return None
    rec = kamis_productno.find(item_code, kind_code)
    if rec is None:
        return None

    params = {
        "action": "recentlyPriceTrendList",
        "p_productno": str(rec["productno"]),
        "p_productclscode": "02",
        "p_cert_key": settings.kamis_cert_key,
        "p_cert_id": settings.kamis_cert_id,
        "p_returntype": "json",
    }
    async with httpx.AsyncClient(timeout=20.0, verify=_SSL_CTX) as client:
        resp = await client.get(KAMIS_URL, params=params)
        resp.raise_for_status()
        body = resp.json()

    price = body.get("price")
    if not isinstance(price, list):
        return None

    cur_year = str((regday or date.today()).year)
    prev_year = str(int(cur_year) - 1)

    def row(year: str) -> dict | None:
        return next((p for p in price if _parse_str(p.get("yyyy")) == year), None)

    cur = row(cur_year)
    if not cur:
        return None

    raw = [
        (_TREND_LABELS[i], _parse_price(_parse_str(cur.get(k))))
        for i, k in enumerate(("d40", "d30", "d20", "d10", "d0"))
    ]
    points = [{"label": lbl, "price": p} for lbl, p in raw if p is not None]
    if not points:
        return None

    py = row(prev_year)
    nm = row("평년")

    return PriceTrend(
        productno=int(rec["productno"]),
        item_code=item_code,
        item_name=str(rec.get("item_name", "")),
        kind_name=str(rec.get("kind_name", "")),
        rank=str(rec.get("rank", "")),
        unit=str(rec.get("unit", "")),
        latest=points[-1]["price"],
        points=points,
        year_ago=_parse_price(_parse_str(py.get("d0"))) if py else None,
        normal=_parse_price(_parse_str(nm.get("d0"))) if nm else None,
        month_high=_parse_price(_parse_str(cur.get("mx"))),
        month_low=_parse_price(_parse_str(cur.get("mn"))),
    )
