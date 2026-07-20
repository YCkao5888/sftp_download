#!/usr/bin/env bash
set -euo pipefail

# install_virtualenv_offline — 由 radar-shm-install 安裝服務管理(已與 radar 分離)
#
# 在主環境(非虛擬環境)離線安裝 virtualenv 套件,供後續 radar 與 SHM-stream-manager
# 各自的 install_env.sh 以 `python3.10 -m virtualenv` 建立虛擬環境使用。
# 因 IPC1(radar+SHM)與 IPC2(僅 SHM)都需要 virtualenv,故本能力改由本安裝服務提供,
# IPC2 不再需要為了取得 virtualenv_wheels 而一併下載 radar。
#
# wheels 來源優先序: VENV_WHEELS_DIR 指定 → 腳本同層 virtualenv_wheels/ → 當前目錄 ./virtualenv_wheels

# 腳本自身所在目錄(無論從何處呼叫,皆能定位到隨附的 virtualenv_wheels)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
WHEELS_DIR="${VENV_WHEELS_DIR:-$SCRIPT_DIR/virtualenv_wheels}"

echo "================================================="
echo "   開始在主環境中安裝 virtualenv (離線模式)      "
echo "================================================="

# 目標解譯器: 後續 radar 與 SHM-stream-manager 的 install_env.sh 都以
# `python3.10 -m virtualenv` 建環境,因此 virtualenv 必須裝到 python3.10。
# 僅在系統沒有 python3.10 時才退回 python3(此時下游 install_env.sh 也會失敗,僅作最後嘗試)。
if command -v python3.10 >/dev/null 2>&1; then
    PY=python3.10
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
    echo "警告: 找不到 python3.10,改用 python3;若下游 install_env.sh 報 No module named virtualenv,代表 virtualenv 裝到了非 python3.10 的解譯器。"
else
    echo "錯誤: 找不到 python3.10 或 python3，請先確認系統已安裝 Python 3.10。"
    exit 1
fi
echo "目標 Python: $("$PY" --version 2>&1) ($PY)"

# 檢查 PIP 是否存在
if ! "$PY" -m pip --version >/dev/null 2>&1; then
    echo "警告: 找不到 $PY 的 pip 模組。"
    echo "嘗試使用 $PY -m ensurepip 建立 pip..."
    "$PY" -m ensurepip --default-pip || true
fi

# 檢查 virtualenv_wheels 資料夾是否存在(同層找不到再退而找當前目錄)
if [ ! -d "$WHEELS_DIR" ]; then
    if [ -d "./virtualenv_wheels" ]; then
        WHEELS_DIR="./virtualenv_wheels"
    else
        echo "錯誤: 找不到 $WHEELS_DIR 或是當前目錄下的 virtualenv_wheels。"
        echo "請確保本安裝服務(radar-shm-install)隨附的 virtualenv_wheels 資料夾完整。"
        exit 1
    fi
fi

echo "使用 wheels 目錄: $WHEELS_DIR"

# 安裝 virtualenv(裝到上面選定的 $PY,確保與下游 python3.10 -m virtualenv 一致)
"$PY" -m pip install \
    --user \
    --no-index \
    --find-links="$WHEELS_DIR" \
    virtualenv

echo "================================================="
echo "virtualenv 安裝完成！"
echo "您現在可以繼續執行各專案的 install_env.sh 進行後續環境建置。"
echo "================================================="
