#!/usr/bin/env bash
# ufw 방화벽 설정 — 외부는 80/443/22만, 나머지 인바운드 차단.
#
# ⚠️ WSL2 mirrored 네트워킹 주의:
#   mirrored 모드에선 127.0.0.1 트래픽이 'lo' 인터페이스를 안 타서, ufw 기본
#   loopback 허용(-i lo)에 안 걸리고 default-deny에 잡혀 내부통신(nginx→gunicorn,
#   gunicorn→pg)이 끊긴다(504). → 'from 127.0.0.0/8' (출발지 기준)로 우회 허용.
#   진짜 VPS에선 이 줄 없이도 동작(거긴 lo가 정상).
#
# 실행: sudo bash setup-ufw.sh
set -euo pipefail
ufw default deny incoming
ufw default allow outgoing          # 러너 outbound·apt 유지
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 22/tcp                    # SSH (실서버 대비)
ufw allow from 127.0.0.0/8          # WSL mirrored 루프백 우회 (VPS면 생략 가능)
ufw --force enable
ufw status verbose
