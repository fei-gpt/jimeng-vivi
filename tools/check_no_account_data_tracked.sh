#!/usr/bin/env bash
set -euo pipefail

tracked="$(git ls-files)"
pattern='(^|/)(accounts|tenants|outputs|script_requests)/|(^|/)logs/[^/]+$|(^|/)users\.json$|(^|/)\.env$|(^|/)bitable_state\.json$|(^|/)worker_instance\.json$|(^|/)tasks/[^/]+/[^/]+$|(^|/)prompts/(generated|manual|bitable)/'

matches="$(printf '%s\n' "$tracked" | grep -E "$pattern" | grep -vE '(^|/)(logs|outputs|script_requests)/\.gitkeep$|(^|/)tasks/[^/]+/\.gitkeep$' || true)"
if [ -n "$matches" ]; then
  printf 'Tracked sensitive runtime paths found:\n%s\n' "$matches" >&2
  exit 1
fi

printf 'OK: no account, tenant, queue, log, output, or env runtime data is tracked.\n'
