"""농사로 텃밭가꾸기(fildMnfct) 수집 → 작물 매칭 → RAG 임베딩.

v3 의 1순위 재배지식 소스. 글 제목을 crops_master 40종과 매칭해
  - 작물 글("바질", "포도 텃밭가꾸기", "실내 텃밭 상추재배 캘린더")
      → data/embeddings/{슬러그}.garden
  - 작물 특정이 안 되는 공통 가이드("물주기", "밭 만들기" 등)
      → data/embeddings/_common.garden  (챗봇이 작물 키와 함께 추가 검색)
로 저장한다. 원문 전체는 data/garden/articles.json 에 보존.

실행:
    uv run python scripts/sync_garden.py --dry-run    # 수집·매칭 결과만
    uv run python scripts/sync_garden.py              # 수집+임베딩
환경: NONGSARO_API_KEY, OPENAI_API_KEY(임베딩)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.planting import matrix  # noqa: E402
from app.data import crop_ids  # noqa: E402
from app.data import nongsaro_garden as gd  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger("sync_garden")

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "garden"
COMMON_KEY = "_common"  # 작물 공통 텃밭 가이드 저장 키
CONCURRENCY = 6
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
MIN_BODY = 80  # 본문이 이보다 짧으면 자료 다운로드 안내 등 → 임베딩 제외

# 제목에서 작물명 후보를 깎아내는 수식어
_STRIP_RE = re.compile(
    r"(를 이용한|을 이용한|텃밭가꾸기|텃밭 ?가꾸기|기르기|키우기|재배 ?캘린더|재배|가정에서|실내 ?텃밭|모종 심는 법)"
)
_PAREN_RE = re.compile(r"\(([^)]*)\)")
# '가지'처럼 일반어와 겹치는 이름은 부분일치 탐색에서 제외(후보 추출로만 매칭)
_AMBIGUOUS = {"가지", "무", "갓"}

# 단일 단어 제목이지만 작물명이 아닌 공통 가이드 (작물 오인 제외의 예외)
_COMMON_KEEP = {"섞어짓기"}


def _is_offlist_crop_article(a: gd.GardenArticle) -> bool:
    """40종 매칭 실패 글이 '40종 외 작물 글'인지 판정 → 공통 풀 오염 방지.

    과수(335002)·특용(335003)은 40종 외 작물 글뿐이라 전부 제외.
    채소(335001)는 제목이 짧은 단일 단어("타임","배추","수박")면 작물 글로 간주.
    """
    if a.se_code != "335001":
        return True
    t = a.title.strip()
    if t in _COMMON_KEEP:
        return False
    return " " not in t and len(t) <= 6


def match_title(title: str) -> str | None:
    """글 제목 → 슬러그. 모호하면(복수 작물/특정 불가) None."""
    # 1) 후보 추출: 괄호 안, 수식어 제거본
    candidates = [m.strip() for m in _PAREN_RE.findall(title)]
    stripped = _PAREN_RE.sub("", title)
    stripped = _STRIP_RE.sub(" ", stripped).strip()
    candidates += [stripped, title.strip()]
    for cand in candidates:
        crop = crop_ids.find_by_name(cand)
        if crop:
            return crop["id"]
    # 2) 제목 내 작물명 부분일치 (모호 이름 제외, 복수 매칭이면 공통 처리)
    hits = {
        c["id"]
        for c in matrix.all_crops()
        if c["name"] not in _AMBIGUOUS and c["name"] in title
    }
    if len(hits) == 1:
        return hits.pop()
    return None


def _split(text: str) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]
    out = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for start in range(0, len(text), step):
        c = text[start : start + CHUNK_SIZE].strip()
        if c:
            out.append(c)
    return out


async def collect(*, limit: int | None) -> list[gd.GardenArticle]:
    async with httpx.AsyncClient() as client:
        metas: list[gd.GardenArticleMeta] = []
        for se in gd.SE_CODES:
            batch = await gd.fetch_list(se, client=client)
            log.info("목록 sSeCode=%s(%s): %d건", se, gd.SE_CODES[se], len(batch))
            metas.extend(batch)
        if limit is not None:
            metas = metas[:limit]

        sem = asyncio.Semaphore(CONCURRENCY)

        async def _one(m: gd.GardenArticleMeta) -> gd.GardenArticle | None:
            async with sem:
                try:
                    return await gd.fetch_view(m, client=client)
                except gd.GardenError as e:
                    log.info("상세 실패 no=%s (%s): %s", m.cntnts_no, m.title, e)
                    return None

        results = await asyncio.gather(*(_one(m) for m in metas))
        return [r for r in results if r is not None]


def group_articles(articles: list[gd.GardenArticle]) -> dict[str, list[gd.GardenArticle]]:
    """슬러그(또는 _common) → 글 목록. 본문 빈약·40종 외 작물 글은 제외."""
    groups: dict[str, list[gd.GardenArticle]] = {}
    skipped = offlist = 0
    for a in articles:
        if len(a.body) < MIN_BODY:
            skipped += 1
            continue
        key = match_title(a.title)
        if key is None:
            if _is_offlist_crop_article(a):
                offlist += 1
                continue
            key = COMMON_KEY
        groups.setdefault(key, []).append(a)
    log.info(
        "매칭: 작물 %d개 + 공통 %d건 (본문 빈약 %d건·40종 외 작물 글 %d건 제외)",
        len([k for k in groups if k != COMMON_KEY]),
        len(groups.get(COMMON_KEY, [])),
        skipped,
        offlist,
    )
    return groups


async def embed_groups(groups: dict[str, list[gd.GardenArticle]]) -> None:
    from app.core.rag import store  # noqa: PLC0415
    from app.core.rag.embeddings import embed_texts  # noqa: PLC0415

    total = 0
    for key, arts in sorted(groups.items()):
        chunks: list[str] = []
        for a in arts:
            for part in _split(a.body):
                chunks.append(f"[{a.title}]\n{part}")
        if not chunks:
            continue
        vectors = await embed_texts(chunks)
        n = store.save(key, "garden", chunks, vectors, source="fildMnfct")
        total += n
        log.info("임베딩 저장 %s: 글 %d건 → %d청크", key, len(arts), n)
    log.info("임베딩 완료: 총 %d청크", total)


async def main() -> None:
    parser = argparse.ArgumentParser(description="텃밭가꾸기 수집·매칭·임베딩")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="수집·매칭 결과만 저장(임베딩 생략)")
    args = parser.parse_args()

    articles = await collect(limit=args.limit)
    log.info("상세 수집: %d건", len(articles))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    groups = group_articles(articles)
    payload = {
        "service": "fildMnfct",
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
        "count": len(articles),
        "groups": {
            key: [
                {**asdict(a), "body": a.body[:300] + ("..." if len(a.body) > 300 else "")}
                for a in arts
            ]
            for key, arts in sorted(groups.items())
        },
    }
    (DATA_DIR / "articles_index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (DATA_DIR / "articles.json").write_text(
        json.dumps(
            {"fetchedAt": payload["fetchedAt"], "items": [asdict(a) for a in articles]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    log.info("원문 저장: %s", DATA_DIR)

    if not args.dry_run:
        await embed_groups(groups)


if __name__ == "__main__":
    asyncio.run(main())
