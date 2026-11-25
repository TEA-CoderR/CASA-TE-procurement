"""
DETERSIVO采购「供应商报价」区的「✉ 邮件」：邮件里批发商的新价目表 → 一键放进「2 供应商报价」。业务在 commands.mail_price_lists /
mail_import_prices / mail_undo_prices + mail_prices.py。

页面显示时（每 30 分钟最多一次、用户停手后）后台悄悄读一次信头（有缓存，只读）→ 有比现在新的就把按钮变成「✉ 新报价 n」；
点按钮 → 窗口列出每家批发商最新的一封价目表信，新的默认勾上 →「放进供应商报价」先确认（替换哪几个文件、旧的进 _旧文件）→ 下载附件、替换 → 可撤销。
"""
import threading
import time
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QHeaderView, QAbstractItemView,
                               QMessageBox)

from i18n import tr
import commands as CMD

CHECK_EVERY = 30 * 60          # 后台最多 30 分钟看一次


class _Sig(QObject):
    done = Signal(object)


class PriceMailWatcher(QObject):
    """记着上一次的结果；check() 后台读，读完 changed.emit()"""
    changed = Signal()

    def __init__(self, win, settings):
        super().__init__(win)
        self.win, self.settings = win, settings
        self.res, self.when, self.err, self.running = None, 0, None, False
        self._sig = _Sig(self)
        self._sig.done.connect(self._done)

    def usable(self):
        """有填了密码的邮箱吗。不 import mail_client（imaplib / ssl 第一次加载在办公电脑上要 2–4 秒，2026-11-25 卡顿记录）"""
        import credentials
        if getattr(self, "_usable", None) is not None and time.time() - self._usable[0] < 60:
            return self._usable[1]
        ok = any(credentials.get("mail." + (a.get("address") or "").strip().lower())
                 for a in ((self.settings.get("mail") or {}).get("accounts") or []) if (a.get("address") or "").strip())
        self._usable = (time.time(), ok)
        return ok

    def n_new(self):
        return sum(1 for b in ((self.res or {}).get("found") or []) if b["newer"])

    def maybe_check(self, root):
        if self.running or not self.usable() or time.time() - self.when < CHECK_EVERY:
            return
        from erp_widgets import when_idle
        when_idle(lambda: self.check(root), 3000)

    def check(self, root, on_done=None):
        if self.running:
            return
        self.running = True

        def work():
            try:
                r = CMD.mail_price_lists(self.settings, root, log=lambda *_: None)
                self._sig.done.emit((r, None, on_done))
            except Exception as e:
                self._sig.done.emit((None, e, on_done))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, payload):
        r, e, cb = payload
        self.running = False
        self.when = time.time()
        if e is None:
            self.res, self.err = r, None
        else:
            self.err = e
            log = getattr(self.win, "log", None)
            if log:
                log(tr("[邮件] 看新报价没成功：{0}", e))
        self.changed.emit()
        if cb:
            cb(r, e)


def style_button(btn, watcher, T):
    n = watcher.n_new()
    btn.setVisible(watcher.usable())
    if n:
        btn.setText(tr("✉ 新报价 {0}", n))
        btn.setStyleSheet(f"color:{T('accent')}; font-weight:600;")
        names = "、".join(b["label"] for b in watcher.res["found"] if b["newer"])
        btn.setToolTip(tr("邮件里有比现在新的批发商报价：{0}。点开看看，确认后放进「供应商报价」", names))
    else:
        btn.setText(tr("✉ 邮件"))
        btn.setStyleSheet("")
        t = datetime.fromtimestamp(watcher.when).strftime("%H:%M") if watcher.when else ""
        btn.setToolTip(tr("从邮件里取批发商的价目表（Meloni、New Grieco、Petrillo…）放进「供应商报价」。") +
                       (tr("{0} 看过，没有新的", t) if t and not watcher.err else ""))


def open_dialog(win, settings, ui, watcher, root, after=None):
    """按钮：结果是 5 分钟内的直接开窗口，不然先读（处理中窗口）"""
    from erp_widgets import BusyDialog
    if watcher.res is None or time.time() - watcher.when > 300:
        r, e = BusyDialog.run(win, tr("邮件里的报价"), tr("正在看邮件里的批发商报价…"),
                              lambda: CMD.mail_price_lists(settings, root, log=lambda *_: None))
        if e:
            QMessageBox.warning(win, tr("邮件里的报价"), str(e))
            return
        watcher.res, watcher.when, watcher.err = r, time.time(), None
        watcher.changed.emit()
    PriceMailDialog(win, settings, ui, watcher, root, after).exec()


COLS = [(0, "", 34), (1, "批发商", 120), (2, "邮件日期", 110), (3, "附件", 230), (4, "发件人", 200),
        (5, "现在用的", 230), (6, "状态", 90)]


class PriceMailDialog(QDialog):
    def __init__(self, win, settings, ui, watcher, root, after=None):
        super().__init__(win)
        from erp_widgets import TableSortFilter, fit_to_screen
        import settings as ST
        self.win, self.settings, self.ui, self.watcher, self.root, self.after = win, settings, ui, watcher, root, after
        self._undo = None
        self.setObjectName("Dialog")
        self.setWindowTitle(tr("邮件里的报价"))
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel(tr("批发商价目表") + "  ⓘ")
        t.setObjectName("Section")
        t.setToolTip(tr("每家批发商最近 21 天里最新的一封价目表信（有 Excel 附件、主题或附件名像 Listino / Canvass / Offerta）。"
                        "比「供应商报价」里现在那份新的默认勾上。放进去 = 替换同一家的旧报价，旧的挪进「_旧文件」，不删除。"
                        "只读邮件：附件用 PEEK 取，不会把信标成已读"))
        head.addWidget(t)
        head.addStretch()
        res = watcher.res or {}
        info = QLabel(tr("读了 {0} 封信", res.get("mails", 0)))
        info.setObjectName("Muted")
        if res.get("errors"):
            info.setText(info.text() + tr(" · {0} 个邮箱没连上", len(res["errors"])))
            info.setToolTip("\n".join(res["errors"]))
            info.setStyleSheet(f"color:{ui.T('warn')};")
        head.addWidget(info)
        v.addLayout(head)
        self.rows = [dict(b, _on=b["newer"]) for b in res.get("found") or []]
        self.t = ui.make_table([tr(h) if h else "" for _f, h, _w in COLS])
        self.t.setSelectionMode(QAbstractItemView.SingleSelection)
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, (_f, _h, w) in enumerate(COLS):
            self.t.setColumnWidth(c, w)
        self._filling = False
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, key=self._key)
        self.ctl.remember(settings.setdefault("mail", {}), "prices_dlg", lambda: ST.save_settings(settings),
                          [f for f, _h, _w in COLS])
        self.t.itemChanged.connect(self._changed)
        v.addWidget(self.t, 1)
        missing = [lab for lab in res.get("labels") or [] if lab not in {b["label"] for b in self.rows}]
        self.note = QLabel(tr("邮件里没找到：{0}", "、".join(missing)) if missing else "")
        self.note.setObjectName("Faint")
        self.note.setToolTip(tr("这几家最近 21 天没有发价目表到设置了的邮箱（例如 MEGA CASH 要自己下载），照旧拖进「供应商报价」"))
        v.addWidget(self.note)
        foot = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        foot.addWidget(self.status, 1)
        self.b_undo = QPushButton(tr("撤销"))
        self.b_undo.setObjectName("Link")
        self.b_undo.setToolTip(tr("刚放进去的挪进「_旧文件」，原来那份放回去"))
        self.b_undo.clicked.connect(self.undo)
        self.b_undo.hide()
        foot.addWidget(self.b_undo)
        self.b_go = __import__("erp_widgets").CountButton("")
        self.b_go.setObjectName("Primary")
        self.b_go.clicked.connect(self.apply)
        foot.addWidget(self.b_go)
        b_close = QPushButton(tr("关闭"))
        b_close.clicked.connect(self.accept)
        foot.addWidget(b_close)
        v.addLayout(foot)
        self.resize(sum(w for _f, _h, w in COLS) + 60, 320)
        fit_to_screen(self)
        self.ctl.render()
        self._update()

    def _text(self, i, c):
        b = self.rows[i]
        f = COLS[c][0]
        m = b["mail"]
        if f == 1:
            return b["label"]
        if f == 2:
            return m["date"].strftime("%d/%m %H:%M") if m.get("date") else ""
        if f == 3:
            return b["part"][1]
        if f == 4:
            return m.get("from_name") or m.get("from") or ""
        if f == 5:
            if not b.get("current"):
                return tr("（还没有）")
            ct = b.get("current_time")
            return b["current"] + (f"  ({ct:%d/%m})" if ct else "")
        if f == 6:
            return tr("新") if b["newer"] else tr("已是最新")
        return ""

    def _key(self, i, c):
        if COLS[c][0] == 2:
            d = self.rows[i]["mail"].get("date")
            return d.timestamp() if d else 0
        return None

    def _fill(self, i, k):
        b = self.rows[i]
        T = self.ui.T
        self._filling = True
        try:
            self.ui.table_set_row(self.t, k, [self._text(i, c) for c in range(len(COLS))])
            ck = self.t.item(k, 0)
            ck.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            ck.setCheckState(Qt.Checked if b["_on"] else Qt.Unchecked)
            st = self.t.item(k, 6)
            st.setForeground(QColor(T("accent") if b["newer"] else T("faint")))
            if b["newer"]:
                f = st.font()
                f.setBold(True)
                st.setFont(f)
            m = b["mail"]
            self.t.item(k, 4).setToolTip(f"{m.get('from')}\n{m.get('subject')}\n" + tr("邮箱：{0}", m.get("mailbox")))
            self.t.item(k, 3).setToolTip(tr("{0:,} KB", round(int(b["part"][3] or 0) / 1024)))
            if not b["newer"]:
                for c in range(1, len(COLS)):
                    it = self.t.item(k, c)
                    if it is not None and c != 6:
                        it.setForeground(QColor(T("muted")))
        finally:
            self._filling = False

    def _changed(self, it):
        if self._filling or it.column() != 0:
            return
        i = self.ctl.row_at(it.row())
        if i is not None:
            self.rows[i]["_on"] = it.checkState() == Qt.Checked
            self._update()

    def _picked(self):
        return [b for b in self.rows if b["_on"]]

    def _update(self):
        n = len(self._picked())
        self.b_go.setText(tr("放进供应商报价"))
        self.b_go.set_count(n)
        self.b_go.setEnabled(bool(n))

    def apply(self):
        picks = self._picked()
        if not picks:
            return
        lines = []
        for b in picks:
            if b.get("current"):
                lines.append(f"• {b['label']}：{b['current']} → {b['part'][1]}")
            else:
                lines.append(f"• {b['label']}：" + tr("新增 {0}", b["part"][1]))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("放进供应商报价"))
        box.setText(tr("从邮件下载 {0} 份报价，放进「2 供应商报价」：", len(picks)))
        box.setInformativeText("\n".join(lines) + "\n\n" + tr(
            "确认后：\n· 同一家的旧报价挪进「_旧文件」，不删除；下次比价用新的；\n· 邮件不会被标成已读；\n· 放错了可以点下方「撤销」。"))
        ok = box.addButton(tr("放进去"), QMessageBox.AcceptRole)
        box.addButton(tr("取消"), QMessageBox.RejectRole)
        box.setDefaultButton(ok)
        box.exec()
        if box.clickedButton() != ok:
            return
        from erp_widgets import BusyDialog, excel_call
        from settings import save_settings
        # .xls 要用 Excel 转成整理好的 .xlsx：交给 Excel 那条一直不退出的线程做（别的线程用 Excel 退出时会卡死）
        r, e = BusyDialog.run(self, tr("放进供应商报价"), tr("正在从邮件下载报价…"),
                              lambda: excel_call(lambda: CMD.mail_import_prices(self.settings, self.root, picks)))
        if e:
            msg = str(e)
            if isinstance(e, PermissionError):
                msg = tr("文件被别的程序打开着（例如在 WPS / Excel 里），关掉以后再试一次。\n\n{0}", e)
            QMessageBox.warning(self, tr("放进供应商报价"), msg)
            return
        done, undo, skipped = r
        save_settings(self.settings)
        picks = [b for b in picks if b["label"] not in {lab for lab, _w in skipped}]
        self._undo = undo
        for (n, _o), b in zip(done, picks):
            b["newer"], b["_on"], b["current"], b["current_time"] = False, False, n, datetime.now()
        labs = {b["label"] for b in picks}
        for x in (self.watcher.res or {}).get("found") or []:
            if x["label"] in labs:
                x["newer"] = False
        self.ctl.render()
        self._update()
        self.status.setText(tr("✓ 已放进 {0} 份报价：{1}", len(done), "、".join(b["label"] for b in picks)))
        if skipped:
            QMessageBox.warning(self, tr("放进供应商报价"), tr("这几份没有放进去（原来的留着）：") + "\n" +
                                "\n".join(f"· {lab}：{why}" for lab, why in skipped))
        notes = [n for n in (undo.get("notes") or []) if n[0] in labs]
        if notes:
            QMessageBox.information(self, tr("放进供应商报价"), tr("已经放进去了，但要注意：") + "\n" +
                                    "\n".join(f"· {lab}：{why}" for lab, why in notes))
        self.b_undo.show()
        self.watcher.changed.emit()
        if self.after:
            self.after()

    def undo(self):
        if not self._undo:
            return
        if QMessageBox.question(self, tr("撤销"), tr("把刚放进去的报价拿走、换回原来那份？")) != QMessageBox.Yes:
            return
        from erp_widgets import BusyDialog
        from settings import save_settings
        _r, e = BusyDialog.run(self, tr("撤销"), tr("正在换回原来的报价…"),
                               lambda: CMD.mail_undo_prices(self.settings, self._undo))
        if e:
            QMessageBox.warning(self, tr("撤销"), str(e))
            return
        save_settings(self.settings)
        self._undo = None
        self.b_undo.hide()
        self.status.setText(tr("已撤销，换回了原来的报价"))
        self.watcher.when = 0                    # 下次再看
        if self.after:
            self.after()
