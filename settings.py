"""共用設定檔（settings.json）讀取/開啟工具，CLI 與 GUI 皆透過此模組載入預設參數。"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

# 船舶基本資訊檔（各船部署時放置），內容如 {"vsl_name": "WH289", "ipc": "IPC-1"}。
# 設定檔字串值中的 {vsl_name}、{ipc} 等佔位符會以此檔案的對應值替換。
# 可用環境變數 VESSEL_INFO_PATH 覆蓋路徑（測試或特殊部署用）。
VESSEL_INFO_PATH = Path(__file__).resolve().parent.parent / ".env" / "vessel_basic_info.json"

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


class PlaceholderError(ValueError):
    """設定檔中的佔位符無法解析（vessel 資訊檔不存在、壞掉或缺少對應 key）。"""

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
    "recursive": True,
    "ignore_file": "",
    "retry_count": 0,
    "retry_delay": 10,
    "upload_log": False,
    "log_remote_dir": "",
    "log_dir": "logs",
    "duplicate_mode": "overwrite",
    "duplicate_suffix": "copy",
}


def _load_vessel_info():
    path = Path(os.environ.get("VESSEL_INFO_PATH") or VESSEL_INFO_PATH)
    if not path.exists():
        raise PlaceholderError(f"設定檔使用了佔位符，但找不到船舶資訊檔：{path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            info = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise PlaceholderError(f"船舶資訊檔 {path} 讀取失敗：{e}")
    if not isinstance(info, dict):
        raise PlaceholderError(f"船舶資訊檔 {path} 內容必須是 JSON 物件")
    return {key: str(value) for key, value in info.items()}


def resolve_placeholders(settings):
    """把設定值字串中的 {vsl_name}、{ipc} 等佔位符換成 vessel_basic_info.json 的對應值。

    - 處理字串值與字串陣列（如 remote_path 的路徑陣列）內的每個元素，其他型別原樣保留。
    - 完全沒有佔位符時不會去讀船舶資訊檔（該檔可以不存在）。
    - 佔位符無法解析（檔案不存在／缺少 key）時拋出 PlaceholderError，
      避免把 "{vsl_name}" 這種字面文字當成路徑上傳到伺服器。
    """
    vessel_info = None

    def resolve_text(field, value):
        nonlocal vessel_info
        for name in _PLACEHOLDER.findall(value):
            if vessel_info is None:
                vessel_info = _load_vessel_info()
            if name not in vessel_info:
                raise PlaceholderError(
                    f"設定檔欄位 {field} 的佔位符 {{{name}}} 在船舶資訊檔中找不到對應值"
                    f"（可用的 key：{', '.join(sorted(vessel_info)) or '（無）'}）"
                )
        if vessel_info:
            value = _PLACEHOLDER.sub(lambda m: vessel_info[m.group(1)], value)
        return value

    resolved = {}
    for field, value in settings.items():
        if isinstance(value, str):
            value = resolve_text(field, value)
        elif isinstance(value, list):
            value = [resolve_text(field, item) if isinstance(item, str) else item for item in value]
        resolved[field] = value
    return resolved


def load_settings(path=SETTINGS_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"警告：設定檔 {path} 讀取失敗，將忽略此檔案：{e}", file=sys.stderr)
        return {}
    return resolve_placeholders(data)


def save_settings(path, data):
    """把設定內容寫成 JSON 檔（覆蓋既有內容），回傳檔案路徑。"""
    path = Path(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


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
