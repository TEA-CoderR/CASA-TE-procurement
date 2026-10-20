"""
批发商比价的 ②：检查门店订货清单（比价用的主订单只能从这里生成）

一次可以放一家或几家门店的清单（和门店配货一样，几家一起才好调货）：
门店发来的订货清单 → 按这家门店的销量、数据库库存和订货周期算建议量
→ 逐条对比清单数量：增加 / 减少 / 不订 / 不变 → 可以手动改「确定件数」
→ 确认时先算门店间调货（别的门店多出来的货先用掉），每家门店剩下要订的写成它自己的主订单
  （每家店一个总文件夹，PEZZI = 确定件数 − 调货），原文件挪进 ORDINE\\_旧订单；调货单写进相关门店的文件夹；
→ 再一家一家点「开始处理」做供应商比价分配（每家店各自下单、各自达到起订量）。
"""
import os
import math
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QLineEdit, QComboBox, QHBoxLayout, QVBoxLayout,
                               QFileDialog, QMessageBox, QSpinBox, QAbstractItemView, QHeaderView, QDialog,
                               QGridLayout, QFrame)

from i18n import tr
from settings import save_settings
import reorder_service as R
from reorder_page import short_decision, store_label, algo_widgets

# 表格列（逻辑顺序）→ 显示顺序：要核对、要改的列放前面
# 从左到右按核对的顺序：门店 判断 等级 名称 | 现在：本店库存 库存够卖 | 门店清单：箱数 件数 清单订后够卖 | 建议件数 |
# 确定：件数 箱数 确定订后够卖 | 日均 目标库存 | 各周期销量 | 周期 箱规 编号 条码（列可以拖动换位置，会记住）
_ORDER = [22, 0, 16, 3, 4, 9, 11, 12, 21, 13, 14, 15, 20, 18, 19, 5, 6, 7, 8, 17, 10, 1, 2]
_LAYOUT_VER = 2           # 改了默认顺序就加 1


def _col(k):
    return _ORDER.index(k)


def _perm(vals):
    return [vals[k] for k in _ORDER]


EDIT_COL = _col(14)
BOX_COL = _col(15)
AFTER_COL = _col(20)          # 确定订后够卖（跟着确定件数变）
LIST_AFTER_COL = _col(21)     # 清单订后够卖（按门店清单原来的数量订）


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
        self.lists = []           # 放进来的清单：[{"path", "info", "store", "unit"}]，一家门店一份
        self.running = False
        self._filling = False
        self.sig = _Sig()
        self.sig.done.connect(self._done)

    def build(self):
        ui = self.ui
        p = ui.Panel(tr("② 检查门店订货清单"))
        intro = QLabel(tr("比价用的主订单只从这里生成：把一家或几家门店的订货清单一起拖进来，按销量和数据库库存检查数量（增加 / 减少 / 不订），同时查五家门店的库存。确认时先用别的门店多出来的货（门店间调货），每家店剩下要订的各自生成主订单；再一家一家点下面的「开始处理」做供应商比价。"))
        intro.setObjectName("Faint")
        intro.setWordWrap(True)
        p.lay.addWidget(intro)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel(tr("清单")))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText(tr("把一家或几家门店的订货清单拖到这里（可以一次拖好几个），或点「选择文件…」"))
        self.file_edit.setToolTip(tr("几家门店的清单一起检查，才能先在门店之间调货、再订剩下的（和门店配货一样）"))
        from erp_widgets import accept_file_drops
        accept_file_drops(self.file_edit, self._dropped, (".xlsx", ".xlsm"), multi=True)
        r1.addWidget(self.file_edit, 1)
        b2 = QPushButton(tr("选择文件…"))
        b2.clicked.connect(self.choose_file)
        r1.addWidget(b2)
        b_clr = QPushButton(tr("清空"))
        b_clr.clicked.connect(self.clear_lists)
        r1.addWidget(b_clr)
        p.lay.addLayout(r1)
        # 放进来的清单：每份一行，门店按文件名认，认不出来的自己选
        self.list_table = ui.make_table([tr("门店"), tr("清单文件"), tr("商品数"), tr("数量单位"), ""], stretch_col=1,
                                        numeric_cols=(2,))
        self.list_table.verticalHeader().setDefaultSectionSize(36)
        self.list_table.setColumnWidth(0, 170)
        self.list_table.setColumnWidth(2, 70)
        self.list_table.setColumnWidth(3, 90)
        self.list_table.setColumnWidth(4, 70)
        self.list_table.setMaximumHeight(36 * 5 + 34)
        self.list_table.hide()
        p.lay.addWidget(self.list_table)
        # 现在的主订单（上一次在这里确认生成的）
        r0 = QHBoxLayout()
        self.order_lbl = QLabel("")
        self.order_lbl.setWordWrap(True)
        r0.addWidget(self.order_lbl, 1)
        self.btn_open_order = QPushButton(tr("打开主订单"))
        self.btn_open_order.setObjectName("Link")
        self.btn_open_order.clicked.connect(lambda: self.win.open_main_order())
        r0.addWidget(self.btn_open_order)
        p.lay.addLayout(r0)

        r2 = QHBoxLayout()
        r2.setSpacing(10)
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
                                          tr("目标库存"), tr("确定订后够卖（天）"), tr("清单订后够卖（天）"), tr("门店")]),
                                   numeric_cols=[_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20, 21]])
        self.table.setMinimumHeight(380)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setFocusPolicy(Qt.StrongFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, wd in enumerate(_perm((70, 80, 120, 300, 70, 52, 52, 52, 52, 96, 52, 70, 70, 70, 76, 70, 50, 50, 60, 70, 120, 120, 96))):
            self.table.setColumnWidth(c, wd)
        self.table.horizontalHeaderItem(EDIT_COL).setToolTip(tr("双击修改，0 = 不订"))
        self.table.horizontalHeaderItem(_col(9)).setToolTip(
            tr("库存够卖 = 本店库存 ÷ 日均（账面库存是负数时按 0 算）。门店配货、检查订货清单、批发商比价都是这个算法"))
        self.table.horizontalHeaderItem(AFTER_COL).setToolTip(
            tr("确定订后够卖 =（本店库存 + 确定件数）÷ 日均，改确定件数时跟着变；超过凑单天数标黄（可能压货）"))
        self.table.horizontalHeaderItem(LIST_AFTER_COL).setToolTip(
            tr("清单订后够卖 =（本店库存 + 门店清单原来的件数）÷ 日均：门店自己报的量订完能卖几天，和「确定订后够卖」对比"))
        self.table.horizontalHeaderItem(_col(18)).setToolTip(
            tr("新算法：加权日均（含季节）；原程序算法：最近一个周期的销量 ÷ 天数"))
        self.table.itemChanged.connect(self._edited)
        # 点表头排序、右键表头按列筛选；上面「判断」「等级」两个下拉框快速筛选
        from erp_widgets import TableSortFilter, ColumnFilterBox
        self.ctl = TableSortFilter(self.table, lambda: len(self.rows), self._text, self._fill_row,
                                   hide=self._hidden, numeric=tuple(_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20, 21]))
        # 列可以拖动换位置、拖宽度，下次打开照旧（右键表头是筛选，所以「恢复默认列顺序」放在下面）
        from erp_widgets import remember_columns
        self._reset_cols = remember_columns(self.table, self.cfg, "check", _ORDER, lambda: save_settings(self.settings),
                                            _LAYOUT_VER)
        self.ctl.rendered.connect(lambda n, tot: self.count_lbl.setText(
            tr("显示 {0} / {1} 个 · 点表头排序，右键表头筛选，拖动表头换列的位置", n, tot)))
        tb.insertWidget(1, ColumnFilterBox(self.ctl, _col(22), tr("门店")))
        tb.insertWidget(2, ColumnFilterBox(self.ctl, _col(0), tr("判断")))
        tb.insertWidget(3, ColumnFilterBox(self.ctl, _col(16), tr("等级")))
        tb.insertSpacing(1, 12)
        bl.addWidget(self.table)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("Faint")
        crow = QHBoxLayout()
        crow.addWidget(self.count_lbl, 1)
        b_cols = QPushButton(tr("恢复默认列顺序"))
        b_cols.setObjectName("Link")
        b_cols.setToolTip(tr("拖动表头可以换列的位置、拖边线改宽度，下次打开保持"))
        b_cols.clicked.connect(lambda: self._reset_cols())
        crow.addWidget(b_cols)
        bl.addLayout(crow)
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

    # ---------- 选文件（一家或几家门店） ----------
    def choose_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self.win, tr("选择订货清单（可以选好几个）"), self.cfg.get("last_dir") or "",
                                                tr("Excel 文件 (*.xlsx *.xlsm)"))
        if paths:
            self.cfg["last_dir"] = os.path.dirname(paths[0])
            self.add_files(paths)

    def _dropped(self, paths):
        """门店发来的清单直接拖进来（文件留在原来的地方，不复制）"""
        paths = paths if isinstance(paths, (list, tuple)) else [paths]
        self.cfg["last_dir"] = os.path.dirname(paths[0])
        n = self.add_files(paths)
        if n:
            self.win.show_toast(tr("已载入 {0} 份清单", n))

    def add_files(self, paths):
        """读清单、按文件名认门店；同一家门店再放一份就替换原来那份。返回放进来几份"""
        notes, n = [], 0
        for path in paths:
            name = os.path.basename(path)
            if any(os.path.normcase(os.path.abspath(x["path"])) == os.path.normcase(os.path.abspath(path)) for x in self.lists):
                continue
            try:
                info = R.parse_order_file(path)
            except Exception as e:
                notes.append(tr("✘ {0} 读不出来：{1}", name, e))
                continue
            f = info["fields"]
            unit = R.qty_unit(f["qty"]) if "qty" in f else "colli"
            sid = R.guess_store(path)
            if sid:
                self.lists = [x for x in self.lists if x["store"] != sid]
            self.lists.append({"path": path, "info": info, "store": sid, "unit": unit})
            n += 1
            names = {"code": tr("商品编号"), "ean": tr("条码"), "name": tr("名称"), "qty": tr("数量")}
            line = tr("{0}：{1} 个商品，识别到 {2}", name, len(info["rows"]),
                      "　".join(f"{names[k]}=「{v}」" for k, v in f.items() if k in names))
            if not sid:
                line += "　" + tr("文件名里看不出是哪家门店，请在上面选")
            for w in info["warnings"]:
                line += "\n⚠ " + w
            notes.append(line)
        self._refresh_lists()
        self.msg.setText("\n".join(notes))
        return n

    def clear_lists(self):
        self.lists = []
        self._refresh_lists()
        self.msg.setText("")

    def _refresh_lists(self):
        t = self.list_table
        t.setRowCount(len(self.lists))
        stores = [k for k in R.STORES if k]
        for i, x in enumerate(self.lists):
            self.ui.table_set_row(t, i, ["", os.path.basename(x["path"]), str(len(x["info"]["rows"])), "", ""])
            t.item(i, 1).setToolTip(x["path"])
            cb = QComboBox()
            cb.addItem(tr("选择门店…"), None)
            for k in stores:
                cb.addItem(store_label(k), k)
            cb.setCurrentIndex(max(0, cb.findData(x["store"])))
            cb.currentIndexChanged.connect(lambda _, i=i, cb=cb: self._set_store(i, cb.currentData()))
            t.setCellWidget(i, 0, cb)
            ub = QComboBox()
            ub.addItem(tr("箱"), "colli")
            ub.addItem(tr("件"), "pcs")
            ub.setCurrentIndex(max(0, ub.findData(x["unit"])))
            ub.currentIndexChanged.connect(lambda _, x=x, ub=ub: x.__setitem__("unit", ub.currentData()))
            t.setCellWidget(i, 3, ub)
            rb = QPushButton(tr("移除"))
            rb.setObjectName("Link")
            rb.clicked.connect(lambda _=False, i=i: self._remove(i))
            t.setCellWidget(i, 4, rb)
        t.setVisible(bool(self.lists))
        names = [R.STORES.get(x["store"]) or "?" for x in self.lists]
        self.file_edit.setText(tr("{0} 份清单：{1}", len(self.lists), "、".join(names)) if self.lists else "")

    def _set_store(self, i, sid):
        if 0 <= i < len(self.lists):
            if sid and any(k != i and x["store"] == sid for k, x in enumerate(self.lists)):
                QMessageBox.information(self.win, tr("检查门店订货清单"),
                                        tr("{0} 已经有一份清单了。同一家门店只能放一份，请先移除另一份。", R.STORES.get(sid)))
                self._refresh_lists()
                return
            self.lists[i]["store"] = sid
            self._refresh_lists()

    def _remove(self, i):
        if 0 <= i < len(self.lists):
            self.lists.pop(i)
            self._refresh_lists()

    def folder_changed(self):
        """总文件夹变了：显示现在的主订单是哪家门店、什么时候生成的"""
        if not hasattr(self, "order_lbl"):
            return
        info = self.win.info or {}
        T = self.ui.T
        name = info.get("ordine")
        if name:
            root = self.win.folder_edit.text().strip()
            path = os.path.join(root, "ORDINE", name)
            snap = R.load_snapshot(path) or {}
            try:
                when = datetime.fromtimestamp(os.path.getmtime(path))
                when = f"{when:%d/%m %H:%M}"
            except OSError:
                when = ""
            self.order_lbl.setText(tr("✔ 现在的主订单：{0}（{1}，{2} 生成）。门店清单变了就重新检查、再确认一次，会替换它。",
                                      name, store_label(snap.get("store")) if snap.get("store") else "—", when))
            self.order_lbl.setStyleSheet(f"color:{T('ok')};")
        else:
            self.order_lbl.setText(info.get("need_order") or tr("还没有主订单。"))
            self.order_lbl.setStyleSheet(f"color:{T('warn') if info.get('ordine_old') else T('muted')};")
        self.btn_open_order.setVisible(bool(name))

    def db_settings(self):
        from reorder_page import DbDialog
        DbDialog(self.win, self.settings).exec()

    # ---------- 计算 ----------
    def start(self):
        if self.running:
            return
        if not self.lists:
            QMessageBox.information(self.win, tr("检查门店订货清单"), tr("先把门店发来的订货清单拖进来，或点「选择文件…」。"))
            return
        if any(not x["store"] for x in self.lists):
            QMessageBox.information(self.win, tr("检查门店订货清单"), tr("有清单还没选是哪家门店，请在清单列表里选。"))
            return
        cycle = self.cycle.currentData()
        use_cfg = self.use_cfg.isChecked()
        algo = self.current_algo()
        days = [sb.value() for sb in self.days]
        self.cfg.update(cycle=cycle, days=days, use_supplier_cycles=use_cfg)
        save_settings(self.settings)
        lists = [{"store": x["store"], "unit": x["unit"], "path": x["path"],
                  "rows": [dict(r) for r in x["info"]["rows"]]} for x in self.lists]
        dbs = self.settings.get("db") or None
        log = self.win.log
        self.running = True
        self.btn_run.setEnabled(False)
        self.msg.setText(tr("正在连接数据库并计算，商品多时要几十秒…"))
        names = "、".join(R.STORES.get(x["store"], "") for x in lists)
        from erp_widgets import TaskDialog
        dlg = TaskDialog(self.win, tr("正在检查订货清单"),
                         tr("{0} · {1} 个商品", names, sum(len(x["rows"]) for x in lists)),
                         [tr("读取清单、对上商品编号"), tr("查询门店销量与库存"), tr("查询五家门店库存（调货用）"),
                          tr("对比清单数量")], cancellable=False)
        dlg.went_background.connect(lambda: self.win.show_toast(tr("清单在后台检查，完成后会提醒")))
        dlg.show()

        def work():
            try:
                dlg.post_step(0, "running", "", "")
                dlg.post_progress(5)
                db = R.make_db(dbs)
                need = [r["ean"] for x in lists for r in x["rows"] if not r["code"] and r["ean"]]
                if need:
                    m = R.codes_from_eans(db, need)
                    for x in lists:
                        for r in x["rows"]:
                            if not r["code"] and r["ean"] in m:
                                r["code"] = m[r["ean"]]
                union = []
                for x in lists:
                    x["codes"] = [r["code"] for r in x["rows"] if r["code"]]
                    union += [c for c in x["codes"] if c not in union]
                dlg.post_step(0, "done", f"{len(union)}", "")
                dlg.post_step(1, "running", "", "")
                for k, x in enumerate(lists):
                    sid = x["store"]
                    log(tr("[检查清单] 门店 {0}，{1} 个商品", R.STORES.get(sid, sid), len(x["codes"])))
                    dlg.post_step(1, "running", f"{k + 1} / {len(lists)}", R.STORES.get(sid, ""))
                    dlg.post_progress(15 + 40 * k / max(1, len(lists)))
                    x["df"], x["result"] = None, []
                    if x["codes"]:
                        x["df"] = R.compute_suggestions(db, sid, x["codes"], days, cycle, None, use_supplier_cycles=use_cfg,
                                                        algo=algo)
                        x["result"] = R.to_rows(x["df"], x["rows"], days)
                dlg.post_step(1, "done", f"{len(lists)} / {len(lists)}", "")
                # 五家门店这批商品（所有清单合起来）的库存和日均：先算门店间调货，比价时算够卖天数（查不到不影响检查）
                dlg.post_step(2, "running", "", "")
                dlg.post_progress(55)
                snap = None
                if union:
                    try:
                        others = [k for k in R.STORES if k != 0]
                        one = lists[0] if len(lists) == 1 else None          # 只有一份清单：它自己那家不用再查
                        snap = R.stock_snapshot(
                            db, union, days, cycle, others, use_cfg, algo, own=one["store"] if one else None,
                            own_df=one["df"] if one else None,
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
                rows = []
                for x in lists:
                    for r in R.check_rows(x["rows"], x["result"], x["unit"]):
                        r["store"] = x["store"]
                        rows.append(r)
                dlg.post_finished(True, tr("完成：{0} 个商品", len(rows)))
                self.sig.done.emit((rows, None, dict(stores=[x["store"] for x in lists], days=days, cycle=cycle,
                                                     units={x["store"]: x["unit"] for x in lists},
                                                     files={x["store"]: x["path"] for x in lists}, snap=snap,
                                                     store=lists[0]["store"])))
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
        # 清单按箱：「清单箱数」+ 折合的「清单件数」；清单本来就是件数：只留一列「清单件数」；几份清单单位不一样：「清单数量」
        units = set(meta["units"].values())
        pieces = units == {"pcs"}
        self.ctl.set_title(_col(11), tr("清单件数") if pieces else (tr("清单箱数") if units == {"colli"} else tr("清单数量")))
        self.table.setColumnHidden(_col(12), pieces)
        self.table.setColumnHidden(_col(22), len(meta["stores"]) < 2)          # 只有一家门店：不显示门店列
        self.ctl.filters = {}
        self._fill()
        self.summary_row.show()
        self.window.setWindowTitle(tr("检查门店订货清单 — {0} · {1} 个商品",
                                      "、".join(store_label(k) for k in meta["stores"]), len(rows)))
        self.window.bring_up()
        self.msg.setText(tr("✔ 门店 {0}：{1} 个商品，检查完成。核对「确定件数」后点「确认，生成主订单」。",
                            "、".join(store_label(k) for k in meta["stores"]), len(rows)))

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
                      _days(_cover(x, x["final"])) if x["final"] > 0 else "",
                      _days(_cover(x, x["file_pcs"])) if (x.get("file_pcs") or 0) > 0 else "",
                      R.STORES.get(x.get("store"), "")])

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
        v = _cover(x, x["file_pcs"]) if (x.get("file_pcs") or 0) > 0 else None
        if v is not None and v > self._limit():
            t.item(k, LIST_AFTER_COL).setForeground(QColor(T("warn")))
            t.item(k, LIST_AFTER_COL).setToolTip(tr("订完要很久才卖得完（超过 {0} 天），可能压货", int(self._limit())))
        self._style_edit(i, k)

    def _limit(self):
        """凑单天数（配货设置）：订后够卖超过它标黄"""
        return float((self.settings.get("store_orders") or {}).get("fill", {}).get("max_stock_days") or 60)

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
        limit = self._limit()
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
        tag = "_".join(str(R.STORES.get(k, k)) for k in self.meta["stores"])
        name = f"清单检查_{tag}_{datetime.now():%Y%m%d_%H%M}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"), os.path.join(self.cfg.get("last_dir") or "", name),
                                              tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        d = self.meta["days"]
        wb = Workbook()
        ws = wb.active
        ws.title = "清单检查"
        ws.append(["门店", "判断", "商品编号", "条码", "名称", "本店库存", *[f"{x}天销量" for x in d], "库存够卖（天）", "箱规",
                   "清单数量", "清单件数", "清单订后够卖（天）", "建议件数", "确定件数", "确定订后够卖（天）", "系统说明"])
        for c in ws[1]:
            c.font = Font(bold=True)
        def num(v):
            return None if v is None else ("∞" if math.isinf(v) else round(v))

        for x in self.rows:
            ws.append([R.STORES.get(x.get("store"), ""), x["verdict"], x["code"], x["ean"], x["name"], x["store_inv"],
                       *x["sales"], num(_cover(x)),
                       x["pack"] or None, x["file_qty"], x["file_pcs"],
                       num(_cover(x, x["file_pcs"])) if (x.get("file_pcs") or 0) > 0 else None,
                       x["suggested"] if x["in_db"] else None, x["final"],
                       num(_cover(x, x["final"])) if x["final"] > 0 else None, x["decision"]])
            ws.cell(ws.max_row, 4).number_format = "@"
        ws.freeze_panes = "A2"
        try:
            wb.save(path)
            self.win.show_toast(tr("已导出"))
        except Exception as e:
            QMessageBox.critical(self.win, tr("导出"), tr("导出失败：{0}", e))

    def batch_root(self):
        """
        比价根目录（下面自动建 批发商比价\\日期\\门店）：现在的总文件夹已经是某一批的门店文件夹 → 它的根目录；
        否则上次用的根目录；再否则现在的总文件夹
        """
        cur = self.win.folder_edit.text().strip()
        r = R.batch_root_of(cur)
        if r:
            return r
        saved = self.settings.get("wholesale_root")
        if saved and os.path.isdir(saved):
            return saved
        return cur if cur and os.path.isdir(cur) else ""

    def _to_order(self, sid):
        return sum(1 for x in self.rows if x.get("store") == sid and x["final"] > 0)

    def transfer_plan(self):
        """按现在的确定件数算门店间调货：(调货单, {(门店, 商品编号): 调货后还要订的件数})"""
        import store_orders as SO
        so = self.settings.get("store_orders") or {}
        far = (dict(SO.DEFAULT_FILL, **(so.get("fill") or {}))).get("far_stores") or []
        by_store = {}
        for x in self.rows:
            by_store.setdefault(x.get("store"), []).append(x)
        snap = self.meta.get("snap")
        if not snap:
            return [], {}
        return R.plan_batch_transfers(by_store, snap, so.get("transfer"), far)

    def confirm(self):
        if not self.rows:
            return
        moves, new_final = self.transfer_plan()
        dlg = BatchConfirmDialog(self, moves)
        if not dlg.exec():
            return
        folders = dlg.folders()
        root_dir = dlg.root()
        self.settings["wholesale_root"] = root_dir
        made, problems = [], []
        tag = datetime.now().strftime("%Y%m%d_%H%M")
        day_dir = R.batch_folder(root_dir)
        os.makedirs(day_dir, exist_ok=True)
        os.makedirs(os.path.join(root_dir, "distribuire"), exist_ok=True)     # 几家门店共用的供应商报价
        all_tf = None
        if moves:                                  # 整批的调货单放在这一天的文件夹里（每家店的也放一份在它自己的文件夹）
            try:
                import store_orders as SO
                all_tf = SO.export_transfers(moves, os.path.join(day_dir, f"调货单_全部门店_{tag}.xlsx"))
            except Exception as e:
                problems.append(tr("调货单没写上：{0}", e))
        for sid in self.meta["stores"]:
            if sid not in folders:                 # 没有要订的：不建文件夹、不生成主订单
                continue
            root = R.make_store_folder(folders[sid])
            if hasattr(self.win, "sync_prices"):
                self.win.sync_prices(root)             # 共用的供应商报价（根目录\\distribuire）复制一份过来
            rows = [dict(x, final=new_final.get((sid, str(x.get("code"))), x["final"]))
                    for x in self.rows if x.get("store") == sid]
            mine = [m for m in moves if sid in (m["from"], m["to"])]
            try:
                path, written, skipped = R.write_checked_order(rows, root, sid)
            except Exception as e:
                problems.append(tr("{0}：写入失败：{1}", R.STORES.get(sid), e))
                continue
            tf = None
            if mine:
                try:
                    import store_orders as SO
                    tf = SO.export_transfers(mine, os.path.join(root, f"调货单_{R.STORES.get(sid)}_{tag}.xlsx"))
                except Exception as e:
                    problems.append(tr("{0}：调货单没写上：{1}", R.STORES.get(sid), e))
            if self.meta.get("snap"):
                try:
                    R.save_snapshot(path, sid, self.meta["snap"], rows, transfers=mine, transfers_file=tf)
                except Exception as e:
                    problems.append(tr("{0}：门店库存快照没存上：{1}", R.STORES.get(sid), e))
            self.win.log(tr("✔ 采购主订单：{0}（{1} 个商品）", path, written))
            if skipped:
                self.win.log(tr("⚠ {0} 个商品没有条码，没写进主订单：{1}", len(skipped),
                                "、".join(f"{x['code']} {x['name'][:20]}" for x in skipped[:10])))
            made.append((sid, root, written, len(skipped)))
        if not made:
            QMessageBox.critical(self.win, tr("确认，生成主订单"), "\n".join(problems))
            return
        if moves:
            self.win.log(tr("✔ 门店间调货：{0} 个商品，少订 {1} 箱", len(moves),
                            sum(m["colli_before"] - m["colli_after"] for m in moves)))
            if all_tf:
                self.win.log(tr("✔ 调货单（全部门店）：{0}", all_tf))
        # 这一批是哪几家门店（批发商比价页上可以一家一家切换过去比价）
        self.settings["wholesale_batch"] = {"time": datetime.now().isoformat(timespec="minutes"),
                                            "stores": {str(sid): root for sid, root, _, _ in made}}
        first = made[0][1]
        self.win.folder_edit.setText(os.path.normpath(first))
        self.settings["last_folder"] = self.win.folder_edit.text()
        save_settings(self.settings)
        self.win.refresh_folder()
        if hasattr(self.win, "cmp_steps"):
            self.win.cmp_steps.set_state(1, "done", tr("已生成主订单"))
        msg = tr("已生成 {0} 家门店的主订单（{1}）", len(made), "、".join(
            f"{R.STORES.get(sid)} {n}" for sid, _, n, _ in made))
        if moves:
            msg += "；" + tr("门店间调货 {0} 个商品，少订 {1} 箱，调货单在 {2}", len(moves),
                            sum(m["colli_before"] - m["colli_after"] for m in moves), day_dir)
        if len(made) > 1:
            msg += "。" + tr("现在显示 {0}，比完价在上面切换到下一家", R.STORES.get(made[0][0]))
        msg += "。" + tr("供应商报价用 {0}（所有门店共用，每家店自动复制一份写自己的订单）", os.path.join(root_dir, "distribuire"))
        if problems:
            msg += "\n⚠ " + "；".join(problems)
        self.win.banner_folder.show_msg(msg, "warn" if problems else "ok")
        # 主订单生成了，检查窗口就用不着了：直接关掉，回到批发商比价，下一步点「开始处理」
        self.window.close()
        if self.win.isMinimized():
            self.win.showNormal()
        self.win.raise_()
        self.win.activateWindow()
        if hasattr(self.win, "go_page"):
            self.win.go_page("order")
        if hasattr(self.win, "btn_run"):
            self.win.btn_run.setFocus()
        self.win.show_toast(tr("主订单已生成，点「开始处理」做供应商比价"))


class BatchConfirmDialog(QDialog):
    """确认生成主订单：每家门店先调货少订多少、文件夹自动建在 根目录\\批发商比价\\日期\\门店；调货明细可以打开看"""

    def __init__(self, oc, moves):
        super().__init__(oc.win)
        import transfers as TR
        self.oc, self.moves = oc, moves
        T = oc.ui.T
        self.setWindowTitle(tr("确认，生成主订单"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(12)
        t = QLabel(tr("每家门店按确定件数生成自己的主订单（各自比价、各自下单）"))
        t.setObjectName("Section")
        lay.addWidget(t)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        # 文件夹自动建：根目录\批发商比价\日期\门店（只要选一次根目录）
        rr = QHBoxLayout()
        rr.addWidget(QLabel(tr("比价根目录")))
        self.root_ed = QLineEdit(oc.batch_root())
        self.root_ed.setMinimumWidth(380)
        self.root_ed.setPlaceholderText(tr("选一个放比价文件的文件夹，例如 D:\\CasaTe"))
        self.root_ed.textChanged.connect(lambda _: self._paths())
        rr.addWidget(self.root_ed, 1)
        b = QPushButton(tr("选择…"))
        b.clicked.connect(self._pick)
        rr.addWidget(b)
        lay.addLayout(rr)
        for c, h in enumerate((tr("门店"), tr("要订"), tr("调货少订"), tr("自动建的文件夹"))):
            lb = QLabel(h)
            lb.setObjectName("Faint")
            g.addWidget(lb, 0, c)
        self.path_lbls = {}
        saved = {}
        for m in moves:
            saved[m["to"]] = saved.get(m["to"], 0) + (m["colli_before"] - m["colli_after"])
        for r, sid in enumerate(oc.meta["stores"], 1):
            n = oc._to_order(sid)
            g.addWidget(QLabel(store_label(sid)), r, 0)
            g.addWidget(QLabel(tr("{0} 个商品", n)), r, 1)
            g.addWidget(QLabel(tr("{0} 箱", saved[sid]) if saved.get(sid) else "—"), r, 2)
            pl = QLabel("")
            pl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            g.addWidget(pl, r, 3)
            self.path_lbls[sid] = (pl, n)
        g.setColumnStretch(3, 1)
        lay.addLayout(g)
        self._paths()
        if moves:
            dn = TR.by_donor(moves)
            txt = tr("门店间调货（先用别的门店多出来的货，再订剩下的）：{0} 个商品，少订 {1} 箱。", len(moves),
                     sum(m["colli_before"] - m["colli_after"] for m in moves)) + "　" + "　".join(
                tr("从 {0}：{1} 个商品 · €{2:,.2f}", R.STORES.get(k, k), v["items"], v["value"]) for k, v in sorted(dn.items()))
            row = QHBoxLayout()
            lb = QLabel(txt)
            lb.setWordWrap(True)
            lb.setStyleSheet(f"color:{T('ok')};")
            row.addWidget(lb, 1)
            bv = QPushButton(tr("查看调货明细"))
            bv.clicked.connect(self._show_moves)
            row.addWidget(bv)
            lay.addLayout(row)
        else:
            lb = QLabel(tr("门店间调货：这次没有可调的商品（其他门店没有多出来的货，或者一家调出的货值不到门槛）。")
                        if oc.meta.get("snap") else tr("门店间调货：没有查到其他门店的库存，这次不调货。"))
            lb.setObjectName("Faint")
            lb.setWordWrap(True)
            lay.addWidget(lb)
        unknown = sum(1 for x in oc.rows if not x["in_db"] and x["final"] > 0)
        if unknown:
            w = QLabel(tr("⚠ 其中 {0} 个商品数据库里没有，件数按清单数量，请确认无误。", unknown))
            w.setStyleSheet(f"color:{T('warn')};")
            lay.addWidget(w)
        h = QLabel(tr("每家门店一个文件夹，里面自动建 ORDINE（主订单）、eliminare（扣减文件）、distribuire（从根目录的共用报价复制一份，写这家店的订单）；整批的调货单放在日期文件夹里。同一天再确认一次会用同一个文件夹，原来的主订单挪进 ORDINE\\_旧订单。"))
        h.setObjectName("Faint")
        h.setWordWrap(True)
        lay.addWidget(h)
        self.err = QLabel("")
        self.err.setStyleSheet(f"color:{T('err')};")
        lay.addWidget(self.err)
        bb = QHBoxLayout()
        bb.addStretch()
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("生成主订单"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self._ok)
        bb.addWidget(cancel)
        bb.addWidget(ok)
        lay.addLayout(bb)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, tr("选择比价根目录"), self.root_ed.text() or "")
        if d:
            self.root_ed.setText(os.path.normpath(d))

    def root(self):
        return self.root_ed.text().strip()

    def _paths(self):
        """每家门店会建在哪里（根目录改了跟着变）"""
        root = self.root()
        for sid, (pl, n) in self.path_lbls.items():
            if not n:
                pl.setText(tr("没有要订的，不生成"))
                pl.setStyleSheet(f"color:{self.oc.ui.T('faint')};")
            elif root:
                pl.setText(R.batch_store_folder(root, sid))
                pl.setStyleSheet("")
            else:
                pl.setText("—")

    def _show_moves(self):
        from store_order_page import TransferDialog
        TransferDialog(self, self.moves, self.oc.ui).exec()

    def folders(self):
        """{门店: 自动建的文件夹}（没有要订的门店不在里面）"""
        root = self.root()
        return {sid: R.batch_store_folder(root, sid) for sid, (_, n) in self.path_lbls.items() if n}

    def _ok(self):
        root = self.root()
        if not root or not os.path.isdir(root):
            self.err.setText(tr("先选比价根目录（已经存在的文件夹，例如 D:\\CasaTe）。"))
            return
        if not self.folders():
            self.err.setText(tr("所有门店都没有要订的商品。"))
            return
        self.accept()
