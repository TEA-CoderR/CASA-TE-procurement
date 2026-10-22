"""
采购管理 · 门店配货（品牌供应商，采购配单文件夹里的 SCHEDA 表）

分步工作台：① 选择供应商 → ② 计算订货量 → ③ 核对明细（独立窗口，可改箱数）→ ④ 配货与凑单 → ⑤ 生成订单
  · 每家店单独达到起订量就直接送店；差一点自动凑单；差太多并到 BMS（0 号总仓）订货再分货
  · BMS 不够起订量时：先把同仓门店整单并入，再向同城门店借不紧急的货，最后凑单
  · 订单文件生成到 采购配单\\生成订单\\日期\\第NN次_HHMM\\星期\\（每次配货一个文件夹，不覆盖）
耗时的步骤都有任务进度窗口（可后台运行、计算可取消）。
"""
import os
import copy
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QLineEdit, QComboBox, QHBoxLayout, QVBoxLayout,
                               QGridLayout, QFileDialog, QMessageBox, QSpinBox, QDoubleSpinBox, QTableWidgetItem,
                               QCheckBox, QFrame, QStackedWidget, QScrollArea, QDialog, QDialogButtonBox,
                               QHeaderView, QButtonGroup, QApplication)

from i18n import tr
from settings import save_settings
from theme import T, icon, num_font, open_path
from erp_widgets import (StepBar, TaskDialog, DotsDelegate, DOTS_ROLE, EmptyState, kpi_card, chip, set_chip,
                         RowSorter, SORT_ROLE)
import reorder_service as R
import store_orders as S
from reorder_page import algo_widgets

ALL = "__all__"
STEPS = [("选择供应商", ""), ("计算订货量", ""), ("核对明细", ""), ("配货与凑单", ""), ("生成订单", "")]


class _Sig(QObject):
    listed = Signal(object)
    computed = Signal(object)
    done = Signal(object)


def _scroll(w):
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    w.setObjectName("Content")
    sa.setWidget(w)
    return sa


def _panel(title=None):
    f = QFrame()
    f.setObjectName("Panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 14, 16, 14)
    v.setSpacing(10)
    if title:
        t = QLabel(title)
        t.setObjectName("Section")
        v.addWidget(t)
    return f, v


# =====================================================================
# 设置窗口：计算参数（原程序算法用）、配货设置
# =====================================================================
class ParamDialog(QDialog):
    def __init__(self, page):
        super().__init__(page.win)
        self.setWindowTitle(tr("计算参数"))
        cfg, rcfg = page.cfg, page.settings.get("reorder") or {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        intro = QLabel(tr("这些参数只在「原程序算法」时使用；新算法按「算法设置」里的订货周期和到货天数计算。"))
        intro.setObjectName("Faint")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        g = QGridLayout()
        g.setSpacing(10)
        g.addWidget(QLabel(tr("订货周期")), 0, 0)
        page.cycle = QComboBox()
        for d in R.REORDER_CYCLE_OPTIONS:
            page.cycle.addItem(tr("{0} 天", d), d)
        page.cycle.setCurrentIndex(max(0, page.cycle.findData(int(cfg.get("cycle") or rcfg.get("cycle") or 30))))
        g.addWidget(page.cycle, 0, 1)
        page.use_cfg = QCheckBox(tr("按配置文件周期"))
        page.use_cfg.setChecked(bool(cfg.get("use_supplier_cycles", True)))
        page.use_cfg.setToolTip(tr("打开：suppliers_config.json 里单独设了订货周期的供应商用它自己的周期，其余用左边选的周期。\n关闭：全部用左边选的周期。"))
        g.addWidget(page.use_cfg, 0, 2)
        g.addWidget(QLabel(tr("销量周期（天）")), 1, 0)
        dr = QHBoxLayout()
        page.days = []
        for d in (cfg.get("days") or rcfg.get("days") or R.DEFAULT_SALES_DAYS):
            sb = QSpinBox()
            sb.setRange(7, 365)
            sb.setValue(int(d))
            sb.setFixedWidth(64)
            dr.addWidget(sb)
            page.days.append(sb)
        dr.addStretch()
        g.addLayout(dr, 1, 1, 1, 2)
        lay.addLayout(g)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)


class TransferDialog(QDialog):
    """门店间调货清单：从哪家店调到哪家店、几件、货值；可以排序、筛选、导出（导出的就是调货单）"""

    def __init__(self, parent, transfers, ui):
        super().__init__(parent)
        import transfers as TR
        self.ts = list(transfers)
        self.setWindowTitle(tr("门店间调货"))
        self.resize(1100, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        t = QLabel(tr("门店间调货"))
        t.setObjectName("PageTitle")
        t.setStyleSheet("font-size:18px;")
        lay.addWidget(t)
        dn = TR.by_donor(self.ts)
        sub = QLabel(tr("这些商品要的件数远小于一箱，从别的门店调多出来的货，就少订一箱。") + "　" + "　".join(
            tr("从 {0}：{1} 个商品 · €{2:,.2f}", S.STORE_SHEETS.get(k, k), v["items"], v["value"])
            for k, v in sorted(dn.items())))
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        bar = QHBoxLayout()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        exp = QPushButton(tr("导出调货单"))
        heads = [tr("调出门店"), tr("调入门店"), tr("供应商"), "Cod.Art.", "EAN", tr("品名"), tr("调货件数"), tr("货值 €"),
                 tr("原来订（箱）"), tr("现在订（箱）"), tr("调出后够卖（天）"), tr("调入后够卖（天）")]
        self._set_row = ui.table_set_row
        self.table = ui.make_table(heads, stretch_col=5, numeric_cols=(6, 7, 8, 9, 10, 11))
        self.table.horizontalHeaderItem(10).setToolTip(tr("调出门店把这些货调走以后，剩下的库存还能卖几天"))
        self.table.horizontalHeaderItem(11).setToolTip(tr("调入门店收到调货（加上这次还订的箱数）以后能卖几天"))
        from erp_widgets import TableSortFilter, ColumnFilterBox
        self.ctl = TableSortFilter(self.table, lambda: len(self.ts), self._text, self._fill, key=self._key,
                                   numeric=(6, 7, 8, 9, 10, 11))
        bar.addWidget(ColumnFilterBox(self.ctl, 0, tr("调出门店")))
        bar.addSpacing(12)
        bar.addWidget(ColumnFilterBox(self.ctl, 1, tr("调入门店")))
        bar.addStretch()
        bar.addWidget(self.count)
        bar.addWidget(exp)
        lay.addLayout(bar)
        lay.addWidget(self.table, 1)
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("显示 {0} / {1} 个", n, tot)))
        exp.clicked.connect(self.export)
        bb = QHBoxLayout()
        bb.addStretch()
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.accept)
        bb.addWidget(close)
        lay.addLayout(bb)
        self.ctl.render()

    def _text(self, i, c):
        x = self.ts[i]
        return [S.STORE_SHEETS.get(x["from"], str(x["from"])), S.STORE_SHEETS.get(x["to"], str(x["to"])),
                x["supplier"], str(x["code"]), str(x.get("ean") or ""), x["desc"], f"{x['pieces']:g}",
                f"{x['value']:,.2f}", f"{x['colli_before']}", f"{x['colli_after']}",
                S.days_text(x.get("from_days")), S.days_text(x.get("to_days"))][c]

    def _key(self, i, c):
        if c in (10, 11):
            v = self.ts[i].get("from_days" if c == 10 else "to_days")
            return -1.0 if v is None else (1e12 if v == float("inf") else v)
        return None

    def _fill(self, i, k):
        self._set_row(self.table, k, [self._text(i, c) for c in range(12)])
        self.table.item(k, 5).setToolTip(self.ts[i]["desc"])

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("导出调货单"), "调货单.xlsx", tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        try:
            S.export_transfers(self.ts, path)
        except Exception as e:
            QMessageBox.critical(self, tr("导出"), tr("导出失败：{0}", e))


def _card(title, sub=""):
    """设置窗口里的一块：带标题的卡片，返回 (卡片, 里面的布局)"""
    f = QFrame()
    f.setObjectName("Panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 12, 16, 14)
    v.setSpacing(8)
    t = QLabel(title + ("  ⓘ" if sub else ""))
    t.setObjectName("PanelTitle")
    if sub:
        t.setToolTip(sub)              # 界面要简洁：说明不占地方，鼠标停在标题上看
    v.addWidget(t)
    return f, v


def _form_rows(grid, rows):
    """设置表单：每行 标签 + 输入框；说明放在鼠标提示里（不显示一大段字）。返回各行的标签"""
    labels = []
    for r, (lab, w, tip) in enumerate(rows):
        lb = QLabel(lab)
        if tip:
            lb.setToolTip(tip)
            w.setToolTip(tip)
        grid.addWidget(lb, r, 0)
        grid.addWidget(w, r, 1)
        labels.append(lb)
    grid.setColumnStretch(2, 1)
    return labels


def _hint(text):
    h = QLabel(text)
    h.setObjectName("Faint")
    h.setWordWrap(True)
    return h


def _store_checks(checked):
    """五家门店的勾选框：一行，返回 (布局, {门店号: 勾选框})"""
    row = QHBoxLayout()
    row.setSpacing(12)
    boxes = {}
    for k, name in S.STORE_SHEETS.items():
        cb = QCheckBox(name)
        cb.setChecked(k in checked)
        boxes[k] = cb
        row.addWidget(cb)
    row.addStretch()
    return row, boxes


class TransferBox(QWidget):
    """
    门店之间调货的设置（门店配货的「配货设置」和批发商比价的「门店间调货…」共用这一块）：
    存在 settings → store_orders.transfer；参与调货门店 = 不在 transfer.exclude_stores 里的（和借货分开设）
    """

    def __init__(self, tc, fallback_far=(), parent=None):
        super().__init__(parent)
        import transfers as TR
        tc_raw = tc
        tc = TR.config(tc)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.on = QCheckBox(tr("启用调货"))
        self.on.setChecked(bool(tc["enabled"]))
        v.addWidget(self.on)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        g.setColumnStretch(2, 1)

        def spin(val, lo, hi, dec, suffix=""):
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setDecimals(dec)
            w.setValue(float(val))
            if suffix:
                w.setSuffix(suffix)
            w.setFixedWidth(90)
            return w
        self.mode = QComboBox()
        self.mode.addItem(tr("只要整箱一小部分时"), "waste")
        self.mode.addItem(tr("所有要订商品"), "all")
        self.mode.setCurrentIndex(max(0, self.mode.findData(tc.get("mode"))))
        # 界面上说「要的只占整箱 ≤ x%」；内部存的是「整箱多出的比例」waste_ratio = 1 − x
        self.share = spin(round((1 - float(tc.get("waste_ratio") or 0.5)) * 100), 1, 99, 0, " %")
        self.keep = spin(tc.get("keep_cycles", 1.0), 0, 10, 1)
        self.itemv = spin(tc["item_min_value"], 0, 100000, 2)
        self.minv = spin(tc["min_value"], 0, 100000, 0)
        self.mode.setFixedWidth(170)
        rows = [
            (tr("什么时候调"), self.mode, ""),
            (tr("只占整箱 ≤"), self.share, tr("例：一箱 12 件只要 3 件 = 25%")),
            (tr("调出店留货系数"), self.keep, tr("留够 日均 × 订货周期 × 系数，多的才调")),
            (tr("单品至少 €"), self.itemv, tr("调货件数 × 件价，太少不值得拣货")),
            (tr("单店合计至少 €"), self.minv, tr("每家调出门店合计，0 = 不限")),
        ]
        labels = _form_rows(g, rows)
        self._share_row = [labels[1], self.share]
        v.addLayout(g)
        # 「所有要订商品」用不上比例：整行藏起来（只变灰看不出来）
        self.mode.currentIndexChanged.connect(lambda _: self._show_share())
        self._show_share()
        out = set(TR.excluded_stores(tc_raw, fallback_far))
        lab = QLabel(tr("参与门店"))
        lab.setToolTip(tr("自己这次也要订的商品不调出。门店配货和批发商比价共用这套设置。"))
        v.addWidget(lab)
        row, self.stores = _store_checks({k for k in S.STORE_SHEETS if k not in out})
        v.addLayout(row)

    def _show_share(self):
        on = self.mode.currentData() == "waste"
        for w in self._share_row:
            w.setVisible(on)

    def transfer_values(self, tc):
        import transfers as TR
        return dict(TR.config(tc), enabled=self.on.isChecked(), mode=self.mode.currentData(),
                    waste_ratio=round(1 - self.share.value() / 100.0, 4), keep_cycles=self.keep.value(),
                    item_min_value=self.itemv.value(), min_value=self.minv.value(),
                    exclude_stores=[k for k, cb in self.stores.items() if not cb.isChecked()])


class FillDialog(QDialog):
    """配货设置：三块——凑单、BMS 不够起订量时、门店之间调货"""

    def __init__(self, page):
        super().__init__(page.win)
        self.page = page
        self.setWindowTitle(tr("配货设置"))
        fc = page.fill_cfg()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        # ① 凑单
        c1, v1 = _card(tr("① 凑单"), tr("每家店离起订量只差一点时，加点卖得动的商品凑够"))
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        g.setColumnStretch(2, 1)
        self.gap_amount = QDoubleSpinBox()
        self.gap_amount.setRange(0, 100000)
        self.gap_amount.setDecimals(2)
        self.gap_amount.setValue(float(fc["max_amount_gap"]))
        self.gap_colli = QSpinBox()
        self.gap_colli.setRange(0, 10000)
        self.gap_colli.setValue(int(fc["max_colli_gap"]))
        self.max_days = QSpinBox()
        self.max_days.setRange(1, 3650)
        self.max_days.setValue(int(fc["max_stock_days"]))
        self.max_days.setSuffix(tr(" 天"))
        for w in (self.gap_amount, self.gap_colli, self.max_days):
            w.setFixedWidth(90)
        _form_rows(g, ((tr("金额差 ≤ €"), self.gap_amount, tr("0 = 不按金额凑单")),
                       (tr("箱数差 ≤"), self.gap_colli, tr("0 = 不按箱数凑单")),
                       (tr("凑完最多够卖"), self.max_days, tr("只加卖得动的商品：加完后库存最多够卖几天"))))
        v1.addLayout(g)
        self.fill_bms = QCheckBox(tr("BMS 也凑单"))
        self.fill_bms.setToolTip(tr("BMS 离起订量只差一点时，也加点卖得动的商品凑够"))
        self.fill_bms.setChecked(bool(fc["fill_bms"]))
        v1.addWidget(self.fill_bms)
        cols = QHBoxLayout()                 # 左边：凑单、BMS；右边：调货（竖着排太高，小屏幕放不下）
        cols.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(c1)

        # ② BMS 凑单：能单独送店的都单独送店；订不起来的门店按下面的先后并进 BMS，够起订量就停；凑不够的这次不订
        c2, v2 = _card(tr("② BMS 凑单顺序"), tr("能单独送店的都单独送店。订不起来的门店按这个先后一家一家并进 BMS，"
                                                 "够起订量就停；没勾的、凑不够的这次不订。配货结果里可以手动选进 BMS 的门店。"))
        from PySide6.QtWidgets import QListWidget, QListWidgetItem, QAbstractItemView
        self.bms_list = QListWidget()
        self.bms_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.bms_list.setDefaultDropAction(Qt.MoveAction)
        far = set(fc.get("far_stores") or [])
        order = [int(x) for x in (fc.get("bms_order") or S.BMS_ORDER)]
        order += [k for k in S.STORE_SHEETS if k not in order]
        for k in order:
            if k not in S.STORE_SHEETS:
                continue
            it = QListWidgetItem(f"{k} · {S.STORE_SHEETS[k]}")
            it.setData(Qt.UserRole, k)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            it.setCheckState(Qt.Unchecked if k in far else Qt.Checked)
            self.bms_list.addItem(it)
        self.bms_list.setMaximumHeight(5 * 30 + 8)
        self.bms_list.setToolTip(tr("勾选 = 参与；拖动调整先后"))
        v2.addWidget(self.bms_list)
        left.addWidget(c2)
        left.addStretch()
        cols.addLayout(left, 1)

        # ③ 门店之间调货
        c3, v3 = _card(tr("③ 门店间调货"), tr("要的件数远小于一箱时，从别的门店调多出来的货，少订一箱"))
        self.tbox = TransferBox(page.transfer_cfg(), fc.get("far_stores") or [])
        v3.addWidget(self.tbox)
        v3.addStretch()
        cols.addWidget(c3, 1)
        lay.addLayout(cols)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumWidth(820)

    def save(self):
        base = {k: v for k, v in self.page.fill_cfg().items() if k != "supplier_cfg"}
        order = [self.bms_list.item(i).data(Qt.UserRole) for i in range(self.bms_list.count())]
        far = [self.bms_list.item(i).data(Qt.UserRole) for i in range(self.bms_list.count())
               if self.bms_list.item(i).checkState() != Qt.Checked]
        self.page.cfg["fill"] = dict(base, max_amount_gap=self.gap_amount.value(),
                                     max_colli_gap=self.gap_colli.value(), max_stock_days=self.max_days.value(),
                                     fill_bms=self.fill_bms.isChecked(), bms_order=order, far_stores=far)
        self.page.cfg["transfer"] = self.tbox.transfer_values(self.page.transfer_cfg())
        save_settings(self.page.settings)
        self.accept()


class MoqDialog(QDialog):
    """
    供应商设置：起订量（文件里读错了自己改；保存到 settings.json，选了对应供应商的话同时写进 suppliers_config.json），
    以及这家供应商单独的门店名单：BMS 不够时可以并进 / 借给 BMS 的门店、参与门店间调货的门店（不设 = 按配货设置）
    """

    def __init__(self, page, name, path, file_m, override):
        super().__init__(page.win)
        self.setWindowTitle(tr("供应商设置 · {0}", name))
        self.result_supplier_cfg = dict((page.cfg.get("supplier_cfg") or {}).get(name) or {})
        self.result_override = override
        cur = S.apply_override(file_m, override)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        t = QLabel(name)
        t.setObjectName("Section")
        lay.addWidget(t)
        info = QLabel(tr("文件：{0}", os.path.basename(path)) + "\n" +
                      tr("文件里写的起订量：{0}", file_m.get("raw") or tr("（空）")) + "\n" +
                      tr("程序读到：{0}", S.moq_label(dict(file_m, source="file"))))
        info.setObjectName("Faint")
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(info)
        g = QGridLayout()
        g.setSpacing(8)

        def spin(v, decimals=0, top=1000000):
            w = QDoubleSpinBox()
            w.setDecimals(decimals)
            w.setRange(0, top)
            w.setValue(float(v or 0))
            w.setFixedWidth(110)
            return w

        self.w_colli = spin(cur.get("colli"))
        self.w_amount = spin(cur.get("amount"), 2)
        self.w_pallets = spin(cur.get("pallets"), 1)
        self.w_per = spin(cur.get("per_pallet"))
        self.w_mult = spin(cur.get("multiple"))
        rows = ((tr("起订箱数"), self.w_colli, tr("0 = 不按箱数")),
                (tr("起订金额 €"), self.w_amount, tr("0 = 不按金额")),
                (tr("起订板数"), self.w_pallets, tr("按板（BANCALE / PEDANE）起订时填")),
                (tr("每板多少箱"), self.w_per, tr("按板起订时必须填，否则判断不了够不够")),
                (tr("按几箱的倍数订"), self.w_mult, tr("只做提示，不影响配货判断")))
        for r, (lab, w, hint) in enumerate(rows):
            g.addWidget(QLabel(lab), r, 0)
            g.addWidget(w, r, 1)
            h = QLabel(hint)
            h.setObjectName("Faint")
            g.addWidget(h, r, 2)
        lay.addLayout(g)
        self.w_note = QLineEdit((override or {}).get("note", ""))
        self.w_note.setPlaceholderText(tr("备注（可不填）"))
        lay.addWidget(self.w_note)

        # 这家供应商单独的门店名单（不勾「单独设」= 按配货设置里的总设置）
        fc = page.fill_cfg()
        glob = {"bms_stores": {k for k in S.STORE_SHEETS if k not in set(fc.get("far_stores") or [])},
                "transfer_stores": {k for k in S.STORE_SHEETS if k not in set(page.transfer_far())}}
        self.store_sections = {}
        for key, title in (("bms_stores", tr("单独设 BMS 凑单门店")),
                           ("transfer_stores", tr("单独设调货门店"))):
            own = self.result_supplier_cfg.get(key)
            cb = QCheckBox(title)
            cb.setToolTip(tr("不勾就按「配货设置」里的总设置"))
            cb.setChecked(own is not None)
            row, boxes = _store_checks({int(x) for x in own} if own is not None else glob[key])
            for b_ in boxes.values():
                b_.setEnabled(own is not None)
            cb.toggled.connect(lambda on, bx=boxes: [b_.setEnabled(on) for b_ in bx.values()])
            lay.addWidget(cb)
            lay.addLayout(row)
            self.store_sections[key] = (cb, boxes)

        # suppliers_config.json 里对应的供应商
        cl = QLabel(tr("同时写进配置文件 suppliers_config.json 的供应商："))
        lay.addWidget(cl)
        self.w_cfg = QComboBox()
        self.w_cfg.addItem(tr("不写进配置文件"), "")
        details = {}
        try:
            from fenpei.config import SUPPLIERS_CONFIG_FILE
            details = S.load_suppliers_config(SUPPLIERS_CONFIG_FILE).get("supplier_details", {})
        except Exception as e:
            cl.setText(tr("读不了 suppliers_config.json：{0}", e))
        for sid, d in sorted(details.items(), key=lambda kv: kv[1].get("name", "")):
            self.w_cfg.addItem(tr("{0}（ID {1}）· 现在起订 €{2}", d.get("name", ""), sid, d.get("min_order_amount", "—")), sid)
        guess = (override or {}).get("config_id") or S.match_config_supplier(name, file_m.get("company"), details)
        self.w_cfg.setCurrentIndex(max(0, self.w_cfg.findData(guess or "")))
        lay.addWidget(self.w_cfg)
        h2 = QLabel(tr("会更新这个供应商的 min_order_amount（起订金额），按箱 / 按板的也一起记下。"))
        h2.setObjectName("Faint")
        h2.setWordWrap(True)
        lay.addWidget(h2)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.btn_reset = bb.addButton(tr("恢复成文件里的值"), QDialogButtonBox.ResetRole)
        self.btn_reset.setEnabled(override is not None)
        self.btn_reset.clicked.connect(self._reset)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumWidth(560)

    def _save(self):
        if self.w_pallets.value() and not self.w_per.value():
            if QMessageBox.question(self, tr("起订量"), tr("按板起订但没填每板多少箱，程序判断不了够不够起订量，"
                                                         "这家会一直算作「不够」。仍然保存？")) != QMessageBox.Yes:
                return
        self.result_override = {"colli": self.w_colli.value(), "amount": self.w_amount.value(),
                                "pallets": self.w_pallets.value(), "per_pallet": self.w_per.value(),
                                "multiple": self.w_mult.value(), "note": self.w_note.text().strip(),
                                "config_id": self.w_cfg.currentData() or ""}
        self._collect_stores()
        self.accept()

    def _collect_stores(self):
        out = {}
        for key, (cb, boxes) in self.store_sections.items():
            if cb.isChecked():
                out[key] = [k for k, b_ in boxes.items() if b_.isChecked()]
        self.result_supplier_cfg = out

    def _reset(self):
        self.result_override = None
        self._collect_stores()
        self.accept()


# =====================================================================
# 页面
# =====================================================================
class BmsPickDialog(QDialog):
    """
    配货结果页「手动选进 BMS 的门店」：一家供应商，勾哪几家门店整单进 BMS（自己够起订量的也可以并进来）。
    每家店显示箱数、金额、自己够不够起订量、紧急缺货占比（红 ≥ 50%、黄 ≥ 25%），下面实时算 BMS 合计够不够起订量。
    确定后 self.picks = {"stores": [...], "force": 不够起订量也下单}
    """

    def __init__(self, page, sc, plan):
        super().__init__(page.win)
        self.sc, self.plan = sc, plan
        self.setWindowTitle(tr("手动选进 BMS 的门店 · {0}", sc["name"]))
        self.picks = None
        items = {it["row"]: it for it in sc["items"]}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)
        top = QLabel(tr("起订量 {0} · 勾上的门店整单进 BMS", S.moq_label(S.sc_moq(sc), mark=False)))
        top.setToolTip(tr("没勾的门店：自己够起订量就直接送店，不够就这次不订"))
        top.setWordWrap(True)
        top.setObjectName("Muted")
        lay.addWidget(top)
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QAbstractItemView
        cur = set((plan.get("bms") or {}).get("stores") or [])
        if not cur:                                       # 没有 BMS 单：默认勾上订不起来的门店
            cur = {s_ for s_, st in plan["stores"].items() if st["status"] in ("bms", "wait")}
        self.rows = []
        for sid, st in plan["stores"].items():
            if st["status"] == "none":
                continue
            base = st.get("base_lines") or st["lines"]
            c, a = S._totals(base, items)
            alone = S._satisfied(c, a, sc["moq_colli"], sc["moq_amount"])
            share, n = S.urgent_share(sc, base, st.get("dem_rows"))
            self.rows.append((sid, c, a, alone, share, n, base))
        t = QTableWidget(len(self.rows), 6)
        t.setHorizontalHeaderLabels([tr("进 BMS"), tr("门店"), tr("箱数"), tr("金额"), tr("自己够起订量？"), tr("紧急缺货")])
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        T_ = page.ui.T
        self.checks = {}
        for r, (sid, c, a, alone, share, n, base) in enumerate(self.rows):
            ck = QTableWidgetItem()
            ck.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            ck.setCheckState(Qt.Checked if sid in cur else Qt.Unchecked)
            t.setItem(r, 0, ck)
            self.checks[sid] = ck
            gap = "" if alone else page._gap_note(sc, {"colli": c, "amount": a})
            vals = [S.STORE_SHEETS[sid], f"{c:g}", f"€{a:,.2f}", tr("够") if alone else gap,
                    tr("{0:.0%}（{1} 个）", share, n) if n else "—"]
            for k, v in enumerate(vals, 1):
                it = QTableWidgetItem(v)
                if k in (2, 3, 5):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(r, k, it)
            t.item(r, 4).setForeground(QColor(T_("ok") if alone else T_("warn")))
            if n and share >= 0.25:
                kk = "err" if share >= 0.5 else "warn"
                t.item(r, 5).setForeground(QColor(T_(kk)))
                t.item(r, 5).setBackground(QColor(T_(kk + "_soft")))
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        t.setMinimumWidth(620)
        t.setMinimumHeight(min(320, 40 + 34 * max(1, len(self.rows))))
        t.itemChanged.connect(lambda _it: self._update())
        self.t = t
        lay.addWidget(t)
        self.sum_lbl = QLabel("")
        self.sum_lbl.setWordWrap(True)
        lay.addWidget(self.sum_lbl)
        self.force = QCheckBox(tr("BMS 不够起订量也下单"))
        self.force.setToolTip(tr("一般不勾：不够起订量的 BMS 单供应商可能不接；确定要订再勾"))
        lay.addWidget(self.force)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(tr("按这样重新生成"))
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.T_ = T_
        self._update()

    def _chosen(self):
        return [sid for sid, ck in self.checks.items() if ck.checkState() == Qt.Checked]

    def _update(self):
        items = {it["row"]: it for it in self.sc["items"]}
        lines = {}
        for sid, c, a, alone, share, n, base in self.rows:
            if sid in self._chosen():
                for row, k in base.items():
                    lines[row] = lines.get(row, 0) + k
        c, a = S._totals(lines, items)
        ok = S._satisfied(c, a, self.sc["moq_colli"], self.sc["moq_amount"])
        chosen = self._chosen()
        if not chosen:
            txt, col = tr("没有门店进 BMS：这家只出直送的单"), "muted"
        elif ok:
            txt, col = tr("BMS：{0} 家 · {1:g} 箱 · €{2:,.2f} → 够起订量", len(chosen), c, a), "ok"
        else:
            txt, col = tr("BMS：{0} 家 · {1:g} 箱 · €{2:,.2f} → 还不够起订量（{3}）", len(chosen), c, a,
                          S.moq_label(S.sc_moq(self.sc), mark=False)), "err"
        self.sum_lbl.setText(txt)
        self.sum_lbl.setStyleSheet(f"color:{self.T_(col)}; font-weight:600;")

    def _ok(self):
        self.picks = {"stores": self._chosen(), "force": self.force.isChecked()}
        self.accept()


class StoreOrderPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cfg = settings.setdefault("store_orders", {})
        self.files = []          # [(星期, 路径)]
        self.moq = {}            # 路径 → 文件里读到的起订量（S.read_moq 的 dict，还没套手动修改）
        self.calc = None         # S.compute() 的结果
        self.colli = {}          # {路径: {门店号: {行号: 箱数}}}（当前值，含手动修改）
        self.edited = set()      # {(路径, 门店号, 行号)}
        self.result = None
        self.result_sig = None     # 配货时的输入（箱数、勾选、配货设置、起订量）；返回修改后没变 → 直接回结果页
        self.alloc_on = set()      # 起订量检查里勾选、要配货的供应商（SCHEDA 路径）
        self.running = False
        self.list_token = 0
        self.detail_win = None
        self.sig = _Sig()
        self.sig.listed.connect(self._listed)
        self.sig.computed.connect(self._computed)
        self.sig.done.connect(self._done)

    def fill_cfg(self):
        """配货设置 + 各供应商单独设的门店名单（供应商设置里选的，supplier_cfg）"""
        f = dict(S.DEFAULT_FILL, **(self.cfg.get("fill") or {}))
        f["supplier_cfg"] = self.cfg.get("supplier_cfg") or {}
        return f

    def transfer_cfg(self):
        import transfers as TR
        return TR.config(self.cfg.get("transfer"))

    def transfer_far(self):
        """不参与调货的门店（和借货分开设）"""
        import transfers as TR
        return TR.excluded_stores(self.cfg.get("transfer"), self.fill_cfg().get("far_stores"))

    def set_transfer(self, key, value):
        """
        订货明细里改了调货：key = (SCHEDA, 调入门店, 行)，value = {"from", "pieces"} 或 0（不调）。
        第一次改的时候把系统建议的调货都冻结下来：以后只按人工定的调，改一行别的行不跟着变。手动改的箱数保留
        """
        import transfers as TR
        if not self.calc:
            return
        c = self.calc
        if not c.get("transfer_frozen"):
            key_of = lambda m: (m["path"], m["to"], m["row"])
            c["transfer_overrides"] = TR.freeze(c.get("transfers"), c.get("transfer_overrides"), key_of)
            c["transfer_auto_plan"] = {key_of(m): {"from": m["from"], "pieces": int(m["pieces"]), "auto": True}
                                       for m in c.get("transfers") or []}
            c["transfer_frozen"] = True
        c["transfer_overrides"][key] = value if isinstance(value, dict) else int(value or 0)
        self.apply_transfers()
        self._refresh_matrix(only_path=key[0])
        self._update_state()

    def reset_transfers(self, keys):
        """「恢复建议值」：这几行的调货恢复成系统建议的；人工改的全恢复了就解冻（以后又按设置自动建议）"""
        import transfers as TR
        c = self.calc or {}
        ov = c.get("transfer_overrides") or {}
        auto = c.get("transfer_auto_plan") or {}
        for k in keys:
            if k in auto:
                ov[k] = dict(auto[k])
            else:
                ov.pop(k, None)
        if not TR.unfreeze(ov):
            c["transfer_overrides"], c["transfer_frozen"] = {}, False
            c.pop("transfer_auto_plan", None)
        self.apply_transfers()

    def apply_transfers(self, keep_edits=True, refresh_auto=False):
        """按现在的调货设置重算调货；手动改过的箱数保留。refresh_auto：调货设置改了 → 解冻，按新设置重新建议（人工改的保留）"""
        if not self.calc:
            return
        if refresh_auto and self.calc.get("transfer_frozen"):
            import transfers as TR
            self.calc["transfer_overrides"] = TR.unfreeze(self.calc.get("transfer_overrides"))
            self.calc["transfer_frozen"] = False
            self.calc.pop("transfer_auto_plan", None)
        S.apply_transfers(self.calc, self.transfer_cfg(), self.transfer_far(), self.cfg.get("supplier_cfg"))
        new = copy.deepcopy(self.calc["base"])
        if keep_edits:
            for path, sid, row in self.edited:
                v = self.colli.get(path, {}).get(sid, {}).get(row, 0)
                lines = new.setdefault(path, {}).setdefault(sid, {})
                if v > 0:
                    lines[row] = v
                else:
                    lines.pop(row, None)
        self.colli = new
        self.edited = {k for k in self.edited
                       if self.colli.get(k[0], {}).get(k[1], {}).get(k[2], 0)
                       != self.calc["base"].get(k[0], {}).get(k[1], {}).get(k[2], 0)}
        self._refresh_transfer_panel()

    def _refresh_transfer_panel(self):
        if not hasattr(self, "tr_title"):
            return
        import transfers as TR
        ts = (self.calc or {}).get("transfers") or []
        cfg = self.transfer_cfg()
        if not self.calc:
            self.tr_title.setText(tr("门店间调货"))
            self.tr_sub.setText(tr("计算后，要的件数远小于一箱的商品会先看别的门店能不能调"))
        elif not cfg["enabled"]:
            self.tr_title.setText(tr("门店间调货 · 已关闭"))
            self.tr_sub.setText(tr("在「配货设置」里可以打开"))
        elif not ts:
            self.tr_title.setText(tr("门店间调货 · 这次没有可调的"))
            self.tr_sub.setText(tr("没有商品的调货货值够 €{0:g}（门店多出来的货不够，或者商品太便宜）", cfg["item_min_value"]))
        else:
            dn = TR.by_donor(ts)
            saved = sum(x["boxes_saved"] for x in dn.values())
            val = sum(x["value"] for x in dn.values())
            self.tr_title.setText(tr("门店间调货 · {0} 个商品，少订 {1} 箱", len(ts), saved))
            self.tr_sub.setText(tr("从 {0} 调出，货值 €{1:,.2f}；配货时会一起生成调货单", tr("、").join(
                f"{S.STORE_SHEETS.get(k, k)}（€{v['value']:,.0f}）" for k, v in sorted(dn.items())), val))
        self.btn_transfers.setEnabled(bool(ts))

    def open_transfers(self):
        ts = (self.calc or {}).get("transfers") or []
        if ts:
            TransferDialog(self.win, ts, self.ui).exec()

    # ---------------------------------------------------------------
    def build(self):
        page = QWidget()
        page.setObjectName("Content")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        hv = QVBoxLayout(head)
        hv.setContentsMargins(24, 18, 24, 4)
        hv.setSpacing(12)
        tr_ = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(3)
        t = QLabel(tr("门店配货"))
        t.setObjectName("PageTitle")
        s = QLabel(tr("品牌供应商直接送店 · 每家店单独满起订量才直送，差一点自动凑单，差太多并到 BMS"))
        s.setObjectName("PageSub")
        col.addWidget(t)
        col.addWidget(s)
        tr_.addLayout(col, 1)
        self.param_dlg = ParamDialog(self)           # 建出 self.cycle / self.use_cfg / self.days
        bp = QPushButton(tr("计算参数"))
        bp.clicked.connect(self.param_dlg.exec)
        tr_.addWidget(bp, 0, Qt.AlignBottom)
        self.btn_recalc = QPushButton(tr("重新计算"))
        self.btn_recalc.setIcon(icon("refresh", T("muted"), 15))
        self.btn_recalc.clicked.connect(self.start_compute)
        self.btn_recalc.hide()
        tr_.addWidget(self.btn_recalc, 0, Qt.AlignBottom)
        hv.addLayout(tr_)
        self.steps = StepBar([(tr(a), b) for a, b in STEPS])
        hv.addWidget(self.steps)
        outer.addWidget(head)

        self.views = QStackedWidget()
        self.views.addWidget(_scroll(self._build_work()))
        self.views.addWidget(_scroll(self._build_result()))
        outer.addWidget(self.views, 1)
        outer.addWidget(self._build_action_bar())
        self.refresh_list()
        self._update_state()
        return page

    # ---------------- 工作台 ----------------
    def _build_work(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(24, 12, 24, 16)
        h.setSpacing(16)

        # 左：供应商
        left = QFrame()
        left.setObjectName("Panel")
        left.setMinimumWidth(360)
        left.setMaximumWidth(400)
        self.left_panel = left
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 12, 0, 8)
        lv.setSpacing(8)
        top = QVBoxLayout()
        top.setContentsMargins(14, 0, 14, 0)
        top.setSpacing(8)
        r1 = QHBoxLayout()
        lt = QLabel(tr("供应商"))
        lt.setObjectName("Section")
        r1.addWidget(lt, 1)
        self.day = QComboBox()
        for d in S.DAY_FOLDERS:
            self.day.addItem(tr(d), d)
        self.day.addItem(tr("全部"), ALL)
        default_day = self.cfg.get("day") or S.DAY_FOLDERS[min(datetime.now().weekday(), 4)]
        self.day.setCurrentIndex(max(0, self.day.findData(default_day)))
        self.day.currentIndexChanged.connect(self.refresh_list)
        r1.addWidget(self.day)
        rb = QPushButton()
        rb.setObjectName("TopBtn")
        rb.setIcon(icon("refresh", T("muted"), 15))
        rb.setToolTip(tr("刷新"))
        rb.clicked.connect(self.refresh_list)
        self.btn_refresh = rb
        r1.addWidget(rb)
        top.addLayout(r1)
        r2 = QHBoxLayout()
        self.folder = QLineEdit(self.cfg.get("folder") or r"D:\CasaTe\采购配单")
        self.folder.setPlaceholderText(tr("采购配单文件夹（里面有 周一 … 周五）"))
        self.folder.setToolTip(tr("里面有 周一 … 周五 子文件夹，每个供应商一个 SCHEDA 表"))
        self.folder.editingFinished.connect(self.refresh_list)
        fb = QPushButton()
        fb.setObjectName("TopBtn")
        fb.setIcon(icon("folder", T("muted"), 15))
        fb.setToolTip(tr("选择文件夹…"))
        fb.clicked.connect(self.choose_folder)
        self.btn_folder_pick = fb
        r2.addWidget(self.folder, 1)
        r2.addWidget(fb)
        top.addLayout(r2)
        self.f_sup_text = QLineEdit()
        self.f_sup_text.setPlaceholderText(tr("筛选供应商"))
        self.f_sup_text.textChanged.connect(self._filter_sup)
        top.addWidget(self.f_sup_text)
        r3 = QHBoxLayout()
        self.list_msg = QLabel("")
        self.list_msg.setObjectName("Faint")
        r3.addWidget(self.list_msg, 1)
        for text, val in ((tr("全选"), True), (tr("清除"), False)):
            bb = QPushButton(text)
            bb.setObjectName("Link")
            bb.clicked.connect(lambda _=False, v=val: self._check_all(v))
            r3.addWidget(bb)
        top.addLayout(r3)
        lv.addLayout(top)
        self.ftable = self.ui.make_table(["", tr("供应商"), tr("起订量"), tr("各店"), tr("星期")], stretch_col=1)
        fh_ = self.ftable.horizontalHeader()
        fh_.setSectionResizeMode(0, QHeaderView.Fixed)
        fh_.setSectionResizeMode(3, QHeaderView.Fixed)
        # 起订量、星期两列固定宽度（不跟着内容变宽），供应商名那一列拿剩下的地方，不会被挤成「B…」
        fh_.setSectionResizeMode(2, QHeaderView.Fixed)
        fh_.setSectionResizeMode(4, QHeaderView.Fixed)
        self.ftable.setColumnWidth(2, 92)
        self.ftable.setColumnWidth(4, 52)
        self.ftable.setColumnWidth(0, 34)
        self.ftable.setColumnWidth(3, 104)
        self.ftable.setItemDelegateForColumn(3, DotsDelegate(self.ftable))
        self.ftable.verticalHeader().setDefaultSectionSize(40)
        self.ftable.itemChanged.connect(self._item_changed)
        self.ftable.cellClicked.connect(lambda r, c: c == 2 and self._fpath(r) and self.edit_moq(self._fpath(r)))
        self.f_sorter = RowSorter(self.ftable, skip=(0,), on_sorted=self._filter_sup)
        self.ftable.horizontalHeaderItem(2).setToolTip(tr("点起订量那一格可以修改（读错了自己改，会保存）"))
        self.ftable.setMinimumHeight(420)
        lv.addWidget(self.ftable, 1)
        hint = QLabel(tr("不再合作的供应商取消勾选，下次会记住"))
        hint.setObjectName("Faint")
        hint.setContentsMargins(14, 0, 14, 0)
        lv.addWidget(hint)
        h.addWidget(left)

        # 右
        right = QVBoxLayout()
        right.setSpacing(14)
        srow = QFrame()
        srow.setObjectName("Panel")
        sr = QHBoxLayout(srow)
        sr.setContentsMargins(16, 10, 16, 10)
        sr.setSpacing(14)
        sl = QLabel(tr("门店"))
        sl.setStyleSheet("font-weight:600;")
        sr.addWidget(sl)
        self.store_cb = {}
        sel = set(self.cfg.get("stores") or S.STORE_SHEETS.keys())
        for sid, name in S.STORE_SHEETS.items():
            cb = QCheckBox(f"{sid} · {name}")
            cb.setChecked(sid in sel)
            sr.addWidget(cb)
            self.store_cb[sid] = cb
        sr.addStretch()
        right.addWidget(srow)
        arow = QFrame()
        arow.setObjectName("Panel")
        ar = QHBoxLayout(arow)
        ar.setContentsMargins(16, 8, 16, 8)
        al = QLabel(tr("算法"))
        al.setStyleSheet("font-weight:600;")
        ar.addWidget(al)
        algo_row, self.engine, self.algo_info = algo_widgets(self, self.ui)
        ar.addLayout(algo_row, 1)
        right.addWidget(arow)

        k = QGridLayout()
        k.setSpacing(12)
        c1, self.k_colli, _ = kpi_card(tr("当前订货合计"), "—")
        c2, self.k_amt, _ = kpi_card(tr("金额（配货前）"), "—")
        c3, self.k_ok, _ = kpi_card(tr("已达起订量"), "—")
        c4, self.k_edit, _ = kpi_card(tr("手动修改"), "0")
        for i, c in enumerate((c1, c2, c3, c4)):
            k.addWidget(c, 0, i)
        right.addLayout(k)

        mp, mv = _panel()
        mh = QHBoxLayout()
        mt = QLabel(tr("起订量检查"))
        mt.setObjectName("Section")
        mh.addWidget(mt)
        mh.addSpacing(12)
        self.m_sel = QLabel("")
        self.m_sel.setObjectName("Faint")
        mh.addWidget(self.m_sel)
        for text, on in ((tr("只选订得起来的"), None), (tr("全选"), True), (tr("全不选"), False)):
            lb = QPushButton(text)
            lb.setObjectName("Link")
            lb.setCursor(Qt.PointingHandCursor)
            lb.clicked.connect(lambda _=False, on=on: self._set_all_alloc(on))
            mh.addWidget(lb)
            self.m_links = getattr(self, "m_links", []) + [lb]
        mh.addStretch(1)
        self.legend = []
        for kind, text in (("ok", tr("单独够起订量 · 直送")), ("near", tr("合起来够 BMS 起订量")), ("far", tr("不够 · 这次不订"))):
            dot = QLabel()
            dot.setFixedSize(10, 10)
            lab = QLabel(text)
            lab.setObjectName("Faint")
            mh.addWidget(dot)
            mh.addWidget(lab)
            mh.addSpacing(8)
            self.legend.append((dot, kind))
        mv.addLayout(mh)
        self.matrix_stack = QStackedWidget()
        es = EmptyState("suggest", tr("还没有计算"), tr("左边勾选供应商、上面选门店，然后点底部的「开始计算」。计算完这里显示每家店的箱数和金额够不够起订量。"))
        self.matrix_stack.addWidget(es)
        self.mtable = self.ui.make_table(["", tr("供应商"), tr("起订量")] + list(S.STORE_SHEETS.values()) + ["BMS", tr("合计")],
                                         stretch_col=1)
        self.mtable.horizontalHeaderItem(3 + len(S.STORE_SHEETS)).setToolTip(
            tr("不够单独送店的门店合起来由 BMS 订（含同仓并单、借货、凑单之后）"))
        self.mtable.horizontalHeaderItem(0).setToolTip(tr("勾选的供应商才会配货、生成订单"))
        self.mtable.horizontalHeaderItem(2).setToolTip(tr("点起订量那一格可以修改（读错了自己改，会保存）"))
        self.mtable.verticalHeader().setDefaultSectionSize(40)
        mh_ = self.mtable.horizontalHeader()
        mh_.setSectionResizeMode(QHeaderView.ResizeToContents)
        mh_.setStretchLastSection(True)
        self.mtable.cellClicked.connect(self._matrix_clicked)
        self.mtable.itemChanged.connect(self._alloc_toggled)
        self.m_sorter = RowSorter(self.mtable, pin_last=True, skip=(0,))     # 合计行永远在最后
        self.mtable.setToolTip(tr("左边勾选要配货的供应商；点格子打开订货明细并筛选到这家供应商 / 门店"))
        self.matrix_stack.addWidget(self.mtable)
        self.matrix_stack.setMinimumHeight(300)
        mv.addWidget(self.matrix_stack, 1)
        right.addWidget(mp, 1)

        dp, dv = _panel()
        dh = QHBoxLayout()
        dh.setSpacing(14)
        dic = QLabel()
        dic.setFixedSize(40, 40)
        dic.setAlignment(Qt.AlignCenter)
        self.detail_icon = dic
        dc = QVBoxLayout()
        dc.setSpacing(2)
        self.detail_title = QLabel(tr("订货明细"))
        self.detail_title.setStyleSheet("font-size:14px; font-weight:600;")
        ds = QLabel(tr("每个商品 × 门店的库存、销量、日均、目标库存和建议箱数，可逐行修改；在独立窗口里打开，不挤占工作台"))
        ds.setObjectName("Faint")
        ds.setWordWrap(True)
        dc.addWidget(self.detail_title)
        dc.addWidget(ds)
        self.btn_detail = QPushButton(tr("打开明细窗口"))
        self.btn_detail.setObjectName("Outline")
        self.btn_detail.clicked.connect(lambda: self.open_detail())
        dh.addWidget(dic)
        dh.addLayout(dc, 1)
        dh.addWidget(self.btn_detail)
        dv.addLayout(dh)
        right.addWidget(dp)

        tp, tv = _panel()
        th = QHBoxLayout()
        th.setSpacing(14)
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        self.tr_title = QLabel(tr("门店间调货"))
        self.tr_title.setStyleSheet("font-size:14px; font-weight:600;")
        self.tr_sub = QLabel("")
        self.tr_sub.setObjectName("Faint")
        self.tr_sub.setWordWrap(True)
        tcol.addWidget(self.tr_title)
        tcol.addWidget(self.tr_sub)
        self.btn_transfers = QPushButton(tr("查看调货单"))
        self.btn_transfers.setObjectName("Outline")
        self.btn_transfers.clicked.connect(self.open_transfers)
        th.addLayout(tcol, 1)
        th.addWidget(self.btn_transfers)
        tv.addLayout(th)
        right.addWidget(tp)
        self._refresh_transfer_panel()
        h.addLayout(right, 1)
        return w

    def _build_action_bar(self):
        bar = QFrame()
        bar.setObjectName("ActionBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(24, 10, 24, 10)
        h.setSpacing(12)
        self.edit_chip = chip(tr("已手动修改 {0} 处", 0), "warn")
        h.addWidget(self.edit_chip)
        self.bar_text = QLabel("")
        self.bar_text.setObjectName("Faint")
        self.bar_text.setWordWrap(True)
        h.addWidget(self.bar_text, 1)
        self.btn_fill = QPushButton(tr("配货设置"))
        self.btn_fill.clicked.connect(self.edit_fill)
        h.addWidget(self.btn_fill)
        self.btn_back = QPushButton(tr("返回修改"))
        self.btn_back.clicked.connect(lambda: self._show_view(0))
        h.addWidget(self.btn_back)
        self.btn_main = QPushButton()
        self.btn_main.setObjectName("Primary")
        self.btn_main.setMinimumHeight(36)
        self.btn_main.clicked.connect(self._main_action)
        h.addWidget(self.btn_main)
        return bar

    # ---------------- 结果页 ----------------
    def _build_result(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 12, 24, 16)
        v.setSpacing(14)
        ok, ov = _panel()
        oh = QHBoxLayout()
        self.ok_icon = QLabel()
        self.ok_icon.setFixedSize(42, 42)
        self.ok_icon.setAlignment(Qt.AlignCenter)
        oc = QVBoxLayout()
        oc.setSpacing(3)
        self.ok_title = QLabel("")
        self.ok_title.setStyleSheet("font-size:16px; font-weight:700;")
        self.ok_path = QLabel("")
        self.ok_path.setObjectName("Faint")
        self.ok_path.setFont(num_font(9))
        self.ok_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        oc.addWidget(self.ok_title)
        oc.addWidget(self.ok_path)
        b1 = QPushButton(tr("打开汇总表"))
        b1.clicked.connect(lambda: self._open(self.result and self.result["summary"]))
        b2 = QPushButton(tr("打开订单文件夹"))
        b2.setObjectName("Primary")
        b2.clicked.connect(lambda: self._open(self.result and self.result["out_root"]))
        oh.addWidget(self.ok_icon)
        oh.addSpacing(6)
        oh.addLayout(oc, 1)
        oh.addWidget(b1)
        oh.addWidget(b2)
        ov.addLayout(oh)
        v.addWidget(ok)

        k = QGridLayout()
        k.setSpacing(12)
        c1, self.r_total, self.r_total_sub = kpi_card(tr("采购总额"), "—", " ")
        c2, self.r_direct, self.r_direct_sub = kpi_card(tr("直接送店"), "—", " ")
        c3, self.r_bms, self.r_bms_sub = kpi_card(tr("BMS 分货"), "—", " ")
        c4, self.r_warn, self.r_warn_sub = kpi_card(tr("需要注意"), "—", " ")
        for i, c in enumerate((c1, c2, c3, c4)):
            k.addWidget(c, 0, i)
        v.addLayout(k)

        rp, rv = _panel()
        rh = QHBoxLayout()
        self.route_title = QLabel(tr("配货路线"))
        self.route_title.setObjectName("Section")
        rh.addWidget(self.route_title, 1)
        self.route_moq = chip("", "accent")
        rh.addWidget(self.route_moq)
        self.route_sup = QComboBox()
        self.route_sup.setMinimumWidth(220)
        self.route_sup.currentIndexChanged.connect(self._show_route)
        rh.addWidget(self.route_sup)
        rv.addLayout(rh)
        mh = QHBoxLayout()
        mh.setSpacing(8)
        ml = QLabel(tr("这家怎么订："))
        ml.setObjectName("Faint")
        mh.addWidget(ml)
        self.mode_group = QButtonGroup(self.route_sup)
        self.mode_group.setExclusive(True)
        self.mode_btns = {}
        for mode, text, tip in (("auto", tr("系统建议"), tr("按配货规则：够起订量的直送；不够的按优先顺序并进 BMS，够起订量就停；凑不够的这次不订")),
                                ("manual", tr("手动选进 BMS 的门店…"), tr("自己决定哪几家门店整单进 BMS（自己够起订量的也可以并进来），看着紧急缺货占比决定")),
                                ("all_bms", tr("全部并入 BMS"), tr("所有要订的门店都并进 BMS，由 BMS 订货后分货（包括自己够起订量的门店）；BMS 不够起订量也照样下单")),
                                ("direct_only", tr("只订达标门店，其他不订"), tr("只给自己够起订量的门店下单，其他门店这次不订，也不借货"))):
            b = QPushButton(text)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("mode", mode)
            from erp_widgets import fit_chip
            fit_chip(b)
            self.mode_group.addButton(b)
            self.mode_btns[mode] = b
            mh.addWidget(b)
        self.mode_group.buttonClicked.connect(lambda b: self._apply_mode(b.property("mode")))
        self.mode_note = QLabel("")
        self.mode_note.setObjectName("Faint")
        mh.addWidget(self.mode_note, 1)
        rv.addLayout(mh)
        self.route_body = QHBoxLayout()
        self.route_body.setSpacing(18)
        rw = QWidget()
        rw.setLayout(self.route_body)
        rv.addWidget(rw)
        self.route_panel = rp
        v.addWidget(rp)

        row = QHBoxLayout()
        row.setSpacing(14)
        tp, tv = _panel()
        th = QHBoxLayout()
        tt = QLabel(tr("配货结果"))
        tt.setObjectName("Section")
        th.addWidget(tt, 1)
        tip = QLabel(tr("双击一行打开订单文件"))
        tip.setObjectName("Faint")
        th.addWidget(tip)
        tv.addLayout(th)
        self.rtable = self.ui.make_table([tr("星期"), tr("供应商"), tr("起订量"), tr("去向"), tr("箱数"), tr("金额"), tr("状态"),
                                          tr("紧急缺货"), tr("说明")], stretch_col=8, numeric_cols=(4, 5, 7))
        self.rtable.horizontalHeaderItem(7).setToolTip(
            tr("这张单里库存够卖不到一周的商品占多少（按金额）。红色 ≥ 50%、黄色 ≥ 25%：「这次不订」的单紧急的多，可以考虑手动凑一张 BMS"))
        self.rtable.setMinimumHeight(360)
        self.rtable.verticalHeader().setDefaultSectionSize(36)
        self.rtable.cellDoubleClicked.connect(self._open_row)
        self._rrows = []
        self.r_sorter = RowSorter(self.rtable, refill=self._results_refill)
        tv.addWidget(self.rtable)
        row.addWidget(tp, 3)
        fp, fv = _panel(tr("生成的文件"))
        self.file_list = QVBoxLayout()
        self.file_list.setSpacing(6)
        fv.addLayout(self.file_list)
        fv.addStretch()
        row.addWidget(fp, 1)
        v.addLayout(row)
        v.addStretch()
        return w

    # =================================================================
    # 状态
    # =================================================================
    def _show_view(self, i):
        self.views.setCurrentIndex(i)
        self._update_state()

    def _update_state(self):
        in_result = self.views.currentIndex() == 1
        st = self.steps
        n_sel = self._n_checked()
        if self.result and in_result:
            for i in range(5):
                st.set_state(i, "done")
            st.set_state(4, "done", tr("{0} 个文件", len(self.result["written"])))
            st.set_state(3, "done", "")
        elif self.calc:
            st.set_state(0, "done", tr("{0} 家", len(self.calc["schede"])))
            st.set_state(1, "done", tr("新算法") if (self.calc["demand"].get("_algo") or {}).get("engine") == "v2" else tr("原程序算法"))
            st.set_state(2, "now", tr("改了 {0} 处", len(self.edited)))
            st.set_state(3, "todo", tr("直接送店 / BMS"))
            st.set_state(4, "todo", tr("文件 + 汇总"))
        else:
            st.set_state(0, "done" if n_sel else "now", tr("已选 {0} 家", n_sel))
            st.set_state(1, "now" if n_sel else "todo", tr("点「开始计算」"))
            st.set_state(2, "todo", tr("可手动修改箱数"))
            st.set_state(3, "todo", tr("直接送店 / BMS"))
            st.set_state(4, "todo", tr("文件 + 汇总"))
        self.btn_recalc.setVisible(bool(self.calc) and not in_result)
        self.btn_back.setVisible(in_result)
        self.btn_fill.setVisible(not in_result)
        unchanged = self._result_current()
        if in_result:
            self.btn_main.setText(tr("完成，回到工作台"))
        elif unchanged:
            self.btn_main.setText(tr("回到配货结果  →"))
        elif self.calc:
            n_on, n_all = len(self.alloc_on), len(self.calc["schede"])
            self.btn_main.setText(tr("开始配货  →") if n_on == n_all else tr("配货 {0} 家  →", n_on))
        else:
            self.btn_main.setText(tr("开始计算  →"))
        self.btn_main.setEnabled(not self.running)
        self.btn_detail.setEnabled(bool(self.calc))
        ne = len(self.edited)
        set_chip(self.edit_chip, tr("已手动修改 {0} 处", ne), "warn")
        self.edit_chip.setVisible(ne > 0 and not in_result)
        fc = self.fill_cfg()
        if in_result:
            self.bar_text.setText(tr("订单文件已生成；要改的话点「返回修改」再配货一次（会另存为新的一次，不覆盖这次的文件）"))
        elif unchanged:
            self.bar_text.setText(tr("和上次配货相比没有改动，点「回到配货结果」直接回去，不用重新配货；改了箱数、勾选或配货设置后会重新配货"))
        else:
            self.bar_text.setText(tr("凑单：金额差 ≤ €{0:g} · 箱数差 ≤ {1} · 凑后最多够卖 {2} 天 · 订单放到 {3}\\第几次_时间",
                                     fc["max_amount_gap"], fc["max_colli_gap"], fc["max_stock_days"],
                                     os.path.normpath(self._out_root()) if self.folder.text().strip() else "—"))

    def _main_action(self):
        if self.views.currentIndex() == 1:
            self.win.go_page("home")
            self._show_view(0)
        elif self._result_current():
            self._show_view(1)
        elif self.calc:
            self.start_allocate()
        else:
            self.start_compute()

    def restyle(self):
        self.steps.restyle()
        for dot, kind in self.legend:
            dot.setStyleSheet(f"background:{T('ok') if kind == 'ok' else T(kind)}; border-radius:3px;")
        self.detail_icon.setPixmap(icon("table", T("accent"), 20).pixmap(20, 20))
        self.detail_icon.setStyleSheet(f"background:{T('accent_soft')}; border-radius:10px;")
        self.ok_icon.setPixmap(icon("check", T("ok"), 22).pixmap(22, 22))
        self.ok_icon.setStyleSheet(f"background:{T('ok_soft')}; border-radius:21px;")
        self.btn_recalc.setIcon(icon("refresh", T("muted"), 15))
        self.btn_refresh.setIcon(icon("refresh", T("muted"), 15))
        self.btn_folder_pick.setIcon(icon("folder", T("muted"), 15))
        self._update_state()
        if self.calc:
            self._refresh_matrix()
        if self.result:
            self._fill_result()

    # =================================================================
    # 供应商列表
    # =================================================================
    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self.win, tr("选择采购配单文件夹"), self.folder.text())
        if d:
            self.folder.setText(os.path.normpath(d))
            self.refresh_list()

    def _skip(self):
        return set(self.cfg.get("skip") or [])

    def _out_root(self):
        return S.output_root(self.folder.text().strip())

    def _fpath(self, row):
        """左边列表第 row 行的 SCHEDA 路径（表格可以排序，不能用 self.files[row]）"""
        it = self.ftable.item(row, 1)
        return it.data(Qt.UserRole) if it is not None else None

    def _frows(self):
        """[(表格行, SCHEDA 路径)]"""
        return [(i, self._fpath(i)) for i in range(self.ftable.rowCount()) if self._fpath(i)]

    def _n_checked(self):
        t = self.ftable
        return sum(1 for i in range(t.rowCount()) if t.item(i, 0) and t.item(i, 0).checkState() == Qt.Checked)

    def refresh_list(self, *_):
        root = self.folder.text().strip()
        day = self.day.currentData()
        if root and root != self.cfg.get("folder"):
            self.cfg["folder"] = root
            save_settings(self.settings)
        self.files = S.list_schede(root, None if day == ALL else day) if os.path.isdir(root) else []
        skip = self._skip()
        t = self.ftable
        t.blockSignals(True)
        t.setRowCount(len(self.files))
        for i, (d, p) in enumerate(self.files):
            name = S.supplier_short_name(p)
            m = self.moq_of(p)
            self.ui.table_set_row(t, i, ["", name, S.moq_label(m, short=True), "", tr(d)])
            self._moq_tip(i, p)
            f = t.item(i, 1).font()
            f.setBold(True)
            t.item(i, 1).setFont(f)
            t.item(i, 1).setData(Qt.UserRole, p)
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Unchecked if name in skip else Qt.Checked)
            t.setItem(i, 0, it)
        t.blockSignals(False)
        t.setColumnHidden(4, day != ALL)
        # 「全部」多一列星期：左边面板放宽一点补回这一列的宽度
        self.left_panel.setMinimumWidth(412 if day == ALL else 360)
        self.left_panel.setMaximumWidth(452 if day == ALL else 400)
        self._update_dots()
        self.f_sorter.apply()
        if not os.path.isdir(root):
            self.list_msg.setText(tr("文件夹不存在"))
        elif not self.files:
            self.list_msg.setText(tr("这一天的文件夹里没有 SCHEDA 文件"))
        else:
            self._update_list_msg()
        self._filter_sup()
        todo = [p for _, p in self.files if p not in self.moq]
        if todo:
            self.list_token += 1
            token = self.list_token

            def work():
                for p in todo:
                    try:
                        self.moq[p] = S.parse_moq(p)
                    except Exception as e:
                        self.moq[p] = dict(S.read_moq(None), raw=tr("读不出来：{0}", e))
                self.sig.listed.emit(token)

            threading.Thread(target=work, daemon=True).start()
        if hasattr(self, "steps"):
            self._update_state()

    def _listed(self, token):
        if token != self.list_token:
            return
        t = self.ftable
        t.blockSignals(True)
        for i, p in self._frows():
            if p in self.moq:
                t.item(i, 2).setText(S.moq_label(self.moq_of(p), short=True))
                self._moq_tip(i, p)
        t.blockSignals(False)
        if hasattr(self.win, "dashboard"):
            self.win.dashboard.refresh()

    def _update_dots(self):
        """各店色块：计算后按起订量检查着色"""
        t = self.ftable
        by_path = {sc["path"]: sc for sc in (self.calc or {}).get("schede", [])}
        for i, p in self._frows():
            sc = by_path.get(p)
            colors = []
            for sid in S.STORE_SHEETS:
                if not sc or sid not in self.calc["stores"]:
                    colors.append(T("track"))
                    continue
                plan = getattr(self, "_plans", {}).get(p)
                c_, _a, _st = self._cell_state(sc, self.colli.get(p, {}).get(sid, {}))
                st = self._plan_state(plan, sid) if (plan and c_) else "none"
                colors.append({"ok": T("ok"), "fill": T("ok"), "bms_ok": T("near"), "short": T("far"),
                               "none": T("track")}[st])
            it = t.item(i, 3)
            if it:
                it.setData(DOTS_ROLE, colors)
                # 排序：够起订量的门店越少越靠前（升序先看问题多的）
                it.setData(SORT_ROLE, sum(1 for c in colors if c == T("ok")) * 10 +
                           sum(1 for c in colors if c == T("near")) if sc else 999)
        t.viewport().update()

    def _filter_sup(self, *_):
        text = self.f_sup_text.text().strip().lower()
        for i in range(self.ftable.rowCount()):
            it = self.ftable.item(i, 1)
            self.ftable.setRowHidden(i, bool(text) and it is not None and text not in it.text().lower())

    def _update_list_msg(self):
        self.list_msg.setText(tr("{0} 家 · 已选 {1}", len(self.files), self._n_checked()))

    def _item_changed(self, item):
        if item.column() != 0:
            return
        name = self.ftable.item(item.row(), 1).text()
        skip = self._skip()
        if item.checkState() == Qt.Checked:
            skip.discard(name)
        else:
            skip.add(name)
        self.cfg["skip"] = sorted(skip)
        save_settings(self.settings)
        self._update_list_msg()
        self._update_state()

    def _check_all(self, on):
        for i in range(self.ftable.rowCount()):
            if not self.ftable.isRowHidden(i):
                self.ftable.item(i, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def edit_fill(self):
        if FillDialog(self).exec():
            self.apply_transfers(refresh_auto=True)
            self._refresh_matrix()
            self._update_state()
            if self.detail_win is not None:
                self.detail_win.load()

    # =================================================================
    # ② 计算
    # =================================================================
    def _save_cfg(self):
        self.cfg.update(folder=self.folder.text().strip(), day=self.day.currentData(),
                        stores=[s for s, cb in self.store_cb.items() if cb.isChecked()],
                        cycle=self.cycle.currentData(), days=[sb.value() for sb in self.days],
                        use_supplier_cycles=self.use_cfg.isChecked())
        save_settings(self.settings)

    def start_compute(self):
        if self.running:
            return
        on = {p for i, p in self._frows() if self.ftable.item(i, 0) and self.ftable.item(i, 0).checkState() == Qt.Checked}
        chosen = [f for f in self.files if f[1] in on]
        stores = [s for s, cb in self.store_cb.items() if cb.isChecked()]
        if not chosen:
            QMessageBox.information(self.win, tr("门店配货"), tr("先勾选要订的供应商。"))
            return
        if not stores:
            QMessageBox.information(self.win, tr("门店配货"), tr("至少选一家门店。"))
            return
        if self.edited and QMessageBox.question(
                self.win, tr("门店配货"), tr("重新计算会丢掉 {0} 处手动修改，继续？", len(self.edited))) != QMessageBox.Yes:
            return
        self._save_cfg()
        days = [sb.value() for sb in self.days]
        cycle = self.cycle.currentData()
        use_cfg = self.use_cfg.isChecked()
        algo = self.current_algo()
        dbs = self.settings.get("db") or None
        log = self.win.log
        self.running = True
        self._update_state()
        dlg = TaskDialog(self.win, tr("正在计算订货量"),
                         tr("{0} 家供应商 · {1} 家门店 · {2}", len(chosen), len(stores),
                            tr("新算法") if algo.get("engine") == "v2" else tr("原程序算法")),
                         [tr(x) for x in S.COMPUTE_STEPS], cancellable=True)
        dlg.went_background.connect(lambda: self.win.show_toast(tr("订货量在后台计算，完成后会提醒")))
        self._task = dlg
        dlg.show()

        def work():
            try:
                db = R.make_db(dbs)
                calc = S.compute(db, chosen, stores, days, cycle, log=lambda m: log("[门店配货] " + m),
                                 use_supplier_cycles=use_cfg, algo=algo, moq_overrides=dict(self.moq_overrides()),
                                 progress=lambda i, st, cnt, det, pct: (dlg.post_step(i, st, cnt, det), dlg.post_progress(pct)),
                                 cancel=lambda: dlg.cancel_event.is_set())
                db.engine.dispose()
                dlg.post_finished(True, tr("完成：{0} 家供应商", len(calc["schede"])))
                self.sig.computed.emit((calc, None))
            except InterruptedError:
                dlg.post_finished(False, tr("已取消"))
                self.sig.computed.emit((None, "__cancel__"))
            except Exception as e:
                import traceback
                log(tr("❌ 门店配货失败：{0}", e))
                log(traceback.format_exc())
                dlg.post_finished(False, tr("{0}\n\n数据库连不上的话，到「系统设置」检查地址和密码。", e))
                self.sig.computed.emit((None, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _computed(self, payload):
        calc, err = payload
        self.running = False
        if err:
            self._update_state()
            if err != "__cancel__":
                self.win.notify(tr("门店配货计算失败：{0}", err))
            return
        import datetime as _dt
        calc.setdefault("date", _dt.date.today())    # 周销量按计算当天往回数
        self.calc = calc
        try:
            S.apply_transfers(calc, self.transfer_cfg(), self.transfer_far(), self.cfg.get("supplier_cfg"))
            if calc.get("transfers"):
                self.win.log(tr("[门店配货] 门店间调货：{0} 个商品", len(calc["transfers"])))
        except Exception as e:
            self.win.log(tr("[门店配货] ⚠ 调货计算失败（按原来的建议订）：{0}", e))
        self.colli = copy.deepcopy(calc["base"])
        self.edited = set()
        self._refresh_transfer_panel()
        # 默认只勾选订得起来的供应商：有门店单独够起订量（含凑单），或合起来够 BMS 起订量
        self.alloc_on = {sc["path"] for sc in calc["schede"] if self._orderable(self._preview(sc))}
        self.result = None
        self.result_sig = None
        self._refresh_matrix()
        if self.detail_win is not None:
            self.detail_win.load()
        n_lines = sum(1 for v in self.colli.values() for st in v.values() for n in st.values() if n > 0)
        msg = tr("计算完成：{0} 家供应商，{1} 行要订", len(calc["schede"]), n_lines)
        if calc["errors"]:
            msg += tr("；{0} 个 SCHEDA 读不出来，详见运行日志", len(calc["errors"]))
        if calc["missing"]:
            msg += tr("；{0} 个商品在数据库里找不到", len(calc["missing"]))
        self.detail_title.setText(tr("订货明细 · {0} 行要订", n_lines))
        self._show_view(0)
        # 订货明细窗口在后台先分批载入（不显示），第一次点开就不用等
        QTimer.singleShot(500, self._prewarm_detail)
        self.win.show_toast(msg)
        if hasattr(self.win, "dashboard"):
            self.win.dashboard.refresh()

    # =================================================================
    # ③ 明细（独立窗口）
    # =================================================================
    def _prewarm_detail(self):
        if not self.calc or self.detail_win is not None:
            return
        from store_detail_window import DetailWindow
        self.detail_win = DetailWindow(self)
        self.detail_win.load()

    def open_detail(self, path=ALL, sid=ALL):
        if not self.calc:
            return
        if self.detail_win is None:
            from store_detail_window import DetailWindow
            self.detail_win = DetailWindow(self)
            self.detail_win.load()
        self.detail_win.set_filter(path, sid)
        self.detail_win.bring_up()

    def set_qty(self, sc, sid, row, n, refresh=True):
        key = (sc["path"], sid, row)
        lines = self.colli.setdefault(sc["path"], {}).setdefault(sid, {})
        if n > 0:
            lines[row] = n
        else:
            lines.pop(row, None)
        base = self.calc["base"][sc["path"]].get(sid, {}).get(row, 0)
        if n == base:
            self.edited.discard(key)
        else:
            self.edited.add(key)
        if refresh:
            self.lines_changed(sc["path"])

    def lines_changed(self, path=None):
        self._refresh_matrix(path)
        self._update_state()

    # ---- 起订量检查 ----
    def _cell_state(self, sc, lines):
        items = {it["row"]: it for it in sc["items"]}
        c, a = S._totals(lines, items)
        if c == 0:
            return c, a, "none"
        if S._satisfied(c, a, sc["moq_colli"], sc["moq_amount"]):
            return c, a, "ok"
        fc = self.fill_cfg()
        gap_c = max(0.0, sc["moq_colli"] - c) if sc["moq_colli"] else 0
        gap_a = max(0.0, sc["moq_amount"] - a) if sc["moq_amount"] else 0
        if (not sc["moq_colli"] or gap_c <= fc["max_colli_gap"]) and (not sc["moq_amount"] or gap_a <= fc["max_amount_gap"]):
            return c, a, "near"
        return c, a, "far"

    def _preview(self, sc):
        """
        按现在的箱数和配货设置模拟一次配货（和真正配货同一套规则），起订量检查着色用。
        箱数和设置没变的供应商用上次的结果（改一个调货 / 箱数时不用把几十家全部重算一遍）
        """
        import json
        lines = self.colli.get(sc["path"], {})
        fill = self.fill_cfg()
        key = (sc["path"], id(self.calc["demand"]), tuple(self.calc["stores"]),
               json.dumps(fill, sort_keys=True, default=str),
               tuple(sorted((sid, tuple(sorted(ln.items()))) for sid, ln in lines.items())), json.dumps(S.sc_moq(sc), default=str))
        cache = self.__dict__.setdefault("_pv_cache", {})
        if key not in cache:
            if len(cache) > 400:
                cache.clear()
            cache[key] = S.plan_supplier(sc, self.calc["demand"], fill, self.calc["stores"], lines)
        return cache[key]

    @staticmethod
    def _orderable(plan):
        return any(st["status"] in ("direct", "filled") for st in plan["stores"].values()) or \
            S.bms_ordered(plan.get("bms"))

    @staticmethod
    def _plan_state(plan, sid):
        st = plan["stores"].get(sid)
        if not st or st["status"] == "none":
            return "none"
        if st["status"] == "direct":
            return "ok"
        if st["status"] == "filled":
            return "fill"
        if st["status"] in ("bms", "merged", "pulled"):
            return "bms_ok" if S.bms_ordered(plan.get("bms")) else "short"
        return "short"                 # wait / dropped：这次不订

    def _refresh_matrix(self, only_path=None):
        if not self.calc:
            self.matrix_stack.setCurrentIndex(0)
            return
        self.matrix_stack.setCurrentIndex(1)
        t = self.mtable
        schede = self.calc["schede"]
        t.blockSignals(True)
        t.setRowCount(len(schede) + 1)
        bg = {"ok": T("ok_soft"), "fill": T("ok_soft"), "bms_ok": T("warn_soft"), "short": T("track"), "none": T("surface")}
        fg = {"ok": T("ok"), "fill": T("ok"), "bms_ok": T("warn"), "short": T("muted"), "none": T("faint")}
        tips = {"ok": tr("单独够起订量，直接送店"), "fill": tr("差一点，凑单后直接送店"),
                "bms_ok": tr("单独不够，和其他门店合起来够 BMS 起订量 → BMS 订货后分货"),
                "short": tr("单独不够起订量，这次不订（凑不够 BMS，或者 BMS 已经够了用不到它）；配货后可以手动选进 BMS")}
        self._plans = {}
        col_c = {sid: 0.0 for sid in S.STORE_SHEETS}
        col_a = {sid: 0.0 for sid in S.STORE_SHEETS}
        n_ok = n_all = 0
        for r, sc in enumerate(schede):
            on = sc["path"] in self.alloc_on
            plan = self._preview(sc)
            self._plans[sc["path"]] = plan
            vals = ["", f"{sc['name']}（{tr(sc['day'])}）", S.moq_label(S.sc_moq(sc), short=True)]
            states, amts = [], []
            rc = ra = 0.0
            for sid in S.STORE_SHEETS:
                if sid not in self.calc["stores"] or sid not in sc["sheets"]:
                    vals.append("")
                    states.append(None)
                    amts.append(None)
                    continue
                c, a, _st = self._cell_state(sc, self.colli.get(sc["path"], {}).get(sid, {}))
                st = self._plan_state(plan, sid) if c else "none"
                rc += c
                ra += a
                if on:
                    col_c[sid] += c
                    col_a[sid] += a
                if on and st != "none":
                    n_all += 1
                    n_ok += st in ("ok", "fill", "bms_ok")
                vals.append(tr("{0:g} 箱 · €{1:,.0f}", c, a) if c else "—")
                states.append(st)
                amts.append(a if c else None)
            b = plan.get("bms")
            if b:
                vals.append(tr("{0:g} 箱 · €{1:,.0f}", b["colli"], b["amount"]))
            else:
                vals.append("—")
            vals.append(tr("{0:g} 箱 · €{1:,.0f}", rc, ra) if rc else "—")
            orderable = any(x in ("ok", "fill", "bms_ok") for x in states)
            self.ui.table_set_row(t, r, vals)
            for k, a_ in enumerate(amts + [b["amount"] if b else None, ra if rc else None]):
                if a_ is not None:
                    t.item(r, 3 + k).setData(SORT_ROLE, float(a_))      # 排序按金额
            t.item(r, 2).setToolTip(tr("起订量：{0}", S.moq_label(S.sc_moq(sc), mark=False)) + "\n" + tr("点一下：起订量、这家供应商参与 BMS / 调货的门店"))
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if on else Qt.Unchecked)
            chk.setData(Qt.UserRole, sc["path"])
            t.setItem(r, 0, chk)
            f = t.item(r, 1).font()
            f.setBold(on and orderable)
            f.setStrikeOut(not on)
            t.item(r, 1).setFont(f)
            if on and not orderable:
                t.item(r, 1).setForeground(QColor(T("muted")))
                t.item(r, 1).setToolTip(tr("这家这次订不起来：没有门店够起订量，合起来也不够 BMS 起订量"))
            if not on:
                for c in range(1, t.columnCount()):
                    t.item(r, c).setForeground(QColor(T("faint")))
                t.item(r, 1).setToolTip(tr("没勾选：这次不配货、不生成订单"))
                continue
            t.item(r, 2).setForeground(QColor(T("muted")))
            for k, st in enumerate(states):
                if st:
                    it = t.item(r, 3 + k)
                    it.setBackground(QColor(bg[st]))
                    it.setForeground(QColor(fg[st]))
                    if st in tips:
                        it.setToolTip(tips[st])
                    if st in ("ok", "bms_ok"):
                        f = it.font()
                        f.setBold(True)
                        it.setFont(f)
            bi = t.item(r, 3 + len(S.STORE_SHEETS))
            if b:
                ok_b = S.bms_ordered(b)
                bi.setBackground(QColor(T("warn_soft") if ok_b else T("track")))
                bi.setForeground(QColor(T("warn") if ok_b else T("muted")))
                f = bi.font()
                f.setBold(ok_b)
                bi.setFont(f)
                how = S.bms_note(b)
                bi.setToolTip((tr("够 BMS 起订量") if ok_b else tr("不够 BMS 起订量（{0}）", self._gap_note(sc, b)))
                              + "\n" + how)
        tc, ta = sum(col_c.values()), sum(col_a.values())
        bc = sum(p_["bms"]["colli"] for k_, p_ in self._plans.items() if k_ in self.alloc_on and S.bms_ordered(p_.get("bms")))
        ba = sum(p_["bms"]["amount"] for k_, p_ in self._plans.items() if k_ in self.alloc_on and S.bms_ordered(p_.get("bms")))
        foot = ["", tr("合计"), ""] + [tr("{0:g} 箱 · €{1:,.0f}", col_c[sid], col_a[sid]) if col_c[sid] else "—"
                                  for sid in S.STORE_SHEETS] + [tr("{0:g} 箱 · €{1:,.0f}", bc, ba) if bc else "—",
                                                                f"€{ta:,.0f}"]
        self.ui.table_set_row(t, len(schede), foot, bold=True)
        for c in range(t.columnCount()):
            t.item(len(schede), c).setBackground(QColor(T("surface2")))
        self.m_sorter.apply()
        t.blockSignals(False)
        n_on = sum(1 for sc in schede if sc["path"] in self.alloc_on)
        self.m_sel.setText(tr("配货 {0} / {1} 家", n_on, len(schede)))
        self.m_sel.setStyleSheet(f"color:{T('warn')};" if n_on < len(schede) else "")
        self.k_colli.setText(tr("{0:g} 箱", tc))
        self.k_amt.setText(f"€{ta:,.2f}")
        self.k_ok.setText(f"{n_ok} / {n_all}")
        self.k_ok.setStyleSheet(f"color:{T('ok')};" if n_all and n_ok == n_all else "")
        self.k_edit.setText(tr("{0} 处", len(self.edited)))
        self.k_edit.setStyleSheet(f"color:{T('warn')};" if self.edited else "")
        self._update_dots()
        if hasattr(self.win, "dashboard") and only_path is None:
            self.win.dashboard.refresh()

    def _matrix_clicked(self, row, col):
        t = self.mtable
        if not self.calc or not 0 <= row < t.rowCount() or col == 0:
            return
        path = ALL if row == t.rowCount() - 1 else (t.item(row, 0).data(Qt.UserRole) if t.item(row, 0) else None)
        if not path:
            return
        if col == 2 and path != ALL:
            self.edit_moq(path)
            return
        stores = list(S.STORE_SHEETS)
        sid = stores[col - 3] if 3 <= col < 3 + len(stores) else ALL     # BMS / 合计列：这家供应商全部门店
        self.open_detail(path, sid)

    def _alloc_toggled(self, item):
        if item.column() != 0 or not self.calc:
            return
        p = item.data(Qt.UserRole)
        if not p:
            return
        if item.checkState() == Qt.Checked:
            self.alloc_on.add(p)
        else:
            self.alloc_on.discard(p)
        self._refresh_matrix()
        self._update_state()

    def _set_all_alloc(self, on):
        if not self.calc:
            return
        if on is None:
            self.alloc_on = {sc["path"] for sc in self.calc["schede"] if self._orderable(self._preview(sc))}
        else:
            self.alloc_on = {sc["path"] for sc in self.calc["schede"]} if on else set()
        self._refresh_matrix()
        self._update_state()

    # =================================================================
    # ④ 配货
    # =================================================================
    def start_allocate(self):
        if self.running or not self.calc:
            return
        self._save_cfg()
        chosen = [sc for sc in self.calc["schede"] if sc["path"] in self.alloc_on]
        if not chosen:
            QMessageBox.information(self.win, tr("门店配货"), tr("起订量检查里没有勾选供应商。勾选要配货的供应商后再点「开始配货」。"))
            return
        try:
            out_root = S.new_run_folder(self._out_root())     # 每次配货单独一个文件夹，不覆盖以前的
        except OSError as e:
            QMessageBox.warning(self.win, tr("门店配货"), tr("建不了订单文件夹：{0}", e))
            return
        fill = self.fill_cfg()
        keep = {sc["path"] for sc in chosen}
        sig = self._alloc_sig()
        calc = dict(self.calc, schede=chosen)
        colli = {p: v for p, v in copy.deepcopy(self.colli).items() if p in keep}
        edited = {k for k in self.edited if k[0] in keep}
        log = self.win.log
        self.running = True
        self._update_state()
        dlg = TaskDialog(self.win, tr("正在配货、生成订单"), tr("{0} 家供应商", len(calc["schede"])),
                         [tr(x) for x in S.ALLOCATE_STEPS], cancellable=False)
        dlg.went_background.connect(lambda: self.win.show_toast(tr("订单在后台生成，完成后会提醒")))
        dlg.show()

        def work():
            try:
                res = S.allocate(calc, colli, fill, out_root, log=lambda m: log("[门店配货] " + m), edited=edited,
                                 progress=lambda i, st, cnt, det, pct: (dlg.post_step(i, st, cnt, det), dlg.post_progress(pct)))
                dlg.post_finished(True, tr("生成 {0} 个订单文件", len(res["written"])))
                res["sig"] = sig
                self.sig.done.emit((res, None))
            except Exception as e:
                import traceback
                log(tr("❌ 门店配货失败：{0}", e))
                log(traceback.format_exc())
                dlg.post_finished(False, str(e))
                self.sig.done.emit((None, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, payload):
        res, err = payload
        self.running = False
        if err:
            self._update_state()
            self.win.notify(tr("门店配货失败：{0}", err))
            return
        self.result = res
        self.result_sig = res.get("sig")
        self._fill_result()
        self._show_view(1)
        self.win.show_toast(tr("门店配货完成：{0} 个订单文件", len(res["written"])))
        if hasattr(self.win, "dashboard"):
            self.win.dashboard.refresh()

    # ---------------- 结果 ----------------
    def _fill_result(self):
        res = self.result
        tot = S.plan_totals(res["results"])
        self.ok_title.setText(tr("配货完成，生成 {0} 个订单文件", len(res["written"]))
                              + (tr(" + 调货单（{0} 个商品）", len((self.calc or {}).get("transfers") or []))
                                 if res.get("transfers_file") else ""))
        self.ok_path.setText(os.path.normpath(res["out_root"]))
        n_direct = sum(1 for _, _, plan in res["results"] for st in plan["stores"].values()
                       if st["status"] in ("direct", "filled"))
        n_bms = sum(1 for _, _, plan in res["results"] if S.bms_ordered(plan.get("bms")))
        self.r_total.setText(f"€{tot['total']:,.2f}")
        self.r_total.setStyleSheet(f"color:{T('accent')};")
        self.r_total_sub.setText(tr("{0:g} 箱 · {1} 家供应商", tot["direct_colli"] + tot["bms_colli"], len(res["results"])))
        self.r_direct.setText(f"€{tot['direct']:,.2f}")
        self.r_direct_sub.setText(tr("{0:g} 箱 · {1} 张单", tot["direct_colli"], n_direct))
        self.r_bms.setText(f"€{tot['bms']:,.2f}")
        self.r_bms_sub.setText(tr("{0:g} 箱 · {1} 张单", tot["bms_colli"], n_bms))
        warns = []
        for _, sc, plan in res["results"]:
            b = plan.get("bms")
            waiting = [S.STORE_SHEETS[s_] for s_, st_ in plan["stores"].items() if st_["status"] == "wait"]
            if b and b["status"] == "forced":
                warns.append(tr("{0} 手动并入 BMS，未达起订量", sc["name"]))
            elif waiting:
                warns.append(tr("{0}：{1} 这次不订（凑不够 BMS）", sc["name"], tr("、").join(waiting)))
        self.r_warn.setText(str(len(warns)))
        self.r_warn.setStyleSheet(f"color:{T('warn')};" if warns else f"color:{T('ok')};")
        self.r_warn_sub.setText(warns[0] if warns else tr("没有需要处理的"))

        # 路线：所有有订货的供应商（可手动改去向）；默认选上次看的那家，没有就选第一家有 BMS 的
        prev = self.route_sup.currentText()
        self.route_sup.blockSignals(True)
        self.route_sup.clear()
        first_bms = None
        for k, (_, sc, plan) in enumerate(res["results"]):
            if plan.get("bms") or any(st["status"] != "none" for st in plan["stores"].values()):
                mode = plan.get("mode", "auto")
                label = sc["name"] + ("" if mode == "auto" else "  ✎")
                self.route_sup.addItem(label, k)
                if first_bms is None and plan.get("bms"):
                    first_bms = self.route_sup.count() - 1
        idx = next((i for i in range(self.route_sup.count())
                    if self.route_sup.itemText(i).replace("  ✎", "") == prev.replace("  ✎", "")), None)
        self.route_sup.setCurrentIndex(idx if idx is not None else (first_bms or 0))
        self.route_sup.blockSignals(False)
        self.route_panel.setVisible(self.route_sup.count() > 0)
        self._show_route()

        # 表格
        rows, self.row_files, self._res_index = [], [], []
        files = {(S.written_source(w, res), w[2]): w[0] for w in res["written"]}
        for k_res, (day, sc, plan) in enumerate(res["results"]):
            items = {it["row"]: it for it in sc["items"]}
            moq = S.moq_label(S.sc_moq(sc), short=True)
            for sid, st in plan["stores"].items():
                if st["status"] == "none":
                    continue
                name = S.STORE_SHEETS[sid]
                notes = []
                if st["status"] == "dropped":
                    notes.append(self._gap_note(sc, st) + "；" + tr("手动选择：只订达标门店"))
                if st["added"]:
                    notes.append(tr("加了：") + tr("、").join(f"{items[r]['desc'][:18]} +{n}" for r, n, _ in st["added"][:3]))
                if st["lent"]:
                    notes.append(tr("借给 BMS {0:g} 箱 €{1:,.2f}", sum(n for _, n, _ in st["lent"]), sum(v for _, _, v in st["lent"])))
                if st["status"] in ("bms", "wait"):
                    notes.append(self._gap_note(sc, st))
                if st["status"] == "wait":
                    notes.append(tr("BMS 已经够起订量，这家这次不订，下次再看") if S.bms_ordered(plan.get("bms"))
                                 else tr("凑不够 BMS，这次不订，下次再看"))
                if st["status"] == "merged":
                    notes.append(tr("和 BMS 同一个仓库，整单并进 BMS"))
                if st["status"] == "pulled":
                    notes.append(tr("自己够起订量，手动并进 BMS"))
                rows.append((tr(day), sc["name"], moq, name, st, st["status"], "；".join(x for x in notes if x)))
                self._res_index.append(k_res)
                self.row_files.append(files.get((sc["path"], name)))
            b = plan.get("bms")
            if b:
                note = tr("合并：") + tr("、").join(S.STORE_SHEETS[s] for s in b["stores"])
                if b.get("lenders"):
                    note += "；" + tr("借货：") + tr("、").join(f"{S.STORE_SHEETS[s_]} {sum(r_.values()):g}" for s_, r_ in b["lenders"].items())
                if b["status"] in ("short", "forced"):
                    note += "；" + self._gap_note(sc, b)
                    if b["status"] == "short":
                        note += "；" + tr("没有生成文件")
                elif b["added"]:
                    note += "；" + tr("凑单 {0:g} 箱", sum(n for _, n, _ in b["added"]))
                if "manual" in b.get("how", []):
                    note += "；" + tr("手动选择：全部并入 BMS")
                if "picked" in b.get("how", []):
                    note += "；" + tr("手动选的门店")
                rows.append((tr(day), sc["name"], moq, S.BMS, b, b["status"], note))
                self._res_index.append(k_res)
                self.row_files.append(files.get((sc["path"], S.BMS)))
        self._rrows = list(zip(rows, self._res_index, self.row_files))
        self._draw_results()
        self.r_sorter.apply()

        # 文件
        while self.file_list.count():
            w = self.file_list.takeAt(0).widget()
            if w:
                w.deleteLater()
        paths = [w[0] for w in res["written"]] + [res["summary"]]
        for p in paths[:40]:
            fr = QFrame()
            fr.setObjectName("MiniCard")
            h = QHBoxLayout(fr)
            h.setContentsMargins(10, 6, 8, 6)
            ic = QLabel()
            ic.setPixmap(icon("file", T("ok"), 16).pixmap(16, 16))
            nm = QLabel(os.path.basename(p))
            nm.setFont(num_font(9))
            nm.setToolTip(p)
            ob = QPushButton(tr("打开"))
            ob.setObjectName("Link")
            ob.clicked.connect(lambda _=False, p=p: self._open(p))
            h.addWidget(ic)
            h.addWidget(nm, 1)
            h.addWidget(ob)
            self.file_list.addWidget(fr)

    def _show_route(self, *_):
        def _drop(w):
            w.hide()
            w.setParent(None)       # 马上从界面拿掉（deleteLater 要等事件循环，切换时会和新的叠在一起）
            w.deleteLater()

        while self.route_body.count():
            it = self.route_body.takeAt(0)
            if it.widget():
                _drop(it.widget())
            elif it.layout():
                lay = it.layout()
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w:
                        _drop(w)
                lay.deleteLater()
        if not self.result or self.route_sup.currentData() is None:
            return
        _, sc, plan = self.result["results"][self.route_sup.currentData()]
        b = plan.get("bms")
        mode = plan.get("mode", "auto")
        for m, btn in self.mode_btns.items():
            btn.setChecked(m == mode)
        self.mode_note.setText(tr("已手动修改，订单文件已按这个重新生成") if mode != "auto" else
                               tr("点上面的选项会马上重新生成这家的订单文件（只改这一家）"))
        set_chip(self.route_moq, tr("起订量 {0}", S.moq_label(S.sc_moq(sc))), "accent")
        self.route_title.setText(tr("{0} 配货路线", sc["name"]))

        def node(title, sub, kind=None, strong=False):
            f = QFrame()
            f.setObjectName("MiniCard")
            if kind:
                f.setStyleSheet(f"#MiniCard{{background:{T(kind + '_soft')}; border:{2 if strong else 1}px solid {T(kind)}; border-radius:9px;}}")
            v = QVBoxLayout(f)
            v.setContentsMargins(12, 8, 12, 8)
            v.setSpacing(1)
            a = QLabel(title)
            a.setStyleSheet(f"font-weight:{700 if strong else 600}; font-size:12px;" + (f"color:{T(kind)};" if kind else ""))
            s = QLabel(sub)
            s.setFont(num_font(9))
            s.setStyleSheet(f"color:{T(kind) if kind else T('muted')};")
            v.addWidget(a)
            v.addWidget(s)
            f.setMinimumWidth(170)
            return f

        src, mid, dst = QVBoxLayout(), QVBoxLayout(), QVBoxLayout()
        for lay in (src, mid, dst):
            lay.setSpacing(8)
        for sid, st in plan["stores"].items():
            if st["status"] == "none":
                continue
            name = S.STORE_SHEETS[sid]
            short_alone = not S._satisfied(st["before_colli"], st["before_amount"], sc["moq_colli"], sc["moq_amount"])
            k = "warn" if st["status"] in ("bms", "dropped", "wait") and short_alone else None
            src.addWidget(node(name, tr("{0:g} 箱 · €{1:,.2f}", st["before_colli"], st["before_amount"]) +
                               (" · " + self._gap_note(sc, st) if k else ""), k))
            if st["status"] in ("dropped", "wait"):
                arrow = tr("── 这次不订 ──✕")
            elif st["status"] in ("bms", "merged", "pulled"):
                arrow = tr("── 整单并入 BMS ──▶")
            elif st["lent"]:
                arrow = tr("── 直送，借 {0:g} 箱给 BMS ┄▶", sum(n for _, n, _ in st["lent"]))
            else:
                arrow = tr("── 直接送店 ──▶")
            al = QLabel(arrow)
            al.setObjectName("Faint")
            al.setMinimumHeight(44)
            mid.addWidget(al)
            if st["status"] in ("direct", "filled"):
                dst.addWidget(node(tr("{0} 直送", name), tr("{0:g} 箱 · €{1:,.2f}", st["colli"], st["amount"]), "ok"))
        if b:
            dst.addWidget(node(tr("BMS 订单"), tr("{0:g} 箱 · €{1:,.2f}", b["colli"], b["amount"]) +
                               (tr(" · 凑单 {0:g} 箱", sum(n for _, n, _ in b["added"])) if b["added"] else "") +
                               ("" if S.bms_ordered(b) else " · " + tr("不订")),
                               "err" if b["status"] == "short" else ("warn" if b["status"] == "forced" else "accent"), True))
        for lay in (src, mid, dst):
            lay.addStretch()
            self.route_body.addLayout(lay)
        expl = []
        name_of = lambda st_: [S.STORE_SHEETS[s_] for s_, x in plan["stores"].items() if x["status"] in st_]
        if mode == "all_bms":
            expl.append(tr("手动选择：所有门店整单并入 BMS，由 BMS 订货后分货。"))
        elif mode == "direct_only":
            dropped = name_of(("dropped",))
            expl.append(tr("手动选择：只订自己够起订量的门店。") +
                        (tr("{0} 这次不订。", tr("、").join(dropped)) if dropped else ""))
        elif mode == "manual":
            expl.append(tr("手动选择：{0} 进 BMS。", tr("、").join(S.STORE_SHEETS[s_] for s_ in (b or {}).get("stores", []))))
        else:
            fc = self.fill_cfg()
            order = [S.STORE_SHEETS.get(int(x), str(x)) for x in (fc.get("bms_order") or S.BMS_ORDER)]
            if b:
                expl.append(tr("能单独送店的直送；订不起来的按 {0} 的顺序并进 BMS，够起订量就停：{1}。",
                               "-".join(order), tr("、").join(S.STORE_SHEETS[s_] for s_ in b["stores"])))
            elif not name_of(("wait",)):
                expl.append(tr("所有要订的门店都够起订量，直接送店。"))
        waiting = name_of(("wait",))
        if waiting:
            expl.append(tr("{0} 这次不订，下次再看（紧急缺货多的话，可以点「手动选进 BMS 的门店…」凑一张 BMS）。",
                           tr("、").join(waiting)))
        if b:
            if b["added"]:
                expl.append(tr("BMS 凑单 {0:g} 箱。", sum(n for _, n, _ in b["added"])))
            expl.append(tr("BMS 结果：{0}。", tr(S.status_text(S.BMS, b["status"]))))
        far = [S.STORE_SHEETS[x] for x in sorted(S.bms_far(self.fill_cfg(), sc)) if x in S.STORE_SHEETS]
        if far:
            expl.append(tr("{0} 不参与 BMS 凑单。", tr("、").join(far)))
        ex = QLabel("\n".join(expl))
        ex.setWordWrap(True)
        ex.setObjectName("Faint")
        ex.setStyleSheet(f"background:{T('surface2')}; border:1px dashed {T('line')}; border-radius:9px; padding:10px 12px;")
        ex.setMinimumWidth(260)
        self.route_body.addWidget(ex, 1)

    def _apply_mode(self, mode):
        if not self.result or self.route_sup.currentData() is None or self.running:
            return
        k = self.route_sup.currentData()
        _, sc, plan = self.result["results"][k]
        picks = None
        if mode == "manual":                       # 手动选哪几家进 BMS
            dlg = BmsPickDialog(self, sc, plan)
            if not dlg.exec():
                self._show_route()                 # 取消：按钮恢复成原来的
                return
            picks = dlg.picks
        elif plan.get("mode", "auto") == mode:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.result = S.reallocate_one(self.calc, self.colli, self.fill_cfg(), self.result, k, mode,
                                           self.edited, log=lambda m: self.win.log("[门店配货] " + m), picks=picks)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self.win, tr("门店配货"), tr("重新生成失败：{0}", e))
            self._show_route()
            return
        QApplication.restoreOverrideCursor()
        self._fill_result()
        self._update_state()
        self.win.show_toast(tr("{0} 已按「{1}」重新生成订单文件", sc["name"], self.mode_btns[mode].text()))

    # =================================================================
    # 返回修改：判断和上次配货相比有没有改动
    # =================================================================
    def _alloc_sig(self):
        """配货的全部输入：勾选的供应商、每个供应商每家店的箱数、配货设置、起订量"""
        if not self.calc:
            return None
        on = sorted(self.alloc_on)
        colli = tuple((p, tuple(sorted((sid, tuple(sorted((r, n) for r, n in lines.items() if n)))
                                       for sid, lines in self.colli.get(p, {}).items())))
                      for p in on)
        moq = tuple((sc["path"], sc.get("moq_colli"), sc.get("moq_amount")) for sc in self.calc["schede"]
                    if sc["path"] in self.alloc_on)
        return repr((on, colli, sorted(self.fill_cfg().items()), moq, id(self.calc)))

    def _result_current(self):
        """有配货结果，而且回来以后什么都没改"""
        return bool(self.result and self.calc and self.result_sig is not None
                    and self.views.currentIndex() == 0 and self._alloc_sig() == self.result_sig)

    # =================================================================
    # 起订量：手动修改
    # =================================================================
    def moq_overrides(self):
        return self.cfg.setdefault("moq_override", {})

    def moq_of(self, path):
        """这个 SCHEDA 现在用的起订量（文件里读的 + 手动修改）；还没读出来返回 None"""
        m = self.moq.get(path)
        if m is None:
            return None
        return S.apply_override(m, self.moq_overrides().get(S.supplier_short_name(path)))

    def _moq_tip(self, i, p):
        t = self.ftable
        m = self.moq.get(p) or {}
        lines = [os.path.basename(p)]
        if m.get("company"):
            lines.insert(0, m["company"])
        if m.get("raw"):
            lines.append(tr("文件里写的起订量：{0}", m["raw"]))
        t.item(i, 1).setToolTip("\n".join(lines))
        cur = self.moq_of(p) or {}
        tip = tr("起订量：{0}", S.moq_label(cur, mark=False)) + "\n" + tr("点一下：起订量、这家供应商参与 BMS / 调货的门店")
        if cur.get("pallets") and not cur.get("per_pallet"):
            tip = tr("按板起订，还没填每板几箱（点起订量修改）") + "\n" + tip
        if cur.get("source") == "manual":
            tip = tr("已手动修改（文件里写的是：{0}）", m.get("raw") or tr("无")) + "\n" + tip
        elif cur.get("uncertain"):
            tip = tr("拿不准，请确认（文件里写的是：{0}）", m.get("raw") or tr("无")) + "\n" + tip
        if t.item(i, 2):
            t.item(i, 2).setToolTip(tip)

    def edit_moq(self, path):
        name = S.supplier_short_name(path)
        file_m = self.moq.get(path)
        if file_m is None:
            try:
                file_m = self.moq[path] = S.parse_moq(path)
            except Exception as e:
                QMessageBox.warning(self.win, tr("起订量"), tr("读不出这个 SCHEDA：{0}", e))
                return
        dlg = MoqDialog(self, name, path, file_m, self.moq_overrides().get(name))
        if not dlg.exec():
            return
        ov = dlg.result_override
        if ov is None:
            self.moq_overrides().pop(name, None)
        else:
            self.moq_overrides()[name] = ov
        scfg = self.cfg.setdefault("supplier_cfg", {})
        stores_changed = (scfg.get(name) or {}) != (dlg.result_supplier_cfg or {})
        if dlg.result_supplier_cfg:
            scfg[name] = dlg.result_supplier_cfg
        else:
            scfg.pop(name, None)
        save_settings(self.settings)
        if stores_changed and self.calc:
            self.apply_transfers()                   # 调货门店变了：重算调货
        msg = tr("{0} 的起订量已保存", name)
        if ov is not None and ov.get("config_id"):
            try:
                from fenpei.config import SUPPLIERS_CONFIG_FILE, AppConfig
                S.save_config_moq(SUPPLIERS_CONFIG_FILE, ov["config_id"], ov, name)
                AppConfig._suppliers_config = None          # 下次重新读
                msg += tr("，也已写进 suppliers_config.json")
            except Exception as e:
                QMessageBox.warning(self.win, tr("起订量"), tr("已保存到程序设置，但写 suppliers_config.json 失败：{0}", e))
        self.win.log("[门店配货] " + msg + "：" + S.moq_text(S.apply_override(file_m, ov)))
        # 列表、起订量检查马上按新的起订量刷新（不用重新计算）
        for i, p in self._frows():
            if S.supplier_short_name(p) == name and self.ftable.item(i, 2):
                self.ftable.blockSignals(True)
                self.ftable.item(i, 2).setText(S.moq_label(self.moq_of(p), short=True))
                self._moq_tip(i, p)
                self.ftable.blockSignals(False)
        if self.calc:
            for sc in self.calc["schede"]:
                if sc["name"] == name:
                    S.set_moq(sc, S.apply_override(sc.get("moq_file") or file_m, ov))
            self._refresh_matrix()
        self._update_state()
        self.win.show_toast(msg)

    def _gap_note(self, sc, st):
        parts = []
        if S.pallet_unknown(sc):
            return tr("按板起订，还没填每板几箱（点起订量修改）")
        if sc["moq_colli"] and st["colli"] < sc["moq_colli"]:
            parts.append(tr("差 {0:g} 箱", sc["moq_colli"] - st["colli"]))
        if sc["moq_amount"] and st["amount"] < sc["moq_amount"]:
            parts.append(tr("差 €{0:,.2f}", sc["moq_amount"] - st["amount"]))
        return "，".join(parts)

    def _results_refill(self, order):
        self._rrows = [self._rrows[i] for i in order]
        self._draw_results()

    def _draw_results(self):
        """配货结果表：self._rrows = [(行内容, 第几个供应商结果, 文件)]，按这个顺序画"""
        rows = [r[0] for r in self._rrows]
        self._res_index = [r[1] for r in self._rrows]
        self.row_files = [r[2] for r in self._rrows]
        t = self.rtable
        t.setRowCount(len(rows))
        kind = {"direct": "ok", "filled": "accent", "bms": "gray", "short": "err", "merged": "gray", "pulled": "gray", "dropped": "gray",
                "forced": "warn", "wait": "warn"}
        rev = {v: k for k, v in S.STORE_SHEETS.items()}
        for i, (day, sup, moq, dest, st, status, note) in enumerate(rows):
            label = tr(S.status_text(dest, status))
            _, sc_, plan_ = self.result["results"][self._res_index[i]]
            share, n_urg = S.plan_urgent(sc_, plan_, None if dest == S.BMS else rev.get(dest))
            self.ui.table_set_row(t, i, [day, sup, moq, dest, f"{st['colli']:g}", f"€{st['amount']:,.2f}", label,
                                         f"{share:.0%}" if n_urg else "", note], bold=dest == S.BMS)
            t.item(i, 2).setForeground(QColor(T("muted")))
            t.item(i, 2).setToolTip(S.moq_label(S.sc_moq(sc_), mark=False))
            if status in ("short", "dropped", "wait"):
                for c in (4, 5):
                    t.item(i, c).setForeground(QColor(T("faint")))
            k = kind.get(status, "ok")
            if dest == S.BMS and status in ("direct", "filled"):
                k = "accent"
            # 状态：直接写在格子里（带颜色），列宽按文字自动定，不会被挤掉
            si = t.item(i, 6)
            si.setForeground(QColor(T("muted") if k == "gray" else T(k)))
            if k != "gray":
                si.setBackground(QColor(T(k + "_soft")))
            f = si.font()
            f.setBold(True)
            si.setFont(f)
            ui_ = t.item(i, 7)
            ui_.setData(SORT_ROLE, float(share))
            if n_urg:
                ui_.setToolTip(tr("库存够卖不到一周的商品：{0} 个，占这张单金额的 {1:.0%}", n_urg, share))
                if share >= 0.5:
                    ui_.setForeground(QColor(T("err")))
                    ui_.setBackground(QColor(T("err_soft")))
                    f = ui_.font()
                    f.setBold(True)
                    ui_.setFont(f)
                elif share >= 0.25:
                    ui_.setForeground(QColor(T("warn")))
                    ui_.setBackground(QColor(T("warn_soft")))
                else:
                    ui_.setForeground(QColor(T("muted")))
            t.item(i, 8).setToolTip(note)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)

    def _open_row(self, row, _col):
        if 0 <= row < len(getattr(self, "row_files", [])):
            self._open(self.row_files[row])

    def _open(self, path):
        if path and os.path.exists(path):
            open_path(path, self.win)
