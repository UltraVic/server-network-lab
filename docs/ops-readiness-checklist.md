# 패턴 A 운영 준비도 점검표 (gap analysis)

> 2026-06-30 실측 기준. "패턴 A로 앱을 **실제 운영**할 때" 부족한 점. `[x]`=해결/양호, `[~]`=이번 하드닝에서 처리, `[ ]`=미해결.
> 근거 문서: [wsl-pattern-a-deploy.md](wsl-pattern-a-deploy.md) §10~16.

## 🔴 Tier 1 — 운영하면 곧 문제
- [ ] **WSL은 운영 호스트가 아님** — 유휴 시 잠듦·단일 머신·공인 IP 없음. 실서비스는 always-on VPS 필요. (지금 자산은 그대로 VPS로 이식 가능)
- [x] **백업 안전성** — ✅오프사이트(/mnt/c)+복원드릴+.env 백업 완료(§21). 남음: RPO 24h(WAL 아카이빙 미도입), .env 평문(실무는 암호화).
- [x] **리소스 제한** — ✅`MemoryMax=512M`/`MemoryHigh=400M`/`TasksMax=200`(§17).
- [x] **관측(observability)** — ✅업타임 프로브(§22-1)+앱 /metrics(§22-2)+Prometheus 수집(§22-3). 남음: 알림 채널(ntfy/메일/슬랙 푸시 — 현재 로그 기반), 에러 트래킹(Sentry), 대시보드(Grafana), 호스트 메트릭(node_exporter).

## 🟡 Tier 2 — 실서비스 신뢰성
- [x] **헬스체크 심화** — ✅`SELECT 1` readiness + DB 다운 시 503(§20). 검증: stop→503/start→200.
- [x] **CI 테스트 게이트** — ✅pytest(인증/JWT, DB불필요) + `deploy needs test`(§23). 실패=배포차단 검증. 남음: DB 통합테스트·커버리지·린트·스테이징.
- [x] **nginx 보호** — ✅rate-limit(/login 분당10)·타임아웃·`client_max_body_size 1m`·보안헤더(§18).
- [x] **방화벽(ufw)** — ✅80/443/22만 허용+deny(§19). WSL은 `from 127.0.0.0/8` 우회 필요(mirrored), 외부 1차 관문은 Windows 방화벽.
- [ ] **단일 장애점** — 호스트1·DB1, 이중화 없음.
- [ ] **시크릿 관리** — 회전 절차·백업 없음, 평문 .env.

## 🟢 Tier 3 — 위생/개선
- [ ] **deploy.sh 서버 사본 수동 동기** — 드리프트 위험(겪음).
- [ ] **마이그레이션 downgrade 미검증** — 파괴적 변경 시 expand→contract 규칙 미강제.
- [ ] **앱 인증이 토이** — admin/secret 하드코딩·평문(학습용).
- [ ] **디스크 증가 모니터링 없음** — 릴리스(~95M/개)·백업 누적(현재 953G 여유).

## ✅ 이미 양호 (실측)
- [x] 자동 보안 업데이트 unattended-upgrades 설치됨
- [x] 디스크 여유 충분(953G/1TB)
- [x] 백엔드·DB 루프백 바인드(외부 비노출), nginx만 외부
- [x] 무중단 배포·자동롤백·git 원자적 릴리스·CI 자동배포·Alembic·일일 백업

---

## 이번 하드닝 범위 (제안 ②)
1. systemd 샌드박싱 + 리소스 제한 (Tier1 #3)
2. nginx rate-limit·타임아웃·바디크기·보안헤더 (Tier2 nginx)
3. ufw 방화벽 (Tier2 ufw)
4. 헬스체크 심화: DB 쿼리 + 503 (Tier2 #5)
5. 백업 안전성: 복원 드릴 + 오프사이트(Windows측) + .env 백업 (Tier1 #2)

## 다음 후보 (미포함)
- 모니터링/알림(uptime·에러), CI 테스트 게이트, 진짜 HTTPS(도메인+Let's Encrypt), 진짜 0갭(socket activation), 실제 VPS 이식.
