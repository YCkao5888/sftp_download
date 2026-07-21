#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""health_check.py — sftp_transfer 安裝後能力測試 + SFTP 連線測試 + 健康報告。

在離線部署 (deploy_offline.sh) 完成後執行，會依序進行：
  1. 環境資訊蒐集 (Python / 平台 / glibc)
  2. 相依套件能力測試 (paramiko 堆疊可否匯入、版本)
  3. 專案模組能力測試 (downloader / settings / gitignore / main 可否匯入)
  4. 單元測試 (若安裝了 pytest，跑一次專案測試套件)
  5. SFTP 連線測試 (TCP 可達性 -> SSH 認證 -> 開啟 SFTP -> 列出遠端路徑)
  6. 彙整健康報告，輸出到終端機並寫入 logs/health_report_*.md

用法：
  python3 deploy/health_check.py                       # 用預設設定檔
  python3 deploy/health_check.py --config path.json    # 指定設定檔
  python3 deploy/health_check.py --skip-tests          # 略過 pytest
  python3 deploy/health_check.py --skip-sftp           # 略過 SFTP 連線測試

離開碼：所有關鍵檢查通過 -> 0；有任何 FAIL -> 1。
"""
from __future__ import annotations

import argparse
import importlib
import io
import json
import os
import platform
import socket
import subprocess
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

DEFAULT_CONFIG = PROJECT_DIR / "config" / "sftp_download_settings.json"

# --- 終端機顏色 ------------------------------------------------------------
_TTY = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s
GREEN = lambda s: _c("32", s)
RED = lambda s: _c("31", s)
YELLOW = lambda s: _c("33", s)
CYAN = lambda s: _c("36", s)
BOLD = lambda s: _c("1", s)

# 收集所有檢查結果： (分類, 名稱, 狀態, 細節)   狀態 in {PASS, FAIL, WARN, INFO}
RESULTS: list[tuple[str, str, str, str]] = []

def record(category: str, name: str, status: str, detail: str = "") -> None:
    RESULTS.append((category, name, status, detail))
    tag = {"PASS": GREEN("[PASS]"), "FAIL": RED("[FAIL]"),
           "WARN": YELLOW("[WARN]"), "INFO": CYAN("[INFO]")}.get(status, status)
    line = f"  {tag} {name}"
    if detail:
        line += f" — {detail}"
    print(line)

def section(title: str) -> None:
    print("\n" + BOLD(f"=== {title} ==="))


# ---------------------------------------------------------------------------
# 1. 環境資訊
# ---------------------------------------------------------------------------
def collect_environment() -> dict:
    section("1. 環境資訊 (Environment)")
    try:
        libc = "/".join(platform.libc_ver()) or "unknown"
    except Exception:
        libc = "unknown"
    env = {
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "system": platform.system(),
        "glibc": libc,
        "in_virtualenv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        "hostname": socket.gethostname(),
    }
    for k, v in env.items():
        record("environment", k, "INFO", str(v))
    return env


# ---------------------------------------------------------------------------
# 2. 相依套件能力測試
# ---------------------------------------------------------------------------
def check_dependencies() -> bool:
    section("2. 相依套件能力測試 (Dependencies)")
    # (import 名稱, 顯示名稱, 是否關鍵)
    deps = [
        ("paramiko", "paramiko", True),
        ("cryptography", "cryptography", True),
        ("nacl", "PyNaCl", True),
        ("bcrypt", "bcrypt", True),
        ("cffi", "cffi", True),
    ]
    all_ok = True
    for mod_name, disp, critical in deps:
        try:
            mod = importlib.import_module(mod_name)
            ver = getattr(mod, "__version__", "?")
            record("dependencies", disp, "PASS", f"v{ver}")
        except Exception as e:
            record("dependencies", disp, "FAIL" if critical else "WARN", str(e))
            if critical:
                all_ok = False
    return all_ok


# ---------------------------------------------------------------------------
# 3. 專案模組能力測試
# ---------------------------------------------------------------------------
def check_project_modules() -> bool:
    section("3. 專案模組能力測試 (Project modules)")
    modules = ["settings", "gitignore", "downloader", "main"]
    all_ok = True
    for m in modules:
        try:
            importlib.import_module(m)
            record("project", m, "PASS", "import ok")
        except Exception as e:
            record("project", m, "FAIL", str(e))
            all_ok = False
    # 進一步驗證關鍵符號存在
    try:
        from downloader import SFTPDownloader, create_logger  # noqa: F401
        from settings import load_settings  # noqa: F401
        record("project", "關鍵符號 (SFTPDownloader/create_logger/load_settings)", "PASS", "可存取")
    except Exception as e:
        record("project", "關鍵符號", "FAIL", str(e))
        all_ok = False
    return all_ok


# ---------------------------------------------------------------------------
# 4. 單元測試 (pytest)
# ---------------------------------------------------------------------------
def run_unit_tests() -> str:
    section("4. 單元測試 (pytest)")
    try:
        importlib.import_module("pytest")
    except Exception:
        record("tests", "pytest", "WARN", "未安裝，略過（可用 deploy_offline.sh --with-tests 安裝）")
        return "SKIP"
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "--color=no"],
        cwd=str(PROJECT_DIR), capture_output=True, text=True,
    )
    dur = time.time() - t0
    out = (proc.stdout or "") + (proc.stderr or "")
    summary = ""
    for line in reversed(out.strip().splitlines()):
        if line.strip():
            summary = line.strip()
            break
    if proc.returncode == 0:
        record("tests", "pytest 測試套件", "PASS", f"{summary} ({dur:.1f}s)")
        return "PASS"
    else:
        record("tests", "pytest 測試套件", "FAIL", f"{summary} (exit={proc.returncode})")
        # 印出末段輸出協助除錯
        tail = "\n".join(out.strip().splitlines()[-15:])
        print(YELLOW("    --- pytest 輸出（末段）---"))
        for ln in tail.splitlines():
            print("      " + ln)
        return "FAIL"


# ---------------------------------------------------------------------------
# 5. SFTP 連線測試
# ---------------------------------------------------------------------------
def load_config(config_path: Path) -> dict:
    try:
        from settings import load_settings
        cfg = load_settings(config_path)
        if cfg:
            return cfg
    except Exception as e:
        record("sftp", "讀取設定檔（含佔位符解析）", "WARN",
               f"{e}；改以原始 JSON 讀取")
    # 後備：直接讀 JSON（不解析佔位符）
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        record("sftp", "讀取設定檔", "FAIL", str(e))
        return {}


def check_sftp(config_path: Path) -> bool:
    section("5. SFTP 連線測試 (Connectivity)")
    cfg = load_config(config_path)
    if not cfg:
        return False

    host = cfg.get("host")
    port = int(cfg.get("port", 22))
    username = cfg.get("username")
    password = cfg.get("password") or None
    key_file = cfg.get("key_file") or None
    remote_path = cfg.get("remote_path", ".")
    record("sftp", "目標主機", "INFO", f"{username}@{host}:{port}  遠端路徑={remote_path}")

    if not host:
        record("sftp", "設定檔 host", "FAIL", "設定檔缺少 host")
        return False

    # 5a. TCP 可達性
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=10):
            pass
        record("sftp", f"TCP 可達性 ({host}:{port})", "PASS", f"{(time.time()-t0)*1000:.0f} ms")
    except Exception as e:
        record("sftp", f"TCP 可達性 ({host}:{port})", "FAIL", str(e))
        record("sftp", "SSH/SFTP", "WARN", "TCP 不通，略過後續 SSH 測試")
        return False

    # 5b. SSH 認證 + 開啟 SFTP + 列出遠端路徑
    try:
        import paramiko
    except Exception as e:
        record("sftp", "paramiko 匯入", "FAIL", str(e))
        return False

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = dict(hostname=host, port=port, username=username, timeout=15,
                          banner_timeout=15, auth_timeout=15)
    if key_file:
        connect_kwargs["key_filename"] = key_file
    elif password:
        connect_kwargs["password"] = password

    ok = True
    try:
        t0 = time.time()
        client.connect(**connect_kwargs)
        record("sftp", "SSH 認證", "PASS",
               f"以{'金鑰' if key_file else '密碼'}認證成功 ({(time.time()-t0)*1000:.0f} ms)")
        try:
            transport = client.get_transport()
            record("sftp", "SSH 傳輸層", "INFO",
                   f"{transport.remote_version if transport else 'n/a'}")
        except Exception:
            pass
        try:
            sftp = client.open_sftp()
            record("sftp", "開啟 SFTP 通道", "PASS", "open_sftp() 成功")
            try:
                entries = sftp.listdir(remote_path)
                record("sftp", f"列出遠端路徑 {remote_path}", "PASS",
                       f"{len(entries)} 個項目")
            except IOError as e:
                record("sftp", f"列出遠端路徑 {remote_path}", "WARN",
                       f"認證成功但無法列出：{e}（路徑或權限問題）")
            finally:
                sftp.close()
        except Exception as e:
            record("sftp", "開啟 SFTP 通道", "FAIL", str(e))
            ok = False
    except paramiko.AuthenticationException as e:
        record("sftp", "SSH 認證", "FAIL", f"認證失敗：{e}")
        ok = False
    except Exception as e:
        record("sftp", "SSH 連線", "FAIL", f"{type(e).__name__}: {e}")
        ok = False
    finally:
        client.close()
    return ok


# ---------------------------------------------------------------------------
# 6. 彙整健康報告
# ---------------------------------------------------------------------------
def write_report(env: dict, config_path: Path, overall: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = PROJECT_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    report_path = log_dir / f"health_report_{ts}.md"

    counts = {"PASS": 0, "FAIL": 0, "WARN": 0}
    for _, _, status, _ in RESULTS:
        if status in counts:
            counts[status] += 1

    lines = []
    lines.append(f"# sftp_transfer 健康報告 (Health Report)")
    lines.append("")
    lines.append(f"- **產生時間**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **主機名稱**：{env.get('hostname')}")
    lines.append(f"- **整體結果**：{'✅ 健康 (HEALTHY)' if overall == 'PASS' else '❌ 有問題 (UNHEALTHY)'}")
    lines.append(f"- **統計**：PASS={counts['PASS']}　WARN={counts['WARN']}　FAIL={counts['FAIL']}")
    lines.append("")
    lines.append("## 環境")
    lines.append("")
    lines.append("| 項目 | 值 |")
    lines.append("|------|----|")
    for k, v in env.items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append(f"- 設定檔：`{config_path}`")
    lines.append("")

    # 分類輸出
    cat_titles = {
        "dependencies": "相依套件能力測試",
        "project": "專案模組能力測試",
        "tests": "單元測試",
        "sftp": "SFTP 連線測試",
    }
    for cat, title in cat_titles.items():
        rows = [(n, s, d) for c, n, s, d in RESULTS if c == cat]
        if not rows:
            continue
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| 狀態 | 項目 | 細節 |")
        lines.append("|------|------|------|")
        for n, s, d in rows:
            lines.append(f"| {s} | {n} | {d} |")
        lines.append("")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    ap = argparse.ArgumentParser(description="sftp_transfer 能力測試 + SFTP 連線 + 健康報告")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="SFTP 設定檔路徑")
    ap.add_argument("--skip-tests", action="store_true", help="略過 pytest 單元測試")
    ap.add_argument("--skip-sftp", action="store_true", help="略過 SFTP 連線測試")
    args = ap.parse_args()

    print(BOLD("==========================================================="))
    print(BOLD(" sftp_transfer 安裝後健康檢查 (health check)"))
    print(BOLD("==========================================================="))

    env = collect_environment()
    deps_ok = check_dependencies()
    proj_ok = check_project_modules()

    tests_result = "SKIP"
    if not args.skip_tests:
        tests_result = run_unit_tests()
    else:
        record("tests", "pytest", "INFO", "使用者指定略過 (--skip-tests)")

    sftp_ok = None
    config_path = Path(args.config)
    if not args.skip_sftp:
        sftp_ok = check_sftp(config_path)
    else:
        record("sftp", "SFTP 連線測試", "INFO", "使用者指定略過 (--skip-sftp)")

    # 整體判定：關鍵能力（相依 + 專案模組）必須通過；
    # 單元測試 FAIL 或 SFTP FAIL 也視為 UNHEALTHY。
    critical_fail = (not deps_ok) or (not proj_ok)
    if tests_result == "FAIL":
        critical_fail = True
    if sftp_ok is False:
        critical_fail = True
    overall = "FAIL" if critical_fail else "PASS"

    section("6. 健康報告總結 (Summary)")
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0}
    for _, _, status, _ in RESULTS:
        if status in counts:
            counts[status] += 1
    print(f"  PASS={GREEN(str(counts['PASS']))}  "
          f"WARN={YELLOW(str(counts['WARN']))}  "
          f"FAIL={RED(str(counts['FAIL']))}")
    if overall == "PASS":
        print("  整體結果：" + GREEN(BOLD("✅ HEALTHY — 環境就緒，SFTP 連線正常")))
    else:
        print("  整體結果：" + RED(BOLD("❌ UNHEALTHY — 請檢視上方 FAIL 項目")))

    report_path = write_report(env, config_path, overall)
    print(f"\n  健康報告已寫入：{CYAN(str(report_path))}")
    print(BOLD("==========================================================="))
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
