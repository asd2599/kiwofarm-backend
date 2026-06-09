"""커뮤니티 게시판 API 스키마 (자랑·나눔 피드).

작성은 multipart(Form+File)라 입력 본문 모델은 두지 않고 라우터에서 Form 으로 받는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

PostType = Literal["show", "share"]
ShareStatus = Literal["open", "closed"]
ShareRequestStatus = Literal["pending", "accepted", "declined"]


class PostImageOut(BaseModel):
    id: int
    url: str
    originalName: str | None = None
    contentType: str | None = None
    size: int = 0


class CommentOut(BaseModel):
    id: int
    authorName: str
    content: str
    createdAt: datetime
    isMine: bool = False


class ShareRequestOut(BaseModel):
    id: int
    requesterName: str
    message: str
    status: ShareRequestStatus
    createdAt: datetime
    isMine: bool = False  # 신청자 본인 여부


class PostListItemOut(BaseModel):
    id: int
    postType: PostType
    authorName: str
    cropSlug: str | None = None
    cropName: str | None = None
    title: str | None = None
    contentPreview: str
    images: list[PostImageOut] = []
    likeCount: int = 0
    commentCount: int = 0
    shareRequestCount: int = 0
    likedByMe: bool = False
    isMine: bool = False
    shareStatus: ShareStatus = "open"
    createdAt: datetime


class PostDetailOut(BaseModel):
    id: int
    postType: PostType
    authorName: str
    cropSlug: str | None = None
    cropName: str | None = None
    title: str | None = None
    content: str
    images: list[PostImageOut] = []
    likeCount: int = 0
    likedByMe: bool = False
    isMine: bool = False
    shareStatus: ShareStatus = "open"
    comments: list[CommentOut] = []
    # 나눔 신청: 작성자에겐 전체, 그 외엔 본인 신청만.
    shareRequests: list[ShareRequestOut] = []
    createdAt: datetime


class CommentIn(BaseModel):
    authorName: str
    content: str


class ShareRequestIn(BaseModel):
    requesterName: str
    message: str


class ShareRequestPatchIn(BaseModel):
    status: ShareRequestStatus


class LikeToggleOut(BaseModel):
    liked: bool
    likeCount: int
