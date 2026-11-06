"""
批发商比价 → Griglia（2026-10-28）：我们的 DETERSIVI 总表和这周批发商报价对比（数据见 griglia.py）。
  上面：Griglia 文件 · 换 Griglia / 打开文件夹 / 刷新 / 导出 Excel
  总览（GridPanel，一个外框）：还在做的占几成 · 要看的四样（点了跳到表）· 每家批发商几个最便宜（点了价格表只看这家）
  标签页：都不做了 / 新品（至少几家在做）/ 价格 / 价格可疑，每张表都能点表头排序、右键表头筛选、搜索
"""
import os
import shutil
import threading

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
                               QTabWidget, QFileDialog, QStackedWidget, QFrame)

from i18n import tr


class _Sig(QObject):
    done = Signal(object)


class _Tab:
    """一张表：rows = [[显示文字…]]，keys = [[排序值…]]（数字列按数字排），colors = {行: [(列, 颜色记号)]}"""

    def __init__(self, page, heads, numeric=(), extra=None):
        ui = page.ui
        self.page, self.rows, self.keys, self.colors, self.hide_fn = page, [], [], {}, None
        self.w = QWidget()
        v = QVBoxLayout(self.w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(8)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("条码或名称"))
        self.search.setFixedWidth(240)
        bar.addWidget(self.search)
        if extra is not None:
            bar.addWidget(extra)
        bar.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        v.addLayout(bar)
        self.t = ui.make_table(heads, stretch_col=None, numeric_cols=numeric)
        v.addWidget(self.t, 1)
        from erp_widgets import TableSortFilter
        # lazy：新品「全部」一万多行，打字搜索时以前每个字都要把整张表重画（2 秒）→ 先画看得见的（2026-11-04）
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), lambda i, c: self.rows[i][c], self._fill,
                                   key=lambda i, c: self.keys[i][c], hide=self._hidden, numeric=numeric, lazy=True)
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        self.search.textChanged.connect(lambda _: self.ctl.render())

    def _hidden(self, i):
        q = self.search.text().strip().lower()
        if q and q not in (self.rows[i][0] + " " + self.rows[i][1] + " " + self.rows[i][2]).lower():
            return True
        return bool(self.hide_fn and self.hide_fn(i))

    def _fill(self, i, k):
        T = self.page.ui.T
        self.page.ui.table_set_row(self.t, k, self.rows[i])
        it = self.t.item(k, 1)                    # 第 1 列一定是商品名（第 0 列条码）：鼠标停上去看全名和条码
        if it is not None:
            it.setToolTip(f"{self.rows[i][1]}\n{self.rows[i][0]}")
        for c, tok in self.colors.get(i, ()):
            it = self.t.item(k, c)
            if it is not None:
                it.setForeground(QColor(T(tok)))
                if tok == "accent":
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)

    def set(self, rows, keys, colors=None):
        self.rows, self.keys, self.colors = rows, keys, colors or {}
        self.ctl.render()


class _StatList(QWidget):
    """
    几行「要看的」：彩色圆点 + 名字 + 右边的个数 + ›，一行一个标签页，点一行跳过去（鼠标停着整行淡底）。
    items = [(名字, 个数文字, 颜色记号, 标签页, 鼠标提示)]
    """
    clicked = Signal(int)
    ROW = 30

    def __init__(self, ui):
        super().__init__()
        self.ui, self.items, self.hover = ui, [], -1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set(self, items):
        self.items = list(items)
        self.setFixedHeight(self.ROW * len(self.items))
        self.update()

    def _row(self, e):
        y = e.position().y() if hasattr(e, "position") else e.y()
        i = int(y // self.ROW)
        return i if 0 <= i < len(self.items) else -1

    def mouseMoveEvent(self, e):
        i = self._row(e)
        if i != self.hover:
            self.hover = i
            self.setToolTip(self.items[i][4] if i >= 0 else "")
            self.update()

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._row(e)
        if i >= 0:
            self.clicked.emit(self.items[i][3])

    def paintEvent(self, _e):
        from PySide6.QtGui import QPainter, QFont
        from PySide6.QtCore import QRectF
        from theme import NUM_FONT, UI_FONT
        T = self.ui.T
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W = self.width()
        f_lbl, f_num = QFont(UI_FONT), QFont(NUM_FONT)
        f_lbl.setPixelSize(13)
        f_num.setPixelSize(15)
        f_num.setBold(True)
        for i, (label, val, tone, _tab, _tip) in enumerate(self.items):
            y = i * self.ROW
            if i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("surface2")))
                p.drawRoundedRect(QRectF(0, y + 2, W, self.ROW - 4), 8, 8)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T(tone)))
            p.drawEllipse(QRectF(10, y + self.ROW / 2 - 4, 8, 8))
            p.setFont(f_lbl)
            p.setPen(QColor(T("text")))
            p.drawText(QRectF(28, y, W - 100, self.ROW), Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setPen(QColor(T("muted")))
            p.drawText(QRectF(W - 22, y, 16, self.ROW), Qt.AlignCenter, "›")
            p.setFont(f_num)
            p.setPen(QColor(T(tone) if val not in ("0", "—") else T("faint")))
            p.drawText(QRectF(W - 110, y, 84, self.ROW), Qt.AlignRight | Qt.AlignVCenter, val)
        p.end()


class _SupChart(QWidget):
    """
    这周每家批发商一栏：名字、「n 个最便宜」（大字）、有几个 Griglia 商品 · 占几成，下面一根竖条（高 = 最便宜的个数），
    最便宜最多的那家蓝条；点一栏 = 价格表只看这家最便宜的（再点一次取消），选中的那栏淡蓝底
    """
    clicked = Signal(str)
    BAR_H = 40

    def __init__(self, ui):
        super().__init__()
        self.ui, self.sups, self.sel, self.hover = ui, [], None, -1
        self.setMouseTracking(True)
        self.setMinimumHeight(128)
        self.setCursor(Qt.PointingHandCursor)

    def set(self, sups, sel=None):
        self.sups, self.sel = list(sups), sel
        self.update()

    def _col(self, e):
        x = e.position().x() if hasattr(e, "position") else e.x()
        n = len(self.sups)
        i = int(x * n / max(1, self.width())) if n else -1
        return i if 0 <= i < n else -1

    def mouseMoveEvent(self, e):
        i = self._col(e)
        if i != self.hover:
            self.hover = i
            self.update()
            if i >= 0:
                d = self.sups[i]
                self.setToolTip(tr("{0}：这周报价里有 {1:,} 个 Griglia 商品（{2:.0%}），其中 {3:,} 个它最便宜\n点一下：价格表只看这家最便宜的",
                                   d["label"], d["carried"], d["share"], d["cheapest"]))

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._col(e)
        if i >= 0:
            self.clicked.emit(self.sups[i]["label"])

    def paintEvent(self, _e):
        from PySide6.QtGui import QPainter, QFont, QFontMetrics
        from PySide6.QtCore import QRectF
        from theme import NUM_FONT, UI_FONT
        T = self.ui.T
        n = len(self.sups)
        if not n:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        cw = W / n
        top = max(d["cheapest"] for d in self.sups) or 1
        base = H - 6
        f_title, f_small, f_num = QFont(UI_FONT), QFont(UI_FONT), QFont(NUM_FONT)
        f_title.setPixelSize(12)
        f_title.setBold(True)
        f_small.setPixelSize(11)
        f_num.setPixelSize(16)
        f_num.setBold(True)
        for i, d in enumerate(self.sups):
            box = QRectF(i * cw + 4, 0, cw - 8, H)
            sel = d["label"] == self.sel
            lead = d["cheapest"] == top and d["cheapest"] > 0
            if sel or i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("accent_soft") if sel else T("surface2")))
                p.drawRoundedRect(box, 10, 10)
            pad = 12
            tx, tw = box.left() + pad, box.width() - 2 * pad
            p.setFont(f_title)
            p.setPen(QColor(T("accent") if (sel or lead) else T("muted")))
            p.drawText(QRectF(tx, 10, tw, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(f_title).elidedText(d["label"], Qt.ElideRight, int(tw)))
            p.setFont(f_num)
            p.setPen(QColor(T("text") if d["cheapest"] else T("faint")))
            num = f"{d['cheapest']:,}"
            p.drawText(QRectF(tx, 30, tw, 22), Qt.AlignLeft | Qt.AlignVCenter, num)
            nw = QFontMetrics(f_num).horizontalAdvance(num)
            p.setFont(f_small)
            p.setPen(QColor(T("muted")))
            p.drawText(QRectF(tx + nw + 5, 30, max(0, tw - nw - 5), 22), Qt.AlignLeft | Qt.AlignVCenter, tr("个最便宜"))
            p.setPen(QColor(T("faint")))
            p.drawText(QRectF(tx, 52, tw, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(f_small).elidedText(tr("有 {0:,} 个 · {1:.0%}", d["carried"], d["share"]),
                                                        Qt.ElideRight, int(tw)))
            h = max(4.0, self.BAR_H * d["cheapest"] / top)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T("accent") if (sel or lead) else T("ghost") if d["cheapest"] else T("track")))
            p.drawRoundedRect(QRectF(tx, base - h, tw, h), 4, 4)
        p.end()


class GridPanel(QFrame):
    """
    Griglia 页顶上的总览（2026-11-02 用户：Griglia 统计也做得好看点；和订单跟踪的「本月」一个样子，整块一个外框）：
      左：一圈 = 这周还有批发商在做的 Griglia 商品占几成 + Griglia 商品数（大字）、几个有批发商在做、几家报价（文件名在页面标题右边）
      中：要看的几样（都不做了 / 新上架 / 最低价换了供应商 / 价格可疑），点一行跳到那张表
      右：每家批发商一栏：几个 Griglia 商品它最便宜、它有几个，点一栏 = 价格表只看这家最便宜的
    """
    tab_clicked = Signal(int)
    sup_clicked = Signal(str)

    def __init__(self, ui):
        super().__init__()
        from erp_widgets import Ring
        self.ui = ui
        self.setObjectName("Panel")
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 12, 14, 10)
        h.setSpacing(14)
        lw = QWidget()
        lw.setFixedWidth(290)
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        self.cap = QLabel(tr("这周批发商还在做"))
        self.cap.setObjectName("GpCap")
        lv.addWidget(self.cap)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.ring = Ring()
        self.ring.setToolTip(tr("Griglia 商品里，这周至少一家批发商报价里有的占几成"))
        row.addWidget(self.ring, 0, Qt.AlignVCenter)
        tv = QVBoxLayout()
        tv.setSpacing(1)
        self.big = QLabel("")
        self.big.setObjectName("GpBig")
        self.sub = QLabel("")
        self.sub.setObjectName("GpSub")
        self.sub2 = QLabel("")
        self.sub2.setObjectName("GpSub")
        tv.addStretch()
        for x in (self.big, self.sub, self.sub2):
            tv.addWidget(x)
        tv.addStretch()
        row.addLayout(tv, 1)
        lv.addLayout(row)
        lv.addStretch()
        h.addWidget(lw)
        self.seps = [QFrame(), QFrame()]
        h.addWidget(self.seps[0])
        mw = QWidget()
        mw.setFixedWidth(250)
        mv = QVBoxLayout(mw)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(4)
        self.cap2 = QLabel(tr("要看的"))
        self.cap2.setObjectName("GpCap")
        mv.addWidget(self.cap2)
        self.stats = _StatList(ui)
        self.stats.clicked.connect(self.tab_clicked.emit)
        mv.addWidget(self.stats)
        mv.addStretch()
        h.addWidget(mw)
        h.addWidget(self.seps[1])
        self.chart = _SupChart(ui)
        self.chart.clicked.connect(self.sup_clicked.emit)
        h.addWidget(self.chart, 1)
        for sp in self.seps:
            sp.setFixedWidth(1)
        self.restyle()

    def show_stats(self, ov, n_sup, stats, sel=None):
        """ov = griglia.overview(…)；stats = _StatList 的几行"""
        s = ov["share"]
        self.ring.set(s, "ok" if s is None or s >= 0.95 else "warn" if s >= 0.85 else "err", color_text=False)
        self.big.setText(tr("{0:,} 个商品", ov["grid"]))
        self.sub.setText(tr("{0:,} 个有批发商在做", ov["covered"]))
        self.sub2.setText(tr("{0} 家批发商报价", n_sup))
        self.stats.set(stats)
        self.chart.set(ov["sups"], sel)

    def set_sel(self, sel):
        self.chart.sel = sel
        self.chart.update()

    def restyle(self):
        from theme import NUM_FONT
        T = self.ui.T
        self.setStyleSheet(
            f"#GpCap {{ color: {T('muted')}; font-size: 12px; font-weight: 600; }}"
            f"#GpBig {{ font-family: \"{NUM_FONT}\"; font-size: 22px; font-weight: 600; color: {T('text')}; }}"
            f"#GpSub {{ color: {T('muted')}; font-size: 12px; }}")
        for sp in self.seps:
            sp.setStyleSheet(f"background: {T('line')};")
        for w in (self.ring, self.stats, self.chart):
            w.update()



# 每列默认宽度（按列编号；批发商价格列没写 = 84）。用户拖过的宽度记在设置里，优先用
_WIDTHS = {1: 130, 2: 330, 3: 80, 4: 120, 5: 170, 6: 170, 7: 130, 8: 210, 9: 170, 10: 84, 11: 150, 12: 72,
           13: 84, 14: 120, 15: 76, 16: 130, 17: 84, 18: 96}
# 默认藏起来的列（右键表头「显示哪些列…」可以打开）：编号、品牌、部门
# （条码、小类以前也藏着；2026-11-02 用户：为什么没有条码列 / 要三个类 → 默认显示，见 _show_once）
_HIDDEN = (3, 4, 11)
_UNHIDE = (1, 7)          # 以前默认藏、现在默认显示的列：条码、小类


def _show_once(cfg, key, fields=_UNHIDE):
    """
    以前存过的「藏哪些列」里有 fields 这几列的，拿掉一次（每列只做一次；之后用户自己再藏起来就照用户的）。
    不改列顺序 / 列宽。返回这次拿掉了哪些
    """
    h = cfg.get(key + "_cols_hidden")
    done = []
    for f in fields:
        flag = f"{key}_shown_{f}"
        if cfg.get(flag) or (f == 1 and cfg.get(key + "_ean_shown")):      # _ean_shown：上一版只放出条码时的记号
            continue
        cfg[flag] = True
        if isinstance(h, list) and f in h:
            h = [x for x in h if x != f]
            done.append(f)
    if done:
        cfg[key + "_cols_hidden"] = h
    return done


def _p(v):
    return f"{v:.3f}".rstrip("0").rstrip(".") if isinstance(v, (int, float)) else ""


class GrigliaPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.sig = _Sig()
        self.sig.done.connect(self._done)
        self.st = None
        self.grid = None
        self.running = False
        self._loaded_once = False
        self._labels, self._price_fids = [], []
        self.panel = None

    # ---------- 界面 ----------
    def build(self):
        ui = self.ui
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(14)
        t = QLabel(tr("Griglia") + "  ⓘ")
        t.setObjectName("PageTitle")
        t.setToolTip(tr("我们的 DETERSIVI 总表（工作文件夹「0 Griglia」里最新的一份）和这周批发商报价对比：哪些商品几家都不做了、哪些新品不在 Griglia 里（几家在做、各家价格）、Griglia 里的商品现在哪家最便宜。新品只算报价里「我们这几类」的部门（Griglia 商品所在的部门）"))
        head = QHBoxLayout()
        head.addWidget(t)
        head.addSpacing(12)
        self.file_lbl = QLabel("")
        self.file_lbl.setObjectName("Faint")
        head.addWidget(self.file_lbl, 1)
        for text, fn, tip in ((tr("换 Griglia…"), self.choose, tr("选一份新的 Griglia，复制到工作文件夹「0 Griglia」（旧的挪进 _旧文件）")),
                              (tr("打开文件夹"), self.open_dir, ""), (tr("刷新"), self.refresh, tr("重新读 Griglia 和「2 供应商报价」")),
                              (tr("导出 Excel"), self.export, "")):
            b = QPushButton(text)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(fn)
            head.addWidget(b)
        lay.addLayout(head)

        self.stack = QStackedWidget()
        from erp_widgets import EmptyState
        self.empty = EmptyState("search", tr("还没有 Griglia"),
                                tr("把我们的 DETERSIVI 总表（ELENCO_DETERSIVI.xlsx）放进工作文件夹「0 Griglia」，或点「换 Griglia…」"))
        b = QPushButton(tr("选择 Griglia…"))
        b.setObjectName("Primary")
        b.clicked.connect(self.choose)
        self.empty.buttons.addWidget(b)
        self.stack.addWidget(self.empty)

        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(12)
        self.panel = GridPanel(ui)
        self.panel.tab_clicked.connect(lambda k: self.tabs.setCurrentIndex(k))
        self.panel.sup_clicked.connect(self._sup_clicked)
        bv.addWidget(self.panel)
        self.tabs = QTabWidget()
        self.min_n = QComboBox()
        for n in (1, 2, 3, 4):
            self.min_n.addItem(tr("≥ {0} 家在做", n), n)
        self.min_n.setCurrentIndex(1)
        self.min_n.setToolTip(tr("只看至少几家批发商在做的新品（几家都在做的多半是常见商品）"))
        self.scope = QComboBox()
        for text, key in ((tr("新上架"), "fresh"), (tr("全部"), "all")):
            self.scope.addItem(text, key)
        self.scope.setToolTip(tr("新上架 = 批发商报价里第一次出现的（和以前读过的报价比，30 天内）；全部 = 这几家报价里我们这几类、Griglia 里没有的"))
        self.new_bar = QWidget()
        nb = QHBoxLayout(self.new_bar)
        nb.setContentsMargins(0, 0, 0, 0)
        nb.addWidget(self.scope)
        nb.addWidget(self.min_n)
        # 四张表的列跟着这周的批发商变（每家一列价格），统计完再建；列可以拖动换位置，记在设置里
        self.t_gone = self.t_new = self.t_price = self.t_odd = None
        self.boxes = []
        for _ in range(4):
            bx = QWidget()
            QVBoxLayout(bx).setContentsMargins(0, 0, 0, 0)
            self.boxes.append(bx)
        for bx, text in zip(self.boxes, (tr("都不做了"), tr("新品"), tr("价格"), tr("价格可疑"))):
            self.tabs.addTab(bx, text)
        self.tabs.setTabToolTip(3, tr("报价表里明显写错的价格（低于 €0.05，或比别家中间价低 70% 以上，多半是箱价 / 件价搞混）：不拿来比最低价，请找供应商确认"))
        bv.addWidget(self.tabs, 1)
        self.stack.addWidget(body)
        self.busy = QLabel(tr("正在统计…"))
        self.busy.setAlignment(Qt.AlignCenter)
        self.busy.setObjectName("Faint")
        self.stack.addWidget(self.busy)
        lay.addWidget(self.stack, 1)
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self._first_show())
        self.page = w
        return w

    def _first_show(self):
        if not self._loaded_once:
            self._loaded_once = True
            self.refresh()

    # ---------- 文件 ----------
    def _root(self):
        try:
            return self.win.ws_root()
        except Exception:
            return ""

    def open_dir(self):
        import griglia as G
        from main import open_path
        r = self._root()
        if r:
            os.makedirs(G.grid_dir(r), exist_ok=True)
            open_path(G.grid_dir(r))

    def choose(self):
        import griglia as G
        r = self._root()
        if not r:
            return
        p, _ = QFileDialog.getOpenFileName(self.win, tr("选择 Griglia"), "", tr("Excel 文件 (*.xlsx *.xlsm)"))
        if not p:
            return
        d = G.grid_dir(r)
        os.makedirs(d, exist_ok=True)
        if os.path.normcase(os.path.dirname(os.path.abspath(p))) != os.path.normcase(os.path.abspath(d)):
            old = os.path.join(d, "_旧文件")
            for f in os.listdir(d):
                fp = os.path.join(d, f)
                if os.path.isfile(fp) and f.lower().endswith(G.EXCEL):
                    os.makedirs(old, exist_ok=True)
                    try:
                        shutil.move(fp, os.path.join(old, f))
                    except OSError:
                        pass
            shutil.copy2(p, os.path.join(d, os.path.basename(p)))
            os.utime(os.path.join(d, os.path.basename(p)))      # 最新的一份
        self.refresh()

    # ---------- 统计 ----------
    def refresh(self):
        if self.running:
            return
        r = self._root()
        import griglia as G
        if not r or not G.find_file(r):
            self.file_lbl.setText("")
            self.stack.setCurrentIndex(0)
            return
        self.running = True
        self.stack.setCurrentIndex(2)
        def work():
            try:
                import workspace as W
                grid = G.load_for(r)
                quotes = self.win.order_check._read_quotes(W.price_dir(r), lambda *_: None) or {}
                from datetime import date
                from settings import app_dir
                today = date.today().isoformat()
                sp = os.path.join(app_dir(), "cache", "griglia", "seen.json")
                seen = G.load_seen(sp)
                hist = any(seen.get(k, {}).get("since", today) < today for k in quotes)
                seen, fresh = G.track(seen, quotes, today)
                G.save_seen(sp, seen)
                self.sig.done.emit((grid, G.stats(grid, quotes, fresh if hist else None), None))
            except Exception as e:
                self.sig.done.emit((None, None, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, payload):
        grid, st, err = payload
        self.running = False
        if err:
            self.stack.setCurrentIndex(0)
            self.win.show_toast(tr("Griglia 统计失败：{0}", err))
            return
        self.grid, self.st = grid, st
        labels = st["labels"]
        self._labels = labels
        self.tabs.setTabText(0, tr("都不做了（{0}）", len(st["gone"])))
        self.tabs.setTabText(1, tr("新品"))
        self.tabs.setTabText(2, tr("价格（{0:,}）", len(st["prices"])))
        self.tabs.setTabText(3, tr("价格可疑（{0}）", len(st["odd"])))
        self._build_tables(labels)
        self._show_panel()
        self.stack.setCurrentIndex(1)

    def _show_panel(self):
        import griglia as G
        from datetime import datetime
        st, grid = self.st, self.grid
        nf = sum(1 for x in st["new"] if x["fresh"])
        moved = sum(1 for x in st["prices"] if x["moved"])
        stats = [(tr("都不做了"), f"{len(st['gone']):,}", "err", 0, tr("Griglia 里有、这周所有批发商报价里都没有")),
                 (tr("新上架"), f"{nf:,}" if st["history"] else "—", "accent", 1,
                  tr("批发商报价里第一次出现、Griglia 里没有的商品（和以前读过的报价比，30 天内）。第一次统计只是记下现在有哪些，下次换了新报价才有")),
                 (tr("最低价换了供应商"), f"{moved:,}", "warn", 2, tr("现在最便宜的批发商不是 Griglia 里登记的那家")),
                 (tr("价格可疑"), f"{len(st['odd']):,}", "warn", 3,
                  tr("报价表里明显写错的价格（低于 €0.05，或比别家中间价低 70% 以上，多半是箱价 / 件价搞混）：不拿来比最低价，请找供应商确认"))]
        self.file_lbl.setText(f"{grid['name']} · {datetime.fromtimestamp(grid.get('mtime') or 0):%d/%m/%Y}")
        self.panel.show_stats(G.overview(st, len(grid["items"])), len(self._labels), stats, self._best_sel())

    def _best_col(self):
        """价格表里「最便宜」是第几列（列编号 14）"""
        return self._price_fids.index(14) if self.t_price is not None and 14 in self._price_fids else None

    def _best_sel(self):
        c = self._best_col()
        a = self.t_price.ctl.filters.get(c) if c is not None else None
        return next(iter(a)) if a and len(a) == 1 else None

    def _sup_clicked(self, label):
        """点总览里的一家批发商：价格表只看它最便宜的（再点一次取消）"""
        c = self._best_col()
        if c is None:
            return
        self.t_price.ctl.set_filter(c, None if self._best_sel() == label else {label})
        self.tabs.setCurrentIndex(2)

    # ---------- 表格 ----------
    def _tab(self, idx, key, cols, data, extra=None):
        """
        建一张表放进第 idx 个标签页。cols = [(列编号, 表头, 类型, 取值, 颜色)]：
        类型 "txt" 文字 / "num" 整数 / "price" 价格 / "pct" 带正负号的百分数；颜色(x) 返回颜色记号或 None。列编号固定，用来记住拖过的列顺序
        """
        import settings as ST
        import product_images as PI
        from PySide6.QtWidgets import QHeaderView
        numeric = tuple(c for c, col in enumerate(cols) if col[2] != "txt")
        tab = _Tab(self, [col[1] for col in cols], numeric=numeric, extra=extra)
        t = tab.t
        # 排版（用户：别挤成一团）：每列固定一个合适的默认宽度、可以拖；名称列左边画商品图（条码取第 0 列），行高跟着图
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        fids = [col[0] for col in cols]
        thumb = PI.thumb_size(self.settings) if self.settings.get("show_images", True) else 0
        for c, f in enumerate(fids):
            t.setColumnWidth(c, (_WIDTHS.get(f, 84) + (thumb + 10 if f == 2 else 0)))
        if 2 in fids and 1 in fids:
            t.setItemDelegateForColumn(fids.index(2), PI.ThumbDelegate(t, fids.index(1), None, self.settings))
            t.verticalHeader().setDefaultSectionSize(PI.row_height(self.settings))
        else:
            t.verticalHeader().setDefaultSectionSize(34)
        rows, keys, colors = [], [], {}
        for i, x in enumerate(data):
            r, k = [], []
            for c, (_fid, _h, kind, get, color) in enumerate(cols):
                v = get(x)
                if kind == "txt":
                    r.append(str(v or ""))
                    k.append(str(v or "").lower())
                else:
                    r.append("" if v is None else _p(v) if kind == "price" else f"{v:+.1f}" if kind == "pct" else str(v))
                    k.append(v if isinstance(v, (int, float)) else 0)
                tok = color(x) if color else None
                if tok:
                    colors.setdefault(i, []).append((c, tok))
            rows.append(r)
            keys.append(k)
        cfg = self.settings.setdefault("griglia_cols", {})
        _show_once(cfg, key)
        tab.ctl.remember(cfg, key, lambda: ST.save_settings(self.settings), fids,
                         default_hidden=[f for f in _HIDDEN if f in fids], ver=2)
        box = self.boxes[idx]
        lay = box.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        lay.addWidget(tab.w)
        tab.rows, tab.keys, tab.colors = rows, keys, colors
        return tab

    def _build_tables(self, labels):
        import zlib
        st = self.st
        cat = lambda k: (lambda x: (x.get("cats") or ["", "", ""])[k])
        base = [(1, tr("条码"), "txt", lambda x: x["ean"], None),
                (2, tr("商品"), "txt", lambda x: x.get("name"), None)]
        cats = [(5, tr("大类"), "txt", cat(0), None), (6, tr("中类"), "txt", cat(1), None),
                (7, tr("小类"), "txt", cat(2), None),
                (8, "FORNITORI DIRETTO", "txt", lambda x: x.get("direct"), None)]

        def sup_cols():
            return [(1000 + zlib.crc32(l.encode("utf-8")) % 1000000, l, "price",
                     (lambda x, l=l: x["prices"].get(l)),
                     (lambda x, l=l: "accent" if x.get("best_sup") == l else None)) for l in labels]
        # 都不做了
        self.t_gone = self._tab(0, "gone", base + [(3, tr("编号"), "txt", lambda x: x.get("code"), None),
                                                   (4, tr("品牌"), "txt", lambda x: x.get("brand"), None)] + cats +
                                [(9, tr("Griglia 供应商"), "txt", lambda x: x.get("supplier"), None),
                                 (10, tr("Griglia 价"), "price", lambda x: x.get("best") or x.get("price"), None)],
                                st["gone"])
        # 新品
        if self.t_new is not None:
            self.new_bar.setParent(None)
        self.t_new = self._tab(1, "new", base + cats +
                               [(11, tr("部门"), "txt", lambda x: x.get("dept"), None),
                                (12, tr("几家在做"), "num", lambda x: x["n"], None),
                                (13, tr("最低价"), "price", lambda x: x["best"], None),
                                (14, tr("最便宜"), "txt", lambda x: x["best_sup"], None)] + sup_cols(),
                               st["new"], extra=self.new_bar)
        for cb in (self.min_n, self.scope):
            try:
                cb.currentIndexChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            cb.currentIndexChanged.connect(lambda _: self.t_new.ctl.render())
        # 默认：有以前的报价记录 → 新上架（几家都行）；第一次（还没有新上架）→ 全部、≥ 2 家在做
        self.scope.blockSignals(True)
        self.min_n.blockSignals(True)
        self.scope.setCurrentIndex(0 if st["history"] else 1)
        self.min_n.setCurrentIndex(0 if st["history"] else 1)
        self.scope.blockSignals(False)
        self.min_n.blockSignals(False)

        def hide(i):
            x = st["new"][i]
            sc = self.scope.currentData()
            return x["n"] < (self.min_n.currentData() or 1) or (sc == "fresh" and not x["fresh"])
        self.t_new.hide_fn = hide
        # 价格
        def ch_color(x):
            ch = x["change"]
            return ("err" if ch > 0 else "ok") if ch is not None and abs(ch) >= 0.05 else None
        pcols = base + [(3, tr("编号"), "txt", lambda x: x.get("code"), None)] + cats + \
            [(9, tr("Griglia 供应商"), "txt", lambda x: x.get("supplier"), None),
             (10, tr("Griglia 价"), "price", lambda x: x["ref"], None),
             (13, tr("现在最低"), "price", lambda x: x["best"], None),
             (14, tr("最便宜"), "txt", lambda x: x["best_sup"], lambda x: "warn" if x["moved"] else None),
             (15, tr("变化 %"), "pct", lambda x: round(x["change"] * 100, 1) if x["change"] is not None else None,
              ch_color)] + sup_cols()
        self.t_price = self._tab(2, "price", pcols, st["prices"])
        self._price_fids = [c[0] for c in pcols]
        self.t_price.ctl.rendered.connect(lambda *_: self.panel.set_sel(self._best_sel()))
        heads = [c[0] for c in pcols]
        self.t_price.t.horizontalHeaderItem(heads.index(15)).setToolTip(
            tr("现在最低价比 Griglia 记的价（PA MIGLIORE）高（红）或低（绿）多少"))
        self.t_price.t.horizontalHeaderItem(heads.index(14)).setToolTip(tr("黄色 = 现在最便宜的不是 Griglia 登记的供应商"))
        # 价格可疑
        self.t_odd = self._tab(3, "odd", base + [(16, tr("供应商"), "txt", lambda x: x["supplier"], None),
                                                 (17, tr("报价价"), "price", lambda x: x["price"], None),
                                                 (18, tr("别家中间价"), "price", lambda x: x["others"], None)], st["odd"])
        for t in (self.t_gone, self.t_new, self.t_price, self.t_odd):
            t.ctl.render()

    def restyle(self):
        """换深色 / 浅色：表格里的彩色字重画一遍"""
        if self.panel is not None:
            self.panel.restyle()
        for tab in (self.t_gone, self.t_new, self.t_price, self.t_odd):
            if tab is not None and tab.rows:
                tab.ctl.render()

    def export(self):
        if not self.st:
            return
        import griglia as G
        from datetime import datetime
        p, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"), os.path.join(
            self._root() or "", f"Griglia_统计_{datetime.now():%Y%m%d}.xlsx"), tr("Excel 文件 (*.xlsx)"))
        if not p:
            return
        try:
            G.export(self.st, p)
            self.win.show_toast(tr("已导出：{0}", os.path.basename(p)))
        except Exception as e:
            self.win.show_toast(tr("导出失败：{0}", e))
