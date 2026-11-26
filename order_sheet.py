"""
订货明细 · 订单表看法（2026-11-26 用户：同事更喜欢直接在订单文件里改，这个界面就是失败的 → 把他们熟悉的
SCHEDA 订单表搬进程序；用户定：平时在门店页看和改、汇总页每家店只要「库存 + Colli」、原来的列表先留着）。

一次看一家供应商（左边列表），底下一排和 Excel 一样的工作表标签：门店页（默认）· 汇总。
  门店页 = 订单文件里那一页同样的列：NO. · Cod.Art. · Cod.Forn. · EAN · Descrizione · PZ*COLLI · Colli · Qtà ·
           Prezzo · Importo · GIA · ULT. ARRIVO · QTA ULT. ARR. · ULT. VENDITA · MEDIA MENS. · ROT.30，后面多「建议」「判断」
  汇总   = GRIGLIA PER TUTTI PDV：一个商品一行，每家店「库存 · Colli」两列，最后合计
只有黄格（Colli）能改，改过的深黄 + 鼠标提示原来的建议；像 Excel 一样直接打字、Enter 往下、Ctrl+V 贴一片。
改数量一律走 page.set_qty（和列表看法、起订量检查同一份数据）。
"""
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QColor, QFont, QKeySequence, QGuiApplication
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel, QListWidget, QListWidgetItem,
                               QTableWidget, QTableWidgetItem, QTabBar, QHeaderView, QAbstractItemView,
                               QStyledItemDelegate, QPushButton, QLineEdit, QMessageBox, QDialog)

import os

import store_orders as S
from i18n import tr
from theme import T, NUM_FONT

SUMMARY = "sum"
ROW_ROLE = Qt.UserRole + 41          # SCHEDA 行号
SID_ROLE = Qt.UserRole + 42          # 这一格是哪家店的 Colli
EAN_ROLE = Qt.UserRole + 43          # 商品名那一格：条码（左边画小图）

ROUTE = {"direct": "DIRETTO", "filled": "DIRETTO", "bms": "BMS", "pulled": "BMS"}


def route_of(plan, sid):
    """(标签, 颜色键)：这家店这次怎么订"""
    st = (plan or {}).get("stores", {}).get(sid) or {}
    r = ROUTE.get(st.get("status"))
    if r == "BMS" and not S.bms_ordered((plan or {}).get("bms")):
        return "BMS", "warn"
    if r:
        return r, "ok" if r == "DIRETTO" else "warn"
    return ("—", "faint") if not st.get("colli") else (tr("这次不订"), "faint")


FILE_COLS = [("NO.", "no"), ("Immagine", "img"), ("Cod.Art.", "code"), ("Codice Fornitore", "sup_code"),
             ("Codice EAN", "ean"), ("Descrizione Prodotto", "desc"), ("PZ*COLLI", "pack"), ("Colli", "colli"),
             ("Qtà Ordine", "qta"), ("PREZZO NETTO", "price"), ("IMPORTO", "imp")]


def _extra_heads(days):
    """订单文件里附加的那几列（GIA · ULT. ARRIVO … period_sales）：[(表头, 在 extra_fields 里第几个)]"""
    h = S.extra_headers(days)
    _e, heads = S.file_extra({0: {0: list(range(len(h)))}}, h)
    return list(zip(heads, _e[0][0]))


def store_cols(days, sc=None):
    """
    门店页的列 = 订单文件那一页的列（表头照 SCHEDA 里写的）+ 附加的门店数据列 + 程序建议 · 判断。
    返回 [(表头, 种类)]：种类 = no / img / code / sup_code / ean / desc / pack / colli / qta / price / imp /
    ("x", 第几个附加值) / sugg / verdict / "" （文件里有、程序不填的列）
    """
    heads = (sc or {}).get("heads") or {}
    cols = (sc or {}).get("cols") or {}
    out = []
    if heads:
        by_col = {v: k for k, v in cols.items()}
        for c in sorted(heads):
            h = heads[c]
            n = S._norm(h)
            role = by_col.get(c) or ("no" if n.rstrip(".") in ("NO", "N", "NR", "NUM") else
                                     "img" if n.startswith(("IMMAG", "FOTO", "IMAG")) else
                                     "qta" if n.startswith(("QTA", "QTÀ", "QT.")) else
                                     "imp" if n.startswith("IMPORT") else "")
            out.append((h, role))
        if not any(r == "colli" for _h, r in out):
            out = []
    if not out:
        out = list(FILE_COLS)
    out += [(h, ("x", i)) for h, i in _extra_heads(days)]
    out += [(tr("建议"), "sugg"), (tr("判断"), "verdict")]
    return out


def parse_boxes(text):
    """剪贴板 / 输入的一格 → 箱数（空 = 0）；不是 0 或正整数返回 None"""
    t = str(text or "").strip().replace(",", ".")
    if not t:
        return 0
    try:
        v = float(t)
    except ValueError:
        return None
    return int(v) if v >= 0 and v == int(v) else None


def parse_block(text):
    """Excel 复制来的一片（Tab / 换行）→ [[格子文字]]"""
    rows = [r for r in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while rows and rows[-1] == "":
        rows.pop()
    return [r.split("\t") for r in rows]


class _EnterDown(QStyledItemDelegate):
    """Colli 格的编辑框：Enter 存好后往下一行（和 Excel 一样）"""

    def __init__(self, sheet):
        super().__init__(sheet.table)
        self.sheet = sheet

    def paint(self, painter, opt, idx):
        # 全局样式表给 ::item 设了边框，格子自己的底色会被忽略 → 这里先画黄底
        bg = idx.data(Qt.BackgroundRole)
        if bg is not None:
            painter.fillRect(opt.rect.adjusted(0, 0, -1, -1), bg)
        super().paint(painter, opt, idx)

    def eventFilter(self, ed, ev):
        if ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.commitData.emit(ed)
            self.closeEditor.emit(ed, QStyledItemDelegate.NoHint)
            QTimer.singleShot(0, lambda: self.sheet.move(1, 0))
            return True
        return super().eventFilter(ed, ev)


class _Table(QTableWidget):
    def __init__(self, sheet):
        super().__init__()
        self.sheet = sheet

    def keyPressEvent(self, ev):
        if ev.matches(QKeySequence.Paste):
            self.sheet.paste()
            return
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.state() != QAbstractItemView.EditingState:
            self.sheet.clear_selected()
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and self.state() != QAbstractItemView.EditingState:
            self.sheet.move(1, 0)
            return
        super().keyPressEvent(ev)


class _SupRow(QWidget):
    """左边供应商一行：名称 · 状态 / 细进度条 / 一行小字（设计稿）"""

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_TranslucentBackground)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 7, 12, 7)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.name = QLabel("")
        f = self.name.font()
        f.setBold(True)
        self.name.setFont(f)
        self.tag = QLabel("")
        top.addWidget(self.name, 1)
        top.addWidget(self.tag)
        v.addLayout(top)
        self.bar = QFrame()
        self.bar.setFixedHeight(4)
        self.fill = QFrame(self.bar)
        self.fill.setFixedHeight(4)
        v.addWidget(self.bar)
        self.sub = QLabel("")
        self.sub.setObjectName("Faint")
        v.addWidget(self.sub)
        self._frac = 0.0

    def show_state(self, name, tag, tok, frac, sub):
        self.name.setText(name)
        self.tag.setText(tag)
        self.tag.setStyleSheet(f"color:{T(tok)}; font-size:10px; font-weight:700;")
        self.sub.setText(sub)
        self.bar.setStyleSheet(f"background:{T('track')}; border-radius:2px;")
        self.fill.setStyleSheet(f"background:{T(tok)}; border-radius:2px;")
        self._frac = max(0.0, min(1.0, frac))
        self._place()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def _place(self):
        self.fill.setFixedWidth(int(self.bar.width() * self._frac))


def _thumb_delegate(view, settings):
    """商品名左边画小图（条码放在格子的 EAN_ROLE 里）"""
    import product_images as PI

    class _Thumb(PI.ThumbDelegate):
        def _ean(self, idx):
            return idx.data(EAN_ROLE)
    return _Thumb(view, None, 26, settings)


class OrderSheet(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win, self.page = win, win.page
        self.sc = None
        self.tab = None
        self._filling = False
        self._undo = []                       # [[(path, sid, row, 之前的箱数)]]

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        # ---- 左：供应商 ----
        left = QFrame()
        left.setObjectName("Panel")
        left.setFixedWidth(208)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 8, 0, 0)
        lv.setSpacing(4)
        self.sup_find = QLineEdit()
        self.sup_find.setPlaceholderText(tr("找供应商"))
        self.sup_find.textChanged.connect(self._filter_sups)
        sf = QHBoxLayout()
        sf.setContentsMargins(10, 0, 10, 4)
        sf.addWidget(self.sup_find)
        lv.addLayout(sf)
        self.sups = QListWidget()
        self.sups.setObjectName("SideList")
        self.sups.setFrameShape(QFrame.NoFrame)
        self.sups.currentRowChanged.connect(self._pick_sup)
        lv.addWidget(self.sups, 1)
        h.addWidget(left)
        left.hide()                            # 用户 11-26：左边供应商栏用不上，换成顶上的下拉（列表留着管选中）

        # ---- 右 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(14, 10, 14, 0)
        rv.setSpacing(8)
        head = QFrame()
        head.setObjectName("Panel")
        hv = QVBoxLayout(head)
        hv.setContentsMargins(14, 8, 14, 10)
        hv.setSpacing(8)
        hh = QHBoxLayout()
        hh.setSpacing(10)
        hv.addLayout(hh)
        # 供应商：下拉选（左边那一栏用户 11-26 说用不上、占地方，去掉了）
        from PySide6.QtWidgets import QComboBox, QGridLayout
        self.sup_box = QComboBox()
        self.sup_box.setMinimumWidth(220)
        self.sup_box.setMaxVisibleItems(20)
        f = self.sup_box.font()
        f.setBold(True)
        self.sup_box.setFont(f)
        self.sup_box.currentIndexChanged.connect(self._box_picked)
        hh.addWidget(self.sup_box)
        self.h_state = QLabel("")
        hh.addWidget(self.h_state)
        self.h_moq = QLabel("")
        self.h_moq.setObjectName("Faint")
        hh.addWidget(self.h_moq)
        hh.addStretch()
        self.h_name = QLabel("")               # 留着（老代码 / 测试会读）
        self.h_name.hide()
        self.h_btns = QHBoxLayout()
        self.h_btns.setSpacing(8)
        hh.addLayout(self.h_btns)
        # 门店页：照订单文件顶上那几格（Fatturazione / Luogo di destinazione / Data Ordine / Tot.colli …）
        self.h_grid = QWidget()
        g = QGridLayout(self.h_grid)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(4)
        self.g_val = {}
        cells = [("Fatturazione", "bill", 0, 0), ("Data Ordine", "date", 0, 2), ("Tot.colli", "colli", 0, 4),
                 ("Luogo di destinazione", "dest", 1, 0), (tr("去向"), "route", 1, 2), ("Tot.imponibile", "amount", 1, 4)]
        for lab, key, r, c in cells:
            l = QLabel(lab)
            l.setObjectName("Faint")
            v = QLabel("")
            v.setTextFormat(Qt.RichText)
            if key in ("colli", "amount"):
                v.setFont(QFont(NUM_FONT, 11, QFont.Bold))
            else:
                fv = v.font()
                fv.setBold(True)
                v.setFont(fv)
            g.addWidget(l, r, c)
            g.addWidget(v, r, c + 1)
            self.g_val[key] = v
        g.setColumnStretch(1, 3)
        g.setColumnStretch(3, 2)
        hv.addWidget(self.h_grid)
        self.h_store = QLabel("")              # 以前一行写开票 / 送货地址，现在是上面的格子
        self.h_store.hide()
        # 汇总页：每个去向一块
        self.h_sum = QWidget()
        sh_ = QHBoxLayout(self.h_sum)
        sh_.setContentsMargins(0, 0, 0, 0)
        sh_.setSpacing(18)
        self.dest_box = QHBoxLayout()
        self.dest_box.setSpacing(18)
        sh_.addLayout(self.dest_box)
        sh_.addStretch()
        tb = QVBoxLayout()
        tb.setSpacing(1)
        lt = QLabel(tr("Tot.colli · Tot.imponibile"))
        lt.setObjectName("Faint")
        lt.setAlignment(Qt.AlignRight)
        self.h_tot = QLabel("")
        self.h_tot.setFont(QFont(NUM_FONT, 12, QFont.Bold))
        self.h_tot.setAlignment(Qt.AlignRight)
        tb.addWidget(lt)
        tb.addWidget(self.h_tot)
        sh_.addLayout(tb)
        hv.addWidget(self.h_sum)
        rv.addWidget(head)

        self.table = _Table(self)
        from erp_widgets import BandHeader
        self.band = BandHeader(self.table)
        self.table.setHorizontalHeader(self.band)
        self._thumb = _thumb_delegate(self.table, self.page.settings)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        self._enter = _EnterDown(self)
        self.table.itemChanged.connect(self._changed)
        self.table.currentCellChanged.connect(lambda r, c, *_: self._show_info(r, c))
        self.table.cellDoubleClicked.connect(self._double)
        rv.addWidget(self.table, 1)

        info = QFrame()
        info.setObjectName("StatusBar")
        ih = QHBoxLayout(info)
        ih.setContentsMargins(12, 6, 12, 6)
        self.info = QLabel("")
        self.info.setObjectName("StatusText")
        self.b_card = QPushButton(tr("商品详情"))
        self.b_card.setObjectName("Link")
        self.b_card.clicked.connect(self._open_card)
        ih.addWidget(self.info, 1)
        ih.addWidget(self.b_card)
        # 在 Excel 里改、改完读回（用户 11-26：同事习惯在订单文件里改；生成订单文件照旧用配货按钮）
        from erp_widgets import CountButton
        self.b_xl_edit = QPushButton(tr("在 Excel 中修改"))
        self.b_xl_edit.setToolTip(tr("把这家供应商现在的箱数写成一份和订单文件一样的 Excel（每家门店一页 + 汇总），"
                                     "在 Excel 里改黄格、存好关掉，再点「读回 Excel 的改动」。\n"
                                     "这份只是改箱数用的，不发给供应商；订单文件照旧用「配货」生成"))
        self.b_xl_edit.clicked.connect(self.excel_edit)
        self.b_xl_read = CountButton(tr("读回 Excel 的改动"), 0, tone="warn")
        self.b_xl_read.setToolTip(tr("把 Excel 里改过的箱数读回程序：在 Excel 中修改的那份，和配货生成的订单文件（每家店的、BMS 的）。\n"
                                     "先列出来哪几格从多少改成多少，确认了才改，可以撤销。数字 = 有几个文件存过还没读回"))
        self.b_xl_read.clicked.connect(self.excel_read)
        self.h_btns.addWidget(self.b_xl_edit)
        self.h_btns.addWidget(self.b_xl_read)
        rv.addWidget(info)
        self._xl_timer = QTimer(self)
        self._xl_timer.setInterval(3000)
        self._xl_timer.timeout.connect(self._xl_poll)
        self._xl_timer.start()

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 6)
        self.tabs = QTabBar()
        self.tabs.setShape(QTabBar.RoundedSouth)
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._pick_tab)
        self._style_tabs()
        bottom.addWidget(self.tabs)
        bottom.addStretch()
        self.note = QLabel("")
        self.note.setObjectName("Faint")
        self.b_undo = QPushButton(tr("撤销"))
        self.b_undo.setObjectName("Link")
        self.b_undo.clicked.connect(self.undo)
        self.b_undo.hide()
        hint = QLabel(tr("黄格 = 能改的箱数 ⓘ"))
        hint.setObjectName("Faint")
        hint.setToolTip(tr("和 Excel 一样：直接打数字，Enter 到下一行，Tab 到下一格，Ctrl+V 贴一片，Delete 清空（= 不订）。\n"
                           "改过的格子变深黄，鼠标停上去看原来的建议。双击商品名看商品详情"))
        bottom.addWidget(self.note)
        bottom.addWidget(self.b_undo)
        bottom.addSpacing(10)
        bottom.addWidget(hint)
        rv.addLayout(bottom)
        h.addWidget(right, 1)

    # ================================================================= 数据
    @property
    def calc(self):
        return self.page.calc or {}

    def _stores(self, sc):
        return [s for s in self.calc.get("stores") or [] if s in sc["sheets"]]

    def _x(self, sid, it):
        return self.calc.get("demand", {}).get(sid, {}).get(it.get("db_code") or it["code"])

    def _cur(self, sc, sid, row):
        return self.page.colli.get(sc["path"], {}).get(sid, {}).get(row, 0)

    def _plan(self, sc):
        try:
            return self.page._preview(sc)
        except Exception:
            return None

    # ================================================================= 左边供应商
    def reload(self):
        """重新配货 / 第一次打开：供应商列表重建，尽量停在原来那家"""
        keep = self.sc["path"] if self.sc else None
        self.sups.blockSignals(True)
        self.sups.clear()
        for sc in self.calc.get("schede") or []:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, sc["path"])
            self.sups.addItem(it)
            self._paint_sup(it, sc)
        self.sups.blockSignals(False)
        self.sup_box.blockSignals(True)
        self.sup_box.clear()
        for i in range(self.sups.count()):
            it = self.sups.item(i)
            self.sup_box.addItem(it.data(Qt.UserRole + 2) or "", it.data(Qt.UserRole))
        self.sup_box.blockSignals(False)
        rows = [i for i in range(self.sups.count()) if self.sups.item(i).data(Qt.UserRole) == keep]
        self.sups.setCurrentRow(rows[0] if rows else (0 if self.sups.count() else -1))
        if self.sups.currentRow() >= 0:
            self._pick_sup(self.sups.currentRow())
        self._filter_sups()

    def sup_state(self, sc):
        """左边一行：(金额, 标签, 颜色键, 进度 0–1, 小字)"""
        plan = self._plan(sc)
        items = {i["row"]: i for i in sc["items"]}
        per = {sid: S._totals(self.page.colli.get(sc["path"], {}).get(sid, {}), items) for sid in self._stores(sc)}
        amt = sum(a for _c, a in per.values())
        m = S.sc_moq(sc)
        need_a, need_c = float(m.get("amount") or 0), float(m.get("colli") or 0)

        def ratio(c, a):
            if need_a:
                return a / need_a
            if need_c and need_c != float("inf"):
                return c / need_c
            return 1.0 if c else 0.0
        b = (plan or {}).get("bms")
        nd = sum(1 for sid in per if route_of(plan, sid)[0] == "DIRETTO")
        best = max([ratio(*v) for v in per.values()] + ([ratio(b.get("colli") or 0, b.get("amount") or 0)] if b else []) + [0])
        if not amt:
            return 0.0, "", "faint", 0.0, tr("不用订")
        parts = [tr("直送 {0} 店", nd)] if nd else []
        if b and S.bms_ordered(b):
            parts.append("BMS")
        if b and not S.bms_ordered(b):
            gap = max(0.0, need_a - float(b.get("amount") or 0)) if need_a else 0
            sub = " · ".join(parts + [tr("BMS 还差 €{0:,.0f}", gap) if gap else tr("BMS 还不够")])
            return amt, tr("差一点"), "warn", min(1.0, best), f"€{amt:,.0f} · {sub}"
        if not parts:
            return amt, tr("这次不订"), "faint", min(1.0, best), f"€{amt:,.0f}"
        return amt, tr("够起订量"), "ok", 1.0, f"€{amt:,.0f} · " + " · ".join(parts)

    def _paint_sup(self, item, sc):
        amt, tag, tok, frac, sub = self.sup_state(sc)
        w = self.sups.itemWidget(item)
        if w is None:
            w = _SupRow()
            self.sups.setItemWidget(item, w)
        w.show_state(sc["name"], tag, tok, frac, sub)
        item.setData(Qt.UserRole + 1, sc["name"].lower())      # 搜索用
        label = sc["name"] + (f"  ·  €{amt:,.0f}" if amt else "")
        item.setData(Qt.UserRole + 2, label)
        k = self.sup_box.findData(sc["path"]) if hasattr(self, "sup_box") else -1
        if k >= 0:
            self.sup_box.setItemText(k, label)
        from PySide6.QtCore import QSize
        item.setSizeHint(QSize(180, 66))
        item.setToolTip(f"{sc['name']}（{sc.get('day', '')}）")

    def _filter_sups(self):
        q = self.sup_find.text().strip().lower()
        for i in range(self.sups.count()):
            it = self.sups.item(i)
            it.setHidden(bool(q) and q not in str(it.data(Qt.UserRole + 1) or "").lower())

    def _box_picked(self, k):
        if 0 <= k < self.sups.count() and self.sups.currentRow() != k:
            self.sups.setCurrentRow(k)

    def _pick_sup(self, r):
        if r < 0:
            return
        if self.sup_box.currentIndex() != r:
            self.sup_box.blockSignals(True)
            self.sup_box.setCurrentIndex(r)
            self.sup_box.blockSignals(False)
        path = self.sups.item(r).data(Qt.UserRole)
        sc = next((x for x in self.calc.get("schede") or [] if x["path"] == path), None)
        if sc is None:
            return
        self.sc = sc
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        stores = self._stores(sc)
        for sid in stores:
            k = self.tabs.addTab(S.STORE_SHEETS.get(sid, str(sid)))
            self.tabs.setTabData(k, sid)
        k = self.tabs.addTab(tr("汇总"))
        self.tabs.setTabData(k, SUMMARY)
        # 默认门店页（用户：一般都去门店页看）：上次看的那家，不然第一家有订的
        want = self.tab if self.tab in stores or self.tab == SUMMARY else None
        if want is None:
            want = next((s for s in stores if self.page.colli.get(sc["path"], {}).get(s)), stores[0] if stores else SUMMARY)
        self.tabs.setCurrentIndex(next(i for i in range(self.tabs.count()) if self.tabs.tabData(i) == want))
        self.tabs.blockSignals(False)
        self.tab = want
        self.render()

    def select(self, path, sid=None):
        """从起订量检查点进来：跳到这家供应商（和门店页）"""
        if sid not in (None, "__all__"):
            self.tab = sid
        for i in range(self.sups.count()):
            if self.sups.item(i).data(Qt.UserRole) == path:
                self.sups.setCurrentRow(i)
                self._pick_sup(i)
                return

    def _pick_tab(self, k):
        if k < 0:
            return
        self.tab = self.tabs.tabData(k)
        self.render()

    # ================================================================= 顶上一条
    def _paint_head(self):
        sc = self.sc
        self._xl_buttons()
        plan = self._plan(sc)
        self.h_name.setText(sc["name"])
        m = S.sc_moq(sc)
        self.h_moq.setText(tr("Minimo ordine {0} · 每个去向单独算", " / ".join(S.moq_parts(m, tr)) or "—"))
        while self.dest_box.count():
            w = self.dest_box.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        items = {i["row"]: i for i in sc["items"]}
        tot_c = tot_a = 0.0
        for sid in self._stores(sc):
            c, a = S._totals(self.page.colli.get(sc["path"], {}).get(sid, {}), items)
            tot_c += c
            tot_a += a
            label, tok = route_of(plan, sid)
            st = ((plan or {}).get("stores") or {}).get(sid) or {}
            sub = {"direct": tr("直送 · 够起订量"), "filled": tr("凑单后直送"), "bms": tr("凑进 BMS"),
                   "pulled": tr("凑进 BMS"), "wait": tr("这次不订"), "dropped": tr("这次不订")}.get(st.get("status"), "")
            self.dest_box.addWidget(self._dest(S.STORE_SHEETS.get(sid, str(sid)), label, tok, a, sid == self.tab, sub))
        b = (plan or {}).get("bms")
        if b:
            ok = S.bms_ordered(b)
            need = float(m.get("amount") or 0)
            gap = max(0.0, need - float(b.get("amount") or 0)) if need else 0
            n = len(b.get("stores") or [])
            sub = tr("{0} 店凑", n) + (" · " + (tr("够起订量") if ok else tr("还差 €{0:,.2f}", gap) if gap else tr("还不够")))
            self.dest_box.addWidget(self._dest("BMS", tr("凑单"), "warn", b.get("amount") or 0, False, sub,
                                               val_tok=None if ok else "warn"))
        self.h_tot.setText(f"{tot_c:g} · €{tot_a:,.2f}")
        amt, tag, tok, _frac, _sub = self.sup_state(sc)
        chip = T(tok + "_soft") if tok in ("ok", "warn") else T("track")
        self.h_state.setText(f"<span style='background:{chip};color:{T(tok)};font-weight:700'>&nbsp;{tag}&nbsp;</span>"
                             if tag else "")
        store = self.tab != SUMMARY
        self.h_grid.setVisible(store)
        self.h_sum.setVisible(not store)
        if store:
            import mail_send as MS
            from datetime import date
            pf = MS.profile(self.page.settings, self.tab)
            bill = MS.profile(self.page.settings, "BMS").get("name") or "BMS PARTNERS SRL"
            label, rtok = route_of(plan, self.tab)
            st = ((plan or {}).get("stores") or {}).get(self.tab) or {}
            why = {"direct": tr("直送 · 够起订量"), "filled": tr("凑单后直送"), "bms": tr("凑进 BMS"),
                   "pulled": tr("凑进 BMS"), "wait": tr("这次不订"), "dropped": tr("这次不订")}.get(st.get("status"), "")
            c, a_ = S._totals(self.page.colli.get(sc["path"], {}).get(self.tab, {}), items)
            mute = T("muted")
            rchip = T(rtok + "_soft") if rtok in ("ok", "warn") else T("track")
            v = self.g_val
            v["bill"].setText(bill)
            v["dest"].setText(f"{pf.get('name', '')} <span style='color:{mute};font-weight:400'>· {pf.get('address', '')}</span>")
            v["date"].setText(date.today().strftime("%d/%m/%Y"))
            v["route"].setText(f"<span style='background:{rchip};color:{T(rtok)};font-size:10px'>&nbsp;{label}&nbsp;</span>"
                               f" <span style='color:{mute};font-weight:400'>{why}</span>")
            v["colli"].setText(f"{c:g}")
            v["amount"].setText(f"<span style='color:{T('ok') if a_ else T('faint')}'>€{a_:,.2f}</span>")

    def _dest(self, name, route, tok, amt, on, sub="", val_tok=None):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        chip = T(tok + "_soft") if tok in ("ok", "warn") else T("track")
        top = QLabel(f"<b>{name}</b> <span style='background:{chip};color:{T(tok)};font-size:9px;font-weight:700'>"
                     f"&nbsp;{route}&nbsp;</span>")
        val = QLabel(f"€{amt:,.2f}" if amt else "—")
        val.setFont(QFont(NUM_FONT, 11, QFont.Bold))
        val.setStyleSheet(f"color:{T('accent') if on else T(val_tok) if val_tok else T('text') if amt else T('faint')}")
        low = QLabel(sub)
        low.setObjectName("Faint")
        f = low.font()
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
        low.setFont(f)
        v.addWidget(top)
        v.addWidget(val)
        v.addWidget(low)
        return w

    # ================================================================= 表格
    def render(self):
        if not self.sc:
            return
        self._paint_head()
        self._filling = True
        t = self.table
        t.setUpdatesEnabled(False)
        t.clear()
        if self.tab == SUMMARY:
            self._render_summary()
        else:
            self._render_store(self.tab)
        t.setUpdatesEnabled(True)
        self._filling = False
        if t.currentRow() < 0 and t.rowCount():
            first = next((c for c in range(t.columnCount()) if self._editable(0, c)), 0)
            t.setCurrentCell(0, first)
        self._show_info(t.currentRow(), t.currentColumn())

    def _num(self, text, bold=False, color=None):
        it = QTableWidgetItem("" if text is None else str(text))
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        it.setFont(QFont(NUM_FONT, 10, QFont.Bold if bold else QFont.Normal))
        it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        if color:
            it.setForeground(QColor(T(color)))
        return it

    def _txt(self, text, color=None):
        it = QTableWidgetItem("" if text is None else str(text))
        it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        if color:
            it.setForeground(QColor(T(color)))
        return it

    def _set_bands(self, bands):
        """分组色带：一种颜色一种意思（DIRETTO 绿、BMS 橙），不按顺序轮流换色"""
        self.band.set_bands(bands)
        for g in set(bands.values()):
            g_ = str(g)
            self.band.tone_of[g] = ("ok" if g_.endswith("DIRETTO") else "warn" if g_.endswith("BMS")
                                    else "accent" if g_ == "DETTAGLIO ORDINE" else "violet" if g_ == tr("程序建议")
                                    else "cyan" if g_ == tr("门店数据（订单文件里也有）") else "info")
        self.band.viewport().update()

    def _desc(self, it):
        d = self._txt(it["desc"])
        d.setData(EAN_ROLE, it["ean"])
        d.setToolTip(f"{it['desc']}\nEAN {it['ean']}")
        return d

    def _colli_item(self, sc, sid, it):
        n = self._cur(sc, sid, it["row"])
        sug = self.win._orig(sc, sid, it["row"])
        edited = (sc["path"], sid, it["row"]) in self.page.edited
        c = QTableWidgetItem(f"{n:g}" if n else "")
        c.setTextAlignment(Qt.AlignCenter)
        c.setFont(QFont(NUM_FONT, 11, QFont.Bold))
        c.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
        c.setData(ROW_ROLE, it["row"])
        c.setData(SID_ROLE, sid)
        bg = QColor("#F5C542")
        bg.setAlpha(150 if edited else 55)
        c.setBackground(bg)
        if edited:
            c.setForeground(QColor(T("warn")))
            c.setToolTip(tr("系统建议 {0:g} 箱，改成了 {1:g} 箱", sug, n))
        else:
            c.setToolTip(tr("系统建议 {0:g} 箱", sug))
        return c

    def _render_store(self, sid):
        sc, t = self.sc, self.table
        days = self.calc.get("days") or [30, 60, 90, 180]
        spec = store_cols(days, sc)
        roles = [r for _h, r in spec]
        t.setColumnCount(len(spec))
        t.setHorizontalHeaderLabels([h + (" ✎" if r == "colli" else "") for h, r in spec])
        t.setRowCount(len(sc["items"]))
        self._colli_col = roles.index("colli")
        for c, r in enumerate(roles):
            t.setItemDelegateForColumn(c, self._enter if r == "colli" else self._thumb if r == "img" else None)
        self._set_bands({c: ("DETTAGLIO ORDINE" if not isinstance(r, tuple) and r not in ("sugg", "verdict")
                             else tr("门店数据（订单文件里也有）") if isinstance(r, tuple) else tr("程序建议"))
                         for c, r in enumerate(roles)})
        tot_c = tot_a = 0.0
        for r, it in enumerate(sc["items"]):
            x = self._x(sid, it)
            n = self._cur(sc, sid, it["row"])
            tot_c += n
            tot_a += n * it["pack"] * it["price"]
            sug = self.win._orig(sc, sid, it["row"])
            kind, vt = self.win._verdict(sc, it, sid, n)
            ex = S.extra_fields(x, sug, days) if x else None
            for c, role in enumerate(roles):
                item = self._store_cell(role, r, it, x, ex, n, sug, kind, vt, sc, sid)
                item.setData(ROW_ROLE, it["row"])
                t.setItem(r, c, item)
        self._fit(roles.index("desc") if "desc" in roles else None, self._colli_col,
                  roles.index("img") if "img" in roles else None)
        self.note.setText(tr("{0}：{1:g} 箱 · €{2:,.2f}", S.STORE_SHEETS.get(sid, sid), tot_c, tot_a))

    def _store_cell(self, role, r, it, x, ex, n, sug, kind, vt, sc, sid):
        if role == "no":
            return self._num(it.get("no", r + 1), color="faint")
        if role == "img":
            c = self._txt("")
            c.setData(EAN_ROLE, it["ean"])
            return c
        if role == "code":
            return self._txt(S.item_code(it), "muted")
        if role == "sup_code":
            return self._txt(it.get("sup_code") or "", "muted")
        if role == "ean":
            return self._txt(it["ean"], "muted")
        if role == "desc":
            d = self._txt(it["desc"])
            d.setToolTip(f"{it['desc']}\nEAN {it['ean']}")
            return d
        if role == "pack":
            return self._num(f"{it['pack']:g}")
        if role == "colli":
            return self._colli_item(sc, sid, it)
        if role == "qta":
            return self._num(f"{n * it['pack']:g}" if n else "")
        if role == "price":
            return self._num(f"{it['price']:.2f} €")
        if role == "imp":
            return self._num(f"{n * it['pack'] * it['price']:,.2f} €" if n else "", bold=True)
        if isinstance(role, tuple):
            v = ex[role[1]] if ex and role[1] < len(ex) else None
            if v is None or v == "":
                return self._txt("")
            if isinstance(v, (int, float)):
                neg = role[1] == 0 and v <= 0
                return self._num(f"{round(v, 1):g}", bold=neg, color="err" if neg else None)
            return self._txt(str(v), "muted")
        if role == "sugg":
            return self._num(f"{sug:g}" if sug else ("0" if x else ""), color="ok" if sug else "faint")
        if role == "verdict":
            return self._txt(vt if x else (tr("同上（重复编号）") if it.get("dup_of") else tr("数据库没有")),
                             "warn" if (x and kind in ("more", "less")) else "faint" if not x else None)
        return self._txt("")

    def _render_summary(self):
        sc, t = self.sc, self.table
        stores = self._stores(sc)
        cols = ["NO.", "Cod.Art.", "Descrizione Prodotto", "PZ*COLLI", "PREZZO NETTO"]
        plan = self._plan(sc)
        bands = {c: "DETTAGLIO ORDINE" for c in range(5)}
        for sid in stores:
            bands[len(cols)] = bands[len(cols) + 1] = f"{S.STORE_SHEETS.get(sid, str(sid))} · {route_of(plan, sid)[0]}"
            cols += [tr("库存"), "Colli ✎"]
        bands[len(cols)] = bands[len(cols) + 1] = tr("合计")
        cols += ["Colli", "Importo"]
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setRowCount(len(sc["items"]))
        for c in range(len(cols)):
            t.setItemDelegateForColumn(c, self._enter if (c >= 5 and (c - 5) % 2 == 1 and c < 5 + 2 * len(stores))
                                       else self._thumb if c == 2 else None)
        self._set_bands(bands)
        for r, it in enumerate(sc["items"]):
            d = self._desc(it)
            row = [self._num(it.get("no", r + 1), color="faint"), self._txt(S.item_code(it), "muted"),
                   d, self._num(f"{it['pack']:g}"), self._num(f"{it['price']:.2f}")]
            tc = 0
            for sid in stores:
                x = self._x(sid, it)
                inv = x.get("inv_raw", x.get("inv")) if x else None
                row.append(self._num("" if inv is None else f"{inv:g}", color="err" if inv is not None and inv <= 0 else "muted"))
                row.append(self._colli_item(sc, sid, it))
                tc += self._cur(sc, sid, it["row"])
            row.append(self._num(f"{tc:g}" if tc else "", bold=True))
            row.append(self._num(f"{tc * it['pack'] * it['price']:,.2f}" if tc else "", bold=True))
            for c, item in enumerate(row):
                if item.data(ROW_ROLE) is None:
                    item.setData(ROW_ROLE, it["row"])
                t.setItem(r, c, item)
        self._fit(2, None)
        self.note.setText("")

    def _fit(self, desc_col, colli_col, img_col=None):
        """列宽按表头和内容量（格子左右各留 10 像素），名称最宽 300；图片列一个小图宽"""
        t = self.table
        t.resizeColumnsToContents()
        for c in range(t.columnCount()):
            w = t.columnWidth(c) + 8
            if c == desc_col:
                w = min(max(w + (36 if img_col is None else 0), 200), 270)
            if c == img_col:
                w = 72
            t.setColumnWidth(c, min(w, 330))

    # ================================================================= 改数量
    def _changed(self, item):
        if self._filling or item.data(SID_ROLE) is None:
            return
        n = parse_boxes(item.text())
        r, c = item.row(), item.column()
        sid, row = item.data(SID_ROLE), item.data(ROW_ROLE)
        # 编辑框还开着：等它关掉再重画（直接重画这一行会让 Qt 闪退，见 CLAUDE.md 表格编辑那一条）
        QTimer.singleShot(0, lambda: self._apply([(sid, row, n)], (r, c)))

    def _apply(self, changes, keep=None):
        sc = self.sc
        bad = [ch for ch in changes if ch[2] is None]
        undo = []
        for sid, row, n in changes:
            if n is None:
                continue
            old = self._cur(sc, sid, row)
            if old == n:
                continue
            undo.append((sc["path"], sid, row, old))
            self.page.set_qty(sc, sid, row, n, refresh=False)
        if undo:
            self._undo.append(undo)
            self.page.lines_changed(sc["path"])
            self._sync_list(undo)
            self._paint_sup(self.sups.currentItem(), sc)
            self.b_undo.show()
            self.win.flash(tr("改了 {0} 格（{1}）· 可以撤销", len(undo), sc["name"]))
        if bad:
            self.win.flash(tr("箱数要填 0 或正整数"))
        self.render()
        if keep:
            self.table.setCurrentCell(*keep)
        return len(undo)

    def _sync_list(self, changes):
        """列表看法里同一格跟着变（只刷这几行）"""
        w = self.win
        if not w.detail or w._loading:
            return
        idx = getattr(w, "_di_index", None)
        if idx is None or len(idx) != len(w.detail):
            idx = {(sc["path"], sid, it["row"]): i for i, (sc, it, sid) in enumerate(w.detail)}
            w._di_index = idx
        for path, sid, row, _old in changes:
            i = idx.get((path, sid, row))
            if i is not None:
                w._show_qty(i)
        w._update_status()

    def undo(self):
        if not self._undo:
            return
        last = self._undo.pop()
        sc = next((x for x in self.calc.get("schede") or [] if x["path"] == last[0][0]), None)
        if sc is None:
            return
        for path, sid, row, old in last:
            self.page.set_qty(sc, sid, row, old, refresh=False)
        self.page.lines_changed(sc["path"])
        self._sync_list(last)
        if self.sc and self.sc["path"] == sc["path"]:
            self._paint_sup(self.sups.currentItem(), sc)
            self.render()
        self.b_undo.setVisible(bool(self._undo))
        self.win.flash(tr("已撤销 {0} 格", len(last)))

    def _editable(self, r, c):
        it = self.table.item(r, c)
        return it is not None and it.data(SID_ROLE) is not None

    def move(self, dr, dc):
        t = self.table
        r, c = t.currentRow() + dr, t.currentColumn() + dc
        if 0 <= r < t.rowCount() and 0 <= c < t.columnCount():
            t.setCurrentCell(r, c)

    def paste(self):
        """Excel 复制来的一片：从选中的左上角往右下贴，只贴到黄格（别的格子跳过）；剪贴板一格 → 贴满选中的黄格"""
        block = parse_block(QGuiApplication.clipboard().text())
        if not block:
            return
        t = self.table
        sel = t.selectedIndexes()
        changes = []
        if len(block) == 1 and len(block[0]) == 1 and len(sel) > 1:
            n = parse_boxes(block[0][0])
            changes = [(t.item(i.row(), i.column()).data(SID_ROLE), t.item(i.row(), i.column()).data(ROW_ROLE), n)
                       for i in sel if self._editable(i.row(), i.column())]
        else:
            r0 = min((i.row() for i in sel), default=t.currentRow())
            c0 = min((i.column() for i in sel), default=t.currentColumn())
            for dr, line in enumerate(block):
                for dc, cell in enumerate(line):
                    r, c = r0 + dr, c0 + dc
                    if r < t.rowCount() and c < t.columnCount() and self._editable(r, c):
                        it = t.item(r, c)
                        changes.append((it.data(SID_ROLE), it.data(ROW_ROLE), parse_boxes(cell)))
        if changes:
            self._apply(changes, (t.currentRow(), t.currentColumn()))

    def clear_selected(self):
        t = self.table
        changes = [(t.item(i.row(), i.column()).data(SID_ROLE), t.item(i.row(), i.column()).data(ROW_ROLE), 0)
                   for i in t.selectedIndexes() if self._editable(i.row(), i.column())]
        if len(changes) > 5 and QMessageBox.question(
                self, tr("清空箱数"), tr("把选中的 {0} 格都改成 0（不订）？可以撤销。", len(changes))) != QMessageBox.Yes:
            return
        if changes:
            self._apply(changes, (t.currentRow(), t.currentColumn()))

    # ================================================================= 下面一行 / 商品详情
    def _item_at(self, r):
        if not self.sc or r < 0 or self.table.item(r, 0) is None:
            return None
        row = self.table.item(r, 0).data(ROW_ROLE)
        return next((i for i in self.sc["items"] if i["row"] == row), None)

    def _sid_at(self, r, c):
        it = self.table.item(r, c) if r >= 0 and c >= 0 else None
        sid = it.data(SID_ROLE) if it is not None else None
        if sid is not None:
            return sid
        return self.tab if self.tab != SUMMARY else None

    def _show_info(self, r, c):
        it = self._item_at(r)
        sid = self._sid_at(r, c)
        if not it or sid is None:
            self.info.setText(it["desc"] if it else "")
            return
        x = self._x(sid, it)
        sc = self.sc
        n = self._cur(sc, sid, it["row"])
        parts = [f"<b>{it['desc'][:40]}</b> · {S.STORE_SHEETS.get(sid, sid)}"]
        if x:
            inv = x.get("inv_raw", x.get("inv"))
            parts.append(tr("库存 {0:g}", inv))
            if x.get("daily"):
                parts.append(tr("月销 {0:g}", round(float(x["daily"]) * 30, 1)))
            tb = S.transit_boxes(x, it.get("pack"))
            if tb:
                parts.append(tr("在途 {0:g} 箱", tb))
            v = S.cover_days(x, n * it["pack"] + float(x.get("transit") or 0))
            if n:
                parts.append(tr("订后够卖 {0} 天", S.days_text(v)))
            parts.append(tr("建议 {0:g} 箱", self.win._orig(sc, sid, it["row"])))
        else:
            parts.append(tr("数据库里没有"))
        self.info.setText(" · ".join(parts))

    def _double(self, r, c):
        if self._editable(r, c):
            return
        if self.table.horizontalHeaderItem(c) and "Descrizione" in self.table.horizontalHeaderItem(c).text():
            self._open_card()

    def _open_card(self):
        r, c = self.table.currentRow(), self.table.currentColumn()
        it = self._item_at(r)
        sid = self._sid_at(r, c)
        if it is None:
            return
        if sid is None:
            sid = next(iter(self._stores(self.sc)), None)
        for i, (sc, it2, s2) in enumerate(self.win.detail):
            if sc is self.sc and it2 is it and s2 == sid:
                self.win.open_card(i)
                return

    # ================================================================= Excel 里改 → 读回
    def _xl_root(self):
        try:
            return self.page._out_root()
        except Exception:
            return None

    @staticmethod
    def _xl_log():
        from settings import app_dir
        return os.path.join(app_dir(), "cache", "excel_read")

    def _xl_sources(self, sc=None):
        sc = sc or self.sc
        if not sc or sc.get("pdv"):
            return []
        try:
            return S.excel_sources(sc, self._xl_root(), getattr(self.page, "result", None), log_dir=self._xl_log())
        except Exception:
            return []

    def _xl_buttons(self):
        pdv = bool(self.sc and self.sc.get("pdv"))
        for b in (self.b_xl_edit, self.b_xl_read):
            b.setEnabled(bool(self.sc) and not pdv)
        if pdv:
            self.b_xl_edit.setToolTip(tr("门店采购的供应商还不能在 Excel 里改"))
        self._xl_poll()

    def _xl_poll(self):
        """只看文件修改时间：有几个文件在 Excel 里存过、还没读回"""
        if not self.isVisible() or not self.sc or self.sc.get("pdv"):
            return
        src = self._xl_sources()
        n = len(S.excel_pending(src, getattr(self.page, "result", None), self._xl_log())) if src else 0
        if (n or None) != self.b_xl_read.count():
            self.b_xl_read.set_count(n or None)

    def excel_edit(self):
        sc, page = self.sc, self.page
        if not sc or sc.get("pdv"):
            return
        root = self._xl_root()
        if not root:
            QMessageBox.warning(self, tr("在 Excel 中修改"), tr("还没有选配货文件夹"))
            return
        from erp_widgets import BusyDialog, open_file
        path = S.edit_copy_path(root, sc)
        if os.path.exists(os.path.join(os.path.dirname(path), "~$" + os.path.basename(path))):
            open_file(self, path)                # 已经在 Excel 里开着：拿到前面
            return
        if S.edit_changed(path):
            b = QMessageBox.question(
                self, tr("在 Excel 中修改"),
                tr("{0} 这份 Excel 改过、还没读回程序。\n先读回再打开吗？（选「否」= 用程序现在的箱数重新写一份，Excel 里的改动不要了）",
                   sc["name"]), QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if b == QMessageBox.Cancel:
                return
            if b == QMessageBox.Yes:
                if self.excel_read() is None:
                    return
        plan = self._plan(sc)
        url = (page.settings or {}).get("image_url") or None
        lines = page.colli.get(sc["path"], {})
        _r, err = BusyDialog.run(self, tr("在 Excel 中修改"), tr("正在写 {0} 的 Excel", sc["name"]),
                                 lambda: S.write_edit_copy(sc, lines, path, self.calc, plan, url))
        if err:
            QMessageBox.warning(self, tr("在 Excel 中修改"), tr("写不出 Excel：{0}", err))
            return
        self.b_xl_read.set_count(None)
        self.win.flash(tr("已打开 {0}：在 Excel 里改黄格，存好关掉，再点「读回 Excel 的改动」", os.path.basename(path)))
        open_file(self, path)

    def excel_read(self):
        """返回读回了几格；取消 / 出错 → None"""
        sc = self.sc
        if not sc or sc.get("pdv"):
            return None
        from erp_widgets import BusyDialog
        src = self._xl_sources()
        if not src:
            QMessageBox.information(self, tr("读回 Excel 的改动"),
                                    tr("{0} 还没有可以读回的 Excel：先点「在 Excel 中修改」，或者先配货生成订单文件", sc["name"]))
            return None
        locked = [os.path.basename(p) for p, *_ in src if os.path.exists(
            os.path.join(os.path.dirname(p), "~$" + os.path.basename(p)))]
        if locked:
            QMessageBox.warning(self, tr("读回 Excel 的改动"),
                                tr("这些文件还在 Excel 里开着，先存好关掉再读回：\n{0}", "\n".join(locked)))
            return None
        cur = self.page.colli.get(sc["path"], {})
        rows, err = BusyDialog.run(self, tr("读回 Excel 的改动"), tr("正在读 {0} 个文件", len(src)),
                                   lambda: S.excel_changes(sc, cur, src))
        if err:
            QMessageBox.warning(self, tr("读回 Excel 的改动"), tr("读不了 Excel：{0}", err))
            return None
        if not rows:
            S.mark_read(sc, src, self._xl_log())
            self.b_xl_read.set_count(None)
            self.win.flash(tr("Excel 里的箱数和程序一样，没有要读回的"))
            return 0
        dlg = ReadBackDialog(self, sc, rows)
        if not dlg.exec():
            return None
        ok = [(c["sid"], c["row"], c["new"]) for c in rows if not c["bad"]]
        n = self._apply(ok) if ok else 0
        S.mark_read(sc, src, self._xl_log())
        self.b_xl_read.set_count(None)
        bad = sum(1 for c in rows if c["bad"])
        self.win.flash(tr("已读回 {0} 格（{1}）· 可以撤销", n, sc["name"]) +
                       (tr(" · {0} 格写的不是箱数，没改", bad) if bad else ""))
        return n

    def _style_tabs(self):
        """工作表标签照 Excel / 设计稿：灰底一格一格，选中的白底、上面一条主题色"""
        self.tabs.setStyleSheet(
            f"QTabBar::tab{{background:{T('track')};color:{T('muted')};border:1px solid {T('line')};"
            f"border-top:2px solid {T('line')};padding:5px 16px;margin-right:-1px;min-width:60px}}"
            f"QTabBar::tab:selected{{background:{T('surface')};color:{T('accent')};font-weight:700;"
            f"border-top:2px solid {T('accent')}}}"
            f"QTabBar::tab:hover:!selected{{background:{T('hover')}}}")

    def restyle(self):
        self._style_tabs()
        if self.sc:
            self.render()


class ReadBackDialog(QDialog):
    """读回之前列出来：哪家店哪个商品，程序现在几箱 → Excel 里几箱"""
    COLS = ("门店", "商品", "程序现在", "Excel 里", "文件", "备注")

    def __init__(self, parent, sc, rows):
        super().__init__(parent)
        from erp_widgets import TableSortFilter, fit_to_screen
        self.setWindowTitle(tr("读回 Excel 的改动"))
        self.rows = rows
        items = {it["row"]: it for it in sc["items"]}
        self.data = []
        for c in rows:
            it = items.get(c["row"]) or {}
            note = ""
            if c["bad"]:
                note = tr("格子写的不是箱数，不改")
            elif c["both"]:
                note = tr("Excel 存好以后程序里也改过这一格：用 Excel 的")
            self.data.append([S.STORE_SHEETS.get(c["sid"], str(c["sid"])), it.get("desc") or it.get("code") or "",
                              c["now"], "?" if c["new"] is None else c["new"],
                              (tr("工作副本") if c["kind"] == "edit" else tr("订单文件")) + " · " + os.path.basename(c["file"]),
                              note, c])
        v = QVBoxLayout(self)
        good = sum(1 for c in rows if not c["bad"])
        t1 = QLabel(tr("{0} 的 Excel 里改了 {1} 格 ⓘ", sc["name"], len(rows)))
        f = t1.font()
        f.setBold(True)
        t1.setFont(f)
        t1.setToolTip(tr("只列出 Excel 里改过、而且和程序现在不一样的格子。确认后改进程序（配货方案跟着重算），可以撤销。\n"
                         "订单文件是配货生成的：之后再点配货会用程序的箱数重新生成，Excel 里没读回的改动会没有"))
        v.addWidget(t1)
        self.t = QTableWidget(0, len(self.COLS))
        self.t.setHorizontalHeaderLabels([tr(h) for h in self.COLS])
        self.t.verticalHeader().hide()
        self.t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, w in enumerate((92, 300, 72, 72, 220, 200)):
            self.t.setColumnWidth(c, w)
        self.ctl = TableSortFilter(self.t, lambda: len(self.data), lambda i, c: str(self.data[i][c]), self._fill,
                                   key=lambda i, c: self.data[i][c] if c in (2, 3) and isinstance(self.data[i][c], int)
                                   else str(self.data[i][c]), numeric=(2, 3))
        self.ctl.render()
        v.addWidget(self.t, 1)
        foot = QHBoxLayout()
        foot.addStretch()
        b_no = QPushButton(tr("取消"))
        b_no.clicked.connect(self.reject)
        from erp_widgets import CountButton
        b_ok = CountButton(tr("读回程序"), good)
        b_ok.setObjectName("Primary")
        b_ok.setEnabled(good > 0)
        b_ok.clicked.connect(self.accept)
        foot.addWidget(b_no)
        foot.addWidget(b_ok)
        v.addLayout(foot)
        self.resize(1000, min(640, 160 + 34 * len(rows)))
        fit_to_screen(self)

    def _fill(self, i, k):
        d = self.data[i]
        c = d[6]
        for col in range(6):
            it = QTableWidgetItem(str(d[col]))
            if col in (2, 3):
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if c["bad"]:
                it.setForeground(QColor(T("faint")))
            elif col == 3:
                it.setForeground(QColor(T("info" if c["new"] > c["now"] else "warn")))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            elif col == 5 and c["both"]:
                it.setForeground(QColor(T("warn")))
            self.t.setItem(k, col, it)
