"""Alembic 환경 — async(asyncpg) 구성.

앱과 같은 asyncpg 드라이버를 재사용한다(별도 sync 드라이버 불필요).
DB 접속 문자열은 .env 의 DATABASE_URL 에서 읽어 SQLAlchemy async URL 로 변환.
ORM 모델은 없다(target_metadata=None) — 마이그레이션은 op.execute/op.* 로 직접 기술.
"""
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config

# .env 의 DATABASE_URL → SQLAlchemy async URL (postgresql+asyncpg://)
db_url = os.environ["DATABASE_URL"]
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # ORM 모델 없이 수동 마이그레이션


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
