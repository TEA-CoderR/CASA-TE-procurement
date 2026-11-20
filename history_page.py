"""
历史记录页（2026-11-16 照设计稿「⑫ 历史记录」重做）：
  左：门店（带次数）· 时间（本周 / 上周 / 这个月 / 全部）· 占用空间 + 保留多久
  中：大标题 + 一句话（这个月几次 · 采购多少 · 平均缺几个）+ 最近 8 周每周采购额小柱子 + 搜索；
      按天分组，每次比价一行（时间 · 门店 · 分给几家的彩色条 · 金额 · 缺几个 · 方案 / 改过）
  右：选中这一次的详情 + 打开这次结果 / 打开文件夹 / 删除这条记录
老的 hist_tree 还在（藏着），MainWindow 里的 view_history / delete_history… 照用：选中的那条由 _hist_sel 决定。
"""
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget, QLineEdit, QComboBox)

from theme import T
from page_kit import set_qss as _set_qss
from i18n import tr
import result_page as RP

WEEKDAY = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class WeekBars(QWidget):
    """最近几周每周采购额：小柱子，本周蓝色"""

    def __init__(self):
        super().__init__()
        self.vals = []
        self.setFixedSize(8 * 14, 44)
        self.setMouseTracking(True)

    def set(self, vals):
        self.vals = vals
        self.setToolTip("\n".join(f"{t}: €{v:,.0f}" for t, v in vals))
        self.update()

    def paintEvent(self, _e):
        if not self.vals:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        mx = max(v for _t, v in self.vals) or 1
        for i, (_t, v) in enumerate(self.vals):
            h = max(3.0, (self.height() - 2) * v / mx)
            p.setBrush(QColor(T("accent") if i == len(self.vals) - 1 else T("accent_ring")))
            p.drawRoundedRect(QRectF(i * 14, self.height() - h, 10, h), 3, 3)
        p.end()


class NavBtn(QPushButton):
    def __init__(self, text, count=""):
        super().__init__()
        self.setCheckable(True)
        self.setObjectName("HistNav")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 10, 0)
        a = QLabel(text)
        h.addWidget(a, 1)
        if count != "":
            h.addWidget(RP.mono(str(count), 12, 500, "muted"))
        for w in self.findChildren(QWidget):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)


class RunRow(QPushButton):
    """一次比价一行"""

    def __init__(self, e):
        super().__init__()
        self.setCheckable(True)
        self.setObjectName("ResScen")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(48)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(14)
        t = RP.mono((e.get("time") or "")[11:16], 12, 500, "muted")
        t.setFixedWidth(42)
        h.addWidget(t)
        s = QLabel(e.get("store") or "—")
        s.setStyleSheet("font-weight:600;")
        s.setFixedWidth(108)
        h.addWidget(s)
        bar = RP.SplitBar(8)
        split = e.get("split") or []
        if split:
            bar.set([(v, RP.PALETTE[i % len(RP.PALETTE)], False) for i, (_n, v) in enumerate(split)])
            bar.setToolTip(" · ".join(f"{n} €{v:,.0f}" for n, v in split))
        else:
            tot = e.get("total_rows") or 0
            bar.set([(e.get("priced_rows") or 0, "accent", False), (max(0, tot - (e.get("priced_rows") or 0)), "warn", True)])
        h.addWidget(bar, 1)
        a = RP.mono(f"€{e.get('final_cost') or 0:,.0f}", 13)
        a.setFixedWidth(84)
        a.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(a)
        m = e.get("missing") or 0
        c = RP.chip(tr("缺 {0}", m) if m else tr("全有"), "warn" if m else "ok")
        c.setFixedSize(64, 22)
        c.setAlignment(Qt.AlignCenter)
        h.addWidget(c, 0, Qt.AlignVCenter)
        tags = QHBoxLayout()
        tags.setSpacing(6)
        tw = QWidget()
        tw.setFixedWidth(150)
        tw.setLayout(tags)
        tags.setContentsMargins(0, 0, 0, 0)
        tags.addStretch()
        for tag in ([tr(e["scenario"])] if e.get("scenario") else []) + ([tr("改过 {0} 次", e["edits"])] if e.get("edits") else []):
            lb = QLabel(tag)
            lb.setStyleSheet(f"background:{T('accent_soft')}; color:{T('accent')}; border-radius:6px; padding:0 8px; font-size:11px;")
            lb.setFixedHeight(20)
            tags.addWidget(lb, 0, Qt.AlignVCenter)
        h.addWidget(tw)
        if e.get("warning"):
            self.setToolTip(tr("有起订供应商没达标"))
        for w in self.findChildren(QWidget):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)


QSS = """
QPushButton#HistNav {{ background:transparent; border:none; border-radius:9px; color:{head}; text-align:left; }}
QPushButton#HistNav:hover {{ background:{hover}; }}
QPushButton#HistNav:checked {{ background:{sel}; color:{text}; border-left:3px solid {accent}; }}
QFrame#HistSide {{ background:{surface}; border:1px solid {line}; border-radius:14px; }}
"""


def qss():
    return RP.qss() + QSS.format(head=T("head"), hover=T("hover"), sel=T("accent_soft"), text=T("text"),
                                 accent=T("accent"), surface=T("surface"), line=T("line"))


def build(win, lay):
    page = lay.parentWidget()
    _set_qss(page, qss())
    win._hist_page = page
    win._hist_store, win._hist_period, win._hist_sel = None, 3, None
    row = QHBoxLayout()
    row.setSpacing(16)
    lay.addLayout(row, 1)

    # 左：筛选
    side = QFrame()
    side.setObjectName("HistSide")
    side.setFixedWidth(210)
    sv = QVBoxLayout(side)
    sv.setContentsMargins(10, 14, 10, 14)
    sv.setSpacing(4)
    cap = QLabel(tr("门店"))
    cap.setObjectName("KpiLabel")
    cap.setContentsMargins(8, 0, 0, 6)
    sv.addWidget(cap)
    win._hist_store_box = QVBoxLayout()
    win._hist_store_box.setSpacing(2)
    sv.addLayout(win._hist_store_box)
    sv.addSpacing(14)
    cap = QLabel(tr("时间"))
    cap.setObjectName("KpiLabel")
    cap.setContentsMargins(8, 0, 0, 6)
    sv.addWidget(cap)
    win._hist_period_btns = []
    for i, t in enumerate((tr("本周"), tr("上周"), tr("这个月"), tr("全部"))):
        b = NavBtn(t)
        b.setChecked(i == 3)
        b.clicked.connect(lambda _=False, k=i: _set_period(win, k))
        sv.addWidget(b)
        win._hist_period_btns.append(b)
    sv.addStretch()
    box = QFrame()
    box.setObjectName("Panel")
    bv = QVBoxLayout(box)
    bv.setContentsMargins(12, 10, 12, 10)
    bv.setSpacing(4)
    bv.addWidget(RP.muted(tr("占用空间"), 12))
    win._hist_mb = RP.mono("—", 14)
    bv.addWidget(win._hist_mb)
    win.keep_box.setParent(None)
    bv.addWidget(win.keep_box)
    sv.addWidget(box)
    row.addWidget(side, 0, Qt.AlignTop)

    # 中
    mid = QVBoxLayout()
    mid.setSpacing(16)
    head = QHBoxLayout()
    hv = QVBoxLayout()
    hv.setSpacing(6)
    t = QLabel(tr("历史记录"))
    t.setObjectName("HeroTitle")
    hv.addWidget(t)
    win._hist_line = QLabel("")
    win._hist_line.setObjectName("HeroSub")
    win._hist_line.setTextFormat(Qt.RichText)
    hv.addWidget(win._hist_line)
    head.addLayout(hv, 1)
    win.hist_filter.setParent(None)
    win.hist_filter.setFixedWidth(200)
    win.hist_filter.textChanged.connect(lambda _t: render(win))
    head.addWidget(win.hist_filter, 0, Qt.AlignBottom)
    win._hist_weeks = WeekBars()
    head.addWidget(win._hist_weeks, 0, Qt.AlignBottom)
    mid.addLayout(head)
    win._hist_days = QVBoxLayout()
    win._hist_days.setSpacing(18)
    mid.addLayout(win._hist_days)
    from erp_widgets import EmptyState
    win.hist_empty.setParent(None)
    mid.addWidget(win.hist_empty)
    mid.addStretch()
    row.addLayout(mid, 1)

    # 右：详情
    det = QFrame()
    det.setObjectName("Panel")
    det.setFixedWidth(330)
    dv = QVBoxLayout(det)
    dv.setContentsMargins(20, 20, 20, 20)
    dv.setSpacing(14)
    win._hist_det = dv
    win._hist_det_frame = det
    row.addWidget(det, 0, Qt.AlignTop)


def _set_period(win, k):
    win._hist_period = k
    for i, b in enumerate(win._hist_period_btns):
        b.setChecked(i == k)
    render(win)


def _set_store(win, st):
    win._hist_store = st
    render(win)


def _period_ok(e, k, now):
    try:
        t = datetime.strptime((e.get("time") or "")[:10], "%Y-%m-%d")
    except ValueError:
        return k == 3
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    if k == 0:
        return t >= monday
    if k == 1:
        return monday - timedelta(days=7) <= t < monday
    if k == 2:
        return t.year == now.year and t.month == now.month
    return True


def _day_name(d, now):
    try:
        t = datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return d
    lab = f"{tr(WEEKDAY[t.weekday()])} {t:%d/%m}"
    if t.date() == now.date():
        return tr("今天") + " · " + lab
    if t.date() == (now - timedelta(days=1)).date():
        return tr("昨天") + " · " + lab
    return lab


def render(win, force=False):
    """按门店 / 时间 / 搜索重新排一遍（refresh_history 读完记录后调）。
    页面没在显示时只记下来（_hist_dirty），切过去再画（2026-11-20：每家比完 / 切换门店都重画，界面停 0.7 秒）"""
    if not hasattr(win, "_hist_days"):
        return
    page = getattr(win, "_hist_page", None)
    if not force and page is not None and not page.isVisible():
        win._hist_dirty = True
        return
    win._hist_dirty = False
    _set_qss(win._hist_page, qss())
    runs = getattr(win, "hist_runs", []) or []
    now = datetime.now()
    # 左边门店
    RP._clear(win._hist_store_box)
    counts = {}
    for e in runs:
        counts[e.get("store") or "—"] = counts.get(e.get("store") or "—", 0) + 1
    for st, n in [(None, len(runs))] + sorted(counts.items()):
        b = NavBtn(tr("全部门店") if st is None else st, n)
        b.setChecked(win._hist_store == st)
        b.clicked.connect(lambda _=False, s=st: _set_store(win, s))
        win._hist_store_box.addWidget(b)
    import history
    mb = history.disk_usage() / 1024 / 1024
    win._hist_mb.setText(f"{mb:.1f} MB")

    text = (win.hist_filter.text() or "").strip().lower()
    shown = [e for e in runs
             if (win._hist_store is None or (e.get("store") or "—") == win._hist_store)
             and _period_ok(e, win._hist_period, now)
             and (not text or text in " ".join(str(e.get(k) or "") for k in ("store", "main_file", "folder", "scenario")).lower()
                  or any(text in (n or "").lower() for n, _v in (e.get("split") or [])))]
    # 一句话 + 每周柱子（这个月）
    month = [e for e in runs if _period_ok(e, 2, now)]
    if month:
        tot = sum(e.get("final_cost") or 0 for e in month)
        avg = sum(e.get("missing") or 0 for e in month) / len(month)
        win._hist_line.setText(tr("这个月 <b>{0}</b> 次比价 · 采购 <b>€{1:,.0f}</b> · 平均每次缺 <b style='color:{3}'>{2:.0f}</b> 个",
                                  len(month), tot, avg, T("warn")))
    else:
        win._hist_line.setText(tr("共 {0} 次比价", len(runs)))
    weeks = []
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    for k in range(7, -1, -1):
        a = monday - timedelta(days=7 * k)
        b = a + timedelta(days=7)
        v = 0.0
        for e in runs:
            try:
                t = datetime.strptime((e.get("time") or "")[:10], "%Y-%m-%d")
            except ValueError:
                continue
            if a <= t < b:
                v += e.get("final_cost") or 0
        weeks.append((tr("第 {0} 周", a.isocalendar()[1]), v))
    win._hist_weeks.set(weeks)

    # 按天
    RP._clear(win._hist_days)
    days = {}
    for e in shown:
        days.setdefault((e.get("time") or "")[:10], []).append(e)
    ids = [e["id"] for e in shown]
    if win._hist_sel not in ids:
        cur = (win.last_summary or {}).get("history_id")
        win._hist_sel = cur if cur in ids else (ids[0] if ids else None)
    for d in sorted(days, reverse=True):
        es = sorted(days[d], key=lambda e: e.get("time") or "", reverse=True)
        h = QHBoxLayout()
        h.setSpacing(10)
        n = QLabel(_day_name(d, now))
        n.setStyleSheet("font-weight:600;")
        h.addWidget(n)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{T('line')};")
        h.addWidget(line, 1)
        h.addWidget(RP.mono(tr("{0} 次 · €{1:,.0f}", len(es), sum(e.get("final_cost") or 0 for e in es)), 12, 500, "muted"))
        win._hist_days.addLayout(h)
        for e in es:
            r = RunRow(e)
            r.setChecked(e["id"] == win._hist_sel)
            r.clicked.connect(lambda _=False, i=e["id"]: _select(win, i))
            r.mouseDoubleClickEvent = lambda _ev, i=e["id"]: (_select(win, i), win.view_history())
            win._hist_days.addLayout(_wrap(r))
    win.hist_empty.setVisible(not shown)
    _detail(win, next((e for e in shown if e["id"] == win._hist_sel), None))


def _wrap(w):
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(w)
    return h


def _select(win, hid):
    win._hist_sel = hid
    render(win)


def _detail(win, e):
    dv = win._hist_det
    RP._clear(dv)
    win._hist_det_frame.setVisible(e is not None)
    if not e:
        return
    try:
        t = datetime.strptime((e.get("time") or "")[:16], "%Y-%m-%d %H:%M")
        when = _day_name(t.strftime("%Y-%m-%d"), datetime.now()) + f" · {t:%H:%M}"
    except ValueError:
        when = e.get("time") or ""
    dv.addWidget(RP.muted(when))
    n = QLabel(e.get("store") or "—")
    n.setStyleSheet("font-size:20px; font-weight:700;")
    dv.addWidget(n)
    f = RP.muted(e.get("main_file") or "")
    f.setToolTip(e.get("folder") or "")
    dv.addWidget(f)
    g = QHBoxLayout()
    g.setSpacing(8)
    m = e.get("missing") or 0
    for lbl, val, tone in ((tr("采购"), f"€{e.get('final_cost') or 0:,.0f}", None),
                           (tr("没有报价"), str(m), "warn" if m else "ok"),
                           (tr("供应商"), str(e.get("suppliers") or len(e.get("split") or [])), None)):
        b = QFrame()
        b.setObjectName("Panel")
        bv = QVBoxLayout(b)
        bv.setContentsMargins(10, 8, 10, 8)
        bv.setSpacing(2)
        bv.addWidget(RP.muted(lbl, 11))
        bv.addWidget(RP.mono(val, 14, tone=tone))
        g.addWidget(b)
    dv.addLayout(g)
    split = e.get("split") or []
    if split:
        dv.addWidget(RP.muted(tr("分给哪几家"), 12))
        for i, (nm, v) in enumerate(split):
            h = QHBoxLayout()
            h.setSpacing(10)
            h.addWidget(RP.Dot(RP.PALETTE[i % len(RP.PALETTE)]))
            h.addWidget(QLabel(nm), 1)
            h.addWidget(RP.mono(f"€{v:,.0f}", 13))
            dv.addLayout(h)
    sc = [x for x in (e.get("scenarios") or [])]
    if e.get("scenario") or sc:
        box = QFrame()
        box.setObjectName("Panel")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(12, 10, 12, 10)
        bv.setSpacing(4)
        bv.addWidget(RP.muted(tr("方案"), 12))
        nm = QLabel(tr(e.get("scenario") or tr("全部供应商")))
        nm.setStyleSheet("font-weight:500;")
        bv.addWidget(nm)
        others = [x for x in sc if not x.get("chosen")]
        if others:
            ch = next((x for x in sc if x.get("chosen")), None) or {}
            bv.addWidget(RP.muted(" · ".join(tr("{0} {1:+,.2f}", tr(x.get("name") or ""), (x.get("total") or 0) - (ch.get("total") or 0))
                                             for x in others[:3]), 11))
        dv.addWidget(box)
    if e.get("edits"):
        dv.addWidget(RP.muted(tr("改过 {0} 次", e["edits"]) + (f" · {e['edited_at'][5:16]}" if e.get("edited_at") else ""), 12))
    dv.addSpacing(6)
    op = QPushButton(tr("打开这次结果"))
    op.setObjectName("Primary")
    op.setMinimumHeight(40)
    op.clicked.connect(win.view_history)
    dv.addWidget(op)
    h = QHBoxLayout()
    h.setSpacing(8)
    b1 = QPushButton(tr("打开文件夹"))
    b1.clicked.connect(win.open_history_folder)
    b2 = QPushButton(tr("删除这条记录"))
    b2.setObjectName("Danger")
    b2.clicked.connect(win.delete_history)
    h.addWidget(b1)
    h.addWidget(b2)
    dv.addLayout(h)
