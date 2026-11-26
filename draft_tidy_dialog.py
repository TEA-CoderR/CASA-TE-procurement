"""
旧的待登记作废前给人看一眼（2026-11-26 用户：「一定要谨慎再谨慎」）。
第一次（这台电脑还没确认过规则）把要作废的单全部列出来：供应商 · 门店 · 生成日期 · 金额 · 为什么；
用户点「作废这 n 张」才作废，之后每天自动作废、右下角提示带「撤销」。规则在 order_tracking.stale_drafts。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QAbstractItemView, QHeaderView)

from i18n import tr
from theme import T
import order_tracking as OT


def why_text(x):
    if x["why"] == "superseded":
        return tr("后来 {0} 又生成了这家的订单", _dm(x.get("by")))
    return tr("生成超过 {0} 天没发出", OT.DRAFT_KEEP_DAYS)


def _dm(iso):
    d = OT.to_date(iso)
    return f"{d:%d/%m}" if d else ""


def void_tip(o):
    """已作废的单为什么作废（鼠标提示）"""
    v = o.get("void") or {}
    return why_text({"why": v.get("why"), "by": v.get("by")}) + tr("，一直没发出，已作废") if v else ""


def summary_text(items):
    sup = sum(1 for x in items if x["why"] == "superseded")
    return tr("{0} 张后来又生成过（被新的配货取代）· {1} 张超过 {2} 天没发出", sup, len(items) - sup, OT.DRAFT_KEEP_DAYS)


class DraftTidyDialog(QDialog):
    COLS = [(tr("供应商"), 190), (tr("门店"), 150), (tr("生成日期"), 80), (tr("金额"), 90), (tr("为什么作废"), 230)]

    def __init__(self, parent, st, items):
        super().__init__(parent)
        self.setWindowTitle(tr("作废旧的待登记"))
        self.st, self.items = st, list(items)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(8)
        head = QLabel(tr("{0} 张待登记一直没发出，可以作废", len(self.items)) + "  ⓘ")
        head.setObjectName("Section")
        head.setToolTip(tr(
            "程序每次算总仓采购 / 门店采购 / DETERSIVO 都会生成一批「待登记」，以前没发出去的旧单一直留着，看板上越堆越多。\n"
            "作废的只是：程序自动生成、没人改过、没发过邮件、没关联过邮件的待登记。\n"
            "· 后来又生成过同一家供应商（DETERSIVO：同一家门店）的订单 → 旧的被新的配货取代；\n"
            "· 生成超过 {0} 天还没发出 → 库存和销量早就变了，要发也该重新算。\n"
            "作废不是删除：「看哪些 → 已作废的待登记」看得到，选中点「恢复成待登记」就回来；\n"
            "用邮件把这张单发出去了，邮件核对也会自动把它恢复。", OT.DRAFT_KEEP_DAYS))
        lay.addWidget(head)
        sub = QLabel(summary_text(self.items))
        sub.setObjectName("Faint")
        lay.addWidget(sub)
        t = QTableWidget(0, len(self.COLS))
        t.setHorizontalHeaderLabels([c for c, _w in self.COLS])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = t.horizontalHeader()
        for c, (_h, w) in enumerate(self.COLS):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            t.setColumnWidth(c, w)
        hh.setStretchLastSection(True)
        self.t = t
        self._rows = []
        for x in sorted(self.items, key=lambda x: (x["why"] != "superseded", x["day"])):
            o = st.orders.get(x["id"]) or {}
            self._rows.append([str(o.get("supplier") or ""), OT.store_label(o), _dm(x["day"]),
                               f"€{float(o.get('amount') or 0):,.2f}", why_text(x), x["day"]])
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(t, lambda: len(self._rows), lambda i, c: self._rows[i][c], self._fill,
                                   key=self._key, numeric=(3,))
        self.ctl.render()
        lay.addWidget(t, 1)
        row = QHBoxLayout()
        row.addStretch()
        no = QPushButton(tr("暂不处理"))
        no.clicked.connect(self.reject)
        ok = QPushButton(tr("作废这 {0} 张", len(self.items)))
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        row.addWidget(no)
        row.addWidget(ok)
        lay.addLayout(row)
        self.resize(820, 560)
        try:
            from erp_widgets import fit_to_screen
            fit_to_screen(self)
        except Exception:
            pass

    def _fill(self, i, k):
        for c, v in enumerate(self._rows[i]):
            it = QTableWidgetItem(v)
            if c == 3:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if c == 4:
                it.setForeground(QColor(T("muted")))
            self.t.setItem(k, c, it)

    def _key(self, i, c):
        v = self._rows[i][c]
        if c == 3:
            try:
                return float(v.replace("€", "").replace(",", ""))
            except ValueError:
                return 0.0
        if c == 2:
            return self._rows[i][5]
        return v.lower()
