#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FIFTEEN_SOURCE="${1:-/Users/mac/Desktop/OKIVIVI-DeepSeek多层提示链生成规则版-创作松动测试版.md}"
SHORT_SOURCE="${2:-/Users/mac/Desktop/OKIVIVI-6s强冲突开头设计规则.md}"

FIFTEEN_TARGET="$ROOT/deepseek/OKIVIVI-feishu-current.md"
SHORT_TARGET="$ROOT/deepseek/OKIVIVI-6s-current.md"

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "missing source file: $path" >&2
    exit 1
  fi
}

require_file "$FIFTEEN_SOURCE"
require_file "$SHORT_SOURCE"

rm -f \
  "$FIFTEEN_TARGET" \
  "$SHORT_TARGET" \
  "$ROOT/deepseek/OKIVIVI-text-zong.md" \
  "$ROOT/deepseek/OKIVIVI-text-assistive.md" \
  "$ROOT/deepseek/OKIVIVI-创作松动版一致性规则提取.md" \
  "$ROOT/OKIVIVI-创作松动版一致性规则提取.md" \
  "$ROOT/script_agent.md" \
  "$ROOT/.tmp_okivivi_script_checker_SKILL.md" \
  "$ROOT/check_okivivi_script.py" \
  "$ROOT/worker/check_okivivi_script.py" \
  "$ROOT/worker/okivivi-script-checker-SKILL.md" \
  "$ROOT/worker/OKIVIVI-6s-current.md"

rm -f \
  "$ROOT/worker/__pycache__/check_okivivi_script.cpython-310.pyc" \
  "$ROOT/worker/__pycache__/check_okivivi_script.cpython-312.pyc"

mkdir -p "$ROOT/deepseek"
cp "$FIFTEEN_SOURCE" "$FIFTEEN_TARGET"
cp "$SHORT_SOURCE" "$SHORT_TARGET"

echo "replaced:"
sha256sum "$FIFTEEN_TARGET" "$SHORT_TARGET"
