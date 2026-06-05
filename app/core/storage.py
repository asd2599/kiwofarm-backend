"""사용자 업로드 파일 로컬 저장소.

메모 사진 등 업로드 파일을 ``settings.upload_dir`` 아래에 저장하고, 정적 서빙용
URL(``/uploads/...``)을 만든다. DB에는 업로드 루트 기준 상대경로만 보관하므로,
나중에 S3 등으로 옮길 때 이 모듈만 교체하면 된다.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings

# main.py 의 StaticFiles 마운트 경로와 반드시 일치해야 한다.
UPLOAD_URL_PREFIX = "/uploads"

# content-type → 확장자. 여기에 없는 형식은 업로드를 거부한다.
_ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
}


def _upload_root() -> Path:
    root = Path(settings.upload_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


async def read_image(file: UploadFile) -> bytes:
    """이미지 업로드를 검증(형식·크기)하고 바이트를 반환 — DB(bytea) 저장용.

    형식·크기 검증에 실패하면 HTTPException 을 던진다.
    """
    ctype = (file.content_type or "").lower()
    if ctype not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"지원하지 않는 이미지 형식입니다: {file.content_type or 'unknown'}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413, detail=f"파일이 너무 큽니다(최대 {settings.max_upload_mb}MB)."
        )
    return data


async def save_image(file: UploadFile, subdir: str = "memo") -> tuple[str, int]:
    """이미지 업로드를 디스크에 저장하고 ``(상대경로, 바이트수)`` 를 반환.

    상대경로는 업로드 루트 기준이며 그대로 DB에 저장한다(수확 사진 등).
    형식·크기 검증에 실패하면 HTTPException 을 던진다.
    """
    data = await read_image(file)
    ext = _ALLOWED_CONTENT_TYPES[(file.content_type or "").lower()]
    rel = f"{subdir}/{uuid.uuid4().hex}{ext}"
    dest = _upload_root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel, len(data)


def delete_file(rel_path: str) -> None:
    """업로드 루트 기준 상대경로 파일을 삭제(없으면 무시). 루트 밖 경로는 무시."""
    if not rel_path:
        return
    root = _upload_root()
    target = (root / rel_path).resolve()
    if root != target and root not in target.parents:
        return  # 경로 탈출 방지
    target.unlink(missing_ok=True)


def file_url(rel_path: str) -> str:
    """저장 상대경로 → 정적 서빙 URL(``/uploads/...``)."""
    return f"{UPLOAD_URL_PREFIX}/{rel_path}"
