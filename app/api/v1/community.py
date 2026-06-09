"""커뮤니티 게시판 API — 수확물 자랑·나눔 피드.

농사계획·수확과 달리 **공용 피드**다(목록은 device 필터 없이 모두의 글). 작성자는
글마다 닉네임(author_name) 으로 식별한다. 사진은 bytea(post_image.data)에 저장하고
<img> 서빙은 무인증(헤더를 못 보냄) — farmplan memo-images 패턴과 동일.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from app.api.deps import DeviceDep
from app.core.storage import read_image
from app.db.models.community import (
    CommunityPost,
    PostComment,
    PostImage,
    PostLike,
    PostShareRequest,
)
from app.db.models.farm_plan import FarmPlan, MemoImage, TaskMemo
from app.db.session import get_session
from app.schemas.community import (
    CommentIn,
    CommentOut,
    LikeToggleOut,
    PostDetailOut,
    PostImageOut,
    PostListItemOut,
    ShareRequestIn,
    ShareRequestOut,
    ShareRequestPatchIn,
)

router = APIRouter(prefix="/community", tags=["community"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

PREVIEW_LEN = 140


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
        )
    )
    if post is None:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")
    is_mine = post.device_id == device
    # 나눔 신청: 작성자에겐 전체, 그 외엔 본인 신청만 노출.
    reqs = (
        post.share_requests
        if is_mine
        else [s for s in post.share_requests if s.requester_device_id == device]
    )
    return PostDetailOut(
        id=post.id,
        postType=post.post_type,  # type: ignore[arg-type]
        authorName=post.author_name,
        cropSlug=post.crop_slug,
        cropName=post.crop_name,
        title=post.title,
        content=post.content,
        images=[_img_out(i) for i in post.images],
        likeCount=len(post.likes),
        likedByMe=any(lk.device_id == device for lk in post.likes),
        isMine=is_mine,
        shareStatus=post.share_status,  # type: ignore[arg-type]
        comments=[_comment_out(c, device) for c in post.comments],
        shareRequests=[_share_out(s, device) for s in reqs],
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
    q = (
        select(CommunityPost)
        .options(selectinload(CommunityPost.images))
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
            contentPreview=p.content[:PREVIEW_LEN],
            images=[_img_out(i) for i in p.images],
            likeCount=like_counts.get(p.id, 0),
            commentCount=comment_counts.get(p.id, 0),
            shareRequestCount=share_counts.get(p.id, 0),
            likedByMe=p.id in liked,
            isMine=p.device_id == device,
            shareStatus=p.share_status,  # type: ignore[arg-type]
            createdAt=p.created_at,
        )
        for p in posts
    ]


@router.post("/posts", response_model=PostDetailOut)
async def create_post(
    session: SessionDep,
    device: DeviceDep,
    content: Annotated[str, Form()],
    authorName: Annotated[str, Form()],
    postType: Annotated[str, Form()] = "show",
    title: Annotated[str | None, Form()] = None,
    cropSlug: Annotated[str | None, Form()] = None,
    cropName: Annotated[str | None, Form()] = None,
    harvestRecordId: Annotated[int | None, Form()] = None,
    fromMemoImageIds: Annotated[list[int] | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> PostDetailOut:
    """게시글 작성(multipart). 사진은 직접 업로드 + 내 메모 사진 복사 둘 다 지원."""
    body = content.strip()
    if not body:
        raise HTTPException(status_code=422, detail="내용을 입력해 주세요.")
    name = (authorName or "").strip()[:40] or "텃밭러"
    pt = postType if postType in ("show", "share") else "show"

    post = CommunityPost(
        device_id=device,
        author_name=name,
        post_type=pt,
        title=(title or "").strip()[:255] or None,
        content=body,
        crop_slug=cropSlug or None,
        crop_name=cropName or None,
        harvest_record_id=harvestRecordId,
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
    return await _build_detail(session, post_id, device)


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
