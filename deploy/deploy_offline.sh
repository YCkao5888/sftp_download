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
#   * 全程 --no-index，永不連外網。
#   * 以 python3.10 -m virtualenv 建立 venv（與 radar / SHM 一致），不再依賴系統的
#     python3-venv / ensurepip；若 python3.10 尚無 virtualenv，會用隨附的
#     install_virtualenv_offline.sh + virtualenv_wheels/ 先離線補齊。
#   * venv 與系統 site-packages 隔離。
#   * 安裝前以 MANIFEST.txt 校驗 wheel sha256（可用 --skip-verify 跳過）。
#   * 安裝後在 venv 內驗證關鍵套件可正常匯入。
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHEELHOUSE="${SCRIPT_DIR}/wheelhouse"
MANIFEST="${SCRIPT_DIR}/MANIFEST.txt"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SHARE_DIR="$(dirname "$PROJECT_DIR")"
# 船舶基本資訊檔：供各設定檔的 {vsl_name}/{ipc} 佔位符替換使用（見 settings.py）。
VESSEL_INFO="${SHARE_DIR}/.env/vessel_basic_info.json"

DEFAULT_VENV="${HOME}/venv/wanhai_nssms/share/sftp_download"
VENV_DIR="${DEFAULT_VENV}"
PYTHON_BIN=""          # 空字串＝自動偵測（優先 python3.10，與下游 install_env.sh 一致）
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

# 未指定 --python 時自動選直譯器：優先 python3.10（下游 install_env.sh 與 wheelhouse
# 的 cp310 wheel 皆以此為準），退而求其次才用 python3。
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3.10 >/dev/null 2>&1; then
    PYTHON_BIN="python3.10"
  else
    PYTHON_BIN="python3"
  fi
fi

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
info "船舶資訊檔    : $VESSEL_INFO"

# --- 船舶基本資訊檔（vessel_basic_info.json）檢查 / 互動建立 ----------------
# 剛啟動就先確認它存在且內容正確（需含非空的 vsl_name / ipc）；
# 缺少或內容不正確時，以互動問答讓使用者輸入並建立該檔。
vessel_info_show() {  # 印出現有內容；有效回傳 0、檔案不存在回傳 3、內容不正確回傳 2
  "$PYTHON_BIN" - "$VESSEL_INFO" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        info = json.load(f)
except FileNotFoundError:
    sys.exit(3)
except Exception as e:  # noqa
    print(f"內容無法解析：{e}")
    sys.exit(2)
if not isinstance(info, dict):
    print("內容不是 JSON 物件")
    sys.exit(2)
for k, v in info.items():
    print(f"{k} = {v}")
missing = [k for k in ("vsl_name", "ipc") if not str(info.get(k, "")).strip()]
if missing:
    print("缺少或為空的必要欄位：" + ", ".join(missing))
    sys.exit(2)
sys.exit(0)
PY
}

vessel_get() {  # $1=key → 印出現有值（去頭尾空白），讀取失敗則印空字串
  "$PYTHON_BIN" - "$VESSEL_INFO" "$1" <<'PY' 2>/dev/null || true
import json, sys
try:
    info = json.load(open(sys.argv[1], encoding="utf-8"))
    print(str(info.get(sys.argv[2], "")).strip())
except Exception:
    print("")
PY
}

prompt_field() {  # $1=提示文字 $2=key → 結果放進 REPLY_VAL（不可為空，有舊值則當預設）
  local cur val
  cur="$(vessel_get "$2")"
  while true; do
    if [ -n "$cur" ]; then
      read -r -p "  $1 [$cur]: " val || val=""
      val="${val:-$cur}"
    else
      read -r -p "  $1: " val || val=""
    fi
    val="$(printf '%s' "$val" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [ -n "$val" ]; then REPLY_VAL="$val"; return 0; fi
    warn "  不可為空，請重新輸入。"
  done
}

create_vessel_info() {
  if [ ! -t 0 ]; then
    err "非互動終端機，無法以問答建立船舶資訊檔。"
    err "請手動建立 $VESSEL_INFO ，內容範例：{\"vsl_name\": \"WH289\", \"ipc\": \"IPC-1\"}"
    exit 1
  fi
  local vsl ipc ans
  while true; do
    echo ""
    info "請輸入船舶基本資訊："
    prompt_field "船名 vsl_name（例：WH289）" "vsl_name"; vsl="$REPLY_VAL"
    prompt_field "IPC 代號 ipc（例：IPC-1）"  "ipc";      ipc="$REPLY_VAL"
    echo ""
    echo "  即將寫入 $VESSEL_INFO ："
    echo "    vsl_name = $vsl"
    echo "    ipc      = $ipc"
    read -r -p "  確認無誤？[Y/n] " ans || ans=""
    case "$ans" in
      ""|Y|y) break ;;
      *) warn "重新輸入。" ;;
    esac
  done
  mkdir -p "$(dirname "$VESSEL_INFO")"
  VSL_NAME="$vsl" IPC="$ipc" "$PYTHON_BIN" - "$VESSEL_INFO" <<'PY'
import json, os, sys
data = {"vsl_name": os.environ["VSL_NAME"], "ipc": os.environ["IPC"]}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
  ok "已建立/更新船舶基本資訊檔：$VESSEL_INFO"
}

echo ""
info "檢查船舶基本資訊檔 ..."
set +e
VESSEL_OUT="$(vessel_info_show)"; VESSEL_RC=$?
set -e
[ -n "$VESSEL_OUT" ] && printf '%s\n' "$VESSEL_OUT" | sed 's/^/       /'
if [ "$VESSEL_RC" -eq 0 ]; then
  ok "船舶基本資訊檔有效，沿用現有內容。"
elif [ "$VESSEL_RC" -eq 3 ]; then
  warn "找不到船舶基本資訊檔，將以互動問答建立。"
  create_vessel_info
else
  warn "船舶基本資訊檔內容不正確，將重新建立。"
  create_vessel_info
fi

# --- 開機自動執行設定（scheduler/install_autostart.sh） --------------------
# 與船舶資訊檔一樣，是需要使用者留意的一次性設定：詢問是否設定開機自動啟動
# （systemd user service + linger）。install_autostart.sh 具冪等性，可重複執行。
#
# 以 --require-linger 呼叫，讓 install_autostart.sh 用離開碼區分結果，deploy 才能
# 「掌握」實際成功狀態(而非只知道有沒有崩)。deploy 端僅據以警告、不中斷部署。
#   0 = 完全成功(service enabled + linger on)
#   3 = user service 已裝，但 linger 未開啟(開機免登入自動執行需要它)
#   4 = 設定失敗(找不到腳本 / 無法寫入 unit / user manager 不可用)
#   2 = install_autostart.sh 參數錯誤
# AUTOSTART_STATUS 供最後的部署總結顯示；先給預設值(set -u 下需先定義)。
AUTOSTART_INSTALLER="${SHARE_DIR}/scheduler/install_autostart.sh"
AUTOSTART_STATUS="未執行"
echo ""
info "檢查開機自動執行設定 ..."
if [ ! -f "$AUTOSTART_INSTALLER" ]; then
  warn "找不到 $AUTOSTART_INSTALLER ，略過開機自動執行設定。"
  AUTOSTART_STATUS="略過（找不到安裝腳本）"
elif [ ! -t 0 ]; then
  # 非互動終端機：不擅自更動 systemd / linger，僅提示手動指令。
  warn "非互動終端機，略過開機自動執行設定。"
  warn "如需設定，請手動執行：bash $AUTOSTART_INSTALLER"
  AUTOSTART_STATUS="略過（非互動終端機）"
else
  autostart_ans=""
  read -r -p "  是否設定開機自動啟動 scheduler（reboot_tmux.sh）？[Y/n] " autostart_ans || autostart_ans=""
  case "$autostart_ans" in
    ""|Y|y)
      # 捕捉離開碼判讀結果；install_autostart.sh 於非互動/無權限時不會中斷，
      # 這裡即使回非 0 也只警告，不影響 sftp_download 的部署結果。
      set +e
      bash "$AUTOSTART_INSTALLER" --require-linger
      AUTOSTART_RC=$?
      set -e
      case "$AUTOSTART_RC" in
        0) ok   "開機自動執行：已設定並啟用（service enabled + linger on）"
           AUTOSTART_STATUS="已啟用" ;;
        3) warn "開機自動執行：user service 已安裝，但 linger 未開啟；請手動執行 sudo loginctl enable-linger $(id -un)"
           AUTOSTART_STATUS="部分完成（linger 未開啟）" ;;
        4) warn "開機自動執行：設定失敗（rc=4：找不到腳本 / 無法寫入 unit / user manager 不可用）"
           AUTOSTART_STATUS="設定失敗（rc=4）" ;;
        2) warn "開機自動執行：install_autostart.sh 參數錯誤（rc=2）"
           AUTOSTART_STATUS="設定失敗（參數錯誤）" ;;
        *) warn "開機自動執行：未預期的結果（rc=$AUTOSTART_RC），請檢視上方訊息"
           AUTOSTART_STATUS="未知（rc=$AUTOSTART_RC）" ;;
      esac
      ;;
    *)
      info "略過開機自動執行設定。日後可執行：bash $AUTOSTART_INSTALLER"
      AUTOSTART_STATUS="使用者略過"
      ;;
  esac
fi

if [ ! -d "$WHEELHOUSE" ]; then
  err "wheelhouse 目錄不存在：$WHEELHOUSE"; exit 1
fi
WHL_COUNT=$(find "$WHEELHOUSE" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')
if [ "$WHL_COUNT" -eq 0 ]; then
  err "wheelhouse 內沒有任何 .whl 檔案"; exit 1
fi
ok "找到 $WHL_COUNT 個 wheel 檔案"

# 建立 venv 改用 python3.10 -m virtualenv（與 radar / SHM 一致，不再依賴系統
# python3-venv / ensurepip）。若目標直譯器尚未安裝 virtualenv，先以隨附的離線
# 安裝腳本補齊（install_virtualenv_offline.sh + virtualenv_wheels/）。
VENV_INSTALLER="${SCRIPT_DIR}/install_virtualenv_offline.sh"
if "$PYTHON_BIN" -m virtualenv --version >/dev/null 2>&1; then
  ok "virtualenv 可用：$("$PYTHON_BIN" -m virtualenv --version 2>&1 | awk '{print $2}')"
elif [ "$CHECK_ONLY" -eq 1 ]; then
  # --check-only 只驗證、不安裝；僅回報缺 virtualenv，實際部署時才會離線補齊。
  warn "$PYTHON_BIN 尚未安裝 virtualenv（--check-only 不進行安裝）。"
  warn "實際部署時將以 $VENV_INSTALLER 離線補齊。"
else
  warn "$PYTHON_BIN 尚未安裝 virtualenv，將以隨附腳本離線安裝 ..."
  if [ ! -f "$VENV_INSTALLER" ]; then
    err "找不到離線安裝腳本：$VENV_INSTALLER"; exit 1
  fi
  bash "$VENV_INSTALLER"
  if ! "$PYTHON_BIN" -m virtualenv --version >/dev/null 2>&1; then
    err "virtualenv 離線安裝後，$PYTHON_BIN 仍無法使用（可能裝到了其他解譯器）。"
    err "請確認 $PYTHON_BIN 與 install_virtualenv_offline.sh 選用的解譯器一致。"
    exit 1
  fi
  ok "virtualenv 離線安裝完成並可用：$("$PYTHON_BIN" -m virtualenv --version 2>&1 | awk '{print $2}')"
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
  info "建立專屬 venv（$PYTHON_BIN -m virtualenv，離線，含 pip）..."
  mkdir -p "$(dirname "$VENV_DIR")"
  "$PYTHON_BIN" -m virtualenv "$VENV_DIR"
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
echo "── 部署總結 ──"
printf "  開機自動執行設定：%s\n" "$AUTOSTART_STATUS"
[ "$RUN_HEALTH" -eq 1 ] && printf "  健康檢查：%s\n" \
  "$( [ "$HEALTH_RC" -eq 0 ] && echo HEALTHY || echo "有問題（exit=$HEALTH_RC）" )"

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
