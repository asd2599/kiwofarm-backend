"""pgvector 코사인 검색으로 작목 청크 회수."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag.embeddings import embed_query
from app.db.models.doc_chunk import DocChunk


async def retrieve(
    session: AsyncSession, crop_key: str, query: str, k: int = 6
) -> list[str]:
    """query 와 의미적으로 가까운 청크 본문 상위 k개."""
    q_vec = await embed_query(query)
    stmt = (
        select(DocChunk.content)
        .where(DocChunk.crop_key == crop_key)
        .order_by(DocChunk.embedding.cosine_distance(q_vec))
        .limit(k)
    )
    rows = await session.scalars(stmt)
    return list(rows)
