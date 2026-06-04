"""키워팜 · 심기 — 작물 마스터 + 커버리지 단일 신뢰 생성기.

농사로 farmWorkingPlanNew 를 실호출해 작물 카탈로그(group/sj/cntntsNo)를 만든 뒤,
아래 CROPS(40종 큐레이션 메타 + 실제 작물명 수동 매핑)와 조인해
  - data/crops_master.json   (작물 고정 메타 + nongsaro_cntnts_no 조인키)
  - data/coverage_report.md  (정직한 커버/미커버 리포트)
  - data/farmwork_catalog.json (감사용 전체 작물 덤프)
를 생성한다. 매핑은 fuzzy 가 아니라 실제 카탈로그를 보고 (group, sj)로 확정한 값.

핵심 정정(2026-06-04 실호출로 확인):
  - workScheduleGrpList = 농사유형 대분류 10개(논농사/밭농사/채소/과수/…). 개별작물 아님.
  - 작물명 필드 = `sj`, per-crop 조인키 = `cntntsNo`(kidofcomdtySeCode 아님).
  - 응답은 정상 UTF-8 (cp949 콘솔에서만 깨져 보임).

실행: uv run python scripts/build_crops_master.py
환경: .env 의 NONGSARO_API_KEY
"""
# 작물 메타는 한 줄당 1종 데이터 리터럴이라 의도적으로 길다.
# ruff: noqa: E501

from __future__ import annotations

import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
load_dotenv(ROOT / ".env")

KEY = os.environ.get("NONGSARO_API_KEY")
BASE = "http://api.nongsaro.go.kr/service/farmWorkingPlanNew"
VERSION = "2026-06-04"

# ── 40종 작물 마스터 ───────────────────────────────────────────────
# nongsaro: [(group, sj)] — 실제 농사로 카탈로그에서 확정한 작물명. 빈 리스트 = 미커버.
# 미커버(허브·니치)는 텃밭가꾸기(fildMnfct)+AI보강+검수로 캘린더 보강 예정.
# 메타: difficulty 1~3 / environments / sunlight / min_sun_hours / space / container_ok
#       / days_to_harvest[min,max] / water_need.  (스키마: Task 부록 A)
CROPS: list[dict] = [
    # ── 잎채소 13 ──
    {"id": "lettuce", "name": "상추", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지", "실내"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [30, 45], "water_need": "보통", "nongsaro": [("채소", "상추")]},
    {"id": "spinach", "name": "시금치", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [40, 55], "water_need": "보통", "nongsaro": [("채소", "시금치")]},
    {"id": "bokchoy", "name": "청경채", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지", "실내"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [30, 45], "water_need": "보통", "nongsaro": [("채소", "청경채")]},
    {"id": "crown_daisy", "name": "쑥갓", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [40, 50], "water_need": "보통", "nongsaro": [("채소", "쑥갓")]},
    {"id": "garlic_chives", "name": "부추", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [60, 90], "water_need": "보통", "nongsaro": [("채소", "부추")]},
    {"id": "kale", "name": "케일", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "중", "container_ok": True, "days_to_harvest": [50, 70], "water_need": "보통", "nongsaro": []},
    {"id": "mustard_greens", "name": "갓", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [40, 50], "water_need": "보통", "nongsaro": [("채소", "갓")]},
    {"id": "chard", "name": "근대", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [50, 60], "water_need": "보통", "nongsaro": [("채소", "근대")]},
    {"id": "curled_mallow", "name": "아욱", "category": "잎채소", "difficulty": 1, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 4, "space": "소", "container_ok": True, "days_to_harvest": [40, 55], "water_need": "보통", "nongsaro": [("채소", "아욱")]},
    {"id": "chicory", "name": "치커리", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [40, 55], "water_need": "보통", "nongsaro": [("채소", "치커리(쌈용, 잎치커리)"), ("채소", "치커리(치콘,뿌리치커리)")]},
    {"id": "arugula", "name": "루꼴라", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지", "실내"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [30, 40], "water_need": "보통", "nongsaro": []},
    {"id": "baby_napa", "name": "엇갈이배추", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [30, 45], "water_need": "보통", "nongsaro": []},
    {"id": "perilla", "name": "깻잎", "category": "잎채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "양지", "min_sun_hours": 4, "space": "중", "container_ok": True, "days_to_harvest": [50, 60], "water_need": "보통", "nongsaro": [("밭농사", "들깨(잎)")]},
    # ── 열매채소 13 ──
    {"id": "cherry_tomato", "name": "방울토마토", "category": "열매채소", "difficulty": 2, "environments": ["베란다", "옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "중", "container_ok": True, "days_to_harvest": [70, 90], "water_need": "보통", "nongsaro": [("채소", "토마토,방울토마토")]},
    {"id": "tomato", "name": "토마토", "category": "열매채소", "difficulty": 2, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "대", "container_ok": True, "days_to_harvest": [80, 100], "water_need": "많음", "nongsaro": [("채소", "토마토,방울토마토")]},
    {"id": "chili_pepper", "name": "고추", "category": "열매채소", "difficulty": 2, "environments": ["베란다", "옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "중", "container_ok": True, "days_to_harvest": [80, 100], "water_need": "보통", "nongsaro": [("채소", "고추(보통재배)"), ("채소", "고추(촉성재배)")]},
    {"id": "paprika", "name": "파프리카", "category": "열매채소", "difficulty": 3, "environments": ["베란다", "옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "중", "container_ok": True, "days_to_harvest": [90, 110], "water_need": "많음", "nongsaro": [("채소", "파프리카")]},
    {"id": "eggplant", "name": "가지", "category": "열매채소", "difficulty": 2, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "중", "container_ok": True, "days_to_harvest": [80, 100], "water_need": "많음", "nongsaro": [("채소", "가지")]},
    {"id": "cucumber", "name": "오이", "category": "열매채소", "difficulty": 2, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "대", "container_ok": True, "days_to_harvest": [50, 70], "water_need": "많음", "nongsaro": [("채소", "오이")]},
    {"id": "korean_zucchini", "name": "애호박", "category": "열매채소", "difficulty": 2, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "대", "container_ok": True, "days_to_harvest": [50, 60], "water_need": "많음", "nongsaro": []},
    {"id": "pumpkin", "name": "호박", "category": "열매채소", "difficulty": 2, "environments": ["노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "대", "container_ok": False, "days_to_harvest": [80, 100], "water_need": "많음", "nongsaro": [("채소", "호박")]},
    {"id": "bitter_melon", "name": "여주", "category": "열매채소", "difficulty": 2, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "대", "container_ok": True, "days_to_harvest": [70, 90], "water_need": "많음", "nongsaro": []},
    {"id": "strawberry", "name": "딸기", "category": "열매채소", "difficulty": 3, "environments": ["베란다", "옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "중", "container_ok": True, "days_to_harvest": [180, 210], "water_need": "보통", "nongsaro": [("채소", "딸기(촉성재배)"), ("채소", "딸기(사계성여름재배)")]},
    {"id": "corn", "name": "옥수수", "category": "열매채소", "difficulty": 2, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "대", "container_ok": False, "days_to_harvest": [90, 110], "water_need": "보통", "nongsaro": [("밭농사", "옥수수")]},
    {"id": "pea", "name": "완두", "category": "열매채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "중", "container_ok": True, "days_to_harvest": [60, 80], "water_need": "보통", "nongsaro": [("밭농사", "완두")]},
    {"id": "kidney_bean", "name": "강낭콩", "category": "열매채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "중", "container_ok": True, "days_to_harvest": [50, 70], "water_need": "보통", "nongsaro": [("밭농사", "강낭콩")]},
    # ── 뿌리채소 11 ──
    {"id": "radish", "name": "무", "category": "뿌리채소", "difficulty": 1, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "중", "container_ok": True, "days_to_harvest": [50, 70], "water_need": "보통", "nongsaro": [("채소", "무"), ("채소", "무(고랭지재배)")]},
    {"id": "altari_radish", "name": "알타리무", "category": "뿌리채소", "difficulty": 1, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "소", "container_ok": True, "days_to_harvest": [40, 50], "water_need": "보통", "nongsaro": []},
    {"id": "carrot", "name": "당근", "category": "뿌리채소", "difficulty": 2, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "중", "container_ok": True, "days_to_harvest": [90, 120], "water_need": "보통", "nongsaro": [("채소", "당근")]},
    {"id": "potato", "name": "감자", "category": "뿌리채소", "difficulty": 1, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "대", "container_ok": True, "days_to_harvest": [90, 110], "water_need": "보통", "nongsaro": [("밭농사", "감자")]},
    {"id": "sweet_potato", "name": "고구마", "category": "뿌리채소", "difficulty": 1, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "대", "container_ok": False, "days_to_harvest": [120, 150], "water_need": "적음", "nongsaro": [("밭농사", "고구마")]},
    {"id": "onion", "name": "양파", "category": "뿌리채소", "difficulty": 2, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 6, "space": "중", "container_ok": True, "days_to_harvest": [210, 240], "water_need": "보통", "nongsaro": [("채소", "양파")]},
    {"id": "garlic", "name": "마늘", "category": "뿌리채소", "difficulty": 1, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "중", "container_ok": True, "days_to_harvest": [220, 250], "water_need": "보통", "nongsaro": [("채소", "마늘")]},
    {"id": "spring_onion", "name": "쪽파", "category": "뿌리채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [50, 70], "water_need": "보통", "nongsaro": [("채소", "쪽파")]},
    {"id": "green_onion", "name": "대파", "category": "뿌리채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "양지", "min_sun_hours": 4, "space": "중", "container_ok": True, "days_to_harvest": [60, 90], "water_need": "보통", "nongsaro": [("채소", "파")]},
    {"id": "kohlrabi", "name": "콜라비", "category": "뿌리채소", "difficulty": 1, "environments": ["베란다", "옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "중", "container_ok": True, "days_to_harvest": [55, 70], "water_need": "보통", "nongsaro": []},
    {"id": "beet", "name": "비트", "category": "뿌리채소", "difficulty": 1, "environments": ["옥상", "노지"], "sunlight": "양지", "min_sun_hours": 5, "space": "중", "container_ok": True, "days_to_harvest": [60, 80], "water_need": "보통", "nongsaro": [("채소", "비트")]},
    # ── 허브 3 (전부 미커버) ──
    {"id": "basil", "name": "바질", "category": "허브", "difficulty": 1, "environments": ["베란다", "옥상", "노지", "실내"], "sunlight": "양지", "min_sun_hours": 4, "space": "소", "container_ok": True, "days_to_harvest": [40, 60], "water_need": "보통", "nongsaro": []},
    {"id": "peppermint", "name": "페퍼민트", "category": "허브", "difficulty": 1, "environments": ["베란다", "옥상", "노지", "실내"], "sunlight": "반음지가능", "min_sun_hours": 3, "space": "소", "container_ok": True, "days_to_harvest": [50, 70], "water_need": "많음", "nongsaro": []},
    {"id": "rosemary", "name": "로즈마리", "category": "허브", "difficulty": 2, "environments": ["베란다", "옥상", "실내"], "sunlight": "양지", "min_sun_hours": 5, "space": "소", "container_ok": True, "days_to_harvest": [80, 120], "water_need": "적음", "nongsaro": []},
]


def _get(op: str, **params) -> ET.Element:
    r = httpx.get(f"{BASE}/{op}", params={"apiKey": KEY, **params}, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)  # 응답 charset=UTF-8 → r.text 정상
    code = root.findtext(".//header/resultCode")
    if code not in ("00", None):
        msg = root.findtext(".//header/resultMsg")
        raise RuntimeError(f"농사로 오류 {code} {msg} (op={op})")
    return root


def fetch_catalog() -> list[dict]:
    """전체 작물 카탈로그: [{group, group_code, sj, cntntsNo}]."""
    grp = _get("workScheduleGrpList")
    groups = [
        (it.findtext("kidofcomdtySeCode"), (it.findtext("codeNm") or "").strip())
        for it in grp.findall(".//item")
    ]
    catalog: list[dict] = []
    for code, name in groups:
        lst = _get("workScheduleLst", kidofcomdtySeCode=code)
        for it in lst.findall(".//item"):
            catalog.append(
                {
                    "group": name,
                    "group_code": code,
                    "sj": (it.findtext("sj") or "").strip(),
                    "cntntsNo": (it.findtext("cntntsNo") or "").strip(),
                }
            )
        time.sleep(0.2)  # 예의상 스로틀
    return catalog


def main() -> None:
    if not KEY:
        sys.exit("NONGSARO_API_KEY 가 .env 에 없습니다.")
    DATA.mkdir(exist_ok=True)

    catalog = fetch_catalog()
    (DATA / "farmwork_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    # (group, sj) → cntntsNo,  group → group_code
    by_key = {(c["group"], c["sj"]): c["cntntsNo"] for c in catalog}
    group_code = {c["group"]: c["group_code"] for c in catalog}

    master: list[dict] = []
    covered: list[dict] = []
    missing: list[dict] = []
    unresolved: list[tuple[str, str]] = []  # 매핑 sj 가 카탈로그에 없음(오타 등) → 신뢰 점검

    for c in CROPS:
        sources = []
        for group, sj in c["nongsaro"]:
            cn = by_key.get((group, sj))
            if cn is None:
                unresolved.append((c["id"], f"{group}/{sj}"))
                continue
            sources.append(
                {"group": group, "group_code": group_code.get(group), "sj": sj, "cntntsNo": cn}
            )
        is_covered = bool(sources)
        master.append(
            {
                "id": c["id"],
                "name": c["name"],
                "category": c["category"],
                "difficulty": c["difficulty"],
                "environments": c["environments"],
                "sunlight": c["sunlight"],
                "min_sun_hours": c["min_sun_hours"],
                "space": c["space"],
                "container_ok": c["container_ok"],
                "days_to_harvest": c["days_to_harvest"],
                "water_need": c["water_need"],
                "nongsaro_sources": sources,
                "nongsaro_cntnts_no": [s["cntntsNo"] for s in sources],
                "source": "농사로 농작업일정(farmWorkingPlanNew)"
                if is_covered
                else "AI보강(검수필요)",
            }
        )
        (covered if is_covered else missing).append(c)

    out = {
        "version": VERSION,
        "schema": "Task 부록 A + nongsaro_sources(group/sj/cntntsNo 조인키)",
        "counts": {"total": len(CROPS), "covered": len(covered), "missing": len(missing)},
        "crops": master,
    }
    (DATA / "crops_master.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 정직한 커버리지 리포트
    lines = [
        "# 농작업일정(farmWorkingPlanNew) × 텃밭 40종 커버리지 리포트",
        "",
        f"- 생성: {VERSION} (실호출 기반)",
        f"- 농사로 작물 카탈로그 총 {len(catalog)}종 / 텃밭 40종 중 "
        f"커버 {len(covered)} · 미커버 {len(missing)}",
        "",
        "> 정정: workScheduleGrpList 는 농사유형 대분류(논농사/밭농사/채소/과수/…)이고, "
        "개별 작물은 workScheduleLst 의 `sj`, 조인키는 `cntntsNo`. "
        "이전 리포트(전부 210011)는 필드 오타(`kidofcomdtySeCodeNm`)+빈문자열 부분일치 버그로 무효.",
        "",
        "## ✅ 커버 (crops_master 매핑값)",
        "",
        "| 작물 | 농사로 작물명(method) | cntntsNo |",
        "|---|---|---|",
    ]
    for c in CROPS:
        srcs = [s for s in master if s["id"] == c["id"]][0]["nongsaro_sources"]
        if not srcs:
            continue
        names = " · ".join(s["sj"] for s in srcs)
        cns = ", ".join(s["cntntsNo"] for s in srcs)
        lines.append(f"| {c['name']} | {names} | {cns} |")
    lines += [
        "",
        "## ❌ 미커버 → 텃밭가꾸기(fildMnfct) + AI보강 + 검수 경로",
        "",
        *[f"- {c['name']} ({c['id']})" for c in missing],
        "",
        "> 미커버 작물은 matrix.json 에서 source 를 'AI보강(검수완료)'로 표기.",
    ]
    if unresolved:
        lines += [
            "",
            "## ⚠ 미해결 매핑 (sj 가 카탈로그에 없음 — 점검 필요)",
            "",
            *[f"- {cid}: {key}" for cid, key in unresolved],
        ]
    (DATA / "coverage_report.md").write_text("\n".join(lines), encoding="utf-8")

    # cp949 콘솔 호환 위해 ASCII 로만 출력
    print(f"[catalog] {len(catalog)} crops | covered {len(covered)} / missing {len(missing)}")
    if unresolved:
        print("[WARN] unresolved mappings:", unresolved)
    else:
        print("[OK] all mapping sj resolved from catalog")


if __name__ == "__main__":
    main()
