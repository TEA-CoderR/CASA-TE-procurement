"""
BMS 凑单核对窗口（订单跟踪 ⋯ →「BMS 凑单核对」）。规则在 bms_check.py。
两页：没标 CEN 的（订单文件说是 BMS / 几家店一起）/ 标了 CEN、日期空着的；勾选 →「标为 BMS 凑单」先确认、能撤销。
程序自己只认标了 CEN 的行；这里列出来的一定要人看过、点了才算。
"""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QAbstractItemView, QHeaderView, QTabWidget, QWidget, QMessageBox)

from i18n import tr
from theme import T
import order_tracking as OT
import bms_check as BC


def _d(d):
    return f"{d:%d/%m/%y}" if d else ""


def _why(t):
    def one(p):
        m = re.search(r"「([^」]*)」", p)          # 邮件主题原样放回去，只翻译外面的话
        if not m:
            return tr(p)
        return tr(p[:m.start()] + "「{0}」" + p[m.end():]).format(m.group(1))
    return "; ".join(one(p) for p in str(t or "").split("；") if p)


def _base(path):
    return str(path or "").replace("\\", "/").rsplit("/", 1)[-1]


class _Tab(QWidget):
    def __init__(self, kind, dialog):
        super().__init__()
        self.kind, self.dialog = kind, dialog
        self.items, self.checked = [], set()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 0)
        heads = [tr("门店"), tr("下单日期"), tr("供应商"), tr("金额"), tr("PIANO 位置"), tr("订单文件"), tr("文件里这家店"),
                 tr("依据")]
        if kind == "date":
            heads[1] = tr("改成日期")
        self.t = QTableWidget(0, len(heads))
        self.t.setHorizontalHeaderLabels(heads)
        self.t.verticalHeader().setVisible(False)
        self.t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.t.horizontalHeader()
        for c, w in enumerate([118, 84, 150, 92, 86, 250, 96, 300]):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.t.setColumnWidth(c, w)
        hh.setStretchLastSection(True)
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(self.t, lambda: len(self.items), self._text, self._fill, key=self._key,
                                   numeric=(3, 6))
        self.t.itemChanged.connect(self._changed)
        v.addWidget(self.t, 1)

    def set_items(self, items):
        self.items = sorted(items, key=lambda x: (str(x.get("file_date") or ""), x.get("file") or "",
                                                  str(x.get("store") or "")))
        self.checked = {i for i, x in enumerate(self.items) if x.get("sure", True)} if self.kind != "unsure" else set()
        self.ctl.render()

    def _text(self, i, c):
        x = self.items[i]
        if self.kind == "unsure":
            vals = ["", _d(x.get("file_date")), "", "", "", _base(x.get("file")), "", _why(x.get("why"))]
            return vals[c]
        amt = x.get("amount")
        vals = [OT.STORE_NAMES.get(str(x.get("store")), str(x.get("store") or "")),
                _d(x.get("new_date") if self.kind == "date" else x.get("date")), str(x.get("supplier") or ""),
                f"€{float(amt):,.2f}" if amt is not None else "", str(x.get("ref") or ""), _base(x.get("file")),
                f"€{float(x['file_amount']):,.2f}" if x.get("file_amount") is not None else "", _why(x.get("why"))]
        return vals[c]

    def _key(self, i, c):
        x = self.items[i]
        if c == 1:
            d = x.get("new_date") if self.kind == "date" else x.get("date")
            return d.isoformat() if d else ""
        if c == 3:
            return float(x.get("amount") or 0)
        if c == 6:
            return float(x.get("file_amount") or 0)
        return self._text(i, c).lower()

    def _fill(self, i, k):
        self.t.blockSignals(True)
        x = self.items[i]
        for c in range(self.t.columnCount()):
            it = QTableWidgetItem(self._text(i, c))
            if c == 0 and self.kind != "unsure":
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if i in self.checked else Qt.Unchecked)
                it.setData(Qt.UserRole, i)
            if c in (3, 6):
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if c == 7 and not x.get("sure", True):
                it.setForeground(QColor(T("warn")))
            if c == 5:
                it.setToolTip(str(x.get("file") or ""))
            self.t.setItem(k, c, it)
        self.t.blockSignals(False)

    def _changed(self, it):
        if it.column() != 0:
            return
        i = it.data(Qt.UserRole)
        if i is None:
            return
        (self.checked.add if it.checkState() == Qt.Checked else self.checked.discard)(i)
        self.dialog._update_buttons()

    def picked(self):
        return [self.items[i] for i in sorted(self.checked)]


class BmsCheckDialog(QDialog):
    def __init__(self, page):
        super().__init__(page.win)
        self.page, self.st = page, page.st
        self.setWindowTitle(tr("BMS 凑单核对"))
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        h = QLabel(tr("BMS 凑单核对") + "  ⓘ")
        h.setObjectName("Section")
        h.setToolTip(tr(
            "几家门店凑成的一张 BMS 单（送 BMS 仓库分货，nPDV 的也是）：程序里算一张单、一个状态，每家店那一份算进那家店的预算。\n"
            "程序自己只认 PIANO 里 Gg.cons. 写了 CEN 的行。这里列出「订单文件说是 BMS、但 PIANO 那一行没标 CEN」的：\n"
            "· 06 RECORD CENTRALE ACQUISTI 里文件名写 BMS 或 nPDV 的订单文件，这家店那一页的金额和 PIANO 一行一模一样"
            "（差 ≤ €{0}）、同一家供应商、下单日期前后 {1} 天；\n"
            "· 文件汇总页这家店写的是 DIRETTO 的，证据不一致，默认不勾（橙字）。\n"
            "勾选后「标为 BMS 凑单」：PIANO 那一行的 Gg.cons. 写上 CEN（浅蓝底），同事打开 Excel 也看得到；能撤销。",
            BC.AMOUNT_TOL, BC.DAYS_TOL))
        lay.addWidget(h)
        self.sub = QLabel("")
        self.sub.setObjectName("Faint")
        lay.addWidget(self.sub)
        self.tabs = QTabWidget()
        self.t_add, self.t_date, self.t_unsure = _Tab("add", self), _Tab("date", self), _Tab("unsure", self)
        self.tabs.addTab(self.t_add, tr("没标 CEN"))
        self.tabs.addTab(self.t_date, tr("CEN 行日期空着"))
        self.tabs.addTab(self.t_unsure, tr("看不出来的"))
        self.tabs.currentChanged.connect(lambda _i: self._update_buttons())
        lay.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        from erp_widgets import CountButton
        self.b_all = QPushButton(tr("全选"))
        self.b_all.clicked.connect(lambda: self._check_all(True))
        self.b_none = QPushButton(tr("全不选"))
        self.b_none.clicked.connect(lambda: self._check_all(False))
        self.b_scan = QPushButton(tr("重新核对"))
        self.b_scan.clicked.connect(self.run)
        row.addWidget(self.b_all)
        row.addWidget(self.b_none)
        row.addWidget(self.b_scan)
        row.addStretch()
        self.b_apply = CountButton(tr("标为 BMS 凑单"))
        self.b_apply.setObjectName("Primary")
        self.b_apply.clicked.connect(self.apply)
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.accept)
        row.addWidget(self.b_apply)
        row.addWidget(close)
        lay.addLayout(row)
        self.resize(1260, 660)
        from erp_widgets import fit_to_screen
        fit_to_screen(self)
        self._update_buttons()

    def run(self):
        from erp_widgets import BusyDialog
        msgs = []
        res, err = BusyDialog.run(self, tr("BMS 凑单核对"), tr("正在读 06 RECORD CENTRALE ACQUISTI 的订单文件…"),
                                  lambda: BC.scan(self.page.settings, self.st, log=msgs.append))
        for m in msgs:
            getattr(self.page.win, "log", print)(m)
        if err is not None:
            QMessageBox.warning(self, tr("BMS 凑单核对"), tr("核对不了：{0}", err))
            return
        self.t_add.set_items(res["add"])
        self.t_date.set_items(res["date"])
        self.t_unsure.set_items(res["unsure"])
        for i, (t, name) in enumerate(((self.t_add, tr("没标 CEN")), (self.t_date, tr("CEN 行日期空着")),
                                        (self.t_unsure, tr("看不出来的")))):
            self.tabs.setTabText(i, f"{name}  {len(t.items)}")
        ng = len([g for g in OT.groups(self.st).values() if g["members"]])
        self.sub.setText(tr("PIANO 里标了 CEN 的 BMS 凑单 {0} 张；订单文件说是 BMS、没标 CEN 的 {1} 行", ng, len(res["add"])))
        self._update_buttons()

    def _tab(self):
        return self.tabs.currentWidget()

    def _check_all(self, on):
        t = self._tab()
        if t.kind == "unsure":
            return
        t.checked = set(range(len(t.items))) if on else set()
        t.ctl.render()
        self._update_buttons()

    def _update_buttons(self):
        t = self._tab()
        n = len(t.checked) if t and t.kind != "unsure" else 0
        for b in (self.b_all, self.b_none, self.b_apply):
            b.setVisible(bool(t) and t.kind != "unsure")
        txt = tr("填上日期") if t and t.kind == "date" else tr("标为 BMS 凑单")
        self.b_apply.setText(txt)
        self.b_apply.setMinimumWidth(self.b_apply.fontMetrics().horizontalAdvance(txt) + 96)   # 字 + 数字徽章
        self.b_apply.setEnabled(n > 0)
        self.b_apply.set_count(n or None)

    def apply(self):
        t = self._tab()
        items = t.picked()
        if not items:
            return
        lines = [f"· {OT.STORE_NAMES.get(str(x['store']), x['store'])} · {x.get('supplier')} · "
                 f"{_d(x.get('new_date') if t.kind == 'date' else x.get('date'))} · €{float(x.get('amount') or 0):,.2f}"
                 f"  ({x.get('ref')})" for x in items[:12]]
        if len(items) > 12:
            lines.append(tr("… 还有 {0} 行", len(items) - 12))
        if t.kind == "date":
            head = tr("给这 {0} 行 CEN 填上下单日期（照订单文件的日期）？", len(items))
            tail = tr("PIANO 那一行的下单日期会改成订单文件的日期（浅蓝底），这样才能和同一张单别的店合在一起。同事都能看到；能撤销。")
        else:
            head = tr("这 {0} 行标为 BMS 凑单（每家店的那一份）？", len(items))
            tail = tr("PIANO 那一行的 Gg.cons. 写上 CEN（浅蓝底）；程序里同一家供应商、同一天的几家店合成一张 BMS 单、一个状态，"
                      "金额照旧算进每家店的预算。同事都能看到；能撤销。")
        if QMessageBox.question(self, self.b_apply.text(), head + "\n\n" + "\n".join(lines) + "\n\n" + tail) \
                != QMessageBox.Yes:
            return
        if t.kind == "date":
            before = BC.apply(self.st, dates={x["id"]: x["new_date"] for x in items})
        else:
            before = BC.apply(self.st, add_ids=[x["id"] for x in items])
        self.page.refresh()
        self.run()

        def back():
            BC.undo(self.st, before)
            self.page.refresh()
            self.page.win.show_toast(tr("已撤销"))
        self.page.win.show_toast(tr("✓ 已改 {0} 行，下次同步写进 PIANO", len(before)), 10000, action=(tr("撤销"), back))


def open_dialog(page):
    if page.st is None:
        return
    dlg = BmsCheckDialog(page)
    dlg.show()
    dlg.run()
    dlg.exec()
