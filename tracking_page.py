"""
采购管理 → 采购订单跟踪（2026-11-02；数据和规则见 order_tracking.py）。
  上面：标题 · 数据在哪 / 上次同步 · 从 Excel 同步 · + 新订单 · 更多 ▾
  四张卡：待确认 · 已超期 · 卡住了 · 本月下单 / 预算（点卡片 = 只看这些 / 看预算）
  标签页：订单（搜索 · 月份 · 门店 · 看哪些；表格可排序、右键表头筛选、列可拖；双击改，右键快速确认 / 到货…）
          供应商（单数、金额、到货天数；选几行右键「合并成一家」）· 预算（每月每店，双击改预算）
共享文件夹里别人改了：每 20 秒看一次，有变化就重新读。
"""
import os
import threading
import time
from datetime import date, datetime

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QDate
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit, QFrame,
                               QProgressBar, QToolButton, QButtonGroup, QSizePolicy,
                               QComboBox, QTabWidget, QFileDialog, QStackedWidget, QMenu, QDialog, QCheckBox,
                               QDateEdit, QDoubleSpinBox, QListWidget, QListWidgetItem, QMessageBox, QInputDialog,
                               QAbstractItemView, QHeaderView)

from i18n import tr
import order_tracking as OT
import order_record as REC
import excel_sync as XS


class _Sig(QObject):
    done = Signal(object)
    exported = Signal(object)
    transit = Signal(object)
    files = Signal(object)


def STATUS_LABELS():
    return {"ordered": tr("已下单"), "confirmed": tr("已确认"), "partial": tr("部分到货"), "arrived": tr("已到货"),
            "stuck": tr("卡住了"), "cancel": tr("已取消"), "deter": "DETER", "draft": tr("待登记")}


STATUS_TOKEN = {"ordered": "text", "confirmed": "accent", "partial": "warn", "arrived": "ok", "stuck": "err",
                "cancel": "faint", "deter": "faint", "draft": "warn"}


def VIEWS():
    return [("all", tr("全部")), ("draft", tr("待登记")), ("open", tr("未完成")), ("pending", tr("待确认")), ("overdue", tr("已超期")),
            ("stuck", tr("卡住了")), ("partial", tr("部分到货")), ("arrived", tr("已到货")), ("deter", "DETER"),
            ("cancel", tr("已取消"))]


def _in_view(r, v):
    if v in (None, "all"):
        return True
    if v == "open":
        return r["open"]
    if v == "pending":
        return r["pending"]
    if v == "overdue":
        return r["overdue"]
    return r["status"] == v


def FIELD_LABELS():
    return {"date": tr("下单日期"), "store": tr("门店"), "supplier": tr("供应商"), "buyer": tr("谁下的"),
            "amount": tr("金额"), "confirmed": tr("确认"), "arrived": tr("到货"), "expected": tr("预计到货"),
            "state": tr("情况"), "kind": tr("类型"), "note": tr("备注"), "ref": tr("Excel 位置"), "origin": tr("来源"),
            "draft": tr("待登记"), "colli": tr("箱数"), "file": tr("订单文件")}


def ORIGINS():
    return {"store_orders": tr("总仓采购"), "store_pdv": tr("门店采购"), "wholesale": tr("DETERSIVO采购"),
            "app": tr("程序"), "excel": "Excel"}


def _d(v):
    d = OT.to_date(v)
    return d.isoformat() if d else ""


def _money(v):
    return "" if v is None else f"{float(v):,.2f}"


def _when(t):
    """修改记录的时间（UTC 存的）→ 本地 mm-dd hh:mm"""
    try:
        from datetime import timezone
        dt = datetime.strptime(str(t)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(t)[:16]


def _fmt_val(k, v):
    if v in (None, ""):
        return tr("（空）")
    if k == "store":
        return OT.STORE_NAMES.get(str(v), str(v))
    if k == "amount":
        return "€ " + _money(v)
    if k == "state":
        return {"partial": tr("部分到货"), "stuck": tr("卡住了"), "cancel": tr("已取消")}.get(v, str(v))
    if k == "kind":
        return "DETER" if v == "deter" else str(v)
    if k == "origin":
        return ORIGINS().get(v, str(v))
    if k == "draft":
        return tr("是") if v else tr("已确认下单")
    if k == "file":
        return os.path.basename(str(v))
    return str(v)


def history_lines(evs):
    """一张单的修改记录 → 一行一条「时间 · 谁 · 改了什么」（新的在上）"""
    lab = FIELD_LABELS()
    out = []
    for e in reversed(evs or []):
        who = f"{_when(e.get('t'))} · {e.get('by') or '?'}" + (" · Excel" if e.get("src") == "excel" else "")
        if e.get("op") == "del":
            out.append(f"{who} · {tr('删除')}")
            continue
        parts = [f"{lab.get(k, k)} {_fmt_val(k, v)}" for k, v in (e.get("f") or {}).items()
                 if k not in ("ref", "origin")]
        out.append(f"{who} · " + ("；".join(parts) if parts else tr("导入")))
    return out


# 订单表的列：(固定编号, 表头, 默认宽度)
COLS = [(0, "下单日期", 96), (1, "门店", 98), (2, "供应商", 170), (3, "谁下的", 84), (4, "金额 €", 96),
        (5, "状态", 88), (6, "确认", 96), (7, "预计到货", 96), (8, "到货", 96), (9, "天数", 72), (10, "备注", 220),
        (11, "来源", 110), (12, "最后修改", 150), (13, "明细", 104)]
NUM_COLS = (4, 9)
HIDDEN = (11, 12)


class _WeekChart(QWidget):
    """
    每周一栏：周名 + 日期、金额、几张单，下面一根竖条（高 = 这周金额，底对齐，几周连起来就是一张小柱状图）。
    本周：蓝字蓝条；点过的周：浅蓝底；以后的周：灰字、虚线框；鼠标停上去整栏浅灰底
    """
    clicked = Signal(int)
    BAR_H = 40

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self.weeks, self.today, self.sel, self.hover = [], date.today(), None, -1
        self.setMouseTracking(True)
        self.setMinimumHeight(128)
        self.setCursor(Qt.PointingHandCursor)

    def set(self, weeks, today, sel):
        self.weeks, self.today, self.sel = weeks, today, sel
        self.update()

    def _col(self, x):
        n = len(self.weeks)
        if not n:
            return -1
        i = int(x * n / max(1, self.width()))
        return i if 0 <= i < n else -1

    def mouseMoveEvent(self, e):
        i = self._col(e.position().x() if hasattr(e, "position") else e.x())
        if i != self.hover:
            self.hover = i
            self.update()
            if i >= 0:
                w = self.weeks[i]
                self.setToolTip(tr("第 {0} 周 {1:%d/%m} – {2:%d/%m}：{3} 张单，€ {4:,.2f}\n点一下：下面的订单只看这一周",
                                   i + 1, w["start"], w["end"], w["n"], w["amount"]))

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._col(e.position().x() if hasattr(e, "position") else e.x())
        if i >= 0:
            self.clicked.emit(i)

    def paintEvent(self, _e):
        from PySide6.QtGui import QPainter, QPen, QFont, QFontMetrics
        from PySide6.QtCore import QRectF
        from theme import NUM_FONT, UI_FONT
        T = self.ui.T
        n = len(self.weeks)
        if not n:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        cw = W / n
        top = max((w["amount"] for w in self.weeks), default=0) or 1
        base = H - 6                                         # 柱子的底线
        f_small, f_amt = QFont(UI_FONT), QFont(NUM_FONT)
        f_small.setPixelSize(11)
        f_amt.setPixelSize(16)
        f_amt.setBold(True)
        f_title = QFont(UI_FONT)
        f_title.setPixelSize(12)
        f_title.setBold(True)
        for i, w in enumerate(self.weeks):
            x0 = i * cw
            box = QRectF(x0 + 4, 0, cw - 8, H)
            now = w["start"] <= self.today <= w["end"]
            future = w["start"] > self.today
            sel = self.sel == (w["start"], w["end"])
            if sel or i == self.hover:                       # 选中 / 鼠标停着：整栏淡底
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("accent_soft") if sel else T("surface2")))
                p.drawRoundedRect(box, 10, 10)
            pad = 12
            tx = box.left() + pad
            tw = box.width() - 2 * pad
            p.setFont(f_title)
            p.setPen(QColor(T("accent") if now else T("muted")))
            p.drawText(QRectF(tx, 10, tw, 16), Qt.AlignLeft | Qt.AlignVCenter, tr("本周") if now else tr("第 {0} 周", i + 1))
            p.setFont(f_small)
            p.setPen(QColor(T("faint")))
            rng = f"{w['start']:%d/%m}–{w['end']:%d/%m}" if w["start"] != w["end"] else f"{w['start']:%d/%m}"
            p.drawText(QRectF(tx, 10, tw, 16), Qt.AlignRight | Qt.AlignVCenter, rng)
            p.setFont(f_amt)
            p.setPen(QColor(T("faint") if (future or not w["n"]) else T("text")))
            amt = f"€ {w['amount']:,.0f}" if w["n"] else "–"
            p.drawText(QRectF(tx, 30, tw, 22), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(f_amt).elidedText(amt, Qt.ElideRight, int(tw)))
            p.setFont(f_small)
            p.setPen(QColor(T("muted")))
            p.drawText(QRectF(tx, 52, tw, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       tr("{0} 张单", w["n"]) if w["n"] else ("" if future else tr("没有单")))
            # 柱子：底对齐，高 = 金额 / 最多那周
            bx, bw = tx, tw
            if future and not w["n"]:
                pen = QPen(QColor(T("line")), 1, Qt.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(bx, base - 8, bw, 8), 4, 4)
            else:
                h = max(4.0, self.BAR_H * w["amount"] / top) if w["n"] else 4.0
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("accent") if (now or sel) else T("ghost") if w["n"] else T("track")))
                p.drawRoundedRect(QRectF(bx, base - h, bw, h), 4, 4)
        p.end()


class MonthPanel(QFrame):
    """
    页面顶上的「本月」（2026-11-02 用户：把 PIANO 表头的预算、本月合计、每周单数和合计放到顶上；没选门店 = 全部，选了 = 这家；
    要美观 → 第二版：不用一格格带框的小卡片，左边一圈预算进度、右边每周一栏连成一张小柱状图，整块只有一个外框）。
      左边：‹ 2026 年 11 月 › · 门店 / 圆圈（用了预算的百分比）+ 已下单（大字）、预算 ✎、还剩 / 超出 · 几张单
      右边：每周一栏（第 1 周 = 1 号到第一个星期天）：金额、几张单、柱子；本周蓝色，点一栏 = 订单表只看这一周
    都不含税，不算待登记、已取消
    """
    week_clicked = Signal(object)        # (第一天, 最后一天) 或 None
    month_step = Signal(int)             # -1 / +1
    budget_clicked = Signal()

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self.setObjectName("Panel")
        self.sel = None
        self.data = None
        self.tone = "ok"
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 12, 14, 10)
        h.setSpacing(14)
        lw = QWidget()
        lw.setFixedWidth(300)
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        nav = QHBoxLayout()
        nav.setSpacing(0)
        self.prev, self.next = QToolButton(), QToolButton()
        for b, d, tip in ((self.prev, -1, tr("上个月")), (self.next, 1, tr("下个月"))):
            b.setText("‹" if d < 0 else "›")
            b.setAutoRaise(True)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(24, 24)
            b.clicked.connect(lambda _=False, d=d: self.month_step.emit(d))
        self.m_lbl = QLabel("")
        self.m_lbl.setObjectName("MpMonth")
        self.scope = QLabel("")
        self.scope.setObjectName("MpScope")
        nav.addWidget(self.prev)
        nav.addWidget(self.m_lbl)
        nav.addWidget(self.next)
        nav.addSpacing(8)
        nav.addWidget(self.scope)
        nav.addStretch()
        lv.addLayout(nav)
        row = QHBoxLayout()
        row.setSpacing(14)
        from erp_widgets import Ring
        self.ring = Ring()        # 预算用了多少：绿 / 黄 ≥ 90% / 红 > 100%
        row.addWidget(self.ring, 0, Qt.AlignVCenter)
        tv = QVBoxLayout()
        tv.setSpacing(1)
        self.amt = QLabel("")
        self.amt.setObjectName("MpAmount")
        self.amt.setToolTip(tr("这个月下的单（不含待登记、已取消），不含税"))
        self.bud = QLabel("")
        self.bud.setObjectName("MpBudget")
        self.bud.setCursor(Qt.PointingHandCursor)
        self.bud.mousePressEvent = lambda _e: self.budget_clicked.emit()
        self.pct = QLabel("")
        self.pct.setObjectName("MpPct")
        tv.addStretch()
        tv.addWidget(self.amt)
        tv.addWidget(self.bud)
        tv.addWidget(self.pct)
        tv.addStretch()
        row.addLayout(tv, 1)
        lv.addLayout(row)
        lv.addStretch()
        h.addWidget(lw)
        self.sep = QFrame()
        self.sep.setFixedWidth(1)
        h.addWidget(self.sep)
        self.chart = _WeekChart(ui)
        self.chart.clicked.connect(self._week)
        h.addWidget(self.chart, 1)
        self.restyle()

    def _week(self, i):
        if not self.data or i >= len(self.data["weeks"]):
            return
        w = self.data["weeks"][i]
        key = (w["start"], w["end"])
        self.week_clicked.emit(None if self.sel == key else key)

    def show_month(self, data, year, month, scope, can_edit, sel=None, today=None):
        today = today or date.today()
        self.data, self.sel = data, sel
        self.m_lbl.setText(tr("{0} 年 {1} 月", year, month))
        self.scope.setText(scope)
        self.amt.setText(f"€ {data['amount']:,.2f}")
        b = data["budget"]
        self.bud.setText((tr("预算 € {0:,.0f}", b) if b else tr("没有预算")) + ("  ✎" if can_edit else ""))
        self.bud.setToolTip(tr("点一下改这家店这个月的预算（不含税）") if can_edit else tr("各门店预算加起来；选一家门店可以改它的预算"))
        if b:
            p = data["amount"] / b
            self.tone = "err" if p > 1 else "warn" if p >= 0.9 else "ok"
            self.pct.setText((tr("超出 € {0:,.0f}", data["amount"] - b) if p > 1 else tr("还剩 € {0:,.0f}", b - data["amount"]))
                             + tr(" · {0} 张单", data["n"]))
            self.ring.set(p, self.tone)
        else:
            self.tone = "ok"
            self.pct.setText(tr("{0} 张单", data["n"]))
            self.ring.set(None, "ok")
        self.chart.set(data["weeks"], today, sel)
        self.restyle()

    def restyle(self):
        from theme import NUM_FONT
        T = self.ui.T
        tone = self.tone
        self.setStyleSheet(
            f"#MpMonth {{ font-size: 15px; font-weight: 600; color: {T('text')}; padding: 0 2px; }}"
            f"#MpScope {{ color: {T('accent')}; background: {T('accent_soft')}; border-radius: 9px; padding: 1px 9px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"#MpAmount {{ font-family: \"{NUM_FONT}\"; font-size: 24px; font-weight: 600; color: {T('text')}; }}"
            f"#MpBudget {{ color: {T('muted')}; font-size: 12px; }}"
            f"#MpPct {{ color: {T(tone) if tone != 'ok' else T('muted')}; font-size: 12px; font-weight: 600; }}"
            f"QToolButton {{ color: {T('muted')}; font-size: 16px; border-radius: 12px; }}"
            f"QToolButton:hover {{ background: {T('surface2')}; color: {T('accent')}; }}")
        self.sep.setStyleSheet(f"background: {T('line')};")
        self.ring.update()
        self.chart.update()


PILLS = (("draft", "warn"), ("pending", "warn"), ("overdue", "err"), ("stuck", "err"))


class TrackingPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cfg = settings.setdefault("tracking", {})
        self.st = None
        self.rows, self.sups, self.buds = [], [], []
        self.sig = _Sig()
        self.sig.done.connect(self._sync_done)
        self.sig.exported.connect(self._export_done)
        self.sig.transit.connect(self._transit_done)
        self.sig.files.connect(self._files_done)
        self.transit = None                          # order_record.compute 的结果（每张没到货的单对上哪个订单文件）
        self._transit_busy = self._transit_again = False
        self.syncing = False
        self._bad_mtime = self._try_mtime = None
        self._shown = False
        self.store_sel = None                        # 选的门店（None = 全部）
        self.week_sel = None                         # 点了「本月」里的哪一周：(第一天, 最后一天)
        t0 = date.today()
        self.panel_ym = (t0.year, t0.month)

    # ================================================================ 界面
    def build(self):
        from erp_widgets import EmptyState
        w = QWidget()
        w.setObjectName("Content")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel(tr("采购订单跟踪") + "  ⓘ")
        t.setObjectName("PageTitle")
        t.setToolTip(tr("每张采购单：下单 → 确认 → 到货。数据在共享文件夹里，大家看到的是同一份；谁在什么时候改了什么都有记录。"
                        "预计到货 = 下单日期 + 这家供应商以往到货的天数（中位数），超过了还没到就算超期"))
        head.addWidget(t)
        head.addSpacing(12)
        self.src_lbl = QLabel("")
        self.src_lbl.setObjectName("Faint")
        head.addWidget(self.src_lbl, 1)
        self.btn_sync = QPushButton(tr("同步 Excel"))
        self.btn_sync.setToolTip(tr("Excel 存过就会自动同步（打开这页时、开着时每 20 秒看一次），一般不用点；想马上同步再点"))
        self.btn_sync.clicked.connect(lambda: self.sync_excel(manual=True))
        head.addWidget(self.btn_sync)
        self.btn_new = QPushButton(tr("+ 新订单"))
        self.btn_new.setObjectName("Primary")
        self.btn_new.clicked.connect(self.new_order)
        head.addWidget(self.btn_new)
        more = QPushButton(tr("更多") + " ▾")
        m = QMenu(more)
        m.addAction(tr("名字相近的供应商…"), self.suggest_merge)
        m.addAction(tr("导出成 PIANO 表（和原表一样）…"), self.export_piano)
        m.addAction(tr("导出 Excel（显示的订单）"), self.export)
        m.addSeparator()
        m.addAction(tr("打开共享文件夹"), self.open_folder)
        m.addAction(tr("设置…"), self.open_settings)
        more.setMenu(m)
        head.addWidget(more)
        lay.addLayout(head)

        self.stack = QStackedWidget()
        self.empty = EmptyState("tracking", tr("选择共享文件夹"),
                                tr("放 PIANO ACQUISTO MESILE 的那个共享文件夹（Synology Drive）。程序会在里面建一个「{0}」文件夹放订单记录，并从 Excel 导入",
                                   OT.DATA_DIR))
        b = QPushButton(tr("选择共享文件夹…"))
        b.setObjectName("Primary")
        b.clicked.connect(self.choose_folder)
        self.empty.buttons.addWidget(b)
        self.stack.addWidget(self.empty)

        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(12)
        # 门店（全部 / 每家店）+ 右边几个要处理的数字；下面「本月」面板；再下面三张表（都跟着门店走）
        sr = QHBoxLayout()
        sr.setSpacing(8)
        self.store_group = QButtonGroup(body)
        self.store_group.setExclusive(True)
        from PySide6.QtGui import QFontMetrics
        for code, text in [(None, tr("全部门店"))] + list(OT.STORES):
            b = QPushButton(text)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(code is None)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            bf = b.font()
            bf.setBold(True)
            b.setMinimumWidth(QFontMetrics(bf).horizontalAdvance(text) + 28)
            b.clicked.connect(lambda _=False, c=code: self.set_store(c))
            self.store_group.addButton(b)
            sr.addWidget(b)
        sr.addStretch()
        self.pills = {}
        tips = {"draft": tr("总仓采购 / DETERSIVO采购生成的订单，还没确认发给供应商。发了以后选中，右键「确认已下单」"),
                "pending": tr("下单 {0} 天以上还没确认的", OT.PENDING_DAYS),
                "overdue": tr("过了预计到货日还没到（含部分到货）"), "stuck": ""}
        for key, _tok in PILLS:
            b = QPushButton("")
            b.setCursor(Qt.PointingHandCursor)
            if tips[key]:
                b.setToolTip(tips[key])
            b.clicked.connect(lambda _=False, k=key: self._kpi_clicked(k))
            sr.addWidget(b)
            self.pills[key] = b
        bv.addLayout(sr)
        self.panel = MonthPanel(self.ui)
        self.panel.month_step.connect(self._month_step)
        self.panel.week_clicked.connect(self._week_clicked)
        self.panel.budget_clicked.connect(self._budget_clicked)
        bv.addWidget(self.panel)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_orders(), tr("订单"))
        self.tabs.addTab(self._build_sups(), tr("供应商"))
        self.tabs.addTab(self._build_budget(), tr("预算"))
        bv.addWidget(self.tabs, 1)
        self.stack.addWidget(body)
        lay.addWidget(self.stack, 1)

        self.timer = QTimer(w)
        self.timer.setInterval(20000)                # 别的电脑改了（同步盘同步过来）：20 秒内看到
        self.timer.timeout.connect(self._poll)
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self._on_show())
        self.page = w
        self.badge_timer = QTimer(w)                 # 侧边栏「采购订单跟踪」上的待登记个数：页面没打开也每分钟看一次
        self.badge_timer.setInterval(60000)
        self.badge_timer.timeout.connect(self.check_badge)
        self.badge_timer.start()
        # 第一次数待登记 / 同步 Excel 放到打开程序 30 秒后：它要读共享文件夹的记录和整张 PIANO 表，
        # 和启动抢时间会让界面卡几秒（11-06 hang.log）。点进订单跟踪页时照样马上读
        QTimer.singleShot(30000, self.check_badge)
        return w

    def _build_orders(self):
        ui = self.ui
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("供应商、谁下的、备注"))
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(lambda _: self.render())
        bar.addWidget(self.search)
        self.f_month = QComboBox()
        self.f_month.setMinimumWidth(110)
        self.f_month.currentIndexChanged.connect(lambda _: self._month_combo())
        bar.addWidget(self.f_month)
        self.week_chip = QPushButton("")
        self.week_chip.setObjectName("Chip")
        self.week_chip.setCheckable(True)
        self.week_chip.setChecked(True)
        self.week_chip.setToolTip(tr("只看这一周的单；点一下取消"))
        self.week_chip.clicked.connect(lambda: self._week_clicked(None))
        self.week_chip.hide()
        bar.addWidget(self.week_chip)
        self.f_view = QComboBox()
        self.f_view.setMinimumWidth(130)
        for k, n in VIEWS():
            self.f_view.addItem(n, k)
        self.f_view.currentIndexChanged.connect(lambda _: self.render())
        bar.addWidget(self.f_view)
        bar.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        v.addLayout(bar)
        self.t = ui.make_table([tr(h) for _f, h, _w in COLS], numeric_cols=NUM_COLS)
        t = self.t
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.verticalHeader().setDefaultSectionSize(32)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, (_f, _h, wd) in enumerate(COLS):
            t.setColumnWidth(c, wd)
        for c, tip in ((4, tr("不含税（Imponibile）")), (7, tr("手动填的，或者 = 下单日期 + 这家供应商以往到货的天数（中位数）。红 = 超期")),
                       (9, tr("到了：下单到到货用了几天；还没到：已经等了几天")),
                       (len(COLS) - 1, tr("没到货的单在「RECORD ORDINE」里对上的订单文件：订了几种商品、几箱。双击看是哪些商品；"
                                          "对错了右键「指定订单文件…」"))):
            hi = t.horizontalHeaderItem(c)
            if hi is not None:
                hi.setToolTip(tip)
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(t, lambda: len(self.rows), self._text, self._fill, key=self._key,
                                   hide=self._hidden, numeric=NUM_COLS)
        self.ctl.rendered.connect(self._rendered)
        import settings as ST
        cols_saved = self.cfg.get("orders_columns")
        if isinstance(cols_saved, list) and cols_saved and 13 not in cols_saved:
            # 2026-11-03 新加「明细」列：已经存过列顺序的电脑放在「状态」后面一次
            at = cols_saved.index(5) + 1 if 5 in cols_saved else len(cols_saved)
            cols_saved.insert(at, 13)
        self.ctl.remember(self.cfg, "orders", lambda: ST.save_settings(self.settings), [f for f, _h, _w in COLS],
                          default_hidden=HIDDEN)
        self.ctl.sort_col, self.ctl.sort_desc = 0, True              # 默认：新的单在上
        t.cellDoubleClicked.connect(self._double)
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.customContextMenuRequested.connect(self._row_menu)
        v.addWidget(t, 1)
        foot = QHBoxLayout()
        self.total = QLabel("")
        self.total.setObjectName("Faint")
        foot.addWidget(self.total)
        foot.addStretch()
        hint = QLabel(tr("双击改 · 右键快速确认 / 到货"))
        hint.setObjectName("Faint")
        foot.addWidget(hint)
        v.addLayout(foot)
        return box

    def _build_sups(self):
        ui = self.ui
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(8)
        bar = QHBoxLayout()
        self.s_search = QLineEdit()
        self.s_search.setPlaceholderText(tr("供应商"))
        self.s_search.setFixedWidth(220)
        self.s_search.textChanged.connect(lambda _: self.s_ctl.render())
        bar.addWidget(self.s_search)
        bar.addStretch()
        self.s_count = QLabel("")
        self.s_count.setObjectName("Faint")
        bar.addWidget(self.s_count)
        v.addLayout(bar)
        heads = [tr("供应商"), tr("单数"), tr("金额 €"), tr("平均天数"), tr("中位天数"), tr("最长天数"), tr("未完成"),
                 tr("已超期"), tr("最近下单"), tr("写法")]
        self.st_t = ui.make_table(heads, numeric_cols=(1, 2, 3, 4, 5, 6, 7))
        t = self.st_t
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.verticalHeader().setDefaultSectionSize(32)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, wd in enumerate((190, 64, 110, 80, 80, 80, 72, 72, 100, 300)):
            t.setColumnWidth(c, wd)
        t.horizontalHeaderItem(4).setToolTip(tr("下单到到货的天数（中位数）：预计到货就按它算（至少 {0} 张到货的单）", OT.LEAD_MIN_SAMPLES))
        t.horizontalHeaderItem(9).setToolTip(tr("Excel 里这家供应商出现过的写法（合并过的也在这里）"))
        from erp_widgets import TableSortFilter
        self.s_ctl = TableSortFilter(t, lambda: len(self.sups), self._s_text, self._s_fill, key=self._s_key,
                                     hide=self._s_hidden, numeric=(1, 2, 3, 4, 5, 6, 7))
        self.s_ctl.rendered.connect(lambda n, tot: self.s_count.setText(tr("{0:,} / {1:,}", n, tot)))
        import settings as ST
        self.s_ctl.remember(self.cfg, "sups", lambda: ST.save_settings(self.settings), list(range(len(heads))))
        self.s_ctl.sort_col, self.s_ctl.sort_desc = 2, True
        t.cellDoubleClicked.connect(lambda k, _c: self._show_supplier(self.s_ctl.row_at(k)))
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.customContextMenuRequested.connect(self._sup_menu)
        v.addWidget(t, 1)
        hint = QLabel(tr("双击看这家的订单 · 选几行右键合并成一家"))
        hint.setObjectName("Faint")
        v.addWidget(hint)
        return box

    def _build_budget(self):
        ui = self.ui
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(8)
        heads = [tr("月份"), tr("门店"), tr("预算"), tr("已下单"), tr("占预算"), tr("单数")]
        self.b_t = ui.make_table(heads, numeric_cols=(2, 3, 4, 5))
        t = self.b_t
        t.verticalHeader().setDefaultSectionSize(32)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, wd in enumerate((90, 120, 130, 140, 90, 70)):
            t.setColumnWidth(c, wd)
        t.horizontalHeaderItem(2).setToolTip(tr("不含税。6–9 月 Excel 里的预算是含税的，导入时已经 ÷ 1.22"))
        t.horizontalHeaderItem(3).setToolTip(tr("不含税（不算待登记、已取消）"))
        t.horizontalHeaderItem(4).setToolTip(tr("已下单 ÷ 预算。黄 ≥ 90%，红 > 100%"))
        from erp_widgets import TableSortFilter
        self.b_ctl = TableSortFilter(t, lambda: len(self.buds), self._b_text, self._b_fill, key=self._b_key,
                                     numeric=(2, 3, 4, 5))
        import settings as ST
        self.b_ctl.remember(self.cfg, "budget", lambda: ST.save_settings(self.settings), list(range(len(heads))), ver=2)
        t.cellDoubleClicked.connect(lambda k, _c: self.edit_budget(self.b_ctl.row_at(k)))
        v.addWidget(t, 1)
        hint = QLabel(tr("双击改预算"))
        hint.setObjectName("Faint")
        v.addWidget(hint)
        return box

    # ================================================================ 数据
    def _folder(self):
        f = self.cfg.get("folder") or ""
        return f if f and os.path.isdir(f) else ""

    def _excel(self):
        p = self.cfg.get("excel") or ""
        if not self.cfg.get("excel_original"):        # 2026-11-02 用户：换成 NUOVO 正本（以前存的是 _COPIA）→ 换一次
            self.cfg["excel_original"] = True
            o = OT.prefer_original(p)
            if o:
                self.cfg["excel"] = p = o
                self.cfg.pop("excel_mtime", None)
            self._save()
        if p and os.path.isfile(p):
            return p
        return OT.find_excel(self._folder()) if self._folder() else ""

    def _me(self):
        return (self.cfg.get("me") or self.settings.get("user_name") or OT.default_me()).strip()

    def _on_show(self):
        if not self._folder():
            self.stack.setCurrentIndex(0)
            return
        self.stack.setCurrentIndex(1)
        if self.st is None or self.st.folder != self._folder():
            self.load()
        elif self.st.changed():
            self.load()
        self.timer.start()
        self._shown = True
        self._auto_sync()                 # 每次打开页面：Excel 存过（修改时间变了）就自动同步

    def _poll(self):
        if not self.page.isVisible():
            self.timer.stop()
            return
        if self.st is not None and not self.syncing and self.st.changed():
            self.load()
        self._auto_sync()                 # 页面开着时别人存了 Excel：20 秒内自动同步过来

    def load(self):
        f = self._folder()
        if not f:
            return
        if self.st is None or self.st.folder != f:
            self.st = OT.Store(f, me=self._me())
        try:
            self.st.reload()
        except Exception as e:
            self.win.show_toast(tr("订单记录读不了：{0}", e))
            return
        self.refresh()

    def refresh(self):
        """重新算每一行（状态、超期…），三张表和卡片都更新"""
        st = self.st
        if st is None:
            return
        self.rows = OT.rows(st)
        # 月份下拉：有单的月份（新的在上）
        cur = self.f_month.currentData()
        months = sorted({OT.month_of(r) for r in self.rows if r["date"]}, reverse=True)
        self.f_month.blockSignals(True)
        self.f_month.clear()
        self.f_month.addItem(tr("全部月份"), None)
        for mth in months:
            self.f_month.addItem(mth, mth)
        i = self.f_month.findData(cur)
        self.f_month.setCurrentIndex(max(0, i))
        self.f_month.blockSignals(False)
        self.badge(sum(1 for r in self.rows if r["status"] == "draft"))
        self._update_src()
        self.update_view()
        self._start_transit()

    def _srows(self):
        return [r for r in self.rows if not self.store_sel or r["store"] == self.store_sel]

    def update_view(self):
        """门店 / 月份 / 周变了：数字、本月面板、三张表按选的门店重算（不重读数据）"""
        st = self.st
        if st is None:
            return
        srows = self._srows()
        self.sups = OT.supplier_stats(srows)
        self.buds = [b for b in OT.budget_rows(self.rows, st) if not self.store_sel or b["store"] == self.store_sel]
        self.f_view.blockSignals(True)
        for i, (k, n) in enumerate(VIEWS()):
            c = sum(1 for r in srows if _in_view(r, k))
            self.f_view.setItemText(i, f"{n}（{c}）" if k != "all" else n)
        self.f_view.blockSignals(False)
        s = OT.summary(srows, st, date.today().strftime("%Y-%m"))
        late = max((r["late_by"] for r in srows if r["overdue"]), default=0)
        labels = {"draft": tr("待登记"), "pending": tr("待确认"), "overdue": tr("已超期"), "stuck": tr("卡住了")}
        T = self.ui.T
        for key, tok in PILLS:
            n = s[key]
            b = self.pills[key]
            b.setText(f"{labels[key]}  {n}")
            if key == "overdue" and late:
                b.setToolTip(tr("过了预计到货日还没到（含部分到货）") + "\n" + tr("最久 {0} 天", late))
            b.setStyleSheet(
                f"QPushButton {{ border-radius: 13px; padding: 4px 12px; font-size: 12px; font-weight: 600;"
                f" border: 1px solid {T(tok) if n else T('line')}; background: {T(tok + '_soft') if n else 'transparent'};"
                f" color: {T(tok) if n else T('faint')}; }}"
                f"QPushButton:hover {{ border-color: {T(tok)}; }}")
        y, m = self.panel_ym
        data = OT.month_summary(self.rows, st, y, m, self.store_sel)
        self.panel.show_month(data, y, m, OT.STORE_NAMES.get(self.store_sel, tr("全部门店")) if self.store_sel
                              else tr("全部门店"), bool(self.store_sel), self.week_sel)
        if self.week_sel:
            a, b = self.week_sel
            self.week_chip.setText(f"{a:%d/%m}–{b:%d/%m}  ✕")
        self.week_chip.setVisible(bool(self.week_sel))
        self.f_month.setEnabled(not self.week_sel)
        self.render()
        self.s_ctl.render()
        self.b_ctl.render()

    def set_store(self, code):
        self.store_sel = code
        self.update_view()

    def _month_step(self, d):
        y, m = self.panel_ym
        m += d
        if m < 1:
            y, m = y - 1, 12
        elif m > 12:
            y, m = y + 1, 1
        self.panel_ym = (y, m)
        self.week_sel = None
        self.update_view()

    def _week_clicked(self, key):
        self.week_sel = key
        if key:
            self.tabs.setCurrentIndex(0)
        self.update_view()

    def _month_combo(self):
        """订单表的月份下拉换了：顶上「本月」也跟着换到那个月"""
        mth = self.f_month.currentData()
        if mth:
            y, m = (int(x) for x in mth.split("-"))
            if (y, m) != self.panel_ym:
                self.panel_ym = (y, m)
                self.week_sel = None
                self.update_view()
                return
        self.render()

    def _budget_clicked(self):
        if not self.store_sel:
            self.tabs.setCurrentIndex(2)
            return
        y, m = self.panel_ym
        ym = f"{y:04d}-{m:02d}"
        cur = self.st.budgets.get(f"{ym}|{self.store_sel}") if self.st else None
        v, ok = QInputDialog.getDouble(self.win, tr("预算"), tr("{0} · {1} 预算（不含税，0 = 不设）", ym,
                                                              OT.STORE_NAMES.get(self.store_sel, self.store_sel)),
                                       float(cur or 0), 0, 99999999, 0)
        if ok:
            self.st.set_budget(ym, self.store_sel, v or None)
            self.refresh()

    def _update_src(self):
        f = self._folder()
        x = self._excel()
        last = self.cfg.get("synced_at") or ""
        txt = tr("{0} 张单", len(self.rows)) + (tr(" · Excel 同步于 {0}", last) if last else "")
        n_wait = 0
        if self._write_on() and self.st is not None:
            try:
                n_wait = len(XS.pending(self.st, self._me(), everyone=True))
            except Exception:
                n_wait = 0
        if n_wait:
            busy = (getattr(self, "_write_state", None) or ("",))[0] == "busy"
            txt += " · " + (tr("⏳ {0} 处修改等 Excel 关闭后写入", n_wait) if busy else tr("⏳ {0} 处修改待写入 Excel", n_wait))
        self.src_lbl.setText(txt)
        self.src_lbl.setToolTip(tr("订单记录：{0}\nExcel：{1}", os.path.join(f, OT.DATA_DIR) if f else "—", x or "—"))
        self.btn_sync.setEnabled(bool(x) and not self.syncing)
        self.btn_sync.setToolTip(tr("和 Excel 双向同步：Excel 里的修改读进来，程序里的修改写进 Excel；同一格两边都改了，以最后修改的为准。"
                                    "一般不用点（自动进行）\n{0}", x or tr("没找到 Excel")))

    # ================================================================ 订单表
    def _hidden(self, i):
        r = self.rows[i]
        q = self.search.text().strip().lower()
        if q and q not in f"{r['supplier']} {r['raw_supplier']} {r['buyer']} {r['note']} {r['store_name']} {r['ref']}".lower():
            return True
        if self.store_sel and r["store"] != self.store_sel:
            return True
        if self.week_sel:
            if not r["date"] or not (self.week_sel[0] <= r["date"] <= self.week_sel[1]):
                return True
        else:
            mth = self.f_month.currentData()
            if mth and OT.month_of(r) != mth:
                return True
        return not _in_view(r, self.f_view.currentData())

    def _text(self, i, c):
        r = self.rows[i]
        f = COLS[c][0]
        if f == 0:
            return _d(r["date"])
        if f == 1:
            return r["store_name"]
        if f == 2:
            return r["supplier"]
        if f == 3:
            return r["buyer"]
        if f == 4:
            return _money(r["amount"])
        if f == 5:
            return STATUS_LABELS().get(r["status"], r["status"])
        if f == 6:
            return _d(r["confirmed"])
        if f == 7:
            return _d(r["expected"]) if r["status"] not in ("deter", "cancel", "arrived") else ""
        if f == 8:
            return _d(r["arrived"])
        if f == 9:
            if r["days"] is None:
                return ""
            return str(r["days"]) if r["arrived"] else tr("已 {0}", r["days"])
        if f == 10:
            return r["note"]
        if f == 11:
            return r["ref"] or ORIGINS().get(r["origin"], "")
        if f == 12:
            return f"{r['last'][1]} · {_when(r['last'][0])}" if r["last"] else ""
        if f == 13:
            return self._rec_text(r)
        return ""

    # ---- 明细（在途）----
    def _rec_info(self, r):
        if self.transit is None:
            return None
        return self.transit["orders"].get(r["id"])

    def _rec_text(self, r):
        if r["status"] not in REC.OPEN_STATES:
            return ""
        if self.transit is None:
            return "…"
        info = self._rec_info(r)
        if info is None:
            return ""                                  # 超过 60 天：不算在途
        if r.get("record") == REC.NO_FILE:
            return tr("不算")
        if not info["file"]:
            return tr("没找到")
        if not info["n"]:
            return tr("没有这家店")
        return ("✎ " if info["how"] == "manual" else "") + tr("{0} 种 · {1:g} 箱", info["n"], info["colli"])

    def _start_transit(self):
        if self._transit_busy:
            self._transit_again = True
            return
        self._transit_busy, self._transit_again = True, False
        rows, settings = list(self.rows), self.settings

        def work():
            try:
                self.sig.transit.emit((REC.cached(settings, rows=rows), None))
            except Exception as e:
                self.sig.transit.emit((None, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _transit_done(self, payload):
        res, err = payload
        self._transit_busy = False
        if err:
            self.win.log(tr("⚠ 订单明细（RECORD ORDINE）读不了：{0}", err))
            res = {"orders": {}, "by_ean": {}, "by_code": {}, "ok": False}
        self.transit = res
        if self._transit_again:
            self._start_transit()
            return
        self.render()

    def _double(self, k, c):
        i = self.ctl.row_at(k)
        if i is None:
            return
        if COLS[c][0] == 13 and self.rows[i]["status"] in REC.OPEN_STATES:
            self.show_lines(self.rows[i])
            return
        self.edit_row(i)

    def show_lines(self, r):
        info = self._rec_info(r)
        if not info or not info.get("file"):
            self.win.show_toast(tr("没找到这张单的订单文件：右键「指定订单文件…」"))
            return
        open_lines(self.win, self, r, info)

    def _set_record(self, r, value):
        self.st.set(r["id"], {"record": value})
        REC.forget()
        self.refresh()

    def pick_record(self, r):
        root = REC.record_root(self.settings)
        start = root if os.path.isdir(root) else ""
        info = self._rec_info(r) or {}
        if info.get("path"):
            start = os.path.dirname(info["path"])
        p, _ = QFileDialog.getOpenFileName(self.win, tr("这张单的订单文件"), start, tr("Excel 文件 (*.xlsx *.xlsm)"))
        if not p:
            return
        try:
            rel = os.path.relpath(p, root) if os.path.isdir(root) else p
        except ValueError:                            # 不同盘
            rel = p
        if rel.startswith(".."):
            rel = p
        self._set_record(r, rel.replace("\\", "/"))

    def _key(self, i, c):
        r = self.rows[i]
        f = COLS[c][0]
        if f in (0, 6, 7, 8):
            d = r[{0: "date", 6: "confirmed", 7: "expected", 8: "arrived"}[f]]
            return d.toordinal() if d else 0
        if f == 4:
            return float(r["amount"] or 0)
        if f == 9:
            return r["days"] if r["days"] is not None else -1
        if f == 5:
            return OT.STATUS.index(r["status"]) if r["status"] in OT.STATUS else 99
        if f == 12:
            return r["last"][0] if r["last"] else ""
        return None

    def _fill(self, i, k):
        r = self.rows[i]
        T = self.ui.T
        vals = [self._text(i, c) for c in range(len(COLS))]
        self.ui.table_set_row(self.t, k, vals)
        fids = [f for f, _h, _w in COLS]
        cancel = r["status"] == "cancel"
        if cancel or r["status"] == "deter":
            for c in range(len(COLS)):
                it = self.t.item(k, c)
                if it is not None:
                    it.setForeground(QColor(T("faint")))
        st_it = self.t.item(k, fids.index(5))
        if st_it is not None and not cancel:
            tok = "warn" if r["pending"] else STATUS_TOKEN.get(r["status"], "text")
            st_it.setForeground(QColor(T(tok)))
            f = st_it.font()
            f.setBold(r["status"] in ("stuck", "partial", "draft") or r["pending"])
            st_it.setFont(f)
            if r["pending"]:
                st_it.setToolTip(tr("下单 {0} 天了还没确认", r["days"]))
        if r["overdue"]:
            for fid in (7, 9):
                it = self.t.item(k, fids.index(fid))
                if it is not None:
                    it.setForeground(QColor(T("err")))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setToolTip(tr("超期 {0} 天", r["late_by"]))
        ex = self.t.item(k, fids.index(7))
        if ex is not None and not r["overdue"] and not r["expected_manual"] and ex.text():
            ex.setForeground(QColor(T("muted")))
            ex.setToolTip(tr("按这家供应商以往到货 {0} 天推算", round(r["lead"])) if r["lead"] is not None
                          else tr("这家供应商到货记录不够，按全部供应商的中位数推算"))
        sup = self.t.item(k, fids.index(2))
        if sup is not None and r["raw_supplier"] and r["raw_supplier"] != r["supplier"]:
            sup.setToolTip(tr("Excel 里写的：{0}", r["raw_supplier"]))
        src = self.t.item(k, fids.index(11))
        if src is not None and r["file"]:
            src.setToolTip(r["file"])
        if r.get("from_file"):                       # 箱数 / 金额是按改过的订单文件重算的
            am = self.t.item(k, fids.index(4))
            if am is not None:
                f = am.font()
                f.setItalic(True)
                am.setFont(f)
                am.setForeground(QColor(T("accent")))
                am.setToolTip(tr("按订单文件里改过的箱数重新算的（{0} 箱）\n{1}", r.get("colli") or 0,
                                 os.path.basename(r["file"]) or r.get("record")))
        note = self.t.item(k, fids.index(10))
        if note is not None and r["note"]:
            note.setToolTip(r["note"])
        rec = self.t.item(k, fids.index(13))
        info = self._rec_info(r) if r["status"] in REC.OPEN_STATES else None
        if rec is not None and info is not None:
            if not info["file"]:
                rec.setForeground(QColor(T("faint")))
                rec.setToolTip(tr("「RECORD ORDINE」里没找到这张单的订单文件（门店 + 供应商 + 下单日期前后几天）。"
                                  "右键「指定订单文件…」") if info["how"] != "none" or not r.get("record")
                               else tr("标了「没有订单文件」"))
            else:
                how = {"manual": tr("手动指定的"), "program": tr("程序生成的订单文件"), "auto": tr("自动找到的")}.get(info["how"], "")
                tip = f"{info['file']}\n{how} · " + tr("双击看商品")
                if info.get("error"):
                    tip += "\n⚠ " + tr("读不了：{0}", info["error"])
                    rec.setForeground(QColor(T("err")))
                rec.setToolTip(tip)

    def _rendered(self, n, tot):
        self.count.setText(tr("{0:,} / {1:,}", n, tot))
        amt = sum(float(self.rows[i]["amount"] or 0) for i in self.ctl.visible if self.rows[i]["status"] not in OT.NOT_ORDERED)
        self.total.setText(tr("{0:,} 张单 · 合计 € {1:,.2f}（不含税）· 含税 € {2:,.2f}", n, amt, amt * (1 + OT.IVA)))

    def render(self):
        if hasattr(self, "ctl"):
            self.ctl.render()

    def _selected(self):
        ks = sorted({ix.row() for ix in self.t.selectionModel().selectedRows()})
        return [self.rows[i] for i in (self.ctl.row_at(k) for k in ks) if i is not None]

    def _kpi_clicked(self, key):
        self.tabs.setCurrentIndex(0)
        self.week_sel = None
        self.f_month.blockSignals(True)
        self.f_month.setCurrentIndex(0)
        self.f_month.blockSignals(False)
        self.week_chip.hide()
        self.f_month.setEnabled(True)
        i = self.f_view.findData(key)
        if i >= 0:
            self.f_view.setCurrentIndex(i)

    def _row_menu(self, pos):
        sel = self._selected()
        if not sel or self.st is None:
            return
        m = QMenu(self.t)
        today = date.today().isoformat()
        n = len(sel)
        suffix = tr("（{0} 张）", n) if n > 1 else ""
        drafts = [r for r in sel if r["status"] == "draft"]
        a_place = a_file = None
        if drafts:                                   # 待登记的：发给供应商以后确认
            a_place = m.addAction(tr("确认已下单（今天）") + (tr("（{0} 张）", len(drafts)) if len(drafts) > 1 else ""))
            f = a_place.font()
            f.setBold(True)
            a_place.setFont(f)
            m.addSeparator()
        if n == 1 and sel[0]["file"]:
            a_file = m.addAction(tr("打开订单文件"))
            a_file.setEnabled(os.path.exists(sel[0]["file"]))
            m.addSeparator()
        a_lines = a_pick = a_nofile = a_auto = None
        if n == 1 and sel[0]["status"] in REC.OPEN_STATES:
            info = self._rec_info(sel[0]) or {}
            a_lines = m.addAction(tr("看订了哪些商品"))
            a_lines.setEnabled(bool(info.get("file")))
            a_pick = m.addAction(tr("指定订单文件…"))
            if sel[0].get("record"):
                a_auto = m.addAction(tr("自动找订单文件"))
            else:
                a_nofile = m.addAction(tr("没有订单文件"))
                a_nofile.setToolTip(tr("这张单不算在途（例如电话订的、没有明细）"))
            m.addSeparator()
        a_conf = m.addAction(tr("已确认（今天）") + suffix)
        a_arr = m.addAction(tr("已到货（今天）") + suffix)
        a_part = m.addAction(tr("部分到货…") + suffix)
        a_stuck = m.addAction(tr("卡住了…") + suffix)
        a_ok = m.addAction(tr("恢复正常") + suffix)
        a_ok.setToolTip(tr("去掉「卡住了 / 部分到货 / 已取消」"))
        m.addSeparator()
        a_edit = m.addAction(tr("编辑…"))
        a_edit.setEnabled(n == 1)
        a_cancel = m.addAction(tr("取消订单") + suffix)
        a_del = m.addAction(tr("删除") + suffix)
        got = m.exec(self.t.viewport().mapToGlobal(pos))
        if got is None:
            return
        ids = [r["id"] for r in sel]
        if a_place is not None and got == a_place:
            import commands as CMD                      # 业务命令（记审计日志；命令行 / 以后的智能体走同一个）
            CMD.confirm_orders(self.st, [r["id"] for r in drafts], via="desktop")
            self.refresh()
            return
        if a_file is not None and got == a_file:
            from erp_widgets import open_file
            open_file(self.win, sel[0]["file"])
            return
        if a_lines is not None and got == a_lines:
            self.show_lines(sel[0])
            return
        if a_pick is not None and got == a_pick:
            self.pick_record(sel[0])
            return
        if got is not None and got in (a_nofile, a_auto):
            self._set_record(sel[0], REC.NO_FILE if got == a_nofile else None)
            return
        if got == a_edit:
            self.edit_row(self.rows.index(sel[0]))
            return
        if got == a_del:
            if QMessageBox.question(self.win, tr("删除"), tr("删除这 {0} 张单？（修改记录里留着）", n)) != QMessageBox.Yes:
                return
            self.st.delete(ids)
            self.refresh()
            return
        ch = None
        if got == a_conf:
            ch = [(r["id"], {"confirmed": today}) for r in sel if not r["confirmed"]]
        elif got == a_arr:
            ch = [(r["id"], {"arrived": today, "state": None} if r["status"] in ("partial", "stuck") else {"arrived": today})
                  for r in sel]
        elif got in (a_part, a_stuck):
            what = tr("部分到货") if got == a_part else tr("卡住了")
            text, ok = QInputDialog.getText(self.win, what, tr("备注（缺什么 / 为什么）"))
            if not ok:
                return
            state = "partial" if got == a_part else "stuck"
            ch = []
            for r in sel:
                f = {"state": state}
                if text.strip():
                    f["note"] = (r["note"] + " · " if r["note"] else "") + text.strip()
                if state == "partial" and not r["arrived"]:
                    f["arrived"] = today
                ch.append((r["id"], f))
        elif got == a_ok:
            ch = [(r["id"], {"state": None}) for r in sel if self.st.orders.get(r["id"], {}).get("state")]
        elif got == a_cancel:
            ch = [(r["id"], {"state": "cancel"}) for r in sel]
        if ch:
            self.st.set_many(ch)
            self.refresh()

    def edit_row(self, i):
        if i is None or self.st is None:
            return
        r = self.rows[i]
        dlg = OrderDialog(self.win, self, self.st.orders.get(r["id"], {}), r, self.st.hist.get(r["id"]))
        if dlg.exec() != QDialog.Accepted:
            return
        if dlg.deleted:
            self.st.delete([r["id"]])
        elif dlg.changes:
            self.st.set(r["id"], dlg.changes)
        self.refresh()

    def new_order(self):
        if self.st is None:
            if not self._folder():
                self.choose_folder()
            if self.st is None:
                return
        sto = self.store_sel
        dlg = OrderDialog(self.win, self, {"date": date.today().isoformat(), "store": sto or "1",
                                           "buyer": self.cfg.get("last_buyer") or ""}, None, None)
        if dlg.exec() != QDialog.Accepted or not dlg.changes:
            return
        f = dict(dlg.values)
        if f.get("buyer"):
            self.cfg["last_buyer"] = f["buyer"]
        self.st.add({k: v for k, v in f.items() if v not in (None, "")})
        self.refresh()

    def known(self):
        """下拉框里的供应商、下单人（按用得多少排）"""
        sup, buy = {}, {}
        for r in self.rows:
            if r["supplier"]:
                sup[r["supplier"]] = sup.get(r["supplier"], 0) + 1
            if r["buyer"]:
                b = r["buyer"].strip()
                buy[b] = buy.get(b, 0) + 1
        order = lambda d: [k for k, _v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0].lower()))]
        return order(sup), order(buy)

    # ================================================================ 供应商表
    def _s_hidden(self, i):
        q = self.s_search.text().strip().lower()
        s = self.sups[i]
        return bool(q) and q not in (s["supplier"] + " " + " ".join(s["names"])).lower()

    def _s_text(self, i, c):
        s = self.sups[i]
        f1 = lambda v: "" if v is None else (f"{v:.1f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))
        return [s["supplier"], str(s["n"]), _money(s["amount"]), f1(s["avg"]), f1(s["med"]), f1(s["max"]),
                str(s["open"]) if s["open"] else "", str(s["overdue"]) if s["overdue"] else "",
                _d(s["last"]), " · ".join(sorted(x for x in s["names"] if x))][c]

    def _s_key(self, i, c):
        s = self.sups[i]
        v = [None, s["n"], s["amount"], s["avg"], s["med"], s["max"], s["open"], s["overdue"],
             s["last"].toordinal() if s["last"] else 0, None][c]
        return -1 if (v is None and c in (3, 4, 5)) else v

    def _s_fill(self, i, k):
        s = self.sups[i]
        self.ui.table_set_row(self.st_t, k, [self._s_text(i, c) for c in range(10)])
        if s["overdue"]:
            it = self.st_t.item(k, 7)
            if it is not None:
                it.setForeground(QColor(self.ui.T("err")))
        it = self.st_t.item(k, 9)
        if it is not None:
            it.setForeground(QColor(self.ui.T("muted")))
            it.setToolTip(it.text())

    def _show_supplier(self, i):
        if i is None:
            return
        self.tabs.setCurrentIndex(0)
        self.f_view.setCurrentIndex(0)
        self.f_month.setCurrentIndex(0)
        self.search.clear()
        c = [f for f, _h, _w in COLS].index(2)
        self.ctl.set_filter(c, {self.sups[i]["supplier"]})

    def _sup_menu(self, pos):
        ks = sorted({ix.row() for ix in self.st_t.selectionModel().selectedRows()})
        sel = [self.sups[i] for i in (self.s_ctl.row_at(k) for k in ks) if i is not None]
        if not sel or self.st is None:
            return
        m = QMenu(self.st_t)
        a_merge = m.addAction(tr("合并成一家…"))
        a_merge.setEnabled(len(sel) > 1)
        merged = [k for k in self.st.aliases if self.st.canon(k) == sel[0]["key"]] if len(sel) == 1 else []
        a_split = m.addAction(tr("拆开（{0} 个合并进来的名字）", len(merged)))
        a_split.setEnabled(bool(merged))
        a_show = m.addAction(tr("看这家的订单"))
        a_show.setEnabled(len(sel) == 1)
        got = m.exec(self.st_t.viewport().mapToGlobal(pos))
        if got == a_merge:
            names = [s["supplier"] for s in sorted(sel, key=lambda s: -s["n"])]
            name, ok = QInputDialog.getItem(self.win, tr("合并成一家"), tr("用哪个名字"), names, 0, False)
            if not ok:
                return
            target = next(s["key"] for s in sel if s["supplier"] == name)
            self.st.merge_suppliers([s["key"] for s in sel], target)
            self.refresh()
        elif got == a_split:
            for k in merged:
                self.st.unmerge(k)
            self.refresh()
        elif got == a_show:
            self._show_supplier(self.sups.index(sel[0]))

    def suggest_merge(self):
        if self.st is None:
            return
        sugg = OT.suggest_merges(self.st)
        if not sugg:
            self.win.show_toast(tr("没有名字相近的供应商"))
            return
        disp = {s["key"]: s["supplier"] for s in self.sups}
        dlg = QDialog(self.win)
        dlg.setWindowTitle(tr("名字相近的供应商"))
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 12)
        lb = QLabel(tr("勾上的合并成一家（用左边的名字） ⓘ"))
        lb.setToolTip(tr("名字是另一个的开头（ZAMBETTI / ZAMBETTI INVERNALE）。是同一家公司就勾上；不同的产品线也可以合并，统计到货天数更准"))
        v.addWidget(lb)
        lst = QListWidget()
        lst.setMinimumSize(520, 360)
        for k, sim in sugg:
            it = QListWidgetItem(f"{disp.get(k, k)}  ←  " + " · ".join(disp.get(x, x) for x in sim))
            it.setData(Qt.UserRole, (k, sim))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            lst.addItem(it)
        v.addWidget(lst, 1)
        row = QHBoxLayout()
        row.addStretch()
        cancel, ok = QPushButton(tr("取消")), QPushButton(tr("合并勾上的"))
        ok.setObjectName("Primary")
        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)
        from erp_widgets import fit_to_screen
        fit_to_screen(dlg)
        if dlg.exec() != QDialog.Accepted:
            return
        n = 0
        for j in range(lst.count()):
            it = lst.item(j)
            if it.checkState() == Qt.Checked:
                k, sim = it.data(Qt.UserRole)
                self.st.merge_suppliers(sim, k)
                n += len(sim)
        if n:
            self.refresh()
            self.win.show_toast(tr("合并了 {0} 个名字", n))

    # ================================================================ 预算表
    def _b_text(self, i, c):
        b = self.buds[i]
        return [b["month"], b["store_name"], _money(b["budget"]) if b["budget"] else "", _money(b["amount"]) if b["n"] else "",
                f"{b['pct']:.0%}" if b["pct"] is not None else "",
                str(b["n"]) if b["n"] else ""][c]

    def _b_key(self, i, c):
        b = self.buds[i]
        return [b["month"], None, b["budget"] or 0, b["amount"], b["pct"] or 0, b["n"]][c]

    def _b_fill(self, i, k):
        b = self.buds[i]
        self.ui.table_set_row(self.b_t, k, [self._b_text(i, c) for c in range(6)])
        if b["pct"] is not None and b["pct"] >= 0.9:
            it = self.b_t.item(k, 4)
            if it is not None:
                it.setForeground(QColor(self.ui.T("err" if b["pct"] > 1 else "warn")))
                f = it.font()
                f.setBold(True)
                it.setFont(f)

    def edit_budget(self, i):
        if i is None or self.st is None:
            return
        b = self.buds[i]
        v, ok = QInputDialog.getDouble(self.win, tr("预算"), tr("{0} · {1} 预算（不含税，0 = 不设）", b["month"], b["store_name"]),
                                       float(b["budget"] or 0), 0, 99999999, 0)
        if ok:
            self.st.set_budget(b["month"], b["store"], v or None)
            self.refresh()

    # ================================================================ 文件夹、Excel
    def choose_folder(self):
        start = self.cfg.get("folder") or ""
        f = QFileDialog.getExistingDirectory(self.win, tr("选择共享文件夹"), start)
        if not f:
            return
        self.cfg["folder"] = f
        x = OT.find_excel(f)
        if x:
            self.cfg["excel"] = x
        self._save()
        self.st = None
        self._shown = False
        self._on_show()

    def open_folder(self):
        f = self._folder()
        if f:
            from main import open_path
            open_path(f)

    def open_settings(self):
        dlg = QDialog(self.win)
        dlg.setWindowTitle(tr("订单跟踪设置"))
        g = QGridLayout(dlg)
        g.setContentsMargins(18, 16, 18, 14)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        from erp_widgets import tip_label
        e_dir, e_x, e_me = QLineEdit(self.cfg.get("folder") or ""), QLineEdit(self._excel()), QLineEdit(self._me())
        for e in (e_dir, e_x):
            e.setMinimumWidth(420)
        b_dir, b_x = QPushButton(tr("选择…")), QPushButton(tr("选择…"))
        b_dir.clicked.connect(lambda: e_dir.setText(QFileDialog.getExistingDirectory(dlg, tr("选择共享文件夹"), e_dir.text()) or e_dir.text()))
        b_x.clicked.connect(lambda: e_x.setText(QFileDialog.getOpenFileName(dlg, tr("选择 Excel"), e_x.text(),
                                                                             tr("Excel 文件 (*.xlsx *.xlsm)"))[0] or e_x.text()))
        g.addWidget(tip_label(tr("共享文件夹"), tr("大家都能看到的同一个文件夹（Synology Drive）。订单记录放在里面的「{0}」", OT.DATA_DIR)), 0, 0)
        g.addWidget(e_dir, 0, 1)
        g.addWidget(b_dir, 0, 2)
        g.addWidget(tip_label("Excel", tr("PIANO ACQUISTO MESILE：「从 Excel 同步」读这一份")), 1, 0)
        g.addWidget(e_x, 1, 1)
        g.addWidget(b_x, 1, 2)
        e_rec = QLineEdit(REC.record_root(self.settings))
        e_rec.setMinimumWidth(420)
        b_rec = QPushButton(tr("选择…"))
        b_rec.clicked.connect(lambda: e_rec.setText(QFileDialog.getExistingDirectory(dlg, tr("选择订单明细文件夹"), e_rec.text()) or e_rec.text()))
        g.addWidget(tip_label(tr("订单明细"), tr("历史订单文件（RECORD ORDINE）：01–05 门店自己订的，06 总仓，07 DETERSIVO。"
                                              "用来看没到货的单订了哪些商品（在途）")), 2, 0)
        g.addWidget(e_rec, 2, 1)
        g.addWidget(b_rec, 2, 2)
        g.addWidget(tip_label(tr("我的名字"), tr("修改记录里显示的名字")), 3, 0)
        g.addWidget(e_me, 3, 1)
        cb_auto = QCheckBox(tr("程序生成的订单自动登记"))
        cb_auto.setToolTip(tr("总仓采购、DETERSIVO采购生成订单文件时，每家供应商 × 每家门店 / BMS 登记一张「待登记」的单；发给供应商后确认"))
        cb_auto.setChecked(self.cfg.get("auto_register") is not False)
        g.addWidget(cb_auto, 4, 1)
        cb_wx = QCheckBox(tr("程序修改写回 Excel"))
        cb_wx.setToolTip(tr("程序里确认、到货、改备注、新增的订单（确认已下单后）、改的预算，自动写进同步文件夹里的 Excel；"
                            "Excel 正被人打开时先排队，关了再写；每次写之前备份原文件（共享文件夹「{0}\\{1}」，留最近 {2} 份）",
                            OT.DATA_DIR, XS.BACKUP_DIR, XS.KEEP_BACKUPS))
        cb_wx.setChecked(self._write_on())
        g.addWidget(cb_wx, 6, 1)
        cb_ded = QCheckBox(tr("建议量扣掉在途"))
        cb_ded.setToolTip(tr("打开（默认）：总仓采购、门店采购、检查门店清单算建议量时，扣掉已经订了还没到货的数量；关掉：在途箱数只显示，不扣"))
        cb_ded.setChecked(REC.deduct_on(self.settings))
        g.addWidget(cb_ded, 5, 1)
        row = QHBoxLayout()
        row.addStretch()
        cancel, ok = QPushButton(tr("取消")), QPushButton(tr("保存"))
        ok.setObjectName("Primary")
        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        g.addLayout(row, 7, 0, 1, 3)
        if dlg.exec() != QDialog.Accepted:
            return
        rec = e_rec.text().strip()
        if rec and os.path.normcase(rec) != os.path.normcase(REC.RECORD_DEFAULT):
            self.cfg["record"] = rec
        else:
            self.cfg.pop("record", None)
        REC.forget()
        self.cfg["folder"] = e_dir.text().strip()
        self.cfg["excel"] = e_x.text().strip()
        self.cfg["me"] = e_me.text().strip()
        self.cfg["auto_register"] = cb_auto.isChecked()
        self.cfg["deduct_transit"] = cb_ded.isChecked()
        self.cfg["write_excel"] = cb_wx.isChecked()
        self._save()
        self.st = None
        self._on_show()

    def _save(self):
        import settings as ST
        ST.save_settings(self.settings)

    def _auto_sync(self):
        """
        Excel 改过（修改时间和上次同步的不一样）就在后台同步一次：打开页面时、页面开着每 20 秒看一次（2026-11-02 用户：不想每次手动点）。
        刚存完 10 秒内先不读（同步盘可能还在传、文件可能只写了一半），下一轮再读
        """
        self._check_files()
        x = self._excel()
        if not x:
            return
        try:
            mt = os.path.getmtime(x)
        except OSError:
            return
        if mt != self.cfg.get("excel_mtime") and mt != self._bad_mtime and time.time() - mt > 10:
            self._try_mtime = mt
            self.sync_excel(manual=False)
            return
        # 程序里改了还没写进 Excel（2026-11-04 用户：双向同步）：Excel 没被人打开就写；开着就等下一轮（20 秒）
        if self._write_on() and self.st is not None and not self.syncing \
                and time.time() - getattr(self, "_last_write_try", 0) > 15:
            try:
                todo = XS.pending(self.st, self._me())
            except Exception:
                todo = []
            if todo:
                self._last_write_try = time.time()
                if XS.locked(x):
                    self._write_state = ("busy", len(XS.pending(self.st, self._me(), everyone=True)))
                    self._update_src()
                    return
                self.sync_excel(manual=False)

    def _check_files(self):
        """
        总仓 / 门店采购生成的订单文件改过（减少 / 砍掉箱数）→ 订单跟踪的箱数、金额跟着改（2026-11-04 用户要同步回程序）。
        后台读，最多 30 秒一次；只打开改过的文件（cache/record_index.json 记着大小 + 修改时间）
        """
        if self.st is None or getattr(self, "_files_busy", False) or time.time() - getattr(self, "_files_t", 0) < 30:
            return
        self._files_busy, self._files_t = True, time.time()
        folder, me, settings = self.st.folder, self._me(), self.settings

        def work():
            msgs = []
            try:
                st = OT.Store(folder, me=me)
                st.reload()
                idx = REC.Index(REC.cache_path())
                ch = REC.sync_files(st, settings, log=msgs.append, index=idx)
                idx.save()
                self.sig.files.emit((ch, msgs))
            except Exception as e:
                self.sig.files.emit(([], [tr("⚠ 订单文件读不了：{0}", e)]))
        threading.Thread(target=work, daemon=True).start()

    def _files_done(self, payload):
        ch, msgs = payload
        self._files_busy = False
        for m in msgs:
            self.win.log(tr("订单跟踪：") + m)
        if ch:
            self.win.show_toast(tr("✔ 订单文件改过：{0} 张单的箱数 / 金额已更新", len(ch)))
            if self.st is not None:
                self.st.reload()
            if self.page.isVisible():
                self.refresh()

    def _write_on(self):
        return self.cfg.get("write_excel") is not False

    def sync_excel(self, manual=True):
        x = self._excel()
        if not x or self.st is None or self.syncing:
            if manual and not x:
                self.win.show_toast(tr("没找到 Excel：在「更多 → 设置…」里选"))
            return
        self.syncing = True
        self.btn_sync.setEnabled(False)
        self.btn_sync.setText(tr("同步中…"))
        folder, me, write = self.st.folder, self._me(), self._write_on()

        def work():
            try:
                st = OT.Store(folder, me=me)
                st.reload()
                res = OT.import_file(st, x)
                if write and not res.get("stale"):
                    res["write"] = XS.writeback(st, x, me, imported=res)      # 程序里的修改写进 Excel
                res["mtime"] = os.path.getmtime(x)
                self.sig.done.emit((res, None, manual))
            except Exception as e:
                self.sig.done.emit((None, str(e), manual))
        threading.Thread(target=work, daemon=True).start()

    def _sync_done(self, payload):
        res, err, manual = payload
        self.syncing = False
        self.btn_sync.setText(tr("同步 Excel"))
        if err:
            self.btn_sync.setEnabled(True)
            if not manual:                # 自动同步失败：这个版本的 Excel 不再自动重试（不然每 20 秒报一次），Excel 再存一次或手动点再试
                self._bad_mtime = self._try_mtime
            self.win.log(tr("⚠ 订单跟踪：Excel 同步失败：{0}", err))
            if manual:
                QMessageBox.warning(self.win, tr("同步 Excel"), tr("同步失败：{0}", err))
            return
        if res.get("stale"):                      # 别的电脑刚写过 Excel、同步盘还没收到：等下一轮
            self.win.log(tr("订单跟踪：同步盘还没收到最新的 Excel，稍后自动再读"))
            self.btn_sync.setEnabled(True)
            return
        self.cfg["excel_mtime"] = res.get("mtime")
        self.cfg["synced_at"] = datetime.now().strftime("%m-%d %H:%M")
        self._save()
        w = res.get("write") or {}
        st_ = w.get("status")
        cf = w.get("conflicts") or {}
        if cf.get("files"):                       # 同步盘冲突副本：合并回来、挪进备份文件夹
            self.win.log(tr("订单跟踪：同步盘冲突副本 {0} 个已合并（恢复 {1} 处、补回 {2} 张单），副本挪到 Backup Excel\\冲突副本",
                            cf["files"], cf["restored"], cf["added"]))
            self.win.show_toast(tr("✔ 已合并同步盘冲突副本：恢复 {0} 处、补回 {1} 张单", cf["restored"], cf["added"]))
        if st_ == "ok":
            self.cfg["written_at"] = datetime.now().strftime("%m-%d %H:%M")
            self._save()
            self.win.log(tr("✔ 订单跟踪：程序里的修改已写入 Excel：{0} 处（写之前已备份原文件：{1}）", w["written"],
                            os.path.basename(w.get("backup") or "")))
            if manual or w["written"]:
                self.win.show_toast(tr("✔ 已写入 Excel：{0} 处", w["written"]))
        elif st_ in ("busy", "changed"):
            self.win.log(tr("订单跟踪：Excel 正被打开 / 刚被别人保存，修改先排队，关掉后自动写入"))
        for kind, oid, why in w.get("failed") or []:
            o = (self.st.orders.get(oid) if self.st else None) or {}
            self.win.log(tr("⚠ 没写进 Excel：{0} {1} · {2}（{3}）", _d(OT.to_date(o.get("date"))), o.get("supplier") or oid,
                            OT.STORE_NAMES.get(str(o.get("store")), ""), why))
        if w.get("failed"):
            self.win.show_toast(tr("⚠ {0} 处修改没写进 Excel（看日志）", len(w["failed"])))
        self._write_state = None
        msg = tr("Excel 同步：新的 {0} 张，更新 {1} 张，Excel 里删掉的 {2} 张", res["new"], res["updated"], res["removed"])
        if res.get("kept"):
            msg += tr("（另有 {0} 张 Excel 里没有了，但程序里改过，留着）", res["kept"])
        self.win.log("✔ " + msg)
        unk = res.get("unknown") or []
        for sh, col, title in unk:                  # 认不出是哪家门店的块：整块没读，一定要让人知道
            self.win.log(tr("⚠ Excel「{0}」表 {1} 列那一块认不出是哪家门店，没读：「{2}」", sh, col, title or "—"))
        if unk:
            self.win.show_toast(tr("⚠ Excel 里有 {0} 块认不出是哪家门店，没读（看日志）", len(unk)))
        elif manual or res["new"] or res["updated"]:
            self.win.show_toast(msg)
        self.load()

    def export(self):
        if not self.rows:
            return
        p, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"), os.path.join(
            self._folder() or "", f"Ordini_{datetime.now():%Y%m%d}.xlsx"), tr("Excel 文件 (*.xlsx)"))
        if not p:
            return
        try:
            OT.export_excel([self.rows[i] for i in self.ctl.visible], p)
            self.win.show_toast(tr("已导出：{0}", os.path.basename(p)))
        except Exception as e:
            QMessageBox.warning(self.win, tr("导出 Excel"), tr("导出失败：{0}", e))

    def badge(self, n):
        """侧边栏「采购订单跟踪」旁边的小红点数字 = 待登记的单数"""
        try:
            self.win.set_badge("tracking", int(n or 0))
        except Exception:
            pass

    def check_badge(self):
        """数一下待登记的单（共享文件夹有变化才重新读）；页面开着就整页刷新"""
        f = self._folder()
        if not f:
            self.badge(0)
            return
        try:
            if self.st is None or self.st.folder != f:
                self.st = OT.Store(f, me=self._me())
                self.st.reload()
                changed = True
            else:
                changed = self.st.changed()
                if changed:
                    self.st.reload()
        except Exception:
            return
        if changed and self.page.isVisible():
            self.refresh()
        else:
            self.badge(sum(1 for o in self.st.orders.values() if o.get("draft") and o.get("state") != "cancel"))
        if hasattr(self, "btn_sync"):
            self._auto_sync()                 # 页面没开着也同步（每分钟）：Excel 改了读进来、程序改了写进去

    def export_piano(self):
        """导出成和 PIANO ACQUISTO MESILE 一样的表：拿原来的 Excel 当模板，每月一张、六块门店并排（不改原文件）"""
        if self.st is None:
            return
        tpl = self._excel()
        if not tpl:
            tpl, _ = QFileDialog.getOpenFileName(self.win, tr("选择原来的 PIANO ACQUISTO MESILE（当模板）"),
                                                 self._folder(), tr("Excel 文件 (*.xlsx *.xlsm)"))
            if not tpl:
                return
        out, _ = QFileDialog.getSaveFileName(self.win, tr("导出成 PIANO 表"), os.path.join(
            self._folder() or "", f"PIANO ACQUISTO MESILE (programma) {datetime.now():%d%m}.xlsx"), tr("Excel 文件 (*.xlsx)"))
        if not out:
            return
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(tpl)):
            QMessageBox.warning(self.win, tr("导出成 PIANO 表"), tr("不能直接盖掉原来的 Excel，请换一个文件名"))
            return
        rows, st = list(self.rows), self.st
        self.win.show_toast(tr("正在导出…"))

        def work():
            try:
                import shutil
                import tempfile
                tmp = os.path.join(tempfile.mkdtemp(prefix="piano_"), "tpl.xlsx")
                shutil.copy2(tpl, tmp)                      # 原文件可能正被 Excel 打开
                self.sig.exported.emit((OT.export_piano(rows, st, tmp, out), out, None))
            except Exception as e:
                self.sig.exported.emit((None, out, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _export_done(self, payload):
        done, out, err = payload
        if err:
            QMessageBox.warning(self.win, tr("导出成 PIANO 表"), tr("导出失败：{0}", err))
            return
        self.win.log(tr("✔ 导出 PIANO 表：{0}（{1}）", out, "、".join(f"{n} {k}" for n, k in done)))
        self.win.show_toast(tr("已导出：{0}", os.path.basename(out)))
        try:
            from main import open_path
            open_path(os.path.dirname(out))
        except Exception:
            pass

    def restyle(self):
        if hasattr(self, "panel"):
            self.panel.restyle()
        if self.st is not None and self.rows:
            self.refresh()


class _OptDate(QWidget):
    """可以空着的日期：勾上才有日期（确认 / 到货 / 预计到货）"""

    def __init__(self, value=None, label=""):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.cb = QCheckBox(label)
        self.de = QDateEdit()
        self.de.setCalendarPopup(True)
        self.de.setDisplayFormat("yyyy-MM-dd")
        self.de.setMinimumWidth(120)
        d = OT.to_date(value)
        self.cb.setChecked(d is not None)
        self.de.setDate(QDate(d.year, d.month, d.day) if d else QDate.currentDate())
        self.de.setEnabled(d is not None)
        self.cb.toggled.connect(self.de.setEnabled)
        h.addWidget(self.cb)
        h.addWidget(self.de)
        h.addStretch()

    def value(self):
        if not self.cb.isChecked():
            return None
        return self.de.date().toString("yyyy-MM-dd")


class OrderDialog(QDialog):
    """新订单 / 改一张单。确定后 self.changes = 改了的字段，self.values = 全部字段，self.deleted = 点了删除"""

    def __init__(self, parent, page, order, row=None, hist=None):
        super().__init__(parent)
        self.setWindowTitle(tr("改订单") if row else tr("新订单"))
        self.order, self.changes, self.values, self.deleted = dict(order or {}), {}, {}, False
        o = self.order
        sups, buys = page.known()
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(12)
        if row:
            h = QLabel(f"{row['supplier']} · {row['store_name']}")
            h.setObjectName("PageTitle")
            h.setStyleSheet("font-size:16px;")
            v.addWidget(h)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        g.setColumnMinimumWidth(1, 200)
        g.setColumnMinimumWidth(3, 200)
        g.setColumnMinimumWidth(2, 20)

        def lab(text, r, c):
            l = QLabel(text)
            l.setObjectName("Muted")
            g.addWidget(l, r, c)

        self.e_date = QDateEdit()
        self.e_date.setCalendarPopup(True)
        self.e_date.setDisplayFormat("yyyy-MM-dd")
        d0 = OT.to_date(o.get("date")) or date.today()
        self.e_date.setDate(QDate(d0.year, d0.month, d0.day))
        self.e_store = QComboBox()
        for k, n in OT.STORES:
            self.e_store.addItem(n, k)
        self.e_store.setCurrentIndex(max(0, self.e_store.findData(str(o.get("store") or "1"))))
        lab(tr("下单日期"), 0, 0)
        g.addWidget(self.e_date, 0, 1)
        lab(tr("门店"), 0, 3)
        g.addWidget(self.e_store, 0, 4)
        self.e_sup = QComboBox()
        self.e_sup.setEditable(True)
        self.e_sup.addItems(sups)
        self.e_sup.setCurrentText(str(o.get("supplier") or ""))
        self.e_sup.setInsertPolicy(QComboBox.NoInsert)
        lab(tr("供应商"), 1, 0)
        g.addWidget(self.e_sup, 1, 1, 1, 4)
        self.e_buy = QComboBox()
        self.e_buy.setEditable(True)
        self.e_buy.addItems(buys)
        self.e_buy.setCurrentText(str(o.get("buyer") or ""))
        self.e_buy.setInsertPolicy(QComboBox.NoInsert)
        self.e_amt = QDoubleSpinBox()
        self.e_amt.setRange(0, 99999999)
        self.e_amt.setDecimals(2)
        self.e_amt.setGroupSeparatorShown(True)
        self.e_amt.setPrefix("€ ")
        self.e_amt.setValue(float(o.get("amount") or 0))
        self.e_amt.setToolTip(tr("不含税（Imponibile）"))
        lab(tr("谁下的"), 2, 0)
        g.addWidget(self.e_buy, 2, 1)
        lab(tr("金额（不含税）"), 2, 3)
        g.addWidget(self.e_amt, 2, 4)
        self.e_conf = _OptDate(o.get("confirmed"), tr("已确认"))
        self.e_arr = _OptDate(o.get("arrived"), tr("已到货"))
        lab(tr("确认"), 3, 0)
        g.addWidget(self.e_conf, 3, 1)
        lab(tr("到货"), 3, 3)
        g.addWidget(self.e_arr, 3, 4)
        self.e_exp = _OptDate(o.get("expected"), tr("手动"))
        auto = row["expected"] if row and not row.get("expected_manual") else None
        self.e_exp.setToolTip(tr("不勾 = 自动（{0}）", _d(auto)) if auto else tr("不勾 = 按这家供应商以往到货的天数推算"))
        self.e_state = QComboBox()
        for k, n in ((None, tr("正常")), ("partial", tr("部分到货")), ("stuck", tr("卡住了")), ("cancel", tr("已取消"))):
            self.e_state.addItem(n, k)
        self.e_state.setCurrentIndex(max(0, self.e_state.findData(o.get("state"))))
        lab(tr("预计到货"), 4, 0)
        g.addWidget(self.e_exp, 4, 1)
        lab(tr("情况"), 4, 3)
        g.addWidget(self.e_state, 4, 4)
        self.e_draft = None
        if o.get("draft"):
            self.e_draft = QCheckBox(tr("待登记（还没发给供应商）"))
            self.e_draft.setChecked(True)
            self.e_draft.setToolTip(tr("发给供应商以后去掉勾，就算正式下单（下单日期按上面的日期）"))
            g.addWidget(self.e_draft, 7, 1, 1, 4)
        self.e_kind = QCheckBox(tr("DETER（洗涤批发商单，不跟踪确认 / 到货）"))
        self.e_kind.setChecked(o.get("kind") == "deter")
        g.addWidget(self.e_kind, 5, 1, 1, 4)
        self.e_note = QLineEdit(str(o.get("note") or ""))
        lab(tr("备注"), 6, 0)
        g.addWidget(self.e_note, 6, 1, 1, 4)
        v.addLayout(g)
        if hist:
            hl = QLabel(tr("修改记录"))
            hl.setObjectName("Muted")
            v.addWidget(hl)
            lst = QListWidget()
            lst.setMaximumHeight(130)
            lst.addItems(history_lines(hist))
            v.addWidget(lst)
        if row and row.get("ref"):
            ref = QLabel(tr("Excel：{0}", row["ref"]))
            ref.setObjectName("Faint")
            v.addWidget(ref)
        row_b = QHBoxLayout()
        if row:
            b_del = QPushButton(tr("删除"))
            b_del.setObjectName("Link")
            b_del.clicked.connect(self._delete)
            row_b.addWidget(b_del)
        row_b.addStretch()
        cancel, ok = QPushButton(tr("取消")), QPushButton(tr("保存"))
        ok.setObjectName("Primary")
        ok.setDefault(True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._ok)
        row_b.addWidget(cancel)
        row_b.addWidget(ok)
        v.addLayout(row_b)
        self.setMinimumWidth(620)
        from erp_widgets import fit_to_screen
        fit_to_screen(self)

    def _delete(self):
        if QMessageBox.question(self, tr("删除"), tr("删除这张单？（修改记录里留着）")) == QMessageBox.Yes:
            self.deleted = True
            self.accept()

    def _ok(self):
        sup = self.e_sup.currentText().strip()
        if not sup:
            QMessageBox.warning(self, tr("保存"), tr("请填供应商"))
            return
        amt = round(self.e_amt.value(), 2)
        old_amt = self.order.get("amount")
        vals = {"date": self.e_date.date().toString("yyyy-MM-dd"), "store": self.e_store.currentData(),
                "supplier": sup, "buyer": self.e_buy.currentText().strip() or None,
                "amount": None if (amt == 0 and old_amt in (None, "")) else amt,
                "confirmed": self.e_conf.value(), "arrived": self.e_arr.value(), "expected": self.e_exp.value(),
                "state": self.e_state.currentData(), "kind": "deter" if self.e_kind.isChecked() else None,
                "note": self.e_note.text().strip() or None}
        if self.e_draft is not None:
            vals["draft"] = True if self.e_draft.isChecked() else None
        self.values = vals
        self.changes = {k: v for k, v in vals.items() if (self.order.get(k) or None) != (v or None) or
                        (k == "amount" and self.order.get(k) != v and v is not None)}
        self.accept()


class RecordLinesDialog(QDialog):
    """一张没到货的单订了哪些商品（订单文件里 Colli > 0 的行）"""
    COLS = [(0, "门店", 90), (1, "条码", 120), (2, "编号", 70), (3, "商品", 300), (4, "每箱", 60), (5, "箱数", 60),
            (6, "件数", 70)]

    def __init__(self, parent, page, r, info, rows=None):
        super().__init__(parent)
        self.page, self.r, self.info = page, r, info
        self._pre = rows                       # 已经在「处理中」窗口里读好的行（open_lines）
        self.setWindowTitle(tr("{0} · {1} · {2}", r["supplier"], r["store_name"], _d(r["date"])))
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)
        top = QHBoxLayout()
        name = QLabel(os.path.basename(info["file"]) + "  ⓘ")
        name.setObjectName("Section")
        name.setToolTip(info["file"])
        top.addWidget(name, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("条码、编号、商品"))
        self.search.setFixedWidth(200)
        top.addWidget(self.search)
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        top.addWidget(self.count)
        v.addLayout(top)
        self.rows = self._load()
        ui = page.ui
        self.t = ui.make_table([tr(h) for _f, h, _w in self.COLS], numeric_cols=(4, 5, 6))
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, (_f, _h, wd) in enumerate(self.COLS):
            self.t.setColumnWidth(c, wd)
        from erp_widgets import TableSortFilter, fit_to_screen
        import settings as ST
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, key=self._key,
                                   hide=self._hidden, numeric=(4, 5, 6))
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        self.ctl.remember(page.cfg, "lines", lambda: ST.save_settings(page.settings), [f for f, _h, _w in self.COLS])
        self.search.textChanged.connect(lambda _: self.ctl.render())
        v.addWidget(self.t, 1)
        foot = QHBoxLayout()
        tot = QLabel(tr("{0} 种 · {1:g} 箱", len(self.rows), sum(x["colli"] for _s, x in self.rows)))
        tot.setObjectName("Faint")
        foot.addWidget(tot)
        foot.addStretch()
        b_open = QPushButton(tr("打开文件"))
        b_open.clicked.connect(self._open)
        b_close = QPushButton(tr("关闭"))
        b_close.clicked.connect(self.accept)
        foot.addWidget(b_open)
        foot.addWidget(b_close)
        v.addLayout(foot)
        self.resize(820, 520)
        fit_to_screen(self)
        self.ctl.render()

    def _load(self):
        if self._pre is not None:
            return self._pre
        idx = REC.Index(REC.cache_path())
        ent = idx.data.get(self.info["file"]) or {}
        lines = ent.get("lines")
        if lines is None:
            try:
                lines = REC.read_lines(self.info["path"], REC.folder_store(self.info["file"]))
            except Exception as e:
                self.page.win.show_toast(tr("读不了：{0}", e))
                lines = {}
        f = {"store": REC.folder_store(self.info["file"])}
        return REC.order_lines(self.r, f, lines)

    def _text(self, i, c):
        sid, x = self.rows[i]
        f = self.COLS[c][0]
        if f == 0:
            return REC.STORE_NAMES.get(sid, sid)
        if f == 1:
            return x["ean"]
        if f == 2:
            return x["code"]
        if f == 3:
            return x["desc"]
        if f == 4:
            return f"{x['pack']:g}"
        if f == 5:
            return f"{x['colli']:g}"
        if f == 6:
            return f"{x['colli'] * (x['pack'] or 1):g}"
        return ""

    def _key(self, i, c):
        _sid, x = self.rows[i]
        f = self.COLS[c][0]
        if f == 4:
            return x["pack"]
        if f == 5:
            return x["colli"]
        if f == 6:
            return x["colli"] * (x["pack"] or 1)
        return None

    def _hidden(self, i):
        q = self.search.text().strip().lower()
        if not q:
            return False
        _sid, x = self.rows[i]
        return not any(q in str(v).lower() for v in (x["ean"], x["code"], x["desc"]))

    def _fill(self, i, k):
        self.page.ui.table_set_row(self.t, k, [self._text(i, c) for c in range(len(self.COLS))])

    def _open(self):
        from erp_widgets import open_file
        open_file(self, self.info["path"])


def read_order_lines(r, info):
    """一张单在订单文件里的行（读过的用缓存；大文件第一次读要几秒）"""
    idx = REC.Index(REC.cache_path())
    ent = idx.data.get(info["file"]) or {}
    lines = ent.get("lines")
    if lines is None:
        lines = REC.read_lines(info["path"], REC.folder_store(info["file"]))
    return REC.order_lines(r, {"store": REC.folder_store(info["file"])}, lines)


def open_lines(parent, page, r, info):
    """「查看订单明细」：先在「处理中」窗口里读文件，读好再打开明细窗口"""
    from erp_widgets import BusyDialog
    rows, err = BusyDialog.run(parent, tr("查看订单明细"), tr("正在读取订单文件 {0}…", os.path.basename(info["file"])),
                               lambda: read_order_lines(r, info))
    if err:
        QMessageBox.warning(parent, tr("查看订单明细"), tr("订单文件读不了：{0}", err))
        return
    RecordLinesDialog(parent, page, r, info, rows=rows).exec()
