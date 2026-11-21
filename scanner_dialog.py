"""
系统设置 → 数据连接 → 扫码枪（2026-11-21 用户：门店 Zebra TC22 扫 DETERSIVO 商品，一台扫码枪一个账号）。
业务在 scanner_catalog.py（账号、目录）；这里只是界面。账号和目录都存在预约平台那台服务器上，要先连好预约平台。
"""
from datetime import datetime

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
                               QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox)

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


def _busy(parent, text, fn):
    from erp_widgets import BusyDialog
    return BusyDialog.run(parent, tr("扫码枪"), text, fn)


class ScannerDialog(QDialog):
    COLS = ("名称", "门店", "状态", "最后在线")

    def __init__(self, win, settings):
        super().__init__(win)
        self.win, self.settings = win, settings
        self.devices = []
        self.setWindowTitle(tr("扫码枪"))
        self.resize(720, 460)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)
        head = QHBoxLayout()
        from erp_widgets import tip_label
        t = tip_label(tr("门店扫码枪"), tr("门店用 Zebra 扫码枪扫要订的 DETERSIVO 商品，当场看到：在 Griglia、这周能订 / 换成替代商品 / "
                                        "这周批发商都没货 / 不在 Griglia。一台扫码枪一个账号：新增后在扫码枪上输入配对码。"))
        t.setObjectName("SectionTitle")
        head.addWidget(t)
        head.addStretch()
        b = QPushButton(tr("下载扫码程序"))
        b.setToolTip(tr("在扫码枪的浏览器里打开 {0}/app 下载安装（安卓程序）", BA.base_url(settings)))
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(BA.base_url(self.settings) + "/app")))
        head.addWidget(b)
        v.addLayout(head)

        cat = QHBoxLayout()
        self.cat_lbl = QLabel("")
        self.cat_lbl.setObjectName("PageSub")
        cat.addWidget(self.cat_lbl, 1)
        b = QPushButton(tr("发布目录"))
        b.setToolTip(tr("把现在的 Griglia、这周批发商报价、替代商品、直厂商品发到扫码枪（Griglia 页刷新时也会自动发布）"))
        b.clicked.connect(self.publish)
        cat.addWidget(b)
        v.addLayout(cat)

        self.t = QTableWidget(0, len(self.COLS))
        self.t.setHorizontalHeaderLabels([tr(c) for c in self.COLS])
        self.t.verticalHeader().setVisible(False)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.t.setSelectionMode(QAbstractItemView.SingleSelection)
        self.t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        for i, w in enumerate((200, 110, 200)):
            self.t.setColumnWidth(i, w)
        hh.setStretchLastSection(True)
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
        c = QPushButton(tr("关闭"))
        c.clicked.connect(self.accept)
        bl.addWidget(c)
        v.addLayout(bl)
        self._sel()
        self.load()

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
                    from PySide6.QtGui import QColor
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
        from erp_widgets import excel_call
        me = (self.settings.get("tracking") or {}).get("me") or self.settings.get("user_name") or ""

        def work():
            meta, rows = excel_call(lambda: SC.build_from_workspace(self.settings, log=lambda *_: None))
            return SC.publish(self.settings, meta, rows, me or "?"), len(rows)
        res, err = _busy(self, tr("正在生成并发布目录（读 Griglia 和这周报价）…"), work)
        if err:
            QMessageBox.warning(self, tr("发布目录"), str(err))
            return
        self.win.show_toast(tr("✓ 扫码枪目录已发布：{0} 个商品", res[1]))
        self.load()
