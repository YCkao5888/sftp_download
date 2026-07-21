"""settings.py 單元測試。"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import settings as settings_module


class TestLoadSettings:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        result = settings_module.load_settings(tmp_path / "nope.json")
        assert result == {}

    def test_valid_json_loads_correctly(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"host": "1.2.3.4", "port": 22}), encoding="utf-8")
        result = settings_module.load_settings(path)
        assert result == {"host": "1.2.3.4", "port": 22}

    def test_corrupt_json_returns_empty_dict_without_raising(self, tmp_path, capsys):
        path = tmp_path / "settings.json"
        path.write_text("{not valid json!", encoding="utf-8")
        result = settings_module.load_settings(path)
        assert result == {}
        assert "讀取失敗" in capsys.readouterr().err

    def test_empty_json_object_returns_empty_dict(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("{}", encoding="utf-8")
        assert settings_module.load_settings(path) == {}


class TestResolvePlaceholders:
    def _write_vessel_info(self, tmp_path, monkeypatch, content='{"vsl_name": "WH289", "ipc": "IPC-1"}'):
        path = tmp_path / "vessel_basic_info.json"
        path.write_text(content, encoding="utf-8")
        monkeypatch.setenv("VESSEL_INFO_PATH", str(path))
        return path

    def test_placeholders_replaced_from_vessel_info(self, tmp_path, monkeypatch):
        self._write_vessel_info(tmp_path, monkeypatch)
        result = settings_module.resolve_placeholders(
            {"log_remote_dir": "/fleet/deploy/{vsl_name}/{ipc}/sftp_logs"}
        )
        assert result["log_remote_dir"] == "/fleet/deploy/WH289/IPC-1/sftp_logs"

    def test_device_name_placeholder_replaced(self, tmp_path, monkeypatch):
        self._write_vessel_info(tmp_path, monkeypatch)
        result = settings_module.resolve_placeholders({"device_name": "{vsl_name}_{ipc}_SFTP_DOWNLOADER"})
        assert result["device_name"] == "WH289_IPC-1_SFTP_DOWNLOADER"

    def test_non_string_values_left_untouched(self, tmp_path, monkeypatch):
        self._write_vessel_info(tmp_path, monkeypatch)
        result = settings_module.resolve_placeholders(
            {"port": 22, "resume": True, "host": "{vsl_name}.example.com"}
        )
        assert result["port"] == 22
        assert result["resume"] is True
        assert result["host"] == "WH289.example.com"

    def test_no_placeholders_does_not_require_vessel_info_file(self, tmp_path, monkeypatch):
        # 完全沒用到佔位符時，vessel 資訊檔可以不存在，行為不變（回歸保護）。
        monkeypatch.setenv("VESSEL_INFO_PATH", str(tmp_path / "nope.json"))
        data = {"host": "1.2.3.4", "log_remote_dir": "/data/logs"}
        assert settings_module.resolve_placeholders(data) == data

    def test_placeholder_with_missing_vessel_info_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VESSEL_INFO_PATH", str(tmp_path / "nope.json"))
        with pytest.raises(settings_module.PlaceholderError, match="找不到船舶資訊檔"):
            settings_module.resolve_placeholders({"log_remote_dir": "/data/{vsl_name}"})

    def test_unknown_placeholder_key_raises_with_field_name(self, tmp_path, monkeypatch):
        self._write_vessel_info(tmp_path, monkeypatch)
        with pytest.raises(settings_module.PlaceholderError, match="log_remote_dir.*vslname"):
            settings_module.resolve_placeholders({"log_remote_dir": "/data/{vslname}"})

    def test_corrupt_vessel_info_file_raises(self, tmp_path, monkeypatch):
        self._write_vessel_info(tmp_path, monkeypatch, content="{broken json")
        with pytest.raises(settings_module.PlaceholderError, match="讀取失敗"):
            settings_module.resolve_placeholders({"host": "{vsl_name}"})

    def test_load_settings_resolves_placeholders(self, tmp_path, monkeypatch):
        self._write_vessel_info(tmp_path, monkeypatch)
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps({"log_remote_dir": "/fleet/{vsl_name}/{ipc}/logs", "port": 22}), encoding="utf-8"
        )
        result = settings_module.load_settings(path)
        assert result == {"log_remote_dir": "/fleet/WH289/IPC-1/logs", "port": 22}

    def test_load_settings_propagates_placeholder_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VESSEL_INFO_PATH", str(tmp_path / "nope.json"))
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"host": "{vsl_name}"}), encoding="utf-8")
        with pytest.raises(settings_module.PlaceholderError):
            settings_module.load_settings(path)

    def test_list_values_resolved_per_element(self, tmp_path, monkeypatch):
        self._write_vessel_info(tmp_path, monkeypatch)
        result = settings_module.resolve_placeholders(
            {"remote_path": ["source/project1", "source/{vsl_name}/project/config"]}
        )
        assert result["remote_path"] == ["source/project1", "source/WH289/project/config"]

    def test_list_with_unknown_placeholder_raises(self, tmp_path, monkeypatch):
        self._write_vessel_info(tmp_path, monkeypatch)
        with pytest.raises(settings_module.PlaceholderError, match="remote_path.*nope"):
            settings_module.resolve_placeholders({"remote_path": ["a", "b/{nope}"]})

    def test_list_non_string_items_left_untouched(self, tmp_path, monkeypatch):
        self._write_vessel_info(tmp_path, monkeypatch)
        result = settings_module.resolve_placeholders({"mixed": ["{vsl_name}", 42, None]})
        assert result["mixed"] == ["WH289", 42, None]

    def test_vessel_info_values_coerced_to_string(self, tmp_path, monkeypatch):
        self._write_vessel_info(tmp_path, monkeypatch, content='{"ipc": 1}')
        result = settings_module.resolve_placeholders({"device_name": "IPC{ipc}"})
        assert result["device_name"] == "IPC1"


class TestSaveSettings:
    def test_writes_json_readable_by_load_settings(self, tmp_path):
        path = tmp_path / "exported.json"
        data = {"host": "10.0.0.1", "port": 22, "recursive": False, "device_name": "邊緣裝置-1"}
        result_path = settings_module.save_settings(path, data)
        assert result_path == path
        assert settings_module.load_settings(path) == data

    def test_overwrites_existing_file(self, tmp_path):
        path = tmp_path / "exported.json"
        path.write_text(json.dumps({"host": "old"}), encoding="utf-8")
        settings_module.save_settings(path, {"host": "new"})
        assert settings_module.load_settings(path) == {"host": "new"}

    def test_chinese_characters_saved_as_readable_text_not_escaped(self, tmp_path):
        # ensure_ascii=False：中文以原字元存檔，方便使用者直接用記事本檢視編輯。
        path = tmp_path / "exported.json"
        settings_module.save_settings(path, {"device_name": "測試裝置"})
        assert "測試裝置" in path.read_text(encoding="utf-8")


class TestEnsureSettingsFile:
    def test_creates_file_from_template_when_missing(self, tmp_path):
        path = tmp_path / "settings.json"
        result_path = settings_module.ensure_settings_file(path)
        assert result_path == path
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == settings_module.SETTINGS_TEMPLATE

    def test_does_not_overwrite_existing_file(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"host": "already-here"}), encoding="utf-8")
        settings_module.ensure_settings_file(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"host": "already-here"}

    def test_seed_values_override_template_defaults(self, tmp_path):
        path = tmp_path / "settings.json"
        settings_module.ensure_settings_file(path, seed={"host": "10.0.0.1", "port": 2222})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["host"] == "10.0.0.1"
        assert data["port"] == 2222
        assert data["username"] == ""  # 未提供的欄位仍沿用範本預設值

    def test_seed_none_and_empty_string_values_are_ignored(self, tmp_path):
        path = tmp_path / "settings.json"
        settings_module.ensure_settings_file(path, seed={"host": "", "username": None, "port": 21})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["host"] == ""  # 範本預設值本來就是空字串
        assert data["port"] == 21

    def test_seed_false_boolean_is_preserved_not_treated_as_empty(self, tmp_path):
        path = tmp_path / "settings.json"
        settings_module.ensure_settings_file(path, seed={"upload_log": False, "recursive": False})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["upload_log"] is False
        assert data["recursive"] is False


class TestOpenInDefaultApp:
    def test_windows_uses_os_startfile(self):
        with patch.object(settings_module.sys, "platform", "win32"), \
             patch.object(settings_module.os, "startfile", create=True) as mock_startfile:
            settings_module.open_in_default_app("C:/settings.json")
            mock_startfile.assert_called_once_with("C:/settings.json")

    def test_macos_uses_open_command(self):
        with patch.object(settings_module.sys, "platform", "darwin"), \
             patch.object(settings_module, "subprocess") as mock_subprocess:
            settings_module.open_in_default_app("/tmp/settings.json")
            mock_subprocess.run.assert_called_once_with(["open", "/tmp/settings.json"])

    def test_linux_uses_xdg_open(self):
        with patch.object(settings_module.sys, "platform", "linux"), \
             patch.object(settings_module, "subprocess") as mock_subprocess:
            settings_module.open_in_default_app("/tmp/settings.json")
            mock_subprocess.run.assert_called_once_with(["xdg-open", "/tmp/settings.json"])
