import threading
import os
import time
import queue
import logging
from datetime import datetime
from tkinter import *
from tkinter import filedialog, messagebox, ttk

from service import run_process
from matcher import find_missing_items
from settings import load_settings, save_settings, match_rules, SETTINGS_PATH
from config import (
    ORDINE_FOLDER, ELIMINARE_FOLDER, DISTRIBUTUIRE_FOLDER,
    LOG_DIR, LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT
)

APP_TITLE = "采购系统 v2.5"
FONT = "Microsoft YaHei"

settings = load_settings()


# =====================
# 日志系统（UI + 文件双写）
# =====================
def _setup_file_logger():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, LOG_FILE)
    lg = logging.getLogger("procurement")
    lg.setLevel(logging.INFO)
    lg.handlers.clear()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    lg.addHandler(handler)
    return lg


file_logger = _setup_file_logger()

# 后台线程不直接碰界面：把界面操作放进队列，由主线程执行
_ui_queue = queue.Queue()


def ui(fn, *args, **kwargs):
    _ui_queue.put((fn, args, kwargs))


def _poll_ui():
    try:
        while True:
            fn, args, kwargs = _ui_queue.get_nowait()
            try:
                fn(*args, **kwargs)
            except Exception as e:
                file_logger.error(f"界面更新失败: {e}")
    except queue.Empty:
        pass
    root.after(80, _poll_ui)


def _log_tag(msg):
    m = msg.strip()
    if m.startswith(("❌", "[错误]")) or "失败" in m[:12]:
        return "err"
    if m.startswith("⚠") or "⚠" in m[:6]:
        return "warn"
    if m.startswith("✔") or "✔" in m[:40]:
        return "ok"
    if m.startswith(("=", "[起订量", "[完成]")):
        return "head"
    return None


def log(msg):
    """同时写入界面和日志文件（任何线程都可调用）"""
    file_logger.info(msg)

    def write():
        tag = _log_tag(msg)
        text.insert(END, msg + "\n", (tag,) if tag else ())
        text.see(END)
        if msg.strip() and not msg.strip().startswith("="):
            status_var.set(msg.strip()[:90])
    ui(write)


def set_progress(pct, msg):
    def f():
        progress["value"] = pct
        stage_var.set(msg)
        status_var.set(msg)
    ui(f)


# =====================
# 文件夹检查
# =====================
def _excel_files(path):
    if not os.path.isdir(path):
        return None
    return sorted(f for f in os.listdir(path)
                  if f.lower().endswith((".xls", ".xlsx")) and not f.startswith("~$"))


def scan_folder(root_path):
    """返回文件夹检查结果 dict"""
    info = {"ok": False, "errors": [], "warnings": [], "ordine": None,
            "eliminare": [], "suppliers": [], "temp": []}
    if not root_path or not os.path.isdir(root_path):
        info["errors"].append("请选择总文件夹")
        return info
    ordine = _excel_files(os.path.join(root_path, ORDINE_FOLDER))
    elim = _excel_files(os.path.join(root_path, ELIMINARE_FOLDER))
    dist = _excel_files(os.path.join(root_path, DISTRIBUTUIRE_FOLDER))

    if ordine is None:
        info["errors"].append(f"缺少 {ORDINE_FOLDER} 文件夹")
    elif not ordine:
        info["errors"].append(f"{ORDINE_FOLDER} 里没有 Excel 文件")
    else:
        info["ordine"] = ordine[0]
        if len(ordine) > 1:
            info["warnings"].append(f"{ORDINE_FOLDER} 里有 {len(ordine)} 个文件，只处理第一个: {ordine[0]}")

    if elim is None:
        info["errors"].append(f"缺少 {ELIMINARE_FOLDER} 文件夹")
    else:
        info["eliminare"] = elim

    if dist is None:
        info["errors"].append(f"缺少 {DISTRIBUTUIRE_FOLDER} 文件夹")
    else:
        for f in dist:
            low = f.lower()
            if ".calc." in low or low.endswith(".xls.xlsx"):
                info["temp"].append(f)
            elif "缺失报告" in f or "missing" in low:
                continue
            else:
                info["suppliers"].append(f)
        if not info["suppliers"]:
            info["errors"].append(f"{DISTRIBUTUIRE_FOLDER} 里没有供应商报价文件")
        if info["temp"]:
            info["warnings"].append(f"发现 {len(info['temp'])} 个上次留下的临时文件（.calc.xlsx 等），已自动忽略")

    if info["ordine"] and info["ordine"] in (info["eliminare"] or []):
        info["warnings"].append("eliminare 里放了和主订单同名的文件，请确认")
    info["ok"] = not info["errors"]
    return info


def refresh_folder_info(*_):
    path = entry_root.get().strip()
    info = scan_folder(path)
    if not path:
        folder_info_var.set("尚未选择文件夹")
        folder_info_lbl.config(fg="#888")
    elif info["errors"]:
        folder_info_var.set("✖ " + "；".join(info["errors"]))
        folder_info_lbl.config(fg="#C62828")
    else:
        msg = (f"✔ 主订单: {info['ordine']}    "
               f"eliminare: {len(info['eliminare'])} 个文件    "
               f"供应商: {len(info['suppliers'])} 个")
        if info["warnings"]:
            msg += "\n⚠ " + "；".join(info["warnings"])
        folder_info_var.set(msg)
        folder_info_lbl.config(fg="#2E7D32" if not info["warnings"] else "#E65100")
    refresh_rule_summary(info)
    return info


def refresh_rule_summary(info=None):
    if info is None:
        info = scan_folder(entry_root.get().strip())
    if not var_min_order.get():
        rule_summary_var.set("起订量凑单：已关闭（只按最低价选供应商）")
        return
    enabled = [r for r in settings["rules"] if r.get("enabled", True)]
    if not enabled:
        rule_summary_var.set("起订量凑单：没有启用的规则，点「起订量设置」添加")
        return
    matched, _ = match_rules(enabled, info.get("suppliers") or [])
    hit = {id(r): h for r, h in matched}
    parts = []
    for r in enabled:
        cond = describe_condition(r)
        tail = ""
        if info.get("suppliers"):
            tail = " ✔" if id(r) in hit else "（本次未找到文件）"
        parts.append(f"{r.get('name') or r.get('keyword')}: {cond}{tail}")
    rule_summary_var.set("起订规则  " + "   |   ".join(parts))


def describe_condition(r):
    items = []
    if float(r.get("min_colli") or 0):
        items.append(f"{float(r['min_colli']):g} 箱")
    if float(r.get("min_amount") or 0):
        items.append(f"€{float(r['min_amount']):g}")
    if not items:
        return "无条件"
    return (" 且 " if r.get("mode", "all") == "all" else " 或 ").join(items)


def choose_root():
    path = filedialog.askdirectory(title="选择采购系统总文件夹",
                                   initialdir=entry_root.get().strip() or None)
    if path:
        entry_root.delete(0, END)
        entry_root.insert(0, path)
        settings["last_folder"] = path
        save_settings(settings)
        refresh_folder_info()


def open_path(path):
    if path and os.path.exists(path):
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))
    else:
        messagebox.showinfo("提示", f"找不到:\n{path}")


def open_main_order():
    info = scan_folder(entry_root.get().strip())
    if info["ordine"]:
        open_path(os.path.join(entry_root.get().strip(), ORDINE_FOLDER, info["ordine"]))
    else:
        messagebox.showinfo("提示", "请先选择包含 ORDINE 的总文件夹")


# =====================
# 起订量设置窗口
# =====================
class RuleEditor(Toplevel):
    """新增 / 编辑一条起订规则"""

    def __init__(self, parent, rule, supplier_files):
        super().__init__(parent)
        self.title("起订规则")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        pad = {"padx": 8, "pady": 5}

        self.v_enabled = BooleanVar(value=rule.get("enabled", True))
        self.v_name = StringVar(value=rule.get("name", ""))
        self.v_kw = StringVar(value=rule.get("keyword", ""))
        self.v_colli = StringVar(value=f"{float(rule.get('min_colli') or 0):g}")
        self.v_amount = StringVar(value=f"{float(rule.get('min_amount') or 0):g}")
        self.v_mode = StringVar(value=rule.get("mode", "all"))
        self.v_drop = BooleanVar(value=rule.get("allow_drop", True))

        f = Frame(self, padx=12, pady=10)
        f.pack(fill=BOTH)
        r = 0
        Label(f, text="供应商名称:", font=(FONT, 9)).grid(row=r, column=0, sticky=E, **pad)
        Entry(f, textvariable=self.v_name, width=32).grid(row=r, column=1, sticky=W, **pad)
        r += 1
        Label(f, text="文件名关键字:", font=(FONT, 9)).grid(row=r, column=0, sticky=E, **pad)
        cb = ttk.Combobox(f, textvariable=self.v_kw, width=30,
                          values=[os.path.splitext(x)[0] for x in supplier_files])
        cb.grid(row=r, column=1, sticky=W, **pad)
        cb.bind("<<ComboboxSelected>>", self._on_pick)
        r += 1
        Label(f, text="供应商文件名里包含这段文字（不区分大小写）就套用本规则；\n"
                      "可以从下拉里选本次的文件，再删短成稳定的部分，例如 mega",
              font=(FONT, 8), fg="#777", justify=LEFT).grid(row=r, column=1, sticky=W, padx=8)
        r += 1
        Label(f, text="起订箱数 (COLLI):", font=(FONT, 9)).grid(row=r, column=0, sticky=E, **pad)
        Entry(f, textvariable=self.v_colli, width=12).grid(row=r, column=1, sticky=W, **pad)
        r += 1
        Label(f, text="起订金额 (€):", font=(FONT, 9)).grid(row=r, column=0, sticky=E, **pad)
        Entry(f, textvariable=self.v_amount, width=12).grid(row=r, column=1, sticky=W, **pad)
        r += 1
        Label(f, text="填 0 表示不限制；金额 = PREZZO NETTO × 箱数 合计",
              font=(FONT, 8), fg="#777").grid(row=r, column=1, sticky=W, padx=8)
        r += 1
        Label(f, text="条件:", font=(FONT, 9)).grid(row=r, column=0, sticky=NE, **pad)
        mf = Frame(f)
        mf.grid(row=r, column=1, sticky=W, **pad)
        Radiobutton(mf, text="箱数和金额都要达到", variable=self.v_mode, value="all",
                    font=(FONT, 9)).pack(anchor=W)
        Radiobutton(mf, text="达到其中一个即可", variable=self.v_mode, value="any",
                    font=(FONT, 9)).pack(anchor=W)
        r += 1
        Checkbutton(f, text="凑单比「这次不在该供应商下单」更贵时，允许不下单",
                    variable=self.v_drop, font=(FONT, 9)).grid(row=r, column=1, sticky=W, **pad)
        r += 1
        Label(f, text="不勾选：只要最低价分到了这家的货，就一定凑够起订量",
              font=(FONT, 8), fg="#777").grid(row=r, column=1, sticky=W, padx=8)
        r += 1
        Checkbutton(f, text="启用本规则", variable=self.v_enabled,
                    font=(FONT, 9)).grid(row=r, column=1, sticky=W, **pad)
        r += 1
        bf = Frame(f)
        bf.grid(row=r, column=0, columnspan=2, pady=(10, 0))
        Button(bf, text="确定", width=10, command=self._ok, bg="#4CAF50", fg="white").pack(side=LEFT, padx=6)
        Button(bf, text="取消", width=10, command=self.destroy).pack(side=LEFT, padx=6)

        self.grab_set()
        self.wait_window()

    def _on_pick(self, _):
        if not self.v_name.get().strip():
            self.v_name.set(self.v_kw.get())

    def _ok(self):
        kw = self.v_kw.get().strip()
        if not kw:
            messagebox.showerror("错误", "请填写文件名关键字", parent=self)
            return
        try:
            colli = float(self.v_colli.get().replace(",", ".") or 0)
            amount = float(self.v_amount.get().replace(",", ".") or 0)
        except ValueError:
            messagebox.showerror("错误", "起订箱数和金额必须是数字", parent=self)
            return
        if colli < 0 or amount < 0:
            messagebox.showerror("错误", "起订箱数和金额不能是负数", parent=self)
            return
        if colli == 0 and amount == 0:
            if not messagebox.askyesno("确认", "箱数和金额都是 0，这条规则不会起作用。仍然保存？", parent=self):
                return
        self.result = {"enabled": self.v_enabled.get(), "name": self.v_name.get().strip() or kw,
                       "keyword": kw, "min_colli": colli, "min_amount": amount,
                       "mode": self.v_mode.get(), "allow_drop": self.v_drop.get()}
        self.destroy()


def open_settings():
    import copy
    info = scan_folder(entry_root.get().strip())
    suppliers = info.get("suppliers") or []
    rules = copy.deepcopy(settings["rules"])

    win = Toplevel(root)
    win.title("起订量设置")
    win.geometry("900x480")
    win.transient(root)
    win.grab_set()

    v_on = BooleanVar(value=var_min_order.get())
    top = Frame(win, padx=12, pady=8)
    top.pack(fill=X)
    Checkbutton(top, text="启用起订量凑单（关闭后只按最低价选供应商）", variable=v_on,
                font=(FONT, 10, "bold")).pack(anchor=W)
    Label(top, text="某个供应商按最低价分到的货不够起订量时，程序会自动比较「从别家改一些商品过来凑单」"
                    "和「这次不在它那里下单」，选总采购成本最低的方案。",
          font=(FONT, 9), fg="#555", wraplength=860, justify=LEFT).pack(anchor=W, pady=(4, 0))

    cols = ("on", "name", "kw", "colli", "amount", "mode", "drop", "file")
    tree_r = ttk.Treeview(win, columns=cols, show="headings", height=10)
    for c, t, w in [("on", "启用", 45), ("name", "供应商", 120), ("kw", "文件名关键字", 120),
                    ("colli", "起订箱数", 70), ("amount", "起订金额€", 80), ("mode", "条件", 110),
                    ("drop", "太贵时可不下单", 100), ("file", "本次匹配到的文件", 230)]:
        tree_r.heading(c, text=t)
        tree_r.column(c, width=w, anchor=W if c in ("name", "kw", "file", "mode") else CENTER)
    tree_r.pack(fill=BOTH, expand=True, padx=12)

    def refresh():
        tree_r.delete(*tree_r.get_children())
        matched, _ = match_rules([r for r in rules if r.get("enabled", True)], suppliers)
        hit = {id(r): h for r, h in matched}
        for i, r in enumerate(rules):
            if not r.get("enabled", True):
                f = "（已停用）"
            elif not suppliers:
                f = "（未选择文件夹）"
            else:
                f = hit.get(id(r), "✖ 未找到，检查关键字")
            tree_r.insert("", END, iid=str(i), values=(
                "✔" if r.get("enabled", True) else "—", r.get("name", ""), r.get("keyword", ""),
                f"{float(r.get('min_colli') or 0):g}", f"{float(r.get('min_amount') or 0):g}",
                "都要达到" if r.get("mode", "all") == "all" else "达到一个即可",
                "是" if r.get("allow_drop", True) else "否", f))

    def selected_index():
        sel = tree_r.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在表格里选一条规则", parent=win)
            return None
        return int(sel[0])

    def add():
        ed = RuleEditor(win, {"name": "", "keyword": "", "min_colli": 0, "min_amount": 0,
                              "mode": "all", "allow_drop": True, "enabled": True}, suppliers)
        win.grab_set()
        if ed.result:
            rules.append(ed.result)
            refresh()

    def edit(_=None):
        i = selected_index()
        if i is None:
            return
        ed = RuleEditor(win, rules[i], suppliers)
        win.grab_set()
        if ed.result:
            rules[i] = ed.result
            refresh()

    def delete():
        i = selected_index()
        if i is None:
            return
        if messagebox.askyesno("确认", f"删除规则「{rules[i].get('name')}」？", parent=win):
            rules.pop(i)
            refresh()

    def toggle():
        i = selected_index()
        if i is None:
            return
        rules[i]["enabled"] = not rules[i].get("enabled", True)
        refresh()

    def save():
        settings["rules"] = rules
        settings["min_order_enabled"] = v_on.get()
        var_min_order.set(v_on.get())
        if save_settings(settings):
            log(f"✔ 起订量设置已保存（{len(rules)} 条规则）")
        else:
            messagebox.showwarning("提示", f"设置无法写入文件，本次运行有效，关闭程序后会丢失:\n{SETTINGS_PATH}", parent=win)
        refresh_rule_summary()
        win.destroy()

    tree_r.bind("<Double-1>", edit)
    bf = Frame(win, padx=12, pady=10)
    bf.pack(fill=X)
    Button(bf, text="＋ 新增供应商规则", command=add, padx=8).pack(side=LEFT, padx=3)
    Button(bf, text="✎ 编辑", command=edit, padx=8).pack(side=LEFT, padx=3)
    Button(bf, text="启用/停用", command=toggle, padx=8).pack(side=LEFT, padx=3)
    Button(bf, text="✖ 删除", command=delete, padx=8).pack(side=LEFT, padx=3)
    Button(bf, text="取消", command=win.destroy, width=10).pack(side=RIGHT, padx=3)
    Button(bf, text="保存", command=save, width=10, bg="#4CAF50", fg="white",
           font=(FONT, 10, "bold")).pack(side=RIGHT, padx=3)
    Label(win, text=f"设置保存在: {SETTINGS_PATH}", font=(FONT, 8), fg="#999").pack(anchor=W, padx=12, pady=(0, 6))
    refresh()


# =====================
# 主处理
# =====================
_run_start = [0.0]
_running = [False]


def _tick_elapsed():
    if _running[0]:
        elapsed_var.set(f"已用时 {time.time() - _run_start[0]:.0f} 秒")
        root.after(1000, _tick_elapsed)


def start():
    root_path = entry_root.get().strip()
    info = refresh_folder_info()
    if not info["ok"]:
        messagebox.showerror("文件夹检查未通过", "\n".join(info["errors"]))
        return

    settings["last_folder"] = root_path
    settings["auto_backup"] = var_backup.get()
    settings["min_order_enabled"] = var_min_order.get()
    save_settings(settings)

    enabled_rules = [r for r in settings["rules"] if r.get("enabled", True)] if var_min_order.get() else []
    lines = [
        f"主订单: {info['ordine']}",
        f"扣减文件: {len(info['eliminare'])} 个    供应商: {len(info['suppliers'])} 个",
        f"起订量凑单: {'开启（' + str(len(enabled_rules)) + ' 条规则）' if var_min_order.get() else '关闭'}",
        "",
        "本次处理会：",
        "  • 按 eliminare 扣减主订单数量，并覆盖保存主订单",
        "  • 把订购数量写进各供应商文件的 ORDINE 列",
        "",
        "处理前自动备份: " + ("开启（备份到 总文件夹\\_备份）" if var_backup.get() else "关闭 ⚠ 建议先手动备份"),
        "",
        "注意：同一批文件重复处理会再扣减一次。确定开始？",
    ]
    if not messagebox.askyesno("确认开始处理", "\n".join(lines)):
        return

    btn_start.config(state=DISABLED, text="处理中...")
    btn_settings.config(state=DISABLED)
    progress["value"] = 0
    _run_start[0] = time.time()
    _running[0] = True
    _tick_elapsed()
    text.delete("1.0", END)

    ordine_file = os.path.join(root_path, ORDINE_FOLDER, info["ordine"])
    eliminare_path = os.path.join(root_path, ELIMINARE_FOLDER)
    distribuire_path = os.path.join(root_path, DISTRIBUTUIRE_FOLDER)
    run_settings = dict(settings)

    def work():
        summary = None
        try:
            log(f"{'=' * 50}")
            log(f"总文件夹: {root_path}")
            log(f"主文件: {ordine_file}")
            log(f"{'=' * 50}\n")
            summary = run_process(ordine_file, eliminare_path, distribuire_path, log,
                                  progress=set_progress, settings=run_settings)
            if summary and summary.get("ok"):
                log("\n✔ 已自动保存（覆盖原文件）")
        except Exception as e:
            file_logger.error(f"处理失败: {e}", exc_info=True)
            log(f"❌ 处理失败: {e}")
            summary = {"ok": False, "error": str(e)}
        ui(_finish, summary)

    threading.Thread(target=work, daemon=True).start()


def _finish(summary):
    _running[0] = False
    btn_start.config(state=NORMAL, text="🚀 开始处理")
    btn_settings.config(state=NORMAL)
    elapsed = time.time() - _run_start[0]
    elapsed_var.set(f"用时 {elapsed:.0f} 秒")
    if not summary or not summary.get("ok"):
        progress["value"] = 0
        stage_var.set("处理失败")
        err = (summary or {}).get("error") or "未知错误"
        messagebox.showerror("处理失败", f"{err}\n\n详细信息见「处理日志」和日志文件。")
        return
    progress["value"] = 100
    stage_var.set("✔ 处理完成")
    show_summary(summary)
    notebook.select(tab_summary)

    mo = summary.get("min_order", {})
    msg = [f"主订单: {summary.get('main_file')}",
           f"商品行: {summary.get('total_rows')}    有报价: {summary.get('priced_rows')}    缺失: {summary.get('missing')}"]
    if mo.get("enabled"):
        if mo.get("changes"):
            msg.append(f"起订量调整: 改动 {mo['changes']} 行，成本增加 €{mo['extra_cost']:.2f}")
        else:
            msg.append("起订量: 无需调整")
        for r in mo.get("rules", []):
            msg.append(f"  {r.get('name')}: {r['after_colli']:.0f} 箱 / €{r['after_amount']:.2f}  {r['status']}")
        if mo.get("warning"):
            msg.append("⚠ " + mo["warning"])
    if summary.get("missing"):
        msg.append("\n有缺失商品，可到「缺失查找」页查找替代品")
    msg.append("\n要现在打开主订单吗？")
    if messagebox.askyesno("处理完成", "\n".join(msg)):
        open_main_order()


def show_summary(s):
    for item in sum_tree.get_children():
        sum_tree.delete(item)
    mo = s.get("min_order", {})
    rule_by_target = {r["target"]: r for r in mo.get("rules", [])}
    tot_rows = tot_colli = tot_amount = 0
    for sup in s.get("suppliers", []):
        r = rule_by_target.get(sup["name"])
        cond = describe_condition(r) if r else ""
        status = r["status"] if r else ""
        tag = ()
        if r:
            tag = ("bad",) if status == "未达标" else ("adj",) if "凑单" in status or "不在" in status else ("good",)
        sum_tree.insert("", END, values=(
            sup["name"], sup["prices"], sup["hits"], sup["rows"], f"{sup['colli']:g}",
            f"{sup['amount']:,.2f}", sup.get("written", 0), cond, status), tags=tag)
        tot_rows += sup["rows"]
        tot_colli += sup["colli"]
        tot_amount += sup["amount"]
    sum_tree.insert("", END, values=("合计", "", "", tot_rows, f"{tot_colli:g}", f"{tot_amount:,.2f}",
                                     "", "", ""), tags=("total",))

    d, u = s.get("eliminare", (0, 0))
    lines = [
        f"主订单: {s.get('main_file')}     商品行: {s.get('total_rows')}     有报价: {s.get('priced_rows')}"
        f"     缺失: {s.get('missing')}     eliminare: 删除 {d} 行 / 更新 {u} 行     用时: {s.get('elapsed', 0):.0f} 秒",
    ]
    if mo.get("enabled"):
        if mo.get("changes"):
            lines.append(f"起订量调整: 改动 {mo['changes']} 行，采购成本增加 €{mo['extra_cost']:.2f}"
                         f"（改动明细在主订单 AC 列）")
        else:
            lines.append("起订量: 所有起订供应商都无需调整")
        if mo.get("unmatched"):
            lines.append("⚠ 本次没找到文件的规则: " + "、".join(mo["unmatched"]))
        if mo.get("warning"):
            lines.append("⚠ " + mo["warning"])
    else:
        lines.append("起订量凑单: 未启用")
    if s.get("backup_dir"):
        lines.append(f"备份: {s['backup_dir']}")
    summary_var.set("\n".join(lines))
    last_summary[0] = s


last_summary = [None]


# =====================
# 缺失查找
# =====================
def run_web_search():
    from matcher import search_web_ean

    selected = tree.selection()
    if not selected:
        messagebox.showinfo("提示", "请先在下面表格里选一行")
        return
    values = tree.item(selected[0])["values"]
    ean, articolo = values[0], values[1]
    if not ean:
        messagebox.showinfo("提示", "这一行没有 EAN 码")
        return
    btn_web.config(state=DISABLED, text="搜索中...")

    def search_thread():
        try:
            log(f"\n{'=' * 60}\n网络搜索 EAN: {ean}  产品: {articolo}")
            result, source, search_time = search_web_ean(str(ean), str(articolo), log)
            if result:
                log(f"  ✔ 产品: {result}\n  来源: {source}")
                ui(messagebox.showinfo, "搜索结果", f"产品信息:\n{result}\n\n来源: {source}\n时间: {search_time}")
            else:
                log(f"  未找到: {source}")
                ui(messagebox.showinfo, "搜索结果", f"未找到 EAN {ean} 的相关信息")
        except Exception as e:
            log(f"❌ 搜索失败: {e}")
            ui(messagebox.showerror, "错误", f"搜索失败: {e}")
        finally:
            ui(lambda: btn_web.config(state=NORMAL, text="🌐 搜索选中行"))

    threading.Thread(target=search_thread, daemon=True).start()


def run_missing_search():
    root_path = entry_root.get().strip()
    if not root_path or not os.path.isdir(root_path):
        messagebox.showerror("错误", "请先选择总文件夹")
        return
    distribuire_path = os.path.join(root_path, DISTRIBUTUIRE_FOLDER)
    if not os.path.isdir(distribuire_path):
        messagebox.showerror("错误", f"缺少 {DISTRIBUTUIRE_FOLDER} 文件夹")
        return
    if not os.path.exists(os.path.join(distribuire_path, "缺失报告.xlsx")):
        messagebox.showinfo("提示", "还没有缺失报告。请先点「开始处理」，处理完成后再做缺失查找。")
        return
    try:
        threshold = float(entry_threshold.get().replace(",", "."))
        if not 0 < threshold <= 1:
            raise ValueError
    except ValueError:
        messagebox.showerror("错误", "匹配阈值要填 0 到 1 之间的小数，例如 0.6")
        return
    use_web = var_use_web.get()
    settings["match_threshold"] = threshold
    settings["use_web"] = use_web
    save_settings(settings)

    btn_search.config(state=DISABLED, text="查找中...")
    miss_status_var.set("查找中…（开启网络搜索时会比较慢）")

    def search_thread():
        try:
            log(f"\n{'=' * 50}\n开始缺失查找（阈值 {threshold}，网络搜索 {'开' if use_web else '关'}）\n{'=' * 50}")
            results, error = find_missing_items(distribuire_path, threshold, use_web, log)
            if error:
                log(f"❌ {error}")
                ui(miss_status_var.set, error)
                return
            if not results:
                log("未找到缺失项目")
                ui(miss_status_var.set, "没有缺失项目")
                return
            ui(_fill_missing_tree, results, use_web)
        except Exception as e:
            log(f"❌ 查找失败: {e}")
            ui(messagebox.showerror, "错误", f"查找失败: {e}")
        finally:
            ui(lambda: btn_search.config(state=NORMAL, text="🔍 开始缺失查找"))

    threading.Thread(target=search_thread, daemon=True).start()


def _fill_missing_tree(results, use_web):
    tree.delete(*tree.get_children())
    found = web_found = 0
    for ean, articolo, count, matches, web_source, web_art, search_time in results:
        art_short = articolo[:50] + "..." if len(articolo) > 50 else articolo
        if not matches:
            tree.insert("", END, values=(ean, art_short, count or "", "未找到", "",
                                         web_art[:40] if web_art else "无", search_time or "", ""),
                        tags=("none",))
        else:
            best_total, best_ean, best_art, best_supplier, best_art_sim, best_web_art = matches[0]
            found += 1
            web_info = best_web_art[:40] if best_web_art else (web_art[:40] if web_art else "无")
            source_info = f"\n来源: {web_source[:40]}" if web_source and 'http' in str(web_source) else ""
            tree.insert("", END, values=(
                ean, art_short, count or "", f"{best_total:.0%}", f"{best_art_sim:.0%}", web_info,
                search_time or "", f"EAN: {best_ean}\nART: {best_art[:30]}\n供应商: {best_supplier}{source_info}"))
            if web_source:
                web_found += 1
    msg = f"共 {len(results)} 个缺失，本地找到可能的替代品 {found} 个"
    if use_web and web_found:
        msg += f"，网络找到 {web_found} 个"
    miss_status_var.set(msg + "。选中一行可「网络搜索」，结果可「导出」")
    log(f"✔ 缺失查找完成: {msg}")
    notebook.select(tab_missing)


def clean_ean_for_export(ean):
    if not ean:
        return ""
    ean_str = str(ean).replace('.', '').replace(',', '')
    try:
        return str(int(ean_str))
    except ValueError:
        return ean_str


def export_search_results():
    if not tree.get_children():
        messagebox.showinfo("提示", "缺失查找结果是空的，先做一次缺失查找")
        return
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "缺失查找结果"
        ws.append(["缺失 EAN", "缺失 ARTICOLO", "数量", "综合匹配", "ART匹配", "网络搜索", "搜索时间",
                   "匹配的 EAN", "匹配的 ARTICOLO", "供应商"])
        for item in tree.get_children():
            ean, articolo, count, total_sim, art_sim, web_art, search_time, detail = tree.item(item)["values"]
            clean = clean_ean_for_export(ean)
            if total_sim == "未找到":
                ws.append([clean, articolo, count, "", "", web_art, search_time, "", "", ""])
            else:
                parts = str(detail).split('\n')
                found_ean = clean_ean_for_export(parts[0].replace("EAN: ", "")) if parts else ""
                found_art = parts[1].replace("ART: ", "") if len(parts) > 1 else ""
                supplier = parts[2].replace("供应商: ", "") if len(parts) > 2 else ""
                ws.append([clean, articolo, count, total_sim, art_sim, web_art, search_time,
                           found_ean, found_art, supplier])
        for col in ws.columns:
            width = max((len(str(c.value)) for c in col if c.value), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(width + 2, 50)
        file_path = filedialog.asksaveasfilename(
            defaultextension=".xlsx", filetypes=[("Excel 文件", "*.xlsx")],
            initialfile=f"缺失查找结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        if file_path:
            wb.save(file_path)
            log(f"✔ 结果已导出到: {file_path}")
            if messagebox.askyesno("导出成功", f"已导出到:\n{file_path}\n\n现在打开？"):
                open_path(file_path)
        wb.close()
    except Exception as e:
        messagebox.showerror("导出失败", str(e))


# =====================
# 界面
# =====================
root = Tk()
root.title(APP_TITLE)
root.geometry("1120x780")
root.minsize(950, 620)

style = ttk.Style()
try:
    style.theme_use("vista")
except TclError:
    pass
style.configure("Treeview", rowheight=24, font=(FONT, 9))
style.configure("Treeview.Heading", font=(FONT, 9, "bold"))
style.configure("TNotebook.Tab", font=(FONT, 10), padding=(14, 5))

# ---- ① 文件夹 ----
sec1 = LabelFrame(root, text=" ① 选择总文件夹（里面要有 ORDINE / eliminare / distribuire） ",
                  font=(FONT, 10, "bold"), padx=10, pady=6)
sec1.pack(fill=X, padx=12, pady=(10, 4))

row1 = Frame(sec1)
row1.pack(fill=X)
entry_root = Entry(row1, font=("Consolas", 10))
entry_root.pack(side=LEFT, fill=X, expand=True, ipady=3)
entry_root.bind("<Return>", refresh_folder_info)
entry_root.bind("<FocusOut>", refresh_folder_info)
Button(row1, text="📁 选择文件夹", command=choose_root, padx=10).pack(side=LEFT, padx=(6, 0))
Button(row1, text="打开文件夹", command=lambda: open_path(entry_root.get().strip()), padx=8).pack(side=LEFT, padx=(6, 0))

folder_info_var = StringVar(value="尚未选择文件夹")
folder_info_lbl = Label(sec1, textvariable=folder_info_var, font=(FONT, 9), fg="#888", anchor=W, justify=LEFT)
folder_info_lbl.pack(fill=X, pady=(5, 0))

# ---- ② 选项 ----
sec2 = LabelFrame(root, text=" ② 处理选项 ", font=(FONT, 10, "bold"), padx=10, pady=6)
sec2.pack(fill=X, padx=12, pady=4)

row2 = Frame(sec2)
row2.pack(fill=X)
var_backup = BooleanVar(value=settings.get("auto_backup", True))
Checkbutton(row2, text="处理前自动备份原文件", variable=var_backup, font=(FONT, 9)).pack(side=LEFT)
var_min_order = BooleanVar(value=settings.get("min_order_enabled", True))
Checkbutton(row2, text="启用起订量凑单", variable=var_min_order, font=(FONT, 9),
            command=lambda: refresh_rule_summary()).pack(side=LEFT, padx=(16, 0))
btn_settings = Button(row2, text="⚙ 起订量设置…", command=open_settings, padx=10)
btn_settings.pack(side=LEFT, padx=(10, 0))

rule_summary_var = StringVar()
Label(sec2, textvariable=rule_summary_var, font=(FONT, 9), fg="#1565C0", anchor=W,
      justify=LEFT, wraplength=1060).pack(fill=X, pady=(5, 0))

# ---- ③ 执行 ----
sec3 = Frame(root, padx=12)
sec3.pack(fill=X, pady=(6, 2))
btn_start = Button(sec3, text="🚀 开始处理", command=start, bg="#4CAF50", fg="white",
                   font=(FONT, 11, "bold"), padx=22, pady=6)
btn_start.pack(side=LEFT)
Button(sec3, text="📄 打开主订单", command=open_main_order, padx=8, pady=4).pack(side=LEFT, padx=(10, 0))
Button(sec3, text="🗒 打开日志文件", command=lambda: open_path(os.path.abspath(os.path.join(LOG_DIR, LOG_FILE))),
       padx=8, pady=4).pack(side=LEFT, padx=(6, 0))

pf = Frame(sec3)
pf.pack(side=LEFT, fill=X, expand=True, padx=(16, 0))
stage_var = StringVar(value="等待开始")
elapsed_var = StringVar(value="")
top_pf = Frame(pf)
top_pf.pack(fill=X)
Label(top_pf, textvariable=stage_var, font=(FONT, 9), anchor=W).pack(side=LEFT)
Label(top_pf, textvariable=elapsed_var, font=(FONT, 9), fg="#777").pack(side=RIGHT)
progress = ttk.Progressbar(pf, mode="determinate", maximum=100)
progress.pack(fill=X, pady=(2, 0))

# ---- 结果页签 ----
notebook = ttk.Notebook(root)
notebook.pack(fill=BOTH, expand=True, padx=12, pady=(6, 4))

# 页签 1：日志
tab_log = Frame(notebook)
notebook.add(tab_log, text="📋 处理日志")
log_bar = Frame(tab_log)
log_bar.pack(fill=X, pady=(4, 2))
Button(log_bar, text="清空", command=lambda: text.delete("1.0", END), padx=8).pack(side=RIGHT)
Label(log_bar, text="绿色 = 成功，橙色 = 提醒，红色 = 错误", font=(FONT, 8), fg="#888").pack(side=LEFT)
log_box = Frame(tab_log)
log_box.pack(fill=BOTH, expand=True)
text = Text(log_box, font=("Consolas", 9), wrap=WORD)
log_scroll = ttk.Scrollbar(log_box, command=text.yview)
text.config(yscrollcommand=log_scroll.set)
log_scroll.pack(side=RIGHT, fill=Y)
text.pack(side=LEFT, fill=BOTH, expand=True)
text.tag_config("ok", foreground="#2E7D32")
text.tag_config("warn", foreground="#E65100")
text.tag_config("err", foreground="#C62828")
text.tag_config("head", foreground="#1565C0")

# 页签 2：分配汇总
tab_summary = Frame(notebook)
notebook.add(tab_summary, text="📊 供应商分配汇总")
summary_var = StringVar(value="处理完成后，这里会显示每个供应商分到多少货、起订量是否达标。")
Label(tab_summary, textvariable=summary_var, font=(FONT, 9), anchor=W, justify=LEFT,
      wraplength=1060).pack(fill=X, padx=4, pady=6)
sum_cols = ("name", "prices", "hits", "rows", "colli", "amount", "written", "cond", "status")
sum_box = Frame(tab_summary)
sum_box.pack(fill=BOTH, expand=True)
sum_tree = ttk.Treeview(sum_box, columns=sum_cols, show="headings")
for c, t, w in [("name", "供应商文件", 240), ("prices", "报价条数", 70), ("hits", "主表命中", 70),
                ("rows", "分到商品行", 80), ("colli", "箱数", 70), ("amount", "金额 €", 100),
                ("written", "写入行数", 70), ("cond", "起订条件", 120), ("status", "起订状态", 170)]:
    sum_tree.heading(c, text=t)
    sum_tree.column(c, width=w, anchor=W if c in ("name", "cond", "status") else E)
sum_scroll = ttk.Scrollbar(sum_box, command=sum_tree.yview)
sum_tree.config(yscrollcommand=sum_scroll.set)
sum_scroll.pack(side=RIGHT, fill=Y)
sum_tree.pack(side=LEFT, fill=BOTH, expand=True)
sum_tree.tag_configure("good", foreground="#2E7D32")
sum_tree.tag_configure("adj", foreground="#1565C0")
sum_tree.tag_configure("bad", foreground="#C62828")
sum_tree.tag_configure("total", font=(FONT, 9, "bold"))

# 页签 3：缺失查找
tab_missing = Frame(notebook)
notebook.add(tab_missing, text="🔍 缺失查找")
mbar = Frame(tab_missing)
mbar.pack(fill=X, pady=6)
Label(mbar, text="匹配阈值:", font=(FONT, 9)).pack(side=LEFT)
entry_threshold = Entry(mbar, width=6, font=("Consolas", 9), justify="center")
entry_threshold.insert(0, str(settings.get("match_threshold", 0.6)))
entry_threshold.pack(side=LEFT, padx=4)
Label(mbar, text="(0~1，越大越严格)", font=(FONT, 8), fg="#888").pack(side=LEFT)
var_use_web = BooleanVar(value=settings.get("use_web", False))
Checkbutton(mbar, text="同时网络搜索（较慢）", variable=var_use_web, font=(FONT, 9)).pack(side=LEFT, padx=10)
btn_search = Button(mbar, text="🔍 开始缺失查找", command=run_missing_search, bg="#2196F3", fg="white",
                    font=(FONT, 9, "bold"), padx=10)
btn_search.pack(side=LEFT, padx=4)
btn_web = Button(mbar, text="🌐 搜索选中行", command=run_web_search, padx=8)
btn_web.pack(side=LEFT, padx=4)
Button(mbar, text="💾 导出结果", command=export_search_results, bg="#FF5722", fg="white",
       font=(FONT, 9, "bold"), padx=10).pack(side=RIGHT)
miss_status_var = StringVar(value="先「开始处理」生成缺失报告，再点「开始缺失查找」，在供应商文件里找相似商品。")
Label(tab_missing, textvariable=miss_status_var, font=(FONT, 9), fg="#555", anchor=W).pack(fill=X, padx=2)

columns = ("EAN", "ARTICOLO", "数量", "综合", "ART", "网络条码", "时间", "匹配详情")
miss_box = Frame(tab_missing)
miss_box.pack(fill=BOTH, expand=True, pady=4)
tree = ttk.Treeview(miss_box, columns=columns, show="headings")
for c, t, w in [("EAN", "EAN", 120), ("ARTICOLO", "ARTICOLO", 200), ("数量", "数量", 45),
                ("综合", "综合匹配", 70), ("ART", "ART匹配", 70), ("网络条码", "网络搜索结果", 170),
                ("时间", "搜索时间", 70), ("匹配详情", "匹配详情", 260)]:
    tree.heading(c, text=t)
    tree.column(c, width=w)
miss_scroll = ttk.Scrollbar(miss_box, command=tree.yview)
tree.config(yscrollcommand=miss_scroll.set)
miss_scroll.pack(side=RIGHT, fill=Y)
tree.pack(side=LEFT, fill=BOTH, expand=True)
tree.tag_configure("none", foreground="#999")

# ---- 状态栏 ----
status_var = StringVar(value="就绪")
Label(root, textvariable=status_var, font=(FONT, 9), fg="#555", anchor=W, padx=12, pady=4,
      relief=SUNKEN, bd=1).pack(fill=X, side=BOTTOM)

# 启动
if settings.get("last_folder") and os.path.isdir(settings["last_folder"]):
    entry_root.insert(0, settings["last_folder"])
refresh_folder_info()
root.after(80, _poll_ui)
log(f"{APP_TITLE} 启动 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log("使用步骤: ① 选择总文件夹 → ② 确认选项和起订量设置 → 点「开始处理」")

root.mainloop()
