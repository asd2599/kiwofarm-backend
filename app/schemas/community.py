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


class BidOut(BaseModel):
    id: int
    bidderName: str
    amount: int
    createdAt: datetime
    isMine: bool = False


class AuctionSummaryOut(BaseModel):
    """목록용 경매 요약."""

    deadline: datetime
    settled: bool = False
    closed: bool = False  # 마감 지났거나 정산됨
    topBid: int | None = None
    bidCount: int = 0


class AuctionOut(BaseModel):
    """상세용 경매 정보(내 상태 포함)."""

    deadline: datetime
    settled: bool = False
    closed: bool = False
    topBid: int | None = None
    topBidderName: str | None = None
    bidCount: int = 0
    myBid: int | None = None  # 이 경매에서 내 현재 입찰액
    iAmSeller: bool = False
    winnerIsMe: bool = False
    myAvailable: int = 0  # 이 경매에 입찰 가능한 내 한도


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
    auction: AuctionSummaryOut | None = None
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
    # 나눔 신청(레거시) — 작성자에겐 전체, 그 외엔 본인 신청만.
    shareRequests: list[ShareRequestOut] = []
    auction: AuctionOut | None = None
    bids: list[BidOut] = []
    createdAt: datetime


class CommentIn(BaseModel):
    authorName: str
    content: str


class ShareRequestIn(BaseModel):
    requesterName: str
    message: str


class ShareRequestPatchIn(BaseModel):
    status: ShareRequestStatus


class BidIn(BaseModel):
    bidderName: str
    amount: int


class WalletOut(BaseModel):
    balance: int  # 활동점수 + 경매 정산
    available: int  # 잔액 − 진행중 경매 에스크로


class LikeToggleOut(BaseModel):
    liked: bool
    likeCount: int
