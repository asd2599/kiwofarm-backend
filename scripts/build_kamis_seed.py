"""KAMIS 부류·품목·품종 코드표 XLSX → JSON seed.

입력: docs/농축수산물 품목 및 등급 코드표.xlsx 의 '코드통합(부류+품목+품종코드)' 시트
출력: backend/seed/kamis_crops.json

JSON 한 항목 구조:
    {
      "groupCode": "200",
      "groupName": "채소류",
      "itemCode": "225",
      "itemName": "토마토",
      "kindCode": "00",
      "kindName": "토마토",
      "label": "토마토",
      "searchText": "토마토 채소류"
    }

같은 itemName + kindName 중복은 합치되 첫 행 유지.
backend/app/api 검색 라우터가 startup 시 이 파일을 메모리에 로드.

재실행:
    uv run python scripts/build_kamis_seed.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
XLSX = REPO / "docs" / "농축수산물 품목 및 등급 코드표.xlsx"
OUT = REPO / "backend" / "seed" / "kamis_crops.json"
SHEET = "코드통합(부류+품목+품종코드)"


def _pad(code: object, width: int) -> str:
    s = str(code).strip()
    if not s or s.lower() == "nan":
        return ""
    if "." in s:
        s = s.split(".", 1)[0]
    return s.zfill(width)


def build() -> list[dict[str, str]]:
    df = pd.read_excel(XLSX, sheet_name=SHEET, header=1)
    df = df.rename(
        columns={
            "품목 그룹코드": "groupCode",
            "품목 그룹명": "groupName",
            "품목 코드": "itemCode",
            "품목명": "itemName",
            "품종코드": "kindCode",
            "품종명": "kindName",
        }
    )
    df = df[["groupCode", "groupName", "itemCode", "itemName", "kindCode", "kindName"]]
    df = df.dropna(subset=["itemCode", "itemName"])

    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, str]] = []
    for _, row in df.iterrows():
        group_code = _pad(row["groupCode"], 3)
        item_code = _pad(row["itemCode"], 3)
        kind_code = _pad(row["kindCode"], 2)
        item_name = str(row["itemName"]).strip()
        kind_name = str(row["kindName"]).strip() if pd.notna(row["kindName"]) else ""
        group_name = str(row["groupName"]).strip() if pd.notna(row["groupName"]) else ""

        if not item_code or not item_name:
            continue
        key = (item_code, kind_code, kind_name)
        if key in seen:
            continue
        seen.add(key)

        label = item_name if (not kind_name or kind_name == item_name) else f"{item_name} — {kind_name}"
        search_text = " ".join(s for s in {item_name, kind_name, group_name} if s)

        out.append(
            {
                "groupCode": group_code,
                "groupName": group_name,
                "itemCode": item_code,
                "itemName": item_name,
                "kindCode": kind_code,
                "kindName": kind_name,
                "label": label,
                "searchText": search_text,
            }
        )
    return out


def main() -> None:
    records = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(records)} rows → {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
