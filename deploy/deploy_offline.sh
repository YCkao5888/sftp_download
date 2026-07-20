#!/usr/bin/env bash
#
# deploy_offline.sh — 離線快速部署腳本 (sftp_download)
# ---------------------------------------------------------------------------
# 在「完全沒有對外網路」的環境下，使用 deploy/wheelhouse/ 內預先下載的 wheel
# 檔案，為 sftp_download 建立一個「專屬 venv」並安裝所有相依套件。
#
# 專屬 venv 預設路徑（與 radar / SHM 等其他專案的慣例一致）：
#     ~/venv/wanhai_nssms/share/sftp_download
#
# 目標平台：Linux aarch64 / CPython 3.10 / glibc >= 2.34  (NVIDIA Tegra, mic-733ao)
#
# 用法：
#   ./deploy_offline.sh                 # 建立/更新專屬 venv，安裝執行期相依
#   ./deploy_offline.sh --with-tests    # 一併安裝 pytest 等測試工具
#   ./deploy_offline.sh --recreate      # 砍掉重建 venv（乾淨安裝）
#   ./deploy_offline.sh --no-health-check # 部署後不自動執行健康檢查
#   ./deploy_offline.sh --check-only    # 只驗證 wheel 完整性與環境，不安裝
#   ./deploy_offline.sh --venv /path/to/venv        # 自訂 venv 路徑
#   ./deploy_offline.sh --python /usr/bin/python3.10 # 指定建立 venv 用的直譯器
#
# 特性：
#   * 全程 --no-index，永不連外網（建立 venv 也用 ensurepip，離線可行）。
#   * venv 與系統 site-packages 隔離（include-system-site-packages = false）。
#   * 安裝前以 MANIFEST.txt 校驗 wheel sha256（可用 --skip-verify 跳過）。
#   * 安裝後在 venv 內驗證關鍵套件可正常匯入。
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHEELHOUSE="${SCRIPT_DIR}/wheelhouse"
MANIFEST="${SCRIPT_DIR}/MANIFEST.txt"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

DEFAULT_VENV="${HOME}/venv/wanhai_nssms/share/sftp_download"
VENV_DIR="${DEFAULT_VENV}"
PYTHON_BIN="python3"
WITH_TESTS=0
CHECK_ONLY=0
SKIP_VERIFY=0
RECREATE=0
RUN_HEALTH=1

# --- 顏色輸出 --------------------------------------------------------------
if [ -t 1 ]; then
  R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[36m'; N=$'\e[0m'
else
  R=""; G=""; Y=""; B=""; N=""
fi
info()  { printf "%s[INFO]%s %s\n"  "$B" "$N" "$*"; }
ok()    { printf "%s[ OK ]%s %s\n"  "$G" "$N" "$*"; }
warn()  { printf "%s[WARN]%s %s\n"  "$Y" "$N" "$*"; }
err()   { printf "%s[FAIL]%s %s\n"  "$R" "$N" "$*" >&2; }

usage() { grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# --- 解析參數 --------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --with-tests)  WITH_TESTS=1 ;;
    --check-only)  CHECK_ONLY=1 ;;
    --skip-verify) SKIP_VERIFY=1 ;;
    --recreate)    RECREATE=1 ;;
    --no-health-check) RUN_HEALTH=0 ;;
    --venv)        VENV_DIR="${2:?--venv 需要一個路徑參數}"; shift ;;
    --python)      PYTHON_BIN="${2:?--python 需要一個路徑參數}"; shift ;;
    -h|--help)     usage ;;
    *) err "未知參數：$1"; echo "執行 --help 查看用法" >&2; exit 2 ;;
  esac
  shift
done

echo "==========================================================="
echo " sftp_download 離線部署 (offline deploy — 專屬 venv)"
echo "==========================================================="

# --- 前置檢查 --------------------------------------------------------------
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  err "找不到 Python 直譯器：$PYTHON_BIN"; exit 1
fi
PY_VER="$("$PYTHON_BIN" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"
PY_TAG="$("$PYTHON_BIN" -c 'import sys;print("cp%d%d"%sys.version_info[:2])')"
info "基底直譯器    : $PYTHON_BIN ($PY_VER, $PY_TAG)"
info "系統架構      : $(uname -m) ($(uname -s) $(uname -r))"
info "Wheelhouse    : $WHEELHOUSE"
info "專案目錄      : $PROJECT_DIR"
info "專屬 venv     : $VENV_DIR"

if [ ! -d "$WHEELHOUSE" ]; then
  err "wheelhouse 目錄不存在：$WHEELHOUSE"; exit 1
fi
WHL_COUNT=$(find "$WHEELHOUSE" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')
if [ "$WHL_COUNT" -eq 0 ]; then
  err "wheelhouse 內沒有任何 .whl 檔案"; exit 1
fi
ok "找到 $WHL_COUNT 個 wheel 檔案"

# 建立 venv 需要 venv + ensurepip 模組（離線 bootstrap pip）
if ! "$PYTHON_BIN" -c 'import venv, ensurepip' >/dev/null 2>&1; then
  err "此 Python 缺少 venv/ensurepip 模組，無法離線建立 venv。請先安裝 python3-venv。"; exit 1
fi

# --- 校驗 wheel 完整性 ------------------------------------------------------
if [ "$SKIP_VERIFY" -eq 0 ] && [ -f "$MANIFEST" ]; then
  info "以 MANIFEST.txt 校驗 wheel sha256 ..."
  if ( cd "$WHEELHOUSE" && grep -E '^[0-9a-f]{64}  ' "$MANIFEST" | sha256sum -c --quiet ) 2>/dev/null; then
    ok "所有 wheel 檔案 sha256 校驗通過"
  else
    err "wheel 校驗失敗，檔案可能損毀或被竄改。可用 --skip-verify 強制略過。"; exit 1
  fi
else
  warn "略過 wheel sha256 校驗"
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  ok "--check-only 完成：環境與 wheel 皆就緒，未執行安裝。"
  exit 0
fi

# --- 建立 / 沿用 venv ------------------------------------------------------
VENV_PY="${VENV_DIR}/bin/python"
if [ "$RECREATE" -eq 1 ] && [ -d "$VENV_DIR" ]; then
  warn "--recreate：移除既有 venv $VENV_DIR"
  rm -rf "$VENV_DIR"
fi

if [ -x "$VENV_PY" ]; then
  ok "沿用既有 venv：$VENV_DIR"
else
  info "建立專屬 venv（離線，含 pip）..."
  mkdir -p "$(dirname "$VENV_DIR")"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  if [ ! -x "$VENV_PY" ]; then
    err "venv 建立失敗：找不到 $VENV_PY"; exit 1
  fi
  ok "venv 建立完成"
fi
info "venv pip 版本 : $("$VENV_PY" -m pip --version 2>/dev/null | awk '{print $2}')"

# --- 執行離線安裝 ----------------------------------------------------------
RUNTIME_PKGS=(paramiko bcrypt cryptography pynacl cffi pycparser invoke typing-extensions)
TEST_PKGS=(pytest pytest-cov coverage pluggy iniconfig packaging pygments tomli exceptiongroup)

PKGS=("${RUNTIME_PKGS[@]}")
if [ "$WITH_TESTS" -eq 1 ]; then
  PKGS+=("${TEST_PKGS[@]}")
  info "安裝範圍      : 執行期相依 + 測試工具"
else
  info "安裝範圍      : 執行期相依 (paramiko 堆疊)"
fi

info "開始離線安裝到 venv（--no-index，不連外網）..."
set +e
"$VENV_PY" -m pip install \
  --no-index \
  --find-links "$WHEELHOUSE" \
  --upgrade \
  "${PKGS[@]}"
PIP_RC=$?
set -e
if [ "$PIP_RC" -ne 0 ]; then
  err "pip 安裝失敗（exit=$PIP_RC）。"; exit "$PIP_RC"
fi
ok "套件安裝完成"

# --- 安裝後驗證 ------------------------------------------------------------
info "在 venv 內驗證關鍵套件可正常匯入 ..."
"$VENV_PY" - <<'PY'
import importlib, sys
mods = ["paramiko", "cryptography", "nacl", "bcrypt", "cffi"]
fail = False
for m in mods:
    try:
        mod = importlib.import_module(m)
        v = getattr(mod, "__version__", "?")
        print(f"  [ OK ] {m:<14} {v}")
    except Exception as e:  # noqa
        print(f"  [FAIL] {m:<14} {e}")
        fail = True
sys.exit(1 if fail else 0)
PY
ok "匯入驗證通過"

echo "-----------------------------------------------------------"
ok "離線部署完成！專屬 venv：$VENV_DIR"

# --- 部署後自動健康檢查 ----------------------------------------------------
HEALTH_RC=0
if [ "$RUN_HEALTH" -eq 1 ]; then
  if [ -f "$SCRIPT_DIR/health_check.py" ]; then
    echo ""
    info "自動執行健康檢查（能力測試 + SFTP 連線 + 健康報告）..."
    echo "==========================================================="
    set +e
    "$VENV_PY" "$SCRIPT_DIR/health_check.py"
    HEALTH_RC=$?
    set -e
    echo "==========================================================="
    if [ "$HEALTH_RC" -eq 0 ]; then
      ok "健康檢查結果：HEALTHY"
    else
      warn "健康檢查發現問題（exit=$HEALTH_RC），請檢視上方報告。"
    fi
  else
    warn "找不到 health_check.py，略過自動健康檢查。"
  fi
else
  info "已指定 --no-health-check，略過自動健康檢查。"
fi

echo ""
echo "啟用 venv："
echo "  source \"$VENV_DIR/bin/activate\""
echo ""
echo "以此 venv 執行工具（不啟用也可以直接用絕對路徑）："
echo "  \"$VENV_PY\" \"$PROJECT_DIR/main.py\" --cli"
echo ""
echo "如需單獨再跑一次健康檢查："
echo "  \"$VENV_PY\" \"$SCRIPT_DIR/health_check.py\""
echo "==========================================================="

# 部署本身成功即回傳 0；健康檢查結果另以訊息呈現，不影響部署離開碼。
exit 0
