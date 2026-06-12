"""커뮤니티 게시판 API — 수확물 자랑·나눔 피드.

농사계획·수확과 달리 **공용 피드**다(목록은 device 필터 없이 모두의 글). 작성자는
글마다 닉네임(author_name) 으로 식별한다. 사진은 bytea(post_image.data)에 저장하고
<img> 서빙은 무인증(헤더를 못 보냄) — farmplan memo-images 패턴과 동일.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from app.api.deps import DeviceDep
from app.core.community.compose import PHOTO_MARKER, ComposeError, compose_brag
from app.core.harvest import rules
from app.core.planting import matrix
from app.core.rewards import wallet
from app.core.storage import read_image
from app.db.models.community import (
    CommunityPost,
    PostComment,
    PostImage,
    PostLike,
    PostShareRequest,
    ShareBid,
)
from app.db.models.farm_plan import FarmPlan, MemoImage, TaskMemo
from app.db.models.point import PointLedger
from app.db.session import get_session
from app.schemas.community import (
    AuctionOut,
    AuctionSummaryOut,
    BidIn,
    BidOut,
    CommentIn,
    CommentOut,
    ComposeDraftIn,
    ComposeDraftOut,
    LikeToggleOut,
    PostDetailOut,
    PostImageOut,
    PostListItemOut,
    ShareRequestIn,
    ShareRequestOut,
    ShareRequestPatchIn,
    WalletOut,
)

router = APIRouter(prefix="/community", tags=["community"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

PREVIEW_LEN = 140

# 글 꾸미기 프리셋 허용값 — 잘못된 값은 무시(기본 프리셋으로 폴백).
_STYLE_FONTS = {"gowun", "pretendard", "nanum"}
_STYLE_SIZES = {"sm", "md", "lg"}
_STYLE_ALIGNS = {"left", "center"}


def _clean_style(raw: str | None) -> str | None:
    """ "font|size|align" 검증·정규화. 형식·허용값 어긋나면 None(=기본)."""
    if not raw:
        return None
    parts = raw.split("|")
    if len(parts) != 3:
        return None
    font, size, align = (p.strip() for p in parts)
    if font in _STYLE_FONTS and size in _STYLE_SIZES and align in _STYLE_ALIGNS:
        return f"{font}|{size}|{align}"
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _passed(dl: datetime | None) -> bool:
    """마감 지났는지 — DB가 naive 로 돌려줘도(예: sqlite) UTC 로 보아 안전 비교."""
    if dl is None:
        return False
    if dl.tzinfo is None:
        dl = dl.replace(tzinfo=timezone.utc)
    return dl <= _now()


def _auction_summary(post: CommunityPost) -> AuctionSummaryOut | None:
    """목록용 경매 요약(post.bids 로드 필요)."""
    if post.post_type != "share" or post.auction_deadline is None:
        return None
    top = max((b.amount for b in post.bids), default=None)
    closed = post.auction_settled or _passed(post.auction_deadline)
    return AuctionSummaryOut(
        deadline=post.auction_deadline,
        settled=post.auction_settled,
        closed=closed,
        topBid=top,
        bidCount=len(post.bids),
    )


async def _auction_detail(
    session: AsyncSession, post: CommunityPost, device: str
) -> AuctionOut | None:
    """상세용 경매 정보(post.bids 로드 필요)."""
    if post.post_type != "share" or post.auction_deadline is None:
        return None
    bids = sorted(post.bids, key=lambda b: (-b.amount, b.id))
    top = bids[0] if bids else None
    my_bid = max((b.amount for b in bids if b.bidder_device == device), default=None)
    closed = post.auction_settled or _passed(post.auction_deadline)
    avail = (
        await wallet.available(session, device, exclude_post=post.id)
        if device.startswith("user:")
        else 0
    )
    return AuctionOut(
        deadline=post.auction_deadline,
        settled=post.auction_settled,
        closed=closed,
        topBid=top.amount if top else None,
        topBidderName=top.bidder_name if top else None,
        bidCount=len(bids),
        myBid=my_bid,
        iAmSeller=post.device_id == device,
        winnerIsMe=post.auction_winner_device == device,
        myAvailable=avail,
    )


def _image_url(img: PostImage) -> str:
    return f"/api/v1/community/images/{img.id}"


def _img_out(img: PostImage) -> PostImageOut:
    return PostImageOut(
        id=img.id,
        url=_image_url(img),
        originalName=img.original_name,
        contentType=img.content_type,
        size=img.size_bytes,
    )


def _comment_out(c: PostComment, device: str) -> CommentOut:
    return CommentOut(
        id=c.id,
        authorName=c.author_name,
        content=c.content,
        createdAt=c.created_at,
        isMine=c.device_id == device,
    )


def _share_out(s: PostShareRequest, device: str) -> ShareRequestOut:
    return ShareRequestOut(
        id=s.id,
        requesterName=s.requester_name,
        message=s.message,
        status=s.status,  # type: ignore[arg-type]
        createdAt=s.created_at,
        isMine=s.requester_device_id == device,
    )


async def _build_detail(session: AsyncSession, post_id: int, device: str) -> PostDetailOut:
    post = await session.scalar(
        select(CommunityPost)
        .where(CommunityPost.id == post_id)
        .options(
            selectinload(CommunityPost.images),
            selectinload(CommunityPost.comments),
            selectinload(CommunityPost.likes),
            selectinload(CommunityPost.share_requests),
            selectinload(CommunityPost.bids),
        )
    )
    if post is None:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")
    is_mine = post.device_id == device
    # 나눔 신청(레거시): 작성자에겐 전체, 그 외엔 본인 신청만 노출.
    reqs = (
        post.share_requests
        if is_mine
        else [s for s in post.share_requests if s.requester_device_id == device]
    )
    bids_sorted = sorted(post.bids, key=lambda b: (-b.amount, b.id))
    return PostDetailOut(
        id=post.id,
        postType=post.post_type,  # type: ignore[arg-type]
        authorName=post.author_name,
        cropSlug=post.crop_slug,
        cropName=post.crop_name,
        title=post.title,
        content=post.content,
        style=post.style,
        aiAssisted=post.ai_assisted,
        images=[_img_out(i) for i in post.images],
        likeCount=len(post.likes),
        likedByMe=any(lk.device_id == device for lk in post.likes),
        isMine=is_mine,
        shareStatus=post.share_status,  # type: ignore[arg-type]
        comments=[_comment_out(c, device) for c in post.comments],
        shareRequests=[_share_out(s, device) for s in reqs],
        auction=await _auction_detail(session, post, device),
        bids=[
            BidOut(
                id=b.id,
                bidderName=b.bidder_name,
                amount=b.amount,
                createdAt=b.created_at,
                isMine=b.bidder_device == device,
            )
            for b in bids_sorted
        ],
        createdAt=post.created_at,
    )


@router.get("/posts", response_model=list[PostListItemOut])
async def list_posts(
    session: SessionDep,
    device: DeviceDep,
    type: str | None = Query(None),
    crop: str | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[PostListItemOut]:
    """공용 피드 — 모두의 글을 최신순. type(show|share)·crop 으로 필터."""
    await wallet.settle_expired(session)  # 마감 지난 경매 정산(lazy)
    q = (
        select(CommunityPost)
        .options(
            selectinload(CommunityPost.images),
            selectinload(CommunityPost.bids),
        )
        .order_by(CommunityPost.created_at.desc())
    )
    if type in ("show", "share"):
        q = q.where(CommunityPost.post_type == type)
    if crop:
        q = q.where(CommunityPost.crop_slug == crop)
    posts = (await session.scalars(q.limit(limit).offset(offset))).all()
    if not posts:
        return []

    ids = [p.id for p in posts]

    async def _counts(model: type) -> dict[int, int]:
        rows = await session.execute(
            select(model.post_id, func.count())
            .where(model.post_id.in_(ids))
            .group_by(model.post_id)
        )
        return {pid: n for pid, n in rows.all()}

    like_counts = await _counts(PostLike)
    comment_counts = await _counts(PostComment)
    share_counts = await _counts(PostShareRequest)
    liked = set(
        (
            await session.scalars(
                select(PostLike.post_id).where(
                    PostLike.post_id.in_(ids), PostLike.device_id == device
                )
            )
        ).all()
    )

    return [
        PostListItemOut(
            id=p.id,
            postType=p.post_type,  # type: ignore[arg-type]
            authorName=p.author_name,
            cropSlug=p.crop_slug,
            cropName=p.crop_name,
            title=p.title,
            contentPreview=p.content.replace(PHOTO_MARKER, " ").strip()[:PREVIEW_LEN],
            style=p.style,
            aiAssisted=p.ai_assisted,
            images=[_img_out(i) for i in p.images],
            likeCount=like_counts.get(p.id, 0),
            commentCount=comment_counts.get(p.id, 0),
            shareRequestCount=share_counts.get(p.id, 0),
            likedByMe=p.id in liked,
            isMine=p.device_id == device,
            shareStatus=p.share_status,  # type: ignore[arg-type]
            auction=_auction_summary(p),
            createdAt=p.created_at,
        )
        for p in posts
    ]


@router.post("/compose-draft", response_model=ComposeDraftOut)
async def compose_draft(
    payload: ComposeDraftIn, session: SessionDep, device: DeviceDep
) -> ComposeDraftOut:
    """선택한 작물 캘린더의 메모를 AI가 블로그형 자랑글 초안으로 정리해 돌려준다.

    사진은 프론트가 '내 기록 사진'으로 첨부하므로 여기선 글(제목·본문)만 생성한다.
    """
    plans = (
        await session.scalars(
            select(FarmPlan)
            .where(FarmPlan.device_id == device)
            .options(selectinload(FarmPlan.memos).selectinload(TaskMemo.images))
        )
    ).all()
    crop = matrix.get_crop(payload.cropSlug)
    crop_name = crop["name"] if crop else payload.cropSlug

    # 메모(텍스트)와 사진 id 를 날짜순으로 모은다 — 본문·사진 시간순 정렬을 맞추기 위해.
    entries: list[tuple[str, str, list[int]]] = []
    for plan in plans:
        if rules.plan_slug(plan) != payload.cropSlug:
            continue
        for m in plan.memos:
            text = (m.content or "").strip()
            # data(bytea)는 deferred — async 세션에서 직접 접근하면 lazy-load(MissingGreenlet)
            # 로 500. 업로드 시 size_bytes=len(data) 로 채워지므로 그걸로 존재 여부를 본다.
            img_ids = [img.id for img in m.images if img.size_bytes > 0]
            if text or img_ids:
                entries.append((m.memo_date.isoformat(), text, img_ids))
    entries.sort(key=lambda x: x[0])
    dated = [(d, t) for d, t, _ in entries if t]
    image_ids = [iid for _, _, ids in entries for iid in ids]
    if not dated:
        raise HTTPException(
            status_code=422,
            detail="정리할 메모 기록이 없어요. 캘린더에 메모를 남기고 다시 시도해 주세요.",
        )
    # 팜 차감(demo 면제). 캘린더 생성과 동일 패턴 — 먼저 잔액 확인, 생성 성공 후 차감.
    cost = wallet.BRAG_COMPOSE_COST
    if not wallet.is_demo(device):
        if await wallet.grant_signup_bonus(session, device):
            await session.commit()
        if await wallet.available(session, device) < cost:
            raise HTTPException(
                status_code=402,
                detail=f"팜이 부족해요. AI 자동작성에 {cost}팜이 필요합니다.",
            )
    try:
        draft = await compose_brag(crop_name, dated, len(image_ids))
    except ComposeError as e:
        raise HTTPException(
            status_code=503, detail="AI 자동 작성을 지금 사용할 수 없어요."
        ) from e
    # 성공했을 때만 차감(AI 실패 시 과금 없음).
    if not wallet.is_demo(device):
        session.add(
            PointLedger(device_id=device, amount=-cost, reason="brag_compose")
        )
        await session.commit()
    return ComposeDraftOut(**draft, imageIds=image_ids)


@router.post("/posts", response_model=PostDetailOut)
async def create_post(
    session: SessionDep,
    device: DeviceDep,
    content: Annotated[str, Form()],
    authorName: Annotated[str, Form()],
    postType: Annotated[str, Form()] = "show",
    title: Annotated[str | None, Form()] = None,
    style: Annotated[str | None, Form()] = None,
    aiAssisted: Annotated[bool, Form()] = False,
    cropSlug: Annotated[str | None, Form()] = None,
    cropName: Annotated[str | None, Form()] = None,
    harvestRecordId: Annotated[int | None, Form()] = None,
    fromMemoImageIds: Annotated[list[int] | None, Form()] = None,
    auctionDeadline: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> PostDetailOut:
    """게시글 작성(multipart). 자랑은 누구나, 나눔은 로그인 + 마감시간(경매)."""
    body = content.strip()
    if not body:
        raise HTTPException(status_code=422, detail="내용을 입력해 주세요.")
    name = (authorName or "").strip()[:40] or "텃밭러"
    pt = postType if postType in ("show", "share") else "show"

    # 나눔(share)=포인트 경매: 로그인 + 마감시간 필수.
    deadline = None
    if pt == "share":
        if not device.startswith("user:"):
            raise HTTPException(
                status_code=401, detail="나눔(경매)은 로그인 후 이용할 수 있어요."
            )
        if not auctionDeadline:
            raise HTTPException(status_code=422, detail="나눔 마감 시간을 정해주세요.")
        try:
            deadline = datetime.fromisoformat(auctionDeadline.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="마감 시간 형식이 올바르지 않아요.") from None
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if deadline <= _now():
            raise HTTPException(status_code=422, detail="마감 시간은 현재 이후여야 해요.")

    post = CommunityPost(
        device_id=device,
        author_name=name,
        post_type=pt,
        title=(title or "").strip()[:255] or None,
        content=body,
        style=_clean_style(style),
        ai_assisted=bool(aiAssisted),
        crop_slug=cropSlug or None,
        crop_name=cropName or None,
        harvest_record_id=harvestRecordId,
        auction_deadline=deadline,
    )
    session.add(post)
    await session.flush()  # post.id 확보

    for f in files or []:
        if not f.filename:
            continue
        data = await read_image(f)  # 형식·크기 검증 → HTTPException
        session.add(
            PostImage(
                post_id=post.id,
                data=data,
                original_name=f.filename,
                content_type=f.content_type,
                size_bytes=len(data),
            )
        )

    # 내 메모 사진 복사 — 소유권(plan.device_id) 확인 후 bytea 복제.
    if fromMemoImageIds:
        rows = await session.execute(
            select(MemoImage)
            .join(TaskMemo, MemoImage.memo_id == TaskMemo.id)
            .join(FarmPlan, TaskMemo.plan_id == FarmPlan.id)
            .where(MemoImage.id.in_(fromMemoImageIds), FarmPlan.device_id == device)
            .options(undefer(MemoImage.data))
        )
        for mi in rows.scalars():
            if mi.data:
                session.add(
                    PostImage(
                        post_id=post.id,
                        data=mi.data,
                        original_name=mi.original_name,
                        content_type=mi.content_type,
                        size_bytes=mi.size_bytes,
                    )
                )

    await session.commit()
    return await _build_detail(session, post.id, device)


@router.get("/posts/{post_id}", response_model=PostDetailOut)
async def get_post(post_id: int, session: SessionDep, device: DeviceDep) -> PostDetailOut:
    await wallet.settle_expired(session)  # 마감 지난 경매 정산(lazy)
    return await _build_detail(session, post_id, device)


@router.get("/wallet", response_model=WalletOut)
async def get_wallet(session: SessionDep, device: DeviceDep) -> WalletOut:
    """내 포인트 지갑 — 잔액(활동점수+경매정산) / 사용가능(에스크로 제외)."""
    await wallet.settle_expired(session)
    return WalletOut(
        balance=await wallet.balance(session, device),
        available=await wallet.available(session, device),
    )


@router.post("/posts/{post_id}/bids", response_model=AuctionOut)
async def place_bid(
    post_id: int, payload: BidIn, session: SessionDep, device: DeviceDep
) -> AuctionOut:
    """나눔 경매 입찰 — 로그인 필수, 현재 최고가 초과, 사용가능 포인트 이하."""
    await wallet.settle_expired(session)
    post = await session.scalar(
        select(CommunityPost)
        .where(CommunityPost.id == post_id)
        .options(selectinload(CommunityPost.bids))
    )
    if post is None:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")
    if post.post_type != "share" or post.auction_deadline is None:
        raise HTTPException(status_code=422, detail="경매 글이 아니에요.")
    if not device.startswith("user:"):
        raise HTTPException(status_code=401, detail="로그인 후 입찰할 수 있어요.")
    if post.auction_settled or _passed(post.auction_deadline):
        raise HTTPException(status_code=409, detail="마감된 경매예요.")
    if post.device_id == device:
        raise HTTPException(status_code=409, detail="본인 나눔글에는 입찰할 수 없어요.")

    amount = payload.amount
    if amount < 1:
        raise HTTPException(status_code=422, detail="1포인트 이상 입찰해주세요.")
    top = max((b.amount for b in post.bids), default=0)
    if amount <= top:
        raise HTTPException(
            status_code=409, detail=f"현재 최고가({top}P)보다 높게 입찰해주세요."
        )
    avail = await wallet.available(session, device, exclude_post=post_id)
    if amount > avail:
        raise HTTPException(
            status_code=409, detail=f"사용 가능 포인트({avail}P)를 초과했어요."
        )

    session.add(
        ShareBid(
            post_id=post_id,
            bidder_device=device,
            bidder_name=(payload.bidderName or "").strip()[:40] or "텃밭러",
            amount=amount,
        )
    )
    await session.commit()
    post = await session.scalar(
        select(CommunityPost)
        .where(CommunityPost.id == post_id)
        .options(selectinload(CommunityPost.bids))
        .execution_options(populate_existing=True)  # 방금 추가한 입찰까지 반영
    )
    result = await _auction_detail(session, post, device)
    assert result is not None
    return result


@router.delete("/posts/{post_id}", status_code=204)
async def delete_post(post_id: int, session: SessionDep, device: DeviceDep) -> Response:
    post = await session.get(CommunityPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")
    if post.device_id != device:
        raise HTTPException(status_code=403, detail="본인 글만 삭제할 수 있습니다.")
    await session.delete(post)
    await session.commit()
    return Response(status_code=204)


@router.get("/images/{image_id}")
async def get_post_image(image_id: int, session: SessionDep) -> Response:
    """게시글 사진 바이트 서빙(bytea). <img> 요청이라 device 검사 없음, 장기 캐시."""
    img = await session.scalar(
        select(PostImage).where(PostImage.id == image_id).options(undefer(PostImage.data))
    )
    if img is None or img.data is None:
        raise HTTPException(status_code=404, detail="해당 사진을 찾을 수 없습니다.")
    return Response(
        content=img.data,
        media_type=img.content_type or "image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post("/posts/{post_id}/like", response_model=LikeToggleOut)
async def toggle_like(post_id: int, session: SessionDep, device: DeviceDep) -> LikeToggleOut:
    post = await session.get(CommunityPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")
    existing = await session.scalar(
        select(PostLike).where(PostLike.post_id == post_id, PostLike.device_id == device)
    )
    if existing:
        await session.delete(existing)
        liked = False
    else:
        session.add(PostLike(post_id=post_id, device_id=device))
        liked = True
    await session.commit()
    count = await session.scalar(
        select(func.count()).select_from(PostLike).where(PostLike.post_id == post_id)
    )
    return LikeToggleOut(liked=liked, likeCount=count or 0)


@router.post("/posts/{post_id}/comments", response_model=CommentOut)
async def add_comment(
    post_id: int, payload: CommentIn, session: SessionDep, device: DeviceDep
) -> CommentOut:
    post = await session.get(CommunityPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")
    body = payload.content.strip()
    if not body:
        raise HTTPException(status_code=422, detail="댓글 내용을 입력해 주세요.")
    c = PostComment(
        post_id=post_id,
        device_id=device,
        author_name=(payload.authorName or "").strip()[:40] or "텃밭러",
        content=body,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return _comment_out(c, device)


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment(
    comment_id: int, session: SessionDep, device: DeviceDep
) -> Response:
    c = await session.get(PostComment, comment_id)
    if c is None:
        raise HTTPException(status_code=404, detail="댓글을 찾을 수 없습니다.")
    if c.device_id != device:
        raise HTTPException(status_code=403, detail="본인 댓글만 삭제할 수 있습니다.")
    await session.delete(c)
    await session.commit()
    return Response(status_code=204)


@router.post("/posts/{post_id}/share-requests", response_model=ShareRequestOut)
async def request_share(
    post_id: int, payload: ShareRequestIn, session: SessionDep, device: DeviceDep
) -> ShareRequestOut:
    post = await session.get(CommunityPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")
    if post.post_type != "share":
        raise HTTPException(status_code=400, detail="나눔 글에만 신청할 수 있습니다.")
    msg = payload.message.strip()
    if not msg:
        raise HTTPException(status_code=422, detail="신청 메시지를 입력해 주세요.")
    sr = PostShareRequest(
        post_id=post_id,
        requester_device_id=device,
        requester_name=(payload.requesterName or "").strip()[:40] or "텃밭러",
        message=msg,
    )
    session.add(sr)
    await session.commit()
    await session.refresh(sr)
    return _share_out(sr, device)


@router.patch("/share-requests/{request_id}", response_model=ShareRequestOut)
async def update_share_request(
    request_id: int,
    payload: ShareRequestPatchIn,
    session: SessionDep,
    device: DeviceDep,
) -> ShareRequestOut:
    sr = await session.scalar(
        select(PostShareRequest)
        .where(PostShareRequest.id == request_id)
        .options(selectinload(PostShareRequest.post))
    )
    if sr is None:
        raise HTTPException(status_code=404, detail="나눔 신청을 찾을 수 없습니다.")
    if sr.post.device_id != device:
        raise HTTPException(status_code=403, detail="작성자만 신청을 처리할 수 있습니다.")
    sr.status = payload.status
    await session.commit()
    await session.refresh(sr)
    return _share_out(sr, device)
