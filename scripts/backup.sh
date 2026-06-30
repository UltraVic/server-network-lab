#!/usr/bin/env bash
# Postgres 정기 백업 (서버 /srv/notes/backup.sh 로 배치, 소유=app, chmod +x).
# notes-backup.timer 가 매일 트리거. 자세한 설명: docs/wsl-pattern-a-deploy.md §16
set -euo pipefail
APP=/srv/notes
KEEP=7                                   # 최근 7개만 보관
set -a; . "$APP/shared/.env"; set +a     # DATABASE_URL 로드
mkdir -p "$APP/backups"
TS=$(date +%Y%m%d-%H%M%S)
OUT="$APP/backups/notes-$TS.sql.gz"
pg_dump "$DATABASE_URL" | gzip > "$OUT"   # 스키마+데이터+alembic_version 전체 덤프
echo "backup: $OUT ($(du -h "$OUT" | cut -f1))"
ls -1t "$APP/backups"/notes-*.sql.gz | tail -n +$((KEEP+1)) | xargs -r rm -f
