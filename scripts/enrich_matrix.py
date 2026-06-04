"""키워팜 · 심기 — matrix.json plain 설명 보강 (Task B).

커버 30종(농사로 농작업일정 출처)의 캘린더 작업에 gpt-4o-mini 로
초보용 한 줄 설명(plain)과 작물별 기후권 노트(climate_note)를 붙인다.

원칙(Task §3, 부록 D 환각방지):
  - 시기·행동(월/파종/정식/수확)은 절대 변경하지 않는다. 설명만 추가.
  - 작업명(label) → plain 매핑은 결정적으로 적용(LLM 1작물 1회 호출).
  - 미커버 10종(AI보강)은 이미 plain 보유 → 건너뜀.

실행: uv run python scripts/enrich_matrix.py
환경: .env 의 OPENAI_API_KEY
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
load_dotenv(ROOT / ".env")

MODEL = "gpt-4o-mini"

SYS = (
    "너는 텃밭 초보에게 농작업을 쉬운 말로 설명하는 도우미다. "
    "주어진 작업명 각각을 초보도 이해할 한 줄(30자 이내)로 풀어 쓴다. "
    "시기·월·숫자는 새로 지어내지 말고, 작업의 '무엇을 왜' 하는지만 쉽게 설명한다."
)


def distinct_labels(crop: dict) -> list[tuple[str, str]]:
    """[(label, action)] 중복 제거·순서 보존. 캘린더+변형 작형 모두에서 수집."""
    seen: dict[str, str] = {}
    for entries in crop.get("calendar", {}).values():
        for e in entries:
            seen.setdefault(e["label"], e["action"])
    for v in crop.get("variants", []):
        for s in v.get("seasons", []):
            for a in s.get("actions", []):
                seen.setdefault(a["label"], a["action"])
    return list(seen.items())


def gen_plain(client: OpenAI, name: str, category: str, labels: list[tuple[str, str]]) -> dict:
    # 키 매칭 실패를 피하려 '순서 정렬' 방식: 번호 목록 → 같은 순서의 설명 배열.
    label_lines = "\n".join(f"{i + 1}. {lb} ({ac})" for i, (lb, ac) in enumerate(labels))
    schema = (
        '{"plain": ["1번 설명(30자 이내)", "2번 설명", "..."], '
        '"climate_note": "이 작물 기후/지역 재배 한 줄(40자 이내)"}'
    )
    user = (
        f"작물: {name} ({category})\n\n[작업명 목록(번호 순서대로)]\n{label_lines}\n\n"
        f"각 번호의 작업명을 초보용 한 줄 설명으로 바꿔 'plain' 배열에 "
        f"**같은 순서·같은 개수**({len(labels)}개)로 넣고, 작물 기후권 노트도 한 줄 써줘. "
        f"아래 JSON 스키마로만(코드블록 금지):\n{schema}"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYS}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=1200,
    )
    return json.loads(resp.choices[0].message.content or "{}")


def build_plain_map(labels: list[tuple[str, str]], plain_list: list) -> dict[str, str]:
    """순서 정렬 결과 → label→plain. 개수 불일치는 min 까지만 매핑."""
    out: dict[str, str] = {}
    for (label, _action), plain in zip(labels, plain_list, strict=False):
        if isinstance(plain, str) and plain.strip():
            out[label] = plain.strip()
    return out


def apply_plain(crop: dict, plain_map: dict[str, str]) -> int:
    n = 0
    for entries in crop.get("calendar", {}).values():
        for e in entries:
            p = plain_map.get(e["label"])
            if p:
                e["plain"] = p
                n += 1
    for v in crop.get("variants", []):
        for s in v.get("seasons", []):
            for a in s.get("actions", []):
                p = plain_map.get(a["label"])
                if p:
                    a["plain"] = p
    return n


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY 가 .env 에 없습니다.")
    client = OpenAI()
    matrix = json.loads((DATA / "matrix.json").read_text(encoding="utf-8"))

    # 커버(농작업일정 출처)만. 미커버(AI보강)는 plain 이미 보유. 매번 재계산(멱등 덮어쓰기).
    targets = [
        cid for cid, c in matrix["crops"].items() if c.get("source", "").startswith("농사로")
    ]
    print(f"plain 보강 대상 {len(targets)}종")
    done = 0
    zero: list[str] = []
    for cid in targets:
        crop = matrix["crops"][cid]
        labels = distinct_labels(crop)
        if not labels:
            continue
        try:
            parsed = gen_plain(client, crop["name"], crop["category"], labels)
        except Exception as e:  # noqa: BLE001
            print(f"  [{cid}] {crop['name']}: LLM 실패 {e}")
            continue
        plain_list = parsed.get("plain", [])
        plain_list = plain_list if isinstance(plain_list, list) else []
        plain_map = build_plain_map(labels, plain_list)
        applied = apply_plain(crop, plain_map)
        crop["climate_note"] = parsed.get("climate_note", "")
        crop["plain_enriched"] = True
        done += 1
        if applied == 0:
            zero.append(cid)
        print(f"  [{cid}] {crop['name']}: labels={len(labels)} applied={applied}")
        time.sleep(0.3)
    if zero:
        print("  [WARN] applied=0:", zero)

    matrix["counts"]["plain_enriched"] = done
    (DATA / "matrix.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[enrich_matrix] {done}/{len(targets)} 종 plain 설명 보강 완료")


if __name__ == "__main__":
    main()
