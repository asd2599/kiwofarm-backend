"""농사로 cropEbook 작목 마스터 트리 동기화.

흐름 (매뉴얼 5.1):
  mainCategoryList
    → middleCategoryList(main)
      → subCategoryList(middle)
        → ebookList(sub)
          → cropIndexList(ebookCode, fileNo)

각 단계 결과를 PostgreSQL에 upsert. 재실행 idempotent (PK 충돌 시 업데이트).

실행:
    uv run python scripts/sync_crop_master.py
환경:
    .env 의 NONGSARO_API_KEY, DATABASE_URL 사용.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

# scripts/ 직접 실행 대응
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data import nongsaro_ebook as api
from app.db.models.crop_master import (
    CropEbook,
    CropEbookIndex,
    CropMainCategory,
    CropMiddleCategory,
    CropSubCategory,
)
from app.db.session import async_session_factory, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync_crop_master")

# 농사로 서버 부하 방지 + 결과코드 91 회피
CONCURRENCY_LEVEL = 6


def _to_int(v: str | None) -> int | None:
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


async def _upsert(session: AsyncSession, model, rows: list[dict], pk_cols: list[str]) -> int:
    """Postgres ON CONFLICT upsert. rows가 비어있으면 0 반환."""
    if not rows:
        return 0
    stmt = insert(model).values(rows)
    update_cols = {c.name: stmt.excluded[c.name] for c in model.__table__.columns if c.name not in pk_cols}
    stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=update_cols)
    await session.execute(stmt)
    return len(rows)


async def sync_mains(session: AsyncSession, client: httpx.AsyncClient) -> list[str]:
    items = await api.main_category_list(client=client)
    rows = [
        {"main_category_code": r["mainCategoryCode"], "main_category_nm": r.get("mainCategoryNm", "")}
        for r in items
        if r.get("mainCategoryCode")
    ]
    n = await _upsert(session, CropMainCategory, rows, ["main_category_code"])
    await session.commit()
    log.info("main %d", n)
    return [r["main_category_code"] for r in rows]


async def sync_middles(
    session: AsyncSession, client: httpx.AsyncClient, main_code: str
) -> list[str]:
    items = await api.middle_category_list(main_code, client=client)
    rows = [
        {
            "middle_category_code": r["middleCategoryCode"],
            "main_category_code": main_code,
            "middle_category_nm": r.get("middleCategoryNm", ""),
        }
        for r in items
        if r.get("middleCategoryCode")
    ]
    n = await _upsert(session, CropMiddleCategory, rows, ["middle_category_code"])
    await session.commit()
    log.info("  middle(main=%s) %d", main_code, n)
    return [r["middle_category_code"] for r in rows]


async def sync_subs(
    session: AsyncSession, client: httpx.AsyncClient, middle_code: str
) -> list[str]:
    items = await api.sub_category_list(middle_code, client=client)
    rows = [
        {
            "sub_category_code": r["subCategoryCode"],
            "middle_category_code": middle_code,
            "sub_category_nm": r.get("subCategoryNm", ""),
        }
        for r in items
        if r.get("subCategoryCode")
    ]
    n = await _upsert(session, CropSubCategory, rows, ["sub_category_code"])
    await session.commit()
    log.info("    sub(middle=%s) %d", middle_code, n)
    return [r["sub_category_code"] for r in rows]


async def sync_ebooks(
    session: AsyncSession, client: httpx.AsyncClient, sub_code: str
) -> list[tuple[str, str]]:
    items = await api.ebook_list(sub_code, client=client)
    rows = []
    for r in items:
        ebook_code = r.get("ebookCode")
        file_no = r.get("cropsEbookFileNo")
        if not ebook_code or not file_no:
            continue
        rows.append(
            {
                "ebook_code": ebook_code,
                "crops_ebook_file_no": file_no,
                "sub_category_code": sub_code,
                "ebook_name": r.get("ebookName"),
                "std_item_cd": r.get("stdItemCd"),
                "std_item_nm": r.get("stdItemNm"),
                "orginl_file_nm": r.get("orginlFileNm"),
                "crops_ebook_file": r.get("cropsEbookFile"),
                "atchmnfl_group_esntl_ebook_code": r.get("atchmnflGroupEsntlEbookCode"),
                "atchmnfl_group_esntl_ebook_nm": r.get("atchmnflGroupEsntlEbookNm"),
            }
        )
    n = await _upsert(session, CropEbook, rows, ["ebook_code", "crops_ebook_file_no"])
    await session.commit()
    log.info("      ebook(sub=%s) %d", sub_code, n)
    return [(r["ebook_code"], r["crops_ebook_file_no"]) for r in rows]


async def sync_indexes(
    session: AsyncSession,
    client: httpx.AsyncClient,
    ebook_code: str,
    file_no: str,
) -> int:
    items = await api.crop_index_list(ebook_code, file_no, client=client)
    rows = []
    for r in items:
        idx_no = r.get("cropsEbookIndexNo")
        if not idx_no:
            continue
        rows.append(
            {
                "ebook_code": ebook_code,
                "crops_ebook_file_no": file_no,
                "crops_ebook_index_no": idx_no,
                "ebook_url": r.get("ebookUrl"),
                "ebook_mobile_url": r.get("ebookMobileUrl"),
                "index_base_page": _to_int(r.get("indexBasePage")),
                "index_page": _to_int(r.get("indexPage")),
                "index_level": _to_int(r.get("indexLevel")),
                "index_name": r.get("indexName"),
                "index_order": _to_int(r.get("indexOrder")),
                "index_sid": r.get("indexSid"),
                "std_item_cd": r.get("stdItemCd"),
                "std_item_nm": r.get("stdItemNm"),
            }
        )
    n = await _upsert(
        session,
        CropEbookIndex,
        rows,
        ["ebook_code", "crops_ebook_file_no", "crops_ebook_index_no"],
    )
    await session.commit()
    return n


async def _gather_bounded(coros, sem: asyncio.Semaphore):
    async def _run(c):
        async with sem:
            return await c

    return await asyncio.gather(*[_run(c) for c in coros], return_exceptions=True)


async def main() -> None:
    started = time.perf_counter()
    sem = asyncio.Semaphore(CONCURRENCY_LEVEL)
    totals = {"main": 0, "middle": 0, "sub": 0, "ebook": 0, "index": 0, "errors": 0}

    timeout = httpx.Timeout(api.DEFAULT_TIMEOUT)
    limits = httpx.Limits(max_connections=CONCURRENCY_LEVEL * 2)

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        async with async_session_factory() as session:
            mains = await sync_mains(session, client)
            totals["main"] = len(mains)

            # middle 단계
            middle_results = await _gather_bounded(
                [sync_middles(session, client, m) for m in mains], sem
            )
            middles: list[str] = []
            for r in middle_results:
                if isinstance(r, Exception):
                    log.error("middle 실패: %s", r)
                    totals["errors"] += 1
                    continue
                middles.extend(r)
            totals["middle"] = len(middles)

            sub_results = await _gather_bounded(
                [sync_subs(session, client, m) for m in middles], sem
            )
            subs: list[str] = []
            for r in sub_results:
                if isinstance(r, Exception):
                    log.error("sub 실패: %s", r)
                    totals["errors"] += 1
                    continue
                subs.extend(r)
            totals["sub"] = len(subs)

            ebook_results = await _gather_bounded(
                [sync_ebooks(session, client, s) for s in subs], sem
            )
            ebooks: list[tuple[str, str]] = []
            for r in ebook_results:
                if isinstance(r, Exception):
                    log.error("ebook 실패: %s", r)
                    totals["errors"] += 1
                    continue
                ebooks.extend(r)
            totals["ebook"] = len(ebooks)

            index_results = await _gather_bounded(
                [sync_indexes(session, client, ec, fn) for ec, fn in ebooks], sem
            )
            for r in index_results:
                if isinstance(r, Exception):
                    log.error("index 실패: %s", r)
                    totals["errors"] += 1
                    continue
                totals["index"] += r

    await engine.dispose()
    elapsed = time.perf_counter() - started
    log.info(
        "완료 %.1fs | main=%d middle=%d sub=%d ebook=%d index=%d errors=%d",
        elapsed,
        totals["main"],
        totals["middle"],
        totals["sub"],
        totals["ebook"],
        totals["index"],
        totals["errors"],
    )


if __name__ == "__main__":
    asyncio.run(main())
