"""농사로 PDF / 일반지식 텍스트의 RAG 청크 + 임베딩.

crop_key = f"{item_code}:{kind_code}" 단위로 청크를 모아두고,
embedding(pgvector) 코사인 검색으로 계획 생성 시 관련 청크를 회수한다.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

EMBED_DIM = 1536  # OpenAI text-embedding-3-small


class DocChunk(Base):
    __tablename__ = "doc_chunk"
    __table_args__ = (
        UniqueConstraint("crop_key", "source", "chunk_index", name="uq_doc_chunk_src_idx"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    crop_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sub_category_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False)  # ebook_code | "general"
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
