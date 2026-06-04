"""키워팜 · 심기 — 미커버 10종 캘린더 보강 (Task A).

농작업일정에 없는 텃밭 작물(케일·루꼴라·엇갈이배추·애호박·여주·알타리무·
콜라비·바질·페퍼민트·로즈마리)을 텃밭가꾸기(fildMnfct) 본문(cn) + gpt-4o-mini 로
파종/정식/관리/수확 월 캘린더를 추출해 matrix.json 에 채운다.

원칙(Task §2.3, API매핑 §4):
  - 시기 '값'은 공신력 데이터 우선. 농작업일정에 없는 작물만 여기서 보강.
  - 본문(grounded=true)에 근거. 본문이 빈약하면 LLM 한국 텃밭 상식(grounded=false).
  - source='AI보강(검수필요)', needs_review=true → 사람 검수 큐.

실행: uv run python scripts/enrich_missing.py
환경: .env 의 NONGSARO_API_KEY, OPENAI_API_KEY
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
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
load_dotenv(ROOT / ".env")

KEY = os.environ.get("NONGSARO_API_KEY")
FILD_BASE = "http://api.nongsaro.go.kr/service/fildMnfct"
MODEL = "gpt-4o-mini"

# 미커버 작물 검색 별칭(농사로 텃밭가꾸기 표기 차이 대응)
ALIASES: dict[str, list[str]] = {
    "kale": ["케일"],
    "arugula": ["루꼴라", "루콜라", "로켓"],
    "baby_napa": ["엇갈이배추", "얼갈이배추", "열무"],
    "korean_zucchini": ["애호박", "호박"],
    "bitter_melon": ["여주"],
    "altari_radish": ["알타리무", "총각무", "알타리"],
    "kohlrabi": ["콜라비"],
    "basil": ["바질"],
    "peppermint": ["페퍼민트", "민트", "박하"],
    "rosemary": ["로즈마리"],
}

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t ]+")


def strip_html(s: str) -> str:
    s = _TAG.sub(" ", s)
    s = s.replace("&nbsp;", " ").replace("\r", "\n")
    s = _WS.sub(" ", s)
    return re.sub(r"\n{2,}", "\n", s).strip()


def fild_bodies(name: str, limit: int = 3, max_chars: int = 2500) -> list[str]:
    """텃밭가꾸기 검색 → 상위 항목 본문(cn) 텍스트 리스트."""
    try:
        r = httpx.get(
            f"{FILD_BASE}/fildMnfctList",
            params={"apiKey": KEY, "sSeCode": "335001", "sType": "sCntntsSj", "sText": name},
            timeout=30,
        )
        root = ET.fromstring(r.text)
    except Exception:
        return []
    out: list[str] = []
    for it in root.findall(".//item")[:limit]:
        txt = strip_html(it.findtext("cn") or "")
        if len(txt) >= 20:
            out.append(txt[:max_chars])
    return out


SYS = (
    "너는 한국 텃밭(베란다·옥상·노지) 재배 캘린더 정리 전문가다. "
    "주어진 참고본문이 있으면 그 시기 정보를 우선 사용하고, 없거나 부족하면 "
    "한국 중부지방 노지 텃밭 기준 일반 상식으로 보수적으로 채운다. "
    "월은 1~12 정수, 행동은 반드시 [파종,정식,관리,수확] 중 하나. "
    "시기를 모르면 지어내지 말고 비워둔다."
)

SCHEMA_HINT = (
    '{"grounded": true|false, '
    '"climate_note": "기후/주의 한 줄(40자 이내)", '
    '"calendar": {"3": [{"action":"파종","label":"씨앗 파종","plain":"쉬운 한 줄(35자)"}], '
    '"5": [{"action":"수확","label":"수확","plain":"..."}]}}'
)


def extract_calendar(client: OpenAI, name: str, bodies: list[str]) -> dict:
    ctx = "\n\n---\n\n".join(bodies) if bodies else "(참고본문 없음)"
    grounded_hint = "참고본문이 있으니 본문 근거로" if bodies else "참고본문이 없으니 일반 상식으로"
    user = (
        f"작물: {name}\n\n[참고본문]\n{ctx[:6000]}\n\n"
        f"{grounded_hint} 이 작물의 텃밭 재배 캘린더를 만들어줘. "
        f"파종/정식/수확 시기를 중심으로, 필요한 관리 작업도 포함. "
        f"아래 JSON 스키마로만 출력(설명·코드블록 금지):\n{SCHEMA_HINT}"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYS}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=900,
    )
    return json.loads(resp.choices[0].message.content or "{}")


def to_matrix_calendar(parsed: dict) -> dict:
    """LLM JSON → matrix calendar(month→entries) 형식."""
    cal: dict[str, list[dict]] = {}
    raw = parsed.get("calendar", {})
    if not isinstance(raw, dict):
        return cal
    for mon, acts in raw.items():
        m = str(mon).strip()
        if not (m.isdigit() and 1 <= int(m) <= 12) or not isinstance(acts, list):
            continue
        for a in acts:
            if not isinstance(a, dict):
                continue
            action = a.get("action", "관리")
            if action not in ("파종", "정식", "관리", "수확"):
                action = "관리"
            cal.setdefault(m, []).append(
                {
                    "action": action,
                    "method": "텃밭(AI보강)",
                    "label": a.get("label", action),
                    "plain": a.get("plain", ""),
                    "window": None,
                    "source": "AI보강(검수필요)",
                }
            )
    return cal


def main() -> None:
    if not KEY:
        sys.exit("NONGSARO_API_KEY 가 .env 에 없습니다.")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY 가 .env 에 없습니다.")
    client = OpenAI()
    matrix = json.loads((DATA / "matrix.json").read_text(encoding="utf-8"))

    targets = [cid for cid, c in matrix["crops"].items() if c.get("needs_enrichment")]
    print(f"보강 대상 {len(targets)}종: {targets}")
    enriched = 0
    for cid in targets:
        crop = matrix["crops"][cid]
        name = crop["name"]
        bodies: list[str] = []
        used_alias = None
        for alias in ALIASES.get(cid, [name]):
            bodies = fild_bodies(alias)
            if bodies:
                used_alias = alias
                break
            time.sleep(0.2)
        try:
            parsed = extract_calendar(client, name, bodies)
        except Exception as e:  # noqa: BLE001
            print(f"  [{cid}] {name}: LLM 실패 {e}")
            continue
        cal = to_matrix_calendar(parsed)
        crop["calendar"] = cal
        crop["needs_enrichment"] = False
        crop["needs_review"] = True
        crop["source"] = "AI보강(검수필요)"
        crop["enrich_meta"] = {
            "grounded": bool(parsed.get("grounded")) and bool(bodies),
            "fild_search": used_alias,
            "fild_bodies": len(bodies),
            "climate_note": parsed.get("climate_note", ""),
        }
        months = sorted(cal.keys(), key=int)
        print(
            f"  [{cid}] {name}: src={used_alias or '없음'}({len(bodies)}) "
            f"grounded={crop['enrich_meta']['grounded']} months={months}"
        )
        enriched += 1
        time.sleep(0.3)

    matrix["counts"]["missing_enriched"] = enriched
    (DATA / "matrix.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[enrich_missing] {enriched}/{len(targets)} 종 캘린더 보강 완료")


if __name__ == "__main__":
    main()
