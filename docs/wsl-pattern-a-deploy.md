# WSL에 직접 배포하기 — Docker 없이 systemd + Nginx (패턴 A)

> 토이 앱(notes API)을 **WSL2 Ubuntu를 "내 VPS"처럼** 써서 배포·운영한 실습 기록.
> 서버 구성 → 접속 → 재배포 → HTTPS → 네트워크/IP → 같은 WiFi 폰 접속까지.

---

## 0. 목표 아키텍처 (패턴 A)

```
[브라우저]  ─ http(s) ─▶  [Nginx :80/:443]   ← 유일한 외부 입구
                            ├ /      → /srv/notes/frontend (정적파일 직접 서빙)
                            └ /api/  → 127.0.0.1:8000
                                       └▶ [uvicorn :8000]  (systemd: notes-api, User=app)
                                            └▶ [Postgres :5432]
   · backend·db 는 127.0.0.1 바인드 = 외부 비노출 (Nginx만 외부 공개)
   · 시크릿은 /srv/notes/.env (systemd EnvironmentFile)
   · 서비스는 enable → 재부팅 시 자동기동
```

| 항목 | 값 |
|---|---|
| OS | Ubuntu 26.04 LTS (WSL2, systemd 기본 활성) |
| 앱 경로 | `/srv/notes` (backend, frontend, .env) |
| 백엔드 서비스 | `notes-api` (uvicorn, 127.0.0.1:8000) |
| DB | Postgres, db=`lab`, 127.0.0.1:5432 |
| 웹 입구 | Nginx :80 → :443 리다이렉트 |
| 전용 유저 | `app` (root 아님) |

---

## 1. 사전 — WSL2를 "내 VPS"로

- WSL은 **내 PC 안의 리눅스**라, 진짜 서버 없이 systemd·Nginx 배포를 실습할 수 있다.
- Docker용 `docker-desktop` 배포판 말고, 일반 **Ubuntu**를 설치해 사용.

```powershell
# Windows PowerShell
wsl --install -d Ubuntu        # 설치 (첫 실행 시 OOBE에서 계정 생성)
wsl --set-default Ubuntu       # 기본 배포판을 Ubuntu로 (그냥 `wsl` = Ubuntu)
wsl -l -v                      # 배포판/상태 확인
```

> 요즘 Ubuntu WSL은 **systemd가 기본 활성**(`systemctl`이 그대로 동작). 확인: `systemctl is-system-running` → `running`.

---

## 2. 서버 구성 (배포 단계별)

> 아래는 모두 **우분투 안에서** 실행. 관리 작업은 `sudo` 필요.

### 2-1. 패키지 설치 + 전용 유저
```bash
sudo apt-get update
sudo apt-get install -y nginx python3-venv python3-pip postgresql
# root로 앱을 돌리지 않기 위해 전용 시스템 유저 생성
sudo useradd --system --create-home --shell /bin/bash app
```

### 2-2. 앱 코드 배치 + 파이썬 가상환경
```bash
sudo mkdir -p /srv/notes
# 코드 복사 (개발 PC → 서버). 실무에선 git clone / rsync.
sudo cp -r <소스>/backend /srv/notes/
sudo cp -r <소스>/frontend /srv/notes/

# 가상환경 + 의존성
sudo python3 -m venv /srv/notes/backend/.venv
sudo /srv/notes/backend/.venv/bin/pip install -r /srv/notes/backend/requirements.txt
```

### 2-3. Postgres 계정·DB
```bash
# 역할(role)과 DB 생성
sudo -u postgres psql -c "CREATE ROLE lab LOGIN PASSWORD '<db-password>';"
sudo -u postgres psql -c "CREATE DATABASE lab OWNER lab;"
# TCP 로그인 확인
PGPASSWORD='<db-password>' psql -h 127.0.0.1 -U lab -d lab -c "SELECT 1;"
```

### 2-4. 시크릿 분리 (.env)
```bash
sudo tee /srv/notes/.env >/dev/null <<'ENV'
DATABASE_URL=postgresql://lab:<db-password>@127.0.0.1:5432/lab
JWT_SECRET=<랜덤-32자-이상>
POOL_SIZE=10
ENV

# 소유권/권한: app 소유, .env 는 600 (남이 못 읽게)
sudo chown -R app:app /srv/notes
sudo chmod 600 /srv/notes/.env
```
> 🔐 `.env`는 절대 git/Notion에 올리지 말 것. 위 `<db-password>`·`JWT_SECRET`은 실제 값으로 채우고, 보관은 서버에만.

### 2-5. 백엔드를 systemd 서비스로
`/etc/systemd/system/notes-api.service`:
```ini
[Unit]
Description=Notes FastAPI backend (uvicorn)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
User=app
WorkingDirectory=/srv/notes/backend
EnvironmentFile=/srv/notes/.env
ExecStart=/srv/notes/backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now notes-api      # 등록 + 시작 + 부팅 자동기동
systemctl is-active notes-api              # active 확인
curl http://127.0.0.1:8000/health          # 백엔드 직접 헬스체크
```
> `--host 127.0.0.1` = **루프백만 바인드 → 외부 비노출** (Docker에서 `ports:` 안 준 것과 동일 효과).

### 2-6. Nginx — 정적 서빙 + /api 프록시
`/etc/nginx/sites-available/notes.conf`:
```nginx
server {
    listen 80 default_server;
    server_name _;
    root /srv/notes/frontend;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;   # SPA 새로고침 대응
    }
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;   # 끝의 / 가 /api 접두어 제거
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;                 # SSE 대비
    }
}
```
```bash
sudo ln -sf /etc/nginx/sites-available/notes.conf /etc/nginx/sites-enabled/notes.conf
sudo rm -f /etc/nginx/sites-enabled/default      # 기본 사이트 비활성 (포트 충돌 방지)
sudo nginx -t && sudo systemctl reload nginx     # 문법검사 후 반영
```

### 2-7. 동작 확인
```bash
curl http://localhost/                 # 프론트 (200)
curl http://localhost/api/health       # 프록시 → 백엔드 (JSON)
sudo ss -ltnp | grep -E ':80|:8000|:5432'
#  0.0.0.0:80  = 외부 노출(nginx) / 127.0.0.1:8000·5432 = 비노출 ✅
```

---

## 3. 접속하기

### 3-1. localhost 접속 (mirrored 네트워킹)
기본 WSL은 **NAT 모드**라 Windows `localhost`로 WSL 서비스에 안 닿는다. **mirrored 모드**로 바꾸면 WSL이 Windows 네트워크를 공유해 `localhost`로 접근 가능.

`C:\Users\<나>\.wslconfig`:
```ini
[wsl2]
networkingMode=mirrored
```
```powershell
wsl --shutdown      # 적용하려면 WSL 재시작 (서비스는 enable돼서 자동 복구됨)
```
→ 이후 브라우저에서 **http://localhost** 로 접속.

### 3-2. ⚠️ WSL은 유휴 시 잠든다
- 터미널을 안 열어두고 활동이 없으면 WSL이 **배포판을 자동으로 중지(Stopped)** → `localhost`도 멈춤.
- **해결(방법 1, 가장 간단):** `wsl` 터미널 창을 하나 열어두면(최소화 OK) 깨어 있음.
- 실제 VPS는 항상 켜져 있어 이 문제가 없다 (WSL만의 특성).

### 3-3. 기본 배포판 / 계정
- `wsl --set-default Ubuntu` 안 하면 `wsl`이 docker-desktop으로 열릴 수 있음.
- 첫 실행 시 OOBE에서 **로그인 계정**(예: dylan) 생성. 관리 작업은 `sudo`.

---

## 4. 재배포 사이클 (코드 수정 → 반영)

```bash
# 1) 변경 파일을 서버로 복사 (실무: git pull / rsync / CI)
sudo cp <소스>/backend/main.py /srv/notes/backend/main.py
sudo cp <소스>/frontend/index.html /srv/notes/frontend/index.html
sudo chown app:app /srv/notes/backend/main.py /srv/notes/frontend/index.html

# 2) 무엇을 고쳤느냐에 따라 반영
sudo systemctl restart notes-api    # 백엔드(.py) → 재기동 필요
#   프론트(정적) → 복사만 하고 브라우저 새로고침 (재기동 불필요)
#   nginx 설정 → sudo nginx -t && sudo systemctl reload nginx

# 3) 확인
curl http://localhost/api/health
```

| 무엇을 고쳤나 | 반영 방법 |
|---|---|
| 백엔드 `.py` | `systemctl restart notes-api` |
| 프론트 정적파일 | 복사 + 브라우저 새로고침 |
| nginx 설정 | `nginx -t && systemctl reload nginx` |
| `.env` | 값 수정 + `restart notes-api` |

> **핵심: "파일 수정 ≠ 배포".** 소스를 고쳐도 서버(`/srv/notes`)에 옮기고 반영해야 라이브에 적용된다.
>
> 📦 이 단순 복사+restart 방식을 **멀티워커·무중단·원자적 롤백**으로 끌어올린 실무 버전은 문서 하단 **[패턴 A 실무 확장 I — §10~13]** 참고.

---

## 5. HTTPS (자체 서명 인증서)

도메인이 없으면(localhost) **자체 서명** 인증서로 TLS 메커니즘을 실습. 동작은 하지만 브라우저가 "신뢰 안 됨" 경고를 띄운다(자체 서명이라).

```bash
# 1) 자체 서명 인증서 생성
sudo mkdir -p /etc/nginx/certs
sudo openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout /etc/nginx/certs/localhost.key \
  -out /etc/nginx/certs/localhost.crt \
  -days 365 -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```
`notes.conf` (80→443 리다이렉트 + 443 SSL):
```nginx
server {                                   # HTTP → HTTPS 리다이렉트
    listen 80 default_server;
    server_name _;
    return 301 https://$host$request_uri;
}
server {                                   # HTTPS
    listen 443 ssl default_server;
    server_name _;
    ssl_certificate     /etc/nginx/certs/localhost.crt;
    ssl_certificate_key /etc/nginx/certs/localhost.key;

    root /srv/notes/frontend;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;   # "원래 https였음"을 백엔드에 알림
        proxy_buffering off;
    }
}
```
```bash
sudo nginx -t && sudo systemctl reload nginx
curl -k https://localhost/api/health       # -k = 자체서명 무시
```

**개념: TLS termination at the edge**
- 암호화/복호화는 **Nginx(정문)에서만**, 내부(Nginx↔백엔드)는 평문 http → 백엔드는 TLS를 몰라도 됨.
- 실무: 공인 도메인 + **Let's Encrypt(certbot)** 또는 **Caddy(자동 HTTPS)** → 경고 없는 진짜 자물쇠 🔒.

---

## 6. 네트워크와 IP

### IP는 3겹
```
① 127.0.0.1 (localhost)   → 이 PC 자신만. 항상 고정.
② LAN IP (예 192.168.x)   → 같은 공유기 안 기기만. 사설(private), 수시로 바뀜.
③ 공인 IP (공유기 WAN)     → 인터넷이 보는 주소. ISP가 줌. 동적. NAT로 인바운드 차단.
```
- **사설 IP**(`10.`, `172.16~31`, `192.168`)는 **인터넷에서 안 보인다**. 같은 네트워크 내부 전용.
- WiFi를 바꾸면 LAN IP·공인 IP 모두 바뀐다. 같은 WiFi에서도 시간 지나면 바뀔 수 있다(DHCP).
- 그래서 **안정적 공개에는 고정 공인 IP(VPS) + 도메인**이 필요하다.

### IP 확인 명령
```powershell
# Windows
ipconfig                                   # LAN IP (Wi-Fi 어댑터 IPv4)
curl.exe ifconfig.me                       # 공인 IP (외부가 보는 주소)
```
```bash
# WSL 안
hostname -I                                # WSL IP (mirrored면 Windows와 공유)
```

### 도메인은 접속 필수가 아니다
- 공인 IP만 있으면 `http://1.2.3.4`처럼 **IP로 바로 접속 가능**.
- 도메인은 ① 외우기 쉬운 이름 ② **신뢰된 HTTPS 인증서**(도메인 기준 발급) ③ IP 바뀌어도 이름 유지 — 를 위한 것.

---

## 7. 같은 WiFi에서 폰으로 접속

같은 공유기에 붙은 폰에서 **PC의 LAN IP**로 접속 가능 — 단 **Windows 방화벽**이 막는다.

1. 폰을 PC와 **같은 WiFi**에 연결
2. 폰 브라우저에서 `http://<PC의 LAN IP>` (예 `http://192.168.72.196`)
3. 막히면 방화벽에서 **80/443 인바운드 허용** 필요

```powershell
# 권장: 방화벽은 켜두고 80/443만 허용 (관리자 PowerShell)
New-NetFirewallRule -DisplayName "notes-lab http/https" -Direction Inbound `
  -Protocol TCP -LocalPort 80,443 -Action Allow -Profile Public
```
> ⚠️ **방화벽 전체 OFF는 비추천** — 모든 포트가 열린다(특히 Public 프로필 네트워크에선 위험). 위처럼 **필요한 포트만** 여는 게 안전. = VPS의 `ufw allow 443`과 같은 원리("통제된 입구만 연다").
>
> 참고: 폰에서 https로 들어가면 인증서 경고가 뜬다(자체서명이 `localhost`용이라 IP와 이름 불일치). "그래도 진행"하면 동작.

---

## 8. 운영 명령어 치트시트

```bash
# 서비스 관리
sudo systemctl status notes-api
sudo systemctl restart notes-api          # 코드 변경 후
sudo systemctl reload nginx               # 설정 변경 후(무중단)
systemctl is-active notes-api nginx postgresql
sudo systemctl enable/disable notes-api   # 부팅 자동기동 on/off

# 로그
sudo journalctl -u notes-api -f           # 실시간
sudo journalctl -u notes-api -n 100 --no-pager
sudo tail -f /var/log/nginx/error.log

# DB
sudo -u postgres psql -d lab              # 접속
sudo -u postgres pg_dump lab > backup.sql # 백업

# 진단
sudo ss -ltnp | grep -E ':80|:443|:8000|:5432'
curl -sk https://localhost/api/health

# WSL 제어 (Windows PowerShell)
wsl                                       # 진입(+켜둠)
wsl --shutdown                            # 전체 종료
```

---

## 9. 핵심 교훈

- **127.0.0.1 바인드 = 외부 비노출.** 외부 진입은 Nginx 한 곳으로 모은다.
- **`systemctl enable` = 부팅 자동기동.** WSL 재시작 후에도 서비스 자동 복구 확인.
- **reload(무중단, 옛 워커가 잠깐 옛 설정으로 응답) vs restart(끊고 새로).**
- **파일 수정 ≠ 배포** — 서버로 옮기고 반영(재기동/새로고침)해야 라이브에 적용.
- **사설 IP는 인터넷에서 안 보인다.** 공개에는 공인 고정 IP(VPS)가 필요. 도메인은 이름+인증서용.
- **방화벽은 "필요한 포트만" 연다.** 통째로 끄지 않는다.
- Docker로 배운 개념(프록시·포트 비노출·시크릿·프록시 버퍼링)이 **도구만 바뀌어 systemd 세상에 그대로 적용**된다.

---
---

# 📦 패턴 A 실무 확장 I — 무중단 배포 & 프로세스 관리

> §2-5(systemd)·§4(재배포)는 "단일 uvicorn + 복사 + restart"라 학습엔 충분하지만, 실제로 **앱을 굴리며 반복 배포**하기엔 세 가지가 부족하다:
> 1. 단일 프로세스 → CPU 코어 1개만 사용, 한 요청이 오래 걸리면 전체가 막힘.
> 2. `restart` 순간 **다운타임**(연결 거부 구간)이 생김.
> 3. 잘못 배포하면 **되돌릴 길**이 없음(이전 버전이 사라짐).
>
> 이 파트에서 ①멀티워커 ②무중단 reload ③원자적 릴리스+롤백 으로 끌어올린다.

---

## 10. Gunicorn 멀티워커 + graceful reload

### 10-1. 개념 — 왜 프로세스 매니저가 필요한가
- `uvicorn main:app` 직접 실행 = **워커 1개**. 죽으면(`Restart=always`라도) 그 순간 다운, 코어도 1개만.
- 실무는 **Gunicorn(마스터)** 이 **워커 N개**(`UvicornWorker`)를 관리:
  - 마스터는 요청을 처리하지 않고 **워커를 감시·재생성**(죽으면 즉시 새로 띄움).
  - 워커가 여러 개 → 멀티코어 활용 + 한 워커가 막혀도 나머지가 응답.
  - **graceful reload**: 새 워커를 먼저 띄우고 → 기존 워커는 **처리 중인 요청을 끝낸 뒤** 종료 → 다운타임 0.

```
[Gunicorn master] ──감시/신호──┬─▶ [worker 1] ─┐
                               ├─▶ [worker 2] ─┼─▶ 각자 uvicorn 이벤트루프
                               └─▶ [worker N] ─┘   (127.0.0.1:8000 공유 리슨)
```

### 10-2. 직접 해보기 — gunicorn 도입
```bash
# 의존성 추가 (requirements.txt 에 gunicorn 추가 후)
/srv/notes/backend/.venv/bin/pip install gunicorn
```
`/srv/notes/backend/gunicorn.conf.py`:
```python
import os
import multiprocessing

worker_class = "uvicorn.workers.UvicornWorker"   # FastAPI(ASGI)를 gunicorn 워커로
# UvicornWorker는 각자 async 이벤트루프 → 워커 수 ≈ CPU 코어 수에서 시작.
#  (※ "(2*N)+1"은 동기 워커 기준 공식 — async 워커엔 과하니 혼동 말 것)
# ⚠️ DB 커넥션 함정: 워커마다 자체 커넥션 풀을 연다 → 총 커넥션 = workers × POOL_SIZE.
#    이게 Postgres max_connections(기본 100)를 넘으면 앱이 커넥션 에러로 죽는다.
#    예: 12코어에서 cpu_count()=12, POOL_SIZE=10 → 120 > 100 → 터짐.
#    그래서 실제론 코어 수보다 "max_connections ÷ POOL_SIZE" 한도가 워커 상한이 된다.
#    WEB_CONCURRENCY로 명시 지정하고, 풀 크기와 함께 계산해서 잡을 것.
workers = int(os.environ.get("WEB_CONCURRENCY", min(multiprocessing.cpu_count(), 3)))
bind = "127.0.0.1:8000"        # 기존 nginx 설정 그대로 재사용 (루프백만 바인드)

graceful_timeout = 30          # reload/stop 시 처리중 요청을 기다리는 한계(초)
timeout = 60                   # 워커가 이 시간 무응답이면 강제 재시작
keepalive = 5
max_requests = 1000            # 워커가 N요청 처리 후 스스로 재생성 → 메모리 누수 방어
max_requests_jitter = 100      # 재생성 시점을 랜덤 분산(동시에 다 죽는 것 방지)
# preload_app 은 기본 False 유지! True면 코드를 마스터가 1번만 로드 →
#   HUP reload로 새 코드가 반영 안 됨(§10-4 함정 참고).
```
`/etc/systemd/system/notes-api.service` (§2-5에서 `ExecStart`·`ExecReload`만 교체):
```ini
[Service]
User=app
Group=app
WorkingDirectory=/srv/notes/backend
EnvironmentFile=/srv/notes/.env
ExecStart=/srv/notes/backend/.venv/bin/gunicorn -c /srv/notes/backend/gunicorn.conf.py main:app
ExecReload=/bin/kill -s HUP $MAINPID      # ← reload = 마스터에 SIGHUP(graceful)
KillMode=mixed                            # stop 시 마스터에 TERM, 남으면 cgroup KILL
TimeoutStopSec=35                         # graceful_timeout(30)보다 약간 크게
Restart=always
RestartSec=3
```
```bash
sudo systemctl daemon-reload
sudo systemctl restart notes-api
```

### 10-3. 확인 — 멀티워커 & 무중단 reload
```bash
# 워커가 여러 개 떠 있나 (마스터 1 + 워커 N)
pgrep -af gunicorn

# graceful reload: 새 워커 PID로 교체되지만 서비스는 안 끊김
curl -s http://127.0.0.1:8000/health      # 200
sudo systemctl reload notes-api           # = SIGHUP, 다운타임 0
curl -s http://127.0.0.1:8000/health      # 그 순간에도 200

# (증명) 0.2초마다 때리면서 reload — 한 번도 실패하지 않아야 함
( while true; do curl -s -o /dev/null -w "%{http_code} " http://127.0.0.1:8000/health; sleep 0.2; done ) &
sudo systemctl reload notes-api ; sleep 2 ; kill %1
#  200 200 200 ... reload 구간에도 200만 (restart로 같은 실험하면 중간에 000/거부가 섞임)
```
> **reload(HUP) vs restart 재정리:** reload는 **마스터는 그대로 두고 워커만** graceful 교체 → 리슨 소켓이 안 닫혀 무중단. restart는 마스터까지 내렸다 올림 → 짧은 갭 발생. **코드/설정만 바꿨고 경로가 그대로면 항상 `reload`.**

---

## 11. 원자적 릴리스 + 즉시 롤백 (releases 심볼릭링크)

### 11-1. 개념 — "복사 덮어쓰기"의 위험
§4는 `/srv/notes/backend`에 새 파일을 **직접 덮어썼다.** 문제:
- 복사 도중(절반만 갱신된 순간) 워커가 import하면 깨질 수 있음.
- 배포 후 버그를 발견해도 **이전 버전이 이미 사라져** 되돌릴 수 없음.

해법: 버전마다 **별도 디렉터리**에 통째로 준비하고, `current` **심볼릭링크**를 한 번에 바꾼다(atomic). 롤백 = 링크를 이전으로 되돌리기.

```
/srv/notes/
├─ releases/
│   ├─ 20260630-120000/   (backend + frontend + .venv)   ← 과거
│   └─ 20260630-153000/   (backend + frontend + .venv)   ← 신규
├─ current  ─▶ releases/20260630-153000     # 심볼릭링크 (배포 = 이걸 flip)
└─ shared/
    └─ .env                                  # 릴리스 공통(시크릿) — 버전 무관 1벌
```
- systemd·nginx는 항상 **`/srv/notes/current/...`** 를 가리키게 바꾼다(아래 경로 갱신).
- `.env`처럼 버전과 무관한 것은 `shared/`에 두고 각 릴리스에서 링크 → 비밀이 릴리스마다 복제되지 않음.

### 11-2. 경로를 current 기준으로 전환
```bash
sudo mkdir -p /srv/notes/releases /srv/notes/shared
sudo mv /srv/notes/.env /srv/notes/shared/.env 2>/dev/null || true
sudo chown -R app:app /srv/notes
```
systemd 유닛(§10-2)과 nginx `root`를 `current` 경유로:
```ini
# notes-api.service
WorkingDirectory=/srv/notes/current/backend
EnvironmentFile=/srv/notes/current/.env       # → shared/.env 로 링크됨(아래 deploy.sh)
ExecStart=/srv/notes/current/backend/.venv/bin/gunicorn -c /srv/notes/current/backend/gunicorn.conf.py main:app
```
```nginx
# notes.conf — root 를 current 경유로
root /srv/notes/current/frontend;
```
> systemd·nginx는 **시작 시점에** 심볼릭링크를 실제 경로로 해소(resolve)한다. 그래서 링크를 바꾼 뒤 **반영(restart/reload)** 을 해줘야 새 릴리스를 본다 — 다음 §11-3의 함정으로 이어진다.

### 11-3. ⚠️ 함정 — "링크만 바꾸고 reload"는 새 코드가 안 먹는다
- `current` 심볼릭링크를 새 릴리스로 바꾼 뒤 `systemctl reload`(HUP) 하면 **옛 코드가 계속 돈다.**
- 이유: gunicorn **마스터**의 작업디렉터리·`sys.path`는 **시작 시점에 링크가 해소된 실제 경로로 고정**됨. HUP로 포크되는 새 워커도 마스터의 옛 실제 경로를 물려받음 → 링크 변경을 못 봄.
- **해법 3단계(상황별):**

| 방법 | 무중단 | 복잡도 | 언제 |
|---|---|---|---|
| **(a) 링크 flip 후 `systemctl restart`** | 갭 수십 ms (소켓 백로그가 대부분 흡수 → 체감 거의 무중단) | 낮음 | **학습/소규모 — 기본 권장** |
| (b) **systemd socket activation** | 진짜 0갭 (리슨 소켓을 `.socket` 유닛이 들고 있어 restart 중 연결이 큐에 쌓임) | 중간 | 무중단이 진짜 필요할 때 (systemd-native) |
| (c) gunicorn `USR2`→`WINCH`→`TERM` | 0갭 | 높음 | 마스터를 새 경로로 re-exec. 단 systemd가 MAINPID 추적을 놓쳐 궁합 나쁨 → 보통 (b) 권장 |

(b) 소켓 액티베이션 스케치 — `notes-api.socket`:
```ini
[Socket]
ListenStream=127.0.0.1:8000     # gunicorn이 이 fd를 물려받아 리슨
[Install]
WantedBy=sockets.target
```
> gunicorn은 systemd 소켓 액티베이션을 지원한다(리슨 fd 상속). 자세한 바인드 설정은 gunicorn `systemd` 문서 참고. 핵심 개념만: **소켓을 서비스 밖(.socket 유닛)이 들고 있으면, 서비스가 죽었다 살아나는 동안에도 커널이 연결을 큐에 받아둬서 "연결 거부"가 안 난다.**
>
> 결론: 이 랩에서는 **(a) restart** 로 간다(아래 deploy.sh). 0갭이 꼭 필요해지면 (b)로 올린다.

### 11-4. git 형상관리 연동 (릴리스 소스 = 커밋 SHA)

릴리스를 "복사한 폴더"가 아니라 **git 커밋**으로 묶으면 무중단 배포가 완성된다 — 무엇이 라이브인지 SHA로 확정되고, 롤백이 커밋 단위가 되며, `git log`가 곧 배포 이력이 된다.

```
[개발 PC] ──git push──▶ [GitHub origin]
                            │  (public이면 서버가 인증 없이 fetch)
                            ▼
[서버] /srv/notes/repo.git  (bare clone = 코드 원본 캐시)
        └─ 배포 시: git fetch → git archive <ref> → releases/<ts>-<sha>/
```

- 개발 PC: `git init` → `.gitignore`로 `.env`·`.venv` 제외 → GitHub로 push. `.gitattributes`에 `* text=auto eol=lf`를 둬 줄바꿈을 LF로 고정(Linux로 갈 셸이 CRLF로 깨지지 않게).
- 서버는 한 번만 `git clone --bare <url> /srv/notes/repo.git` (app 소유). 이후 배포 때 `fetch`로 최신화.
- **서버는 편집하는 곳이 아니라 배포받는 곳** — 서버에서 만든 설정(gunicorn.conf.py 등)도 repo에 커밋해 git을 진실의 소스로 유지.
- 릴리스명 = `<타임스탬프>-<짧은SHA>` → 디렉터리 이름만 봐도 어느 커밋인지 안다.

---

## 12. deploy.sh — 한 줄 배포 (fetch→archive→flip→헬스체크→자동 롤백)

위 조각들을 하나로 묶는다. **git fetch → 커밋 추출(archive) → 릴리스 venv → 심볼릭링크 원자적 교체 → 헬스체크 → 실패 시 자동 롤백 → 오래된 릴리스 정리.**

`/srv/notes/deploy.sh` (소유: `app`, `chmod +x`):
```bash
#!/usr/bin/env bash
set -euo pipefail

APP=/srv/notes
GD=$APP/repo.git
REF=${1:-main}            # 배포할 git ref (기본 main, 특정 커밋/브랜치/태그도 가능)
KEEP=5                    # 보관 릴리스 개수

echo "▶ [1/6] repo fetch"
git --git-dir="$GD" fetch -q origin '+refs/heads/*:refs/heads/*'
SHA=$(git --git-dir="$GD" rev-parse --short "$REF")
TS=$(date +%Y%m%d-%H%M%S)
REL="$APP/releases/$TS-$SHA"
echo "  → $REL"

echo "▶ [2/6] git archive 로 커밋 스냅샷 추출"
mkdir -p "$REL"
git --git-dir="$GD" archive "$REF" | tar -x -C "$REL"

echo "▶ [3/6] venv + deps (릴리스 전용 → 롤백 시 의존성도 그 버전)"
python3 -m venv "$REL/backend/.venv"
"$REL/backend/.venv/bin/pip" install -q -r "$REL/backend/requirements.txt"
# (선택) DB 마이그레이션 자리 — '패턴 A 실무 확장 II(DB 운영)'에서 Alembic 도입 시:
#   "$REL/backend/.venv/bin/alembic" -c "$REL/backend/alembic.ini" upgrade head

echo "▶ [4/6] .env 링크 + current 원자적 flip (rename(2)=atomic)"
ln -sfn "$APP/shared/.env" "$REL/.env"
PREV=$(readlink "$APP/current" 2>/dev/null || true)   # 롤백 대상 기억
ln -sfn "$REL" "$APP/current.tmp"
mv -T "$APP/current.tmp" "$APP/current"               # 절반 상태 없이 한 번에 전환

echo "▶ [5/6] restart + 헬스체크"
sudo systemctl restart notes-api          # 링크 flip은 restart로 새 경로 해소(§11-3 a)
ok=0
for _ in $(seq 1 10); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null; then ok=1; break; fi
  sleep 1
done

if [ "$ok" != 1 ]; then
  echo "❌ 헬스체크 실패 → 자동 롤백"
  if [ -n "$PREV" ] && [ "$PREV" != "$REL" ]; then
    ln -sfn "$PREV" "$APP/current.tmp"; mv -T "$APP/current.tmp" "$APP/current"
    sudo systemctl restart notes-api
    echo "↩ 이전 릴리스로 롤백: $PREV"
  fi
  exit 1
fi

echo "▶ [6/6] 오래된 릴리스 정리 (최근 $KEEP개만 유지)"
ls -1dt "$APP/releases"/*/ | tail -n +$((KEEP+1)) | xargs -r rm -rf
echo "✅ 배포 성공: $(basename "$REL")"
```
**실무 디테일 — app 유저가 sudo로 서비스만 만지게 (NOPASSWD 한정):**
```bash
# /etc/sudoers.d/notes  (visudo -cf 로 문법 검증 — 잘못되면 sudo가 잠긴다)
app ALL=(root) NOPASSWD: /usr/bin/systemctl restart notes-api, /usr/bin/systemctl reload notes-api
```
배포·확인:
```bash
sudo -u app /srv/notes/deploy.sh            # main 최신 배포
sudo -u app /srv/notes/deploy.sh <ref>      # 특정 커밋/브랜치/태그 배포
curl -s http://localhost/api/health         # version 으로 반영 확인
ls -1dt /srv/notes/releases/*/              # 릴리스 이력(최신순, 이름에 SHA)
```

> **✅ 이 랩에서 실제 검증함**
> - 버전 `1.1→1.2` git 배포가 라이브 반영(`/health`의 version으로 확인).
> - **`reload`=0갭(전부 200) vs `deploy.sh restart`=짧은 갭(`000` 몇 개)** 대비 관측 — §11-3 트레이드오프 그대로.
> - 심볼릭링크 flip만으로 `1.1↔1.2` 즉시 롤백(재빌드 없음).
> - **부팅 실패 커밋을 배포하니 헬스체크 10회 실패 → deploy.sh가 직전 릴리스로 자동 롤백**, 서비스는 정상 유지. 안전장치 작동 확인.

### 수동 롤백 (직전 릴리스로 1초 복귀)
```bash
PREV=$(ls -1dt /srv/notes/releases/*/ | sed -n 2p)        # 두 번째 = 직전
sudo ln -sfn "$PREV" /srv/notes/current.tmp
sudo mv -T /srv/notes/current.tmp /srv/notes/current
sudo systemctl restart notes-api
```

---

## 13. 갱신된 재배포 표 & 치트시트 (이 파트 기준)

| 무엇을 고쳤나 | 반영 방법 |
|---|---|
| 백엔드 코드(같은 릴리스 내 핫픽스 아님) | `deploy.sh` → 새 릴리스 + flip + restart |
| 코드 안 바뀌고 **설정/env만** (경로 동일) | `systemctl reload notes-api` (HUP, 무중단) |
| 잘못된 배포 되돌리기 | 심볼릭링크를 이전 릴리스로 flip + restart (수동 롤백) |
| nginx 설정 | `nginx -t && systemctl reload nginx` |

```bash
# 무중단 reload (워커만 graceful 교체 — 경로 안 바뀔 때)
sudo systemctl reload notes-api
# 원자적 배포 (fetch→archive→flip→헬스체크→실패 시 자동 롤백)
sudo -u app /srv/notes/deploy.sh           # main 최신 / 인자로 특정 ref 가능
# 워커 상태 / 동시처리량 확인
pgrep -af gunicorn ; curl -s http://localhost/api/health
```

> **이 파트의 핵심 교훈**
> - **프로세스 매니저(gunicorn)** = 멀티코어 + 워커 자동 재생성 + graceful reload. 단일 uvicorn은 학습용.
> - **reload(HUP)=무중단(경로 그대로), restart=경로 바뀔 때(릴리스 flip).** 둘을 상황별로 구분.
> - **원자적 릴리스 = 절반 상태 없는 전환 + 즉시 롤백.** 배포는 "덮어쓰기"가 아니라 "링크 바꾸기".
> - **배포는 헬스체크까지가 한 세트.** 실패하면 사람이 알기 전에 스크립트가 되돌린다.
> - 다음 확장 후보: **DB 운영**(Alembic 마이그레이션을 deploy.sh의 [3/6] 자리에), **보안 하드닝**(systemd 샌드박싱·rate limit), **진짜 공개+HTTPS**(도메인+Let's Encrypt).

---
---

# 🤖 패턴 A 실무 확장 II — CI/CD (self-hosted 러너 자동 배포)

> §12까지는 `deploy.sh`를 **사람이 직접** 실행했다. 이제 **`git push`만 하면 자동 배포**되게 잇는다.
> 집 PC/WSL은 공인 IP가 없어(NAT 뒤, §6) GitHub가 서버로 못 들어온다 → **서버가 바깥으로 나가 폴링하는** self-hosted 러너로 해결.

## 14. self-hosted 러너로 push → 자동 배포

### 14-1. 개념 — 왜 self-hosted 러너인가
- GitHub 호스팅 러너는 우리 `/srv/notes`·systemd에 접근 못 함.
- webhook/Actions가 **서버로 인바운드**하려면 공인 IP/터널 필요(NAT 문제).
- **self-hosted 러너**: 서버 안에서 돌며 GitHub로 **outbound 폴링** → 인바운드 0, NAT 통과. push 시 작업을 받아 로컬에서 `deploy.sh` 실행.

```
[로컬] git push ─▶ [GitHub] ──(작업 큐)
                                 ▲ outbound 폴링
                   [WSL: 러너(app)] ──▶ /srv/notes/deploy.sh
```

### 14-2. 러너 설치·등록 (app 유저)
GitHub: 저장소 → Settings → Actions → Runners → New self-hosted runner(Linux x64)에서 **등록 토큰**(1시간 만료) 확보. 러너를 **app 유저**로 설치 → 워크플로가 `deploy.sh`를 app 권한으로 바로 실행(이미 만든 NOPASSWD sudoers 활용).
```bash
RUNNER_VERSION=<페이지의 버전> ; TOKEN=<페이지의 --token>
sudo -u app -H env RV="$RUNNER_VERSION" TK="$TOKEN" bash -c '
  set -e
  mkdir -p /home/app/actions-runner && cd /home/app/actions-runner
  curl -fsSL -o runner.tar.gz "https://github.com/actions/runner/releases/download/v${RV}/actions-runner-linux-x64-${RV}.tar.gz"
  tar xzf runner.tar.gz && rm runner.tar.gz
  ./config.sh --url https://github.com/<owner>/<repo> --token "${TK}" --name wsl-app --labels self-hosted --unattended
'
```

### 14-3. 러너를 systemd 서비스로 (부팅 자동기동)
```bash
# /home/app 은 app 전용이라 root로 통째 실행 (cd 포함)
sudo bash -c 'cd /home/app/actions-runner && ./svc.sh install app && ./svc.sh start && ./svc.sh status'
# → active (running) + 로그 "Listening for Jobs" 확인
```

### 14-4. 워크플로 — `.github/workflows/deploy.yml`
```yaml
name: deploy
on:
  push:
    branches: [main]          # main push 만 (fork PR 로는 안 돎)
concurrency: { group: deploy, cancel-in-progress: false }   # 배포 직렬화
jobs:
  deploy:
    runs-on: self-hosted      # checkout 불필요 — deploy.sh 가 repo.git 에서 fetch
    steps:
      - run: /srv/notes/deploy.sh
```

### 14-5. ⚠️ public 저장소 + self-hosted 주의
- public + self-hosted = fork PR로 **러너에서 코드 실행** 위험 → GitHub도 비권장.
- 완화: **`push`(main) 트리거만** 사용(`pull_request` 안 씀), main push 권한은 본인만.
- 실무/민감 프로젝트는 **private 저장소** 권장(그 경우 서버 fetch에 deploy key/PAT 필요).

> **결과**: 개발 루프가 **코드 수정 → `git push` → (자동) deploy.sh → 헬스체크 실패 시 자동 롤백** 으로 완성. 사람은 push만 한다.
> **✅ 검증(이 랩)**: 워크플로를 push하자 `wsl-app` 러너가 작업을 받아 새 릴리스를 빌드·배포(Actions 탭에서 초록 체크).

---
---

# 🗄️ 패턴 A 실무 확장 III — DB 운영 (Alembic 마이그레이션 + 자동 백업)

> 앱이 커지면 스키마가 바뀐다. "그때그때 손으로 ALTER"는 추적·재현·롤백이 안 된다 → **Alembic 마이그레이션**으로 버전관리하고 **deploy.sh에 끼워** push 한 번에 스키마까지 반영. 데이터는 **pg_dump 타이머**로 정기 백업.

## 15. Alembic 마이그레이션 (async/asyncpg)

### 15-1. 왜 / 드라이버 선택
- 앱 lifespan의 `CREATE TABLE IF NOT EXISTS`는 "초기 1테이블"엔 됐지만 컬럼 추가·인덱스·제약 변경을 추적 못 함.
- Alembic = 스키마 변경을 **리비전(버전)** 으로 관리 → 각 배포가 어느 스키마인지 확정, 롤백 가능.
- 이 서버는 Python 3.14 → psycopg2 휠 이슈 회피 위해 **이미 쓰는 asyncpg를 Alembic에서도 재사용**(async 구성). env.py에서 `postgresql://`→`postgresql+asyncpg://` 변환.

### 15-2. 구성 파일
- requirements: `alembic`, `SQLAlchemy` (asyncpg는 기존).
- `backend/alembic.ini`: `script_location = %(here)s/migrations`(어디서 실행하든 OK), **url은 .env에서 주입**(시크릿 미포함).
- `backend/migrations/env.py`: async 엔진, `os.environ["DATABASE_URL"]`→asyncpg URL, `target_metadata=None`(ORM 없이 `op.*`로 수동 기술).
- **기존 DB 안전 채택**: `0001_baseline`을 `CREATE TABLE IF NOT EXISTS`로 → 기존 DB면 no-op(버전만 기록), 새 DB면 생성. 첫 `upgrade head`가 `alembic_version` 테이블 생성.

### 15-3. deploy.sh 에 끼우기 ([4/7])
```bash
echo "▶ [4/7] DB 마이그레이션 (alembic upgrade head)"
set -a; . "$APP/shared/.env"; set +a       # DATABASE_URL 로드(alembic env.py가 읽음)
"$REL/backend/.venv/bin/alembic" -c "$REL/backend/alembic.ini" upgrade head
```
- **flip(restart) 전에** 적용 = 새 코드가 뜨기 전 스키마 준비.
- **expand(추가형) 우선 원칙**: 컬럼 추가처럼 구코드와 호환되는 변경은 flip 전 적용해도 안전(구코드는 새 컬럼 무시). 파괴적 변경(컬럼 삭제/rename)은 **expand→deploy→contract** 2단계로 나눈다.
- ⚠️ 서버 `/srv/notes/deploy.sh`는 자동 동기 안 됨 → deploy.sh를 바꾸면 서버 파일도 수동 갱신(tee). (repo 보관본: `scripts/deploy.sh`)

### 15-4. 새 마이그레이션 추가 (예: 컬럼)
`migrations/versions/0002_add_created_at.py`:
```python
def upgrade():
    op.add_column("notes", sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False))
def downgrade():
    op.drop_column("notes", "created_at")
```
→ 코드(모델/쿼리)와 **같은 커밋**에 담아 push → 자동배포 `[4/7]`이 적용(기존 행은 default로 백필).

> **✅ 검증(이 랩)**: `git push` 하나로 0002 적용 → 기존 행 `created_at` 백필 → API가 `created_at` 서빙. lifespan의 CREATE TABLE은 제거(스키마=Alembic 소유, v1.3).

## 16. pg_dump 자동 백업 (systemd timer)

`/srv/notes/backup.sh` (app 소유):
```bash
set -a; . /srv/notes/shared/.env; set +a
pg_dump "$DATABASE_URL" | gzip > /srv/notes/backups/notes-$(date +%Y%m%d-%H%M%S).sql.gz
ls -1t /srv/notes/backups/notes-*.sql.gz | tail -n +8 | xargs -r rm -f   # 최근 7개 유지
```
`notes-backup.service`(oneshot, User=app, ExecStart=backup.sh) + `notes-backup.timer`:
```ini
[Timer]
OnCalendar=daily
RandomizedDelaySec=300
Persistent=true        # WSL이 꺼져 놓친 백업은 부팅 후 보충
[Install]
WantedBy=timers.target
```
```bash
sudo systemctl enable --now notes-backup.timer
sudo systemctl start notes-backup.service      # 즉시 1회(검증)
systemctl list-timers notes-backup.timer       # 다음 실행 확인
```
> 덤프엔 **스키마(+created_at)·데이터·`alembic_version`** 까지 포함 → 복원 시 마이그레이션 레벨도 일치.
> **복원**: `gunzip -c notes-<ts>.sql.gz | psql "$DATABASE_URL"`. 정기 **복원 드릴**로 백업이 실제 살아나는지 확인하는 게 실무(백업은 "복원될 때"만 백업).

> **이 파트 핵심**: 스키마 변경 = **마이그레이션(버전관리)**, 파이프라인이 자동 적용(expand 우선). 데이터는 **타이머 백업** + 복원 드릴. 다음 후보: 보안 하드닝(systemd 샌드박싱·rate limit·ufw) / 진짜 HTTPS(도메인+Let's Encrypt) / 진짜 0갭(socket activation).

---
---

# 🛡️ 패턴 A 실무 확장 IV — 보안 하드닝 & 운영 견고화

> 운영 준비도 점검([ops-readiness-checklist.md](ops-readiness-checklist.md))의 Tier1~2 항목 처리. 적용본 설정은 [`ops/`](../ops/)에 보존.

## 17. systemd 샌드박싱 + 리소스 제한
- 노출도 측정: `systemd-analyze security notes-api` → **하드닝 전 9.2 UNSAFE, 후 3.3 OK**.
- **리소스**: `MemoryMax=512M`/`MemoryHigh=400M`/`TasksMax=200` → 누수·폭주가 박스 전체 RAM을 먹는 것 차단(`max_requests` 워커 재생성과 2중 방어).
- **샌드박싱**: `ProtectSystem=strict`(FS 읽기전용), `NoNewPrivileges`, `PrivateTmp`, 커널 보호류, `SystemCallFilter=@system-service`, `RestrictAddressFamilies` 등. (전체: `ops/systemd/notes-api.service`)
- ⚠️ **함정(실제 겪음)**: `ProtectHome=true`면 asyncpg가 시작 시 `~/.postgresql` 클라이언트 인증서를 탐색하다 'Permission denied' → 풀 생성 실패 → **startup 크래시**(503). → **`ProtectHome=tmpfs`**(빈 홈)로 해결. "샌드박싱이 앱을 깨뜨릴 수 있다"의 산 증거 — **적용 후 반드시 실제 요청으로 검증**.

## 18. nginx 보호 (rate-limit·타임아웃·바디·헤더)
- `limit_req_zone`(http 컨텍스트) + `limit_req`: **/login 분당 10**(무차별 대입 차단, 초과 429), 일반 API 30r/s.
- `client_max_body_size 1m`(거대 요청 413), 프록시 타임아웃, 보안헤더(`nosniff`/`DENY`/`no-referrer`). SSE 위해 `/api/` 는 `proxy_read_timeout 60s`.
- 검증: 로그인 연타→200×6(버스트)→429, 2MB→413, 헤더 존재. (전체: `ops/nginx/notes.conf`)

## 19. ufw 방화벽 (+ WSL mirrored 함정)
- 외부는 80/443/22만, 나머지 인바운드 deny. (`ops/ufw/setup-ufw.sh`)
- ⚠️ **WSL mirrored 함정(실제 겪음)**: `127.0.0.1`이 `lo`를 안 타 ufw 기본 loopback 허용(`-i lo`)에 안 걸림 → 내부통신(nginx→gunicorn, gunicorn→pg)이 끊겨 **504**. → **`ufw allow from 127.0.0.0/8`**(출발지 기준)로 우회하면 외부는 막고 내부는 통과. 진짜 VPS는 이 줄 불필요(`lo` 정상).
- 본질: **WSL의 외부 차단 1차 관문은 Windows 방화벽**(§7). ufw는 보조 + VPS 이식용 패턴.

## 20. 헬스체크 심화 (liveness → readiness)
- 기존 `/health`는 풀 객체만 봄 → DB 죽어도 200(거짓 양호). → **실제 `SELECT 1`** 쿼리, 실패 시 **503**(`pool.acquire(timeout=2)`로 풀 고갈 시도 매달리지 않음).
- 효과: deploy.sh 헬스 게이트가 "떴지만 DB 안 되는" 배포를 **503으로 잡아 자동 롤백**, 모니터링도 DB 장애를 정확히 인지. 검증: `systemctl stop postgresql`→503, start→200.

## 21. 백업 안전성 (복원 드릴 · 오프사이트 · 시크릿)
- **복원 드릴**: 임시 DB에 최신 덤프 복원 → 행수·`alembic_version` 확인 → 삭제. *"백업은 복원될 때만 백업."*
  - RPO 교훈: 덤프는 **시점 스냅샷** → 복원 행수가 라이브보다 적을 수 있음(일1회=최대 24h 유실). 줄이려면 주기↑/WAL 아카이빙(PITR).
- **오프사이트**: `backup.sh`가 Windows `/mnt/c`에도 사본 → WSL 초기화돼도 생존. **`.env`(시크릿)도 백업** — 없으면 JWT_SECRET·DB비번 복구 불가. ⚠️ 평문이라 실무는 **gpg/age 암호화** 또는 시크릿 매니저.

> **이 파트 핵심**: 격리·제한으로 **폭발반경 축소**(systemd), **입구 보호**(nginx·ufw), **진짜 상태 노출**(readiness), **데이터 생존**(오프사이트+복원드릴). 남은 큰 것: 모니터링/알림, CI 테스트 게이트, 진짜 HTTPS(도메인+Let's Encrypt), 진짜 0갭(socket activation), **그리고 결국 실제 VPS 이식**(WSL의 구조적 한계: idle-sleep·loopback·단일 호스트).

---
---

# 📈 패턴 A 실무 확장 V — 모니터링 (관측)

> 점검표 Tier1 "관측 없음" 처리. **블랙박스**(밖에서 두드려보기) + **화이트박스**(앱 내부 지표) 둘 다.

## 22. 헬스 프로브 + 앱 메트릭 + Prometheus

### 22-1. 블랙박스 — 합성(synthetic) 업타임 프로브
- `healthprobe.sh`(app): 1분마다 `/health` curl → UP/DOWN·응답시간 journald 기록, **DOWN 3연속 시 `monitor/alerts.log` 알림**. (`ops/systemd/notes-healthprobe.*`, `scripts/healthprobe.sh`)
- `/health`가 readiness(§20)라 **DB 장애까지 잡음**. 조회: `journalctl -u notes-healthprobe`.
- **진짜 폰 푸시(ntfy.sh)**: DOWN 3연속 시 🚨, 복구 시 ✅ 한 번씩(스팸 방지). 토픽은 `/srv/notes/shared/monitor.env`의 `NTFY_TOPIC`(repo 미포함=시크릿), 폰 ntfy 앱/브라우저로 구독. 계정·인바운드 불필요(서버가 outbound POST).
- ⚠️ **테스트 함정(겪음)**: Ubuntu `postgresql.service`는 래퍼(active exited) → `stop`해도 클러스터가 안 멈춤. 실제 정지는 **`postgresql@18-main.service`**. DOWN 드릴은 이걸 멈추고 `/health=503` 확인 후 프로브.

### 22-2. 화이트박스 — 앱 `/metrics` (prometheus_client)
- 미들웨어가 `http_requests_total{method,path,status}` + `http_request_duration_seconds{method,path}` 기록. path는 **라우트 템플릿**(`/notes/{note_id}`)으로 카디널리티 폭발 방지.
- ⚠️ **gunicorn 멀티워커 함정**: 워커마다 따로 집계 → 스크랩마다 값이 들쭉날쭉. → **멀티프로세스 모드**: `Environment=PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus`(PrivateTmp라 워커 공유), gunicorn `on_starting`(디렉터리 초기화)·`child_exit`(`mark_process_dead`) 훅, `/metrics`가 `MultiProcessCollector`로 합산. **검증: 두 번 스크랩해도 동일 합계.**
- ⚠️ **노출 주의**: `/metrics`는 내부 전용 → nginx에서 `location = /api/metrics { return 404; }`로 외부 차단. Prometheus는 `127.0.0.1:8000` 직접 스크랩(루프백, nginx 우회 = rate-limit 무관).

### 22-3. Prometheus 수집·저장·조회
- apt `prometheus`(:9090, systemd 서비스). `prometheus.yml`에 `notes-api`(127.0.0.1:8000) 스크랩(15s). (`ops/prometheus/prometheus.yml`)
- ufw가 9090 외부 차단 → 로컬 전용. 조회: 웹 UI `http://localhost:9090` 또는 HTTP API.
- **PromQL 예시**:
  - `http_requests_total` — 상태코드별 누적 요청 수
  - `sum(rate(http_requests_total[1m]))` — 초당 요청률
  - `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))` — p95 지연
- 검증: 타깃 `up`, `http_requests_total{job="notes-api"}` 수집 확인.

> **이 파트 핵심**: 블랙박스(살아있나?)·화이트박스(무슨 일이?)는 상호보완. 멀티워커면 메트릭은 **멀티프로세스 합산 필수**, `/metrics`는 내부 전용. 남은 것: 대시보드(Grafana), 호스트 메트릭(node_exporter), 에러트래킹, 그리고 실제 VPS.

---
---

# ✅ 패턴 A 실무 확장 VI — CI 테스트 게이트

> push가 곧 배포(§14)인데 그 사이 자동 테스트가 없으면 깨진 코드도 배포된다. **테스트 통과해야만 배포**되게 게이트를 건다.

## 23. 테스트 게이트 (`deploy needs test`)
- 워크플로에 `test` 잡(pytest) 추가 + `deploy` 잡이 **`needs: test`** → 테스트 실패 시 배포 자동 차단.
- 테스트: `backend/tests/test_auth.py` — 로그인/JWT 검증 등 **DB 불필요한 보안 핵심 로직**(`login()`·`_decode_user()`는 DB 연결 없이 순수). `conftest.py`가 임포트용 env 주입.
```yaml
jobs:
  test:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - working-directory: backend
        run: |
          VENV="$RUNNER_TEMP/ci-venv"      # 잡 전용 임시디렉터리(러너 소유·자동청소)
          rm -rf "$VENV"; python3 -m venv "$VENV"
          "$VENV/bin/pip" install -q -r requirements.txt pytest
          "$VENV/bin/pytest" -q
  deploy:
    needs: test                            # ← 통과해야만 배포
    runs-on: self-hosted
    steps:
      - run: /srv/notes/deploy.sh
```
- ⚠️ **함정(겪음)**: CI venv를 `/tmp/ci-venv` 공유 경로에 두니, 다른 유저가 만든 동명 디렉터리를 **sticky `/tmp`에서 못 지워**(`rm` 실패 → `set -e`로 잡 실패). → **`$RUNNER_TEMP`**(잡별 격리·자동청소) 사용. CI는 공유 상태 오염을 피하라는 교훈.
- ✅ **양방향 검증**: 테스트 실패 → deploy 건너뜀(릴리스 불변=배포 차단), 통과 → deploy 진행(새 릴리스).

> **핵심**: 게이트는 "**실패가 배포를 막는** 것"을 봐야 진짜다. 다음: DB 통합 테스트(테스트 전용 DB), 커버리지, 린트(ruff).

---
---

# 📊 패턴 A 실무 확장 VII — Grafana 대시보드

> Prometheus(§22-3)의 시계열을 시각화. 데이터소스·대시보드를 **프로비저닝(선언적 파일)** 으로 관리 — 클릭 설정이 아니라 repo로.

## 24. Grafana (프로비저닝 기반)
- 설치: Grafana 공식 apt 저장소(`apt.grafana.com`) → `grafana-server`(:3000, systemd).
- **데이터소스 프로비저닝** `/etc/grafana/provisioning/datasources/prometheus.yml`: Prometheus(`http://127.0.0.1:9090`), `uid=prometheus`.
- **대시보드 프로비저닝** `/etc/grafana/provisioning/dashboards/notes.yml` → `/var/lib/grafana/dashboards/*.json` 자동 로드. `notes-api.json` 패널: **UP**(stat), **요청률 req/s by status**(timeseries), **p95 지연**(`histogram_quantile`). (전체: `ops/grafana/`)
- 접근: 브라우저 `http://localhost:3000`. ufw가 3000 외부 차단(로컬 전용). 
- ⚠️ **함정(겪음)**: 기본 `admin/admin` API 인증이 거부될 수 있음(버전차) → `sudo grafana cli admin reset-admin-password '<pw>'`로 알려진 값 지정 후 로그인.

> **핵심**: 대시보드도 **코드(JSON)로 버전관리** — 클릭 설정은 재현이 안 된다. 수집(Prometheus)과 시각화(Grafana)는 분리. 남은 관측 후보: 알림 룰(Grafana alerting/Alertmanager), node_exporter(호스트 지표), 로그 수집(Loki).
