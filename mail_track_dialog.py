"""
采购订单跟踪「邮件核对」（2026-11-07 用户：邮件摸底看完，开始做第 1 步 = 订单跟踪按邮件更新）。业务在 commands.mail_* / mail_match。

读所有邮箱（收件箱 + 已发送）最近 21 天的信头（只读、读过的有缓存）→ 窗口三页：
  邮件进展：我们发了订单 → 待登记改成已下单；供应商确认 → 已确认；发了 DDT / 发货通知 → 已发货
  重复的待登记：程序生成的待登记和 Excel 里手填的是同一张单 → 合并（订单文件挂到手填那张上，待登记删掉）
  跟踪里没有：已发送里有订单信、订单跟踪里找不到 → 补登记（默认不勾，先看「附近已有的单」）
勾选 → 「应用所选」先弹确认（改哪几张、改完会怎样、所有同事可见）→ 写进共享的订单跟踪 → 底部「撤销」。
「忽略所选」= 这台电脑以后不再提这几条。
"""
import threading
from collections import Counter

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QHeaderView, QTabWidget,
                               QAbstractItemView, QMessageBox, QWidget)

from i18n import tr
import commands as CMD
import order_tracking as OT


def _d(v):
    return v.strftime("%d/%m") if v else ""


def KIND_TEXT():
    return {"order": tr("已下单"), "confirm": tr("已确认"), "ship": tr("已发货")}


def _now_text(s):
    from tracking_page import STATUS_LABELS
    return STATUS_LABELS().get(s.get("status"), s.get("status") or "")


# 每页的列：(固定编号, 表头, 宽度)
TABS = {
    "suggest": [(0, "", 34), (1, "邮件日期", 82), (2, "门店", 92), (3, "供应商", 160), (4, "下单日期", 82),
                (5, "现在", 76), (6, "改成", 96), (7, "邮件主题", 340), (8, "发件人", 200)],
    "dups": [(0, "", 34), (2, "门店", 92), (3, "供应商（待登记）", 190), (4, "生成日期", 82),
             (9, "Excel 里的那张", 200), (10, "金额 €", 90), (11, "订单文件", 300)],
    "missing": [(0, "", 34), (1, "邮件日期", 82), (2, "门店", 92), (3, "供应商", 160), (4, "下单日期", 82),
                (12, "附近已有的单", 110), (7, "邮件主题", 340), (8, "发件人", 180)],
}


class _Tab:
    def __init__(self, dlg, name, rows, checked):
        from erp_widgets import TableSortFilter
        import settings as ST
        self.dlg, self.name, self.ui = dlg, name, dlg.ui
        self.cols = TABS[name]
        self.rows = rows
        for r in rows:
            r["_on"] = checked and not r.get("unsure")          # 门店不确定的默认不勾
        self.t = self.ui.make_table([tr(h) if h else "" for _f, h, _w in self.cols],
                                    numeric_cols=tuple(c for c, (f, _h, _w) in enumerate(self.cols) if f == 10))
        self.t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, (_f, _h, wd) in enumerate(self.cols):
            self.t.setColumnWidth(c, wd)
        self._filling = False
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, key=self._key)
        self.ctl.remember(dlg.settings.setdefault("mail", {}), "track_" + name, lambda: ST.save_settings(dlg.settings),
                          [f for f, _h, _w in self.cols])
        self.t.itemChanged.connect(self._changed)
        self.t.cellDoubleClicked.connect(self._double)
        self.ctl.render()

    def _text(self, i, c):
        s = self.rows[i]
        f = self.cols[c][0]
        m = s.get("mail") or {}
        if f == 0:
            return ""
        if f == 1:
            return _d(m.get("date"))
        if f == 2:
            return OT.STORE_NAMES.get(s["store"], s["store"]) if s["store"] else tr("（没写门店）")
        if f == 3:
            return s["supplier"]
        if f == 4:
            return _d(s.get("order_date"))
        if f == 5:
            return _now_text(s)
        if f == 6:
            return KIND_TEXT().get(s["kind"], "") + ((" " + _d(OT.to_date(s["value"]))) if s["field"] == "shipped" else "")
        if f == 7:
            return m.get("subject") or ""
        if f == 8:
            return m.get("from") or ""
        if f == 9:
            return s.get("twin_text") or ""
        if f == 10:
            o = self.dlg.st.orders.get(s.get("id") or "") or {}
            return "" if o.get("amount") is None else f"{float(o['amount']):,.2f}"
        if f == 11:
            import os
            o = self.dlg.st.orders.get(s.get("id") or "") or {}
            return os.path.basename(str(o.get("file") or ""))
        if f == 12:
            return _d(s.get("near")) if s.get("near") else tr("没有")
        return ""

    def _key(self, i, c):
        s = self.rows[i]
        f = self.cols[c][0]
        if f == 0:
            return 0 if s["_on"] else 1
        if f in (1, 4):
            d = (s.get("mail") or {}).get("date") if f == 1 else s.get("order_date")
            return d.toordinal() if d else 0
        return None

    def _fill(self, i, k):
        s = self.rows[i]
        T = self.ui.T
        self._filling = True
        try:
            self.ui.table_set_row(self.t, k, [self._text(i, c) for c in range(len(self.cols))])
            ck = self.t.item(k, 0)
            if ck is not None:
                ok = not (self.name == "missing" and not s["store"])
                ck.setFlags((Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable) if ok else Qt.ItemIsSelectable)
                ck.setCheckState(Qt.Checked if s["_on"] and ok else Qt.Unchecked)
                if not ok:
                    ck.setToolTip(tr("主题里没写是哪几家门店（「5PDV」），补登记不了；请在订单跟踪里手动加"))
            for c, (f, _h, _w) in enumerate(self.cols):
                it = self.t.item(k, c)
                if it is None:
                    continue
                if f == 6:
                    it.setForeground(QColor(T("accent")))
                    fo = it.font()
                    fo.setBold(True)
                    it.setFont(fo)
                if f == 7:
                    m = s.get("mail") or {}
                    att = m.get("attachments") or []
                    it.setToolTip(m.get("subject", "") + (("\n" + tr("附件：") + "\n" + "\n".join(att)) if att else "")
                                  + "\n" + tr("邮箱：{0}", m.get("mailbox", "")))
                if f == 11:
                    o = self.dlg.st.orders.get(s.get("id") or "") or {}
                    it.setToolTip(str(o.get("file") or ""))
                if f == 12 and s.get("near"):
                    it.setForeground(QColor(T("warn")))
                    it.setToolTip(tr("同一家供应商、同一家门店 {0} 已经有一张单，可能就是这张（日期填得不一样）。"
                                     "是的话别补登记，点「忽略所选」", _d(s["near"])))
                if f == 2 and not s["store"]:
                    it.setForeground(QColor(T("faint")))
                if f == 2 and s.get("unsure"):
                    it.setText(it.text() + " ?")
                    it.setForeground(QColor(T("warn")))
                    it.setToolTip(tr("这封信没写是哪家门店，正文里也没认出来，同一批有好几家店的单。"
                                     "一张 DDT / 确认通常只管一家店：请打开邮件看是哪家，只勾那一家"))
        finally:
            self._filling = False

    def _changed(self, it):
        if self._filling or it.column() != 0:
            return
        i = self.ctl.row_at(it.row())
        if i is not None:
            self.rows[i]["_on"] = it.checkState() == Qt.Checked
            self.dlg._update()

    def _double(self, k, c):
        i = self.ctl.row_at(k)
        if i is None or c == 0:
            return
        s = self.rows[i]
        s["_on"] = not s["_on"]
        self.ctl.render()
        self.dlg._update()

    def checked(self):
        return [s for s in self.rows if s["_on"] and not (self.name == "missing" and not s["store"])]

    def selected(self):
        ks = sorted({ix.row() for ix in self.t.selectionModel().selectedRows()})
        return [self.rows[i] for i in (self.ctl.row_at(k) for k in ks) if i is not None]

    def set_all(self, on):
        for s in self.rows:
            s["_on"] = on
        self.ctl.render()

    def drop(self, keys):
        self.rows[:] = [s for s in self.rows if s["key"] not in keys]
        self.ctl.render()


class MailTrackDialog(QDialog):
    def __init__(self, win, settings, ui, res, page=None):
        super().__init__(win)
        from erp_widgets import fit_to_screen
        self.win, self.settings, self.ui, self.page = win, settings, ui, page
        self.st = res["store"]
        self._undo = None
        self.setObjectName("Dialog")
        self.setWindowTitle(tr("邮件核对"))
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel(tr("邮件里的订单进展") + "  ⓘ")
        t.setObjectName("Section")
        t.setToolTip(tr("只读了信头（发件人、主题、日期、附件名），没读正文、没改已读、没发信。\n"
                        "· 已发送里有「Ordine 供应商 门店」→ 待登记改成已下单（下单日期 = 发信那天）\n"
                        "· 供应商的确认信（Conferma ordine、Proposta d'ordine、Preventivo、回复我们的订单信）→ 已确认\n"
                        "· DDT、Bolla、发货通知 → 已发货\n"
                        "勾上的才会改；改之前会再问一次。认不准的可以「忽略所选」，以后不再提"))
        head.addWidget(t)
        head.addStretch()
        info = QLabel(tr("读了 {0} 封信", res["mails"]) + (tr(" · {0} 个邮箱没连上", len(res["errors"])) if res["errors"] else ""))
        info.setObjectName("Muted")
        if res["errors"]:
            info.setToolTip("\n".join(res["errors"]))
            info.setStyleSheet(f"color:{ui.T('warn')};")
        head.addWidget(info)
        v.addLayout(head)

        self.tabs = QTabWidget()
        self.tab = {}
        for name, title, rows, on in (("suggest", tr("邮件进展"), res["suggest"], True),
                                      ("dups", tr("重复的待登记"), res["dups"], True),
                                      ("missing", tr("跟踪里没有"), res["missing"], False)):
            tb = _Tab(self, name, rows, on)
            self.tab[name] = tb
            w = QWidget()
            lv = QVBoxLayout(w)
            lv.setContentsMargins(0, 8, 0, 0)
            lv.addWidget(tb.t)
            self.tabs.addTab(w, "")
            self.tabs.setTabToolTip(self.tabs.count() - 1, {
                "suggest": tr("邮件说明这些单往前走了一步（下单 / 确认 / 发货），订单跟踪里还没登记"),
                "dups": tr("程序生成的「待登记」和 Excel 里手填的是同一张单（同一家门店、供应商名字相近、日期前后 3 天）。"
                           "合并 = 订单文件 / 箱数 / 金额挂到手填的那张上，待登记删掉；以后重新配货也不会再生成"),
                "missing": tr("已发送里有这张订单的信，订单跟踪里找不到。补登记 = 加一张「已下单」的单（金额空着，要的话自己填）。"
                              "默认不勾：先看「附近已有的单」，可能只是日期填得不一样")}[name])
        self._titles = {"suggest": tr("邮件进展"), "dups": tr("重复的待登记"), "missing": tr("跟踪里没有")}
        v.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(lambda _i: self._update())

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        foot.addWidget(self.status, 1)
        self.b_undo = QPushButton(tr("撤销"))
        self.b_undo.setObjectName("Link")
        self.b_undo.setToolTip(tr("把刚才改的订单改回去（删掉的待登记恢复、补登记的删掉）"))
        self.b_undo.clicked.connect(self.undo)
        self.b_undo.hide()
        foot.addWidget(self.b_undo)
        self.b_all = QPushButton(tr("全选"))
        self.b_all.clicked.connect(lambda: (self._cur().set_all(True), self._update()))
        self.b_none = QPushButton(tr("全不选"))
        self.b_none.clicked.connect(lambda: (self._cur().set_all(False), self._update()))
        self.b_ign = QPushButton(tr("忽略所选"))
        self.b_ign.setToolTip(tr("表格里选中的行（不是勾选的）以后不再提示；只影响这台电脑，订单跟踪不变"))
        self.b_ign.clicked.connect(self.ignore)
        self.b_apply = QPushButton("")
        self.b_apply.setObjectName("Primary")
        self.b_apply.clicked.connect(self.apply)
        b_close = QPushButton(tr("关闭"))
        b_close.clicked.connect(self.accept)
        for b in (self.b_all, self.b_none, self.b_ign, self.b_apply, b_close):
            foot.addWidget(b)
        v.addLayout(foot)
        self.resize(1240, 560)
        fit_to_screen(self)
        first = next((i for i, n in enumerate(("suggest", "dups", "missing")) if self.tab[n].rows), 0)
        self.tabs.setCurrentIndex(first)
        self._update()

    def _cur(self):
        return self.tab[("suggest", "dups", "missing")[self.tabs.currentIndex()]]

    def _all_checked(self):
        return [s for n in ("suggest", "dups", "missing") for s in self.tab[n].checked()]

    def _update(self):
        for i, n in enumerate(("suggest", "dups", "missing")):
            self.tabs.setTabText(i, f"{self._titles[n]}（{len(self.tab[n].rows)}）")
        sel = self._all_checked()
        n = len({s.get("id") or s["key"] for s in sel})
        self.b_apply.setText(tr("应用所选（{0} 张单）", n) if n else tr("应用所选"))
        self.b_apply.setEnabled(bool(sel))
        self.b_ign.setEnabled(bool(self._cur().rows))

    # ---------------------------------------------------------------- 改
    def apply(self):
        sel = self._all_checked()
        if not sel:
            return
        c = Counter(s["kind"] for s in sel)
        lines = []
        for k, text in (("order", tr("待登记 → 已下单（下单日期改成发订单信那天）")),
                        ("confirm", tr("登记已确认（确认日期 = 供应商回信那天）")),
                        ("ship", tr("登记已发货（发货日期 = DDT / 发货通知那天）")),
                        ("dup", tr("合并重复的待登记（订单文件挂到 Excel 手填的那张，待登记删掉）")),
                        ("missing", tr("补登记已下单的订单（金额空着）"))):
            if c.get(k):
                lines.append(f"• {text}：{c[k]}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("应用邮件核对"))
        box.setText(tr("将修改采购订单跟踪里的 {0} 张订单：", len({s.get('id') or s['key'] for s in sel})))
        box.setInformativeText("\n".join(lines) + "\n\n" + tr(
            "确认后：\n· 写进共享文件夹的订单跟踪，所有同事都能看到，每张单的修改记录里写着「邮件」；\n"
            "· 已下单 / 已确认的会照常写回 PIANO Excel；\n· 改错了可以点下方「撤销」，或在订单跟踪里改回。"))
        ok = box.addButton(tr("应用"), QMessageBox.AcceptRole)
        box.addButton(tr("取消"), QMessageBox.RejectRole)
        box.setDefaultButton(ok)
        box.exec()
        if box.clickedButton() != ok:
            return
        from erp_widgets import BusyDialog
        r, e = BusyDialog.run(self, tr("邮件核对"), tr("正在写进订单跟踪…"), lambda: CMD.mail_apply(self.st, sel))
        if e:
            QMessageBox.warning(self, tr("邮件核对"), str(e))
            return
        n, undo = r
        self._undo = (n, undo)
        keys = {s["key"] for s in sel}
        for tb in self.tab.values():
            tb.drop(keys)
        self.status.setText(tr("✓ 已更新 {0} 张订单", n))
        self.b_undo.setVisible(bool(undo))
        self._update()
        self._refresh_page()

    def undo(self):
        if not self._undo:
            return
        n, items = self._undo
        if QMessageBox.question(self, tr("撤销"), tr("把刚才改的 {0} 张订单改回去？", n)) != QMessageBox.Yes:
            return
        from erp_widgets import BusyDialog
        _r, e = BusyDialog.run(self, tr("撤销"), tr("正在改回去…"), lambda: CMD.mail_undo(self.st, items))
        if e:
            QMessageBox.warning(self, tr("撤销"), str(e))
            return
        self._undo = None
        self.b_undo.hide()
        self.status.setText(tr("已撤销（关掉再点「邮件核对」，这几条会重新列出来）"))
        self._refresh_page()

    def ignore(self):
        tb = self._cur()
        sel = tb.selected()
        if not sel:
            QMessageBox.information(self, tr("忽略所选"), tr("先在表格里选中要忽略的行（可以按住 Ctrl 选几行）。"))
            return
        CMD.mail_ignore([s["key"] for s in sel])
        tb.drop({s["key"] for s in sel})
        self.status.setText(tr("已忽略 {0} 条，以后不再提示", len(sel)))
        self._update()

    def _refresh_page(self):
        if self.page is not None:
            try:
                self.page.st = self.st
                self.page.refresh()
            except Exception:
                pass


class _Sig(QObject):
    done = Signal(object)


def run(win, settings, ui, page=None):
    """订单跟踪页「邮件核对」：读邮件（进度窗口，可后台运行）→ 打开核对窗口"""
    from erp_widgets import TaskDialog, ask_return
    import mail_client as MC
    accs = MC.accounts(settings)
    if not accs:
        QMessageBox.information(win, tr("邮件核对"), tr("还没设置邮箱：系统设置 → 邮箱 → 管理邮箱…"))
        return
    usable = [a for a in accs if CMD.mail_password(a["address"])]
    if not usable:
        QMessageBox.information(win, tr("邮件核对"), tr("邮箱都还没填密码：系统设置 → 邮箱 → 管理邮箱…"))
        return
    days = int((settings.get("mail") or {}).get("track_days") or CMD.MAIL_TRACK_DAYS)
    dlg = TaskDialog(win, tr("邮件核对"), tr("{0} 个邮箱 · 最近 {1} 天 · 只读信头", len(usable), days),
                     [a["address"] for a in usable], cancellable=True)
    dlg.went_background.connect(lambda: win.show_toast(tr("邮件核对在后台进行，完成后会提醒")))
    dlg.show()
    sig = _Sig(dlg)
    log = getattr(win, "log", print)
    sets = dict(settings, mail=dict(settings.get("mail") or {},
                                    accounts=[a for a in (settings.get("mail") or {}).get("accounts") or []
                                              if CMD.mail_password(a.get("address") or "")]))

    def work():
        try:
            res = CMD.mail_tracking(sets, days=days, log=log, progress=dlg, cancel=dlg.cancel_event.is_set)
            n = len(res["suggest"]) + len(res["dups"]) + len(res["missing"])
            dlg.post_finished(True, tr("读了 {0} 封信 · {1} 条要核对", res["mails"], n))
            sig.done.emit((res, None))
        except Exception as e:
            dlg.post_finished(False, str(e))
            sig.done.emit((None, e))

    def done(payload):
        res, err = payload
        if err:
            QMessageBox.warning(win, tr("邮件核对"), str(err))
            return
        n = len(res["suggest"]) + len(res["dups"]) + len(res["missing"])

        def go():
            if not n:
                win.show_toast(tr("邮件核对：读了 {0} 封信，订单跟踪都是最新的", res["mails"]))
                return
            QTimer.singleShot(0, lambda: MailTrackDialog(win, settings, ui, res, page).exec())
        ask_return(win, dlg, tr("邮件核对完成：{0} 条要核对", n), go)
    sig.done.connect(done)
    threading.Thread(target=work, daemon=True).start()
