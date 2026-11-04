"""
在途箱数那一格双击 →「在途订单」窗口（订货明细 / 检查门店清单共用）。

2026-11-04 用户：操作都要摆出来、名字要专业、改状态要先确认、打开 / 读取文件要有进度提示。
  上面一排操作：确认收货 · 部分收货… · 不计入在途 | 查看订单明细 · 打开订单文件（按选中的订单启用 / 灰掉，鼠标提示说明）
  改订单状态前弹确认（写明改哪几张、改完会怎样、所有同事可见）；改完底部状态栏写结果 +「撤销」
  读订单文件、重算在途、打开 Excel 都在 BusyDialog 里做
改完写进采购订单跟踪 → 重算在途 → on_change(新的在途)：订货明细 StoreOrderPage.reapply_transit / 检查清单 OrderCheck.reapply_transit
"""
import os
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QHeaderView, QMenu, QFrame,
                               QAbstractItemView, QInputDialog, QMessageBox)

from i18n import tr
import order_record as REC
import order_tracking as OT

COLS = [(0, "下单日期", 96), (1, "门店", 96), (2, "供应商", 150), (3, "本品箱数", 76), (4, "预计到货", 96),
        (5, "订单状态", 88), (6, "订单文件", 250)]
_FIELDS = ("arrived", "state", "note", "record")


def tracking_store(settings):
    folder = REC.tracking_folder(settings)
    if not folder:
        return None
    cfg = (settings or {}).get("tracking") or {}
    me = (cfg.get("me") or (settings or {}).get("user_name") or OT.default_me()).strip()
    return OT.Store(folder, me=me).reload()


def _d(v):
    return v.strftime("%d/%m/%Y") if v else ""


class TransitOrdersDialog(QDialog):
    def __init__(self, win, settings, ui, title, tinfo, on_change=None, tracking=None):
        super().__init__(win)
        self.win, self.settings, self.ui, self.on_change = win, settings, ui, on_change
        self.tracking = tracking                 # 采购订单跟踪页面（看明细用它的窗口；改完让它重读）
        self.tinfo = tinfo or {}
        self.changed = False
        self._undo = None                      # (说明, [(单号, 改之前的字段)])
        self.setWindowTitle(tr("在途订单"))
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel(title + "  ⓘ")
        t.setObjectName("Section")
        t.setToolTip(tr("这个商品已下单、尚未收货的采购订单（来自采购订单跟踪）。订货箱数 = 建议 − 在途；"
                        "订单实际已收货但未登记的，在这里确认收货，订货箱数会立即重新计算"))
        head.addWidget(t)
        head.addStretch()
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        head.addWidget(self.summary)
        v.addLayout(head)

        # ---- 操作栏：改状态（左）| 查看（右）----
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.b_recv = QPushButton(tr("✓ 确认收货"))
        self.b_recv.setObjectName("Primary")
        self.b_recv.setToolTip(tr("选中的订单登记为今天已收货，不再计入在途（会先让你确认）"))
        self.b_part = QPushButton(tr("部分收货…"))
        self.b_part.setToolTip(tr("只到了一部分：登记部分收货并填写缺货说明；订单仍计入在途"))
        self.b_excl = QPushButton(tr("不计入在途"))
        self.b_excl.setToolTip(tr("这张订单没有可用的订单明细（例如电话下单），不参与在途计算；订单状态不变"))
        self.b_view = QPushButton(tr("查看订单明细"))
        self.b_view.setToolTip(tr("这张订单订了哪些商品、各几箱（读取订单文件）"))
        self.b_open = QPushButton(tr("打开订单文件"))
        self.b_open.setToolTip(tr("用 Excel 打开这张订单的原始文件"))
        for b in (self.b_recv, self.b_part, self.b_excl):
            bar.addWidget(b)
        sep = QFrame()                          # 改状态 | 查看 之间一条细竖线
        sep.setFixedSize(1, 22)
        sep.setStyleSheet(f"background: {ui.T('line')};")
        bar.addSpacing(6)
        bar.addWidget(sep)
        bar.addSpacing(6)
        bar.addWidget(self.b_view)
        bar.addWidget(self.b_open)
        bar.addStretch()
        v.addLayout(bar)
        self.b_recv.clicked.connect(lambda: self.receive(self._selected()))
        self.b_part.clicked.connect(lambda: self.receive_partial(self._selected()))
        self.b_excl.clicked.connect(lambda: self.exclude(self._selected()))
        self.b_view.clicked.connect(lambda: self.view_lines(self._one()))
        self.b_open.clicked.connect(lambda: self.open_file(self._one()))

        # ---- 订单表 ----
        self.t = ui.make_table([tr(h) for _f, h, _w in COLS], numeric_cols=(3,))
        self.t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, (_f, _h, wd) in enumerate(COLS):
            self.t.setColumnWidth(c, wd)
        from erp_widgets import TableSortFilter, fit_to_screen
        import settings as ST
        self.rows = []
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, key=self._key, numeric=(3,))
        self.ctl.remember(settings.setdefault("tracking", {}), "transit_dlg2", lambda: ST.save_settings(settings),
                          [f for f, _h, _w in COLS])
        self.t.setContextMenuPolicy(Qt.CustomContextMenu)
        self.t.customContextMenuRequested.connect(self._menu)
        self.t.cellDoubleClicked.connect(lambda k, _c: self.view_lines(self._row_at(k)))
        self.t.itemSelectionChanged.connect(self._update_buttons)
        v.addWidget(self.t, 1)

        # ---- 底部：上一步做了什么 + 撤销 | 关闭 ----
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        foot.addWidget(self.status, 1)
        self.b_undo = QPushButton(tr("撤销"))
        self.b_undo.setObjectName("Link")
        self.b_undo.setToolTip(tr("把刚才的修改改回去（在途和订货箱数也会重新计算）"))
        self.b_undo.clicked.connect(self.undo)
        self.b_undo.hide()
        foot.addWidget(self.b_undo)
        b_close = QPushButton(tr("关闭"))
        b_close.clicked.connect(self.accept)
        foot.addWidget(b_close)
        v.addLayout(foot)
        self.resize(900, 360)
        fit_to_screen(self)
        self._load()
        if self.rows:
            self.t.selectRow(0)
        self._update_buttons()

    # ================================================================ 数据
    def _load(self):
        self.st = None
        try:
            self.st = tracking_store(self.settings)
        except Exception as e:
            self.status.setText(tr("⚠ 采购订单跟踪读不了：{0}", e))
        ids = REC.order_ids(self.tinfo)
        colli = {}
        for o in self.tinfo.get("orders") or []:
            if len(o) > 3:
                colli[o[3]] = colli.get(o[3], 0) + o[2]
        rows = {r["id"]: r for r in OT.rows(self.st)} if self.st is not None else {}
        info_all = ((REC._MEMO.get("res") or {}).get("orders") or {})
        self.rows = [dict(rows[i], _colli=colli.get(i, 0), _info=info_all.get(i) or {}) for i in ids if i in rows]
        self.ctl.render()
        n_open = sum(1 for r in self.rows if r["status"] in REC.OPEN_STATES)
        c_open = sum(r["_colli"] for r in self.rows if r["status"] in REC.OPEN_STATES)
        self.summary.setText(tr("在途 {0:g} 箱 · {1} 张订单", c_open, n_open))

    def _text(self, i, c):
        r = self.rows[i]
        f = COLS[c][0]
        if f == 0:
            return _d(r["date"])
        if f == 1:
            return r["store_name"]
        if f == 2:
            return r["supplier"]
        if f == 3:
            return f"{r['_colli']:g}"
        if f == 4:
            return _d(r["expected"]) if r["status"] in REC.OPEN_STATES else ""
        if f == 5:
            from tracking_page import STATUS_LABELS
            s = STATUS_LABELS().get(r["status"], r["status"])
            if r["status"] == "arrived" and r["arrived"]:
                s += " " + r["arrived"].strftime("%d/%m")
            return s
        if f == 6:
            if r.get("record") == REC.NO_FILE:
                return tr("不计入在途")
            return os.path.basename(r["_info"].get("file") or "")
        return ""

    def _key(self, i, c):
        r = self.rows[i]
        f = COLS[c][0]
        if f in (0, 4):
            d = r["date"] if f == 0 else r["expected"]
            return d.toordinal() if d else 0
        if f == 3:
            return r["_colli"]
        return None

    def _fill(self, i, k):
        r = self.rows[i]
        T = self.ui.T
        self.ui.table_set_row(self.t, k, [self._text(i, c) for c in range(len(COLS))])
        done = r["status"] not in REC.OPEN_STATES or r.get("record") == REC.NO_FILE
        for c in range(len(COLS)):
            it = self.t.item(k, c)
            if it is not None and done:
                it.setForeground(QColor(T("faint")))
        st = self.t.item(k, 5)
        if st is not None and not done:
            if r["overdue"]:
                st.setForeground(QColor(T("err")))
                st.setToolTip(tr("已超过预计到货 {0} 天", r["late_by"]))
            elif r["status"] == "partial":
                st.setForeground(QColor(T("warn")))
        ex = self.t.item(k, 4)
        if ex is not None and r["overdue"] and not done:
            ex.setForeground(QColor(T("err")))
        fi = self.t.item(k, 6)
        if fi is not None and r["_info"].get("file"):
            fi.setToolTip(r["_info"]["file"])

    def _row_at(self, k):
        return self.ctl.row_at(k)

    def _selected(self):
        ks = sorted({ix.row() for ix in self.t.selectionModel().selectedRows()})
        return [self.rows[i] for i in (self.ctl.row_at(k) for k in ks) if i is not None]

    def _one(self):
        sel = self._selected()
        return self.rows.index(sel[0]) if len(sel) == 1 else None

    def _update_buttons(self):
        sel = self._selected()
        ok = self.st is not None and bool(sel)
        open_ = [r for r in sel if r["status"] in REC.OPEN_STATES]
        self.b_recv.setEnabled(ok and bool(open_))
        self.b_part.setEnabled(ok and bool(open_))
        self.b_excl.setEnabled(ok and any(r.get("record") != REC.NO_FILE for r in sel))
        one = sel[0] if len(sel) == 1 else None
        self.b_view.setEnabled(bool(one and one["_info"].get("file")))
        self.b_open.setEnabled(bool(one and one["_info"].get("path")))

    def _menu(self, pos):
        """右键：和上面那排按钮一样（习惯右键的也能用）"""
        if not self._selected():
            return
        m = QMenu(self.t)
        for b in (self.b_recv, self.b_part, self.b_excl, None, self.b_view, self.b_open):
            if b is None:
                m.addSeparator()
                continue
            a = m.addAction(b.text())
            a.setEnabled(b.isEnabled())
            a.triggered.connect(b.click)
        m.exec(self.t.viewport().mapToGlobal(pos))

    # ================================================================ 改订单状态（都先确认）
    @staticmethod
    def _list(rows):
        return "\n".join(f"• {_d(r['date'])} · {r['store_name']} · {r['supplier']}"
                         + (f" · € {float(r['amount']):,.2f}" if r.get("amount") else "") for r in rows)

    def _confirm(self, title, head, rows, after, ok_text):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(head)
        box.setInformativeText(self._list(rows) + "\n\n" + after)
        ok = box.addButton(ok_text, QMessageBox.AcceptRole)
        box.addButton(tr("取消"), QMessageBox.RejectRole)
        box.setDefaultButton(ok)
        box.exec()
        return box.clickedButton() == ok

    def receive(self, sel):
        rows = [r for r in sel if r["status"] in REC.OPEN_STATES]
        if not rows:
            return
        today = date.today()
        if not self._confirm(tr("确认收货"), tr("将以下 {0} 张订单登记为已收货（收货日期 {1}）：", len(rows), _d(today)), rows,
                             tr("确认后：\n· 这些订单不再计入在途，相关商品的订货箱数会立即重新计算；\n"
                                "· 收货状态写入采购订单跟踪，所有同事都能看到；\n· 登记错了可以点下方「撤销」，或在采购订单跟踪里改回。"),
                             tr("确认收货")):
            return
        iso = today.isoformat()
        self._apply([(r["id"], {"arrived": iso, "state": None} if r["status"] in ("partial", "stuck") else {"arrived": iso})
                     for r in rows], tr("已登记收货 {0} 张订单", len(rows)))

    def receive_partial(self, sel):
        rows = [r for r in sel if r["status"] in REC.OPEN_STATES]
        if not rows:
            return
        text, ok = QInputDialog.getText(self, tr("部分收货"), tr("缺货说明（缺哪些、什么时候补）："))
        if not ok:
            return
        if not self._confirm(tr("部分收货"), tr("将以下 {0} 张订单登记为部分收货：", len(rows)), rows,
                             tr("确认后：\n· 订单状态改为「部分到货」，仍计入在途（订货箱数不变）；\n"
                                "· 写入采购订单跟踪，所有同事都能看到；可以点下方「撤销」。"), tr("登记部分收货")):
            return
        iso = date.today().isoformat()
        ch = []
        for r in rows:
            f = {"state": "partial"}
            if not r["arrived"]:
                f["arrived"] = iso
            if text.strip():
                f["note"] = (r["note"] + " · " if r["note"] else "") + text.strip()
            ch.append((r["id"], f))
        self._apply(ch, tr("已登记部分收货 {0} 张订单", len(rows)))

    def exclude(self, sel):
        rows = [r for r in sel if r.get("record") != REC.NO_FILE]
        if not rows:
            return
        if not self._confirm(tr("不计入在途"), tr("以下 {0} 张订单将不再计入在途：", len(rows)), rows,
                             tr("确认后：\n· 订单状态不变，只是不参与在途计算，相关商品的订货箱数会立即重新计算；\n"
                                "· 可以点下方「撤销」，或在采购订单跟踪里右键「自动找订单文件」恢复。"), tr("不计入在途")):
            return
        self._apply([(r["id"], {"record": REC.NO_FILE}) for r in rows], tr("{0} 张订单已不计入在途", len(rows)))

    def undo(self):
        if not self._undo:
            return
        what, before = self._undo
        if QMessageBox.question(self, tr("撤销"), tr("撤销「{0}」？", what)) != QMessageBox.Yes:
            return
        self._undo = None
        self._apply(before, tr("已撤销：{0}", what), undoable=False)

    def _apply(self, changes, what, undoable=True):
        """写进采购订单跟踪 → 重算在途（处理中窗口）→ 页面重新扣"""
        if self.st is None or not changes:
            return
        before = []
        for oid, f in changes:
            o = self.st.orders.get(oid, {})
            before.append((oid, {k: o.get(k) for k in f}))
        self.st.set_many(changes)
        self.changed = True
        from erp_widgets import BusyDialog
        transit, err = BusyDialog.run(self, tr("重新计算"), tr("正在重新计算在途和订货箱数…"),
                                      lambda: (REC.forget(), REC.cached(self.settings, force=True))[1])
        tp = self.tracking
        if tp is not None and getattr(tp, "st", None) is not None:
            try:
                tp.load()
            except Exception:
                pass
        if err:
            QMessageBox.warning(self, tr("重新计算"), tr("订单状态已保存，但在途重算失败：{0}\n重新计算订货量后会更新。", err))
        elif transit is not None and self.on_change:
            self.on_change(transit)
        self._undo = (what, before) if undoable else None
        self.b_undo.setVisible(bool(self._undo))
        self.status.setText("✓ " + what + (tr("，订货箱数已重新计算") if not err else ""))
        sel_ids = {oid for oid, _f in changes}
        self._load()
        for k in range(self.t.rowCount()):
            i = self.ctl.row_at(k)
            if i is not None and self.rows[i]["id"] in sel_ids:
                self.t.selectRow(k)
                break
        self._update_buttons()

    # ================================================================ 查看
    def view_lines(self, i):
        if i is None or i >= len(self.rows):
            return
        r = self.rows[i]
        if not r["_info"].get("file"):
            QMessageBox.information(self, tr("查看订单明细"), tr("这张订单没有对应的订单文件。可以在采购订单跟踪里右键「指定订单文件…」"))
            return
        tp = self.tracking
        if tp is None:
            return
        from tracking_page import open_lines
        open_lines(self, tp, r, r["_info"])

    def open_file(self, i):
        if i is None or i >= len(self.rows):
            return
        r = self.rows[i]
        from erp_widgets import open_file
        if open_file(self, r["_info"].get("path")):
            self.status.setText(tr("已用 Excel 打开：{0}", os.path.basename(r["_info"]["path"])))
