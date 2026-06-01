"""스마트팜 우수농가(SmartFarmDATA2) 시계열 적재 / 연결 점검 CLI.

데이터: 완숙토마토·딸기·파프리카 우수농가 91호 (seed/smartfarm_farms.json).
온실환경(env)·작물생육(growth)·생산량(prod) 3종 시계열을 농가코드별로 받아
data/smartfarm/{env,growth,prod}/<frmhs_id>.json 에 로컬 캐시한다.

사용:
    # 연결 점검 (농가 1호 × 3 op, 기본 81)
    uv run python scripts/ingest_smartfarm.py --probe
    uv run python scripts/ingest_smartfarm.py --probe S17

    # 전체 적재
    uv run python scripts/ingest_smartfarm.py
    # 작목 한정 / 개수 제한 / op 한정
    uv run python scripts/ingest_smartfarm.py --crop tomato --limit 5 --only prod

장애 처리: 제공자 서버가 500("Unexpected errors")을 주면 SmartFarmUpstreamError 로
구분해 즉시 중단하고 명확히 안내한다 (2026-06 현재 해당 API 장애 상태).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data import smartfarm
from app.data.smartfarm import SmartFarmError, SmartFarmUpstreamError

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "smartfarm"
OPS = ("env", "growth", "prod")

_FETCH = {
    "env": smartfarm.fetch_env,
    "growth": smartfarm.fetch_growth,
    "prod": smartfarm.fetch_prod,
}


def _write(op: str, frmhs_id: str, rows: list) -> Path:
    d = OUT_DIR / op
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{frmhs_id}.json"
    path.write_text(
        json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return path


async def _probe(frmhs_id: str) -> int:
    print(f"[probe] 농가코드={frmhs_id}  endpoint={smartfarm.BASE}")
    ok = True
    for op in OPS:
        try:
            rows = await _FETCH[op](frmhs_id)
            print(f"  {op:6s}: OK  {len(rows)}건")
        except SmartFarmUpstreamError as e:
            print(f"  {op:6s}: 서버장애 → {e}")
            ok = False
        except SmartFarmError as e:
            print(f"  {op:6s}: 실패 → {e}")
            ok = False
    if not ok:
        print(
            "\n[진단] 호출 자체는 올바름(끝점/필수파라미터 정상). 응답이 서버장애면 "
            "제공자(농진청) 백엔드 문제이므로 잠시 후 재시도하세요."
        )
    return 0 if ok else 1


async def _ingest(crop: str | None, limit: int | None, only: str | None) -> int:
    farms = smartfarm.load_farms(crop_id=crop)
    if limit:
        farms = farms[:limit]
    ops = (only,) if only else OPS
    print(f"[ingest] 농가 {len(farms)}호 × op {list(ops)} → {OUT_DIR}")

    done = 0
    for f in farms:
        for op in ops:
            try:
                rows = await _FETCH[op](f.frmhs_id)
            except SmartFarmUpstreamError as e:
                print(f"  ! 서버장애로 중단 ({f.frmhs_id}/{op}): {e}")
                print(f"  진행: {done}건 적재 후 중단. 복구 후 동일 명령 재실행.")
                return 2
            except SmartFarmError as e:
                print(f"  - 건너뜀 ({f.frmhs_id}/{op}): {e}")
                continue
            path = _write(op, f.frmhs_id, rows)
            done += 1
            print(f"  + {f.frmhs_id:6s} {op:6s} {len(rows):5d}건 → {path.name}")
    print(f"[done] 적재 완료 {done}건")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="SmartFarmDATA2 우수농가 적재/점검")
    p.add_argument("--probe", nargs="?", const="81", metavar="FRMHS_ID",
                   help="연결 점검 (기본 농가 81)")
    p.add_argument("--crop", choices=["tomato", "strawberry", "paprika"], help="작목 한정")
    p.add_argument("--limit", type=int, help="농가 수 제한")
    p.add_argument("--only", choices=list(OPS), help="op 한정")
    args = p.parse_args()

    if args.probe is not None:
        return asyncio.run(_probe(args.probe))
    return asyncio.run(_ingest(args.crop, args.limit, args.only))


if __name__ == "__main__":
    sys.exit(main())
