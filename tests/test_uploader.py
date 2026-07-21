"""uploader.py 單元測試：本地走訪、上傳決策、斷點續傳、忽略規則與整體流程。

沿用 conftest.py 的 FakeSFTPClient（支援串流寫入）與 uploader_factory；不碰真實網路。
"""

import hashlib
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uploader as up  # noqa: E402


def _write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _mtime(path: Path):
    return int(path.stat().st_mtime)


class TestListLocalFiles:
    def test_recursive_walk_collects_all_files_with_relative_paths(self, uploader_factory, fake_sftp_factory, tmp_path):
        _write(tmp_path / "a.txt", b"a")
        _write(tmp_path / "sub" / "b.txt", b"b")
        _write(tmp_path / "sub" / "deep" / "c.txt", b"c")
        d = uploader_factory(recursive=True)
        d.sftp = fake_sftp_factory(files={})

        files = d._list_local_files(tmp_path, "/remote")

        rels = sorted(rel for _, rel in files)
        assert rels == ["a.txt", "sub/b.txt", "sub/deep/c.txt"]

    def test_recursive_walk_creates_empty_remote_dirs(self, uploader_factory, fake_sftp_factory, tmp_path):
        (tmp_path / "emptydir").mkdir()
        _write(tmp_path / "a.txt", b"a")
        d = uploader_factory(recursive=True)
        d.sftp = fake_sftp_factory(files={})

        d._list_local_files(tmp_path, "/remote")

        # 即使 emptydir 底下沒有檔案，也要在遠端建立對應資料夾（鏡射下載端行為）。
        assert "/remote/emptydir" in d.sftp.dirs

    def test_single_layer_skips_subdirectories(self, uploader_factory, fake_sftp_factory, tmp_path):
        _write(tmp_path / "a.txt", b"a")
        _write(tmp_path / "sub" / "b.txt", b"b")
        d = uploader_factory(recursive=False)
        d.sftp = fake_sftp_factory(files={})

        files = d._list_local_files(tmp_path, "/remote")

        assert sorted(rel for _, rel in files) == ["a.txt"]

    def test_single_file_source(self, uploader_factory, fake_sftp_factory, tmp_path):
        target = tmp_path / "only.txt"
        _write(target, b"x")
        d = uploader_factory()
        d.sftp = fake_sftp_factory(files={})

        files = d._list_local_files(target, "/remote")

        assert [rel for _, rel in files] == ["only.txt"]

    def test_manifest_file_is_excluded_from_upload(self, uploader_factory, fake_sftp_factory, tmp_path):
        _write(tmp_path / "a.txt", b"a")
        _write(tmp_path / up.UPLOAD_MANIFEST_FILENAME, b"{}")
        _write(tmp_path / up.MANIFEST_FILENAME, b"{}")
        d = uploader_factory(recursive=True)
        d.sftp = fake_sftp_factory(files={})

        files = d._list_local_files(tmp_path, "/remote")

        assert sorted(rel for _, rel in files) == ["a.txt"]

    def test_ignore_rules_skip_matching_files(self, uploader_factory, fake_sftp_factory, tmp_path):
        ignore = tmp_path / "up_ignore.txt"
        ignore.write_text("*.log\n", encoding="utf-8")
        _write(tmp_path / "keep.txt", b"k")
        _write(tmp_path / "skip.log", b"s")
        d = uploader_factory(recursive=True, ignore_file=str(ignore))
        d._ignore_spec = d._load_ignore_spec()
        d.sftp = fake_sftp_factory(files={})

        files = d._list_local_files(tmp_path, "/remote")

        assert sorted(rel for _, rel in files) == ["keep.txt", "up_ignore.txt"]


class TestNextRemoteDuplicatePath:
    def test_first_duplicate_uses_suffix(self, uploader_factory, fake_sftp_factory):
        d = uploader_factory(duplicate_suffix="copy")
        d.sftp = fake_sftp_factory(files={"/remote/a.txt": b"x"})
        assert d._next_remote_duplicate_path("/remote/a.txt") == "/remote/a_copy.txt"

    def test_increments_when_duplicate_already_exists(self, uploader_factory, fake_sftp_factory):
        d = uploader_factory(duplicate_suffix="copy")
        d.sftp = fake_sftp_factory(files={"/remote/a.txt": b"x", "/remote/a_copy.txt": b"y"})
        assert d._next_remote_duplicate_path("/remote/a.txt") == "/remote/a_copy1.txt"


class TestUploadOneFileFresh:
    def test_fresh_upload_writes_remote_and_records_manifest(self, uploader_factory, fake_sftp_factory, tmp_path):
        content = b"hello world"
        local = tmp_path / "a.txt"
        _write(local, content)
        d = uploader_factory()
        d.sftp = fake_sftp_factory(files={})

        result = d._upload_one_file(local, "a.txt", "/remote", tmp_path)

        assert result == "uploaded"
        assert d.sftp.files["/remote/a.txt"] == content
        assert d._manifest["a.txt"]["local_bytes"] == len(content)
        assert d._manifest["a.txt"]["local_sha256"] == hashlib.sha256(content).hexdigest()

    def test_fresh_upload_creates_remote_parent_dirs(self, uploader_factory, fake_sftp_factory, tmp_path):
        local = tmp_path / "b.txt"
        _write(local, b"data")
        d = uploader_factory()
        d.sftp = fake_sftp_factory(files={})

        d._upload_one_file(local, "sub/deep/b.txt", "/remote", tmp_path)

        assert d.sftp.files["/remote/sub/deep/b.txt"] == b"data"
        assert "/remote/sub/deep" in d.sftp.dirs


class TestUploadOneFileSkip:
    def test_skips_when_remote_matches_and_manifest_unchanged(self, uploader_factory, fake_sftp_factory, tmp_path):
        content = b"unchanged content"
        local = tmp_path / "a.txt"
        _write(local, content)
        d = uploader_factory()
        d.sftp = fake_sftp_factory(files={})

        first = d._upload_one_file(local, "a.txt", "/remote", tmp_path)
        second = d._upload_one_file(local, "a.txt", "/remote", tmp_path)

        assert first == "uploaded"
        assert second == "skipped"


class TestUploadOneFileUpdated:
    def test_overwrites_when_local_updated(self, uploader_factory, fake_sftp_factory, tmp_path):
        local = tmp_path / "a.txt"
        _write(local, b"NEWCONTENT")  # 與遠端同長度、內容不同
        d = uploader_factory(duplicate_mode="overwrite")
        d.sftp = fake_sftp_factory(files={"/remote/a.txt": b"OLDCONTENT"})
        # 版本紀錄記的是舊 mtime，與目前本地 mtime 不符 → 視為已更新，覆蓋上傳。
        d._manifest = {"a.txt": {"size": len(b"NEWCONTENT"), "mtime": 1}}

        result = d._upload_one_file(local, "a.txt", "/remote", tmp_path)

        assert result == "uploaded"
        assert d.sftp.files["/remote/a.txt"] == b"NEWCONTENT"

    def test_duplicate_mode_saves_new_remote_file(self, uploader_factory, fake_sftp_factory, tmp_path):
        local = tmp_path / "a.txt"
        _write(local, b"a much longer new content")
        d = uploader_factory(duplicate_mode="duplicate", duplicate_suffix="copy")
        d.sftp = fake_sftp_factory(files={"/remote/a.txt": b"short"})

        result = d._upload_one_file(local, "a.txt", "/remote", tmp_path)

        assert result == "uploaded"
        assert d.sftp.files["/remote/a.txt"] == b"short"  # 舊檔不動
        assert d.sftp.files["/remote/a_copy.txt"] == b"a much longer new content"


class TestUploadOneFileResume:
    def test_resumes_from_partial_remote_when_prefix_verified(self, uploader_factory, fake_sftp_factory, tmp_path):
        content = b"Z" * (up.CHUNK_SIZE * 3)
        partial = up.CHUNK_SIZE  # 已上傳 1/3
        local = tmp_path / "big.bin"
        _write(local, content)
        d = uploader_factory()
        d.sftp = fake_sftp_factory(files={"/remote/big.bin": content[:partial]})
        d._manifest = {
            "big.bin": {
                "size": len(content),
                "mtime": _mtime(local),
                "local_sha256": hashlib.sha256(content[:partial]).hexdigest(),
                "local_bytes": partial,
            }
        }

        result = d._upload_one_file(local, "big.bin", "/remote", tmp_path)

        assert result == "uploaded"
        assert d.sftp.files["/remote/big.bin"] == content

    def test_reupload_from_scratch_when_prefix_hash_mismatches(self, uploader_factory, fake_sftp_factory, tmp_path):
        content = b"Q" * (up.CHUNK_SIZE * 2)
        partial = up.CHUNK_SIZE
        local = tmp_path / "big.bin"
        _write(local, content)
        d = uploader_factory()
        d.sftp = fake_sftp_factory(files={"/remote/big.bin": content[:partial]})
        # 紀錄的雜湊與實際本地前綴不符 → 不可續傳，整份重新上傳並覆蓋。
        d._manifest = {
            "big.bin": {
                "size": len(content),
                "mtime": _mtime(local),
                "local_sha256": "deadbeef",
                "local_bytes": partial,
            }
        }

        result = d._upload_one_file(local, "big.bin", "/remote", tmp_path)

        assert result == "uploaded"
        assert d.sftp.files["/remote/big.bin"] == content


class TestUploadOneFileCheckpointing:
    def test_checkpoint_persists_progress_during_transfer(self, uploader_factory, fake_sftp_factory, tmp_path):
        content = b"X" * (up.CHUNK_SIZE * 15)
        local = tmp_path / "big.bin"
        _write(local, content)
        d = uploader_factory()
        d.sftp = fake_sftp_factory(files={})

        d._upload_one_file(local, "big.bin", "/remote", tmp_path)

        manifest = d._load_manifest(tmp_path)
        assert manifest["big.bin"]["local_bytes"] == len(content)
        assert manifest["big.bin"]["local_sha256"] == hashlib.sha256(content).hexdigest()


class TestRun:
    def _prepare(self, uploader_factory, fake_sftp_factory, files=None, **overrides):
        d = uploader_factory(wait_for_network=False, **overrides)
        fake = fake_sftp_factory(files=files or {})
        d._connect_with_retry = MagicMock(side_effect=lambda: setattr(d, "sftp", fake))
        d._close = MagicMock()
        return d, fake

    def test_run_uploads_all_files(self, uploader_factory, fake_sftp_factory, tmp_path):
        _write(tmp_path / "a.txt", b"aaa")
        _write(tmp_path / "sub" / "b.txt", b"bbb")
        d, fake = self._prepare(uploader_factory, fake_sftp_factory)

        ok = d.run()

        assert ok is True
        assert fake.files["/remote/a.txt"] == b"aaa"
        assert fake.files["/remote/sub/b.txt"] == b"bbb"

    def test_run_returns_false_when_source_missing(self, uploader_factory, fake_sftp_factory, tmp_path):
        d, fake = self._prepare(uploader_factory, fake_sftp_factory, local_path=str(tmp_path / "nope"))
        assert d.run() is False

    def test_run_retries_upload_on_connection_error(self, uploader_factory, fake_sftp_factory, tmp_path):
        _write(tmp_path / "a.txt", b"data")
        d, fake = self._prepare(uploader_factory, fake_sftp_factory)
        d._upload_one_file = MagicMock(side_effect=[OSError("dropped"), "uploaded"])

        ok = d.run()

        assert ok is True
        assert d._upload_one_file.call_count == 2

    def test_run_uses_first_path_when_remote_is_list(self, uploader_factory, fake_sftp_factory, tmp_path, caplog):
        _write(tmp_path / "a.txt", b"aaa")
        d, fake = self._prepare(uploader_factory, fake_sftp_factory, remote_path=["/first", "/second"])

        with caplog.at_level(logging.WARNING):
            ok = d.run()

        assert ok is True
        assert fake.files["/first/a.txt"] == b"aaa"
        assert any("僅支援單一目的地路徑" in r.message for r in caplog.records)
