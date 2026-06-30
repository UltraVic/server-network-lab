# 실습 일지 — 패턴 A 기초 배포 (§0~9, "이전 버전")

> 대상: `server-network-lab` 토이 메모 API · 환경: WSL2 Ubuntu(`dylan`)를 "내 VPS"처럼
> 목표: Docker 없이 **systemd + Nginx**로 직접 배포·운영 (브라우저→Nginx→uvicorn→Postgres).
> how-to 레퍼런스: [`wsl-pattern-a-deploy.md`](wsl-pattern-a-deploy.md) §0~9. 후속(무중단화)은 [`practice-log-zero-downtime-deploy.md`](practice-log-zero-downtime-deploy.md).
>
> ⚠️ **이 일지는 재구성본**입니다. 이 기초 배포는 **이전 세션**에서 했고 그때의 실시간 출력 기록은 남아있지 않습니다. 그래서 ① 레퍼런스 문서(그 작업의 정식 기록) + ② **지금 살아있는 시스템의 실측 상태**(인증서·유닛·nginx conf·포트 등)로 채웠습니다. "실측" 표시가 붙은 값은 2026-06-30 현재 시스템에서 직접 확인한 것.

---

## 0. 목표 아키텍처

```
[브라우저] ─ http(s) ─▶ [Nginx :80/:443]   ← 유일한 외부 입구
                          ├ /      → 정적파일 직접 서빙
                          └ /api/  → 127.0.0.1:8000
                                     └▶ [uvicorn :8000] (systemd: notes-api, User=app)
                                          └▶ [Postgres :5432]
   · backend·db 는 127.0.0.1 바인드 = 외부 비노출 (Nginx만 외부 공개)
   · 시크릿은 /srv/notes/.env, 서비스는 enable → 재부팅 자동기동
```

> 핵심 개념: **외부 진입을 Nginx 한 곳으로 모으고, 백엔드·DB는 루프백(127.0.0.1)만 바인드해 외부에 안 연다.** Docker Compose에서 `ports:` 안 준 것과 같은 효과를 systemd 세상에서 재현.

---

## §1. WSL2를 "내 VPS"로

- WSL = 내 PC 안의 리눅스 → 진짜 서버 없이 systemd·Nginx 배포 실습 가능.
- `docker-desktop` 배포판 말고 일반 **Ubuntu** 사용.
```powershell
wsl --install -d Ubuntu        # 설치 (첫 실행 OOBE에서 계정 dylan 생성)
wsl --set-default Ubuntu
```
- 요즘 Ubuntu WSL은 **systemd 기본 활성** → `systemctl is-system-running` = `running`. **실측 ✅**

---

## §2. 서버 구성 (배포 단계별)

### 2-1. 패키지 + 전용 유저
```bash
sudo apt-get install -y nginx python3-venv python3-pip postgresql
sudo useradd --system --create-home --shell /bin/bash app   # root로 안 돌리려 전용 유저
```
> **실측 ✅** `app` 유저 존재(uid 999). 앱은 root가 아니라 `app`으로 구동.

### 2-2. 코드 배치 + venv
```bash
sudo mkdir -p /srv/notes
sudo cp -r <소스>/backend /srv/notes/ ; sudo cp -r <소스>/frontend /srv/notes/
sudo python3 -m venv /srv/notes/backend/.venv
sudo /srv/notes/backend/.venv/bin/pip install -r /srv/notes/backend/requirements.txt
```

### 2-3. Postgres 역할·DB
```bash
sudo -u postgres psql -c "CREATE ROLE lab LOGIN PASSWORD '<db-pw>';"
sudo -u postgres psql -c "CREATE DATABASE lab OWNER lab;"
PGPASSWORD='<db-pw>' psql -h 127.0.0.1 -U lab -d lab -c "SELECT 1;"   # TCP 로그인 확인
```

### 2-4. 시크릿 분리 (.env)
```bash
sudo tee /srv/notes/.env <<'ENV'
DATABASE_URL=postgresql://lab:<db-pw>@127.0.0.1:5432/lab
JWT_SECRET=<랜덤-32자+>
POOL_SIZE=10
ENV
sudo chown -R app:app /srv/notes && sudo chmod 600 /srv/notes/.env
```
> **실측 ✅** `/srv/notes/.env` = `-rw------- app app`(600). 남이 못 읽음.

### 2-5. 백엔드를 systemd 서비스로 (원본 유닛 — **실측 ✅**, `.bak`에 보존됨)
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
sudo systemctl daemon-reload && sudo systemctl enable --now notes-api
```
> `--host 127.0.0.1` = **루프백만 바인드 → 외부 비노출**. `enable` = 부팅 자동기동.
> (이 단일 uvicorn 유닛이 나중에 §10에서 gunicorn 멀티워커로 교체됨.)

### 2-6. Nginx — 정적 서빙 + /api 프록시
> **실측 ✅** 현재 `/etc/nginx/sites-available/notes.conf` (이미 §5 HTTPS·§11 current 반영분 포함):
```nginx
server { listen 80 default_server; server_name _; return 301 https://$host$request_uri; }
server {
    listen 443 ssl default_server; server_name _;
    ssl_certificate /etc/nginx/certs/localhost.crt; ssl_certificate_key /etc/nginx/certs/localhost.key;
    root /srv/notes/current/frontend; index index.html;       # (기초판엔 /srv/notes/frontend 였음)
    location / { try_files $uri $uri/ /index.html; }          # SPA 새로고침 대응
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;                    # 끝의 / 가 /api 접두어 제거
        proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme; proxy_buffering off;
    }
}
```
```bash
sudo ln -sf .../notes.conf /etc/nginx/sites-enabled/ ; sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

### 2-7. 동작 확인 — 포트 노출 형태 (**실측 ✅**)
```
127.0.0.1:8000   ← uvicorn (비노출)
0.0.0.0:80,443   ← nginx (외부 공개)
127.0.0.1:5432   ← postgres (비노출)
```
> "0.0.0.0 = 외부 노출 / 127.0.0.1 = 비노출"이 한눈에. nginx만 외부, 나머지는 루프백.

---

## §3. 접속하기

### 3-1. localhost 접속 (mirrored 네트워킹) — **실측 ✅**
기본 WSL은 NAT라 Windows `localhost`로 WSL 서비스에 안 닿음. **mirrored**로 바꿔 공유.
`C:\Users\<나>\.wslconfig`:
```ini
[wsl2]
networkingMode=mirrored
```
→ `wsl --shutdown` 후 재시작하면 `http://localhost` 접속. (서비스는 `enable`돼서 자동 복구)

### 3-2. ⚠️ WSL은 유휴 시 잠든다
- 활동 없으면 WSL이 배포판을 자동 Stopped → `localhost`도 멈춤.
- 가장 간단한 해결: `wsl` 터미널 창 하나 열어둠(최소화 OK). 실제 VPS는 이 문제 없음(항상 켜짐).
- (이번 세션 시작 때도 Ubuntu가 `Stopped`였다가 깨워서 진행)

---

## §4. 재배포 사이클 (기초판 — "복사 + 재기동")

```bash
sudo cp <소스>/backend/main.py /srv/notes/backend/main.py
sudo chown app:app /srv/notes/backend/main.py
sudo systemctl restart notes-api          # 백엔드(.py) → 재기동 필요
#   프론트 정적 → 복사 + 브라우저 새로고침 (재기동 불필요)
#   nginx 설정 → nginx -t && systemctl reload nginx
```
| 무엇을 고쳤나 | 반영 |
|---|---|
| 백엔드 `.py` | `systemctl restart notes-api` |
| 프론트 정적 | 복사 + 새로고침 |
| nginx 설정 | `nginx -t && systemctl reload nginx` |
| `.env` | 값 수정 + `restart` |

> **핵심: "파일 수정 ≠ 배포".** 서버(`/srv/notes`)로 옮기고 반영해야 라이브 적용.
> ⚠️ 이 방식의 한계(덮어쓰기·다운타임·롤백 불가)가 바로 §10~12에서 **gunicorn 무중단 + git 원자적 릴리스 + 자동 롤백**으로 해결한 동기.

---

## §5. HTTPS (자체 서명 인증서) — **실측 ✅**

도메인 없으면(localhost) 자체 서명으로 TLS 메커니즘 실습. 브라우저는 "신뢰 안 됨" 경고(자체 서명이라).
```bash
sudo openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout /etc/nginx/certs/localhost.key -out /etc/nginx/certs/localhost.crt \
  -days 365 -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```
> **실측 ✅** `/etc/nginx/certs/`에 `localhost.crt`(644 root) + `localhost.key`(600 root) 존재(2026-06-29 생성).
> nginx는 80→443 리다이렉트 + 443 SSL. 확인: `curl -k https://localhost/api/health` (`-k`=자체서명 무시).

**개념 — TLS termination at the edge**: 암복호화는 **Nginx(정문)에서만**, 내부(Nginx↔백엔드)는 평문 http → 백엔드는 TLS 몰라도 됨. 실무는 공인 도메인 + Let's Encrypt(certbot)/Caddy로 경고 없는 진짜 자물쇠.

---

## §6. 네트워크와 IP (개념)

```
① 127.0.0.1   → 이 PC 자신만. 항상 고정.
② LAN IP (192.168.x / 10.x) → 같은 공유기 안 기기만. 사설(private), 수시로 바뀜.
③ 공인 IP (공유기 WAN) → 인터넷이 보는 주소. ISP가 줌. 동적. NAT로 인바운드 차단.
```
- **사설 IP는 인터넷에서 안 보인다** → 같은 네트워크 내부 전용.
- WiFi 바꾸면 LAN·공인 IP 모두 바뀜(DHCP). → 안정적 공개엔 **고정 공인 IP(VPS) + 도메인** 필요.
- 도메인은 접속 필수 아님(공인 IP로 바로 접속 가능). 도메인은 ①외우기 쉬운 이름 ②신뢰된 HTTPS 인증서 ③IP 바뀌어도 이름 유지 용도.
```powershell
ipconfig                  # LAN IP (Wi-Fi IPv4)
curl.exe ifconfig.me      # 공인 IP
```
```bash
hostname -I               # WSL IP (mirrored면 Windows와 공유)
```

---

## §7. 같은 WiFi에서 폰으로 접속

같은 공유기 폰에서 **PC의 LAN IP**로 접속 가능 — 단 **Windows 방화벽**이 막음.
1. 폰을 PC와 같은 WiFi에 연결
2. 폰 브라우저 `http://<PC의 LAN IP>`
3. 막히면 80/443 인바운드 허용:
```powershell
New-NetFirewallRule -DisplayName "notes-lab http/https" -Direction Inbound `
  -Protocol TCP -LocalPort 80,443 -Action Allow -Profile Public
```
> ⚠️ **방화벽 전체 OFF는 비추천** — 필요한 포트만 연다(= VPS의 `ufw allow 443` 원리). 자체서명이라 폰에선 이름 불일치 경고가 뜸("그래도 진행"하면 동작).
>
> **실측 현재상태**: 위 `notes-lab` 방화벽 규칙은 **현재 시스템엔 안 잡힘**(네트워크가 바뀌었거나 그때만 임시로 열었던 듯). 폰 접속을 다시 하려면 위 규칙을 재추가하면 됨.

---

## 💥 이 단계에서 배운 핵심 함정 / 개념

| 포인트 | 요지 |
|---|---|
| 127.0.0.1 vs 0.0.0.0 바인드 | 루프백 = 외부 비노출. 외부 진입은 Nginx 한 곳으로 |
| `proxy_pass .../;` 끝 슬래시 | `/api/` 접두어를 떼고 백엔드로 전달 |
| `proxy_buffering off` | SSE(스트리밍)가 버퍼에 갇히지 않게 |
| `X-Forwarded-Proto` | 내부는 http지만 "원래 https였음"을 백엔드에 알림 |
| `systemctl enable` | 부팅/재시작 후 서비스 자동 복구 |
| `.env` 600 + chown app | 시크릿을 남이 못 읽게, 앱 유저 소유 |
| WSL 유휴 Stopped | 실 서버엔 없는 WSL만의 특성 (창 열어두기) |
| 자체서명 경고 | TLS 메커니즘은 동작, 신뢰만 없음 → 실무는 공인 인증서 |

---

## 🧠 복습 셀프 퀴즈

1. 백엔드를 `--host 127.0.0.1`로 띄우면 외부에서 `:8000` 직접 접속이 왜 안 되나?
2. `proxy_pass http://127.0.0.1:8000/;`에서 **끝의 `/`** 가 하는 일은?
3. 자체서명 인증서인데 `curl`이 성공하려면 왜 `-k`가 필요한가?
4. 사설 IP(192.168.x)로 인터넷의 친구가 내 앱에 접속할 수 없는 이유는?
5. WSL `localhost` 접속이 안 될 때 `.wslconfig`에 뭘 넣나?
6. 방화벽을 통째로 끄는 대신 "80/443만 허용"이 더 안전한 이유는?
7. `systemctl restart`와 (코드 안 바뀐) 설정 변경 시 쓰는 명령의 차이는?

---

## 📋 이전 버전 최종 상태 (실측 2026-06-30)

```
서비스:   notes-api(=현재는 gunicorn으로 업글됨) · nginx · postgresql = active
포트:     127.0.0.1:8000 / 0.0.0.0:80 / 0.0.0.0:443 / 127.0.0.1:5432
HTTPS:    자체서명 localhost.crt/.key (/etc/nginx/certs)
nginx:    80→443 리다이렉트 + 443 SSL + /api 프록시
시크릿:   /srv/notes/.env (600, app)  → 이후 shared/.env 로 이동(§11)
네트워킹: .wslconfig networkingMode=mirrored
```

> **이 기초 배포(§0~9)가 토대**, 그 위에 [무중단 배포 일지(§10~12)](practice-log-zero-downtime-deploy.md)가 쌓였다:
> 단일 uvicorn → gunicorn 멀티워커 → git 원자적 릴리스 → deploy.sh 자동배포/자동롤백.
