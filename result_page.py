"""
比价结果页（2026-11-16 照设计稿「⑩ 比价结果」重做）：
  上面一排：这一批比过的门店（点一家 = 看它的结果）
  左边：这次采购大字 + 三个小数 + 分给几家批发商的彩色条 → 每家批发商一张卡（金额 · 箱数 · 起订量进度 · 打开订单）
        → 「没用最低价的商品」表
  右边：「要你看的」（没有报价 / 调货 / 起订量没达标 / 门店报告）+「方案对比」（点一张 = 换成这个方案）
  底部：写进了几个订单 · 修改分配… · 去发送订单 →
数据都来自 MainWindow.last_summary（show_summary 最后调 fill）；老的表格 / 指标卡对象还在（藏起来），别的代码照用。
"""
import os
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QPushButton, QWidget,
                               QSizePolicy)

from theme import T, NUM_FONT
from page_kit import set_qss as _set_qss
from i18n import tr

PALETTE = ("accent", "cyan", "violet", "ok", "warn", "err")


def eur(v, dec=0):
    return f"€{v:,.{dec}f}"


def _clear(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            _clear(it.layout())


def chip_css(tone):
    c = T(tone) if tone not in ("muted",) else T("muted")
    soft = T("track") if tone == "muted" else T(tone + "_soft") if tone != "accent" else T("accent_soft")
    return f"QLabel {{ color:{c}; background:{soft}; border-radius:9px; padding:2px 9px; font-size:12px; font-weight:600; }}"


def chip(text, tone):
    lb = QLabel(text)
    lb.setStyleSheet(chip_css(tone))
    return lb


class SplitBar(QWidget):
    """一条按比例分段的彩色条（每家批发商一段，中间留 2 像素缝）"""

    def __init__(self, h=14):
        super().__init__()
        self.parts = []
        self.setFixedHeight(h)

    def set(self, parts):
        """parts = [(值, 颜色记号, 淡一点吗)]"""
        self.parts = [p for p in parts if p[0] > 0]
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tot = sum(x[0] for x in self.parts) or 1
        W, H = self.width(), self.height()
        gap = 2
        avail = W - gap * max(0, len(self.parts) - 1)
        x = 0.0
        p.setPen(Qt.NoPen)
        if not self.parts:
            p.setBrush(QColor(T("track")))
            p.drawRoundedRect(QRectF(0, 0, W, H), H / 2, H / 2)
        for i, (v, tone, faint) in enumerate(self.parts):
            w = avail * v / tot
            c = QColor(T(tone))
            if faint:
                c.setAlphaF(0.35)
            p.setBrush(c)
            r = QRectF(x, 0, max(2.0, w), H)
            p.drawRoundedRect(r, min(H / 2, w / 2), H / 2)
            x += w + gap
        p.end()


class Progress(QWidget):
    """细进度条（起订量进度）"""

    def __init__(self, h=6):
        super().__init__()
        self.v, self.tone = 0.0, "ok"
        self.setFixedHeight(h)

    def set(self, v, tone):
        self.v, self.tone = max(0.0, min(1.0, v)), tone
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        H = self.height()
        p.setBrush(QColor(T("track")))
        p.drawRoundedRect(QRectF(0, 0, self.width(), H), H / 2, H / 2)
        if self.v > 0:
            p.setBrush(QColor(T(self.tone)))
            p.drawRoundedRect(QRectF(0, 0, max(H, self.width() * self.v), H), H / 2, H / 2)
        p.end()


class Dot(QWidget):
    def __init__(self, tone, d=8):
        super().__init__()
        self.tone = tone
        self.setFixedSize(d, d)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T(self.tone)))
        p.drawEllipse(self.rect())
        p.end()


def mono(text, px=13, weight=600, tone=None):
    """等宽数字（全局样式表会盖掉 setFont，所以字号 / 字体写在样式里）"""
    lb = QLabel(text)
    lb.setStyleSheet(f"font-family:'{NUM_FONT}'; font-size:{px}px; font-weight:{weight};" +
                     (f" color:{T(tone)};" if tone else ""))
    return lb


def set_tone(lb, tone):
    css = lb.styleSheet().split(" color:")[0]
    lb.setStyleSheet(css + (f" color:{T(tone)};" if tone else ""))


def muted(text, px=12):
    lb = QLabel(text)
    lb.setStyleSheet(f"color:{T('muted')}; font-size:{px}px;")
    lb.setWordWrap(True)
    return lb


class StoreChip(QPushButton):
    """上面一排的门店：名字 · 金额 · 一行小字"""

    def __init__(self, store, amount, sub, tone, on):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(on)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("ResStore")
        self.setMinimumWidth(150)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(3)
        h = QHBoxLayout()
        h.setSpacing(8)
        h.addWidget(Dot(tone))
        n = QLabel(store)
        n.setStyleSheet("font-weight:600;")
        h.addWidget(n)
        h.addStretch()
        v.addLayout(h)
        v.addWidget(mono(amount, 15))
        v.addWidget(muted(sub, 11))
        for w in self.findChildren(QWidget):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(84)


class LinkRow(QPushButton):
    """「要你看的」一行：彩色数字方块 · 标题 · 一行小字 · ›"""

    def __init__(self, n, title, sub, tone):
        super().__init__()
        self.setObjectName("ResTodo")
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(12)
        b = mono(n, 13)
        b.setAlignment(Qt.AlignCenter)
        b.setFixedSize(40, 30)
        b.setStyleSheet(b.styleSheet() + chip_css(tone).split("{")[1].split("}")[0].replace("padding:2px 9px;", ""))
        h.addWidget(b)
        v = QVBoxLayout()
        v.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-weight:500;")
        v.addWidget(t)
        sl = muted(sub)
        sl.setWordWrap(False)
        sl.setToolTip(sub)
        v.addWidget(sl)
        h.addLayout(v, 1)
        a = QLabel("›")
        a.setStyleSheet(f"color:{T('accent')}; font-size:16px;")
        h.addWidget(a)
        for w in self.findChildren(QWidget):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setMinimumHeight(60)


class ScenCard(QPushButton):
    """方案对比：一张卡 = 一个方案（名字 + 选用标签 / 一行说明 + 比选用的多花多少）"""

    def __init__(self, name, tag, tag_tone, sub, delta, delta_tone, on):
        super().__init__()
        self.setObjectName("ResScen")
        self.setCheckable(True)
        self.setChecked(on)
        self.setCursor(Qt.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        h = QHBoxLayout()
        n = QLabel(name)
        n.setStyleSheet("font-weight:600;")
        h.addWidget(n, 1)
        if tag:
            h.addWidget(chip(tag, tag_tone))
        v.addLayout(h)
        h2 = QHBoxLayout()
        h2.addWidget(muted(sub), 1)
        h2.addWidget(mono(delta, 12, tone=delta_tone))
        v.addLayout(h2)
        for w in self.findChildren(QWidget):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setMinimumHeight(64)


def card(title=None, right=None, margins=(18, 16, 18, 16)):
    f = QFrame()
    f.setObjectName("Panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(*margins)
    v.setSpacing(12)
    if title:
        h = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("Section")
        h.addWidget(t)
        h.addStretch()
        if right is not None:
            h.addWidget(right)
        v.addLayout(h)
        f.title = t
    return f, v


QSS = """
QPushButton#ResStore {{ background:{panel}; border:1px solid {border}; border-radius:12px; text-align:left; }}
QPushButton#ResStore:hover {{ border-color:{ring}; }}
QPushButton#ResStore:checked {{ background:{sel}; border:1px solid {accent}; }}
QPushButton#ResTodo {{ background:{input}; border:1px solid {border}; border-radius:12px; text-align:left; }}
QPushButton#ResTodo:hover {{ border-color:{ring}; }}
QPushButton#ResScen {{ background:{input}; border:1px solid {border}; border-radius:12px; text-align:left; }}
QPushButton#ResScen:hover {{ border-color:{ring}; }}
QPushButton#ResScen:checked {{ background:{sel}; border:1px solid {accent}; }}
QFrame#ResFoot {{ background:{panel}; border:1px solid {border}; border-radius:14px; }}
"""


def qss():
    return QSS.format(panel=T("surface"), border=T("line"), ring=T("accent_ring"), sel=T("accent_soft"),
                      accent=T("accent"), input=T("canvas"))


def build(win, lay, hold):
    """在比价结果页里搭新版面。win = MainWindow；lay = 页面的竖排；hold = 藏起来放老控件的容器"""
    page = lay.parentWidget()
    _set_qss(page, qss())
    win._res_qss_page = page
    from erp_widgets import EmptyState
    win.res_empty = EmptyState("result", tr("还没有比价结果"), tr("比完一家门店，这里显示分配结果；以前的结果在「历史记录」里打开"))
    lay.addWidget(win.res_empty)
    cw = QWidget()
    lay.addWidget(cw)
    cw.hide()
    win.res_body = cw
    lay = QVBoxLayout(cw)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(16)

    # 一排门店
    win.res_stores = QHBoxLayout()
    win.res_stores.setSpacing(10)
    lay.addLayout(win.res_stores)

    body = QHBoxLayout()
    body.setSpacing(16)
    left = QVBoxLayout()
    left.setSpacing(16)
    right = QVBoxLayout()
    right.setSpacing(16)
    body.addLayout(left, 1)
    rw = QWidget()
    rw.setFixedWidth(340)
    rw.setLayout(right)
    right.setContentsMargins(0, 0, 0, 0)
    body.addWidget(rw, 0, Qt.AlignTop)
    lay.addLayout(body)

    # 这次采购
    hero, hv = card()
    hv.setContentsMargins(24, 22, 24, 22)
    hv.setSpacing(16)
    top = QHBoxLayout()
    top.setSpacing(24)
    hl = QVBoxLayout()
    hl.setSpacing(6)
    win.res_cap = muted("")
    hl.addWidget(win.res_cap)
    win.res_amt = mono("—", 38)
    hl.addWidget(win.res_amt)
    win.res_line = QLabel("")
    win.res_line.setStyleSheet(f"color:{T('head')};")
    win.res_line.setWordWrap(True)
    hl.addWidget(win.res_line)
    top.addLayout(hl, 1)
    grid = QGridLayout()
    grid.setSpacing(10)
    win.res_mini = []
    for i, (lbl, tip) in enumerate(((tr("分到供应商"), tr("至少一家批发商有报价、分到了供应商的商品")),
                                    (tr("没有报价"), tr("哪家批发商都没有报价的商品，点开看是哪些")),
                                    (tr("比全部最低价多"), tr("为了起订量 / 你的决定，没用最低价多花的钱")))):
        b = QPushButton()
        b.setObjectName("ResTodo")
        b.setToolTip(tip)
        b.setFixedSize(132, 64)
        v = QVBoxLayout(b)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        v.addWidget(muted(lbl, 11))
        val = mono("—", 17)
        v.addWidget(val)
        for w in b.findChildren(QWidget):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        grid.addWidget(b, 0, i)
        win.res_mini.append((b, val))
    win.res_mini[1][0].setCursor(Qt.PointingHandCursor)
    win.res_mini[1][0].clicked.connect(win.show_missing)
    top.addLayout(grid)
    hv.addLayout(top)
    win.res_split = SplitBar()
    hv.addWidget(win.res_split)
    win.res_legend = QHBoxLayout()
    win.res_legend.setSpacing(16)
    hv.addLayout(win.res_legend)
    left.addWidget(hero)

    # 每家批发商一张卡
    win.res_cards = QGridLayout()
    win.res_cards.setSpacing(12)
    left.addLayout(win.res_cards)

    # 没用最低价的商品（老表格挪进来）
    chg, cv = card(tr("没用最低价的商品"))
    win.res_chg_card = chg
    cv.setContentsMargins(0, 14, 0, 0)
    head = cv.itemAt(0).layout()
    head.setContentsMargins(18, 0, 18, 0)
    win.chg_filter.setParent(None)
    head.addWidget(win.chg_filter)
    win.chg_title = chg.title
    cv.addWidget(win.chg_table)
    win.chg_empty.setContentsMargins(18, 0, 18, 16)
    cv.addWidget(win.chg_empty)
    left.addWidget(chg)

    # 右边：要你看的 / 方案对比
    todo, tv = card(tr("要你看的"))
    win.res_todo = tv
    right.addWidget(todo)
    sc, sv = card(tr("方案对比"), muted(tr("不用重算，点一下就换"), 12))
    win.res_scen_card = sc
    win.res_scen = sv
    right.addWidget(sc)
    right.addStretch()

    # 底部
    foot = QFrame()
    foot.setObjectName("ResFoot")
    fh = QHBoxLayout(foot)
    fh.setContentsMargins(20, 12, 20, 12)
    fh.setSpacing(12)
    win.res_foot = QLabel("")
    win.res_foot.setStyleSheet(f"color:{T('head')};")
    fh.addWidget(win.res_foot, 1)
    from page_kit import more_menu
    more, _a = more_menu([
        (tr("打开供应商文件夹"), win.open_supplier_folder, tr("这次采购了的供应商订单（Ordini 文件夹）")),
        (tr("门店报告"), win.open_store_report, tr("发给门店的意大利文报告")),
        (tr("打开主订单"), win.open_main_order, None),
        (tr("打开备份"), lambda: win.last_summary and __import__("main").open_path(win.last_summary.get("backup_dir")),
         tr("比价前所有文件的备份")),
        None,
        (tr("导出改动明细"), win.export_changes, None),
        (tr("缺失商品…"), win.show_missing, None),
    ])
    fh.addWidget(more)
    win.btn_edit.setParent(None)
    win.btn_edit.setMinimumHeight(40)
    fh.addWidget(win.btn_edit)
    go = QPushButton(tr("去发送订单 →"))
    go.setObjectName("Primary")
    go.setMinimumHeight(40)
    go.clicked.connect(lambda: win._to_send_step())
    fh.addWidget(go)
    lay.addWidget(foot)


def _batch_key(e):
    """
    同一批比价的认法：工作文件夹「4 比价结果\日期\门店」→ 同一个日期文件夹的几家门店算一批
    （11-18 修：以前按门店文件夹比，每家店的文件夹都不一样 → 顶上只显示一家）；
    别的文件夹照旧按 文件夹 + 同一天
    """
    import reorder_service as RS
    folder = e.get("folder") or ""
    if folder and RS.batch_root_of(folder):
        return ("batch", os.path.normcase(os.path.dirname(os.path.normpath(folder))))
    return ("day", os.path.normcase(folder), (e.get("time") or "")[:10])


def _batch_runs(s):
    """和这条结果同一批的几家门店（每家取最近一次）"""
    import history
    try:
        runs = history.list_runs()
    except Exception:
        return []
    hid = s.get("history_id")
    me = next((e for e in runs if e.get("id") == hid), None)
    if not me:
        return []
    key = _batch_key(me)
    seen, out = set(), []
    for e in sorted(runs, key=lambda e: e.get("time") or "", reverse=True):
        if _batch_key(e) != key:
            continue
        st = e.get("store") or e.get("main_file")
        if st in seen:
            continue
        seen.add(st)
        out.append(e)
    return sorted(out, key=lambda e: e.get("time") or "")


def _open_run(win, hid):
    """点顶上一家门店：是那家店最新的一次比价 → 切换到那家店（修改分配、缺失商品、门店报告都跟着这家）；
    不是最新的 → 只看历史记录"""
    import history
    import reorder_service as RS
    e = next((x for x in history.list_runs() if x.get("id") == hid), None)
    folder = (e or {}).get("folder") or ""
    if folder and RS.batch_root_of(folder) and os.path.isdir(folder) and not getattr(win, "running", False):
        try:
            from service import load_state
            st = load_state(folder)
        except Exception:
            st = None
        if st and st.get("history_id") == hid:
            win._show_store_result(folder)
            return
    data = history.load_run(hid)
    if data:
        win.last_summary = data
        win.show_summary(data)


def _moq_text(r):
    from min_order_optimizer import effective_limits
    c, a, mode = effective_limits(r.get("min_colli"), r.get("min_amount"), r.get("mode", "all"))
    fc, fa = r.get("after_colli") or 0, r.get("after_amount") or 0
    parts = []
    if a:
        parts.append(fa / a)
    if c:
        parts.append(fc / c)
    pct = (min(parts) if mode == "all" else max(parts)) if parts else 1.0
    need = (tr("起订 €{0:g}", a) if a else "") + (" · " if a and c else "") + (tr("{0:g} 箱", c) if c else "")
    st = r.get("status") or ""
    if st == "已达标":
        return pct, "ok", tr("{0} · 已达标", need)
    if st == "已凑单达标":
        gap = max(0.0, (a or 0) - (r.get("before_amount") or 0))
        return pct, "warn", (tr("{0} · 差 €{1:,.0f}，已凑单", need, gap) if gap else tr("{0} · 已凑单达标", need))
    if st == "未达标":
        return pct, "err", tr("{0} · 没达标", need)
    return pct, "muted", tr("{0} · 这次不订", need)


def fill(win, s):
    page = getattr(win, "_res_qss_page", None)
    if page is not None:
        _set_qss(page, qss())
    win.res_empty.hide()
    win.res_body.show()
    st = s.get("state") or {}
    labels = st.get("labels") or {}
    current = win._is_current(s)
    mo = s.get("min_order") or {}

    # 一排门店
    _clear(win.res_stores)
    runs = _batch_runs(s)
    for e in runs:
        miss = e.get("missing") or 0
        b = StoreChip(e.get("store") or e.get("main_file") or "?", eur(e.get("final_cost") or 0),
                      tr("缺 {0}", miss) if miss else tr("全部有报价"), "warn" if miss else "ok",
                      e.get("id") == s.get("history_id"))
        b.clicked.connect(lambda _=False, h=e["id"]: _open_run(win, h))
        win.res_stores.addWidget(b)
    win.res_stores.addStretch()

    # 这次采购
    import history
    store = history._store_of(s) or tr("这家店")
    sups = s.get("suppliers") or []
    amt = sum(sp.get("amount") or 0 for sp in sups)
    used = [sp for sp in sups if (sp.get("colli") or 0) > 0]
    hm = (s.get("_history") or {}).get("time") or ""
    win.res_cap.setText(tr("{0} · 这次采购", store) + (f" · {hm[8:10]}/{hm[5:7]} {hm[11:16]}" if hm else ""))
    win.res_amt.setText(eur(amt, 2))
    line = [tr("分给 {0} 家批发商", len(used))]
    if s.get("scenario"):
        line.append(tr("方案「{0}」", tr(s["scenario"])))
    line.append(tr("用时 {0:.0f} 秒", s.get("elapsed", 0)))
    if s.get("history_id") and not current:
        line.append(f"<span style='color:{T('warn')}'>" + tr("历史记录 · 只能查看") + "</span>")
    win.res_line.setText(" · ".join(line))
    tot = s.get("total_rows") or 0
    miss = s.get("missing") or 0
    extra = mo.get("extra_cost") or 0
    vals = ((f"{s.get('priced_rows', 0):,} / {tot:,}", None), (f"{miss:,}", "warn" if miss else "ok"),
            (eur(extra, 2), "warn" if extra > 0.005 else None))
    for (b, lb), (v, tone) in zip(win.res_mini, vals):
        lb.setText(v)
        set_tone(lb, tone)

    tone_of = {}
    for i, sp in enumerate(sorted(sups, key=lambda x: -(x.get("amount") or 0))):
        tone_of[sp["name"]] = PALETTE[i % len(PALETTE)]
    order = sorted(sups, key=lambda x: -(x.get("amount") or 0))
    win.res_split.set([(sp.get("amount") or 0, tone_of[sp["name"]], (sp.get("colli") or 0) == 0) for sp in order])
    _clear(win.res_legend)
    for sp in order:
        if not sp.get("amount"):
            continue
        h = QHBoxLayout()
        h.setSpacing(6)
        h.addWidget(Dot(tone_of[sp["name"]]))
        n = QLabel(labels.get(sp["name"]) or sp["name"])
        n.setStyleSheet(f"color:{T('head')}; font-size:12px;")
        h.addWidget(n)
        h.addWidget(mono(f"{100 * sp['amount'] / (amt or 1):.0f}%", 12))
        win.res_legend.addLayout(h)
    win.res_legend.addStretch()

    # 每家批发商一张卡
    _clear(win.res_cards)
    rule_by = {r["target"]: r for r in mo.get("rules", [])} if mo.get("enabled") else {}
    for i, sp in enumerate(order):
        f, v = card(margins=(16, 14, 16, 14))
        h = QHBoxLayout()
        h.setSpacing(8)
        h.addWidget(Dot(tone_of[sp["name"]]))
        n = QLabel(labels.get(sp["name"]) or sp["name"])
        n.setStyleSheet("font-weight:600;")
        n.setToolTip(sp["name"])
        h.addWidget(n, 1)
        ok = (sp.get("colli") or 0) > 0
        h.addWidget(chip(tr("已写入") if ok else tr("这次不订"), "ok" if ok else "muted"))
        v.addLayout(h)
        v.addWidget(mono(eur(sp.get("amount") or 0), 22))
        v.addWidget(muted(tr("{0:,.0f} 箱 · {1:,} 个商品", sp.get("colli") or 0, sp.get("rows") or 0)))
        r = rule_by.get(sp["name"])
        if r:
            pct, tone, text = _moq_text(r)
            bar = Progress()
            bar.set(pct, tone)
            v.addWidget(bar)
            t = muted(text, 11)
            t.setStyleSheet(f"color:{T(tone if tone != 'muted' else 'muted')}; font-size:11px;")
            v.addWidget(t)
        else:
            v.addWidget(muted(tr("没有起订规则"), 11))
        bh = QHBoxLayout()
        bh.setSpacing(8)
        b1 = QPushButton(tr("打开订单"))
        b1.clicked.connect(lambda _=False, name=sp["name"]: _open_order(win, name, labels))
        b1.setEnabled(ok)
        b2 = QPushButton(tr("换供应商的商品"))
        b2.setToolTip(tr("打开「修改分配」"))
        b2.clicked.connect(win.edit_allocation)
        b2.setEnabled(win.btn_edit.isEnabled())
        bh.addWidget(b1)
        bh.addWidget(b2)
        v.addLayout(bh)
        win.res_cards.addWidget(f, i // 3, i % 3)

    # 没用最低价的商品
    n_ch = len(s.get("changes") or [])
    win.chg_title.setText(tr("没用最低价的商品（{0}）", n_ch))
    win.chg_table.setMinimumHeight(0)
    win.chg_table.setFixedHeight(min(440, 46 + 40 * max(1, n_ch)))
    win.chg_filter.setVisible(n_ch > 8)

    # 要你看的
    _clear(win.res_todo)
    head = QHBoxLayout()
    tt = QLabel(tr("要你看的"))
    tt.setObjectName("Section")
    head.addWidget(tt)
    head.addStretch()
    win.res_todo.addLayout(head)
    rows = []
    if miss:
        rows.append((str(miss), tr("{0} 个商品没有报价", miss), tr("哪家批发商都没有 → 找替代商品"), "warn",
                     lambda: (win.go_page("search"))))
    if mo.get("warning"):
        rows.append(("!", tr("起订量没达标"), mo["warning"], "err", win.edit_allocation))
    ts = list(s.get("transfers") or []) + list(s.get("transfers_out") or [])
    if ts:
        rows.append((str(len(ts)), tr("{0} 个商品门店间调货", len(ts)), tr("少订的箱数 · 调货单已生成"), "violet",
                     win.show_transfers))
    try:
        from service import pack_changes
        pch = pack_changes(st.get("rows") or [], st.get("assignment") or {}) if st.get("rows") \
            else list(s.get("pack_changes") or [])
    except Exception:
        pch = list(s.get("pack_changes") or [])
    if pch:
        rows.append((str(len(pch)), tr("{0} 个商品按批发商箱规换算", len(pch)),
                     tr("订到的件数和门店要的不一样（取最接近的整箱）"), "accent",
                     lambda p=pch: _pack_dialog(win, p, (st.get("labels") or s.get("supplier_labels") or {}))))
    lost = [c for c in (s.get("changes") or []) if c.get("new") is None]
    if lost:
        rows.append((str(len(lost)), tr("{0} 个商品这次不采购", len(lost)), tr("放弃了供应商，只有它有货"), "err",
                     lambda: win.chg_filter.setFocus()))
    if mo.get("unmatched"):
        rows.append((str(len(mo["unmatched"])), tr("起订规则没对上报价"), "、".join(mo["unmatched"]), "muted",
                     lambda: win.go_page("rules")))
    rows.append(("✓" if not rows else "↗", tr("门店报告"), tr("发给门店的意大利文报告（非 Griglia 商品等）"),
                 "ok" if not rows else "accent", win.open_store_report))
    for n, title, sub, tone, fn in rows:
        b = LinkRow(n, title, sub, tone)
        b.clicked.connect(lambda _=False, f=fn: f())
        win.res_todo.addWidget(b)

    # 方案对比
    _clear(win.res_scen)
    head = QHBoxLayout()
    tt = QLabel(tr("方案对比"))
    tt.setObjectName("Section")
    head.addWidget(tt)
    head.addStretch()
    head.addWidget(muted(tr("不用重算，点一下就换"), 12))
    win.res_scen.addLayout(head)
    res = s.get("scenarios") or []
    from service import can_switch_scenario
    can = can_switch_scenario(s) and current
    chosen = next((r for r in res if r.get("chosen")), None)
    for i, r in enumerate(res):
        on = bool(r.get("chosen"))
        d = (r.get("total") or 0) - ((chosen or {}).get("total") or 0)
        m = len(r.get("missing") or [])
        sub = tr("缺 {0} 个", m) if m else tr("全部能订到")
        b = ScenCard(tr(r.get("name") or ""), tr("✓ 选用") if on else "", "accent", sub,
                     "" if on else (("+" if d >= 0 else "−") + eur(abs(d), 2)),
                     "warn" if d > 0.005 else "ok" if d < -0.005 else "muted", on)
        if on or not can:
            b.setToolTip(tr("现在用的方案") if on else tr("只能换每个文件夹最近一次的结果"))
            b.clicked.connect(lambda _=False, w=b, o=on: w.setChecked(o))
        else:
            b.setToolTip(tr("点一下：改用这个方案（会先问你）"))
            b.clicked.connect(lambda _=False, k=i, w=b: (w.setChecked(False), win.switch_scenario(k)))
        win.res_scen.addWidget(b)
    win.res_scen_card.setVisible(bool(res))
    if res:
        # 11-18（用户）：以前是两行蓝字「详细对比… / 差异商品（n）」，不正式 → 两个正式的小按钮，数字放徽章
        from erp_widgets import CountButton
        lk = QHBoxLayout()
        lk.setSpacing(8)
        b = QPushButton(tr("方案明细"))
        b.setObjectName("Link")
        b.setToolTip(tr("几个方案的金额、缺几个、各供应商箱数逐项对比"))
        b.clicked.connect(lambda: _scen_dialog(win))
        lk.addWidget(b)
        if can_switch_scenario(s):
            import scenarios as SC
            diff = SC.diff_items(st.get("rows") or [], st["scenario_assigns"], st.get("labels") or {})
            b2 = CountButton(tr("差异商品"), len(diff))
            b2.setObjectName("Link")
            b2.setToolTip(tr("在不同方案里分给了不同供应商、或者买不到的商品，逐个对比"))
            b2.setEnabled(bool(diff))
            from main import ScenarioDiffDialog
            b2.clicked.connect(lambda: ScenarioDiffDialog(win, res, diff).exec())
            lk.addWidget(b2)
        lk.addStretch()
        win.res_scen.addLayout(lk)

    # 底部
    n_files = len(used)
    win.res_foot.setText(tr("已写进 <b>{0}</b> 个供应商订单 · Ordini 文件夹", n_files) +
                         (" · " + tr("调货 {0} 个", len(ts)) if ts else ""))


def _open_order(win, name, labels):
    """打开这家批发商这次的订单文件（Ordini 里文件名带它的名字的那份）；找不到就打开文件夹"""
    import service as SV
    d = win._shown_state().get("distribuire_path")
    if not d:
        return win.open_supplier_folder()
    o = os.path.join(os.path.dirname(os.path.abspath(d)), SV.ORDERS_DIR)
    key = (labels.get(name) or name).replace(" ", "_").lower()
    try:
        hit = [f for f in os.listdir(o) if f.lower().endswith(".xlsx") and key in f.lower()]
    except OSError:
        hit = []
    if hit:
        from erp_widgets import open_file
        open_file(win, os.path.join(o, hit[0]))
    else:
        win.open_supplier_folder()


def _scen_dialog(win):
    """方案对比的完整表格（以前直接摆在页面上的那张）"""
    from PySide6.QtWidgets import QDialog
    from erp_widgets import fit_to_screen
    d = QDialog(win)
    d.setWindowTitle(tr("方案对比"))
    v = QVBoxLayout(d)
    v.setContentsMargins(16, 16, 16, 16)
    win.scen_panel.setParent(d)
    win.scen_panel.show()
    v.addWidget(win.scen_panel)
    d.resize(900, 520)
    fit_to_screen(d)
    d.exec()
    win.scen_panel.setParent(win._res_hold)
    win.scen_panel.hide()


def _pack_dialog(win, items, labels):
    """按批发商箱规换算的商品：门店要几件 → 分到的那家订几箱、几件（2026-11-19）"""
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView, QDialogButtonBox
    from erp_widgets import TableSortFilter, fit_to_screen
    d = QDialog(win)
    d.setWindowTitle(tr("按批发商箱规换算"))
    v = QVBoxLayout(d)
    heads = [tr("条码"), tr("商品"), tr("门店要（件）"), tr("供应商"), tr("每箱"), tr("订几箱"), tr("订到（件）"), tr("差")]
    t = QTableWidget(0, len(heads))
    t.setHorizontalHeaderLabels(heads)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    rows = [[x["ean"], x["articolo"], x["need"], labels.get(x["supplier"]) or x["supplier"], x["pack"],
             float(x["colli"]), x["got"], x["got"] - x["need"]] for x in items]

    def text(i, c):
        val = rows[i][c]
        return f"{val:+g}" if c == 7 else f"{val:g}" if isinstance(val, float) else str(val)

    def fill(i, k):
        if t.rowCount() <= k:
            t.setRowCount(k + 1)
        for c in range(len(heads)):
            it = QTableWidgetItem(text(i, c))
            if c >= 2 and c != 3:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if c == 7:
                it.setForeground(QColor(T("warn") if rows[i][7] > 0 else T("err")))
            t.setItem(k, c, it)

    ctl = TableSortFilter(t, lambda: len(rows), text, fill, key=lambda i, c: rows[i][c], numeric=(2, 4, 5, 6, 7))
    ctl.render()
    d._ctl = ctl
    t.setColumnWidth(1, 320)
    v.addWidget(t)
    bb = QDialogButtonBox(QDialogButtonBox.Close)
    bb.button(QDialogButtonBox.Close).setText(tr("关闭"))
    bb.rejected.connect(d.reject)
    v.addWidget(bb)
    d.resize(900, 520)
    fit_to_screen(d)
    d.exec()
