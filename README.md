# server-network-lab

서버/네트워크 학습용 토이앱. 전체 계획은 [ROADMAP.md](ROADMAP.md).
이 README는 **Phase 0~3 실행 가이드** (스캐폴딩 완료분).

```
backend/    FastAPI 메모 API (SQLite)
frontend/   순수 HTML/JS 1페이지
nginx/      리버스 프록시 설정
docker-compose.yml   Phase 3 — 네트워크 토폴로지
```

## 사전 준비
- Python 3.12+ (Phase 0~2)
- Docker Desktop (Phase 3)

---

## Phase 0 — 백엔드 직결
백엔드만 띄우고 직접 호출해 HTTP·포트를 본다.

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate   # (mac/linux: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
확인:
```bash
curl -v http://localhost:8000/health
curl -X POST http://localhost:8000/notes -H "Content-Type: application/json" -d "{\"text\":\"hello\"}"
curl http://localhost:8000/notes
```
👀 `curl -v`로 요청라인·상태코드·헤더를 본다. 포트를 8001로 바꿔보면 "포트=프로세스 주소"가 와닿는다.
📖 대응: `docs/ONBOARDING.md` §2 — 백엔드가 8000에서 uvicorn으로 뜨는 것과 동일.

---

## Phase 1 — CORS를 직접 깨뜨려 본다
프론트를 다른 포트(:5173)로 띄워 백엔드(:8000)를 **직접** 호출 → 브라우저가 막는 걸 본다.

1. `frontend/app.js`의 `API_BASE`를 `"http://localhost:8000"`으로 바꾼다.
2. 프론트를 간단 정적 서버로 띄운다:
   ```bash
   cd frontend
   python -m http.server 5173
   ```
3. 브라우저로 `http://localhost:5173` 접속 → **콘솔에 CORS 차단 메시지** 확인.
4. 이번엔 백엔드를 CORS 허용으로 재기동:
   ```bash
   cd backend
   ALLOW_CORS=1 uvicorn main:app --reload --port 8000   # (windows ps: $env:ALLOW_CORS=1; uvicorn ...)
   ```
   → 같은 화면이 이제 동작. 응답 헤더 `Access-Control-Allow-Origin` 등장 확인.

👀 "다른 출처면 막힌다 / 서버가 허용하면 통과"를 몸으로.
📖 대응: Atomytics가 절대 URL 대신 `/api` 상대경로만 쓰는 이유 → Phase 2에서 근본 해결.

---

## Phase 2 — 리버스 프록시로 한 출처 만들기 (로컬 Nginx 없이 미리보기)
`app.js`의 `API_BASE`를 다시 `"/api"`로 되돌린다. (실제 프록시는 Phase 3에서 Docker로 띄움)
> 로컬에 Nginx가 있으면 `nginx/nginx.conf`로 직접 실행해도 되지만, 다음 Phase의 Docker가 더 깔끔하다.

---

## Phase 3 — Docker Compose: 포트 비노출 체험 ⭐
> ℹ️ Phase 5에서 Postgres(db)가, Phase 7에서 `.env`가 추가됐다. 지금 스택은 `db`까지 포함하며 **`.env`가 있어야 뜬다.** 최초 1회만:
> ```bash
> cp .env.example .env       # (Windows PowerShell: copy .env.example .env)
> ```
```bash
# 루트(server-network-lab)에서
docker compose up --build
```
- 접속: `http://localhost:8080` (nginx → 프론트 + `/api` 프록시). 메모 추가/삭제 동작 확인.
- **핵심 실험 — 백엔드는 외부에서 안 보인다:**
  ```bash
  curl http://localhost:8000/health      # ❌ 연결 거부 (backend는 호스트에 노출 안 됨)
  docker compose exec nginx wget -qO- http://backend:8000/health   # ✅ 내부망에선 됨
  ```
- 노출 포트 비교:
  ```bash
  docker compose ps      # nginx만 0.0.0.0:8080->80, backend는 노출 포트 없음
  ```

👀 `ports:`(호스트 노출) vs `expose:`/내부망의 차이 = "백엔드 8000 비노출"의 실체.
📖 대응: `docs/ONBOARDING.md` §2·§4 — 백엔드 8000 외부 비노출(프록시), 사설망 구조의 축소판.

정리:
```bash
docker compose down          # 컨테이너 정리 (-v 붙이면 메모 데이터 볼륨도 삭제)
```

---

## Phase 4 — JWT 인증
스택을 띄운 상태(`docker compose up`)에서 `http://localhost:8080` 접속 → **로그인 화면**이 먼저 뜬다.
- 계정: `admin` / `secret` → 로그인하면 메모 화면으로 전환, 추가/삭제 동작.

curl로 인증 흐름을 직접 본다:
```bash
B=http://localhost:8080/api
curl -i $B/notes                                   # ❌ 401 (토큰 없음)
TOKEN=$(curl -s -X POST $B/login -H "Content-Type: application/json" \
        -d '{"username":"admin","password":"secret"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -i $B/notes -H "Authorization: Bearer $TOKEN"  # ✅ 200
curl -i $B/notes -H "Authorization: Bearer ${TOKEN}X"  # ❌ 401 (서명 변조 탄로)
```
👀 토큰은 `헤더.페이로드.서명`. 페이로드는 누구나 디코드 가능(비밀 아님), 1글자만 바꿔도 서명 불일치로 401.
📖 대응: `fetchWithAuth`가 Bearer 자동 첨부, 백엔드 `get_current_user`가 검증.

---

## Phase 5 — 커넥션 풀 + Postgres (비동기)
SQLite → Postgres로 바뀌었고, 백엔드는 `asyncpg` **커넥션 풀**로 DB를 쓴다. 풀 크기를 바꿔 부하를 걸어본다.

```bash
# 풀=1 로 기동 (한 번에 커넥션 1개)
POOL_SIZE=1 docker compose up -d
curl -s http://localhost:8080/api/health        # {"pool_max":1, ...} 확인
python scripts/loadtest.py 30 100                # 동시 30요청 → 직렬화되어 ~3s

# 풀=10 으로 바꿔 재기동 (환경변수만 바뀌면 backend 컨테이너만 재생성됨)
POOL_SIZE=10 docker compose up -d
curl -s http://localhost:8080/api/health        # {"pool_max":10, ...}
python scripts/loadtest.py 30 100                # ~0.4s — 약 7배 빠름
```
👀 코드는 그대로, **풀 크기 숫자 하나**로 동시처리량이 갈린다. 풀이 비면 "대기"가 지연으로 나타남.
📖 대응: 동기 드라이버를 풀로 재사용 + 비동기로 대기를 블로킹하지 않는 패턴.

> DB에 직접 들어가 확인: `docker compose exec db psql -U lab -d lab -c "SELECT * FROM notes;"`

---

## Phase 6 — SSE 실시간 진행률 + 프록시 버퍼링
로그인 후 화면 아래 **"작업 시작"** 버튼 → 진행률 바가 0.3초마다 차오른다(서버가 SSE로 진행률을 흘림).

```bash
# 터미널에서 스트림을 직접 보기 (각 이벤트가 실시간으로 도착)
TOKEN=$(curl -s -X POST http://localhost:8080/api/login -H "Content-Type: application/json" \
        -d '{"username":"admin","password":"secret"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -N "http://localhost:8080/api/stream?token=$TOKEN"   # data: {progress} 가 0→100 흘러나옴
```
👀 `EventSource`는 헤더를 못 붙여 토큰을 **쿼리파라미터**로 전달. 스트리밍이 버퍼링에 갇히지 않게 nginx `proxy_buffering off` + 백엔드 `X-Accel-Buffering: no` 2중 신호.
📖 대응: EventSource가 프록시 우회/직결 + 짧은 수명 stream 토큰. 동일 함정·동일 해법.

---

## Phase 7 — 설정/시크릿 분리
비밀(DB 비번·JWT 키)은 코드가 아니라 `.env`에 있다. `.env`는 커밋 금지(`.gitignore`), 템플릿 `.env.example`만 커밋.

```bash
cp .env.example .env          # 협업자는 이렇게 시작, 값은 각자 채움
# compose 가 .env 를 자동으로 읽어 ${VAR} 를 채운다:
docker compose config | grep -E "DATABASE_URL|JWT_SECRET"   # .env 값으로 치환됨 확인
# OS 환경변수가 .env 보다 우선:
JWT_SECRET=OVERRIDE docker compose config | grep JWT_SECRET # OVERRIDE 가 이김
```
👀 우선순위 **OS env > .env > 코드 기본값**. 보안 핵심값(`JWT_SECRET`)은 없으면 앱이 *fail-loud*로 죽는다(알려진 기본값으로 조용히 넘어가지 않음).
📖 대응: `.env` 배포 제외·서버 보존, OS env 우선, 사설망/점프호스트로 DB 격리. (VPS+Caddy+systemd 무중단 배포는 심화 — 실서버 필요)

> ⚠️ Postgres 비번은 데이터 볼륨 **최초 생성 시 1회**만 적용된다. `.env`에서 비번을 바꾸면 `docker compose down -v`로 볼륨을 비우고 재초기화해야 반영(데이터 삭제됨).

---

## 정리
```bash
docker compose down       # 컨테이너 정리 (데이터 볼륨은 보존)
docker compose down -v    # 데이터(Postgres 볼륨)까지 삭제
```

전체 설계 의도와 단계별 "왜"는 [ROADMAP.md](ROADMAP.md) 참고.
