"""
起订规则页（2026-11-16 照设计稿「⑪ 起订规则」重做）：
  上：大标题 + 一句话（几家 · 这周几家达标 / 靠凑单）+ 新增规则
      两张开关卡：起订量凑单 / 允许部分商品不采购
  中：每家批发商一张卡（启用 / 已停用、起订量大字、对上的报价文件、最近 6 次比价的小方块）+ 虚线「加规则」卡
  右：选中那家的编辑区（就是原来的规则窗口，嵌在页面里）+ 这次比价怎么样 + 停用 / 删除
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QPushButton, QWidget)

from theme import T
from i18n import tr
import result_page as RP

STATUS_TONE = {"已达标": "ok", "已凑单达标": "warn", "未达标": "err",
               "不在该供应商下单（更省钱）": "err", "未分到货，无需下单": "muted"}
STATUS_TEXT = {"已达标": "按最低价就够", "已凑单达标": "凑单后够了", "未达标": "没达标",
               "不在该供应商下单（更省钱）": "不够 · 这次排除", "未分到货，无需下单": "没分到货"}


class Dots(QWidget):
    """最近几次比价：一格一次（绿 = 够 / 黄 = 凑单后够 / 红 = 不够），没比过的灰框"""

    def __init__(self, sts):
        super().__init__()
        self.sts = (list(sts) + [None] * 6)[:6]
        self.setFixedHeight(8)
        self.setToolTip("\n".join(tr(STATUS_TEXT.get(s, s)) for s in sts if s))

    def paintEvent(self, _e):
        from PySide6.QtGui import QPainter, QColor
        from PySide6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        n, gap = 6, 4
        w = (self.width() - gap * (n - 1)) / n
        for i, s in enumerate(self.sts):
            p.setBrush(QColor(T(STATUS_TONE.get(s, "muted")) if s else T("track")))
            p.drawRoundedRect(QRectF(i * (w + gap), 0, w, 8), 3, 3)
        p.end()


class RuleCard(QPushButton):
    def __init__(self, r, file, sts):
        super().__init__()
        self.setCheckable(True)
        self.setObjectName("ResStore")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(196)
        on = r.get("enabled", True)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        h = QHBoxLayout()
        n = QLabel(r.get("name") or r.get("keyword") or "?")
        n.setStyleSheet("font-weight:600; font-size:14px;")
        h.addWidget(n, 1)
        h.addWidget(RP.chip(tr("启用") if on else tr("已停用"), "ok" if on else "muted"))
        v.addLayout(h)
        from min_order_optimizer import effective_limits
        c, a, mode = effective_limits(r.get("min_colli"), r.get("min_amount"), r.get("mode", "all"))
        big = f"€{a:,.0f}" if a else (f"{c:,.0f}" if c else "—")
        unit = (tr("起订") if a and not c else tr("箱起订") if c and not a else
                (tr("且 {0:,.0f} 箱", c) if mode == "all" else tr("或 {0:,.0f} 箱", c)) if a and c else tr("没有条件"))
        bh = QHBoxLayout()
        bh.setSpacing(8)
        bh.addWidget(RP.mono(big, 28))
        u = RP.muted(unit, 13)
        bh.addWidget(u, 0, Qt.AlignBottom)
        bh.addStretch()
        v.addLayout(bh)
        f = RP.muted("▤ " + (file or tr("这周没有对上的报价文件")), 12)
        f.setWordWrap(False)
        f.setToolTip(file or "")
        f.setStyleSheet(f"color:{T('head') if file else T('warn')}; font-size:12px;")
        v.addWidget(f)
        hh = QHBoxLayout()
        hh.addWidget(RP.muted(tr("最近 6 次比价"), 11))
        hh.addStretch()
        last = next((s for s in reversed(sts) if s), None)
        hh.addWidget(RP.muted(tr(STATUS_TEXT.get(last, last or "—")), 11))
        v.addLayout(hh)
        v.addWidget(Dots(sts))
        for w in self.findChildren(QWidget):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        if not on:
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            eff = QGraphicsOpacityEffect(self)
            eff.setOpacity(0.6)
            self.setGraphicsEffect(eff)


def _switch_card(toggle, sub):
    f = QFrame()
    f.setObjectName("Panel")
    h = QHBoxLayout(f)
    h.setContentsMargins(16, 12, 16, 12)
    h.setSpacing(14)
    v = QVBoxLayout()
    v.setSpacing(2)
    toggle.setParent(None)
    v.addWidget(toggle)
    v.addWidget(RP.muted(sub, 12))
    h.addLayout(v, 1)
    return f


def build(win, lay):
    page = lay.parentWidget()
    page.setStyleSheet(RP.qss())
    win._rules_page = page
    win._rule_sel = 0
    row = QHBoxLayout()
    row.setSpacing(16)
    lay.addLayout(row, 1)
    mid = QVBoxLayout()
    mid.setSpacing(16)
    row.addLayout(mid, 1)

    head = QHBoxLayout()
    hv = QVBoxLayout()
    hv.setSpacing(6)
    t = QLabel(tr("起订规则"))
    t.setObjectName("HeroTitle")
    hv.addWidget(t)
    win._rules_line = QLabel("")
    win._rules_line.setObjectName("HeroSub")
    win._rules_line.setTextFormat(Qt.RichText)
    hv.addWidget(win._rules_line)
    head.addLayout(hv, 1)
    add = QPushButton(tr("+ 新增规则"))
    add.setObjectName("Primary")
    add.setMinimumHeight(36)
    add.clicked.connect(win.add_rule)
    head.addWidget(add, 0, Qt.AlignBottom)
    mid.addLayout(head)
    win.banner_rules.setParent(None)
    mid.addWidget(win.banner_rules)

    sw = QHBoxLayout()
    sw.setSpacing(12)
    sw.addWidget(_switch_card(win.t_min2, tr("不够起订时，自动找最省钱的凑单办法")))
    sw.addWidget(_switch_card(win.t_partial, tr("放弃一家时，只有它有货的商品这次可以不买")))
    mid.addLayout(sw)

    win._rules_grid = QGridLayout()
    win._rules_grid.setSpacing(12)
    mid.addLayout(win._rules_grid)
    leg = QHBoxLayout()
    leg.setSpacing(18)
    for tone, text in (("ok", tr("按最低价就够")), ("warn", tr("凑单后够")), ("err", tr("不够 · 排除了这家"))):
        h = QHBoxLayout()
        h.setSpacing(6)
        h.addWidget(RP.Dot(tone, 10))
        h.addWidget(RP.muted(text, 12))
        leg.addLayout(h)
    leg.addStretch()
    mid.addLayout(leg)
    mid.addStretch()

    side = QFrame()
    side.setObjectName("Panel")
    side.setFixedWidth(380)
    win._rules_side = QVBoxLayout(side)
    win._rules_side.setContentsMargins(4, 4, 4, 16)
    win._rules_side.setSpacing(10)
    row.addWidget(side, 0, Qt.AlignTop)


def _history_status():
    """规则名 → 最近 6 次比价的状态（旧 → 新）"""
    import history
    out = {}
    try:
        runs = sorted(history.list_runs(), key=lambda e: e.get("time") or "")
    except Exception:
        return out
    for e in runs:
        for name, st in e.get("rules") or []:
            out.setdefault(name, []).append(st)
    return {k: v[-6:] for k, v in out.items()}


def render(win, rules, hit):
    """rules = settings['rules']；hit = {id(规则): 对上的报价文件}"""
    if not hasattr(win, "_rules_grid"):
        return
    win._rules_page.setStyleSheet(RP.qss())
    sts = _history_status()
    RP._clear(win._rules_grid)
    if win._rule_sel is not None and win._rule_sel >= len(rules):
        win._rule_sel = len(rules) - 1 if rules else None
    for i, r in enumerate(rules):
        c = RuleCard(r, hit.get(id(r)), sts.get(r.get("name"), []))
        c.setChecked(i == win._rule_sel)
        c.clicked.connect(lambda _=False, k=i: _select(win, k))
        c.mouseDoubleClickEvent = lambda _e, k=i: (_select(win, k))
        win._rules_grid.addWidget(c, i // 3, i % 3)
    add = QPushButton(tr("+  给别的批发商加起订规则"))
    add.setMinimumHeight(196)
    add.setCursor(Qt.PointingHandCursor)
    add.setStyleSheet(f"QPushButton {{ border:1.5px dashed {T('line')}; border-radius:14px; background:transparent; color:{T('muted')}; }}"
                      f"QPushButton:hover {{ border-color:{T('accent')}; color:{T('accent')}; }}")
    add.clicked.connect(win.add_rule)
    n = len(rules)
    win._rules_grid.addWidget(add, n // 3, n % 3)
    on = [r for r in rules if r.get("enabled", True)]
    last = {k: v[-1] for k, v in sts.items() if v}
    ok = sum(1 for r in on if last.get(r.get("name")) == "已达标")
    fill = sum(1 for r in on if last.get(r.get("name")) == "已凑单达标")
    line = tr("{0} 家批发商 · {1} 家启用", len(rules), len(on))
    if last:
        line += " · " + tr("上次比价 {0} 家达标、{1} 家靠凑单", ok, fill)
    if not win.t_min2.isChecked():
        line += f" · <span style='color:{T('warn')}'>" + tr("起订量凑单已关") + "</span>"
    win._rules_line.setText(line)
    _side(win, rules, hit, last)


def _select(win, k):
    win._rule_sel = k
    win.refresh_rules_table()


def _side(win, rules, hit, last):
    sv = win._rules_side
    RP._clear(sv)
    i = win._rule_sel
    if i is None or not rules:
        sv.addWidget(RP.muted(tr("还没有起订规则"), 13))
        return
    r = rules[i]
    d = win._rule_dialog(r)
    d.setWindowFlags(Qt.Widget)
    d.setStyleSheet("QDialog { background: transparent; }")

    def saved():
        if d.result_rule:
            win._set_rule(i, d.result_rule)
    d.accepted.connect(saved)
    d.rejected.connect(lambda: win.refresh_rules_table())
    sv.addWidget(d)
    st = last.get(r.get("name"))
    box = QFrame()
    box.setObjectName("Panel")
    bv = QVBoxLayout(box)
    bv.setContentsMargins(14, 12, 14, 12)
    bv.setSpacing(4)
    bv.addWidget(RP.muted(tr("上次比价"), 12))
    lb = QLabel(tr(STATUS_TEXT.get(st, st or "还没比过")))
    lb.setStyleSheet(f"font-weight:500; color:{T(STATUS_TONE.get(st, 'text'))};" if st else "font-weight:500;")
    bv.addWidget(lb)
    bv.addWidget(RP.muted(tr("这周对上的报价：{0}", hit.get(id(r)) or tr("没有")), 12))
    w = QWidget()
    wl = QVBoxLayout(w)
    wl.setContentsMargins(20, 0, 20, 0)
    wl.setSpacing(10)
    wl.addWidget(box)
    h = QHBoxLayout()
    b1 = QPushButton(tr("停用") if r.get("enabled", True) else tr("启用"))
    b1.clicked.connect(win.toggle_rule)
    b2 = QPushButton(tr("删除"))
    b2.setObjectName("Danger")
    b2.clicked.connect(win.delete_rule)
    h.addWidget(b1, 1)
    h.addWidget(b2)
    wl.addLayout(h)
    sv.addWidget(w)
