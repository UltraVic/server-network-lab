"""
서버/네트워크 학습용 토이 백엔드 — 개인 메모(notes) API.

학습 포인트:
- Phase 0: 이 앱을 :8000에 띄워 직접 호출 (브라우저/curl)
- Phase 1: 프론트(:5173)에서 직접 부르면 CORS가 어떻게 걸리는지
           → 환경변수 ALLOW_CORS=1 로 켜고 끄며 차이를 본다
- Phase 2~3: Nginx 프록시 뒤에 두면 같은 출처가 되어 CORS 자체가 사라짐
- Phase 4: JWT 인증 (login → Bearer 검증)
- Phase 5: SQLite → Postgres + 비동기 커넥션 풀(asyncpg).
           커넥션은 비싸다 → 풀로 재사용. 풀이 비면 빌릴 때까지 '대기'한다.
           POOL_SIZE 를 1 vs 10 으로 바꿔 부하를 걸면 그 대기를 체감할 수 있다.

도메인(메모)은 중요치 않다. 네트워크/DB 배관을 보기 위한 그릇일 뿐.
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt  # PyJWT
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── Phase 7: 설정/시크릿은 환경에서 읽는다 (코드에 비밀 기본값을 두지 않음) ──
def _require_env(key: str) -> str:
    """보안 필수 설정을 읽는다. 없으면 '조용한 안전한 실패' 대신 시끄럽게 죽인다.

    JWT_SECRET 같은 값에 기본값을 두면, env 주입 실패 시 알려진 비밀키로
    토큰을 서명하게 되어 위조에 뚫린다 → 그래서 일부러 기본값을 안 둔다.
    """
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"필수 환경변수 {key} 가 설정되지 않았습니다. .env 를 확인하세요 "
            f"(.env.example 참고)."
        )
    return val


# ── Phase 5: Postgres 연결 설정 (compose 내부망에선 호스트가 'db') ──
DATABASE_URL = _require_env("DATABASE_URL")
# POOL_SIZE = 풀에 유지할 커넥션 수(비밀 아님 → 기본값 OK). 1 vs 10 으로 부하 실험.
POOL_SIZE = int(os.getenv("POOL_SIZE", "10"))

# ── Phase 4: JWT 설정 (비밀키는 반드시 환경에서) ──
JWT_SECRET = _require_env("JWT_SECRET")
JWT_ALG = "HS256"
JWT_EXPIRE_MIN = int(os.getenv("JWT_EXPIRE_MIN", "30"))

# 토이 사용자 저장소 (실무라면 DB + 해시. 여기선 인증 배관이 학습 대상).
USERS = {"admin": "secret"}

# 배포 버전 (재배포 실습용: 이 값을 바꿔 배포하면 /health 로 확인 가능)
APP_VERSION = "1.2"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 수명주기: 시작 시 풀 생성+테이블 보장, 종료 시 풀 정리.

    sqlite처럼 매 요청 connect/close 하지 않는다. 풀을 '한 번' 만들어 두고
    모든 요청이 그 안의 커넥션을 빌려 쓴다(재사용) — 이게 Phase 5의 핵심.
    """
    app.state.pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=POOL_SIZE, max_size=POOL_SIZE
    )
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS notes ("
            "  id   SERIAL PRIMARY KEY,"
            "  text TEXT NOT NULL"
            ")"
        )
    yield
    await app.state.pool.close()


app = FastAPI(title="server-network-lab notes API", lifespan=lifespan)

# ── CORS: Phase 1 학습용 토글 (프록시 뒤에선 불필요) ─────────
if os.getenv("ALLOW_CORS") == "1":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )


class NoteIn(BaseModel):
    text: str


class Note(BaseModel):
    id: int
    text: str


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


@app.get("/health")
async def health() -> dict:
    """헬스 체크 + 풀 상태 노출(학습용): 최대 크기와 지금 노는 커넥션 수."""
    pool = app.state.pool
    return {
        "status": "ok",
        "version": APP_VERSION,              # 재배포 실습: 배포된 버전 확인용
        "pool_max": pool.get_max_size(),     # 설정된 풀 크기 (= POOL_SIZE)
        "pool_idle": pool.get_idle_size(),   # 지금 비어 있는(빌릴 수 있는) 커넥션 수
    }


# ── Phase 5: 풀 효과 체감용 '느린' 엔드포인트 ───────────────
# DB에서 일부러 ms 만큼 잠들며 커넥션을 붙잡는다.
# 풀=1 이면 동시에 1개만 처리 → 나머지는 줄 서서 대기. 풀=10 이면 10개씩.
# (인증 없이 둔다 — 부하 실험을 단순하게 하려고. 실제 서비스 라우트 아님)
@app.get("/work")
async def work(ms: int = 100) -> dict:
    async with app.state.pool.acquire() as conn:
        await conn.fetchval("SELECT pg_sleep($1::float)", ms / 1000)
    return {"slept_ms": ms}


# ── Phase 4: 인증 — 토큰 발급 ───────────────────────────────
@app.post("/login", response_model=TokenOut)
async def login(body: LoginIn) -> TokenOut:
    if USERS.get(body.username) != body.password:
        raise HTTPException(status_code=401, detail="invalid credentials")
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": body.username,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MIN),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
    return TokenOut(access_token=token)


# ── Phase 4: 인가 — 매 요청 토큰 검증 ───────────────────────
def _decode_user(token: str) -> str:
    """토큰 문자열 하나를 검증하고 사용자명(sub)을 돌려준다. 실패하면 401.

    헤더(get_current_user)와 SSE 쿼리파라미터(/stream) 양쪽에서 재사용한다.
    """
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token")
    return payload["sub"]


def get_current_user(authorization: str = Header(default="")) -> str:
    """일반 라우트용: Authorization: Bearer <token> 헤더에서 토큰을 꺼낸다."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _decode_user(token)


# ── Phase 6: SSE 진행률 스트리밍 ────────────────────────────
# EventSource(브라우저 SSE)는 커스텀 헤더를 못 붙인다 → 토큰을 쿼리파라미터로 받는다.
#   (실무에선 짧은 수명의 '전용 stream 토큰'을 쓴다. 여기선 학습용으로 JWT 재사용.)
@app.get("/stream")
async def stream(token: str = ""):
    user = _decode_user(token)  # 쿼리파라미터 토큰을 헤더와 동일하게 검증

    async def event_gen():
        # 진행률 0→100 을 0.3초 간격으로 흘려보낸다.
        # 버퍼링이 켜져 있으면 이게 실시간으로 안 흐르고 끝에 몰려 도착한다.
        for pct in range(0, 101, 10):
            yield f"data: {json.dumps({'user': user, 'progress': pct})}\n\n"
            await asyncio.sleep(0.3)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 앱 측 해법: nginx/프록시에게 "이 응답은 버퍼링하지 말고 즉시 흘려라".
            # 프록시 설정에 의존하지 않고 응답 스스로 의도를 신호한다.
            "X-Accel-Buffering": "no",
        },
    )


# ── 메모 CRUD: 이제 풀에서 커넥션을 빌려 비동기로 질의 ───────
@app.get("/notes", response_model=list[Note])
async def list_notes(user: str = Depends(get_current_user)) -> list[Note]:
    rows = await app.state.pool.fetch("SELECT id, text FROM notes ORDER BY id DESC")
    return [Note(id=r["id"], text=r["text"]) for r in rows]


@app.post("/notes", response_model=Note, status_code=201)
async def create_note(note: NoteIn, user: str = Depends(get_current_user)) -> Note:
    text = note.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    # RETURNING id: Postgres에서 INSERT 직후 새 id를 한 번에 돌려받는다.
    new_id = await app.state.pool.fetchval(
        "INSERT INTO notes (text) VALUES ($1) RETURNING id", text
    )
    return Note(id=new_id, text=text)


@app.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: int, user: str = Depends(get_current_user)) -> None:
    # execute 는 "DELETE N" 같은 상태 문자열을 돌려준다 → 끝 숫자로 삭제 행수 확인.
    status = await app.state.pool.execute("DELETE FROM notes WHERE id = $1", note_id)
    if status.split()[-1] == "0":
        raise HTTPException(status_code=404, detail="not found")
