"""작목별 정리 데이터(data/farminfo/by_crop) → 로컬 임베딩 스토어 적재.

sync_farm_info_by_crop.py 가 만든 작물별 passage(이달의+주간)를 임베딩해
store 의 item_code 단위 'farminfo' kind 로 저장한다. 이렇게 하면 retrieve()/
knowledge.py 가 기존 crop_key 검색 시 해당 작물의 농사로 본문도 함께 회수한다
(품종 공유 — itemCode 단위 1회 임베딩).

실행:
    uv run python scripts/embed_farminfo.py            # 미적재 작물만
    uv run python scripts/embed_farminfo.py --force    # 전체 재적재
환경: .env 의 OPENAI_API_KEY (임베딩).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.rag import store  # noqa: E402
from app.core.rag.embeddings import embed_texts  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("embed_farminfo")

BY_CROP_DIR = Path(__file__).resolve().parents[1] / "data" / "farminfo" / "by_crop"
KINDS = ("monthtech", "weekfarm")  # 출처별 kind (store.ITEM_KINDS 와 일치)
MIN_HANGUL = 15
MIN_HANGUL_RATIO = 0.35  # (한글/공백제외문자) 비율 — 목차 점선 페이지 등 저밀도 청크 제외
_HANGUL = re.compile(r"[가-힣]")


def _is_content(t: str) -> bool:
    """본문성 청크인지: 길이·한글수·한글밀도·가운뎃점(목차) 기준."""
    nonspace = sum(1 for c in t if not c.isspace())
    hangul = len(_HANGUL.findall(t))
    if nonspace < 20 or hangul < MIN_HANGUL:
        return False
    if hangul / nonspace < MIN_HANGUL_RATIO:
        return False
    if t.count("·") >= 10:  # 목차 점선('· · ·') 페이지
        return False
    return True


def _select_by_kind(passages: list[dict]) -> dict[str, list[str]]:
    """passage 를 출처(source) 별로 분리. 비본문/중복 청크 제외(순서 유지)."""
    out: dict[str, list[str]] = {k: [] for k in KINDS}
    seen: dict[str, set[str]] = {k: set() for k in KINDS}
    for p in passages:
        src = p.get("source")
        if src not in out:
            continue
        t = (p.get("text") or "").strip()
        if not _is_content(t) or t in seen[src]:
            continue
        seen[src].add(t)
        out[src].append(t)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser(description="작목별 농사로 정리 → 임베딩 스토어 적재")
    ap.add_argument("--force", action="store_true", help="이미 적재된 작물도 재적재")
    args = ap.parse_args()

    files = sorted(BY_CROP_DIR.glob("*.json"))
    if not files:
        log.error("by_crop 데이터 없음: %s (먼저 sync_farm_info_by_crop.py 실행)", BY_CROP_DIR)
        return
    log.info("대상 작물 파일 %d개", len(files))

    total_chunks = 0
    embedded = skipped = empty = 0
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        item_code = data.get("itemCode") or ""
        crop_name = data.get("cropName") or f.stem
        if not item_code:
            log.info("  skip %s (itemCode 없음)", crop_name)
            empty += 1
            continue
        by_kind = _select_by_kind(data.get("passages") or [])
        did = False
        for kind in KINDS:
            texts = by_kind[kind]
            if not texts:
                continue
            if not args.force and store.exists(item_code, kind):
                skipped += 1
                continue
            vectors = await embed_texts(texts)
            store.save(item_code, kind, texts, vectors, kind)
            total_chunks += len(texts)
            did = True
            log.info("  embed %s(%s) %s: %d청크", crop_name, item_code, kind, len(texts))
        if did:
            embedded += 1
        elif not any(by_kind.values()):
            empty += 1

    log.info(
        "완료: 적재 %d작물 / %d청크, skip(kind) %d, empty %d",
        embedded, total_chunks, skipped, empty,
    )


if __name__ == "__main__":
    asyncio.run(main())
