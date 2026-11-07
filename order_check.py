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
import copy
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
# 2026-10-25 第七版（用户：审核太累，很多列从来不看）：一行只放判断要用的 ——
#   商品（图 + 名称）供应商 | 现在：库存 月销 现在够卖 | 订多少：每箱件数 清单 → 确定 ✎ | 结果：订后够卖 金额 | 判断 调货
#   订后够卖一个数就能看出订得合不合理：红 = 下次到货前就卖完，黄 = 压货。30/60/90/180 天、日均、周期、等级、编号、条码收起来（详情栏里有）
#   29 = 金额（确定箱数 × 每箱件数 × 件价，有调货的按调货以后的箱数）
#   2026-11-01 加 30 = 情况（Griglia 商品 · 批发商都没有 / 非 Griglia 商品 · 批发商都没有 / 非 Griglia 商品 · 批发商有），放在判断后面，
#   待核对的商品也在主表格里显示，右键表头按「情况」筛选、直接改（用户：在列表里直接判断和修改）
#   2026-11-03 加 31 = 在途箱数（已经订了还没到货的箱数，order_record；用户同一天定：从建议箱数里扣，init_boxes），放在现在够卖后面
_ORDER = [22, 3, 27, 4, 28, 9, 31, 10, 11, 15, 20, 29, 0, 30, 24, 2, 26, 16, 17, 18, 5, 6, 7, 8, 14, 12, 13, 19, 21, 23, 25, 1]
SITU = 30
TRANSIT = 31
# 2026-10-21 第四版：窗口和订货明细一个样子（门店按钮、一行工具条、状态栏），名称显示、供应商和原因收起来（详情栏里有）：
#   门店 条码 Codice 名称 等级 周期 本店库存 日均 库存够卖 每箱件数 | 清单 判断 确定箱数 确定件数 ✎ 调货 ✎ | 到货后够卖
_LAYOUT_VER = 9           # 改了默认顺序就加 1（第 9 版起列宽也一起恢复默认）
_DEFAULT_HIDDEN = [2, 26, 16, 17, 18, 5, 6, 7, 8, 14, 12, 13, 19, 21, 23, 25, 1]
_HIDDEN_VER = 7           # 改了默认藏哪些列就加 1（settings.COMPANY_DEFAULTS 里的 check_cols_hidden 一起改）
CHANGE_COL, TRANSFER_COL, REASON_COL = 23, 24, 25
AMOUNT = 29
# 「判断」排序：要看的排前面（不订、少订、调货、多订），照清单订的放最后
VERDICT_RANK = {"none": 0, "less": 1, "transfer": 2, "more": 3, "nodb": 4, "same": 5}


def _col(k):
    return _ORDER.index(k)


def _perm(vals):
    return [vals[k] for k in _ORDER]


EDIT_COL = _col(15)           # 确定箱数 ✎（按箱审核）
AMOUNT_COL = _col(AMOUNT)     # 金额
PCS_COL = _col(14)            # 确定件数（= 箱数 × 每箱件数，默认收起来）


from check_rules import (  # noqa: F401  业务规则搬到 check_rules.py（2026-11-06），这里照旧能用这些名字
    _pk, PROB_KINDS_TIPS, _prob_title, _issue, _situation, _edited, _issue_text, _problem, PROB_TABS, _tab_of,
    _report_rows, report_name, _advice, write_problem_report, _can_transfer, _set_box, is_new_item,
    REVIEW_DEFAULTS, review_rules, _cover_now, PHANTOM_MIN_DAYS, PHANTOM_UNITS, censored_info, phantom_info,
    init_boxes)

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
         ("less", "只看少订"), ("none", "只看不订"), ("phantom", "只看库存可能不准"),
         ("promo", "只看促销"))


def promo_text(p):
    """「会员价 €4.99（标价 €6.49，−23%）· 到 31/12/2026」"""
    s = tr("{0} €{1:.2f}（标价 €{2:.2f}，−{3:.0%}）", tr(p.get("label") or "促销"), p["price"], p["list"], p["pct"])
    if p.get("end"):
        s += " · " + tr("到 {0}", p["end"].strftime("%d/%m/%Y"))
    return s


class _Sig(QObject):
    done = Signal(object)
    months = Signal(object)          # 近两年每月销量读好了（后台线程 → 界面）
    promos = Signal(object)          # 本店在促销的商品读好了
    transit = Signal(object)         # 在途读好了（order_record）


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
        self.sig.months.connect(self._months_arrived)
        self.sig.promos.connect(self._promos_arrived)
        self.sig.transit.connect(self._transit_arrived)
        self.transit = None
        self._card = None                # 商品详情窗口（双击商品打开，一个窗口反复用）
        self._m24, self._m24_keys, self._m24_done = {}, [], set()

    def build(self):
        ui = self.ui
        p = ui.Panel(tr("② 检查门店订货清单"))
        p.setToolTip(tr("比价用的主订单只从这里生成：把一家或几家门店的订货清单一起拖进来，按销量和数据库库存检查数量（增加 / 减少 / 不订），同时查五家门店的库存。确认时先用别的门店多出来的货（门店间调货），每家店剩下要订的各自生成主订单；再一家一家点下面的「开始处理」做供应商比价。"))

        r1 = QHBoxLayout()
        r1.addWidget(QLabel(tr("清单")))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText(tr("拖入门店清单"))
        self.file_edit.setToolTip(tr("几家门店的清单一起检查，才能先在门店之间调货、再订剩下的（和总仓采购一样）"))
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
        # 少砍门店清单（2026-10-25 用户定：清单数量有它的原因；2026-11-07：库存真的多才砍）
        from erp_widgets import tip_label
        rr = review_rules(self.cfg)
        r4 = QHBoxLayout()
        r4.setSpacing(10)

        def days_box(key, tip_text, label):
            r4.addWidget(tip_label(label, tip_text))
            sb = QSpinBox()
            sb.setRange(1, 365)
            sb.setSingleStep(5)
            sb.setSuffix(tr(" 天"))
            sb.setValue(int(round(rr[key])))
            sb.valueChanged.connect(lambda v: (self.cfg.__setitem__(key, float(v)), save_settings(self.settings)))
            r4.addWidget(sb)
            r4.addSpacing(12)
            return sb
        self.keep_days = days_box("keep_days", tr("现在的库存够卖不到这么多天：照门店清单订，一箱不砍（默认 30 天）。下次检查生效"),
                                  tr("照清单订：库存少于"))
        r4.addWidget(tip_label(tr("少订最多"), tr("系统建议比门店清单少订时，最多砍掉清单的几成（默认 50%：清单 4 箱最少订 2 箱）。门店报的数量有它的原因（促销、陈列等），改得太狠反而不好。下次检查生效")))
        self.max_cut = QSpinBox()
        self.max_cut.setRange(0, 100)
        self.max_cut.setSingleStep(10)
        self.max_cut.setSuffix(" %")
        self.max_cut.setValue(int(round(rr["max_cut"] * 100)))
        self.max_cut.valueChanged.connect(lambda v: (self.cfg.__setitem__("max_cut", v / 100.0), save_settings(self.settings)))
        r4.addWidget(self.max_cut)
        r4.addSpacing(12)
        self.free_days = days_box("free_days", tr("现在的库存够卖这么多天以上，才按系统建议少订或不订（默认 60 天）；介于两个天数之间的最多砍掉上面的比例。下次检查生效"),
                                  tr("可不订：库存够卖"))
        r4.addStretch()
        pv.addLayout(r4)
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
        # 「看哪些」右边：一个「⚠ 待核对」按钮（有才显示），点开是一个窗口，按问题分标签页（用户：按钮别排一长串、名字要好懂）
        from PySide6.QtWidgets import QTabWidget
        self.prob = {}
        self._prob_filling = False
        self.prob_btn = QPushButton()
        self.prob_btn.setStyleSheet(f"color:{ui.T('warn')};")
        self.prob_btn.clicked.connect(lambda: self._open_prob(None))
        self.prob_btn.hide()
        tb.addWidget(self.prob_btn)
        dlg = QDialog(self.window)
        dlg.setWindowTitle(tr("待核对的商品"))
        dlg.resize(980, 560)
        dv = QVBoxLayout(dlg)
        dv.setContentsMargins(16, 14, 16, 12)
        dv.setSpacing(10)
        self.prob_tabs = QTabWidget()
        dv.addWidget(self.prob_tabs, 1)
        close = QPushButton(tr("关闭"))
        close.clicked.connect(dlg.accept)
        exp = QPushButton(tr("导出门店报告") + " ▾")
        exp.setToolTip(tr("意大利文报告，可以直接发给门店（带商品照片，Excel 365 打开时从网上读）。确认生成主订单时，每家店的比价文件夹里也会自动生成一份"))
        em = QMenu(exp)                               # 两种导出放在一个按钮的下拉里（用户：每店一个 / 合成一个都要）
        em.addAction(tr("每家店一个文件"), self._export_prob)
        em.addAction(tr("合成一个文件（每店一页）"), self._export_prob_one)
        exp.setMenu(em)
        cb = QHBoxLayout()
        cb.addWidget(exp)
        cb.addStretch()
        cb.addWidget(close)
        dv.addLayout(cb)
        self.prob_dlg = dlg
        self._sum_timer = QTimer(dlg)                 # 改了箱数：合计等停手 0.3 秒再算（连着改不卡）
        self._sum_timer.setSingleShot(True)
        self._sum_timer.setInterval(300)
        self._sum_timer.timeout.connect(self._summary)
        for kind, tip in PROB_TABS():
            page = QWidget()
            pv = QVBoxLayout(page)
            pv.setContentsMargins(0, 8, 0, 0)
            pv.setSpacing(8)
            t = ui.make_table([tr("门店"), tr("条码"), tr("名称"), tr("供应商"), tr("批发商"), tr("清单箱数"), tr("确定箱数") + " ✎"],
                              stretch_col=2, numeric_cols=(5, 6))
            t.verticalHeader().setDefaultSectionSize(30)
            t.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                              QAbstractItemView.AnyKeyPressed)
            for c, wd in ((0, 90), (1, 130), (3, 170), (4, 86), (5, 76), (6, 110)):
                t.setColumnWidth(c, wd)
            t.horizontalHeaderItem(4).setToolTip(tr("这周几家批发商的报价里有没有这个商品：都没有 = 比价时会缺失"))
            t.setColumnHidden(4, kind == "noquote")       # Griglia 那页全是「都没有」，不用这一列
            t.itemChanged.connect(lambda it, k=kind: self._prob_edited(k, it))
            from erp_widgets import StepperDelegate
            StepperDelegate.install(t, 6)
            from erp_widgets import HeaderFilter      # 右键表头按列筛选
            t._hf = HeaderFilter(t)
            pv.addWidget(t, 1)
            if True:
                bb = QHBoxLayout()
                b_none = QPushButton(tr("全部不订"))
                b_none.setToolTip(tr("这一页的商品确定箱数都改成 0"))
                b_none.clicked.connect(lambda _=False, k=kind: self._nogrid_none(k))
                bb.addWidget(b_none)
                bb.addStretch()
                pv.addLayout(bb)
            idx = self.prob_tabs.addTab(page, "")
            self.prob_tabs.setTabToolTip(idx, tip)
            self.prob[kind] = (idx, page, t)
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
        self.table = ui.make_table(_perm([tr("判断"), tr("商品编号"), tr("条码"), tr("商品"), tr("库存"),
                                          "30", "60", "90", "180", tr("现在够卖"), tr("每箱"), tr("清单数量"),
                                          tr("清单件数"), tr("建议件数"), tr("确定件数"), tr("确定箱数"), tr("等级"), tr("周期"), tr("日均"),
                                          tr("目标库存"), tr("订后够卖"), tr("清单订后够卖（天）"), tr("门店"),
                                          tr("变化（箱）"), tr("调货"), tr("原因"), "Codice Fornitore", tr("供应商"), tr("月销"),
                                          tr("金额 €"), tr("情况"), tr("在途箱数")]),
                                   numeric_cols=[_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20, 21, 23, 28, 29, 31]])
        self.table.setMinimumHeight(380)
        import product_images as PI
        self.table.verticalHeader().setDefaultSectionSize(PI.row_height(self.settings))
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setFocusPolicy(Qt.StrongFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        import product_images as PI          # 「商品」那一格：左边商品图（条码在藏起来的条码列），右边名称
        self.table.setItemDelegateForColumn(_col(3), PI.ThumbDelegate(self.table, _col(2), None, self.settings))
        for c, wd in enumerate(_perm((96, 80, 130, PI.thumb_size(self.settings) + 300, 60, 52, 52, 52, 52, 76, 52, 56, 70, 70, 76,
                                      70, 50, 50, 60, 70, 76, 120, 80, 76, 110, 380, 110, 120, 60, 76, 180, 60))):
            self.table.setColumnWidth(c, wd)
        self.table.horizontalHeaderItem(_col(28)).setToolTip(tr("月销：一个月大约卖几件（新算法 = 加权日均 × 30；原程序算法 = 最长周期销量 ÷ 月数）"))
        self.table.horizontalHeaderItem(_col(4)).setToolTip(tr("本店现在的库存（件）"))
        self.table.horizontalHeaderItem(_col(10)).setText(tr("每箱"))
        self.table.horizontalHeaderItem(AMOUNT_COL).setToolTip(tr("金额 = 确定箱数 × 每箱件数 × 件价（有调货的按调货以后的箱数）"))
        self.table.horizontalHeaderItem(EDIT_COL).setToolTip(tr("确定订几箱：点 − / ＋ 改 1 箱，双击直接输入，0 = 不订"))
        self.table.horizontalHeaderItem(EDIT_COL).setText(tr("确定") + " ✎")
        self.table.horizontalHeaderItem(_col(10)).setToolTip(
            tr("每箱件数：按 distribuire 里最便宜那家报价的箱规（比价默认挑它）；报价里没有的用数据库箱规。蓝色 = 和数据库不一样"))
        self.table.horizontalHeaderItem(_col(9)).setToolTip(
            tr("现在够卖（天）= 本店库存 ÷ 日均（账面库存是负数时按 0 算）"))
        self.table.horizontalHeaderItem(AFTER_COL).setToolTip(
            tr("订后够卖（天）=（库存 + 订的 + 调进来的）÷ 日均，看订得合不合理就看它：红 = 不到一个订货周期，下次到货前会卖断；黄 = 超过凑单天数，可能压货。不订的显示现在的库存够卖几天"))
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
        from erp_widgets import StepperDelegate        # 确定箱数：− / ＋ 点一下改 1 箱（2026-11-07 用户）
        StepperDelegate.install(self.table, EDIT_COL)
        # 点表头排序、右键表头按列筛选；上面「判断」「等级」两个下拉框快速筛选
        from erp_widgets import TableSortFilter, ColumnFilterBox
        # lazy：一行要算判断 / 颜色 / 原因，1500 行全画要 2 秒（办公电脑上更久）→ 先画看得见的，剩下的一批批画（2026-11-04）
        self.ctl = TableSortFilter(self.table, lambda: len(self.rows), self._text, self._fill_row_safe, key=self._key,
                                   hide=self._hidden, lazy=True, numeric=tuple(_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20, 21, 23, 28, 29, 31]))
        # 列可以拖动换位置、拖宽度，下次打开照旧（右键表头是筛选，所以「恢复默认列顺序」放在下面）
        from erp_widgets import remember_columns
        saved = self.cfg.get("check_columns")
        if isinstance(saved, list) and saved and SITU not in saved:     # 新加的「情况」列：插在判断后面，原来排好的不动
            saved.insert(saved.index(0) + 1 if 0 in saved else len(saved), SITU)
        if isinstance(saved, list) and saved and TRANSIT not in saved:  # 2026-11-03 新加的「在途」：插在现在够卖后面
            saved.insert(saved.index(9) + 1 if 9 in saved else len(saved), TRANSIT)
        self.table.horizontalHeaderItem(_col(TRANSIT)).setToolTip(
            tr("在途箱数：已经订了、还没到货的箱数（采购订单跟踪里没到货的单 + RECORD ORDINE 里的订单文件）。"
               "订单跟踪设置里「建议量扣掉在途」打开时（默认）已经从建议里扣掉了；鼠标停在格子上看是哪几张单"))
        self.table.horizontalHeaderItem(_col(SITU)).setToolTip(
            tr("待核对的商品属于哪一类：Griglia 商品批发商都没有 / 非 Griglia 商品批发商都没有 / 非 Griglia 商品批发商有；正常的空着。右键表头可以只看某一类"))
        if self.cfg.get("check_columns_ver") != _LAYOUT_VER:
            self.cfg.pop("check_widths", None)        # 第 9 版重新设计了：旧的列宽（名称列太窄等）也不用了
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
        self.table.cellDoubleClicked.connect(self._open_transit)      # 双击「在途箱数」：看是哪几张单、标到货
        self.table.cellDoubleClicked.connect(self._open_card)         # 双击商品：商品详情窗口
        self.table.horizontalHeaderItem(_col(3)).setToolTip(tr("双击打开商品详情（入库记录、每天销量、各店库存）；名称红色 = 门店在促销"))
        self.table.horizontalHeaderItem(_col(TRANSFER_COL)).setText(tr("调货") + " ✎")
        self._plan_timer = QTimer()
        self._plan_timer.setSingleShot(True)
        self._plan_timer.setInterval(250)
        self._plan_timer.timeout.connect(self._replan_in_place)
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
        log = self.win.log
        price_dir = self._price_dir()
        box_algo, box_cfg = self._algo(), copy.deepcopy(self.cfg)
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
        self._task = dlg
        dlg.show()

        def work():
            # 业务步骤都在 commands.run_check（不依赖界面，命令行 / 以后的服务器也用它）；这里只管进度窗口和结果
            import commands as CMD
            try:
                rows, meta = CMD.run_check(self.settings, lists, price_dir=price_dir, log=log, progress=dlg,
                                           days=days, cycle=cycle, use_cfg=use_cfg, algo=algo)
                init_boxes(rows, None, box_algo, box_cfg)       # 建议箱数也在这里算好（1000 多行，别在界面线程算）
                meta["_boxed"] = True
                dlg.post_finished(True, tr("完成：{0} 个商品", len(rows)))
                self.sig.done.emit((rows, None, meta))
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
        if not meta.pop("_boxed", False):
            init_boxes(rows, None, self._algo(), self.cfg)
        self.rows, self.meta = rows, meta
        self.transit = meta.get("transit")            # 在途（检查时读好的，建议箱数里扣过了）
        # 人工改的调货 {(门店, 商品编号): {"from", "pieces", "auto"} 或 0 = 不调}；改过一次就冻结（别的行不再跟着变）
        self.transfer_ov = {}
        self.transfer_frozen = False
        self._by_key = {(x.get("store"), str(x.get("code"))): x for x in rows}
        self._cur_i = None
        d = meta["days"]
        for i in range(4):
            self.ctl.set_title(_col(5 + i), tr("{0}天", d[i]), tr("最近 {0} 天销量", d[i]))
        # 一律按箱看：清单箱数（清单按件的折成箱）→ 确定箱数
        self.ctl.set_title(_col(11), tr("清单"), tr("门店清单要的箱数"))
        self.ctl.sort_col, self.ctl.sort_desc = _col(0), False     # 要看的排前面，照清单订的在最后
        self._fill_prob()
        self._build_chips(meta["stores"])
        self._fill_suppliers()
        self._apply_forced()
        self.ctl.filters = {}
        self._replan(render=False)
        self._fill()
        self._load_transit()                            # 在途列：后台读（检查时一般已经读好，这里直接返回）
        # 不急的：每月销量（查数据库）、商品详情窗口（建窗口 + 连数据库）等界面空下来再做，
        # 不和「后台任务完成」弹窗、打开检查窗口抢时间（2026-11-07 用户：跳过去会卡一会）
        self._m24, self._m24_done = {}, set()           # 上一次检查的每月销量作废（读好之前详情栏显示「正在读」）
        self._m24_token = getattr(self, "_m24_token", 0) + 1
        from erp_widgets import when_idle
        when_idle(self._after_done, quiet_ms=1500)
        self.summary_row.show()
        self.window.setWindowTitle(tr("检查门店订货清单 — {0} · {1} 个商品",
                                      "、".join(store_label(k) for k in meta["stores"]), len(rows)))
        done = tr("✔ {0}：{1} 个商品", "、".join(store_label(k) for k in meta["stores"]), len(rows))
        self.msg.setText(done)
        from erp_widgets import ask_return
        ask_return(self.win, getattr(self, "_task", None), tr("订货清单检查完成") + "\n" + done, self.window.bring_up)

    def _after_done(self):
        if not self.rows:
            return
        self._load_months()                             # 详情栏的近两年每月销量：后台读
        try:                                            # 商品详情窗口：先在后台连好数据库，第一次双击就快
            from product_window import ProductWindow
            if self._card is None:
                self._card = ProductWindow(self.window, self.ui, self.settings)
            self._card._cache.clear()
            self._card.warm_up()
        except Exception:
            pass

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
                      self._after_info(x)[0],
                      _days(_cover(x, x["file_pcs"])) if (x.get("file_pcs") or 0) > 0 else "",
                      R.STORES.get(x.get("store"), ""), self._change_text(x), self._transfer_text(x),
                      "；".join(self._reason_lines(x)), x.get("sup_code") or "", x.get("sup_name") or "", self._monthly(x),
                      self._amount_text(x), _situation(x), self._transit_text(x)])

    def _amount(self, x):
        """这一行要花多少钱：确定箱数（有调货的按调货以后的箱数）× 每箱件数 × 件价"""
        m = self._moves_in.get((x.get("store"), str(x.get("code"))))
        b = m["colli_after"] if m else x.get("fbox") or 0
        try:
            p = float(x.get("price") or 0)
        except (TypeError, ValueError):
            return 0.0
        return b * _pk(x) * (p if p == p else 0.0)

    def _amount_text(self, x):
        a = self._amount(x)
        return f"{a:,.2f}" if a > 0 else ""

    def _key(self, i, c):
        """排序：「判断」按要看的先后（不订、少订、调货、多订 … 照清单订最后），同一类里金额大的在前"""
        x = self.rows[i]
        if c == _col(0):
            rank = VERDICT_RANK.get(self._verdict(x)[0], 9)
            if x.get("phantom"):
                rank = min(rank, 2.5)                  # 库存可能不准：排在前面要看的里
            return rank * 1e7 - min(self._amount(x), 9e6)
        if c == AMOUNT_COL:
            return self._amount(x)
        if c == _col(TRANSIT):
            return self._transit_boxes(x)
        return None

    # ---------- 在途（已经订了、还没到货）----------
    def _transit_of(self, x):
        import order_record as REC
        return REC.lookup(getattr(self, "transit", None), x.get("store"), x.get("ean"), x.get("code"))

    def _transit_boxes(self, x):
        """在途箱数：扣过的按这一行的箱规折算（在途件数 ÷ 每箱件数，整箱）；只显示的用订单文件里的箱数"""
        tp, pk = float(x.get("transit_pcs") or 0), _pk(x)
        if tp > 0 and pk > 0:
            return float(int(tp / pk + 1e-9))
        t = self._transit_of(x)
        return float(t["colli"]) if t else 0.0

    def _transit_text(self, x):
        n = self._transit_boxes(x)
        return f"{n:g}" if n else ""

    def _load_transit(self):
        """采购订单跟踪里没到货的单 + RECORD ORDINE 订单文件 → 每家店每个商品在途几箱（后台线程，第一次要读很多文件）"""
        import order_record as REC
        self.transit = (self.meta or {}).get("transit")
        if self.transit is not None:
            return                                      # 检查时已经读好（建议箱数里扣过了）
        if not REC.tracking_folder(self.settings):
            return
        self._tr_token = getattr(self, "_tr_token", 0) + 1
        token, settings = self._tr_token, self.settings

        def work():
            try:
                self.sig.transit.emit((token, REC.cached(settings), None))
            except Exception as e:
                self.sig.transit.emit((token, None, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _transit_arrived(self, payload):
        token, res, err = payload
        if token != getattr(self, "_tr_token", None) or not hasattr(self, "ctl"):
            return
        if err:
            self.win.log(tr("⚠ 在途（RECORD ORDINE）读不了：{0}", err))
            return
        self.transit = res
        n = sum(1 for x in self.rows if self._transit_of(x))
        if n:
            self.win.log(tr("在途：{0} 个商品已经订了还没到货（在途列）", n))
        self._refresh_rows(list(self.ctl.visible))

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
        sugg = max(0, (x.get("suggested") or 0) - float(x.get("transit_pcs") or 0))
        lines = R.plain_reason(x.get("inv0"), x.get("daily0"), x.get("cycle"), sugg,
                               boxes=math.ceil(sugg / pack) if pack > 0 and sugg > 0 else None, target=x.get("target"),
                               in_db=x.get("in_db", True), transfer=tin, pack=pack)
        if x.get("protect") == "new":
            lines.insert(0, tr("新品保护：近两个月才开始卖（或还没卖过），销量还说明不了什么，按门店清单订"))
        elif x.get("protect") == "empty":
            lines.insert(0, tr("缺货保护：店里已经没货，门店又点名要，至少订 1 箱"))
        elif x.get("protect") == "a_low":
            lines.insert(0, tr("A 类保护：卖得好的商品，库存不够卖一个订货周期，至少订 1 箱"))
        cz = x.get("censored")
        if cz:
            lines.insert(0, tr("断货补算：最近 {0} 周一件没卖、库存只剩 {1:g} 件，多半是货架上没货（不是卖不动了）；按过去一年的平均算，月销 {2:.1f} 件（不是 {3:.1f}）",
                               cz["weeks"], float(x.get("inv0") or 0), cz["long"] * 30, cz["orig"] * 30))
        ph = x.get("phantom")
        if ph:
            line = tr("库存可能不准：账面有 {0:g} 件，但已经 {1} 天一件没卖（平时这段时间能卖 {2:.0f} 件），门店又点名要，货架上多半没货了",
                      float(x.get("inv0") or 0), ph["days"], ph["expect"])
            if x.get("protect") == "phantom":
                line += "；" + (tr("A 类：按门店清单订") if (x.get("tier") or "") == "A" else tr("库存按 0 重新算"))
            else:
                line += "；" + tr("C 类卖得慢，只提醒，订多少你决定")
            lines.insert(0, line)
        if x.get("transit_pcs"):
            t_ = self._transit_of(x) or {}
            line = tr("在途：已经订了 {0:g} 箱（{1:g} 件）还没到货", t_.get("colli") or 0, x["transit_pcs"])
            if x.get("transit_cut"):
                line += "，" + tr("建议少订 {0} 箱", x["transit_cut"])
            lines.insert(0, line)
        if x.get("kept"):
            rr = review_rules(self.cfg)
            lines.append(tr("系统算出来只要 {0:,} 箱，但现在库存只够卖 {1} 天（不到 {2:g} 天），照门店清单 {3:,} 箱订",
                            x.get("sys_box") or 0, _days(x.get("cover_now") or 0) or "0", rr["keep_days"], x["lbox"]))
        elif x.get("limited"):
            rr = review_rules(self.cfg)
            lines.append(tr("系统算出来只要 {0:,} 箱；现在库存够卖 {1} 天，还不算多（够卖 {2:g} 天以上才会砍得更多），少订最多砍掉 {3:.0%}，按 {4:,} 箱订",
                            x.get("sys_box") or 0, _days(x.get("cover_now") or 0) or "0", rr["free_days"], rr["max_cut"], x["sbox"]))
        elif x.get("sys_box") is not None and x["sys_box"] > x["lbox"]:
            lines.append(tr("系统算出来要 {0:,} 箱，多于门店清单，按清单 {1:,} 箱订（先不多订）", x["sys_box"], x["lbox"]))
        if x.get("in_db", True) and x.get("sbox") is not None and x["lbox"] != x["sbox"]:
            lines.append(tr("门店清单报了 {0:,} 箱", x["lbox"]))
        if _edited(x):
            lines.append(tr("现在确定 {0:,} 箱（手动改过）", x["fbox"]))
        return lines

    def _marks(self, x):
        """判断后面的提醒：慢销（订了要卖很久）；库存可能不准（虚库存）"""
        if not x.get("in_db", True):
            return []
        out = R.review_marks(x.get("target") is not None, x["final"] > 0, self._arrive_days(x), x.get("store_inv"))
        cz = x.get("censored")
        if cz:
            out.append(("censored", tr("断货补算"),
                        tr("最近 {0} 周没卖、库存只剩 {1:g} 件：按过去一年平均（月销 {2:.1f}）算", cz["weeks"],
                           float(x.get("inv0") or 0), cz["long"] * 30)))
        ph = x.get("phantom")
        if ph:
            out.append(("phantom", tr("库存可能不准"),
                        tr("账面有 {0:g} 件，但已经 {1} 天一件没卖（平时能卖 {2:.0f} 件），货架上多半没货了", float(x.get("inv0") or 0),
                           ph["days"], ph["expect"])))
        return out

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
        return _cover(x, have - out + float(x.get("transit_pcs") or 0))       # 在途到了也算

    def _change_text(self, x):
        """确定箱数比门店清单多 / 少几箱（一样就空着，表格干净）"""
        d = int(x["fbox"]) - int(x["lbox"])
        return f"{d:+,}" if d else ""

    def _need_pcs(self, x):
        """调货用的「真正要的件数」：确定箱数没改（= 系统建议）就用系统算的件数（零头才能调），改过就按箱数 × 每箱件数"""
        if x.get("sbox") is not None and x["fbox"] == x["sbox"]:
            pk = _pk(x)
            sug = max(0.0, float(x.get("suggested") or 0) - float(x.get("transit_pcs") or 0))     # 在途扣掉
            return int(min(sug, x["fbox"] * pk) if pk > 0 else sug)
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

    def _open_transit(self, k, c):
        """双击「在途箱数」：这个商品在途的是哪几张单（transit_dialog），到了没标的直接标到货 → 重新扣"""
        if c != _col(TRANSIT):
            return
        i = self.ctl.row_at(k)
        if i is None:
            return
        x = self.rows[i]
        t = self._transit_of(x)
        if not t:
            return
        from transit_dialog import TransitOrdersDialog
        TransitOrdersDialog(self.window, self.settings, self.ui, f"{x['name']} · {R.STORES.get(x.get('store'), '')}", t,
                            on_change=self.reapply_transit, tracking=getattr(self.win, "tracking", None)).exec()

    def reapply_transit(self, transit):
        """在途变了：重新扣在途（手动改过确定箱数的行只更新在途，不动箱数）"""
        import order_record as REC
        self.transit = transit
        self.meta["transit"] = transit
        deduct = REC.deduct_on(self.settings)
        algo = self._algo()
        for x in self.rows:
            t_ = REC.lookup(transit, x.get("store"), x.get("ean"), x.get("code")) if (transit and deduct) else None
            new = float(t_["pcs"]) if t_ else None
            if new == x.get("transit_pcs"):
                continue
            edited = _edited(x)
            if new is None:
                x.pop("transit_pcs", None)
            else:
                x["transit_pcs"] = new
            if not edited and "fbox" in x:
                init_boxes([x], None, algo, self.cfg)
        self._replan_in_place()

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
            self.win.show_toast(tr("DETERSIVO采购没有启用门店间调货（设置 → 门店间调货…）"))
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

    # ---------- 右边详情栏 ----------
    # 2026-10-25 重新设计（用户：柱状图、服务水平这些从来不看）：只放判断这一行要用的 ——
    #   商品图和名称 → 这一行怎么订（清单 → 确定、订后够卖、金额）→ 库存 / 月销 / 现在够卖
    #   → 近两年每月销量（今年和去年同月对比，看季节和趋势）→ 为什么 → 其他门店
    def _build_drawer(self):
        from PySide6.QtWidgets import QScrollArea
        from erp_widgets import MonthGrid
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFixedWidth(360)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setFrameShape(QFrame.NoFrame)
        d = QWidget()
        d.setObjectName("Drawer")
        v = QVBoxLayout(d)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        self.d_img = QLabel()                     # 商品图（公司图片服务器上有这个条码的图才显示）
        self.d_img.setAlignment(Qt.AlignCenter)
        self.d_img.setMinimumHeight(160)
        self.d_img.hide()
        self.d_title = QLabel(tr("选中一行查看详情"))
        self.d_title.setStyleSheet("font-size:15px; font-weight:700;")
        self.d_title.setWordWrap(True)
        self.d_codes = QLabel("")
        self.d_codes.setObjectName("Faint")
        self.d_codes.setWordWrap(True)
        self.d_codes.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for w in (self.d_img, self.d_title, self.d_codes):
            v.addWidget(w)
        self.d_more = QPushButton(tr("商品详情 ›"))       # 和双击商品一样：入库记录、每天销量、各店库存
        self.d_more.setObjectName("CrumbLink")
        self.d_more.setCursor(Qt.PointingHandCursor)
        self.d_more.setToolTip(tr("入库记录、每天销量、各店库存（双击表格里的商品也能打开）"))
        self.d_more.clicked.connect(lambda: self.open_card(self._cur_i) if self._cur_i is not None else None)
        v.addWidget(self.d_more, 0, Qt.AlignLeft)
        # 这一行怎么订：清单 3 箱 → 确定 1 箱（大字），下面一行订后够卖、金额
        dec = QFrame()
        dec.setObjectName("MiniCard")
        dv = QVBoxLayout(dec)
        dv.setContentsMargins(12, 10, 12, 10)
        dv.setSpacing(2)
        self.d_verdict = QLabel("")
        self.d_verdict.setStyleSheet("font-weight:700; font-size:12px;")
        self.d_boxes = QLabel("—")
        self.d_boxes.setStyleSheet("font-size:20px; font-weight:700;")
        self.d_after = QLabel("")
        self.d_after.setWordWrap(True)
        for w in (self.d_verdict, self.d_boxes, self.d_after):
            dv.addWidget(w)
        v.addWidget(dec)
        g = QGridLayout()
        g.setSpacing(8)
        self.minis = []
        for i, lab in enumerate((tr("库存"), tr("月销"), tr("现在够卖"))):
            f = QFrame()
            f.setObjectName("MiniCard")
            fv = QVBoxLayout(f)
            fv.setContentsMargins(10, 8, 10, 8)
            fv.setSpacing(1)
            a_ = QLabel(lab)
            a_.setObjectName("MiniLabel")
            b_ = QLabel("—")
            b_.setObjectName("MiniValue")
            c_ = QLabel("")
            c_.setObjectName("MiniLabel")
            for w in (a_, b_, c_):
                fv.addWidget(w)
            g.addWidget(f, 0, i)
            self.minis.append((b_, c_))
        v.addLayout(g)
        sp = QFrame()
        sp.setObjectName("MiniCard")
        spv = QVBoxLayout(sp)
        spv.setContentsMargins(12, 10, 12, 10)
        hl = QHBoxLayout()
        t = QLabel(tr("近两年每月销量"))
        t.setStyleSheet("font-weight:600; font-size:12px;")
        u = QLabel(tr("件 · 颜色越深卖得越多"))
        u.setObjectName("MiniLabel")
        hl.addWidget(t, 1)
        hl.addWidget(u)
        spv.addLayout(hl)
        self.months_w = MonthGrid()
        spv.addWidget(self.months_w)
        self.months_note = QLabel("")
        self.months_note.setObjectName("MiniLabel")
        self.months_note.setWordWrap(True)
        spv.addWidget(self.months_note)
        v.addWidget(sp)
        why = QFrame()
        why.setObjectName("MiniCard")
        wv = QVBoxLayout(why)
        wv.setContentsMargins(12, 10, 12, 10)
        self.why_t = QLabel(tr("为什么"))
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

    # ---------- 近两年每月销量（检查完以后后台读，不拖慢检查）----------
    def _load_months(self):
        """每家门店这次清单里的商品，最近 24 个月每月销量 → self._m24[(门店, 编号)]（只读查询，后台线程）"""
        self._m24, self._m24_keys = {}, R.month_keys(24)
        self._m24_token = getattr(self, "_m24_token", 0) + 1
        self._m24_done = set()
        token = self._m24_token
        by_store = {}
        for x in self.rows:
            if x.get("in_db", True) and str(x.get("code") or "").isdigit():
                by_store.setdefault(x.get("store"), set()).add(str(x["code"]))
        if not by_store:
            return
        dbs = self.settings.get("db") or None

        def work():
            try:
                db = R.make_db(dbs)
                for sid, codes in by_store.items():       # 促销先查（主表格要标红），每月销量后查
                    try:
                        self.sig.promos.emit((token, sid, R.query_promos(db, sid, codes)))
                    except Exception as e:
                        self.sig.promos.emit((token, sid, {"_error": str(e)}))
                for sid, codes in by_store.items():
                    try:
                        data = R.query_monthly_sales(db, sid, codes)
                    except Exception as e:
                        data = {"_error": str(e)}
                    self.sig.months.emit((token, sid, codes, data))
                db.engine.dispose()
            except Exception as e:
                for sid, codes in by_store.items():
                    self.sig.months.emit((token, sid, codes, {"_error": str(e)}))
        threading.Thread(target=work, daemon=True).start()

    def _promos_arrived(self, payload):
        """本店在促销的商品：x["promo"]，商品 / 条码格标红、「只看促销」"""
        token, sid, data = payload
        if token != getattr(self, "_m24_token", None):
            return
        err = data.pop("_error", None)
        if err:
            self.win.log(tr("⚠ 促销没查到：{0}", err))
            return
        for w in data.pop("_warn", None) or ():
            self.win.log("⚠ " + w)
        self.win.log(tr("[检查清单] {0}：{1} 个商品在促销", R.STORES.get(sid, sid), len(data)))
        idx = []
        for i, x in enumerate(self.rows):
            if x.get("store") == sid:
                p = data.get(str(x.get("code")))
                if p or x.get("promo"):
                    x["promo"] = p
                    idx.append(i)
        if idx:
            self._refresh_rows(idx)
            self._summary()
        if self._card is not None and self._card.isVisible() and self._card.x is not None and \
                self._card.x.get("store") == sid:
            self._card._show_promo(self._card.x.get("promo"))

    def _promo_tip(self, x):
        p = x.get("promo")
        if not p:
            return ""
        return promo_text(p) + "\n" + tr("促销期间销量会偏高")

    def _open_card(self, k, c):
        """双击商品 / 条码 / 编号：打开商品详情窗口（入库记录、每天销量、各店库存…）"""
        if c not in (_col(1), _col(2), _col(3)):
            return
        i = self.ctl.row_at(k)
        if i is not None:
            self.open_card(i)

    def open_card(self, i):
        if not (0 <= i < len(self.rows)):
            return
        x = self.rows[i]
        from product_window import ProductWindow
        if self._card is None:
            self._card = ProductWindow(self.window, self.ui, self.settings)
        key = (x.get("store"), str(x.get("code")))
        vals = self._m24.get(key) if x.get("store") in getattr(self, "_m24_done", set()) else None
        self._card.show_item(x, (vals, self._m24_keys) if vals is not None else None)

    def _months_arrived(self, payload):
        token, sid, codes, data = payload
        if token != getattr(self, "_m24_token", None):
            return
        err = data.pop("_error", None) if isinstance(data, dict) else None
        for c in codes:
            self._m24[(sid, c)] = None if err else R.monthly_series(data.get(c, {}), self._m24_keys)
        self._m24_done.add(sid)
        if err:
            self._m24_err = err
        if self._cur_i is not None and 0 <= self._cur_i < len(self.rows) and self.rows[self._cur_i].get("store") == sid:
            self._show_months(self.rows[self._cur_i])

    def _show_months(self, x):
        key = (x.get("store"), str(x.get("code")))
        if not x.get("in_db", True):
            self.months_w.set_message(tr("数据库里没有这个商品"))
            self.months_note.setText("")
            return
        if x.get("store") not in getattr(self, "_m24_done", set()):
            self.months_w.set_message(tr("正在读取…"))
            self.months_note.setText("")
            return
        vals = self._m24.get(key)
        if vals is None:
            self.months_w.set_message(tr("没读到（数据库）"))
            self.months_note.setText(getattr(self, "_m24_err", ""))
            return
        self.months_w.set_data(vals, self._m24_keys)
        cur, prev, full = self.months_w.ytd()
        m = self._m24_keys[-1][1]
        note = tr("今年 1–{0} 月 {1:g} 件", m, round(cur, 1))
        if prev:
            note += " · " + tr("去年同期 {0:g} 件（{1:+.0%}）", round(prev, 1), cur / prev - 1)
        else:
            note += " · " + tr("去年同期没卖")
        note += "\n" + tr("去年全年 {0:g} 件 · * 本月到今天", round(full, 1))
        self.months_note.setText(note)

    def _show_drawer(self, i):
        """详情栏：这一行怎么订 + 判断要用的几个数 + 近两年每月销量 + 为什么 + 其他门店"""
        if not (0 <= i < len(self.rows)):
            return
        T = self.ui.T
        x = self.rows[i]
        code = str(x.get("code"))
        sid = x.get("store")
        self.d_title.setText(x.get("name") or code)
        import product_images as PI
        PI.set_label_image(self.d_img, x.get("ean"), 160, self.settings)
        pack = x.get("pack") or 0
        try:
            price = float(x.get("price") or 0)
            price = price if price == price else 0
        except (TypeError, ValueError):
            price = 0
        parts1 = [p for p in (R.STORES.get(sid, ""), x.get("sup_name") or "", (x.get("tier") or "") and tr("{0} 类", x["tier"])) if p]
        parts2 = [p for p in (code, x.get("ean") or "", f"Cod.Forn. {x['sup_code']}" if x.get("sup_code") else "") if p]
        parts3 = [p for p in (f"{pack:g} " + tr("件/箱") if pack else "", f"€{price:.3f}/" + tr("件") if price else "",
                              tr("{0} 报价", x["quote_sup"]) if x.get("quote_sup") else "") if p]
        self.d_codes.setText("\n".join(" · ".join(ps) for ps in (parts1, parts2, parts3) if ps))
        # 这一行怎么订
        kind, vt = self._verdict(x)
        self.d_verdict.setText(vt)
        self.d_verdict.setStyleSheet(f"font-weight:700; font-size:12px; color:{T(VERDICT_TOKEN.get(kind, 'text'))};")
        m_in = self._moves_in.get((sid, code))
        boxes = tr("清单 {0:,} 箱  →  确定 {1:,} 箱", x["lbox"], x["fbox"])
        if m_in:
            boxes += "\n" + tr("调进 {0} 件 ← {1}，还订 {2} 箱", m_in["pieces"], R.STORES.get(m_in["from"], m_in["from"]),
                                m_in["colli_after"])
        self.d_boxes.setText(boxes)
        text, tok, tip = self._after_info(x)
        amt = self._amount(x)
        bits = []
        if text:
            bits.append((tr("订后够卖 {0} 天", text) if self._arrive_days(x) is not None else tr("现在的库存够卖 {0} 天", text)))
        if amt > 0:
            bits.append(f"€{amt:,.2f}")
        self.d_after.setText(" · ".join(bits) + (f"\n{tip}" if tip and tok in ("err", "warn") else ""))
        self.d_after.setStyleSheet(f"color:{T(tok) if tok in ('err', 'warn') else T('text')};")
        (a1, a2), (b1, b2), (c1, c2) = self.minis
        if x.get("in_db", True):
            inv = x.get("store_inv")
            inv = float(inv) if isinstance(inv, (int, float)) else 0.0     # 显示原值（计算时负数当 0）
            a1.setText(f"{inv:g}")
            a2.setText(tr("件") + ("  " + tr("（账面负数）") if inv < 0 else ""))
            mo = self._monthly(x)
            b1.setText(mo or "0")
            b2.setText(tr("件 / 月"))
            cv = _cover(x)
            c1.setText(_days(cv) or "0")
            cyc = x.get("cycle")
            c2.setText(tr("天 · 周期 {0:g} 天", cyc) if isinstance(cyc, (int, float)) else tr("天"))
        else:
            for m1, m2 in self.minis:
                m1.setText("—")
                m2.setText("")
        self._show_months(x)
        if not x.get("in_db", True):
            self.why_t.setText(tr("为什么"))
        elif x.get("sbox") is not None:
            self.why_t.setText(tr("为什么建议 {0:g} 箱", x["sbox"]))
        else:
            self.why_t.setText(tr("为什么"))
        self.why.setText("\n".join(["· " + l for l in self._reason_lines(x)] + ["⚠ " + m[2] for m in self._marks(x)] +
                                   (["🏷 " + self._promo_tip(x)] if x.get("promo") else [])))
        self.d_title.setStyleSheet("font-size:15px; font-weight:700;" + (f" color:{T('err')};" if x.get("promo") else ""))
        # 其他门店：库存、够卖几天、这次定了几箱
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
            cv = _cover({"in_db": True, "inv0": d.get("inv"), "daily0": d.get("daily")})
            l = QLabel(f"{R.STORES.get(osid, osid)}    " +
                       tr("库存 {0:g} · 够卖 {1} 天 · 订 {2:g} 箱", float(d.get("inv") or 0), _days(cv) or "—", on))
            l.setObjectName("Faint" if not on else "")
            self.others.addWidget(l)
        if not self.others.count():
            l = QLabel(tr("没有其他门店的数据"))
            l.setObjectName("Faint")
            self.others.addWidget(l)

    def _replan_in_place(self):
        """改了确定箱数以后重算调货，但表格不重新排序 / 筛选：改过的行留在原地（点表头或换筛选时才重排）"""
        self._replan(render=False)
        self._refresh_rows(list(self.ctl.visible))
        self._fill_prob()
        self._summary()

    def _replan(self, render=True):
        """按现在的确定箱数重新算门店间调货（改了以后稍等一下再算，不卡）"""
        missing = [x for x in self.rows if "fbox" not in x]
        if missing:                                    # 还没按箱整理过的行（保险）
            init_boxes(missing, None, self._algo(), self.cfg)
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
        has_grid = any(x.get("in_grid") is not None for x in self.rows)
        total, tips = 0, []
        for kind, (ti, page, t) in self.prob.items():
            allk = [i for i, x in enumerate(self.rows) if _tab_of(_problem(x)) == kind]
            idx = [i for i in allk if (sid is None or self.rows[i].get("store") == sid) and
                   (not text or text in f"{self.rows[i].get('code')} {self.rows[i].get('ean')} {self.rows[i].get('name')}".lower())]
            title = _prob_title(kind, len(allk), has_grid)
            self.prob_tabs.setTabText(ti, title)
            self.prob_tabs.setTabVisible(ti, bool(allk))
            if allk:
                total += len(allk)
                tips.append(title)
            t.blockSignals(True)
            t.setUpdatesEnabled(False)                 # 几百行：填完再一次画出来
            QTimer.singleShot(0, lambda t=t: t.setUpdatesEnabled(True))     # 万一中途出错也会恢复
            t.setRowCount(len(idx))
            for r, i in enumerate(idx):
                x = self.rows[i]
                none = _problem(x) in ("noquote", "nogrid_noq")
                self.ui.table_set_row(t, r, [R.STORES.get(x.get("store"), ""), x.get("ean") or "", x.get("name") or "",
                                             x.get("sup_name") or "", tr("都没有") if none else tr("有"),
                                             f"{x['lbox']:,}", f"{x['fbox']:,}"])
                t.item(r, 0).setData(Qt.UserRole, i)
                t.item(r, 2).setToolTip(f"{x.get('name') or ''}\n{x.get('code') or ''} · {x.get('ean') or ''}" +
                                        ("\n" + _issue_text(x) if _issue(x) else ""))
                t.item(r, 4).setForeground(QColor(self.ui.T("err") if none else self.ui.T("ok")))
                for c in range(t.columnCount()):
                    it = t.item(r, c)
                    if c == 6:
                        it.setFlags(it.flags() | Qt.ItemIsEditable)
                        it.setToolTip(tr("点 − / ＋ 改 1 箱，双击直接输入，0 = 不订"))
                        self._style_prob_cell(it, x)
                    else:
                        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            t.setUpdatesEnabled(True)
            t.blockSignals(False)
            if getattr(t, "_hf", None) is not None and t._hf.active():
                t._hf.reapply()
            t.setColumnHidden(0, len((self.meta or {}).get("stores") or []) < 2)
        self.prob_btn.setText(tr("⚠ 待核对（{0}）", total))
        self.prob_btn.setToolTip("\n".join(tips) + "\n" + tr("点一下打开"))
        self.prob_btn.setVisible(bool(total))
        self._prob_filling = False

    def _nogrid_none(self, kind="nogrid"):
        """这个问题列表（不在 Griglia / 没报价）里的商品全部不订"""
        n, idx = 0, []
        for i, x in enumerate(self.rows):
            if _tab_of(_problem(x)) == kind and x.get("fbox"):
                _set_box(x, 0)
                n += 1
                idx.append(i)
        self._fill_prob()
        self._refresh_rows(idx)                       # 主表格里这些行也标黄
        self._summary()
        self.win.show_toast(tr("{0} 个商品改成不订", n))

    def _open_prob(self, kind=None):
        """点「⚠ 待核对」：打开窗口（不挡住检查窗口，可以一边对照一边改），停在第一个有商品的标签页"""
        self._fill_prob()
        tabs = self.prob_tabs
        if kind is not None and kind in self.prob:
            tabs.setCurrentIndex(self.prob[kind][0])
        elif not tabs.isTabVisible(tabs.currentIndex()):
            for k in range(tabs.count()):
                if tabs.isTabVisible(k):
                    tabs.setCurrentIndex(k)
                    break
        dlg = self.prob_dlg
        from erp_widgets import fit_to_screen
        fit_to_screen(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _prob_edited(self, kind, item):
        """待核对窗口里改了确定箱数：只更新这一格（不重画整张表，改得快），合计停手 0.3 秒再算。
        等编辑框关掉再改格子（编辑中途重画会卡住）"""
        if self._prob_filling or item.column() != 6:
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
        row, x = item.row(), self.rows[i]

        def later():
            cell = t.item(row, 6)
            if cell is None:
                return
            self._prob_filling = True
            t.blockSignals(True)
            cell.setText(f"{x['fbox']:,}")
            self._style_prob_cell(cell, x)
            t.blockSignals(False)
            self._prob_filling = False
            self._refresh_rows([i])                     # 主表格里这一行也跟着变（改过的标黄）
            self._sum_timer.start()
        QTimer.singleShot(0, later)

    def _style_prob_cell(self, it, x):
        """待核对窗口的确定箱数格：改过的标黄（和主表格一样）"""
        T = self.ui.T
        ch = _edited(x)
        it.setForeground(QColor(T("warn") if ch else T("accent") if x["fbox"] > 0 else T("faint")))
        it.setBackground(QColor(T("warn_soft")) if ch else QColor(0, 0, 0, 0))

    def _export_prob_one(self):
        """导出门店报告：五家店合成一个文件，每家店一个工作表（发到大家都在的群里用）"""
        start = self.win.ws_root() if hasattr(self.win, "ws_root") else ""
        path, _ = QFileDialog.getSaveFileName(self.prob_dlg, tr("导出门店报告"), os.path.join(
            start or "", f"Articoli_da_verificare_{datetime.now():%d%m}.xlsx"), tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        try:
            p = write_problem_report(self.rows, path, store=None, url=self.settings.get("image_url") or None,
                                     sources=self.meta.get("quote_labels"))
        except Exception as e:
            QMessageBox.warning(self.prob_dlg, tr("导出门店报告"), tr("导出失败：{0}", e))
            return
        if p:
            self.win.log(tr("✔ 门店报告：{0}", p))
            self.win.show_toast(tr("已导出：{0}", os.path.basename(p)))
            from erp_widgets import open_file                  # 一个文件：直接打开（2026-11-07 用户）
            open_file(self.prob_dlg, p, tr("导出门店报告"))

    def _export_prob(self):
        """待核对窗口「导出门店报告」：选一个文件夹，每家店一个意大利文报告（可以直接发给门店）"""
        start = self.win.ws_root() if hasattr(self.win, "ws_root") else ""
        folder = QFileDialog.getExistingDirectory(self.prob_dlg, tr("导出门店报告"), start or "")
        if not folder:
            return
        url = self.settings.get("image_url") or None
        done, errs = [], []
        for sid in sorted({x.get("store") for x in self.rows if _problem(x)}, key=lambda v: v or 0):
            try:
                p = write_problem_report(self.rows, os.path.join(folder, report_name(sid)), store=sid, url=url,
                                         sources=self.meta.get("quote_labels"))
                if p:
                    done.append(p)
                    self.win.log(tr("✔ 门店报告：{0}", p))
            except Exception as e:
                errs.append(f"{R.STORES.get(sid, sid)}：{e}")
        if errs:
            QMessageBox.warning(self.prob_dlg, tr("导出门店报告"), tr("导出失败：{0}", "\n".join(errs)))
        if done:
            self.win.show_toast(tr("已导出 {0} 家门店的报告", len(done)))
            try:
                os.startfile(folder)                    # Windows：打开文件夹，方便直接发
            except Exception:
                pass

    def _store_sel(self):
        b = self.store_group.checkedButton() if hasattr(self, "store_group") else None
        v = b.property("sid") if b is not None else -1
        return None if v is None or int(v) < 0 else int(v)

    def _apply_forced(self):
        """程序自己藏的列：清单按件时的箱数列；只有一家门店 / 只看一家门店时的「门店」列"""
        one = len((self.meta or {}).get("stores") or []) < 2 or self._store_sel() is not None
        off = not TR.config(TR.wholesale_raw(self.settings)).get("enabled")    # 没开调货：调货列全是「不调」，藏起来
        self._forced = set(getattr(self, "_unit_forced", set())) | ({22} if one else set()) | ({TRANSFER_COL} if off else set())
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
        n["phantom"] = sum(1 for x in self.rows if x.get("phantom") and not _problem(x))
        n["promo"] = sum(1 for x in self.rows if x.get("promo"))
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
        n_edit = sum(1 for x in self.rows if _edited(x))
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
        text = self.flt.text().strip().lower()
        view = self.view.currentData()
        kind = self._verdict(x)[0]
        hide = (view == "changed" and kind not in ("transfer", "more", "less", "none")) or \
            (view in ("transfer", "more", "less", "none") and kind != view) or \
            (view == "phantom" and not x.get("phantom")) or (view == "promo" and not x.get("promo"))
        return hide or \
               (bool(text) and text not in f"{x['code']} {x['ean']} {x['name']} {x.get('sup_code') or ''} {x.get('sup_name') or ''}".lower())

    def _fill(self):
        missing = [x for x in self.rows if "fbox" not in x]
        if missing:
            init_boxes(missing, None, self._algo(), self.cfg)
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
        tri = self._transit_of(x)
        if tri:                                   # 在途：蓝字，鼠标提示哪几张单
            import order_record as REC
            it = t.item(k, _col(TRANSIT))
            it.setForeground(QColor(T("accent")))
            it.setToolTip(tr("已经订了、还没到货 {0:g} 箱（{1:g} 件）：", tri["colli"], tri["pcs"]) + "\n" + REC.transit_text(tri)
                          + "\n" + tr("双击看订单、标到货"))
        if x.get("promo"):                       # 门店在促销：商品名 / 条码标红
            ptip = self._promo_tip(x)
            for pc in (_col(2), _col(3)):
                it = t.item(k, pc)
                it.setForeground(QColor(T("err")))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            t.item(k, _col(3)).setToolTip(f"{x['name']}\nEAN {x['ean'] or '—'}\n🏷 {ptip}")
            t.item(k, _col(2)).setToolTip("🏷 " + ptip)
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
        st = t.item(k, _col(SITU))
        if st is not None and st.text():
            st.setForeground(QColor(T("err") if _problem(x) in ("noquote", "nogrid_noq") else T("warn")))
            st.setToolTip(dict(PROB_KINDS_TIPS()).get(_problem(x), "") + ("\n" + _issue_text(x) if _issue(x) else ""))
        self._style_edit(i, k)

    def _after_info(self, x):
        """订后够卖那一格：(文字, 颜色, 鼠标提示)。不订的显示现在的库存够卖几天"""
        if not x.get("in_db", True):
            return "", "text", ""
        cyc = float(x.get("cycle") or 0)
        limit = self._limit()
        v = self._arrive_days(x)
        if v is None:                                  # 不订、也不调：到下次订货就靠现在的库存
            v = _cover(x, float(x.get("transit_pcs") or 0))            # 在途到了也算
            if v is None or not float(x.get("daily0") or 0):
                return "", "text", ""
            if cyc and v < cyc:
                return _days(v), "err", tr("不订的话，现在的库存只够卖 {0} 天，不到一个订货周期（{1:g} 天）", _days(v), cyc)
            return _days(v), "faint", tr("不订：现在的库存够卖 {0} 天", _days(v))
        if cyc and v < cyc:
            return _days(v), "err", tr("订完只够卖 {0} 天，不到一个订货周期（{1:g} 天），下次到货前可能卖断", _days(v), cyc)
        if v > limit:
            return _days(v), "warn", tr("订完要很久才卖得完（超过 {0} 天），可能压货", int(limit))
        return _days(v), "text", ""

    def _limit(self):
        """凑单天数（配货设置）：订后够卖超过它标黄"""
        return float((self.settings.get("store_orders") or {}).get("fill", {}).get("max_stock_days") or 60)

    def _style_edit(self, i, k):
        self.ctl.ensure(k)                      # lazy：这一行可能还没画
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
        changed = _edited(x)
        c.setForeground(QColor(T("warn") if changed else T("accent") if x["fbox"] > 0 else T("faint")))
        c.setBackground(QColor(T("warn_soft")) if changed else QColor(0, 0, 0, 0))
        c.setToolTip((tr("手动改过：系统建议 {0:,} 箱", x["sbox"]) if x.get("sbox") is not None else
                      tr("手动改过：门店清单 {0:,} 箱", x["lbox"])) if changed else tr("双击修改箱数，0 = 不订"))
        self.table.item(k, PCS_COL).setText(f"{x['final']:,}" if _pk(x) > 0 else "")
        # 订后够卖：跟着确定箱数变。红 = 不到一个订货周期（下次到货前卖断），黄 = 超过凑单天数（压货）
        a = self.table.item(k, AFTER_COL)
        text, tok, tip = self._after_info(x)
        a.setText(text)
        a.setForeground(QColor(T(tok)))
        f = a.font()
        f.setBold(tok in ("err", "warn"))
        a.setFont(f)
        a.setToolTip(tip)
        am = self.table.item(k, AMOUNT_COL)
        if am is not None:
            am.setText(self._amount_text(x))
        ch = self.table.item(k, _col(CHANGE_COL))
        if ch is not None:
            d = int(x["fbox"]) - int(x["lbox"])
            ch.setText(self._change_text(x))
            ch.setForeground(QColor(T("accent") if d > 0 else T("warn") if d < 0 else T("faint")))

    def _fill_row_safe(self, i, k):
        """表格（lazy 时一批批）画一行：画的时候改格子不算人工修改"""
        was = self._filling
        self._filling = True
        try:
            self._fill_row(i, k)
        finally:
            self._filling = was

    def _refresh_rows(self, idx):
        """只重画这几行（改一个调货不用整张表重画）；很多行时：看得见的马上画，其余的一批批重画"""
        if len(idx) > 80:
            ks = [k for k in (self.ctl.pos.get(i) for i in idx) if k is not None]
            if self.ctl.invalidate(ks):
                if self._cur_i in idx:
                    self._show_drawer(self._cur_i)
                return
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
        n_edit = sum(1 for x in self.rows if _edited(x))
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
            return
        from erp_widgets import open_file
        open_file(self.win, path, tr("导出 Excel"))

    def _price_dir(self):
        """共用的供应商报价文件夹：比价根目录（或现在的总文件夹）下面的 distribuire"""
        import workspace as W
        root = self.win.ws_root() if hasattr(self.win, "ws_root") else self.batch_root()
        return W.price_dir(root) if root else ""

    def _read_quotes(self, price_dir, log):
        """读 price_dir 里的全部报价（逻辑在 commands.read_quotes；Griglia 页也用这个）"""
        import commands as CMD
        return CMD.read_quotes(self.settings, price_dir, log)

    def _apply_quotes(self, rows, price_dir, log, files=False):
        import commands as CMD
        return CMD.apply_quotes(self.settings, rows, price_dir, log, files=files)

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
        tag = datetime.now().strftime("%Y%m%d_%H%M")
        day_dir = R.batch_folder(root_dir)
        os.makedirs(day_dir, exist_ok=True)
        made, problems = [], []

        def work():
            """写主订单、复制报价 / 扣减文件、调货单、库存快照、门店报告（全是读写文件，几家店加起来要好几秒）：
            后台线程里做，界面显示进度（2026-11-04 提速：以前在界面线程里做，点了会卡住）"""
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
                try:                                       # 发给门店的提醒：非 Griglia 商品下次别加、这次批发商没货的
                    rep = write_problem_report(self.rows, os.path.join(root, report_name(sid)), store=sid,
                                               url=self.settings.get("image_url") or None, sources=self.meta.get("quote_labels"))
                    if rep:
                        self.win.log(tr("✔ 门店报告：{0}", rep))
                except Exception as e:
                    problems.append(tr("{0}：门店提醒没写上：{1}", R.STORES.get(sid), e))
                self.win.log(tr("✔ 采购主订单：{0}（{1} 个商品）", path, written))
                if skipped:
                    self.win.log(tr("⚠ {0} 个商品没有条码，没写进主订单：{1}", len(skipped),
                                    "、".join(f"{x['code']} {x['name'][:20]}" for x in skipped[:10])))
                made.append((sid, root, written, len(skipped)))
            return all_tf

        from erp_widgets import BusyDialog
        all_tf, err = BusyDialog.run(self.win, tr("确认，开始比价"), tr("正在生成 {0} 家门店的主订单…", len(folders)), work)
        if err:
            problems.append(str(err))
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
