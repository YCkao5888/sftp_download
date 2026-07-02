# SFTP 自動化下載工具

**語言選擇：Python**（跨平台支援 Windows/Linux 最成熟，`paramiko` 套件內建 SFTP 客戶端，`tkinter` 為 Python 內建 GUI 套件不需額外安裝，CLI 用標準庫 `argparse` 即可，最符合「同時支援 CLI 與 GUI、跨平台」的需求）。

檔案結構：
- `main.py`：進入點。不帶參數 → 開啟 GUI；帶參數 → CLI 模式。
- `downloader.py`：下載核心邏輯（連線、斷線重連、斷點續傳、Log）。
- `gui.py`：圖形化介面。
- `settings.py`：設定檔（`settings.json`）讀取/開啟工具，CLI 與 GUI 共用。
- `example_settings.json`：設定檔範本，複製改名為 `settings.json` 後填入實際值即可使用。
- `tests/`：pytest 單元測試（`downloader.py`／`settings.py`／`main.py`），詳見下方【開發：執行單元測試】。

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
會跳出視窗。若工具資料夾內已有 `settings.json`，畫面欄位會自動帶入其中的值（見下方【設定檔】章節）；否則請自行依序填入：SFTP 主機、Port、SFTP 帳號、SFTP 密碼、來源路徑、本地端儲存路徑，勾選需要的進階選項（斷線自動重連 / 斷點續傳 / 網路偵測自動下載 / 結構化下載資料夾的單層或多層 / 來源檔案更新時的處理方式，詳見下方【來源檔案更新時的版本處理】），並在「Log 設定」區塊填寫**裝置名稱**（必填）與選填的上傳版號資訊，按下「開始下載」即可。畫面下方會即時顯示執行紀錄。

畫面左上角的「載入設定檔...」按鈕可挑選任一份設定檔（例如同一台裝置用來下載不同資料夾的 `settings_A.json`、`settings_B.json`），選擇後畫面欄位會立刻換成該檔案的內容，視窗標題也會顯示目前使用的是哪一份設定檔；「開始下載」時就會用當下載入的這份設定檔資料。

視窗會依螢幕解析度自動決定初始大小，也可自由拉伸縮放；若螢幕較小、內容顯示不下，畫面右側會出現捲軸（也支援滑鼠滾輪），往下捲動即可看到其餘欄位，不會有欄位被裁切、點不到的問題。

右上角的「開啟設定檔」按鈕會開啟**目前已載入**的那份設定檔（未手動切換過的話就是預設的 `settings.json`）供編輯；若尚未有對應檔案，會先用目前畫面上的值建立一份。編輯儲存後，回到程式按「載入設定檔...」重新選一次同一份檔案即可套用變更，不需要重新啟動程式。

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
| `--no-recursive` | 只下載來源路徑當層的檔案，略過所有子資料夾（預設會下載所有子資料夾，即多層） |
| `--upload-log --log-remote-dir /data/logs` | 下載結束後把 Log 上傳回 SFTP 指定目錄 |
| `--key-file id_rsa` | 使用 SSH 私鑰登入，取代密碼 |
| `--retry-count 10` | 重試次數上限；不指定或填 `0` 代表無限次重試（預設無限次） |
| `--config settings_A.json` | 指定要讀取的設定檔路徑（預設讀取工具資料夾內的 `settings.json`） |

完整參數說明可執行 `python main.py --help` 查看。

#### 同一台裝置要下載多組不同的來源/本地路徑

如果同一台裝置需要從 SFTP 上多個不同的資料夾下載到不同的本地端路徑（例如同時同步 `/data/A` 到 `C:\A`、又要同步 `/data/B` 到 `D:\B`），做法是**每一組路徑各自準備一份設定檔，並各排一個排程任務**，用 `--config` 指定要用哪一份：

1. 複製 `example_settings.json` 建立多份設定檔，例如 `settings_A.json`、`settings_B.json`，各自填入對應的 `remote_path` / `local_path`（`host`、帳密等共同欄位可以重複，也可以各自不同）。
2. 排程任務各自指定要用的設定檔：
   ```
   python main.py --cli --config settings_A.json
   python main.py --cli --config settings_B.json
   ```
3. 每份設定檔各自獨立連線、獨立產生 Log（檔名同樣會標示 `device_name`），彼此不會互相影響；若想在 Log 裡進一步分辨是哪一組路徑，可以把 `device_name` 也取成不同的名稱（如 `edge-101-A`、`edge-101-B`）。

> GUI 一次仍只會執行單一組來源/本地路徑，但可以用左上角的「載入設定檔...」按鈕手動切換要用 `settings_A.json` 還是 `settings_B.json` 再按「開始下載」，適合手動操作的情境；**排程自動化仍建議用上述 CLI + `--config` 的方式**，讓每組路徑各自跑一個排程任務，不需要人在旁邊切換。

---

## 【設定檔 settings.json（可省略重複輸入參數）】

工具資料夾內若有 `settings.json`，CLI 與 GUI 都會自動讀取其中的值當作預設參數；**command line 上明確帶入的參數優先權最高，其次才是 settings.json，最後才是程式內建預設值**。適合上百台 edge device 各自放一份自己的 `settings.json`，之後直接排程執行即可，不必每次重複輸入一長串參數。

工具資料夾內附有 `example_settings.json` 作為範本，複製一份改名為 `settings.json` 再依下方欄位說明填入實際值即可（`settings.json` 內含帳密，已列在 `.gitignore` 不會被版本控制追蹤；`example_settings.json` 沒有真實密碼，可安心放入版本控制供其他裝置/人員參考複製）：
```
# Windows (PowerShell)
Copy-Item example_settings.json settings.json
# Linux
cp example_settings.json settings.json
```

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
| `version_info` | `--version-info` | `"v1.2.3"` 或 `""` | 選填的上傳版號資訊，會一併記錄在 Log 內容與 CSV 欄位中（不影響下載邏輯），適合用來標記這批資料對應的韌體/軟體版本或批次編號；不需要則留空字串 |
| `username` | `--username` | `"myuser"` | SFTP 登入帳號（必填） |
| `password` | `--password` / 環境變數 `SFTP_PASSWORD` | `"your_password"` | SFTP 登入密碼。若改用 `key_file` 金鑰登入則留空字串 `""`；未提供時 CLI 會互動提示輸入 |
| `key_file` | `--key-file` | `"C:\\Users\\me\\.ssh\\id_rsa"` 或 `""` | SSH 私鑰檔路徑，填寫後會取代密碼登入；不使用金鑰登入則留空字串 |
| `remote_path` | `--remote-path` | `"/data/reports"` | SFTP 上要下載的來源路徑（單一檔案或整個目錄，目錄預設會含子目錄一併遞迴下載，可用 `recursive` 設定改為只下載當層）（必填）。**這是伺服器端路徑，若 SFTP 伺服器是 Linux，請用 `/` 分隔的路徑，不要填本機的 Windows 路徑（如 `C:\...`）** |
| `local_path` | `--local-path` | `"C:\\Users\\me\\Downloads"` | 下載後要存放的本機資料夾路徑（必填），可用本機作業系統慣用的路徑格式 |
| `auto_reconnect` | `--no-auto-reconnect`（僅能停用） | `true` / `false` | 下載中斷線時是否自動重新連線並接續下載，未填預設 `true` |
| `resume` | `--no-resume`（僅能停用） | `true` / `false` | 是否啟用斷點續傳（略過已完整下載的檔案、接續未下載完的部分），未填預設 `true` |
| `wait_for_network` | `--no-wait-network`（僅能停用） | `true` / `false` | 網路不通時是否持續等待，待恢復後自動開始/繼續下載，未填預設 `true` |
| `recursive` | `--no-recursive`（僅能停用） | `true` / `false` | 來源路徑若為資料夾，是否連同所有子資料夾一併下載（多層）；設為 `false` 則只下載該路徑當層的檔案，略過所有子資料夾（單層），未填預設 `true`。啟用多層時，即使某個子資料夾內沒有任何檔案（空資料夾），本地端也會建立對應的空資料夾，完整保留原始的資料夾結構 |
| `retry_count` | `--retry-count` | `0` | 連線/下載失敗時的最大重試次數；**`0` 或留空代表無限次重試（預設值，會持續嘗試直到連線恢復）**；設為正整數（如 `10`）則達上限後放棄該檔案 |
| `retry_delay` | `--retry-delay` | `10` | 每次重試之間的等待秒數，未填預設 `10` |
| `upload_log` | `--upload-log`（僅能開啟） | `true` / `false` | 下載工作結束（成功或失敗）後，是否把本次的 Log 檔上傳回 SFTP 指定目錄，未填預設 `false` |
| `log_remote_dir` | `--log-remote-dir` | `"/data/logs"` | `upload_log` 為 `true` 時，Log 要上傳到 SFTP 上的哪個目錄（伺服器端路徑，同 `remote_path` 的路徑格式注意事項） |
| `log_dir` | `--log-dir` | `""` 或 `"C:\\logs"` | 本機儲存 Log 檔（`.csv`）的資料夾，留空字串則使用預設的 `logs/` 資料夾 |
| `duplicate_mode` | `--duplicate-mode` | `"overwrite"` 或 `"duplicate"` | 偵測到來源檔案已被更新時的處理方式：`overwrite`（**預設**，直接覆蓋舊檔案）或 `duplicate`（另存新檔、保留舊檔）；詳見下方【來源檔案更新時的版本處理】 |
| `duplicate_suffix` | `--duplicate-suffix` | `"copy"` | `duplicate_mode` 為 `duplicate` 時，另存新檔用的檔名後綴，未填預設 `"copy"` |

---

## 【來源檔案更新時的版本處理】

只用「檔案大小」判斷是否已下載完成有個盲點：如果 SFTP 上的來源檔案被換成新內容、但檔案大小剛好一樣，工具會誤判為「已下載過」而略過，導致更新被漏掉。

為了正確偵測版本是否有變，本工具在**本地端儲存路徑**根目錄會建立一個隱藏的版本紀錄檔 `.sftp_download_manifest.json`，記錄每個檔案目前對應到來源端的檔案大小與修改時間，**下載過程中也會每跨過 10% 進度就存一次檢查點**（下載中斷時也會存），內容包含目前已下載部分的 **SHA-256 雜湊**與位元組數。之後每次執行都會拿遠端目前的檔案大小 + 修改時間，跟紀錄檔裡的值比對：

- **大小相同**：用版本紀錄（若有）判斷是否真的未變更；沒有紀錄可比對時，姑且信任大小相同代表未變更。
  - 判斷為未變更 → 略過，並（重新）建立版本紀錄。
  - 有紀錄但跟目前遠端對不上（代表大小沒變但內容其實已更新過）→ 視為來源已更新，依 `duplicate_mode` 處理（見下方）。
- **本地檔案比遠端大** → 直接視為需要整份重新下載，依 `duplicate_mode` 處理（見下方）：`overwrite` 覆蓋回原檔名，`duplicate` 一樣保留舊檔、另存新檔。
- **本地檔案比遠端小** → 這是斷點續傳最常遇到的情況。只靠檔案大小/修改時間無法確定本地已下載的這段內容是否真的沒被更動過——伺服器的修改時間精確度、或剛好巧合相符的情形都可能造成誤判，且本地端檔案也可能在下載過程之外被人為修改過。因此：
  - **`overwrite`（預設，GUI 進階選項的「直接覆蓋」）**：會重新計算**本地端檔案目前內容**的 SHA-256，跟版本紀錄檔裡存的檢查點雜湊比對——**確認完全相符才會接續下載**。這個驗證只讀取本機磁碟、完全不需要重新連線或重新從 SFTP 下載已完成的部分，所以不會因為檔案很大、已下載比例很高而變慢或卡住。一旦比對不符（代表本地檔案內容已經跟預期的檢查點不一樣，可能是被人為修改過，或是找不到對應的檢查點），就直接整份重新下載覆蓋原檔名，不會把新舊內容硬接在一起造成檔案損毀。
  - **`duplicate`（GUI 進階選項的「另存新檔」）**：不需要判斷是否可以接續，一律整份重新下載並存成新檔案，因此也不會做雜湊驗證（斷點續傳形同停用，見下方說明）。

`duplicate_mode` 決定「需要整份重新下載」時的存放方式：

- **`overwrite`**：整份重新下載，直接覆蓋原檔名，不保留舊內容。
- **`duplicate`**：保留舊檔不動，把新版本另存成新檔案，檔名規則是「原檔名 + `_` + `duplicate_suffix` 設定值」，第一次是 `原檔名_copy.ext`，同一個檔案再被更新則依序是 `原檔名_copy1.ext`、`原檔名_copy2.ext`……後綴字串可自訂。適合需要保留每一版歷史檔案的情境；由於一律整份重新下載，斷點續傳形同停用。

> 若某個檔案是**這台裝置第一次遇到、還沒有版本紀錄**（例如升級到這個版本之前就已經下載過的舊檔案，或本地端本來就已經放了同名檔案），沒有歷史資料可比對時：大小相同會姑且信任為未變更略過；大小不同（不論大於或小於遠端）則依上述規則處理（小於遠端的情況一樣會經過 SHA 雜湊驗證再決定是否接續）。

> 若不希望保留這份版本紀錄檔或想重新讓所有檔案回到「首次遇到」的狀態（例如手動清空過本地端資料夾），直接刪除 `.sftp_download_manifest.json` 即可，下次執行會依單純檔案大小比對重新建立。

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

畫面／終端機顯示的仍是易讀文字，但本地儲存的 log 檔（`logs/` 資料夾內、副檔名 `.csv`）是 **CSV 格式**，欄位為 `timestamp, device_name, version_info, level, message`（`version_info` 為選填欄位，未填則該欄位為空），可直接用 Excel 開啟；若把上百台裝置的 log 檔集中到同一資料夾，可直接合併成一份總表，用「裝置名稱」或「版號」欄位篩選、用「時間」排序即可彙整查看所有裝置的下載狀況。

---

## 【常見錯誤排除】

| 錯誤訊息 | 原因 | 解決辦法 |
|---|---|---|
| `連線失敗：帳號或密碼錯誤` | 帳號密碼輸入錯誤 | 確認帳密正確；若使用金鑰登入，改用 `--key-file` 而非密碼 |
| `寫入失敗（權限不足）` | 本地端儲存路徑沒有寫入權限 | 確認 `--local-path` 資料夾有寫入權限，或改存到有權限的路徑（如自己的使用者資料夾） |
| `連線失敗（第 N 次）：... Connection refused` | SFTP 伺服器拒絕連線 | 確認主機位址與 Port 是否正確、SFTP 服務是否已啟動、防火牆是否開放該 Port |
| `遠端路徑不存在` | `--remote-path` 路徑打錯或已被移除 | 用 SFTP 客戶端（如 FileZilla）確認路徑是否存在、大小寫是否相符 |
| `無法連線至 host:port，N 秒後重試...`（一直重複） | 網路中斷或斷網環境 | 若已啟用「網路偵測自動下載」，程式會自動持續等待，網路恢復後會自動繼續下載；也可先確認本機網路是否正常 |

---

## 【開發：執行單元測試】

本工具附有 `tests/` 資料夾內的 pytest 單元測試（涵蓋 `downloader.py`、`settings.py`、`main.py`），所有網路/檔案 I/O 都經過 Mock，不會真的連線到 SFTP 伺服器，可安心在任何環境執行。這份章節只有要修改程式碼或想確認改動沒有破壞既有行為時才需要，一般下載工具的日常使用不需要理會。

1. 安裝測試相依套件（僅需一次）：
   ```
   pip install -r requirements-dev.txt
   ```
2. 執行全部測試：
   ```
   python -m pytest
   ```
3. 執行測試並在終端機顯示覆蓋率報告（含未覆蓋的行號）：
   ```
   python -m pytest --cov=downloader --cov=settings --cov=main --cov-report=term-missing
   ```
4. 若想要更方便瀏覽的 HTML 覆蓋率報告：
   ```
   python -m pytest --cov=downloader --cov=settings --cov=main --cov-report=html
   ```
   產生的報告在 `htmlcov/index.html`，用瀏覽器開啟即可依檔案、行數檢視覆蓋狀況。

只想跑單一檔案或單一測試時，可以用 `python -m pytest tests/test_downloader.py`，或加上 `-k 關鍵字` 只跑名稱符合的測試（例如 `python -m pytest -k duplicate_mode`）。

