# -*- coding: utf-8 -*-
"""
SELL OUT 分析页（分析 → SELL OUT，2026-11-16）

  页头：SELL OUT 分析（大字）+ 一行说明（日期 · 门店 · 几个商品 · 销售数据到）· 右边 导出 Excel · ⋯
  筛选卡：日期（快捷 + 两个日期）· 门店（可以多选，全部 = 五家店 + 仓库）
          供应商 · 大类 · 中类 · 小类（都能搜、能多选，下一级只列上一级里面的）· 商品名单（导入 Excel）· 清空 · 查询
  KPI：采购额 · 销售额 · 销售率 · 毛利 · 剩余库存
  进货和销售走势（累计进货额 vs 累计销售额 + 每天销售额柱子）· 卖得怎么样（卖完 / 卖得好 / 一般 / 滞销 / 没卖出…，点一行 = 表格只看这些）
  明细表：按 商品 / 供应商 / 大类 / 中类 / 小类 / 门店 看；列和 ERP「进销存报表」一样（两行表头分组），最下面一行合计；负数红字
数据和公式见 sellout.py。
"""
import threading
from datetime import date

from PySide6.QtCore import Qt, QObject, Signal, QRectF, QPointF, QSize, QDate, QTimer, QPoint
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QFontMetrics, QPainterPath
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QDateEdit,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QHeaderView, QTableWidget,
                               QTableWidgetItem, QAbstractItemView, QSizePolicy, QScrollArea, QButtonGroup,
                               QFileDialog, QMessageBox, QStackedWidget)

from i18n import tr


def T(k):
    import theme
    return theme.T(k)


def _num_font(px, bold=False):
    from theme import NUM_FONT, crisp
    f = QFont(NUM_FONT)
    f.setPixelSize(px)
    f.setBold(bold)
    return crisp(f)


def _alpha(c, a):
    q = QColor(T(c))
    q.setAlpha(a)
    return q


def _eur(v, dec=2):
    return "" if v is None else f"{v:,.{dec}f}"


def _pct(v):
    return "" if v is None else f"{v * 100:.1f}%"


def _qty(v):
    return "" if v is None else f"{v:,.0f}"


class _Sig(QObject):
    done = Signal(object)
    lists = Signal(object)


# =====================================================================
# 能搜、能多选的下拉（供应商 / 大类 / 中类 / 小类）
# =====================================================================
class _PickPopup(QFrame):
    def __init__(self, owner):
        super().__init__(None, Qt.Popup)
        self.owner = owner
        self.setObjectName("SoPop")
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        self.q = QLineEdit()
        self.q.setPlaceholderText(tr("搜索"))
        self.q.textChanged.connect(self._filter)
        v.addWidget(self.q)
        self.lst = QListWidget()
        self.lst.setObjectName("SoPopList")
        self.lst.itemChanged.connect(lambda _it: self._count())
        v.addWidget(self.lst, 1)
        h = QHBoxLayout()
        self.n = QLabel("")
        self.n.setObjectName("Faint")
        h.addWidget(self.n)
        h.addStretch()
        b = QPushButton(tr("清空"))
        b.setObjectName("Link")
        b.clicked.connect(self._clear)
        h.addWidget(b)
        ok = QPushButton(tr("确定"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self._ok)
        h.addWidget(ok)
        v.addLayout(h)
        self.resize(360, 400)

    def fill(self, items, picked):
        self.lst.blockSignals(True)
        self.lst.clear()
        picked = set(picked)
        for key, text in items:
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if key in picked else Qt.Unchecked)
            self.lst.addItem(it)
        self.lst.blockSignals(False)
        self.q.clear()
        self._count()

    def _filter(self, text):
        q = text.strip().lower()
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def _count(self):
        n = sum(1 for i in range(self.lst.count()) if self.lst.item(i).checkState() == Qt.Checked)
        self.n.setText(tr("已选 {0}", n) if n else tr("全部"))

    def _clear(self):
        self.lst.blockSignals(True)
        for i in range(self.lst.count()):
            self.lst.item(i).setCheckState(Qt.Unchecked)
        self.lst.blockSignals(False)
        self._count()

    def _ok(self):
        keys = [self.lst.item(i).data(Qt.UserRole) for i in range(self.lst.count())
                if self.lst.item(i).checkState() == Qt.Checked]
        self.hide()
        self.owner.set_picked(keys, emit=True)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._ok()
            return
        super().keyPressEvent(e)


class MultiPick(QPushButton):
    changed = Signal(list)

    def __init__(self, label, tip=""):
        super().__init__()
        self.label, self.items, self.picked = label, [], []
        self.setObjectName("SoPick")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setToolTip(tip)
        self.pop = _PickPopup(self)
        self.clicked.connect(self._open)
        self._text()

    def set_items(self, items):
        self.items = list(items)
        keys = {k for k, _ in self.items}
        self.picked = [k for k in self.picked if k in keys]
        self._text()

    def set_picked(self, keys, emit=False):
        self.picked = list(keys)
        self._text()
        if emit:
            self.changed.emit(self.picked)

    def _text(self):
        names = dict(self.items)
        if not self.picked:
            t = tr("{0}：全部", self.label)
        elif len(self.picked) == 1:
            t = f"{self.label}：{names.get(self.picked[0], '?')}"
        else:
            t = tr("{0}：{1} 个", self.label, len(self.picked))
        fm = QFontMetrics(self.font())
        self.setText(fm.elidedText(t, Qt.ElideRight, 210) + "  ▾")
        self.setProperty("on", bool(self.picked))
        self.style().unpolish(self)
        self.style().polish(self)
        full = "\n".join(names.get(k, "?") for k in self.picked[:30])
        self.setToolTip(full or self.toolTip())

    def _open(self):
        self.pop.fill(self.items, self.picked)
        self.pop.setStyleSheet(f"#SoPop {{ background:{T('surface')}; border:1px solid {T('border')}; border-radius:10px; }}"
                               f"#SoPopList {{ border:1px solid {T('line')}; border-radius:6px; }}")
        g = self.mapToGlobal(QPoint(0, self.height() + 4))
        try:
            scr = self.screen().availableGeometry()
            g.setX(min(g.x(), scr.right() - self.pop.width() - 8))
            if g.y() + self.pop.height() > scr.bottom():
                g.setY(self.mapToGlobal(QPoint(0, 0)).y() - self.pop.height() - 4)
        except Exception:
            pass
        self.pop.move(g)
        self.pop.show()
        self.pop.q.setFocus()


# =====================================================================
# 两行表头：上面一行分组（采购 / 销售 / 销售率 …），下面列名
# =====================================================================
class GroupHeader(QHeaderView):
    def __init__(self, bands, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.bands = bands                 # {列: 分组名}
        self.setSectionsMovable(True)
        self.setSectionsClickable(True)

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width(), 58)

    def paintSection(self, p, rect, logical):
        if logical in self.bands:
            r = rect.adjusted(0, rect.height() // 2, 0, 0)
        else:
            r = rect
        p.save()
        super().paintSection(p, r, logical)
        p.restore()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self.viewport())
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(12)
        p.setFont(f)
        h = self.height() // 2
        runs = []
        for v in range(self.count()):
            lg = self.logicalIndex(v)
            if self.isSectionHidden(lg):
                continue
            b = self.bands.get(lg)
            x = self.sectionViewportPosition(lg)
            w = self.sectionSize(lg)
            if runs and runs[-1][0] == b and b is not None and runs[-1][2] == x:
                runs[-1][2] = x + w
            else:
                runs.append([b, x, x + w])
        for b, x1, x2 in runs:
            if b is None:
                continue
            r = QRectF(x1, 0, x2 - x1, h)
            p.fillRect(r, QColor(T("surface2")))
            p.setPen(QPen(QColor(T("line")), 1))
            p.drawLine(QPointF(x1 + 6, h - 0.5), QPointF(x2 - 6, h - 0.5))
            p.drawLine(QPointF(x2 - 0.5, 4), QPointF(x2 - 0.5, h - 4))
            p.setPen(QColor(T("head")))
            p.drawText(r, Qt.AlignCenter, tr(b))
        p.end()


# =====================================================================
# 进货和销售走势：累计进货额（紫线）· 累计销售额（蓝线）· 每天销售额（浅柱）
# =====================================================================
class FlowChart(QWidget):
    def __init__(self):
        super().__init__()
        self.days, self.hover = [], -1
        self.setMouseTracking(True)
        self.setMinimumHeight(250)

    def set(self, days):
        self.days = list(days)
        self.hover = -1
        self.update()

    def _geo(self):
        W, H = self.width(), self.height()
        return 56, 10, max(10, W - 56 - 12), max(10, H - 10 - 34)

    def mouseMoveEvent(self, e):
        n = len(self.days)
        if not n:
            return
        x0, _y0, w, _h = self._geo()
        i = int((e.position().x() - x0) / (w / n))
        i = i if 0 <= i < n else -1
        if i != self.hover:
            self.hover = i
            self.update()
        if i >= 0:
            d, sq, sv, pq, pv = self.days[i]
            cp = sum(x[4] for x in self.days[:i + 1])
            cs = sum(x[2] for x in self.days[:i + 1])
            self.setToolTip(tr("{0:%d/%m/%Y}\n这天：销售 € {1:,.2f}（{2:,.0f} 件）· 进货 € {3:,.2f}（{4:,.0f} 件）\n"
                               "到这天累计：进货 € {5:,.2f} · 销售 € {6:,.2f}", d, sv, sq, pv, pq, cp, cs))

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def paintEvent(self, _e):
        n = len(self.days)
        if not n:
            return
        from volantino_report import _nice_top, eur_short
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x0, y0, w, h = self._geo()
        cp, cs, acc_p, acc_s = [], [], 0.0, 0.0
        for d, sq, sv, pq, pv in self.days:
            acc_p += pv
            acc_s += sv
            cp.append(acc_p)
            cs.append(acc_s)
        top = _nice_top(max(max(cp + cs), 1.0))
        # 网格 + 左边刻度
        p.setFont(_num_font(10))
        for k in range(5):
            y = y0 + h - h * k / 4
            p.setPen(QPen(QColor(T("line")), 1, Qt.DashLine if k else Qt.SolidLine))
            p.drawLine(QPointF(x0, y), QPointF(x0 + w, y))
            p.setPen(QColor(T("faint")))
            p.drawText(QRectF(0, y - 8, x0 - 8, 16), Qt.AlignRight | Qt.AlignVCenter, eur_short(top * k / 4))
        bw = w / n
        # 每天销售额：浅柱（自己的刻度：最高的一天 = 图高 40%）
        dmax = max(x[2] for x in self.days) or 1.0
        for i, (d, sq, sv, pq, pv) in enumerate(self.days):
            if i == self.hover:
                p.fillRect(QRectF(x0 + i * bw, y0, bw, h), _alpha("accent", 18))
            bh = h * 0.4 * max(sv, 0) / dmax
            if bh > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha("accent", 60))
                p.drawRoundedRect(QRectF(x0 + i * bw + bw * 0.18, y0 + h - bh, bw * 0.64, bh), 2, 2)

        def line(vals, tone, fill):
            path = QPainterPath()
            for i, v in enumerate(vals):
                pt = QPointF(x0 + (i + 0.5) * bw, y0 + h - h * v / top)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            if fill:
                area = QPainterPath(path)
                area.lineTo(QPointF(x0 + (n - 0.5) * bw, y0 + h))
                area.lineTo(QPointF(x0 + 0.5 * bw, y0 + h))
                area.closeSubpath()
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha(tone, 26))
                p.drawPath(area)
            p.setPen(QPen(QColor(T(tone)), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        line(cp, "violet", False)
        line(cs, "accent", True)
        if self.hover >= 0:
            for vals, tone in ((cp, "violet"), (cs, "accent")):
                pt = QPointF(x0 + (self.hover + 0.5) * bw, y0 + h - h * vals[self.hover] / top)
                p.setPen(QPen(QColor(T("surface")), 2))
                p.setBrush(QColor(T(tone)))
                p.drawEllipse(pt, 4, 4)
        # 下面日期：最多 8 个
        p.setFont(_num_font(10))
        p.setPen(QColor(T("muted")))
        step = max(1, -(-n // 8))
        for i in range(0, n, step):
            d = self.days[i][0]
            p.drawText(QRectF(x0 + i * bw - 30 + bw / 2, y0 + h + 8, 60, 16), Qt.AlignCenter, f"{d:%d/%m}")
        p.end()


# =====================================================================
# 卖得怎么样：一行一种（点一行 = 表格只看这些）
# =====================================================================
class BucketList(QWidget):
    picked = Signal(object)            # 键；再点一次选中的 = None
    ROW = 28

    def __init__(self):
        super().__init__()
        self.rows, self.sel, self.hover = [], None, -1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set(self, rows, sel):
        """rows = [(键, 名称, 颜色, 商品数, 剩余额)]"""
        self.rows = [r for r in rows if r[3]]
        self.sel = sel
        self.setFixedHeight(max(1, len(self.rows)) * self.ROW)
        self.update()

    def _row(self, y):
        i = int(y // self.ROW)
        return i if 0 <= i < len(self.rows) else -1

    def mouseMoveEvent(self, e):
        i = self._row(e.position().y())
        if i != self.hover:
            self.hover = i
            self.update()
        tips = {"out": tr("卖出的件数 ≥ 进货件数（剩余量 ≤ 0）"), "good": tr("卖掉了进货的 70% – 100%"),
                "mid": tr("卖掉了进货的 30% – 70%"), "slow": tr("卖掉的不到进货的 30%"),
                "none": tr("进了货、一件没卖出"), "nobuy": tr("这段时间没进货，只有销售（卖的是以前的库存）"),
                "stock": tr("这段时间没进也没卖，只有库存")}
        if i >= 0:
            k, name, _t, n, rem = self.rows[i]
            self.setToolTip(tr("{0}：{1} 个商品 · 剩余额 € {2:,.2f}\n{3}\n点一下：下面表格只看这些", tr(name), n, rem, tips.get(k, "")))

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._row(e.position().y())
        if i >= 0:
            k = self.rows[i][0]
            self.picked.emit(None if k == self.sel else k)

    def paintEvent(self, _e):
        if not self.rows:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W = self.width()
        tot = sum(r[3] for r in self.rows) or 1
        name_w, right_w = 96, 150
        bx, bw = name_w, max(30, W - name_w - right_w - 10)
        for i, (k, name, tone, n, rem) in enumerate(self.rows):
            y = i * self.ROW
            sel = k == self.sel
            if sel or i == self.hover:
                p.setPen(QPen(QColor(T("accent_ring")), 1) if sel else Qt.NoPen)
                p.setBrush(_alpha("accent", 26 if sel else 12))
                p.drawRoundedRect(QRectF(0.5, y + 2.5, W - 1, self.ROW - 5), 8, 8)
            col = QColor(T(tone)) if tone != "muted" else QColor(T("faint"))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(14, y + self.ROW / 2), 4, 4)
            f = QFont()
            f.setPixelSize(13)
            p.setFont(f)
            p.setPen(QColor(T("text")))
            p.drawText(QRectF(26, y, name_w - 26, self.ROW), Qt.AlignVCenter, tr(name))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T("track")))
            p.drawRoundedRect(QRectF(bx, y + self.ROW / 2 - 4, bw, 8), 4, 4)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(bx, y + self.ROW / 2 - 4, max(8.0, bw * n / tot), 8), 4, 4)
            p.setFont(_num_font(12))
            p.setPen(QColor(T("text")))
            p.drawText(QRectF(W - right_w, y, 48, self.ROW), Qt.AlignVCenter | Qt.AlignRight, f"{n:,}")
            p.setPen(QColor(T("err") if rem < 0 else T("head")))
            p.drawText(QRectF(W - right_w + 52, y, right_w - 56, self.ROW), Qt.AlignVCenter | Qt.AlignRight,
                       f"€ {rem:,.0f}")
        p.end()


class TopRemain(QWidget):
    """剩余额最多的几个商品（压货最多）：点一个 = 下面表格搜它"""
    picked = Signal(str)
    ROW = 26

    def __init__(self):
        super().__init__()
        self.rows, self.hover = [], -1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set(self, rows):
        """rows = [(名称, 条码, 剩余量, 剩余额)]"""
        self.rows = rows
        self.setFixedHeight(max(1, len(rows)) * self.ROW)
        self.update()

    def _row(self, y):
        i = int(y // self.ROW)
        return i if 0 <= i < len(self.rows) else -1

    def mouseMoveEvent(self, e):
        i = self._row(e.position().y())
        if i != self.hover:
            self.hover = i
            self.update()
        if i >= 0:
            n, ean, q, v = self.rows[i]
            self.setToolTip(tr("{0}\n{1} · 还剩 {2:,.0f} 件 · € {3:,.2f}\n点一下：下面表格搜它", n, ean, q, v))

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._row(e.position().y())
        if i >= 0:
            self.picked.emit(self.rows[i][1] or self.rows[i][0])

    def paintEvent(self, _e):
        p = QPainter(self)
        W = self.width()
        f = QFont()
        f.setPixelSize(12)
        fm = QFontMetrics(f)
        for i, (n, _e2, q, v) in enumerate(self.rows):
            y = i * self.ROW
            if i == self.hover:
                p.fillRect(QRectF(0, y + 1, W, self.ROW - 2), _alpha("accent", 14))
            p.setFont(f)
            p.setPen(QColor(T("head")))
            p.drawText(QRectF(8, y, W - 120, self.ROW), Qt.AlignVCenter, fm.elidedText(n, Qt.ElideRight, W - 128))
            p.setFont(_num_font(12))
            p.setPen(QColor(T("text")))
            p.drawText(QRectF(W - 112, y, 104, self.ROW), Qt.AlignVCenter | Qt.AlignRight, f"€ {v:,.0f}")
        p.end()


# =====================================================================
# 页面
# =====================================================================
COLS = ["条码", "编号", "商品", "现有库存", "采购量", "平均进价", "采购额", "销售量", "平均售价", "销售额", "件数", "金额",
        "差额率", "销售−采购", "毛利率", "毛利额", "剩余量", "剩余额", "供应商", "大类", "中类", "小类", "商品数", "卖得怎样"]
BANDS = {4: "采购", 5: "采购", 6: "采购", 7: "销售", 8: "销售", 9: "销售", 10: "销售率", 11: "销售率", 12: "销采率",
         13: "销采率", 14: "毛利", 15: "毛利", 16: "剩余库存", 17: "剩余库存"}
NUMERIC = tuple(range(3, 18)) + (22,)
WIDTHS = [124, 70, 300, 76, 70, 74, 90, 70, 74, 90, 76, 76, 70, 92, 66, 90, 70, 90, 180, 150, 150, 170, 64, 80]
TIPS = {3: "选的门店现在的库存（数据库每天早上同步）", 4: "这段时间的进货件数：CA 供应商进货 + 调入 − 调出（UI-N 不算）",
        5: "采购额 ÷ 采购量", 6: "CA 按进价；调货没有进价，按这个商品 CA 的平均进价算",
        7: "这段时间卖出的件数", 8: "销售额 ÷ 销售量（不含税）", 9: "不含税（TotRighe − TotCodIva）",
        10: "销售率（件数）= 销售量 ÷ 采购量", 11: "销售率（金额）= 销售额 ÷ 采购额",
        12: "差额率 =（销售额 − 采购额）÷ 销售额", 13: "销售额 − 采购额", 14: "毛利率 = 毛利额 ÷ 销售额",
        15: "毛利额 = 销售额 − 卖出商品的进价", 16: "剩余量 = 采购量 − 销售量（这批货还剩多少，负数 = 卖的比这段时间进的多）",
        17: "剩余额 = 剩余量 × 平均进价", 20: "小类名称「-」前面那段", 21: "小类名称「-」后面那段",
        23: "卖完 ≥ 100% · 卖得好 70–100% · 一般 30–70% · 滞销 < 30%（销售率件数）"}
VIEWS = (("item", "商品"), ("sup", "供应商"), ("cat", "大类"), ("mid", "中类"), ("small", "小类"), ("store", "门店"))


class SelloutPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cfg = settings.setdefault("sellout", {})
        self.sig = _Sig()
        self.sig.done.connect(self._done)
        self.sig.lists.connect(self._lists_arrived)
        self.res = None
        self.running = False
        self.tree = None
        self.view = self.cfg.get("view") or "item"
        self.bucket = None
        self.codes = []                # 导入的商品名单（商品编号）
        self.codes_name = ""
        self._lists_loading = False
        self.rows = []
        self.page = None

    # ---------- 界面 ----------
    def build(self):
        import sellout as SO
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 24)
        lay.setSpacing(14)
        head = QHBoxLayout()
        tv = QVBoxLayout()
        tv.setSpacing(6)
        t = QLabel(tr("SELL OUT 分析") + "  ⓘ")
        t.setObjectName("PageTitle")
        t.setToolTip(tr("进销存：选的日期、门店里每个商品进了多少、卖了多少、还剩多少，销售率、毛利。"
                        "和 ERP「进销存报表」一样的列，导出照 SELLOUT 模板。数据库只读"))
        tv.addWidget(t)
        self.h1 = QLabel(tr("SELL OUT 分析"))
        tv.addWidget(self.h1)
        self.status = QLabel(tr("选好日期、门店、供应商或分类，点「查询」"))
        tv.addWidget(self.status)
        head.addLayout(tv, 1)
        self.b_exp = QPushButton(tr("导出 Excel"))
        self.b_exp.setObjectName("Primary")
        self.b_exp.setMinimumHeight(36)
        self.b_exp.setToolTip(tr("照 SELLOUT 模板导出现在表格里的行（按现在的看法、排序、筛选）"))
        self.b_exp.clicked.connect(self.export)
        self.b_exp.setEnabled(False)
        head.addWidget(self.b_exp, 0, Qt.AlignBottom)
        more = QPushButton("⋯")
        more.setFixedSize(40, 36)
        more.setToolTip(tr("更多"))
        mm = QMenu(more)
        mm.addAction(tr("导入商品名单…"), self.import_list).setToolTip(
            tr("选一个 Excel（有条码或商品编号就行，例如 Halloween 的货单）：只分析名单里的商品"))
        self.a_clear_list = mm.addAction(tr("不用商品名单"), self.clear_list)
        mm.addAction(tr("重新读分类和供应商"), lambda: self._load_lists(force=True))
        mm.setToolTipsVisible(True)
        mm.aboutToShow.connect(lambda: self.a_clear_list.setEnabled(bool(self.codes)))
        more.setMenu(mm)
        more.setStyleSheet("QPushButton::menu-indicator { image: none; width: 0; }")
        head.addWidget(more, 0, Qt.AlignBottom)
        lay.addLayout(head)
        lay.addWidget(self._build_filters())

        self.stack = QStackedWidget()
        from erp_widgets import EmptyState
        self.empty = EmptyState("sellout", tr("还没有查询"),
                                tr("选好日期、门店，再选供应商或分类（也可以导入商品名单），点「查询」"))
        b = QPushButton(tr("查询"))
        b.setObjectName("Primary")
        b.clicked.connect(self.run)
        self.empty.buttons.addWidget(b)
        self.stack.addWidget(self.empty)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("SoBody")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 2, 4, 8)
        bv.setSpacing(14)
        self._build_report(bv)
        scroll.setWidget(body)
        self.scroll, self.body = scroll, body
        self.stack.addWidget(scroll)
        self.busy = QLabel(tr("正在读数据库…"))
        self.busy.setAlignment(Qt.AlignCenter)
        self.busy.setObjectName("Faint")
        self.stack.addWidget(self.busy)
        lay.addWidget(self.stack, 1)
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self._first_show())
        self.page = w
        self.restyle()
        return w

    def _build_filters(self):
        import sellout as SO
        box = QFrame()
        box.setObjectName("Panel")
        v = QVBoxLayout(box)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(10)
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        self.preset = QComboBox()
        for k, n in SO.PRESETS:
            self.preset.addItem(tr(n), k)
        self.preset.setMinimumHeight(34)
        r1.addWidget(self.preset)
        self.d1, self.d2 = QDateEdit(), QDateEdit()
        for d in (self.d1, self.d2):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd/MM/yyyy")
            d.setMinimumHeight(34)
            d.setFixedWidth(124)
        r1.addWidget(self.d1)
        dash = QLabel("–")
        dash.setObjectName("Faint")
        r1.addWidget(dash)
        r1.addWidget(self.d2)
        sep = QFrame()
        sep.setFixedSize(1, 22)
        self._sep = sep
        r1.addSpacing(6)
        r1.addWidget(sep)
        r1.addSpacing(6)
        self.store_btns = {}
        self.store_group = QButtonGroup(box)
        self.store_group.setExclusive(False)
        for code, name in [(None, tr("全部"))] + [(s, n) for s, n in SO.STORES] + [(SO.WAREHOUSE, tr("仓库"))]:
            b = QPushButton(name)
            b.setObjectName("ShopChip")
            b.setCheckable(True)
            b.setMinimumHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            if code == SO.WAREHOUSE:
                b.setToolTip(tr("仓库（BMS）：只算进货和库存（仓库不卖东西）"))
            elif code is None:
                b.setToolTip(tr("五家店 + 仓库：门店之间、仓库到门店的调货一进一出抵掉"))
            b.clicked.connect(lambda _=False, c=code: self._store_clicked(c))
            self.store_btns[code] = b
            r1.addWidget(b)
        r1.addStretch()
        v.addLayout(r1)
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        self.p_sup = MultiPick(tr("供应商"), tr("商品表里写的供应商（可以多选）"))
        self.p_cat = MultiPick(tr("大类"))
        self.p_mid = MultiPick(tr("中类"), tr("中类 = 小类名称里「-」前面那段（ERP 只有大类 / 小类两级）"))
        self.p_sub = MultiPick(tr("小类"))
        for p in (self.p_sup, self.p_cat, self.p_mid, self.p_sub):
            r2.addWidget(p)
        self.p_cat.changed.connect(lambda _k: self._cascade(1))
        self.p_mid.changed.connect(lambda _k: self._cascade(2))
        self.list_chip = QPushButton("")
        self.list_chip.setObjectName("ShopChip")
        self.list_chip.setCheckable(True)
        self.list_chip.setChecked(True)
        self.list_chip.setMinimumHeight(34)
        self.list_chip.setToolTip(tr("只分析这份名单里的商品；点一下不用名单"))
        self.list_chip.clicked.connect(self.clear_list)
        self.list_chip.hide()
        r2.addWidget(self.list_chip)
        r2.addStretch()
        clr = QPushButton(tr("清空筛选"))
        clr.setObjectName("Link")
        clr.clicked.connect(self.clear_filters)
        r2.addWidget(clr)
        self.b_run = QPushButton(tr("查询"))
        self.b_run.setObjectName("Primary")
        self.b_run.setMinimumHeight(34)
        self.b_run.setMinimumWidth(96)
        self.b_run.clicked.connect(self.run)
        r2.addWidget(self.b_run)
        v.addLayout(r2)
        # 上次的筛选
        c = self.cfg
        self.preset.setCurrentIndex(max(0, self.preset.findData(c.get("preset") or "month")))
        rng = SO.preset_range(self.preset.currentData())
        d1 = c.get("start") if self.preset.currentData() == "custom" else None
        d2 = c.get("end") if self.preset.currentData() == "custom" else None
        if rng:
            d1, d2 = rng
        else:
            d1, d2 = SO._d(d1) or date.today().replace(day=1), SO._d(d2) or date.today()
        self.d1.setDate(QDate(d1.year, d1.month, d1.day))
        self.d2.setDate(QDate(d2.year, d2.month, d2.day))
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.d1.dateChanged.connect(lambda _d: self._date_edited())
        self.d2.dateChanged.connect(lambda _d: self._date_edited())
        self._set_stores(c.get("stores") or [])
        return box

    def _build_report(self, bv):
        from volantino_report import Kpi, Card, Legend
        kr = QHBoxLayout()
        kr.setSpacing(12)
        self.k_buy = Kpi(tr("采购额"), tr("这段时间进的货（CA 进货 + 调入 − 调出），按进价"))
        self.k_sell = Kpi(tr("销售额"), tr("不含税"))
        self.k_rate = Kpi(tr("销售率"), tr("销售额 ÷ 采购额；标签里是件数的销售率"))
        self.k_pro = Kpi(tr("毛利额"), tr("销售额 − 卖出商品的进价"))
        self.k_rem = Kpi(tr("剩余库存"), tr("剩余额 =（采购量 − 销售量）× 平均进价；这批货还剩多少"))
        for k in (self.k_buy, self.k_sell, self.k_rate, self.k_pro, self.k_rem):
            kr.addWidget(k)
        bv.addLayout(kr)
        r2 = QHBoxLayout()
        r2.setSpacing(12)
        self.c_flow = Card(tr("进货和销售走势"), tr("线 = 从开始那天累计到这天的进货额 / 销售额；柱 = 每天的销售额。"
                                                 "蓝线追上紫线 = 进的货按金额卖回来了"))
        self.legend = Legend([("violet", "line", tr("累计进货额")), ("accent", "line", tr("累计销售额")),
                              (("accent", 90), "bar", tr("每天销售额"))])
        self.c_flow.head.addWidget(self.legend)
        self.flow = FlowChart()
        self.c_flow.v.addWidget(self.flow, 1)
        r2.addWidget(self.c_flow, 2)
        self.c_bk = Card(tr("卖得怎么样"), tr("按销售率（件数）分；右边：商品数 · 剩余额。点一行 = 下面表格只看这些"))
        self.bk = BucketList()
        self.bk.picked.connect(self.set_bucket)
        self.c_bk.v.addWidget(self.bk)
        self.c_bk.v.addStretch()
        self.rem_cap = QLabel(tr("压货最多") + "  ⓘ")
        self.rem_cap.setObjectName("RpMuted")
        self.rem_cap.setToolTip(tr("剩余额最多的商品：进了货还没卖掉的金额"))
        self.c_bk.v.addWidget(self.rem_cap)
        self.top_rem = TopRemain()
        self.top_rem.picked.connect(self._search_for)
        self.c_bk.v.addWidget(self.top_rem)
        r2.addWidget(self.c_bk, 1)
        for c in (self.c_flow, self.c_bk):
            c.setFixedHeight(380)
        bv.addLayout(r2)
        # 明细
        card = QFrame()
        card.setObjectName("RpCard")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(16, 14, 16, 12)
        cv.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        tl = QLabel(tr("明细"))
        tl.setObjectName("RpTitle")
        bar.addWidget(tl)
        bar.addSpacing(8)
        seg = QFrame()
        seg.setObjectName("Seg")
        seg.setFixedHeight(36)
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(3, 3, 3, 3)
        sh.setSpacing(2)
        self.view_group = QButtonGroup(card)
        self.view_btns = {}
        for k, n in VIEWS:
            b = QPushButton(tr(n))
            b.setObjectName("SegBtn")
            b.setCheckable(True)
            b.setChecked(k == self.view)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=k: self.set_view(k))
            self.view_group.addButton(b)
            self.view_btns[k] = b
            sh.addWidget(b)
        seg.setToolTip(tr("按什么看：一个商品一行，或按供应商 / 分类 / 门店合计"))
        bar.addWidget(seg)
        self.bk_chip = QPushButton("")
        self.bk_chip.setObjectName("ShopChip")
        self.bk_chip.setCheckable(True)
        self.bk_chip.setChecked(True)
        self.bk_chip.setMinimumHeight(30)
        self.bk_chip.setToolTip(tr("点一下：看全部"))
        self.bk_chip.clicked.connect(lambda: self.set_bucket(None))
        self.bk_chip.hide()
        bar.addWidget(self.bk_chip)
        bar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("条码 / 名称 / 供应商"))
        self.search.setFixedWidth(230)
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search)
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        cv.addLayout(bar)
        cv.addWidget(self._build_table(), 1)
        card.setMinimumHeight(560)
        bv.addWidget(card)

    def _build_table(self):
        import settings as ST
        import product_images as PI
        from erp_widgets import TableSortFilter
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        heads = [tr(x) for x in COLS]
        t = self.ui.make_table(heads, stretch_col=None, numeric_cols=NUMERIC)
        hdr = GroupHeader(BANDS, t)
        t.setHorizontalHeader(hdr)
        t.setHorizontalHeaderLabels(heads)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setDefaultAlignment(Qt.AlignCenter)
        t.setObjectName("CleanTable")
        self.thumb = PI.thumb_size(self.settings) if self.settings.get("show_images", True) else 0
        for c, wd in enumerate(WIDTHS):
            t.setColumnWidth(c, wd + (self.thumb + 10 if c == 2 and self.thumb else 0))
        if self.thumb:
            t.setItemDelegateForColumn(2, PI.ThumbDelegate(t, 0, None, self.settings))
        self.t = t
        self._row_h()
        for c, tp in TIPS.items():
            t.horizontalHeaderItem(c).setToolTip(tr(tp))
        t.cellDoubleClicked.connect(self._double)
        v.addWidget(t, 1)
        self.t = t
        # 合计（一直在最下面，列和上面对齐）
        sr = QHBoxLayout()
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(0)
        s = QTableWidget(1, len(COLS))
        s.horizontalHeader().hide()
        s.verticalHeader().hide()
        s.setShowGrid(False)
        s.setFixedHeight(38)
        s.verticalHeader().setDefaultSectionSize(38)
        s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        s.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        s.setEditTriggers(QAbstractItemView.NoEditTriggers)
        s.setSelectionMode(QAbstractItemView.NoSelection)
        s.setFocusPolicy(Qt.NoFocus)
        s.setObjectName("SoSum")
        sr.addWidget(s, 1)
        self.sum_pad = QWidget()
        self.sum_pad.setFixedWidth(0)
        sr.addWidget(self.sum_pad)
        v.addLayout(sr)
        self.t_sum = s
        hh.sectionResized.connect(lambda *_: self._sync_sum())
        hh.sectionMoved.connect(lambda *_: self._sync_sum())
        t.horizontalScrollBar().valueChanged.connect(lambda x: s.horizontalScrollBar().setValue(x))
        t.horizontalScrollBar().rangeChanged.connect(lambda *_: self._sync_sum())
        self.ctl = TableSortFilter(t, lambda: len(self.rows), lambda i, c: self.rows[i][1][c], self._fill,
                                   key=lambda i, c: self.rows[i][2][c], hide=self._hidden, numeric=NUMERIC, lazy=True)
        self.ctl.remember(self.cfg, "cols", lambda: ST.save_settings(self.settings), default_hidden=[0, 22])
        self.ctl.rendered.connect(self._rendered)
        self.search.textChanged.connect(lambda _: self.ctl.render())
        return box

    def _row_h(self):
        import product_images as PI
        h = PI.row_height(self.settings) if (self.thumb and self.view == "item") else 40
        self.t.verticalHeader().setDefaultSectionSize(h)

    # ---------- 筛选 ----------
    def _store_clicked(self, code):
        import sellout as SO
        if code is None:
            self._set_stores([])
            return
        picked = [c for c, b in self.store_btns.items() if c is not None and b.isChecked()]
        if set(picked) >= set(SO.ALL_SCOPE):
            picked = []
        self._set_stores(picked)

    def _set_stores(self, picked):
        picked = list(picked)
        for c, b in self.store_btns.items():
            b.setChecked(c in picked if c is not None else not picked)

    def stores(self):
        return [c for c, b in self.store_btns.items() if c is not None and b.isChecked()]

    def _preset_changed(self, _i):
        import sellout as SO
        rng = SO.preset_range(self.preset.currentData())
        if rng:
            for d, v in ((self.d1, rng[0]), (self.d2, rng[1])):
                d.blockSignals(True)
                d.setDate(QDate(v.year, v.month, v.day))
                d.blockSignals(False)

    def _date_edited(self):
        i = self.preset.findData("custom")
        if self.preset.currentIndex() != i:
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(i)
            self.preset.blockSignals(False)

    def _cascade(self, level):
        """选了大类 → 中类只列这些大类里的；选了中类 → 小类只列里面的"""
        import sellout as SO
        if self.tree is None:
            return
        cats = set(self.p_cat.picked)
        mids = SO.mids_of(self.tree, cats)
        if level <= 1:
            self.p_mid.set_items([(f"{c}|{m}", m + (f"  · {self.tree['cats'].get(c, '')}" if not cats else ""))
                                  for c, m in mids])
        mp = {tuple(k.split("|", 1)) for k in self.p_mid.picked}
        mp = {(int(c), m) for c, m in mp}
        subs = SO.subs_of(self.tree, cats, mp)
        self.p_sub.set_items([(f"{c}:{s}", self.tree["subs"][(c, s)]["small"] +
                               ("" if mp else f"  · {self.tree['subs'][(c, s)]['mid']}")) for c, s in subs])

    def clear_filters(self):
        for p in (self.p_sup, self.p_cat, self.p_mid, self.p_sub):
            p.set_picked([])
        self._cascade(1)
        self.clear_list()
        self._set_stores([])

    def filters(self):
        d1, d2 = self.d1.date().toPython(), self.d2.date().toPython()
        mids = []
        for k in self.p_mid.picked:
            c, m = k.split("|", 1)
            mids.append((int(c), m))
        subs = []
        for k in self.p_sub.picked:
            c, s = k.split(":", 1)
            subs.append((int(c), int(s)))
        return {"start": d1, "end": d2, "stores": self.stores(), "sups": list(self.p_sup.picked),
                "cats": list(self.p_cat.picked), "mids": mids, "subs": subs, "codes": list(self.codes)}

    def _first_show(self):
        if self.tree is None and not self._lists_loading:
            self._load_lists()

    def _load_lists(self, force=False):
        if self._lists_loading:
            return
        self._lists_loading = True
        dbs = self.settings.get("db") or None
        if force:
            import sellout as SO
            SO._TREE.clear()

        def work():
            import reorder_service as R
            import sellout as SO
            try:
                db = R.make_db(dbs)
                try:
                    tree = SO.category_tree(db)
                    sups = R.supplier_names(db)
                    try:
                        upto = R.sales_latest(db)
                    except Exception:
                        upto = None
                finally:
                    db.engine.dispose()
                self.sig.lists.emit((tree, sups, upto, None))
            except Exception as e:
                self.sig.lists.emit((None, None, None, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _lists_arrived(self, payload):
        tree, sups, upto, err = payload
        self._lists_loading = False
        if err:
            self.win.log(tr("⚠ SELL OUT：读分类 / 供应商失败：{0}", err))
            self.status.setText(tr("连不上数据库：{0}", err))
            return
        self.tree, self.upto = tree, upto
        c = self.cfg
        self.p_sup.set_items(sorted(((k, v or str(k)) for k, v in sups.items()), key=lambda kv: kv[1].upper()))
        self.p_cat.set_items([(k, f"{k} {v}") for k, v in sorted(tree["cats"].items())])
        self.p_sup.set_picked(c.get("sups") or [])
        self.p_cat.set_picked(c.get("cats") or [])
        self._cascade(1)
        self.p_mid.set_picked(c.get("mids") or [])
        self._cascade(2)
        self.p_sub.set_picked(c.get("subs") or [])

    def _save_filters(self):
        import settings as ST
        c = self.cfg
        c["preset"] = self.preset.currentData()
        c["start"] = self.d1.date().toPython().isoformat()
        c["end"] = self.d2.date().toPython().isoformat()
        c["stores"] = self.stores()
        c["sups"], c["cats"] = list(self.p_sup.picked), list(self.p_cat.picked)
        c["mids"], c["subs"] = list(self.p_mid.picked), list(self.p_sub.picked)
        c["view"] = self.view
        ST.save_settings(self.settings)

    # ---------- 商品名单 ----------
    def import_list(self):
        path, _ = QFileDialog.getOpenFileName(self.win, tr("导入商品名单"), self.cfg.get("last_dir") or "",
                                              tr("Excel 文件 (*.xlsx *.xlsm *.xls)"))
        if not path:
            return
        import os
        self.cfg["last_dir"] = os.path.dirname(path)
        from erp_widgets import BusyDialog
        dbs = self.settings.get("db") or None

        def work():
            import reorder_service as R
            import volantino as V
            items = V.read_file(path)["items"]
            db = R.make_db(dbs)
            try:
                return V._codes(db, items), len(items)
            finally:
                db.engine.dispose()
        res, err = BusyDialog.run(self.win, tr("导入商品名单"), tr("正在读名单…"), work)
        if err:
            QMessageBox.warning(self.win, tr("导入商品名单"), tr("读不了这个文件：{0}", err))
            return
        codes, n = res
        if not codes:
            QMessageBox.information(self.win, tr("导入商品名单"), tr("名单里没有认得出的商品（要有条码或商品编号）"))
            return
        self.codes = codes
        self.codes_name = os.path.basename(path)
        self.list_chip.setText(tr("名单：{0} 个商品", len(codes)) + "  ✕")
        self.list_chip.setToolTip(tr("{0}\n名单 {1} 行，认出 {2} 个商品。点一下：不用名单", self.codes_name, n, len(codes)))
        self.list_chip.show()
        self.run()

    def clear_list(self):
        self.codes, self.codes_name = [], ""
        if hasattr(self, "list_chip"):
            self.list_chip.hide()

    # ---------- 查询 ----------
    def run(self):
        if self.running:
            return
        f = self.filters()
        if f["end"] < f["start"]:
            QMessageBox.warning(self.win, tr("SELL OUT 分析"), tr("结束日期比开始早"))
            return
        if not (f["sups"] or f["cats"] or f["mids"] or f["subs"] or f["codes"]) and (f["end"] - f["start"]).days > 120:
            if QMessageBox.question(self.win, tr("SELL OUT 分析"),
                                    tr("没有选供应商、分类或商品名单，日期又超过 4 个月：要读全部商品，可能要一两分钟。继续吗？")) \
                    != QMessageBox.Yes:
                return
        self._save_filters()
        self.running = True
        self.b_run.setEnabled(False)
        self.stack.setCurrentIndex(2)
        dbs = self.settings.get("db") or None
        log = self.win.log

        def work():
            import time
            import reorder_service as R
            import sellout as SO
            t0 = time.time()
            try:
                db = R.make_db(dbs)
                try:
                    raw = SO.query(db, f, log=log)
                    try:
                        upto = R.sales_latest(db)
                    except Exception:
                        upto = None
                finally:
                    db.engine.dispose()
                res = SO.build(raw)
                res["upto"] = upto.date() if hasattr(upto, "date") else upto
                res["secs"] = time.time() - t0
                self.sig.done.emit((res, None))
            except Exception as e:
                self.sig.done.emit((None, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, payload):
        import sellout as SO
        res, err = payload
        self.running = False
        self.b_run.setEnabled(True)
        if err:
            self.stack.setCurrentIndex(0 if self.res is None else 1)
            self.win.log(tr("❌ SELL OUT 分析失败：{0}", err))
            self.win.show_toast(tr("SELL OUT 分析失败：{0}", err))
            return
        self.res = res
        self.bucket = None
        n = len(res["rows"])
        other = res.get("other_types") or {}
        self.win.log(tr("SELL OUT：{0:%d/%m/%Y} – {1:%d/%m/%Y} · {2} · {3} 个商品 · 用了 {4:.1f} 秒",
                        res["start"], res["end"], SO.scope_label(res["scope"]), n, res.get("secs", 0)) +
                     (tr("（没算的入库类型：{0}）", ", ".join(f"{k} {v:,.0f}" for k, v in sorted(other.items())))
                      if other else ""))
        self.stack.setCurrentIndex(1)
        self.b_exp.setEnabled(bool(n))
        self.update_view()

    # ---------- 显示 ----------
    def set_view(self, k):
        self.view = k
        for kk, b in self.view_btns.items():
            b.setChecked(kk == k)
        self.cfg["view"] = k
        self._row_h()
        if self.res is not None:
            self._fill_table()

    def set_bucket(self, k):
        if k is not None and self.view != "item":
            self.set_view("item")
        self.bucket = k
        import sellout as SO
        name = dict((kk, n) for kk, n, _t in SO.BUCKETS).get(k, "")
        self.bk_chip.setText(tr(name) + "  ✕" if k else "")
        self.bk_chip.setVisible(bool(k))
        if self.res is not None:
            self.bk.set(SO.bucket_counts(self.res["rows"]), k)
            self.ctl.render()
            self._update_sum()

    def update_view(self):
        import sellout as SO
        from volantino_report import eur0, pct, _delta_tone
        res = self.res
        rows = res["rows"]
        t = SO.total(rows)
        stores = SO.scope_label(res["scope"])
        nd = (res["end"] - res["start"]).days + 1
        up = res.get("upto")
        self.h1.setText(tr("SELL OUT · {0}", stores))
        self.status.setText(tr("{0:%d/%m/%Y} – {1:%d/%m/%Y} · {2} 天 · {3:,} 个商品", res["start"], res["end"], nd, len(rows)) +
                            (tr(" · 名单：{0}", self.codes_name) if self.codes_name and res["filters"].get("codes") else "") +
                            (tr(" · 销售数据到 {0:%d/%m}", up) if up else ""))
        bars = [x[2] for x in res["days"]]
        pbars = [x[4] for x in res["days"]]
        self.k_buy.set(eur0(t["pv"]), tr("{0:,.0f} 件", t["pq"]), "muted", spark=pbars, spark_tone="violet")
        self.k_sell.set(eur0(t["sv"]), tr("{0:,.0f} 件", t["sq"]), "muted", spark=bars)
        rq = t["rate_q"]
        tone = "muted" if t["rate_v"] is None else "ok" if t["rate_v"] >= 1 else "warn" if t["rate_v"] < 0.5 else "accent"
        self.k_rate.set(pct(t["rate_v"]), tr("件数 {0}", pct(rq)) if rq is not None else "", tone)
        self.k_pro.set(eur0(t["profit"]), tr("毛利率 {0}", pct(t["margin"])) if t["margin"] is not None else "",
                       "err" if (t["margin"] or 0) < 0 else "ok", spark=[], spark_tone="cyan")
        self.k_rem.set(eur0(t["rem_v"]), tr("{0:,.0f} 件", t["rem_q"]), "err" if t["rem_q"] < 0 else "muted")
        self.flow.set(res["days"])
        self.bk.set(SO.bucket_counts(rows), self.bucket)
        top = sorted((r for r in rows if r["rem_v"] > 0), key=lambda r: -r["rem_v"])[:3]
        self.top_rem.set([(r["name"], r["ean"], r["rem_q"], r["rem_v"]) for r in top])
        self.rem_cap.setVisible(bool(top))
        self._fill_table()

    def _fill_table(self):
        import sellout as SO
        res = self.res
        bk = dict((k, (n, tone)) for k, n, tone in SO.BUCKETS)
        data = SO.group(res["rows"], self.view)
        rows = []
        item = self.view == "item"
        for r in data:
            m = [r["stock"], r["pq"], r["p_avg"], r["pv"], r["sq"], r["s_avg"], r["sv"], r["rate_q"], r["rate_v"],
                 r["diff_rate"], r["diff"], r["margin"], r["profit"], r["rem_q"], r["rem_v"]]
            disp_m = [_qty(m[0]), _qty(m[1]), _eur(m[2]), _eur(m[3]), _qty(m[4]), _eur(m[5]), _eur(m[6]), _pct(m[7]),
                      _pct(m[8]), _pct(m[9]), _eur(m[10]), _pct(m[11]), _eur(m[12]), _qty(m[13]), _eur(m[14])]
            if item:
                bname = tr(bk[r["bucket"]][0])
                disp = [r["ean"], str(r["code"]), r["name"]] + disp_m + \
                       [r["sup_name"], r["cat_name"], r["mid"], r["small"], "", bname]
                keys = [r["ean"], r["code"], r["name"].lower()]
            else:
                disp = ["", "", r["name"]] + disp_m + ["", r.get("cat_name") or "", r.get("mid") or "",
                                                       r.get("small") or "", f"{r['n']:,}", ""]
                keys = ["", 0, str(r["name"]).lower()]
            keys += [(-1e18 if v is None else v) for v in m] + [x.lower() for x in disp[18:22]] + \
                    [r.get("n", 1), _BK_ORDER.get(r.get("bucket"), 9)]
            rows.append((r, disp, keys))
        self.rows = rows
        t = self.t
        self.ctl.set_title(2, tr(SO.GROUP_LABEL.get(self.view, "商品")))
        ch = self.ctl._chooser
        if ch is not None:
            v = self.view
            hide = set()
            if v != "item":
                hide |= {0, 1, 18, 23}
                if v in ("sup", "cat", "store"):
                    hide |= {19, 20, 21}
                elif v == "mid":
                    hide |= {20, 21}
                else:
                    hide |= {21}
            else:
                hide |= {22}
            ch.forced = lambda h=hide: h
            ch.apply()
        if self.ctl.sort_col is None:
            self.ctl.sort_col, self.ctl.sort_desc = 9, True             # 默认按销售额从高到低
        self.ctl.render()
        self._update_sum()
        QTimer.singleShot(0, self._sync_sum)

    def _search_for(self, text):
        self.set_bucket(None)
        if self.view != "item":
            self.set_view("item")
        self.search.setText(text)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _hidden(self, i):
        r, disp, _ = self.rows[i]
        if self.bucket and self.view == "item" and r.get("bucket") != self.bucket:
            return True
        q = self.search.text().strip().lower()
        if q:
            hay = f"{disp[0]} {disp[1]} {disp[2]} {disp[18]} {disp[19]} {disp[20]} {disp[21]}".lower()
            return q not in hay
        return False

    def _fill(self, i, k):
        r, disp, keys = self.rows[i]
        self.ui.table_set_row(self.t, k, disp)
        err, faint = QColor(T("err")), QColor(T("faint"))
        for c in range(3, 18):
            v = keys[c]
            if v is not None and v != -1e18 and v < 0:
                it = self.t.item(k, c)
                if it is not None:
                    it.setForeground(err)
        it = self.t.item(k, 2)
        if self.view == "item":
            it.setToolTip(f"{disp[2]}\n{disp[0]} · {disp[1]}\n{disp[18]}\n{disp[19]} / {disp[20]} / {disp[21]}")
            import sellout as SO
            tone = dict((kk, t_) for kk, _n, t_ in SO.BUCKETS).get(r.get("bucket"), "muted")
            b = self.t.item(k, 23)
            if b is not None:
                b.setForeground(QColor(T(tone)) if tone != "muted" else faint)
            if r["pq"] <= 0 and r["sq"] <= 0:
                for c in range(4, 18):
                    x = self.t.item(k, c)
                    if x is not None:
                        x.setForeground(faint)
        else:
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            it.setToolTip(tr("{0}：{1} 个商品", disp[2], r.get("n", 0)))

    def _rendered(self, n, tot):
        self.count.setText(tr("{0:,} / {1:,}", n, tot))
        self._update_sum()

    def _visible_data(self):
        return [self.rows[i][0] for i in self.ctl.visible]

    def _update_sum(self):
        import sellout as SO
        if self.res is None:
            return
        vis = self._visible_data()
        if self.view == "store":
            t = SO.total(vis)
        elif self.view == "item":
            t = SO.total(vis)
        else:
            t = SO.total(vis)
        vals = ["", "", tr("合计 · {0:,} 行", len(vis)), _qty(t["stock"]), _qty(t["pq"]), _eur(t["p_avg"]), _eur(t["pv"]),
                _qty(t["sq"]), _eur(t["s_avg"]), _eur(t["sv"]), _pct(t["rate_q"]), _pct(t["rate_v"]), _pct(t["diff_rate"]),
                _eur(t["diff"]), _pct(t["margin"]), _eur(t["profit"]), _qty(t["rem_q"]), _eur(t["rem_v"]),
                "", "", "", "", f"{sum(r.get('n', 1) for r in vis):,}", ""]
        nums = [None, None, None, t["stock"], t["pq"], t["p_avg"], t["pv"], t["sq"], t["s_avg"], t["sv"], t["rate_q"],
                t["rate_v"], t["diff_rate"], t["diff"], t["margin"], t["profit"], t["rem_q"], t["rem_v"]]
        f = _num_font(13, True)
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            if c in NUMERIC or c == 2:
                it.setFont(f if c != 2 else QFont())
            if c in NUMERIC:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if c < len(nums) and nums[c] is not None and nums[c] < 0:
                it.setForeground(QColor(T("err")))
            elif c == 2:
                fb = it.font()
                fb.setBold(True)
                it.setFont(fb)
            self.t_sum.setItem(0, c, it)
        self._sync_sum()

    def _sync_sum(self):
        t, s = self.t, self.t_sum
        hh, sh = t.horizontalHeader(), s.horizontalHeader()
        for lg in range(t.columnCount()):
            vis = hh.visualIndex(lg)
            if sh.visualIndex(lg) != vis:
                sh.moveSection(sh.visualIndex(lg), vis)
            s.setColumnHidden(lg, t.isColumnHidden(lg))
            if s.columnWidth(lg) != t.columnWidth(lg):
                s.setColumnWidth(lg, t.columnWidth(lg))
        vb = t.verticalScrollBar()
        self.sum_pad.setFixedWidth(vb.width() if vb.isVisible() else 0)
        s.horizontalScrollBar().setRange(t.horizontalScrollBar().minimum(), t.horizontalScrollBar().maximum())
        s.horizontalScrollBar().setValue(t.horizontalScrollBar().value())

    def _double(self, k, _c):
        if self.view != "item":
            i = self.ctl.row_at(k)
            if i is None:
                return
            r = self.rows[i][0]
            if self.view == "store":
                return
            # 合计行双击 → 回到商品，只看这一组
            self.search.setText(str(r["name"]) if self.view != "sup" else str(r["name"]))
            self.set_view("item")
            return
        i = self.ctl.row_at(k)
        if i is None:
            return
        r = self.rows[i][0]
        from product_window import ProductWindow
        if getattr(self, "_card", None) is None:
            self._card = ProductWindow(self.win, self.ui, self.settings)
        sc = self.res["scope"]
        st = sc[0] if len(sc) == 1 else None
        self._card.show_item({"code": r["code"], "ean": r["ean"], "name": r["name"], "sup_name": r["sup_name"],
                              "store": st})

    # ---------- 导出 ----------
    def export(self):
        if self.res is None:
            return
        import os
        import sellout as SO
        self.ctl.flush()
        rows = self._visible_data()
        if not rows:
            return
        res = self.res
        name = "SELLOUT_" + (os.path.splitext(self.codes_name)[0] if self.codes_name else SO.scope_label(res["scope"]))
        name = "".join(ch for ch in name if ch not in '\\/:*?"<>|').replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"),
                                              os.path.join(self.cfg.get("exp_dir") or "",
                                                           f"{name}_{res['start']:%d%m}-{res['end']:%d%m}.xlsx"),
                                              tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        self.cfg["exp_dir"] = os.path.dirname(path)
        from erp_widgets import BusyDialog, open_file
        url = self.settings.get("image_url") or None
        title = f"SELL OUT · {SO.scope_label(res['scope'])} · {res['start']:%d/%m/%Y} – {res['end']:%d/%m/%Y}"
        view = self.view
        _, err = BusyDialog.run(self.win, tr("导出 Excel"), tr("正在写…"),
                                lambda: SO.export(res, rows, path, title=title, url=url, by=view))
        if err:
            QMessageBox.warning(self.win, tr("导出 Excel"), tr("导出失败：{0}", err))
            return
        open_file(self.win, path, tr("导出 Excel"))

    # ---------- 深浅色 ----------
    def restyle(self):
        if self.page is None:
            return
        from theme import NUM_FONT
        self.h1.setStyleSheet(f"font-size:26px; font-weight:700; color:{T('text')};")
        self.status.setStyleSheet(f"color:{T('head')};")
        self._sep.setStyleSheet(f"background:{T('line')};")
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.body.setStyleSheet(
            f"#SoBody {{ background: transparent; }}"
            f"#RpCard {{ background:{T('surface')}; border:1px solid {T('line')}; border-radius:14px; }}"
            f"#RpTitle {{ font-size:15px; font-weight:600; color:{T('text')}; }}"
            f"#RpKpiCap {{ font-size:12px; color:{T('muted')}; }}"
            f"#RpKpiVal {{ font-family:\"{NUM_FONT}\"; font-size:24px; font-weight:600; color:{T('text')}; }}"
            f"#RpMuted {{ color:{T('muted')}; font-size:12px; }}"
            f"QTableWidget#SoSum {{ background:{T('surface2')}; border:none; border-top:1px solid {T('border')}; "
            f"border-bottom-left-radius:8px; border-bottom-right-radius:8px; }}"
            f"QTableWidget#SoSum::item {{ border:none; color:{T('text')}; padding:0 8px; }}")
        pick = (f"QPushButton#SoPick {{ text-align:left; padding:0 12px; }}"
                f"QPushButton#SoPick[on=\"true\"] {{ border-color:{T('accent')}; background:{T('accent_soft')}; "
                f"color:{T('text')}; font-weight:600; }}")
        for p in (self.p_sup, self.p_cat, self.p_mid, self.p_sub):
            p.setStyleSheet(pick)
        for w in self.body.findChildren(QWidget):
            w.update()
        if self.res is not None:
            self.update_view()


_BK_ORDER = {"out": 0, "good": 1, "mid": 2, "slow": 3, "none": 4, "nobuy": 5, "stock": 6}
