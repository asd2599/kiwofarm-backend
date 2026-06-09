"""커뮤니티 게시판 모델 — 수확물 자랑·나눔 피드.

농사계획·수확과 달리 device_id 로 격리하지 않는 **공용 피드**다(목록은 모두의
글을 함께 본다). 게스트는 전원 'demo' device 를 공유하므로(api/deps.py) 작성자
식별은 글마다 닉네임(author_name) 문자열로 저장한다.

사진은 farm_plan.MemoImage 와 동일하게 bytea(data, deferred)에 보존한다.
마이그레이션 20260609_0010 과 1:1 매핑.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CommunityPost(Base):
    __tablename__ = "community_post"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 작성자 식별 — farm_plan.device_id 와 동일 체계. 'demo'=게스트 공용.
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    author_name: Mapped[str] = mapped_column(String(40), nullable=False)
    # 'show'=자랑, 'share'=나눔
    post_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="show", index=True
    )
    crop_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    crop_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    harvest_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("harvest_record.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 나눔 진행 상태 — 'open'=신청 가능, 'closed'=마감
    share_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="open"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    images: Mapped[list[PostImage]] = relationship(
        back_populates="post",
        cascade="all, delete-orphan",
        order_by="PostImage.id",
    )
    comments: Mapped[list[PostComment]] = relationship(
        back_populates="post",
        cascade="all, delete-orphan",
        order_by="PostComment.id",
    )
    likes: Mapped[list[PostLike]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )
    share_requests: Mapped[list[PostShareRequest]] = relationship(
        back_populates="post",
        cascade="all, delete-orphan",
        order_by="PostShareRequest.id",
    )


class PostImage(Base):
    """게시글 사진 — MemoImage 와 동일 패턴. 원본은 data(bytea)에 저장."""

    __tablename__ = "post_image"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("community_post.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # deferred: 피드 목록 조회 때 사진 바이트까지 끌어오지 않도록 지연 로딩.
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    post: Mapped[CommunityPost] = relationship(back_populates="images")


class PostComment(Base):
    __tablename__ = "post_comment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("community_post.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    author_name: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    post: Mapped[CommunityPost] = relationship(back_populates="comments")


class PostLike(Base):
    __tablename__ = "post_like"
    __table_args__ = (
        UniqueConstraint("post_id", "device_id", name="uq_post_like_device"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("community_post.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    post: Mapped[CommunityPost] = relationship(back_populates="likes")


class PostShareRequest(Base):
    """나눔 신청 — 나눔글(post_type='share')에 '저 주세요' 신청."""

    __tablename__ = "post_share_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("community_post.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requester_device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    requester_name: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # 'pending'|'accepted'|'declined'
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    post: Mapped[CommunityPost] = relationship(back_populates="share_requests")
