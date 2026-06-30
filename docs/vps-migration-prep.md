# 실제 VPS 이식 준비 가이드 (졸업 체크리스트)

> 목표: WSL 랩(패턴 A)을 **실제 always-on VPS**로 옮긴다. 지금까지 만든 것 대부분이 **그대로** 이식되고,
> WSL 전용 우회를 걷어내고 **진짜 도메인 + Let's Encrypt TLS + SSH 하드닝**을 더하는 게 차이.
> 레퍼런스: [wsl-pattern-a-deploy.md](wsl-pattern-a-deploy.md) §10~24, 적용 설정은 [`ops/`](../ops/).
>
> ⚠️ 아직 **이식 전**. 이 문서는 "나중에 이대로 하면 됨" 준비 체크리스트. 실제 실행은 별도 세션에서.

---

## 0. 사전 준비물 (시작 전 모아둘 것)
- [ ] **VPS** 1대 — Ubuntu LTS, 1~2 vCPU / 1~2GB RAM면 토이앱 충분. (예: Hetzner, DigitalOcean, Vultr, Oracle Free 등)
- [ ] **도메인** 1개 (예: `notes.example.com`) — Let's Encrypt 신뢰 인증서 발급에 필요.
- [ ] **SSH 키페어** — 비밀번호 로그인 대신 키 인증(하드닝).
- [ ] **GitHub 저장소 접근** — 이미 있음(`UltraVic/server-network-lab`, public이라 서버가 인증 없이 fetch).
- [ ] **시크릿 값** — `JWT_SECRET`(새로 생성 권장), DB 비밀번호. (WSL `shared/.env` 또는 오프사이트 `.env.bak` 참고)
- [ ] (선택) **오프사이트 백업 대상** — S3/객체스토리지 또는 다른 호스트(rsync). `/mnt/c`는 VPS에 없음.

---

## 1. 그대로 이식되는 것 (재사용 자산)
거의 수정 없이 옮겨감 — 이게 이 랩의 핵심 가치.

| 자산 | 위치 | 비고 |
|---|---|---|
| systemd 유닛 | `ops/systemd/notes-api.service` 등 | **그대로** (경로 동일 `/srv/notes/current`) |
| 배포 스크립트 | `scripts/deploy.sh` → `/srv/notes/deploy.sh` | 그대로 (git fetch→archive→migrate→flip→헬스체크→롤백) |
| 백업 | `scripts/backup.sh` + `ops/systemd/notes-backup.*` | 오프사이트 경로만 교체(§2) |
| 헬스프로브·알림 | `scripts/healthprobe.sh` + timer + ntfy | 그대로 (ntfy는 outbound라 VPS도 동일) |
| DB 마이그레이션 | `backend/alembic.ini`, `migrations/` | 그대로 |
| 앱 메트릭 | `/metrics` + gunicorn 멀티프로세스 | 그대로 |
| Prometheus·Grafana | `ops/prometheus/`, `ops/grafana/` | 그대로 (접근만 SSH 터널/내부, §3) |
| CI 러너·워크플로 | `.github/workflows/deploy.yml`, self-hosted 러너 | 러너를 VPS에 재등록만 |

---

## 2. WSL 전용 → 걷어내거나 바꿀 것
| WSL에서 했던 것 | VPS에서는 |
|---|---|
| `.wslconfig` `networkingMode=mirrored` | **불필요** (실제 NIC) |
| ufw `allow from 127.0.0.0/8` 루프백 우회 | **삭제** — VPS는 `lo`가 정상 작동 (`ops/ufw/setup-ufw.sh`에서 그 줄 제거) |
| Windows 방화벽이 외부 1차 관문(§7) | **ufw가 진짜 관문** — 우회 없이 그대로 작동 |
| WSL idle-sleep(§3-2, 창 열어두기) | **없음** — always-on 서버 |
| 오프사이트 백업 = `/mnt/c` | **진짜 오프사이트** — S3/rsync/다른 호스트 (`backup.sh`의 `OFFSITE` 교체) |
| 자체서명 인증서(`localhost.crt`, §5) | **Let's Encrypt** 실인증서 (§3) |
| `nginx server_name _` | **실제 도메인** (`server_name notes.example.com`) |
| Prometheus/Grafana 로컬 접근 | **SSH 터널** 또는 nginx 인증 뒤 (공개 금지) |

---

## 3. VPS에서 새로/달라지는 것
### 3-1. SSH 하드닝 (제일 먼저, 잠기지 않게 주의)
- 키 인증만, `PasswordAuthentication no`, `PermitRootLogin no`, 가능하면 비표준 포트.
- **ufw에서 SSH 포트(22 또는 변경포트) 먼저 허용** 후 enable — 안 그러면 자기 자신을 잠근다.

### 3-2. ufw (진짜 방화벽)
```bash
# ops/ufw/setup-ufw.sh 에서 'allow from 127.0.0.0/8' 줄만 빼고 그대로
ufw default deny incoming; ufw default allow outgoing
ufw allow 22/tcp        # ← SSH 먼저! (잠김 방지)
ufw allow 80/tcp; ufw allow 443/tcp
ufw --force enable
```
- 9090(Prometheus)·3000(Grafana)은 **열지 않는다** → SSH 터널로 접근(`ssh -L 3000:127.0.0.1:3000 ...`).

### 3-3. 도메인 + Let's Encrypt (자체서명 대체)
- DNS A 레코드: `notes.example.com → VPS 공인 IP`.
- `sudo apt install certbot python3-certbot-nginx` → `sudo certbot --nginx -d notes.example.com`.
- nginx `server_name _` → `notes.example.com`, cert 경로는 certbot이 관리(자동 갱신 타이머 포함). 자체서명 §5 블록 대체.
- 결과: 브라우저 경고 없는 진짜 자물쇠 🔒 + 자동 갱신.

### 3-4. 시크릿 이전
- VPS `/srv/notes/shared/.env` 새로 작성(JWT_SECRET 재생성 권장). DB 비번도 새로.
- ntfy 토픽은 `monitor.env` 재작성.

---

## 4. 이식 순서 (단계별 — 대부분 §2~24 그대로 재실행)
1. [ ] VPS 프로비저닝 + SSH 하드닝(§3-1) + ufw에 SSH 먼저 허용
2. [ ] 패키지 설치: nginx, python3-venv, postgresql, git, prometheus, grafana (§2-1, §16, §22-3, §24)
3. [ ] `app` 시스템 유저 + `/srv/notes` 구조(releases/shared/repo.git) 생성 (§2-1, §11)
4. [ ] Postgres 역할·DB + `.env` 작성(시크릿) (§2-3~4, §3-4)
5. [ ] `git clone --bare`로 repo.git 캐시 (§11-C)
6. [ ] systemd 유닛 배치(`ops/systemd/*`) — notes-api(하드닝 포함)·backup·healthprobe (§10,17,16,22)
7. [ ] sudoers(app NOPASSWD systemctl) (§12-A)
8. [ ] `deploy.sh`·`backup.sh`·`healthprobe.sh` 배치 + 첫 `deploy.sh` 실행(릴리스 생성·migrate) (§12, §15)
9. [ ] nginx 설정(`ops/nginx/notes.conf`) → **server_name 도메인** + **certbot TLS**(§3-3), `/api/metrics` 차단
10. [ ] ufw 활성(§3-2, 루프백 우회 줄 제거)
11. [ ] Prometheus·Grafana 프로비저닝(`ops/prometheus`, `ops/grafana`) + admin 비번 (§22-3, §24)
12. [ ] self-hosted 러너 재등록(`app` 유저) + 워크플로는 그대로 (§14)
13. [ ] 오프사이트 백업 대상 교체(S3/rsync) (§2)
14. [ ] ntfy 토픽 재설정 (§22)

---

## 5. 이식 후 검증 체크리스트
- [ ] `https://notes.example.com` 진짜 인증서(경고 없음), `/api/health` = `{db:ok}`
- [ ] `git push` → 러너 test→deploy → 새 릴리스 배포(무중단), 자동 롤백 동작
- [ ] `systemctl list-timers` — backup·healthprobe 등록
- [ ] DB 다운 드릴 → 🚨 ntfy 푸시, 복구 → ✅
- [ ] 복원 드릴(임시 DB) 성공, 오프사이트 사본 생성 확인
- [ ] Prometheus 타깃 up, Grafana 대시보드 데이터 (SSH 터널로 확인)
- [ ] `systemd-analyze security notes-api` 양호, ufw 활성(SSH 안 잠김)
- [ ] 재부팅 후 모든 서비스 자동 기동(enable 확인)

---

## 6. 컷오버 & 안전
- **DNS 컷오버**: 도메인을 VPS IP로 전환(TTL 낮춰두면 빠름). WSL은 그대로 두고 VPS 검증 후 전환.
- **롤백**: 문제 시 DNS를 되돌리거나, VPS에서 `deploy.sh`로 이전 릴리스 flip.
- **데이터 이전**: 운영 데이터가 있으면 WSL `pg_dump` → VPS `psql` 복원(복원 드릴과 동일 절차).
- **WSL 보존**: 이식 성공·안정화 전까지 WSL 랩 그대로 유지(롤백 안전망).

> **한 줄 요약**: VPS 이식 = (지금 자산 그대로) + (WSL 우회 제거) + (도메인·Let's Encrypt·SSH 하드닝 추가). 랩에서 만든 모든 근육이 실서버에서 그대로 작동한다 — 그게 이 랩의 목적이었다. 🎓
