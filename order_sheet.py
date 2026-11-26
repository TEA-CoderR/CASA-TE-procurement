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
                               QStyledItemDelegate, QPushButton, QLineEdit, QMessageBox)

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


def store_cols(days):
    """门店页的列（和订单文件同样的顺序）"""
    return ["NO.", "Cod.Art.", "Cod.Forn.", "Codice EAN", "Descrizione", "PZ*COLLI", "Colli", "Qtà", "Prezzo",
            "Importo", "GIA", "ULT. ARRIVO", "QTA ULT. ARR.", "ULT. VENDITA", "MEDIA MENS.", f"ROT.{days[0]}",
            tr("建议"), tr("判断")]


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

        # ---- 右 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(14, 10, 14, 0)
        rv.setSpacing(8)
        head = QFrame()
        head.setObjectName("Panel")
        hv = QVBoxLayout(head)
        hv.setContentsMargins(14, 8, 14, 8)
        hv.setSpacing(6)
        hh = QHBoxLayout()
        hh.setSpacing(16)
        hv.addLayout(hh)
        # 门店页：和订单文件那一页顶上一样写开票 / 送货地址
        self.h_store = QLabel("")
        self.h_store.setTextFormat(Qt.RichText)
        self.h_store.setWordWrap(False)
        hv.addWidget(self.h_store)
        nb = QVBoxLayout()
        nb.setSpacing(1)
        self.h_name = QLabel("")
        f = self.h_name.font()
        f.setPointSizeF(f.pointSizeF() + 3)
        f.setBold(True)
        self.h_name.setFont(f)
        self.h_moq = QLabel("")
        self.h_moq.setObjectName("Faint")
        nb.addWidget(self.h_name)
        nb.addWidget(self.h_moq)
        hh.addLayout(nb)
        self.dest_box = QHBoxLayout()
        self.dest_box.setSpacing(18)
        hh.addLayout(self.dest_box)
        hh.addStretch()
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
        hh.addLayout(tb)
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
        rv.addWidget(info)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 6)
        self.tabs = QTabBar()
        self.tabs.setShape(QTabBar.RoundedSouth)
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._pick_tab)
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
        from PySide6.QtCore import QSize
        item.setSizeHint(QSize(180, 66))
        item.setToolTip(f"{sc['name']}（{sc.get('day', '')}）")

    def _filter_sups(self):
        q = self.sup_find.text().strip().lower()
        for i in range(self.sups.count()):
            it = self.sups.item(i)
            it.setHidden(bool(q) and q not in str(it.data(Qt.UserRole + 1) or "").lower())

    def _pick_sup(self, r):
        if r < 0:
            return
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
        # 门店页：开票 / 送货地址 / 这家店这次怎么订
        if self.tab != SUMMARY:
            import mail_send as MS
            pf = MS.profile(self.page.settings, self.tab)
            bill = MS.profile(self.page.settings, "BMS").get("name") or "BMS PARTNERS SRL"
            label, tok = route_of(plan, self.tab)
            c, a = S._totals(self.page.colli.get(sc["path"], {}).get(self.tab, {}), items)
            mute = T("faint")
            self.h_store.setText(
                f"<span style='color:{mute}'>Fatturazione</span> <b>{bill}</b> &nbsp;&nbsp; "
                f"<span style='color:{mute}'>Luogo di destinazione</span> <b>{pf.get('name', '')}</b> · {pf.get('address', '')}"
                f" &nbsp;&nbsp; <span style='background:{T(tok + '_soft') if tok != 'faint' else T('track')};color:{T(tok)};"
                f"font-weight:700;font-size:10px'>&nbsp;{label}&nbsp;</span> "
                f"<span style='color:{mute}'>{S.STORE_SHEETS.get(self.tab, self.tab)}：{c:g} {tr('箱')} · €{a:,.2f}</span>")
            self.h_store.show()
        else:
            self.h_store.hide()

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
        cols = store_cols(days)
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.horizontalHeaderItem(6).setText("Colli ✎")
        t.setRowCount(len(sc["items"]))
        for c in range(len(cols)):
            t.setItemDelegateForColumn(c, self._enter if c == 6 else self._thumb if c == 4 else None)
        self._set_bands({c: ("DETTAGLIO ORDINE" if c < 10 else tr("门店数据（订单文件里也有）") if c < 16 else tr("程序建议"))
                             for c in range(len(cols))})
        tot_c = tot_a = 0.0
        for r, it in enumerate(sc["items"]):
            x = self._x(sid, it)
            n = self._cur(sc, sid, it["row"])
            tot_c += n
            tot_a += n * it["pack"] * it["price"]
            sug = self.win._orig(sc, sid, it["row"])
            kind, vt = self.win._verdict(sc, it, sid, n)
            mon = S.monthly_and_need(x, days)[0] if x else None
            inv = x.get("inv_raw", x.get("inv")) if x else None
            row = [self._num(r + 1, color="faint"), self._txt(S.item_code(it), "muted"), self._txt(it.get("sup_code") or "", "muted"),
                   self._txt(it["ean"], "muted"), self._desc(it), self._num(f"{it['pack']:g}"),
                   self._colli_item(sc, sid, it), self._num(f"{n * it['pack']:g}" if n else ""),
                   self._num(f"{it['price']:.2f}"), self._num(f"{n * it['pack'] * it['price']:,.2f}" if n else "", bold=True),
                   self._num("" if inv is None else f"{inv:g}", bold=inv is not None and inv <= 0,
                             color="err" if inv is not None and inv <= 0 else None),
                   self._txt((x or {}).get("last_in") or "", "muted"),
                   self._num(f"{x['last_in_qty']:g}" if x and x.get("last_in_qty") else ""),
                   self._txt((x or {}).get("last_sale") or "", "muted"),
                   self._num("" if mon is None else f"{mon:g}"),
                   self._num(f"{float(x['sales'][0]):g}" if x and x.get("sales") else ""),
                   self._num(f"{sug:g}" if sug else ("0" if x else ""), color="ok" if sug else "faint"),
                   self._txt(vt if x else (tr("同上（重复编号）") if it.get("dup_of") else tr("数据库没有")),
                             "warn" if (x and kind in ("more", "less")) else "faint" if not x else None)]
            for c, item in enumerate(row):
                item.setData(ROW_ROLE, it["row"])
                t.setItem(r, c, item)
        self._fit(4, 6)
        self.note.setText(tr("{0}：{1:g} 箱 · €{2:,.2f}", S.STORE_SHEETS.get(sid, sid), tot_c, tot_a))

    def _render_summary(self):
        sc, t = self.sc, self.table
        stores = self._stores(sc)
        cols = ["NO.", "Cod.Art.", "Descrizione", "PZ*COLLI", "Prezzo"]
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
            row = [self._num(r + 1, color="faint"), self._txt(S.item_code(it), "muted"),
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

    def _fit(self, desc_col, colli_col):
        """列宽按表头和内容量（格子左右各留 10 像素），名称最宽 300；用户拖过的照用户的（这一次打开里）"""
        t = self.table
        t.resizeColumnsToContents()
        for c in range(t.columnCount()):
            w = t.columnWidth(c) + 8
            if c == desc_col:
                w = min(max(w + 36, 200), 270)        # 左边小图
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

    def restyle(self):
        if self.sc:
            self.render()
