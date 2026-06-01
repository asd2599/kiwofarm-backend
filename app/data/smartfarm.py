"""농촌진흥청 스마트팜 우수농가 공개데이터 (SmartFarmDATA2) 클라이언트.

데이터셋: 공공데이터포털 15042594. 대상 작목 = 완숙토마토·딸기·파프리카 (시설).
인증키: settings.data_go_kr_key.

서비스 URL: http://apis.data.go.kr/1390000/SmartFarmDATA2  (★ http 전용 — 게이트웨이 SSL 미지원)
오퍼레이션 (모두 GET, returnType=xml|json). 공통 필수: searchFrmhsCode(농가코드):
  envdatarqst  온실환경  (측정시각별, 시간단위)  옵션 searchMeasDt=yyyyMMddHH
  grwdatarqst  작물생육  (주차별)
  prddatarqst  생산량    (주차별, frmAr·outtrn)

농가코드(searchFrmhsCode) 목록은 seed/smartfarm_farms.json 에 적재돼 있다 (기술명세서 3장).

주의(2026-06 기준): 제공자 백엔드가 모든 요청에 HTTP 500 "Unexpected errors" 를 반환하는
장애 상태. 더미키로도 동일 → 우리 키/파라미터 문제 아님. 본 클라이언트는 그 500을
SmartFarmUpstreamError 로 명확히 구분해 올린다. 복구 시 수정 없이 동작하도록 명세 기준으로 구현.

CLAUDE.md 규칙: 공공데이터 응답은 로컬 캐시(treat upstream as flaky). 적재는
scripts/ingest_smartfarm.py 가 담당 (data/smartfarm/*.json).
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

BASE = "http://apis.data.go.kr/1390000/SmartFarmDATA2"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)

OP_ENV = "envdatarqst"
OP_GROWTH = "grwdatarqst"
OP_PROD = "prddatarqst"

SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "smartfarm_farms.json"


class SmartFarmError(RuntimeError):
    """스마트팜 API 호출 실패(공통)."""


class SmartFarmUpstreamError(SmartFarmError):
    """제공자 서버 장애 — HTTP 5xx / 'Unexpected errors' 등. 재시도 대상."""


# ─────────────────────── 농가 로스터 (seed) ───────────────────────


@dataclass(frozen=True)
class FarmRecord:
    """우수농가 한 호의 메타 (기술명세서 농가코드표 기준)."""

    frmhs_id: str
    crop: str  # 완숙토마토 | 딸기 | 파프리카
    crop_id: str  # tomato | strawberry | paprika
    season: str
    facility: str  # vinyl_house | glass_house
    province: str
    city: str
    area_pyeong: int
    cultivar: str | None
    density_per_pyeong: float | None
    medium: str | None
    transplant_date: str | None
    first_harvest_date: str | None
    last_harvest_date: str | None


def load_farms(crop_id: str | None = None, province: str | None = None) -> list[FarmRecord]:
    """seed/smartfarm_farms.json 로스터 로드 (작목·도 필터)."""
    rows = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    out: list[FarmRecord] = []
    for r in rows:
        if crop_id and r["crop_id"] != crop_id:
            continue
        if province and r["province"] != province:
            continue
        out.append(FarmRecord(**r))
    return out


# ─────────────────────── 응답 타입 ───────────────────────


@dataclass(frozen=True)
class EnvReading:
    """온실환경 측정 1건 (envdatarqst)."""

    frmhs_id: str
    meas_dt: str  # yyyyMMddHHmmss
    in_temp: float | None  # inTp 내부온도(℃)
    out_temp: float | None  # outTp 외부온도(℃)
    in_humidity: float | None  # inHd 내부습도(%)
    in_co2: float | None  # inCo2 (ppm)
    out_wind: float | None  # outWs 풍속(m/s)
    acc_solar: float | None  # acSlrdQy 누적일사량(j/cm2) — 일 23:00 만 존재
    ec: float | None
    ph: float | None
    supply_count: float | None  # cunt 일 급액횟수
    day_supply: float | None  # daysuplyqy 일 급액량(cc/주)
    once_supply: float | None  # otmsuplyqy 1회 급액량(cc/주)


@dataclass(frozen=True)
class GrowthReading:
    """작물생육 측정 1건 (grwdatarqst, 주차별)."""

    frmhs_id: str
    year: int
    week: int
    month: int
    raw: dict[str, Any]  # 생육 지표는 작목별 항목이 많아 원본 dict 보존


@dataclass(frozen=True)
class ProdReading:
    """생산량 1건 (prddatarqst, 주차별)."""

    frmhs_id: str
    year: int
    week: int
    month: int
    area_3_3m2: float | None  # frmAr 면적 (평=3.3㎡ 단위)
    outturn: float | None  # outtrn 생산량 (Kg/3.3㎡)


# ─────────────────────── 저수준 요청 ───────────────────────


def _check_plain_error(text: str) -> None:
    """게이트웨이/백엔드의 평문 에러를 감지해 적절한 예외로 변환."""
    head = text.lstrip()[:200]
    if head.startswith("<"):
        return  # XML 정상 응답
    low = head.lower()
    if "unexpected error" in low or "internal server" in low:
        raise SmartFarmUpstreamError(f"제공자 서버 장애(500): {head!r}")
    if "api not found" in low:
        raise SmartFarmError(f"오퍼레이션 경로 오류(API not found): {head!r}")
    if "service key" in low or "service_key" in low:
        raise SmartFarmError(f"서비스키 오류: {head!r}")
    raise SmartFarmError(f"예상치 못한 응답: {head!r}")


def _parse_items_xml(text: str) -> tuple[list[dict[str, str]], int]:
    """XML 응답 → (item dict 리스트, totalCount)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise SmartFarmError(f"XML 파싱 실패: {e}") from e

    code_el = root.find(".//resultCode")
    if code_el is not None and (code_el.text or "").strip() not in ("00", "0"):
        msg = root.findtext(".//resultMsg", default="")
        raise SmartFarmError(f"API 결과코드 {code_el.text}: {msg}")

    total = int(root.findtext(".//totalCount", default="0") or 0)
    items: list[dict[str, str]] = []
    for item in root.findall(".//items/item"):
        items.append({child.tag: (child.text or "").strip() for child in item})
    return items, total


async def _request(
    client: httpx.AsyncClient, op: str, frmhs_code: str, *, page_no: int, page_size: int,
    extra: dict[str, str] | None = None,
) -> tuple[list[dict[str, str]], int]:
    """단일 페이지 호출. returnType=xml 고정(파싱 안정). 빈 키면 즉시 에러."""
    if not settings.data_go_kr_key:
        raise SmartFarmError("DATA_GO_KR_KEY 가 설정되지 않았습니다 (.env 확인)")

    params = {
        "serviceKey": settings.data_go_kr_key,
        "searchFrmhsCode": frmhs_code,
        "pageNo": str(page_no),
        "pageSize": str(page_size),
        "returnType": "xml",
        **(extra or {}),
    }
    try:
        resp = await client.get(f"{BASE}/{op}", params=params)
    except httpx.HTTPError as e:
        raise SmartFarmUpstreamError(f"네트워크 오류: {e}") from e

    if resp.status_code >= 500:
        _check_plain_error(resp.text)  # 보통 'Unexpected errors'
        raise SmartFarmUpstreamError(f"HTTP {resp.status_code}: {resp.text[:200]!r}")
    if resp.status_code != 200:
        raise SmartFarmError(f"HTTP {resp.status_code}: {resp.text[:200]!r}")

    _check_plain_error(resp.text)
    return _parse_items_xml(resp.text)


async def _fetch_all(
    op: str, frmhs_code: str, *, page_size: int = 500, extra: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """페이지네이션 순회해 전체 item 수집."""
    collected: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        page = 1
        while True:
            items, total = await _request(
                client, op, frmhs_code, page_no=page, page_size=page_size, extra=extra
            )
            collected.extend(items)
            if not items or len(collected) >= total or len(items) < page_size:
                break
            page += 1
    return collected


# ─────────────────────── 타입 변환 헬퍼 ───────────────────────


def _f(d: dict[str, str], key: str) -> float | None:
    v = d.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _i(d: dict[str, str], key: str, default: int = 0) -> int:
    v = d.get(key)
    try:
        return int(float(v)) if v not in (None, "") else default
    except ValueError:
        return default


# ─────────────────────── 공개 fetch API ───────────────────────


async def fetch_env(frmhs_code: str, meas_dt: str | None = None) -> list[EnvReading]:
    """온실환경 시계열. meas_dt(yyyyMMddHH) 주면 해당 시각만."""
    extra = {"searchMeasDt": meas_dt} if meas_dt else None
    rows = await _fetch_all(OP_ENV, frmhs_code, extra=extra)
    return [
        EnvReading(
            frmhs_id=d.get("frmhsId", frmhs_code),
            meas_dt=d.get("measDtStr", ""),
            in_temp=_f(d, "inTp"), out_temp=_f(d, "outTp"), in_humidity=_f(d, "inHd"),
            in_co2=_f(d, "inCo2"), out_wind=_f(d, "outWs"), acc_solar=_f(d, "acSlrdQy"),
            ec=_f(d, "ec"), ph=_f(d, "ph"), supply_count=_f(d, "cunt"),
            day_supply=_f(d, "daysuplyqy"), once_supply=_f(d, "otmsuplyqy"),
        )
        for d in rows
    ]


async def fetch_growth(frmhs_code: str) -> list[GrowthReading]:
    """작물생육 주차별 시계열."""
    rows = await _fetch_all(OP_GROWTH, frmhs_code)
    return [
        GrowthReading(
            frmhs_id=d.get("frmhsId", frmhs_code),
            year=_i(d, "frmYear"), week=_i(d, "frmWeek"), month=_i(d, "frmMonth"), raw=d,
        )
        for d in rows
    ]


async def fetch_prod(frmhs_code: str) -> list[ProdReading]:
    """생산량 주차별 시계열."""
    rows = await _fetch_all(OP_PROD, frmhs_code)
    return [
        ProdReading(
            frmhs_id=d.get("frmhsId", frmhs_code),
            year=_i(d, "frmYear"), week=_i(d, "frmWeek"), month=_i(d, "frmMonth"),
            area_3_3m2=_f(d, "frmAr"), outturn=_f(d, "outtrn"),
        )
        for d in rows
    ]
