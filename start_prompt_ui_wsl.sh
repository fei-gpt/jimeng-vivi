#!/usr/bin/env bash
set -euo pipefail

PROJECT_WIN="/mnt/c/Users/aaa/Documents/okivivi"
PROJECT_HOME="$HOME/okivivi"

mkdir -p "$PROJECT_HOME/worker" "$PROJECT_HOME/logs"
cp "$PROJECT_WIN/.env" "$PROJECT_HOME/.env"
cp "$PROJECT_WIN/worker/create_task.py" "$PROJECT_HOME/worker/create_task.py"
cp "$PROJECT_WIN/worker/generate_scripts.py" "$PROJECT_HOME/worker/generate_scripts.py"
cp "$PROJECT_WIN/worker/submit_script_request.py" "$PROJECT_HOME/worker/submit_script_request.py"
cp "$PROJECT_WIN/worker/prompt_ui_server.py" "$PROJECT_HOME/worker/prompt_ui_server.py"
cp "$PROJECT_WIN/worker/stop_prompt_ui.py" "$PROJECT_HOME/worker/stop_prompt_ui.py"

cd "$PROJECT_HOME"
. .venv/bin/activate
python3 worker/stop_prompt_ui.py
python3 worker/prompt_ui_server.py --host 127.0.0.1 --port 8765
