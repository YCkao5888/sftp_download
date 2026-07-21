"""船上更新用腳本：遍歷 config/ 內所有「上傳」設定檔並依序執行 SFTP 上傳。

只挑選檔名符合 `*_upload_settings.json` 的設定檔，避免把下載設定檔
（`*_download_settings.json`，由 run_all_downloads.py 負責）也一併跑成上傳。
每份設定檔各跑一次 `main.py --cli --mode upload --config <設定檔>`，前一個專案
上傳結束（成功或失敗）後才會執行下一個，最後彙總各專案結果。

使用方式：
    python run_all_uploads.py
    python run_all_uploads.py --config-dir other_config
"""

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
MAIN_SCRIPT = BASE_DIR / "main.py"


def find_setting_files(config_dir: Path):
    """回傳 config 目錄下所有「上傳」設定檔（*_upload_settings.json，排序後）。"""
    return sorted(config_dir.glob("*_upload_settings.json"))


def main():
    parser = argparse.ArgumentParser(description="依序執行所有設定檔的 SFTP 上傳")
    parser.add_argument("--config-dir", default=str(CONFIG_DIR), help="設定檔資料夾（預設 ./config）")
    args = parser.parse_args()

    files = find_setting_files(Path(args.config_dir))
    if not files:
        print(f"錯誤：{args.config_dir} 內找不到任何上傳設定檔（*_upload_settings.json）", file=sys.stderr)
        return 1

    results = []
    for i, path in enumerate(files, 1):
        print(f"\n===== [{i}/{len(files)}] 開始上傳：{path.name} =====")
        proc = subprocess.run(
            [sys.executable, str(MAIN_SCRIPT), "--cli", "--mode", "upload", "--config", str(path)],
            cwd=str(BASE_DIR),
        )
        ok = proc.returncode == 0
        results.append((path.name, ok))
        print(f"===== [{i}/{len(files)}] {path.name} {'完成' if ok else '失敗'} =====")

    print("\n========== 上傳結果彙總 ==========")
    for name, ok in results:
        print(f"  {'[成功]' if ok else '[失敗]'} {name}")
    failed = sum(1 for _, ok in results if not ok)
    print(f"共 {len(results)} 個專案，成功 {len(results) - failed}，失敗 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
