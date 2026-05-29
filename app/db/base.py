"""SQLAlchemy 2.0 DeclarativeBase 및 모델 메타데이터.

alembic env.py의 target_metadata로 사용. 새 ORM 모델은 이 Base를 상속하고
app.db.models 패키지의 __init__.py에서 import해야 autogenerate가 인식한다.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
