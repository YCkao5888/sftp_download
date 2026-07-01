# SFTP 自動化下載工具

**語言選擇：Python**（跨平台支援 Windows/Linux 最成熟，`paramiko` 套件內建 SFTP 客戶端，`tkinter` 為 Python 內建 GUI 套件不需額外安裝，CLI 用標準庫 `argparse` 即可，最符合「同時支援 CLI 與 GUI、跨平台」的需求）。

檔案結構：
- `main.py`：進入點。不帶參數 → 開啟 GUI；帶參數 → CLI 模式。
- `downloader.py`：下載核心邏輯（連線、斷線重連、斷點續傳、Log）。
- `gui.py`：圖形化介面。
- `settings.py`：設定檔（`settings.json`）讀取/開啟工具，CLI 與 GUI 共用。

---

## 【環境初始化（僅第一次需要）】

1. 安裝 Python 3.9 以上版本
   - Windows：至 [python.org](https://www.python.org/downloads/) 下載安裝，安裝時勾選「Add python.exe to PATH」
   - Linux：多數發行版已內建，若無請執行 `sudo apt install python3 python3-pip python3-tk`（`python3-tk` 為 GUI 模式所需）
2. 安裝套件（在本工具的資料夾內執行）：
   ```
   pip install -r requirements.txt
   ```

以上完成後，之後每次執行都不需要重新安裝。

---

## 【執行步驟】

### GUI 模式（適合手動操作）

直接執行，不加任何參數：
```
python main.py
```
會跳出視窗。若工具資料夾內已有 `settings.json`，畫面欄位會自動帶入其中的值（見下方【設定檔】章節）；否則請自行依序填入：SFTP 主機、Port、**裝置名稱**、帳號、密碼、來源路徑、本地端儲存路徑，勾選需要的功能（斷線重連 / 斷點續傳 / 網路偵測自動下載 / 上傳 Log），按下「開始下載」即可。畫面下方會即時顯示執行紀錄。

畫面右上角的「開啟設定檔」按鈕可直接開啟 `settings.json`（若尚未建立，會先用目前畫面上的值建立一份）供編輯；編輯儲存後需**重新啟動程式**才會套用新值。

### CLI 模式（適合排程自動化，如 Windows 工作排程器 / Linux cron）

```
python main.py --host 192.168.1.100 --device-name edge-101 --username myuser --remote-path /data/reports --local-path ./downloads
```
`--device-name` 為必填，用於在 Log 內容與檔名中標示這是哪一台裝置/使用者產生的（多台 edge device 若共用同一個 SFTP 帳號，仍可從 Log 分辨來源；上傳 Log 回 SFTP 時也不會互相覆蓋）。建議每台裝置給一個唯一名稱，例如裝置的序號或固定 IP。
執行時若未帶 `--password`，會提示手動輸入密碼；也可先設定環境變數避免密碼留在指令紀錄中：
```
# Windows (PowerShell)
$env:SFTP_PASSWORD = "your_password"
# Linux
export SFTP_PASSWORD="your_password"
```

#### CLI 直接套用 settings.json（排程最常用的方式）

1. 準備好 `settings.json`（可用 GUI 的「開啟設定檔」按鈕產生一份範本再編輯，或直接照【設定檔 settings.json】章節的欄位表手動建立），把 host、帳密、路徑等都填好。
2. 排程指令直接帶 `--cli` 旗標即可，所有參數全部從 `settings.json` 自動讀取：
   ```
   python main.py --cli
   ```
3. 這行 `python main.py --cli` 就是 Windows 工作排程器「動作」欄位要填的完整命令（工作目錄設定為本工具所在資料夾），或 Linux crontab 裡的指令。

> **為什麼需要 `--cli`**：`python main.py` 不帶任何參數時，程式會判斷為要開啟 GUI 視窗（見上方 GUI 模式說明）。若排程時完全不帶參數，工作排程器會卡在背景等待一個沒有人會去點擊的視窗，看起來就像卡住或沒有反應。`--cli` 本身不代表任何實際設定值，純粹是告訴程式「用 CLI 模式執行、不要開 GUI」，因此不需要因為這個規則而重複帶入 `settings.json` 裡已經有的參數。

常用參數：
| 參數 | 說明 |
|---|---|
| `--no-auto-reconnect` | 停用斷線自動重連（預設啟用） |
| `--no-resume` | 停用斷點續傳（預設啟用，預設會略過已下載完成的檔案） |
| `--no-wait-network` | 停用網路偵測自動下載（預設啟用） |
| `--upload-log --log-remote-dir /data/logs` | 下載結束後把 Log 上傳回 SFTP 指定目錄 |
| `--key-file id_rsa` | 使用 SSH 私鑰登入，取代密碼 |
| `--retry-count 10` | 重試次數上限；不指定或填 `0` 代表無限次重試（預設無限次） |

完整參數說明可執行 `python main.py --help` 查看。

---

## 【設定檔 settings.json（可省略重複輸入參數）】

工具資料夾內若有 `settings.json`，CLI 與 GUI 都會自動讀取其中的值當作預設參數；**command line 上明確帶入的參數優先權最高，其次才是 settings.json，最後才是程式內建預設值**。適合上百台 edge device 各自放一份自己的 `settings.json`，之後直接排程執行即可，不必每次重複輸入一長串參數。

- GUI 畫面右上角有「開啟設定檔」按鈕：若尚未有 `settings.json`，會先用目前畫面上已填的值建立一份，再用系統預設程式（如記事本）開啟；編輯儲存後**需重新啟動程式**才會套用。
- CLI 沒有對應按鈕，請直接用文字編輯器開啟工具資料夾內的 `settings.json` 編輯。
- 開關類參數的 CLI 覆蓋方式是「單向」的：`--no-auto-reconnect` 只能把設定檔中的 `true` 覆蓋成停用，無法用 CLI 把設定檔中已停用的功能臨時開啟；若要改變開關狀態，直接修改 `settings.json` 最單純。
- **安全性提醒**：`password` 欄位若填寫，會以明碼存在 `settings.json` 中，方便無人值守的排程執行；若環境允許，建議改用 `key_file`（SSH 私鑰）取代密碼，或至少限制此資料夾的存取權限，避免密碼外洩。
- **注意**：`retry_count` 預設無限次重試，若是主機位址、帳號等打錯導致永遠連不上，程式會持續重試並持續寫入 Log 而不會自行停止；請確認參數正確，或視情況改設一個合理的重試上限（如 `10`）。

### 欄位說明

| 欄位（settings.json） | 對應 CLI 參數 | 範例值 | 用途 |
|---|---|---|---|
| `host` | `--host` | `"192.168.6.79"` | SFTP 伺服器位址或網域名稱（必填） |
| `port` | `--port` | `22` | SFTP 連接埠，未填預設為 `22` |
| `device_name` | `--device-name` | `"edge-101"` | 裝置/使用者識別名稱，會標示在 Log 內容與檔名中，方便日後彙整分辨來源（必填，建議每台裝置給唯一名稱） |
| `username` | `--username` | `"myuser"` | SFTP 登入帳號（必填） |
| `password` | `--password` / 環境變數 `SFTP_PASSWORD` | `"your_password"` | SFTP 登入密碼。若改用 `key_file` 金鑰登入則留空字串 `""`；未提供時 CLI 會互動提示輸入 |
| `key_file` | `--key-file` | `"C:\\Users\\me\\.ssh\\id_rsa"` 或 `""` | SSH 私鑰檔路徑，填寫後會取代密碼登入；不使用金鑰登入則留空字串 |
| `remote_path` | `--remote-path` | `"/data/reports"` | SFTP 上要下載的來源路徑（單一檔案或整個目錄，目錄會含子目錄一併遞迴下載）（必填）。**這是伺服器端路徑，若 SFTP 伺服器是 Linux，請用 `/` 分隔的路徑，不要填本機的 Windows 路徑（如 `C:\...`）** |
| `local_path` | `--local-path` | `"C:\\Users\\me\\Downloads"` | 下載後要存放的本機資料夾路徑（必填），可用本機作業系統慣用的路徑格式 |
| `auto_reconnect` | `--no-auto-reconnect`（僅能停用） | `true` / `false` | 下載中斷線時是否自動重新連線並接續下載，未填預設 `true` |
| `resume` | `--no-resume`（僅能停用） | `true` / `false` | 是否啟用斷點續傳（略過已完整下載的檔案、接續未下載完的部分），未填預設 `true` |
| `wait_for_network` | `--no-wait-network`（僅能停用） | `true` / `false` | 網路不通時是否持續等待，待恢復後自動開始/繼續下載，未填預設 `true` |
| `retry_count` | `--retry-count` | `0` | 連線/下載失敗時的最大重試次數；**`0` 或留空代表無限次重試（預設值，會持續嘗試直到連線恢復）**；設為正整數（如 `10`）則達上限後放棄該檔案 |
| `retry_delay` | `--retry-delay` | `10` | 每次重試之間的等待秒數，未填預設 `10` |
| `upload_log` | `--upload-log`（僅能開啟） | `true` / `false` | 下載工作結束（成功或失敗）後，是否把本次的 Log 檔上傳回 SFTP 指定目錄，未填預設 `false` |
| `log_remote_dir` | `--log-remote-dir` | `"/data/logs"` | `upload_log` 為 `true` 時，Log 要上傳到 SFTP 上的哪個目錄（伺服器端路徑，同 `remote_path` 的路徑格式注意事項） |
| `log_dir` | `--log-dir` | `""` 或 `"C:\\logs"` | 本機儲存 Log 檔（`.csv`）的資料夾，留空字串則使用預設的 `logs/` 資料夾 |

---

## 【狀態判斷】

- **執行中**：畫面（GUI）或終端機（CLI）持續出現如下訊息：
  ```
  連線成功
  開始下載: xxx.txt (1.2MB)
    xxx.txt 進度: 50%
  完成下載: xxx.txt
  ```
- **已完美結束**：最後出現以下訊息，且「失敗」數為 0：
  ```
  === 下載任務結束：成功 X，略過 Y，失敗 0 ===
  ```
  GUI 狀態列會顯示「下載完成」。CLI 執行結束後指令的結束代碼（exit code）為 `0`。
- **有問題發生**：出現 `=== 任務中止：... ===` 或「失敗」數大於 0，代表過程中有錯誤，請查看本地 `logs/` 資料夾內對應的 log 檔案確認細節。

> Windows 命令提示字元（cmd）若看到中文變成亂碼，屬顯示編碼問題非程式錯誤，先執行 `chcp 65001` 或改用 PowerShell / Windows Terminal 即可正常顯示。

### Log 檔案格式

畫面／終端機顯示的仍是易讀文字，但本地儲存的 log 檔（`logs/` 資料夾內、副檔名 `.csv`）是 **CSV 格式**，欄位為 `timestamp, device_name, level, message`，可直接用 Excel 開啟；若把上百台裝置的 log 檔集中到同一資料夾，可直接合併成一份總表，用「裝置名稱」欄位篩選、用「時間」排序即可彙整查看所有裝置的下載狀況。

---

## 【常見錯誤排除】

| 錯誤訊息 | 原因 | 解決辦法 |
|---|---|---|
| `連線失敗：帳號或密碼錯誤` | 帳號密碼輸入錯誤 | 確認帳密正確；若使用金鑰登入，改用 `--key-file` 而非密碼 |
| `寫入失敗（權限不足）` | 本地端儲存路徑沒有寫入權限 | 確認 `--local-path` 資料夾有寫入權限，或改存到有權限的路徑（如自己的使用者資料夾） |
| `連線失敗（第 N 次）：... Connection refused` | SFTP 伺服器拒絕連線 | 確認主機位址與 Port 是否正確、SFTP 服務是否已啟動、防火牆是否開放該 Port |
| `遠端路徑不存在` | `--remote-path` 路徑打錯或已被移除 | 用 SFTP 客戶端（如 FileZilla）確認路徑是否存在、大小寫是否相符 |
| `無法連線至 host:port，N 秒後重試...`（一直重複） | 網路中斷或斷網環境 | 若已啟用「網路偵測自動下載」，程式會自動持續等待，網路恢復後會自動繼續下載；也可先確認本機網路是否正常 |
