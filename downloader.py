"""SFTP 下載核心邏輯：連線、斷線重連、斷點續傳、Log 紀錄與回傳。"""

import csv
import hashlib
import json
import logging
import os
import re
import socket
import stat
import time
from datetime import datetime
from pathlib import Path

import paramiko

from gitignore import GitIgnoreSpec

CHUNK_SIZE = 32768
SOCKET_TIMEOUT = 30
KEEPALIVE_INTERVAL = 15
MANIFEST_FILENAME = ".sftp_download_manifest.json"

_FILENAME_UNSAFE = re.compile(r'[<>:"/\\|?*]')


def format_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024


class _CSVFileHandler(logging.Handler):
    """把 Log 寫成 CSV，方便日後把上百台裝置的 Log 彙整成同一份表格用 Excel 檢視。"""

    def __init__(self, filename, device_name, version_info=""):
        super().__init__()
        self._device_name = device_name
        self._version_info = version_info
        # utf-8-sig：讓 Excel 開啟時能正確辨識 UTF-8 中文，不會顯示成亂碼。
        self._file = open(filename, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._file)
        self._writer.writerow(["timestamp", "device_name", "version_info", "level", "message"])
        self._file.flush()

    def emit(self, record):
        try:
            timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
            self._writer.writerow(
                [timestamp, self._device_name, self._version_info, record.levelname, record.getMessage()]
            )
            self._file.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        try:
            self._file.close()
        except Exception:
            pass
        super().close()


def create_logger(log_dir, device_name, version_info="", log_callback=None):
    """device_name 用於標示這份 Log 屬於哪一台設備/使用者（多台 edge device 共用同一 SFTP 帳號時仍可分辨）。
    version_info 為選填的上傳版號資訊，會一併記錄在 Log 中，不影響任何下載邏輯。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_device_name = _FILENAME_UNSAFE.sub("_", device_name).strip() or "unknown"
    log_file = log_dir / f"sftp_download_{safe_device_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    logger = logging.getLogger(f"sftp_downloader.{id(log_file)}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    version_tag = f"[{version_info}]" if version_info else ""
    fmt = logging.Formatter(
        f"%(asctime)s [%(levelname)s] [{device_name}]{version_tag} %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    csv_handler = _CSVFileHandler(log_file, device_name, version_info)
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
        recursive=True,
        ignore_file=None,
        retry_count=None,
        retry_delay=10,
        upload_log=False,
        remote_log_dir=None,
        duplicate_mode="overwrite",
        duplicate_suffix="copy",
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
        self.recursive = recursive  # True：下載所有子資料夾（多層）；False：只下載該路徑下的檔案（單層）
        self.ignore_file = ignore_file  # 下載忽略設定檔路徑（格式同 .gitignore），None 或檔案不存在代表無需忽略
        self.retry_count = retry_count  # None 或 <= 0 代表無限次重試
        self.retry_delay = retry_delay
        self.upload_log = upload_log
        self.remote_log_dir = remote_log_dir
        self.duplicate_mode = duplicate_mode or "overwrite"  # "duplicate"（另存新檔）或 "overwrite"（直接覆蓋，預設）
        self.duplicate_suffix = duplicate_suffix or "copy"
        self.logger = logger
        self.log_file = log_file

        self.client = None
        self.sftp = None
        self._manifest = {}
        self._ignore_spec = None

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

    def _load_ignore_spec(self):
        """讀取「下載忽略設定檔」（格式同 .gitignore）。未設定或檔案不存在代表無需忽略；
        格式錯誤的規則逐行略過並記錄警告，其餘正確的規則仍照常生效。"""
        if not self.ignore_file:
            return None
        path = Path(self.ignore_file)
        if not path.exists():
            self.logger.info(f"下載忽略設定檔不存在，不忽略任何檔案: {path}")
            return None
        try:
            # utf-8-sig：Windows 記事本以 UTF-8 存檔時常會加上 BOM，若不去除，
            # BOM 會黏在第一行規則前面，導致第一條規則永遠比對不到。
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError) as e:
            self.logger.warning(f"下載忽略設定檔讀取失敗，不忽略任何檔案: {e}")
            return None
        valid_lines = []
        for lineno, line in enumerate(lines, 1):
            try:
                GitIgnoreSpec.from_lines([line])
                valid_lines.append(line)
            except ValueError:
                self.logger.warning(f"下載忽略設定檔第 {lineno} 行格式錯誤，已略過此規則: {line!r}")
        self.logger.info(f"已載入下載忽略設定檔: {path}")
        return GitIgnoreSpec.from_lines(valid_lines)

    def _is_ignored(self, rel_path):
        """rel_path 為相對於下載根目錄的路徑；資料夾請加上結尾的 /（gitignore 的資料夾規則才會匹配）。"""
        return self._ignore_spec is not None and self._ignore_spec.match_file(rel_path)

    def _list_remote_files(self, remote_root, local_root):
        files = []
        root_stat = self.sftp.stat(remote_root)
        if not stat.S_ISDIR(root_stat.st_mode):
            filename = os.path.basename(remote_root.rstrip("/"))
            if self._is_ignored(filename):
                self.logger.info(f"依忽略設定檔略過: {filename}")
            else:
                files.append((remote_root, filename))
        elif self.recursive:
            self._walk_remote_dir(remote_root, "", files, local_root)
        else:
            skipped_dirs = []
            for entry in self.sftp.listdir_attr(remote_root):
                if stat.S_ISDIR(entry.st_mode):
                    skipped_dirs.append(entry.filename)
                elif self._is_ignored(entry.filename):
                    self.logger.info(f"依忽略設定檔略過: {entry.filename}")
                else:
                    remote_path = remote_root.rstrip("/") + "/" + entry.filename
                    files.append((remote_path, entry.filename))
            if skipped_dirs:
                self.logger.info(f"僅下載單層（未啟用多層），略過 {len(skipped_dirs)} 個子資料夾: {', '.join(skipped_dirs)}")
        return files

    def _walk_remote_dir(self, remote_dir, rel_dir, files, local_root):
        # 即使子資料夾底下沒有任何檔案，也要在本地端建立對應的空資料夾，
        # 否則單純比對「有沒有檔案」永遠不會觸發 mkdir，空資料夾就不會被下載下來。
        local_dir = local_root / Path(*rel_dir.split("/")) if rel_dir else local_root
        local_dir.mkdir(parents=True, exist_ok=True)
        for entry in self.sftp.listdir_attr(remote_dir):
            remote_path = remote_dir.rstrip("/") + "/" + entry.filename
            rel_path = f"{rel_dir}/{entry.filename}" if rel_dir else entry.filename
            if stat.S_ISDIR(entry.st_mode):
                # 被忽略的資料夾整棵略過、不往下走訪，本地端也不會建立對應資料夾（與 git 行為一致）。
                if self._is_ignored(rel_path + "/"):
                    self.logger.info(f"依忽略設定檔略過資料夾: {rel_path}/")
                    continue
                self._walk_remote_dir(remote_path, rel_path, files, local_root)
            elif self._is_ignored(rel_path):
                self.logger.info(f"依忽略設定檔略過: {rel_path}")
            else:
                files.append((remote_path, rel_path))

    def _manifest_path(self, local_root):
        return local_root / MANIFEST_FILENAME

    def _load_manifest(self, local_root):
        path = self._manifest_path(local_root)
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(f"版本紀錄檔讀取失敗，將視為未追蹤過任何檔案: {e}")
            return {}

    def _save_manifest(self, local_root):
        try:
            with open(self._manifest_path(local_root), "w", encoding="utf-8") as f:
                json.dump(self._manifest, f, ensure_ascii=False, indent=2)
        except OSError as e:
            self.logger.warning(f"版本紀錄檔寫入失敗: {e}")

    def _next_duplicate_path(self, local_file):
        candidate = local_file.with_name(f"{local_file.stem}_{self.duplicate_suffix}{local_file.suffix}")
        n = 1
        while candidate.exists():
            candidate = local_file.with_name(f"{local_file.stem}_{self.duplicate_suffix}{n}{local_file.suffix}")
            n += 1
        return candidate

    def _hash_local_file(self, local_file):
        """計算本地端檔案目前內容的 SHA-256（只讀本機磁碟，不牽涉網路），
        回傳 hashlib 雜湊物件，方便驗證後可直接沿用繼續累加後續新下載的內容。"""
        local_hash = hashlib.sha256()
        with open(local_file, "rb") as local_f:
            while True:
                chunk = local_f.read(CHUNK_SIZE)
                if not chunk:
                    break
                local_hash.update(chunk)
        return local_hash

    def _download_one_file(self, remote_file, rel_path, local_root):
        local_file = local_root / Path(*rel_path.split("/"))
        local_file.parent.mkdir(parents=True, exist_ok=True)
        remote_stat = self.sftp.stat(remote_file)
        remote_size = remote_stat.st_size
        remote_mtime = int(remote_stat.st_mtime)

        target_file = local_file
        local_size = 0
        mode = "wb"
        running_hash = hashlib.sha256()  # 邊下載邊累加，最後（或中斷當下）存進版本紀錄檔

        if local_file.exists():
            if not self.resume:
                # 斷點續傳未啟用：不判斷是否未變更、也不接續，一律整份重新下載；
                # 但存到哪個檔名仍然要依 duplicate_mode 決定，這一步跟斷點續傳是否啟用無關。
                if self.duplicate_mode == "overwrite":
                    self.logger.info(f"重新下載並覆蓋舊檔案: {rel_path}")
                else:
                    target_file = self._next_duplicate_path(local_file)
                    self.logger.info(f"重新下載，另存為: {target_file.name}")
            else:
                disk_size = local_file.stat().st_size
                known = self._manifest.get(rel_path)

                if disk_size == remote_size:
                    # 大小相同：用版本紀錄（若有）判斷是否真的未變更；沒有紀錄則姑且視為未變更略過。
                    # 這裡不逐一雜湊比對整個檔案內容，避免每次執行都要重新讀取所有已下載完成的檔案。
                    if known is None or (known.get("size") == remote_size and known.get("mtime") == remote_mtime):
                        self.logger.info(f"略過（已完整下載）: {rel_path}")
                        self._manifest[rel_path] = {"size": remote_size, "mtime": remote_mtime}
                        self._save_manifest(local_root)
                        return "skipped"
                    if self.duplicate_mode == "overwrite":
                        self.logger.info(f"偵測到來源檔案已更新，覆蓋舊檔案: {rel_path}")
                    else:
                        target_file = self._next_duplicate_path(local_file)
                        self.logger.info(f"偵測到來源檔案已更新，另存為: {target_file.name}")
                elif disk_size > remote_size:
                    if self.duplicate_mode == "overwrite":
                        self.logger.warning(f"本地檔案大於遠端檔案，重新下載: {rel_path}")
                    else:
                        target_file = self._next_duplicate_path(local_file)
                        self.logger.warning(f"本地檔案大於遠端檔案，另存為: {target_file.name}")
                elif self.duplicate_mode == "duplicate":
                    # 「另存新檔」模式一律整份重新下載、不接續舊檔案，斷點續傳形同停用，不需要驗證內容。
                    target_file = self._next_duplicate_path(local_file)
                    self.logger.info(f"重新下載，另存為: {target_file.name}")
                else:
                    # 本地檔案比遠端小：檢查遠端版本是否仍與紀錄一致，並用「本地端雜湊」確認這段尚未
                    # 下載完的內容有沒有被外部更動過（例如被人手動修改）。這裡刻意只讀本機磁碟跟紀錄檔
                    # 裡存的雜湊比對，不會為了驗證而重新從遠端讀取已下載的內容，避免已下載比例越高、
                    # 驗證反而越花時間、越像卡住的問題。
                    same_remote_version = (
                        known is not None
                        and known.get("size") == remote_size
                        and known.get("mtime") == remote_mtime
                    )
                    verified = False
                    if same_remote_version and known.get("local_bytes") == disk_size and known.get("local_sha256"):
                        disk_hash = self._hash_local_file(local_file)
                        if disk_hash.hexdigest() == known["local_sha256"]:
                            verified = True
                            running_hash = disk_hash  # 直接沿用，後續新下載的內容繼續累加上去

                    if verified:
                        self.logger.info(f"本地端內容雜湊比對相符，接續下載: {rel_path}")
                        local_size = disk_size
                        mode = "ab"
                    else:
                        # 走到這裡 duplicate_mode 必定是 "overwrite"："duplicate" 模式在上面
                        # 的 elif 分支就已經攔截、一律整份重新下載成新檔案，不會執行到這裡。
                        reason = "本地檔案內容與紀錄不符（可能已被人為修改）" if same_remote_version else "偵測到來源檔案已更新"
                        self.logger.info(f"{reason}，覆蓋舊檔案: {rel_path}")

        self.logger.info(f"開始下載: {rel_path} ({format_size(remote_size)})")
        last_pct_logged = -1
        last_checkpoint_pct = -1
        transferred = local_size
        try:
            with self.sftp.open(remote_file, "rb") as remote_f:
                remote_f.seek(local_size)
                with open(target_file, mode) as local_f:
                    while True:
                        chunk = remote_f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        local_f.write(chunk)
                        running_hash.update(chunk)
                        transferred += len(chunk)
                        if remote_size > 0:
                            pct = int(transferred / remote_size * 100)
                            if pct > last_pct_logged:
                                self.logger.info(f"  {rel_path} 進度: {pct}%")
                                last_pct_logged = pct
                            # 每跨過 10% 進度就存一次檢查點，而不是每個 chunk 都寫檔，
                            # 避免大檔案下載時頻繁寫入版本紀錄檔造成不必要的效能負擔。
                            if self.resume and pct >= last_checkpoint_pct + 10:
                                local_f.flush()
                                self._manifest[rel_path] = {
                                    "size": remote_size,
                                    "mtime": remote_mtime,
                                    "local_sha256": running_hash.hexdigest(),
                                    "local_bytes": transferred,
                                }
                                self._save_manifest(local_root)
                                last_checkpoint_pct = pct
        finally:
            # 不論成功、失敗或中途被中斷，都存下目前實際寫到的位置與雜湊，讓下次重試時
            # 能正確判斷「這是同一版本尚未下載完的部分」，而不是每次中斷後都只能整份重來。
            if self.resume:
                self._manifest[rel_path] = {
                    "size": remote_size,
                    "mtime": remote_mtime,
                    "local_sha256": running_hash.hexdigest(),
                    "local_bytes": transferred,
                }
                self._save_manifest(local_root)

        self.logger.info(f"完成下載: {target_file.name if target_file != local_file else rel_path}")
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
        self._manifest = self._load_manifest(local_root) if self.resume else {}
        self._ignore_spec = self._load_ignore_spec()

        downloaded, skipped, failed = 0, 0, []
        try:
            if self.wait_for_network:
                self._wait_for_network()
            self._connect_with_retry()

            file_list = None
            list_attempts = 0
            while file_list is None:
                try:
                    file_list = self._list_remote_files(self.remote_path, local_root)
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
