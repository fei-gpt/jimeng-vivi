#!/usr/bin/env bash
set -euo pipefail

GH_BIN="${GH_BIN:-/tmp/gh_2.92.0_linux_amd64/bin/gh}"
cd "$(dirname "$0")/.."

export GITHUB_TOKEN="$("$GH_BIN" auth token)"
python3 tools/github_api_upload.py
