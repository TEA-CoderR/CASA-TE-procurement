"""
Griglia「都不做了」→ 找替代商品（2026-11-09 用户：很多不做了是换了规格，40 洗 → 50 洗、950 ml → 1000 ml，
在新报价里找出来让我核对，再加进 Griglia；用户定：下面加一行新的，旧的留着）。
  左：一行一个不做了的商品（勾选 · 不做了的（小图）· 替代成（小图）· 规格 · 像不像 · 几家 · 最低价），TableSortFilter
  右：选中那一行：旧 → 新 两张商品卡并排（大图），下面候选卡片列表（「不替代」+ 程序找的 + 搜这周报价）
  下：勾了几个 ·「替换进 Griglia（n）」先确认 → commands.griglia_add_replacements（备份 + Excel 原地替换、旧行挪到最下面划线）
窗口可以最大化（用户 11-09：要能放大全屏；列表看着乱、眼睛累 → 规格合成一列、像不像画成彩色小标签、候选画成卡片）。
业务在 griglia_replace.py / commands.py。
"""
from PySide6.QtCore import Qt, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
                               QListWidget, QListWidgetItem, QSplitter, QTableWidgetItem, QHeaderView, QMessageBox,
                               QAbstractItemView, QFrame, QTableWidget, QStyledItemDelegate, QStyle,
                               QStyleOptionViewItem, QApplication)

from i18n import tr
from theme import T

# (字段, 表头, 宽)。old_ean / new_ean 默认藏着（小图从这两列取条码；右键表头「显示哪些列」能打开）
COLS = [("pick", "", 34), ("old", "原商品", 260), ("new", "替代成", 260), ("size", "规格", 120),
        ("score", "相似度", 76), ("n", "几家有货", 72), ("best", "最低价", 76),
        ("old_ean", "条码", 120), ("new_ean", "新条码", 120)]
F = {f: c for c, (f, _h, _w) in enumerate(COLS)}
NUM = (F["n"], F["best"])
THUMB = 36
ROW_H = 46


def pct(c):
    return min(100, round((c or {}).get("score", 0) * 100))


def tone(p):
    return "ok" if p >= 80 else "warn" if p >= 55 else "err"


def _money(v):
    return f"€{v:,.2f}" if v else ""


def _pack(v):
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _pill(p, rect, text, tok, bold=True):
    """一个圆角小标签：浅色底 + 同色字（像不像 80% 绿、55% 黄、更低红）"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(T(tok + "_soft")))
    p.drawRoundedRect(QRectF(rect), rect.height() / 2, rect.height() / 2)
    f = QFont(p.font())
    f.setBold(bold)
    p.setFont(f)
    p.setPen(QColor(T(tok)))
    p.drawText(rect, Qt.AlignCenter, text)
    p.restore()


SEGS = Qt.UserRole + 2           # 名字分段 [(文字, 0 一样 / 1 不一样 / None 标点)]


def diff_colors(side):
    """一样的字：正常颜色；不一样的：旧名字红、新名字蓝（加粗）"""
    return T("text"), T("err" if side == "old" else "accent")


def draw_segs(p, rect, segs, font, side, align=Qt.AlignVCenter):
    """按段画名字（一样 / 不一样两种颜色），画不下的地方写 …"""
    same_c, diff_c = diff_colors(side)
    fb = QFont(font)
    fb.setWeight(QFont.Bold)
    x, right = rect.left(), rect.right()
    ell = p.fontMetrics().horizontalAdvance("…")
    for n, (t, k) in enumerate(segs):
        f = fb if k == 1 else font
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(t)
        if x + w > right - (ell if n < len(segs) - 1 else 0):
            p.setFont(font)
            p.setPen(QColor(T("muted")))
            p.drawText(QRect(x, rect.top(), right - x + 1, rect.height()), Qt.AlignLeft | align, "…")
            break
        p.setPen(QColor(diff_c if k == 1 else same_c))
        p.drawText(QRect(x, rect.top(), w + 1, rect.height()), Qt.AlignLeft | align, t)
        x += w
    p.setFont(font)


def segs_html(segs, side):
    import html
    same_c, diff_c = diff_colors(side)
    return "".join(f"<span style='color:{diff_c}; font-weight:800'>{html.escape(t)}</span>" if k == 1
                   else f"<span style='color:{same_c}'>{html.escape(t)}</span>" for t, k in segs)


def _name_delegate(view, ean_col, settings, side):
    """商品名那一格：左边小图（ThumbDelegate）+ 名字按段上色"""
    import product_images as PI

    class D(PI.ThumbDelegate):
        def initStyleOption(self, o, idx):
            super().initStyleOption(o, idx)
            if idx.data(SEGS):
                o.text = ""

        def paint(self, p, opt, idx):
            super().paint(p, opt, idx)
            segs = idx.data(SEGS)
            if not segs:
                return
            on = self.store.enabled and idx.sibling(idx.row(), self.ean_col).data()
            r = opt.rect.adjusted((self.size + 10 if on else 0) + 6, 0, -6, 0)
            p.save()
            draw_segs(p, r, segs, QFont(opt.font), side)
            p.restore()
    return D(view, ean_col, THUMB, settings)


class _ScoreDelegate(QStyledItemDelegate):
    """「像不像」那一格画成彩色小标签（数字在 UserRole+1）"""

    def paint(self, p, opt, idx):
        o = QStyleOptionViewItem(opt)
        self.initStyleOption(o, idx)
        o.text = ""
        w = opt.widget
        (w.style() if w else QApplication.style()).drawControl(QStyle.CE_ItemViewItem, o, p, w)
        v = idx.data(Qt.UserRole + 1)
        if v is None:
            return
        r = opt.rect
        pr = QRect(r.center().x() - 26, r.center().y() - 11, 52, 22)
        _pill(p, pr, f"{v}%", tone(v))


class _CandDelegate(QStyledItemDelegate):
    """候选列表一项画成一张卡片：小图 · 名称（一行）· 条码 / 规格 / 几家 / 最低价（一行小字）· 右边像不像标签"""
    H, H_NONE, IMG = 62, 40, 44

    def __init__(self, view, settings):
        super().__init__(view)
        import product_images as PI
        self.view, self.store = view, PI.store(settings)
        self.store.ready.connect(lambda _e: self._repaint())

    def _repaint(self):
        try:
            self.view.viewport().update()
        except RuntimeError:
            pass

    def sizeHint(self, opt, idx):
        d = idx.data(Qt.UserRole)
        return QSize(opt.rect.width(), self.H_NONE if not d else self.H)

    def paint(self, p, opt, idx):
        d = idx.data(Qt.UserRole)
        r = opt.rect.adjusted(2, 2, -2, -2)
        sel = bool(opt.state & QStyle.State_Selected)
        hov = bool(opt.state & QStyle.State_MouseOver)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(T("accent") if sel else T("line")), 1.4 if sel else 1))
        p.setBrush(QColor(T("accent_soft") if sel else T("bg") if hov else T("surface")))
        p.drawRoundedRect(QRectF(r), 8, 8)
        if sel:                                           # 选中的：左边一条蓝
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T("accent")))
            p.drawRoundedRect(QRectF(r.left(), r.top() + 8, 3, r.height() - 16), 1.5, 1.5)
        f0 = QFont(opt.font)
        if not d:                                         # 「— 不替代」/「这周报价里没有」
            p.setPen(QColor(T("muted") if idx.flags() & Qt.ItemIsEnabled else T("faint")))
            p.setFont(f0)
            p.drawText(r.adjusted(14, 0, -10, 0), Qt.AlignVCenter | Qt.AlignLeft, idx.data(Qt.DisplayRole) or "")
            p.restore()
            return
        # 图
        box = QRect(r.left() + 10, r.top() + (r.height() - self.IMG) // 2, self.IMG, self.IMG)
        dpr = self.view.devicePixelRatioF() if hasattr(self.view, "devicePixelRatioF") else None
        pm = self.store.pixmap(d["ean"], self.IMG, dpr) if self.store.enabled else None
        if pm is not None:
            pw, ph = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
            p.drawPixmap(int(box.left() + (self.IMG - pw) / 2), int(box.top() + (self.IMG - ph) / 2), pm)
        else:
            p.setPen(Qt.NoPen)
            c = QColor(T("line"))
            c.setAlpha(110)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(box), 6, 6)
        # 右边像不像
        v = pct(d)
        pill = QRect(r.right() - 62, r.top() + (r.height() - 22) // 2, 52, 22)
        _pill(p, pill, f"{v}%", tone(v))
        # 两行字
        x = box.right() + 12
        tw = pill.left() - 10 - x
        f1 = QFont(f0)
        f1.setPointSizeF(max(8.0, f0.pointSizeF() * 1.02))
        f1.setWeight(QFont.DemiBold)
        p.setFont(f1)
        segs = idx.data(SEGS)
        if segs:
            f1.setWeight(QFont.Normal)
            draw_segs(p, QRect(x, r.top() + 9, tw, 22), segs, f1, "new")
        else:
            p.setPen(QColor(T("text")))
            name = p.fontMetrics().elidedText(d["name"], Qt.ElideRight, tw)
            p.drawText(QRect(x, r.top() + 9, tw, 22), Qt.AlignLeft | Qt.AlignVCenter, name)
        f2 = QFont(f0)
        f2.setPointSizeF(max(7.5, f0.pointSizeF() * 0.92))
        p.setFont(f2)
        y = r.top() + 33
        fm = p.fontMetrics()
        parts = []
        size, changed = d.get("_chg") or ("", False)
        if size:
            parts.append((size, T("accent") if changed else T("muted")))
        if d.get("in_grid"):
            parts.insert(0, (tr("Griglia 里已有"), T("warn")))
        parts.append((tr("{0} 家", d["n"]), T("muted")))
        parts.append((_money(d["best"]) + " · " + str(d["best_sup"]), T("muted")))
        parts.append((d["ean"], T("faint")))
        for k, (t, col) in enumerate(parts):
            if k:
                p.setPen(QColor(T("faint")))
                p.drawText(x, y, 14, 18, Qt.AlignCenter, "·")
                x += 14
            w = fm.horizontalAdvance(t)
            if x + w > pill.left() - 10:
                break
            p.setPen(QColor(col))
            p.drawText(x, y, w + 2, 18, Qt.AlignLeft | Qt.AlignVCenter, t)
            x += w
        p.restore()


class _Card(QFrame):
    """右上角并排的商品卡：大图 + 名称 + 一两行小字"""

    def __init__(self, caption, settings):
        super().__init__()
        self.settings = settings
        self.setObjectName("Panel")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(6)
        self.cap = QLabel(caption)
        self.cap.setObjectName("Faint")
        v.addWidget(self.cap)
        self.img = QLabel()
        self.img.setFixedSize(104, 104)
        self.img.setAlignment(Qt.AlignCenter)
        v.addWidget(self.img, 0, Qt.AlignHCenter)
        self.name = QLabel("")
        self.name.setWordWrap(True)
        self.name.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.name.setStyleSheet("font-weight:600;")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.name.setMinimumHeight(38)
        self.name.setTextFormat(Qt.RichText)
        v.addWidget(self.name)
        self.meta = QLabel("")
        self.meta.setObjectName("Faint")
        self.meta.setWordWrap(True)
        self.meta.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.meta)
        v.addStretch()

    def set_name(self, html_or_text, faint=False):
        import html
        t = html_or_text if html_or_text.startswith("<") else html.escape(html_or_text)
        self.name.setText(t)
        self.name.setStyleSheet(f"font-weight:600; color:{T('faint') if faint else T('text')};")

    def set(self, ean, name, meta, faint=False):
        import product_images as PI
        if ean:
            PI.set_label_image(self.img, ean, 100, self.settings)
            if self.img.isHidden() or self.img.pixmap() is None or self.img.pixmap().isNull():
                self.img.setText(tr("没有图片"))
                self.img.setStyleSheet(f"color:{T('faint')}; border:1px dashed {T('line')}; border-radius:8px;")
            else:
                self.img.setStyleSheet("")
            self.img.show()
        else:
            self.img.clear()
            self.img.setText("—")
            self.img.setStyleSheet(f"color:{T('faint')}; border:1px dashed {T('line')}; border-radius:8px;")
        self.set_name(name, faint)
        self.meta.setText(meta)


class ReplaceDialog(QDialog):
    def __init__(self, parent, settings, grid, sugg, pool):
        super().__init__(parent)
        import griglia_replace as GR
        self.GR = GR
        self.settings, self.grid, self.rows, self.pool = settings, grid, sugg, pool
        self.result_info = None
        self.choice = {i: (r["cands"][0] if r["cands"] else None) for i, r in enumerate(sugg)}
        # 默认勾相似度 ≥ 80% 的（2026-11-11 用户：停售就是停售，Griglia 里划没划线只影响怎么改那一行）
        self.checked = {i for i, r in enumerate(sugg) if r["cands"] and r["cands"][0]["score"] >= GR.SURE}
        self._filling = False
        self.setWindowTitle(tr("找替代商品"))
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(12)
        # 头
        head = QHBoxLayout()
        from erp_widgets import tip_label
        title = tip_label(tr("找替代商品"), tr(
            "停售 = Griglia 里的商品，这周几家批发商的报价里都没有了（Griglia 里已经划线的也算，还有批发商在卖的不算）。\n"
            "程序在这周报价里找名字、品牌、规格最相似的（多半是换了规格：40 洗 → 50 洗、950 ml → 1000 ml）。"
            "相似度 ≥ 80% 的先勾上，都要看一眼再替换。\n"
            "替换进 Griglia：原来那一行换成新商品（分类 / 品牌 / 门店勾选照旧，条码 / 名称 / 箱规 / 价格 / 供应商换成新的、"
            "编号空着，改过的格子浅蓝底），原商品那一行抄到文件最下面、划线停用。新商品 Griglia 里已经有的，只把原商品那一行移到最下面。"
            "Griglia 里已经划线的也一样：那一行换成新商品，原商品抄到文件最下面划线。\n"
            "替换过的商品，以后门店清单里再有旧条码，检查清单时自动换成新商品去采购。"))
        title.setStyleSheet("font-size:15px; font-weight:700;")
        head.addWidget(title)
        found = sum(1 for r in sugg if r["cands"])
        sub = QLabel(tr("{0} 个停售 · {1} 个有相似商品", len(sugg), found))
        sub.setObjectName("Faint")
        head.addSpacing(10)
        head.addWidget(sub)
        head.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("条码或名称"))
        self.search.setFixedWidth(220)
        self.search.setClearButtonEnabled(True)
        head.addWidget(self.search)
        self.show_cb = QComboBox()
        for text, k in ((tr("全部"), "all"), (tr("有相似商品"), "found"), (tr("没有相似商品"), "none"), (tr("已勾选"), "on")):
            self.show_cb.addItem(text, k)
        head.addWidget(self.show_cb)
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        head.addWidget(self.count)
        lay.addLayout(head)
        # 左表格
        self.t = QTableWidget(0, len(COLS))
        self.t.setHorizontalHeaderLabels([tr(h) if h else "" for _f, h, _w in COLS])
        self.t.verticalHeader().setVisible(False)
        self.t.verticalHeader().setDefaultSectionSize(ROW_H)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.t.setSelectionMode(QAbstractItemView.SingleSelection)
        self.t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t.setShowGrid(False)
        self.t.setWordWrap(False)
        self.t.setTextElideMode(Qt.ElideRight)
        hh = self.t.horizontalHeader()
        for c, (_f, _h, w) in enumerate(COLS):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.t.setColumnWidth(c, w)
        hh.setStretchLastSection(False)
        self.t.horizontalHeaderItem(F["pick"]).setToolTip(tr("勾上的替换进 Griglia"))
        self.t.horizontalHeaderItem(F["score"]).setToolTip(
            tr("名字、品牌、条码前缀（同一个厂家）、规格合起来算的，100% = 很像。只是参考，要人看"))
        self.t.horizontalHeaderItem(F["old"]).setToolTip(tr("红字 = 新商品名字里没有的（多半就是差别：规格、款式、香型）"))
        self.t.horizontalHeaderItem(F["new"]).setToolTip(tr("蓝字 = 旧商品名字里没有的；黑字 = 两个名字一样的部分"))
        self.t.horizontalHeaderItem(F["size"]).setToolTip(tr("蓝字 = 换了规格（40 → 50 lav）"))
        self.t.setItemDelegateForColumn(F["old"], _name_delegate(self.t, F["old_ean"], settings, "old"))
        self.t.setItemDelegateForColumn(F["new"], _name_delegate(self.t, F["new_ean"], settings, "new"))
        self.t.setItemDelegateForColumn(F["score"], _ScoreDelegate(self.t))
        from erp_widgets import TableSortFilter
        import settings as ST
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, key=self._key,
                                   hide=self._hidden, numeric=NUM)
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        self.ctl.remember(settings.setdefault("griglia_cols", {}), "replace", lambda: ST.save_settings(settings),
                          [f for f, _h, _w in COLS], default_hidden=["old_ean", "new_ean"], ver=5)
        self.t.viewport().installEventFilter(self)
        self.t.itemChanged.connect(self._item_changed)
        self.t.itemSelectionChanged.connect(self._show)
        self.search.textChanged.connect(lambda _: self.ctl.render())
        self.show_cb.currentIndexChanged.connect(lambda _: self.ctl.render())
        # 右：旧 → 新 两张卡并排 + 规格 / 像不像一行 + 候选卡片
        side = QFrame()
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(10)
        pair = QHBoxLayout()
        pair.setSpacing(8)
        self.c_old = _Card(tr("原商品"), settings)
        self.c_new = _Card(tr("替代成"), settings)
        arrow = QLabel("→")
        arrow.setStyleSheet(f"font-size:22px; color:{T('faint')};")
        pair.addWidget(self.c_old, 1)
        pair.addWidget(arrow, 0, Qt.AlignVCenter)
        pair.addWidget(self.c_new, 1)
        sv.addLayout(pair)
        self.sum = QLabel("")
        self.sum.setAlignment(Qt.AlignCenter)
        self.sum.setTextFormat(Qt.RichText)
        sv.addWidget(self.sum)
        ch = QHBoxLayout()
        self.cap = QLabel(tr("候选"))
        self.cap.setStyleSheet("font-weight:600;")
        ch.addWidget(self.cap)
        ch.addStretch()
        self.q = QLineEdit()
        self.q.setPlaceholderText(tr("在这周报价里搜…"))
        self.q.setClearButtonEnabled(True)
        self.q.setToolTip(tr("程序找的都不对：输入名称里的词或条码，在这周所有批发商报价（Griglia 里没有的商品）里找"))
        self.q.textChanged.connect(lambda _: self._fill_cands())
        self.q.setMinimumWidth(220)
        ch.addWidget(self.q, 1)
        sv.addLayout(ch)
        self.cands = QListWidget()
        self.cands.setFrameShape(QFrame.NoFrame)
        self.cands.setSpacing(2)
        self.cands.setMouseTracking(True)
        self.cands.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.cands.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cands.setStyleSheet("QListWidget{background:transparent;} QListWidget::item{border:none; background:transparent;}")
        self.cands.setItemDelegate(_CandDelegate(self.cands, settings))
        self.cands.itemClicked.connect(self._picked)
        sv.addWidget(self.cands, 1)
        side.setMinimumWidth(380)
        sp = QSplitter(Qt.Horizontal)
        sp.addWidget(self.t)
        sp.addWidget(side)
        sp.setStretchFactor(0, 3)
        sp.setStretchFactor(1, 2)
        sp.setChildrenCollapsible(False)
        sp.setHandleWidth(14)
        sp.setSizes([880, 440])                 # 表格多一点；右边够放两张卡
        lay.addWidget(sp, 1)
        # 底
        foot = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        foot.addWidget(self.status, 1)
        for text, fn, tip in ((tr("全部不勾"), lambda: self._check_all(False), ""),
                              (tr("勾上 ≥ 80%"), lambda: self._check_all(True), tr("相似度 ≥ 80% 的都勾上"))):
            b = QPushButton(text)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(fn)
            foot.addWidget(b)
        foot.addSpacing(12)
        cancel = QPushButton(tr("关闭"))
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        self.b_add = QPushButton("")
        self.b_add.setObjectName("Primary")
        self.b_add.clicked.connect(self._add)
        foot.addWidget(self.b_add)
        lay.addLayout(foot)
        self.resize(1320, 780)
        from erp_widgets import fit_to_screen
        fit_to_screen(self)
        self.ctl.set_sort(F["score"], desc=True)
        self.ctl.render()
        if self.rows:
            self.t.selectRow(0)
        self._update_foot()

    def eventFilter(self, obj, ev):
        """窗口放大（最大化）时表格多出来的宽度平分给两列商品名，不留一块空白"""
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Resize and obj is self.t.viewport():
            hh = self.t.horizontalHeader()
            used = sum(self.t.columnWidth(c) for c in range(self.t.columnCount()) if not hh.isSectionHidden(c))
            free = obj.width() - used
            if free > 4:
                for c in (F["old"], F["new"]):
                    self.t.setColumnWidth(c, self.t.columnWidth(c) + free // 2)
        return False

    # ------------------------------------------------------------ 表格
    def _chg(self, i, c=None):
        """规格变化（缓存在候选里）"""
        c = c if c is not None else self.choice.get(i)
        if not c:
            return "", False
        if "_chg" not in c:
            c["_chg"] = self.GR.change(self.rows[i]["old"].get("name") or "", c["name"])
        return c["_chg"]

    def _text(self, i, c):
        r, ch = self.rows[i], self.choice.get(i)
        f = COLS[c][0]
        old = r["old"]
        if f == "pick":
            return "✓" if i in self.checked else ""
        if f == "old":
            return old.get("name") or old["ean"]
        if f == "new":
            return ch["name"] if ch else (tr("— 不替代") if r["cands"] else tr("— 没找到像的"))
        if f == "size":
            return self._chg(i)[0]
        if f == "score":
            return f"{pct(ch)}%" if ch else ""
        if f == "n":
            return str(ch["n"]) if ch else ""
        if f == "best":
            return _money(ch["best"]) if ch else ""
        if f == "old_ean":
            return old["ean"]
        if f == "new_ean":
            return ch["ean"] if ch else ""
        return ""

    def _key(self, i, c):
        ch = self.choice.get(i)
        f = COLS[c][0]
        if f == "score":
            return (ch or {}).get("score", -1)
        if f == "n":
            return (ch or {}).get("n", 0)
        if f == "best":
            return (ch or {}).get("best") or 0
        if f == "pick":
            return 0 if i in self.checked else 1
        return self._text(i, c).lower()

    def _hidden(self, i):
        r = self.rows[i]
        q = self.search.text().strip().lower()
        if q:
            ch = self.choice.get(i) or {}
            hay = " ".join([r["old"]["ean"], r["old"].get("name") or "", ch.get("ean", ""), ch.get("name", "")]).lower()
            if q not in hay:
                return True
        k = self.show_cb.currentData()
        return (k == "found" and not r["cands"]) or (k == "none" and r["cands"]) or (k == "on" and i not in self.checked)

    def _fill(self, i, k):
        self._filling = True
        try:
            ch = self.choice.get(i)
            old = self.rows[i]["old"]
            for c in range(len(COLS)):
                f = COLS[c][0]
                it = QTableWidgetItem("" if f in ("pick", "score") else self._text(i, c))
                it.setData(Qt.UserRole, i)
                if f == "pick":
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.ItemIsUserCheckable if ch else Qt.NoItemFlags))
                    it.setCheckState(Qt.Checked if i in self.checked else Qt.Unchecked)
                    if not ch:
                        it.setToolTip(tr("先在右边选一个替代品"))
                elif f in ("n", "best"):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif f == "score":
                    it.setData(Qt.UserRole + 1, pct(ch) if ch else None)
                if f == "old":
                    it.setToolTip(f"{old.get('name') or ''}\n{old['ean']}" + (
                        "\n\n" + tr("Griglia 第 {0} 行已经划线：替换时这一行换成新商品，原商品抄到最下面划线", old.get("row") or "?")
                        if old.get("struck") else ""))
                    if ch:
                        it.setData(SEGS, self.GR.diff_tokens(old.get("name") or "", ch["name"]))
                elif f == "new":
                    if ch:
                        it.setToolTip(f"{ch['name']}\n{ch['ean']}")
                        it.setData(SEGS, self.GR.diff_tokens(ch["name"], old.get("name") or ""))
                    else:
                        it.setForeground(QColor(T("faint")))
                elif f == "size":
                    txt, changed = self._chg(i)
                    if changed:
                        it.setForeground(QColor(T("accent")))       # 规格变了（40 → 50 洗）：蓝字
                    elif txt:
                        it.setForeground(QColor(T("muted")))
                elif f in ("old_ean", "new_ean"):
                    it.setForeground(QColor(T("muted")))
                self.t.setItem(k, c, it)
        finally:
            self._filling = False

    def _row_of(self, k):
        it = self.t.item(k, 0)
        return it.data(Qt.UserRole) if it else None

    def _current(self):
        sel = self.t.selectionModel().selectedRows() if self.t.selectionModel() else []
        return self._row_of(sel[0].row()) if sel else None

    def _item_changed(self, it):
        if self._filling or it.column() != F["pick"]:
            return
        i = it.data(Qt.UserRole)
        if i is None:
            return
        on = it.checkState() == Qt.Checked
        if on and self.choice.get(i):
            self.checked.add(i)
        else:
            self.checked.discard(i)
        self._update_foot()

    def _refresh_row(self, i):
        k = self.ctl.pos.get(i)
        if k is not None:
            self._fill(i, k)

    def _check_all(self, on):
        if on:
            self.checked |= {i for i, c in self.choice.items() if c and c["score"] >= self.GR.SURE}
        else:
            self.checked.clear()
        for i in range(len(self.rows)):
            self._refresh_row(i)
        self._update_foot()

    def _update_foot(self):
        n = len(self.checked)
        self.b_add.setText(tr("替换进 Griglia（{0}）", n))
        self.b_add.setEnabled(n > 0)
        changed = sum(1 for i in self.checked if self._chg(i)[1])
        self.status.setText(tr("勾了 {0} 个 · 其中 {1} 个换了规格", n, changed) if n else tr("勾上要替换的"))

    # ------------------------------------------------------------ 右边
    def _show(self):
        i = self._current()
        if i is None:
            return
        old = self.rows[i]["old"]
        bits = [old["ean"]]
        if old.get("pack"):
            bits.append(tr("{0} 件/箱", _pack(old["pack"])))
        p = old.get("best") or old.get("price")
        if p:
            bits.append(_money(p))
        meta = " · ".join(str(b) for b in bits)
        if old.get("supplier"):
            meta += "\n" + str(old["supplier"])
        self.c_old.set(old["ean"], old.get("name") or "", meta)
        self._show_new(i)
        self.q.blockSignals(True)
        self.q.clear()
        self.q.blockSignals(False)
        self._fill_cands()

    def _show_new(self, i):
        ch = self.choice.get(i)
        if not ch:
            self.c_old.set_name(self.rows[i]["old"].get("name") or "")
            self.c_new.set(None, tr("— 不替代") if self.rows[i]["cands"] else tr("— 没找到像的"), "", faint=True)
            self.sum.setText("")
            return
        bits = [ch["ean"]]
        if ch.get("pack"):
            bits.append(tr("{0} 件/箱", _pack(ch["pack"])))
        prices = sorted((ch.get("prices") or {}).items(), key=lambda kv: kv[1])
        meta = " · ".join(bits) + "\n" + " · ".join(f"{k} {_money(v)}" for k, v in prices)
        oname = self.rows[i]["old"].get("name") or ""
        self.c_new.set(ch["ean"], segs_html(self.GR.diff_tokens(ch["name"], oname), "new"), meta)
        self.c_old.set_name(segs_html(self.GR.diff_tokens(oname, ch["name"]), "old"))
        txt, changed = self._chg(i)
        v = pct(ch)
        parts = []
        if txt:
            parts.append(f"<span style='color:{T('accent') if changed else T('muted')}; font-weight:600'>{txt}</span>")
        parts.append(f"<span style='color:{T(tone(v))}; font-weight:700'>{tr('相似度')} {v}%</span>")
        if ch.get("fresh"):
            parts.append(f"<span style='color:{T('info')}'>{tr('这周新上架的')}</span>")
        if ch.get("in_grid"):
            parts.append(f"<span style='color:{T('warn')}; font-weight:600'>{tr('Griglia 里已有 · 只把旧行移到最下面')}</span>")
        self.sum.setText(f"&nbsp;&nbsp;<span style='color:{T('faint')}'>·</span>&nbsp;&nbsp;".join(parts))

    def _fill_cands(self):
        i = self._current()
        self.cands.clear()
        if i is None:
            return
        old = self.rows[i]["old"]
        q = self.q.text().strip()
        if len(q) >= 2:
            hits = self.GR.search(self.pool, q)
            cands = sorted((self.GR.rescore(old, c) for c in hits if c["ean"] != old["ean"]), key=lambda c: -c["score"])[:80]
            self.cap.setText(tr("搜到 {0} 个", len(cands)))
            if not cands:
                it = QListWidgetItem(tr("这周报价里没有"))
                it.setFlags(Qt.NoItemFlags)
                self.cands.addItem(it)
                return
        else:
            cands = self.rows[i]["cands"]
            self.cap.setText(tr("候选（{0}）", len(cands)))
        cands = [c for c in cands if c["ean"] != old["ean"]]     # 同一个商品不能当自己的替代品
        for c in cands:
            self._chg(i, c)
        none = QListWidgetItem(tr("— 不替代"))
        none.setData(Qt.UserRole, None)
        self.cands.addItem(none)
        cur = self.choice.get(i)
        for c in cands:
            it = QListWidgetItem(c["name"])
            it.setData(Qt.UserRole, c)
            it.setData(SEGS, self.GR.diff_tokens(c["name"], old.get("name") or ""))
            it.setToolTip("\n".join(c.get("names") or [c["name"]])
                          + ("\n\n" + tr("Griglia 里已经有这个商品：选它 = 旧的那行移到最下面划线，不再加新行") if c.get("in_grid") else ""))
            self.cands.addItem(it)
            if cur and cur["ean"] == c["ean"]:
                self.cands.setCurrentItem(it)
        if not cur:
            self.cands.setCurrentItem(none)

    def _picked(self, it):
        i = self._current()
        if i is None or not (it.flags() & Qt.ItemIsEnabled):
            return
        c = it.data(Qt.UserRole)
        self.choice[i] = c
        if c:
            self.checked.add(i)                 # 自己挑的：就是要加
        else:
            self.checked.discard(i)
        self._refresh_row(i)
        self._show_new(i)
        self._update_foot()

    # ------------------------------------------------------------ 写进 Griglia
    def _add(self):
        import os
        import commands as CMD
        from erp_widgets import BusyDialog
        picks = [(self.rows[i]["old"], self.choice[i]) for i in sorted(self.checked) if self.choice.get(i)]
        if not picks:
            return
        picks = [(o, {k: v for k, v in c.items() if not k.startswith("_")}) for o, c in picks]
        src = getattr(self, "source", "") or ""
        target = src if src and os.path.isfile(src) else self.grid["path"]
        name = os.path.basename(target)
        where = tr("同步文件夹里的原件") if target == src else tr("工作文件夹「0 Griglia」里的这份")
        lines = "\n".join(f"· {o.get('name') or o['ean']}  →  {c['name']}" for o, c in picks[:12])
        if len(picks) > 12:
            lines += "\n" + tr("…还有 {0} 个", len(picks) - 12)
        n_in = sum(1 for _o, c in picks if c.get("in_grid"))
        msg = tr("替换 Griglia 里 {0} 个商品：\n{1}\n\n改的文件：{2}（{3}）\n{4}\n\n"
                 "原来那一行换成新商品（改过的格子浅蓝底，编号空着）；旧商品那一行抄到文件最下面、划线。\n"
                 "其中 {5} 个新商品 Griglia 里已经有，只把旧行移到最下面。\n"
                 "改之前在程序文件夹存一份备份；先在本机副本上改，逐格核对：除了这几行别的格子都没变才放回，核对不过原文件不动。Excel / WPS 要关着，文件大，可能要几分钟。\n"
                 "同步文件夹里的文件同事都会看到。确定替换？", len(picks), lines, name, where, os.path.dirname(target), n_in)
        if QMessageBox.question(self, tr("替换进 Griglia"), msg) != QMessageBox.Yes:
            return
        import product_images as PI
        url = self.settings.get("image_url") or PI.DEFAULT_URL
        res, err = BusyDialog.run(self, tr("替换进 Griglia"), tr("正在备份并用 Excel 改 {0}…（文件大，请等一下）", name),
                                  lambda: CMD.griglia_add_replacements(self.grid, picks, source=src, url=url))
        if err:
            QMessageBox.warning(self, tr("替换进 Griglia"), tr("没替换：{0}", err))
            return
        self.result_info = res
        skipped = len(picks) - len(res["written"])
        if skipped:
            QMessageBox.information(self, tr("替换进 Griglia"),
                                    tr("替换了 {0} 个；{1} 个在 Griglia 里找不到旧条码（可能被人改过），没改", len(res["written"]), skipped))
        self.accept()


def open_replace(page):
    """Griglia 页「都不做了」那张表上的「找替代商品」按钮"""
    import commands as CMD
    import griglia_replace as GR
    from erp_widgets import BusyDialog
    grid, quotes = page.grid, getattr(page, "_quotes", None)
    if not grid or quotes is None:
        return
    def find():
        ready = getattr(page, "_repl_ready", None)
        if ready is not None:
            ready.wait(60)                        # 页面统计完在后台已经在算了：等它算完，不重复算
        pre = getattr(page, "_repl", None)
        if pre and pre[0] is grid and pre[1] is quotes:
            return pre[2]
        return (CMD.griglia_replacements(grid, quotes, getattr(page, "_fresh", None), struck="both"),
                GR.pool(grid, quotes))
    pre = getattr(page, "_repl", None)
    if pre and pre[0] is grid and pre[1] is quotes:      # 已经算好：直接打开，不弹进度
        res, err = pre[2], None
    else:
        res, err = BusyDialog.run(page.win, tr("找替代商品"), tr("正在这周报价里找相似的商品…"), find)
    if err:
        page.win.show_toast(tr("找替代商品失败：{0}", err))
        return
    sugg, pool = res
    if not sugg:
        page.win.show_toast(tr("没有要找替代的商品"))
        return
    dlg = ReplaceDialog(page.win, page.settings, grid, sugg, pool)
    dlg.source = page.source() if hasattr(page, "source") else ""
    dlg.exec()
    if dlg.result_info:
        n = len(dlg.result_info["written"])
        if hasattr(page.win, "log"):
            page.win.log(tr("Griglia 加了 {0} 个替代商品：{1}（备份：{2}）", n, dlg.result_info.get("file", ""),
                            dlg.result_info["backup"]))
        page.win.show_toast(tr("Griglia 替换了 {0} 个商品；已逐格核对，其余 {1} 个格子都没变", n,
                               dlg.result_info.get("checked", 0)))
        page.refresh()


class ReplaceLogDialog(QDialog):
    """替换记录（用户 11-09：程序里要有替换记录，不然不知道改了哪个）：0 Griglia/_替代记录.json，新的在上面"""
    COLS = [("at", "时间", 128), ("by", "谁", 70), ("old_name", "原商品", 280), ("old", "旧条码", 116),
            ("name", "替代成", 280), ("new", "新条码", 116), ("size", "规格", 110), ("in", "在 Griglia 里", 92),
            ("file", "文件 · 行", 220)]

    def __init__(self, parent, settings, done, grid):
        super().__init__(parent)
        import os
        self.setWindowTitle(tr("替换记录"))
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        items = (grid or {}).get("items") or {}
        self.rows = []
        for old, r in sorted(done.items(), key=lambda kv: kv[1].get("at") or "", reverse=True):
            f = r.get("file") or ""
            self.rows.append({"at": r.get("at") or "", "by": r.get("by") or "", "old": old,
                              "old_name": r.get("old_name") or (items.get(old) or {}).get("name") or "",
                              "new": r.get("new") or "", "name": r.get("name") or "", "size": r.get("size") or "",
                              "in": tr("在") if r.get("new") in items else tr("不在"),
                              "file": (os.path.basename(f) + (f" · {r['row']}" if r.get("row") else "")
                                       + (f" ← {r['moved_to']}" if r.get("moved_to") else "")) if f else "",
                              "_path": f})
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        from erp_widgets import tip_label, TableSortFilter
        title = tip_label(tr("替换记录"), tr("「找替代商品」替换进 Griglia 的每一条：哪天、谁、旧商品 → 新商品、改的哪个文件第几行 ← 旧行挪到第几行（行号是改的那天的）。"
                                         "「在 Griglia 里」= 现在程序读的这份 Griglia 里有没有新条码（换了一份没加过的 Griglia，旧商品又会算「停售」）"))
        title.setStyleSheet("font-size:15px; font-weight:700;")
        head.addWidget(title)
        head.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("条码或名称"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        head.addWidget(self.search)
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        head.addWidget(self.count)
        lay.addLayout(head)
        self.t = QTableWidget(0, len(self.COLS))
        self.t.setHorizontalHeaderLabels([tr(h) for _f, h, _w in self.COLS])
        self.t.verticalHeader().setVisible(False)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t.setShowGrid(False)
        self.t.verticalHeader().setDefaultSectionSize(34)
        hh = self.t.horizontalHeader()
        for c, (_f, _h, w) in enumerate(self.COLS):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.t.setColumnWidth(c, w)
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, hide=self._hidden)
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        import settings as ST
        self.ctl.remember(settings.setdefault("griglia_cols", {}), "replace_log", lambda: ST.save_settings(settings),
                          [f for f, _h, _w in self.COLS])
        self.search.textChanged.connect(lambda _: self.ctl.render())
        lay.addWidget(self.t, 1)
        foot = QHBoxLayout()
        foot.addStretch()
        self.b_open = QPushButton(tr("打开文件"))
        self.b_open.setToolTip(tr("用 Excel 打开选中那一条改的 Griglia"))
        self.b_open.clicked.connect(self._open)
        foot.addWidget(self.b_open)
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        lay.addLayout(foot)
        self.resize(1300, 560)
        from erp_widgets import fit_to_screen
        fit_to_screen(self)
        self.ctl.render()

    def _text(self, i, c):
        return str(self.rows[i].get(self.COLS[c][0]) or "")

    def _hidden(self, i):
        q = self.search.text().strip().lower()
        r = self.rows[i]
        return bool(q) and q not in " ".join((r["old"], r["old_name"], r["new"], r["name"])).lower()

    def _fill(self, i, k):
        r = self.rows[i]
        for c, (f, _h, _w) in enumerate(self.COLS):
            it = QTableWidgetItem(self._text(i, c))
            it.setData(Qt.UserRole, i)
            if f == "old_name":
                it.setForeground(QColor(T("err")))
            elif f == "name":
                it.setForeground(QColor(T("accent")))
            elif f == "in" and r["in"] != tr("在"):
                it.setForeground(QColor(T("warn")))
            elif f in ("old", "new", "at", "by", "file"):
                it.setForeground(QColor(T("muted")))
            if f == "file" and r["_path"]:
                it.setToolTip(r["_path"])
            self.t.setItem(k, c, it)

    def _open(self):
        sel = self.t.selectionModel().selectedRows()
        it = self.t.item(sel[0].row(), 0) if sel else None
        i = it.data(Qt.UserRole) if it else None
        p = self.rows[i]["_path"] if i is not None else ""
        import os
        if p and os.path.isfile(p):
            from erp_widgets import open_file
            open_file(self, p)
        else:
            QMessageBox.information(self, tr("替换记录"), tr("找不到这个文件：{0}", p or "—"))
