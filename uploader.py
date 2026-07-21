"""SFTP 上傳核心邏輯（local → remote）：與 `downloader.SFTPDownloader` 對稱的反向傳輸。

以 `local_path` 為來源、`remote_path` 為目的地，鏡射下載端的能力：遞迴走訪本地目錄、忽略規則、
以 size/mtime 判斷跳過未變更檔案、byte 級斷點續傳與版本紀錄（manifest）。連線/重試/網路偵測/關閉
等方向無關邏輯全部繼承自 `SFTPBase`。
"""

import hashlib
import os
from pathlib import Path, PurePosixPath

import paramiko

from downloader import CHUNK_SIZE, MANIFEST_FILENAME, SFTPBase, format_size

UPLOAD_MANIFEST_FILENAME = ".sftp_upload_manifest.json"

# 版本紀錄檔存放在「本地來源目錄」內，走訪來源時必須排除，否則會把自己的 manifest 一起上傳。
# 同時排除下載端的 manifest，避免同一目錄雙向使用時把對方的紀錄檔也上傳出去。
_MANIFEST_NAMES = {UPLOAD_MANIFEST_FILENAME, MANIFEST_FILENAME}


class SFTPUploader(SFTPBase):
    """SFTP 上傳（local → remote）：遞迴走訪本地目錄、斷點續傳、忽略規則與版本紀錄。"""

    manifest_filename = UPLOAD_MANIFEST_FILENAME

    def _remote_exists(self, remote_path):
        try:
            self.sftp.stat(remote_path)
            return True
        except FileNotFoundError:
            return False

    def _list_local_files(self, source, remote_root):
        """回傳 [(本地絕對路徑, rel_path)]。rel_path 一律以 / 分隔，作為遠端相對路徑與 manifest 的鍵。"""
        files = []
        if source.is_file():
            filename = source.name
            if self._is_ignored(filename):
                self.logger.info(f"依忽略設定檔略過: {filename}")
            else:
                files.append((source, filename))
        elif self.recursive:
            self._walk_local_dir(source, "", files, remote_root)
        else:
            skipped_dirs = []
            for name in sorted(os.listdir(source)):
                full = source / name
                if full.is_dir():
                    skipped_dirs.append(name)
                elif name in _MANIFEST_NAMES:
                    continue
                elif self._is_ignored(name):
                    self.logger.info(f"依忽略設定檔略過: {name}")
                else:
                    files.append((full, name))
            if skipped_dirs:
                self.logger.info(
                    f"僅上傳單層（未啟用多層），略過 {len(skipped_dirs)} 個子資料夾: {', '.join(skipped_dirs)}"
                )
        return files

    def _walk_local_dir(self, local_dir, rel_dir, files, remote_root):
        # 即使子資料夾底下沒有任何檔案，也要在遠端建立對應的空資料夾，與下載端「鏡射空資料夾」的行為對稱。
        remote_dir = remote_root.rstrip("/") + ("/" + rel_dir if rel_dir else "")
        self._ensure_remote_dir(remote_dir)
        for name in sorted(os.listdir(local_dir)):
            full = local_dir / name
            rel_path = f"{rel_dir}/{name}" if rel_dir else name
            # 只有根目錄層才會有 manifest 檔；rel_dir 為空字串代表目前正在走訪根目錄。
            if not rel_dir and name in _MANIFEST_NAMES:
                continue
            if full.is_dir():
                # 被忽略的資料夾整棵略過、不往下走訪，遠端也不會建立對應資料夾（與 git 行為一致）。
                if self._is_ignored(rel_path + "/"):
                    self.logger.info(f"依忽略設定檔略過資料夾: {rel_path}/")
                    continue
                self._walk_local_dir(full, rel_path, files, remote_root)
            elif self._is_ignored(rel_path):
                self.logger.info(f"依忽略設定檔略過: {rel_path}")
            else:
                files.append((full, rel_path))

    def _next_remote_duplicate_path(self, remote_file):
        p = PurePosixPath(remote_file)

        def make(suffix):
            return str(p.with_name(f"{p.stem}_{suffix}{p.suffix}"))

        candidate = make(self.duplicate_suffix)
        n = 1
        while self._remote_exists(candidate):
            candidate = make(f"{self.duplicate_suffix}{n}")
            n += 1
        return candidate

    def _hash_local_prefix(self, local_file, nbytes):
        """計算本地檔案前 nbytes 位元組的 SHA-256（只讀本機磁碟），回傳 hashlib 雜湊物件，
        用來驗證遠端已上傳的前段內容是否與本地相符後可直接沿用續傳。"""
        local_hash = hashlib.sha256()
        remaining = nbytes
        with open(local_file, "rb") as local_f:
            while remaining > 0:
                chunk = local_f.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                local_hash.update(chunk)
                remaining -= len(chunk)
        return local_hash

    def _upload_one_file(self, local_file, rel_path, remote_root, local_root):
        remote_file = remote_root.rstrip("/") + "/" + rel_path
        self._ensure_remote_dir(str(PurePosixPath(remote_file).parent))
        local_stat = local_file.stat()
        local_size = local_stat.st_size
        local_mtime = int(local_stat.st_mtime)

        target_remote = remote_file
        uploaded_bytes = 0  # 遠端已存在的位元組數（續傳起點）
        mode = "wb"
        running_hash = hashlib.sha256()  # 邊上傳邊累加，最後（或中斷當下）存進版本紀錄檔

        try:
            remote_stat = self.sftp.stat(remote_file)
            remote_disk_size = remote_stat.st_size
            remote_exists = True
        except FileNotFoundError:
            remote_disk_size = 0
            remote_exists = False

        if remote_exists:
            if not self.resume:
                # 斷點續傳未啟用：不判斷是否未變更、也不接續，一律整份重新上傳；
                # 但存到哪個遠端檔名仍然要依 duplicate_mode 決定。
                if self.duplicate_mode == "overwrite":
                    self.logger.info(f"重新上傳並覆蓋遠端檔案: {rel_path}")
                else:
                    target_remote = self._next_remote_duplicate_path(remote_file)
                    self.logger.info(f"重新上傳，另存為: {PurePosixPath(target_remote).name}")
            else:
                known = self._manifest.get(rel_path)

                if remote_disk_size == local_size:
                    # 大小相同：用版本紀錄（若有）判斷是否真的未變更；沒有紀錄則姑且視為未變更略過。
                    if known is None or (known.get("size") == local_size and known.get("mtime") == local_mtime):
                        self.logger.info(f"略過（已完整上傳）: {rel_path}")
                        self._manifest[rel_path] = {"size": local_size, "mtime": local_mtime}
                        self._save_manifest(local_root)
                        return "skipped"
                    if self.duplicate_mode == "overwrite":
                        self.logger.info(f"偵測到本地檔案已更新，覆蓋遠端檔案: {rel_path}")
                    else:
                        target_remote = self._next_remote_duplicate_path(remote_file)
                        self.logger.info(f"偵測到本地檔案已更新，另存為: {PurePosixPath(target_remote).name}")
                elif remote_disk_size > local_size:
                    if self.duplicate_mode == "overwrite":
                        self.logger.warning(f"遠端檔案大於本地檔案，重新上傳: {rel_path}")
                    else:
                        target_remote = self._next_remote_duplicate_path(remote_file)
                        self.logger.warning(f"遠端檔案大於本地檔案，另存為: {PurePosixPath(target_remote).name}")
                elif self.duplicate_mode == "duplicate":
                    # 「另存新檔」模式一律整份重新上傳、不接續遠端舊檔案，斷點續傳形同停用，不需要驗證內容。
                    target_remote = self._next_remote_duplicate_path(remote_file)
                    self.logger.info(f"重新上傳，另存為: {PurePosixPath(target_remote).name}")
                else:
                    # 遠端檔案比本地小：檢查本地版本是否仍與紀錄一致，並用「本地端前綴雜湊」確認遠端這段
                    # 已上傳的內容有沒有被外部更動過。與下載端對稱：只讀本機磁碟跟紀錄檔裡的雜湊比對，
                    # 不會為了驗證而回讀遠端已上傳的內容。
                    same_local_version = (
                        known is not None
                        and known.get("size") == local_size
                        and known.get("mtime") == local_mtime
                    )
                    verified = False
                    if same_local_version and known.get("local_bytes") == remote_disk_size and known.get("local_sha256"):
                        prefix_hash = self._hash_local_prefix(local_file, remote_disk_size)
                        if prefix_hash.hexdigest() == known["local_sha256"]:
                            verified = True
                            running_hash = prefix_hash  # 直接沿用，後續新上傳的內容繼續累加上去

                    if verified:
                        self.logger.info(f"遠端內容雜湊比對相符，接續上傳: {rel_path}")
                        uploaded_bytes = remote_disk_size
                        mode = "ab"
                    else:
                        # 走到這裡 duplicate_mode 必定是 "overwrite"："duplicate" 模式在上面的 elif
                        # 分支就已經攔截、一律整份重新上傳成新檔案，不會執行到這裡。
                        reason = "本地檔案內容與紀錄不符（可能已被人為修改）" if same_local_version else "偵測到本地檔案已更新"
                        self.logger.info(f"{reason}，覆蓋遠端檔案: {rel_path}")

        self.logger.info(f"開始上傳: {rel_path} ({format_size(local_size)})")
        last_pct_logged = -1
        last_checkpoint_pct = -1
        transferred = uploaded_bytes
        try:
            with open(local_file, "rb") as local_f:
                local_f.seek(uploaded_bytes)
                with self.sftp.open(target_remote, mode) as remote_f:
                    while True:
                        chunk = local_f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        remote_f.write(chunk)
                        running_hash.update(chunk)
                        transferred += len(chunk)
                        if local_size > 0:
                            pct = int(transferred / local_size * 100)
                            if pct > last_pct_logged:
                                self.logger.info(f"  {rel_path} 進度: {pct}%")
                                last_pct_logged = pct
                            # 每跨過 10% 進度就存一次檢查點，避免大檔案上傳時頻繁寫入版本紀錄檔。
                            if self.resume and pct >= last_checkpoint_pct + 10:
                                remote_f.flush()
                                self._manifest[rel_path] = {
                                    "size": local_size,
                                    "mtime": local_mtime,
                                    "local_sha256": running_hash.hexdigest(),
                                    "local_bytes": transferred,
                                }
                                self._save_manifest(local_root)
                                last_checkpoint_pct = pct
        finally:
            # 不論成功、失敗或中途被中斷，都存下目前實際上傳到的位置與雜湊，讓下次重試時能正確判斷
            # 「這是同一版本尚未上傳完的部分」，而不是每次中斷後都只能整份重來。
            if self.resume:
                self._manifest[rel_path] = {
                    "size": local_size,
                    "mtime": local_mtime,
                    "local_sha256": running_hash.hexdigest(),
                    "local_bytes": transferred,
                }
                self._save_manifest(local_root)

        self.logger.info(
            f"完成上傳: {PurePosixPath(target_remote).name if target_remote != remote_file else rel_path}"
        )
        return "uploaded"

    def run(self):
        self.logger.info("=== SFTP 上傳任務開始 ===")
        # 上傳僅使用單一目的地路徑；若不慎傳入路徑陣列，取第一個並警告。
        remote_root = self.remote_path[0] if isinstance(self.remote_path, list) else self.remote_path
        if isinstance(self.remote_path, list) and len(self.remote_path) > 1:
            self.logger.warning(f"上傳僅支援單一目的地路徑，將使用第一個: {remote_root}")

        source = Path(self.local_path)
        if not source.exists():
            self.logger.error(f"來源路徑不存在: {source}")
            return False
        # 單一檔案上傳時 manifest 放在其所在目錄；目錄上傳時放在該目錄本身。
        local_root = source if source.is_dir() else source.parent
        self._manifest = self._load_manifest(local_root) if self.resume else {}
        self._ignore_spec = self._load_ignore_spec()

        uploaded, skipped, failed = 0, 0, []
        try:
            if self.wait_for_network:
                self._wait_for_network()
            self._connect_with_retry()

            file_list = None
            list_attempts = 0
            while file_list is None:
                try:
                    file_list = self._list_local_files(source, remote_root)
                except (paramiko.SSHException, OSError, EOFError) as e:
                    file_list = None
                    list_attempts += 1
                    self.logger.warning(f"建立遠端目錄或列出本地檔案清單發生錯誤（第 {list_attempts} 次）: {e}")
                    if not self.auto_reconnect or self._retry_limit_reached(list_attempts):
                        self.logger.error("已達重試上限，任務中止")
                        return False
                    self._connect_with_retry()

            self.logger.info(f"共發現 {len(file_list)} 個檔案")

            for local_file, rel_path in file_list:
                attempts = 0
                while True:
                    try:
                        result = self._upload_one_file(local_file, rel_path, remote_root, local_root)
                        if result == "skipped":
                            skipped += 1
                        else:
                            uploaded += 1
                        break
                    except PermissionError as e:
                        self.logger.error(f"上傳失敗（權限不足）: {rel_path}: {e}")
                        failed.append(rel_path)
                        break
                    except FileNotFoundError as e:
                        self.logger.error(f"本地檔案不存在: {rel_path}: {e}")
                        failed.append(rel_path)
                        break
                    except (paramiko.SSHException, OSError, EOFError) as e:
                        attempts += 1
                        self.logger.warning(f"上傳 {rel_path} 發生錯誤（第 {attempts} 次）: {e}")
                        if not self.auto_reconnect or self._retry_limit_reached(attempts):
                            self.logger.error(f"檔案 {rel_path} 上傳失敗，放棄重試")
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

        self.logger.info(f"=== 上傳任務結束：成功 {uploaded}，略過 {skipped}，失敗 {len(failed)} ===")
        if failed:
            self.logger.info("失敗清單：" + ", ".join(failed))

        if self.upload_log:
            self._upload_log_file()

        return len(failed) == 0
