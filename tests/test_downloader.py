"""downloader.py 單元測試：涵蓋 Happy Path、邊界條件與錯誤處理。"""

import hashlib
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import paramiko
import pytest

import downloader as dl
from conftest import FakeSFTPAttr


# ---------------------------------------------------------------------------
# format_size
# ---------------------------------------------------------------------------

class TestFormatSize:
    def test_zero_bytes_returns_b(self):
        assert dl.format_size(0) == "0.0B"

    def test_bytes_under_1024_returns_b(self):
        assert dl.format_size(512) == "512.0B"

    def test_exact_1024_rolls_over_to_kb(self):
        assert dl.format_size(1024) == "1.0KB"

    def test_megabyte_boundary(self):
        assert dl.format_size(1024 * 1024) == "1.0MB"

    def test_gigabyte_and_beyond_stays_gb(self):
        # 超過 GB 仍以 GB 為單位顯示（不會再往上換算 TB）。
        assert dl.format_size(1024 ** 4) == "1024.0GB"


# ---------------------------------------------------------------------------
# _retry_limit_reached
# ---------------------------------------------------------------------------

class TestRetryLimitReached:
    def test_none_means_unlimited(self, downloader_factory):
        d = downloader_factory(retry_count=None)
        assert d._retry_limit_reached(1) is False
        assert d._retry_limit_reached(10_000) is False

    def test_zero_means_unlimited(self, downloader_factory):
        d = downloader_factory(retry_count=0)
        assert d._retry_limit_reached(999) is False

    def test_negative_means_unlimited(self, downloader_factory):
        d = downloader_factory(retry_count=-5)
        assert d._retry_limit_reached(999) is False

    def test_positive_limit_not_reached_at_boundary(self, downloader_factory):
        d = downloader_factory(retry_count=3)
        assert d._retry_limit_reached(3) is False

    def test_positive_limit_reached_just_over_boundary(self, downloader_factory):
        d = downloader_factory(retry_count=3)
        assert d._retry_limit_reached(4) is True


# ---------------------------------------------------------------------------
# _connect
# ---------------------------------------------------------------------------

class TestConnect:
    @patch("downloader.paramiko.SSHClient")
    def test_password_auth_connects_and_configures_timeouts(self, mock_ssh_client_cls, downloader_factory):
        mock_client = MagicMock()
        mock_ssh_client_cls.return_value = mock_client
        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp
        mock_transport = MagicMock()
        mock_client.get_transport.return_value = mock_transport

        d = downloader_factory(password="secret")
        d._connect()

        mock_client.connect.assert_called_once_with(
            hostname="host.example.com", port=22, username="user", timeout=15, password="secret"
        )
        mock_sftp.get_channel.return_value.settimeout.assert_called_once_with(dl.SOCKET_TIMEOUT)
        mock_transport.set_keepalive.assert_called_once_with(dl.KEEPALIVE_INTERVAL)
        assert d.client is mock_client
        assert d.sftp is mock_sftp

    @patch("downloader.paramiko.SSHClient")
    def test_key_file_auth_used_instead_of_password(self, mock_ssh_client_cls, downloader_factory):
        mock_client = MagicMock()
        mock_ssh_client_cls.return_value = mock_client

        d = downloader_factory(key_file="/home/user/.ssh/id_rsa", password="should-be-ignored")
        d._connect()

        _, kwargs = mock_client.connect.call_args
        assert kwargs["key_filename"] == "/home/user/.ssh/id_rsa"
        assert "password" not in kwargs

    @patch("downloader.paramiko.SSHClient")
    def test_sets_auto_add_host_key_policy(self, mock_ssh_client_cls, downloader_factory):
        mock_client = MagicMock()
        mock_ssh_client_cls.return_value = mock_client

        d = downloader_factory(password="secret")
        d._connect()

        assert mock_client.set_missing_host_key_policy.call_args[0][0].__class__ is paramiko.AutoAddPolicy


# ---------------------------------------------------------------------------
# _connect_with_retry
# ---------------------------------------------------------------------------

class TestConnectWithRetry:
    def test_succeeds_on_first_try(self, downloader_factory):
        d = downloader_factory()
        d._connect = MagicMock()
        d._connect_with_retry()
        d._connect.assert_called_once()

    def test_authentication_exception_raises_immediately_without_retry(self, downloader_factory):
        d = downloader_factory(retry_count=5)
        d._connect = MagicMock(side_effect=paramiko.AuthenticationException("bad creds"))
        with pytest.raises(paramiko.AuthenticationException):
            d._connect_with_retry()
        d._connect.assert_called_once()

    def test_retries_after_transient_error_then_succeeds(self, downloader_factory):
        d = downloader_factory(retry_count=5, wait_for_network=False)
        d._connect = MagicMock(side_effect=[OSError("refused"), OSError("refused"), None])
        d._connect_with_retry()
        assert d._connect.call_count == 3

    def test_raises_after_exceeding_retry_limit(self, downloader_factory):
        d = downloader_factory(retry_count=2, wait_for_network=False)
        d._connect = MagicMock(side_effect=paramiko.SSHException("still down"))
        with pytest.raises(paramiko.SSHException):
            d._connect_with_retry()
        assert d._connect.call_count == 3  # 初次 + 2 次重試後才放棄

    def test_auto_reconnect_disabled_raises_immediately(self, downloader_factory):
        d = downloader_factory(auto_reconnect=False)
        d._connect = MagicMock(side_effect=OSError("refused"))
        with pytest.raises(OSError):
            d._connect_with_retry()
        d._connect.assert_called_once()

    def test_waits_for_network_between_retries_when_enabled(self, downloader_factory):
        d = downloader_factory(retry_count=3, wait_for_network=True)
        d._connect = MagicMock(side_effect=[OSError("refused"), None])
        d._wait_for_network = MagicMock()
        d._connect_with_retry()
        d._wait_for_network.assert_called_once()

    def test_does_not_wait_for_network_when_disabled(self, downloader_factory):
        d = downloader_factory(retry_count=3, wait_for_network=False)
        d._connect = MagicMock(side_effect=[OSError("refused"), None])
        d._wait_for_network = MagicMock()
        d._connect_with_retry()
        d._wait_for_network.assert_not_called()


# ---------------------------------------------------------------------------
# _wait_for_network
# ---------------------------------------------------------------------------

class TestWaitForNetwork:
    @patch("downloader.socket.create_connection")
    def test_returns_immediately_when_reachable(self, mock_create_conn, downloader_factory):
        mock_create_conn.return_value.__enter__ = MagicMock()
        mock_create_conn.return_value.__exit__ = MagicMock(return_value=False)
        d = downloader_factory()
        d._wait_for_network()
        mock_create_conn.assert_called_once()

    @patch("downloader.socket.create_connection")
    def test_retries_until_reachable(self, mock_create_conn, downloader_factory):
        ok_ctx = MagicMock()
        ok_ctx.__enter__ = MagicMock()
        ok_ctx.__exit__ = MagicMock(return_value=False)
        mock_create_conn.side_effect = [OSError("unreachable"), OSError("unreachable"), ok_ctx]
        d = downloader_factory(retry_delay=0)
        d._wait_for_network()
        assert mock_create_conn.call_count == 3


# ---------------------------------------------------------------------------
# _close
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_with_none_client_and_sftp_does_not_raise(self, downloader_factory):
        d = downloader_factory()
        d.sftp = None
        d.client = None
        d._close()  # 不應拋出例外

    def test_close_swallows_exceptions_from_sftp_and_client(self, downloader_factory):
        d = downloader_factory()
        d.sftp = MagicMock()
        d.sftp.close.side_effect = Exception("already closed")
        d.client = MagicMock()
        d.client.close.side_effect = Exception("already closed")
        d._close()  # 不應向外拋出
        d.sftp.close.assert_called_once()
        d.client.close.assert_called_once()


# ---------------------------------------------------------------------------
# _list_remote_files / _walk_remote_dir
# ---------------------------------------------------------------------------

class TestListRemoteFiles:
    def test_single_remote_file_returns_one_entry(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = downloader_factory(remote_path="/remote/report.csv")
        d.sftp = fake_sftp_factory(files={"/remote/report.csv": b"data"})
        files = d._list_remote_files("/remote/report.csv", tmp_path)
        assert files == [("/remote/report.csv", "report.csv")]

    def test_recursive_directory_lists_all_nested_files(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = downloader_factory(recursive=True)
        d.sftp = fake_sftp_factory(files={
            "/remote/a.txt": b"a",
            "/remote/sub/b.txt": b"b",
            "/remote/sub/deeper/c.txt": b"c",
        })
        files = d._list_remote_files("/remote", tmp_path)
        rels = sorted(rel for _, rel in files)
        assert rels == ["a.txt", "sub/b.txt", "sub/deeper/c.txt"]

    def test_recursive_creates_empty_subdirectories_locally(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = downloader_factory(recursive=True)
        sftp = fake_sftp_factory(files={"/remote/a.txt": b"a"})
        # 手動補一個沒有任何檔案的空資料夾（FakeSFTPClient 靠檔案路徑推導資料夾，這裡直接擴充 listdir_attr 行為）
        original_listdir = sftp.listdir_attr

        def listdir_with_empty_dir(path):
            entries = original_listdir(path)
            if path.rstrip("/") == "/remote":
                entries.append(FakeSFTPAttr("empty_sub", True))
            return entries

        sftp.listdir_attr = listdir_with_empty_dir
        d.sftp = sftp
        files = d._list_remote_files("/remote", tmp_path)
        assert [rel for _, rel in files] == ["a.txt"]
        assert (tmp_path / "empty_sub").is_dir()

    def test_single_level_mode_skips_subdirectories(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = downloader_factory(recursive=False)
        d.sftp = fake_sftp_factory(files={"/remote/a.txt": b"a", "/remote/sub/b.txt": b"b"})
        files = d._list_remote_files("/remote", tmp_path)
        assert [rel for _, rel in files] == ["a.txt"]

    def test_single_level_mode_logs_skipped_directory_count(self, downloader_factory, fake_sftp_factory, tmp_path, caplog):
        d = downloader_factory(recursive=False)
        d.sftp = fake_sftp_factory(files={"/remote/a.txt": b"a", "/remote/sub/b.txt": b"b"})
        with caplog.at_level(logging.INFO):
            d._list_remote_files("/remote", tmp_path)
        assert any("略過 1 個子資料夾" in r.message for r in caplog.records)

    def test_remote_path_not_found_raises_file_not_found(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = downloader_factory()
        d.sftp = fake_sftp_factory(files={})
        with pytest.raises(FileNotFoundError):
            d._list_remote_files("/remote/missing", tmp_path)


# ---------------------------------------------------------------------------
# 下載忽略設定檔（_load_ignore_spec / _is_ignored / 列表過濾）
# ---------------------------------------------------------------------------

class TestIgnoreSpec:
    def _make_with_ignore(self, downloader_factory, tmp_path, rules, **overrides):
        ignore_path = tmp_path / "download_ignore.txt"
        ignore_path.write_text(rules, encoding="utf-8")
        d = downloader_factory(ignore_file=str(ignore_path), **overrides)
        d._ignore_spec = d._load_ignore_spec()
        return d

    def test_no_ignore_file_configured_returns_none(self, downloader_factory):
        d = downloader_factory()
        assert d._load_ignore_spec() is None

    def test_missing_ignore_file_means_no_ignore_and_logs_info(self, downloader_factory, tmp_path, caplog):
        d = downloader_factory(ignore_file=str(tmp_path / "not_exist.txt"))
        with caplog.at_level(logging.INFO):
            assert d._load_ignore_spec() is None
        assert any("忽略設定檔不存在" in r.message for r in caplog.records)

    def test_invalid_line_is_skipped_with_warning_but_other_rules_still_apply(self, downloader_factory, tmp_path, caplog):
        # "!" 單獨一行是不合法的 gitignore 規則，應跳過並警告；"*.tmp" 仍要生效。
        with caplog.at_level(logging.WARNING):
            d = self._make_with_ignore(downloader_factory, tmp_path, "!\n*.tmp\n")
        assert any("格式錯誤" in r.message for r in caplog.records)
        assert d._is_ignored("a.tmp")
        assert not d._is_ignored("a.txt")

    def test_utf8_bom_and_crlf_do_not_break_first_rule(self, downloader_factory, tmp_path):
        """Windows 記事本以 UTF-8 存檔常帶 BOM 且用 CRLF 換行；BOM 若沒去除會黏在
        第一行規則前面，導致第一條規則永遠比對不到（實際回報過的問題）。"""
        ignore_path = tmp_path / "download_ignore.txt"
        ignore_path.write_bytes("a.txt\r\nb.txt\r\n".encode("utf-8-sig"))
        d = downloader_factory(ignore_file=str(ignore_path))
        d._ignore_spec = d._load_ignore_spec()
        assert d._is_ignored("a.txt")  # 第一行規則（緊接在 BOM 後）也要生效
        assert d._is_ignored("b.txt")
        assert not d._is_ignored("c.txt")

    def test_comments_and_blank_lines_do_not_warn(self, downloader_factory, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            self._make_with_ignore(downloader_factory, tmp_path, "# 註解\n\n*.tmp\n")
        assert not any("格式錯誤" in r.message for r in caplog.records)

    def test_negation_rule_re_includes_file(self, downloader_factory, tmp_path):
        d = self._make_with_ignore(downloader_factory, tmp_path, "*.tmp\n!keep.tmp\n")
        assert d._is_ignored("a.tmp")
        assert not d._is_ignored("keep.tmp")

    def test_recursive_listing_filters_ignored_files(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = self._make_with_ignore(downloader_factory, tmp_path, "*.tmp\n", recursive=True)
        d.sftp = fake_sftp_factory(files={
            "/remote/a.txt": b"a",
            "/remote/b.tmp": b"b",
            "/remote/sub/c.tmp": b"c",
            "/remote/sub/d.txt": b"d",
        })
        files = d._list_remote_files("/remote", tmp_path)
        assert sorted(rel for _, rel in files) == ["a.txt", "sub/d.txt"]

    def test_recursive_listing_prunes_ignored_directory_entirely(self, downloader_factory, fake_sftp_factory, tmp_path, caplog):
        d = self._make_with_ignore(downloader_factory, tmp_path, "logs/\n", recursive=True)
        d.sftp = fake_sftp_factory(files={
            "/remote/a.txt": b"a",
            "/remote/logs/x.log": b"x",
            "/remote/logs/deep/y.log": b"y",
        })
        with caplog.at_level(logging.INFO):
            files = d._list_remote_files("/remote", tmp_path)
        assert [rel for _, rel in files] == ["a.txt"]
        # 整棵資料夾剪枝：本地端不建立被忽略的資料夾
        assert not (tmp_path / "logs").exists()
        assert any("略過資料夾" in r.message for r in caplog.records)

    def test_single_level_listing_filters_ignored_files(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = self._make_with_ignore(downloader_factory, tmp_path, "*.tmp\n", recursive=False)
        d.sftp = fake_sftp_factory(files={"/remote/a.txt": b"a", "/remote/b.tmp": b"b"})
        files = d._list_remote_files("/remote", tmp_path)
        assert [rel for _, rel in files] == ["a.txt"]

    def test_single_remote_file_matching_rule_is_ignored(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = self._make_with_ignore(downloader_factory, tmp_path, "report.csv\n")
        d.sftp = fake_sftp_factory(files={"/remote/report.csv": b"data"})
        assert d._list_remote_files("/remote/report.csv", tmp_path) == []

    def test_no_spec_loaded_nothing_is_ignored(self, downloader_factory):
        d = downloader_factory()
        assert not d._is_ignored("anything.txt")

    def test_run_loads_ignore_spec_and_skips_ignored_files(self, downloader_factory, fake_sftp_factory, tmp_path):
        ignore_path = tmp_path / "download_ignore.txt"
        ignore_path.write_text("*.tmp\n", encoding="utf-8")
        d = downloader_factory(wait_for_network=False, ignore_file=str(ignore_path))
        d._connect_with_retry = MagicMock(side_effect=lambda: setattr(
            d, "sftp",
            fake_sftp_factory(files={"/remote/a.txt": b"A", "/remote/b.tmp": b"B"},
                              mtimes={"/remote/a.txt": 1, "/remote/b.tmp": 2}),
        ))
        d._close = MagicMock()
        assert d.run() is True
        assert (Path(d.local_path) / "a.txt").exists()
        assert not (Path(d.local_path) / "b.tmp").exists()


# ---------------------------------------------------------------------------
# manifest load / save
# ---------------------------------------------------------------------------

class TestManifestPersistence:
    def test_load_missing_manifest_returns_empty_dict(self, downloader_factory, tmp_path):
        d = downloader_factory()
        assert d._load_manifest(tmp_path) == {}

    def test_save_then_load_round_trips(self, downloader_factory, tmp_path):
        d = downloader_factory()
        d._manifest = {"a.txt": {"size": 10, "mtime": 123}}
        d._save_manifest(tmp_path)
        loaded = d._load_manifest(tmp_path)
        assert loaded == {"a.txt": {"size": 10, "mtime": 123}}

    def test_load_corrupt_json_returns_empty_dict_and_warns(self, downloader_factory, tmp_path, caplog):
        d = downloader_factory()
        manifest_path = d._manifest_path(tmp_path)
        manifest_path.write_text("{not valid json", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            result = d._load_manifest(tmp_path)
        assert result == {}
        assert any("讀取失敗" in r.message for r in caplog.records)

    def test_save_failure_is_caught_and_logged(self, downloader_factory, tmp_path, caplog):
        d = downloader_factory()
        d._manifest = {"a.txt": {"size": 1}}
        with patch("builtins.open", side_effect=OSError("disk full")):
            with caplog.at_level(logging.WARNING):
                d._save_manifest(tmp_path)  # 不應拋出例外
        assert any("寫入失敗" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _next_duplicate_path
# ---------------------------------------------------------------------------

class TestNextDuplicatePath:
    def test_no_conflict_returns_plain_copy_name(self, downloader_factory, tmp_path):
        d = downloader_factory(duplicate_suffix="copy")
        target = tmp_path / "report.csv"
        result = d._next_duplicate_path(target)
        assert result == tmp_path / "report_copy.csv"

    def test_one_conflict_returns_numbered_suffix(self, downloader_factory, tmp_path):
        d = downloader_factory(duplicate_suffix="copy")
        target = tmp_path / "report.csv"
        (tmp_path / "report_copy.csv").write_bytes(b"x")
        result = d._next_duplicate_path(target)
        assert result == tmp_path / "report_copy1.csv"

    def test_multiple_conflicts_increment_correctly(self, downloader_factory, tmp_path):
        d = downloader_factory(duplicate_suffix="copy")
        target = tmp_path / "report.csv"
        (tmp_path / "report_copy.csv").write_bytes(b"x")
        (tmp_path / "report_copy1.csv").write_bytes(b"x")
        (tmp_path / "report_copy2.csv").write_bytes(b"x")
        result = d._next_duplicate_path(target)
        assert result == tmp_path / "report_copy3.csv"

    def test_custom_suffix_is_respected(self, downloader_factory, tmp_path):
        d = downloader_factory(duplicate_suffix="backup")
        target = tmp_path / "report.csv"
        result = d._next_duplicate_path(target)
        assert result == tmp_path / "report_backup.csv"


# ---------------------------------------------------------------------------
# _hash_local_file
# ---------------------------------------------------------------------------

class TestHashLocalFile:
    def test_computes_correct_sha256(self, downloader_factory, tmp_path):
        d = downloader_factory()
        content = b"hello world" * 1000
        f = tmp_path / "data.bin"
        f.write_bytes(content)
        result = d._hash_local_file(f)
        assert result.hexdigest() == hashlib.sha256(content).hexdigest()

    def test_empty_file_hashes_to_empty_digest(self, downloader_factory, tmp_path):
        d = downloader_factory()
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        result = d._hash_local_file(f)
        assert result.hexdigest() == hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# _download_one_file — the core state machine
# ---------------------------------------------------------------------------

class TestDownloadOneFileFreshDownload:
    def test_new_file_downloads_full_content(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = downloader_factory()
        d.sftp = fake_sftp_factory(files={"/remote/a.txt": b"hello world"}, mtimes={"/remote/a.txt": 1000})
        result = d._download_one_file("/remote/a.txt", "a.txt", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "a.txt").read_bytes() == b"hello world"

    def test_nested_relative_path_creates_parent_directories(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = downloader_factory()
        d.sftp = fake_sftp_factory(files={"/remote/sub/b.txt": b"nested"}, mtimes={"/remote/sub/b.txt": 1000})
        d._download_one_file("/remote/sub/b.txt", "sub/b.txt", tmp_path)
        assert (tmp_path / "sub" / "b.txt").read_bytes() == b"nested"


class TestDownloadOneFileResumeDisabled:
    def test_resume_disabled_overwrite_mode_replaces_in_place(self, downloader_factory, fake_sftp_factory, tmp_path):
        (tmp_path / "f.json").write_bytes(b"OLD")
        d = downloader_factory(resume=False, duplicate_mode="overwrite")
        d.sftp = fake_sftp_factory(files={"/remote/f.json": b"NEW-DATA"}, mtimes={"/remote/f.json": 1000})
        result = d._download_one_file("/remote/f.json", "f.json", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.json").read_bytes() == b"NEW-DATA"
        assert not (tmp_path / "f_copy.json").exists()

    def test_resume_disabled_duplicate_mode_creates_new_file(self, downloader_factory, fake_sftp_factory, tmp_path):
        (tmp_path / "f.json").write_bytes(b"OLD")
        d = downloader_factory(resume=False, duplicate_mode="duplicate")
        d.sftp = fake_sftp_factory(files={"/remote/f.json": b"NEW-DATA"}, mtimes={"/remote/f.json": 1000})
        result = d._download_one_file("/remote/f.json", "f.json", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.json").read_bytes() == b"OLD"
        assert (tmp_path / "f_copy.json").read_bytes() == b"NEW-DATA"


class TestDownloadOneFileSameSize:
    def test_no_manifest_entry_and_same_size_skips_and_bootstraps_manifest(self, downloader_factory, fake_sftp_factory, tmp_path):
        (tmp_path / "legacy.bin").write_bytes(b"SAMESIZE12")
        d = downloader_factory()
        d.sftp = fake_sftp_factory(files={"/remote/legacy.bin": b"SAMESIZE99"}, mtimes={"/remote/legacy.bin": 5000})
        result = d._download_one_file("/remote/legacy.bin", "legacy.bin", tmp_path)
        assert result == "skipped"
        assert (tmp_path / "legacy.bin").read_bytes() == b"SAMESIZE12"  # 內容未被覆蓋
        assert d._manifest["legacy.bin"] == {"size": 10, "mtime": 5000}

    def test_manifest_confirms_unchanged_skips(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = downloader_factory(duplicate_mode="duplicate")
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": b"STABLE"}, mtimes={"/remote/f.bin": 1000})
        d._download_one_file("/remote/f.bin", "f.bin", tmp_path)  # 建立紀錄
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)  # 第二次應略過
        assert result == "skipped"
        assert not (tmp_path / "f_copy.bin").exists()

    def test_same_size_but_manifest_mtime_mismatch_overwrite_mode_replaces(self, downloader_factory, fake_sftp_factory, tmp_path):
        """驗證原本 manifest 功能的核心目的：大小相同、內容其實已更新（用 mtime 判斷出來）。"""
        d = downloader_factory(duplicate_mode="overwrite")
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": b"HELLO"}, mtimes={"/remote/f.bin": 1000})
        d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": b"WORLD"}, mtimes={"/remote/f.bin": 2000})  # 同大小、不同 mtime
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.bin").read_bytes() == b"WORLD"

    def test_same_size_but_manifest_mtime_mismatch_duplicate_mode_creates_new_file(self, downloader_factory, fake_sftp_factory, tmp_path):
        d = downloader_factory(duplicate_mode="duplicate")
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": b"HELLO"}, mtimes={"/remote/f.bin": 1000})
        d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": b"WORLD"}, mtimes={"/remote/f.bin": 2000})
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.bin").read_bytes() == b"HELLO"  # 原檔不動
        assert (tmp_path / "f_copy.bin").read_bytes() == b"WORLD"


class TestDownloadOneFileLocalBigger:
    def test_local_bigger_overwrite_mode_replaces_in_place(self, downloader_factory, fake_sftp_factory, tmp_path):
        (tmp_path / "f.bin").write_bytes(b"THIS-LOCAL-FILE-IS-QUITE-LONG")
        d = downloader_factory(duplicate_mode="overwrite")
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": b"SHORT"}, mtimes={"/remote/f.bin": 1000})
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.bin").read_bytes() == b"SHORT"
        assert not (tmp_path / "f_copy.bin").exists()

    def test_local_bigger_duplicate_mode_creates_new_file(self, downloader_factory, fake_sftp_factory, tmp_path):
        (tmp_path / "f.bin").write_bytes(b"THIS-LOCAL-FILE-IS-QUITE-LONG")
        d = downloader_factory(duplicate_mode="duplicate")
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": b"SHORT"}, mtimes={"/remote/f.bin": 1000})
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.bin").read_bytes() == b"THIS-LOCAL-FILE-IS-QUITE-LONG"
        assert (tmp_path / "f_copy.bin").read_bytes() == b"SHORT"


class TestDownloadOneFileLocalSmallerDuplicateMode:
    def test_duplicate_mode_never_resumes_always_creates_new_file(self, downloader_factory, fake_sftp_factory, tmp_path):
        (tmp_path / "f.bin").write_bytes(b"SMALL")
        d = downloader_factory(duplicate_mode="duplicate")
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": b"MUCH-BIGGER-CONTENT"}, mtimes={"/remote/f.bin": 1000})
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.bin").read_bytes() == b"SMALL"
        assert (tmp_path / "f_copy.bin").read_bytes() == b"MUCH-BIGGER-CONTENT"


class TestDownloadOneFileLocalSmallerOverwriteMode:
    def test_verified_same_version_resumes_via_append(self, downloader_factory, fake_sftp_factory, tmp_path):
        full_content = b"AAAAABBBBBCCCCCDDDDDEEEEE"
        (tmp_path / "f.bin").write_bytes(full_content[:10])
        d = downloader_factory(duplicate_mode="overwrite")
        d._manifest = {
            "f.bin": {
                "size": len(full_content),
                "mtime": 1000,
                "local_sha256": hashlib.sha256(full_content[:10]).hexdigest(),
                "local_bytes": 10,
            }
        }
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": full_content}, mtimes={"/remote/f.bin": 1000})
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.bin").read_bytes() == full_content
        assert not (tmp_path / "f_copy.bin").exists()

    def test_hash_mismatch_tampered_local_file_falls_back_to_full_redownload(self, downloader_factory, fake_sftp_factory, tmp_path):
        full_content = b"ORIGINAL-CONTENT-DATA"
        (tmp_path / "f.bin").write_bytes(b"TAMPERED12")  # 與紀錄檔中的雜湊對不上
        d = downloader_factory(duplicate_mode="overwrite")
        d._manifest = {
            "f.bin": {
                "size": len(full_content),
                "mtime": 1000,
                "local_sha256": hashlib.sha256(b"DIFFERENT-PREFIX").hexdigest(),
                "local_bytes": 10,
            }
        }
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": full_content}, mtimes={"/remote/f.bin": 1000})
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.bin").read_bytes() == full_content

    def test_remote_version_changed_falls_back_to_full_redownload(self, downloader_factory, fake_sftp_factory, tmp_path):
        """就算本地雜湊本身沒問題，只要遠端版本（size/mtime）跟紀錄不符，就不能信任接續。"""
        old_full = b"OLD-VERSION-CONTENT"
        (tmp_path / "f.bin").write_bytes(old_full[:5])
        d = downloader_factory(duplicate_mode="overwrite")
        d._manifest = {
            "f.bin": {
                "size": len(old_full),
                "mtime": 1000,  # 舊版本的 mtime
                "local_sha256": hashlib.sha256(old_full[:5]).hexdigest(),
                "local_bytes": 5,
            }
        }
        new_full = b"BRAND-NEW-VERSION-CONTENT"
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": new_full}, mtimes={"/remote/f.bin": 9999})  # mtime 已變
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.bin").read_bytes() == new_full

    def test_no_checkpoint_at_all_conservatively_redownloads(self, downloader_factory, fake_sftp_factory, tmp_path):
        (tmp_path / "f.bin").write_bytes(b"SOME-OLD-STUFF")
        d = downloader_factory(duplicate_mode="overwrite")
        d._manifest = {}  # 完全沒有版本紀錄
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": b"COMPLETELY-DIFFERENT-BIGGER-CONTENT"}, mtimes={"/remote/f.bin": 5000})
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.bin").read_bytes() == b"COMPLETELY-DIFFERENT-BIGGER-CONTENT"

    def test_resume_only_reads_remaining_bytes_not_already_downloaded_portion(self, downloader_factory, fake_sftp_factory, tmp_path):
        """效能保證：驗證接續下載時不會重新從遠端讀取已下載的部分（只讀本機雜湊）。"""
        full_content = b"A" * 6000 + b"B" * 4000
        (tmp_path / "f.bin").write_bytes(full_content[:6000])
        d = downloader_factory(duplicate_mode="overwrite")
        d._manifest = {
            "f.bin": {
                "size": len(full_content),
                "mtime": 1000,
                "local_sha256": hashlib.sha256(full_content[:6000]).hexdigest(),
                "local_bytes": 6000,
            }
        }
        sftp = fake_sftp_factory(files={"/remote/f.bin": full_content}, mtimes={"/remote/f.bin": 1000})
        read_sizes = []
        original_open = sftp.open

        def tracking_open(path, mode="rb"):
            fake_file = original_open(path, mode)
            original_read = fake_file.read

            def tracked_read(n=-1):
                chunk = original_read(n)
                read_sizes.append(len(chunk))
                return chunk

            fake_file.read = tracked_read
            return fake_file

        sftp.open = tracking_open
        d.sftp = sftp
        result = d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        assert result == "downloaded"
        assert (tmp_path / "f.bin").read_bytes() == full_content
        assert sum(read_sizes) == 4000, "只應該從遠端讀取剩餘的 4000 bytes，不應重新讀取已下載的 6000 bytes"


class TestDownloadOneFileCheckpointing:
    def test_checkpoint_persists_progress_during_transfer(self, downloader_factory, fake_sftp_factory, tmp_path):
        # chunk 大小是 32768，構造夠大的檔案讓進度跨越多個 10% 門檻
        content = b"X" * (dl.CHUNK_SIZE * 15)
        d = downloader_factory()
        d.sftp = fake_sftp_factory(files={"/remote/big.bin": content}, mtimes={"/remote/big.bin": 42})
        d._download_one_file("/remote/big.bin", "big.bin", tmp_path)
        manifest = d._load_manifest(tmp_path)
        assert manifest["big.bin"]["local_bytes"] == len(content)
        assert manifest["big.bin"]["local_sha256"] == hashlib.sha256(content).hexdigest()

    def test_interrupted_transfer_still_checkpoints_partial_progress(self, downloader_factory, fake_sftp_factory, tmp_path):
        """下載中途丟例外時，finally 仍要存下已寫入的進度，讓下次重試能安全接續。"""
        content = b"Y" * (dl.CHUNK_SIZE * 3)
        d = downloader_factory()
        sftp = fake_sftp_factory(files={"/remote/f.bin": content}, mtimes={"/remote/f.bin": 77})

        call_count = {"n": 0}
        original_open = sftp.open

        def flaky_open(path, mode="rb"):
            fake_file = original_open(path, mode)
            original_read = fake_file.read

            def flaky_read(n=-1):
                call_count["n"] += 1
                if call_count["n"] == 2:
                    raise OSError("simulated dropped connection")
                return original_read(n)

            fake_file.read = flaky_read
            return fake_file

        sftp.open = flaky_open
        d.sftp = sftp

        with pytest.raises(OSError):
            d._download_one_file("/remote/f.bin", "f.bin", tmp_path)

        manifest = d._load_manifest(tmp_path)
        assert manifest["f.bin"]["local_bytes"] == dl.CHUNK_SIZE  # 只成功寫入了第一個 chunk
        partial_on_disk = (tmp_path / "f.bin").read_bytes()
        assert len(partial_on_disk) == dl.CHUNK_SIZE

    def test_progress_logged_and_increases_monotonically(self, downloader_factory, fake_sftp_factory, tmp_path, caplog):
        content = b"Z" * (dl.CHUNK_SIZE * 5)
        d = downloader_factory()
        d.sftp = fake_sftp_factory(files={"/remote/f.bin": content}, mtimes={"/remote/f.bin": 1})
        with caplog.at_level(logging.INFO):
            d._download_one_file("/remote/f.bin", "f.bin", tmp_path)
        pct_lines = [r.message for r in caplog.records if "進度" in r.message]
        percents = [int(line.split(":")[-1].strip().rstrip("%")) for line in pct_lines]
        assert percents == sorted(percents)
        assert percents[-1] == 100


# ---------------------------------------------------------------------------
# _upload_log_file
# ---------------------------------------------------------------------------

class TestUploadLogFile:
    def test_successful_upload(self, downloader_factory, fake_sftp_factory, tmp_path, logger):
        log_file = tmp_path / "run.csv"
        log_file.write_text("timestamp,message\n", encoding="utf-8")
        d = downloader_factory(remote_log_dir="/data/logs", log_file=str(log_file))
        d.logger.addHandler(logging.NullHandler())
        d._connect_with_retry = MagicMock()
        d.sftp = fake_sftp_factory(files={})
        d._upload_log_file()
        assert "/data/logs/run.csv" in d.sftp.files

    def test_upload_failure_is_caught_and_does_not_propagate(self, downloader_factory, tmp_path):
        log_file = tmp_path / "run.csv"
        log_file.write_text("data", encoding="utf-8")
        d = downloader_factory(remote_log_dir="/data/logs", log_file=str(log_file))
        d.logger.addHandler(logging.NullHandler())
        d._connect_with_retry = MagicMock(side_effect=OSError("network down"))
        d._upload_log_file()  # 不應拋出例外

    def test_close_called_even_after_failure(self, downloader_factory, tmp_path):
        log_file = tmp_path / "run.csv"
        log_file.write_text("data", encoding="utf-8")
        d = downloader_factory(remote_log_dir="/data/logs", log_file=str(log_file))
        d.logger.addHandler(logging.NullHandler())
        d._connect_with_retry = MagicMock(side_effect=OSError("network down"))
        d._close = MagicMock()
        d._upload_log_file()
        d._close.assert_called_once()


# ---------------------------------------------------------------------------
# run() — full orchestration
# ---------------------------------------------------------------------------

class TestRun:
    def _prepare(self, downloader_factory, fake_sftp_factory, files=None, mtimes=None, **overrides):
        d = downloader_factory(wait_for_network=False, **overrides)
        d._connect_with_retry = MagicMock(side_effect=lambda: setattr(d, "sftp", fake_sftp_factory(files=files or {}, mtimes=mtimes or {})))
        d._close = MagicMock()
        return d

    def test_successful_run_downloads_all_files_and_returns_true(self, downloader_factory, fake_sftp_factory):
        d = self._prepare(
            downloader_factory, fake_sftp_factory,
            files={"/remote/a.txt": b"A", "/remote/b.txt": b"B"},
            mtimes={"/remote/a.txt": 1, "/remote/b.txt": 2},
        )
        result = d.run()
        assert result is True

    def test_second_run_skips_already_downloaded_unchanged_file(self, downloader_factory, fake_sftp_factory):
        files = {"/remote/a.txt": b"A"}
        mtimes = {"/remote/a.txt": 1}
        d = self._prepare(downloader_factory, fake_sftp_factory, files=files, mtimes=mtimes)
        assert d.run() is True  # 第一次：全新下載

        d2 = self._prepare(downloader_factory, fake_sftp_factory, files=files, mtimes=mtimes, local_path=d.local_path)
        assert d2.run() is True  # 第二次：內容未變，應該略過而非重新下載

    def test_wait_for_network_called_when_enabled(self, downloader_factory, fake_sftp_factory):
        d = downloader_factory(wait_for_network=True)
        d._wait_for_network = MagicMock()
        d._connect_with_retry = MagicMock(side_effect=lambda: setattr(d, "sftp", fake_sftp_factory(files={})))
        d._close = MagicMock()
        d.run()
        d._wait_for_network.assert_called_once()

    def test_authentication_exception_returns_false_without_retry(self, downloader_factory):
        d = downloader_factory(wait_for_network=False)
        d._connect_with_retry = MagicMock(side_effect=paramiko.AuthenticationException("bad creds"))
        d._close = MagicMock()
        result = d.run()
        assert result is False
        d._connect_with_retry.assert_called_once()

    def test_remote_path_not_found_returns_false(self, downloader_factory, fake_sftp_factory):
        d = self._prepare(downloader_factory, fake_sftp_factory, files={})
        d.remote_path = "/remote/missing"
        result = d.run()
        assert result is False

    def test_listing_error_retries_then_succeeds(self, downloader_factory, fake_sftp_factory):
        d = downloader_factory(wait_for_network=False, retry_count=3)
        attempts = {"n": 0}
        good_sftp = fake_sftp_factory(files={"/remote/a.txt": b"A"}, mtimes={"/remote/a.txt": 1})

        def connect_side_effect():
            attempts["n"] += 1
            d.sftp = good_sftp

        d._connect_with_retry = MagicMock(side_effect=connect_side_effect)
        d._close = MagicMock()

        original_list = d._list_remote_files
        call_state = {"first": True}

        def flaky_list(remote_root, local_root):
            if call_state["first"]:
                call_state["first"] = False
                raise OSError("connection reset")
            return original_list(remote_root, local_root)

        d._list_remote_files = flaky_list
        result = d.run()
        assert result is True

    def test_listing_error_exceeds_retry_limit_returns_false(self, downloader_factory):
        d = downloader_factory(wait_for_network=False, retry_count=1)
        d._connect_with_retry = MagicMock()
        d._close = MagicMock()
        d._list_remote_files = MagicMock(side_effect=OSError("still broken"))
        result = d.run()
        assert result is False

    def test_permission_error_on_one_file_is_recorded_but_others_continue(self, downloader_factory, fake_sftp_factory):
        d = self._prepare(
            downloader_factory, fake_sftp_factory,
            files={"/remote/a.txt": b"A", "/remote/b.txt": b"B"},
            mtimes={"/remote/a.txt": 1, "/remote/b.txt": 2},
        )
        original_download = d._download_one_file

        def flaky_download(remote_file, rel_path, local_root):
            if rel_path == "a.txt":
                raise PermissionError("no write access")
            return original_download(remote_file, rel_path, local_root)

        d._download_one_file = flaky_download
        result = d.run()
        assert result is False  # 有檔案失敗，整體視為不完全成功

    def test_file_not_found_during_download_is_recorded_as_failure(self, downloader_factory, fake_sftp_factory):
        d = self._prepare(downloader_factory, fake_sftp_factory, files={"/remote/a.txt": b"A"}, mtimes={"/remote/a.txt": 1})
        d._download_one_file = MagicMock(side_effect=FileNotFoundError("gone"))
        result = d.run()
        assert result is False

    def test_connection_error_during_download_reconnects_and_succeeds(self, downloader_factory, fake_sftp_factory):
        d = self._prepare(downloader_factory, fake_sftp_factory, files={"/remote/a.txt": b"A"}, mtimes={"/remote/a.txt": 1}, retry_count=3)
        original_download = d._download_one_file
        state = {"failed_once": False}

        def flaky_download(remote_file, rel_path, local_root):
            if not state["failed_once"]:
                state["failed_once"] = True
                raise OSError("dropped")
            return original_download(remote_file, rel_path, local_root)

        d._download_one_file = flaky_download
        result = d.run()
        assert result is True
        assert d._connect_with_retry.call_count >= 2  # 初次連線 + 下載失敗後重連

    def test_connection_error_exceeds_retry_limit_marks_file_failed_but_continues(self, downloader_factory, fake_sftp_factory):
        d = self._prepare(
            downloader_factory, fake_sftp_factory,
            files={"/remote/a.txt": b"A", "/remote/b.txt": b"B"},
            mtimes={"/remote/a.txt": 1, "/remote/b.txt": 2},
            retry_count=1,
        )
        original_download = d._download_one_file

        def flaky_download(remote_file, rel_path, local_root):
            if rel_path == "a.txt":
                raise OSError("permanently broken")
            return original_download(remote_file, rel_path, local_root)

        d._download_one_file = flaky_download
        result = d.run()
        assert result is False
        # b.txt 仍應該成功下載
        assert (Path(d.local_path) / "b.txt").exists()

    def test_reconnect_failure_during_per_file_retry_marks_failed_and_continues(self, downloader_factory, fake_sftp_factory):
        d = self._prepare(
            downloader_factory, fake_sftp_factory,
            files={"/remote/a.txt": b"A", "/remote/b.txt": b"B"},
            mtimes={"/remote/a.txt": 1, "/remote/b.txt": 2},
            retry_count=3,
        )
        original_download = d._download_one_file
        original_connect = d._connect_with_retry
        state = {"a_failed": False}

        def flaky_download(remote_file, rel_path, local_root):
            if rel_path == "a.txt" and not state["a_failed"]:
                state["a_failed"] = True
                raise OSError("dropped")
            return original_download(remote_file, rel_path, local_root)

        def reconnect_side_effect():
            if state["a_failed"]:
                raise paramiko.SSHException("cannot reconnect")
            original_connect()

        d._download_one_file = flaky_download
        d._connect_with_retry = MagicMock(side_effect=reconnect_side_effect)
        result = d.run()
        assert result is False

    def test_unexpected_exception_aborts_task_and_returns_false(self, downloader_factory):
        d = downloader_factory(wait_for_network=False)
        d._connect_with_retry = MagicMock(side_effect=RuntimeError("totally unexpected"))
        d._close = MagicMock()
        result = d.run()
        assert result is False

    def test_close_always_called_even_on_exception(self, downloader_factory):
        d = downloader_factory(wait_for_network=False)
        d._connect_with_retry = MagicMock(side_effect=RuntimeError("boom"))
        d._close = MagicMock()
        d.run()
        d._close.assert_called_once()

    def test_upload_log_called_when_enabled(self, downloader_factory, fake_sftp_factory):
        d = self._prepare(
            downloader_factory, fake_sftp_factory,
            files={"/remote/a.txt": b"A"}, mtimes={"/remote/a.txt": 1},
            upload_log=True, remote_log_dir="/logs",
        )
        d._upload_log_file = MagicMock()
        d.run()
        d._upload_log_file.assert_called_once()

    def test_upload_log_not_called_when_disabled(self, downloader_factory, fake_sftp_factory):
        d = self._prepare(
            downloader_factory, fake_sftp_factory,
            files={"/remote/a.txt": b"A"}, mtimes={"/remote/a.txt": 1},
            upload_log=False,
        )
        d._upload_log_file = MagicMock()
        d.run()
        d._upload_log_file.assert_not_called()


# ---------------------------------------------------------------------------
# create_logger / _CSVFileHandler
# ---------------------------------------------------------------------------

class TestCreateLogger:
    def test_creates_csv_with_expected_header(self, tmp_path):
        logger, log_file = dl.create_logger(tmp_path, "edge-1")
        for h in logger.handlers:
            h.close()
        with open(log_file, encoding="utf-8-sig", newline="") as f:
            import csv as csv_module
            rows = list(csv_module.reader(f))
        assert rows[0] == ["timestamp", "device_name", "version_info", "level", "message"]

    def test_log_message_appears_in_csv_row(self, tmp_path):
        logger, log_file = dl.create_logger(tmp_path, "edge-1", "v1.0")
        logger.info("hello test")
        for h in logger.handlers:
            h.close()
        with open(log_file, encoding="utf-8-sig", newline="") as f:
            import csv as csv_module
            rows = list(csv_module.reader(f))
        assert rows[1] == [rows[1][0], "edge-1", "v1.0", "INFO", "hello test"]

    def test_device_name_with_unsafe_characters_sanitized_in_filename(self, tmp_path):
        logger, log_file = dl.create_logger(tmp_path, "edge/1:test")
        for h in logger.handlers:
            h.close()
        assert "/" not in log_file.name.replace(str(tmp_path), "")
        assert ":" not in log_file.name

    def test_version_info_omitted_from_text_format_when_empty(self, tmp_path, caplog):
        logger, log_file = dl.create_logger(tmp_path, "edge-1", "")
        with caplog.at_level(logging.INFO, logger=logger.name):
            pass
        # 直接檢查 handler 的 formatter 字串，確認沒有多餘的空括號
        text_handler = next(h for h in logger.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, dl._CSVFileHandler))
        assert "[]" not in text_handler.formatter._fmt
        for h in logger.handlers:
            h.close()

    def test_log_callback_invoked_with_formatted_message(self, tmp_path):
        received = []
        logger, log_file = dl.create_logger(tmp_path, "edge-1", log_callback=received.append)
        logger.info("callback test")
        for h in logger.handlers:
            h.close()
        assert any("callback test" in msg for msg in received)


class TestCSVFileHandlerErrorHandling:
    def test_emit_error_is_handled_without_crashing(self, tmp_path):
        log_file = tmp_path / "test.csv"
        handler = dl._CSVFileHandler(log_file, "device")
        handler.handleError = MagicMock()
        with patch.object(handler, "_writer") as mock_writer:
            mock_writer.writerow.side_effect = Exception("write failed")
            record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)
            handler.emit(record)  # 不應拋出例外
        handler.handleError.assert_called_once()
        handler.close()

    def test_close_swallows_exception_from_underlying_file_close(self, tmp_path):
        log_file = tmp_path / "test.csv"
        handler = dl._CSVFileHandler(log_file, "device")
        handler._file.close()  # 先手動關閉，讓 handler.close() 內部再次呼叫 close() 時真的出錯
        handler._file.close = MagicMock(side_effect=Exception("already closed"))
        handler.close()  # 不應向外拋出例外
