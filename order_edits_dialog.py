"""
「订单文件和程序对不上」窗口（2026-11-22 用户：订单文件里手改了箱数，程序要同步回来）。
列出每一行：人手改的（同步 = 照订单文件写进程序）/ 以前比价留下的（同步 = 清掉，发出去会重复订货）。
按钮：同步进程序（先确认）· 不同步 · 取消。规则在 order_edits.py（不依赖界面）。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QHeaderView, QMessageBox)

from i18n import tr
from theme import T

KIND_TEXT = {"manual": "人手改的", "stale": "以前留下的"}
KIND_TIP = {"manual": "订单文件被人手改过，和程序算的不一样。同步 = 照订单文件写进程序（主订单、工作副本、比价结果、订单跟踪）",
            "stale": "程序这次没分给这家，是以前几次比价留在供应商文件里的数量；订单文件里有，发出去会重复订货。同步 = 清掉"}


def _eur(v):
    return f"€{v:,.2f}" if v is not None else "—"


class OrderEditsDialog(QDialog):
    """result：'sync' 同步进程序 / 'skip' 不同步（接着做原来要做的）/ None 取消"""

    def __init__(self, parent, edits, store="", action=None, settings=None, save=None):
        super().__init__(parent)
        self.setObjectName("Dialog")
        self.setWindowTitle(tr("订单文件和程序对不上"))
        self.result_kind = None
        self.edits = list(edits)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        n_man = sum(1 for e in self.edits if e.get("kind") == "manual")
        n_old = len(self.edits) - n_man
        head = QLabel(tr("{0} · {1} 行箱数和程序不一样", store, len(self.edits)) + "  ⓘ")
        head.setObjectName("Section")
        head.setToolTip(tr("订单文件 = 比价结果文件夹 Ordini 里发给供应商的那份。\n人手改的：{0} 行；以前比价留下的：{1} 行。\n"
                           "同步以后订单文件按程序重新生成（箱数和同步后的一样），之后修改分配、换方案不会再盖掉。"
                           "写之前自动备份。", n_man, n_old))
        v.addWidget(head)
        heads = [tr("类型"), tr("供应商"), tr("商品"), tr("条码"), tr("程序里（箱）"), tr("订单文件里（箱）"), tr("金额变化")]
        t = QTableWidget(0, len(heads))
        t.setHorizontalHeaderLabels(heads)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        rows = []
        for e in self.edits:
            d = None
            if e.get("price"):
                d = (float(e["new"]) - float(e["old"])) * float(e.get("pack") or 1) * float(e["price"])
            rows.append([tr(KIND_TEXT.get(e.get("kind"), "")), e["supplier"], e["desc"], e["ean"],
                         float(e["old"]), float(e["new"]), d])
        self._rows = rows

        def text(i, c):
            val = rows[i][c]
            if c == 6:
                return ("+" if (val or 0) > 0 else "") + _eur(val) if val is not None else "—"
            return f"{val:g}" if isinstance(val, float) else str(val or "")

        def fill(i, k):
            if t.rowCount() <= k:
                t.setRowCount(k + 1)
            kind = self.edits[i].get("kind")
            for c in range(len(heads)):
                it = QTableWidgetItem(text(i, c))
                if c >= 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 0:
                    it.setForeground(QColor(T("accent") if kind == "manual" else T("err")))
                    it.setToolTip(tr(KIND_TIP.get(kind, "")))
                t.setItem(k, c, it)

        from erp_widgets import TableSortFilter, fit_to_screen
        self.ctl = TableSortFilter(t, lambda: len(rows), text, fill, key=lambda i, c: (rows[i][c] if rows[i][c] is not None else 0),
                                   numeric=(4, 5, 6))
        if settings is not None and save is not None:
            self.ctl.remember(settings.setdefault("order_edits_cols", {}), "edits", save)
        self.ctl.render()
        for c, w in enumerate((96, 110, 300, 120, 96, 110, 96)):
            t.setColumnWidth(c, w)
        v.addWidget(t, 1)
        tot = sum(r[6] or 0 for r in rows)
        sm = QLabel(tr("订单文件比程序多 {0}", _eur(tot)) if tot >= 0 else tr("订单文件比程序少 {0}", _eur(-tot)))
        sm.setObjectName("Muted")
        bar = QHBoxLayout()
        bar.addWidget(sm)
        bar.addStretch()
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        bar.addWidget(cancel)
        if action:
            skip = QPushButton(tr("不同步，{0}", action))
            skip.setToolTip(tr("程序里照旧，订单文件里的改动会被重新生成的订单文件盖掉"))
            skip.clicked.connect(lambda: self._done("skip"))
            bar.addWidget(skip)
        ok = QPushButton(tr("同步进程序"))
        ok.setObjectName("Primary")
        ok.setToolTip(tr("人手改的照订单文件写进程序，以前留下的清掉"))
        ok.clicked.connect(self._sync)
        bar.addWidget(ok)
        v.addLayout(bar)
        self.resize(1040, 520)
        fit_to_screen(self)

    def _done(self, kind):
        self.result_kind = kind
        self.accept()

    def _sync(self):
        n_man = sum(1 for e in self.edits if e.get("kind") == "manual")
        n_old = len(self.edits) - n_man
        files = sorted({e["supplier"] for e in self.edits})
        msg = tr("要同步 {0} 行：\n· 人手改的 {1} 行：照订单文件写进主订单和比价结果\n· 以前留下的 {2} 行：从供应商文件里清掉\n\n"
                 "会重新生成这几家的订单文件：{3}。订单跟踪的箱数、金额跟着变（同事也看得到）。写之前自动备份。",
                 len(self.edits), n_man, n_old, "、".join(files))
        if n_old:
            msg += tr("\n\n注意：已经发给供应商的订单里如果有「以前留下的」，要另外通知供应商取消。")
        if QMessageBox.question(self, tr("同步进程序"), msg) == QMessageBox.Yes:
            self._done("sync")


def ask(parent, edits, store="", action=None, settings=None, save=None):
    d = OrderEditsDialog(parent, edits, store, action, settings, save)
    d.exec()
    return d.result_kind
