"""
历史未结订单核对窗口（订单跟踪 ⋯ →「历史未结订单核对」）。规则在 old_orders.py。
三页：查得到入库 / 查不到入库 / 供应商对不上；勾选 → 登记到货（按入库记录补登）/ 取消订单 / 继续跟踪，都先确认、能撤销。
"""
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QAbstractItemView, QHeaderView, QTabWidget, QWidget, QDateEdit, QMessageBox)

from i18n import tr
from theme import T
import order_tracking as OT
import old_orders as OO


def _d(d):
    return f"{d:%d/%m/%y}" if d else ""


class _Tab(QWidget):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.items = []                    # [(行, 入库 或 None)]
        self.checked = set()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 0)
        heads = [tr("下单日期"), tr("门店"), tr("供应商"), tr("金额"), tr("现在状态")]
        if kind == "found":
            heads += [tr("入库日期"), tr("入库"), tr("数据库供应商")]
        self.t = QTableWidget(0, len(heads))
        self.t.setHorizontalHeaderLabels(heads)
        self.t.verticalHeader().setVisible(False)
        self.t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.t.horizontalHeader()
        for c, w in enumerate([112, 140, 180, 100, 80, 84, 190, 190][:len(heads)]):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.t.setColumnWidth(c, w)
        hh.setStretchLastSection(True)
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(self.t, lambda: len(self.items), self._text, self._fill, key=self._key, numeric=(3,))
        self.t.itemChanged.connect(self._changed)
        v.addWidget(self.t, 1)

    def set_items(self, items):
        self.items = list(items)
        self.checked = set(range(len(self.items))) if self.kind == "found" else set()
        self.ctl.render()

    def _text(self, i, c):
        r, x = self.items[i]
        vals = [_d(r["date"]), r["store_name"], r["supplier"], f"€{float(r['amount'] or 0):,.2f}",
                {"ordered": tr("已下单"), "confirmed": tr("已确认"), "shipped": tr("已发货"), "partial": tr("部分到货"),
                 "stuck": tr("卡住了")}.get(r["status"], r["status"])]
        if self.kind == "found":
            vals += [_d(x["day"]), tr("{0} 行 · {1:g} 件 · €{2:,.2f}", x["n"], x["q"], x["v"]), x.get("sup_name") or ""]
        return vals[c]

    def _key(self, i, c):
        r, x = self.items[i]
        if c == 0:
            return r["date"].isoformat()
        if c == 3:
            return float(r["amount"] or 0)
        if c == 5 and x:
            return x["day"].isoformat()
        return self._text(i, c).lower()

    def _fill(self, i, k):
        self.t.blockSignals(True)
        for c in range(self.t.columnCount()):
            it = QTableWidgetItem(self._text(i, c))
            if c == 0:
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if i in self.checked else Qt.Unchecked)
                it.setData(Qt.UserRole, i)
            if c == 3:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if c == 4 and self.items[i][0]["status"] == "stuck":
                it.setForeground(QColor(T("err")))
            self.t.setItem(k, c, it)
        self.t.blockSignals(False)

    def _changed(self, it):
        if it.column() != 0:
            return
        i = it.data(Qt.UserRole)
        if i is None:
            return
        (self.checked.add if it.checkState() == Qt.Checked else self.checked.discard)(i)
        self.parent_dialog._update_buttons()

    def picked(self):
        return [self.items[i] for i in sorted(self.checked)]


class OldOrdersDialog(QDialog):
    def __init__(self, page):
        super().__init__(page.win)
        self.page, self.st = page, page.st
        self.setWindowTitle(tr("历史未结订单核对"))
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        top = QHBoxLayout()
        h = QLabel(tr("历史未结订单核对") + "  ⓘ")
        h.setObjectName("Section")
        h.setToolTip(tr(
            "以前下的单还开着（已下单 / 已确认 / 卡住了），多半是货早到了、没人在程序里登记收货。\n"
            "按数据库入库记录查：同一家供应商、同一家门店（BMS = 仓库），下单以后 {0} 天内有供应商送货（CA）就算查得到；"
            "一次送货只算给一张单。\n"
            "· 查得到入库 → 勾选后「登记到货」：收货记录写「{1}」和入库日期 / 件数，不写卸货人；\n"
            "· 查不到 → 自己决定「取消订单」或「继续跟踪」（继续跟踪的以后不再列出来）。\n"
            "这里只清以前的旧账；以后的单照旧由门店点「确认收货」、选卸货人。", OO.MATCH_DAYS, OO.BY))
        top.addWidget(h)
        top.addStretch()
        top.addWidget(QLabel(tr("下单日期早于")))
        self.e_before = QDateEdit()
        self.e_before.setCalendarPopup(True)
        self.e_before.setDisplayFormat("dd/MM/yyyy")
        b0 = OO.default_before()
        self.e_before.setDate(QDate(b0.year, b0.month, b0.day))
        top.addWidget(self.e_before)
        q = QPushButton(tr("查询"))
        q.clicked.connect(self.run)
        top.addWidget(q)
        lay.addLayout(top)
        self.sub = QLabel("")
        self.sub.setObjectName("Faint")
        lay.addWidget(self.sub)
        self.tabs = QTabWidget()
        self.t_found, self.t_none, self.t_nosup = _Tab("found"), _Tab("none"), _Tab("nosup")
        for t in (self.t_found, self.t_none, self.t_nosup):
            t.parent_dialog = self
        self.tabs.addTab(self.t_found, tr("查得到入库"))
        self.tabs.addTab(self.t_none, tr("查不到入库"))
        self.tabs.addTab(self.t_nosup, tr("数据库里找不到这家供应商"))
        self.tabs.currentChanged.connect(lambda _i: self._update_buttons())
        lay.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        from erp_widgets import CountButton
        self.b_all = QPushButton(tr("全选"))
        self.b_all.clicked.connect(lambda: self._check_all(True))
        self.b_none = QPushButton(tr("全不选"))
        self.b_none.clicked.connect(lambda: self._check_all(False))
        row.addWidget(self.b_all)
        row.addWidget(self.b_none)
        row.addStretch()
        self.b_keep = CountButton(tr("继续跟踪"))
        self.b_keep.setToolTip(tr("这几张确实还没到、还要跟：留着，以后不再列在这里"))
        self.b_keep.clicked.connect(self.keep)
        self.b_cancel = CountButton(tr("取消订单"))
        self.b_cancel.setToolTip(tr("这几张不会来了：取消，不再算在途、不算花费（能撤销）"))
        self.b_cancel.clicked.connect(self.cancel)
        self.b_arr = CountButton(tr("登记到货"))
        self.b_arr.setObjectName("Primary")
        self.b_arr.setToolTip(tr("按数据库入库记录登记到货：到货日期 = 入库那天，收货记录写明是按入库记录补登的（能撤销）"))
        self.b_arr.clicked.connect(self.arrived)
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.accept)
        for b in (self.b_keep, self.b_cancel, self.b_arr, close):
            row.addWidget(b)
        lay.addLayout(row)
        self.resize(1180, 640)
        from erp_widgets import fit_to_screen
        fit_to_screen(self)
        self._update_buttons()

    def _before(self):
        d = self.e_before.date()
        from datetime import date
        return date(d.year(), d.month(), d.day())

    def run(self):
        from erp_widgets import BusyDialog
        import reorder_service as R
        before = self._before()
        S = self.page.settings

        msgs = []

        def work():                       # 后台线程：日志先收起来，回到界面线程再写
            db = R.make_db(S.get("db") or None)
            return OO.check(self.st, db, before, log=msgs.append)
        res, err = BusyDialog.run(self, tr("历史未结订单核对"), tr("正在查数据库入库记录…"), work)
        for m in msgs:
            getattr(self.page.win, "log", print)(m)
        if err is not None:
            QMessageBox.warning(self, tr("历史未结订单核对"), tr("查不了数据库：{0}", err))
            return
        self.t_found.set_items(res["found"])
        self.t_none.set_items([(r, None) for r in res["none"]])
        self.t_nosup.set_items([(r, None) for r in res["no_supplier"]])
        for i, (t, name) in enumerate(((self.t_found, tr("查得到入库")), (self.t_none, tr("查不到入库")),
                                        (self.t_nosup, tr("数据库里找不到这家供应商")))):
            self.tabs.setTabText(i, f"{name}  {len(t.items)}")
        n = len(res["found"]) + len(res["none"]) + len(res["no_supplier"])
        self.sub.setText(tr("{0} 以前下的、还开着的单 {1} 张", _d(before), n) if n else
                         tr("{0} 以前下的单都处理完了", _d(before)))
        self._update_buttons()

    def _tab(self):
        return self.tabs.currentWidget()

    def _check_all(self, on):
        t = self._tab()
        t.checked = set(range(len(t.items))) if on else set()
        t.ctl.render()
        self._update_buttons()

    def _update_buttons(self):
        t = self._tab()
        n = len(t.checked) if t else 0
        found = t is self.t_found
        self.b_arr.setVisible(found)
        self.b_keep.setVisible(not found)
        self.b_cancel.setVisible(not found)
        for b in (self.b_arr, self.b_keep, self.b_cancel):
            b.setEnabled(n > 0)
            b.set_count(n or None)

    def _list(self, items, limit=10):
        out = [f"· {_d(r['date'])} · {r['store_name']} · {r['supplier']} · €{float(r['amount'] or 0):,.2f}"
               + (f" → {tr('入库')} {_d(x['day'])}" if x else "") for r, x in items[:limit]]
        if len(items) > limit:
            out.append(tr("… 还有 {0} 张", len(items) - limit))
        return "\n".join(out)

    def _done(self, text, undo):
        self.page.refresh()
        self.run()

        def back():
            self.st.set_many(undo)
            self.page.refresh()
            self.page.win.show_toast(tr("已撤销"))
        self.page.win.show_toast(text, 10000, action=(tr("撤销"), back))

    def arrived(self):
        items = self.t_found.picked()
        if not items:
            return
        txt = tr("按数据库入库记录登记这 {0} 张单到货？", len(items)) + "\n\n" + self._list(items) + "\n\n" + \
            tr("到货日期 = 入库那天，收货记录写「{0}」（没有卸货人）。改了写进共享的订单记录，同事都能看到；能撤销。", OO.BY)
        if QMessageBox.question(self, tr("登记到货"), txt) != QMessageBox.Yes:
            return
        import socket
        undo = OO.apply_arrived(self.st, items, pc=socket.gethostname())
        self._done(tr("✓ {0} 张已按入库记录登记到货", len(undo)), undo)

    def cancel(self):
        items = self._tab().picked()
        if not items:
            return
        txt = tr("取消这 {0} 张单？", len(items)) + "\n\n" + self._list(items) + "\n\n" + \
            tr("取消后不再算在途、不算花费，备注写「历史单核对：数据库查不到入库，取消」。同事都能看到；能撤销。")
        if QMessageBox.question(self, tr("取消订单"), txt) != QMessageBox.Yes:
            return
        undo = OO.cancel(self.st, [r["id"] for r, _x in items])
        self._done(tr("✓ 已取消 {0} 张单", len(undo)), undo)

    def keep(self):
        items = self._tab().picked()
        if not items:
            return
        undo = OO.keep_tracking(self.st, [r["id"] for r, _x in items])
        self._done(tr("✓ {0} 张继续跟踪，以后不再列在这里", len(undo)), undo)


def open_dialog(page):
    if page.st is None:
        return
    dlg = OldOrdersDialog(page)
    dlg.show()
    dlg.run()
    dlg.exec()
