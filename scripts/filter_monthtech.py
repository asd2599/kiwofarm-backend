"""monthtech 임베딩 청크를 '텃밭 관련성' 기준으로 선별 (gpt-4o-mini).

이달의 농업기술은 전문농 대상 기사라 시설하우스 설비·약제 방제력·출하/유통·
대면적 시비 처방 등 v3(도시 생활농 텃밭) 무관 내용이 많다. 청크별로 LLM 판정해
유관 청크만 남긴다. 벡터는 행 필터만 하므로 재임베딩 비용이 없다.

실행:
    uv run python scripts/filter_monthtech.py --dry-run   # 판정 결과만 출력
    uv run python scripts/filter_monthtech.py             # 적용(파일 덮어쓰기)
환경: OPENAI_API_KEY
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import numpy as np
from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger("filter_monthtech")

EMBED_DIR = Path(__file__).resolve().parents[1] / "data" / "embeddings"
MODEL = "gpt-4o-mini"
BATCH = 20  # 호출당 청크 수
EXCERPT = 500  # 판정용 청크 발췌 길이

SYSTEM = """\
당신은 도시 텃밭(베란다·옥상·주말텃밭) 서비스의 데이터 큐레이터입니다.
농업기술 텍스트 청크가 '집·텃밭 규모 초보 재배자'에게 유용한지 판정합니다.

유지(true): 가정 텃밭 규모에서 적용 가능한 파종·모종·물주기·웃거름·솎음·수확 요령,
병해충 증상 식별과 예방 관리(환기·습도·비가림·천적·끈끈이 등), 보관 요령.
제외(false): 시설하우스 전문 설비(환기팬·보일러·양액·CO2), 농약 방제력·약제 살포
처방 중심 내용, 출하·유통·경영·소득, 10a/ha 단위 대면적 시비 처방, 농기계 작업,
육종·종자 생산, 특정 작물과 무관한 행정 공지."""


async def judge_batch(
    client: AsyncOpenAI, crop_name: str, chunks: list[str], offset: int
) -> set[int]:
    """청크 묶음 → 유지할 인덱스(전체 기준) 집합."""
    numbered = "\n\n".join(
        f"[{offset + i}] {c[:EXCERPT]}" for i, c in enumerate(chunks)
    )
    resp = await client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": (
                    f"작물: {crop_name}\n다음 청크들을 판정해 유지할 번호만 JSON 으로 답하세요."
                    f' 형식: {{"keep": [번호, ...]}}\n\n{numbered}'
                ),
            },
        ],
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return {int(i) for i in data.get("keep", [])}


async def filter_crop(client: AsyncOpenAI, key: str, dry: bool) -> tuple[int, int]:
    json_path = EMBED_DIR / f"{key}.monthtech.json"
    npy_path = EMBED_DIR / f"{key}.monthtech.npy"
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    chunks: list[str] = meta.get("chunks", [])
    vectors = np.load(npy_path)

    keep: set[int] = set()
    for off in range(0, len(chunks), BATCH):
        batch = chunks[off : off + BATCH]
        try:
            keep |= await judge_batch(client, key, batch, off)
        except Exception as e:  # noqa: BLE001 - 판정 실패 배치는 보수적으로 전부 유지
            log.warning("판정 실패 %s offset=%d: %s — 해당 배치 유지", key, off, e)
            keep |= set(range(off, off + len(batch)))

    kept_idx = sorted(i for i in keep if 0 <= i < len(chunks))
    before, after = len(chunks), len(kept_idx)
    log.info("%s.monthtech: %d → %d청크", key, before, after)

    if dry:
        return before, after
    if after == 0:
        json_path.unlink()
        npy_path.unlink()
        log.info("  전부 무관 → 파일 삭제")
        return before, after
    meta["chunks"] = [chunks[i] for i in kept_idx]
    meta["source"] = f"{meta.get('source', 'monthtech')} (텃밭 선별)"
    np.save(npy_path, vectors[kept_idx])
    json_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return before, after


async def main() -> None:
    parser = argparse.ArgumentParser(description="monthtech 텃밭 관련성 선별")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not settings.openai_api_key:
        log.error("OPENAI_API_KEY 미설정")
        return

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    keys = sorted(f.name.split(".monthtech.json")[0] for f in EMBED_DIR.glob("*.monthtech.json"))
    log.info("대상 %d개 작물: %s", len(keys), ", ".join(keys))

    tb = ta = 0
    for key in keys:
        b, a = await filter_crop(client, key, args.dry_run)
        tb += b
        ta += a
    log.info("합계: %d → %d청크 (%.0f%% 제거)", tb, ta, (1 - ta / tb) * 100 if tb else 0)


if __name__ == "__main__":
    asyncio.run(main())
