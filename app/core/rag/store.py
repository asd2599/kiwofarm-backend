"""작목 임베딩 로컬 파일 스토어 (postgres/pgvector 대체).

작목 단위(crop_key)로 청크 텍스트와 임베딩 벡터를 backend/data/embeddings/ 아래
파일로 저장한다. 규모가 작고(작목 수십 × ~80청크) 쓰기 1회·읽기 위주라 벡터 DB
없이 numpy brute-force 코사인으로 충분히 빠르며, 인프라가 필요 없다.

파일 레이아웃 (kind = "cultivation" | "ncpms" | "garden" | ...):
  {safe_key}.{kind}.npy   float32 (N, 1536) 임베딩 행렬
  {safe_key}.{kind}.json  {"source": str, "chunks": [str, ...]}

- vectors 는 .npy (pickle 미사용), 메타는 사람이 읽을 수 있는 .json 으로 분리.
- crop_key 의 ':' 는 Windows 파일명에 못 쓰므로 '__' 로 치환.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# backend/app/core/rag/store.py → parents[3] = backend
EMBED_DIR = Path(__file__).resolve().parents[3] / "data" / "embeddings"

# kind 구분과 키 단위 (v3 표준 키 = crops_master 슬러그 — 슬러그 키에선 두 단위가 동일):
#  - CROP_KINDS: crop_key 단위. cultivation=농사로PDF/GPT 재배지식, ncpms=병해충,
#    garden=농사로 텃밭가꾸기(fildMnfct, v3 1순위 — 배치 sync_garden + 온디맨드 ingest).
#  - ITEM_KINDS: item_code 단위(레거시 품종 공유). monthtech=이달의 농업기술(텃밭 선별본),
#    general=GPT 표준 재배지식 폴백. 출처별로 나눠 저장해 검색 시 가중치를 줄 수 있다.
#  - weekfarm 은 2026-06-04 폐기(회보 통짜 복사라 작물간 중복·무관 내용 오염).
#  - monthfd(보관·손질·영양)는 수확인증 카드 전용 — 의도적으로 KINDS 제외, 카드에서 명시 로드.
#  - "_common".garden = 작물 공통 텃밭 가이드(챗봇이 작물 키와 병행 검색).
CROP_KINDS: tuple[str, ...] = ("cultivation", "ncpms", "garden")
ITEM_KINDS: tuple[str, ...] = ("monthtech", "general")
KINDS: tuple[str, ...] = CROP_KINDS + ITEM_KINDS


def _safe(crop_key: str) -> str:
    return crop_key.replace(":", "__").replace("/", "_").replace("\\", "_")


def _vec_path(crop_key: str, kind: str) -> Path:
    return EMBED_DIR / f"{_safe(crop_key)}.{kind}.npy"


def _meta_path(crop_key: str, kind: str) -> Path:
    return EMBED_DIR / f"{_safe(crop_key)}.{kind}.json"


def exists(crop_key: str, kind: str) -> bool:
    return _vec_path(crop_key, kind).exists() and _meta_path(crop_key, kind).exists()


def save(
    crop_key: str, kind: str, chunks: list[str], vectors: list[list[float]], source: str
) -> int:
    """청크+벡터를 kind 파일로 저장. 반환: 저장한 청크 수."""
    if not chunks:
        return 0
    EMBED_DIR.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(vectors, dtype=np.float32)
    np.save(_vec_path(crop_key, kind), arr)
    _meta_path(crop_key, kind).write_text(
        json.dumps({"source": source, "chunks": chunks}, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("embed store save key=%s kind=%s source=%s n=%d", crop_key, kind, source, len(chunks))
    return len(chunks)


def load_grouped(crop_key: str) -> list[tuple[str, list[str], np.ndarray]]:
    """작물의 청크를 kind 별로 (kind, 청크리스트, 벡터행렬) 목록으로 반환.

    crop_key(item:kind) 단위 CROP_KINDS + item_code 단위 ITEM_KINDS 를 모두 로드한다
    (품종 공유). 검색 시 kind 별 가중치를 주려는 호출자(추천)가 사용한다. 손상 파일은 건너뜀.
    """
    item_code = crop_key.split(":", 1)[0]
    out: list[tuple[str, list[str], np.ndarray]] = []
    for key, kinds in ((crop_key, CROP_KINDS), (item_code, ITEM_KINDS)):
        for kind in kinds:
            if not exists(key, kind):
                continue
            try:
                vecs = np.load(_vec_path(key, kind))
                meta = json.loads(_meta_path(key, kind).read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as e:
                log.warning("embed store load 실패 key=%s kind=%s reason=%s", key, kind, e)
                continue
            chunks = meta.get("chunks") or []
            if len(chunks) != len(vecs):
                log.warning(
                    "embed store 불일치 key=%s kind=%s chunks=%d vecs=%d",
                    key, kind, len(chunks), len(vecs),
                )
                continue
            out.append((kind, chunks, vecs))
    return out


def load_all(crop_key: str) -> tuple[list[str], np.ndarray]:
    """작물의 모든 kind 청크를 합쳐 (청크 리스트, 벡터 행렬) 반환. 없으면 ([], (0,0))."""
    groups = load_grouped(crop_key)
    if not groups:
        return [], np.empty((0, 0), dtype=np.float32)
    chunks_all: list[str] = []
    mats: list[np.ndarray] = []
    for _kind, chunks, vecs in groups:
        chunks_all.extend(chunks)
        mats.append(vecs)
    return chunks_all, np.vstack(mats)


def cultivation_source(crop_key: str) -> str | None:
    """재배지식(cultivation) 파일의 source 라벨. ebook_code → PDF, 'general' → 일반."""
    p = _meta_path(crop_key, "cultivation")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("source")
    except (OSError, json.JSONDecodeError):
        return None
