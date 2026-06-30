"""notes 에 created_at 컬럼 추가 (추가형/expand 마이그레이션)

기존 행에는 server_default(now())로 채워지고, NOT NULL 보장.
추가형이라 구코드(컬럼 모름)와도 호환 → flip 전에 적용해도 안전.

Revision ID: 0002_created_at
Revises: 0001_baseline
Create Date: 2026-06-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_created_at"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("notes", "created_at")
