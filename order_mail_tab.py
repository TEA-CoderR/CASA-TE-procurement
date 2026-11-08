"""
订单窗口的「邮件」标签页（2026-11-08 用户：不做完整的邮件软件，和订单有关的邮件放进订单里看）。
  上：这张单的邮件列表（日期 · 类型 · 发件人 · 主题 · 附件），TableSortFilter
  下：选中的那封：主题 / 发件人 → 收件人 · 日期，附件按钮（点了下载到本机缓存再打开），正文（纯文字）
只读：读正文、附件都是 EXAMINE + BODY.PEEK，Foxmail 里不会变成已读。回信还是用 Foxmail（「复制主题」方便在 Foxmail 里搜）。
业务在 commands.order_mails / mail_view_body / mail_attachment。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QAbstractItemView, QHeaderView, QPlainTextEdit, QSplitter)

from i18n import tr
from theme import T

COLS = [("date", "日期", 132), ("kind", "类型", 56), ("from", "发件人", 170), ("subject", "主题", 300), ("att", "附件", 52)]


def kind_label(k):
    return {"order": tr("下单"), "confirm": tr("确认"), "ship": tr("发货"), "reply": tr("回信"),
            "invoice": tr("发票")}.get(k, k or "")


def _fmt(d):
    return d.strftime("%Y-%m-%d %H:%M") if d else ""


class OrderMailTab(QWidget):
    def __init__(self, dlg, settings, st, oid, on_count=None):
        super().__init__(dlg)
        self.dlg, self.settings, self.st, self.oid, self.on_count = dlg, settings, st, oid, on_count
        self.rows, self.loaded, self.bodies = [], False, {}
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 10, 0, 0)
        v.setSpacing(8)
        self.t = QTableWidget(0, len(COLS))
        self.t.setHorizontalHeaderLabels([tr(h) for _f, h, _w in COLS])
        self.t.verticalHeader().setVisible(False)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.t.setSelectionMode(QAbstractItemView.SingleSelection)
        self.t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.t.horizontalHeader()
        for c, (_f, _h, w) in enumerate(COLS):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.t.setColumnWidth(c, w)
        hh.setStretchLastSection(False)
        from erp_widgets import TableSortFilter
        import settings as ST
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, key=self._key)
        self.ctl.remember(settings.setdefault("tracking", {}), "order_mails", lambda: ST.save_settings(settings),
                          [f for f, _h, _w in COLS])
        self.t.itemSelectionChanged.connect(self._show)
        # 下半：选中的那封
        low = QWidget()
        lv = QVBoxLayout(low)
        lv.setContentsMargins(0, 4, 0, 0)
        lv.setSpacing(6)
        top = QHBoxLayout()
        self.subj = QLabel("")
        self.subj.setStyleSheet("font-weight:700;")
        self.subj.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.subj, 1)
        self.b_copy = QPushButton(tr("复制主题"))
        self.b_copy.setObjectName("Link")
        self.b_copy.setToolTip(tr("回信用 Foxmail：复制主题，在 Foxmail 里搜这封信再回复"))
        self.b_copy.clicked.connect(self._copy)
        top.addWidget(self.b_copy)
        lv.addLayout(top)
        self.meta = QLabel("")
        self.meta.setObjectName("Faint")
        self.meta.setWordWrap(True)
        self.meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lv.addWidget(self.meta)
        self.att_row = QHBoxLayout()
        self.att_row.setSpacing(6)
        lv.addLayout(self.att_row)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        lv.addWidget(self.body, 1)
        sp = QSplitter(Qt.Vertical)
        sp.addWidget(self.t)
        sp.addWidget(low)
        sp.setSizes([160, 340])
        sp.setChildrenCollapsible(False)
        v.addWidget(sp, 1)
        self.empty = QLabel("")
        self.empty.setObjectName("Faint")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.hide()
        v.addWidget(self.empty)
        self._clear()

    # ------------------------------------------------------------ 数据
    def activate(self):
        """第一次切到这一页才读（读本机缓存的信头，几 MB）"""
        if self.loaded:
            return
        self.loaded = True
        import commands as CMD
        from erp_widgets import BusyDialog, fit_to_screen
        rows, err = BusyDialog.run(self, tr("邮件"), tr("正在找这张单的邮件…"), lambda: CMD.order_mails(self.st, self.oid))
        self.rows = rows or []
        if err:
            self.empty.setText(str(err))
        elif not self.rows:
            self.empty.setText(tr("这张单还没有邮件（点「✉ 邮件核对」把邮件对上订单）"))
        self.empty.setVisible(not self.rows)
        if self.on_count:
            self.on_count(len(self.rows))
        self.ctl.render()
        if self.rows:
            k = self.ctl.pos.get(len(self.rows) - 1)          # 默认选最新的一封
            self.t.selectRow(k if k is not None else 0)
        w = self.dlg.window()
        if w.width() < 900 or w.height() < 640:
            w.resize(max(w.width(), 900), max(w.height(), 640))
            fit_to_screen(w)

    def _text(self, i, c):
        r = self.rows[i]
        f = COLS[c][0]
        if f == "date":
            return _fmt(r["date"])
        if f == "kind":
            return kind_label(r["kind"])
        if f == "from":
            m = r["mail"] or {}
            return m.get("from_name") or r["from"]
        if f == "subject":
            return r["subject"]
        if f == "att":
            n = len((r["mail"] or {}).get("attachments") or [])
            return f"📎 {n}" if n else ""
        return ""

    def _key(self, i, c):
        if COLS[c][0] == "date":
            return _fmt(self.rows[i]["date"])
        return self._text(i, c).lower()

    def _fill(self, i, k):
        r = self.rows[i]
        for c in range(len(COLS)):
            it = QTableWidgetItem(self._text(i, c))
            it.setData(Qt.UserRole, i)
            if c == 1:
                tok = {"confirm": "ok", "ship": "accent", "reply": "warn"}.get(r["kind"])
                if tok:
                    it.setForeground(_color(T(tok)))
            if r["mail"] is None:
                it.setForeground(_color(T("faint")))
                it.setToolTip(tr("这封信不在本机的邮件缓存里（太早了，或者还没跑过邮件核对），看不了正文"))
            elif c == 2:
                it.setToolTip(r["from"])
            self.t.setItem(k, c, it)

    def _current(self):
        sel = self.t.selectionModel().selectedRows() if self.t.selectionModel() else []
        if not sel:
            return None
        it = self.t.item(sel[0].row(), 0)
        i = it.data(Qt.UserRole) if it else None
        return self.rows[i] if i is not None and i < len(self.rows) else None

    # ------------------------------------------------------------ 看一封
    def _clear(self):
        self.subj.setText("")
        self.meta.setText("")
        self.body.setPlainText("")
        self.b_copy.setEnabled(False)
        self._set_atts([], None)

    def _set_atts(self, parts, m, names=()):
        while self.att_row.count():
            w = self.att_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        if names:                                     # 老缓存没有附件段号：只列名字（再跑一次邮件核对就能点）
            lab = QLabel("📎 " + " · ".join(names))
            lab.setObjectName("Faint")
            lab.setToolTip(tr("再跑一次「✉ 邮件核对」以后就能点开附件"))
            self.att_row.addWidget(lab)
        for p in parts:
            b = QPushButton("📎 " + str(p[1]))
            b.setObjectName("Chip")
            size = int(p[3] or 0) if len(p) > 3 else 0
            b.setToolTip(tr("下载到本机再打开（只读，不影响邮箱）· {0} KB", max(1, round(size / 1024))) if size
                         else tr("下载到本机再打开（只读，不影响邮箱）"))
            b.clicked.connect(lambda _=False, pp=p, mm=m: self._open_att(mm, pp))
            self.att_row.addWidget(b)
        self.att_row.addStretch()

    def _show(self):
        r = self._current()
        if r is None:
            self._clear()
            return
        m = r["mail"]
        self.subj.setText(r["subject"])
        self.b_copy.setEnabled(bool(r["subject"]))
        if m is None:
            self.meta.setText(tr("{0} · {1}", r["from"], _fmt(r["date"])))
            self.body.setPlainText(tr("这封信不在本机的邮件缓存里（太早了，或者还没跑过邮件核对），看不了正文。"))
            self._set_atts([], None)
            return
        to = ", ".join(x for x in (m.get("to"), m.get("cc")) if x)
        self.meta.setText(tr("{0} → {1} · {2} · 邮箱 {3}", m.get("from_name") or m.get("from") or "", to or "—",
                             _fmt(r["date"]), m.get("mailbox") or ""))
        parts = [p for p in (m.get("parts") or []) if p and len(p) >= 3]
        self._set_atts(parts, m, [] if parts else list(m.get("attachments") or []))
        key = (m.get("mailbox"), m.get("folder"), m.get("uid"))
        if key in self.bodies:
            self.body.setPlainText(self.bodies[key])
            return
        import commands as CMD
        from erp_widgets import BusyDialog
        text, err = BusyDialog.run(self, tr("邮件"), tr("正在读这封信…"), lambda: CMD.mail_view_body(self.settings, m))
        if err:
            self.body.setPlainText(tr("读不了正文：{0}", err))
            return
        self.bodies[key] = text or ""
        if self._current() is r:
            self.body.setPlainText(text or tr("（没有正文）"))

    def _open_att(self, m, part):
        import commands as CMD
        from erp_widgets import BusyDialog, open_file
        from PySide6.QtWidgets import QMessageBox
        p, err = BusyDialog.run(self, tr("附件"), tr("正在下载 {0}…", part[1]), lambda: CMD.mail_attachment(self.settings, m, part))
        if err:
            QMessageBox.warning(self, tr("附件"), str(err))
            return
        open_file(self, p)

    def _copy(self):
        r = self._current()
        if r:
            QGuiApplication.clipboard().setText(r["subject"])
            self.b_copy.setText(tr("已复制 ✓"))
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: self.b_copy.setText(tr("复制主题")))


def _color(v):
    from PySide6.QtGui import QColor
    return QColor(v)
