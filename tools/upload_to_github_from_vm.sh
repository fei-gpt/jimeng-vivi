#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${1:-https://github.com/fei-gpt/jimeng-vivi.git}"
SOURCE="${2:-jimeng-vivi.bundle}"
WORKDIR="${3:-jimeng-vivi-upload}"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required. Install it first:"
  echo "  sudo apt update && sudo apt install -y git"
  exit 1
fi

if [ ! -e "$SOURCE" ]; then
  echo "Source not found: $SOURCE"
  echo "Put jimeng-vivi.bundle or jimeng-vivi-source.zip in this directory, then run again."
  exit 1
fi

rm -rf "$WORKDIR"

case "$SOURCE" in
  *.bundle)
    git clone "$SOURCE" "$WORKDIR"
    ;;
  *.zip)
    if ! command -v unzip >/dev/null 2>&1; then
      echo "unzip is required. Install it first:"
      echo "  sudo apt update && sudo apt install -y unzip"
      exit 1
    fi
    mkdir -p "$WORKDIR"
    unzip -q "$SOURCE" -d "$WORKDIR"
    cd "$WORKDIR"
    git init
    git add .
    git config user.name "${GIT_AUTHOR_NAME:-OKIVIVI}"
    git config user.email "${GIT_AUTHOR_EMAIL:-okivivi@example.local}"
    git commit -m "Initial OKIVIVI video workflow"
    cd ..
    ;;
  *)
    echo "Unsupported source type: $SOURCE"
    echo "Use .bundle or .zip"
    exit 1
    ;;
esac

cd "$WORKDIR"
git branch -M main
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"
git push -u origin main
