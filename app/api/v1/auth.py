"""인증 API — 아이디+비밀번호 가입/로그인 (베타: 제약 최소화)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_password, issue_token, verify_password
from app.db.models.user import AppUser
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1)


class AuthOut(BaseModel):
    token: str
    username: str


@router.post("/signup", response_model=AuthOut)
async def signup(payload: Credentials, session: SessionDep) -> AuthOut:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="아이디를 입력해 주세요.")
    exists = await session.scalar(
        select(AppUser).where(AppUser.username == username)
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.")
    user = AppUser(username=username, password_hash=hash_password(payload.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return AuthOut(token=issue_token(user.id, user.username), username=user.username)


@router.post("/login", response_model=AuthOut)
async def login(payload: Credentials, session: SessionDep) -> AuthOut:
    user = await session.scalar(
        select(AppUser).where(AppUser.username == payload.username.strip())
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    return AuthOut(token=issue_token(user.id, user.username), username=user.username)
