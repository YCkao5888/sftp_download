#!/usr/bin/env bash
# 更新 radar
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
config="$SCRIPT_DIR/../config/radar_download_settings.json"

if [[ ! -f "$config" ]]; then
    echo "找不到設定檔: $config" >&2
    exit 1
fi

# sftp_download 專屬 venv 的 Python（離線部署由 deploy/deploy_offline.sh 建立）
VENV_PY="${SFTP_DOWNLOAD_VENV:-$HOME/venv/wanhai_nssms/share/sftp_download}/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    echo "找不到 sftp_download 專屬 venv 的 Python: $VENV_PY" >&2
    echo "請先執行 deploy/deploy_offline.sh 建立 venv。" >&2
    exit 1
fi

"$VENV_PY" "$BASE_DIR/main.py" --cli --config "$config"