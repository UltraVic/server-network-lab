#!/usr/bin/env bash
# 합성(synthetic) 업타임 프로브 (서버 /srv/notes/healthprobe.sh, 소유=app).
# notes-healthprobe.timer 가 1분마다 실행 → /health UP/DOWN·응답시간을 journald 기록,
# DOWN 3연속 시 monitor/alerts.log 에 알림. 자세히: docs §22.
set -uo pipefail
URL=http://127.0.0.1:8000/health
DIR=/srv/notes/monitor; mkdir -p "$DIR"
CF="$DIR/.failcount"; THRESH=3
out=$(curl -s -m 5 -o /dev/null -w '%{http_code} %{time_total}' "$URL" 2>/dev/null) || out="000 0"
code=${out%% *}; t=${out##* }; ts=$(date '+%F %T')
if [ "$code" = "200" ]; then
  echo "UP code=$code t=${t}s"; echo 0 > "$CF"
else
  n=$(( $(cat "$CF" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$CF"
  echo "DOWN code=$code t=${t}s (연속 ${n}회)" >&2
  [ "$n" -ge "$THRESH" ] && echo "[$ts] ALERT: /health DOWN ${n}회 연속 (code=$code)" >> "$DIR/alerts.log"
  exit 1
fi
