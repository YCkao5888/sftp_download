"""SFTP 自動化下載工具進入點。

不帶參數執行 -> 啟動 GUI。
帶參數執行   -> 依參數以 CLI 模式執行（適合排程自動化）。

參數優先順序：command line > settings.json > 內建預設值。
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

from downloader import SFTPDownloader, create_logger
from settings import load_settings

DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"


def build_parser():
    parser = argparse.ArgumentParser(description="SFTP 自動化下載工具")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="強制以 CLI 模式執行（不開啟 GUI）。當所有必要參數都已寫在 settings.json 時，可單獨帶這個旗標即可，不需重複輸入其他參數",
    )
    parser.add_argument(
        "--config",
        help="指定要讀取的設定檔路徑（預設為工具資料夾內的 settings.json）。"
        "適合同一台裝置需要下載多組不同的 SFTP 來源/本地路徑時，每組各自用一份設定檔、各排一個排程任務",
    )
    parser.add_argument("--host", help="SFTP 主機位址")
    parser.add_argument("--port", type=int, help="SFTP 連接埠（預設 22）")
    parser.add_argument("--username", help="SFTP 帳號")
    parser.add_argument(
        "--device-name",
        help="裝置/使用者識別名稱，用於標示 Log 是哪一台設備所產生（多台 edge device 共用同一 SFTP 帳號時仍可分辨）",
    )
    parser.add_argument(
        "--version-info",
        help="選填的上傳版號資訊，會一併記錄在 Log 中，不影響下載邏輯",
    )
    parser.add_argument("--password", help="SFTP 密碼（可用環境變數 SFTP_PASSWORD 取代，避免明碼留在指令紀錄）")
    parser.add_argument("--key-file", help="SSH 私鑰檔路徑（若使用金鑰登入，取代 --password）")
    parser.add_argument("--remote-path", help="SFTP 來源路徑（檔案或目錄）")
    parser.add_argument("--local-path", help="本地端儲存路徑")
    parser.add_argument(
        "--ignore-file",
        help="下載忽略設定檔路徑，內容格式完全同 .gitignore，符合規則的檔案/資料夾不會被下載；"
        "找不到該檔案則代表無需忽略任何檔案",
    )

    parser.add_argument("--no-auto-reconnect", action="store_true", help="停用斷線自動重連")
    parser.add_argument("--no-resume", action="store_true", help="停用斷點續傳")
    parser.add_argument("--no-wait-network", action="store_true", help="停用網路偵測自動下載")
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="停用多層下載，只下載來源路徑當層的檔案，略過所有子資料夾（預設會下載所有子資料夾）",
    )
    parser.add_argument("--retry-count", type=int, help="重試次數上限，0 或不指定代表無限次重試（預設無限次）")
    parser.add_argument("--retry-delay", type=int, help="重試間隔秒數（預設 10）")

    parser.add_argument("--upload-log", action="store_true", help="下載結束後將 Log 上傳回 SFTP")
    parser.add_argument("--log-remote-dir", help="上傳 Log 的 SFTP 目錄（搭配 --upload-log 使用）")
    parser.add_argument("--log-dir", help="本地端 Log 儲存目錄（預設 ./logs）")
    parser.add_argument(
        "--duplicate-mode",
        choices=["duplicate", "overwrite"],
        help="來源檔案偵測到已更新版本時的處理方式：overwrite=直接覆蓋舊檔案（預設）、duplicate=另存新檔",
    )
    parser.add_argument(
        "--duplicate-suffix",
        help="duplicate-mode 為 duplicate 時，另存新檔用的檔名後綴（預設 copy，第二次更新起會自動加上流水號 copy1、copy2...）",
    )
    return parser


def _resolve(cli_value, settings, key, fallback=None):
    if cli_value is not None:
        return cli_value
    return settings.get(key, fallback)


def run_cli(args):
    settings = load_settings(args.config) if args.config else load_settings()

    host = _resolve(args.host, settings, "host")
    port = _resolve(args.port, settings, "port", 22)
    device_name = _resolve(args.device_name, settings, "device_name")
    version_info = _resolve(args.version_info, settings, "version_info", "")
    username = _resolve(args.username, settings, "username")
    key_file = _resolve(args.key_file, settings, "key_file")
    remote_path = _resolve(args.remote_path, settings, "remote_path")
    local_path = _resolve(args.local_path, settings, "local_path")
    ignore_file = _resolve(args.ignore_file, settings, "ignore_file")
    retry_count = _resolve(args.retry_count, settings, "retry_count", None)
    retry_delay = _resolve(args.retry_delay, settings, "retry_delay", 10)
    log_remote_dir = _resolve(args.log_remote_dir, settings, "log_remote_dir")
    # log_dir 留空字串代表「未設定」，不像 retry_count=0 是有意義的值，因此用 or 串接才能正確回退到預設值。
    log_dir = args.log_dir or settings.get("log_dir") or str(DEFAULT_LOG_DIR)
    duplicate_mode = args.duplicate_mode or settings.get("duplicate_mode") or "overwrite"
    duplicate_suffix = args.duplicate_suffix or settings.get("duplicate_suffix") or "copy"

    # 布林旗標：settings.json 提供基準值，CLI 的 --no-* / --upload-log 只能單向覆蓋（關閉/開啟）。
    auto_reconnect = False if args.no_auto_reconnect else bool(settings.get("auto_reconnect", True))
    resume = False if args.no_resume else bool(settings.get("resume", True))
    wait_for_network = False if args.no_wait_network else bool(settings.get("wait_for_network", True))
    recursive = False if args.no_recursive else bool(settings.get("recursive", True))
    upload_log = True if args.upload_log else bool(settings.get("upload_log", False))

    missing = [
        name
        for name, value in (
            ("--host", host),
            ("--device-name", device_name),
            ("--username", username),
            ("--remote-path", remote_path),
            ("--local-path", local_path),
        )
        if not value
    ]
    if missing:
        print(f"錯誤：缺少必要參數 {', '.join(missing)}（可透過 command line 或 settings.json 提供）", file=sys.stderr)
        return 1
    if upload_log and not log_remote_dir:
        print("錯誤：啟用上傳 Log 時必須指定 --log-remote-dir（或設定檔中的 log_remote_dir）", file=sys.stderr)
        return 1

    password = args.password or os.environ.get("SFTP_PASSWORD") or settings.get("password")
    if not key_file and not password:
        password = getpass.getpass(f"請輸入 {username}@{host} 的密碼: ")

    logger, log_file = create_logger(log_dir, device_name, version_info)
    downloader = SFTPDownloader(
        host=host,
        port=port,
        username=username,
        password=password,
        key_file=key_file,
        remote_path=remote_path,
        local_path=local_path,
        auto_reconnect=auto_reconnect,
        resume=resume,
        wait_for_network=wait_for_network,
        recursive=recursive,
        ignore_file=ignore_file or None,
        retry_count=retry_count,
        retry_delay=retry_delay,
        upload_log=upload_log,
        remote_log_dir=log_remote_dir,
        duplicate_mode=duplicate_mode,
        duplicate_suffix=duplicate_suffix,
        logger=logger,
        log_file=log_file,
    )
    success = downloader.run()
    return 0 if success else 1


def main():
    if len(sys.argv) == 1:
        from gui import launch_gui

        launch_gui()
        return 0

    parser = build_parser()
    args = parser.parse_args()
    return run_cli(args)


if __name__ == "__main__":
    sys.exit(main())
