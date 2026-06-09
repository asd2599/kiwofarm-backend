"""결정적 작목 추천 스코어 엔진 (부록 B).

"매트릭스가 결정하고 AI는 설명한다" — 점수 산정은 전부 결정적 코드.
하드 필터(장소 부적합 제외) → 시기/일조/환경/공간/경험/선호 가중 점수 → top N.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.planting import matrix, region
from app.schemas.planting import (
    CalendarAction,
    PlantingInput,
    PlantingRecommendResponse,
    RecommendationItem,
)

# 사용자 일조 입력 → 대표 직사광 시간
SUN_HOURS_VALUE = {"<3h": 2, "3~5h": 4, ">5h": 6}
# 경험 → 감당 가능 난이도 상한 가중
EXP_RANK = {"처음": 1, "1~2년": 2, "3년+": 3}
SPACE_MIN_AREA = {"소": 0.0, "중": 1.0, "대": 3.0}


def _now_month() -> int:
    return datetime.now().month


def _next(month: int) -> int:
    return month % 12 + 1


def _month_of(date_str: str | None) -> int | None:
    """'YYYY-MM-DD' → 월(1~12). 비었거나 형식 오류면 None."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").month
    except ValueError:
        return None


def _sun_match(user_hours: int, crop_min: int) -> int:
    """0~20. 충분하면 만점, 부족하면 시간당 -7 감점."""
    diff = user_hours - crop_min
    if diff >= 0:
        return 20
    return max(0, 20 + diff * 7)


def _env_fit(crop: dict[str, Any], user_hours: int) -> int:
    """0~15. 반음지 가능 작물이 저일조 환경에 들어오면 가점."""
    score = 10
    if crop["sunlight"] == "반음지가능" and user_hours < 4:
        score += 5
    elif crop["sunlight"] == "양지" and user_hours >= 6:
        score += 3
    return min(15, score)


def _space_fit(area_m2: float | None, space: str, container_ok: bool, facility: list[str]) -> int:
    """0~10. 면적이 작물 요구공간을 충족하면 만점."""
    need = SPACE_MIN_AREA.get(space, 0.0)
    if area_m2 is None:
        # 면적 미입력: 화분 가능 작물이면 중간, 아니면 보수적
        return 8 if container_ok else 5
    if area_m2 >= need:
        return 10
    if container_ok and ("화분" in facility or "플랜터" in facility):
        return 7
    return max(0, 10 - int((need - area_m2) * 3))


def _exp_fit(experience: str, difficulty: int) -> int:
    """0~10. 경험이 난이도를 감당하면 만점, 초보+고난도면 감점."""
    rank = EXP_RANK.get(experience, 1)
    gap = difficulty - rank
    if gap <= 0:
        return 10
    return max(0, 10 - gap * 5)


def _pref_bonus(prefs: list[str], category: str) -> int:
    return 5 if category in prefs else 0


def _to_actions(entries: list[dict[str, Any]]) -> list[CalendarAction]:
    # 동일 (action,method) 중복 제거 후 표시
    seen: set[tuple[str, str]] = set()
    out: list[CalendarAction] = []
    for e in entries:
        key = (e.get("action", ""), e.get("method", "") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(
            CalendarAction(
                action=e.get("action", ""),
                method=e.get("method"),
                label=e.get("label"),
                plain=e.get("plain"),
            )
        )
    return out


def recommend(inp: PlantingInput, now_month: int | None = None) -> PlantingRecommendResponse:
    zone = region.zone_of(inp.sigungu)
    base_month = now_month or _now_month()
    # 시작 날짜를 직접 고르면 그 달 기준으로 추천. 비우면 오늘 기준.
    month = _month_of(inp.startDate) or base_month
    nxt = _next(month)
    user_hours = SUN_HOURS_VALUE.get(inp.sun_hours, 4)

    scored: list[tuple[int, bool, RecommendationItem]] = []
    next_pool: list[tuple[int, str]] = []

    for crop in matrix.all_crops():
        if inp.place not in crop["environments"]:  # 하드 필터
            continue

        m_adj = region.adjust(month, zone)
        plantable = matrix.plantable_in_month(crop, m_adj)
        plantable_next = matrix.plantable_in_month(crop, region.adjust(nxt, zone))

        score = 0
        reasons: list[str] = []
        if plantable:
            score += 40
            reasons.append("이번 달 심기 적기")
        elif plantable_next:
            score += 15
            reasons.append("다음 달 심기 가능")

        s_sun = _sun_match(user_hours, crop["min_sun_hours"])
        score += s_sun
        if s_sun >= 18:
            reasons.append("일조 조건 잘 맞음")
        elif s_sun <= 7:
            reasons.append("일조가 다소 부족")

        score += _env_fit(crop, user_hours)
        if crop["sunlight"] == "반음지가능":
            reasons.append("반음지에서도 잘 자람")

        score += _space_fit(inp.area_m2, crop["space"], crop["container_ok"], inp.facility)
        score += _exp_fit(inp.experience, crop["difficulty"])
        if crop["difficulty"] == 1:
            reasons.append("초보도 키우기 쉬움")
        score += _pref_bonus(inp.prefs, crop["category"])
        if crop["category"] in inp.prefs:
            reasons.append(f"선호하는 {crop['category']}")

        item = RecommendationItem(
            crop_id=crop["id"],
            name=crop["name"],
            category=crop["category"],
            difficulty=crop["difficulty"],
            score=score,
            reasons=reasons,
            plantable_now=plantable,
            plantable_next=plantable_next,
            calendar_this_month=_to_actions(matrix.actions_in_month(crop, m_adj)),
            days_to_harvest=crop["days_to_harvest"],
            source=crop["source"],
            needs_review=crop["needs_review"],
        )
        scored.append((score, plantable, item))
        if plantable_next and not plantable:
            next_pool.append((score, crop["id"]))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [it for _, _, it in scored[: inp.top_n]]
    top_ids = {it.crop_id for it in top}
    next_pool.sort(key=lambda x: x[0], reverse=True)
    next_candidates = [cid for _, cid in next_pool if cid not in top_ids][:6]

    return PlantingRecommendResponse(
        month=month,
        zone=zone,
        recommendations=top,
        next_month_candidates=next_candidates,
    )
