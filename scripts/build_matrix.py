"""키워팜 · 심기 — matrix.json (작목 × 시기 × 행동) 생성기.

crops_master.json 의 커버 작물별 cntntsNo 로 농사로
workScheduleEraInfoJsonLst 를 호출해 시기 행을 받아 캘린더로 구조화한다.

실호출로 확인한 응답 구조(2026-06-04):
  - workScheduleEraInfoLst      → htmlCn(HTML 표) 한 덩어리(보조용)
  - workScheduleEraInfoJsonLst  → 구조화 행(item). 이름은 Json 이나 XML 반환. ← 매트릭스 원천
  - 캘린더 판별은 infoSeCode 숫자가 아니라 infoSeCodeNm 에 '생육과정' 포함 여부.
    (생육과정 섹션 코드는 작물마다 다름: 상추=410001, 가지=410022~, 토마토=410024~)
      '생육과정(주요농작업)'              ← 한 섹션 + 내부 ▶ 작형헤더 (상추형)  ★사용
      '생육과정(주요농작업) - 촉성재배'    ← 작형별 섹션, '- ' 뒤가 작형 (가지/토마토형) ★사용
      '기상재해…' '예상되는 문제점…' '병충해 방제…' '병해충…'  ← 작업 아님 → raw 보존
  - 작형(method): infoSeCodeNm '- ' 뒤 텍스트, 없으면 내부 ▶/[…] 헤더, 둘 다 없으면 '기본'
  - farmWorkFlag = 작물시트명(작형 아님)

산출물:
  data/matrix.json        (40종: 커버 30 = 농사로 캘린더 / 미커버 10 = 빈 캘린더+검수플래그)
  data/raw_farmwork.json  (커버 작물 전체 원본 행 — 감사·검수용)

실행: uv run python scripts/build_matrix.py
환경: .env 의 NONGSARO_API_KEY
"""

from __future__ import annotations

import json
import os
import re
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

CALENDAR_MARK = "생육과정"  # infoSeCodeNm 에 이 문자열이 있으면 캘린더 행


def _get_rows(cntnts_no: str) -> list[dict]:
    r = httpx.get(
        f"{BASE}/workScheduleEraInfoJsonLst",
        params={"apiKey": KEY, "cntntsNo": cntnts_no},
        timeout=30,
    )
    r.raise_for_status()
    root = ET.fromstring(r.text)
    code = root.findtext(".//header/resultCode")
    if code not in ("00", None):
        raise RuntimeError(f"농사로 오류 {code} cntntsNo={cntnts_no}")
    return [{c.tag: (c.text or "").strip() for c in it} for it in root.findall(".//item")]


def is_season_header(op: str) -> bool:
    op = op.strip()
    return ("▶" in op) or (op.startswith("[") and op.endswith("]"))


def clean_season(op: str) -> str:
    return re.sub(r"[▶\[\]]", "", op).strip()


def classify(op: str) -> str:
    """작업명 → 행동(파종/정식/관리/수확). 룰 사전(API매핑 §2)."""
    s = op.replace(" ", "")
    if "수확" in s:
        return "수확"
    if any(k in s for k in ("아주심기", "정식", "옮겨심", "모종심", "이식")):
        return "정식"
    if any(k in s for k in ("씨뿌", "파종")):
        return "파종"
    return "관리"


def months_span(b: str, e: str) -> list[int]:
    """beginMon~endMon 을 월 리스트로 전개(연말 wrap 처리)."""
    try:
        bi, ei = int(b), int(e)
    except ValueError:
        return []
    if not (1 <= bi <= 12 and 1 <= ei <= 12):
        return []
    return list(range(bi, ei + 1)) if ei >= bi else list(range(bi, 13)) + list(range(1, ei + 1))


def window(row: dict) -> dict:
    return {
        "from": {"m": int(row["beginMon"]), "p": row.get("beginEra", "")},
        "to": {"m": int(row["endMon"]), "p": row.get("endEra", "")},
    }


def section_method(info_se_nm: str) -> str | None:
    """'생육과정(주요농작업) - 촉성재배' → '촉성재배'. '- ' 없으면 None."""
    if " - " in info_se_nm:
        return info_se_nm.split(" - ", 1)[1].strip()
    return None


def build_variant(cntnts_no: str, sheet: str, rows: list[dict]) -> dict:
    """한 cntntsNo(작물시트) → 작형(season)별 행동 + 월별 캘린더.

    캘린더 행 = infoSeCodeNm 에 '생육과정' 포함. 작형 경계는 (1)섹션(infoSeCodeNm)
    변경 (2)내부 ▶/[…] 헤더 둘 다로 결정.
    """
    cal_rows = [r for r in rows if CALENDAR_MARK in (r.get("infoSeCodeNm") or "")]
    seasons: list[dict] = []
    cur: dict | None = None
    cur_section: str | None = None
    for r in cal_rows:
        nm = r.get("infoSeCodeNm", "")
        op = r.get("opertNm", "")
        if nm != cur_section:  # 섹션 전환 → 작형 리셋
            cur_section = nm
            cur = None
        if not op:
            continue
        if is_season_header(op):  # 내부 ▶/[…] 헤더 → 새 작형
            cur = {"method": clean_season(op), "window": window(r), "actions": []}
            seasons.append(cur)
            continue
        action = {
            "action": classify(op),
            "label": op,
            "window": window(r),
            "months": months_span(r["beginMon"], r["endMon"]),
        }
        if r.get("vodUrl"):
            action["vod"] = r["vodUrl"]
        if cur is None:  # 헤더 없는 섹션 → 섹션명(작형) 또는 '기본'
            cur = {"method": section_method(nm) or "기본", "window": None, "actions": []}
            seasons.append(cur)
        cur["actions"].append(action)
    return {"cntntsNo": cntnts_no, "sheet": sheet, "seasons": seasons}


def merge_calendar(variants: list[dict]) -> dict:
    """모든 작형/작물시트를 월(1~12)→행동 리스트로 병합(추천 엔진용)."""
    cal: dict[str, list[dict]] = {}
    for v in variants:
        for s in v["seasons"]:
            for a in s["actions"]:
                entry = {
                    "action": a["action"],
                    "method": s["method"],
                    "label": a["label"],
                    "window": a["window"],
                    "sheet": v["sheet"],
                    "cntntsNo": v["cntntsNo"],
                }
                for m in a["months"]:
                    cal.setdefault(str(m), []).append(entry)
    return cal


def main() -> None:
    if not KEY:
        sys.exit("NONGSARO_API_KEY 가 .env 에 없습니다.")
    master = json.loads((DATA / "crops_master.json").read_text(encoding="utf-8"))

    out_crops: dict[str, dict] = {}
    raw_dump: dict[str, dict] = {}
    covered_n = missing_n = 0

    for c in master["crops"]:
        cid = c["id"]
        base_meta = {
            "id": cid,
            "name": c["name"],
            "category": c["category"],
            "difficulty": c["difficulty"],
            "days_to_harvest": c["days_to_harvest"],
        }
        sources = c.get("nongsaro_sources", [])
        if not sources:  # 미커버 → 빈 캘린더 + 검수 플래그
            out_crops[cid] = {
                **base_meta,
                "variants": [],
                "calendar": {},
                "source": c["source"],
                "needs_enrichment": True,
            }
            missing_n += 1
            continue

        variants: list[dict] = []
        for src in sources:
            cn = src["cntntsNo"]
            rows = _get_rows(cn)
            raw_dump[cn] = {"crop": cid, "sheet": src["sj"], "rows": rows}
            variants.append(build_variant(cn, src["sj"], rows))
            time.sleep(0.2)  # 스로틀

        out_crops[cid] = {
            **base_meta,
            "variants": variants,
            "calendar": merge_calendar(variants),
            "source": "농사로 농작업일정(farmWorkingPlanNew)",
            "needs_enrichment": False,
        }
        covered_n += 1
        print(f"  [{cid}] {c['name']}: {len(variants)} variant(s)")

    matrix = {
        "matrix_version": VERSION,
        "source": "농사로 농작업일정 workScheduleEraInfoJsonLst (infoSeCode=410001 생육과정)",
        "action_legend": ["파종", "정식", "관리", "수확"],
        "window_note": "p = 순(상/중/하). months 는 begin~end 월 전개(연말 wrap 포함).",
        "counts": {"total": len(out_crops), "covered": covered_n, "missing": missing_n},
        "crops": out_crops,
    }
    (DATA / "matrix.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (DATA / "raw_farmwork.json").write_text(
        json.dumps(raw_dump, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[matrix] total {len(out_crops)} | covered {covered_n} / missing {missing_n}")


if __name__ == "__main__":
    main()
