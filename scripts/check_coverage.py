"""[DEPRECATED] 이 스크립트는 신뢰 불가 리포트를 생성해 폐기되었습니다.

폐기 사유 (2026-06-04 실호출로 확인된 버그):
  1) 품목명을 `kidofcomdtySeCodeNm` 로 읽었으나 실제 필드는 `codeNm` → 전부 빈 문자열.
  2) 빈 문자열 부분일치(`k in nc`)로 40종이 마지막 그룹(210011)에 전부 오매칭
     → "미커버 0종" 이라는 거짓 결과.
  3) 애초에 workScheduleGrpList 는 농사유형 대분류(논농사/밭농사/채소/…)라
     개별 작물(상추·토마토…)과 매칭 대상이 아니었음. 작물은 workScheduleLst 의
     `sj`, 조인키는 `cntntsNo`.

대체:
  uv run python scripts/build_crops_master.py
  → data/crops_master.json + data/coverage_report.md + data/farmwork_catalog.json
"""

import sys

sys.exit(
    "DEPRECATED: scripts/check_coverage.py 는 폐기됨. "
    "대신 `uv run python scripts/build_crops_master.py` 를 실행하세요."
)
