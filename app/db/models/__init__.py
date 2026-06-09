"""ORM 모델 패키지.

여기서 모든 모델을 import 해 Base.metadata 에 등록한다. alembic env.py 가
`from app.db import models` 로 이 패키지를 불러와 autogenerate 대상 메타데이터를
완성한다.
"""

from __future__ import annotations

from app.db.models.community import (
    CommunityPost,
    PostComment,
    PostImage,
    PostLike,
    PostShareRequest,
)
from app.db.models.crop_master import (
    CropEbook,
    CropEbookIndex,
    CropMainCategory,
    CropMiddleCategory,
    CropSubCategory,
)
from app.db.models.farm_plan import FarmPlan, FarmTask, MemoImage, TaskMemo
from app.db.models.harvest import HarvestRecord
from app.db.models.user import AppUser

__all__ = [
    "AppUser",
    "CropMainCategory",
    "CropMiddleCategory",
    "CropSubCategory",
    "CropEbook",
    "CropEbookIndex",
    "FarmPlan",
    "FarmTask",
    "TaskMemo",
    "MemoImage",
    "HarvestRecord",
    "CommunityPost",
    "PostImage",
    "PostComment",
    "PostLike",
    "PostShareRequest",
]
