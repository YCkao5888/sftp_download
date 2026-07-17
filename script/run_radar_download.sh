#!/usr/bin/env bash
# 更新 radar
set -euo pipefail

config="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../config/radar_download_settings.json"

if [[ ! -f "$config" ]]; then
    echo "找不到設定檔: $config" >&2
    exit 1
fi

python /home/mic-733ao/Documents/wanhai_nssms/share/sftp_download/main.py --cli --config $config