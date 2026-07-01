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
        frm = ttk.Frame(self.root)
        frm.grid(row=0, column=0, sticky="nsew", **pad)

        row = 0
        ttk.Button(frm, text="載入設定檔...", command=self._load_settings_file).grid(
            row=row, column=0, columnspan=2, sticky="w", **pad
        )
        ttk.Button(frm, text="開啟設定檔", command=self._open_settings_file).grid(
            row=row, column=2, columnspan=2, sticky="e", **pad
        )

        row += 1
        ttk.Label(frm, text="SFTP 主機").grid(row=row, column=0, sticky="w", **pad)
        self.host_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.host_var, width=30).grid(row=row, column=1, **pad)

        ttk.Label(frm, text="Port").grid(row=row, column=2, sticky="w", **pad)
        self.port_var = tk.StringVar(value="22")
        ttk.Entry(frm, textvariable=self.port_var, width=8).grid(row=row, column=3, **pad)

        row += 1
        ttk.Label(frm, text="裝置名稱").grid(row=row, column=0, sticky="w", **pad)
        self.device_name_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.device_name_var, width=30).grid(row=row, column=1, **pad)

        row += 1
        ttk.Label(frm, text="帳號").grid(row=row, column=0, sticky="w", **pad)
        self.username_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.username_var, width=30).grid(row=row, column=1, **pad)

        ttk.Label(frm, text="密碼").grid(row=row, column=2, sticky="w", **pad)
        self.password_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.password_var, width=15, show="*").grid(row=row, column=3, **pad)

        row += 1
        ttk.Label(frm, text="SFTP 來源路徑").grid(row=row, column=0, sticky="w", **pad)
        self.remote_path_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.remote_path_var, width=50).grid(row=row, column=1, columnspan=3, sticky="we", **pad)

        row += 1
        ttk.Label(frm, text="本地端儲存路徑").grid(row=row, column=0, sticky="w", **pad)
        self.local_path_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.local_path_var, width=42).grid(row=row, column=1, columnspan=2, sticky="we", **pad)
        ttk.Button(frm, text="瀏覽...", command=self._browse_local_path).grid(row=row, column=3, **pad)

        row += 1
        self.auto_reconnect_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="斷線自動重連", variable=self.auto_reconnect_var).grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        self.resume_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="斷點續傳", variable=self.resume_var).grid(row=row, column=2, columnspan=2, sticky="w", **pad)

        row += 1
        self.wait_network_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="網路偵測自動下載", variable=self.wait_network_var).grid(row=row, column=0, columnspan=2, sticky="w", **pad)

        row += 1
        self.upload_log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm, text="完成後將 Log 上傳回 SFTP", variable=self.upload_log_var, command=self._toggle_log_dir
        ).grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        self.remote_log_dir_var = tk.StringVar()
        self.remote_log_dir_entry = ttk.Entry(frm, textvariable=self.remote_log_dir_var, width=26, state="disabled")
        self.remote_log_dir_entry.grid(row=row, column=2, columnspan=2, sticky="we", **pad)

        row += 1
        self.start_button = ttk.Button(frm, text="開始下載", command=self._start_download)
        self.start_button.grid(row=row, column=0, columnspan=4, sticky="we", **pad)

        row += 1
        self.status_var = tk.StringVar(value="尚未開始")
        ttk.Label(frm, textvariable=self.status_var).grid(row=row, column=0, columnspan=4, sticky="w", **pad)

        row += 1
        self.log_text = scrolledtext.ScrolledText(frm, width=80, height=18, state="disabled")
        self.log_text.grid(row=row, column=0, columnspan=4, sticky="nsew", **pad)

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
            messagebox.showerror("欄位不完整", "請填寫主機、裝置名稱、帳號、來源路徑與本地端儲存路徑")
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
