"""
在途箱数那一格双击 → 这个商品在途的是哪几张单（2026-11-03 用户：点进去看订单，到了没标的直接标到货）。

订货明细（总仓 / 门店采购）和检查门店清单共用。改了以后（标到货、不算在途）重算在途，on_change(新的在途) 让页面
重新扣：订货明细 → StoreOrderPage.reapply_transit；检查清单 → OrderCheck.reapply_transit。
"""
import os
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QHeaderView, QMenu,
                               QAbstractItemView, QApplication, QInputDialog)

from i18n import tr
import order_record as REC
import order_tracking as OT

COLS = [(0, "下单日期", 92), (1, "门店", 96), (2, "供应商", 150), (3, "这个商品", 74), (4, "状态", 84),
        (5, "订单文件", 260)]


def tracking_store(settings):
    folder = REC.tracking_folder(settings)
    if not folder:
        return None
    cfg = (settings or {}).get("tracking") or {}
    me = (cfg.get("me") or (settings or {}).get("user_name") or OT.default_me()).strip()
    return OT.Store(folder, me=me).reload()


class TransitOrdersDialog(QDialog):
    def __init__(self, win, settings, ui, title, tinfo, on_change=None):
        super().__init__(win)
        self.win, self.settings, self.ui, self.on_change = win, settings, ui, on_change
        self.tinfo = tinfo or {}
        self.changed = False
        self.setWindowTitle(tr("在途的订单"))
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)
        t = QLabel(title + "  ⓘ")
        t.setObjectName("Section")
        t.setToolTip(tr("这个商品已经订了、还没到货的单（采购订单跟踪）。货其实到了的：选中点「已到货」，建议量马上重新算；"
                        "右键还可以看整张单的商品、打开订单文件"))
        v.addWidget(t)
        self.t = ui.make_table([tr(h) for _f, h, _w in COLS], numeric_cols=(3,))
        self.t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, (_f, _h, wd) in enumerate(COLS):
            self.t.setColumnWidth(c, wd)
        from erp_widgets import TableSortFilter, fit_to_screen
        import settings as ST
        self.rows = []
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, key=self._key, numeric=(3,))
        self.ctl.remember(settings.setdefault("tracking", {}), "transit_dlg", lambda: ST.save_settings(settings),
                          [f for f, _h, _w in COLS])
        self.t.setContextMenuPolicy(Qt.CustomContextMenu)
        self.t.customContextMenuRequested.connect(self._menu)
        self.t.cellDoubleClicked.connect(lambda k, _c: self._lines(self.ctl.row_at(k)))
        v.addWidget(self.t, 1)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.b_arr = QPushButton(tr("已到货（今天）"))
        self.b_arr.setObjectName("Primary")
        self.b_arr.setToolTip(tr("选中的单标成今天到货：不再算在途"))
        self.b_arr.clicked.connect(lambda: self._mark_arrived(self._selected()))
        self.b_lines = QPushButton(tr("看订了哪些商品"))
        self.b_lines.clicked.connect(lambda: self._lines(self._cur()))
        foot.addWidget(self.b_arr)
        foot.addWidget(self.b_lines)
        foot.addStretch()
        b_close = QPushButton(tr("关闭"))
        b_close.clicked.connect(self.accept)
        foot.addWidget(b_close)
        v.addLayout(foot)
        self.resize(820, 340)
        fit_to_screen(self)
        self._load()

    # ---- 数据 ----
    def _load(self):
        self.st = None
        try:
            self.st = tracking_store(self.settings)
        except Exception as e:
            self.win.show_toast(tr("订单记录读不了：{0}", e))
        ids = REC.order_ids(self.tinfo)
        colli = {}
        for o in self.tinfo.get("orders") or []:
            if len(o) > 3:
                colli[o[3]] = colli.get(o[3], 0) + o[2]
        rows = {r["id"]: r for r in OT.rows(self.st)} if self.st is not None else {}
        self.rows = [dict(rows[i], _colli=colli.get(i, 0)) for i in ids if i in rows]
        tr_res = REC._MEMO.get("res") or {}
        for r in self.rows:
            r["_info"] = (tr_res.get("orders") or {}).get(r["id"]) or {}
        self.ctl.render()
        self.b_arr.setEnabled(bool(self.rows) and self.st is not None)
        self.b_lines.setEnabled(bool(self.rows))

    def _text(self, i, c):
        r = self.rows[i]
        f = COLS[c][0]
        if f == 0:
            return r["date"].strftime("%d/%m/%Y") if r["date"] else ""
        if f == 1:
            return r["store_name"]
        if f == 2:
            return r["supplier"]
        if f == 3:
            return f"{r['_colli']:g}"
        if f == 4:
            from tracking_page import STATUS_LABELS
            return STATUS_LABELS().get(r["status"], r["status"])
        if f == 5:
            return os.path.basename(r["_info"].get("file") or "")
        return ""

    def _key(self, i, c):
        r = self.rows[i]
        f = COLS[c][0]
        if f == 0:
            return r["date"].toordinal() if r["date"] else 0
        if f == 3:
            return r["_colli"]
        return None

    def _fill(self, i, k):
        r = self.rows[i]
        self.ui.table_set_row(self.t, k, [self._text(i, c) for c in range(len(COLS))])
        it = self.t.item(k, 5)
        if it is not None and r["_info"].get("file"):
            it.setToolTip(r["_info"]["file"])
        st = self.t.item(k, 4)
        if st is not None and r["overdue"]:
            st.setForeground(QColor(self.ui.T("err")))
            st.setToolTip(tr("超期 {0} 天", r["late_by"]))

    def _selected(self):
        ks = sorted({ix.row() for ix in self.t.selectionModel().selectedRows()})
        out = [self.rows[i] for i in (self.ctl.row_at(k) for k in ks) if i is not None]
        if not out and len(self.rows) == 1:
            out = list(self.rows)
        return out

    def _cur(self):
        sel = self._selected()
        return self.rows.index(sel[0]) if sel else (0 if self.rows else None)

    # ---- 操作 ----
    def _menu(self, pos):
        sel = self._selected()
        if not sel:
            return
        m = QMenu(self.t)
        a_arr = m.addAction(tr("已到货（今天）"))
        a_part = m.addAction(tr("部分到货…"))
        m.addSeparator()
        a_lines = m.addAction(tr("看订了哪些商品"))
        a_lines.setEnabled(len(sel) == 1 and bool(sel[0]["_info"].get("file")))
        a_open = m.addAction(tr("打开订单文件"))
        a_open.setEnabled(len(sel) == 1 and bool(sel[0]["_info"].get("path")))
        a_none = m.addAction(tr("没有订单文件"))
        a_none.setToolTip(tr("这张单不算在途（例如电话订的、没有明细）"))
        got = m.exec(self.t.viewport().mapToGlobal(pos))
        if got == a_arr:
            self._mark_arrived(sel)
        elif got == a_part:
            text, ok = QInputDialog.getText(self, tr("部分到货"), tr("备注（缺什么 / 为什么）"))
            if ok:
                today = date.today().isoformat()
                ch = []
                for r in sel:
                    f = {"state": "partial"}
                    if not r["arrived"]:
                        f["arrived"] = today
                    if text.strip():
                        f["note"] = (r["note"] + " · " if r["note"] else "") + text.strip()
                    ch.append((r["id"], f))
                self._save(ch)
        elif got == a_lines:
            self._lines(self.rows.index(sel[0]))
        elif got == a_open:
            from main import open_path
            open_path(sel[0]["_info"]["path"])
        elif got == a_none:
            self._save([(r["id"], {"record": REC.NO_FILE}) for r in sel])

    def _mark_arrived(self, sel):
        if not sel:
            return
        today = date.today().isoformat()
        self._save([(r["id"], {"arrived": today, "state": None} if r["status"] in ("partial", "stuck")
                     else {"arrived": today}) for r in sel])

    def _save(self, changes):
        if self.st is None or not changes:
            return
        self.st.set_many(changes)
        self.changed = True
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            REC.forget()
            transit = REC.cached(self.settings, force=True)
        except Exception as e:
            transit = None
            self.win.show_toast(tr("在途重算失败：{0}", e))
        finally:
            QApplication.restoreOverrideCursor()
        tp = getattr(self.win, "tracking", None)
        if tp is not None and getattr(tp, "st", None) is not None:
            try:
                tp.load()
            except Exception:
                pass
        if transit is not None and self.on_change:
            self.on_change(transit)
        self.win.show_toast(tr("已更新 {0} 张单，在途和建议量已重新算", len(changes)))
        self._load()

    def _lines(self, i):
        if i is None or i >= len(self.rows):
            return
        r = self.rows[i]
        info = r["_info"]
        if not info.get("file"):
            self.win.show_toast(tr("没找到这张单的订单文件：右键「指定订单文件…」"))
            return
        tp = getattr(self.win, "tracking", None)
        if tp is None:
            return
        from tracking_page import RecordLinesDialog
        RecordLinesDialog(self, tp, r, info).exec()
