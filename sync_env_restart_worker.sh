#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/okivivi"

echo "[sync] syncing .env and worker files"
cp /mnt/c/Users/aaa/Documents/okivivi/.env "$HOME/okivivi/.env"
cp /mnt/c/Users/aaa/Documents/okivivi/worker/feishu_worker.py "$HOME/okivivi/worker/feishu_worker.py"
cp /mnt/c/Users/aaa/Documents/okivivi/worker/create_task.py "$HOME/okivivi/worker/create_task.py"
cp /mnt/c/Users/aaa/Documents/okivivi/worker/generate_scripts.py "$HOME/okivivi/worker/generate_scripts.py"
cp /mnt/c/Users/aaa/Documents/okivivi/worker/submit_script_request.py "$HOME/okivivi/worker/submit_script_request.py"
cp /mnt/c/Users/aaa/Documents/okivivi/jimeng_account.sh "$HOME/okivivi/jimeng_account.sh"
chmod +x "$HOME/okivivi/jimeng_account.sh"

mkdir -p "$HOME/okivivi/vivi-image" "$HOME/okivivi/logs"
find /mnt/c/Users/aaa/Documents/okivivi/vivi-image -maxdepth 1 -type f -print0 2>/dev/null \
  | xargs -0 -r cp -f -t "$HOME/okivivi/vivi-image/"

echo "[sync] stopping old worker if any"
ps -ef | grep '[f]eishu_worker.py' | awk '{print $2}' | xargs -r kill || true
sleep 1

echo "[sync] starting worker"
. "$HOME/okivivi/.venv/bin/activate"
nohup python3 -u worker/feishu_worker.py > "$HOME/okivivi/logs/feishu_worker_bg.log" 2>&1 &
sleep 5

echo "[sync] env check"
grep -E 'DEEPSEEK_API_KEY|SCRIPT_AGENT_DOC|DEFAULT_JIMENG_ACCOUNT' "$HOME/okivivi/.env" | sed 's/DEEPSEEK_API_KEY=.*/DEEPSEEK_API_KEY=<set>/'

echo "[sync] worker log tail"
tail -120 "$HOME/okivivi/logs/feishu_worker_bg.log"
