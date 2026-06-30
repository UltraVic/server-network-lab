"""baseline — notes 테이블 (기존 운영 DB 안전 채택)

이미 notes 테이블이 있는 DB에도 안전하게 Alembic을 도입하기 위한 baseline.
CREATE TABLE IF NOT EXISTS 라서: 기존 DB면 no-op(버전만 기록), 새 DB면 생성.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS notes ("
        " id SERIAL PRIMARY KEY,"
        " text TEXT NOT NULL"
        ")"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notes")
