#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/okivivi"
. .venv/bin/activate
python3 worker/prompt_ui_server.py --host 127.0.0.1 --port 8765 >/tmp/okivivi-prompt-ui-test.log 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT
sleep 2
python3 - <<'PY'
import urllib.request
html = urllib.request.urlopen("http://127.0.0.1:8765", timeout=5).read().decode("utf-8")
checks = {
    "script_duration": "视频文案时长" in html,
    "character_mode": "角色选择" in html,
    "model_tabs": "seedance2.0fast_vip" in html and "调用模型" in html,
    "brief_label": "备注内容" in html,
    "old_image_choice_removed": "Blue 两张" not in html,
}
print(checks)
if not all(checks.values()):
    raise SystemExit(1)
PY
