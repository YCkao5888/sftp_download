"""SFTP 下載工具的圖形化介面（不帶任何 CLI 參數執行時自動啟動）。"""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from downloader import SFTPDownloader, create_logger
from settings import SETTINGS_PATH, ensure_settings_file, load_settings, open_in_default_app

DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"


class SFTPDownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.downloader = None
        self.settings_path = SETTINGS_PATH
        self.settings = load_settings(self.settings_path)
        self._build_widgets()
        self._apply_settings_to_fields()
        self._update_title()

    def _build_widgets(self):
        pad = {"padx": 6, "pady": 4}
        self.root.minsize(600, 560)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        outer = ttk.Frame(self.root, padding=10)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)

        settings_bar = ttk.Frame(outer)
        settings_bar.grid(row=0, column=0, sticky="we", pady=(0, 8))
        ttk.Button(settings_bar, text="載入設定檔...", command=self._load_settings_file).pack(side="left")
        ttk.Button(settings_bar, text="開啟設定檔", command=self._open_settings_file).pack(side="left", padx=(6, 0))

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

        ttk.Label(conn_frame, text="裝置名稱 *").grid(row=1, column=0, sticky="w", **pad)
        self.device_name_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.device_name_var).grid(
            row=1, column=1, columnspan=3, sticky="we", **pad
        )

        ttk.Label(conn_frame, text="SFTP 帳號 *").grid(row=2, column=0, sticky="w", **pad)
        self.username_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.username_var).grid(row=2, column=1, sticky="we", **pad)

        ttk.Label(conn_frame, text="SFTP 密碼 *").grid(row=2, column=2, sticky="w", **pad)
        self.password_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.password_var, show="*").grid(row=2, column=3, sticky="we", **pad)

        ttk.Label(
            conn_frame,
            text="（若已在設定檔中設定 key_file 金鑰登入，密碼可留空）",
            foreground="#777777",
        ).grid(row=3, column=0, columnspan=4, sticky="w", padx=6)

        # --- 下載路徑（必填） ---
        path_frame = ttk.LabelFrame(outer, text="下載路徑", padding=8)
        path_frame.grid(row=4, column=0, sticky="we", pady=(0, 8))
        path_frame.columnconfigure(1, weight=1)

        ttk.Label(path_frame, text="SFTP 來源路徑 *").grid(row=0, column=0, sticky="w", **pad)
        self.remote_path_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.remote_path_var).grid(
            row=0, column=1, columnspan=2, sticky="we", **pad
        )

        ttk.Label(path_frame, text="本地端儲存路徑 *").grid(row=1, column=0, sticky="w", **pad)
        self.local_path_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.local_path_var).grid(row=1, column=1, sticky="we", **pad)
        ttk.Button(path_frame, text="瀏覽...", command=self._browse_local_path).grid(row=1, column=2, **pad)

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

        # --- Log 設定（選填） ---
        log_frame = ttk.LabelFrame(outer, text="Log 設定（選填）", padding=8)
        log_frame.grid(row=6, column=0, sticky="we", pady=(0, 8))
        log_frame.columnconfigure(2, weight=1)

        self.upload_log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            log_frame, text="完成後將 Log 上傳回 SFTP", variable=self.upload_log_var, command=self._toggle_log_dir
        ).grid(row=0, column=0, sticky="w", **pad)
        ttk.Label(log_frame, text="上傳目錄：").grid(row=0, column=1, sticky="e", **pad)
        self.remote_log_dir_var = tk.StringVar()
        self.remote_log_dir_entry = ttk.Entry(log_frame, textvariable=self.remote_log_dir_var, state="disabled")
        self.remote_log_dir_entry.grid(row=0, column=2, sticky="we", **pad)

        # --- 執行 ---
        self.start_button = ttk.Button(outer, text="開始下載", command=self._start_download)
        self.start_button.grid(row=7, column=0, sticky="we", pady=(4, 8), ipady=4)

        self.status_var = tk.StringVar(value="尚未開始")
        ttk.Label(outer, textvariable=self.status_var, font=("", 9, "bold")).grid(
            row=8, column=0, sticky="w", pady=(0, 4)
        )

        self.log_text = scrolledtext.ScrolledText(outer, width=80, height=16, state="disabled")
        self.log_text.grid(row=9, column=0, sticky="nsew")
        outer.rowconfigure(9, weight=1)

    def _toggle_log_dir(self):
        state = "normal" if self.upload_log_var.get() else "disabled"
        self.remote_log_dir_entry.config(state=state)

    def _apply_settings_to_fields(self):
        s = self.settings
        if not s:
            return
        self.host_var.set(s.get("host", self.host_var.get()))
        self.port_var.set(str(s.get("port", self.port_var.get())))
        self.device_name_var.set(s.get("device_name", self.device_name_var.get()))
        self.username_var.set(s.get("username", self.username_var.get()))
        self.password_var.set(s.get("password", self.password_var.get()))
        self.remote_path_var.set(s.get("remote_path", self.remote_path_var.get()))
        self.local_path_var.set(s.get("local_path", self.local_path_var.get()))
        self.auto_reconnect_var.set(bool(s.get("auto_reconnect", self.auto_reconnect_var.get())))
        self.resume_var.set(bool(s.get("resume", self.resume_var.get())))
        self.wait_network_var.set(bool(s.get("wait_for_network", self.wait_network_var.get())))
        self.upload_log_var.set(bool(s.get("upload_log", self.upload_log_var.get())))
        self.remote_log_dir_var.set(s.get("log_remote_dir", self.remote_log_dir_var.get()))
        self._toggle_log_dir()

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
        self.settings_path = Path(chosen)
        self.settings = load_settings(self.settings_path)
        self._apply_settings_to_fields()
        self._update_title()
        self.status_var.set(f"已載入設定檔：{self.settings_path.name}")

    def _open_settings_file(self):
        try:
            port = int(self.port_var.get().strip() or "22")
        except ValueError:
            port = 22
        seed = {
            "host": self.host_var.get().strip(),
            "port": port,
            "device_name": self.device_name_var.get().strip(),
            "username": self.username_var.get().strip(),
            "password": self.password_var.get(),
            "remote_path": self.remote_path_var.get().strip(),
            "local_path": self.local_path_var.get().strip(),
            "auto_reconnect": self.auto_reconnect_var.get(),
            "resume": self.resume_var.get(),
            "wait_for_network": self.wait_network_var.get(),
            "upload_log": self.upload_log_var.get(),
            "log_remote_dir": self.remote_log_dir_var.get().strip(),
        }
        ensure_settings_file(self.settings_path, seed=seed)
        open_in_default_app(self.settings_path)
        messagebox.showinfo("設定檔", f"已開啟設定檔：\n{self.settings_path}\n\n編輯並儲存後，請按「載入設定檔」重新載入（或重新啟動程式）以套用變更。")

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
        host = self.host_var.get().strip()
        device_name = self.device_name_var.get().strip()
        username = self.username_var.get().strip()
        remote_path = self.remote_path_var.get().strip()
        local_path = self.local_path_var.get().strip()

        if not host or not device_name or not username or not remote_path or not local_path:
            messagebox.showerror("欄位不完整", "請填寫 SFTP 主機、裝置名稱、SFTP 帳號、SFTP 來源路徑與本地端儲存路徑")
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
        logger, log_file = create_logger(log_dir, device_name, log_callback=self._append_log)
        self.downloader = SFTPDownloader(
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
            retry_count=self.settings.get("retry_count"),
            retry_delay=self.settings.get("retry_delay", 10),
            upload_log=self.upload_log_var.get(),
            remote_log_dir=self.remote_log_dir_var.get().strip() or None,
            logger=logger,
            log_file=log_file,
        )

        threading.Thread(target=self._run_download, daemon=True).start()

    def _run_download(self):
        success = self.downloader.run()
        self.root.after(0, self._on_finished, success)

    def _on_finished(self, success):
        self.status_var.set("下載完成" if success else "下載失敗，詳見 Log")
        self.start_button.config(state="normal")


def launch_gui():
    root = tk.Tk()
    SFTPDownloaderGUI(root)
    root.mainloop()
