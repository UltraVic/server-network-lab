#!/usr/bin/env bash
# 합성(synthetic) 업타임 프로브 (서버 /srv/notes/healthprobe.sh, 소유=app).
# notes-healthprobe.timer 가 1분마다 실행 → /health UP/DOWN·응답시간 journald 기록,
# DOWN 3연속 시 ntfy.sh 푸시(🚨) + alerts.log, 복구 시 푸시(✅). 자세히: docs §22.
#   토픽은 /srv/notes/shared/monitor.env 의 NTFY_TOPIC (repo 미포함, 시크릿).
set -uo pipefail
URL=http://127.0.0.1:8000/health
DIR=/srv/notes/monitor; mkdir -p "$DIR"
CF="$DIR/.failcount"; THRESH=3
[ -f /srv/notes/shared/monitor.env ] && . /srv/notes/shared/monitor.env   # NTFY_TOPIC

notify() {  # title, message, priority, tags
  [ -n "${NTFY_TOPIC:-}" ] || return 0
  curl -s -m 5 -H "Title: $1" -H "Priority: ${3:-default}" -H "Tags: ${4:-}" \
    -d "$2" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
}

out=$(curl -s -m 5 -o /dev/null -w '%{http_code} %{time_total}' "$URL" 2>/dev/null) || out="000 0"
code=${out%% *}; t=${out##* }; ts=$(date '+%F %T')
prev=$(cat "$CF" 2>/dev/null || echo 0)

if [ "$code" = "200" ]; then
  echo 0 > "$CF"
  if [ "$prev" -ge "$THRESH" ]; then
    notify "✅ notes-api 복구" "DOWN ${prev}회 후 정상화 (200, ${t}s)" default white_check_mark
    echo "[$ts] RECOVERED (DOWN ${prev}회 후)" >> "$DIR/alerts.log"
  fi
  echo "UP code=$code t=${t}s"
else
  n=$((prev + 1)); echo "$n" > "$CF"
  echo "DOWN code=$code t=${t}s (연속 ${n}회)" >&2
  if [ "$n" -eq "$THRESH" ]; then
    echo "[$ts] ALERT: /health DOWN ${n}회 연속 (code=$code)" >> "$DIR/alerts.log"
    notify "🚨 notes-api DOWN" "/health ${n}회 연속 실패 (code=$code)" urgent rotating_light
  fi
  exit 1
fi
