"""共用設定檔（settings.json）讀取/開啟工具，CLI 與 GUI 皆透過此模組載入預設參數。"""

import json
import os
import subprocess
import sys
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

SETTINGS_TEMPLATE = {
    "host": "",
    "port": 22,
    "device_name": "",
    "version_info": "",
    "username": "",
    "password": "",
    "key_file": "",
    "remote_path": "",
    "local_path": "",
    "auto_reconnect": True,
    "resume": True,
    "wait_for_network": True,
    "retry_count": 0,
    "retry_delay": 10,
    "upload_log": False,
    "log_remote_dir": "",
    "log_dir": "logs",
    "duplicate_mode": "duplicate",
    "duplicate_suffix": "copy",
}


def load_settings(path=SETTINGS_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"警告：設定檔 {path} 讀取失敗，將忽略此檔案：{e}", file=sys.stderr)
        return {}


def ensure_settings_file(path=SETTINGS_PATH, seed=None):
    """若設定檔不存在則建立一份（可用目前畫面上的值當作起始內容），回傳檔案路徑。"""
    path = Path(path)
    if not path.exists():
        data = dict(SETTINGS_TEMPLATE)
        if seed:
            data.update({k: v for k, v in seed.items() if v not in (None, "")})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def open_in_default_app(path):
    path = str(path)
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", path])
    else:
        subprocess.run(["xdg-open", path])
