#!/usr/bin/env bash
# Postgres 정기 백업 (서버 /srv/notes/backup.sh 로 배치, 소유=app, chmod +x).
# notes-backup.timer 가 매일 트리거. 로컬 + 오프사이트(Windows /mnt/c) 2벌 + .env 백업.
# 자세한 설명: docs/wsl-pattern-a-deploy.md §16
set -euo pipefail
APP=/srv/notes
KEEP=7
OFFSITE=/mnt/c/Users/ocean/notes-backups     # Windows측(WSL 디스크와 분리) = 오프사이트
set -a; . "$APP/shared/.env"; set +a
mkdir -p "$APP/backups"
TS=$(date +%Y%m%d-%H%M%S)
OUT="$APP/backups/notes-$TS.sql.gz"
pg_dump "$DATABASE_URL" | gzip > "$OUT"        # 스키마+데이터+alembic_version
echo "backup: $OUT ($(du -h "$OUT" | cut -f1))"

# 오프사이트 사본 (best-effort: 실패해도 로컬 백업은 유지)
if mkdir -p "$OFFSITE" 2>/dev/null && cp "$OUT" "$OFFSITE/" 2>/dev/null; then
  cp "$APP/shared/.env" "$OFFSITE/.env.bak" 2>/dev/null || true   # ⚠️ 시크릿: 실무는 gpg/age 암호화
  ls -1t "$OFFSITE"/notes-*.sql.gz 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
  echo "offsite: $OFFSITE  (.env.bak 포함)"
else
  echo "WARN: 오프사이트($OFFSITE) 사용 불가 — 로컬만 수행" >&2
fi

ls -1t "$APP/backups"/notes-*.sql.gz | tail -n +$((KEEP+1)) | xargs -r rm -f
