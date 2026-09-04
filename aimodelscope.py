import csv
import json
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from tkinter import messagebox, ttk

import customtkinter as ctk
import requests

CONFIG_FILE = "api_configs.json"
SPEED_TEST_TOKENS = 100
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
MODELS_DEV_URL = "https://models.dev/api.json"

FONT_FAMILY = "Microsoft YaHei"
FONT_UI = lambda size=13, bold=False: ctk.CTkFont(family=FONT_FAMILY, size=size, weight="bold" if bold else "normal")
FONT_TREE = (FONT_FAMILY, 11)

DEV_INFO = "需求 by Tiger | 开发 by DeepSeek V4 Pro | 更新: 2026-09-04"

STATUS_LABELS: dict[str, str] = {
    "ok": "可用的",
    "no_permission": "无权限",
    "invalid": "无效模型",
    "timeout": "超时",
    "error": "错误",
}

DEFAULT_CONFIGS: list[dict[str, str]] = []


def load_configs() -> list[dict[str, str]]:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_configs(configs: list[dict[str, str]]) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(configs, f, ensure_ascii=False, indent=2)


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


class App:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        root.title("AIModelScope - AI模型管理器")
        root.geometry("1280x760")
        root.resizable(True, True)
        root.minsize(1000, 560)

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.configs: list[dict[str, str]] = load_configs()
        self.current_key: str = self.configs[0]["key"] if self.configs else ""
        self.models: list[dict] = []
        self.statuses: dict[str, str] = {}
        self.speeds: dict[str, float] = {}
        self.ttfts: dict[str, float] = {}
        self.price_info: dict[str, dict] = {}
        self._dev_exact: dict[str, dict] | None = None
        self._dev_base: dict[str, list[dict]] | None = None
        self._pricing_source: str = ""
        self._speed_lock = threading.Lock()

        self._build_ui()
        self._config_speed_tags()
        self._refresh_dropdown()
        if self.configs:
            self._select_config(0)

    # ── UI ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("Treeview", font=FONT_TREE)
        style.configure("Treeview.Heading", font=(FONT_FAMILY, 11, "bold"))

        # ── top: API config ──
        top = ctk.CTkFrame(self.root)
        top.pack(fill=tk.X, padx=12, pady=(12, 0))

        header_row = ctk.CTkFrame(top, fg_color="transparent")
        header_row.pack(fill=tk.X, padx=10, pady=(10, 0))
        ctk.CTkLabel(header_row, text="API 配置", font=FONT_UI(15, True)).pack(side=tk.LEFT)

        row0 = ctk.CTkFrame(top, fg_color="transparent")
        row0.pack(fill=tk.X, padx=10, pady=(8, 0))
        ctk.CTkLabel(row0, text="已保存:", width=50, font=FONT_UI()).pack(side=tk.LEFT)
        self.cfg_var = tk.StringVar()
        self.cfg_dropdown = ctk.CTkComboBox(row0, variable=self.cfg_var, state="readonly", width=400, font=FONT_UI())
        self.cfg_dropdown.pack(side=tk.LEFT, padx=5)
        self.cfg_dropdown.configure(command=self._on_dropdown_select)
        ctk.CTkButton(row0, text="+ 新增", width=70, command=self._add_config, font=FONT_UI(13, True),
                      fg_color="#27ae60", hover_color="#1e8449", text_color="white").pack(side=tk.LEFT, padx=4)
        ctk.CTkButton(row0, text="删除", width=60, command=self._delete_config, font=FONT_UI(13, True),
                      fg_color="#c0392b", hover_color="#922b21", text_color="white").pack(side=tk.LEFT)

        row1 = ctk.CTkFrame(top, fg_color="transparent")
        row1.pack(fill=tk.X, padx=10, pady=(8, 0))
        ctk.CTkLabel(row1, text="URL :", width=50, font=FONT_UI()).pack(side=tk.LEFT)
        self.url_var = tk.StringVar()
        self.url_entry = ctk.CTkEntry(row1, textvariable=self.url_var, width=600, font=FONT_UI())
        self.url_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        row2 = ctk.CTkFrame(top, fg_color="transparent")
        row2.pack(fill=tk.X, padx=10, pady=(8, 10))
        ctk.CTkLabel(row2, text="Key :", width=50, font=FONT_UI()).pack(side=tk.LEFT)
        self.key_var = tk.StringVar()
        self.key_entry = ctk.CTkEntry(row2, textvariable=self.key_var, width=600, show="*", font=FONT_UI())
        self.key_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.eye_btn = ctk.CTkButton(row2, text="👁", width=35, command=self._toggle_key_visible, font=FONT_UI(14, True),
                                     fg_color="transparent", border_width=1, text_color=("gray30", "gray70"))
        self.eye_btn.pack(side=tk.LEFT)

        # ── toolbar ──
        toolbar = ctk.CTkFrame(self.root, fg_color="transparent")
        toolbar.pack(fill=tk.X, padx=12, pady=(10, 0))
        ctk.CTkButton(toolbar, text="获取模型列表", command=self.fetch_models, width=120,
                      font=FONT_UI(13, True), fg_color="#2563eb", hover_color="#1d4ed8", text_color="white").pack(side=tk.LEFT, padx=(0, 6))
        ctk.CTkButton(toolbar, text="测试全部可用性", command=self.test_all, width=130,
                      font=FONT_UI(13, True), fg_color="#d97706", hover_color="#b45309", text_color="white").pack(side=tk.LEFT, padx=(0, 6))
        ctk.CTkButton(toolbar, text="测速 (选中)", command=self._speed_test_selected, width=110,
                      font=FONT_UI(13, True), fg_color="#2563eb", hover_color="#1d4ed8", text_color="white").pack(side=tk.LEFT, padx=(0, 6))
        ctk.CTkButton(toolbar, text="导出CSV", command=self.export_csv, width=90,
                      font=FONT_UI(13, True), fg_color="#27ae60", hover_color="#1e8449", text_color="white").pack(side=tk.LEFT, padx=(0, 6))
        ctk.CTkButton(toolbar, text="价格 (models.dev)", command=self.fetch_pricing, width=140,
                      font=FONT_UI(13, True), fg_color="#7c3aed", hover_color="#6d28d9", text_color="white").pack(side=tk.LEFT, padx=(0, 6))
        self.progress = ctk.CTkProgressBar(toolbar, width=180)
        self.progress.pack(side=tk.LEFT, padx=12)
        self.progress.set(0)
        self.status_lbl = ctk.CTkLabel(toolbar, text="", font=FONT_UI())
        self.status_lbl.pack(side=tk.LEFT)

        # ── filter row ──
        filter_row = ctk.CTkFrame(self.root, fg_color="transparent")
        filter_row.pack(fill=tk.X, padx=12, pady=(6, 0))
        ctk.CTkLabel(filter_row, text="搜索:", font=FONT_UI()).pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = ctk.CTkEntry(filter_row, textvariable=self.search_var, width=260,
                                         font=FONT_UI(), placeholder_text="模型 ID / 来源...")
        self.search_entry.pack(side=tk.LEFT, padx=(5, 12))
        self.search_entry.bind("<KeyRelease>", lambda e: self._render_rows())
        self.only_ok_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(filter_row, text="只看可用", variable=self.only_ok_var,
                        command=self._render_rows, font=FONT_UI()).pack(side=tk.LEFT)
        ctk.CTkButton(filter_row, text="清除", width=50, fg_color="transparent", border_width=1,
                      text_color=("gray30", "gray70"), hover_color=("gray85", "gray25"),
                      command=self._clear_filter, font=FONT_UI(12)).pack(side=tk.LEFT, padx=12)

        # ── tree + scrollbar ──
        tree_frame = ctk.CTkFrame(self.root)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        self._sort_col: str = ""
        self._sort_asc: bool = True
        columns = ("id", "created_date", "owned_by", "status", "speed", "ttft", "price", "context", "output")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("id", text="模型 ID", command=lambda: self._sort_by("id"))
        self.tree.heading("created_date", text="创建日期", command=lambda: self._sort_by("created_date"))
        self.tree.heading("owned_by", text="来源", command=lambda: self._sort_by("owned_by"))
        self.tree.heading("status", text="状态", command=lambda: self._sort_by("status"))
        self.tree.heading("speed", text="速度", command=lambda: self._sort_by("speed"))
        self.tree.heading("ttft", text="首token", command=lambda: self._sort_by("ttft"))
        self.tree.heading("price", text="价格 $/1M", command=lambda: self._sort_by("price"))
        self.tree.heading("context", text="上下文", command=lambda: self._sort_by("context"))
        self.tree.heading("output", text="输出上限", command=lambda: self._sort_by("output"))
        self.tree.column("id", width=280)
        self.tree.column("created_date", width=130, anchor=tk.CENTER)
        self.tree.column("owned_by", width=80, anchor=tk.CENTER)
        self.tree.column("status", width=80, anchor=tk.CENTER)
        self.tree.column("speed", width=90, anchor=tk.CENTER)
        self.tree.column("ttft", width=70, anchor=tk.CENTER)
        self.tree.column("price", width=100, anchor=tk.CENTER)
        self.tree.column("context", width=80, anchor=tk.CENTER)
        self.tree.column("output", width=80, anchor=tk.CENTER)

        v_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # right-click menu
        self.ctx_menu = tk.Menu(self.root, tearoff=0)
        self.ctx_menu.add_command(label="测速", command=self._speed_test_selected)
        self.ctx_menu.add_command(label="复制 ID", command=self._copy_model_id)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-1>", self._on_double_click)

        # ── stats ──
        self.stats_lbl = ctk.CTkLabel(self.root, text="就绪", font=FONT_UI(12))
        self.stats_lbl.pack(fill=tk.X, padx=12, pady=(0, 2))

        # ── footer ──
        self.footer_lbl = ctk.CTkLabel(self.root, text=DEV_INFO, text_color="gray50", font=FONT_UI(11))
        self.footer_lbl.pack(pady=(0, 10))

    def _config_speed_tags(self) -> None:
        self.tree.tag_configure("speed_fast", foreground="green")
        self.tree.tag_configure("speed_medium", foreground="#CC8800")
        self.tree.tag_configure("speed_slow", foreground="red")
        self.tree.tag_configure("ttft_fast", foreground="green")
        self.tree.tag_configure("ttft_medium", foreground="#CC8800")
        self.tree.tag_configure("ttft_slow", foreground="red")

    # ── config management ────────────────────────────────────────

    def _refresh_dropdown(self) -> None:
        values = [f"{c['name']}  [{mask_key(c['key'])}]" for c in self.configs]
        self.cfg_dropdown.configure(values=values)

    def _select_config(self, idx: int) -> None:
        if not self.configs:
            return
        values = self.cfg_dropdown.cget("values")
        if values:
            self.cfg_dropdown.set(values[min(idx, len(values) - 1)])
        c = self.configs[idx]
        self.url_var.set(c["url"])
        self.current_key = c["key"]
        self.key_visible = False
        self.key_var.set(c["key"])
        self.key_entry.configure(show="*")
        self.eye_btn.configure(text="👁")

    def _on_dropdown_select(self, choice: str) -> None:
        values = self.cfg_dropdown.cget("values")
        try:
            idx = values.index(choice)
        except ValueError:
            return
        self._select_config(idx)

    def _toggle_key_visible(self) -> None:
        if self.key_visible:
            self.key_entry.configure(show="*")
            self.eye_btn.configure(text="👁")
        else:
            self.key_entry.configure(show="")
            self.eye_btn.configure(text="🔒")
        self.key_visible = not self.key_visible

    def _add_config(self) -> None:
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("新增 API 配置")
        dlg.geometry("440x240")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        self._center_window(dlg)

        ctk.CTkLabel(dlg, text="新增 API 配置", font=FONT_UI(15, True)).pack(pady=(16, 12))

        row0 = ctk.CTkFrame(dlg, fg_color="transparent")
        row0.pack(fill=tk.X, padx=30, pady=4)
        ctk.CTkLabel(row0, text="名称", width=50, font=FONT_UI()).pack(side=tk.LEFT)
        name_var = tk.StringVar()
        ctk.CTkEntry(row0, textvariable=name_var, width=280, font=FONT_UI()).pack(side=tk.LEFT, padx=10)

        row1 = ctk.CTkFrame(dlg, fg_color="transparent")
        row1.pack(fill=tk.X, padx=30, pady=4)
        ctk.CTkLabel(row1, text="URL", width=50, font=FONT_UI()).pack(side=tk.LEFT)
        url_var = tk.StringVar()
        ctk.CTkEntry(row1, textvariable=url_var, width=280, font=FONT_UI()).pack(side=tk.LEFT, padx=10)

        row2 = ctk.CTkFrame(dlg, fg_color="transparent")
        row2.pack(fill=tk.X, padx=30, pady=4)
        ctk.CTkLabel(row2, text="Key", width=50, font=FONT_UI()).pack(side=tk.LEFT)
        key_var = tk.StringVar()
        ctk.CTkEntry(row2, textvariable=key_var, width=280, show="*", font=FONT_UI()).pack(side=tk.LEFT, padx=10)

        def _save() -> None:
            name = name_var.get().strip()
            url = url_var.get().strip()
            key = key_var.get().strip()
            if not name or not url or not key:
                messagebox.showwarning("提示", "所有字段必填")
                return
            self.configs.append({"name": name, "url": url, "key": key})
            save_configs(self.configs)
            self._refresh_dropdown()
            self._select_config(len(self.configs) - 1)
            dlg.destroy()

        ctk.CTkButton(dlg, text="保存", command=_save, width=120,
                      font=FONT_UI(13, True), fg_color="#2563eb", hover_color="#1d4ed8", text_color="white").pack(pady=18)

    def _delete_config(self) -> None:
        idx = self._current_config_index()
        if idx < 0:
            return
        if len(self.configs) <= 1:
            messagebox.showwarning("提示", "至少保留一个配置")
            return
        name = self.configs[idx]["name"]
        if not messagebox.askyesno("确认", f"删除配置 \"{name}\"?"):
            return
        del self.configs[idx]
        save_configs(self.configs)
        self._refresh_dropdown()
        self._select_config(min(idx, len(self.configs) - 1))

    def _current_config_index(self) -> int:
        choice = self.cfg_var.get()
        values = self.cfg_dropdown.cget("values")
        try:
            return values.index(choice)
        except ValueError:
            return -1

    # ── helpers ──────────────────────────────────────────────────

    def _center_window(self, win: ctk.CTkToplevel) -> None:
        win.update_idletasks()
        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        parent_w = self.root.winfo_width()
        parent_h = self.root.winfo_height()
        w = win.winfo_width()
        h = win.winfo_height()
        x = parent_x + (parent_w - w) // 2
        y = parent_y + (parent_h - h) // 2
        win.geometry(f"+{x}+{y}")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.current_key}", "Content-Type": "application/json"}

    def _api_url(self) -> str:
        return self.url_var.get().strip().rstrip("/")

    # ── fetch models ─────────────────────────────────────────────

    def fetch_models(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.models.clear()
        self.statuses.clear()
        self.speeds.clear()
        self.ttfts.clear()
        self.status_lbl.configure(text="获取中...")
        self.root.update_idletasks()
        try:
            resp = requests.get(f"{self._api_url()}/models", headers=self._headers(), timeout=30)
            resp.raise_for_status()
            self.models = resp.json().get("data", [])
            self._render_rows()
            self.status_lbl.configure(text=f"共 {len(self.models)} 个模型")
            self.stats_lbl.configure(text=f"共 {len(self.models)} 个模型 | 可用性未测试")
        except requests.exceptions.RequestException as e:
            messagebox.showerror("错误", f"获取模型失败:\n{e}")
            self.status_lbl.configure(text="失败")

    # ── filter & render ──────────────────────────────────────────

    def _clear_filter(self) -> None:
        self.search_var.set("")
        self.only_ok_var.set(False)
        self._render_rows()

    def _matches_filter(self, mid: str, m: dict) -> bool:
        kw = self.search_var.get().strip().lower()
        if kw and kw not in mid.lower() and kw not in m.get("owned_by", "").lower():
            return False
        if self.only_ok_var.get() and self.statuses.get(mid) != "ok":
            return False
        return True

    @staticmethod
    def _speed_tag(tps: float) -> str:
        if tps >= 50:
            return "speed_fast"
        if tps >= 20:
            return "speed_medium"
        return "speed_slow"

    @staticmethod
    def _ttft_tag(ttft: float) -> str:
        if ttft <= 1.0:
            return "ttft_fast"
        if ttft <= 2.5:
            return "ttft_medium"
        return "ttft_slow"

    def _row_values(self, m: dict) -> tuple:
        mid = m.get("id", "")
        ts = m.get("created", 0)
        d = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        status = self.statuses.get(mid, "")
        speed = self.speeds.get(mid)
        if isinstance(speed, float) and speed > 0:
            speed_lbl = f"{speed:.1f} tok/s"
        elif mid in self.speeds:
            speed_lbl = "失败"
        else:
            speed_lbl = ""
        ttft = self.ttfts.get(mid)
        ttft_lbl = f"{ttft:.2f}s" if isinstance(ttft, float) and ttft > 0 else ""
        info = self.price_info.get(mid)
        price_lbl = self._fmt_price(info) if info else ""
        ctx_lbl = self._fmt_tokens(info.get("context")) if info and info.get("context") else ""
        out_lbl = self._fmt_tokens(info.get("output")) if info and info.get("output") else ""
        return (mid, d, m.get("owned_by", ""), STATUS_LABELS.get(status, status),
                speed_lbl, ttft_lbl, price_lbl, ctx_lbl, out_lbl)

    def _render_rows(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for m in self.models:
            mid = m.get("id", "")
            if not self._matches_filter(mid, m):
                continue
            tags = []
            s = self.speeds.get(mid)
            if isinstance(s, float) and s > 0:
                tags.append(self._speed_tag(s))
            t = self.ttfts.get(mid)
            if isinstance(t, float) and t > 0:
                tags.append(self._ttft_tag(t))
            self.tree.insert("", tk.END, iid=mid, values=self._row_values(m), tags=tuple(tags))

    # ── availability test ────────────────────────────────────────

    def test_all(self) -> None:
        if not self.models:
            messagebox.showwarning("提示", "请先获取模型列表")
            return
        self.statuses.clear()
        ids = [m["id"] for m in self.models]
        total = len(ids)
        done = 0
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self.status_lbl.configure(text="测试中...")

        api_url = self._api_url()
        headers = self._headers()

        def _run() -> None:
            nonlocal done
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(self._test_one, mid, api_url, headers): mid for mid in ids}
                for fut in as_completed(futures):
                    mid = futures[fut]
                    self.statuses[mid] = fut.result()
                    done += 1
                    progress_val = done / total if total else 0
                    self.root.after(0, self.progress.set, progress_val)
                    self.root.after(0, self._update_progress, done, total)
            self.root.after(0, self._test_done)

        threading.Thread(target=_run, daemon=True).start()

    def _test_one(self, model_id: str, api_url: str, headers: dict[str, str]) -> str:
        try:
            resp = requests.post(
                f"{api_url}/chat/completions",
                headers=headers,
                json={"model": model_id, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 3},
                timeout=60,
            )
            if resp.status_code == 200:
                return "ok"
            if resp.status_code == 403:
                return "no_permission"
            if resp.status_code == 404:
                return "invalid"
            return "error"
        except requests.exceptions.Timeout:
            return "timeout"
        except requests.exceptions.RequestException:
            return "error"

    def _update_progress(self, done: int, total: int) -> None:
        ok = sum(1 for v in self.statuses.values() if v == "ok")
        self.status_lbl.configure(text=f"测试中... {done}/{total}  可用 {ok}")
        for mid, s in self.statuses.items():
            if self.tree.exists(mid):
                self.tree.set(mid, "status", STATUS_LABELS.get(s, s))

    def _test_done(self) -> None:
        self.progress.set(0)
        ok = sum(1 for v in self.statuses.values() if v == "ok")
        total = len(self.models)
        self.status_lbl.configure(text="测试完成")
        self.stats_lbl.configure(text=f"共 {total} 个模型 | 可用 {ok} | 不可用 {total - ok}")
        self._render_rows()

    # ── speed test ───────────────────────────────────────────────

    def _speed_test_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请在表格中选中要测速的模型")
            return
        threading.Thread(target=self._speed_test_batch, args=(list(selected),), daemon=True).start()

    def _speed_test_batch(self, model_ids: list[str]) -> None:
        total = len(model_ids)
        for i, mid in enumerate(model_ids):
            self.root.after(0, self.progress.set, (i + 1) / total)
            self.root.after(0, lambda m=mid: self._set_speed(m, "测速中...", "", "-", ""))
            self.root.after(0, self.status_lbl.configure, f"测速中... {i + 1}/{total}")
            tps, ttft, elapsed = self._speed_test_one(mid)
            with self._speed_lock:
                self.speeds[mid] = tps
                self.ttfts[mid] = ttft
            if tps > 0:
                label = f"{tps:.1f} tok/s"
                tag = self._speed_tag(tps)
            else:
                label = "失败"
                tag = "speed_slow"
            ttft_label = f"{ttft:.2f}s" if ttft > 0 else "-"
            ttft_tag = self._ttft_tag(ttft) if ttft > 0 else ""
            self.root.after(0, lambda m=mid, l=label, t=tag, tl=ttft_label, tt=ttft_tag:
                            self._set_speed(m, l, t, tl, tt))
        self.root.after(0, self.progress.set, 0)
        self.root.after(0, self.status_lbl.configure, "测速完成")

    def _speed_test_one(self, model_id: str) -> tuple[float, float, float]:
        """流式请求测速：返回 (tokens/s, 首token延迟TTFT秒, 总耗时秒)。失败返回 (0, 0, elapsed)。"""
        api_url = self._api_url()
        headers = self._headers()
        start = time.time()
        try:
            with requests.post(
                f"{api_url}/chat/completions",
                headers=headers,
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": SPEED_TEST_TOKENS,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
                timeout=120,
                stream=True,
            ) as resp:
                if resp.status_code != 200 or "text/event-stream" not in resp.headers.get("Content-Type", ""):
                    resp.close()
                    return self._speed_test_one_nostream(model_id, api_url, headers)
                ttft: float | None = None
                completion_tokens = 0
                content_chunks = 0
                for raw in resp.iter_lines():
                    if not raw:
                        continue
                    line = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        if delta.get("content") or delta.get("reasoning_content"):
                            if ttft is None:
                                ttft = time.time() - start
                            content_chunks += 1
                    usage = chunk.get("usage")
                    if usage:
                        completion_tokens = usage.get("completion_tokens") or 0
                elapsed = time.time() - start
            if completion_tokens <= 0:
                completion_tokens = content_chunks  # 服务端未返回 usage 时按内容块数估算
            speed = completion_tokens / elapsed if elapsed > 0 and completion_tokens > 0 else 0
            return speed, (ttft if ttft is not None else 0.0), elapsed
        except requests.exceptions.RequestException:
            return 0, 0.0, time.time() - start

    def _speed_test_one_nostream(self, model_id: str, api_url: str, headers: dict[str, str]) -> tuple[float, float, float]:
        """不支持流式输出的接口走非流式测速，TTFT 未知记 0。"""
        start = time.time()
        try:
            resp = requests.post(
                f"{api_url}/chat/completions",
                headers=headers,
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": SPEED_TEST_TOKENS,
                },
                timeout=120,
            )
            elapsed = time.time() - start
            if resp.status_code == 200:
                completion_tokens = resp.json().get("usage", {}).get("completion_tokens", 0)
                speed = completion_tokens / elapsed if elapsed > 0 and completion_tokens else 0
                return speed, 0.0, elapsed
            return 0, 0.0, elapsed
        except requests.exceptions.RequestException:
            return 0, 0.0, time.time() - start

    def _set_speed(self, model_id: str, speed_label: str, speed_tag: str, ttft_label: str, ttft_tag: str) -> None:
        if self.tree.exists(model_id):
            self.tree.set(model_id, "speed", speed_label)
            self.tree.set(model_id, "ttft", ttft_label)
            tags = [t for t in (speed_tag, ttft_tag) if t]
            self.tree.item(model_id, tags=tuple(tags))

    # ── context menu ─────────────────────────────────────────────

    def _on_right_click(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.ctx_menu.tk_popup(event.x_root, event.y_root)

    def _on_double_click(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item and self.statuses.get(item) == "ok":
            threading.Thread(target=self._speed_test_batch, args=([item],), daemon=True).start()

    def _copy_model_id(self) -> None:
        selected = self.tree.selection()
        if selected:
            self.root.clipboard_clear()
            self.root.clipboard_append(selected[0])

    # ── sorting ──────────────────────────────────────────────────

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True

        all_cols = ("id", "created_date", "owned_by", "status", "speed", "ttft", "price", "context", "output")
        for c in all_cols:
            text = self.tree.heading(c)["text"].rstrip(" ▲▼")
            if c == col:
                text += " ▲" if self._sort_asc else " ▼"
            self.tree.heading(c, text=text, command=lambda c2=c: self._sort_by(c2))

        items = list(self.tree.get_children(""))
        key_map = {
            "id": str,
            "created_date": str,
            "owned_by": str,
            "status": str,
            "speed": self._speed_sort_key,
            "ttft": self._ttft_sort_key,
            "price": self._price_sort_key,
            "context": self._size_sort_key,
            "output": self._size_sort_key,
        }
        key_fn = key_map.get(col, str)
        items.sort(key=lambda iid: key_fn(self.tree.set(iid, col)), reverse=not self._sort_asc)
        for idx, iid in enumerate(items):
            self.tree.move(iid, "", idx)

    @staticmethod
    def _speed_sort_key(val: str) -> float:
        try:
            return float(val.split()[0])
        except (ValueError, IndexError):
            return -1.0

    @staticmethod
    def _ttft_sort_key(val: str) -> float:
        try:
            return float(val.rstrip("s"))
        except ValueError:
            return 1e9  # 未测的排到最后

    @staticmethod
    def _price_sort_key(val: str) -> float:
        v = val.strip()
        if v == "免费":
            return 0.0
        if not v.startswith("$"):
            return -1.0
        try:
            return float(v[1:].split("/")[0])
        except ValueError:
            return -1.0

    @staticmethod
    def _size_sort_key(val: str) -> float:
        v = val.strip()
        if v.endswith("M"):
            return float(v[:-1]) * 1_000_000
        if v.endswith("K"):
            return float(v[:-1]) * 1000
        try:
            return float(v)
        except ValueError:
            return -1.0

    # ── pricing (models.dev) ─────────────────────────────────────

    def fetch_pricing(self) -> None:
        if self._dev_exact is not None:
            self._apply_pricing()
            return
        if not self.models:
            messagebox.showwarning("提示", "请先获取模型列表")
            return
        self.status_lbl.configure(text="获取价格信息中...")
        threading.Thread(target=self._pricing_worker, daemon=True).start()

    def _pricing_worker(self) -> None:
        # 首选 OpenRouter：单一权威口径，每个模型一条标准定价
        try:
            resp = requests.get(OPENROUTER_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            resp.raise_for_status()
            exact: dict[str, dict] = {}
            suffix: dict[str, list[tuple[str, dict]]] = {}
            for m in resp.json().get("data", []):
                pricing = m.get("pricing") or {}
                top = m.get("top_provider") or {}
                try:
                    ci = float(pricing.get("prompt") or 0) * 1e6   # OpenRouter 单价为 $/token，换算成 $/1M
                    co = float(pricing.get("completion") or 0) * 1e6
                except (TypeError, ValueError):
                    ci = co = None
                mid = m.get("id") or ""
                if not mid:
                    continue
                info = {"context": m.get("context_length"), "output": top.get("max_completion_tokens"),
                        "cost_in": ci, "cost_out": co}
                exact[mid] = info
                suffix.setdefault(mid.split("/")[-1], []).append((mid, info))
            # 裸 ID（无厂商前缀）匹配时，取 ID 最短的条目（通常是官方/规范收录）
            for name, lst in suffix.items():
                lst.sort(key=lambda t: (len(t[0]), t[0]))
                exact.setdefault(name, lst[0][1])
            self._dev_exact, self._dev_base = exact, None
            self._pricing_source = "OpenRouter"
            self.root.after(0, self._apply_pricing)
            return
        except (requests.exceptions.RequestException, ValueError):
            pass  # OpenRouter 不可达时回退到 models.dev

        try:
            resp = requests.get(MODELS_DEV_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            self.root.after(0, messagebox.showerror, "错误", f"获取价格失败（OpenRouter 和 models.dev 均不可达）:\n{e}")
            self.root.after(0, lambda: self.status_lbl.configure(text="价格获取失败"))
            return
        exact_md: dict[str, list[dict]] = {}
        base: dict[str, list[dict]] = {}
        for prov in data.values():
            for key, m in (prov.get("models") or {}).items():
                limit = m.get("limit") or {}
                cost = m.get("cost") or {}
                info = {
                    "context": limit.get("context"),
                    "output": limit.get("output"),
                    "cost_in": cost.get("input"),
                    "cost_out": cost.get("output"),
                }
                mid = m.get("id") or key
                for k in {mid, key}:
                    exact_md.setdefault(k, []).append(info)
                base.setdefault(mid.split("/")[-1], []).append(info)
        self._dev_exact, self._dev_base = exact_md, base
        self._pricing_source = "models.dev（中位数）"
        self.root.after(0, self._apply_pricing)

    def _lookup_dev_info(self, mid: str) -> dict | None:
        if self._dev_exact is None:
            return None
        if self._pricing_source.startswith("OpenRouter"):
            info = self._dev_exact.get(mid)
            if info is None and "/" in mid:
                info = self._dev_exact.get(mid.split("/")[-1])
            return info
        # models.dev：同名模型被多家服务商收录时，各字段取中位数，避免随机匹配到偏离值
        candidates = self._dev_exact.get(mid) or self._dev_base.get(mid.split("/")[-1], [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        return self._merge_infos(candidates)

    @staticmethod
    def _merge_infos(candidates: list[dict]) -> dict:
        def median(field: str, prefer_paid: bool = False):
            pool = candidates
            if prefer_paid:
                paid = [c for c in candidates if c.get(field) is not None and c.get(field) > 0]
                if paid:
                    pool = paid  # 免费推广价会拉低中位数，优先统计正常收费条目
            vals = sorted(v for c in pool if (v := c.get(field)) is not None)
            if not vals:
                return None
            return vals[len(vals) // 2]

        return {
            "context": median("context"),
            "output": median("output"),
            "cost_in": median("cost_in", prefer_paid=True),
            "cost_out": median("cost_out", prefer_paid=True),
        }

    def _apply_pricing(self) -> None:
        matched = 0
        for m in self.models:
            mid = m.get("id", "")
            info = self._lookup_dev_info(mid)
            if info:
                self.price_info[mid] = info
                matched += 1
        self._render_rows()
        total = len(self.models)
        self.status_lbl.configure(text="价格匹配完成")
        source = self._pricing_source or "OpenRouter"
        self.stats_lbl.configure(text=f"共 {total} 个模型 | 价格已匹配 {matched}/{total}（数据来源 {source}，单位 $/1M tokens）")

    @staticmethod
    def _fmt_price(info: dict) -> str:
        ci, co = info.get("cost_in"), info.get("cost_out")
        if ci is None and co is None:
            return ""
        if not ci and not co:
            return "免费"
        return f"${ci:.2f}/${co:.2f}"

    @staticmethod
    def _fmt_tokens(n) -> str:
        try:
            n = int(n)
        except (TypeError, ValueError):
            return ""
        if n >= 1_000_000:
            s = f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".")
            return f"{s}M"
        if n >= 1000:
            return f"{n / 1000:.0f}K"
        return str(n)

    # ── CSV export ───────────────────────────────────────────────

    def export_csv(self) -> None:
        if not self.models:
            messagebox.showwarning("提示", "请先获取模型列表")
            return
        filename = "aimodelscope_models.csv"
        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["id", "object", "created", "created_date", "owned_by", "status",
                                 "speed_tok_s", "ttft_s", "price_in_usd_per_1m", "price_out_usd_per_1m",
                                 "context_tokens", "output_tokens"])
                for m in self.models:
                    mid = m.get("id", "")
                    ts = m.get("created", 0)
                    d = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
                    speed = self.speeds.get(mid)
                    ttft = self.ttfts.get(mid)
                    info = self.price_info.get(mid) or {}
                    writer.writerow([
                        mid,
                        m.get("object", ""),
                        ts,
                        d,
                        m.get("owned_by", ""),
                        self.statuses.get(mid, ""),
                        f"{speed:.1f}" if isinstance(speed, float) else "",
                        f"{ttft:.2f}" if isinstance(ttft, float) and ttft > 0 else "",
                        info.get("cost_in", "") if info.get("cost_in") is not None else "",
                        info.get("cost_out", "") if info.get("cost_out") is not None else "",
                        info.get("context", "") if info.get("context") is not None else "",
                        info.get("output", "") if info.get("output") is not None else "",
                    ])
            messagebox.showinfo("导出成功", f"已导出 {len(self.models)} 个模型到\n{filename}")
        except OSError as e:
            messagebox.showerror("错误", f"导出失败:\n{e}")


def main() -> None:
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
