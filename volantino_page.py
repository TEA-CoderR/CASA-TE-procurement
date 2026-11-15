# -*- coding: utf-8 -*-
"""
销售分析 → VOLANTINO 分析（2026-11-15，数据和计算见 volantino.py）

  上面：标题 · 这一期（下拉：导入的名单 + 数据库里的促销单）· 导入名单… · 修改… · 刷新 · 导出 Excel
  门店按钮：全部门店 / 每家店（整页跟着门店）
  总览（一个外框三块）：VOLANTINO 商品（毛利率圆环、销售额、毛利、销量、让利）· 当月毛利率（全店所有商品：实际 / 没有让利 / 影响）·
                       周期里每天的销售额和毛利（柱子）
  标签页：门店对比（五家店一行一家）/ 商品（每个商品；全部门店时多五列各店销量）
"""
import os
import threading
from datetime import date, datetime

from PySide6.QtCore import Qt, QObject, Signal, QDate, QRectF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
                               QTabWidget, QFileDialog, QStackedWidget, QFrame, QDialog, QCheckBox, QButtonGroup,
                               QSizePolicy, QDateEdit, QGridLayout, QMessageBox, QHeaderView)

from i18n import tr

STORES = [(1, "FOIANO"), (2, "MARLIA"), (3, "S.FILIPPO"), (4, "S.MARCO"), (5, "S.CONCORDIO")]
NAMES = dict(STORES)


def _eur(v, dec=0):
    return f"€ {v:,.{dec}f}" if v is not None else "—"


def _pct(v):
    return f"{v * 100:.1f}%" if v is not None else "—"


class _Sig(QObject):
    done = Signal(object)         # (结果, 错误)
    periods = Signal(object)      # 数据库里的促销单读好了


# =====================================================================
# 一期 VOLANTINO：名称、开始 / 结束、哪几家店（导入名单 / 修改）
# =====================================================================
class PeriodDialog(QDialog):
    def __init__(self, parent, period, n_items, can_delete=False):
        super().__init__(parent)
        from erp_widgets import tip_label
        self.setWindowTitle(tr("VOLANTINO 这一期"))
        self.setMinimumWidth(600)
        self.deleted = False
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(10)
        g.addWidget(QLabel(tr("名称")), 0, 0)
        self.name = QLineEdit(period.get("name") or "")
        g.addWidget(self.name, 0, 1, 1, 3)
        g.addWidget(tip_label(tr("日期"), tr("这一期 VOLANTINO 从哪天到哪天（两天都算）。文件里写了日期的已经认出来了，请核对")), 1, 0)
        self.d1, self.d2 = QDateEdit(), QDateEdit()
        for w, val in ((self.d1, period.get("start")), (self.d2, period.get("end"))):
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd/MM/yyyy")
            d = val if isinstance(val, date) else None
            if d is None and val:
                try:
                    d = datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
                except ValueError:
                    d = None
            d = d or date.today()
            w.setDate(QDate(d.year, d.month, d.day))
        g.addWidget(self.d1, 1, 1)
        g.addWidget(QLabel("–"), 1, 2, Qt.AlignCenter)
        g.addWidget(self.d2, 1, 3)
        g.addWidget(tip_label(tr("门店"), tr("这一期在哪几家店做")), 2, 0, Qt.AlignTop)
        sw = QWidget()
        sl = QHBoxLayout(sw)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        self.boxes = {}
        on = set(period.get("stores") or [s for s, _ in STORES])
        for sid, name in STORES:
            cb = QCheckBox(name)
            cb.setChecked(sid in on)
            self.boxes[sid] = cb
            sl.addWidget(cb)
        sl.addStretch()
        g.addWidget(sw, 2, 1, 1, 3)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        v.addLayout(g)
        info = QLabel(tr("名单里 {0} 个商品", n_items))
        info.setObjectName("Faint")
        v.addWidget(info)
        foot = QHBoxLayout()
        if can_delete:
            de = QPushButton(tr("删除这一期"))
            de.setObjectName("Danger")
            de.clicked.connect(self._delete)
            foot.addWidget(de)
        foot.addStretch()
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("保存"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self._ok)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        v.addLayout(foot)

    def _ok(self):
        if self.d2.date() < self.d1.date():
            QMessageBox.information(self, tr("VOLANTINO 这一期"), tr("结束日期不能早于开始日期"))
            return
        if not self.stores():
            QMessageBox.information(self, tr("VOLANTINO 这一期"), tr("至少选一家门店"))
            return
        self.accept()

    def _delete(self):
        if QMessageBox.question(self, tr("删除这一期"), tr("只删除程序里记的这一期（名单和日期），原来的 Excel 文件不动。删除吗？")) \
                == QMessageBox.Yes:
            self.deleted = True
            self.accept()

    def values(self):
        to = lambda q: date(q.year(), q.month(), q.day()).isoformat()      # noqa: E731
        return {"name": self.name.text().strip(), "start": to(self.d1.date()), "end": to(self.d2.date()),
                "stores": self.stores()}

    def stores(self):
        return [sid for sid, cb in self.boxes.items() if cb.isChecked()]


# =====================================================================
# 总览：VOLANTINO 商品 · 当月毛利率 · 每天
# =====================================================================
class _DayChart(QWidget):
    """周期里每天一根柱子：浅色 = 销售额，里面深色 = 毛利；今天以后的日子虚线框；鼠标停上去看数字"""

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self.days, self.hover = [], -1
        self.setMouseTracking(True)
        self.setMinimumHeight(120)

    def set(self, days):
        self.days = days
        self.update()

    def _idx(self, x):
        n = len(self.days)
        if not n:
            return -1
        i = int(x * n / max(1, self.width()))
        return i if 0 <= i < n else -1

    def mouseMoveEvent(self, e):
        i = self._idx(e.position().x() if hasattr(e, "position") else e.x())
        if i != self.hover:
            self.hover = i
            self.update()
            if i >= 0:
                d, rev, pr = self.days[i]
                self.setToolTip(tr("{0:%a %d/%m}：销售额 € {1:,.2f} · 毛利 € {2:,.2f}（{3}）", d, rev, pr, _pct(pr / rev if rev else None)))

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def paintEvent(self, _e):
        from PySide6.QtGui import QPainter, QPen, QFont
        from theme import NUM_FONT
        T = self.ui.T
        n = len(self.days)
        if not n:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        lab_h = 16
        top_pad = 4
        base = H - lab_h - 2
        cw = W / n
        bw = max(4.0, min(26.0, cw * 0.62))
        top = max((r for _, r, _ in self.days), default=0) or 1
        today = date.today()
        f = QFont(NUM_FONT)
        f.setPixelSize(10)
        p.setFont(f)
        step = max(1, int(28 // max(cw, 1)) + 1)           # 日子多时隔几天写一个日期
        for i, (d, rev, pr) in enumerate(self.days):
            cx = i * cw + cw / 2
            if i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("surface2")))
                p.drawRoundedRect(QRectF(i * cw + 1, 0, cw - 2, H), 6, 6)
            h = (base - top_pad) * rev / top
            x0 = cx - bw / 2
            if d > today:
                pen = QPen(QColor(T("line")), 1, Qt.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(x0, base - 18, bw, 18), 3, 3)
            elif rev > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("accent_soft")))
                p.drawRoundedRect(QRectF(x0, base - h, bw, h), 3, 3)
                hp = (base - top_pad) * max(pr, 0) / top
                p.setBrush(QColor(T("accent") if i != self.hover else T("accent")))
                p.drawRoundedRect(QRectF(x0 + bw * 0.22, base - hp, bw * 0.56, hp), 2, 2)
            if i % step == 0 or i == n - 1:
                p.setPen(QColor(T("accent") if d == today else T("faint")))
                p.drawText(QRectF(i * cw - 10, base + 2, cw + 20, lab_h), Qt.AlignHCenter | Qt.AlignVCenter, f"{d:%d}")
        p.setPen(QPen(QColor(T("line")), 1))
        p.drawLine(0, int(base), W, int(base))
        p.end()


class VolPanel(QFrame):
    def __init__(self, ui):
        super().__init__()
        from erp_widgets import Ring
        self.ui = ui
        self.setObjectName("Panel")
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 12, 14, 12)
        h.setSpacing(16)
        # 左：VOLANTINO 商品
        lw = QWidget()
        lw.setFixedWidth(300)
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        self.cap1 = QLabel(tr("VOLANTINO 商品") + "  ⓘ")
        self.cap1.setObjectName("VpCap")
        self.cap1.setToolTip(tr("这一期名单里的商品，在活动日期里卖了多少（不含税）。圆圈 = 毛利率\n"
                                "毛利 = 销售额 − 进价；让利 = (原价 − 实际售价) × 数量（不含税）"))
        lv.addWidget(self.cap1)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.ring = Ring()
        self.ring.setToolTip(tr("VOLANTINO 商品的毛利率"))
        row.addWidget(self.ring, 0, Qt.AlignVCenter)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        self.big = QLabel("")
        self.big.setObjectName("VpBig")
        self.sub1, self.sub2, self.sub3 = QLabel(""), QLabel(""), QLabel("")
        tv.addStretch()
        tv.addWidget(self.big)
        for x in (self.sub1, self.sub2, self.sub3):
            x.setObjectName("VpSub")
            tv.addWidget(x)
        tv.addStretch()
        row.addLayout(tv, 1)
        lv.addLayout(row)
        lv.addStretch()
        h.addWidget(lw)
        self.seps = [QFrame(), QFrame()]
        h.addWidget(self.seps[0])
        # 中：当月毛利率
        mw = QWidget()
        mw.setFixedWidth(290)
        mv = QVBoxLayout(mw)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(6)
        self.cap2 = QLabel(tr("当月毛利率（全店所有商品）") + "  ⓘ")
        self.cap2.setObjectName("VpCap")
        self.cap2.setToolTip(tr("活动所在的整月（到销售数据最新的那天为止），这几家店所有商品的毛利率。\n"
                                "没有让利 = 假设 VOLANTINO 商品按原价卖：(当月毛利 + 让利) ÷ (当月销售额 + 让利)\n"
                                "影响 = 实际 − 没有让利，单位是百分点"))
        mv.addWidget(self.cap2)
        mr = QHBoxLayout()
        mr.setSpacing(10)
        self.m_big = QLabel("")
        self.m_big.setObjectName("VpBig")
        mr.addWidget(self.m_big, 0, Qt.AlignBottom)
        self.m_chip = QLabel("")
        mr.addWidget(self.m_chip, 0, Qt.AlignVCenter)
        mr.addStretch()
        mv.addLayout(mr)
        self.m_wo = QLabel("")
        self.m_wo.setObjectName("VpSub")
        self.m_share = QLabel("")
        self.m_share.setObjectName("VpSub")
        self.m_month = QLabel("")
        self.m_month.setObjectName("VpFaint")
        self.m_bar = _ImpactBar(ui)
        self.m_bar.setToolTip(tr("深色 = 实际毛利率；浅红那一截 = VOLANTINO 让利吃掉的"))
        mv.addWidget(self.m_bar)
        mv.addWidget(self.m_wo)
        mv.addWidget(self.m_share)
        mv.addWidget(self.m_month)
        mv.addStretch()
        h.addWidget(mw)
        h.addWidget(self.seps[1])
        # 右：每天
        rw = QWidget()
        rv = QVBoxLayout(rw)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        cr = QHBoxLayout()
        self.cap3 = QLabel(tr("每天销售额"))
        self.cap3.setObjectName("VpCap")
        cr.addWidget(self.cap3)
        cr.addStretch()
        self.legend = QLabel("")
        self.legend.setObjectName("VpSub")
        cr.addWidget(self.legend)
        rv.addLayout(cr)
        self.chart = _DayChart(ui)
        rv.addWidget(self.chart, 1)
        h.addWidget(rw, 1)
        for sp in self.seps:
            sp.setFixedWidth(1)
        self.restyle()

    def show(self, a, days, month_rng):
        from erp_widgets import set_chip
        T = self.ui.T
        m = a.get("margin")
        self.ring.set(m, "ok" if m is None or m >= 0.25 else "warn" if m >= 0.10 else "err", color_text=False)
        self.big.setText(_eur(a.get("rev", 0)))
        self.sub1.setText(tr("毛利 {0} · {1:,.0f} 件", _eur(a.get("profit", 0)), a.get("qty", 0)))
        self.sub2.setText(tr("让利 {0}", _eur(a.get("disc", 0))))
        n_items = a.get("items")
        self.sub3.setText(tr("卖出 {0} / {1} 个商品", a.get("sold", 0), n_items) if n_items else
                          tr("卖出 {0} 个商品", a.get("sold", 0)))
        im = a.get("impact") or {}
        self.m_big.setText(_pct(im.get("margin")))
        imp = im.get("impact")
        if imp is None:
            set_chip(self.m_chip, "—", "gray")
        else:
            set_chip(self.m_chip, tr("{0:+.2f} 个百分点", imp), "err" if imp <= -0.5 else "warn" if imp < 0 else "ok")
        self.m_chip.setToolTip(tr("VOLANTINO 让利把当月毛利率拉低了多少（百分点）"))
        self.m_bar.set(im.get("margin"), im.get("margin_wo"))
        self.m_wo.setText(tr("没有让利：{0}", _pct(im.get("margin_wo"))))
        self.m_share.setText(tr("VOLANTINO 占当月销售额 {0}", _pct(a.get("share"))))
        self.m_month.setText(tr("当月：{0:%d/%m} – {1:%d/%m}", month_rng[0], month_rng[1]))
        self.legend.setText(tr("浅色 销售额 · 深色 毛利"))
        self.chart.set(days)

    def restyle(self):
        from theme import NUM_FONT
        T = self.ui.T
        self.setStyleSheet(
            f"#VpCap {{ color: {T('muted')}; font-size: 12px; font-weight: 600; }}"
            f"#VpBig {{ font-family: \"{NUM_FONT}\"; font-size: 24px; font-weight: 600; color: {T('text')}; }}"
            f"#VpSub {{ color: {T('muted')}; font-size: 12px; }}"
            f"#VpFaint {{ color: {T('faint')}; font-size: 11px; }}")
        for sp in self.seps:
            sp.setStyleSheet(f"background: {T('line')};")
        for w in (self.ring, self.chart, self.m_bar):
            w.update()


class _ImpactBar(QWidget):
    """一根横条：实际毛利率（实心）和没有让利时的毛利率（浅色），两者之间那一截就是让利吃掉的"""

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self.a = self.b = None
        self.setFixedHeight(10)

    def set(self, act, wo):
        self.a, self.b = act, wo
        self.update()

    def paintEvent(self, _e):
        from PySide6.QtGui import QPainter
        T = self.ui.T
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T("track")))
        p.drawRoundedRect(QRectF(0, 0, W, H), H / 2, H / 2)
        top = max(0.5, (self.b or 0) * 1.25, (self.a or 0) * 1.25)      # 刻度：两个数都放得下
        if self.b:
            p.setBrush(QColor(T("err_soft")))
            p.drawRoundedRect(QRectF(0, 0, W * max(0, self.b) / top, H), H / 2, H / 2)
        if self.a:
            p.setBrush(QColor(T("accent")))
            p.drawRoundedRect(QRectF(0, 0, W * max(0, self.a) / top, H), H / 2, H / 2)
        p.end()


class _Spread(QObject):
    """行很少的表（门店对比）：列宽按比例铺满整个宽度，表格变宽变窄跟着变（用户自己拖过列宽就不管）"""

    def __init__(self, table, weights, enabled):
        super().__init__(table)
        self.t, self.weights, self.enabled = table, weights, enabled
        table.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Resize and self.enabled():
            self.spread()
        return False

    def spread(self):
        W = self.t.viewport().width()
        cols = [c for c in range(self.t.columnCount()) if not self.t.isColumnHidden(c)]
        tot = sum(self.weights[c] for c in cols) or 1
        for c in cols:
            self.t.setColumnWidth(c, max(56, int(W * self.weights[c] / tot)))


# =====================================================================
# 页面
# =====================================================================
class VolantinoPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cfg = settings.setdefault("volantino", {})
        self.sig = _Sig()
        self.sig.done.connect(self._done)
        self.sig.periods.connect(self._db_periods_arrived)
        self.res = None
        self.store = None
        self.running = False
        self._loaded = False
        self._db_list = []
        self.panel = None

    # ---------- 界面 ----------
    def build(self):
        ui = self.ui
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(8)
        t = QLabel(tr("VOLANTINO 分析") + "  ⓘ")
        t.setObjectName("PageTitle")
        t.setToolTip(tr("每一期 VOLANTINO（促销传单）的销售情况：各门店卖了多少、毛利多少、毛利率，五家店汇总，"
                        "以及这期让利把整个月的毛利率拉低了多少。\n名单可以导入 Excel（条码 / 商品编号 + 日期），"
                        "也可以直接选数据库里的促销单。数据库只读"))
        head.addWidget(t)
        head.addSpacing(12)
        self.period = QComboBox()
        self.period.setMinimumWidth(320)
        self.period.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.period.setToolTip(tr("哪一期：上面是导入的名单，下面是数据库里的促销单"))
        self.period.currentIndexChanged.connect(self._period_changed)
        head.addWidget(self.period, 1)
        self.b_imp = QPushButton(tr("导入名单…"))
        self.b_imp.setToolTip(tr("选这一期 VOLANTINO 的 Excel：有条码或商品编号就行；促销价、日期能认就自动认"))
        self.b_imp.clicked.connect(self.import_file)
        self.b_edit = QPushButton(tr("修改…"))
        self.b_edit.setToolTip(tr("改这一期的名称、日期、门店，或删除（只有导入的名单能改）"))
        self.b_edit.clicked.connect(self.edit_period)
        self.b_ref = QPushButton(tr("刷新"))
        self.b_ref.setToolTip(tr("重新从数据库读销售"))
        self.b_ref.clicked.connect(self.run)
        self.b_exp = QPushButton(tr("导出 Excel"))
        self.b_exp.clicked.connect(self.export)
        for b in (self.b_imp, self.b_edit, self.b_ref, self.b_exp):
            head.addWidget(b)
        lay.addLayout(head)

        # 门店
        sr = QHBoxLayout()
        sr.setSpacing(8)
        self.store_group = QButtonGroup(w)
        self.store_group.setExclusive(True)
        self.store_btns = {}
        from erp_widgets import fit_chip
        for code, text in [(None, tr("全部门店"))] + STORES:
            b = QPushButton(text)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(code is None)
            b.setCursor(Qt.PointingHandCursor)
            fit_chip(b, 28)
            b.clicked.connect(lambda _=False, c=code: self.set_store(c))
            self.store_group.addButton(b)
            self.store_btns[code] = b
            sr.addWidget(b)
        sr.addStretch()
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        sr.addWidget(self.status)
        lay.addLayout(sr)

        self.stack = QStackedWidget()
        from erp_widgets import EmptyState
        self.empty = EmptyState("volantino", tr("还没有 VOLANTINO"),
                                tr("点「导入名单…」选这一期 VOLANTINO 的 Excel，或在上面的下拉里选数据库里的促销单"))
        b = QPushButton(tr("导入名单…"))
        b.setObjectName("Primary")
        b.clicked.connect(self.import_file)
        self.empty.buttons.addWidget(b)
        self.stack.addWidget(self.empty)
        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(12)
        self.panel = VolPanel(ui)
        bv.addWidget(self.panel)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_stores_tab(), tr("门店对比"))
        self.tabs.addTab(self._build_items_tab(), tr("商品"))
        bv.addWidget(self.tabs, 1)
        self.stack.addWidget(body)
        self.busy = QLabel(tr("正在读销售数据…"))
        self.busy.setAlignment(Qt.AlignCenter)
        self.busy.setObjectName("Faint")
        self.stack.addWidget(self.busy)
        lay.addWidget(self.stack, 1)
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self._first_show())
        self.page = w
        self._fill_periods()
        return w

    def _build_stores_tab(self):
        import settings as ST
        from erp_widgets import TableSortFilter
        heads = [tr("门店"), tr("销量"), tr("销售额"), tr("毛利"), tr("毛利率"), tr("让利"), tr("卖出商品"),
                 tr("占当月销售额"), tr("当月毛利率"), tr("没有让利"), tr("影响")]
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 8, 0, 0)
        self.t_st = self.ui.make_table(heads, stretch_col=None, numeric_cols=tuple(range(1, len(heads))))
        tips = {4: tr("VOLANTINO 商品的毛利率"), 5: tr("(原价 − 实际售价) × 数量，不含税"),
                7: tr("VOLANTINO 商品销售额 ÷ 这家店当月全部销售额"), 8: tr("这家店当月所有商品的毛利率"),
                9: tr("假设 VOLANTINO 商品按原价卖时当月的毛利率"), 10: tr("实际 − 没有让利，单位百分点；负数 = 让利拉低了毛利率")}
        for c, tp in tips.items():
            self.t_st.horizontalHeaderItem(c).setToolTip(tp)
        self.t_st.verticalHeader().setDefaultSectionSize(40)
        hh = self.t_st.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        self._st_spread = _Spread(self.t_st, (1.5, 0.8, 1.2, 1.1, 0.9, 1.1, 0.9, 1.1, 1.0, 1.0, 0.9),
                                  lambda: not (self.cfg.get("stores_cols_widths") or {}))
        v.addWidget(self.t_st, 1)
        self.st_rows = []
        self.ctl_st = TableSortFilter(self.t_st, lambda: len(self.st_rows), lambda i, c: self.st_rows[i][1][c],
                                      self._fill_st, key=lambda i, c: self.st_rows[i][2][c], numeric=range(1, len(heads)))
        self.ctl_st.remember(self.cfg, "stores_cols", lambda: ST.save_settings(self.settings))
        self.t_st.doubleClicked.connect(lambda ix: self._st_double(ix.row()))
        self.t_st.setToolTip(tr("双击一家店：整页只看这家店"))
        return box

    def _build_items_tab(self):
        import settings as ST
        import product_images as PI
        from erp_widgets import TableSortFilter
        self.item_heads = [tr("条码"), tr("商品"), tr("促销价"), tr("销量"), tr("销售额"), tr("毛利"), tr("毛利率"),
                           tr("让利"), tr("活动前销量"), tr("变化")] + [n for _, n in STORES]
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(8)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("条码或名称"))
        self.search.setFixedWidth(240)
        bar.addWidget(self.search)
        self.show_sel = QComboBox()
        for key, text in (("all", tr("全部商品")), ("sold", tr("卖出的")), ("unsold", tr("没卖出的")),
                          ("loss", tr("毛利为负的"))):
            self.show_sel.addItem(text, key)
        bar.addWidget(self.show_sel)
        bar.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        v.addLayout(bar)
        numeric = tuple(range(2, len(self.item_heads)))
        self.t_it = self.ui.make_table(self.item_heads, stretch_col=None, numeric_cols=numeric)
        hh = self.t_it.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        thumb = PI.thumb_size(self.settings) if self.settings.get("show_images", True) else 0
        widths = [130, 270 + (thumb + 10 if thumb else 0), 72, 66, 100, 90, 74, 90, 86, 84] + [74] * len(STORES)
        for c, wd in enumerate(widths):
            self.t_it.setColumnWidth(c, wd)
        if thumb:
            self.t_it.setItemDelegateForColumn(1, PI.ThumbDelegate(self.t_it, 0, None, self.settings))
            self.t_it.verticalHeader().setDefaultSectionSize(PI.row_height(self.settings))
        tips = {7: tr("(原价 − 实际售价) × 数量，不含税"), 8: tr("活动开始前同样天数里的销量"),
                9: tr("活动期间销量比活动前多 / 少几成")}
        for c, tp in tips.items():
            self.t_it.horizontalHeaderItem(c).setToolTip(tp)
        for k, (_sid, n) in enumerate(STORES):
            self.t_it.horizontalHeaderItem(10 + k).setToolTip(tr("{0} 的销量", n))
        v.addWidget(self.t_it, 1)
        self.it_rows = []
        self.ctl_it = TableSortFilter(self.t_it, lambda: len(self.it_rows), lambda i, c: self.it_rows[i][1][c],
                                      self._fill_it, key=lambda i, c: self.it_rows[i][2][c], hide=self._it_hidden,
                                      numeric=numeric, lazy=True)
        self.ctl_it.remember(self.cfg, "items_cols", lambda: ST.save_settings(self.settings), default_hidden=[0])
        self.ctl_it.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        self.search.textChanged.connect(lambda _: self.ctl_it.render())
        self.show_sel.currentIndexChanged.connect(lambda _: self.ctl_it.render())
        return box

    def restyle(self):
        if self.panel is not None:
            self.panel.restyle()
        if self.res is not None:
            self.update_view()

    # ---------- 几期 ----------
    def _base(self):
        from settings import app_dir
        return app_dir()

    def _files(self):
        import volantino as V
        return V.load_periods(self._base())

    def _fill_periods(self, select=None):
        import volantino as V
        files = sorted(self._files(), key=lambda p: p["start"], reverse=True)
        sel = select or self.cfg.get("last")
        self.period.blockSignals(True)
        self.period.clear()
        self._plist = []
        for p in files:
            self.period.addItem("📄 " + V.label(p), p["id"])
            self._plist.append(p)
        if self._db_list:
            if files:
                self.period.insertSeparator(self.period.count())
                self._plist.append(None)
            for p in self._db_list:
                self.period.addItem(tr("🗄 数据库 · {0}（{1} 个商品）", V.label(p), p.get("n", 0)), p["id"])
                self._plist.append(p)
        i = self.period.findData(sel) if sel else -1
        if i < 0 and self.period.count():
            i = 0
        self.period.setCurrentIndex(i)
        self.period.blockSignals(False)
        self.b_edit.setEnabled(bool(self.current() and self.current().get("source") == "file"))
        has = self.period.count() > 0
        for b in (self.b_ref, self.b_exp):
            b.setEnabled(has)
        if not has:
            self.stack.setCurrentIndex(0)

    def current(self):
        i = self.period.currentIndex()
        return self._plist[i] if 0 <= i < len(getattr(self, "_plist", [])) else None

    def _first_show(self):
        if self._loaded:
            return
        self._loaded = True
        self._load_db_periods()
        if self.current():
            self.run()

    def _load_db_periods(self):
        dbs = self.settings.get("db") or None

        def work():
            import reorder_service as R
            import volantino as V
            try:
                db = R.make_db(dbs)
                try:
                    ps = V.db_periods(db)
                finally:
                    db.engine.dispose()
            except Exception:
                ps = []
            self.sig.periods.emit(ps)
        threading.Thread(target=work, daemon=True).start()

    def _db_periods_arrived(self, ps):
        had = self.current()
        self._db_list = ps
        self._fill_periods(had["id"] if had else None)
        if not had and self.current():
            self.run()

    def _period_changed(self, _i):
        p = self.current()
        if not p:
            return
        self.cfg["last"] = p["id"]
        import settings as ST
        ST.save_settings(self.settings)
        self.b_edit.setEnabled(p.get("source") == "file")
        self.run()

    def import_file(self):
        import volantino as V
        path, _ = QFileDialog.getOpenFileName(self.win, tr("导入 VOLANTINO 名单"), self.cfg.get("last_dir") or "",
                                              tr("Excel 文件 (*.xlsx *.xlsm)"))
        if not path:
            return
        self.cfg["last_dir"] = os.path.dirname(path)
        from erp_widgets import BusyDialog
        got, err = BusyDialog.run(self.win, tr("导入名单"), tr("正在读 {0}…", os.path.basename(path)), lambda: V.read_file(path))
        if err:
            QMessageBox.warning(self.win, tr("导入名单"), tr("读不了这个文件：{0}", err))
            return
        if not got["items"]:
            QMessageBox.information(self.win, tr("导入名单"), tr("文件里没找到商品（要有条码或商品编号列）"))
            return
        items = got["items"]
        for k, pr in (got.get("prices") or {}).items():
            if 0 <= k < len(items):
                try:
                    items[k]["price"] = float(pr) if pr not in (None, "") else None
                except (TypeError, ValueError):
                    pass
        p = {"name": got["name"], "start": got["start"] or date.today(), "end": got["end"] or date.today(),
             "stores": [s for s, _ in STORES]}
        dlg = PeriodDialog(self.win, p, len(items))
        if not dlg.exec():
            return
        periods = V.load_periods(self._base())
        new = dict(dlg.values(), id=V.new_id(periods), source="file", file=path, items=items,
                   created=datetime.now().isoformat(timespec="seconds"))
        periods.append(new)
        V.save_periods(self._base(), periods)
        self.win.log(tr("[VOLANTINO] 导入名单 {0}：{1} 个商品，{2} – {3}", os.path.basename(path), len(items),
                        new["start"], new["end"]))
        self._fill_periods(new["id"])
        self.run()

    def edit_period(self):
        import volantino as V
        cur = self.current()
        if not cur or cur.get("source") != "file":
            return
        dlg = PeriodDialog(self.win, cur, len(cur.get("items") or []), can_delete=True)
        if not dlg.exec():
            return
        periods = V.load_periods(self._base())
        if dlg.deleted:
            periods = [p for p in periods if p["id"] != cur["id"]]
            sel = None
        else:
            for p in periods:
                if p["id"] == cur["id"]:
                    p.update(dlg.values())
            sel = cur["id"]
        V.save_periods(self._base(), periods)
        self._fill_periods(sel)
        if self.current():
            self.run()
        else:
            self.res = None

    # ---------- 计算 ----------
    def run(self):
        p = self.current()
        if not p or self.running:
            return
        self.running = True
        self.stack.setCurrentIndex(2)
        self.status.setText("")
        dbs = self.settings.get("db") or None
        log = self.win.log

        def work():
            import reorder_service as R
            import volantino as V
            try:
                db = R.make_db(dbs)
                try:
                    try:
                        upto = R.sales_latest(db)
                        upto = upto.date() if hasattr(upto, "date") else upto
                    except Exception:
                        upto = None
                    res = V.analyze(db, p, upto=upto, log=log)
                    res["upto"] = upto
                finally:
                    db.engine.dispose()
                self.sig.done.emit((res, None))
            except Exception as e:
                self.sig.done.emit((None, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, payload):
        res, err = payload
        self.running = False
        if err:
            self.stack.setCurrentIndex(0)
            self.win.log(tr("❌ VOLANTINO 分析失败：{0}", err))
            self.win.show_toast(tr("VOLANTINO 分析失败：{0}", err))
            return
        self.res = res
        if self.store is not None and self.store not in res["stores"]:
            self.set_store(None)
            return
        for code, b in self.store_btns.items():
            b.setEnabled(code is None or code in res["stores"])
        self.stack.setCurrentIndex(1)
        self.update_view()

    def set_store(self, code):
        self.store = code
        b = self.store_btns.get(code)
        if b is not None:
            b.setChecked(True)
        if self.res is not None:
            self.update_view()

    def _st_double(self, k):
        i = self.ctl_st.row_at(k) if hasattr(self.ctl_st, "row_at") else k
        if i is not None and 0 <= i < len(self.st_rows):
            self.set_store(self.st_rows[i][0])

    def update_view(self):
        import volantino as V
        res = self.res
        a = V.view(res, self.store)
        if self.store is not None:
            a = dict(a, items=res["total"]["items"])
        self.panel.show(a, V.day_series(res, self.store), res["month"])
        upto = res.get("upto")
        bits = [tr("{0:%d/%m} – {1:%d/%m} · {2} 天", res["start"], res["end"], (res["end"] - res["start"]).days + 1)]
        if upto:
            bits.append(tr("销售数据到 {0:%d/%m}", upto))
        if res["matched"] < res["total"]["items"]:
            bits.append(tr("⚠ {0} 个商品数据库里没找到", res["total"]["items"] - res["matched"]))
        if res.get("no_cost"):
            bits.append(tr("⚠ {0} 个商品没有进价", res["no_cost"]))
        self.status.setText(" · ".join(bits))
        self.status.setToolTip(tr("没有进价的商品毛利按 0 成本算，会偏高；数据库里没找到的商品不算销售"))
        self._fill_stores()
        self._fill_items()

    # ---------- 门店对比 ----------
    def _fill_stores(self):
        res = self.res
        rows = []
        for sid in res["stores"]:
            a = res["store"][sid]
            im = a.get("impact") or {}
            disp = [NAMES[sid], f"{a['qty']:,.0f}", _eur(a["rev"], 2), _eur(a["profit"], 2), _pct(a.get("margin")),
                    _eur(a["disc"], 2), f"{a['sold']}", _pct(a.get("share")), _pct(im.get("margin")),
                    _pct(im.get("margin_wo")), f"{im['impact']:+.2f}" if im.get("impact") is not None else "—"]
            keys = [sid, a["qty"], a["rev"], a["profit"], a.get("margin") or 0, a["disc"], a["sold"], a.get("share") or 0,
                    im.get("margin") or 0, im.get("margin_wo") or 0, im.get("impact") or 0]
            rows.append((sid, disp, keys))
        self.st_rows = rows
        self.ctl_st.render()
        if self._st_spread.enabled():
            self._st_spread.spread()

    def _fill_st(self, i, k):
        T = self.ui.T
        sid, disp, keys = self.st_rows[i]
        sel = self.store == sid
        self.ui.table_set_row(self.t_st, k, disp, bold=sel)
        it = self.t_st.item(k, 0)
        f = it.font()
        f.setBold(True)
        it.setFont(f)
        if sel:
            it.setForeground(QColor(T("accent")))
        m = keys[4]
        self.t_st.item(k, 4).setForeground(QColor(T("err") if m < 0 else T("text")))
        imp = keys[10]
        c = self.t_st.item(k, 10)
        c.setForeground(QColor(T("err") if imp <= -0.5 else T("warn") if imp < 0 else T("ok")))
        f = c.font()
        f.setBold(True)
        c.setFont(f)

    # ---------- 商品 ----------
    def _fill_items(self):
        import volantino as V
        res = self.res
        rows = []
        for r in V.item_rows(res, self.store):
            before, qty = r.get("before", 0.0), r.get("qty", 0.0)
            ch = (qty - before) / before if before else None
            price = r.get("price")
            try:
                price = float(price) if price not in (None, "") else None
            except (TypeError, ValueError):
                price = None
            per = [r["per_store"].get(s) for s, _ in STORES]
            disp = [r["ean"], r["name"], f"{price:.2f}" if price else "", f"{qty:,.0f}", _eur(r["rev"], 2),
                    _eur(r["profit"], 2), _pct(r.get("margin")), _eur(r["disc"], 2), f"{before:,.0f}",
                    (f"{ch:+.0%}" if ch is not None else (tr("新") if qty > 0 else "")) ] + \
                   [("" if v is None else f"{v:,.0f}") for v in per]
            keys = [r["ean"], (r["name"] or "").lower(), price or 0, qty, r["rev"], r["profit"],
                    r["margin"] if r.get("margin") is not None else -9, r["disc"], before,
                    ch if ch is not None else (9 if qty > 0 else -9)] + [v or 0 for v in per]
            rows.append((r, disp, keys))
        self.it_rows = rows
        # 只看一家店时，各店销量那几列藏起来；这一期没做的店也藏（程序藏的，用户在「显示哪些列」里选了也不显示）
        ch_ = self.ctl_it._chooser
        if ch_ is not None:
            ch_.forced = lambda: {10 + k for k, (sid, _n) in enumerate(STORES)
                                  if self.store is not None or sid not in res["stores"]}
            ch_.apply()
        if self.ctl_it.sort_col is None:
            self.ctl_it.sort_col, self.ctl_it.sort_desc = 4, True          # 默认按销售额从高到低
        self.ctl_it.render()

    def _it_hidden(self, i):
        r, disp, _ = self.it_rows[i]
        q = self.search.text().strip().lower()
        if q and q not in f"{disp[0]} {disp[1]} {r.get('code') or ''}".lower():
            return True
        sel = self.show_sel.currentData()
        qty = r.get("qty", 0.0)
        return (sel == "sold" and qty <= 0) or (sel == "unsold" and qty > 0) or \
               (sel == "loss" and not (r.get("margin") is not None and r["margin"] < 0))

    def _fill_it(self, i, k):
        T = self.ui.T
        r, disp, keys = self.it_rows[i]
        self.ui.table_set_row(self.t_it, k, disp)
        it = self.t_it.item(k, 1)
        it.setToolTip(f"{disp[1]}\n{disp[0]} · {r.get('code') or ''}")
        if r.get("qty", 0) <= 0:
            for c in range(3, 10):
                cell = self.t_it.item(k, c)
                if cell is not None:
                    cell.setForeground(QColor(T("faint")))
            return
        m = r.get("margin")
        if m is not None and m < 0:
            self.t_it.item(k, 6).setForeground(QColor(T("err")))
            self.t_it.item(k, 5).setForeground(QColor(T("err")))
        ch = keys[9]
        c = self.t_it.item(k, 9)
        if ch not in (9, -9):
            c.setForeground(QColor(T("ok") if ch > 0 else T("warn") if ch < 0 else T("muted")))
        else:
            c.setForeground(QColor(T("accent")))

    # ---------- 导出 ----------
    def export(self):
        if self.res is None:
            return
        import volantino as V
        p = self.res["period"]
        name = "".join(ch for ch in (p.get("name") or "VOLANTINO") if ch not in '\\/:*?"<>|').strip() or "VOLANTINO"
        path, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"),
                                              os.path.join(self.cfg.get("last_dir") or "",
                                                           f"Volantino_{name}_{self.res['start']:%d%m}.xlsx"),
                                              tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        from erp_widgets import BusyDialog, open_file
        res = self.res
        url = self.settings.get("image_url") or None
        _, err = BusyDialog.run(self.win, tr("导出 Excel"), tr("正在写…"), lambda: V.export(res, path, url=url, names=NAMES))
        if err:
            QMessageBox.warning(self.win, tr("导出 Excel"), tr("导出失败：{0}", err))
            return
        open_file(self.win, path, tr("导出 Excel"))
