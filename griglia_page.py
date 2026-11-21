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
                               QTabWidget, QFileDialog, QStackedWidget, QFrame, QDialog, QCheckBox)

from i18n import tr


class _Sig(QObject):
    done = Signal(object)
    log = Signal(str)


class _ExportDialog(QDialog):
    """导出哪几张表：四个勾选框（带行数，默认勾现在打开的那张）+「按表里现在的筛选」"""

    def __init__(self, win, page):
        super().__init__(win)
        import griglia as G
        from erp_widgets import tip_label
        self.page = page
        self.setObjectName("Dialog")
        self.setWindowTitle(tr("导出 Excel"))
        self.setMinimumWidth(300)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(tip_label(tr("导出哪几张表"), tr("每张表一个工作表：照片（Excel 365 打开才显示）、彩色表头、斑马纹、"
                                                  "表头自带筛选，最便宜的价格绿底、涨价红 / 降价绿、换了供应商橙色")))
        cur = page.tabs.currentIndex() if page.tabs is not None else 0
        self.boxes = []
        for i, (key, name) in enumerate(G.EXPORT_TABLES):
            cb = QCheckBox()
            cb.setChecked(i == cur)
            cb.setProperty("key", key)
            self.boxes.append(cb)
            v.addWidget(cb)
        v.addSpacing(4)
        self.filtered = QCheckBox(tr("按表里现在的筛选和排序"))
        self.filtered.setChecked(True)
        self.filtered.setToolTip(tr("勾上：只导出表里现在看得到的行（搜索、右键表头筛选、新品的「几家在做」都算），顺序也一样；"
                                    "不勾：导出整张表"))
        self.filtered.toggled.connect(self._label)
        v.addWidget(self.filtered)
        self._label()
        foot = QHBoxLayout()
        foot.addStretch()
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(tr("导出"))
        self.ok.setObjectName("Primary")
        self.ok.clicked.connect(self.accept)
        foot.addWidget(cancel)
        foot.addWidget(self.ok)
        v.addLayout(foot)
        for cb in self.boxes:
            cb.toggled.connect(lambda _: self.ok.setEnabled(bool(self.which())))
        self.ok.setEnabled(bool(self.which()))

    def _label(self, *_):
        import griglia as G
        for cb, (key, name) in zip(self.boxes, G.EXPORT_TABLES):
            total = len(self.page.st.get(key) or [])
            if self.filtered.isChecked():
                n = len(self.page._rows_of(key))
                cb.setText(tr("{0}（{1:,} 行）", tr(name), n) if n == total else
                           tr("{0}（{1:,} / {2:,} 行）", tr(name), n, total))
            else:
                cb.setText(tr("{0}（{1:,} 行）", tr(name), total))

    def which(self):
        return [cb.property("key") for cb in self.boxes if cb.isChecked()]


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


class _SupRank(QWidget):
    """
    这周每家批发商一行（和促销分析「门店排名」一个样子）：名字 · 横条（它最便宜的个数）· 「n 个最便宜」· 有几个 Griglia 商品 · 占几成。
    最便宜最多的那家蓝条；点一行 = 价格表只看这家最便宜的（再点一次取消），选中的那行淡蓝底 + 青色条
    """
    clicked = Signal(str)
    ROW = 36

    def __init__(self, ui):
        super().__init__()
        self.ui, self.sups, self.sel, self.hover = ui, [], None, -1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set(self, sups, sel=None):
        self.sups, self.sel = list(sups), sel
        self.setFixedHeight(max(1, len(self.sups)) * self.ROW)
        self.update()

    def _row(self, e):
        y = e.position().y() if hasattr(e, "position") else e.y()
        i = int(y // self.ROW)
        return i if 0 <= i < len(self.sups) else -1

    def mouseMoveEvent(self, e):
        i = self._row(e)
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
        i = self._row(e)
        if i >= 0:
            self.clicked.emit(self.sups[i]["label"])

    def paintEvent(self, _e):
        from PySide6.QtGui import QPainter, QFont, QFontMetrics
        from PySide6.QtCore import QRectF
        from theme import NUM_FONT, UI_FONT
        T = self.ui.T
        if not self.sups:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W = self.width()
        top = max(d["cheapest"] for d in self.sups) or 1
        f_name, f_num, f_small = QFont(UI_FONT), QFont(NUM_FONT), QFont(UI_FONT)
        f_name.setPixelSize(13)
        f_name.setWeight(QFont.Medium)
        f_num.setPixelSize(13)
        f_num.setWeight(QFont.DemiBold)
        f_small.setPixelSize(12)
        name_w, num_w, sub_w = 130, 130, 150
        bar_x = name_w + 10
        bar_w = max(40, W - bar_x - num_w - sub_w - 24)
        for i, d in enumerate(self.sups):
            y = i * self.ROW
            sel = d["label"] == self.sel
            lead = d["cheapest"] == top and d["cheapest"] > 0
            if sel or i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("accent_soft") if sel else T("hover")))
                p.drawRoundedRect(QRectF(0, y + 2, W, self.ROW - 4), 9, 9)
            p.setFont(f_name)
            p.setPen(QColor(T("text")))
            p.drawText(QRectF(12, y, name_w - 12, self.ROW), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(f_name).elidedText(d["label"], Qt.ElideRight, name_w - 12))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T("track")))
            p.drawRoundedRect(QRectF(bar_x, y + self.ROW / 2 - 5, bar_w, 10), 5, 5)
            w = bar_w * d["cheapest"] / top
            if w > 0:
                p.setBrush(QColor(T("cyan") if sel else T("accent") if lead else T("ghost")))
                p.drawRoundedRect(QRectF(bar_x, y + self.ROW / 2 - 5, max(10.0, w), 10), 5, 5)
            x = bar_x + bar_w + 12
            p.setFont(f_num)
            p.setPen(QColor(T("text") if d["cheapest"] else T("faint")))
            p.drawText(QRectF(x, y, num_w, self.ROW), Qt.AlignRight | Qt.AlignVCenter,
                       tr("{0:,} 个最便宜", d["cheapest"]))
            p.setFont(f_small)
            p.setPen(QColor(T("muted")))
            p.drawText(QRectF(x + num_w, y, sub_w, self.ROW), Qt.AlignRight | Qt.AlignVCenter,
                       tr("有 {0:,} · {1:.0%}", d["carried"], d["share"]))
        p.end()


class GridPanel(QWidget):
    """
    Griglia 页顶上的总览（2026-11-16 重做，照设计稿的样子）：
      一排指标卡：批发商还在做（占几成）· 停售 · 新上架 · 最低价换了供应商 · 价格可疑（点一张跳到那张表）
      卡片「这周各家批发商」：每家一行横条（几个 Griglia 商品它最便宜），点一行 = 价格表只看这家最便宜的
    接口和以前一样：show_stats / set_sel / restyle，tab_clicked(int) / sup_clicked(str)
    """
    tab_clicked = Signal(int)
    sup_clicked = Signal(str)

    def __init__(self, ui):
        super().__init__()
        from page_kit import Stat, Card
        self.ui = ui
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.k_cov = Stat(tr("批发商还在做"), "check", "ok", tr("Griglia 商品里，这周至少一家批发商报价里有的占几成"))
        self.kpis = [Stat(tr("停售"), "box", "err", "", clickable=True),
                     Stat(tr("新上架"), "spark", "accent", "", clickable=True),
                     Stat(tr("最低价换了供应商"), "refresh", "warn", "", clickable=True),
                     Stat(tr("价格可疑"), "search", "violet", "", clickable=True)]
        row.addWidget(self.k_cov)
        self._tabs_of = [0, 1, 2, 3]
        for i, k in enumerate(self.kpis):
            row.addWidget(k)
            k.clicked.connect(lambda i=i: self.tab_clicked.emit(self._tabs_of[i]))
        v.addLayout(row)
        self.card = Card(tr("这周各家批发商"), tr("每家批发商这周报价里，几个 Griglia 商品它最便宜（条越长越多）；点一行：价格表只看这家最便宜的"))
        self.chart = _SupRank(ui)
        self.chart.clicked.connect(self.sup_clicked.emit)
        self.card.v.addWidget(self.chart)
        v.addWidget(self.card)

    def show_stats(self, ov, n_sup, stats, sel=None):
        """ov = griglia.overview(…)；stats = [(名字, 个数文字, 颜色记号, 标签页, 鼠标提示)]（四张卡）"""
        s = ov["share"]
        tone = "ok" if s is None or s >= 0.95 else "warn" if s >= 0.85 else "err"
        self.k_cov.set_tone(tone)
        self.k_cov.set("—" if s is None else f"{s:.0%}", None,
                       sub=tr("{0:,} / {1:,} 个商品 · {2} 家报价", ov["covered"], ov["grid"], n_sup))
        for k, (label, val, tone, tab, tip) in zip(self.kpis, stats):
            k.cap.setText(label + "  ⓘ")
            k.cap.setToolTip(tip)
            k.setToolTip(tr("点一下：看「{0}」那张表", label))
            k.set(val, self.ui.T(tone) if val not in ("0", "—") else None, sub=tr("点开看明细"))
        self._tabs_of = [st[3] for st in stats]
        self.card.set_title(tr("这周各家批发商（{0}）", len(ov["sups"])))
        self.chart.set(ov["sups"], sel)

    def set_sel(self, sel):
        self.chart.sel = sel
        self.chart.update()

    def restyle(self):
        for k in [self.k_cov] + self.kpis:
            k.restyle()
        self.chart.update()


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
        self.sig.log.connect(lambda m: self.win.log(m) if hasattr(self.win, "log") else None)
        self.st = None
        self.grid = None
        self.running = False
        self._loaded_once = False
        self._labels, self._price_fids = [], []
        self.panel = None

    # ---------- 界面 ----------
    def build(self):
        """
        2026-11-16 重做（用户：Griglia 也重做，现在的不好看）：页头（文件名 · 日期 · 几个商品）+ 右边 找替代商品 · 导出 Excel · ⋯；
        一排指标卡 · 「这周各家批发商」排名 · 一张卡片放四张表（停售 / 新品 / 价格 / 价格可疑）；整页往下滚
        """
        ui = self.ui
        from page_kit import Hero, Card, button, more_menu
        from PySide6.QtWidgets import QScrollArea
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 24)
        lay.setSpacing(14)
        tip = tr("我们的 DETERSIVI 总表（工作文件夹「0 Griglia」里最新的一份）和这周批发商报价对比：哪些商品停售了（批发商都不卖了）、哪些新品不在 Griglia 里（几家在做、各家价格）、Griglia 里的商品现在哪家最便宜。新品只算报价里「我们这几类」的部门（Griglia 商品所在的部门）")
        self.hero = Hero(tr("Griglia"), tr("我们的 DETERSIVI 总表 vs 这周批发商报价"), tip=tip)
        self.file_lbl = self.hero.sub
        self.b_repl = button(tr("找替代商品"), self._find_repl,
                             tip=tr("停售的商品很多是换了规格（40 洗 → 50 洗、950 ml → 1000 ml）：在这周报价里找像的商品，核对后替换进 Griglia"))
        self.b_exp = button(tr("导出 Excel"), self.export, primary=True)
        more, acts = more_menu([
            (tr("换 Griglia…"), self.choose, tr("选一份新的 Griglia，复制到工作文件夹「0 Griglia」（旧的挪进 _旧文件）")),
            (tr("打开文件夹"), self.open_dir, None),
            (tr("刷新"), self.refresh, tr("重新读 Griglia 和「2 供应商报价」")),
            None,
            (tr("替换记录"), self._repl_log, tr("哪天谁把哪个原商品换成了哪个新商品、改的是哪个文件第几行")),
        ])
        self.b_log = acts[tr("替换记录")]
        for b in (self.b_repl, self.b_exp, more):
            self.hero.add(b)
        lay.addWidget(self.hero)

        self.stack = QStackedWidget()
        from erp_widgets import EmptyState
        self.empty = EmptyState("griglia", tr("还没有 Griglia"),
                                tr("把我们的 DETERSIVI 总表（ELENCO_DETERSIVI.xlsx）放进工作文件夹「0 Griglia」，或点「换 Griglia…」"))
        b = QPushButton(tr("选择 Griglia"))
        b.setObjectName("Primary")
        b.clicked.connect(self.choose)
        self.empty.buttons.addWidget(b)
        self.stack.addWidget(self.empty)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        body = QWidget()
        body.setObjectName("GrBody")
        body.setStyleSheet("#GrBody { background: transparent; }")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, 4, 8)
        bv.setSpacing(14)
        self.panel = GridPanel(ui)
        self.panel.tab_clicked.connect(self._go_tab)
        self.panel.sup_clicked.connect(self._sup_clicked)
        bv.addWidget(self.panel)
        card = Card(margins=(16, 10, 16, 14))
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
        self.gone_bar = QWidget()              # 以前放「找替代商品」「替换记录」，挪到页头了；留个空的占位
        QHBoxLayout(self.gone_bar).setContentsMargins(0, 0, 0, 0)
        self._quotes, self._fresh = None, None
        self._repl = None             # 找替代商品提前算好的 (grid, (候选, 搜索池))，点按钮直接打开（用户 11-11：点了卡一下）
        self._repl_ready = threading.Event()
        self._repl_ready.set()
        # 四张表的列跟着这周的批发商变（每家一列价格），统计完再建；列可以拖动换位置，记在设置里
        self.t_gone = self.t_new = self.t_price = self.t_odd = None
        self.boxes = []
        for _ in range(4):
            bx = QWidget()
            QVBoxLayout(bx).setContentsMargins(0, 0, 0, 0)
            self.boxes.append(bx)
        for bx, text in zip(self.boxes, (tr("停售"), tr("新品"), tr("价格"), tr("价格可疑"))):
            self.tabs.addTab(bx, text)
        self.tabs.setTabToolTip(3, tr("报价表里明显写错的价格（低于 €0.05，或比别家中间价低 70% 以上，多半是箱价 / 件价搞混）：不拿来比最低价，请找供应商确认"))
        card.v.addWidget(self.tabs, 1)
        card.setMinimumHeight(600)
        bv.addWidget(card, 1)
        scroll.setWidget(body)
        self.scroll = scroll
        self.stack.addWidget(scroll)
        self.busy = QLabel(tr("正在统计…"))
        self.busy.setAlignment(Qt.AlignCenter)
        self.busy.setObjectName("Faint")
        self.stack.addWidget(self.busy)
        lay.addWidget(self.stack, 1)
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self._first_show())
        self.page = w
        return w

    def _go_tab(self, k):
        self.tabs.setCurrentIndex(k)
        self.scroll.ensureWidgetVisible(self.tabs, 0, 40)

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
                    import file_inbox as FI
                    try:
                        FI.archive(fp, old)               # 重名加时间、一模一样的不多存一份
                    except OSError:
                        pass
            try:
                shutil.copy2(p, os.path.join(d, os.path.basename(p)))
                os.utime(os.path.join(d, os.path.basename(p)))      # 最新的一份
            except OSError as e:                                     # 「0 Griglia」那份在 WPS / Excel 里开着
                self.win.show_toast(tr("换不了 Griglia：「0 Griglia」里那份文件正在 WPS / Excel 里打开，请先关掉再试（{0}）", e))
                return
            src = os.path.abspath(p)                             # 记下原件：加替代品时直接改它（用户 11-09）
        else:
            src = ""
        import settings as ST
        self.settings.setdefault("griglia", {})["source"] = src
        ST.save_settings(self.settings)
        self.refresh()

    # ---------- 统计 ----------
    def refresh(self):
        if self.running:
            return
        r = self._root()
        import griglia as G
        if r and self.source():
            try:
                if G.sync_from_source(r, self.source()) and hasattr(self.win, "log"):
                    self.win.log(tr("Griglia 原件有更新，已重新复制：{0}", self.source()))
            except OSError as e:
                if hasattr(self.win, "log"):
                    self.win.log(tr("⚠ 读不了 Griglia 原件（{0}）：{1}", self.source(), e))
        if not r or not G.find_file(r):
            self.file_lbl.setText(tr("我们的 DETERSIVI 总表 vs 这周批发商报价"))
            self.stack.setCurrentIndex(0)
            return
        self.running = True
        self.stack.setCurrentIndex(2)
        self._repl = None
        self._repl_ready.clear()
        def work():
            try:
                import workspace as W
                grid = G.load_for(r)
                quotes = self.win.order_check._read_quotes(W.price_dir(r), lambda *_: None) or {}
                from datetime import date
                from settings import app_dir
                today = date.today().isoformat()
                sp = os.path.join(app_dir(), "cache", "griglia", "seen.json")
                shared = W.data_dir(r)                 # 工作文件夹在同步盘：几台电脑的新上架记录合起来
                seen = G.load_seen_all(sp, shared)
                hist = any(seen.get(k, {}).get("since", today) < today for k in quotes)
                seen, fresh = G.track(seen, quotes, today)
                G.save_seen_all(sp, seen, shared)
                st = G.stats(grid, quotes, fresh if hist else None)
                import griglia_replace as GR
                GR.set_shared((self.settings.get("griglia") or {}).get("source") or "")
                done = GR.done_eans(GR.load_done(os.path.dirname(grid["path"])), grid)   # 加过替代品的：不再算「都不做了」
                st["gone"] = [x for x in st["gone"] if x["ean"] not in done]
                self._quotes, self._fresh = quotes, (fresh if hist else None)
                self.sig.done.emit((grid, st, None))
            except Exception as e:
                self._repl_ready.set()
                self.sig.done.emit((None, None, str(e)))
                return
            self._warm_replacements(grid, quotes, self._fresh)
            self._publish_scanner(grid, quotes)
        __import__("erp_widgets").run_excel_job(work)      # 可能用到 Excel：排进同一条线程（不在线程退出时卡死）

    def _publish_scanner(self, grid, quotes):
        """统计完顺便把门店扫码枪的目录发布到预约平台（内容没变 / 没连平台就不发；失败只记日志）"""
        try:
            import booking_api as BA
            if not BA.ready(self.settings):
                return
            import griglia_replace as GR
            import scanner_catalog as SC
            from datetime import date
            subs = GR.substitutes(GR.load_done(os.path.dirname(grid["path"])), grid)
            meta, rows = SC.build(grid, quotes, subs, SC.direct_index(self.settings, lambda *_: None),
                                  week=date.today().strftime("%G-W%V"))
            me = (self.settings.get("tracking") or {}).get("me") or self.settings.get("user_name") or "?"
        except Exception as e:
            self.sig.log.emit(tr("⚠ 扫码枪目录没发布：{0}", e))
            return

        def send():
            """每家门店的库存 / 销量 / 价格要查数据库（几分钟）：单独一条线程（不占 Excel 那条线程，也不用 Excel）"""
            try:
                stores = SC.cached_store_data(self.settings, rows, quotes, log=self.sig.log.emit)
                SC.publish_if_changed(self.settings, meta, rows, me, log=self.sig.log.emit, stores=stores)
            except Exception as e:
                self.sig.log.emit(tr("⚠ 扫码枪目录没发布：{0}", e))
        import threading
        threading.Thread(target=send, name="scanner-publish", daemon=True).start()

    def _warm_replacements(self, grid, quotes, fresh):
        """统计完在后台接着把「找替代商品」算好（约 1 秒）：点按钮时不用再等"""
        try:
            import commands as CMD
            import griglia_replace as GR
            res = (CMD.griglia_replacements(grid, quotes, fresh, struck="both"), GR.pool(grid, quotes))
            self._repl = (grid, quotes, res)
        except Exception:
            self._repl = None
        finally:
            self._repl_ready.set()

    def _done(self, payload):
        grid, st, err = payload
        self.running = False
        if err:
            self.stack.setCurrentIndex(0)
            self.win.show_toast(tr("Griglia 统计失败：{0}", err))
            return
        self.grid, self.st = grid, st
        bad = (grid or {}).get("bad_rows") or []
        if bad:                                       # 源文件有列错位的行：提醒一次（这几行只用编号 / 名称 / 价格）
            import griglia as G
            msg = tr("Griglia 第 {0} 行列错位了（{1} 行）：这几行的品牌、供应商、分类、批发商价格没用，请在 Excel 里挪回对的列",
                     G.bad_rows_text(bad), len(bad))
            if hasattr(self.win, "log"):
                self.win.log("⚠ " + msg)
            self.win.show_toast(msg)
        labels = st["labels"]
        self._labels = labels
        self.tabs.setTabText(0, tr("停售（{0}）", len(st["gone"])))
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
        stats = [(tr("停售"), f"{len(st['gone']):,}", "err", 0, tr("Griglia 里有、这周所有批发商报价里都没有")),
                 (tr("新上架"), f"{nf:,}" if st["history"] else "—", "accent", 1,
                  tr("批发商报价里第一次出现、Griglia 里没有的商品（和以前读过的报价比，30 天内）。第一次统计只是记下现在有哪些，下次换了新报价才有")),
                 (tr("最低价换了供应商"), f"{moved:,}", "warn", 2, tr("现在最便宜的批发商不是 Griglia 里登记的那家")),
                 (tr("价格可疑"), f"{len(st['odd']):,}", "warn", 3,
                  tr("报价表里明显写错的价格（低于 €0.05，或比别家中间价低 70% 以上，多半是箱价 / 件价搞混）：不拿来比最低价，请找供应商确认"))]
        self.file_lbl.setText(" · ".join(x for x in (
            grid["name"], f"{datetime.fromtimestamp(grid.get('mtime') or 0):%d/%m/%Y}",
            tr("同步文件夹") if self.source() and os.path.isfile(self.source()) else "",
            tr("{0:,} 个商品", len(grid.get("items") or [])), tr("{0} 家批发商报价", len(self._labels))) if x))
        self.file_lbl.setToolTip(tr("原件：{0}\n程序读的是「0 Griglia」里的副本，原件变了会自动重新复制；加替代品时直接改原件", self.source())
                                 if self.source() else tr("这份 Griglia 只在工作文件夹「0 Griglia」里（没有记下原件）。"
                                                          "想直接改同步文件夹那份：点「换 Griglia…」从同步文件夹选一次"))
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
        self._go_tab(2)

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
        if self.t_gone is not None:
            self.gone_bar.setParent(None)                 # 按钮先拿出来，旧表格删掉时别跟着删
        self.t_gone = self._tab(0, "gone", base + [(3, tr("编号"), "txt", lambda x: x.get("code"), None),
                                                   (4, tr("品牌"), "txt", lambda x: x.get("brand"), None)] + cats +
                                # Griglia 没写供应商的：用 Griglia 里最便宜的批发商价格推出来的那家，和别的行一样写（用户 11-20）
                                [(9, tr("Griglia 供应商"), "txt",
                                  lambda x: x.get("supplier") or x.get("supplier_fill") or x.get("wholesaler"), None),
                                 (10, tr("Griglia 价"), "price", lambda x: x.get("best") or x.get("price"), None)],
                                st["gone"], extra=self.gone_bar)
        self.b_repl.setEnabled(bool(st["gone"]) or bool((self.grid or {}).get("struck")))   # 划线的商品也能找（用户 11-11）
        self._update_log_btn()
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
        """导出 Excel：先选要哪几张表（默认现在打开的那张）、要不要按表里现在的筛选（2026-11-07 用户）"""
        if not self.st:
            return
        import griglia as G
        from datetime import datetime
        from erp_widgets import BusyDialog
        dlg = _ExportDialog(self.win, self)
        if dlg.exec() != QDialog.Accepted:
            return
        which, filtered = dlg.which(), dlg.filtered.isChecked()
        stem = G.SHEETS[which[0]].replace(" ", "_") if len(which) == 1 else "Statistiche"   # 文件名意大利文
        p, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"), os.path.join(
            self._root() or "", f"Griglia_{stem}_{datetime.now():%Y%m%d}.xlsx"), tr("Excel 文件 (*.xlsx)"))
        if not p:
            return
        rows = {k: self._rows_of(k) for k in which} if filtered else None
        import product_images as PI
        url = self.settings.get("image_url") or PI.DEFAULT_URL
        gname = (self.grid or {}).get("name")
        _, err = BusyDialog.run(self.win, tr("导出 Excel"), tr("正在写 {0}…", os.path.basename(p)),
                                lambda: G.export(self.st, p, which=which, rows=rows, url=url, grid_name=gname))
        if err:
            self.win.show_toast(tr("导出失败：{0}", err))
            return
        self.win.show_toast(tr("已导出：{0}", os.path.basename(p)))
        from erp_widgets import open_file                      # 导出完直接打开（2026-11-07 用户）
        open_file(self.win, p, tr("导出 Excel"))

    def _find_repl(self):
        from griglia_replace_dialog import open_replace
        open_replace(self)

    def _done_log(self):
        import griglia_replace as GR
        return GR.load_done(os.path.dirname(self.grid["path"])) if self.grid and self.grid.get("path") else {}

    def _update_log_btn(self):
        n = len(self._done_log())
        self.b_log.setToolTip(tr("已替换 {0} 个商品", n) if n else "")
        self.b_log.setEnabled(bool(n))
        self.b_repl.setVisible(self.b_repl.isEnabled())       # 没有停售 / 划线的：页头不放这个按钮

    def _repl_log(self):
        from griglia_replace_dialog import ReplaceLogDialog
        dlg = ReplaceLogDialog(self.win, self.settings, self._done_log(), self.grid, source=self.source())
        dlg.exec()
        if dlg.restored:
            self.refresh()

    def source(self):
        """Griglia 原件（同步文件夹里的）：「换 Griglia…」时记下的"""
        src = (self.settings.get("griglia") or {}).get("source") or ""
        import griglia_replace as GR
        GR.set_shared(src)                     # 替换记录在 Griglia 原件旁边也存一份，同事看得到
        return src

    def _tab_of(self, key):
        return {"gone": self.t_gone, "new": self.t_new, "prices": self.t_price, "odd": self.t_odd}.get(key)

    def _rows_of(self, key):
        """这张表现在看得到的行（搜索 / 筛选 / 新品的几家在做之后），按表里现在的顺序"""
        tab, data = self._tab_of(key), self.st.get(key) or []
        if tab is None:
            return list(data)
        return [data[i] for i in tab.ctl.visible if 0 <= i < len(data)]
