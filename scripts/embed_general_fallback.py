"""농사로 자료가 없는 작물의 GPT 텃밭 재배지식 폴백 → 임베딩 스토어 적재.

crops_master 40종 중 임베딩 파일이 하나도 없는 작물을 자동 탐지해, GPT 로
'도시 텃밭(베란다·옥상·노지 소규모) 눈높이' 재배지식을 생성하고 {슬러그}.general
로 적재한다. 출처는 농사로가 아닌 GPT 이므로 source="general" 로 구분한다.

실행:
    uv run python scripts/embed_general_fallback.py            # 빈 작물 자동 탐지
    uv run python scripts/embed_general_fallback.py --crops peppermint kohlrabi
    uv run python scripts/embed_general_fallback.py --force    # 재생성
환경: .env 의 OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.planting import matrix  # noqa: E402
from app.core.rag import store  # noqa: E402
from app.core.rag.embeddings import embed_texts  # noqa: E402
from app.core.rag.ingest import _chunk_text  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("embed_general_fallback")

GENERAL_KIND = "general"
MODEL = "gpt-4o-mini"

SYSTEM = (
    "당신은 도시 텃밭(베란다·옥상·주말텃밭) 전문 코치입니다. 농촌진흥청 표준 "
    "재배지침을 바탕으로 하되, 초보 생활농이 집에서 바로 따라할 수 있는 눈높이로 "
    "한국어 재배 가이드를 작성합니다. 시설하우스 설비·농약 방제력·대면적 시비 같은 "
    "전문농 내용은 넣지 않습니다."
)


def _user_prompt(crop: dict) -> str:
    envs = "·".join(crop.get("environments", []) or [])
    return (
        f"작물: {crop['name']} (분류: {crop.get('category', '')}, "
        f"적합 환경: {envs}, 난이도 {crop.get('difficulty', '?')}/5)\n\n"
        "다음 항목을 각각 문단으로 상세히 작성하세요:\n"
        "1) 키우기 좋은 환경(햇빛·온도·용기 크기·흙)\n"
        "2) 씨앗/모종 심는 시기와 방법 (월 기준)\n"
        "3) 물주기와 거름 주기 요령\n"
        "4) 생육 단계별 관리 (솎음·순지르기·지주 등 해당 시)\n"
        "5) 자주 생기는 문제와 병해충 — 증상 식별과 친환경 예방·대처\n"
        "6) 수확 시기 판단법과 수확 방법\n"
        "7) 수확 후 보관 요령\n"
        "베란다 화분 기준과 노지 텃밭 기준이 다르면 둘 다 적어주세요."
    )


async def generate_text(client: AsyncOpenAI, crop: dict) -> str:
    resp = await client.chat.completions.create(
        model=MODEL,
        temperature=0.3,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _user_prompt(crop)},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _empty_crops() -> list[dict]:
    """임베딩 파일이 하나도 없는 마스터 작물."""
    have = {f.name.split(".")[0] for f in store.EMBED_DIR.glob("*.json")}
    return [c for c in matrix.all_crops() if c["id"] not in have]


async def main() -> None:
    ap = argparse.ArgumentParser(description="빈 작물 GPT 텃밭 재배지식 폴백 적재")
    ap.add_argument("--crops", nargs="*", default=None, help="대상 슬러그 직접 지정")
    ap.add_argument("--force", action="store_true", help="이미 적재된 작물도 재생성")
    args = ap.parse_args()

    if not settings.openai_api_key:
        log.error("OPENAI_API_KEY 미설정")
        return

    if args.crops:
        targets = [c for s in args.crops if (c := matrix.get_crop(s))]
    else:
        targets = _empty_crops()
    log.info("대상 %d종: %s", len(targets), ", ".join(f"{c['name']}({c['id']})" for c in targets))

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    embedded = skipped = failed = total_chunks = 0
    for crop in targets:
        slug = crop["id"]
        if not args.force and store.exists(slug, GENERAL_KIND):
            skipped += 1
            continue
        try:
            text = await generate_text(client, crop)
            chunks = _chunk_text(text)
            if not chunks:
                raise ValueError("생성 텍스트 비어있음")
            vectors = await embed_texts(chunks)
        except Exception as e:  # noqa: BLE001 - 한 작물 실패가 전체를 막지 않게
            log.warning("실패 %s(%s): %s", crop["name"], slug, e)
            failed += 1
            continue
        store.save(slug, GENERAL_KIND, chunks, vectors, "general")
        embedded += 1
        total_chunks += len(chunks)
        log.info("  embed %s(%s) general: %d청크", crop["name"], slug, len(chunks))

    log.info("완료: 적재 %d종 / %d청크, skip %d, fail %d", embedded, total_chunks, skipped, failed)


if __name__ == "__main__":
    asyncio.run(main())
