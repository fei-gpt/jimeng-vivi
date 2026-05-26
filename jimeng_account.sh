#!/usr/bin/env bash
set -euo pipefail

ACCOUNT_ROOT="${JIMENG_ACCOUNT_ROOT:-$HOME/okivivi/accounts/jimeng}"
CURRENT_FILE="$ACCOUNT_ROOT/current"

candidate_paths=(
  "$HOME/.dreamina_cli"
  "$HOME/.config/dreamina"
  "$HOME/.config/dreamina_cli"
  "$HOME/.cache/dreamina"
  "$HOME/.local/share/dreamina"
)

usage() {
  cat <<'EOF'
Usage:
  jimeng_account.sh list
  jimeng_account.sh current
  jimeng_account.sh save ACCOUNT_NAME
  jimeng_account.sh use ACCOUNT_NAME
  jimeng_account.sh clear
  jimeng_account.sh checklogin [DEVICE_CODE]

Typical flow:
  1. dreamina login --headless
  2. finish authorization in browser
  3. jimeng_account.sh checklogin DEVICE_CODE
  4. jimeng_account.sh save account_a
  5. jimeng_account.sh use account_a
EOF
}

profile_dir() {
  local name="$1"
  if [[ ! "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "[ERROR] Invalid account name: $name" >&2
    exit 2
  fi
  echo "$ACCOUNT_ROOT/$name"
}

rel_name() {
  local path="$1"
  echo "${path#$HOME/}"
}

save_profile() {
  local name="$1"
  local target
  target="$(profile_dir "$name")"
  mkdir -p "$target"
  rm -rf "$target/home"
  mkdir -p "$target/home"

  local saved=0
  for path in "${candidate_paths[@]}"; do
    if [ -e "$path" ]; then
      local rel
      rel="$(rel_name "$path")"
      mkdir -p "$target/home/$(dirname "$rel")"
      cp -a "$path" "$target/home/$rel"
      saved=$((saved + 1))
      echo "[save] $path"
    fi
  done

  if [ "$saved" -eq 0 ]; then
    echo "[WARN] No Dreamina credential/config paths found. Is this account logged in?"
  fi
  date -Is > "$target/saved_at"
  echo "$name" > "$CURRENT_FILE"
  echo "[OK] saved Jimeng account profile: $name"
}

backup_active() {
  mkdir -p "$ACCOUNT_ROOT"
  local backup="$ACCOUNT_ROOT/_last_active"
  rm -rf "$backup/home"
  mkdir -p "$backup/home"
  for path in "${candidate_paths[@]}"; do
    if [ -e "$path" ]; then
      local rel
      rel="$(rel_name "$path")"
      mkdir -p "$backup/home/$(dirname "$rel")"
      cp -a "$path" "$backup/home/$rel"
    fi
  done
  date -Is > "$backup/saved_at"
}

clear_active() {
  for path in "${candidate_paths[@]}"; do
    rm -rf "$path"
  done
  echo "[OK] cleared active Jimeng login/config paths"
}

use_profile() {
  local name="$1"
  local source
  source="$(profile_dir "$name")"
  if [ ! -d "$source/home" ]; then
    echo "[ERROR] Jimeng account profile not found: $name" >&2
    exit 3
  fi
  if [ -f "$CURRENT_FILE" ] && [ "$(cat "$CURRENT_FILE")" = "$name" ]; then
    for path in "${candidate_paths[@]}"; do
      if [ -e "$path" ]; then
        echo "[OK] Jimeng account already active: $name"
        return
      fi
    done
  fi

  backup_active
  clear_active

  cp -a "$source/home/." "$HOME/"
  echo "[use] restored files from $source/home"

  echo "$name" > "$CURRENT_FILE"
  echo "[OK] switched Jimeng account profile: $name"
}

list_profiles() {
  mkdir -p "$ACCOUNT_ROOT"
  find "$ACCOUNT_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | grep -v '^_last_active$' | sort || true
}

current_profile() {
  if [ -f "$CURRENT_FILE" ]; then
    cat "$CURRENT_FILE"
  else
    echo "(none)"
  fi
}

check_login() {
  local device_code="${1:-}"
  if [ -n "$device_code" ]; then
    dreamina login checklogin --device_code="$device_code" --poll=30
  else
    dreamina user_credit
  fi
}

main() {
  mkdir -p "$ACCOUNT_ROOT"
  local action="${1:-}"
  case "$action" in
    list) list_profiles ;;
    current) current_profile ;;
    save) [ $# -ge 2 ] || { usage; exit 1; }; save_profile "$2" ;;
    use) [ $# -ge 2 ] || { usage; exit 1; }; use_profile "$2" ;;
    clear) backup_active; clear_active; echo "(none)" > "$CURRENT_FILE" ;;
    checklogin) check_login "${2:-}" ;;
    ""|-h|--help|help) usage ;;
    *) echo "[ERROR] Unknown action: $action" >&2; usage; exit 1 ;;
  esac
}

main "$@"
