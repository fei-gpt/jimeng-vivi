#!/usr/bin/env bash
set -euo pipefail

ROOT="${OKIVIVI_ROOT:-$HOME/okivivi}"
ENV_FILE="$ROOT/.env"

load_env() {
  if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
  fi
}

load_env

SOURCE_DIR="${SERVER_IMAGE_SOURCE_DIR:-$ROOT/vivi-image}"
REMOTE_DIR="${SERVER_IMAGE_REMOTE_DIR:-}"
HOST="${SERVER_SSH_HOST:-}"
USER="${SERVER_SSH_USER:-}"
PORT="${SERVER_SSH_PORT:-22}"
KEY="${SERVER_SSH_KEY:-}"

if [ -z "$HOST" ] || [ -z "$USER" ] || [ -z "$REMOTE_DIR" ]; then
  echo "[ERROR] Please configure SERVER_SSH_HOST, SERVER_SSH_USER, and SERVER_IMAGE_REMOTE_DIR in .env"
  exit 2
fi

if [ ! -d "$SOURCE_DIR" ]; then
  echo "[ERROR] Source image directory does not exist: $SOURCE_DIR"
  exit 3
fi

SSH_OPTS=(-p "$PORT")
SCP_OPTS=(-P "$PORT")
if [ -n "$KEY" ]; then
  SSH_OPTS+=(-i "$KEY")
  SCP_OPTS+=(-i "$KEY")
fi

TARGET="$USER@$HOST"
echo "[upload] source: $SOURCE_DIR"
echo "[upload] target: $TARGET:$REMOTE_DIR"

ssh "${SSH_OPTS[@]}" "$TARGET" "mkdir -p '$REMOTE_DIR'"
scp "${SCP_OPTS[@]}" "$SOURCE_DIR"/* "$TARGET:$REMOTE_DIR/"

echo "[upload] done"
