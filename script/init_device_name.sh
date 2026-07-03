#!/usr/bin/env bash
# 首次部署：互動輸入船號與電腦號，更新 config/ 內所有設定檔的 device_name
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

read -rp "請輸入船號: 例如 325, A12 " SHIP_ID
read -rp "請輸入電腦號: 例如 IPC1, IPC2 " PC_ID

if [[ -z "$SHIP_ID" || -z "$PC_ID" ]]; then
    echo "錯誤：船號與電腦號皆不可為空" >&2
    exit 1
fi

python "$BASE_DIR/init_device_name.py" --ship-id "$SHIP_ID" --pc-id "$PC_ID"
