"""pytest 共用 fixtures：假 SFTP client（不碰真實網路）、靜音 logger、封鎖 time.sleep。"""

import logging
import stat as stat_module
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import downloader as downloader_module  # noqa: E402
import uploader as uploader_module  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """所有測試都封鎖 time.sleep，避免重試/等待邏輯的測試真的卡住等待。"""
    monkeypatch.setattr(downloader_module.time, "sleep", lambda seconds: None)


@pytest.fixture
def logger():
    """一般 logging.Logger，交給 pytest 內建的 caplog fixture 擷取訊息斷言用。"""
    lg = logging.getLogger("sftp_transfer_test")
    lg.setLevel(logging.DEBUG)
    lg.propagate = True
    lg.handlers.clear()
    return lg


class FakeSFTPAttr:
    """模擬 paramiko.SFTPAttributes。"""

    def __init__(self, filename, is_dir, size=0, mtime=0):
        self.filename = filename
        self.st_mode = stat_module.S_IFDIR if is_dir else stat_module.S_IFREG
        self.st_size = size
        self.st_mtime = mtime


class FakeSFTPFile:
    """模擬 paramiko 開啟遠端檔案回傳的檔案物件，支援讀取（下載）與寫入（上傳）。

    寫入模式（"w"/"a"）會即時把緩衝內容寫回 client.files，讓上傳過程中的斷點續傳測試
    可以觀察到「已上傳到一半」的遠端狀態。"""

    def __init__(self, client, path, mode="rb"):
        self.client = client
        self.path = path
        self.mode = mode
        if "a" in mode:
            self.buffer = bytearray(client.files.get(path, b""))
            self.pos = len(self.buffer)
        elif "w" in mode:
            self.buffer = bytearray()
            client.files[path] = b""  # 開檔即截斷，對應覆蓋上傳
            self.pos = 0
        else:
            self.buffer = bytearray(client.files.get(path, b""))
            self.pos = 0

    def seek(self, pos):
        self.pos = pos

    def read(self, n=-1):
        data = bytes(self.buffer)
        chunk = data[self.pos:] if n is None or n < 0 else data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk

    def write(self, data):
        self.buffer[self.pos:self.pos + len(data)] = data
        self.pos += len(data)
        self._flush_to_client()

    def flush(self):
        self._flush_to_client()

    def _flush_to_client(self):
        if "w" in self.mode or "a" in self.mode:
            self.client.files[self.path] = bytes(self.buffer)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._flush_to_client()
        return False


class FakeSFTPClient:
    """輕量假 SFTP client：用一個 {遠端路徑: bytes} 的字典模擬檔案樹，資料夾由路徑前綴自動推導。
    用於不需要真正連線的邏輯測試（列表、下載決策、版本比對等）。"""

    def __init__(self, files=None, mtimes=None):
        self.files = dict(files or {})
        self.mtimes = dict(mtimes or {})
        self.put_calls = []
        self.dirs = set()
        self.mkdir_calls = []

    def stat(self, path):
        path = path.rstrip("/")
        if path in self.files:
            return FakeSFTPAttr(path, is_dir=False, size=len(self.files[path]), mtime=self.mtimes.get(path, 0))
        prefix = path + "/"
        if path == "" or path in self.dirs or any(p.startswith(prefix) for p in self.files):
            return FakeSFTPAttr(path, is_dir=True)
        raise FileNotFoundError(f"No such file: {path}")

    def mkdir(self, path):
        self.dirs.add(path.rstrip("/"))
        self.mkdir_calls.append(path)

    def listdir_attr(self, path):
        prefix = path.rstrip("/") + "/"
        seen_dirs = set()
        results = []
        for full_path, data in self.files.items():
            if not full_path.startswith(prefix):
                continue
            rest = full_path[len(prefix):]
            if "/" in rest:
                dirname = rest.split("/")[0]
                if dirname not in seen_dirs:
                    seen_dirs.add(dirname)
                    results.append(FakeSFTPAttr(dirname, is_dir=True))
            else:
                results.append(FakeSFTPAttr(rest, is_dir=False, size=len(data), mtime=self.mtimes.get(full_path, 0)))
        return results

    def open(self, path, mode="rb"):
        return FakeSFTPFile(self, path.rstrip("/"), mode)

    def put(self, local_path, remote_path):
        with open(local_path, "rb") as f:
            data = f.read()
        self.files[remote_path] = data
        self.put_calls.append((local_path, remote_path))


@pytest.fixture
def fake_sftp_factory():
    """回傳一個可建立 FakeSFTPClient 的工廠函式，讓測試自訂檔案樹內容。"""
    def _make(files=None, mtimes=None):
        return FakeSFTPClient(files=files, mtimes=mtimes)
    return _make


def make_downloader(tmp_path, logger, **overrides):
    """建立一個不會真的連線的 SFTPDownloader，供測試直接操作內部方法。"""
    kwargs = dict(
        host="host.example.com",
        port=22,
        username="user",
        remote_path="/remote",
        local_path=str(tmp_path),
        logger=logger,
    )
    kwargs.update(overrides)
    return downloader_module.SFTPDownloader(**kwargs)


@pytest.fixture
def downloader_factory(tmp_path, logger):
    def _make(**overrides):
        return make_downloader(tmp_path, logger, **overrides)
    return _make


def make_uploader(tmp_path, logger, **overrides):
    """建立一個不會真的連線的 SFTPUploader，供測試直接操作內部方法。
    預設以 tmp_path 為本地來源、/remote 為遠端目的地。"""
    kwargs = dict(
        host="host.example.com",
        port=22,
        username="user",
        remote_path="/remote",
        local_path=str(tmp_path),
        logger=logger,
    )
    kwargs.update(overrides)
    return uploader_module.SFTPUploader(**kwargs)


@pytest.fixture
def uploader_factory(tmp_path, logger):
    def _make(**overrides):
        return make_uploader(tmp_path, logger, **overrides)
    return _make
