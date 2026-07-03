"""首次部署 SFTP downloader 時的初始化腳本。

遍歷 config/ 資料夾內所有 *_settings.json（或所有 .json 設定檔），
讓用戶輸入「船號」與「電腦號」，將 device_name 由原本的
"PROJECTNAME" 改為 "船號_電腦號_PROJECTNAME"。

若 device_name 已包含相同的「船號_電腦號_」前綴則跳過，避免重複執行時疊加。

使用方式：
    python init_device_name.py
    python init_device_name.py --ship-id 2C2C --pc-id IPC1   # 免互動，適合自動化
"""

import argparse
import json
import sys
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "config"


def find_setting_files(config_dir: Path):
    """回傳 config 目錄下所有 JSON 設定檔（排序後）。"""
    return sorted(config_dir.glob("*.json"))


def update_device_name(path: Path, ship_id: str, pc_id: str) -> str:
    """更新單一設定檔的 device_name，回傳處理結果訊息。"""
    with open(path, "r", encoding="utf-8") as f:
        settings = json.load(f)

    original = settings.get("device_name", "")
    if not original:
        return f"[跳過] {path.name}：找不到 device_name 欄位"

    prefix = f"{ship_id}_{pc_id}_"
    if original.startswith(prefix):
        return f"[跳過] {path.name}：device_name 已是 {original}"

    settings["device_name"] = f"{prefix}{original}"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return f"[完成] {path.name}：{original} -> {settings['device_name']}"


def main():
    parser = argparse.ArgumentParser(description="首次部署：更新所有設定檔的 device_name")
    parser.add_argument("--ship-id", help="船號，例如 2C2C")
    parser.add_argument("--pc-id", help="電腦號，例如 IPC1")
    parser.add_argument("--config-dir", default=str(CONFIG_DIR), help="設定檔資料夾（預設 ./config）")
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    files = find_setting_files(config_dir)
    if not files:
        print(f"錯誤：{config_dir} 內找不到任何 JSON 設定檔", file=sys.stderr)
        return 1

    ship_id = args.ship_id or input("請輸入船號: ").strip()
    pc_id = args.pc_id or input("請輸入電腦號: ").strip()
    if not ship_id or not pc_id:
        print("錯誤：船號與電腦號皆不可為空", file=sys.stderr)
        return 1

    print(f"將以前綴「{ship_id}_{pc_id}_」更新 {len(files)} 個設定檔：")
    for path in files:
        try:
            print(update_device_name(path, ship_id, pc_id))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[失敗] {path.name}：{e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
