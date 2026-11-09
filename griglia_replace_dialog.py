"""
Griglia「都不做了」→ 找替代商品（2026-11-09 用户：很多不做了是换了规格，40 洗 → 50 洗、950 ml → 1000 ml，
在新报价里找出来让我核对，再加进 Griglia；用户定：下面加一行新的，旧的留着）。
  左：一行一个不做了的商品（勾选 · 不做了的（小图）· 替代成（小图）· 规格 · 像不像 · 几家 · 最低价），TableSortFilter
  右：选中那一行：旧 → 新 两张商品卡并排（大图），下面候选卡片列表（「不替代」+ 程序找的 + 搜这周报价）
  下：勾了几个 ·「加进 Griglia（n）」先确认 → commands.griglia_add_replacements（备份 + Excel 插行）
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
COLS = [("pick", "", 34), ("old", "不做了的商品", 260), ("new", "替代成", 260), ("size", "规格", 126),
        ("score", "像不像", 76), ("n", "几家", 52), ("best", "最低价", 76),
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
        v.addWidget(self.name)
        self.meta = QLabel("")
        self.meta.setObjectName("Faint")
        self.meta.setWordWrap(True)
        self.meta.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.meta)
        v.addStretch()

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
        self.name.setText(name)
        self.name.setStyleSheet(f"font-weight:600; color:{T('faint') if faint else T('text')};")
        self.meta.setText(meta)


class ReplaceDialog(QDialog):
    def __init__(self, parent, settings, grid, sugg, pool):
        super().__init__(parent)
        import griglia_replace as GR
        self.GR = GR
        self.settings, self.grid, self.rows, self.pool = settings, grid, sugg, pool
        self.result_info = None
        self.choice = {i: (r["cands"][0] if r["cands"] else None) for i, r in enumerate(sugg)}
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
            "Griglia 里这周所有批发商都不做了的商品，在这周报价里找名字、品牌、规格最像的（多半是换了规格：40 洗 → 50 洗、"
            "950 ml → 1000 ml）。像不像 ≥ 80% 的先勾上，都要看一眼再加。\n"
            "加进 Griglia：每个旧商品下面插一行新的（浅蓝底，分类 / 品牌 / 门店勾选照抄旧的，条码 / 名称 / 箱规 / 价格 / 供应商换成新的、"
            "编号空着），旧的那行留着、字变灰。加过的以后不再算「都不做了」"))
        title.setStyleSheet("font-size:15px; font-weight:700;")
        head.addWidget(title)
        found = sum(1 for r in sugg if r["cands"])
        sub = QLabel(tr("{0} 个都不做了 · {1} 个找到像的", len(sugg), found))
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
        for text, k in ((tr("全部"), "all"), (tr("找到像的"), "found"), (tr("没找到"), "none"), (tr("已勾选"), "on")):
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
        self.t.horizontalHeaderItem(F["pick"]).setToolTip(tr("勾上的加进 Griglia"))
        self.t.horizontalHeaderItem(F["score"]).setToolTip(
            tr("名字、品牌、条码前缀（同一个厂家）、规格合起来算的，100% = 很像。只是参考，要人看"))
        self.t.horizontalHeaderItem(F["size"]).setToolTip(tr("蓝字 = 换了规格（40 → 50 lav）"))
        import product_images as PI
        self.t.setItemDelegateForColumn(F["old"], PI.ThumbDelegate(self.t, F["old_ean"], THUMB, settings))
        self.t.setItemDelegateForColumn(F["new"], PI.ThumbDelegate(self.t, F["new_ean"], THUMB, settings))
        self.t.setItemDelegateForColumn(F["score"], _ScoreDelegate(self.t))
        from erp_widgets import TableSortFilter
        import settings as ST
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, key=self._key,
                                   hide=self._hidden, numeric=NUM)
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        self.ctl.remember(settings.setdefault("griglia_cols", {}), "replace", lambda: ST.save_settings(settings),
                          [f for f, _h, _w in COLS], default_hidden=["old_ean", "new_ean"], ver=2)
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
        self.c_old = _Card(tr("不做了的"), settings)
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
                              (tr("勾上 ≥ 80%"), lambda: self._check_all(True), tr("像不像 ≥ 80% 的都勾上"))):
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
        return ((k == "found" and not r["cands"]) or (k == "none" and r["cands"]) or (k == "on" and i not in self.checked))

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
                    it.setToolTip(f"{old.get('name') or ''}\n{old['ean']}")
                elif f == "new":
                    if ch:
                        it.setToolTip(f"{ch['name']}\n{ch['ean']}")
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
        self.b_add.setText(tr("加进 Griglia（{0}）", n))
        self.b_add.setEnabled(n > 0)
        changed = sum(1 for i in self.checked if self._chg(i)[1])
        self.status.setText(tr("勾了 {0} 个 · 其中 {1} 个换了规格", n, changed) if n else tr("勾上要加进 Griglia 的"))

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
            self.c_new.set(None, tr("— 不替代") if self.rows[i]["cands"] else tr("— 没找到像的"), "", faint=True)
            self.sum.setText("")
            return
        bits = [ch["ean"]]
        if ch.get("pack"):
            bits.append(tr("{0} 件/箱", _pack(ch["pack"])))
        prices = sorted((ch.get("prices") or {}).items(), key=lambda kv: kv[1])
        meta = " · ".join(bits) + "\n" + " · ".join(f"{k} {_money(v)}" for k, v in prices)
        self.c_new.set(ch["ean"], ch["name"], meta)
        txt, changed = self._chg(i)
        v = pct(ch)
        parts = []
        if txt:
            parts.append(f"<span style='color:{T('accent') if changed else T('muted')}; font-weight:600'>{txt}</span>")
        parts.append(f"<span style='color:{T(tone(v))}; font-weight:700'>{tr('像不像')} {v}%</span>")
        if ch.get("fresh"):
            parts.append(f"<span style='color:{T('info')}'>{tr('这周新上架的')}</span>")
        self.sum.setText(f"&nbsp;&nbsp;<span style='color:{T('faint')}'>·</span>&nbsp;&nbsp;".join(parts))

    def _fill_cands(self):
        i = self._current()
        self.cands.clear()
        if i is None:
            return
        old = self.rows[i]["old"]
        q = self.q.text().strip()
        if len(q) >= 2:
            ws = list(self.GR.words(q)) or [q.upper()]
            hits = [c for c in self.pool if c["ean"].startswith(q)
                    or all(w in " ".join(c.get("names") or [c["name"]]).upper() for w in ws)]
            cands = sorted((self.GR.rescore(old, c) for c in hits[:400]), key=lambda c: -c["score"])[:40]
            self.cap.setText(tr("搜到 {0} 个", len(cands)))
            if not cands:
                it = QListWidgetItem(tr("这周报价里没有"))
                it.setFlags(Qt.NoItemFlags)
                self.cands.addItem(it)
                return
        else:
            cands = self.rows[i]["cands"]
            self.cap.setText(tr("候选（{0}）", len(cands)))
        for c in cands:
            self._chg(i, c)
        none = QListWidgetItem(tr("— 不替代"))
        none.setData(Qt.UserRole, None)
        self.cands.addItem(none)
        cur = self.choice.get(i)
        for c in cands:
            it = QListWidgetItem(c["name"])
            it.setData(Qt.UserRole, c)
            it.setToolTip("\n".join(c.get("names") or [c["name"]]))
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
        name = os.path.basename(self.grid["path"])
        lines = "\n".join(f"· {o.get('name') or o['ean']}  →  {c['name']}" for o, c in picks[:12])
        if len(picks) > 12:
            lines += "\n" + tr("…还有 {0} 个", len(picks) - 12)
        msg = tr("在 Griglia（{0}）里加 {1} 行：\n{2}\n\n每个旧商品下面插一行新的（浅蓝底），旧的留着、字变灰；编号空着。\n"
                 "改之前先备份到「0 Griglia\\_备份」。Excel 要关着，文件大，可能要几分钟。\n"
                 "Griglia 在共用的工作文件夹里，同事都会看到。确定加？", name, len(picks), lines)
        if QMessageBox.question(self, tr("加进 Griglia"), msg) != QMessageBox.Yes:
            return
        res, err = BusyDialog.run(self, tr("加进 Griglia"), tr("正在用 Excel 改 {0}…（文件大，请等一下）", name),
                                  lambda: CMD.griglia_add_replacements(self.grid, picks))
        if err:
            QMessageBox.warning(self, tr("加进 Griglia"), tr("没加进去：{0}", err))
            return
        self.result_info = res
        skipped = len(picks) - len(res["written"])
        if skipped:
            QMessageBox.information(self, tr("加进 Griglia"),
                                    tr("加了 {0} 行；{1} 个在 Griglia 里找不到旧条码（可能被人改过），没加", len(res["written"]), skipped))
        self.accept()


def open_replace(page):
    """Griglia 页「都不做了」那张表上的「找替代商品」按钮"""
    import commands as CMD
    import griglia_replace as GR
    from erp_widgets import BusyDialog
    grid, quotes = page.grid, getattr(page, "_quotes", None)
    if not grid or quotes is None:
        return
    res, err = BusyDialog.run(page.win, tr("找替代商品"), tr("正在这周报价里找像的商品…"),
                              lambda: (CMD.griglia_replacements(grid, quotes, getattr(page, "_fresh", None)),
                                       GR.pool(grid, quotes)))
    if err:
        page.win.show_toast(tr("找替代商品失败：{0}", err))
        return
    sugg, pool = res
    if not sugg:
        page.win.show_toast(tr("没有要找替代的商品"))
        return
    dlg = ReplaceDialog(page.win, page.settings, grid, sugg, pool)
    dlg.exec()
    if dlg.result_info:
        n = len(dlg.result_info["written"])
        if hasattr(page.win, "log"):
            page.win.log(tr("Griglia 加了 {0} 个替代商品（备份：{1}）", n, dlg.result_info["backup"]))
        page.win.show_toast(tr("已加进 Griglia：{0} 行（旧的字变灰）", n))
        page.refresh()
