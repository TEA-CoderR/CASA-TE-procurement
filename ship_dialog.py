"""
登记发货 + 预约卸货时间（2026-11-21 用户：在程序里就能选卸货时间，不用开那么多平台；在「登记发货」窗口做，和预约平台同步）。
业务（和平台对话）在 booking_api.py；这里只是界面。

register(parent, settings, st, r) → (改之前的字段, 改的字段, 新约的预约 或 None) / None（取消）
"""
from datetime import date, timedelta

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox,
                               QLineEdit, QDateEdit, QSpinBox, QButtonGroup, QFrame, QCheckBox, QMessageBox,
                               QScrollArea, QWidget)

from i18n import tr
import booking_api as BA
import theme


def _next_workday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _qd(d):
    return QDate(d.year, d.month, d.day)


class ShipDialog(QDialog):
    COLS = 7

    def __init__(self, parent, settings, r, o, order_no):
        super().__init__(parent)
        self.settings, self.r, self.o, self.order_no = settings or {}, r, o or {}, order_no
        self.setWindowTitle(tr("登记发货"))
        self.resize(720, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        head = QLabel(f"{r.get('supplier', '')} · {r.get('store_name', '')}")
        f = head.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        head.setFont(f)
        lay.addWidget(head)
        sub = QLabel(tr("订单号 {0}", order_no))
        sub.setObjectName("PageSub")
        sub.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(sub)

        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(10)
        self.ship_day = QDateEdit(_qd(date.today()))
        self.ship_day.setDisplayFormat("dd/MM/yyyy")
        self.ship_day.setCalendarPopup(True)
        g.addWidget(QLabel(tr("发货日期")), 0, 0)
        g.addWidget(self.ship_day, 0, 1)
        self.ddt = QLineEdit()
        self.ddt.setPlaceholderText(tr("送货单上的号码"))
        g.addWidget(self._lab(tr("送货单号"), tr("知道就填，承运商预约时也会带上")), 0, 2)
        g.addWidget(self.ddt, 0, 3)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        lay.addLayout(g)

        # ---------------------------------------------------------------- 预约卸货
        sec = QHBoxLayout()
        t = self._lab(tr("预约卸货"), tr("在这里选的时间直接约到预约平台（已确认），仓库看板上马上看得到。\n"
                                       "承运商自己在平台上约过的会显示在这里。没有预约的送货一律拒收。"))
        t.setObjectName("SectionTitle")
        sec.addWidget(t)
        sec.addStretch()
        self.no_book = QCheckBox(tr("暂不预约"))
        self.no_book.setToolTip(tr("只登记发货；承运商之后自己在平台上约，或者再打开这里约"))
        sec.addWidget(self.no_book)
        lay.addLayout(sec)

        self.book_box = QFrame()
        bl = QVBoxLayout(self.book_box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        bk = self.o.get("booking") or {}
        self.has_live = bk.get("status") in BA.LIVE
        self.cur = QLabel(tr("已预约：{0}", BA.text(bk)) if bk else "")
        self.cur.setVisible(bool(bk))
        self.cur.setStyleSheet(f"color:{theme.T('ok') if self.has_live else theme.T('muted')};font-weight:600;")
        bl.addWidget(self.cur)
        self.rebook = QCheckBox(tr("改约别的时间"))
        self.rebook.setToolTip(tr("原来的预约在平台上取消，换成下面选的时间"))
        self.rebook.setVisible(self.has_live)
        bl.addWidget(self.rebook)

        self.form = QFrame()
        fg = QGridLayout(self.form)
        fg.setContentsMargins(0, 0, 0, 0)
        fg.setHorizontalSpacing(12)
        fg.setVerticalSpacing(10)
        exp = self.r.get("expected")
        d0 = exp if isinstance(exp, date) and exp > date.today() else _next_workday(date.today())
        self.day = QDateEdit(_qd(d0))
        self.day.setDisplayFormat("dd/MM/yyyy")
        self.day.setCalendarPopup(True)
        self.day.setMinimumDate(_qd(date.today()))
        self.vehicle = QComboBox()
        for k, name in BA.VEHICLES:
            self.vehicle.addItem(tr(name), k)
        self.vehicle.setCurrentIndex(1)
        self.pallets = QSpinBox()
        self.pallets.setRange(0, 40)
        self.pallets.setSuffix(tr(" 托"))
        self.parcels = QSpinBox()
        self.parcels.setRange(0, 999)
        self.parcels.setSuffix(tr(" 件"))
        self.parcels.setToolTip(tr("快递 / 零担：几件包裹"))
        self.find_btn = QPushButton(tr("查询可约时间"))
        self.find_btn.clicked.connect(self.load_slots)
        fg.addWidget(QLabel(tr("卸货日期")), 0, 0)
        fg.addWidget(self.day, 0, 1)
        fg.addWidget(QLabel(tr("车型")), 0, 2)
        fg.addWidget(self.vehicle, 0, 3)
        fg.addWidget(self.pallets, 0, 4)
        fg.addWidget(self.parcels, 0, 5)
        fg.addWidget(self.find_btn, 0, 6)
        bl.addWidget(self.form)

        self.slot_info = QLabel(tr("选好日期和货量，点「查询可约时间」"))
        self.slot_info.setObjectName("PageSub")
        bl.addWidget(self.slot_info)
        self.slot_area = QScrollArea()
        self.slot_area.setWidgetResizable(True)
        self.slot_area.setFrameShape(QFrame.NoFrame)
        self.slot_area.setFixedHeight(120)
        self.slot_w = QWidget()
        self.slot_grid = QGridLayout(self.slot_w)
        self.slot_grid.setContentsMargins(0, 0, 0, 0)
        self.slot_grid.setSpacing(6)
        self.slot_area.setWidget(self.slot_w)
        self.slot_group = QButtonGroup(self)
        self.slot_group.setExclusive(True)
        bl.addWidget(self.slot_area)

        cg = QGridLayout()
        cg.setHorizontalSpacing(12)
        cg.setVerticalSpacing(10)
        self.carrier = QLineEdit()
        self.carrier.setPlaceholderText(tr("例如 BRT、GLS、供应商自己的车"))
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("+39 …")
        self.email = QLineEdit()
        self.email.setPlaceholderText(tr("可以不填"))
        cg.addWidget(QLabel(tr("承运商")), 0, 0)
        cg.addWidget(self.carrier, 0, 1)
        cg.addWidget(QLabel(tr("司机电话")), 0, 2)
        cg.addWidget(self.phone, 0, 3)
        cg.addWidget(self._lab(tr("承运商邮箱"), tr("填了，预约平台会给承运商发确认信（意大利文）")), 1, 0)
        cg.addWidget(self.email, 1, 1, 1, 3)
        cg.setColumnStretch(1, 1)
        cg.setColumnStretch(3, 1)
        bl.addLayout(cg)
        lay.addWidget(self.book_box)

        self.off = QLabel(tr("预约平台还没连接：系统设置 → 数据连接 → 预约平台（填平台密钥）。现在只能登记发货。"))
        self.off.setWordWrap(True)
        self.off.setStyleSheet(f"color:{theme.T('warn')};")
        lay.addWidget(self.off)
        lay.addStretch(1)

        self.err = QLabel("")
        self.err.setStyleSheet(f"color:{theme.T('err')};")
        lay.addWidget(self.err)
        b = QHBoxLayout()
        b.addStretch(1)
        c = QPushButton(tr("取消"))
        c.clicked.connect(self.reject)
        b.addWidget(c)
        self.ok = QPushButton(tr("登记发货"))
        self.ok.setObjectName("Primary")
        self.ok.clicked.connect(self._accept)
        b.addWidget(self.ok)
        lay.addLayout(b)

        self.online = BA.ready(self.settings) and BA.entity_of(self.o.get("store")) is not None
        self.off.setVisible(not self.online)
        if not self.online and BA.entity_of(self.o.get("store")) is None and BA.ready(self.settings):
            self.off.setText(tr("这张单的门店在预约平台上没有收货点，只能登记发货。"))
        self.no_book.setVisible(self.online)
        self.book_box.setVisible(self.online)
        self.no_book.toggled.connect(self._update)
        self.rebook.toggled.connect(self._update)
        for w in (self.day, self.vehicle, self.pallets, self.parcels):
            sig = w.dateChanged if w is self.day else (w.currentIndexChanged if w is self.vehicle else w.valueChanged)
            sig.connect(self._clear_slots)
        self._update()
        try:
            from erp_widgets import fit_to_screen
            fit_to_screen(self)
        except Exception:
            pass

    @staticmethod
    def _lab(text, tip=""):
        try:
            from erp_widgets import tip_label
            return tip_label(text, tip) if tip else QLabel(text)
        except Exception:
            return QLabel(text)

    # ---------------------------------------------------------------- 交互
    def booking_wanted(self):
        """要在平台上约一个新时间吗"""
        return self.online and not self.no_book.isChecked() and (not self.has_live or self.rebook.isChecked())

    def _update(self):
        want = self.booking_wanted()
        for w in (self.form, self.slot_area, self.slot_info, self.carrier, self.phone, self.email):
            w.setEnabled(want)
        self.ok.setText(tr("登记发货并预约") if want else tr("登记发货"))
        self.err.setText("")

    def _clear_slots(self, *_a):
        for btn in list(self.slot_group.buttons()):
            self.slot_group.removeButton(btn)
            btn.deleteLater()
        self.slot_info.setText(tr("选好日期和货量，点「查询可约时间」"))

    def load_slots(self):
        from erp_widgets import BusyDialog
        self._clear_slots()
        day = self.day.date().toPython()
        args = (self.settings, self.o.get("store"), day, self.vehicle.currentData(), self.pallets.value(),
                self.parcels.value())
        res, err = BusyDialog.run(self, tr("预约卸货"), tr("正在向预约平台查询可约时间…"), lambda: BA.availability(*args))
        if err:
            self.slot_info.setText("⚠ " + str(err))
            return
        dur, slots = res
        if not slots:
            self.slot_info.setText(tr("{0:%d/%m} 没有可约的时间，换一天或换车型", day))
            return
        self.slot_info.setText(tr("{0:%d/%m} 可约 {1} 个时间 · 卸货约 {2} 分钟", day, len(slots), dur))
        for k, t in enumerate(slots):
            btn = QPushButton(t)
            btn.setCheckable(True)
            btn.setObjectName("Chip")
            btn.setCursor(Qt.PointingHandCursor)
            self.slot_group.addButton(btn)
            self.slot_grid.addWidget(btn, k // self.COLS, k % self.COLS)

    def chosen_time(self):
        b = self.slot_group.checkedButton()
        return b.text() if b else None

    def result(self):
        return {"shipped": self.ship_day.date().toPython(), "ddt": self.ddt.text().strip(),
                "book": self.booking_wanted(), "day": self.day.date().toPython(), "time": self.chosen_time(),
                "vehicle": self.vehicle.currentData(), "pallets": self.pallets.value(), "parcels": self.parcels.value(),
                "carrier": self.carrier.text().strip(), "phone": self.phone.text().strip(),
                "email": self.email.text().strip(), "rebook": self.has_live and self.rebook.isChecked()}

    def _accept(self):
        x = self.result()
        if x["book"]:
            if not x["time"]:
                self.err.setText("⚠ " + tr("请选一个卸货时间（或勾「暂不预约」）"))
                return
            if not x["carrier"]:
                self.err.setText("⚠ " + tr("请填承运商"))
                return
            if x["vehicle"] != "parcel" and not x["pallets"] and not x["parcels"]:
                self.err.setText("⚠ " + tr("请填托盘数"))
                return
        self.accept()


def register(parent, settings, st, r, me=""):
    """窗口 → （约时间）→ 写进订单。返回 (改之前的字段, 改的字段, 平台上新约的预约 或 None) / None"""
    import mail_send as MS
    from erp_widgets import BusyDialog
    oid = r["id"]
    o = st.orders.get(oid) or {}
    no = MS.order_no(oid, o)
    d = ShipDialog(parent, settings, r, o, no)
    if d.exec() != QDialog.Accepted:
        return None
    x = d.result()
    f = {"shipped": x["shipped"].isoformat()}
    created = None
    if x["book"]:
        old = (o.get("booking") or {}) if x["rebook"] else {}

        def work():
            if old.get("id"):
                try:
                    BA.set_status(settings, old["id"], "cancelled", me, "Spostata dall'ufficio acquisti")
                except BA.BookingApiError as e:
                    if e.code not in ("INVALID_TRANSITION",):
                        raise
            return BA.create(settings, o.get("store"), x["day"], x["time"], x["vehicle"], x["pallets"], x["parcels"],
                             x["carrier"], me, order_no=no, supplier=r.get("raw_supplier") or r.get("supplier"),
                             phone=x["phone"], email=x["email"], ddt=x["ddt"])
        created, err = BusyDialog.run(parent, tr("预约卸货"), tr("正在预约平台上预约…"), work)
        if err:
            QMessageBox.warning(parent, tr("预约卸货"), tr("没约上：{0}\n发货没有登记，可以换个时间再试。", err))
            return None
        bk = BA.record({**created, "date": x["day"].isoformat(), "carrierName": x["carrier"], "driverPhone": x["phone"],
                        "vehicleType": x["vehicle"], "pallets": x["pallets"], "parcels": x["parcels"]}, by=me)
        f["booking"] = bk
        f["expected"] = bk["date"]
    if x["ddt"]:
        f["ddt"] = x["ddt"]
    before = {k: o.get(k) for k in f}
    st.set(oid, f)
    return before, f, created
