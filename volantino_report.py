# -*- coding: utf-8 -*-
"""
促销分析的报告版面（2026-11-15 用户：「做成世界级企业的销售数据分析报告」）

  KPI 一排五张卡：销售额 · 毛利 · 毛利率 · 销量 · 让利
                  （大数字 + 比活动前同样天数多 / 少几成 + 每天的小走势线）
  第二排：每天销售额与毛利率（柱 = 销售额，里面深色 = 毛利，橙线 = 毛利率，虚线 = 活动前日均销售额）
          对当月毛利率的影响（桥形图：没有让利 → 让利吃掉 → 实际）
  第三排：门店排名（横条，点一家 = 整页看这家）· 销售额前 8 名商品（+ 卖出 / 没卖出 / 毛利为负，点了跳到商品明细）

全部自己画（QPainter），颜色都用 theme.T，深浅色都能看。数据来自 volantino.analyze 的结果。
"""
from datetime import date

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QFontMetrics, QPainterPath
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy

from i18n import tr


def T(k):
    import theme
    return theme.T(k)


def _num_font(px, bold=False):
    from theme import NUM_FONT
    f = QFont(NUM_FONT)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def _ui_font(px, bold=False):
    f = QFont()
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def eur(v, dec=0):
    return f"€ {v:,.{dec}f}" if v is not None else "—"


def eur_short(v):
    """坐标轴用：€ 1,2k / € 850"""
    a = abs(v)
    if a >= 1_000_000:
        return f"€ {v / 1_000_000:.1f}M"
    if a >= 10_000:
        return f"€ {v / 1000:.0f}k"
    if a >= 1000:
        return f"€ {v / 1000:.1f}k"
    return f"€ {v:.0f}"


def pct(v, dec=1):
    return f"{v * 100:.{dec}f}%" if v is not None else "—"


def _alpha(c, a):
    q = QColor(T(c)) if isinstance(c, str) else QColor(c)
    q.setAlpha(a)
    return q


def _nice_top(v):
    """坐标轴最大值取个整数：1 / 2 / 2.5 / 5 × 10^n"""
    import math
    if v <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * e:
            return m * e
    return 10 * e


def _delta_tone(d, good_up=True):
    if d is None or abs(d) < 0.005:
        return "muted"
    return "ok" if (d > 0) == good_up else "err"


# =====================================================================
# 卡片外框（白底圆角 + 标题行）
# =====================================================================
class Card(QFrame):
    def __init__(self, title="", tip=""):
        super().__init__()
        self.setObjectName("RpCard")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(18, 14, 18, 14)
        self.v.setSpacing(10)
        self.head = QHBoxLayout()
        self.head.setSpacing(8)
        self.title = QLabel(title + ("  ⓘ" if tip else ""))
        self.title.setObjectName("RpTitle")
        if tip:
            self.title.setToolTip(tip)
        self.head.addWidget(self.title)
        self.head.addStretch()
        self.v.addLayout(self.head)

    def set_title(self, text, tip=""):
        self.title.setText(text + ("  ⓘ" if tip else ""))
        self.title.setToolTip(tip)


# =====================================================================
# KPI 卡：标题 · 大数字 · 比活动前 · 小走势线
# =====================================================================
class _Spark(QWidget):
    def __init__(self):
        super().__init__()
        self.vals, self.tone = [], "accent"
        self.setFixedHeight(30)

    def set(self, vals, tone="accent"):
        self.vals, self.tone = list(vals), tone
        self.update()

    def paintEvent(self, _e):
        n = len(self.vals)
        if n < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height() - 3
        lo, hi = min(self.vals), max(self.vals)
        rng = (hi - lo) or 1
        pts = [QPointF(i * (W - 2) / (n - 1) + 1, 2 + (H - 2) * (1 - (v - lo) / rng)) for i, v in enumerate(self.vals)]
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        fill = QPainterPath(path)
        fill.lineTo(pts[-1].x(), H + 2)
        fill.lineTo(pts[0].x(), H + 2)
        fill.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(_alpha(self.tone, 34))
        p.drawPath(fill)
        pen = QPen(QColor(T(self.tone)), 1.6)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setBrush(QColor(T(self.tone)))
        p.drawEllipse(pts[-1], 2.4, 2.4)
        p.end()


class Kpi(QFrame):
    def __init__(self, title, tip=""):
        super().__init__()
        self.setObjectName("RpCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 13, 16, 10)
        v.setSpacing(3)
        self.cap = QLabel(title + ("  ⓘ" if tip else ""))
        self.cap.setObjectName("RpKpiCap")
        self.cap.setToolTip(tip)
        v.addWidget(self.cap)
        self.val = QLabel("—")
        self.val.setObjectName("RpKpiVal")
        v.addWidget(self.val)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.delta = QLabel("")
        self.delta.setObjectName("RpDelta")
        self.sub = QLabel("")
        self.sub.setObjectName("RpFaint")
        row.addWidget(self.delta)
        row.addWidget(self.sub)
        row.addStretch()
        v.addLayout(row)
        v.addSpacing(4)
        self.spark = _Spark()
        v.addWidget(self.spark)

    def set(self, value, delta_text="", tone="muted", sub="", spark=(), spark_tone="accent", delta_tip=""):
        self.val.setText(value)
        self.delta.setText(delta_text)
        self.delta.setVisible(bool(delta_text))
        self.delta.setToolTip(delta_tip)
        fg = T(tone) if tone != "muted" else T("muted")
        bg = T(tone + "_soft") if tone in ("ok", "err", "warn") else T("track")
        self.delta.setStyleSheet(f"color:{fg}; background:{bg}; border-radius:9px; padding:1px 7px; "
                                 f"font-size:11px; font-weight:600;")
        self.sub.setText(sub)
        self.spark.set(spark, spark_tone)


# =====================================================================
# 每天销售额与毛利率
# =====================================================================
class TrendChart(QWidget):
    def __init__(self):
        super().__init__()
        self.days, self.ref, self.hover = [], None, -1
        self.setMouseTracking(True)
        self.setMinimumHeight(230)

    def set(self, days, ref=None):
        """days = [(date, rev, profit)]；ref = 活动前日均销售额（画一条虚线）"""
        self.days, self.ref = days, ref
        self.update()

    LEFT, RIGHT, TOP, BOTTOM = 52, 40, 10, 24

    def _plot(self):
        return QRectF(self.LEFT, self.TOP, self.width() - self.LEFT - self.RIGHT, self.height() - self.TOP - self.BOTTOM)

    def _idx(self, x):
        r = self._plot()
        n = len(self.days)
        if not n or x < r.left() or x > r.right():
            return -1
        i = int((x - r.left()) * n / r.width())
        return i if 0 <= i < n else -1

    def mouseMoveEvent(self, e):
        i = self._idx(e.position().x())
        if i != self.hover:
            self.hover = i
            self.update()
        if i >= 0:
            d, rev, pr = self.days[i]
            self.setToolTip(tr("{0:%a %d/%m}：销售额 € {1:,.2f} · 毛利 € {2:,.2f}（{3}）", d, rev, pr,
                               pct(pr / rev if rev else None)))

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def paintEvent(self, _e):
        n = len(self.days)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._plot()
        top = _nice_top(max([rv for _, rv, _ in self.days] + [self.ref or 0, 1]) * 1.08)
        mtop = 0.6
        margins = [(pr / rv if rv > 0 else None) for _, rv, pr in self.days]
        mvals = [m for m in margins if m is not None]
        if mvals:
            mtop = max(0.1, min(1.0, _nice_top(max(mvals) * 1.15)))
        # 横格线 + 左边金额 / 右边毛利率
        p.setFont(_num_font(10))
        for k in range(5):
            y = r.bottom() - r.height() * k / 4
            p.setPen(QPen(QColor(T("line")), 1, Qt.SolidLine if k == 0 else Qt.DotLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(T("faint")))
            p.drawText(QRectF(0, y - 8, self.LEFT - 8, 16), Qt.AlignRight | Qt.AlignVCenter, eur_short(top * k / 4))
            p.setPen(_alpha("near", 230))
            p.drawText(QRectF(r.right() + 6, y - 8, self.RIGHT - 6, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{mtop * k / 4 * 100:.0f}%")
        if not n:
            p.end()
            return
        cw = r.width() / n
        bw = max(3.0, min(28.0, cw * 0.64))
        today = date.today()
        step = max(1, int(30 // max(cw, 1)) + 1)
        pts = []
        for i, (d, rv, pr) in enumerate(self.days):
            cx = r.left() + i * cw + cw / 2
            if i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha("accent", 18))
                p.drawRoundedRect(QRectF(r.left() + i * cw, r.top(), cw, r.height()), 4, 4)
            if d > today:
                p.setPen(QPen(QColor(T("line")), 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(cx - bw / 2, r.bottom() - 16, bw, 16), 3, 3)
            elif rv > 0:
                h = r.height() * rv / top
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha("accent", 70 if i != self.hover else 110))
                p.drawRoundedRect(QRectF(cx - bw / 2, r.bottom() - h, bw, h), 3, 3)
                hp = r.height() * max(pr, 0) / top
                p.setBrush(QColor(T("accent")))
                p.drawRoundedRect(QRectF(cx - bw / 2, r.bottom() - hp, bw, hp), 3, 3)
            m = margins[i]
            if m is not None and d <= today:
                pts.append(QPointF(cx, r.bottom() - r.height() * max(0.0, min(m, mtop)) / mtop))
            if i % step == 0 or i == n - 1:
                p.setPen(QColor(T("accent") if d == today else T("faint")))
                p.setFont(_num_font(10, d == today))
                p.drawText(QRectF(cx - 20, r.bottom() + 4, 40, 16), Qt.AlignCenter, f"{d:%d/%m}")
        if self.ref:
            y = r.bottom() - r.height() * self.ref / top
            p.setPen(QPen(QColor(T("muted")), 1.2, Qt.DashLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        if len(pts) > 1:
            path = QPainterPath(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            pen = QPen(QColor(T("near")), 2.2)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setPen(QPen(QColor(T("surface")), 1.5))
        p.setBrush(QColor(T("near")))
        for q in pts:
            p.drawEllipse(q, 3, 3)
        p.end()


class Legend(QWidget):
    """一排图例：[(颜色, 样式 bar / line / dash, 文字)]"""

    def __init__(self, items):
        super().__init__()
        self.items = items
        fm = QFontMetrics(_ui_font(11))
        self.setFixedHeight(18)
        self.setMinimumWidth(sum(fm.horizontalAdvance(t) + 32 for _, _, t in items))

    def set_items(self, items):
        self.items = items
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(_ui_font(11))
        fm = QFontMetrics(p.font())
        x = self.width() - sum(fm.horizontalAdvance(t) + 32 for _, _, t in self.items)
        cy = self.height() / 2
        for color, kind, text in self.items:
            if kind == "bar":
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha(color, 255) if not isinstance(color, tuple) else _alpha(*color))
                p.drawRoundedRect(QRectF(x, cy - 5, 10, 10), 2, 2)
            else:
                p.setPen(QPen(QColor(T(color)), 2, Qt.DashLine if kind == "dash" else Qt.SolidLine))
                p.drawLine(QPointF(x - 1, cy), QPointF(x + 12, cy))
            p.setPen(QColor(T("muted")))
            w = fm.horizontalAdvance(text)
            p.drawText(QRectF(x + 16, 0, w + 4, self.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += w + 32
        p.end()


# =====================================================================
# 对当月毛利率的影响：桥形图
# =====================================================================
class Bridge(QWidget):
    def __init__(self):
        super().__init__()
        self.wo = self.act = None
        self.labels = ("", "", "")
        self.setMinimumHeight(150)

    def set(self, wo, act, labels):
        self.wo, self.act, self.labels = wo, act, labels
        self.update()

    def paintEvent(self, _e):
        if self.wo is None or self.act is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        lab_h, val_h = 18, 18
        base = H - lab_h - 4
        lo = max(0.0, min(self.wo, self.act) - max(0.02, abs(self.wo - self.act) * 3))   # 纵轴不从 0 开始，差距才看得见
        hi = max(self.wo, self.act) + (max(self.wo, self.act) - lo) * 0.25
        span = (hi - lo) or 1
        y_of = lambda v: base - (base - val_h - 4) * (v - lo) / span          # noqa: E731
        cw = W / 3
        bw = min(64, cw * 0.56)
        bars = [(self.wo, lo, _alpha("faint", 120), pct(self.wo)),
                (self.wo, self.act, QColor(T("err")), f"{(self.act - self.wo) * 100:+.2f} pt"),
                (self.act, lo, QColor(T("accent")), pct(self.act))]
        prev_top = None
        for i, (a, b, col, txt) in enumerate(bars):
            cx = i * cw + cw / 2
            y1, y2 = y_of(max(a, b)), y_of(min(a, b))
            if y2 - y1 < 2:
                y1 -= 1
                y2 += 1
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(cx - bw / 2, y1, bw, y2 - y1), 4, 4)
            p.setFont(_num_font(12, True))
            p.setPen(QColor(T("err") if i == 1 else T("text")))
            p.drawText(QRectF(cx - cw / 2, y1 - val_h - 2, cw, val_h), Qt.AlignCenter, txt)
            p.setFont(_ui_font(11))
            p.setPen(QColor(T("muted")))
            p.drawText(QRectF(cx - cw / 2, base + 4, cw, lab_h), Qt.AlignCenter, self.labels[i])
            if prev_top is not None:                         # 连接线
                p.setPen(QPen(QColor(T("faint")), 1, Qt.DotLine))
                p.drawLine(QPointF(prev_top[0], prev_top[1]), QPointF(cx - bw / 2, prev_top[1]))
            prev_top = (cx + bw / 2, y_of(a if i == 0 else (b if i == 1 else a)))
        p.setPen(QPen(QColor(T("line")), 1))
        p.drawLine(QPointF(0, base), QPointF(W, base))
        p.end()


# =====================================================================
# 门店排名
# =====================================================================
class StoreRank(QWidget):
    picked = Signal(object)            # 门店号；再点一次选中的 = None（全部门店）

    ROW = 40

    def __init__(self):
        super().__init__()
        self.rows, self.sel, self.hover = [], None, -1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set(self, rows, sel):
        """rows = [(门店号, 名称, 销售额, 毛利率, 比活动前, 影响)]"""
        self.rows, self.sel = rows, sel
        self.setMinimumHeight(max(1, len(rows)) * self.ROW)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.update()

    def _rh(self):
        """一行多高：卡片比旁边矮时把行放高一点铺满（最多 60）"""
        return min(60, max(self.ROW, self.height() / max(1, len(self.rows))))

    def _row(self, y):
        i = int(y // self._rh())
        return i if 0 <= i < len(self.rows) else -1

    def mouseMoveEvent(self, e):
        i = self._row(e.position().y())
        if i != self.hover:
            self.hover = i
            self.update()
        if i >= 0:
            sid, name, rv, m, ch, imp = self.rows[i]
            self.setToolTip(tr("{0}：销售额 {1} · 毛利率 {2} · 比活动前 {3} · 当月毛利率 {4}\n点一下：整页只看这家店",
                               name, eur(rv, 2), pct(m), f"{ch:+.0%}" if ch is not None else "—",
                               f"{imp:+.2f} pt" if imp is not None else "—"))

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._row(e.position().y())
        if i >= 0:
            sid = self.rows[i][0]
            self.picked.emit(None if sid == self.sel else sid)

    def paintEvent(self, _e):
        if not self.rows:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W = self.width()
        name_w, right_w = 104, 150
        bar_x, bar_w = name_w, max(40, W - name_w - right_w - 12)
        top = max(r[2] for r in self.rows) or 1
        for i, (sid, name, rv, m, ch, imp) in enumerate(self.rows):
            rh = self._rh()
            y = i * rh
            sel = sid == self.sel
            if sel or i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha("accent", 26 if sel else 12))
                p.drawRoundedRect(QRectF(0, y + 2, W, rh - 4), 8, 8)
            p.setFont(_ui_font(12, True))
            p.setPen(QColor(T("accent") if sel else T("text")))
            p.drawText(QRectF(10, y, name_w - 14, rh), Qt.AlignVCenter | Qt.AlignLeft,
                       QFontMetrics(p.font()).elidedText(name, Qt.ElideRight, name_w - 14))
            bh = 12
            by = y + (rh - bh) / 2
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T("track")))
            p.drawRoundedRect(QRectF(bar_x, by, bar_w, bh), bh / 2, bh / 2)
            w = bar_w * rv / top
            p.setBrush(QColor(T("accent")) if (sel or self.sel is None) else _alpha("accent", 120))
            if w > 0:
                p.drawRoundedRect(QRectF(bar_x, by, max(bh, w), bh), bh / 2, bh / 2)
            # 右边：金额 · 毛利率 · 比活动前
            rx = W - right_w
            p.setFont(_num_font(12, True))
            p.setPen(QColor(T("text")))
            p.drawText(QRectF(rx, y, 70, rh), Qt.AlignVCenter | Qt.AlignRight, eur_short(rv))
            p.setFont(_num_font(11))
            p.setPen(QColor(T("err") if (m or 0) < 0 else T("muted")))
            p.drawText(QRectF(rx + 72, y, 36, rh), Qt.AlignVCenter | Qt.AlignRight, pct(m, 0))
            if ch is not None:
                tone = _delta_tone(ch)
                p.setPen(QColor(T(tone)))
                arrow = "▲" if ch > 0 else "▼" if ch < 0 else ""
                p.drawText(QRectF(rx + 108, y, 42, rh), Qt.AlignVCenter | Qt.AlignRight,
                           f"{arrow}{abs(ch):.0%}" if abs(ch) < 10 else f"{arrow}>999%")
        p.end()


# =====================================================================
# 销售额前几名商品
# =====================================================================
class TopItems(QWidget):
    picked = Signal(str)               # 双击一行：条码（跳到商品明细搜这个）

    ROW = 30

    def __init__(self):
        super().__init__()
        self.rows, self.hover = [], -1
        self.setMouseTracking(True)

    def set(self, rows):
        """rows = [(条码, 名称, 销售额, 毛利率, 件数)]"""
        self.rows = rows
        self.setFixedHeight(max(1, len(rows)) * self.ROW)
        self.update()

    def mouseMoveEvent(self, e):
        i = int(e.position().y() // self.ROW)
        i = i if 0 <= i < len(self.rows) else -1
        if i != self.hover:
            self.hover = i
            self.update()
        if i >= 0:
            ean, name, rv, m, q = self.rows[i]
            self.setToolTip(f"{name}\n{ean} · {q:,.0f} " + tr("件") + f" · {eur(rv, 2)} · " + tr("毛利率") + f" {pct(m)}")

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mouseDoubleClickEvent(self, e):
        i = int(e.position().y() // self.ROW)
        if 0 <= i < len(self.rows):
            self.picked.emit(self.rows[i][0] or self.rows[i][1])

    def paintEvent(self, _e):
        if not self.rows:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W = self.width()
        top = max(r[2] for r in self.rows) or 1
        right_w = 118
        name_w = max(80, int((W - 26 - right_w) * 0.62))
        bar_x = 26 + name_w + 8
        bar_w = max(20, W - bar_x - right_w - 6)
        for i, (ean, name, rv, m, q) in enumerate(self.rows):
            y = i * self.ROW
            if i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha("accent", 12))
                p.drawRoundedRect(QRectF(0, y + 1, W, self.ROW - 2), 6, 6)
            p.setFont(_num_font(11, True))
            p.setPen(QColor(T("accent") if i < 3 else T("faint")))
            p.drawText(QRectF(2, y, 20, self.ROW), Qt.AlignCenter, str(i + 1))
            p.setFont(_ui_font(12))
            p.setPen(QColor(T("text")))
            p.drawText(QRectF(26, y, name_w, self.ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       QFontMetrics(p.font()).elidedText(name or ean, Qt.ElideRight, name_w))
            bh = 6
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T("track")))
            p.drawRoundedRect(QRectF(bar_x, y + (self.ROW - bh) / 2, bar_w, bh), 3, 3)
            p.setBrush(_alpha("accent", 200))
            p.drawRoundedRect(QRectF(bar_x, y + (self.ROW - bh) / 2, max(bh, bar_w * rv / top), bh), 3, 3)
            p.setFont(_num_font(12, True))
            p.setPen(QColor(T("text")))
            p.drawText(QRectF(W - right_w, y, 70, self.ROW), Qt.AlignVCenter | Qt.AlignRight, eur(rv))
            p.setFont(_num_font(11))
            p.setPen(QColor(T("err") if (m or 0) < 0 else T("muted")))
            p.drawText(QRectF(W - 46, y, 44, self.ROW), Qt.AlignVCenter | Qt.AlignRight, pct(m, 0))
        p.end()


class StatPill(QLabel):
    """可以点的小统计：「没卖出 156」"""
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)

    def set(self, text, tone):
        self.setText(text)
        fg = T(tone) if tone != "muted" else T("muted")
        bg = T(tone + "_soft") if tone in ("ok", "err", "warn", "accent") else T("track")
        self.setStyleSheet(f"color:{fg}; background:{bg}; border-radius:11px; padding:2px 10px; font-size:12px; "
                           f"font-weight:600;")

    def mousePressEvent(self, _e):
        self.clicked.emit()


# =====================================================================
# 整份报告
# =====================================================================
class Report(QWidget):
    store_picked = Signal(object)
    show_items = Signal(str)            # 「sold / unsold / loss」或条码：跳到商品明细

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        # KPI
        kr = QHBoxLayout()
        kr.setSpacing(12)
        self.k_rev = Kpi(tr("销售额"), tr("名单里的商品在活动日期里的销售额（不含税）"))
        self.k_pro = Kpi(tr("毛利"), tr("销售额 − 进价"))
        self.k_mar = Kpi(tr("毛利率"), tr("毛利 ÷ 销售额"))
        self.k_qty = Kpi(tr("销量"), tr("卖了多少件"))
        self.k_dis = Kpi(tr("让利"), tr("让利 = (原价 − 实际售价) × 数量，不含税。原价用名单里写的原价（Prezzo Continuo / 促销单原价），名单没写的用收银记录里的原价"))
        for k in (self.k_rev, self.k_pro, self.k_mar, self.k_qty, self.k_dis):
            kr.addWidget(k)
        v.addLayout(kr)
        # 第二排
        r2 = QHBoxLayout()
        r2.setSpacing(12)
        self.c_trend = Card(tr("每天销售额与毛利率"))
        self.legend = Legend([])
        self.c_trend.head.addWidget(self.legend)
        self.trend = TrendChart()
        self.c_trend.v.addWidget(self.trend, 1)
        r2.addWidget(self.c_trend, 5)
        self.c_imp = Card(tr("对当月毛利率的影响"))
        ib = QHBoxLayout()
        ib.setSpacing(10)
        self.imp_big = QLabel("—")
        self.imp_big.setObjectName("RpBig")
        ib.addWidget(self.imp_big, 0, Qt.AlignBottom)
        self.imp_unit = QLabel(tr("个百分点"))
        self.imp_unit.setObjectName("RpFaint")
        ib.addWidget(self.imp_unit, 0, Qt.AlignBottom)
        ib.addStretch()
        self.c_imp.v.addLayout(ib)
        self.imp_sub = QLabel("")
        self.imp_sub.setObjectName("RpMuted")
        self.imp_sub.setWordWrap(True)
        self.c_imp.v.addWidget(self.imp_sub)
        self.bridge = Bridge()
        self.c_imp.v.addWidget(self.bridge, 1)
        self.imp_foot = QLabel("")
        self.imp_foot.setObjectName("RpFaint")
        self.c_imp.v.addWidget(self.imp_foot)
        r2.addWidget(self.c_imp, 3)
        v.addLayout(r2)
        # 第三排
        r3 = QHBoxLayout()
        r3.setSpacing(12)
        self.c_rank = Card(tr("门店排名 · 按销售额"))
        self.rank_hint = QLabel(tr("毛利率 · 比活动前"))
        self.rank_hint.setObjectName("RpFaint")
        self.c_rank.head.addWidget(self.rank_hint)
        self.rank = StoreRank()
        self.rank.picked.connect(self.store_picked)
        self.c_rank.v.addWidget(self.rank, 1)
        r3.addWidget(self.c_rank, 1)
        self.c_top = Card(tr("销售额前 8 名商品"), tr("双击一个商品：在下面的商品明细里看它"))
        self.top = TopItems()
        self.top.picked.connect(self.show_items)
        self.c_top.v.addWidget(self.top)
        self.c_top.v.addStretch()
        pr = QHBoxLayout()
        pr.setSpacing(8)
        self.p_sold, self.p_unsold, self.p_loss = StatPill(), StatPill(), StatPill()
        for w, key, tip in ((self.p_sold, "sold", tr("点一下：商品明细只看卖出的")),
                            (self.p_unsold, "unsold", tr("点一下：商品明细只看没卖出的")),
                            (self.p_loss, "loss", tr("点一下：商品明细只看毛利为负的"))):
            w.setToolTip(tip)
            w.clicked.connect(lambda k=key: self.show_items.emit(k))
            pr.addWidget(w)
        pr.addStretch()
        self.c_top.v.addLayout(pr)
        r3.addWidget(self.c_top, 1)
        v.addLayout(r3)
        self.restyle()

    # ---------- 填数据 ----------
    def show(self, res, store, kname, names):
        import volantino as V
        a = V.view(res, store)
        prev = a.get("prev") or {}
        nd = (res["end"] - res["start"]).days + 1
        days = V.day_series(res, store)
        today = date.today()
        past = [x for x in days if x[0] <= today] or days
        before_lbl = tr("比活动前 {0} 天", nd)
        before_tip = tr("活动开始前同样天数（{0:%d/%m} – {1:%d/%m}）名单里这些商品卖的", res["before"][0], res["before"][1])

        def dset(k, cur, old, fmt_val, spark, spark_tone="accent", good_up=True, sub=""):
            ch = V.change(cur, old)
            txt = "" if ch is None else (f"{'▲' if ch >= 0 else '▼'} {abs(ch):.0%}" if abs(ch) < 10 else "▲ >999%")
            k.set(fmt_val, txt, _delta_tone(ch, good_up), sub or (before_lbl if txt else tr("活动前没有销售")),
                  spark, spark_tone, before_tip)

        dset(self.k_rev, a.get("rev", 0), prev.get("rev", 0), eur(a.get("rev", 0)), [x[1] for x in past])
        dset(self.k_pro, a.get("profit", 0), prev.get("profit", 0), eur(a.get("profit", 0)), [x[2] for x in past], "ok")
        m, pm = a.get("margin"), prev.get("margin")
        md = None if m is None or pm is None else (m - pm) * 100
        self.k_mar.set(pct(m), "" if md is None else f"{'▲' if md >= 0 else '▼'} {abs(md):.1f} pt",
                       _delta_tone(md / 100 if md is not None else None), before_lbl if md is not None else "",
                       [(pr / rv if rv else 0) for _, rv, pr in past], "near", before_tip)
        qty_days = [v for d, v in V.day_values(res, "qty", store) if d <= today]
        disc_days = [v for d, v in V.day_values(res, "disc", store) if d <= today]
        dset(self.k_qty, a.get("qty", 0), prev.get("qty", 0), f"{a.get('qty', 0):,.0f}", qty_days)
        disc_rate = a.get("disc", 0) / (a.get("rev", 0) + a.get("disc", 0)) if (a.get("rev", 0) + a.get("disc", 0)) else None
        self.k_dis.set(eur(a.get("disc", 0)), tr("平均折扣 {0}", pct(disc_rate, 0)) if disc_rate else "", "warn",
                       "", disc_days, "warn", tr("让利 ÷ (销售额 + 让利)：顾客平均少付了几成"))
        self.k_dis.sub.setText(tr("占当月销售额 {0}", pct(a.get("share"))))
        # 走势
        ref = prev.get("rev", 0) / nd if prev.get("rev") else None
        self.trend.set(days, ref)
        items = [("accent", "bar", tr("毛利")), (("accent", 70), "bar", tr("销售额")), ("near", "line", tr("毛利率"))]
        if ref:
            items.append(("muted", "dash", tr("活动前日均")))
        self.legend.set_items(items)
        self.legend.setMinimumWidth(QFontMetrics(_ui_font(11)).horizontalAdvance("".join(t for *_x, t in items)) +
                                    32 * len(items))
        # 影响
        im = a.get("impact") or {}
        imp = im.get("impact")
        self.imp_big.setText("—" if imp is None else f"{imp:+.2f}")
        self.imp_big.setStyleSheet(f"color:{T('err') if (imp or 0) <= -0.5 else T('warn') if (imp or 0) < 0 else T('ok')};")
        mrng = res["month"]
        months = (mrng[1].year - mrng[0].year) * 12 + mrng[1].month - mrng[0].month + 1
        self.c_imp.set_title(tr("对当月毛利率的影响") if months <= 1 else tr("对活动这几个月毛利率的影响"),
                             tr("这几家店所有商品（不只是 {0}）在「当月」的毛利率；当月 = 活动天数最多的那个月"
                                "（{1:%d/%m} – {2:%d/%m}，最多到销售数据最新那天）。\n没有让利 = 假设 {0} 商品按原价卖："
                                "(当月毛利 + 当月里的让利) ÷ (当月销售额 + 当月里的让利)，活动落在别的月的那几天不算\n"
                                "影响 = 实际 − 没有让利，单位是百分点", kname, mrng[0], mrng[1]))
        self.imp_sub.setText(tr("{0} 让利把全店毛利率从 {1} 拉到 {2}", kname, pct(im.get("margin_wo")), pct(im.get("margin"))))
        self.bridge.set(im.get("margin_wo"), im.get("margin"), (tr("没有让利"), tr("让利"), tr("实际")))
        self.imp_foot.setText(tr("{0} 占当月销售额 {1} · 当月 {2:%d/%m} – {3:%d/%m}", kname, pct(a.get("share")),
                                 mrng[0], mrng[1]))
        # 门店排名
        rows = []
        for sid in res["stores"]:
            s = res["store"][sid]
            rows.append((sid, names.get(sid, str(sid)), s.get("rev", 0), s.get("margin"),
                         V.change(s.get("rev", 0), (s.get("prev") or {}).get("rev", 0)), (s.get("impact") or {}).get("impact")))
        rows.sort(key=lambda r: r[2], reverse=True)
        self.rank.set(rows, store)
        # 商品
        tops = V.top_items(res, store, 8)
        self.top.set([(r["ean"], r["name"], r["rev"], r.get("margin"), r["qty"]) for r in tops])
        all_rows = V.item_rows(res, store)
        sold = sum(1 for r in all_rows if r["qty"] > 0)
        loss = sum(1 for r in all_rows if r.get("margin") is not None and r["margin"] < 0)
        self.p_sold.set(tr("卖出 {0} / {1}", sold, len(all_rows)), "accent")
        self.p_unsold.set(tr("没卖出 {0}", len(all_rows) - sold), "warn" if len(all_rows) - sold else "muted")
        self.p_loss.set(tr("毛利为负 {0}", loss), "err" if loss else "muted")
        self.k_rev.cap.setToolTip(tr("{0} 名单里的商品在活动日期里的销售额（不含税）", kname))

    def restyle(self):
        from theme import NUM_FONT
        self.setStyleSheet(
            f"#RpCard {{ background:{T('surface')}; border:1px solid {T('line')}; border-radius:12px; }}"
            f"#RpTitle {{ font-size:14px; font-weight:600; color:{T('text')}; }}"
            f"#RpKpiCap {{ font-size:12px; color:{T('muted')}; font-weight:600; }}"
            f"#RpKpiVal {{ font-family:\"{NUM_FONT}\"; font-size:26px; font-weight:600; color:{T('text')}; }}"
            f"#RpBig {{ font-family:\"{NUM_FONT}\"; font-size:34px; font-weight:600; }}"
            f"#RpMuted {{ color:{T('muted')}; font-size:12px; }}"
            f"#RpFaint {{ color:{T('faint')}; font-size:11px; }}")
        for w in self.findChildren(QWidget):
            w.update()
