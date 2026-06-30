import os

worker_class = "uvicorn.workers.UvicornWorker"   # FastAPI(ASGI)를 gunicorn 워커로
# 워커 수: 기본 3.
#  이 박스는 12코어지만 워커마다 asyncpg 풀(POOL_SIZE=10)을 따로 연다.
#  총 DB 커넥션 = workers × POOL_SIZE. 12×10=120 > Postgres max_connections(100) → 터짐.
#  그래서 3 (3×10=30, 안전). 올리려면 WEB_CONCURRENCY 환경변수로, 단 DB 커넥션 계산 필수.
workers = int(os.environ.get("WEB_CONCURRENCY", "3"))
bind = "127.0.0.1:8000"          # 기존 nginx 설정 그대로 재사용 (루프백만)

graceful_timeout = 30            # reload/stop 시 처리중 요청 기다리는 한계(초)
timeout = 60                     # 워커가 이 시간 무응답이면 강제 재시작
keepalive = 5
max_requests = 1000              # 워커가 N요청 처리 후 스스로 재생성 → 메모리 누수 방어
max_requests_jitter = 100        # 재생성 시점 랜덤 분산(동시에 다 죽는 것 방지)
# preload_app 은 기본 False 유지! True면 HUP reload로 새 코드가 반영 안 됨(§11-3 함정).
