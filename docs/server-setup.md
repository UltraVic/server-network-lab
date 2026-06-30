# 서버 세팅 가이드 — 빈 Ubuntu → 전체 스택 (패턴 A)

> 빈 **Ubuntu LTS 서버 한 대**에 이 앱의 실무 스택(무중단 배포·CI·DB 마이그레이션·백업·하드닝·관측)을 처음부터 올리는 **실행 런북**.
> WSL 전용 우회는 제거됨. 설정 파일 실내용은 [`ops/`](../ops/)·[`scripts/`](../scripts/)에 있고, 이 문서는 **순서 + 배치 + 환경별 치환**.
> 개념 설명은 [wsl-pattern-a-deploy.md](wsl-pattern-a-deploy.md) §10~24 참고. WSL→VPS 이전(기존 랩 이동)은 [vps-migration-prep.md](vps-migration-prep.md).

---

## 0. 변수 (먼저 정하고 셸에 export)
```bash
export REPO_URL="https://github.com/UltraVic/server-network-lab.git"
export DOMAIN="notes.example.com"        # 도메인 없으면 비워두고 §9-B(자체서명)로
export DB_PASSWORD="$(openssl rand -base64 24)"
export JWT_SECRET="$(openssl rand -base64 48)"
export OFFSITE_DIR="/var/backups/notes-offsite"   # 진짜 오프사이트면 S3/rsync로 교체(§11)
echo "DB_PASSWORD=$DB_PASSWORD"; echo "JWT_SECRET=$JWT_SECRET"   # 어딘가 안전히 보관
```
> 전제: 앱 유저 `app`, 앱 경로 `/srv/notes`는 `ops/`·`scripts/` 파일에 고정돼 있으니 그대로 쓴다(바꾸려면 그 파일들도 함께 수정).
> 먼저 **SSH 키 인증 + sshd 하드닝**(`PasswordAuthentication no`, `PermitRootLogin no`)을 끝내고 시작 — 그리고 §10 ufw에서 **SSH 포트를 가장 먼저 허용**(자기 잠금 방지).

---

## 1. 베이스 패키지 + 전용 유저
```bash
sudo apt-get update
sudo apt-get install -y nginx python3-venv python3-pip postgresql git curl ufw
sudo useradd --system --create-home --shell /bin/bash app
```

## 2. 앱 디렉터리 구조
```bash
sudo mkdir -p /srv/notes/{releases,shared}
sudo chown -R app:app /srv/notes
```

## 3. Postgres 역할·DB
```bash
sudo -u postgres psql -c "CREATE ROLE lab LOGIN PASSWORD '$DB_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE lab OWNER lab;"
PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U lab -d lab -c "SELECT 1;"   # TCP 로그인 확인
```

## 4. 시크릿 (.env)
```bash
sudo -u app tee /srv/notes/shared/.env >/dev/null <<ENV
DATABASE_URL=postgresql://lab:$DB_PASSWORD@127.0.0.1:5432/lab
JWT_SECRET=$JWT_SECRET
POOL_SIZE=10
ENV
sudo -u app chmod 600 /srv/notes/shared/.env
```

## 5. repo 캐시 (bare clone)
```bash
sudo -u app -H git clone --bare "$REPO_URL" /srv/notes/repo.git
```

## 6. 코드/설정 가져오기 (ops/ 파일 배치용으로 작업트리 1벌)
```bash
sudo -u app -H git clone "$REPO_URL" /tmp/app-src
R=/tmp/app-src           # 이후 cp 소스로 사용
```

## 7. systemd 유닛 (notes-api 하드닝 + backup + healthprobe)
```bash
sudo cp $R/ops/systemd/notes-api.service        /etc/systemd/system/
sudo cp $R/ops/systemd/notes-backup.service     /etc/systemd/system/
sudo cp $R/ops/systemd/notes-backup.timer       /etc/systemd/system/
sudo cp $R/ops/systemd/notes-healthprobe.service /etc/systemd/system/
sudo cp $R/ops/systemd/notes-healthprobe.timer   /etc/systemd/system/
sudo systemctl daemon-reload
```
> `notes-api.service`엔 `PROMETHEUS_MULTIPROC_DIR`·`ProtectHome=tmpfs`·리소스제한·샌드박싱이 이미 포함(§17,22). 그대로 사용.

## 8. sudoers (app 이 서비스만 무인증)
```bash
echo 'app ALL=(root) NOPASSWD: /usr/bin/systemctl restart notes-api, /usr/bin/systemctl reload notes-api' \
  | sudo tee /etc/sudoers.d/notes >/dev/null
sudo chmod 0440 /etc/sudoers.d/notes && sudo visudo -cf /etc/sudoers.d/notes
```

## 9. 배포 스크립트 + 백업 + 프로브 배치, 첫 배포
```bash
sudo cp $R/scripts/deploy.sh $R/scripts/backup.sh $R/scripts/healthprobe.sh /srv/notes/
sudo chown app:app /srv/notes/{deploy.sh,backup.sh,healthprobe.sh}
sudo chmod +x /srv/notes/{deploy.sh,backup.sh,healthprobe.sh}
# 오프사이트 경로 교체 (기본 /mnt/c → 서버용)
sudo -u app sed -i "s#^OFFSITE=.*#OFFSITE=$OFFSITE_DIR#" /srv/notes/backup.sh
sudo install -d -o app -g app "$OFFSITE_DIR"
# 첫 배포: git archive → venv → alembic upgrade → current flip → 헬스체크
sudo -u app /srv/notes/deploy.sh
```
> 첫 배포 후 `notes-api`가 떠야 함: `curl -s 127.0.0.1:8000/health` → `{"db":"ok"}`.
> (deploy.sh가 `systemctl restart notes-api`까지 함. `systemctl enable notes-api`로 부팅 자동기동 등록: `sudo systemctl enable notes-api`)

## 10. nginx + TLS
```bash
sudo cp $R/ops/nginx/notes.conf /etc/nginx/sites-available/notes.conf
sudo ln -sf /etc/nginx/sites-available/notes.conf /etc/nginx/sites-enabled/notes.conf
sudo rm -f /etc/nginx/sites-enabled/default
```
**A. 도메인 있음 (권장, 진짜 인증서):**
```bash
sudo sed -i "s/server_name _;/server_name $DOMAIN;/" /etc/nginx/sites-available/notes.conf
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d "$DOMAIN" --redirect -m you@example.com --agree-tos -n
# certbot 이 cert 경로·갱신 타이머까지 설정. 자체서명 ssl_certificate 줄은 certbot 값으로 대체됨.
sudo nginx -t && sudo systemctl reload nginx
```
**B. 도메인 없음 (자체서명 fallback):**
```bash
sudo mkdir -p /etc/nginx/certs
sudo openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout /etc/nginx/certs/localhost.key -out /etc/nginx/certs/localhost.crt \
  -days 365 -subj "/CN=$(hostname)"
sudo nginx -t && sudo systemctl reload nginx   # notes.conf 의 자체서명 경로 그대로 사용
```

## 11. ufw 방화벽
```bash
sudo bash $R/ops/ufw/setup-ufw.sh        # 80/443/22 허용 + deny
# ⚠️ 일반 서버는 'allow from 127.0.0.0/8' 줄이 불필요(WSL mirrored 전용). 실서버면 그 줄 삭제 권장.
```
> SSH 비표준 포트면 setup-ufw.sh의 22를 그 포트로 바꾼 뒤 실행. **SSH 허용 확인 후** enable.

## 12. 백업 + 헬스프로브 타이머 켜기
```bash
sudo systemctl enable --now notes-backup.timer notes-healthprobe.timer
sudo systemctl start notes-backup.service        # 즉시 1회 백업
# ntfy 푸시 알림 쓰려면:
echo 'NTFY_TOPIC=여기에랜덤토픽' | sudo -u app tee /srv/notes/shared/monitor.env
sudo -u app chmod 600 /srv/notes/shared/monitor.env
```

## 13. 모니터링 (Prometheus + Grafana)
```bash
# Prometheus (apt 에 있으면 apt, 없으면 §22-3 의 바이너리/설명 참고)
sudo apt-get install -y --no-install-recommends prometheus || true
sudo cp $R/ops/prometheus/prometheus.yml /etc/prometheus/prometheus.yml
sudo systemctl restart prometheus

# Grafana (공식 저장소)
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://apt.grafana.com/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list >/dev/null
sudo apt-get update && sudo apt-get install -y grafana
sudo cp $R/ops/grafana/provisioning/datasources/prometheus.yml /etc/grafana/provisioning/datasources/
sudo cp $R/ops/grafana/provisioning/dashboards/notes.yml       /etc/grafana/provisioning/dashboards/
sudo mkdir -p /var/lib/grafana/dashboards
sudo cp $R/ops/grafana/dashboards/notes-api.json /var/lib/grafana/dashboards/
sudo chown -R grafana:grafana /var/lib/grafana/dashboards
sudo systemctl enable --now grafana-server
sudo grafana cli admin reset-admin-password "$(openssl rand -base64 18)"   # 출력 비번 보관
```
> 9090·3000은 **ufw로 외부 차단**(열지 않음). 접근은 SSH 터널: `ssh -L 3000:127.0.0.1:3000 서버`.

## 14. CI 자동배포 (self-hosted 러너)
```bash
# GitHub → 저장소 Settings → Actions → Runners → New self-hosted runner(Linux) 에서 토큰 확보
sudo -u app -H env RV=<버전> TK=<토큰> bash -c '
  mkdir -p /home/app/actions-runner && cd /home/app/actions-runner
  curl -fsSL -o r.tar.gz https://github.com/actions/runner/releases/download/v${RV}/actions-runner-linux-x64-${RV}.tar.gz
  tar xzf r.tar.gz && rm r.tar.gz
  ./config.sh --url '"$REPO_URL"' --token "${TK}" --name srv-app --labels self-hosted --unattended'
sudo bash -c 'cd /home/app/actions-runner && ./svc.sh install app && ./svc.sh start'
```
> 워크플로 `.github/workflows/deploy.yml`(test→deploy)는 저장소에 이미 있음 → 이후 `git push`면 자동 테스트+배포.
> ⚠️ public 저장소+self-hosted는 fork PR 위험 → push(main) 트리거만 사용(이미 그렇게 설정됨). 민감하면 private.

---

## 15. 검증
```bash
curl -s https://$DOMAIN/api/health            # {"db":"ok"} (도메인 없으면 https://서버IP -k)
curl -sk https://localhost/api/metrics        # 404 (외부 차단 확인)
systemctl is-active notes-api nginx postgresql prometheus grafana-server
systemctl list-timers | grep -E 'notes-backup|notes-healthprobe'
sudo systemd-analyze security notes-api | tail -1     # 양호(낮은 점수)
# git push → Actions test→deploy → 새 릴리스, DB다운 드릴 → ntfy 푸시
```

---

## 부록 — 환경마다 다른 곳 (체크)
| 항목 | 어디서 바뀌나 |
|---|---|
| `OFFSITE`(backup.sh) | 서버마다 실제 오프사이트(S3/rsync)로 |
| `server_name` + 인증서 | 도메인 유무(certbot vs 자체서명) |
| ufw `from 127.0.0.0/8` | **WSL mirrored 전용** — 실서버는 삭제 |
| SSH 포트 | 하드닝 시 변경했으면 ufw·sshd 반영 |
| 러너 라벨/이름 | 서버마다 고유하게 |
| 워커 수(`WEB_CONCURRENCY`) | 코어 수·Postgres `max_connections`에 맞춰 |
| 9090/3000 접근 | 절대 공개 금지 — SSH 터널 |

> **요지**: `ops/`·`scripts/`의 파일은 거의 서버 무관(경로 `/srv/notes` 고정). 가이드는 **순서대로 cp + 도메인/오프사이트만 치환**하면 빈 서버가 풀스택으로 선다. WSL이든 VPS든 동일.
