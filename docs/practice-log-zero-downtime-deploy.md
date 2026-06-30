# 실습 일지 — 패턴 A 무중단 배포 사이클 구축 (§10~12)

> 날짜: 2026-06-30 · 환경: WSL2 Ubuntu(`dylan`) · 대상: `server-network-lab` 토이 메모 API
> 목표: 이미 떠 있던 패턴 A 배포(단일 uvicorn)를 **실무급 무중단 배포 사이클**로 끌어올린다.
> how-to 레퍼런스는 [`wsl-pattern-a-deploy.md`](wsl-pattern-a-deploy.md) §10~13. 이 문서는 **실제로 친 명령·본 출력·배운 점**의 기록.

---

## 0. 시작 상태 (점검 결과)

| 항목 | 값 |
|---|---|
| OS / 코어 | Ubuntu(WSL2, systemd `running`) / **12코어** |
| Python / nginx | 3.14 / 1.28 |
| 앱 경로 | `/srv/notes` (소유 `app`, `.env`는 600) |
| 서비스 | `notes-api`·`nginx`·`postgresql` 전부 `active` |
| 포트 | `127.0.0.1:8000`(uvicorn) · `0.0.0.0:80`(nginx) · `127.0.0.1:5432`(pg) |
| 기타 | §5의 80→443 리다이렉트 적용됨(http는 301), gunicorn 미설치 |

**작업 방식**: `sudo`가 비밀번호를 요구 → **변경 명령은 내가 직접 WSL 터미널에 입력**, 읽기 검증(curl/ss/pgrep/systemctl status)은 Claude가 `wsl -d Ubuntu` 로 수행.

---

## §10. 단일 uvicorn → Gunicorn 멀티워커 + 무중단 reload

### 목표
프로세스 1개(코어 1개, restart마다 다운타임) → **마스터-워커 구조**로. 워커 여러 개 + 처리 중 요청 안 끊는 graceful reload.

### 10-A. gunicorn 설치
```bash
echo "gunicorn==23.0.0" | sudo -u app tee -a /srv/notes/backend/requirements.txt
sudo -u app /srv/notes/backend/.venv/bin/pip install gunicorn==23.0.0
# → Successfully installed gunicorn-23.0.0
```
`app` 소유 venv라 `sudo -u app`으로 설치(권한 일치).

### 10-B. gunicorn.conf.py — **DB 커넥션 함정 반영**
```python
workers = int(os.environ.get("WEB_CONCURRENCY", "3"))   # 12코어지만 3으로!
bind = "127.0.0.1:8000"
graceful_timeout = 30
max_requests = 1000          # 워커가 N요청 후 재생성 → 메모리 누수 방어
max_requests_jitter = 100
```
> ⚠️ **핵심 깨달음**: 워커마다 asyncpg 풀(`POOL_SIZE=10`)을 따로 연다 → **총 DB 커넥션 = 워커수 × 풀크기**.
> `12 × 10 = 120 > Postgres max_connections(100)` → 터진다. 그래서 코어 수가 아니라 **`max_connections ÷ 풀크기`** 가 워커 상한. → 3으로 설정.

### 10-C. systemd 유닛 교체
```ini
ExecStart=/srv/notes/backend/.venv/bin/gunicorn -c /srv/notes/backend/gunicorn.conf.py main:app
ExecReload=/bin/kill -s HUP $MAINPID     # ← reload = graceful HUP
KillMode=mixed
TimeoutStopSec=35
```
(교체 전 `notes-api.service.bak`로 백업)

### 10-D. 멀티워커 확인
```
gunicorn 마스터 987  +  워커 992 / 993 / 994     ← 설정대로 3개
health(직접 8000): {"status":"ok","version":"1.1","pool_max":10}
```

### 10-E. 무중단 reload 실험 (하이라이트)
0.1초마다 `/health`를 때리며 `sudo systemctl reload notes-api` 실행:
```
응답코드: 200 200 200 ... 200   ← reload 순간에도 전부 200, 단 하나도 안 끊김
reload 전 워커: 992 993 994
reload 후 워커: 1143 1144 1145   ← 전부 새 PID
마스터 PID 987: 그대로
```
> **결론**: 마스터가 리슨 소켓을 쥔 채 살아있고 워커만 graceful 교체 → **다운타임 0**. 이게 "reload"의 의미.

---

## §11. git(GitHub) 기반 원자적 릴리스 + `current` 심볼릭링크

### 목표
배포를 "폴더 덮어쓰기"에서 **"커밋을 릴리스로 추출 + 심볼릭링크 전환"** 으로. 무엇이 라이브인지 SHA로 확정, 롤백은 링크 되돌리기.

### 11-A. 로컬 git 초기화
```bash
git init -b main
# .gitattributes 추가: * text=auto eol=lf  (Linux로 갈 셸이 CRLF로 안 깨지게)
git add -A && git commit -m "..."        # → 6127fb9
```
> ✅ `.gitignore`가 `.env`·`.venv`를 막아 **시크릿이 커밋에 안 들어감**을 직접 확인(`git ls-files`에 `.env` 없음). Phase 7에서 만든 gitignore가 여기서 빛을 발함.

### 11-B. GitHub push
- `github.com`에 **public** 저장소 `UltraVic/server-network-lab` 생성(README/gitignore 체크 해제 = 빈 저장소).
- `git remote add origin ... && git push -u origin main`.
- public인 이유: 시크릿은 gitignore됨 → 안전 + **서버가 인증 없이 fetch** 가능.

### 11-C. 서버에 bare repo 캐시
```bash
sudo -u app -H git clone --bare https://github.com/UltraVic/server-network-lab.git /srv/notes/repo.git
```
서버의 "코드 원본 캐시". 작업트리 없는 bare(서버는 편집 안 하고 추출만 함).

### 11-D1. **정합성 문제 해결** — 서버 설정을 repo로 편입
§10에서 `gunicorn`/`gunicorn.conf.py`를 **서버에서만** 만들었음 → git엔 없음 → archive 릴리스에 빠짐.
> **원칙: 서버는 편집하는 곳이 아니라 배포받는 곳.** 서버 프로토타입을 git에 반영해야 git이 진실의 소스.
- `backend/requirements.txt`에 `gunicorn==23.0.0` + `backend/gunicorn.conf.py` 추가 → commit `3bd19a7` → push.

### 11-D2. 릴리스 1개를 git으로 직접 빌드 (deploy.sh가 할 일을 손으로 1회)
```bash
git --git-dir=repo.git fetch ...
SHA=$(... rev-parse --short main)             # 3bd19a7
REL=/srv/notes/releases/20260630-132414-3bd19a7
git --git-dir=repo.git archive main | tar -x -C "$REL"   # 커밋 스냅샷 추출
python3 -m venv "$REL/backend/.venv" && pip install -r ...  # 릴리스 전용 venv
cp -p /srv/notes/.env /srv/notes/shared/.env  # .env는 shared 공통 1벌
ln -sfn "$REL/.env" → shared/.env
ln -sfn "$REL" current.tmp && mv -T current.tmp current   # 원자적 전환
```
구조:
```
/srv/notes/
├─ repo.git/                         (bare 캐시)
├─ shared/.env                       (릴리스 공통 시크릿)
├─ releases/20260630-132414-3bd19a7/ (backend+frontend+.venv)
└─ current ─▶ releases/20260630-132414-3bd19a7
```

### 11-D3. Cutover — systemd·nginx 를 `current/` 경유로
```ini
WorkingDirectory=/srv/notes/current/backend
EnvironmentFile=/srv/notes/current/.env
ExecStart=/srv/notes/current/backend/.venv/bin/gunicorn -c .../current/backend/gunicorn.conf.py main:app
```
```bash
sudo sed -i 's#root /srv/notes/frontend;#root /srv/notes/current/frontend;#' .../notes.conf
sudo systemctl daemon-reload && sudo systemctl restart notes-api
sudo nginx -t && sudo systemctl reload nginx
```
확인: gunicorn 실행 경로가 `/srv/notes/releases/...-3bd19a7/...`로 해소됨(=current 경유), health·https 정상.

---

## §12. deploy.sh — 한 줄 배포 + 롤백

### 12-A. sudoers + deploy.sh
```bash
# app 이 서비스 restart/reload 만 무인증 (딱 그 2개만 = 최소권한)
echo 'app ALL=(root) NOPASSWD: /usr/bin/systemctl restart notes-api, /usr/bin/systemctl reload notes-api' \
  | sudo tee /etc/sudoers.d/notes
sudo visudo -cf /etc/sudoers.d/notes          # → parsed OK (문법 검증 필수!)
```
`deploy.sh`(요지): `fetch → git archive <ref> → venv → .env링크 → current flip → restart → 헬스체크 10회 → 실패 시 자동 롤백 → 오래된 릴리스 정리`.

### 12-B. 실제 배포 (1.1 → 1.2)
`APP_VERSION = "1.2"` 커밋(`4b3981c`) → push → `sudo -u app /srv/notes/deploy.sh`:
```
✅ 배포 성공: 20260630-133346-4b3981c
배포 후 health: {"version":"1.2"}              ← git→배포가 라이브 반영
배포 중 응답코드: 200...200  000 000 000  200...200
```
> **reload vs restart 대비 실측**:
> - §10-E `reload`(HUP): 전부 200 → **0갭**
> - §12-B `deploy.sh restart`: `000` 3개 → **짧은 갭**(~0.3s)
> deploy.sh가 restart를 쓰는 이유 = 심볼릭링크 flip된 **새 경로를 마스터가 다시 해소**해야 하기 때문. 진짜 0갭은 socket activation 몫.
> (`[5/6]`의 `curl (7) Failed to connect`는 헬스체크 재시도 루프가 갭에 걸렸다 다음 시도에 통과 = 안전장치 정상)

### 12-C. 수동 롤백 (즉시)
```bash
PREV=$(ls -1dt /srv/notes/releases/*/ | sed -n 2p)
sudo -u app bash -c "ln -sfn '$PREV' current.tmp && mv -T current.tmp current"
sudo systemctl restart notes-api
# 롤백 후: version 1.1   /   롤포워드 후: version 1.2   ← 재빌드 0, 링크만 교체
```

### 12-C2. 자동 롤백 시연 (안전장치 하이라이트)
`broken-test` 브랜치에 **import 시 터지는 커밋**(`raise RuntimeError(...)`, `0349d91`) push → `deploy.sh broken-test`:
```
[5/6] restart + 헬스체크
curl: (7) Failed to connect ... (×10)          ← 워커가 import에서 터져 부팅 실패
❌ 헬스체크 실패 → 자동 롤백
↩ 이전 릴리스로 롤백: .../20260630-133346-4b3981c   (=1.2)
(exit 1)
```
검증: `current → 4b3981c(1.2)`(깨진 0349d91 아님), `active`, health `1.2`.
> **사람이 알기 전에 스스로 직전 정상 릴리스로 복구.** 무중단 배포의 핵심 안전장치.

정리: `broken-test` 로컬 삭제 + `main` 복귀. (원격/서버 ref는 별도 정리)

---

## §13. CI/CD — `git push` 자동 배포 (self-hosted 러너)

### 목표
`deploy.sh`를 사람이 치는 대신 **push하면 자동 실행**. 단 집 WSL은 공인 IP 없음(NAT) → GitHub가 못 들어옴 → **서버가 outbound로 폴링하는** self-hosted 러너 사용.

### 한 일
1. **러너 등록** (app 유저, `/home/app/actions-runner`) — GitHub Settings→Actions→Runners→New 에서 토큰 받아 `config.sh ... --labels self-hosted --unattended`. → `√ Runner successfully added`.
2. **systemd 서비스화** — `sudo bash -c 'cd /home/app/actions-runner && ./svc.sh install app && ./svc.sh start'` → `actions.runner.UltraVic-server-network-lab.wsl-app` `active`, `Listening for Jobs`. (러너 실행 유저=app → 워크플로가 deploy.sh를 app 권한으로 바로 실행, NOPASSWD sudoers 활용)
3. **워크플로** `.github/workflows/deploy.yml`: `on: push(main)` → `runs-on: self-hosted` → `run: /srv/notes/deploy.sh`. **checkout 불필요**(deploy.sh가 repo.git에서 fetch).

```
git push ─▶ GitHub ──(작업 큐)──▶ [WSL 러너(app)] ──▶ /srv/notes/deploy.sh ──▶ 무중단 배포(+자동롤백)
                       ▲ outbound 폴링 (인바운드 0 = NAT 통과)
```

### ⚠️ 주의 (실측 반영)
- **public + self-hosted 위험**: fork PR로 러너에서 코드 실행 가능 → GitHub 비권장. 완화: **`push`(main)만** 트리거, main push 권한은 본인뿐. 실무는 private 저장소 권장.

> **결과**: 개발 루프 = **코드 수정 → `git push` → (자동) 배포 → 실패 시 자동 롤백.** 사람은 push만.

---

## 💥 실습 중 만난 함정 (트러블슈팅)

| 증상 | 원인 | 해결 |
|---|---|---|
| `wsl` 명령 not found | **이미 WSL 안**에서 `wsl`을 또 침 | 안에선 바로 명령 입력 |
| PowerShell이 `\|`·따옴표 깨뜨림 | `wsl -- bash -lc "..."` 중첩 인용 | 스크립트 파일로 만들어 실행 |
| `sudo`가 비번 요구 → 자동화 멈춤 | dylan 무인증 sudo 아님 | 변경은 사람이, 검증은 비-sudo로 |
| `shared/` `total 0`인데 파일 있음 | `cp -p`라 타임스탬프 옛날 + 표시 타이밍 | `ls -l 파일`로 직접 확인 |
| `readlink -f`가 없는 파일도 경로 출력 | 링크 대상 존재여부 ≠ 경로 해소 | 실제 파일 `ls`로 확인 |
| nginx `/api/health`가 301 | §5의 80→443 리다이렉트 | `curl -k https://...` |
| `pgrep \| wc -l`이 +1 | pgrep이 자기 bash 줄도 매칭 | master+worker 수로 판단 |

---

## 🧠 복습 셀프 퀴즈 (답은 위 본문에)

1. 워커를 12개로 안 하고 3개로 한 이유는? (힌트: `워커수 × 풀크기` vs `___`)
2. `reload`(HUP)는 왜 무중단인데 `restart`는 짧은 갭이 생기나? (마스터/소켓 관점)
3. `current` 심볼릭링크만 바꾸고 `reload`하면 왜 새 코드가 안 먹히나? → 어떻게 해결?
4. 릴리스 디렉터리 이름에 SHA를 넣어 얻는 것 3가지?
5. `.env`가 git에 안 올라가게 막은 건 무엇이고, 안 올라간 걸 어떻게 확인했나?
6. deploy.sh가 헬스체크에 실패하면 일어나는 일을 순서대로?
7. `git archive`를 쓰는 이유는? (`cp -r`/`git clone` 대비)
8. sudoers에 `restart`·`reload`만 NOPASSWD로 넣은 이유는?

---

## 📋 최종 상태 & 자주 쓸 명령

```bash
# 배포 (main 최신 / 특정 ref)
sudo -u app /srv/notes/deploy.sh
sudo -u app /srv/notes/deploy.sh <커밋|브랜치|태그>

# 무중단 reload (코드 경로 안 바뀔 때 = 설정/env만)
sudo systemctl reload notes-api

# 수동 롤백 (직전 릴리스로)
PREV=$(ls -1dt /srv/notes/releases/*/ | sed -n 2p)
sudo -u app bash -c "ln -sfn '$PREV' /srv/notes/current.tmp && mv -T /srv/notes/current.tmp /srv/notes/current"
sudo systemctl restart notes-api

# 상태/이력
systemctl is-active notes-api ; pgrep -af gunicorn | grep main:app
curl -s http://127.0.0.1:8000/health           # version 확인
ls -1dt /srv/notes/releases/*/                  # 릴리스 이력(이름에 SHA)
readlink /srv/notes/current                     # 지금 라이브 릴리스
```

**개발→배포 루프(앞으로)**: 코드 수정 → `git push` → `sudo -u app /srv/notes/deploy.sh` → (문제 시 자동 롤백). 끝.
