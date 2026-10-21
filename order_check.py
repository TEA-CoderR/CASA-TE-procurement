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

# 表格列（逻辑顺序）→ 显示顺序：按审核时看一行的顺序
#   哪个商品：门店 判断 等级 名称 | 现在：本店库存 库存够卖 日均 | 门店要多少：清单箱数 清单件数 清单订后够卖 |
#   系统建议：建议件数 | 最后订多少：确定件数 变化（比清单多 / 少几件）确定箱数 确定订后够卖 | 调货 | 原因 |
#   （默认藏起来，「显示哪些列」里可以打开）目标库存 各周期销量 周期 箱规 编号 条码
# 23 = 变化，24 = 调货，25 = 原因（列可以拖动换位置，会记住）
# 2026-10-20 第二版（用户：要简洁、改前改后一眼看出）：
#   「判断」只写结论：不订 / 照清单订 / 多订 N 箱 / 少订 N 箱 / 建议调货（和门店清单比；为什么建议放在鼠标提示里）
#   紧接着「清单 → 确定 → 调货」对比改前改后和调货件数：
#     清单按箱：清单箱数 | 确定箱数 | 确定件数 ✎；清单按件：清单件数 | 确定件数 ✎（箱数列自动藏起来）
# 2026-10-20 第三版：和门店配货的订货明细同一个样子（调货只显示从哪家调进来）：
#   门店 条码 名称 等级 周期 本店库存 日均 库存够卖 每箱件数 | 清单 判断 确定箱数 确定件数 ✎ 调货 | 到货后够卖 原因
_ORDER = [22, 2, 3, 16, 17, 4, 18, 9, 10, 11, 12, 0, 15, 14, 24, 20, 25, 13, 19, 23, 21, 5, 6, 7, 8, 1]
_LAYOUT_VER = 5           # 改了默认顺序就加 1
# 默认只显示审核要看的：门店 判断 名称 | 本店库存 库存够卖 | 清单 → 确定 | 调货 | 确定订后够卖
# （等级、日均、建议件数、变化、原因等在「显示哪些列」里打开）
_DEFAULT_HIDDEN = [12, 13, 19, 23, 21, 5, 6, 7, 8, 1, 3]       # 名称也藏（右边详情栏里有）
CHANGE_COL, TRANSFER_COL, REASON_COL = 23, 24, 25


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
VERDICT_TOKEN = {"transfer": "ok", "more": "accent", "less": "warn", "none": "err", "same": "muted", "nodb": "err"}
# 「看哪些」下拉框
VIEWS = (("all", "全部商品"), ("changed", "只看有变化的"), ("transfer", "只看调货"), ("more", "只看多订"),
         ("less", "只看少订"), ("none", "只看不订"))


class _Sig(QObject):
    done = Signal(object)


class OrderCheck:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cfg = settings.setdefault("reorder", {})
        self.rows = []
        self.meta = {}
        self._moves = []
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

        # 检查用的参数（订货周期、销量周期、算法、数据库）：批发商比价页的「设置」窗口里改
        self.params_box = QWidget()
        pv = QVBoxLayout(self.params_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(8)
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
        pv.addLayout(r2)
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
        self.btn_run.hide()                  # 页面上的「开始」按钮代替它
        pv.addLayout(r3)
        p.lay.addWidget(self.params_box)
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
        tb.setSpacing(8)
        self.view = QComboBox()
        for key, text in VIEWS:
            self.view.addItem(tr(text), key)
        self.view.setToolTip(tr("有变化 = 判断是多订、少订、不订或建议调货"))
        self.view.currentIndexChanged.connect(self._filter)
        tb.addWidget(self.view)
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
                                          "30", "60", "90", "180", tr("库存够卖（天）"), tr("每箱件数"), tr("清单数量"),
                                          tr("清单件数"), tr("建议件数"), tr("确定件数"), tr("确定箱数"), tr("等级"), tr("周期"), tr("日均"),
                                          tr("目标库存"), tr("到货后够卖（天）"), tr("清单订后够卖（天）"), tr("门店"),
                                          tr("变化（件）"), tr("调货"), tr("原因")]),
                                   numeric_cols=[_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20, 21, 23]])
        self.table.setMinimumHeight(380)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setFocusPolicy(Qt.StrongFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, wd in enumerate(_perm((70, 80, 120, 280, 70, 52, 52, 52, 52, 96, 52, 70, 70, 70, 76, 70, 50, 50, 60, 70, 120, 120, 96,
                                      76, 200, 380))):
            self.table.setColumnWidth(c, wd)
        self.table.horizontalHeaderItem(EDIT_COL).setToolTip(tr("双击修改，0 = 不订"))
        self.table.horizontalHeaderItem(_col(10)).setToolTip(
            tr("每箱件数：按 distribuire 里最便宜那家报价的箱规（比价默认挑它）；报价里没有的用数据库箱规。蓝色 = 和数据库不一样"))
        self.table.horizontalHeaderItem(_col(9)).setToolTip(
            tr("库存够卖 = 本店库存 ÷ 日均（账面库存是负数时按 0 算）。门店配货、检查订货清单、批发商比价都是这个算法"))
        self.table.horizontalHeaderItem(AFTER_COL).setToolTip(
            tr("到货后够卖 =（本店库存 + 确定件数 − 调给别的店的）÷ 日均：订的货和调进来的货都到了以后能卖几天（调进来的已经算在确定件数里）；改确定件数时跟着变，超过凑单天数标黄（可能压货）"))
        self.table.horizontalHeaderItem(LIST_AFTER_COL).setToolTip(
            tr("清单订后够卖 =（本店库存 + 门店清单原来的件数）÷ 日均：门店自己报的量订完能卖几天，和「确定订后够卖」对比"))
        self.table.horizontalHeaderItem(_col(18)).setToolTip(
            tr("新算法：加权日均（含季节）；原程序算法：最近一个周期的销量 ÷ 天数"))
        self.table.horizontalHeaderItem(_col(CHANGE_COL)).setToolTip(
            tr("确定件数 − 门店清单的件数：+ 是比门店要的多订，− 是少订"))
        self.table.horizontalHeaderItem(_col(TRANSFER_COL)).setToolTip(
            tr("门店间调货（按现在的确定件数实时算）：从哪家门店调进几件，调进来的就少订"))
        self.table.horizontalHeaderItem(_col(REASON_COL)).setToolTip(
            tr("系统为什么这么建议：库存够卖几天、订货周期里大约卖多少"))
        self.table.itemChanged.connect(self._edited)
        # 点表头排序、右键表头按列筛选；上面「判断」「等级」两个下拉框快速筛选
        from erp_widgets import TableSortFilter, ColumnFilterBox
        self.ctl = TableSortFilter(self.table, lambda: len(self.rows), self._text, self._fill_row,
                                   hide=self._hidden, numeric=tuple(_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20, 21, 23]))
        # 列可以拖动换位置、拖宽度，下次打开照旧（右键表头是筛选，所以「恢复默认列顺序」放在下面）
        from erp_widgets import remember_columns
        self._reset_cols = remember_columns(self.table, self.cfg, "check", _ORDER, lambda: save_settings(self.settings),
                                            _LAYOUT_VER)
        # 「显示哪些列」：默认只放审核要看的，其他的勾选了才显示
        from erp_widgets import ColumnChooser
        self._forced = set()
        self.cols = ColumnChooser(self.table, self.cfg, "check_cols", _ORDER, lambda: save_settings(self.settings),
                                  _DEFAULT_HIDDEN, ver=4, forced=lambda: self._forced)
        self._moves_in, self._moves_out = {}, {}
        self._plan_timer = QTimer()
        self._plan_timer.setSingleShot(True)
        self._plan_timer.setInterval(250)
        self._plan_timer.timeout.connect(self._replan)
        self.ctl.rendered.connect(lambda n, tot: self.count_lbl.setText(
            tr("显示 {0} / {1} 个 · 点表头排序，右键表头筛选，拖动表头换列的位置", n, tot)))
        tb.insertWidget(0, ColumnFilterBox(self.ctl, _col(22), tr("门店")))
        tb.insertWidget(tb.count() - 2, self.cols.button())
        # 表格 + 右边详情栏（和门店配货的订货明细一样：选中一行看为什么）
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        mid.addWidget(self.table, 1)
        mid.addWidget(self._build_drawer())
        bl.addLayout(mid, 1)
        self._cur_i = None
        self._by_key = {}
        self.table.currentCellChanged.connect(lambda r, c, pr, pc: self._select(r))
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
        self.btn_confirm = QPushButton(tr("确认，开始比价"))
        self.btn_confirm.setObjectName("Primary")
        self.btn_confirm.setToolTip(tr("按「确定件数」给每家门店生成主订单，然后自动一家一家比价"))
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
        cb = QPushButton(tr("确认，开始比价"))
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

    def run_lists(self, items):
        """工作文件夹里的门店清单 [(门店编号, 路径)] 放进来，直接开始检查"""
        self.lists = []
        self.add_files([p for _, p in items])
        by_path = {os.path.normcase(os.path.abspath(p)): sid for sid, p in items}
        for x in self.lists:
            x["store"] = by_path.get(os.path.normcase(os.path.abspath(x["path"])), x["store"])
        self._refresh_lists()
        if not self.lists:
            return False
        self.start()
        return True

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
        price_dir = self._price_dir()
        self.running = True
        self.btn_run.setEnabled(False)
        self.msg.setText(tr("正在连接数据库并计算，商品多时要几十秒…"))
        names = "、".join(R.STORES.get(x["store"], "") for x in lists)
        from erp_widgets import TaskDialog
        dlg = TaskDialog(self.win, tr("正在检查订货清单"),
                         tr("{0} · {1} 个商品", names, sum(len(x["rows"]) for x in lists)),
                         [tr("读取清单、对上商品编号"), tr("查询门店销量与库存"), tr("查询五家门店库存（调货用）"),
                          tr("读取供应商报价（箱规、件价）"), tr("对比清单数量")], cancellable=False)
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
                rows = []
                for x in lists:
                    for r in R.check_rows(x["rows"], x["result"], x["unit"]):
                        r["store"] = x["store"]
                        rows.append(r)
                if rows and not any(r.get("in_db") for r in rows):
                    # 一个都没在数据库里找到：几乎一定是数据库刚才没连上或者清单不对，不能拿这个结果去生成主订单
                    raise RuntimeError(tr("清单里的 {0} 个商品在数据库里一个都没找到：可能是数据库刚才没连上，或者清单里的条码 / 商品编号不对。请检查数据库连接后再点一次「开始」。",
                                          len(rows)))
                # 箱规、件价用 distribuire 里最便宜那家的报价（比价默认挑它）；报价里没有的才用数据库的
                dlg.post_step(3, "running", "", "")
                dlg.post_progress(86)
                n_q, n_files = self._apply_quotes(rows, price_dir, log)
                dlg.post_step(3, "done", tr("{0} 份报价", n_files) if n_files else tr("没有报价，用数据库"), "")
                dlg.post_step(4, "running", "", "")
                dlg.post_progress(95)
                dlg.post_finished(True, tr("完成：{0} 个商品", len(rows)))
                self.sig.done.emit((rows, None, dict(stores=[x["store"] for x in lists], days=days, cycle=cycle,
                                                     units={x["store"]: x["unit"] for x in lists},
                                                     files={x["store"]: x["path"] for x in lists}, snap=snap,
                                                     quoted=n_q, quote_files=n_files,
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
        self._by_key = {(x.get("store"), str(x.get("code"))): x for x in rows}
        self._cur_i = None
        d = meta["days"]
        for i in range(4):
            self.ctl.set_title(_col(5 + i), tr("{0}天", d[i]), tr("最近 {0} 天销量", d[i]))
        # 清单按箱：「清单箱数」+ 折合的「清单件数」；清单本来就是件数：只留一列「清单件数」；几份清单单位不一样：「清单数量」
        units = set(meta["units"].values())
        pieces = units == {"pcs"}
        self.ctl.set_title(_col(11), tr("清单件数") if pieces else (tr("清单箱数") if units == {"colli"} else tr("清单数量")))
        # 清单本来就是件数：不显示重复的「清单件数」，也不显示「确定箱数」（清单件数 ↔ 确定件数 直接对比）；
        # 只有一家门店：不显示门店列
        self._forced = ({12, 15} if pieces else set()) | ({22} if len(meta["stores"]) < 2 else set())
        self.cols.apply()
        self.ctl.filters = {}
        self._replan(render=False)
        self._fill()
        self.summary_row.show()
        self.window.setWindowTitle(tr("检查门店订货清单 — {0} · {1} 个商品",
                                      "、".join(store_label(k) for k in meta["stores"]), len(rows)))
        self.window.bring_up()
        self.msg.setText(tr("✔ 门店 {0}：{1} 个商品，检查完成。在检查窗口核对「确定件数」后点「确认，开始比价」。",
                            "、".join(store_label(k) for k in meta["stores"]), len(rows)))

    def _vals(self, i):
        """数据行 i 每一列显示的文字（按屏幕上的列顺序）"""
        x = self.rows[i]
        sales = [f"{v:g}" if isinstance(v, (int, float)) else "" for v in x["sales"]]
        pack = x["pack"] or 0
        daily = x.get("daily") if x.get("daily") is not None else x.get("daily0")
        return _perm([" · ".join([self._verdict(x)[1]] + [m[1] for m in self._marks(x)]), x["code"], x["ean"], x["name"],
                      f"{x['store_inv']:g}" if isinstance(x["store_inv"], (int, float)) else "",
                      *sales, _days(_cover(x)), f"{x['pack']:g}" if x["pack"] else "",
                      f"{x['file_qty']:g}", f"{x['file_pcs']:g}",
                      f"{x['suggested']:,}" if x["in_db"] else "", f"{x['final']:,}",
                      f"{math.ceil(x['final'] / pack):,}" if pack > 0 and x["final"] > 0 else "",
                      x.get("tier") or "",
                      f"{x['cycle']:g}" if isinstance(x.get("cycle"), (int, float)) else "",
                      f"{daily:.2f}" if daily else "",
                      f"{x['target']:.0f}" if isinstance(x.get("target"), (int, float)) else "",
                      _days(self._arrive_days(x)),
                      _days(_cover(x, x["file_pcs"])) if (x.get("file_pcs") or 0) > 0 else "",
                      R.STORES.get(x.get("store"), ""), self._change_text(x), self._transfer_text(x),
                      "；".join(self._reason_lines(x))])

    def _verdict(self, x):
        """(类别, 文字)：确定件数和门店清单比，不订 / 照清单订 / 多订 / 少订 / 建议调货"""
        key = (x.get("store"), str(x.get("code")))
        moved = key in self._moves_in                  # 只算调进来的
        return R.review_verdict(math.ceil(x.get("file_pcs") or 0), x["final"], moved, x.get("in_db", True),
                                x.get("pack") or None, tr("照清单订"))

    def _reason_lines(self, x):
        """为什么：直白的几句话（「原因」列、鼠标提示、右边详情栏共用，和订货明细同一个写法）"""
        code = str(x.get("code"))
        tin = None
        m = self._moves_in.get((x.get("store"), code))
        if m:
            d = ((self.meta.get("snap") or {}).get(m["from"]) or {}).get(code) or {}
            tin = {"name": R.STORES.get(m["from"], m["from"]), "pieces": m["pieces"],
                   "inv": max(0.0, float(d.get("inv") or 0)), "days": _cover({"in_db": True, "inv0": d.get("inv"),
                                                                              "daily0": d.get("daily")}) if d else None,
                   "saved": (m.get("colli_before") or 0) - (m.get("colli_after") or 0)}
        pack = x.get("pack") or 0
        sugg = x.get("suggested") or 0
        lines = R.plain_reason(x.get("inv0"), x.get("daily0"), x.get("cycle"), sugg,
                               boxes=math.ceil(sugg / pack) if pack > 0 and sugg > 0 else None, target=x.get("target"),
                               in_db=x.get("in_db", True), transfer=tin, pack=pack)
        listed = math.ceil(x.get("file_pcs") or 0)
        if x.get("in_db", True) and listed != sugg:
            lines.append(tr("门店清单报了 {0:,} 件", listed))
        if x.get("in_db", True) and x["final"] != sugg:
            lines.append(tr("现在确定 {0:,} 件（手动改过）", x["final"]))
        return lines

    def _marks(self, x):
        """判断后面的提醒（只提醒）：新算法订了一箱要卖半年以上 → 慢销；库存是负数 → 库存不准"""
        if not x.get("in_db", True):
            return []
        return R.review_marks(x.get("target") is not None, x["final"] > 0, self._arrive_days(x), x.get("store_inv"))

    def _arrive_days(self, x):
        """到货后够卖：(库存 + 确定件数 − 调给别的店的) ÷ 日均（调进来的已经算在确定件数里）；不订也不调出 → None"""
        out = sum(t["pieces"] for t in self._moves_out.get((x.get("store"), str(x.get("code"))), []))
        if x["final"] <= 0 and not out:
            return None
        return _cover(x, x["final"] - out)

    def _change_text(self, x):
        """确定件数比门店清单多 / 少几件（一样就空着，表格干净）"""
        d = int(x["final"]) - int(math.ceil(x.get("file_pcs") or 0))
        return f"{d:+,}" if d else ""

    def _transfer_text(self, x):
        key = (x.get("store"), str(x.get("code")))
        m = self._moves_in.get(key)
        if m:                                          # 短：← 从哪家 几件（详细的在鼠标提示里）
            return f"← {R.STORES.get(m['from'], m['from'])} {m['pieces']}" + tr("件")
        return ""                                      # 调出去的不显示（审核只要知道从哪调进来）

    def _transfer_tip(self, x):
        key = (x.get("store"), str(x.get("code")))
        m = self._moves_in.get(key)
        if m:
            return tr("调进 {0} 件 ← {1}，还订 {2} 件", m["pieces"], R.STORES.get(m["from"], m["from"]),
                      max(0, int(x["final"]) - int(m["pieces"])))
        return ""

    # ---------- 右边详情栏（和订货明细一致）----------
    def _build_drawer(self):
        from PySide6.QtWidgets import QScrollArea, QGridLayout
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFixedWidth(330)
        sa.setFrameShape(QFrame.NoFrame)
        d = QWidget()
        d.setObjectName("Drawer")
        v = QVBoxLayout(d)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        self.d_over = QLabel("")
        self.d_over.setObjectName("Faint")
        self.d_title = QLabel(tr("选中一行查看详情"))
        self.d_title.setStyleSheet("font-size:16px; font-weight:700;")
        self.d_title.setWordWrap(True)
        self.d_codes = QLabel("")
        self.d_codes.setObjectName("Faint")
        self.d_codes.setWordWrap(True)
        for w in (self.d_over, self.d_title, self.d_codes):
            v.addWidget(w)
        g = QGridLayout()
        g.setSpacing(8)
        self.minis = []
        for i, lab in enumerate((tr("本店库存"), tr("日均"), tr("门店清单"), tr("确定"))):
            f = QFrame()
            f.setObjectName("MiniCard")
            fv = QVBoxLayout(f)
            fv.setContentsMargins(10, 8, 10, 8)
            fv.setSpacing(1)
            a = QLabel(lab)
            a.setObjectName("MiniLabel")
            b = QLabel("—")
            b.setObjectName("MiniValue")
            c = QLabel("")
            c.setObjectName("MiniLabel")
            for w in (a, b, c):
                fv.addWidget(w)
            g.addWidget(f, i // 2, i % 2)
            self.minis.append((b, c))
        v.addLayout(g)

        def card(title):
            f = QFrame()
            f.setObjectName("MiniCard")
            fv = QVBoxLayout(f)
            fv.setContentsMargins(12, 10, 12, 10)
            t = QLabel(title)
            t.setStyleSheet("font-weight:600; font-size:12px;")
            body = QLabel("")
            body.setWordWrap(True)
            fv.addWidget(t)
            fv.addWidget(body)
            v.addWidget(f)
            return t, body
        _t, self.d_sales = card(tr("最近销量"))
        self.why_t, self.why = card(tr("为什么这么建议"))
        _t, self.d_others = card(tr("其他门店"))
        v.addStretch()
        sa.setWidget(d)
        return sa

    def _select(self, r):
        i = self.ctl.row_at(r) if r is not None and r >= 0 else None
        if i is None:
            return
        self._cur_i = i
        self._show_drawer(i)

    def _show_drawer(self, i):
        if not (0 <= i < len(self.rows)):
            return
        x = self.rows[i]
        code = str(x.get("code"))
        sid = x.get("store")
        self.d_over.setText(f"{R.STORES.get(sid, '')} · {code}")
        self.d_title.setText(x.get("name") or "")
        pack = x.get("pack") or 0
        try:
            price = float(x.get("price") or 0)
            price = price if price == price else 0
        except (TypeError, ValueError):
            price = 0
        src = tr("（{0} 报价）", x["quote_sup"]) if x.get("quote_sup") else (tr("（数据库）") if pack else "")
        self.d_codes.setText(" · ".join(p for p in (x.get("ean") or "", tr("{0:g} 件/箱", pack) + src if pack else "",
                                                     f"€{price:.3f}/" + tr("件") if price else "") if p))
        (a1, a2), (b1, b2), (c1, c2), (d1, d2) = self.minis
        inv = x.get("store_inv")
        if isinstance(inv, (int, float)):
            a1.setText(f"{inv:g}")
            a1.setStyleSheet(f"color:{self.ui.T('err')};" if inv < 0 else "")
            a2.setText(tr("计算时按 0") if inv < 0 else tr("够卖 {0} 天", _days(_cover(x))))
        else:
            a1.setText("—")
            a2.setText("")
        daily = x.get("daily0")
        b1.setText(f"{daily:.2f}" if isinstance(daily, (int, float)) else "—")
        b2.setText(tr("件 / 天"))
        listed = math.ceil(x.get("file_pcs") or 0)
        c1.setText(f"{listed:,}")
        c2.setText(tr("件") + (f" · {listed / pack:g} " + tr("箱") if pack else ""))
        d1.setText(f"{x['final']:,}")
        d2.setText(tr("件") + (f" · {math.ceil(x['final'] / pack):g} " + tr("箱") if pack and x["final"] else ""))
        days = self.meta.get("days") or []
        self.d_sales.setText("\n".join(tr("最近 {0} 天卖了 {1:g} 件", dd, v) for dd, v in zip(days, x.get("sales") or [])
                                       if isinstance(v, (int, float))) or tr("没有销量数据"))
        sugg = x.get("suggested") or 0
        if not x.get("in_db", True):
            self.why_t.setText(tr("为什么这么建议"))
        elif sugg > 0:
            self.why_t.setText(tr("为什么建议 {0:,} 件", sugg))
        else:
            self.why_t.setText(tr("为什么不用订"))
        self.why.setText("\n".join(["· " + l for l in self._reason_lines(x)] + ["⚠ " + m[2] for m in self._marks(x)]))
        # 其他门店：库存、够卖几天、这次清单里定了多少
        lines = []
        snap = self.meta.get("snap") or {}
        for osid, items in snap.items():
            if osid == sid:
                continue
            d = (items or {}).get(code)
            if not d:
                continue
            ox = self._by_key.get((osid, code))
            v = _cover({"in_db": True, "inv0": d.get("inv"), "daily0": d.get("daily")})
            line = tr("{0}　库存 {1:g} · 够卖 {2} 天", R.STORES.get(osid, osid), float(d.get("inv") or 0), _days(v) or "—")
            if ox:
                line += tr(" · 定 {0:,} 件", ox["final"])
            lines.append(line)
        self.d_others.setText("\n".join(lines) or tr("没有其他门店的数据"))

    def _replan(self, render=True):
        """按现在的确定件数重新算门店间调货（改了确定件数以后稍等一下再算，不卡）"""
        self._moves_in, self._moves_out = {}, {}
        self._moves = []
        if self.rows and self.meta.get("snap"):
            try:
                self._moves, _ = self.transfer_plan()
            except Exception as e:
                self.win.log(tr("⚠ 调货计算失败：{0}", e))
        for m in self._moves:
            self._moves_in[(m["to"], str(m["code"]))] = m
            self._moves_out.setdefault((m["from"], str(m["code"])), []).append(m)
        if render:
            self.ctl.render()
            self._summary()
            if self._cur_i is not None:
                self._show_drawer(self._cur_i)

    def _text(self, i, c):
        return self._vals(i)[c]

    def _hidden(self, i):
        x = self.rows[i]
        text = self.flt.text().strip().lower()
        view = self.view.currentData()
        kind = self._verdict(x)[0]
        hide = (view == "changed" and kind not in ("transfer", "more", "less", "none")) or \
            (view in ("transfer", "more", "less", "none") and kind != view)
        return hide or \
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
        tip = short_decision(x["decision"]) if x["in_db"] else tr("数据库里找不到这个商品，箱规未知；确定件数默认 = 清单数量，请核对")
        t.item(k, 0).setToolTip(tip)
        t.item(k, 0).setData(Qt.UserRole, i)
        t.item(k, _col(3)).setToolTip(f"{x['name']}\nEAN {x['ean'] or '—'}")
        if isinstance(x.get("store_inv"), (int, float)) and x["store_inv"] < 0:
            for c_ in (_col(4), _col(9)):
                t.item(k, c_).setForeground(QColor(T("warn")))
                t.item(k, c_).setToolTip(tr("账面库存是负数（{0:g}），通常是卖了还没入库。算够卖天数时按 0 算", x["store_inv"]))
        pk = t.item(k, _col(10))
        if x.get("quote_sup"):
            same = not x.get("db_pack") or abs(x["pack"] - x["db_pack"]) < 1e-9
            pk.setToolTip(tr("{0} 的报价：每箱 {1:g} 件", x["quote_sup"], x["pack"]) +
                          ("" if same else "\n" + tr("数据库箱规是每箱 {0:g} 件", x["db_pack"])))
            if not same:
                pk.setForeground(QColor(T("accent")))
        elif x.get("pack"):
            pk.setToolTip(tr("报价里没有这个商品，用数据库箱规"))
        ch = t.item(k, _col(CHANGE_COL))
        d = int(x["final"]) - int(math.ceil(x.get("file_pcs") or 0))
        ch.setForeground(QColor(T("accent") if d > 0 else T("warn") if d < 0 else T("faint")))
        if d:
            f = ch.font()
            f.setBold(True)
            ch.setFont(f)
        tri = t.item(k, _col(TRANSFER_COL))
        if tri.text():
            tri.setForeground(QColor(T("ok") if (x.get("store"), str(x.get("code"))) in self._moves_in else T("accent")))
            tri.setToolTip(self._transfer_tip(x))
        reason = "\n".join(self._reason_lines(x) + ["⚠ " + m[2] for m in self._marks(x)])
        t.item(k, _col(REASON_COL)).setToolTip(reason)
        t.item(k, _col(REASON_COL)).setForeground(QColor(T("muted")))
        t.item(k, 0).setToolTip(reason)          # 鼠标停在「判断」上也能看为什么
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
        # 判断：跟着确定件数变
        kind, vt = self._verdict(x)
        marks = self._marks(x)
        vc = self.table.item(k, 0)
        vc.setText(" · ".join([vt] + [m[1] for m in marks]))
        vc.setForeground(QColor(T("warn") if (marks and kind in ("same", "more")) else T(VERDICT_TOKEN.get(kind, "text"))))
        f = vc.font()
        f.setBold(kind in ("transfer", "more", "less", "none"))
        vc.setFont(f)
        c = self.table.item(k, EDIT_COL)
        c.setText(f"{x['final']:,}")
        f = c.font()
        f.setBold(x["final"] > 0)
        c.setFont(f)
        changed = x["in_db"] and x["final"] != x["suggested"]
        c.setForeground(QColor(T("warn") if changed else T("accent") if x["final"] > 0 else T("faint")))
        c.setBackground(QColor(T("warn_soft")) if changed else QColor(0, 0, 0, 0))
        c.setToolTip(tr("手动改过：系统建议 {0:,} 件", x["suggested"]) if changed else tr("双击修改，0 = 不订"))
        pack = x["pack"] or 0
        self.table.item(k, BOX_COL).setText(
            f"{math.ceil(x['final'] / pack):,}" if pack > 0 and x["final"] > 0 else "")
        # 订后够卖：跟着确定件数变；超过凑单天数标黄
        a = self.table.item(k, AFTER_COL)
        v = self._arrive_days(x)
        a.setText(_days(v))
        limit = self._limit()
        over = v is not None and v > limit
        a.setForeground(QColor(T("warn") if over else T("text")))
        a.setToolTip(tr("订完要很久才卖得完（超过 {0} 天），可能压货", int(limit)) if over else "")
        ch = self.table.item(k, _col(CHANGE_COL))
        if ch is not None:
            d = int(x["final"]) - int(math.ceil(x.get("file_pcs") or 0))
            ch.setText(self._change_text(x))
            ch.setForeground(QColor(T("accent") if d > 0 else T("warn") if d < 0 else T("faint")))

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
        if i == self._cur_i:
            self._show_drawer(i)

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
        QTimer.singleShot(0, lambda: (self._show(i), self._summary(), self._plan_timer.start()))

    def _set_all(self, which):
        for x in self.rows:
            if which == "sugg":
                x["final"] = x["suggested"] if x["in_db"] else int(math.ceil(x["file_pcs"]))
            else:
                x["final"] = int(math.ceil(x["file_pcs"]))
        self._replan()

    def _filter(self, *_):
        if hasattr(self, "ctl"):
            self.ctl.render()
        self._summary()

    def _summary(self):
        cnt = {v: 0 for v in R.VERDICT_RANK}
        for x in self.rows:
            k_ = self._verdict(x)[0]
            cnt[k_] = cnt.get(k_, 0) + 1
        n_edit = sum(1 for x in self.rows if x["in_db"] and x["final"] != x["suggested"])
        txt = tr("{0} 个商品：照清单订 {1}，多订 {2}，少订 {3}，不订 {4}，建议调货 {5}，数据库没有 {6}。手动改了 {7} 个。",
                 len(self.rows), cnt["same"], cnt["more"], cnt["less"], cnt["none"], cnt["transfer"], cnt["nodb"], n_edit)
        # 采购金额估算：最便宜那家报价的件价（没有报价的用数据库进价 PrezzoForn）× 件数；实际金额以「开始处理」后的比价为准
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
            txt += "\n" + tr("估算采购金额（有报价的按最便宜那家，没有的按数据库进价）：清单 €{0:,.2f} → 确定 €{1:,.2f}",
                             amt_file, amt_final)
            if no_price:
                txt += tr("（{0} 个商品没有进价，没算进去）", no_price)
        mv = getattr(self, "_moves", None) or []
        if mv:
            txt += "\n" + tr("门店间调货（按现在的确定件数）：{0} 个商品，少订 {1} 箱，货值 €{2:,.2f}", len(mv),
                             sum(m["colli_before"] - m["colli_after"] for m in mv), sum(m["value"] for m in mv))
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
                   "清单数量", "清单件数", "清单订后够卖（天）", "建议件数", "确定件数", "变化（件）", "确定订后够卖（天）",
                   "调货", "原因", "系统说明"])
        for c in ws[1]:
            c.font = Font(bold=True)
        def num(v):
            return None if v is None else ("∞" if math.isinf(v) else round(v))

        for x in self.rows:
            ws.append([R.STORES.get(x.get("store"), ""), self._verdict(x)[1], x["code"], x["ean"], x["name"], x["store_inv"],
                       *x["sales"], num(_cover(x)),
                       x["pack"] or None, x["file_qty"], x["file_pcs"],
                       num(_cover(x, x["file_pcs"])) if (x.get("file_pcs") or 0) > 0 else None,
                       x["suggested"] if x["in_db"] else None, x["final"], self._change_text(x),
                       num(self._arrive_days(x)), self._transfer_tip(x),
                       R.reason_text(x.get("inv0"), x.get("daily0"), x.get("cycle"), x.get("suggested"), x.get("target"),
                                     x.get("in_db", True)), x["decision"]])
            ws.cell(ws.max_row, 4).number_format = "@"
        ws.freeze_panes = "A2"
        try:
            wb.save(path)
            self.win.show_toast(tr("已导出"))
        except Exception as e:
            QMessageBox.critical(self.win, tr("导出"), tr("导出失败：{0}", e))

    def _price_dir(self):
        """共用的供应商报价文件夹：比价根目录（或现在的总文件夹）下面的 distribuire"""
        import workspace as W
        root = self.win.ws_root() if hasattr(self.win, "ws_root") else self.batch_root()
        return W.price_dir(root) if root else ""

    def _apply_quotes(self, rows, price_dir, log):
        """读 price_dir 里的报价，检查清单的行改用最便宜那家的每箱件数和件价：(改了几行, 读了几份报价)"""
        if not price_dir or not os.path.isdir(price_dir):
            log(tr("[检查清单] 没找到供应商报价文件夹，箱规和件价用数据库的"))
            return 0, 0
        import service as SV
        import price_check as PC
        import supplier_names as SN
        import workspace as W
        files = {}
        for f in sorted(os.listdir(price_dir)):
            low = f.lower()
            if not low.endswith((".xlsx", ".xls")) or f.startswith(("~", "$")) or not W.is_price_name(f):
                continue
            try:
                name = SN.label_of(f, self.settings)
                files[name if name not in files else f"{name} ({SN.stem(f)})"] = SV.read_price_file(os.path.join(price_dir, f))
            except Exception as e:
                log(tr("⚠ 报价文件读不了，跳过：{0}（{1}）", f, e))
        if not files:
            log(tr("[检查清单] 供应商报价文件夹里没有报价，箱规和件价用数据库的"))
            return 0, 0
        n = PC.apply_quotes(rows, PC.best_quotes(files))
        diff = sum(1 for x in rows if x.get("quote_sup") and x.get("db_pack") and abs(x["pack"] - x["db_pack"]) > 1e-9)
        log(tr("[检查清单] {0} 份报价：{1} 个商品改用最便宜那家的箱规和件价，其中 {2} 个箱规和数据库不一样",
               len(files), n, diff))
        return n, len(files)

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
        import transfers as TR
        so = self.settings.get("store_orders") or {}
        far = TR.excluded_stores(so.get("transfer"), (dict(SO.DEFAULT_FILL, **(so.get("fill") or {}))).get("far_stores"))
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
        import workspace as W
        moves, new_final = self.transfer_plan()
        # 文件夹不用选：工作文件夹\4 比价结果\今天\门店（没有要订的门店不建）
        root_dir = self.win.ws_root() if hasattr(self.win, "ws_root") else self.batch_root()
        if not root_dir:
            QMessageBox.warning(self.win, tr("确认，开始比价"), tr("还没有工作文件夹。"))
            return
        folders = {sid: R.batch_store_folder(root_dir, sid) for sid in self.meta["stores"] if self._to_order(sid)}
        made, problems = [], []
        tag = datetime.now().strftime("%Y%m%d_%H%M")
        day_dir = R.batch_folder(root_dir)
        os.makedirs(day_dir, exist_ok=True)
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
                self.win.sync_prices(root)             # 共用的供应商报价（工作文件夹\\2 供应商报价）复制一份过来
            try:
                n_el = W.copy_elim(root_dir, sid, root)  # 这家店的扣减文件复制进它的 eliminare
                if n_el:
                    self.win.log(tr("[文件] {0}：扣减文件 {1} 个", R.STORES.get(sid), n_el))
            except Exception as e:
                problems.append(tr("{0}：扣减文件没复制过去：{1}", R.STORES.get(sid), e))
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
            if True:                                   # 一定要存（没查到其他门店库存也存空的）：没有快照的主订单会被当成旧系统文件
                try:
                    R.save_snapshot(path, sid, self.meta.get("snap") or {}, rows, transfers=mine, transfers_file=tf)
                except Exception as e:
                    problems.append(tr("{0}：门店库存快照没存上：{1}", R.STORES.get(sid), e))
            self.win.log(tr("✔ 采购主订单：{0}（{1} 个商品）", path, written))
            if skipped:
                self.win.log(tr("⚠ {0} 个商品没有条码，没写进主订单：{1}", len(skipped),
                                "、".join(f"{x['code']} {x['name'][:20]}" for x in skipped[:10])))
            made.append((sid, root, written, len(skipped)))
        if not made:
            if problems:
                QMessageBox.critical(self.win, tr("确认，开始比价"), "\n".join(problems))
            else:
                QMessageBox.information(self.win, tr("确认，开始比价"), tr("这几家门店这次都不用订货，不用比价。"))
            return
        if moves:
            self.win.log(tr("✔ 门店间调货：{0} 个商品，少订 {1} 箱", len(moves),
                            sum(m["colli_before"] - m["colli_after"] for m in moves)))
            if all_tf:
                self.win.log(tr("✔ 调货单（全部门店）：{0}", all_tf))
        # 这一批是哪几家门店（批发商比价页上能一家一家看结果）
        self.settings["wholesale_batch"] = {"time": datetime.now().isoformat(timespec="minutes"),
                                            "stores": {str(sid): root for sid, root, _, _ in made}}
        save_settings(self.settings)
        # 用过的扣减文件挪进 _已用（下次不会再扣一遍；今天再比价用的是比价文件夹里那份）
        try:
            W.archive_used(root_dir, [sid for sid, _, _, _ in made])     # 门店清单留着：想重新比价直接再点「开始」
        except Exception as e:
            problems.append(tr("用过的清单 / 扣减文件没挪走：{0}", e))
        msg = tr("已生成 {0} 家门店的主订单（{1}）", len(made), "、".join(
            f"{R.STORES.get(sid)} {n}" for sid, _, n, _ in made))
        if moves:
            msg += "；" + tr("门店间调货 {0} 个商品，少订 {1} 箱，调货单在 {2}", len(moves),
                            sum(m["colli_before"] - m["colli_after"] for m in moves), day_dir)
        if problems:
            msg += "\n⚠ " + "；".join(problems)
        self.win.log(msg)
        self.lists = []
        self._refresh_lists()
        self.summary_row.hide()
        # 检查窗口关掉，回到批发商比价，自动一家一家比价
        self.window.close()
        if self.win.isMinimized():
            self.win.showNormal()
        self.win.raise_()
        self.win.activateWindow()
        if hasattr(self.win, "go_page"):
            self.win.go_page("order")
        self.win.run_batch([root for _, root, _, _ in made], msg, bool(problems))
