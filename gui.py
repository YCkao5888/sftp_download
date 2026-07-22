"""SFTP 傳輸工具的圖形化介面（不帶任何 CLI 參數執行時自動啟動），支援下載與上傳兩種模式。"""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from downloader import SFTPDownloader, create_logger
from uploader import SFTPUploader
from settings import (
    SETTINGS_PATH,
    SETTINGS_TEMPLATE,
    PlaceholderError,
    ensure_settings_file,
    load_settings,
    open_in_default_app,
    save_settings,
)

DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"


class SFTPDownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.downloader = None
        self.settings_path = SETTINGS_PATH
        try:
            self.settings = load_settings(self.settings_path)
        except PlaceholderError as e:
            messagebox.showerror("設定檔載入失敗", str(e))
            self.settings = {}
        self._build_widgets()
        self._apply_settings_to_fields()
        self._toggle_log_dir()
        self._toggle_duplicate_suffix()
        self._on_mode_change()
        self._update_title()

    def _build_widgets(self):
        pad = {"padx": 6, "pady": 4}
        self.root.minsize(420, 360)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # 用 Canvas + Scrollbar 包住整份表單：畫面內容在小螢幕/低解析度下放不下時可以捲動查看，
        # 而不會被視窗邊界裁掉、變成無法觸及的欄位。
        canvas = tk.Canvas(self.root, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        outer = ttk.Frame(canvas, padding=10)
        outer_window = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.columnconfigure(0, weight=1)

        def _sync_scrollregion(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_inner_width(event):
            canvas.itemconfig(outer_window, width=event.width)

        outer.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _sync_inner_width)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_mousewheel_linux(event):
            canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_mousewheel_linux)
            canvas.bind_all("<Button-5>", _on_mousewheel_linux)

        def _unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

        # 依螢幕解析度決定初始視窗大小（不超過螢幕的 90%/85%），並置中顯示；
        # 視窗仍可自由縮放，內容放不下時改用上面的捲軸捲動查看。
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = max(420, min(700, int(screen_w * 0.9)))
        height = max(360, min(750, int(screen_h * 0.85)))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        settings_bar = ttk.Frame(outer)
        settings_bar.grid(row=0, column=0, sticky="we", pady=(0, 8))
        ttk.Button(settings_bar, text="載入設定檔...", command=self._load_settings_file).pack(side="left")
        ttk.Button(settings_bar, text="開啟設定檔", command=self._open_settings_file).pack(side="left", padx=(6, 0))
        ttk.Button(settings_bar, text="匯出設定檔...", command=self._export_settings_file).pack(side="left", padx=(6, 0))

        ttk.Label(settings_bar, text="模式：").pack(side="left", padx=(12, 0))
        self.mode_var = tk.StringVar(value="download")
        ttk.Radiobutton(
            settings_bar, text="下載", value="download", variable=self.mode_var, command=self._on_mode_change
        ).pack(side="left")
        ttk.Radiobutton(
            settings_bar, text="上傳", value="upload", variable=self.mode_var, command=self._on_mode_change
        ).pack(side="left")

        steps = (
            "操作步驟：① 如已有設定檔，先按「載入設定檔」自動帶入欄位　"
            "② 填寫下方標示 * 的必填欄位　③ 依需求勾選進階選項　④ 按「開始下載」，下方會即時顯示進度"
        )
        ttk.Label(outer, text=steps, wraplength=580, foreground="#444444").grid(
            row=1, column=0, sticky="we", pady=(0, 2)
        )
        ttk.Label(outer, text="* 為必填欄位", foreground="#b00020").grid(row=2, column=0, sticky="w", pady=(0, 8))

        # --- SFTP 連線資訊（必填） ---
        conn_frame = ttk.LabelFrame(outer, text="SFTP 連線資訊", padding=8)
        conn_frame.grid(row=3, column=0, sticky="we", pady=(0, 8))
        conn_frame.columnconfigure(1, weight=1)
        conn_frame.columnconfigure(3, weight=1)

        ttk.Label(conn_frame, text="SFTP 主機 *").grid(row=0, column=0, sticky="w", **pad)
        self.host_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.host_var).grid(row=0, column=1, sticky="we", **pad)

        ttk.Label(conn_frame, text="Port（預設 22）").grid(row=0, column=2, sticky="w", **pad)
        self.port_var = tk.StringVar(value="22")
        ttk.Entry(conn_frame, textvariable=self.port_var, width=8).grid(row=0, column=3, sticky="w", **pad)

        ttk.Label(conn_frame, text="SFTP 帳號 *").grid(row=1, column=0, sticky="w", **pad)
        self.username_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.username_var).grid(row=1, column=1, sticky="we", **pad)

        ttk.Label(conn_frame, text="SFTP 密碼 *").grid(row=1, column=2, sticky="w", **pad)
        self.password_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.password_var, show="*").grid(row=1, column=3, sticky="we", **pad)

        ttk.Label(
            conn_frame,
            text="（若已在設定檔中設定 key_file 金鑰登入，密碼可留空）",
            foreground="#777777",
        ).grid(row=2, column=0, columnspan=4, sticky="w", padx=6)

        # --- 傳輸路徑（必填；標籤依模式變動） ---
        self.path_frame = ttk.LabelFrame(outer, text="下載路徑", padding=8)
        self.path_frame.grid(row=4, column=0, sticky="we", pady=(0, 8))
        self.path_frame.columnconfigure(1, weight=1)

        self.remote_path_label = ttk.Label(self.path_frame, text="SFTP 來源路徑 *\n（多個路徑以 ; 分隔）")
        self.remote_path_label.grid(row=0, column=0, sticky="w", **pad)
        self.remote_path_var = tk.StringVar()
        ttk.Entry(self.path_frame, textvariable=self.remote_path_var).grid(
            row=0, column=1, columnspan=2, sticky="we", **pad
        )

        self.local_path_label = ttk.Label(self.path_frame, text="本地端儲存路徑 *")
        self.local_path_label.grid(row=1, column=0, sticky="w", **pad)
        self.local_path_var = tk.StringVar()
        ttk.Entry(self.path_frame, textvariable=self.local_path_var).grid(row=1, column=1, sticky="we", **pad)
        ttk.Button(self.path_frame, text="瀏覽...", command=self._browse_local_path).grid(row=1, column=2, **pad)

        # --- 進階選項（選填） ---
        adv_frame = ttk.LabelFrame(outer, text="進階選項（選填，預設皆已啟用）", padding=8)
        adv_frame.grid(row=5, column=0, sticky="we", pady=(0, 8))

        self.auto_reconnect_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(adv_frame, text="斷線自動重連", variable=self.auto_reconnect_var).grid(
            row=0, column=0, sticky="w", **pad
        )
        self.resume_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(adv_frame, text="斷點續傳", variable=self.resume_var).grid(
            row=0, column=1, sticky="w", **pad
        )
        self.wait_network_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(adv_frame, text="網路偵測自動下載", variable=self.wait_network_var).grid(
            row=0, column=2, sticky="w", **pad
        )

        ttk.Label(adv_frame, text="結構化下載資料夾：").grid(row=1, column=0, sticky="w", **pad)
        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Radiobutton(
            adv_frame, text="多層（含所有子資料夾）", value=True, variable=self.recursive_var,
        ).grid(row=1, column=1, sticky="w", **pad)
        ttk.Radiobutton(
            adv_frame, text="單層（僅此層檔案）", value=False, variable=self.recursive_var,
        ).grid(row=1, column=2, sticky="w", **pad)

        self.duplicate_mode_label = ttk.Label(adv_frame, text="來源檔案更新時：")
        self.duplicate_mode_label.grid(row=2, column=0, sticky="w", **pad)
        self.duplicate_mode_var = tk.StringVar(value="overwrite")
        ttk.Radiobutton(
            adv_frame, text="另存新檔", value="duplicate", variable=self.duplicate_mode_var,
            command=self._toggle_duplicate_suffix,
        ).grid(row=2, column=1, sticky="w", **pad)
        ttk.Radiobutton(
            adv_frame, text="直接覆蓋", value="overwrite", variable=self.duplicate_mode_var,
            command=self._toggle_duplicate_suffix,
        ).grid(row=2, column=2, sticky="w", **pad)

        ttk.Label(adv_frame, text="另存新檔後綴：").grid(row=3, column=0, sticky="w", **pad)
        self.duplicate_suffix_var = tk.StringVar(value="copy")
        self.duplicate_suffix_entry = ttk.Entry(adv_frame, textvariable=self.duplicate_suffix_var, width=12)
        self.duplicate_suffix_entry.grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(
            adv_frame, text="例：copy → 更新時存為 name_copy.ext，再更新則 name_copy1.ext、copy2...",
            foreground="#777777",
        ).grid(row=3, column=2, columnspan=2, sticky="w", padx=6)

        # --- Log 設定 ---
        log_frame = ttk.LabelFrame(outer, text="Log 設定", padding=8)
        log_frame.grid(row=6, column=0, sticky="we", pady=(0, 8))
        log_frame.columnconfigure(1, weight=1)
        log_frame.columnconfigure(3, weight=1)

        ttk.Label(log_frame, text="裝置名稱 *").grid(row=0, column=0, sticky="w", **pad)
        self.device_name_var = tk.StringVar()
        ttk.Entry(log_frame, textvariable=self.device_name_var).grid(row=0, column=1, sticky="we", **pad)

        ttk.Label(log_frame, text="上傳版號資訊（選填）").grid(row=0, column=2, sticky="w", **pad)
        self.version_info_var = tk.StringVar()
        ttk.Entry(log_frame, textvariable=self.version_info_var).grid(row=0, column=3, sticky="we", **pad)

        self.upload_log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            log_frame, text="完成後將 Log 上傳回 SFTP", variable=self.upload_log_var, command=self._toggle_log_dir
        ).grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(log_frame, text="上傳目錄：").grid(row=1, column=2, sticky="e", **pad)
        self.remote_log_dir_var = tk.StringVar()
        self.remote_log_dir_entry = ttk.Entry(log_frame, textvariable=self.remote_log_dir_var, state="disabled")
        self.remote_log_dir_entry.grid(row=1, column=3, sticky="we", **pad)

        # --- 執行 ---
        self.start_button = ttk.Button(outer, text="開始下載", command=self._start_download)
        self.start_button.grid(row=7, column=0, sticky="we", pady=(4, 8), ipady=4)

        self.status_var = tk.StringVar(value="尚未開始")
        ttk.Label(outer, textvariable=self.status_var, font=("", 9, "bold")).grid(
            row=8, column=0, sticky="w", pady=(0, 4)
        )

        self.log_text = scrolledtext.ScrolledText(outer, width=80, height=12, state="disabled")
        self.log_text.grid(row=9, column=0, sticky="nsew")

    def _toggle_log_dir(self):
        state = "normal" if self.upload_log_var.get() else "disabled"
        self.remote_log_dir_entry.config(state=state)

    def _toggle_duplicate_suffix(self):
        state = "normal" if self.duplicate_mode_var.get() == "duplicate" else "disabled"
        self.duplicate_suffix_entry.config(state=state)

    def _on_mode_change(self):
        """依目前模式（下載/上傳）調整路徑欄位與按鈕文字，讓來源/目的地標示與方向一致。"""
        if self.mode_var.get() == "upload":
            self.path_frame.config(text="上傳路徑")
            self.remote_path_label.config(text="SFTP 目的地路徑 *")
            self.local_path_label.config(text="本地端來源路徑 *")
            self.duplicate_mode_label.config(text="遠端已有同名檔時：")
            self.start_button.config(text="開始上傳")
        else:
            self.path_frame.config(text="下載路徑")
            self.remote_path_label.config(text="SFTP 來源路徑 *\n（多個路徑以 ; 分隔）")
            self.local_path_label.config(text="本地端儲存路徑 *")
            self.duplicate_mode_label.config(text="來源檔案更新時：")
            self.start_button.config(text="開始下載")

    def _apply_settings_to_fields(self):
        s = self.settings
        if not s:
            return
        self.mode_var.set(s.get("mode", self.mode_var.get()))
        self.host_var.set(s.get("host", self.host_var.get()))
        self.port_var.set(str(s.get("port", self.port_var.get())))
        self.device_name_var.set(s.get("device_name", self.device_name_var.get()))
        self.username_var.set(s.get("username", self.username_var.get()))
        self.password_var.set(s.get("password", self.password_var.get()))
        remote_path = s.get("remote_path", self.remote_path_var.get())
        # 設定檔的 remote_path 可以是路徑陣列，GUI 以「; 」分隔顯示在同一欄位。
        if isinstance(remote_path, list):
            remote_path = "; ".join(remote_path)
        self.remote_path_var.set(remote_path)
        self.local_path_var.set(s.get("local_path", self.local_path_var.get()))
        self.auto_reconnect_var.set(bool(s.get("auto_reconnect", self.auto_reconnect_var.get())))
        self.resume_var.set(bool(s.get("resume", self.resume_var.get())))
        self.wait_network_var.set(bool(s.get("wait_for_network", self.wait_network_var.get())))
        self.recursive_var.set(bool(s.get("recursive", self.recursive_var.get())))
        self.upload_log_var.set(bool(s.get("upload_log", self.upload_log_var.get())))
        self.remote_log_dir_var.set(s.get("log_remote_dir", self.remote_log_dir_var.get()))
        self.duplicate_mode_var.set(s.get("duplicate_mode", self.duplicate_mode_var.get()))
        self.duplicate_suffix_var.set(s.get("duplicate_suffix", self.duplicate_suffix_var.get()))
        self.version_info_var.set(s.get("version_info", self.version_info_var.get()))
        self._toggle_log_dir()
        self._toggle_duplicate_suffix()

    def _update_title(self):
        self.root.title(f"SFTP 自動化下載工具 - {self.settings_path.name}")

    def _load_settings_file(self):
        chosen = filedialog.askopenfilename(
            title="選擇設定檔",
            initialdir=str(self.settings_path.parent),
            filetypes=[("JSON 設定檔", "*.json"), ("所有檔案", "*.*")],
        )
        if not chosen:
            return
        try:
            settings = load_settings(Path(chosen))
        except PlaceholderError as e:
            messagebox.showerror("設定檔載入失敗", str(e))
            return
        self.settings_path = Path(chosen)
        self.settings = settings
        self._apply_settings_to_fields()
        self._toggle_log_dir()
        self._toggle_duplicate_suffix()
        self._on_mode_change()
        self._update_title()
        self.status_var.set(f"已載入設定檔：{self.settings_path.name}")

    def _parse_remote_paths(self):
        """把來源路徑欄位解析成單一字串或路徑陣列（多個路徑以 ; 分隔）。"""
        paths = [p.strip() for p in self.remote_path_var.get().split(";") if p.strip()]
        if not paths:
            return ""
        return paths[0] if len(paths) == 1 else paths

    def _collect_fields(self):
        """收集目前畫面上所有欄位的值（鍵名同 settings.json 的欄位）。"""
        try:
            port = int(self.port_var.get().strip() or "22")
        except ValueError:
            port = 22
        return {
            "mode": self.mode_var.get(),
            "host": self.host_var.get().strip(),
            "port": port,
            "device_name": self.device_name_var.get().strip(),
            "username": self.username_var.get().strip(),
            "password": self.password_var.get(),
            "remote_path": self._parse_remote_paths(),
            "local_path": self.local_path_var.get().strip(),
            "auto_reconnect": self.auto_reconnect_var.get(),
            "resume": self.resume_var.get(),
            "wait_for_network": self.wait_network_var.get(),
            "recursive": self.recursive_var.get(),
            "upload_log": self.upload_log_var.get(),
            "log_remote_dir": self.remote_log_dir_var.get().strip(),
            "duplicate_mode": self.duplicate_mode_var.get(),
            "duplicate_suffix": self.duplicate_suffix_var.get().strip(),
            "version_info": self.version_info_var.get().strip(),
        }

    def _open_settings_file(self):
        ensure_settings_file(self.settings_path, seed=self._collect_fields())
        open_in_default_app(self.settings_path)
        messagebox.showinfo("設定檔", f"已開啟設定檔：\n{self.settings_path}\n\n編輯並儲存後，請按「載入設定檔」重新載入（或重新啟動程式）以套用變更。")

    def _export_settings_file(self):
        chosen = filedialog.asksaveasfilename(
            title="匯出設定檔",
            initialdir=str(self.settings_path.parent),
            initialfile="settings.json",
            defaultextension=".json",
            filetypes=[("JSON 設定檔", "*.json"), ("所有檔案", "*.*")],
        )
        if not chosen:
            return
        # 範本補齊所有欄位 → 疊上目前載入設定檔的值（保留 GUI 沒有欄位的設定，
        # 如 key_file、retry_count、ignore_file）→ 最後以畫面上目前的欄位值為準。
        data = dict(SETTINGS_TEMPLATE)
        data.update(self.settings)
        data.update(self._collect_fields())
        try:
            save_settings(chosen, data)
        except OSError as e:
            messagebox.showerror("匯出失敗", f"設定檔寫入失敗：\n{e}")
            return
        self.status_var.set(f"已匯出設定檔：{Path(chosen).name}")
        messagebox.showinfo("匯出設定檔", f"已匯出設定檔：\n{chosen}")

    def _browse_local_path(self):
        path = filedialog.askdirectory()
        if path:
            self.local_path_var.set(path)

    def _append_log(self, message):
        self.root.after(0, self._append_log_safe, message)

    def _append_log_safe(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def _start_download(self):
        is_upload = self.mode_var.get() == "upload"
        host = self.host_var.get().strip()
        device_name = self.device_name_var.get().strip()
        username = self.username_var.get().strip()
        remote_path = self._parse_remote_paths()
        local_path = self.local_path_var.get().strip()

        if not host or not device_name or not username or not remote_path or not local_path:
            path_hint = "SFTP 目的地路徑與本地端來源路徑" if is_upload else "SFTP 來源路徑與本地端儲存路徑"
            messagebox.showerror("欄位不完整", f"請填寫 SFTP 主機、裝置名稱、SFTP 帳號、{path_hint}")
            return
        if self.upload_log_var.get() and not self.remote_log_dir_var.get().strip():
            messagebox.showerror("欄位不完整", "已勾選上傳 Log，請填寫 SFTP 上的 Log 目錄")
            return
        try:
            port = int(self.port_var.get().strip() or "22")
        except ValueError:
            messagebox.showerror("格式錯誤", "Port 必須為數字")
            return

        self.start_button.config(state="disabled")
        self.status_var.set("執行中...")
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")

        log_dir = self.settings.get("log_dir") or DEFAULT_LOG_DIR
        version_info = self.version_info_var.get().strip()
        logger, log_file = create_logger(
            log_dir, device_name, version_info, log_callback=self._append_log,
            mode="upload" if is_upload else "download",
        )
        transfer_cls = SFTPUploader if is_upload else SFTPDownloader
        self.downloader = transfer_cls(
            host=host,
            port=port,
            username=username,
            password=self.password_var.get(),
            key_file=self.settings.get("key_file") or None,
            remote_path=remote_path,
            local_path=local_path,
            auto_reconnect=self.auto_reconnect_var.get(),
            resume=self.resume_var.get(),
            wait_for_network=self.wait_network_var.get(),
            recursive=self.recursive_var.get(),
            ignore_file=self.settings.get("ignore_file") or None,
            retry_count=self.settings.get("retry_count"),
            retry_delay=self.settings.get("retry_delay", 10),
            upload_log=self.upload_log_var.get(),
            remote_log_dir=self.remote_log_dir_var.get().strip() or None,
            duplicate_mode=self.duplicate_mode_var.get(),
            duplicate_suffix=self.duplicate_suffix_var.get().strip() or "copy",
            logger=logger,
            log_file=log_file,
        )

        self._active_is_upload = is_upload
        threading.Thread(target=self._run_download, daemon=True).start()

    def _run_download(self):
        success = self.downloader.run()
        self.root.after(0, self._on_finished, success)

    def _on_finished(self, success):
        verb = "上傳" if getattr(self, "_active_is_upload", False) else "下載"
        self.status_var.set(f"{verb}完成" if success else f"{verb}失敗，詳見 Log")
        self.start_button.config(state="normal")


def launch_gui():
    root = tk.Tk()
    SFTPDownloaderGUI(root)
    root.mainloop()
