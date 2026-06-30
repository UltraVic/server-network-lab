#!/usr/bin/env bash
# 패턴 A 무중단 배포 스크립트 (서버 /srv/notes/deploy.sh 로 배치, 소유=app, chmod +x).
# git fetch → 커밋 추출(archive) → 릴리스 venv → current 심볼릭링크 원자적 flip
#   → 헬스체크 → 실패 시 자동 롤백 → 오래된 릴리스 정리.
# 자세한 설명: docs/wsl-pattern-a-deploy.md §11~12
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
# (선택) DB 마이그레이션 자리 — Alembic 도입 시:
#   "$REL/backend/.venv/bin/alembic" -c "$REL/backend/alembic.ini" upgrade head

echo "▶ [4/6] .env 링크 + current 원자적 flip (rename(2)=atomic)"
ln -sfn "$APP/shared/.env" "$REL/.env"
PREV=$(readlink "$APP/current" 2>/dev/null || true)   # 롤백 대상 기억
ln -sfn "$REL" "$APP/current.tmp"
mv -T "$APP/current.tmp" "$APP/current"

echo "▶ [5/6] restart + 헬스체크"
sudo systemctl restart notes-api          # 링크 flip은 restart로 새 경로 해소
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
