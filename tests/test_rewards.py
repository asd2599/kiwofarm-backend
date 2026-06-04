"""보상 도메인 순수 계산 로직 테스트 (DB 불필요)."""

from datetime import date

from app.core.rewards.badges import _evaluate
from app.core.rewards.streak import _streaks


def test_streak_empty():
    assert _streaks(set(), date(2026, 6, 4)) == (0, 0)


def test_streak_current_from_today():
    days = {date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4)}
    assert _streaks(days, date(2026, 6, 4)) == (3, 3)


def test_streak_current_from_yesterday():
    # 오늘 기록이 아직 없어도 어제까지의 연속은 유지된다
    days = {date(2026, 6, 2), date(2026, 6, 3)}
    assert _streaks(days, date(2026, 6, 4)) == (2, 2)


def test_streak_broken_keeps_best():
    days = {
        date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3), date(2026, 5, 4),
        date(2026, 6, 4),
    }
    current, best = _streaks(days, date(2026, 6, 4))
    assert current == 1
    assert best == 4


def test_top_percent_interpolation():
    from app.core.rewards.compare import _top_percent

    q = {"p25": 1, "p50": 3, "p75": 5, "p90": 7}
    assert _top_percent(0, q) == 100
    assert _top_percent(3, q) == 50  # 중앙값 = 상위 50%
    assert _top_percent(5, q) == 25
    assert _top_percent(10, q) <= 10  # p90 초과 → 상위 10% 이내
    assert _top_percent(2, q) == 60  # 1~3 구간 보간(62.5 → 5단위 banker's rounding)


def test_weekly_message_positive_vs_encourage():
    from app.core.rewards.compare import _weekly_message

    q = {"p25": 1, "p50": 3, "p75": 5, "p90": 7}
    positive, msg = _weekly_message(5, 25, q)
    assert positive and "상위 25%" in msg
    positive, msg = _weekly_message(1, 75, q)
    assert not positive
    assert "더 기록하면" in msg  # 하위권엔 순위 대신 격려


def test_badge_evaluate_progress():
    stats = {"totalHarvests": 1, "collectedCrops": 4, "bestStreak": 7}
    badges = {b["id"]: b for b in _evaluate(stats)}
    assert badges["first_harvest"]["achieved"] is True
    assert badges["collector_3"]["achieved"] is True
    assert badges["collector_5"]["achieved"] is False
    assert badges["collector_5"]["progress"] == 0.8
    assert badges["streak_7"]["achieved"] is True
    assert badges["streak_30"]["achieved"] is False
    assert badges["harvester_10"]["current"] == 1
