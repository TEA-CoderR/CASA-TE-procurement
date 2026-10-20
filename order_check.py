"""
「订单处理」页上的第一步：检查门店订货清单

门店发来的订货清单（通常就是 ORDINE 里的主订单）→ 按这家门店的销量、数据库库存和订货周期算建议量
→ 逐条对比清单数量：增加 / 减少 / 不订 / 不变 → 可以手动改「确定件数」
→ 确认后生成新的主订单（PEZZI = 确定件数），原文件挪进 ORDINE\\_旧订单，再点「开始处理」做供应商比价分配。
"""
import os
import math
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QLineEdit, QComboBox, QHBoxLayout, QVBoxLayout,
                               QFileDialog, QMessageBox, QSpinBox, QAbstractItemView, QHeaderView)

from i18n import tr
from settings import save_settings
import reorder_service as R
from reorder_page import short_decision, store_label, algo_widgets

# 表格列（逻辑顺序）→ 显示顺序：要核对、要改的列放前面
# 判断 等级 名称 | 清单 → 建议 → 确定 | 订后够卖 | 本店库存 库存够卖 日均 目标库存 | 各周期销量 | 周期 箱规 编号 条码
_ORDER = [0, 16, 3, 11, 12, 13, 14, 15, 20, 4, 9, 18, 19, 5, 6, 7, 8, 17, 10, 1, 2]


def _col(k):
    return _ORDER.index(k)


def _perm(vals):
    return [vals[k] for k in _ORDER]


EDIT_COL = _col(14)
BOX_COL = _col(15)
AFTER_COL = _col(20)


def _cover(x, pieces=0):
    """
    够卖几天（和门店配货同一个算法 store_orders.cover_days）：(库存 + pieces) ÷ 日均。
    账面库存是负数时按 0 算（负库存不代表欠了多少天的货）；数据库里没有的商品返回 None
    """
    if not x.get("in_db") or x.get("daily0") is None:
        return None
    import store_orders as SO
    return SO.cover_days({"inv": x.get("inv0") or 0, "daily": x["daily0"]}, pieces)


def _days(v):
    import store_orders as SO
    return SO.days_text(v)
VERDICT_COLOR = {"增加": "accent", "减少": "warn", "不订": "err", "不变": "muted", "数据库没有": "err"}


class _Sig(QObject):
    done = Signal(object)


class OrderCheck:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cfg = settings.setdefault("reorder", {})
        self.rows = []
        self.file_info = None
        self.file_path = ""
        self.running = False
        self._filling = False
        self.sig = _Sig()
        self.sig.done.connect(self._done)

    def build(self):
        ui = self.ui
        p = ui.Panel(tr("② 检查门店订货清单（推荐）"))
        intro = QLabel(tr("把门店发来的订货清单拖进来：按销量和数据库库存检查数量是否合理（增加 / 减少 / 不订），同时查其他门店的库存；确认后生成新的主订单，再点下面的「开始处理」做供应商比价。这样比价时还能算门店间调货和够卖天数。"))
        intro.setObjectName("Faint")
        intro.setWordWrap(True)
        p.lay.addWidget(intro)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel(tr("清单")))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText(tr("默认用 ORDINE 里的主订单；门店发来的清单可以直接拖到这里，或点「选择文件…」"))
        self.file_edit.setToolTip(tr("把门店发来的订货清单（Excel）拖到这里"))
        from erp_widgets import accept_file_drops
        accept_file_drops(self.file_edit, self._dropped, (".xlsx", ".xlsm"))
        r1.addWidget(self.file_edit, 1)
        b1 = QPushButton(tr("用主订单"))
        b1.setToolTip(tr("用总文件夹 ORDINE 里的主订单"))
        b1.clicked.connect(self.use_main_order)
        r1.addWidget(b1)
        b2 = QPushButton(tr("选择文件…"))
        b2.clicked.connect(self.choose_file)
        r1.addWidget(b2)
        p.lay.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(10)
        r2.addWidget(QLabel(tr("门店")))
        self.store = QComboBox()
        for sid in R.STORES:
            if sid:
                self.store.addItem(store_label(sid), sid)
        self.store.setCurrentIndex(max(0, self.store.findData(int(self.cfg.get("store", 1) or 1))))
        r2.addWidget(self.store)
        r2.addWidget(QLabel(tr("清单数量单位")))
        self.unit = QComboBox()
        self.unit.addItem(tr("箱"), "colli")
        self.unit.addItem(tr("件"), "pcs")
        r2.addWidget(self.unit)
        r2.addWidget(QLabel(tr("订货周期")))
        self.cycle = QComboBox()
        for d in R.REORDER_CYCLE_OPTIONS:
            self.cycle.addItem(tr("{0} 天", d), d)
        self.cycle.setCurrentIndex(max(0, self.cycle.findData(int(self.cfg.get("cycle", 30)))))
        r2.addWidget(self.cycle)
        self.use_cfg = ui.Toggle(tr("按配置文件周期"), bool(self.cfg.get("use_supplier_cycles", True)))
        self.use_cfg.setToolTip(tr("打开：suppliers_config.json 里单独设了订货周期的供应商用它自己的周期，其余用左边选的周期。\n关闭：全部用左边选的周期。"))
        r2.addWidget(self.use_cfg)
        r2.addSpacing(12)
        r2.addWidget(QLabel(tr("销量周期（天）")))
        self.days = []
        for d in self.cfg.get("days") or R.DEFAULT_SALES_DAYS:
            sb = QSpinBox()
            sb.setRange(7, 365)
            sb.setValue(int(d))
            sb.setFixedWidth(76)
            r2.addWidget(sb)
            self.days.append(sb)
        r2.addStretch()
        p.lay.addLayout(r2)
        r3 = QHBoxLayout()
        r3.setSpacing(10)
        r3.addWidget(QLabel(tr("算法")))
        algo_row, self.engine, self.algo_info = algo_widgets(self, ui)
        self.algo_info.setWordWrap(True)
        r3.addLayout(algo_row, 1)
        dbb = QPushButton(tr("数据库设置…"))
        dbb.clicked.connect(self.db_settings)
        r3.addWidget(dbb)
        self.btn_run = QPushButton(tr("检查数量"))
        self.btn_run.setObjectName("Primary")
        self.btn_run.clicked.connect(self.start)
        r3.addWidget(self.btn_run)
        p.lay.addLayout(r3)
        self.msg = QLabel("")
        self.msg.setObjectName("Muted")
        self.msg.setWordWrap(True)
        p.lay.addWidget(self.msg)

        # 检查结果在独立窗口里（表格大，不挤在页面上）
        from erp_widgets import ToolWindow
        self.window = ToolWindow(self.win, tr("检查门店订货清单"), (1360, 820))
        self.box = QWidget()
        self.box.setObjectName("Dialog")
        bl = QVBoxLayout(self.box)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(8)
        tb = QHBoxLayout()
        self.only = ui.Toggle(tr("只看有变化的"), False)
        self.only.toggled.connect(self._filter)
        tb.addWidget(self.only)
        tb.addStretch()
        self.flt = QLineEdit()
        self.flt.setPlaceholderText(tr("筛选编号、条码或名称"))
        self.flt.setFixedWidth(200)
        self.flt.textChanged.connect(self._filter)
        tb.addWidget(self.flt)
        b_s = QPushButton(tr("全部用建议量"))
        b_s.clicked.connect(lambda: self._set_all("sugg"))
        b_f = QPushButton(tr("全部用清单数量"))
        b_f.clicked.connect(lambda: self._set_all("file"))
        tb.addWidget(b_s)
        tb.addWidget(b_f)
        bl.addLayout(tb)
        self.table = ui.make_table(_perm([tr("判断"), tr("商品编号"), tr("条码"), tr("名称"), tr("本店库存"),
                                          "30", "60", "90", "180", tr("库存够卖（天）"), tr("箱规"), tr("清单数量"),
                                          tr("清单件数"), tr("建议件数"), tr("确定件数"), tr("确定箱数"), tr("等级"), tr("周期"), tr("日均"),
                                          tr("目标库存"), tr("订后够卖（天）")]),
                                   numeric_cols=[_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20]])
        self.table.setMinimumHeight(380)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setFocusPolicy(Qt.StrongFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, wd in enumerate(_perm((80, 80, 120, 300, 70, 52, 52, 52, 52, 90, 52, 70, 70, 70, 76, 70, 50, 50, 60, 70, 90))):
            self.table.setColumnWidth(c, wd)
        self.table.horizontalHeaderItem(EDIT_COL).setToolTip(tr("双击修改，0 = 不订"))
        self.table.horizontalHeaderItem(_col(9)).setToolTip(
            tr("库存够卖 = 本店库存 ÷ 日均（账面库存是负数时按 0 算）。门店配货、检查订货清单、批发商比价都是这个算法"))
        self.table.horizontalHeaderItem(AFTER_COL).setToolTip(
            tr("订后够卖 =（本店库存 + 确定件数）÷ 日均，改确定件数时跟着变；超过凑单天数标黄（可能压货）"))
        self.table.horizontalHeaderItem(_col(18)).setToolTip(
            tr("新算法：加权日均（含季节）；原程序算法：最近一个周期的销量 ÷ 天数"))
        self.table.itemChanged.connect(self._edited)
        # 点表头排序、右键表头按列筛选；上面「判断」「等级」两个下拉框快速筛选
        from erp_widgets import TableSortFilter, ColumnFilterBox
        self.ctl = TableSortFilter(self.table, lambda: len(self.rows), self._text, self._fill_row,
                                   hide=self._hidden, numeric=tuple(_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20]))
        self.ctl.rendered.connect(lambda n, tot: self.count_lbl.setText(
            tr("显示 {0} / {1} 个 · 点表头排序，右键表头筛选", n, tot)))
        tb.insertWidget(1, ColumnFilterBox(self.ctl, _col(0), tr("判断")))
        tb.insertWidget(2, ColumnFilterBox(self.ctl, _col(16), tr("等级")))
        tb.insertSpacing(1, 12)
        bl.addWidget(self.table)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("Faint")
        bl.addWidget(self.count_lbl)
        acts = QHBoxLayout()
        self.sum_msg = QLabel("")
        self.sum_msg.setObjectName("Faint")
        self.sum_msg.setWordWrap(True)
        acts.addWidget(self.sum_msg, 1)
        self.btn_export = QPushButton(tr("导出 Excel"))
        self.btn_export.clicked.connect(self.export)
        acts.addWidget(self.btn_export)
        self.btn_confirm = QPushButton(tr("确认，生成主订单"))
        self.btn_confirm.setObjectName("Primary")
        self.btn_confirm.setToolTip(tr("按「确定件数」生成新的主订单，原来的挪进 ORDINE\\_旧订单"))
        self.btn_confirm.clicked.connect(self.confirm)
        acts.addWidget(self.btn_confirm)
        bl.addLayout(acts)
        self.window.setCentralWidget(self.box)
        # 页面上只放一行摘要 + 打开窗口
        self.summary_row = QWidget()
        sr = QHBoxLayout(self.summary_row)
        sr.setContentsMargins(0, 0, 0, 0)
        self.page_sum = QLabel("")
        self.page_sum.setObjectName("Faint")
        self.page_sum.setWordWrap(True)
        sr.addWidget(self.page_sum, 1)
        ob = QPushButton(tr("打开检查窗口"))
        ob.setObjectName("Outline")
        ob.clicked.connect(self.window.bring_up)
        sr.addWidget(ob)
        cb = QPushButton(tr("确认，生成主订单"))
        cb.setObjectName("Primary")
        cb.clicked.connect(self.confirm)
        sr.addWidget(cb)
        self.summary_row.hide()
        p.lay.addWidget(self.summary_row)
        return p

    # ---------- 选文件 ----------
    def _main_order_path(self):
        root = self.win.folder_edit.text().strip()
        name = (self.win.info or {}).get("ordine")
        return os.path.join(root, "ORDINE", name) if root and name else ""

    def use_main_order(self):
        p = self._main_order_path()
        if not p or not os.path.exists(p):
            QMessageBox.information(self.win, tr("检查门店订货清单"), tr("总文件夹的 ORDINE 里没有主订单。"))
            return
        self.load_file(p)

    def choose_file(self):
        start = os.path.dirname(self.file_edit.text()) if self.file_edit.text() else (self.cfg.get("last_dir") or "")
        path, _ = QFileDialog.getOpenFileName(self.win, tr("选择订货清单"), start, tr("Excel 文件 (*.xlsx *.xlsm)"))
        if path:
            self.cfg["last_dir"] = os.path.dirname(path)
            self.load_file(path)

    def _dropped(self, path):
        """门店发来的清单直接拖进来（文件留在原来的地方，不复制）"""
        self.cfg["last_dir"] = os.path.dirname(path)
        self.load_file(path)
        self.win.show_toast(tr("已载入清单：{0}", os.path.basename(path)))

    def folder_changed(self):
        """总文件夹变了：没选过文件时默认用主订单"""
        if not self.file_path or not os.path.exists(self.file_path):
            p = self._main_order_path()
            if p and os.path.exists(p):
                self.load_file(p, quiet=True)

    def load_file(self, path, quiet=False):
        self.file_edit.setText(os.path.normpath(path))
        self.file_path = path
        try:
            info = R.parse_order_file(path)
        except Exception as e:
            self.file_info = None
            self.msg.setText(tr("✘ 读不出来：{0}", e))
            return
        self.file_info = info
        f = info["fields"]
        names = {"code": tr("商品编号"), "ean": tr("条码"), "name": tr("名称"), "qty": tr("数量")}
        parts = [f"{names[k]}=「{v}」" for k, v in f.items() if k in names]
        msg = tr("{0} 个商品。识别到：{1}", len(info["rows"]), "　".join(parts))
        if "qty" in f:
            u = R.qty_unit(f["qty"])
            self.unit.setCurrentIndex(self.unit.findData(u))
        sid = R.guess_store(path)
        if sid:
            self.store.setCurrentIndex(self.store.findData(sid))
            msg += "　" + tr("门店已按文件名设为 {0}", store_label(sid))
        else:
            msg += "　" + tr("请确认门店")
        for w in info["warnings"]:
            msg += "\n⚠ " + w
        self.msg.setText(msg)

    def db_settings(self):
        from reorder_page import DbDialog
        DbDialog(self.win, self.settings).exec()

    # ---------- 计算 ----------
    def start(self):
        if self.running:
            return
        if not self.file_info:
            p = self._main_order_path()
            if p and os.path.exists(p):
                self.load_file(p)
            if not self.file_info:
                QMessageBox.information(self.win, tr("检查门店订货清单"), tr("先选择订货清单。"))
                return
        store, cycle = self.store.currentData(), self.cycle.currentData()
        use_cfg = self.use_cfg.isChecked()
        algo = self.current_algo()
        days = [sb.value() for sb in self.days]
        unit = self.unit.currentData()
        self.cfg.update(store=store, cycle=cycle, days=days, use_supplier_cycles=use_cfg)
        save_settings(self.settings)
        file_rows = [dict(x) for x in self.file_info["rows"]]
        dbs = self.settings.get("db") or None
        log = self.win.log
        self.running = True
        self.btn_run.setEnabled(False)
        self.msg.setText(tr("正在连接数据库并计算，商品多时要几十秒…"))
        from erp_widgets import TaskDialog
        dlg = TaskDialog(self.win, tr("正在检查订货清单"), tr("{0} · {1} 个商品", store_label(store), len(file_rows)),
                         [tr("读取清单、对上商品编号"), tr("查询门店销量与库存"), tr("查询其他门店库存（调货用）"),
                          tr("对比清单数量")], cancellable=False)
        dlg.went_background.connect(lambda: self.win.show_toast(tr("清单在后台检查，完成后会提醒")))
        dlg.show()

        def work():
            try:
                dlg.post_step(0, "running", "", "")
                dlg.post_progress(5)
                db = R.make_db(dbs)
                need = [x["ean"] for x in file_rows if not x["code"] and x["ean"]]
                if need:
                    m = R.codes_from_eans(db, need)
                    for x in file_rows:
                        if not x["code"] and x["ean"] in m:
                            x["code"] = m[x["ean"]]
                codes = [x["code"] for x in file_rows if x["code"]]
                log(tr("[检查清单] 门店 {0}，{1} 个商品", store, len(codes)))
                dlg.post_step(0, "done", f"{len(codes)} / {len(file_rows)}", "")
                dlg.post_step(1, "running", "", "")
                dlg.post_progress(20)
                result, df, snap = [], None, None
                if codes:
                    df = R.compute_suggestions(db, store, codes, days, cycle, None, use_supplier_cycles=use_cfg,
                                               algo=algo)
                    result = R.to_rows(df, file_rows, days)
                dlg.post_step(1, "done", "", "")
                # 其他门店同一批商品的库存和日均：批发商比价时算门店间调货、够卖天数（查不到不影响检查）
                dlg.post_step(2, "running", "", "")
                dlg.post_progress(55)
                if codes:
                    try:
                        others = [k for k in R.STORES if k != 0]
                        snap = R.stock_snapshot(
                            db, codes, days, cycle, others, use_cfg, algo, own=store, own_df=df,
                            on_store=lambda k, sid: (dlg.post_step(2, "running", f"{k + 1} / {len(others)}", R.STORES.get(sid, "")),
                                                     dlg.post_progress(55 + 30 * k / max(1, len(others)))))
                        dlg.post_step(2, "done", f"{len(others)} / {len(others)}", "")
                    except Exception as e:
                        log(tr("⚠ 其他门店库存没查到（不能算调货）：{0}", e))
                        dlg.post_step(2, "error", "", str(e))
                else:
                    dlg.post_step(2, "done", "", "")
                db.engine.dispose()
                dlg.post_step(3, "running", "", "")
                dlg.post_progress(90)
                rows = R.check_rows(file_rows, result, unit)
                dlg.post_finished(True, tr("完成：{0} 个商品", len(rows)))
                self.sig.done.emit((rows, None, dict(store=store, days=days, cycle=cycle, unit=unit, snap=snap)))
            except Exception as e:
                import traceback
                log(tr("❌ 检查清单失败：{0}", e))
                log(traceback.format_exc())
                dlg.post_finished(False, str(e))
                self.sig.done.emit((None, str(e), None))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, payload):
        rows, err, meta = payload
        self.running = False
        self.btn_run.setEnabled(True)
        if err:
            self.msg.setText(tr("计算失败"))
            self.win.notify(tr("检查清单失败：{0}", err))
            return
        self.rows, self.meta = rows, meta
        d = meta["days"]
        for i in range(4):
            self.ctl.set_title(_col(5 + i), tr("{0}天", d[i]), tr("最近 {0} 天销量", d[i]))
        # 清单按箱：「清单箱数」+ 折合的「清单件数」；清单本来就是件数：只留一列「清单件数」
        pieces = meta["unit"] != "colli"
        self.ctl.set_title(_col(11), tr("清单件数") if pieces else tr("清单箱数"))
        self.table.setColumnHidden(_col(12), pieces)
        self.ctl.filters = {}
        self._fill()
        self.summary_row.show()
        self.window.setWindowTitle(tr("检查门店订货清单 — {0} · {1} 个商品", store_label(meta["store"]), len(rows)))
        self.window.bring_up()
        self.msg.setText(tr("✔ 门店 {0}：{1} 个商品，检查完成。核对「确定件数」后点「确认，生成主订单」。",
                            store_label(meta["store"]), len(rows)))

    def _vals(self, i):
        """数据行 i 每一列显示的文字（按屏幕上的列顺序）"""
        x = self.rows[i]
        sales = [f"{v:g}" if isinstance(v, (int, float)) else "" for v in x["sales"]]
        pack = x["pack"] or 0
        daily = x.get("daily") if x.get("daily") is not None else x.get("daily0")
        return _perm([tr(x["verdict"]), x["code"], x["ean"], x["name"],
                      f"{x['store_inv']:g}" if isinstance(x["store_inv"], (int, float)) else "",
                      *sales, _days(_cover(x)), f"{x['pack']:g}" if x["pack"] else "",
                      f"{x['file_qty']:g}", f"{x['file_pcs']:g}",
                      f"{x['suggested']:,}" if x["in_db"] else "", f"{x['final']:,}",
                      f"{math.ceil(x['final'] / pack):,}" if pack > 0 and x["final"] > 0 else "",
                      x.get("tier") or "",
                      f"{x['cycle']:g}" if isinstance(x.get("cycle"), (int, float)) else "",
                      f"{daily:.2f}" if daily else "",
                      f"{x['target']:.0f}" if isinstance(x.get("target"), (int, float)) else "",
                      _days(_cover(x, x["final"])) if x["final"] > 0 else ""])

    def _text(self, i, c):
        return self._vals(i)[c]

    def _hidden(self, i):
        x = self.rows[i]
        text = self.flt.text().strip().lower()
        return (self.only.isChecked() and x["verdict"] == "不变" and x["final"] == x["suggested"]) or \
               (bool(text) and text not in f"{x['code']} {x['ean']} {x['name']}".lower())

    def _fill(self):
        self.ctl.render()
        self._summary()

    def _fill_row(self, i, k):
        T = self.ui.T
        t = self.table
        x = self.rows[i]
        self.ui.table_set_row(t, k, self._vals(i))
        for c in range(t.columnCount()):
            it = t.item(k, c)
            if c == EDIT_COL:
                it.setFlags(it.flags() | Qt.ItemIsEditable)
            else:
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        t.item(k, 0).setForeground(QColor(T(VERDICT_COLOR.get(x["verdict"], "text"))))
        tip = short_decision(x["decision"]) if x["in_db"] else tr("数据库里找不到这个商品，箱规未知；确定件数默认 = 清单数量，请核对")
        t.item(k, 0).setToolTip(tip)
        t.item(k, 0).setData(Qt.UserRole, i)
        t.item(k, _col(3)).setToolTip(f"{x['name']}\nEAN {x['ean'] or '—'}")
        if isinstance(x.get("store_inv"), (int, float)) and x["store_inv"] < 0:
            for c_ in (_col(4), _col(9)):
                t.item(k, c_).setForeground(QColor(T("warn")))
                t.item(k, c_).setToolTip(tr("账面库存是负数（{0:g}），通常是卖了还没入库。算够卖天数时按 0 算", x["store_inv"]))
        self._style_edit(i, k)

    def _style_edit(self, i, k):
        x = self.rows[i]
        T = self.ui.T
        c = self.table.item(k, EDIT_COL)
        c.setText(f"{x['final']:,}")
        f = c.font()
        f.setBold(x["final"] > 0)
        c.setFont(f)
        changed = x["in_db"] and x["final"] != x["suggested"]
        c.setForeground(QColor(T("warn") if changed else T("accent") if x["final"] > 0 else T("faint")))
        c.setBackground(QColor(T("warn_soft")) if changed else QColor(0, 0, 0, 0))
        pack = x["pack"] or 0
        self.table.item(k, BOX_COL).setText(
            f"{math.ceil(x['final'] / pack):,}" if pack > 0 and x["final"] > 0 else "")
        # 订后够卖：跟着确定件数变；超过凑单天数标黄
        a = self.table.item(k, AFTER_COL)
        v = _cover(x, x["final"]) if x["final"] > 0 else None
        a.setText(_days(v))
        limit = float((self.settings.get("store_orders") or {}).get("fill", {}).get("max_stock_days") or 60)
        over = v is not None and v > limit
        a.setForeground(QColor(T("warn") if over else T("text")))
        a.setToolTip(tr("订完要很久才卖得完（超过 {0} 天），可能压货", int(limit)) if over else "")

    def _show(self, i):
        k = self.ctl.pos.get(i)
        if k is None:
            return
        was = self._filling
        self._filling = True
        self.table.blockSignals(True)
        self._style_edit(i, k)
        self.table.blockSignals(False)
        self._filling = was

    def _edited(self, item):
        if self._filling or item.column() != EDIT_COL:
            return
        i = self.ctl.row_at(item.row())
        if i is None:
            return
        try:
            n = float(item.text().strip().replace(",", "") or 0)
            if n < 0 or n != int(n):
                raise ValueError
        except ValueError:
            self.win.show_toast(tr("件数要填 0 或正整数"))
            n = None
        if n is not None:
            self.rows[i]["final"] = int(n)
        # 等编辑框关掉再更新这一行（编辑中途改格子会让程序卡住）
        QTimer.singleShot(0, lambda: (self._show(i), self._summary()))

    def _set_all(self, which):
        for x in self.rows:
            if which == "sugg":
                x["final"] = x["suggested"] if x["in_db"] else int(math.ceil(x["file_pcs"]))
            else:
                x["final"] = int(math.ceil(x["file_pcs"]))
        self.ctl.render()
        self._summary()

    def _filter(self, *_):
        if hasattr(self, "ctl"):
            self.ctl.render()
        self._summary()

    def _summary(self):
        cnt = {v: 0 for v in R.VERDICTS}
        for x in self.rows:
            cnt[x["verdict"]] = cnt.get(x["verdict"], 0) + 1
        n_final = sum(1 for x in self.rows if x["final"] > 0)
        n_edit = sum(1 for x in self.rows if x["in_db"] and x["final"] != x["suggested"])
        txt = tr("{0} 个商品：增加 {1}，减少 {2}，不订 {3}，不变 {4}，数据库没有 {5}。确定要订 {6} 个，手动改了 {7} 个。",
                 len(self.rows), cnt["增加"], cnt["减少"], cnt["不订"], cnt["不变"], cnt["数据库没有"], n_final, n_edit)
        # 采购金额估算：数据库进价（PrezzoForn，每件）× 件数；实际金额以「开始处理」后各批发商报价为准
        def price(x):
            try:
                v = float(x.get("price") or 0)
                return v if v == v else 0.0
            except (TypeError, ValueError):
                return 0.0
        amt_file = sum(price(x) * (x["file_pcs"] or 0) for x in self.rows)
        amt_final = sum(price(x) * x["final"] for x in self.rows)
        no_price = sum(1 for x in self.rows if x["final"] > 0 and not price(x))
        if amt_file or amt_final:
            txt += "\n" + tr("按数据库进价估算采购金额：清单 €{0:,.2f} → 确定 €{1:,.2f}", amt_file, amt_final)
            if no_price:
                txt += tr("（{0} 个商品没有进价，没算进去）", no_price)
        self.sum_msg.setText(txt)
        if hasattr(self, "page_sum"):
            self.page_sum.setText(txt)

    # ---------- 导出 / 确认 ----------
    def export(self):
        if not self.rows:
            return
        from openpyxl import Workbook
        from openpyxl.styles import Font
        name = f"清单检查_{R.STORES.get(self.meta['store'])}_{datetime.now():%Y%m%d_%H%M}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"), os.path.join(self.cfg.get("last_dir") or "", name),
                                              tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        d = self.meta["days"]
        wb = Workbook()
        ws = wb.active
        ws.title = "清单检查"
        ws.append(["判断", "商品编号", "条码", "名称", "本店库存", *[f"{x}天销量" for x in d], "库存够卖（天）", "箱规",
                   "清单数量", "清单件数", "建议件数", "确定件数", "订后够卖（天）", "系统说明"])
        for c in ws[1]:
            c.font = Font(bold=True)
        def num(v):
            return None if v is None else ("∞" if math.isinf(v) else round(v))

        for x in self.rows:
            ws.append([x["verdict"], x["code"], x["ean"], x["name"], x["store_inv"], *x["sales"], num(_cover(x)),
                       x["pack"] or None, x["file_qty"], x["file_pcs"], x["suggested"] if x["in_db"] else None, x["final"],
                       num(_cover(x, x["final"])) if x["final"] > 0 else None, x["decision"]])
            ws.cell(ws.max_row, 3).number_format = "@"
        ws.freeze_panes = "A2"
        try:
            wb.save(path)
            self.win.show_toast(tr("已导出"))
        except Exception as e:
            QMessageBox.critical(self.win, tr("导出"), tr("导出失败：{0}", e))

    def confirm(self):
        if not self.rows:
            return
        root = self.win.folder_edit.text().strip()
        if not root or not os.path.isdir(root):
            root = QFileDialog.getExistingDirectory(self.win, tr("选择采购用的总文件夹（里面要有 distribuire 供应商报价）"), root or "")
            if not root:
                return
        n = sum(1 for x in self.rows if x["final"] > 0)
        unknown = sum(1 for x in self.rows if not x["in_db"] and x["final"] > 0)
        text = tr("按确定件数把 {0} 个商品写进：\n{1}\\ORDINE\n\nORDINE 里原来的文件会挪进 ORDINE\\_旧订单。", n, os.path.normpath(root))
        if unknown:
            text += "\n\n" + tr("⚠ 其中 {0} 个商品数据库里没有，件数按清单数量，请确认无误。", unknown)
        if QMessageBox.question(self.win, tr("确认，生成主订单"), text + "\n\n" + tr("继续？")) != QMessageBox.Yes:
            return
        try:
            for sub in ("eliminare", "distribuire"):
                os.makedirs(os.path.join(root, sub), exist_ok=True)
            path, written, skipped = R.write_checked_order(self.rows, root, self.meta["store"])
        except Exception as e:
            QMessageBox.critical(self.win, tr("确认，生成主订单"), tr("写入失败：{0}", e))
            return
        self.win.log(tr("✔ 采购主订单：{0}（{1} 个商品）", path, written))
        if self.meta.get("snap"):
            try:
                R.save_snapshot(path, self.meta["store"], self.meta["snap"], self.rows)
            except Exception as e:
                self.win.log(tr("⚠ 门店库存快照没存上（批发商比价不能算调货）：{0}", e))
        if skipped:
            self.win.log(tr("⚠ {0} 个商品没有条码，没写进主订单：{1}", len(skipped),
                            "、".join(f"{x['code']} {x['name'][:20]}" for x in skipped[:10])))
        self.win.folder_edit.setText(os.path.normpath(root))
        self.settings["last_folder"] = self.win.folder_edit.text()
        save_settings(self.settings)
        self.file_path = path
        self.file_edit.setText(os.path.normpath(path))
        self.win.refresh_folder()
        if hasattr(self.win, "cmp_steps"):
            self.win.cmp_steps.set_state(1, "done", tr("已生成主订单"))
        msg = tr("已生成采购主订单（{0} 个商品），确认后点「开始处理」分配供应商", written)
        if skipped:
            msg += tr("；{0} 个没条码的没写进去，详见运行日志", len(skipped))
        self.win.banner_folder.show_msg(msg, "ok" if not skipped else "warn")
        self.win.show_toast(tr("主订单已生成"))
