"""
确认收货窗口（2026-11-21 用户：点确认收货要弹出内容编辑窗口，写明谁卸的货、到了什么；拒收要能选原因）。
业务规则在 receipt.py；这里只是界面。

register(parent, settings, st, r, info=None) → 写好的字段 / None（取消）
  r = 订单跟踪的一行（id / supplier / store_name / date / amount / note…），info = 在途信息（有订单文件就读明细）
"""
import os
import socket
from datetime import datetime

from PySide6.QtCore import Qt, QDateTime, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox,
                               QLineEdit, QPlainTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
                               QDateTimeEdit, QMessageBox, QAbstractItemView)

from i18n import tr
import receipt as RC
import theme

RES_TEXT = {"full": "全部收到", "partial": "部分收到", "refused": "拒收"}


def _n(v):
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return f


def _store(sid):
    try:
        import order_record as REC
        return REC.STORE_NAMES.get(str(sid), str(sid or ""))
    except Exception:
        return str(sid or "")


def _fmt(v):
    return f"{v:g}"


class ReceiptDialog(QDialog):
    def __init__(self, parent, settings, r, lines=None, lines_note=""):
        super().__init__(parent)
        self.settings = settings or {}
        self.r = r
        self.setWindowTitle(tr("确认收货"))
        self.resize(760, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        head = QLabel(f"{r.get('supplier', '')} · {r.get('store_name', '')}")
        head.setObjectName("SectionTitle")
        f = head.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        head.setFont(f)
        lay.addWidget(head)
        sub = [tr("下单 {0}", str(r.get("date") or "")[:10])]
        if r.get("amount"):
            sub.append(f"€ {float(r['amount']):,.2f}")
        last = RC.last(r) if isinstance(r, dict) else None
        if last:
            sub.append(tr("上次登记：{0}", RC.summary(last)))
        s = QLabel(" · ".join(sub))
        s.setObjectName("PageSub")
        lay.addWidget(s)

        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(10)
        # 卸货人（必选）
        self.by = QComboBox()
        self.by.setEditable(True)
        self.by.addItem("")
        for p in RC.people(self.settings):
            self.by.addItem(p)
        lastby = ((self.settings.get("receiving") or {}).get("last_by") or "")
        self.by.setCurrentText(lastby if lastby in RC.people(self.settings) else "")
        self.by.lineEdit().setPlaceholderText(tr("选卸货人"))
        g.addWidget(self._lab(tr("卸货人"), tr("谁卸的货、点的数（必填）。出问题找不到货时按这个找人。名单在系统设置里改。")), 0, 0)
        g.addWidget(self.by, 0, 1)
        self.at = QDateTimeEdit(QDateTime.currentDateTime())
        self.at.setDisplayFormat("dd/MM/yyyy HH:mm")
        self.at.setCalendarPopup(True)
        g.addWidget(self._lab(tr("到货时间")), 0, 2)
        g.addWidget(self.at, 0, 3)
        self.ddt = QLineEdit()
        self.ddt.setPlaceholderText(tr("送货单上的号码"))
        g.addWidget(self._lab(tr("送货单号"), tr("DDT 号码，以后对账、找货用")), 1, 0)
        g.addWidget(self.ddt, 1, 1)
        # 结果
        from page_kit import Seg
        self.res = "full"
        self.seg = Seg([(k, tr(RES_TEXT[k])) for k in RC.RESULTS], "full")
        self.seg.changed.connect(self._set_result)
        g.addWidget(self._lab(tr("收货结果"), tr("全部收到：订单算到货、不再算在途\n部分收到：仍算在途，等补货\n拒收：订单标「卡住了」，等供应商重新送")), 1, 2)
        g.addWidget(self.seg, 1, 3)
        self.reason = QComboBox()
        self.reason.addItem("")
        for x in RC.REFUSE_REASONS:
            self.reason.addItem(tr(x), x)
        self.l_reason = self._lab(tr("拒收原因"))
        g.addWidget(self.l_reason, 2, 0)
        g.addWidget(self.reason, 2, 1, 1, 3)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        lay.addLayout(g)

        # 明细
        self.lines = list(lines or [])
        hl = QHBoxLayout()
        t = QLabel(tr("到货明细"))
        t.setObjectName("SectionTitle")
        hl.addWidget(t)
        self.l_sum = QLabel("")
        self.l_sum.setObjectName("PageSub")
        hl.addWidget(self.l_sum, 1)
        self.b_all = QPushButton(tr("全部按订货数"))
        self.b_all.setObjectName("Link")
        self.b_all.clicked.connect(self._fill_all)
        hl.addWidget(self.b_all)
        lay.addLayout(hl)
        self.t = QTableWidget(0, 5)
        self.t.setHorizontalHeaderLabels([tr("条码"), tr("商品"), tr("门店"), tr("订了（箱）"), tr("收到（箱）")])
        self.t.verticalHeader().setVisible(False)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        self.t.setColumnWidth(0, 130)
        self.t.setColumnWidth(1, 300)
        self.t.setColumnWidth(2, 100)
        self.t.setColumnWidth(3, 80)
        hh.setStretchLastSection(True)
        self._filling = True
        for sid, x in self.lines:
            k = self.t.rowCount()
            self.t.insertRow(k)
            vals = [str(x.get("ean") or ""), str(x.get("desc") or ""), _store(sid), _fmt(_n(x.get("colli"))),
                    _fmt(_n(x.get("colli")))]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c >= 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c != 4:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.t.setItem(k, c, it)
        self._filling = False
        if self.lines:
            try:
                from erp_widgets import StepperDelegate
                StepperDelegate.install(self.t, 4)
            except Exception:
                pass
            self.t.itemChanged.connect(self._edited)
            lay.addWidget(self.t, 1)
        else:
            self.t.setVisible(False)
            self.b_all.setVisible(False)
            no = QLabel(lines_note or tr("没有找到订单文件明细：请在备注里写清楚到了什么、缺了什么。"))
            no.setObjectName("PageSub")
            no.setWordWrap(True)
            lay.addWidget(no)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText(tr("备注（缺了什么、破损情况、司机说了什么…）"))
        self.note.setFixedHeight(70 if self.lines else 120)
        lay.addWidget(self.note)
        self.err = QLabel("")
        self.err.setStyleSheet(f"color:{theme.T('err')};")
        lay.addWidget(self.err)

        bl = QHBoxLayout()
        bl.addStretch(1)
        c = QPushButton(tr("取消"))
        c.clicked.connect(self.reject)
        bl.addWidget(c)
        self.ok = QPushButton(tr("确认收货"))
        self.ok.setObjectName("Primary")
        self.ok.clicked.connect(self._accept)
        bl.addWidget(self.ok)
        lay.addLayout(bl)
        self._set_result("full")
        self._update_sum()
        try:
            from erp_widgets import fit_to_screen
            fit_to_screen(self)
        except Exception:
            pass

    @staticmethod
    def _lab(text, tip=""):
        if tip:
            try:
                from erp_widgets import tip_label
                return tip_label(text, tip)
            except Exception:
                pass
        return QLabel(text)

    # ---------------------------------------------------------------- 交互
    def _set_result(self, k, auto=False):
        self.res = k
        for kk, b in self.seg.btns.items():
            b.setChecked(kk == k)
        ref = k == "refused"
        self.reason.setVisible(ref)
        self.l_reason.setVisible(ref)
        self.t.setEnabled(not ref)
        self.b_all.setEnabled(not ref)
        self.ok.setText(tr({"full": "确认收货", "partial": "登记部分收货", "refused": "登记拒收"}[k]))
        if k == "full" and not auto and self.lines:
            self._fill_all()
        self.err.setText("")

    def _fill_all(self):
        self._filling = True
        for k in range(self.t.rowCount()):
            self.t.item(k, 4).setText(self.t.item(k, 3).text())
        self._filling = False
        self._update_sum()
        if self.res == "partial":
            self._set_result("full", auto=True)

    def _edited(self, it):
        if self._filling or it.column() != 4:
            return
        QTimer.singleShot(0, self._after_edit)

    def _after_edit(self):
        got = self._got()
        rs = RC.result_of(got)
        if self.res != "refused" and rs != self.res:
            self._set_result(rs, auto=True)
        self._update_sum()

    def _got(self):
        out = []
        for k, (sid, x) in enumerate(self.lines):
            v = _n(self.t.item(k, 4).text().replace(",", "."))
            it = self.t.item(k, 4)
            short = v < _n(x.get("colli"))
            was = self._filling
            self._filling = True
            it.setData(Qt.ForegroundRole, QColor(theme.T("err")) if short else None)
            self._filling = was
            out.append({"ean": x.get("ean"), "code": x.get("code"), "desc": x.get("desc"), "store": sid,
                        "ordered": _n(x.get("colli")), "got": max(0.0, v)})
        return out

    def _update_sum(self):
        if not self.lines:
            return
        got = self._got()
        o = sum(x["ordered"] for x in got)
        g = sum(x["got"] for x in got)
        miss = sum(1 for x in got if x["got"] < x["ordered"])
        txt = tr("订了 {0} 箱 · 收到 {1} 箱", _fmt(o), _fmt(g))
        if miss:
            txt += " · " + tr("{0} 个商品少了", miss)
        self.l_sum.setText(txt)

    def record(self):
        at = self.at.dateTime().toPython()
        return {"by": self.by.currentText().strip(), "at": at, "result": self.res, "ddt": self.ddt.text().strip(),
                "reason": self.reason.currentData() or "", "note": self.note.toPlainText().strip(),
                "lines": self._got() if self.lines and self.res != "refused" else []}

    def _accept(self):
        rec = self.record()
        msg = RC.check(rec)
        if msg:
            self.err.setText("⚠ " + tr(msg))
            return
        self.accept()


def register(parent, settings, st, r, info=None, save=None):
    """读明细 → 窗口 → 写进订单跟踪。返回 (改之前的字段, 改的字段) 或 None"""
    lines, note = [], ""
    if info and info.get("file"):
        from erp_widgets import BusyDialog
        from tracking_page import read_order_lines
        rows, err = BusyDialog.run(parent, tr("确认收货"), tr("正在读取订单文件 {0}…", os.path.basename(info["file"])),
                                   lambda: read_order_lines(r, info))
        if err:
            note = tr("订单文件读不了：{0}。请在备注里写清楚到了什么。", err)
        else:
            lines = [(sid, x) for sid, x in rows or [] if _n(x.get("colli")) > 0]
    old = st.orders.get(r["id"], {}) if st is not None else {}
    d = ReceiptDialog(parent, settings, dict(old, **{k: r.get(k) for k in ("supplier", "store_name", "date", "amount")}),
                      lines, note)
    if d.exec() != QDialog.Accepted:
        return None
    rec = d.record()
    f = RC.changes(old, rec, pc=socket.gethostname())
    before = {k: old.get(k) for k in f}
    st.set(r["id"], f)
    rv = settings.setdefault("receiving", {})
    if rv.get("last_by") != rec["by"]:
        rv["last_by"] = rec["by"]
        if save:
            try:
                save()
            except Exception:
                pass
    return before, f, rec
