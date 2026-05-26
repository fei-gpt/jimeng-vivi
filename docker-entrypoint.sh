#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/logs /app/tasks/pending /app/tasks/reviewing /app/tasks/running /app/tasks/done /app/tasks/failed \
  /app/outputs /app/script_requests /app/prompts/generated /app/prompts/manual /app/prompts/bitable \
  /app/accounts /app/tenants /app/vivi-image

if ! command -v dreamina >/dev/null 2>&1; then
  echo "[entrypoint] installing Dreamina CLI..."
  curl -fsSL https://jimeng.jianying.com/cli | bash
fi

export PATH="/root/.local/bin:${HOME}/.local/bin:${PATH}"

exec "$@"
