#!/usr/bin/env bash
# 船上更新：遍歷 config/ 內所有設定檔並依序執行 SFTP 下載
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

python "$BASE_DIR/run_all_downloads.py" "$@"
