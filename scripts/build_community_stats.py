"""커뮤니티 비교 통계 시드 생성 → seed/community_stats.json.

실사용자 풀이 생기기 전까지 쓰는 합성 분포. crops_master 의 난이도·환경에서
결정론적으로 만들어 재실행해도 같은 값이 나온다 (난수 미사용).
실서비스 전환 시 이 시드를 실제 집계 쿼리로 교체한다.

값 설계 근거:
  - growers: 베란다 적합·난이도 낮은 작물일수록 많음 (상추·방토 인기 반영)
  - weeklyRecords 분위수: 주 1~7회 기록 분포 (Streak 설계와 일관)
  - completionRate: 난이도 역비례 (기획서의 '중도 포기율' 문제의식 반영)

실행: uv run python scripts/build_community_stats.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.planting import matrix  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "seed" / "community_stats.json"


def _stable_jitter(slug: str, span: int) -> int:
    """슬러그 문자합 기반 0..span-1 지터 (난수 없이 작물별 변화)."""
    return sum(ord(c) for c in slug) % span


def build() -> dict:
    crops = []
    total = 0
    for c in matrix.all_crops():
        slug = c["id"]
        difficulty = int(c.get("difficulty") or 3)
        envs = c.get("environments") or []
        # 베란다·실내 가능 + 쉬울수록 재배자 많음. 총합 ~1,400명 규모
        # (베타 초기 — 기획서 '6개월 내 5,000명 목표'와 정합).
        base = 10 + (5 - difficulty) * 8
        if "베란다" in envs:
            base += 14
        if "실내" in envs:
            base += 6
        growers = base + _stable_jitter(slug, 13)
        total += growers

        # 기획서 '도시 텃밭 중도 포기율 약 70%' 와 정합 — 평균 완주율 ~30%대
        completion = round(max(0.12, 0.45 - difficulty * 0.06) + _stable_jitter(slug, 7) / 100, 2)
        crops.append(
            {
                "slug": slug,
                "cropName": c["name"],
                "growers": growers,
                "weeklyRecords": {"p25": 1, "p50": 3, "p75": 5, "p90": 7},
                "completionRate": completion,
                "medianDaysToHarvest": (c.get("days_to_harvest") or [45])[0],
            }
        )

    return {
        "note": "합성 커뮤니티 통계 (실사용자 풀 확보 전 데모·비교용). build_community_stats.py 로 재생성.",
        "all": {
            "growers": total,
            "weeklyRecords": {"p25": 1, "p50": 3, "p75": 5, "p90": 7},
        },
        "crops": crops,
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {OUT} (crops={len(data['crops'])}, totalGrowers={data['all']['growers']})")
