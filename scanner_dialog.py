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
    LCOLS = ("门店", "截止", "状态", "商品", "箱数", "紧急", "提交时间", "提交人")

    def __init__(self, win, settings, tab="devices"):
        super().__init__(win)
        self.win, self.settings = win, settings
        self.devices, self.lists = [], []
        self.setWindowTitle(tr("扫码枪"))
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
        v.addWidget(self.tabs, 1)

        bl = QHBoxLayout()
        bl.addStretch(1)
        c = QPushButton(tr("关闭"))
        c.clicked.connect(self.accept)
        bl.addWidget(c)
        v.addLayout(bl)
        from erp_widgets import fit_to_screen
        fit_to_screen(self)
        self.load()
        if tab == "lists":
            self.tabs.setCurrentIndex(1)
            self.load_lists()
        self.tabs.currentChanged.connect(lambda i: self.load_lists() if i == 1 and not self.lists else None)

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
        b = QPushButton(tr("发布目录"))
        b.setToolTip(tr("把现在的 Griglia、这周批发商报价、替代商品、直厂商品，还有每家门店的库存 / 销量 / 在途 / 建议箱数 / 进价 / 卖价 / "
                        "打折价发到扫码枪（每家店只收到自己的；Griglia 页刷新时也会自动发布，门店数据最多 4 小时重算一次）"))
        b.clicked.connect(self.publish)
        cat.addWidget(b)
        v.addLayout(cat)
        self.t = self._table(self.COLS, (200, 110, 200))
        self.t.setSelectionMode(QAbstractItemView.SingleSelection)
        self.t.itemSelectionChanged.connect(self._sel)
        v.addWidget(self.t, 1)
        bl = QHBoxLayout()
        self.b_add = QPushButton(tr("新增扫码枪"))
        self.b_add.setObjectName("Primary")
        self.b_add.clicked.connect(self.add)
        self.b_code = QPushButton(tr("重新配对"))
        self.b_code.setToolTip(tr("扫码枪丢了 / 重置了：生成新的配对码，原来那台马上不能用"))
        self.b_code.clicked.connect(self.renew)
        self.b_off = QPushButton(tr("停用"))
        self.b_off.clicked.connect(self.toggle)
        for b in (self.b_add, self.b_code, self.b_off):
            bl.addWidget(b)
        bl.addStretch(1)
        v.addLayout(bl)
        self._sel()
        return w

    def _current(self):
        r = self.t.currentRow()
        return self.devices[r] if 0 <= r < len(self.devices) else None

    def _sel(self):
        d = self._current()
        self.b_code.setEnabled(bool(d))
        self.b_off.setEnabled(bool(d))
        self.b_off.setText(tr("启用") if d and not d.get("active") else tr("停用"))

    def load(self):
        res, err = _busy(self, tr("正在读取扫码枪…"), lambda: SC.devices(self.settings))
        if err:
            self.cat_lbl.setText("⚠ " + str(err))
            self.cat_lbl.setStyleSheet(f"color:{theme.T('err')};")
            return
        self.devices, cat = res
        self.cat_lbl.setStyleSheet("")
        self.cat_lbl.setText(tr("目录：{0} 发布 · {1} 个商品 · {2}", _when(cat["createdAt"]), cat["itemCount"], cat["createdBy"])
                             if cat else tr("目录还没发布过"))
        self.t.setRowCount(len(self.devices))
        for k, d in enumerate(self.devices):
            if not d.get("active"):
                st, tok = tr("已停用"), "muted"
            elif d.get("paired"):
                st, tok = tr("已配对"), "ok"
            else:
                st, tok = tr("等待配对（{0} 前）", _when(d.get("pairExpiresAt"))), "warn"
            vals = [d.get("name") or "", STORE_NAMES.get(d.get("entityCode"), ""), st, _when(d.get("lastSeenAt"))]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c == 2:
                    it.setForeground(QColor(theme.T(tok)))
                self.t.setItem(k, c, it)
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
        ok = QPushButton(tr("关闭"))
        ok.setObjectName("Primary")
        ok.clicked.connect(d.accept)
        lay.addWidget(ok)
        d.exec()

    def add(self):
        d = QDialog(self)
        d.setWindowTitle(tr("新增扫码枪"))
        g = QGridLayout(d)
        g.setContentsMargins(20, 18, 20, 16)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(10)
        name = QLineEdit()
        name.setPlaceholderText(tr("例如 TC22 FOIANO 1"))
        store = QComboBox()
        for code, n in ((1, "FOIANO"), (2, "MARLIA"), (3, "S.FILIPPO"), (4, "S.MARCO"), (5, "S.CONCORDIO"), (0, "BMS")):
            store.addItem(n, code)
        g.addWidget(QLabel(tr("名称")), 0, 0)
        g.addWidget(name, 0, 1)
        g.addWidget(QLabel(tr("门店")), 1, 0)
        g.addWidget(store, 1, 1)
        b = QHBoxLayout()
        b.addStretch(1)
        c = QPushButton(tr("取消"))
        c.clicked.connect(d.reject)
        ok = QPushButton(tr("新增"))
        ok.setObjectName("Primary")
        ok.clicked.connect(lambda: d.accept() if name.text().strip() else name.setFocus())
        b.addWidget(c)
        b.addWidget(ok)
        g.addLayout(b, 2, 0, 1, 2)
        store.currentIndexChanged.connect(lambda _i: name.setText(name.text() or f"TC22 {store.currentText()}"))
        name.setText(f"TC22 {store.currentText()}")
        if d.exec() != QDialog.Accepted:
            return
        sid = store.currentData()
        res, err = _busy(self, tr("正在新增扫码枪…"),
                         lambda: SC.add_device(self.settings, name.text().strip(), "BMS" if sid == 0 else str(sid)))
        if err:
            QMessageBox.warning(self, tr("新增扫码枪"), str(err))
            return
        self.load()
        self._show_code(*res)

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
            stores = SC.cached_store_data(self.settings, rows, quotes, log=logs.append, force=True)
            return SC.publish(self.settings, meta, rows, me or "?", stores=stores), len(rows), len(stores)
        res, err = _busy(self, tr("正在发布目录（读 Griglia、这周报价，查五家门店的库存 / 销量 / 价格，要几分钟）…"), work)
        for m in logs:
            self.win.log(m)
        if err:
            QMessageBox.warning(self, tr("发布目录"), str(err))
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
                    it.setToolTip(tr("门店标了紧急的（不在 Griglia、从批发商订）：检查清单里会写「门店紧急」和原因"))
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
        d.resize(760, 520)
        v = QVBoxLayout(d)
        cols = ("条码", "商品", "箱数", "类型", "原因 / 原商品", "扫码枪")
        t = self._table(cols, (130, 260, 56, 80, 200))
        kinds = {"griglia": "Griglia", "sub": tr("替代商品"), "urgent": tr("紧急"), "remind": tr("回来了提醒"), "other": ""}
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
