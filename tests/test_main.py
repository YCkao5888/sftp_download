"""main.py 單元測試：CLI 參數解析與 settings.json 合併邏輯。"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as main_module


def make_args(**overrides):
    """建立一份預設全為「未指定」的 argparse.Namespace，測試時只覆寫需要的欄位。"""
    defaults = dict(
        cli=True, config=None, host=None, port=None, username=None, device_name=None,
        version_info=None, password=None, key_file=None, remote_path=None, local_path=None,
        ignore_file=None, no_auto_reconnect=False, no_resume=False, no_wait_network=False, no_recursive=False,
        retry_count=None, retry_delay=None, upload_log=False, log_remote_dir=None, log_dir=None,
        duplicate_mode=None, duplicate_suffix=None,
    )
    defaults.update(overrides)
    return main_module.argparse.Namespace(**defaults)


class TestResolve:
    def test_cli_value_takes_priority_over_settings(self):
        assert main_module._resolve("cli-val", {"key": "settings-val"}, "key") == "cli-val"

    def test_falls_back_to_settings_when_cli_value_is_none(self):
        assert main_module._resolve(None, {"key": "settings-val"}, "key") == "settings-val"

    def test_falls_back_to_default_fallback_when_key_missing_from_settings(self):
        assert main_module._resolve(None, {}, "key", fallback="default-val") == "default-val"

    def test_cli_value_of_zero_is_respected_not_treated_as_unset(self):
        # 0 是有意義的值（例如 retry_count=0 代表無限次），不可誤判成「沒有提供」。
        assert main_module._resolve(0, {"key": 99}, "key") == 0

    def test_cli_value_of_false_is_respected(self):
        assert main_module._resolve(False, {"key": True}, "key") is False


class TestRunCliSettingsOnly:
    def _fake_downloader_and_logger(self, monkeypatch):
        captured = {}

        class FakeDownloader:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

            def run(self):
                return True

        monkeypatch.setattr(main_module, "SFTPDownloader", FakeDownloader)
        monkeypatch.setattr(
            main_module, "create_logger",
            lambda log_dir, device_name, version_info="", log_callback=None: (MagicMock(), "fake.csv"),
        )
        return captured

    def _write_settings(self, tmp_path, **overrides):
        data = dict(
            host="10.0.0.5", device_name="edge-1", username="svc", password="pw",
            remote_path="/data", local_path=str(tmp_path / "dl"),
        )
        data.update(overrides)
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_all_params_resolved_purely_from_settings_file(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        settings_path = self._write_settings(tmp_path, retry_count=9, duplicate_mode="duplicate")
        args = make_args(config=str(settings_path))
        rc = main_module.run_cli(args)
        assert rc == 0
        assert captured["kwargs"]["host"] == "10.0.0.5"
        assert captured["kwargs"]["retry_count"] == 9
        assert captured["kwargs"]["duplicate_mode"] == "duplicate"

    def test_cli_argument_overrides_settings_file(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        settings_path = self._write_settings(tmp_path)
        args = make_args(config=str(settings_path), host="192.168.9.9")
        main_module.run_cli(args)
        assert captured["kwargs"]["host"] == "192.168.9.9"

    def test_ignore_file_resolved_from_settings(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        settings_path = self._write_settings(tmp_path, ignore_file="ignore_rules.txt")
        args = make_args(config=str(settings_path))
        main_module.run_cli(args)
        assert captured["kwargs"]["ignore_file"] == "ignore_rules.txt"

    def test_ignore_file_cli_overrides_settings(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        settings_path = self._write_settings(tmp_path, ignore_file="from_settings.txt")
        args = make_args(config=str(settings_path), ignore_file="from_cli.txt")
        main_module.run_cli(args)
        assert captured["kwargs"]["ignore_file"] == "from_cli.txt"

    def test_ignore_file_empty_string_in_settings_passed_as_none(self, tmp_path, monkeypatch):
        # 設定檔中留空字串（範本預設值）代表未設定，不應被當成路徑傳入。
        captured = self._fake_downloader_and_logger(monkeypatch)
        settings_path = self._write_settings(tmp_path, ignore_file="")
        args = make_args(config=str(settings_path))
        main_module.run_cli(args)
        assert captured["kwargs"]["ignore_file"] is None

    def test_failed_download_returns_exit_code_one(self, tmp_path, monkeypatch):
        class FailingDownloader:
            def __init__(self, **kwargs):
                pass

            def run(self):
                return False

        monkeypatch.setattr(main_module, "SFTPDownloader", FailingDownloader)
        monkeypatch.setattr(
            main_module, "create_logger",
            lambda *a, **k: (MagicMock(), "fake.csv"),
        )
        settings_path = self._write_settings(tmp_path)
        args = make_args(config=str(settings_path))
        rc = main_module.run_cli(args)
        assert rc == 1


class TestRunCliBooleanFlags:
    def _fake_downloader_and_logger(self, monkeypatch):
        captured = {}

        class FakeDownloader:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

            def run(self):
                return True

        monkeypatch.setattr(main_module, "SFTPDownloader", FakeDownloader)
        monkeypatch.setattr(main_module, "create_logger", lambda *a, **k: (MagicMock(), "fake.csv"))
        return captured

    def _base_settings(self, tmp_path, **overrides):
        data = dict(host="h", device_name="d", username="u", password="p", remote_path="/r", local_path=str(tmp_path))
        data.update(overrides)
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_no_auto_reconnect_forces_off_even_if_settings_true(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        settings_path = self._base_settings(tmp_path, auto_reconnect=True)
        args = make_args(config=str(settings_path), no_auto_reconnect=True)
        main_module.run_cli(args)
        assert captured["kwargs"]["auto_reconnect"] is False

    def test_no_resume_cannot_be_overridden_back_on_by_settings(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        settings_path = self._base_settings(tmp_path, resume=True)
        args = make_args(config=str(settings_path), no_resume=True)
        main_module.run_cli(args)
        assert captured["kwargs"]["resume"] is False

    def test_upload_log_flag_can_only_turn_on(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        settings_path = self._base_settings(tmp_path, upload_log=False, log_remote_dir="/logs")
        args = make_args(config=str(settings_path), upload_log=True, log_remote_dir="/logs")
        main_module.run_cli(args)
        assert captured["kwargs"]["upload_log"] is True

    def test_settings_alone_can_enable_upload_log_without_cli_flag(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        settings_path = self._base_settings(tmp_path, upload_log=True, log_remote_dir="/logs")
        args = make_args(config=str(settings_path))
        main_module.run_cli(args)
        assert captured["kwargs"]["upload_log"] is True

    def test_no_recursive_forces_single_level(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        settings_path = self._base_settings(tmp_path, recursive=True)
        args = make_args(config=str(settings_path), no_recursive=True)
        main_module.run_cli(args)
        assert captured["kwargs"]["recursive"] is False


class TestRunCliRemotePathList:
    def test_remote_path_list_from_settings_passed_through(self, tmp_path, monkeypatch):
        captured = {}

        class FakeDownloader:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

            def run(self):
                return True

        monkeypatch.setattr(main_module, "SFTPDownloader", FakeDownloader)
        monkeypatch.setattr(
            main_module, "create_logger",
            lambda log_dir, device_name, version_info="", log_callback=None: (MagicMock(), "fake.csv"),
        )
        data = dict(
            host="10.0.0.5", device_name="edge-1", username="svc", password="pw",
            remote_path=["/standard/proj", "/unique/proj"], local_path=str(tmp_path / "dl"),
        )
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        rc = main_module.run_cli(make_args(config=str(path)))
        assert rc == 0
        assert captured["kwargs"]["remote_path"] == ["/standard/proj", "/unique/proj"]

    def test_repeated_remote_path_flag_parses_to_list(self):
        parser = main_module.build_parser()
        args = parser.parse_args(["--cli", "--remote-path", "/a", "--remote-path", "/b"])
        assert args.remote_path == ["/a", "/b"]


class TestRunCliPlaceholders:
    def test_placeholder_error_returns_one_and_prints_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("VESSEL_INFO_PATH", str(tmp_path / "nope.json"))
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"log_remote_dir": "/data/{vsl_name}"}), encoding="utf-8")
        rc = main_module.run_cli(make_args(config=str(path)))
        assert rc == 1
        assert "找不到船舶資訊檔" in capsys.readouterr().err

    def test_placeholders_resolved_into_downloader_kwargs(self, tmp_path, monkeypatch):
        vessel = tmp_path / "vessel_basic_info.json"
        vessel.write_text(json.dumps({"vsl_name": "WH289", "ipc": "IPC-1"}), encoding="utf-8")
        monkeypatch.setenv("VESSEL_INFO_PATH", str(vessel))
        captured = {}

        class FakeDownloader:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

            def run(self):
                return True

        monkeypatch.setattr(main_module, "SFTPDownloader", FakeDownloader)
        monkeypatch.setattr(
            main_module, "create_logger",
            lambda log_dir, device_name, version_info="", log_callback=None: (MagicMock(), "fake.csv"),
        )
        data = dict(
            host="10.0.0.5", device_name="{vsl_name}_{ipc}_edge", username="svc", password="pw",
            remote_path="/data", local_path=str(tmp_path / "dl"),
            upload_log=True, log_remote_dir="/fleet/{vsl_name}/{ipc}/sftp_logs",
        )
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        rc = main_module.run_cli(make_args(config=str(path)))
        assert rc == 0
        assert captured["kwargs"]["remote_log_dir"] == "/fleet/WH289/IPC-1/sftp_logs"


class TestRunCliValidation:
    def test_missing_required_fields_returns_one_and_prints_error(self, capsys):
        args = make_args()  # 完全沒有任何參數，也沒有 settings.json
        with patch.object(main_module, "load_settings", return_value={}):
            rc = main_module.run_cli(args)
        assert rc == 1
        assert "缺少必要參數" in capsys.readouterr().err

    def test_upload_log_without_remote_dir_returns_one(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(main_module, "create_logger", lambda *a, **k: (MagicMock(), "fake.csv"))
        settings = dict(host="h", device_name="d", username="u", password="p", remote_path="/r", local_path=str(tmp_path))
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(settings), encoding="utf-8")
        args = make_args(config=str(path), upload_log=True)
        rc = main_module.run_cli(args)
        assert rc == 1
        assert "log-remote-dir" in capsys.readouterr().err


class TestRunCliPasswordResolution:
    def _fake_downloader_and_logger(self, monkeypatch):
        captured = {}

        class FakeDownloader:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

            def run(self):
                return True

        monkeypatch.setattr(main_module, "SFTPDownloader", FakeDownloader)
        monkeypatch.setattr(main_module, "create_logger", lambda *a, **k: (MagicMock(), "fake.csv"))
        return captured

    def _settings_without_password(self, tmp_path):
        data = dict(host="h", device_name="d", username="u", remote_path="/r", local_path=str(tmp_path))
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_cli_password_takes_priority(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        monkeypatch.delenv("SFTP_PASSWORD", raising=False)
        path = self._settings_without_password(tmp_path)
        args = make_args(config=str(path), password="cli-pass")
        main_module.run_cli(args)
        assert captured["kwargs"]["password"] == "cli-pass"

    def test_env_var_used_when_no_cli_password(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        monkeypatch.setenv("SFTP_PASSWORD", "env-pass")
        path = self._settings_without_password(tmp_path)
        args = make_args(config=str(path))
        main_module.run_cli(args)
        assert captured["kwargs"]["password"] == "env-pass"

    def test_settings_password_used_when_no_cli_or_env(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        monkeypatch.delenv("SFTP_PASSWORD", raising=False)
        data = dict(host="h", device_name="d", username="u", password="settings-pass", remote_path="/r", local_path=str(tmp_path))
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        args = make_args(config=str(path))
        main_module.run_cli(args)
        assert captured["kwargs"]["password"] == "settings-pass"

    def test_interactive_prompt_used_as_last_resort(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        monkeypatch.delenv("SFTP_PASSWORD", raising=False)
        monkeypatch.setattr(main_module.getpass, "getpass", lambda prompt: "typed-pass")
        path = self._settings_without_password(tmp_path)
        args = make_args(config=str(path))
        main_module.run_cli(args)
        assert captured["kwargs"]["password"] == "typed-pass"

    def test_key_file_present_skips_password_prompt_entirely(self, tmp_path, monkeypatch):
        captured = self._fake_downloader_and_logger(monkeypatch)
        monkeypatch.delenv("SFTP_PASSWORD", raising=False)

        def fail_if_called(prompt):
            raise AssertionError("getpass should not be called when key_file is set")

        monkeypatch.setattr(main_module.getpass, "getpass", fail_if_called)
        path = self._settings_without_password(tmp_path)
        args = make_args(config=str(path), key_file="/home/user/.ssh/id_rsa")
        main_module.run_cli(args)
        assert captured["kwargs"]["key_file"] == "/home/user/.ssh/id_rsa"


class TestBuildParser:
    def test_duplicate_mode_rejects_invalid_choice(self, capsys):
        parser = main_module.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--duplicate-mode", "not-a-real-choice"])

    def test_cli_flag_alone_is_sufficient_to_avoid_argparse_errors(self):
        parser = main_module.build_parser()
        args = parser.parse_args(["--cli"])
        assert args.cli is True
        assert args.host is None


class TestMainDispatch:
    """main() 的 GUI/CLI 分流：這是 --cli 旗標存在的根本原因，值得直接驗證。"""

    def test_zero_arguments_launches_gui_not_cli(self, monkeypatch):
        fake_gui_module = MagicMock()
        monkeypatch.setitem(sys.modules, "gui", fake_gui_module)
        monkeypatch.setattr(sys, "argv", ["main.py"])
        result = main_module.main()
        assert result == 0
        fake_gui_module.launch_gui.assert_called_once()

    def test_any_argument_present_dispatches_to_cli_not_gui(self, monkeypatch):
        fake_gui_module = MagicMock()
        monkeypatch.setitem(sys.modules, "gui", fake_gui_module)
        monkeypatch.setattr(sys, "argv", ["main.py", "--cli"])
        monkeypatch.setattr(main_module, "run_cli", lambda args: 0)
        result = main_module.main()
        assert result == 0
        fake_gui_module.launch_gui.assert_not_called()
