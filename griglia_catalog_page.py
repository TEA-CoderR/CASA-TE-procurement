# -*- coding: utf-8 -*-
"""
Griglia → 商品目录（2026-11-24 第 1 步：只看）。数据见 griglia_db.py。

  页头：几个商品 · 什么时候从哪份 Excel 导入 · 价格什么时候更新；右边 更新价格 · 导出 Excel · ⋯（从 Excel 导入 / 和 Excel 对比 / 导入检查 / 打开文件夹）
  左边：「分类 | 供应商」切换，一次只看一样、整列高度给它：分类三级树（大类 › 中类 › 小类，每级几个商品）/
        供应商（直厂 / 这周最便宜的批发商，能搜）；两样可以叠在一起筛
  中间：搜索 + 已选的筛选小标签 · 状态一排按钮（带个数）· 商品表（TableSortFilter；原表 67 列都在「显示哪些列」里）·
        右下角像 Excel 一样缩放（− 滑块 +，Ctrl + 滚轮 / Ctrl + / Ctrl − / Ctrl 0），记在本机
  右边：选中商品的详情（照原表的分组），可以收起
第 1 步 Excel 还是正本：这里只看、不改；价格每次报价换了自动更新。
（11-24 用户：操作起来费劲 → 状态挪到表格上面一排、左边分类 / 供应商切换、表格能缩放、行矮一点、名字写短的、详情栏能收起）
"""
import os
import threading

from PySide6.QtCore import Qt, QObject, Signal, QEvent
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QFrame, QStackedWidget,
                               QTreeWidget, QTreeWidgetItem, QScrollArea, QPushButton, QButtonGroup, QSlider,
                               QHeaderView, QDialog, QTabWidget, QFileDialog, QMessageBox, QGridLayout)

from i18n import tr

STATUS_TEXT = {"active": "在售", "new": "新品", "paused": "暂停", "off": "停用", "replaced": "被替换"}
STATUS_TIP = {"active": "Griglia 里在用的商品",
              "new": "原表 STATO PRODOTTO 写了 NUOVO 的",
              "paused": "这周几家批发商的报价里都没有（程序按报价自动标）",
              "off": "原表里划了删除线的（以前不做了的商品）",
              "replaced": "找替代商品时被换掉的原商品"}
STATUS_TOKEN = {"active": None, "new": "accent", "paused": "warn", "off": "faint", "replaced": "violet"}
NONE = "（没写）"
# 表格的列：(固定编号, 表头, 类型)；原表的列 = 100 + 原表列号（默认藏着）
COLS = [(0, "条码", "txt"), (1, "商品", "txt"), (2, "Cod.Art.", "txt"), (3, "品牌", "txt"),
        (4, "大类", "txt"), (5, "中类", "txt"), (6, "小类", "txt"), (7, "直厂", "txt"), (8, "箱规", "num"),
        (9, "最便宜批发商", "txt"), (10, "最低进价", "price"), (15, "卖价", "price"), (16, "毛利率", "pct"),
        (17, "状态", "txt"), (18, "要看的", "txt")]
HIDDEN = [0, 3, 6] + [100 + c for c in range(200)]
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.6, 1.6, 0.1
THUMB0 = 40                  # 100% 时的小图（以前用设置里的「中」64，行太高、一屏看不了几个）
FONT0, HEAD0 = 13, 12        # 100% 时正文 / 表头字号（像素）


def _p(v):
    return "" if v is None else f"€{v:,.2f}"


class _Sig(QObject):
    loaded = Signal(object)
    priced = Signal(object)


class _WheelZoom(QObject):
    """表格上 Ctrl + 滚轮 = 缩放（和 Excel 一样）；不按 Ctrl 照常滚动"""

    def __init__(self, page):
        super().__init__(page.t)
        self.page = page

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Wheel and e.modifiers() & Qt.ControlModifier:
            d = e.angleDelta().y()
            if d:
                self.page.zoom_by(ZOOM_STEP if d > 0 else -ZOOM_STEP)
            return True
        return False


class GrigliaCatalogPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cat = None
        self.sig = _Sig()
        self.sig.loaded.connect(self._loaded)
        self.sig.priced.connect(self._priced)
        self.f_status, self.f_cat, self.f_sup = None, (), None     # f_sup = (kind, 名称)
        self.sup_kind = "direct"
        self._rows, self._keys, self._hay = [], [], []
        self._loading = self._pricing = False
        self._built_cols = None
        self._zoom = 1.0
        self._px = FONT0
        self.page = None

    def _cfg(self):
        return self.settings.setdefault("griglia_db", {})

    def _save(self):
        import settings as ST
        ST.save_settings(self.settings)

    # ------------------------------------------------------------------ 界面
    def build(self):
        from page_kit import Hero, Card, button, more_menu, Seg
        from erp_widgets import EmptyState, CountButton
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 20)
        lay.setSpacing(14)
        tip = tr("以后 Griglia 在程序里管（现在第 1 步：只看，Excel 还是正本）。每家批发商价格、最低进价、Fornitore 按这周报价自动更新，卖价跟数据库")
        self.hero = Hero(tr("Griglia 商品目录"), "", tip=tip)
        self.b_price = button(tr("更新价格"), lambda: self.update_prices(manual=True),
                              tip=tr("按「2 供应商报价」里这周的报价重算每家批发商价格、最低进价、Fornitore，卖价从数据库读"))
        self.b_exp = button(tr("导出 Excel"), self.export, primary=True,
                            tip=tr("导出一份和原表一样的 SCHEDA DETERSIVI（图片用 IMAGE 公式，几 MB）"))
        more, _acts = more_menu([
            (tr("从 Excel 导入"), self.do_import, tr("把现在的 SCHEDA DETERSIVI 读进程序（重新导入会换掉上一次导入的）")),
            (tr("和 Excel 对比"), self.compare, tr("程序里的和「0 Griglia」里现在的 Excel 有什么不一样（过渡期有人还在 Excel 里改）")),
            (tr("导入检查"), self.show_issues, tr("导入时发现要人看的：两套分类不一样、没有 Cod.Art.、卖价低于进价…")),
            None,
            (tr("打开文件夹"), self.open_dir, None),
        ])
        for b in (self.b_price, self.b_exp, more):
            self.hero.add(b)
        lay.addWidget(self.hero)

        self.stack = QStackedWidget()
        self.empty = EmptyState("griglia", tr("还没有导入 Griglia"),
                                tr("把现在的 SCHEDA DETERSIVI 读进程序：GRIGLIA 页每一列、其他几页都会搬进来，原 Excel 不动"))
        b = QPushButton(tr("从 Excel 导入"))
        b.setObjectName("Primary")
        b.clicked.connect(self.do_import)
        self.empty.buttons.addWidget(b)
        self.stack.addWidget(self.empty)

        body = QWidget()
        bh = QHBoxLayout(body)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(14)
        # 左边：分类 / 供应商（切换，一次一样，整列高度给它）
        left = Card(margins=(12, 12, 12, 12))
        left.setFixedWidth(250)
        self.left_seg = Seg([("cat", tr("分类")), ("sup", tr("供应商"))], "cat",
                            tip=tr("分类 = 大类 › 中类 › 小类（原表右边那套 CAT.1 / CAT.2 / CAT.3，空着的用左边那套补）；"
                                   "供应商 = 直厂（FORNITORI DIRETTO）或这周最便宜的批发商。两样可以叠在一起筛"))
        self.left_seg.changed.connect(self._left_page)
        left.v.addWidget(self.left_seg)
        self.left_stack = QStackedWidget()
        self.tree = _two_col(root=True)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.left_stack.addWidget(self.tree)
        sw = QWidget()
        sv = QVBoxLayout(sw)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(8)
        kr = QHBoxLayout()
        kr.setSpacing(6)
        self.kind_btns = {}
        kg = QButtonGroup(w)
        for k, text, tip in (("direct", tr("直厂"), tr("原表 FORNITORI DIRETTO")),
                             ("best", tr("最便宜批发商"), tr("这周报价里件价最低的那家批发商"))):
            kb = QPushButton(text)
            kb.setObjectName("Chip")
            kb.setCheckable(True)
            kb.setChecked(k == "direct")
            kb.setCursor(Qt.PointingHandCursor)
            kb.setToolTip(tip)
            kb.clicked.connect(lambda _=False, kk=k: self._sup_kind(kk))
            kg.addButton(kb)
            kr.addWidget(kb)
            self.kind_btns[k] = kb
        kr.addStretch()
        sv.addLayout(kr)
        self.sup_search = QLineEdit()
        self.sup_search.setPlaceholderText(tr("搜供应商"))
        self.sup_search.setClearButtonEnabled(True)
        self.sup_search.textChanged.connect(lambda _t: self._fill_sups())
        sv.addWidget(self.sup_search)
        self.sup_list = _two_col()
        self.sup_list.itemClicked.connect(self._sup_clicked)
        sv.addWidget(self.sup_list, 1)
        self.left_stack.addWidget(sw)
        left.v.addWidget(self.left_stack, 1)
        bh.addWidget(left)

        # 中间：表格
        mid = Card(margins=(16, 12, 16, 10))
        mid.v.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("名称 / 条码 / Cod.Art. / 品牌"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(280)
        self.search.textChanged.connect(lambda _t: self._render())
        bar.addWidget(self.search)
        self.tags = QHBoxLayout()
        self.tags.setSpacing(6)
        bar.addLayout(self.tags)
        bar.addStretch()
        self.b_drawer = QPushButton(tr("详情栏"))
        self.b_drawer.setObjectName("Chip")
        self.b_drawer.setCheckable(True)
        self.b_drawer.setCursor(Qt.PointingHandCursor)
        self.b_drawer.setToolTip(tr("右边的商品详情：收起来表格更宽"))
        self.b_drawer.toggled.connect(self._toggle_drawer)
        bar.addWidget(self.b_drawer)
        mid.v.addLayout(bar)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.status_group = QButtonGroup(w)
        self.status_group.setExclusive(True)
        self.status_btns = {}
        for key, text, tip in [(None, tr("全部"), "")] + \
                [(s, tr(STATUS_TEXT[s]), tr(STATUS_TIP[s])) for s in ("active", "new", "paused", "off", "replaced")] + \
                [("issues", tr("要看的"), tr("导入时发现要人看的（两套分类不一样、没有 Cod.Art.、卖价低于进价…）"))]:
            cb = CountButton(text, 0, show_zero=True)
            cb.setObjectName("Chip")
            cb.setCheckable(True)
            cb.setCursor(Qt.PointingHandCursor)
            cb.setToolTip(tip)
            cb.clicked.connect(lambda _=False, k=key: self._status_clicked(k))
            self.status_group.addButton(cb)
            self.status_btns[key] = cb
            chips.addWidget(cb)
        self.status_btns[None].setChecked(True)
        chips.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        chips.addWidget(self.count)
        mid.v.addLayout(chips)
        self.t = self.ui.make_table([tr(h) for _f, h, _k in COLS], stretch_col=None,
                                    numeric_cols=tuple(i for i, c in enumerate(COLS) if c[2] != "txt"))
        mid.v.addWidget(self.t, 1)
        foot = QHBoxLayout()
        foot.setSpacing(6)
        hint = QLabel(tr("Ctrl + 滚轮缩放"))
        hint.setObjectName("Faint")
        foot.addWidget(hint)
        foot.addStretch()
        b_out = QPushButton("−")
        b_in = QPushButton("+")
        for b in (b_out, b_in):
            b.setFixedSize(30, 28)
            b.setStyleSheet("QPushButton { padding: 0; min-height: 0; font-size: 16px; font-weight: 600; }")
            b.setCursor(Qt.PointingHandCursor)
        b_out.setToolTip(tr("缩小（Ctrl −）"))
        b_in.setToolTip(tr("放大（Ctrl +）"))
        b_out.clicked.connect(lambda: self.zoom_by(-ZOOM_STEP))
        b_in.clicked.connect(lambda: self.zoom_by(ZOOM_STEP))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(int(ZOOM_MIN * 100), int(ZOOM_MAX * 100))
        self.zoom_slider.setSingleStep(10)
        self.zoom_slider.setPageStep(10)
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.setValue(100)
        self.zoom_slider.valueChanged.connect(lambda v: self.zoom_to(v / 100.0))
        self.zoom_lbl = QPushButton("100%")
        self.zoom_lbl.setFixedSize(60, 28)
        self.zoom_lbl.setStyleSheet("QPushButton { padding: 0; min-height: 0; }")
        self.zoom_lbl.setCursor(Qt.PointingHandCursor)
        self.zoom_lbl.setToolTip(tr("点一下回到 100%（Ctrl 0）"))
        self.zoom_lbl.clicked.connect(lambda: self.zoom_to(1.0))
        for x in (b_out, self.zoom_slider, b_in, self.zoom_lbl):
            foot.addWidget(x)
        mid.v.addLayout(foot)
        bh.addWidget(mid, 1)

        # 右边：详情
        self.drawer = _Drawer(self)
        bh.addWidget(self.drawer)
        self.stack.addWidget(body)
        self.busy = QLabel(tr("正在读 Griglia…"))
        self.busy.setAlignment(Qt.AlignCenter)
        self.busy.setObjectName("Faint")
        self.stack.addWidget(self.busy)
        lay.addWidget(self.stack, 1)
        self._setup_table()
        for seq, fn in ((QKeySequence.ZoomIn, lambda: self.zoom_by(ZOOM_STEP)), ("Ctrl+=", lambda: self.zoom_by(ZOOM_STEP)),
                        (QKeySequence.ZoomOut, lambda: self.zoom_by(-ZOOM_STEP)), ("Ctrl+0", lambda: self.zoom_to(1.0)),
                        (QKeySequence.Find, lambda: (self.search.setFocus(), self.search.selectAll()))):
            sc = QShortcut(QKeySequence(seq), w)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        show = self._cfg().get("drawer", True)
        self.b_drawer.setChecked(bool(show))
        self.drawer.setVisible(bool(show))
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self._first_show())
        self.page = w
        self._shown = False
        return w

    def _left_page(self, k):
        self.left_stack.setCurrentIndex(0 if k == "cat" else 1)

    def _toggle_drawer(self, on):
        self.drawer.setVisible(on)
        self._cfg()["drawer"] = bool(on)
        self._save()
        if on:
            self._selected()

    def _setup_table(self):
        import product_images as PI
        from erp_widgets import TableSortFilter
        t = self.t
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        self._images = bool(self.settings.get("show_images", True))
        widths = {0: 120, 1: 300 + (THUMB0 + 10 if self._images else 0), 2: 78, 3: 110, 4: 130, 5: 130, 6: 120,
                  7: 140, 8: 56, 9: 120, 10: 84, 15: 72, 16: 70, 17: 70, 18: 160}
        for c, (f, _h, _k) in enumerate(COLS):
            t.setColumnWidth(c, widths.get(f, 90))
        self.thumb = None
        if self._images:
            self.thumb = PI.ThumbDelegate(t, 0, THUMB0, self.settings)
            t.setItemDelegateForColumn(1, self.thumb)
        t.itemSelectionChanged.connect(self._selected)
        t.viewport().installEventFilter(_WheelZoom(self))
        self.ctl = TableSortFilter(t, lambda: len(self._rows), lambda i, c: self._rows[i][c], self._fill,
                                   key=lambda i, c: self._keys[i][c], hide=self._hidden,
                                   numeric=[i for i, c in enumerate(COLS) if c[2] != "txt"], lazy=True)
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        self._apply_zoom(float(self._cfg().get("zoom") or 1.0), scale_cols=False, render=False)

    # ------------------------------------------------------------------ 缩放（像 Excel）
    def zoom_by(self, d):
        self.zoom_to(self._zoom + d)

    def zoom_to(self, z):
        z = round(max(ZOOM_MIN, min(ZOOM_MAX, z)), 2)
        if abs(z - self._zoom) < 1e-6:
            return
        self._apply_zoom(z)
        self._cfg()["zoom"] = z
        self._save()

    def _apply_zoom(self, z, scale_cols=True, render=True):
        """字号、表头、小图、行高按比例；列宽跟着放大缩小（列宽记的是缩放后的宽度，所以重开程序不会再乘一次）"""
        t = self.t
        ratio = z / self._zoom if self._zoom else 1.0
        self._zoom = z
        if scale_cols and abs(ratio - 1) > 1e-6:
            for c in range(t.columnCount()):
                t.setColumnWidth(c, max(28, int(round(t.columnWidth(c) * ratio))))
        self._px = max(8, int(round(FONT0 * z)))
        t.setStyleSheet(f"QTableWidget::item {{ padding: 2px {max(3, int(round(8 * z)))}px; }}")
        t.horizontalHeader().setStyleSheet(
            f"QHeaderView::section {{ font-size: {max(8, int(round(HEAD0 * z)))}px; "
            f"padding: {max(3, int(round(9 * z)))}px {max(3, int(round(8 * z)))}px; }}")
        if self.thumb is not None:
            self.thumb.size = max(18, int(round(THUMB0 * z)))
            rowh = max(int(round(30 * z)), self.thumb.size + 8)
        else:
            rowh = max(20, int(round(34 * z)))
        t.verticalHeader().setDefaultSectionSize(rowh)
        pct = int(round(z * 100))
        self.zoom_lbl.setText(f"{pct}%")
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(pct)
        self.zoom_slider.blockSignals(False)
        if render and self.cat is not None:
            self.ctl.render()

    def _rebuild_columns(self):
        """原表的每一列也放进表格（默认藏着，右键表头「显示哪些列」打开）；列的设置记在 settings griglia_db_cols"""
        from PySide6.QtWidgets import QTableWidgetItem
        cat = self.cat
        names = [c["name"].replace("\n", " ").strip() or f"#{i + 1}" for i, c in enumerate(cat.columns)]
        sig = tuple(names)
        if self._built_cols == sig:
            return
        base = len(COLS)
        self.t.setColumnCount(base + len(names))
        for k, n in enumerate(names):
            it = QTableWidgetItem(n)
            it.setToolTip(tr("原表第 {0} 列 · {1}", k + 1, cat.columns[k].get("group") or ""))
            self.t.setHorizontalHeaderItem(base + k, it)
            self.t.setColumnWidth(base + k, int(round(100 * self._zoom)))
        self.t._numeric = set(getattr(self.t, "_numeric", ())) | {base + k for k in range(len(names))
                                                                    if self._is_num_col(k)}
        self.ctl.titles = [self.t.horizontalHeaderItem(c).text() for c in range(self.t.columnCount())]
        fids = [f for f, _h, _k in COLS] + [100 + k for k in range(len(names))]
        cfg = self.settings.setdefault("griglia_db_cols", {})
        self.ctl.remember(cfg, "catalog", self._save, fids, default_hidden=[f for f in HIDDEN if f in fids], ver=2)
        self._built_cols = sig

    def _is_num_col(self, k):
        n = 0
        for x in self.cat.items[:200]:
            v = self.cat.value(x, k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                n += 1
        return n >= 50

    # ------------------------------------------------------------------ 读数据
    def _root(self):
        try:
            return self.win.ws_root()
        except Exception:
            return ""

    def _source(self):
        return (self.settings.get("griglia") or {}).get("source") or ""

    def _first_show(self):
        if not self._shown:
            self._shown = True
            self.reload()

    def reload(self, then_price=True):
        if self._loading:
            return
        r = self._root()
        if not r:
            self.stack.setCurrentIndex(0)
            return
        self._loading = True
        if self.cat is None:
            self.stack.setCurrentIndex(2)

        def work():
            import griglia_db as D
            try:
                cat = D.load(r, source=self._source())
                self.sig.loaded.emit((cat, None, then_price))
            except Exception as e:
                self.sig.loaded.emit((None, str(e), False))
        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, payload):
        cat, err, then_price = payload
        self._loading = False
        if err:
            self.stack.setCurrentIndex(0)
            self._log(tr("⚠ 读不了程序里的 Griglia：{0}", err))
            self.win.show_toast(tr("读不了程序里的 Griglia：{0}", err))
            return
        sel = self.drawer.x["ean"] if self.drawer.x else None
        self.cat = cat
        if cat is None:
            self.stack.setCurrentIndex(0)
            self.hero.set_sub(tr("还没有导入"))
            return
        self.stack.setCurrentIndex(1)
        self._rebuild_columns()
        self._build_rows()
        self._fill_status()
        self._fill_tree()
        self._fill_sups()
        self._update_sub()
        self._render()
        k = None
        if sel and sel in cat.by_ean:
            i = cat.by_ean[sel]["i"]
            k = self.ctl.pos.get(i)
        if k is None and self.ctl.visible:
            k = 0
        if k is not None:
            self.ctl.ensure(k)
            self.t.selectRow(k)                       # 详情栏一打开就有东西看
        if then_price and self._quotes_newer():
            self.update_prices(manual=False)

    def _update_sub(self):
        cat = self.cat
        imp = cat.imp or {}
        when = (imp.get("t") or "")[:16].replace("T", " ")
        a = cat.auto or {}
        pr = tr("价格 {0} 更新", (a.get("at") or "")[:16].replace("T", " ")) if a.get("at") else tr("价格还没按报价更新")
        self.hero.set_sub(tr("{0:,} 个商品 · {1} 从 {2} 导入 · {3}", len(cat.items), when, imp.get("source_name") or "?", pr))

    def _quotes_newer(self):
        """「2 供应商报价」里有比上次算价格新的报价（或者还没算过）"""
        import workspace as W
        d = W.price_dir(self._root())
        if not os.path.isdir(d):
            return False
        ts = [os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d)
              if f.lower().endswith((".xlsx", ".xls")) and not f.startswith(("~", "$"))]
        if not ts:
            return False
        at = (self.cat.auto or {}).get("at")
        if not at:
            return True
        from datetime import datetime
        try:
            return max(ts) > datetime.fromisoformat(at).timestamp()
        except ValueError:
            return True

    def _log(self, m):
        if hasattr(self.win, "log"):
            self.win.log(m)

    # ------------------------------------------------------------------ 行
    def _build_rows(self):
        cat = self.cat
        rows, keys, hay = [], [], []
        T = dict(STATUS_TEXT)
        for x in cat.items:
            c1, c2, c3 = (x["cats"] + ["", "", ""])[:3]
            iss = "、".join(tr(dict(_issue_names())[k]) for k in x["issues"] if k not in ("off", "replaced"))
            vals = {0: x["ean"], 1: x["name"], 2: x["code"], 3: x["brand"], 4: c1, 5: c2, 6: c3,
                    7: x.get("direct_short") or x["direct"], 8: x["pack"], 9: x.get("best_short") or "",
                    10: x["best"], 15: x["pvp"], 16: x["mrg"], 17: tr(T[x["status"]]), 18: iss}
            r, k = [], []
            for f, _h, kind in COLS:
                v = vals.get(f)
                if kind == "txt":
                    r.append(str(v or ""))
                    k.append(str(v or "").lower())
                elif kind == "price":
                    r.append(_p(v))
                    k.append(v if v is not None else -1)
                elif kind == "pct":
                    r.append("" if v is None else f"{v * 100:.1f}%")
                    k.append(v if v is not None else -9)
                else:
                    r.append("" if v is None else (str(int(v)) if float(v).is_integer() else f"{v:g}"))
                    k.append(v if v is not None else -1)
            for c in range(len(cat.columns)):
                v = self._shown_value(x, c)
                r.append(_fmt_col(cat.heads[c], v))
                k.append(v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v or "").lower())
            rows.append(r)
            keys.append(k)
            hay.append(" ".join((x["ean"], x["name"], x["code"], x["brand"])).lower())
        self._rows, self._keys, self._hay = rows, keys, hay

    def _shown_value(self, x, c):
        """原表第 c 列显示什么：程序自动的列用这周算的；公式列（PVI / Margine / Mrg% / PREZZO NETTO）用程序算的"""
        h = self.cat.heads[c]
        if h == "PVI":
            return x["pvi"]
        if h == "MARGINE":
            return x["margin"]
        if h == "MRG%":
            return x["mrg"]
        if h == "PREZZO NETTO" and x["best"] is not None:
            return x["best"]
        return self.cat.value(x, c)

    def _fill(self, i, k):
        from main import table_set_row
        x = self.cat.items[i]
        t = self.t
        table_set_row(t, k, self._rows[i])
        T = self.ui.T
        px = self._px
        for c in range(t.columnCount()):            # 缩放：每一格的字号
            it = t.item(k, c)
            if it is not None:
                f = it.font()
                f.setPixelSize(px)
                it.setFont(f)
        it = t.item(k, 1)
        if it is not None:
            it.setToolTip(f"{x['name']}\n{x['ean']}")
        dc = [i2 for i2, c in enumerate(COLS) if c[0] == 7][0]
        it = t.item(k, dc)
        if it is not None and x["direct"]:
            it.setToolTip(x["direct"])
        bc = [i2 for i2, c in enumerate(COLS) if c[0] == 9][0]
        it = t.item(k, bc)
        if it is not None and x.get("best_sup"):
            it.setToolTip(x["best_sup"])
        tok = STATUS_TOKEN.get(x["status"])
        sc = [i2 for i2, c in enumerate(COLS) if c[0] == 17][0]
        it = t.item(k, sc)
        if it is not None:
            it.setToolTip(tr(STATUS_TIP[x["status"]]) + (f" → {x.get('new_ean')}" if x.get("new_ean") else ""))
            if tok:
                it.setForeground(QColor(T(tok)))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
        if x["status"] in ("off", "replaced"):
            it = t.item(k, 1)
            if it is not None:
                f = it.font()
                f.setStrikeOut(True)
                it.setFont(f)
                it.setForeground(QColor(T("faint")))
        mc = [i2 for i2, c in enumerate(COLS) if c[0] == 16][0]
        it = t.item(k, mc)
        if it is not None and x["mrg"] is not None:
            import griglia_db as D
            if x["mrg"] < 0:
                it.setForeground(QColor(T("err")))
            elif x["mrg"] < D.MARGIN_LOW:
                it.setForeground(QColor(T("warn")))
        ic = [i2 for i2, c in enumerate(COLS) if c[0] == 18][0]
        it = t.item(k, ic)
        if it is not None and self._rows[i][ic]:
            it.setForeground(QColor(T("warn")))

    def _hidden(self, i, status=True):
        x = self.cat.items[i]
        if status:
            if self.f_status == "issues":
                if not [k for k in x["issues"] if k not in ("off", "replaced")]:
                    return True
            elif self.f_status and x["status"] != self.f_status:
                return True
        if self.f_cat:
            cs = [(c or NONE) for c in (x["cats"] + ["", "", ""])[:3]]
            if cs[:len(self.f_cat)] != list(self.f_cat):
                return True
        if self.f_sup:
            kind, name = self.f_sup
            v = x["direct"] if kind == "direct" else x.get("best_short")
            if (v or NONE) != name:
                return True
        q = self.search.text().strip().lower()
        if q and not all(w in self._hay[i] for w in q.split()):
            return True
        return False

    def _render(self):
        if self.cat is None:
            return
        self.ctl.render()
        self._fill_tags()
        self._fill_status()

    # ------------------------------------------------------------------ 状态 / 分类 / 供应商
    def _fill_status(self):
        """状态按钮上的个数 = 按现在别的筛选（分类 / 供应商 / 搜索）算的，一看就知道点了会剩几个"""
        cat = self.cat
        from collections import Counter
        c, n_iss, n_all = Counter(), 0, 0
        for i, x in enumerate(cat.items):
            if self._hidden(i, status=False):
                continue
            n_all += 1
            c[x["status"]] += 1
            if [k for k in x["issues"] if k not in ("off", "replaced")]:
                n_iss += 1
        for key, b in self.status_btns.items():
            n = n_all if key is None else n_iss if key == "issues" else c.get(key, 0)
            b.set_count(n, tone=("warn" if key == "issues" and n else "accent"))
            b.setChecked(key == self.f_status)

    def _status_clicked(self, key):
        self.f_status = key
        self._render()

    def _fill_tree(self):
        self.tree.clear()
        tree = self.cat.tree()
        top = _row(self.tree, None, tr("全部分类"), len(self.cat.items), (), self.ui.T)
        if not self.f_cat:
            top.setSelected(True)

        def add(parent, name, n, path):
            it = _row(self.tree, parent, name or tr(NONE), n, path, self.ui.T)
            it.setToolTip(0, " › ".join(tr(p) if p == NONE else p for p in path))
            if path == self.f_cat:
                it.setSelected(True)
            return it

        order = sorted(tree.items(), key=lambda kv: (kv[0] == "", -sum(sum(c.values()) for c in kv[1].values()), kv[0]))
        for c1, mids in order:
            n1 = sum(sum(c.values()) for c in mids.values())
            p1 = add(None, c1, n1, (c1 or NONE,))
            for c2, smalls in sorted(mids.items(), key=lambda kv: (kv[0] == "", -sum(kv[1].values()), kv[0])):
                p2 = add(p1, c2, sum(smalls.values()), (c1 or NONE, c2 or NONE))
                if any(smalls):
                    for c3, n in sorted(smalls.items(), key=lambda kv: (kv[0] == "", -kv[1], kv[0])):
                        add(p2, c3, n, (c1 or NONE, c2 or NONE, c3 or NONE))

    def _tree_clicked(self, it, _col=0):
        path = tuple(it.data(0, Qt.UserRole) or ())
        self.f_cat = () if path == self.f_cat else path
        if path and it.childCount():
            it.setExpanded(True)
        if not self.f_cat:
            self.tree.clearSelection()
        self._render()

    def _sup_kind(self, k):
        self.sup_kind = k
        if k in getattr(self, "kind_btns", {}):
            self.kind_btns[k].setChecked(True)
        self._fill_sups()

    def _fill_sups(self):
        if self.cat is None:
            return
        import griglia_db as D
        q = self.sup_search.text().strip().lower()
        self.sup_list.clear()
        for name, n in self.cat.suppliers(self.sup_kind):
            label = (D.short_company(name) if self.sup_kind == "direct" else name) or tr(NONE)
            if q and q not in (name or "").lower() and q not in label.lower():
                continue
            it = _row(self.sup_list, None, label, n, name or NONE, self.ui.T)
            it.setToolTip(0, name or tr(NONE))
            if self.f_sup == (self.sup_kind, name or NONE):
                it.setSelected(True)

    def _sup_clicked(self, it, _col=0):
        key = (self.sup_kind, it.data(0, Qt.UserRole))
        self.f_sup = None if self.f_sup == key else key
        if self.f_sup is None:
            self.sup_list.clearSelection()
        self._render()

    def _fill_tags(self):
        import griglia_db as D
        while self.tags.count():
            w = self.tags.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
        tags = []
        if self.f_cat:
            tags.append((" › ".join(tr(c) if c == NONE else c for c in self.f_cat), "cat"))
        if self.f_sup:
            kind, name = self.f_sup
            label = tr(NONE) if name == NONE else (D.short_company(name) if kind == "direct" else name)
            tags.append(((tr("直厂") if kind == "direct" else tr("最便宜批发商")) + " · " + label, "sup"))
        for text, key in tags:
            b = QPushButton(text + "  ✕")
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tr("点一下取消这个筛选"))
            b.clicked.connect(lambda _=False, k=key: self._clear(k))
            self.tags.addWidget(b)

    def _clear(self, key):
        if key == "cat":
            self.f_cat = ()
            self.tree.clearSelection()
        else:
            self.f_sup = None
            self.sup_list.clearSelection()
        self._render()

    # ------------------------------------------------------------------ 详情
    def _selected(self):
        if not self.drawer.isVisible() or self.cat is None:
            return
        rows = self.t.selectionModel().selectedRows() if self.t.selectionModel() else []
        if not rows or not self.ctl.visible:
            return
        k = rows[0].row()
        if 0 <= k < len(self.ctl.visible):
            self.drawer.show_item(self.cat.items[self.ctl.visible[k]])

    # ------------------------------------------------------------------ 操作
    def open_dir(self):
        import griglia_db as D
        from main import open_path
        r = self._root()
        if r:
            d = D.store_dir(r)
            os.makedirs(d, exist_ok=True)
            open_path(d)

    def do_import(self):
        import griglia as G
        import griglia_db as D
        from erp_widgets import BusyDialog
        r = self._root()
        if not r:
            self.win.show_toast(tr("先在 DETERSIVO 采购里设置工作文件夹"))
            return
        start = self._source() or G.find_file(r) or G.grid_dir(r)
        p, _ = QFileDialog.getOpenFileName(self.win, tr("选择 SCHEDA DETERSIVI"), start, tr("Excel 文件 (*.xlsx *.xlsm)"))
        if not p:
            return
        again = D.current_import(D.store_dir(r)) is not None
        msg = tr("把这份 Excel 读进程序：\n{0}\n\nGRIGLIA 页每一列、其他几页都搬进来；原 Excel 不动。\n"
                 "存在同步文件夹「0 Griglia\\Griglia programma」，办公室每台电脑都看得到。", p)
        if again:
            msg += "\n\n" + tr("程序里已经导入过一次：这次导入的会换掉上一次的（上一次的文件留着）。")
        if QMessageBox.question(self.win, tr("从 Excel 导入"), msg) != QMessageBox.Yes:
            return
        url = self.settings.get("image_url") or None

        def work():
            snap = D.read_excel(p)
            ev = D.save_import(r, snap, p, url=url)
            return D.load(r, source=self._source()), ev
        res, err = BusyDialog.run(self.win, tr("从 Excel 导入"), tr("正在读 Griglia（大文件要一两分钟）"), work)
        if err:
            QMessageBox.warning(self.win, tr("从 Excel 导入"), tr("导入没成功，程序里的 Griglia 没变：\n{0}", err))
            return
        cat, ev = res
        self._log(tr("Griglia 导入：{0} 个商品（{1}）", ev.get("n"), os.path.basename(p)))
        self._loaded((cat, None, True))
        self.show_issues()

    def update_prices(self, manual=True):
        """按这周的报价 + 数据库卖价重算程序自动的列（后台；没连数据库只更新批发商价格）"""
        if self.cat is None or self._pricing:
            return
        r = self._root()
        if manual:
            if QMessageBox.question(self.win, tr("更新价格"),
                                    tr("按「2 供应商报价」里这周的报价重算每家批发商价格、最低进价、Fornitore，卖价从数据库读。\n\n"
                                       "只改程序算的这几列（存在同步文件夹，每台电脑都看得到），别的不动。")) != QMessageBox.Yes:
                return
        self._pricing = True
        self.b_price.setEnabled(False)
        self.b_price.setText(tr("正在更新价格"))
        self._pill(tr("正在更新 Griglia 价格"), 30.0)
        cat, settings = self.cat, self.settings

        def work():
            import commands as CMD
            import griglia_db as D
            import workspace as W
            msgs = []
            try:
                d = W.price_dir(r)
                quotes = CMD.read_quotes(settings, d, msgs.append) or {}
                files = _quote_files(settings, d)
                pvp = None
                try:
                    import reorder_service as R
                    db = R.make_db(settings.get("db") or None)
                    try:
                        pvp = D.db_prices(db, cat)
                    finally:
                        try:
                            db.engine.dispose()
                        except Exception:
                            pass
                except Exception as e:
                    msgs.append(tr("⚠ Griglia：数据库卖价没读到，这次只更新批发商价格（{0}）", e))
                if not quotes:
                    raise RuntimeError(tr("「2 供应商报价」里没有报价"))
                auto = D.compute_auto(cat, quotes, files, pvp)
                if pvp is None:
                    old = (cat.auto or {}).get("items") or {}
                    for e, a in auto["items"].items():
                        if (old.get(e) or {}).get("pvp"):
                            a["pvp"] = old[e]["pvp"]
                    auto["pvp_at"] = (cat.auto or {}).get("pvp_at")
                D.save_auto(r, auto)
                self.sig.priced.emit((auto, None, msgs, manual))
            except Exception as e:
                self.sig.priced.emit((None, str(e), msgs, manual))
        __import__("erp_widgets").run_excel_job(work)        # 个别报价要用 Excel 算公式：排进 Excel 那条线程

    def _pill(self, text, pct):
        """顶栏小进度（后台更新价格，不挡操作）"""
        p = getattr(self.win, "bg_pill", None)
        if p is not None:
            try:
                p.set_manual(text, pct, tr("按这周报价重算 Griglia 的批发商价格") if text else "")
            except RuntimeError:
                pass

    def _priced(self, payload):
        auto, err, msgs, manual = payload
        self._pricing = False
        self.b_price.setEnabled(True)
        self.b_price.setText(tr("更新价格"))
        self._pill(None, 0)
        for m in msgs:
            self._log(m)
        if err:
            self._log(tr("⚠ Griglia 价格没更新：{0}", err))
            if manual:
                QMessageBox.warning(self.win, tr("更新价格"), tr("价格没更新：{0}", err))
            return
        n = sum(1 for a in auto["items"].values() if a.get("p"))
        self._log(tr("Griglia 价格已按 {0} 份报价更新：{1} 个商品有批发商价格", len(auto.get("files") or {}), n))
        self.win.show_toast(tr("Griglia 价格已更新：{0} 份报价", len(auto.get("files") or {})))
        self.reload(then_price=False)

    def export(self):
        import griglia_db as D
        from erp_widgets import BusyDialog, open_file
        from datetime import date
        if self.cat is None:
            return
        r = self._root()
        d = os.path.join(D.store_dir(r), "Export")
        os.makedirs(d, exist_ok=True)
        name = f"SCHEDA DETERSIVI (programma) {date.today():%d%m%y}.xlsx"
        p, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"), os.path.join(d, name), tr("Excel 文件 (*.xlsx)"))
        if not p:
            return
        url = self.settings.get("image_url") or None
        res, err = BusyDialog.run(self.win, tr("导出 Excel"), tr("正在写 Excel"), lambda: D.export(r, self.cat, p, url=url))
        if err:
            QMessageBox.warning(self.win, tr("导出 Excel"), tr("没导出成功：{0}", err))
            return
        self._log(tr("Griglia 导出：{0} 个商品 → {1}", res, p))
        open_file(self.win, p)

    def compare(self):
        import griglia as G
        import griglia_db as D
        from erp_widgets import BusyDialog
        if self.cat is None:
            return
        r = self._root()
        path = self._source() if self._source() and os.path.isfile(self._source()) else G.find_file(r)
        if not path:
            QMessageBox.information(self.win, tr("和 Excel 对比"), tr("「0 Griglia」里没有 Excel"))
            return
        res, err = BusyDialog.run(self.win, tr("和 Excel 对比"), tr("正在读 Excel"),
                                  lambda: D.compare(self.cat, G.load(path)))
        if err:
            QMessageBox.warning(self.win, tr("和 Excel 对比"), str(err))
            return
        _CompareDialog(self, res, path).exec()

    def show_issues(self):
        if self.cat is not None:
            _IssuesDialog(self).exec()

    def restyle(self):
        if self.cat is not None:
            self._fill_status()
            self._fill_tree()
            self.ctl.render()
            self.drawer.restyle()


def _two_col(root=False):
    """左边的小列表：名称 + 右边的个数（个数右对齐、灰色）；root=True = 分类树（能展开）"""
    t = QTreeWidget()
    t.setObjectName("SideTree")
    t.setHeaderHidden(True)
    t.setColumnCount(2)
    t.setRootIsDecorated(root)
    t.setIndentation(14 if root else 0)
    t.setUniformRowHeights(True)
    t.setFrameShape(QFrame.NoFrame)
    t.header().setStretchLastSection(False)
    t.header().setSectionResizeMode(0, QHeaderView.Stretch)
    t.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    t.setTextElideMode(Qt.ElideRight)
    return t


def _row(tree, parent, text, n, data, T):
    it = QTreeWidgetItem([text, f"{n:,}"])
    it.setData(0, Qt.UserRole, data)
    it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
    it.setForeground(1, QColor(T("muted")))
    (parent.addChild(it) if parent is not None else tree.addTopLevelItem(it))
    return it


def _fit_height(tree):
    n = tree.topLevelItemCount()
    h = tree.sizeHintForRow(0) if n else 28
    tree.setFixedHeight(max(1, n) * max(h, 24) + 6)


def _issue_names():
    import griglia_db as D
    return D.ISSUES


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "✓" if v else ""
    if isinstance(v, float):
        return f"{v:,.3f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.2f}"
    return str(v)


def _fmt_col(head, v):
    """原表的格子怎么显示：Mrg% 写百分数，别的照 _fmt"""
    if head in ("MRG%",) and isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v * 100:.1f}%"
    return _fmt(v)


def _quote_files(settings, d):
    """报价批发商名 → 文件名（auto.json 里记一下这次用的是哪份）"""
    import supplier_names as SN
    import workspace as W
    out = {}
    try:
        for f in sorted(os.listdir(d)):
            if f.lower().endswith((".xlsx", ".xls")) and not f.startswith(("~", "$")) and W.is_price_name(f):
                out.setdefault(SN.label_of(f, settings), f)
    except OSError:
        pass
    return out


class _Drawer(QScrollArea):
    """右边详情：图 + 名称 + 条码 / 编号 + 状态；下面按原表的分组一项一项（程序算的列鼠标停上去有说明）"""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setWidgetResizable(True)
        self.setFixedWidth(290)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.body = QWidget()
        self.body.setObjectName("Drawer")
        self.v = QVBoxLayout(self.body)
        self.v.setContentsMargins(18, 16, 18, 16)
        self.v.setSpacing(10)
        self.img = QLabel()
        self.img.setFixedSize(120, 120)
        self.img.setAlignment(Qt.AlignCenter)
        self.v.addWidget(self.img, 0, Qt.AlignHCenter)
        self.title = QLabel(tr("选中一行查看详情"))
        self.title.setWordWrap(True)
        self.title.setObjectName("Section")
        self.v.addWidget(self.title)
        self.sub = QLabel("")
        self.sub.setObjectName("Faint")
        self.sub.setWordWrap(True)
        self.sub.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.v.addWidget(self.sub)
        self.fields = QWidget()
        self.fv = QVBoxLayout(self.fields)
        self.fv.setContentsMargins(0, 4, 0, 0)
        self.fv.setSpacing(12)
        self.v.addWidget(self.fields)
        self.v.addStretch()
        self.setWidget(self.body)
        self.x = None

    def restyle(self):
        if self.x is not None:
            self.show_item(self.x)

    def show_item(self, x):
        import griglia_db as D
        import product_images as PI
        self.x = x
        page, cat, T = self.page, self.page.cat, self.page.ui.T
        try:
            self.img._img_click = True
            PI.set_label_image(self.img, x["ean"], 120, page.settings)
        except Exception:
            pass
        self.title.setText(x["name"])
        st = x["status"]
        color = T(STATUS_TOKEN.get(st) or "ok")
        extra = f" → {x['new_ean']}" if x.get("new_ean") else ""
        self.sub.setText(f"<span style='color:{color}; font-weight:600'>{tr(STATUS_TEXT[st])}{extra}</span>"
                         f" · {x['ean']}" + (f" · Cod.Art. {x['code']}" if x["code"] else ""))
        self.sub.setTextFormat(Qt.RichText)
        while self.fv.count():
            w = self.fv.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
        # 要看的
        iss = [tr(dict(D.ISSUES)[k]) for k in x["issues"] if k not in ("off", "replaced")]
        if iss:
            lb = QLabel("⚠ " + "、".join(iss))
            lb.setWordWrap(True)
            lb.setStyleSheet(f"color:{T('warn')}; background:{T('warn_soft')}; border-radius:8px; padding:6px 8px;")
            if "cats" in x["issues"]:
                lb.setToolTip(tr("左边那套：{0}\n右边那套：{1}", " › ".join(c for c in x["cats_left"] if c),
                                 " › ".join(c for c in x["cats_right"] if c)))
            self.fv.addWidget(lb)
        # 一眼要看的几样
        key = QGridLayout()
        key.setHorizontalSpacing(10)
        key.setVerticalSpacing(4)
        for i, (lab, val) in enumerate(((tr("最低进价"), _p(x["best"])), (tr("最便宜"), x.get("best_short") or "—"),
                                        (tr("卖价"), _p(x["pvp"])),
                                        (tr("毛利率"), "" if x["mrg"] is None else f"{x['mrg'] * 100:.1f}%"))):
            a, b = QLabel(lab), QLabel(val or "—")
            a.setObjectName("Faint")
            b.setTextInteractionFlags(Qt.TextSelectableByMouse)
            key.addWidget(a, i // 2 * 2, i % 2)
            key.addWidget(b, i // 2 * 2 + 1, i % 2)
        kw = QWidget()
        kw.setLayout(key)
        self.fv.addWidget(kw)
        if x["prices"]:
            import griglia_db as D
            pr = QLabel("  ·  ".join(f"{D.short_wholesaler(h)} {_p(v)}" for h, v in sorted(x["prices"].items(), key=lambda kv: kv[1])))
            pr.setWordWrap(True)
            pr.setObjectName("Faint")
            pr.setToolTip(tr("这周每家批发商的件价（报价里写错的价格不算）"))
            self.fv.addWidget(pr)
        # 原表每一列，按原表的分组
        auto_cols = set(x["auto"]) | {c for c, h in enumerate(cat.heads) if h in ("PVI", "MARGINE", "MRG%", "PREZZO NETTO")}
        groups, cur = [], None
        for c, col in enumerate(cat.columns):
            if col.get("img") or cat.heads[c] in D.ORDER_HEADS or cat.heads[c] in ("QTÀ ORDINE", "IMPORTO"):
                continue
            g = col.get("group") or ""
            if cur is None or cur[0] != g:
                cur = (g, [])
                groups.append(cur)
            cur[1].append(c)
        for g, cols in groups:
            vals = [(c, page._shown_value(x, c)) for c in cols]
            vals = [(c, v) for c, v in vals if v not in (None, "")]
            if not vals:
                continue
            box = QWidget()
            gl = QGridLayout(box)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setHorizontalSpacing(10)
            gl.setVerticalSpacing(3)
            head = QLabel(g.title() if g else tr("其他"))
            head.setObjectName("Faint")
            f = head.font()
            f.setBold(True)
            head.setFont(f)
            gl.addWidget(head, 0, 0, 1, 2)
            for i, (c, v) in enumerate(vals, 1):
                name = cat.columns[c]["name"].replace("\n", " ").strip() or f"#{c + 1}"
                a = QLabel(name)
                a.setObjectName("Faint")
                a.setToolTip(tr("程序算的（按这周报价 / 数据库），以后不用人填") if c in auto_cols else "")
                b = QLabel(_fmt_col(cat.heads[c], v))
                b.setWordWrap(True)
                b.setTextInteractionFlags(Qt.TextSelectableByMouse)
                gl.addWidget(a, i, 0, Qt.AlignTop)
                gl.addWidget(b, i, 1)
            gl.setColumnStretch(1, 1)
            self.fv.addWidget(box)


class _IssuesDialog(QDialog):
    """导入时发现要人看的：一类一个标签页（第 1 步只列出来；第 2 步可以在程序里改）"""

    def __init__(self, page):
        super().__init__(page.win)
        import griglia_db as D
        from erp_widgets import TableSortFilter, fit_to_screen
        self.setWindowTitle(tr("导入检查"))
        self.resize(980, 620)
        cat = page.cat
        v = QVBoxLayout(self)
        refs = cat.ref_columns()
        head = QLabel(tr("{0:,} 个商品已经在程序里。下面是要人看的（第 1 步先列出来，到能在程序里改的时候再处理）", len(cat.items)))
        head.setWordWrap(True)
        v.addWidget(head)
        if refs:
            lb = QLabel(tr("原表里写着 #REF! 的列（导出时空着）：{0}", "、".join(f"{n}（{k}）" for n, k in refs)))
            lb.setObjectName("Faint")
            lb.setWordWrap(True)
            v.addWidget(lb)
        tabs = QTabWidget()
        self._ctls = []
        for key, text in D.ISSUES:
            items = [x for x in cat.items if key in x["issues"]]
            if not items:
                continue
            rows = [[x["ean"], x["name"], x["code"], _detail(key, x)] for x in items]
            t = page.ui.make_table([tr("条码"), tr("商品"), "Cod.Art.", tr("说明")], stretch_col=3)
            t.setColumnWidth(0, 130)
            t.setColumnWidth(1, 330)
            t.setColumnWidth(2, 80)
            ctl = TableSortFilter(t, lambda r=rows: len(r), lambda i, c, r=rows: r[i][c],
                                  lambda i, k, r=rows, tt=t: __import__("main").table_set_row(tt, k, r[i]))
            ctl.render()
            self._ctls.append(ctl)
            tabs.addTab(t, f"{tr(text)}  {len(items)}")
        if not tabs.count():
            tabs.addTab(QLabel(tr("没有要人看的")), tr("全部正常"))
        v.addWidget(tabs, 1)
        bb = QHBoxLayout()
        bb.addStretch()
        b = QPushButton(tr("关闭"))
        b.clicked.connect(self.accept)
        bb.addWidget(b)
        v.addLayout(bb)
        fit_to_screen(self)


def _detail(key, x):
    if key == "cats":
        return tr("左边：{0}　右边：{1}", " › ".join(c for c in x["cats_left"] if c) or "—",
                  " › ".join(c for c in x["cats_right"] if c) or "—")
    if key == "below_cost":
        return tr("卖价不含税 {0} < 最低进价 {1}", _p(x["pvi"]), _p(x["best"]))
    if key == "replaced":
        return tr("换成了 {0}", x.get("new_ean") or "")
    if key == "off":
        return tr("原表划了删除线 → 状态「停用」")
    if key == "shifted":
        return tr("原表第 {0} 行从 Marca 开始列错位了，分类 / 供应商这一行没读", x["n"])
    if key == "nocode":
        return tr("原表 Cod.Art. 空着")
    if key == "dup":
        return tr("同一个条码在原表出现了不止一次，只用第一行")
    return ""


class _CompareDialog(QDialog):
    """程序里的 vs 现在的 Excel（过渡期：有人还在 Excel 里改，提醒，不自动合并）"""

    def __init__(self, page, diffs, path):
        super().__init__(page.win)
        from erp_widgets import TableSortFilter, fit_to_screen
        self.setWindowTitle(tr("和 Excel 对比"))
        self.resize(1000, 600)
        v = QVBoxLayout(self)
        kinds = {"only_excel": tr("只在 Excel 里"), "only_prog": tr("只在程序里"), "changed": tr("不一样")}
        if not diffs:
            lb = QLabel(tr("✓ 程序里的和 Excel 一样（价格、Fornitore、卖价这些程序算的不比）\n{0}", path))
            lb.setWordWrap(True)
            v.addWidget(lb)
        else:
            lb = QLabel(tr("{0} 个商品不一样 · {1}", len(diffs), os.path.basename(path)))
            lb.setToolTip(tr("Excel 里改了的，要在程序里也照改（或者重新导入一次）；价格、Fornitore、卖价是程序算的，不比"))
            v.addWidget(lb)
            rows = []
            for d in diffs:
                text = "；".join(f"{tr(f)}：{_fmt(a) or '—'} → {_fmt(b) or '—'}" for f, a, b in d["diff"])
                rows.append([kinds[d["kind"]], d["ean"], d["name"], text])
            t = page.ui.make_table([tr("差别"), tr("条码"), tr("商品"), tr("程序 → Excel")], stretch_col=3)
            t.setColumnWidth(0, 100)
            t.setColumnWidth(1, 130)
            t.setColumnWidth(2, 300)
            self._ctl = TableSortFilter(t, lambda: len(rows), lambda i, c: rows[i][c],
                                        lambda i, k: __import__("main").table_set_row(t, k, rows[i]))
            self._ctl.render()
            v.addWidget(t, 1)
        bb = QHBoxLayout()
        bb.addStretch()
        b = QPushButton(tr("关闭"))
        b.clicked.connect(self.accept)
        bb.addWidget(b)
        v.addLayout(bb)
        fit_to_screen(self)
