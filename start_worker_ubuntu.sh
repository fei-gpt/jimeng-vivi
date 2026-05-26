#!/usr/bin/env bash
set -u

echo "[ubuntu] entered bash"
cd ~/okivivi || exit 10
echo "[ubuntu] cwd=$(pwd)"

mkdir -p logs worker vivi-image tasks/pending tasks/reviewing tasks/running tasks/done tasks/failed tasks/needs_revision outputs

echo "[ubuntu] syncing worker and env"
cp /mnt/c/Users/aaa/Documents/okivivi/worker/feishu_worker.py ~/okivivi/worker/feishu_worker.py || exit 11
cp /mnt/c/Users/aaa/Documents/okivivi/worker/create_task.py ~/okivivi/worker/create_task.py || exit 11
cp /mnt/c/Users/aaa/Documents/okivivi/worker/generate_scripts.py ~/okivivi/worker/generate_scripts.py || exit 11
cp /mnt/c/Users/aaa/Documents/okivivi/worker/submit_script_request.py ~/okivivi/worker/submit_script_request.py || exit 11
cp /mnt/c/Users/aaa/Documents/okivivi/jimeng_account.sh ~/okivivi/jimeng_account.sh || exit 11
cp /mnt/c/Users/aaa/Documents/okivivi/.env ~/okivivi/.env || exit 12
if [ -f /mnt/c/Users/aaa/Documents/okivivi/users.json ]; then
  cp /mnt/c/Users/aaa/Documents/okivivi/users.json ~/okivivi/users.json || exit 12
fi
cp -f /mnt/c/Users/aaa/Documents/okivivi/vivi-image/* ~/okivivi/vivi-image/ || exit 14
chmod +x ~/okivivi/jimeng_account.sh

if [ ! -f .venv/bin/activate ]; then
  echo "[ubuntu] missing .venv/bin/activate"
  exit 13
fi

echo "[ubuntu] activating venv"
. .venv/bin/activate
export PYTHONUNBUFFERED=1

if pgrep -u "$(id -u)" -f "worker/feishu_worker.py" >/dev/null 2>&1; then
  echo "[ubuntu] feishu worker is already running; not starting a duplicate"
  exit 0
fi

while true; do
  echo "[ubuntu] starting python worker"
  python3 -u worker/feishu_worker.py
  code=$?
  echo "[ubuntu] worker exited with code $code"
  echo "[ubuntu] restarting in 5 seconds. Close this window to stop."
  sleep 5
done
