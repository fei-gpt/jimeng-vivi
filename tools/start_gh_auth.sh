#!/usr/bin/env bash
set -euo pipefail

GH_BIN="${GH_BIN:-/tmp/gh_2.92.0_linux_amd64/bin/gh}"
LOG="${1:-/tmp/gh-auth.log}"
PID="${2:-/tmp/gh-auth.pid}"

rm -f "$LOG" "$PID" /tmp/gh-auth-input
printf 'Y\n' > /tmp/gh-auth-input

script -q -f "$LOG" -c "$GH_BIN auth login --hostname github.com --git-protocol https --web < /tmp/gh-auth-input" >/tmp/gh-auth.nohup 2>&1 &
echo $! > "$PID"
sleep 5
cat "$LOG" /tmp/gh-auth.nohup 2>/dev/null
