"""pytest 부트스트랩 — main 임포트 전에 필수 환경변수를 채운다.

main.py 는 임포트 시점에 DATABASE_URL·JWT_SECRET 을 요구한다(_require_env).
단 DB 연결(풀)은 lifespan(앱 기동)에서만 일어나므로, 임포트만 하는 단위 테스트는
실제 DB 없이도 동작한다 → 더미 DATABASE_URL 로 충분.
"""
import os

os.environ.setdefault("JWT_SECRET", "test-secret-ci-only-not-real")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/testdb")
