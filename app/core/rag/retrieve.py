"""로컬 임베딩 스토어에서 numpy 코사인 검색으로 작목 청크 회수.

이전엔 pgvector(postgres)의 cosine_distance 로 검색했으나, 작목 단위 청크가
수백 개 이하라 로컬 .npy 를 메모리에 올려 brute-force 코사인으로 충분히 빠르다.
DB 세션이 필요 없다.
"""

from __future__ import annotations

import numpy as np

from app.core.rag import store
from app.core.rag.embeddings import embed_query


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-8)


async def retrieve(crop_key: str, query: str, k: int = 6) -> list[str]:
    """query 와 코사인 유사도가 높은 청크 본문 상위 k개(전 kind 동일 가중)."""
    chunks, vectors = store.load_all(crop_key)
    if not chunks:
        return []

    q = _unit(np.asarray(await embed_query(query), dtype=np.float32))
    v_norm = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-8)
    sims = v_norm @ q  # (N,)

    k = min(k, len(chunks))
    top = np.argpartition(-sims, k - 1)[:k]
    top = top[np.argsort(-sims[top])]
    return [chunks[i] for i in top]


async def retrieve_boosted(
    crop_key: str,
    query: str,
    k: int = 6,
    boost: dict[str, float] | None = None,
) -> list[str]:
    """kind 별 가중치를 더해 검색. boost={'monthtech':0.08} 면 그 kind 점수에 +0.08.

    이달의 농업기술(monthtech, 작물특화) 을 주간 회보(weekfarm, 다작물)보다 우선시키는
    추천 컨텍스트용. 데이터 없으면 빈 리스트.
    """
    boost = boost or {}
    groups = store.load_grouped(crop_key)
    if not groups:
        return []

    q = _unit(np.asarray(await embed_query(query), dtype=np.float32))
    scored: list[tuple[float, str]] = []
    for kind, chunks, vecs in groups:
        v_norm = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-8)
        sims = v_norm @ q
        b = boost.get(kind, 0.0)
        scored.extend((float(sims[i]) + b, c) for i, c in enumerate(chunks))

    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:k]]
