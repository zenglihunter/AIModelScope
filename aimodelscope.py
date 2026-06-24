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

FONT_FAMILY = "Microsoft YaHei"
FONT_UI = lambda size=13, bold=False: ctk.CTkFont(family=FONT_FAMILY, size=size, weight="bold" if bold else "normal")
FONT_TREE = (FONT_FAMILY, 11)

DEV_INFO = "需求 by Tiger | 开发 by DeepSeek V4 Pro | 更新: 2026-06-24"

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
        root.geometry("960x720")
        root.resizable(True, True)
        root.minsize(780, 520)

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.configs: list[dict[str, str]] = load_configs()
        self.current_key: str = self.configs[0]["key"] if self.configs else ""
        self.models: list[dict] = []
        self.statuses: dict[str, str] = {}
        self.speeds: dict[str, float] = {}
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
        self.progress = ctk.CTkProgressBar(toolbar, width=180)
        self.progress.pack(side=tk.LEFT, padx=12)
        self.progress.set(0)
        self.status_lbl = ctk.CTkLabel(toolbar, text="", font=FONT_UI())
        self.status_lbl.pack(side=tk.LEFT)

        # ── tree + scrollbar ──
        tree_frame = ctk.CTkFrame(self.root)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        self._sort_col: str = ""
        self._sort_asc: bool = True
        columns = ("id", "created_date", "owned_by", "status", "speed")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("id", text="模型 ID", command=lambda: self._sort_by("id"))
        self.tree.heading("created_date", text="创建日期", command=lambda: self._sort_by("created_date"))
        self.tree.heading("owned_by", text="来源", command=lambda: self._sort_by("owned_by"))
        self.tree.heading("status", text="状态", command=lambda: self._sort_by("status"))
        self.tree.heading("speed", text="速度", command=lambda: self._sort_by("speed"))
        self.tree.column("id", width=300)
        self.tree.column("created_date", width=140, anchor=tk.CENTER)
        self.tree.column("owned_by", width=90, anchor=tk.CENTER)
        self.tree.column("status", width=90, anchor=tk.CENTER)
        self.tree.column("speed", width=110, anchor=tk.CENTER)

        v_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=v_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

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
        self.status_lbl.configure(text="获取中...")
        self.root.update_idletasks()
        try:
            resp = requests.get(f"{self._api_url()}/models", headers=self._headers(), timeout=30)
            resp.raise_for_status()
            self.models = resp.json().get("data", [])
            for m in self.models:
                mid = m.get("id", "")
                ts = m.get("created", 0)
                d = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
                self.tree.insert("", tk.END, iid=mid, values=(mid, d, m.get("owned_by", ""), "", ""))
            self.status_lbl.configure(text=f"共 {len(self.models)} 个模型")
            self.stats_lbl.configure(text=f"共 {len(self.models)} 个模型 | 可用性未测试")
        except requests.exceptions.RequestException as e:
            messagebox.showerror("错误", f"获取模型失败:\n{e}")
            self.status_lbl.configure(text="失败")

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
            self.root.after(0, lambda m=mid: self.tree.set(m, "speed", "测速中..."))
            self.root.after(0, self.status_lbl.configure, f"测速中... {i + 1}/{total}")
            tps, elapsed = self._speed_test_one(mid)
            with self._speed_lock:
                self.speeds[mid] = tps
            label = f"{tps:.1f} tok/s"
            if tps >= 50:
                tag = "speed_fast"
            elif tps >= 20:
                tag = "speed_medium"
            else:
                tag = "speed_slow"
            if tps == 0:
                label = "失败"
                tag = "speed_slow"
            self.root.after(0, lambda m=mid, l=label, t=tag: self._set_speed(m, l, t))
        self.root.after(0, self.progress.set, 0)
        self.root.after(0, self.status_lbl.configure, "测速完成")

    def _speed_test_one(self, model_id: str) -> tuple[float, float]:
        api_url = self._api_url()
        headers = self._headers()
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
                body = resp.json()
                completion_tokens = body.get("usage", {}).get("completion_tokens", 0)
                speed = completion_tokens / elapsed if elapsed > 0 else 0
                return speed, elapsed
            return 0, elapsed
        except requests.exceptions.RequestException:
            return 0, time.time() - start

    def _set_speed(self, model_id: str, label: str, tag: str) -> None:
        if self.tree.exists(model_id):
            self.tree.set(model_id, "speed", label)
            self.tree.item(model_id, tags=(tag,))

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

        for c in ("id", "created_date", "owned_by", "status", "speed"):
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

    # ── CSV export ───────────────────────────────────────────────

    def export_csv(self) -> None:
        if not self.models:
            messagebox.showwarning("提示", "请先获取模型列表")
            return
        filename = "aimodelscope_models.csv"
        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["id", "object", "created", "created_date", "owned_by", "status", "speed_tok_s"])
                for m in self.models:
                    mid = m.get("id", "")
                    ts = m.get("created", 0)
                    d = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
                    speed = self.speeds.get(mid)
                    writer.writerow([
                        mid,
                        m.get("object", ""),
                        ts,
                        d,
                        m.get("owned_by", ""),
                        self.statuses.get(mid, ""),
                        f"{speed:.1f}" if isinstance(speed, float) else "",
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
