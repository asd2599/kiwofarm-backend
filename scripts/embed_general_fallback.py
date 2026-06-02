"""농사로 미등장 작물의 GPT 표준 재배지식 폴백 → 임베딩 스토어 적재.

이달의 농업기술/주간농사정보에 (작물명으로) 등장하지 않아 farminfo 가 비는 작물
(당근·양배추·브로콜리 등)에 대해, GPT 로 농촌진흥청 표준 재배·병해충 지식을 생성해
store 의 item_code 단위 'general' kind 로 적재한다. 출처는 농사로가 아닌 GPT 다.

실행:
    uv run python scripts/embed_general_fallback.py          # 미적재 작물만
    uv run python scripts/embed_general_fallback.py --force  # 재생성
환경: .env 의 OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.rag import store  # noqa: E402
from app.core.rag.embeddings import embed_texts  # noqa: E402
from app.core.rag.ingest import _chunk_text, _generate_general_text  # noqa: E402
from app.data import kamis_crops  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("embed_general_fallback")

GENERAL_KIND = "general"

# 농사로 이달의/주간에 미등장이라 farminfo 가 비는 재배작물(작물명 기준).
TARGET_CROPS: list[str] = [
    "당근", "양배추", "브로콜리", "부추", "미나리",
    "갓", "연근", "열무", "우엉", "청경채", "케일",
]


def _resolve_codes() -> list[tuple[str, str]]:
    """작물명 → (이름, KAMIS itemCode). 시드에 없으면 경고하고 제외."""
    name_to_code: dict[str, str] = {}
    for r in kamis_crops.all_crops():
        name_to_code.setdefault(r["itemName"], r["itemCode"])
    out: list[tuple[str, str]] = []
    for nm in TARGET_CROPS:
        code = name_to_code.get(nm)
        if not code:
            log.warning("KAMIS 시드에 없음, 건너뜀: %s", nm)
            continue
        out.append((nm, code))
    return out


async def main() -> None:
    ap = argparse.ArgumentParser(description="농사로 미등장 작물 GPT 재배지식 폴백 적재")
    ap.add_argument("--force", action="store_true", help="이미 적재된 작물도 재생성")
    args = ap.parse_args()

    targets = _resolve_codes()
    log.info("대상 작물 %d종: %s", len(targets), ", ".join(n for n, _ in targets))

    embedded = skipped = failed = 0
    total_chunks = 0
    for name, code in targets:
        if not args.force and store.exists(code, GENERAL_KIND):
            skipped += 1
            continue
        try:
            text = await _generate_general_text(name, None)
            chunks = _chunk_text(text)
            if not chunks:
                raise ValueError("생성 텍스트 비어있음")
            vectors = await embed_texts(chunks)
        except Exception as e:  # noqa: BLE001 - 한 작물 실패가 전체를 막지 않게
            log.warning("실패 %s(%s): %s", name, code, e)
            failed += 1
            continue
        store.save(code, GENERAL_KIND, chunks, vectors, "general")
        embedded += 1
        total_chunks += len(chunks)
        log.info("  embed %s(%s) general: %d청크", name, code, len(chunks))

    log.info(
        "완료: 적재 %d종 / %d청크, skip %d, fail %d",
        embedded, total_chunks, skipped, failed,
    )


if __name__ == "__main__":
    asyncio.run(main())
