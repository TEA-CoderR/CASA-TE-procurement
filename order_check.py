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
import time
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QLineEdit, QComboBox, QHBoxLayout, QVBoxLayout,
                               QFileDialog, QMessageBox, QSpinBox, QAbstractItemView, QHeaderView, QDialog,
                               QGridLayout, QFrame, QStyledItemDelegate, QAbstractSpinBox, QButtonGroup, QMenu,
                               QSizePolicy)

from i18n import tr
from settings import save_settings
import reorder_service as R
import transfers as TR
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
# 2026-10-21 加了 26 Codice Fornitore、27 供应商（数据库登记的），默认放在条码后面
# 2026-10-21 第五版：按箱审核（用户：件数看着乱，少订几件不改变订的箱数没意义）：清单箱数 → 判断 → 确定箱数 ✎ → 调货；
#   件数列（清单件数、建议件数、确定件数）默认收起来；数据库没有 / 没有箱规的商品放在单独的标签页
# 2026-10-21 加了 28 月均（最长周期销量 ÷ 月数，和订货明细一样），放在日均后面
# 2026-10-21 第六版：用户自己排好的顺序做默认：条码 供应商 Codice 名称 等级 周期 本店库存 日均 月均 够卖 30/60/90/180 天
#   每箱件数 清单箱数 判断 确定箱数 ✎ 确定件数 调货 到货后够卖（其余收起来）
_ORDER = [22, 2, 27, 26, 3, 16, 17, 4, 18, 28, 9, 5, 6, 7, 8, 10, 11, 0, 15, 14, 24, 20, 25, 12, 13, 19, 23, 21, 1]
# 2026-10-21 第四版：窗口和订货明细一个样子（门店按钮、一行工具条、状态栏），名称显示、供应商和原因收起来（详情栏里有）：
#   门店 条码 Codice 名称 等级 周期 本店库存 日均 库存够卖 每箱件数 | 清单 判断 确定箱数 确定件数 ✎ 调货 ✎ | 到货后够卖
_LAYOUT_VER = 8           # 改了默认顺序就加 1
_DEFAULT_HIDDEN = [1, 12, 13, 19, 21, 23, 25]
_HIDDEN_VER = 6           # 改了默认藏哪些列就加 1（settings.COMPANY_DEFAULTS 里的 check_cols_hidden 一起改）
CHANGE_COL, TRANSFER_COL, REASON_COL = 23, 24, 25


def _col(k):
    return _ORDER.index(k)


def _perm(vals):
    return [vals[k] for k in _ORDER]


EDIT_COL = _col(15)           # 确定箱数 ✎（按箱审核）
PCS_COL = _col(14)            # 确定件数（= 箱数 × 每箱件数，默认收起来）


def _pk(x):
    try:
        v = float(x.get("pack") or 0)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v and v > 0 else 0.0


def _problem(x):
    """
    要单独核对的商品：「nodb」数据库里没有、供应商报价里也找不到；「nopack」数据库有、但数据库和报价里都没有箱规。
    数据库没有但报价里找得到的（补上了箱规、件价）留在主表格。其他返回 None
    """
    if not x.get("in_db", True):
        return None if (x.get("quote_sup") and _pk(x) > 0) else "nodb"
    if _pk(x) <= 0:
        return "nopack"
    return None


def _can_transfer(x):
    """能算调货的：数据库里有（有其他门店的库存、销量）而且知道每箱几件"""
    return bool(x.get("in_db", True)) and _pk(x) > 0


def _set_box(x, boxes):
    """确定箱数 → fbox；final = 件数（箱数 × 每箱件数；没有箱规的就是箱数）"""
    b = max(0, int(boxes or 0))
    x["fbox"] = b
    pk = _pk(x)
    x["final"] = int(round(b * pk)) if pk > 0 else b


def is_new_item(x):
    """
    新品保护：180 天里从没卖过，或者 180 天的销量全是最近 60 天卖的（两个月前还没开始卖）→ 新品 / 刚补上货。
    这种商品销量数据还说明不了什么，按门店清单订
    """
    s = x.get("sales") or []
    if len(s) < 4 or not all(isinstance(v, (int, float)) for v in s[:4]):
        return False
    return float(s[3]) <= 0 or float(s[3]) - float(s[1]) <= 0


def init_boxes(rows, units=None, algo=None):
    """
    检查结果按箱算（2026-10-25 用户定）：门店清单的数量一律是箱（lbox）；
      sys_box 系统算出来的箱数；sbox 建议箱数 = 系统算的，但**不多于门店清单**（先不做多订）；
      系统算出来要订（哪怕不到一箱）就至少 1 箱，不因为凑不成整箱砍成 0；
      新品（is_new_item）按门店清单订（protect = "new"）；fbox 确定箱数默认 = 建议；数据库没有 / 没有箱规的 = 清单。
    units 不再用（以前分箱 / 件）；algo：新算法参数（凑整箱用 forecast.to_packs）
    """
    import forecast as F
    v2 = bool(algo) and algo.get("engine") == "v2"
    for x in rows:
        pk = _pk(x)
        x["lbox"] = int(math.ceil(float(x.get("file_qty") or 0) - 1e-9))
        x.pop("protect", None)
        if not (x.get("in_db", True) and pk > 0):
            x["sbox"] = x["sys_box"] = None
            _set_box(x, x["lbox"])
            continue
        sugg = float(x.get("suggested") or 0)
        if v2 and x.get("daily") is not None:
            sys_ = F.to_packs(sugg, pk, float(x.get("inv0") or 0), float(x.get("daily") or 0), algo,
                              tier=x.get("tier") or "C", cycle=x.get("cycle"))
        else:
            sys_ = int(math.ceil(sugg / pk - 1e-9))
        if sugg > 0 and sys_ <= 0:
            sys_ = 1                                   # 系统也说要订：至少 1 箱
        if sys_ <= 0 and float(x.get("inv0") or 0) <= 0 and x["lbox"] > 0:
            sys_ = 1                                   # 店里已经没货、门店又点名要：至少 1 箱（缺货保护）
            x["protect"] = "empty"
        x["sys_box"] = sys_
        if is_new_item(x):
            x["protect"] = "new"
            x["sbox"] = x["lbox"]
        else:
            x["sbox"] = min(sys_, x["lbox"])           # 先不多订：最多订到门店清单的箱数
        _set_box(x, x["sbox"])
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
        p.setToolTip(tr("比价用的主订单只从这里生成：把一家或几家门店的订货清单一起拖进来，按销量和数据库库存检查数量（增加 / 减少 / 不订），同时查五家门店的库存。确认时先用别的门店多出来的货（门店间调货），每家店剩下要订的各自生成主订单；再一家一家点下面的「开始处理」做供应商比价。"))

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
        self.list_table = ui.make_table([tr("门店"), tr("清单文件"), tr("商品数"), ""], stretch_col=1,
                                        numeric_cols=(2,))
        self.list_table.horizontalHeaderItem(2).setToolTip(tr("门店清单的数量一律按箱"))
        self.list_table.verticalHeader().setDefaultSectionSize(36)
        self.list_table.setColumnWidth(0, 170)
        self.list_table.setColumnWidth(2, 70)
        self.list_table.setColumnWidth(3, 70)
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
        self.use_cfg.setToolTip(tr("打开：配置文件里单独设了订货周期的供应商用它自己的周期，其余用默认周期（新算法 = 算法设置里的）。\n关闭：全部用默认周期。"))
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
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        # ---- 工具条（一行，和门店配货的订货明细一样）----
        # 门店（按钮）| 搜索 | 看哪些（下拉，带个数）……  恢复建议值 | 更多 ▾（用清单数量、显示哪些列、列顺序、导出）
        bar = QFrame()
        bar.setObjectName("TopBar")
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(16, 8, 16, 8)
        tb.setSpacing(8)
        self.chip_lay = QHBoxLayout()          # 门店按钮：检查完按这次的门店建
        self.chip_lay.setSpacing(8)
        tb.addLayout(self.chip_lay)
        self.store_group = QButtonGroup(bar)
        self.store_group.setExclusive(True)
        self.store_group.buttonClicked.connect(lambda _b: self._store_changed())
        tb.addSpacing(6)
        # 供应商（数据库登记的）：和订货明细的供应商下拉框一样
        self.f_sup = QComboBox()
        self.f_sup.setMinimumWidth(130)
        self.f_sup.setMaximumWidth(240)
        self.f_sup.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.f_sup.setMinimumContentsLength(10)
        self.f_sup.addItem(tr("全部供应商"), None)
        self.f_sup.currentIndexChanged.connect(self._filter)
        tb.addWidget(self.f_sup)
        self.flt = QLineEdit()
        self.flt.setPlaceholderText(tr("编号、条码或名称"))
        self.flt.setMinimumWidth(90)
        self.flt.setMaximumWidth(200)
        self.flt.textChanged.connect(self._filter)
        tb.addWidget(self.flt)
        self.view = QComboBox()
        for key, text in VIEWS:
            self.view.addItem(tr(text), key)
        self.view.setToolTip(tr("有变化 = 判断是多订、少订、不订或建议调货"))
        self.view.currentIndexChanged.connect(self._filter)
        tb.addWidget(self.view)
        # 「全部商品」右边：数据库没有 / 没有箱规（供应商报价里也找不到的）两个按钮，点开是列表，在里面核对、改箱数
        self.prob = {}
        self.prob_btns = {}
        self._prob_filling = False
        for kind, title, tip in (("nodb", tr("数据库没有"), tr("数据库里找不到、供应商报价里也没有的商品：系统算不了，确定箱数默认 = 门店清单，请核对")),
                                 ("nopack", tr("没有箱规"), tr("数据库里有，但数据库和供应商报价里都不知道每箱几件：确定箱数默认 = 门店清单，请核对"))):
            b = QPushButton("⚠ " + title)
            b.setToolTip(tip + "\n" + tr("点一下打开列表"))
            b.setStyleSheet(f"color:{ui.T('warn')};")
            b.clicked.connect(lambda _=False, k=kind: self._open_prob(k))
            b.hide()
            tb.addWidget(b)
            self.prob_btns[kind] = b
            dlg = QDialog(self.window)
            dlg.setWindowTitle(title)
            dlg.resize(820, 460)
            dv = QVBoxLayout(dlg)
            dv.setContentsMargins(16, 14, 16, 12)
            dv.setSpacing(8)
            lb = QLabel(tip)
            lb.setObjectName("Muted")
            lb.setWordWrap(True)
            t = ui.make_table([tr("门店"), tr("条码"), tr("名称"), tr("供应商"), tr("清单箱数"), tr("确定箱数") + " ✎"],
                              stretch_col=2, numeric_cols=(4, 5))
            t.verticalHeader().setDefaultSectionSize(30)
            t.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                              QAbstractItemView.AnyKeyPressed)
            for c, wd in ((0, 90), (1, 130), (3, 140), (4, 76), (5, 90)):
                t.setColumnWidth(c, wd)
            t.itemChanged.connect(lambda it, k=kind: self._prob_edited(k, it))
            close = QPushButton(tr("关闭"))
            close.clicked.connect(dlg.accept)
            bb = QHBoxLayout()
            bb.addStretch()
            bb.addWidget(close)
            dv.addWidget(lb)
            dv.addWidget(t, 1)
            dv.addLayout(bb)
            self.prob[kind] = (dlg, b, t)
        tb.addStretch()
        b_s = QPushButton(tr("恢复建议值"))
        b_s.setToolTip(tr("全部商品的确定件数恢复成系统建议的（人工改的调货也恢复）"))
        b_s.clicked.connect(lambda: self._set_all("sugg"))
        tb.addWidget(b_s)
        more = QPushButton(tr("更多") + " ▾")
        more.setToolTip(tr("全部用清单数量、显示哪些列、导出 Excel"))
        more.clicked.connect(lambda: self._more_menu(more))
        tb.addWidget(more)
        bl.addWidget(bar)
        self.table = ui.make_table(_perm([tr("判断"), tr("商品编号"), tr("条码"), tr("名称"), tr("本店库存"),
                                          "30", "60", "90", "180", tr("库存够卖（天）"), tr("每箱件数"), tr("清单数量"),
                                          tr("清单件数"), tr("建议件数"), tr("确定件数"), tr("确定箱数"), tr("等级"), tr("周期"), tr("日均"),
                                          tr("目标库存"), tr("到货后够卖（天）"), tr("清单订后够卖（天）"), tr("门店"),
                                          tr("变化（箱）"), tr("调货"), tr("原因"), "Codice Fornitore", tr("供应商"), tr("月均")]),
                                   numeric_cols=[_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20, 21, 23, 28]])
        self.table.setMinimumHeight(380)
        import product_images as PI
        self.table.verticalHeader().setDefaultSectionSize(PI.row_height(self.settings))
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setFocusPolicy(Qt.StrongFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        import product_images as PI          # 条码那一格左边放商品小图（只下载看得见的行）
        self.table.setItemDelegateForColumn(_col(2), PI.ThumbDelegate(self.table, _col(2), None, self.settings))
        for c, wd in enumerate(_perm((70, 80, PI.thumb_size(self.settings) + 120, 280, 70, 52, 52, 52, 52, 96, 52, 70, 70, 70, 76, 70, 50, 50, 60, 70, 120, 120, 96,
                                      76, 200, 380, 110, 150, 56))):
            self.table.setColumnWidth(c, wd)
        self.table.horizontalHeaderItem(_col(28)).setToolTip(tr("月均：新算法 = 日均（加权 + 季节）× 30；原程序算法 = 最长周期销量 ÷ 月数（默认 180 天 ÷ 6）"))
        self.table.horizontalHeaderItem(EDIT_COL).setToolTip(tr("双击修改箱数，0 = 不订"))
        self.table.horizontalHeaderItem(EDIT_COL).setText(tr("确定箱数") + " ✎")
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
            tr("门店间调货（按现在的确定件数实时算）：从哪家门店调进几件，调进来的就少订。双击改件数，0 = 不调（比如过季品）"))
        self.table.horizontalHeaderItem(_col(REASON_COL)).setToolTip(
            tr("系统为什么这么建议：库存够卖几天、订货周期里大约卖多少"))
        self.table.itemChanged.connect(self._edited)
        # 点表头排序、右键表头按列筛选；上面「判断」「等级」两个下拉框快速筛选
        from erp_widgets import TableSortFilter, ColumnFilterBox
        self.ctl = TableSortFilter(self.table, lambda: len(self.rows), self._text, self._fill_row,
                                   hide=self._hidden, numeric=tuple(_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20, 21, 23, 28]))
        # 列可以拖动换位置、拖宽度，下次打开照旧（右键表头是筛选，所以「恢复默认列顺序」放在下面）
        from erp_widgets import remember_columns
        self._reset_cols = remember_columns(self.table, self.cfg, "check", _ORDER, lambda: save_settings(self.settings),
                                            _LAYOUT_VER)
        # 「显示哪些列」：默认只放审核要看的，其他的勾选了才显示
        from erp_widgets import ColumnChooser
        self._forced = set()
        self.cols = ColumnChooser(self.table, self.cfg, "check_cols", _ORDER, lambda: save_settings(self.settings),
                                  _DEFAULT_HIDDEN, ver=_HIDDEN_VER, forced=lambda: self._forced)
        self._moves_in, self._moves_out = {}, {}
        self.transfer_ov = {}
        self.transfer_frozen = False
        self.table.cellDoubleClicked.connect(self._edit_transfer)     # 双击「调货」：选调出门店、填件数
        self.table.horizontalHeaderItem(_col(TRANSFER_COL)).setText(tr("调货") + " ✎")
        self._plan_timer = QTimer()
        self._plan_timer.setSingleShot(True)
        self._plan_timer.setInterval(250)
        self._plan_timer.timeout.connect(self._replan)
        self.ctl.rendered.connect(lambda n, tot: self._status(n, tot))
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
        # ---- 状态栏：显示几个 · 改了几个 · 金额 · 调货 ……  确认，开始比价 ----
        sb = QFrame()
        sb.setObjectName("StatusBar")
        sh = QHBoxLayout(sb)
        sh.setContentsMargins(16, 7, 16, 7)
        sh.setSpacing(12)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("StatusText")
        sh.addWidget(self.count_lbl, 1)
        hint = QLabel(tr("双击「确定件数」修改 · Enter 确认 · Esc 取消"))
        hint.setObjectName("StatusText")
        sh.addWidget(hint)
        self.btn_confirm = QPushButton(tr("确认，开始比价"))
        self.btn_confirm.setObjectName("Primary")
        self.btn_confirm.setToolTip(tr("按「确定件数」给每家门店生成主订单，然后自动一家一家比价"))
        self.btn_confirm.clicked.connect(self.confirm)
        sh.addWidget(self.btn_confirm)
        bl.addWidget(sb)
        self._shown = (0, 0)
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
            unit = "colli"                      # 门店清单的数量一律是箱（2026-10-25 用户定）
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
            self.ui.table_set_row(t, i, ["", os.path.basename(x["path"]), str(len(x["info"]["rows"])), ""])
            t.item(i, 1).setToolTip(x["path"])
            cb = QComboBox()
            cb.addItem(tr("选择门店…"), None)
            for k in stores:
                cb.addItem(store_label(k), k)
            cb.setCurrentIndex(max(0, cb.findData(x["store"])))
            cb.currentIndexChanged.connect(lambda _, i=i, cb=cb: self._set_store(i, cb.currentData()))
            t.setCellWidget(i, 0, cb)
            rb = QPushButton(tr("移除"))
            rb.setObjectName("Link")
            rb.clicked.connect(lambda _=False, i=i: self._remove(i))
            t.setCellWidget(i, 3, rb)
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
                t0 = time.time()
                pre_snap = None
                others = [k for k in R.STORES if k != 0]
                if len(lists) == 1 and lists[0]["codes"] and union:
                    # 只有一份清单（平常都是）：这家店和其他门店（调货用）同时查，结果和一家家查一样
                    x0 = lists[0]
                    log(tr("[检查清单] 门店 {0}，{1} 个商品", R.STORES.get(x0["store"], x0["store"]), len(x0["codes"])))
                    every = [x0["store"]] + [k for k in others if k != x0["store"]]

                    def one(sid):
                        return R.compute_suggestions(db, sid, x0["codes"] if sid == x0["store"] else union, days, cycle,
                                                     None, use_supplier_cycles=use_cfg, algo=algo)
                    try:
                        dfs = R.per_store(every, one, lambda k, sid: (
                            dlg.post_step(1, "running", f"{k + 1} / {len(every)}", R.STORES.get(sid, "")),
                            dlg.post_progress(15 + 70 * k / max(1, len(every)))))
                        x0["df"] = dfs[x0["store"]]
                        x0["result"] = R.to_rows(x0["df"], x0["rows"], days)
                        pre_snap = {sid: R.stock_of(dfs[sid], days) for sid in others if sid in dfs}
                    except Exception as e:
                        x0.pop("df", None)
                        log(tr("⚠ 几家门店一起查没成功，改成一家家查：{0}", e))
                for k, x in enumerate(lists):
                    if x.get("df") is not None:
                        continue
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
                snap = pre_snap
                if union and snap is None:
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
                sup_info = R.supplier_info(db, union) if union else {}     # Codice Fornitore + 供应商名（只显示用）
                log(tr("[检查清单] 数据库查询用了 {0:.0f} 秒", time.time() - t0))
                db.engine.dispose()
                rows = []
                for x in lists:
                    for r in R.check_rows(x["rows"], x["result"], x["unit"]):
                        r["store"] = x["store"]
                        rows.append(r)
                R.add_supplier_info(rows, sup_info)
                if rows and not any(r.get("in_db") for r in rows):
                    # 一个都没在数据库里找到：几乎一定是数据库刚才没连上或者清单不对，不能拿这个结果去生成主订单
                    raise RuntimeError(tr("清单里的 {0} 个商品在数据库里一个都没找到：可能是数据库刚才没连上，或者清单里的条码 / 商品编号不对。请检查数据库连接后再点一次「开始」。",
                                          len(rows)))
                # 箱规、件价用 distribuire 里最便宜那家的报价（比价默认挑它）；报价里没有的才用数据库的
                dlg.post_step(3, "running", "", "")
                dlg.post_progress(86)
                t1 = time.time()
                n_q, n_files = self._apply_quotes(rows, price_dir, log)
                if n_files:
                    log(tr("[检查清单] 读报价用了 {0:.0f} 秒", time.time() - t1))
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

    def _algo(self):
        from reorder_page import algo_params
        return algo_params(self.settings)

    def _done(self, payload):
        rows, err, meta = payload
        self.running = False
        self.btn_run.setEnabled(True)
        if err:
            self.msg.setText(tr("计算失败"))
            self.win.notify(tr("检查清单失败：{0}", err))
            return
        init_boxes(rows, None, self._algo())
        self.rows, self.meta = rows, meta
        # 人工改的调货 {(门店, 商品编号): {"from", "pieces", "auto"} 或 0 = 不调}；改过一次就冻结（别的行不再跟着变）
        self.transfer_ov = {}
        self.transfer_frozen = False
        self._by_key = {(x.get("store"), str(x.get("code"))): x for x in rows}
        self._cur_i = None
        d = meta["days"]
        for i in range(4):
            self.ctl.set_title(_col(5 + i), tr("{0}天", d[i]), tr("最近 {0} 天销量", d[i]))
        # 一律按箱看：清单箱数（清单按件的折成箱）→ 确定箱数
        self.ctl.set_title(_col(11), tr("清单箱数"))
        self._fill_prob()
        self._build_chips(meta["stores"])
        self._fill_suppliers()
        self._apply_forced()
        self.ctl.filters = {}
        self._replan(render=False)
        self._fill()
        self.summary_row.show()
        self.window.setWindowTitle(tr("检查门店订货清单 — {0} · {1} 个商品",
                                      "、".join(store_label(k) for k in meta["stores"]), len(rows)))
        self.window.bring_up()
        self.msg.setText(tr("✔ 门店 {0}：{1} 个商品，检查完成。在检查窗口核对「确定箱数」后点「确认，开始比价」。",
                            "、".join(store_label(k) for k in meta["stores"]), len(rows)))

    def _vals(self, i):
        """数据行 i 每一列显示的文字（按屏幕上的列顺序）"""
        x = self.rows[i]
        sales = [f"{v:g}" if isinstance(v, (int, float)) else "" for v in x["sales"]]
        pack = x["pack"] or 0
        daily = x.get("daily") if x.get("daily") is not None else x.get("daily0")
        return _perm([" · ".join([self._verdict(x)[1]] + [m[1] for m in self._marks(x)]), x["code"], x["ean"], x["name"],
                      f"{x['store_inv']:g}" if isinstance(x["store_inv"], (int, float)) else "",
                      *sales, (_days(_cover(x)) or "0") if x.get("in_db", True) else "",    # 没销量也没库存 → 0
                      f"{x['pack']:g}" if x["pack"] else "",
                      f"{x['lbox']:,}", f"{x['file_pcs']:g}",
                      f"{x['suggested']:,}" if x["in_db"] else "", f"{x['final']:,}" if pack > 0 else "",
                      f"{x['fbox']:,}",
                      x.get("tier") or "",
                      f"{x['cycle']:g}" if isinstance(x.get("cycle"), (int, float)) else "",
                      (f"{daily:.2f}" if daily else "0") if x.get("in_db", True) else "",
                      f"{x['target']:.0f}" if isinstance(x.get("target"), (int, float)) else "",
                      _days(self._arrive_days(x)),
                      _days(_cover(x, x["file_pcs"])) if (x.get("file_pcs") or 0) > 0 else "",
                      R.STORES.get(x.get("store"), ""), self._change_text(x), self._transfer_text(x),
                      "；".join(self._reason_lines(x)), x.get("sup_code") or "", x.get("sup_name") or "", self._monthly(x)])

    def _monthly(self, x):
        """月均：新算法 = 加权日均 × 30；原程序算法 = 最长周期销量 ÷ 月数（和订货明细同一个算法，store_orders.monthly_and_need）"""
        import store_orders as SO
        days = (self.meta or {}).get("days") or [30, 60, 90, 180]
        sales = x.get("sales") or []
        if not x.get("in_db", True) or len(sales) < 4 or not isinstance(sales[3], (int, float)):
            return ""
        return f"{SO.monthly_and_need(x, days)[0]:g}"

    def _verdict(self, x):
        """(类别, 文字)：确定件数和门店清单比，不订 / 照清单订 / 多订 / 少订 / 建议调货"""
        key = (x.get("store"), str(x.get("code")))
        moved = key in self._moves_in                  # 只算调进来的
        return R.review_verdict(x["lbox"], x["fbox"], moved, x.get("in_db", True), None, tr("照清单订"))

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
        if x.get("protect") == "new":
            lines.insert(0, tr("新品保护：近两个月才开始卖（或还没卖过），销量还说明不了什么，按门店清单订"))
        elif x.get("protect") == "empty":
            lines.insert(0, tr("缺货保护：店里已经没货，门店又点名要，至少订 1 箱"))
        elif x.get("sys_box") is not None and x["sys_box"] > x["lbox"]:
            lines.append(tr("系统算出来要 {0:,} 箱，多于门店清单，按清单 {1:,} 箱订（先不多订）", x["sys_box"], x["lbox"]))
        if x.get("in_db", True) and x.get("sbox") is not None and x["lbox"] != x["sbox"]:
            lines.append(tr("门店清单报了 {0:,} 箱", x["lbox"]))
        if x.get("sbox") is not None and x["fbox"] != x["sbox"]:
            lines.append(tr("现在确定 {0:,} 箱（手动改过）", x["fbox"]))
        return lines

    def _marks(self, x):
        """判断后面的提醒（只提醒）：新算法订了一箱要卖半年以上 → 慢销；库存是负数 → 库存不准"""
        if not x.get("in_db", True):
            return []
        return R.review_marks(x.get("target") is not None, x["final"] > 0, self._arrive_days(x), x.get("store_inv"))

    def _arrive_days(self, x):
        """
        到货后够卖：(库存 + 订的箱数 × 每箱件数 + 调进来的 − 调给别的店的) ÷ 日均；不订也不调出 → None
        （有调进来的：订的箱数是调货以后的）
        """
        key = (x.get("store"), str(x.get("code")))
        out = sum(t["pieces"] for t in self._moves_out.get(key, []))
        m = self._moves_in.get(key)
        have = (m["colli_after"] * _pk(x) + m["pieces"]) if m else x["final"]
        if have <= 0 and not out:
            return None
        return _cover(x, have - out)

    def _change_text(self, x):
        """确定箱数比门店清单多 / 少几箱（一样就空着，表格干净）"""
        d = int(x["fbox"]) - int(x["lbox"])
        return f"{d:+,}" if d else ""

    def _need_pcs(self, x):
        """调货用的「真正要的件数」：确定箱数没改（= 系统建议）就用系统算的件数（零头才能调），改过就按箱数 × 每箱件数"""
        if x.get("sbox") is not None and x["fbox"] == x["sbox"]:
            pk = _pk(x)
            return int(min(float(x.get("suggested") or 0), x["fbox"] * pk) if pk > 0 else (x.get("suggested") or 0))
        return int(x["final"])

    def _transfer_text(self, x):
        key = (x.get("store"), str(x.get("code")))
        m = self._moves_in.get(key)
        if m:                                          # 短：← 从哪家 几件（详细的在鼠标提示里）
            return f"← {R.STORES.get(m['from'], m['from'])} {m['pieces']}" + tr("件") + ("  ✎" if m.get("manual") else "")
        ov = self.transfer_ov.get(key)
        if ov is not None and not TR.norm_override(ov)["auto"]:     # 人工改过：不调 / 调出门店不够
            p = TR.norm_override(ov)["pieces"]
            return tr("不调") + "  ✎" if not p else tr("调不了 {0} 件", p)
        # 要订、没调货的写「不调」（不订的行空着；调出去的不显示，审核只要知道从哪调进来）
        return tr("不调")                              # 没调货的一律写「不调」

    def freeze_transfers(self):
        """第一次人工改（调货或确定件数）：把现在系统建议的调货冻结下来，以后改一行别的行不跟着变"""
        if self.transfer_frozen:
            return
        self.transfer_ov = TR.freeze(self._moves, self.transfer_ov, lambda m: (m["to"], str(m["code"])))
        self.transfer_frozen = True

    def _edit_transfer(self, k, c):
        """双击「调货」格：选从哪家门店调、调几件（或者不调）。只改这一行"""
        if c != _col(TRANSFER_COL):
            return
        i = self.ctl.row_at(k)
        if i is None:
            return
        x = self.rows[i]
        snap = self.meta.get("snap") or {}
        if not snap:
            self.win.show_toast(tr("没有其他门店的库存数据，不能调货"))
            return
        if not _can_transfer(x) or int(x.get("fbox") or 0) <= 0:
            self.win.show_toast(tr("这一行不订货，不用调货"))
            return
        import store_orders as SO
        so = self.settings.get("store_orders") or {}
        wt = TR.wholesale_raw(self.settings)          # 批发商比价自己的调货设置
        cfg = TR.config(wt)
        if not cfg.get("enabled"):
            self.win.show_toast(tr("批发商比价没有启用门店间调货（设置 → 门店间调货…）"))
            return
        far = set(TR.excluded_stores(wt, (dict(SO.DEFAULT_FILL, **(so.get("fill") or {}))).get("far_stores")))
        sid, code = x.get("store"), str(x.get("code"))
        ordering = {(r.get("store"), str(r.get("code"))) for r in self.rows if int(r.get("final") or 0) > 0}
        stores = []
        for s_, d in snap.items():
            v = (d or {}).get(code)
            if s_ == sid or not v:
                continue
            notes = []
            if s_ in far:
                notes.append(tr("不参与调货"))
            if (s_, code) in ordering:
                notes.append(tr("自己也要订"))
            stores.append({"sid": s_, "name": R.STORES.get(s_, str(s_)), "inv": v.get("inv"), "daily": v.get("daily"),
                           "surplus": TR.surplus(v, cfg.get("keep_cycles")), "note": "、".join(notes)})
        if not stores:
            self.win.show_toast(tr("其他门店没有这个商品的库存数据"))
            return
        key = (sid, code)
        m = self._moves_in.get(key)
        pack = float(x.get("pack") or 1) or 1.0
        need = self._need_pcs(x)
        colli = int(x["fbox"])
        from erp_widgets import TransferEditDialog
        dlg = TransferEditDialog(self.window, x.get("name") or code,
                                 tr("{0} 这次要 {1:g} 件，一箱 {2:g} 件，现在订 {3:g} 箱",
                                    R.STORES.get(sid, sid), need, pack, colli),
                                 stores, (m["from"], m["pieces"]) if m else None, need, pack, colli)
        if not dlg.exec() or dlg.value is None:
            return
        frm, pcs = dlg.value
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.freeze_transfers()
            self.transfer_ov[key] = {"from": frm, "pieces": pcs} if pcs else 0
            self._replan(render=False)
            self._refresh_rows([j for j, r in enumerate(self.rows) if str(r.get("code")) == code])   # 只刷新这个商品的几行
            self._summary()
        finally:
            QApplication.restoreOverrideCursor()
        if pcs and key not in self._moves_in:
            self.win.show_toast(tr("{0} 库存不够，这次调不了", R.STORES.get(frm, frm)))

    def _transfer_tip(self, x):
        key = (x.get("store"), str(x.get("code")))
        m = self._moves_in.get(key)
        if m:
            return tr("调进 {0} 件 ← {1}，还订 {2} 箱", m["pieces"], R.STORES.get(m["from"], m["from"]), m["colli_after"])
        return ""

    # ---------- 右边详情栏（和订货明细一致）----------
    def _build_drawer(self):
        """右边详情栏：和门店配货的订货明细一模一样（编号、四个小卡片、26 周销量图、为什么、其他门店）"""
        from PySide6.QtWidgets import QScrollArea
        from erp_widgets import Sparkline
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFixedWidth(350)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)     # 详情栏只上下滚，内容自动换行
        sa.setFrameShape(QFrame.NoFrame)
        d = QWidget()
        d.setObjectName("Drawer")
        v = QVBoxLayout(d)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        self.d_over = QLabel("")
        self.d_over.setObjectName("Faint")
        self.d_over.setWordWrap(True)
        self.d_title = QLabel(tr("选中一行查看详情"))
        self.d_title.setStyleSheet("font-size:16px; font-weight:700;")
        self.d_title.setWordWrap(True)
        self.d_codes = QLabel("")
        self.d_codes.setObjectName("Faint")
        self.d_codes.setWordWrap(True)
        self.d_img = QLabel()                     # 商品图（公司图片服务器上有这个条码的图才显示）
        self.d_img.setAlignment(Qt.AlignCenter)
        self.d_img.setMinimumHeight(180)
        self.d_img.hide()
        for w in (self.d_img, self.d_over, self.d_title, self.d_codes):
            v.addWidget(w)
        g = QGridLayout()
        g.setSpacing(8)
        self.minis = []
        for i, lab in enumerate((tr("本店库存"), tr("加权日均"), tr("目标库存"), tr("服务水平"))):
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
        sp = QFrame()
        sp.setObjectName("MiniCard")
        spv = QVBoxLayout(sp)
        spv.setContentsMargins(12, 10, 12, 10)
        hl = QHBoxLayout()
        t = QLabel(tr("近 26 周销量"))
        t.setStyleSheet("font-weight:600; font-size:12px;")
        u = QLabel(tr("件 / 周"))
        u.setObjectName("MiniLabel")
        hl.addWidget(t, 1)
        hl.addWidget(u)
        spv.addLayout(hl)
        self.spark = Sparkline(height=150)
        spv.addWidget(self.spark)
        self.spark_note = QLabel("")
        self.spark_note.setObjectName("MiniLabel")
        self.spark_note.setWordWrap(True)
        spv.addWidget(self.spark_note)
        v.addWidget(sp)
        why = QFrame()
        why.setObjectName("MiniCard")
        wv = QVBoxLayout(why)
        wv.setContentsMargins(12, 10, 12, 10)
        self.why_t = QLabel(tr("为什么这么建议"))
        self.why_t.setStyleSheet("font-weight:600; font-size:12px;")
        self.why = QLabel("")
        self.why.setWordWrap(True)
        self.why.setObjectName("Faint")
        wv.addWidget(self.why_t)
        wv.addWidget(self.why)
        v.addWidget(why)
        oth = QFrame()
        oth.setObjectName("MiniCard")
        ov = QVBoxLayout(oth)
        ov.setContentsMargins(12, 10, 12, 10)
        ot = QLabel(tr("其他门店"))
        ot.setStyleSheet("font-weight:600; font-size:12px;")
        ov.addWidget(ot)
        self.others = QVBoxLayout()
        self.others.setSpacing(4)
        ov.addLayout(self.others)
        v.addWidget(oth)
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
        """详情栏内容（和订货明细同一个写法）"""
        if not (0 <= i < len(self.rows)):
            return
        T = self.ui.T
        x = self.rows[i]
        code = str(x.get("code"))
        sid = x.get("store")
        self.d_over.setText(" · ".join(p for p in (R.STORES.get(sid, ""), x.get("sup_name") or "") if p))
        self.d_title.setText(x.get("name") or code)
        import product_images as PI
        PI.set_label_image(self.d_img, x.get("ean"), 180, self.settings)
        pack = x.get("pack") or 0
        try:
            price = float(x.get("price") or 0)
            price = price if price == price else 0
        except (TypeError, ValueError):
            price = 0
        fc = f" · Cod.Forn. {x['sup_code']}" if x.get("sup_code") else ""
        line2 = " · ".join(p for p in (f"{pack:g} " + tr("件/箱") if pack else "",
                                       f"€{price:.3f}/" + tr("件") if price else "",
                                       tr("{0} 报价", x["quote_sup"]) if x.get("quote_sup") else "") if p)
        self.d_codes.setText(f"{code} · {x.get('ean') or '—'}{fc}" + (f"\n{line2}" if line2 else ""))
        (a1, a2), (b1, b2), (c1, c2), (d1, d2) = self.minis
        if x.get("in_db", True):
            inv = x.get("store_inv")
            inv = float(inv) if isinstance(inv, (int, float)) else 0.0     # 显示原值（计算时负数当 0）
            a1.setText(f"{inv:g}")
            a2.setText(tr("件"))
            daily = x.get("daily") if x.get("daily") is not None else x.get("daily0")
            b1.setText(f"{(daily or 0):.2f}")
            b2.setText(tr("季节系数 {0:.2f}", x.get("season") or 1) if x.get("season") else tr("件 / 天"))
            tg = x.get("target")
            c1.setText(f"{tg:.0f}" if isinstance(tg, (int, float)) else "—")
            c2.setText(tr("含安全库存 {0:.0f}", x.get("safety") or 0) if tg is not None else tr("原程序算法"))
            svc = x.get("service")
            d1.setText(f"{svc:.1%}" if isinstance(svc, (int, float)) else "—")
            d2.setText(tr("{0} 类商品", x.get("tier") or "?"))
            wk = list(x.get("weekly") or [])
            self.spark.set_values(wk, (daily or 0) * 7)
            if wk:
                last4 = sum(wk[-4:])
                imax = max(range(len(wk)), key=lambda k: wk[k])
                a, b = self.spark.week_range(imax)
                self.spark_note.setText(tr("最近 4 周 {0:g} 件 · 26 周合计 {1:g} 件 · 最多 {2:g} 件（{3:%d/%m}–{4:%d/%m}）",
                                           round(last4, 1), round(sum(wk), 1), round(wk[imax], 1), a, b) + "\n" +
                                        tr("虚线：加权平均 {0:.1f} 件 / 周 · 鼠标移到柱子上看那一周", (daily or 0) * 7))
            else:
                self.spark_note.setText(tr("原程序算法没有周销量"))
        else:
            for m1, m2 in self.minis:
                m1.setText("—")
                m1.setStyleSheet("")
                m2.setText("")
            self.spark.set_values([])
            self.spark_note.setText("")
        sugg = x.get("suggested") or 0
        if not x.get("in_db", True):
            self.why_t.setText(tr("为什么这么建议"))
        elif sugg > 0:
            self.why_t.setText(tr("为什么建议 {0:g} 箱", x["sbox"]) if x.get("sbox") is not None else tr("为什么建议 {0:,} 件", sugg))
        else:
            self.why_t.setText(tr("为什么不用订"))
        self.why.setText("\n".join(["· " + l for l in self._reason_lines(x)] + ["⚠ " + m[2] for m in self._marks(x)]))
        # 其他门店：库存、够卖几天、这次定了几箱（和订货明细同一个写法）
        while self.others.count():
            w = self.others.takeAt(0).widget()
            if w:
                w.deleteLater()
        snap = self.meta.get("snap") or {}
        for osid in sorted(k for k in snap if k != sid):
            d = (snap.get(osid) or {}).get(code)
            if not d:
                continue
            ox = self._by_key.get((osid, code))
            on = int(ox.get("fbox") or 0) if ox else 0
            v = _cover({"in_db": True, "inv0": d.get("inv"), "daily0": d.get("daily")})
            l = QLabel(f"{R.STORES.get(osid, osid)}    " +
                       tr("库存 {0:g} · 够卖 {1} 天 · 订 {2:g} 箱", float(d.get("inv") or 0), _days(v) or "—", on))
            l.setObjectName("Faint" if not on else "")
            self.others.addWidget(l)
        if not self.others.count():
            l = QLabel(tr("没有其他门店的数据"))
            l.setObjectName("Faint")
            self.others.addWidget(l)

    def _replan(self, render=True):
        """按现在的确定箱数重新算门店间调货（改了以后稍等一下再算，不卡）"""
        missing = [x for x in self.rows if "fbox" not in x]
        if missing:                                    # 还没按箱整理过的行（保险）
            init_boxes(missing, None, self._algo())
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
            self._fill_prob()
            self._summary()
            if self._cur_i is not None:
                self._show_drawer(self._cur_i)

    def _text(self, i, c):
        return self._vals(i)[c]

    # ---------- 工具条：门店按钮、看哪些、更多 ----------
    def _build_chips(self, stores):
        """门店按钮：「全部门店」+ 五家店（和订货明细一样）；这次没检查的门店按钮灰掉"""
        import store_orders as SO
        for b in list(self.store_group.buttons()):
            self.store_group.removeButton(b)
            b.deleteLater()
        stores = {int(k) for k in stores or []}
        for sid, text in [(None, tr("全部门店"))] + list(SO.STORE_SHEETS.items()):
            b = QPushButton(text)
            b.setEnabled(sid is None or sid in stores)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(sid is None)
            b.setProperty("sid", -1 if sid is None else int(sid))
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            bf = b.font()
            bf.setBold(True)
            b.setMinimumWidth(QFontMetrics(bf).horizontalAdvance(text) + 28)
            self.store_group.addButton(b)
            self.chip_lay.addWidget(b)

    def _fill_suppliers(self):
        """供应商下拉框：这次清单里商品的供应商（数据库登记的），后面带商品数"""
        cnt = {}
        for x in self.rows:
            n = x.get("sup_name") or ""
            if n:
                cnt[n] = cnt.get(n, 0) + 1
        self.f_sup.blockSignals(True)
        self.f_sup.clear()
        self.f_sup.addItem(tr("全部供应商"), None)
        for n in sorted(cnt):
            self.f_sup.addItem(f"{n}（{cnt[n]}）", n)
        self.f_sup.blockSignals(False)

    def _fill_prob(self):
        """「数据库没有 / 没有箱规」两个按钮（带个数）和点开的列表：列表跟着门店按钮、搜索框筛选；没有这类商品就不显示按钮"""
        if not hasattr(self, "prob"):
            return
        sid = self._store_sel()
        text = self.flt.text().strip().lower()
        self._prob_filling = True
        for kind, (dlg, btn, t) in self.prob.items():
            allk = [i for i, x in enumerate(self.rows) if _problem(x) == kind]
            idx = [i for i in allk if (sid is None or self.rows[i].get("store") == sid) and
                   (not text or text in f"{self.rows[i].get('code')} {self.rows[i].get('ean')} {self.rows[i].get('name')}".lower())]
            btn.setText((tr("⚠ 数据库没有（{0}）", len(allk)) if kind == "nodb" else tr("⚠ 没有箱规（{0}）", len(allk))))
            btn.setVisible(bool(allk))
            t.blockSignals(True)
            t.setRowCount(len(idx))
            for r, i in enumerate(idx):
                x = self.rows[i]
                self.ui.table_set_row(t, r, [R.STORES.get(x.get("store"), ""), x.get("ean") or "", x.get("name") or "",
                                             x.get("sup_name") or "", f"{x['lbox']:,}", f"{x['fbox']:,}"])
                t.item(r, 0).setData(Qt.UserRole, i)
                t.item(r, 2).setToolTip(f"{x.get('name') or ''}\n{x.get('code') or ''} · {x.get('ean') or ''}")
                for c in range(t.columnCount()):
                    it = t.item(r, c)
                    if c == 5:
                        it.setFlags(it.flags() | Qt.ItemIsEditable)
                        it.setToolTip(tr("双击修改箱数，0 = 不订"))
                        it.setForeground(QColor(self.ui.T("accent") if x["fbox"] > 0 else self.ui.T("faint")))
                    else:
                        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            t.blockSignals(False)
            t.setColumnHidden(0, len((self.meta or {}).get("stores") or []) < 2)
        self._prob_filling = False

    def _open_prob(self, kind):
        """点「数据库没有 / 没有箱规」按钮：打开列表（不挡住检查窗口，可以一边对照一边改）"""
        self._fill_prob()
        dlg = self.prob[kind][0]
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _prob_edited(self, kind, item):
        """顶上小表格里改了确定箱数：等编辑框关掉再刷新（编辑中途重画会卡住）"""
        if self._prob_filling or item.column() != 5:
            return
        t = self.prob[kind][2]
        first = t.item(item.row(), 0)
        i = first.data(Qt.UserRole) if first is not None else None
        if i is None:
            return
        try:
            n = float(item.text().strip().replace(",", "") or 0)
            if n < 0 or n != int(n):
                raise ValueError
            _set_box(self.rows[i], int(n))
        except ValueError:
            self.win.show_toast(tr("箱数要填 0 或正整数"))
        QTimer.singleShot(0, lambda: (self._fill_prob(), self._summary()))

    def _store_sel(self):
        b = self.store_group.checkedButton() if hasattr(self, "store_group") else None
        v = b.property("sid") if b is not None else -1
        return None if v is None or int(v) < 0 else int(v)

    def _apply_forced(self):
        """程序自己藏的列：清单按件时的箱数列；只有一家门店 / 只看一家门店时的「门店」列"""
        one = len((self.meta or {}).get("stores") or []) < 2 or self._store_sel() is not None
        self._forced = set(getattr(self, "_unit_forced", set())) | ({22} if one else set())
        self.cols.apply()

    def _store_changed(self):
        self._apply_forced()
        self._filter()

    def _more_menu(self, btn):
        m = QMenu(self.window)
        a_file = m.addAction(tr("全部用清单数量"))
        m.addSeparator()
        a_cols = m.addAction(tr("显示哪些列…"))
        a_order = m.addAction(tr("恢复默认列顺序"))
        a_exp = m.addAction(tr("导出 Excel"))
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        got = m.exec(pos)
        if got == a_file:
            self._set_all("file")
        elif got == a_cols:
            self.cols.menu(pos)
        elif got == a_order:
            self._reset_cols()
        elif got == a_exp:
            self.export()

    def _update_view_counts(self, cnt):
        """「看哪些」下拉框里带上个数：只看多订（12）"""
        n = {"all": len(self.rows), "changed": sum(cnt.get(k, 0) for k in ("transfer", "more", "less", "none"))}
        n.update(cnt)
        for k in range(self.view.count()):
            key = self.view.itemData(k)
            text = tr(dict(VIEWS)[key])
            self.view.setItemText(k, f"{text}（{n.get(key, 0)}）" if key in n else text)

    def _status(self, shown, total):
        """状态栏一行（和订货明细一样）：显示几行 · 改了几处 · 当前筛选合计几箱、多少钱"""
        self._shown = (shown, total)
        if not self.rows:
            self.count_lbl.setText("")
            return
        n_edit = sum(1 for x in self.rows if x.get("sbox") is not None and x["fbox"] != x["sbox"])
        boxes = amt = 0.0
        for i in getattr(self.ctl, "visible", None) or []:
            x = self.rows[i]
            m = self._moves_in.get((x.get("store"), str(x.get("code"))))
            b = m["colli_after"] if m else x["fbox"]          # 有调货的按调货以后的箱数
            boxes += b
            try:
                p = float(x.get("price") or 0)
                amt += b * _pk(x) * (p if p == p else 0)
            except (TypeError, ValueError):
                pass
        self.count_lbl.setText(tr("显示 {0} / {1} 行 · 已改 {2} 处 · 当前筛选合计 {3:g} 箱 · €{4:,.2f}",
                                  shown, total, n_edit, boxes, amt))
        self.count_lbl.setToolTip(getattr(self, "page_sum", None).text() if hasattr(self, "page_sum") else "")

    def _hidden(self, i):
        x = self.rows[i]
        sid = self._store_sel()
        if sid is not None and x.get("store") != sid:
            return True
        sup = self.f_sup.currentData()
        if sup and (x.get("sup_name") or "") != sup:
            return True
        if _problem(x):                                # 数据库没有 / 没有箱规的在顶上的小表格里
            return True
        text = self.flt.text().strip().lower()
        view = self.view.currentData()
        kind = self._verdict(x)[0]
        hide = (view == "changed" and kind not in ("transfer", "more", "less", "none")) or \
            (view in ("transfer", "more", "less", "none") and kind != view)
        return hide or \
               (bool(text) and text not in f"{x['code']} {x['ean']} {x['name']} {x.get('sup_code') or ''} {x.get('sup_name') or ''}".lower())

    def _fill(self):
        missing = [x for x in self.rows if "fbox" not in x]
        if missing:
            init_boxes(missing, None, self._algo())
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
        d = int(x["fbox"]) - int(x["lbox"])
        ch.setForeground(QColor(T("accent") if d > 0 else T("warn") if d < 0 else T("faint")))
        if d:
            f = ch.font()
            f.setBold(True)
            ch.setFont(f)
        tri = t.item(k, _col(TRANSFER_COL))
        tkey = (x.get("store"), str(x.get("code")))
        m_in = self._moves_in.get(tkey)
        if m_in:
            tri.setForeground(QColor(T("warn") if m_in.get("manual") else T("ok")))
            tri.setToolTip(self._transfer_tip(x) + "\n" + tr("双击修改：换调出门店、改件数或者不调"))
        elif tkey in self.transfer_ov:
            tri.setForeground(QColor(T("faint")))
            tri.setToolTip(tr("人工改过：双击重新选调出门店和件数；「全部用建议量」恢复系统算的调货"))
        else:
            tri.setForeground(QColor(T("faint")))
            tri.setToolTip(tr("双击：这个商品改成从别的门店调货") if (x["fbox"] > 0 and _can_transfer(x)) else "")
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
        c.setText(f"{x['fbox']:,}")
        f = c.font()
        f.setBold(x["fbox"] > 0)
        c.setFont(f)
        changed = x.get("sbox") is not None and x["fbox"] != x["sbox"]
        c.setForeground(QColor(T("warn") if changed else T("accent") if x["fbox"] > 0 else T("faint")))
        c.setBackground(QColor(T("warn_soft")) if changed else QColor(0, 0, 0, 0))
        c.setToolTip(tr("手动改过：系统建议 {0:,} 箱", x["sbox"]) if changed else tr("双击修改箱数，0 = 不订"))
        self.table.item(k, PCS_COL).setText(f"{x['final']:,}" if _pk(x) > 0 else "")
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
            d = int(x["fbox"]) - int(x["lbox"])
            ch.setText(self._change_text(x))
            ch.setForeground(QColor(T("accent") if d > 0 else T("warn") if d < 0 else T("faint")))

    def _refresh_rows(self, idx):
        """只重画这几行（改一个调货不用整张表重画）"""
        was = self._filling
        self._filling = True
        self.table.blockSignals(True)
        for i in idx:
            k = self.ctl.pos.get(i)
            if k is not None:
                self._fill_row(i, k)
        self.table.blockSignals(False)
        self._filling = was
        if self._cur_i in idx:
            self._show_drawer(self._cur_i)

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
            self.win.show_toast(tr("箱数要填 0 或正整数"))
            n = None
        if n is not None and int(n) != self.rows[i]["fbox"]:
            self.freeze_transfers()                   # 改了确定箱数：别的行的调货不跟着变
            _set_box(self.rows[i], int(n))
        # 等编辑框关掉再更新这一行（编辑中途改格子会让程序卡住）
        QTimer.singleShot(0, lambda: (self._show(i), self._summary(), self._plan_timer.start()))

    def _set_all(self, which):
        if which == "sugg":
            self.transfer_ov = {}                     # 人工改的调货也恢复成系统算的（解冻，按设置重新建议）
            self.transfer_frozen = False
        for x in self.rows:
            if which == "sugg" and x.get("sbox") is not None:
                _set_box(x, x["sbox"])
            else:
                _set_box(x, x["lbox"])
        self._replan()

    def _filter(self, *_):
        if hasattr(self, "ctl"):
            self.ctl.render()
        self._fill_prob()
        self._summary()

    def _summary(self):
        cnt = {v: 0 for v in R.VERDICT_RANK}
        for x in self.rows:
            k_ = self._verdict(x)[0]
            cnt[k_] = cnt.get(k_, 0) + 1
        n_edit = sum(1 for x in self.rows if x.get("sbox") is not None and x["fbox"] != x["sbox"])
        txt = tr("{0} 个商品：照清单订 {1}，多订 {2}，少订 {3}，不订 {4}，建议调货 {5}，数据库没有 {6}。手动改了 {7} 个。",
                 len(self.rows), cnt["same"], cnt["more"], cnt["less"], cnt["none"], cnt["transfer"], cnt["nodb"], n_edit)
        # 采购金额估算：最便宜那家报价的件价（没有报价的用数据库进价 PrezzoForn）× 件数；实际金额以「开始处理」后的比价为准
        def price(x):
            try:
                v = float(x.get("price") or 0)
                return v if v == v else 0.0
            except (TypeError, ValueError):
                return 0.0
        amt_file = sum(price(x) * x["lbox"] * _pk(x) for x in self.rows)
        amt_final = sum(price(x) * x["fbox"] * _pk(x) for x in self.rows)
        no_price = sum(1 for x in self.rows if x["fbox"] > 0 and not price(x))
        if amt_file or amt_final:
            txt += "\n" + tr("估算采购金额（有报价的按最便宜那家，没有的按数据库进价）：清单 €{0:,.2f} → 确定 €{1:,.2f}",
                             amt_file, amt_final)
            if no_price:
                txt += tr("（{0} 个商品没有进价，没算进去）", no_price)
        mv = getattr(self, "_moves", None) or []
        if mv:
            txt += "\n" + tr("门店间调货（按现在的确定件数）：{0} 个商品，少订 {1} 箱，货值 €{2:,.2f}", len(mv),
                             sum(m["colli_before"] - m["colli_after"] for m in mv), sum(m["value"] for m in mv))
        self._money = (amt_file, amt_final)
        self._update_view_counts(cnt)
        self._status(*self._shown)
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
                   "清单箱数", "清单件数", "清单订后够卖（天）", "建议件数", "确定箱数", "变化（箱）", "确定订后够卖（天）",
                   "调货", "原因", "系统说明"])
        for c in ws[1]:
            c.font = Font(bold=True)
        def num(v):
            return None if v is None else ("∞" if math.isinf(v) else round(v))

        for x in self.rows:
            ws.append([R.STORES.get(x.get("store"), ""), self._verdict(x)[1], x["code"], x["ean"], x["name"], x["store_inv"],
                       *x["sales"], num(_cover(x)),
                       x["pack"] or None, x["lbox"], x["file_pcs"],
                       num(_cover(x, x["file_pcs"])) if (x.get("file_pcs") or 0) > 0 else None,
                       x["suggested"] if x["in_db"] else None, x["fbox"], self._change_text(x),
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
        return sum(1 for x in self.rows if x.get("store") == sid and x["fbox"] > 0)

    def transfer_plan(self):
        """按现在的确定件数算门店间调货：(调货单, {(门店, 商品编号): 调货后还要订的件数})"""
        import store_orders as SO
        import transfers as TR
        so = self.settings.get("store_orders") or {}
        wt = TR.wholesale_raw(self.settings)          # 批发商比价自己的调货设置
        far = TR.excluded_stores(wt, (dict(SO.DEFAULT_FILL, **(so.get("fill") or {}))).get("far_stores"))
        by_store = {}
        for x in self.rows:                       # 调货按「真正要的件数」算（确定箱数没改就是系统算的件数，零头才能调）
            by_store.setdefault(x.get("store"), []).append(dict(x, final=self._need_pcs(x)) if _can_transfer(x) else
                                                          dict(x, final=0))
        snap = self.meta.get("snap")
        if not snap:
            return [], {}
        return R.plan_batch_transfers(by_store, snap, wt, far, getattr(self, "transfer_ov", None),
                                      auto=not getattr(self, "transfer_frozen", False))

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
            rows = []
            for x in self.rows:
                if x.get("store") != sid:
                    continue
                y = dict(x)
                left = new_final.get((sid, str(x.get("code"))))
                if left is not None:                       # 调货以后还要订的箱数
                    _set_box(y, int(math.ceil(left / _pk(x) - 1e-9)) if _pk(x) > 0 else 0)
                rows.append(y)
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
