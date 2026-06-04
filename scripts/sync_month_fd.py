"""농사로 이달의 음식(monthFd) 수집 → 작물별 분류 → RAG 임베딩 스크립트.

수확인증 카드의 '관리(보관·손질)' + '음식(섭취·영양·레시피)' 데이터 원천.
전체 연도 × 12개월을 순회해 식재료·레시피를 수집하고(중복은 cntntsNo 기준
1회, 등장 월은 시즌 정보로 보존), v3 작물 마스터(crops_master 40종)와 이름
매칭해 작물별로 분류 저장한다. --embed 시 작물 단위로 청크 임베딩해 로컬
스토어(backend/data/embeddings/{슬러그}.monthfd.npy/json)에 저장한다.
40종 밖 식재료는 unmatchedIngredients 로 보존만 한다.

실행:
    uv run python scripts/sync_month_fd.py                  # 수집+분류 전체
    uv run python scripts/sync_month_fd.py --limit 10       # 식재료 10건만(테스트)
    uv run python scripts/sync_month_fd.py --no-recipes     # 레시피 제외
    uv run python scripts/sync_month_fd.py --embed          # + RAG 임베딩
환경:
    .env 의 NONGSARO_API_KEY (이달의음식 신청 키), --embed 는 OPENAI_API_KEY.
산출:
    backend/data/monthfd/ingredients.json   식재료 상세 전체
    backend/data/monthfd/recipes.json       레시피 상세 전체
    backend/data/monthfd/by_crop.json       작물별 분류(매칭 결과 포함)
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
from functools import lru_cache
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data import crop_ids  # noqa: E402
from app.data import nongsaro_monthfd as fd  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync_month_fd")

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "monthfd"
CONCURRENCY = 6

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

# 식재료명 정규화: 괄호 보충어·공백 제거. 괄호 안도 별도 후보로 시도
# ("총각무(알타리무)" → "총각무", "알타리무" 둘 다).
_PAREN_RE = re.compile(r"\(([^)]*)\)")


def _candidates(name: str) -> list[str]:
    main = _PAREN_RE.sub("", name).replace(" ", "").strip()
    inner = [m.replace(" ", "").strip() for m in _PAREN_RE.findall(name)]
    return [c for c in [main, *inner] if c]


def _match_crop(name: str) -> dict | None:
    """식재료명 → v3 작물 마스터 레코드(40종). 미매칭 None.

    별칭은 crop_ids.NAME_ALIAS 에서 관리 — 첫 수집 후 by_crop.json 의
    unmatchedIngredients 를 보고 보강한다 (접미사 추측 매칭 금지).
    """
    for cand in _candidates(name):
        crop = crop_ids.find_by_name(cand)
        if crop:
            return crop
    return None


@lru_cache(maxsize=1)
def _containment_names() -> list[tuple[str, str]]:
    """음식명 부분일치 스캔용 (이름, 슬러그). 2자 이상만 — '무'·'갓'·'파' 같은
    1자 이름은 '무침'·'갓김치 외 오탐'·'파전 외 오탐' 때문에 제외."""
    from app.core.planting import matrix  # noqa: PLC0415

    pairs = [(c["name"], c["id"]) for c in matrix.all_crops() if len(c["name"]) >= 2]
    for alias, target in crop_ids.NAME_ALIAS.items():
        crop = crop_ids.find_by_name(target)
        if crop and len(alias) >= 2:
            pairs.append((alias, crop["id"]))
    # 긴 이름 우선 매칭(방울토마토 > 토마토 — 둘 다 같은 요리에 잡혀도 무방하나 정렬 유지)
    return sorted(set(pairs), key=lambda p: -len(p[0]))


def match_recipe_crops(title: str) -> set[str]:
    """음식명("구운마늘 연근조림")에 포함된 40종 작물 슬러그 전부."""
    return {slug for name, slug in _containment_names() if name in title}


# ───────────────────────── 수집 ─────────────────────────


async def collect_ingredients(
    years: list[str], *, client: httpx.AsyncClient, limit: int | None
) -> list[fd.FoodIngredient]:
    """전 연도×월 식재료 목록 → 등장 월 누적 → 상세 수집."""
    appearances: dict[str, set[str]] = {}  # cntntsNo → {"YYYY-MM"}
    names: dict[str, str] = {}
    for year in years:
        for month in range(1, 13):
            mm = f"{month:02d}"
            try:
                metas = await fd.fetch_ingredient_metas(year, mm, client=client)
            except fd.MonthFdError as e:
                log.info("식재료 목록 실패 %s-%s: %s", year, mm, e)
                continue
            for m in metas:
                appearances.setdefault(m.cntnts_no, set()).add(f"{year}-{mm}")
                names.setdefault(m.cntnts_no, m.name)
    log.info("식재료 고유 %d건 (연도 %s)", len(appearances), ",".join(years))

    targets = list(appearances.items())
    if limit is not None:
        targets = targets[:limit]

    sem = asyncio.Semaphore(CONCURRENCY)

    async def _one(no: str, months: set[str]) -> fd.FoodIngredient | None:
        async with sem:
            try:
                return await fd.fetch_ingredient_detail(
                    no, client=client, months=tuple(sorted(months))
                )
            except fd.MonthFdError as e:
                log.info("식재료 상세 실패 no=%s (%s): %s", no, names.get(no, ""), e)
                return None

    results = await asyncio.gather(*(_one(no, ms) for no, ms in targets))
    return [r for r in results if r is not None]


async def collect_recipes(
    years: list[str], *, client: httpx.AsyncClient, limit: int | None
) -> list[fd.FoodRecipe]:
    metas: dict[str, fd.FoodRecipeMeta] = {}
    for year in years:
        for month in range(1, 13):
            mm = f"{month:02d}"
            try:
                batch = await fd.fetch_recipe_metas(year, mm, client=client)
            except fd.MonthFdError as e:
                log.info("레시피 목록 실패 %s-%s: %s", year, mm, e)
                continue
            for m in batch:
                metas.setdefault(m.cntnts_no, m)
    log.info("레시피 고유 %d건", len(metas))

    targets = list(metas.values())
    if limit is not None:
        targets = targets[:limit]

    sem = asyncio.Semaphore(CONCURRENCY)

    async def _one(meta: fd.FoodRecipeMeta) -> fd.FoodRecipe | None:
        async with sem:
            try:
                return await fd.fetch_recipe_detail(meta, client=client)
            except fd.MonthFdError as e:
                log.info("레시피 상세 실패 no=%s (%s): %s", meta.cntnts_no, meta.food_name, e)
                return None

    results = await asyncio.gather(*(_one(m) for m in targets))
    return [r for r in results if r is not None]


# ───────────────────────── 작물별 분류 ─────────────────────────


def group_by_crop(
    ingredients: list[fd.FoodIngredient], recipes: list[fd.FoodRecipe]
) -> dict:
    """식재료를 v3 작물 마스터(40종)와 매칭해 작물별 그룹으로 묶고, 레시피는
    목록 제목(식재료명)으로 같은 그룹에 연결한다."""
    groups: dict[str, dict] = {}  # 슬러그 → group
    name_to_slug: dict[str, str] = {}  # 식재료 원문명 → 슬러그
    unmatched: list[dict] = []

    for ing in ingredients:
        rec = asdict(ing)
        crop = _match_crop(ing.name)
        if crop is None:
            unmatched.append(rec)
            continue
        slug = crop["id"]
        g = groups.setdefault(
            slug,
            {
                "slug": slug,
                "cropName": crop["name"],
                "category": crop["category"],
                "ingredients": [],
                "recipes": [],
            },
        )
        g["ingredients"].append(rec)
        name_to_slug[ing.name] = slug

    matched_recipes = 0
    unmatched_recipes: list[dict] = []
    for r in recipes:
        rec = asdict(r)
        # 레시피 목록의 fdmtNm 은 대부분 비어 있어 음식명(fdNm) 부분일치로 매칭.
        # 여러 작물이 든 요리는 해당 작물 그룹 전부에 연결한다(수확카드 용도).
        slugs = match_recipe_crops(f"{r.list_title} {r.name}") & set(groups)
        if slugs:
            for slug in slugs:
                groups[slug]["recipes"].append(rec)
            matched_recipes += 1
        else:
            unmatched_recipes.append(rec)

    crops_sorted = sorted(groups.values(), key=lambda g: g["slug"])
    log.info(
        "작물 매칭: 식재료 %d/%d, 레시피 %d/%d, 작물그룹 %d개",
        sum(len(g["ingredients"]) for g in crops_sorted), len(ingredients),
        matched_recipes, len(recipes), len(crops_sorted),
    )
    return {
        "service": "monthFd",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "crops": len(crops_sorted),
            "ingredients": len(ingredients),
            "ingredientsMatched": sum(len(g["ingredients"]) for g in crops_sorted),
            "recipes": len(recipes),
            "recipesMatched": matched_recipes,
        },
        "crops": crops_sorted,
        "unmatchedIngredients": unmatched,
        "unmatchedRecipes": unmatched_recipes,
    }


# ───────────────────────── RAG 임베딩 ─────────────────────────


def _split(text: str) -> list[str]:
    """긴 본문 슬라이딩 분할 (ingest._chunk_text 와 동일 파라미터)."""
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]
    out: list[str] = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for start in range(0, len(text), step):
        c = text[start : start + CHUNK_SIZE].strip()
        if c:
            out.append(c)
    return out


def build_chunks(group: dict) -> list[str]:
    """작물 그룹 → RAG 청크. 섹션별 청크에 '[식재료] 섹션' 머리말을 붙여
    검색 결과만으로 출처를 알 수 있게 한다."""
    chunks: list[str] = []
    for ing in group["ingredients"]:
        name = ing["name"]
        season = ", ".join(m.split("-")[1] + "월" for m in ing["months"][:12])
        sections = (
            ("보관방법·손질법", ing["storage"]),
            ("섭취방법", ing["eating"]),
            ("영양성분·효능", ing["nutrition"]),
            ("품종특성·구입요령", ing["buying"]),
            ("식재료 유래", ing["origin"]),
            ("기타정보", ing["etc"]),
        )
        for title, body in sections:
            if not body:
                continue
            for part in _split(body):
                chunks.append(f"[{name}] {title}\n{part}")
        if season:
            chunks.append(f"[{name}] 제철(이달의 식재료 선정 월): {season}")
    for r in group["recipes"]:
        nut = ", ".join(f"{k} {v}" for k, v in r["nutrients"].items())
        body = "\n".join(
            s for s in (
                f"재료: {r['materials']}" if r["materials"] else "",
                f"조리법: {r['cooking']}" if r["cooking"] else "",
                f"영양({r['nutrients'].get('인분', '1인분')} 기준): {nut}" if nut else "",
            ) if s
        )
        if not body:
            continue
        for part in _split(body):
            chunks.append(f"[{group['cropName']} 레시피] {r['name']}\n{part}")
    return chunks


async def embed_groups(grouped: dict) -> None:
    from app.core.rag import store  # noqa: PLC0415 - OPENAI 키 필요 시에만 로드
    from app.core.rag.embeddings import embed_texts  # noqa: PLC0415

    total = 0
    for group in grouped["crops"]:
        chunks = build_chunks(group)
        if not chunks:
            continue
        vectors = await embed_texts(chunks)
        n = store.save(group["slug"], "monthfd", chunks, vectors, source="monthFd")
        total += n
        log.info("임베딩 저장 %s(%s): %d청크", group["cropName"], group["slug"], n)
    log.info("임베딩 완료: 총 %d청크", total)


# ───────────────────────── main ─────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(description="농사로 이달의 음식 수집·분류·임베딩")
    parser.add_argument("--limit", type=int, default=None, help="식재료/레시피 상세 최대 건수(테스트용)")
    parser.add_argument("--no-recipes", action="store_true", help="레시피 수집 생략")
    parser.add_argument("--embed", action="store_true", help="작물별 RAG 임베딩까지 수행")
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR, help="산출 디렉터리")
    args = parser.parse_args()

    async with httpx.AsyncClient() as client:
        try:
            years = await fd.fetch_years(client=client)
        except fd.MonthFdError as e:
            log.error("연도 목록 조회 실패: %s", e)
            log.error("NONGSARO_API_KEY 설정·monthFd 서비스 승인 상태를 확인하세요.")
            return
        if not years:
            log.error("연도 목록이 비어 있음 — 키/서비스 신청 상태 확인 필요")
            return
        log.info("연도: %s", ", ".join(years))

        ingredients = await collect_ingredients(years, client=client, limit=args.limit)
        log.info("식재료 상세 수집: %d건", len(ingredients))

        recipes: list[fd.FoodRecipe] = []
        if not args.no_recipes:
            recipes = await collect_recipes(years, client=client, limit=args.limit)
            log.info("레시피 상세 수집: %d건", len(recipes))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")

    (args.out_dir / "ingredients.json").write_text(
        json.dumps(
            {"fetchedAt": stamp, "count": len(ingredients),
             "items": [asdict(i) for i in ingredients]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (args.out_dir / "recipes.json").write_text(
        json.dumps(
            {"fetchedAt": stamp, "count": len(recipes),
             "items": [asdict(r) for r in recipes]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    grouped = group_by_crop(ingredients, recipes)
    (args.out_dir / "by_crop.json").write_text(
        json.dumps(grouped, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("저장 완료: %s (ingredients/recipes/by_crop.json)", args.out_dir)

    if args.embed:
        await embed_groups(grouped)


if __name__ == "__main__":
    asyncio.run(main())
