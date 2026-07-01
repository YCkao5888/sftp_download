"""SFTP 下載核心邏輯：連線、斷線重連、斷點續傳、Log 紀錄與回傳。"""

import csv
import logging
import os
import re
import socket
import stat
import time
from datetime import datetime
from pathlib import Path

import paramiko

CHUNK_SIZE = 32768
SOCKET_TIMEOUT = 30
KEEPALIVE_INTERVAL = 15

_FILENAME_UNSAFE = re.compile(r'[<>:"/\\|?*]')


def format_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024


class _CSVFileHandler(logging.Handler):
    """把 Log 寫成 CSV，方便日後把上百台裝置的 Log 彙整成同一份表格用 Excel 檢視。"""

    def __init__(self, filename, device_name):
        super().__init__()
        self._device_name = device_name
        # utf-8-sig：讓 Excel 開啟時能正確辨識 UTF-8 中文，不會顯示成亂碼。
        self._file = open(filename, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._file)
        self._writer.writerow(["timestamp", "device_name", "level", "message"])
        self._file.flush()

    def emit(self, record):
        try:
            timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
            self._writer.writerow([timestamp, self._device_name, record.levelname, record.getMessage()])
            self._file.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        try:
            self._file.close()
        except Exception:
            pass
        super().close()


def create_logger(log_dir, device_name, log_callback=None):
    """device_name 用於標示這份 Log 屬於哪一台設備/使用者（多台 edge device 共用同一 SFTP 帳號時仍可分辨）。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_device_name = _FILENAME_UNSAFE.sub("_", device_name).strip() or "unknown"
    log_file = log_dir / f"sftp_download_{safe_device_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    logger = logging.getLogger(f"sftp_downloader.{id(log_file)}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(f"%(asctime)s [%(levelname)s] [{device_name}] %(message)s", "%Y-%m-%d %H:%M:%S")

    csv_handler = _CSVFileHandler(log_file, device_name)
    logger.addHandler(csv_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    if log_callback:
        class CallbackHandler(logging.Handler):
            def emit(self, record):
                log_callback(self.format(record))

        callback_handler = CallbackHandler()
        callback_handler.setFormatter(fmt)
        logger.addHandler(callback_handler)

    return logger, log_file


class SFTPDownloader:
    def __init__(
        self,
        host,
        port,
        username,
        remote_path,
        local_path,
        password=None,
        key_file=None,
        auto_reconnect=True,
        resume=True,
        wait_for_network=True,
        retry_count=None,
        retry_delay=10,
        upload_log=False,
        remote_log_dir=None,
        logger=None,
        log_file=None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.remote_path = remote_path
        self.local_path = local_path
        self.password = password
        self.key_file = key_file
        self.auto_reconnect = auto_reconnect
        self.resume = resume
        self.wait_for_network = wait_for_network
        self.retry_count = retry_count  # None 或 <= 0 代表無限次重試
        self.retry_delay = retry_delay
        self.upload_log = upload_log
        self.remote_log_dir = remote_log_dir
        self.logger = logger
        self.log_file = log_file

        self.client = None
        self.sftp = None

    def _retry_limit_reached(self, attempts):
        if self.retry_count is None or self.retry_count <= 0:
            return False
        return attempts > self.retry_count

    def _connect(self):
        self.logger.info(f"正在連線至 {self.host}:{self.port} ...")
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs = dict(hostname=self.host, port=self.port, username=self.username, timeout=15)
        if self.key_file:
            connect_kwargs["key_filename"] = self.key_file
        else:
            connect_kwargs["password"] = self.password
        client.connect(**connect_kwargs)
        self.client = client
        self.sftp = client.open_sftp()
        # 若無此逾時設定，連線在傳輸中途「無聲斷線」（如網路線拔掉、Wi-Fi 斷線）時，
        # 讀寫呼叫會永遠卡住不會丟出例外，導致斷線重連機制永遠不會被觸發。
        self.sftp.get_channel().settimeout(SOCKET_TIMEOUT)
        client.get_transport().set_keepalive(KEEPALIVE_INTERVAL)
        self.logger.info("連線成功")

    def _connect_with_retry(self):
        attempts = 0
        while True:
            try:
                self._connect()
                return
            except paramiko.AuthenticationException:
                self.logger.error("連線失敗：帳號或密碼錯誤")
                raise
            except (paramiko.SSHException, OSError) as e:
                attempts += 1
                self.logger.warning(f"連線失敗（第 {attempts} 次）：{e}")
                if not self.auto_reconnect or self._retry_limit_reached(attempts):
                    self.logger.error("已達重試上限，放棄連線")
                    raise
                if self.wait_for_network:
                    self._wait_for_network()
                time.sleep(self.retry_delay)

    def _wait_for_network(self):
        self.logger.info("正在偵測網路連線狀態...")
        while True:
            try:
                with socket.create_connection((self.host, self.port), timeout=5):
                    self.logger.info("網路連線已恢復")
                    return
            except OSError:
                self.logger.warning(f"無法連線至 {self.host}:{self.port}，{self.retry_delay} 秒後重試...")
                time.sleep(self.retry_delay)

    def _close(self):
        try:
            if self.sftp:
                self.sftp.close()
        except Exception:
            pass
        try:
            if self.client:
                self.client.close()
        except Exception:
            pass

    def _list_remote_files(self, remote_root):
        files = []
        root_stat = self.sftp.stat(remote_root)
        if stat.S_ISDIR(root_stat.st_mode):
            self._walk_remote_dir(remote_root, "", files)
        else:
            files.append((remote_root, os.path.basename(remote_root.rstrip("/"))))
        return files

    def _walk_remote_dir(self, remote_dir, rel_dir, files):
        for entry in self.sftp.listdir_attr(remote_dir):
            remote_path = remote_dir.rstrip("/") + "/" + entry.filename
            rel_path = f"{rel_dir}/{entry.filename}" if rel_dir else entry.filename
            if stat.S_ISDIR(entry.st_mode):
                self._walk_remote_dir(remote_path, rel_path, files)
            else:
                files.append((remote_path, rel_path))

    def _download_one_file(self, remote_file, rel_path, local_root):
        local_file = local_root / Path(*rel_path.split("/"))
        local_file.parent.mkdir(parents=True, exist_ok=True)
        remote_size = self.sftp.stat(remote_file).st_size

        if not self.resume:
            local_size = 0
            mode = "wb"
        elif local_file.exists():
            local_size = local_file.stat().st_size
            if local_size == remote_size:
                self.logger.info(f"略過（已完整下載）: {rel_path}")
                return "skipped"
            elif local_size > remote_size:
                self.logger.warning(f"本地檔案大於遠端檔案，重新下載: {rel_path}")
                local_size = 0
                mode = "wb"
            else:
                mode = "ab"
        else:
            local_size = 0
            mode = "wb"

        self.logger.info(f"開始下載: {rel_path} ({format_size(remote_size)})")
        last_pct_logged = -1
        with self.sftp.open(remote_file, "rb") as remote_f:
            remote_f.seek(local_size)
            with open(local_file, mode) as local_f:
                transferred = local_size
                while True:
                    chunk = remote_f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    local_f.write(chunk)
                    transferred += len(chunk)
                    if remote_size > 0:
                        pct = int(transferred / remote_size * 100)
                        if pct > last_pct_logged:
                            self.logger.info(f"  {rel_path} 進度: {pct}%")
                            last_pct_logged = pct
        self.logger.info(f"完成下載: {rel_path}")
        return "downloaded"

    def _upload_log_file(self):
        try:
            self.logger.info("正在上傳 Log 檔至 SFTP...")
            for handler in self.logger.handlers:
                handler.flush()
            self._connect_with_retry()
            remote_name = self.remote_log_dir.rstrip("/") + "/" + Path(self.log_file).name
            self.sftp.put(str(self.log_file), remote_name)
            self.logger.info(f"Log 上傳完成: {remote_name}")
        except Exception as e:
            self.logger.error(f"Log 上傳失敗: {e}")
        finally:
            self._close()

    def run(self):
        self.logger.info("=== SFTP 下載任務開始 ===")
        local_root = Path(self.local_path)
        local_root.mkdir(parents=True, exist_ok=True)

        downloaded, skipped, failed = 0, 0, []
        try:
            if self.wait_for_network:
                self._wait_for_network()
            self._connect_with_retry()

            file_list = None
            list_attempts = 0
            while file_list is None:
                try:
                    file_list = self._list_remote_files(self.remote_path)
                except FileNotFoundError:
                    self.logger.error(f"遠端路徑不存在: {self.remote_path}")
                    return False
                except (paramiko.SSHException, OSError, EOFError) as e:
                    list_attempts += 1
                    self.logger.warning(f"列出遠端檔案清單發生錯誤（第 {list_attempts} 次）: {e}")
                    if not self.auto_reconnect or self._retry_limit_reached(list_attempts):
                        self.logger.error("已達重試上限，任務中止")
                        return False
                    self._connect_with_retry()

            self.logger.info(f"共發現 {len(file_list)} 個檔案")

            for remote_file, rel_path in file_list:
                attempts = 0
                while True:
                    try:
                        result = self._download_one_file(remote_file, rel_path, local_root)
                        if result == "skipped":
                            skipped += 1
                        else:
                            downloaded += 1
                        break
                    except PermissionError as e:
                        self.logger.error(f"寫入失敗（權限不足）: {rel_path}: {e}")
                        failed.append(rel_path)
                        break
                    except FileNotFoundError as e:
                        self.logger.error(f"檔案不存在: {rel_path}: {e}")
                        failed.append(rel_path)
                        break
                    except (paramiko.SSHException, OSError, EOFError) as e:
                        attempts += 1
                        self.logger.warning(f"下載 {rel_path} 發生錯誤（第 {attempts} 次）: {e}")
                        if not self.auto_reconnect or self._retry_limit_reached(attempts):
                            self.logger.error(f"檔案 {rel_path} 下載失敗，放棄重試")
                            failed.append(rel_path)
                            break
                        try:
                            self._connect_with_retry()
                        except Exception:
                            failed.append(rel_path)
                            break
        except paramiko.AuthenticationException:
            self.logger.error("=== 任務中止：帳號或密碼錯誤 ===")
            return False
        except Exception as e:
            self.logger.error(f"=== 任務中止：{e} ===")
            return False
        finally:
            self._close()

        self.logger.info(f"=== 下載任務結束：成功 {downloaded}，略過 {skipped}，失敗 {len(failed)} ===")
        if failed:
            self.logger.info("失敗清單：" + ", ".join(failed))

        if self.upload_log:
            self._upload_log_file()

        return len(failed) == 0
