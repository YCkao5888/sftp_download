#!/usr/bin/env bash
# 船上更新：遍歷 config/ 內所有設定檔並依序執行 SFTP 下載
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

# sftp_download 專屬 venv 的 Python（離線部署由 deploy/deploy_offline.sh 建立）
VENV_PY="${SFTP_DOWNLOAD_VENV:-$HOME/venv/wanhai_nssms/share/sftp_download}/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    echo "找不到 sftp_download 專屬 venv 的 Python: $VENV_PY" >&2
    echo "請先執行 deploy/deploy_offline.sh 建立 venv。" >&2
    exit 1
fi

"$VENV_PY" "$BASE_DIR/run_all_downloads.py" "$@"
