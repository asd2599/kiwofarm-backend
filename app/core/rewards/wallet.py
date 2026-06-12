"""포인트 지갑 — 단일 풀(활동점수 + 경매 정산) 잔액·에스크로·경매 정산.

- 잔액 = total_points(활동 집계) + point_ledger 합계(경매 ±).
- 에스크로(held) = 내가 '현재 최고가'인 진행중 경매들의 입찰 합 → 사용가능 = 잔액 − held.
- 정산(settle) = 마감 지난 경매의 최고가 낙찰 → 낙찰자 −, 나눔자 + 원장 기록(lazy).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rewards.points import total_points
from app.db.models.community import CommunityPost, ShareBid
from app.db.models.point import PointLedger


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dl: datetime) -> datetime:
    """naive datetime(예: sqlite)을 UTC 로 보아 안전 비교."""
    return dl if dl.tzinfo is not None else dl.replace(tzinfo=timezone.utc)


async def balance(session: AsyncSession, device: str) -> int:
    """총 잔액 = total_points(이미 활동점수 + 경매 정산 원장 포함)."""
    return await total_points(session, device)


async def _unsettled_auctions(session: AsyncSession) -> list[CommunityPost]:
    """미정산 나눔 경매 글(마감 비교는 호출자가 Python 으로 — DB별 tz 차이 회피)."""
    return list(
        (
            await session.scalars(
                select(CommunityPost).where(
                    CommunityPost.post_type == "share",
                    CommunityPost.auction_settled.is_(False),
                    CommunityPost.auction_deadline.isnot(None),
                )
            )
        ).all()
    )


async def _open_auction_tops(session: AsyncSession) -> dict[int, tuple[str, int]]:
    """진행 중(미정산·마감 전) 나눔 경매별 현재 최고 입찰 {post_id: (device, amount)}."""
    now = _now()
    post_ids = [
        p.id
        for p in await _unsettled_auctions(session)
        if _aware(p.auction_deadline) > now
    ]
    if not post_ids:
        return {}
    bids = (
        await session.scalars(select(ShareBid).where(ShareBid.post_id.in_(post_ids)))
    ).all()
    tops: dict[int, tuple[str, int]] = {}
    for b in bids:
        cur = tops.get(b.post_id)
        if cur is None or b.amount > cur[1]:
            tops[b.post_id] = (b.bidder_device, b.amount)
    return tops


async def held(
    session: AsyncSession, device: str, exclude_post: int | None = None
) -> int:
    """내가 현재 최고가인 입찰들의 합(에스크로). exclude_post 는 제외(같은 경매 올려부르기)."""
    tops = await _open_auction_tops(session)
    return sum(
        amt for pid, (dev, amt) in tops.items() if dev == device and pid != exclude_post
    )


async def available(
    session: AsyncSession, device: str, exclude_post: int | None = None
) -> int:
    """입찰 가능 한도 = 잔액 − 에스크로. (음수면 0 으로 보지 않고 그대로 — 검증에서 사용)"""
    return await balance(session, device) - await held(session, device, exclude_post)


# --- 팜 경제: 가입보너스 · 캘린더 생성 비용 ---
SIGNUP_BONUS = 300  # 로그인 계정 가입 시 1회 지급
CALENDAR_COST = 300  # 캘린더(농사계획) 1개 생성 비용
BRAG_COMPOSE_COST = 100  # 자랑글 AI 자동작성 1회 비용


def is_demo(device: str) -> bool:
    """시연(demo) 계정은 팜 차감 면제."""
    return device == "demo"


async def grant_signup_bonus(session: AsyncSession, device: str) -> bool:
    """로그인 계정 가입 시 +300 팜 1회 지급(멱등). 지급 시 True. 커밋은 호출자."""
    if not device.startswith("user:"):
        return False
    exists = await session.scalar(
        select(PointLedger.id)
        .where(PointLedger.device_id == device, PointLedger.reason == "signup_bonus")
        .limit(1)
    )
    if exists is not None:
        return False
    session.add(
        PointLedger(device_id=device, amount=SIGNUP_BONUS, reason="signup_bonus")
    )
    return True


async def top_bid(session: AsyncSession, post_id: int) -> ShareBid | None:
    """경매 현재 최고 입찰(동점은 먼저 입찰)."""
    return await session.scalar(
        select(ShareBid)
        .where(ShareBid.post_id == post_id)
        .order_by(ShareBid.amount.desc(), ShareBid.id.asc())
        .limit(1)
    )


async def settle_auction(session: AsyncSession, post: CommunityPost) -> bool:
    """마감 지난 미정산 경매 정산. 정산했으면 True. 커밋은 호출자가 한다."""
    if (
        post.post_type != "share"
        or post.auction_settled
        or post.auction_deadline is None
        or _aware(post.auction_deadline) > _now()
    ):
        return False
    top = await top_bid(session, post.id)
    if top is not None:
        # 낙찰자 차감 + 나눔자 적립.
        session.add(
            PointLedger(
                device_id=top.bidder_device,
                amount=-top.amount,
                reason="auction_win",
                post_id=post.id,
            )
        )
        session.add(
            PointLedger(
                device_id=post.device_id,
                amount=top.amount,
                reason="auction_sale",
                post_id=post.id,
            )
        )
        post.auction_winner_device = top.bidder_device
    post.auction_settled = True
    post.share_status = "closed"
    return True


async def settle_expired(session: AsyncSession) -> int:
    """마감 지난 모든 미정산 나눔 경매를 정산(lazy). 정산 건수 반환."""
    now = _now()
    expired = [
        p
        for p in await _unsettled_auctions(session)
        if _aware(p.auction_deadline) <= now
    ]
    n = 0
    for p in expired:
        if await settle_auction(session, p):
            n += 1
    if n:
        await session.commit()
    return n
