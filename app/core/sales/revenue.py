"""판매 채널별 예상 실수령액 비교.

출하 도우미에서 넘어온 작물 + 판매량으로 두 채널을 비교한다.
  - 직매장 직거래: KAMIS 소매가를 농가 직거래 단가로 보정 → 직매장 수수료 차감
  - 도매시장 출하: KAMIS 도매가 → 도매시장 위탁수수료 차감

판매량 입력 단위는 **KAMIS 도매가 단위로 결정**한다.
  - 무게형(예 '10kg') → kg 으로 입력, kg당 단가로 환산
  - 갯수형(예 '1개', '20개', '포기') → 개수로 입력, 개당 단가로 환산
도매가 단위가 기준(anchor)이고, 소매가 단위가 종류(무게/갯수)가 다르면 환산이
안 되므로 도매가 기반 추정으로 폴백한다.

모든 가정(수수료율·소매 보정계수)은 결과에 그대로 실어 UI가 '참고 수치'로
표기하게 한다. 실거래가가 아니라 시장가 기반 추정이다.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from app.data import kamis

# 가정값 (참고 수치). 출처: 도매시장 위탁·하역 수수료 통상 7~10%,
# 직매장 판매수수료 통상 5~12%. 직매장 직거래가는 시중 소매가보다 낮게 책정되는 편.
WHOLESALE_COMMISSION = 0.08  # 도매시장 출하 수수료
DIRECT_COMMISSION = 0.10     # 직매장 판매 수수료
DIRECT_RETAIL_FACTOR = 0.85  # 직매장 직거래 단가 = 소매가 × 0.85
RETAIL_FROM_WHOLESALE = 1.6  # 소매가 미반영 시 도매가 → 소매가 추정 배수

_KG = re.compile(r"([\d.]+)\s*[kK][gG]")
_COUNT = re.compile(r"([\d.]+)\s*(개|통|포기|마리|단|속|쪽|망|포|짝)")
_COUNT_WORDS = re.compile(r"(개|통|포기|마리|단|속|쪽|망|포|짝)")

# 입력 모드별 표시 단위
MODE_UNIT = {"weight": "kg", "count": "개"}


def parse_unit(unit: str | None) -> tuple[str, float] | None:
    """KAMIS 단위 → (kind, divisor). kind='weight'|'count'.

    '10kg'→('weight',10), 'kg'→('weight',1), '1개'→('count',1),
    '20개'→('count',20), '포기'→('count',1). 해석 불가 시 None.
    무게 표기(kg)를 갯수보다 우선한다(예 '10kg(그물망 3포기)'는 무게).
    """
    u = (unit or "").strip()
    if not u:
        return None
    m = _KG.search(u)
    if m:
        try:
            return ("weight", float(m.group(1)))
        except ValueError:
            return None
    if u.lower() == "kg":
        return ("weight", 1.0)
    m = _COUNT.search(u)
    if m:
        try:
            return ("count", float(m.group(1)))
        except ValueError:
            return ("count", 1.0)
    if _COUNT_WORDS.search(u):
        return ("count", 1.0)
    return None


def _per_base(price: int, unit: str | None, mode: str) -> float | None:
    """mode(무게/갯수)와 같은 종류의 단위일 때 기준단위(1kg/1개)당 단가."""
    p = parse_unit(unit)
    if p is None or p[0] != mode or not p[1]:
        return None
    return price / p[1]


@dataclass
class Channel:
    key: str
    label: str
    source_price: int            # KAMIS 원단가 (원/unit)
    source_unit: str
    unit_price: float | None     # 기준단위(kg 또는 개)당 판매단가 (보정 후)
    gross: int | None            # 총 판매액
    commission_pct: float
    net: int | None              # 수수료 차감 실수령액
    note: str
    estimated: bool              # 소매가 추정 등 가정 강도 표시


@dataclass
class CompareResult:
    found: bool
    crop_name: str
    amount: float | None         # 입력 판매량
    amount_unit: str             # 'kg' | '개'
    input_mode: str              # 'weight' | 'count'
    obs_date: str | None
    channels: list[Channel]
    best_key: str | None
    delta_net: int | None        # 1위-2위 실수령 차액
    message: str | None = None


async def compare_channels(
    *,
    category_code: str,
    item_code: str,
    kind_code: str,
    crop_name: str,
    amount: float | None,
) -> CompareResult:
    wholesale, retail = await asyncio.gather(
        kamis.fetch_recent_price(
            category_code=category_code, item_code=item_code,
            kind_code=kind_code, product_cls_code="02",
        ),
        kamis.fetch_recent_price(
            category_code=category_code, item_code=item_code,
            kind_code=kind_code, product_cls_code="01",
        ),
    )

    # 도매가 단위가 입력 모드(무게/갯수)를 결정한다.
    w_parsed = parse_unit(wholesale.unit) if wholesale is not None else None
    mode = w_parsed[0] if w_parsed else "weight"
    amount_unit = MODE_UNIT[mode]

    if wholesale is None:
        return CompareResult(
            found=False, crop_name=crop_name, amount=amount, amount_unit=amount_unit,
            input_mode=mode, obs_date=None, channels=[], best_key=None, delta_net=None,
            message="도매가 데이터가 없어 채널 비교를 할 수 없습니다.",
        )

    w_per_base = _per_base(wholesale.price, wholesale.unit, mode)

    # 직매장 단가는 소매가 기반. 소매가가 없거나 단위 종류(무게/갯수)가 도매와
    # 다르면 환산 불가 → 도매가에서 추정한다.
    retail_per_base = (
        _per_base(retail.price, retail.unit, mode) if retail is not None else None
    )
    if retail_per_base is not None:
        r_per_base = retail_per_base
        retail_estimated = False
        retail_src_price, retail_src_unit = retail.price, retail.unit  # type: ignore[union-attr]
    else:
        r_per_base = (w_per_base * RETAIL_FROM_WHOLESALE) if w_per_base else None
        retail_estimated = True
        retail_src_price, retail_src_unit = wholesale.price, wholesale.unit

    def revenue(per_base: float | None, commission: float, factor: float = 1.0):
        if per_base is None or amount is None:
            return None, None, None
        unit_price = per_base * factor
        gross = int(round(unit_price * amount))
        net = int(round(gross * (1 - commission)))
        return unit_price, gross, net

    direct_price, direct_gross, direct_net = revenue(
        r_per_base, DIRECT_COMMISSION, DIRECT_RETAIL_FACTOR
    )
    whole_price, whole_gross, whole_net = revenue(w_per_base, WHOLESALE_COMMISSION)

    direct_note = (
        "소매가 미반영 → 도매가 기준 추정" if retail_estimated
        else f"소매가의 {int(DIRECT_RETAIL_FACTOR * 100)}% 직거래 단가 가정"
    )

    channels = [
        Channel(
            key="direct",
            label="직매장 직거래",
            source_price=retail_src_price,
            source_unit=retail_src_unit,
            unit_price=round(direct_price, 1) if direct_price else None,
            gross=direct_gross,
            commission_pct=DIRECT_COMMISSION * 100,
            net=direct_net,
            note=direct_note,
            estimated=retail_estimated,
        ),
        Channel(
            key="wholesale",
            label="도매시장 출하",
            source_price=wholesale.price,
            source_unit=wholesale.unit,
            unit_price=round(whole_price, 1) if whole_price else None,
            gross=whole_gross,
            commission_pct=WHOLESALE_COMMISSION * 100,
            net=whole_net,
            note=f"위탁수수료 {int(WHOLESALE_COMMISSION * 100)}% 가정",
            estimated=False,
        ),
    ]

    rated = [c for c in channels if c.net is not None]
    rated.sort(key=lambda c: c.net or 0, reverse=True)
    best_key = rated[0].key if rated else None
    delta_net = (rated[0].net - rated[1].net) if len(rated) >= 2 else None

    return CompareResult(
        found=True,
        crop_name=crop_name,
        amount=amount,
        amount_unit=amount_unit,
        input_mode=mode,
        obs_date=wholesale.obs_date.isoformat(),
        channels=channels,
        best_key=best_key,
        delta_net=delta_net,
    )
