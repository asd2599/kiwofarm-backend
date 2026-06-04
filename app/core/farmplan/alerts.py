"""위기 알림 합성 — 병해충 발생정보 + 기상 특보를 정규화된 알림으로 만든다.

현재: 농사로 병해충발생정보(전국 회보) 현재 기간 1건.
추후: 기상청 기상특보(지역) 연동 → _weather_alerts 채우기.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.core.farmplan.coach import pest_situation
from app.data import nongsaro_dbyhs as dbyhs


@dataclass(frozen=True)
class CrisisAlert:
    type: str  # pest | weather
    severity: str  # info | warn | danger
    title: str
    detail: str
    source: str  # 출처 라벨
    link: str | None  # 원문 URL
    date: str | None  # 관련 일자/기간 라벨


async def _pest_alerts(crop_name: str, region: str, ref: date) -> list[CrisisAlert]:
    b = await dbyhs.fetch_current(ref)
    if b is None:
        return []
    current = bool(
        b.period_start and b.period_end and b.period_start <= ref <= b.period_end
    )
    period = (
        f"{b.period_start.isoformat()} ~ {b.period_end.isoformat()}"
        if b.period_start and b.period_end
        else (b.regist_date or None)
    )

    # 회보 PDF 본문을 읽어 그 작물의 '현재 상황'을 요약(실시간 느낌). 실패 시 일반 안내로 폴백.
    title = b.title
    detail = (
        "이 시기 전국 농작물 병해충 발생정보입니다. "
        "원문에서 작물별 주의 병해충을 확인하세요."
    )
    text = await dbyhs.fetch_bulletin_text(b)
    if text:
        summary = await pest_situation(crop_name, region, period or b.title, text)
        if summary:
            title, detail = summary

    return [
        CrisisAlert(
            type="pest",
            # 이번 기간에 해당하면 위기(빨강), 지난 최신 회보면 주의(노랑).
            severity="danger" if current else "warn",
            title=title,
            detail=detail,
            source=f"농사로 병해충발생정보 · {b.title}",
            link=b.down_url or None,
            date=period,
        )
    ]


async def _weather_alerts(province: str | None, ref: date) -> list[CrisisAlert]:
    """기상청 기상특보 연동 자리(가이드 수령 후 구현). 지금은 빈 리스트."""
    _ = (province, ref)
    return []


_SEVERITY_RANK = {"danger": 0, "warn": 1, "info": 2}


async def build_alerts(
    crop_name: str, region: str, province: str | None, ref: date
) -> list[CrisisAlert]:
    """위기 알림 목록. 심각도 높은(danger) 순으로 정렬."""
    pest = await _pest_alerts(crop_name, region, ref)
    weather = await _weather_alerts(province, ref)
    alerts = weather + pest
    return sorted(alerts, key=lambda a: _SEVERITY_RANK.get(a.severity, 9))
