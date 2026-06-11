"""작물 수확 레벨 — 5회 수확마다 +1레벨(최대 Lv5). 도감 표시 + 레벨업 팜 지급 공용.

collection(레벨 표시)·badges(레벨업 팜 지급) 양쪽에서 쓰므로 순환 임포트를 피해 분리.
레벨별 팜 = 뱃지 난이도 스케일과 동일.
"""

from __future__ import annotations

CROP_LEVEL_STEP = 5
CROP_MAX_LEVEL = 5
LEVEL_REWARD = {1: 10, 2: 20, 3: 40, 4: 70, 5: 120}


def crop_level(harvest_count: int) -> int:
    return min(CROP_MAX_LEVEL, harvest_count // CROP_LEVEL_STEP)


def level_reward(level: int) -> int:
    return LEVEL_REWARD.get(level, 0)
