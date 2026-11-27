"""
系统设置 → 数据连接 → 扫码枪（2026-11-21 用户：门店 Zebra TC22 扫 DETERSIVO 商品，一台扫码枪一个账号）。
业务在 scanner_catalog.py（账号、目录、门店清单）；这里只是界面。账号、目录、清单都存在预约平台那台服务器上，要先连好预约平台。

两个标签页：扫码枪（账号）· 门店清单（门店在扫码枪上做的 DETERSIVO 清单：截止时间、谁交了、放进「1 门店清单」）。
"""
import os
import shutil
from datetime import datetime

from PySide6.QtCore import Qt, QTime, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
                               QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox,
                               QTabWidget, QWidget, QTimeEdit)

from i18n import tr
import booking_api as BA
import scanner_catalog as SC
import theme

STORE_NAMES = {0: "BMS", 1: "FOIANO", 2: "MARLIA", 3: "S.FILIPPO", 4: "S.MARCO", 5: "S.CONCORDIO"}


def _when(iso):
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone()
        return d.strftime("%d/%m %H:%M")
    except ValueError:
        return str(iso)[:16]


def _day(ymd):
    s = str(ymd or "")
    return f"{s[8:10]}/{s[5:7]}" if len(s) >= 10 else s


def _busy(parent, text, fn):
    from erp_widgets import BusyDialog
    return BusyDialog.run(parent, tr("扫码枪"), text, fn)


def _maximizable(d):
    """窗口右上角有最大化按钮、双击标题栏全屏（门店清单明细 / 库存报错要看得多，2026-11-25 用户）"""
    d.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
                     | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
    d.setSizeGripEnabled(True)


def list_state(lst):
    """一张清单现在是什么状态 → (文字, 颜色 token)"""
    if lst.get("status") == "ordered":
        return tr("已回写结果"), "ok"
    sub, rec, upd = lst.get("submittedAt"), lst.get("receivedAt"), lst.get("updatedAt") or ""
    if rec and upd > rec:
        return tr("放进来以后又改过"), "warn"
    if rec:
        return tr("已放进门店清单"), "ok"
    if sub and upd > sub:
        return tr("已提交 · 之后又改过"), "warn"
    if sub:
        return tr("已提交"), "accent"
    return tr("正在做（还没提交）"), "muted"


class ScannerDialog(QDialog):
    COLS = ("名称", "门店", "状态", "最后在线")
    LCOLS = ("门店", "截止", "状态", "商品", "箱数", "非 Griglia", "提交时间", "提交人")

    def __init__(self, win, settings, tab="devices"):
        super().__init__(win)
        self.win, self.settings = win, settings
        self.devices, self.lists = [], []
        self.setWindowTitle(tr("扫码枪"))
        _maximizable(self)
        self.resize(820, 520)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)
        head = QHBoxLayout()
        from erp_widgets import tip_label
        t = tip_label(tr("门店扫码枪"), tr("门店用 Zebra 扫码枪扫要订的 DETERSIVO 商品，当场看到能不能订、本店库存 / 销量 / 价格，"
                                        "做成这一周的清单交给采购部。一台扫码枪一个账号：新增后在扫码枪上输入配对码。"))
        t.setObjectName("SectionTitle")
        head.addWidget(t)
        head.addStretch()
        b = QPushButton(tr("下载扫码程序"))
        b.setToolTip(tr("在扫码枪的浏览器里打开 {0}/app 下载安装（安卓程序）", BA.base_url(settings)))
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(BA.base_url(self.settings) + "/app")))
        head.addWidget(b)
        v.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._devices_tab(), tr("扫码枪"))
        self.tabs.addTab(self._lists_tab(), tr("门店清单"))
        self.reports = []
        self.tabs.addTab(self._reports_tab(), tr("库存报错"))
        v.addWidget(self.tabs, 1)

        bl = QHBoxLayout()
        bl.addStretch(1)
        c = QPushButton(tr("关闭"))
        c.clicked.connect(self.accept)
        bl.addWidget(c)
        v.addLayout(bl)
        from erp_widgets import fit_to_screen
        fit_to_screen(self)
        self._start_poll()
        self.load()
        if tab == "lists":
            self.tabs.setCurrentIndex(1)
            self.load_lists()
        elif tab == "stock":
            self.tabs.setCurrentIndex(2)
            self.load_reports()
        self.tabs.currentChanged.connect(self._tab_changed)

    def _tab_changed(self, i):
        if i == 1 and not self.lists:
            self.load_lists()
        elif i == 2 and not self.reports:
            self.load_reports()

    # ------------------------------------------------------------------ 扫码枪
    def _table(self, cols, widths):
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels([tr(c) for c in cols])
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        for i, w in enumerate(widths):
            t.setColumnWidth(i, w)
        hh.setStretchLastSection(True)
        return t

    def _devices_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(10)
        cat = QHBoxLayout()
        self.cat_lbl = QLabel("")
        self.cat_lbl.setObjectName("PageSub")
        cat.addWidget(self.cat_lbl, 1)
        self.ref_lbl = QLabel("")
        self.ref_lbl.setObjectName("PageSub")
        cat.addWidget(self.ref_lbl)
        self.b_ref = QPushButton(tr("刷新"))
        self.b_ref.setToolTip(tr("马上重新读一次扫码枪的状态（窗口开着时也会自动刷新：有扫码枪在等配对时每 {0} 秒一次）",
                                 self.POLL_FAST // 1000))
        self.b_ref.clicked.connect(lambda: self.load(quiet=True))
        cat.addWidget(self.b_ref)
        b = QPushButton(tr("发布目录"))
        b.setToolTip(tr("把现在的 Griglia、这周批发商报价、替代商品、直厂商品，还有每家门店的库存 / 销量 / 在途 / 建议箱数 / 进价 / 卖价 / "
                        "打折价发到扫码枪（每家店只收到自己的；Griglia 页刷新时也会自动发布，门店数据最多 4 小时重算一次）"))
        b.clicked.connect(self.publish)
        cat.addWidget(b)
        v.addLayout(cat)
        self.t = self._table(self.COLS, (220, 110, 220))
        self.t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.t.itemSelectionChanged.connect(self._sel)
        self.t.doubleClicked.connect(lambda _ix: self.edit())
        v.addWidget(self.t, 1)
        bl = QHBoxLayout()
        self.b_add = QPushButton(tr("新增扫码枪"))
        self.b_add.setObjectName("Primary")
        self.b_add.clicked.connect(self.add)
        self.b_code = QPushButton(tr("重新配对"))
        self.b_code.setToolTip(tr("扫码枪丢了 / 重置了：生成新的配对码，原来那台马上不能用"))
        self.b_code.clicked.connect(self.renew)
        self.b_edit = QPushButton(tr("编辑"))
        self.b_edit.setToolTip(tr("改名称、换门店（扫码枪不用重新配对；换门店后它收到新门店的库存 / 销量，清单算新门店的）"))
        self.b_edit.clicked.connect(self.edit)
        self.b_off = QPushButton(tr("停用"))
        self.b_off.clicked.connect(self.toggle)
        self.b_del = QPushButton(tr("删除"))
        self.b_del.clicked.connect(self.delete)
        for b in (self.b_add, self.b_code, self.b_edit, self.b_off, self.b_del):
            bl.addWidget(b)
        bl.addStretch(1)
        self.dev_sum = QLabel("")
        self.dev_sum.setObjectName("PageSub")
        bl.addWidget(self.dev_sum)
        v.addLayout(bl)
        self._sel()
        return w

    def _picked(self):
        rows = sorted({i.row() for i in self.t.selectedIndexes()})
        return [self.devices[r] for r in rows if 0 <= r < len(self.devices)]

    def _current(self):
        p = self._picked()
        return p[0] if len(p) == 1 else None

    def _sel(self):
        p = self._picked()
        d = p[0] if len(p) == 1 else None
        self.b_code.setEnabled(bool(d))
        self.b_edit.setEnabled(bool(d))
        self.b_off.setEnabled(bool(d))
        self.b_off.setText(tr("启用") if d and not d.get("active") else tr("停用"))
        stopped = bool(p) and all(not x.get("active") for x in p)
        self.b_del.setEnabled(stopped)
        self.b_del.setToolTip(tr("永久删除选中的扫码枪账号（可以按住 Ctrl / Shift 一次选几台）") if stopped
                              else tr("只能删除已停用的扫码枪：先选中点「停用」，再删除"))

    def load(self, quiet=False):
        """读扫码枪列表；quiet = 不弹进度窗口（刷新按钮 / 自动刷新在后台读）"""
        if quiet:
            self._poll_now(force=True)
            return
        res, err = _busy(self, tr("正在读取扫码枪…"), lambda: SC.devices(self.settings))
        self._polled(res, err)

    # 后台自动刷新：有扫码枪等配对 / 配对码窗口开着时 8 秒一次，不然 1 分钟一次（只改文字，不重建窗口）
    POLL_FAST, POLL_SLOW = 8000, 60000

    def _start_poll(self):
        from PySide6.QtCore import QObject, QTimer, Signal

        class _Bridge(QObject):
            done = Signal(object, object)
        self._bridge = _Bridge(self)
        self._bridge.done.connect(self._polled)
        self._polling, self._code_box, self._last_poll = False, None, 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        import time
        if not self.isVisible() or self._polling:
            return
        fast = SC.waiting(self.devices) > 0 or self._code_box is not None
        if self.tabs.currentIndex() != 0 and self._code_box is None:
            return
        if (time.monotonic() - self._last_poll) * 1000 >= (self.POLL_FAST if fast else self.POLL_SLOW):
            self._poll_now()

    def _poll_now(self, force=False):
        import threading
        import time
        if self._polling and not force:
            return
        self._polling = True
        self._last_poll = time.monotonic()
        self.b_ref.setEnabled(False)
        self.ref_lbl.setText(tr("正在刷新…"))

        def work():
            try:
                self._bridge.done.emit(SC.devices(self.settings), None)
            except Exception as e:          # noqa: BLE001 — 连不上平台只在标签上写，不弹窗
                self._bridge.done.emit(None, e)
        threading.Thread(target=work, daemon=True).start()

    def _polled(self, res, err):
        import time
        self._polling = False
        self._last_poll = time.monotonic()
        if hasattr(self, "b_ref"):
            self.b_ref.setEnabled(True)
        if err:
            self.ref_lbl.setText("")
            self.cat_lbl.setText("⚠ " + str(err))
            self.cat_lbl.setStyleSheet(f"color:{theme.T('err')};")
            return
        devs, cat = res
        before = {d.get("id"): d for d in self.devices}
        newly = [d for d in devs if d.get("paired") and d.get("active")
                 and before.get(d.get("id")) and not before[d.get("id")].get("paired")]
        self.cat_lbl.setStyleSheet("")
        self.cat_lbl.setText(tr("目录：{0} 发布 · {1} 个商品 · {2}", _when(cat["createdAt"]), cat["itemCount"], cat["createdBy"])
                             if cat else tr("目录还没发布过"))
        self.ref_lbl.setText(tr("{0} 刷新", datetime.now().strftime("%H:%M:%S")))
        self._fill_devices(SC.sort_devices(devs))
        box = self._code_box
        for d in newly:
            if box and box[0] == d.get("id"):
                box[1].setText(tr("✓ 已配对，可以关掉这个窗口了"))
                box[1].setStyleSheet(f"color:{theme.T('ok')};font-weight:600;")
            self.win.show_toast(tr("✓ {0} 已配对", d.get("name") or ""))

    def _fill_devices(self, devs):
        keep = {d.get("id") for d in self._picked()} if self.devices else set()
        self.devices = devs
        self.t.blockSignals(True)
        self.t.setRowCount(len(devs))
        sel = self.t.selectionModel()
        sel.clearSelection()
        for k, d in enumerate(devs):
            if not d.get("active"):
                st, tok = tr("已停用"), "muted"
            elif d.get("paired"):
                st, tok = tr("已配对"), "ok"
            else:
                st, tok = tr("等待配对（{0} 前）", _when(d.get("pairExpiresAt"))), "warn"
            vals = [d.get("name") or "", STORE_NAMES.get(d.get("entityCode"), ""), st, _when(d.get("lastSeenAt"))]
            for c, val in enumerate(vals):
                it = self.t.item(k, c)
                if it is None:
                    it = QTableWidgetItem()
                    self.t.setItem(k, c, it)
                it.setText(val)
                if c == 2:
                    it.setForeground(QColor(theme.T(tok)))
                elif not d.get("active"):
                    it.setForeground(QColor(theme.T("muted")))
                else:
                    it.setData(Qt.ForegroundRole, None)
            if d.get("id") in keep:
                sel.select(self.t.model().index(k, 0), sel.SelectionFlag.Select | sel.SelectionFlag.Rows)
        self.t.blockSignals(False)
        n_on = sum(1 for d in devs if d.get("active") and d.get("paired"))
        n_wait = SC.waiting(devs)
        n_off = sum(1 for d in devs if not d.get("active"))
        parts = [tr("{0} 台在用", n_on)]
        if n_wait:
            parts.append(tr("{0} 台等待配对", n_wait))
        if n_off:
            parts.append(tr("{0} 台已停用", n_off))
        self.dev_sum.setText(" · ".join(parts))
        self._sel()

    def _show_code(self, device, code):
        d = QDialog(self)
        d.setWindowTitle(tr("配对码"))
        lay = QVBoxLayout(d)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)
        lay.addWidget(QLabel(tr("{0} 的配对码", device.get("name") or "")))
        lab = QLabel(SC.pretty_code(code))
        f = QFont(theme.NUM_FONT if hasattr(theme, "NUM_FONT") else "Consolas")
        f.setPointSize(30)
        f.setBold(True)
        lab.setFont(f)
        lab.setAlignment(Qt.AlignCenter)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(lab)
        from erp_widgets import tip_label
        lay.addWidget(tip_label(tr("{0} 前有效 · 只能用一次", _when(device.get("pairExpiresAt"))),
                                tr("扫码枪上：浏览器打开 {0}/app 下载安装 → 打开 CASA&TE Scanner → 输入这个码 → Collega。"
                                   "这个码只显示这一次，关掉窗口后看不到了（可以再点「重新配对」）。", BA.base_url(self.settings))))
        state = QLabel(tr("等待扫码枪输入配对码…"))
        state.setObjectName("PageSub")
        state.setAlignment(Qt.AlignCenter)
        state.setToolTip(tr("窗口开着时每 {0} 秒查一次，配对好了这里会变成绿色", self.POLL_FAST // 1000))
        lay.addWidget(state)
        ok = QPushButton(tr("关闭"))
        ok.setObjectName("Primary")
        ok.clicked.connect(d.accept)
        lay.addWidget(ok)
        self._code_box = (device.get("id"), state)
        try:
            d.exec()
        finally:
            self._code_box = None

    def _store_box(self, code=None):
        store = QComboBox()
        for c, n in ((1, "FOIANO"), (2, "MARLIA"), (3, "S.FILIPPO"), (4, "S.MARCO"), (5, "S.CONCORDIO"), (0, "BMS")):
            store.addItem(n, c)
        if code is not None and store.findData(code) >= 0:
            store.setCurrentIndex(store.findData(code))
        return store

    def _name_dialog(self, title, ok_text, name0="", code0=None):
        """名称 + 门店小窗口 → (名称, 门店号) 或 None"""
        d = QDialog(self)
        d.setWindowTitle(title)
        g = QGridLayout(d)
        g.setContentsMargins(20, 18, 20, 16)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(10)
        name = QLineEdit(name0)
        name.setMaxLength(60)
        name.setPlaceholderText(tr("例如 TC22 FOIANO 1"))
        name.setMinimumWidth(260)
        store = self._store_box(code0)
        g.addWidget(QLabel(tr("名称")), 0, 0)
        g.addWidget(name, 0, 1)
        g.addWidget(QLabel(tr("门店")), 1, 0)
        g.addWidget(store, 1, 1)
        b = QHBoxLayout()
        b.addStretch(1)
        c = QPushButton(tr("取消"))
        c.clicked.connect(d.reject)
        ok = QPushButton(ok_text)
        ok.setObjectName("Primary")
        ok.clicked.connect(lambda: d.accept() if name.text().strip() else name.setFocus())
        b.addWidget(c)
        b.addWidget(ok)
        g.addLayout(b, 2, 0, 1, 2)
        if not name0:
            store.currentIndexChanged.connect(lambda _i: name.setText(f"TC22 {store.currentText()}"))
            name.setText(f"TC22 {store.currentText()}")
        name.selectAll()
        if d.exec() != QDialog.Accepted:
            return None
        return name.text().strip(), store.currentData()

    @staticmethod
    def _store_arg(code):
        return "BMS" if code == 0 else str(code)

    def _api_error(self, title, err):
        msg = str(err)
        if getattr(err, "code", "") == "VALIDATION" or "VALIDATION" in msg:
            msg = tr("预约平台还不能做这个操作：要先在服务器上更新预约平台（合并「改名 / 删除扫码枪」那次更新，再跑一次 deploy/update.sh）。")
        QMessageBox.warning(self, title, msg)

    def add(self):
        got = self._name_dialog(tr("新增扫码枪"), tr("新增"))
        if not got:
            return
        nm, sid = got
        res, err = _busy(self, tr("正在新增扫码枪…"), lambda: SC.add_device(self.settings, nm, self._store_arg(sid)))
        if err:
            QMessageBox.warning(self, tr("新增扫码枪"), str(err))
            return
        self.load()
        self._show_code(*res)

    def edit(self):
        d = self._current()
        if not d:
            return
        got = self._name_dialog(tr("编辑扫码枪"), tr("保存"), d.get("name") or "", d.get("entityCode"))
        if not got:
            return
        nm, sid = got
        old_nm, old_sid = d.get("name") or "", d.get("entityCode")
        if nm == old_nm and sid == old_sid:
            return
        _r, err = _busy(self, tr("正在保存…"), lambda: SC.update_device(
            self.settings, d["id"], nm, self._store_arg(sid) if sid != old_sid else None))
        if err:
            self._api_error(tr("编辑扫码枪"), err)
            return
        self.load(quiet=True)

        def undo():
            _u, e = _busy(self, tr("正在撤销…"), lambda: SC.update_device(
                self.settings, d["id"], old_nm, self._store_arg(old_sid) if sid != old_sid and old_sid is not None else None))
            if e:
                QMessageBox.warning(self, tr("编辑扫码枪"), str(e))
            if self.isVisible():
                self.load(quiet=True)
        txt = tr("✓ 扫码枪已改名：{0} → {1}", old_nm, nm) if nm != old_nm else tr("✓ {0} 已改到 {1}", nm, STORE_NAMES.get(sid, ""))
        if nm != old_nm and sid != old_sid:
            txt += " · " + STORE_NAMES.get(sid, "")
        self.win.show_toast(txt, 6000, action=(tr("撤销"), undo))

    def delete(self):
        p = [d for d in self._picked() if not d.get("active")]
        if not p:
            return
        names = "\n".join("· " + (d.get("name") or "") + " · " + STORE_NAMES.get(d.get("entityCode"), "") for d in p[:15])
        if len(p) > 15:
            names += "\n" + tr("…还有 {0} 台", len(p) - 15)
        if QMessageBox.question(self, tr("删除"), tr(
                "永久删除这 {0} 台已停用的扫码枪？\n\n{1}\n\n删除后不能撤销；以后要再用这台扫码枪，点「新增扫码枪」重新配对。"
                "门店已经交过的清单、库存报错不受影响。", len(p), names)) != QMessageBox.Yes:
            return
        done = []

        def work():
            for d in p:
                SC.delete_device(self.settings, d["id"])
                done.append(d)
        _r, err = _busy(self, tr("正在删除…"), work)
        if err:
            self._api_error(tr("删除"), err)
        if done:
            self.win.show_toast(tr("✓ 已删除 {0} 台扫码枪", len(done)))
        self.load(quiet=True)

    def renew(self):
        d = self._current()
        if not d:
            return
        if d.get("paired") and QMessageBox.question(
                self, tr("重新配对"), tr("{0} 现在的配对会失效（那台扫码枪马上不能用），生成一个新的配对码。继续吗？", d.get("name"))) != QMessageBox.Yes:
            return
        res, err = _busy(self, tr("正在生成配对码…"), lambda: SC.renew_code(self.settings, d["id"]))
        if err:
            QMessageBox.warning(self, tr("重新配对"), str(err))
            return
        self.load()
        self._show_code(*res)

    def toggle(self):
        d = self._current()
        if not d:
            return
        on = not d.get("active")
        if not on and QMessageBox.question(
                self, tr("停用"), tr("停用 {0}：这台扫码枪马上不能用，要再用得重新配对。继续吗？", d.get("name"))) != QMessageBox.Yes:
            return
        _r, err = _busy(self, tr("正在保存…"), lambda: SC.set_active(self.settings, d["id"], on))
        if err:
            QMessageBox.warning(self, tr("扫码枪"), str(err))
        self.load()

    def publish(self):
        from erp_widgets import TaskDialog, excel_call
        me = (self.settings.get("tracking") or {}).get("me") or self.settings.get("user_name") or ""
        logs = []

        def work():
            meta, rows, quotes = excel_call(lambda: SC.build_from_workspace(self.settings, log=lambda *_: None, with_quotes=True))
            stores, ctx = SC.cached_store_data(self.settings, rows, quotes, log=logs.append, force=True, with_ctx=True)
            if ctx:
                meta = dict(meta, advice=ctx)
            return SC.publish(self.settings, meta, rows, me or "?", stores=stores), len(rows), len(stores)
        res, err = _busy(self, tr("正在发布目录（读 Griglia、这周报价，查五家门店的库存 / 销量 / 价格，要几分钟）…"), work)
        for m in logs:
            self.win.log(m)
        if err:
            import reorder_service as R
            txt = R.db_error_text(err)
            hint = ""
            if "数据库" in txt or "MySQL" in txt:
                blocked, _e = _busy(self, tr("正在检查数据库连接…"),
                                    lambda: R.big_query_blocked(R.make_db(self.settings.get("db") or None)))
                hint = "\n\n" + (tr(R.BIG_QUERY_HINT) if blocked else tr("多半是数据库连接断了一下，过一会再点一次「发布目录」"))
            QMessageBox.warning(self, tr("发布目录"), tr("没发布：{0}", txt) + hint)
            return
        self.win.show_toast(tr("✓ 扫码枪目录已发布：{0} 个商品，{1} 家门店的数据", res[1], res[2]))
        self.load()

    # ------------------------------------------------------------------ 门店清单
    def _lists_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(10)
        top = QHBoxLayout()
        from erp_widgets import tip_label
        top.addWidget(tip_label(tr("每周截止"), tr("门店在扫码枪上做这一周的 DETERSIVO 清单，截止之前随时能改、能再提交；截止以后扫的商品进下一周的清单。"
                                              "一家店几台扫码枪共用一张清单。时间是意大利时间。")))
        self.dl_day = QComboBox()
        for i, n in enumerate(SC.WEEKDAYS, 1):
            self.dl_day.addItem(tr(n), i)
        self.dl_time = QTimeEdit()
        self.dl_time.setDisplayFormat("HH:mm")
        self.dl_save = QPushButton(tr("保存"))
        self.dl_save.clicked.connect(self.save_rule)
        for x in (self.dl_day, self.dl_time, self.dl_save):
            top.addWidget(x)
        top.addStretch(1)
        self.lists_lbl = QLabel("")
        self.lists_lbl.setObjectName("PageSub")
        top.addWidget(self.lists_lbl)
        v.addLayout(top)
        self.lt = self._table(self.LCOLS, (110, 80, 170, 56, 56, 56, 110))
        for c in (3, 4, 5):
            self.lt.horizontalHeaderItem(c).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lt.itemSelectionChanged.connect(self._lsel)
        self.lt.doubleClicked.connect(lambda _i: self.show_lines())
        v.addWidget(self.lt, 1)
        bl = QHBoxLayout()
        self.b_import = QPushButton(tr("放进门店清单"))
        self.b_import.setObjectName("Primary")
        self.b_import.setToolTip(tr("选中的清单（没选 = 所有已提交、还没放进来的）写成 Excel 放进 DETERSIVO 工作文件夹「1 门店清单」，"
                                    "同一家店原来的清单挪进 _旧文件；之后照常「开始检查」"))
        self.b_import.clicked.connect(self.import_selected)
        self.b_lines = QPushButton(tr("查看清单明细"))
        self.b_lines.clicked.connect(self.show_lines)
        b_ref = QPushButton(tr("刷新"))
        b_ref.clicked.connect(self.load_lists)
        for b in (self.b_import, self.b_lines, b_ref):
            bl.addWidget(b)
        bl.addStretch(1)
        v.addLayout(bl)
        self._lsel()
        return w

    def load_lists(self):
        res, err = _busy(self, tr("正在读取门店清单…"), lambda: SC.store_lists(self.settings))
        if err:
            self.lists_lbl.setText("⚠ " + str(err))
            self.lists_lbl.setStyleSheet(f"color:{theme.T('err')};")
            return
        self.lists_lbl.setStyleSheet("")
        rule = res.get("rule") or {}
        self.dl_day.setCurrentIndex(max(0, int(rule.get("day") or 7) - 1))
        hh, mm = (str(rule.get("time") or "10:00") + ":00").split(":")[:2]
        self.dl_time.setTime(QTime(int(hh), int(mm)))
        self.lists = res.get("lists") or []
        self.lists_lbl.setText(tr("这一周截止 {0}", _when(res.get("deadline"))))
        self.lt.setRowCount(len(self.lists))
        for k, l in enumerate(self.lists):
            lines = [x for x in l.get("lines") or [] if int(x.get("boxes") or 0) > 0]
            st, tok = list_state(l)
            vals = [STORE_NAMES.get(l.get("entityCode"), str(l.get("entityCode"))), _day(l.get("week")), st, len(lines),
                    sum(int(x["boxes"]) for x in lines), sum(1 for x in lines if x.get("kind") == "urgent"),
                    _when(l.get("submittedAt")), l.get("submittedBy") or ""]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(str(val) if not isinstance(val, int) else f"{val:,}")
                if isinstance(val, int):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 2:
                    it.setForeground(QColor(theme.T(tok)))
                if c == 5 and val:
                    it.setForeground(QColor(theme.T("err")))
                    it.setToolTip(tr("不在 Griglia、门店说等不及直厂、要从批发商订的商品（扫码枪上叫 Non in Griglia，门店要写原因）：检查清单里会写「非 Griglia · 门店要订」和原因"))
                self.lt.setItem(k, c, it)
        self._lsel()

    def _lsel(self):
        rows = sorted({i.row() for i in self.lt.selectedIndexes()})
        self.b_lines.setEnabled(len(rows) == 1)

    def _picked_lists(self):
        rows = sorted({i.row() for i in self.lt.selectedIndexes()})
        if rows:
            return [self.lists[r] for r in rows if 0 <= r < len(self.lists)]
        return [l for l in self.lists if l.get("submittedAt") and not l.get("receivedAt")]

    def save_rule(self):
        day = self.dl_day.currentData()
        t = self.dl_time.time().toString("HH:mm")
        if QMessageBox.question(self, tr("每周截止"), tr("门店清单每周截止改成 {0} {1}（意大利时间）：五家店的扫码枪马上按新的时间算。继续吗？",
                                                       self.dl_day.currentText(), t)) != QMessageBox.Yes:
            return
        _r, err = _busy(self, tr("正在保存…"), lambda: SC.set_deadline_rule(self.settings, day, t))
        if err:
            QMessageBox.warning(self, tr("每周截止"), str(err))
            return
        self.win.show_toast(tr("✓ 门店清单每周截止：{0} {1}", self.dl_day.currentText(), t))
        self.load_lists()

    def show_lines(self):
        rows = sorted({i.row() for i in self.lt.selectedIndexes()})
        if len(rows) != 1:
            return
        l = self.lists[rows[0]]
        d = QDialog(self)
        d.setWindowTitle(tr("{0} · {1} 的清单", STORE_NAMES.get(l.get("entityCode"), ""), _day(l.get("week"))))
        _maximizable(d)
        d.resize(860, 560)
        v = QVBoxLayout(d)
        cols = ("条码", "商品", "箱数", "类型", "原因 / 原商品", "扫码枪")
        t = self._table(cols, (130, 260, 56, 80, 200))
        kinds = {"griglia": "Griglia", "sub": tr("替代商品"), "urgent": tr("非 Griglia 商品"), "remind": tr("回来了提醒"), "other": ""}
        lines = l.get("lines") or []
        t.setRowCount(len(lines))
        for k, x in enumerate(lines):
            why = x.get("reason") or ""
            if x.get("kind") == "sub":
                why = f"{x.get('origEan') or ''} {x.get('origName') or ''}".strip()
            vals = [x.get("ean") or "", x.get("name") or "", int(x.get("boxes") or 0), kinds.get(x.get("kind"), ""), why,
                    x.get("updatedBy") or ""]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(str(val))
                if c == 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if x.get("kind") == "urgent" and c in (3, 4):
                    it.setForeground(QColor(theme.T("err")))
                t.setItem(k, c, it)
        import product_images as PI
        PI.add_thumbs(t, 1, 0, self.settings)
        v.addWidget(t, 1)
        ok = QPushButton(tr("关闭"))
        ok.clicked.connect(d.accept)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(ok)
        v.addLayout(h)
        d.exec()

    def import_selected(self):
        import workspace as W
        root = self.settings.get("wholesale_root") or ""
        if not root or not os.path.isdir(root):
            QMessageBox.warning(self, tr("放进门店清单"), tr("DETERSIVO 工作文件夹找不到（DETERSIVO采购 → 收集文件 → 工作文件夹）"))
            return
        picked = [l for l in self._picked_lists() if l.get("entityCode") in W.STORES
                  and any(int(x.get("boxes") or 0) > 0 for x in l.get("lines") or [])]
        if not picked:
            QMessageBox.information(self, tr("放进门店清单"), tr("没有要放进来的清单（门店还没提交，或者清单是空的）。"))
            return
        unsent = [STORE_NAMES[l["entityCode"]] for l in picked if not l.get("submittedAt")]
        names = "、".join(STORE_NAMES[l["entityCode"]] for l in picked)
        msg = tr("把 {0} 的扫码枪清单写成 Excel 放进「1 门店清单」：同一家店原来的清单挪进 _旧文件，平台上记成已收到（门店扫码枪上看得到）。", names)
        if unsent:
            msg += "\n\n⚠ " + tr("{0} 还没提交（门店可能还在做）。", "、".join(unsent))
        if QMessageBox.question(self, tr("放进门店清单"), msg + "\n\n" + tr("继续吗？")) != QMessageBox.Yes:
            return
        logs = []
        res, err = _busy(self, tr("正在放进门店清单…"), lambda: SC.import_lists(self.settings, root, picked, log=logs.append))
        for m in logs:
            self.win.log(m)
        if err:
            QMessageBox.warning(self, tr("放进门店清单"), str(err))
            return
        done = res or []

        def undo():
            """撤销：删掉放进来的文件，原来的清单从 _旧文件 拿回来（平台上的「已收到」不改）"""
            for _sid, dst, moved in done:
                try:
                    if os.path.exists(dst):
                        os.remove(dst)
                    for orig, arch in moved:
                        if os.path.exists(arch) and not os.path.exists(orig):
                            shutil.move(arch, orig)
                except OSError as e:
                    self.win.log(tr("⚠ 撤销没完成：{0}", e))
            if hasattr(self.win, "refresh_ws"):
                self.win.refresh_ws()
            self.win.show_toast(tr("已撤销：门店清单换回原来的"))
        if hasattr(self.win, "refresh_ws"):
            self.win.refresh_ws()
        self.win.show_toast(tr("✓ {0} 家门店的扫码枪清单已放进「1 门店清单」", len(done)), 6000, action=(tr("撤销"), undo))
        self.load_lists()

    # ------------------------------------------------------------------ 库存报错
    RCOLS = ("门店", "商品", "条码", "系统", "实际", "差", "清点人", "报告时间", "状态")
    STATUS = {"open": ("待处理", "warn"), "fixed": ("已在 ERP 改好", "ok"), "rejected": ("已驳回", "muted")}

    def _reports_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(10)
        top = QHBoxLayout()
        from erp_widgets import tip_label
        top.addWidget(tip_label(tr("门店报的库存错误"), tr(
            "门店在扫码枪上清点（货架和仓库都看过、写了谁点的、按住按钮确认）后报的实际件数。程序不改数据库："
            "请在 ERP 里把库存改好，再点「已在 ERP 改好」；数不对就「驳回」并写原因，门店扫码枪上都看得到。")))
        top.addStretch(1)
        self.rep_view = QComboBox()
        self.rep_view.addItem(tr("待处理"), "open")
        self.rep_view.addItem(tr("全部（近 90 天）"), "all")
        self.rep_view.currentIndexChanged.connect(lambda _i: self.load_reports())
        top.addWidget(self.rep_view)
        self.rep_lbl = QLabel("")
        self.rep_lbl.setObjectName("PageSub")
        top.addWidget(self.rep_lbl)
        v.addLayout(top)
        self.rt = self._table(self.RCOLS, (100, 230, 120, 56, 56, 56, 100, 100))
        for c in (3, 4, 5):
            self.rt.horizontalHeaderItem(c).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        import product_images as PI
        PI.add_thumbs(self.rt, 1, 2, self.settings)
        self.rt.itemSelectionChanged.connect(self._rsel)
        v.addWidget(self.rt, 1)
        bl = QHBoxLayout()
        self.b_fixed = QPushButton(tr("已在 ERP 改好"))
        self.b_fixed.setObjectName("Primary")
        self.b_fixed.setToolTip(tr("ERP 里已经把库存改成门店点的数：报告关掉，门店扫码枪上显示「已更正」"))
        self.b_fixed.clicked.connect(lambda: self.handle_reports("fixed"))
        self.b_reject = QPushButton(tr("驳回"))
        self.b_reject.setToolTip(tr("门店点的数不对（例如仓库里还有）：写原因，门店扫码枪上看得到，可以重新清点"))
        self.b_reject.clicked.connect(lambda: self.handle_reports("rejected"))
        b_ref = QPushButton(tr("刷新"))
        b_ref.clicked.connect(self.load_reports)
        for b in (self.b_fixed, self.b_reject, b_ref):
            bl.addWidget(b)
        bl.addStretch(1)
        v.addLayout(bl)
        self._rsel()
        return w

    def load_reports(self):
        status = self.rep_view.currentData() if hasattr(self, "rep_view") else "open"
        res, err = _busy(self, tr("正在读取库存报错…"), lambda: SC.stock_reports(self.settings, status))
        if err:
            self.rep_lbl.setText("⚠ " + str(err))
            self.rep_lbl.setStyleSheet(f"color:{theme.T('err')};")
            return
        self.rep_lbl.setStyleSheet("")
        self.reports = res or []
        n_open = sum(1 for r in self.reports if r.get("status") == "open")
        self.rep_lbl.setText(tr("{0} 条待处理", n_open) if status == "open" else tr("{0} 条 · 待处理 {1}", len(self.reports), n_open))
        self.rt.setRowCount(len(self.reports))
        for k, r in enumerate(self.reports):
            sysq, cnt = r.get("systemQty"), r.get("counted")
            diff = (cnt - sysq) if (sysq is not None and cnt is not None) else None
            st, tok = self.STATUS.get(r.get("status"), (r.get("status") or "", "muted"))
            vals = [STORE_NAMES.get(r.get("entityCode"), str(r.get("entityCode"))), r.get("name") or "", r.get("ean") or "",
                    "" if sysq is None else f"{sysq:g}", f"{cnt}", "" if diff is None else f"{diff:+g}",
                    r.get("countedBy") or "", _when(r.get("createdAt")), tr(st)]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c in (3, 4, 5):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 5 and diff:
                    it.setForeground(QColor(theme.T("err" if diff < 0 else "accent")))
                if c == 8:
                    it.setForeground(QColor(theme.T(tok)))
                    tip = []
                    if r.get("answer"):
                        tip.append(tr("原因：{0}", r["answer"]))
                    if r.get("handledBy") or r.get("handledAt"):
                        tip.append(tr("{0} · {1}", r.get("handledBy") or "", _when(r.get("handledAt"))))
                    it.setToolTip("\n".join(tip))
                if c == 6 and r.get("device"):
                    it.setToolTip(r["device"])
                self.rt.setItem(k, c, it)
        self._rsel()

    def _picked_reports(self):
        rows = sorted({i.row() for i in self.rt.selectedIndexes()})
        return [self.reports[r] for r in rows if 0 <= r < len(self.reports) and self.reports[r].get("status") == "open"]

    def _rsel(self):
        n = len(self._picked_reports()) if hasattr(self, "rt") else 0
        for b in (self.b_fixed, self.b_reject):
            b.setEnabled(n > 0)

    def handle_reports(self, action):
        picked = self._picked_reports()
        if not picked:
            return
        lines = "\n".join(f"· {STORE_NAMES.get(r.get('entityCode'), '')} · {(r.get('name') or r.get('ean') or '')[:40]}："
                          f"{r.get('systemQty')} → {r.get('counted')}" for r in picked[:12])
        if len(picked) > 12:
            lines += "\n" + tr("…还有 {0} 条", len(picked) - 12)
        answer = None
        if action == "fixed":
            if QMessageBox.question(self, tr("已在 ERP 改好"), tr(
                    "确认 ERP 里已经把这 {0} 个商品的库存改成门店点的数？\n\n{1}\n\n报告会关掉，门店扫码枪上显示「已更正」。程序不改数据库。",
                    len(picked), lines)) != QMessageBox.Yes:
                return
        else:
            from PySide6.QtWidgets import QInputDialog
            answer, ok = QInputDialog.getText(self, tr("驳回"), tr("驳回原因（门店扫码枪上看得到）：") + "\n\n" + lines)
            answer = (answer or "").strip()
            if not ok or not answer:
                return
        me = (self.settings.get("tracking") or {}).get("me") or self.settings.get("user_name") or ""

        def work():
            for r in picked:
                SC.handle_stock_report(self.settings, r["id"], action, answer, me)
        _r, err = _busy(self, tr("正在保存…"), work)
        if err:
            QMessageBox.warning(self, tr("库存报错"), str(err))
            self.load_reports()
            return

        def undo():
            _u, e = _busy(self, tr("正在撤销…"), lambda: [SC.handle_stock_report(self.settings, r["id"], "reopen") for r in picked])
            if e:
                QMessageBox.warning(self, tr("库存报错"), str(e))
            self.win.show_toast(tr("已撤销：{0} 条库存报错回到待处理", len(picked)))
            if self.isVisible():
                self.load_reports()
        self.win.show_toast(tr("✓ {0} 条库存报错：{1}", len(picked), tr("已在 ERP 改好") if action == "fixed" else tr("已驳回")),
                            6000, action=(tr("撤销"), undo))
        self.load_reports()
