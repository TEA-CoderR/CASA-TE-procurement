"""
Griglia「都不做了」→ 找替代商品（2026-11-09 用户：很多不做了是换了规格，40 洗 → 50 洗、950 ml → 1000 ml，
在新报价里找出来让我核对，再加进 Griglia；用户定：下面加一行新的，旧的留着）。
  左：不做了的商品一行一个（勾选 · 不做了的 · 规格 · 替代成 · 新规格 · 像不像 · 几家 · 最低价），TableSortFilter
  右：选中那一行：旧商品（图 + 资料）→ 替代成（图 + 各家价格），候选列表（「不替代」+ 程序找的 + 搜这周报价）
  下：勾了几个 ·「加进 Griglia（n）」先确认 → commands.griglia_add_replacements（备份 + Excel 插行）
业务在 griglia_replace.py / commands.py。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
                               QListWidget, QListWidgetItem, QSplitter, QTableWidgetItem, QHeaderView, QMessageBox,
                               QAbstractItemView, QFrame, QTableWidget)

from i18n import tr
from theme import T

COLS = [("pick", "", 34), ("old", "不做了的商品", 250), ("size0", "规格", 78), ("new", "替代成", 250),
        ("size1", "新规格", 78), ("score", "像不像", 64), ("n", "几家", 48), ("best", "最低价", 70)]
NUM = (5, 6, 7)


def pct(c):
    return min(100, round((c or {}).get("score", 0) * 100))


def _money(v):
    return f"€{v:,.2f}" if v else ""


class ReplaceDialog(QDialog):
    def __init__(self, parent, settings, grid, sugg, pool):
        super().__init__(parent)
        import griglia_replace as GR
        self.settings, self.grid, self.rows, self.pool = settings, grid, sugg, pool
        self.result_info = None
        self.choice = {i: (r["cands"][0] if r["cands"] else None) for i, r in enumerate(sugg)}
        self.checked = {i for i, r in enumerate(sugg) if r["cands"] and r["cands"][0]["score"] >= GR.SURE}
        self._filling = False
        self.setWindowTitle(tr("找替代商品"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)
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
        self.search.setFixedWidth(200)
        head.addWidget(self.search)
        self.show_cb = QComboBox()
        for text, k in ((tr("全部"), "all"), (tr("找到像的"), "found"), (tr("没找到"), "none"), (tr("已勾选"), "on")):
            self.show_cb.addItem(text, k)
        head.addWidget(self.show_cb)
        lay.addLayout(head)
        # 左表格
        self.t = QTableWidget(0, len(COLS))
        self.t.setHorizontalHeaderLabels([tr(h) if h else "" for _f, h, _w in COLS])
        self.t.verticalHeader().setVisible(False)
        self.t.verticalHeader().setDefaultSectionSize(34)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.t.setSelectionMode(QAbstractItemView.SingleSelection)
        self.t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t.setShowGrid(False)
        hh = self.t.horizontalHeader()
        for c, (_f, _h, w) in enumerate(COLS):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.t.setColumnWidth(c, w)
        hh.setStretchLastSection(False)
        self.t.horizontalHeaderItem(0).setToolTip(tr("勾上的加进 Griglia"))
        self.t.horizontalHeaderItem(5).setToolTip(tr("名字、品牌、条码前缀（同一个厂家）、规格合起来算的，100% = 很像。只是参考，要人看"))
        from erp_widgets import TableSortFilter
        import settings as ST
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, key=self._key,
                                   hide=self._hidden, numeric=NUM)
        self.ctl.remember(settings.setdefault("griglia_cols", {}), "replace", lambda: ST.save_settings(settings),
                          [f for f, _h, _w in COLS])
        self.t.itemChanged.connect(self._item_changed)
        self.t.itemSelectionChanged.connect(self._show)
        self.search.textChanged.connect(lambda _: self.ctl.render())
        self.show_cb.currentIndexChanged.connect(lambda _: self.ctl.render())
        # 右：详情
        side = QFrame()
        side.setObjectName("Panel")
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 12, 14, 12)
        sv.setSpacing(8)
        self.old_img, self.old_name, self.old_meta = self._product(sv, tr("不做了的"))
        self.new_img, self.new_name, self.new_meta = self._product(sv, tr("替代成"))
        cap = QLabel(tr("候选"))
        cap.setObjectName("Faint")
        sv.addWidget(cap)
        self.q = QLineEdit()
        self.q.setPlaceholderText(tr("在这周报价里搜…"))
        self.q.setToolTip(tr("程序找的都不对：输入名称里的词或条码，在这周所有批发商报价（Griglia 里没有的商品）里找"))
        self.q.textChanged.connect(lambda _: self._fill_cands())
        sv.addWidget(self.q)
        self.cands = QListWidget()
        self.cands.setWordWrap(True)
        self.cands.setSpacing(3)
        self.cands.itemClicked.connect(self._picked)
        sv.addWidget(self.cands, 1)
        side.setMinimumWidth(360)
        sp = QSplitter(Qt.Horizontal)
        sp.addWidget(self.t)
        sp.addWidget(side)
        sp.setStretchFactor(0, 3)
        sp.setStretchFactor(1, 2)
        sp.setChildrenCollapsible(False)
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
        self.resize(1260, 720)
        from erp_widgets import fit_to_screen
        fit_to_screen(self)
        self.ctl.render()
        if self.rows:
            self.t.selectRow(0)
        self._update_foot()

    # ------------------------------------------------------------ 右边的商品卡
    def _product(self, v, caption):
        cap = QLabel(caption)
        cap.setObjectName("Faint")
        v.addWidget(cap)
        row = QHBoxLayout()
        row.setSpacing(10)
        img = QLabel()
        img.setFixedSize(72, 72)
        img.setAlignment(Qt.AlignCenter)
        row.addWidget(img, 0, Qt.AlignTop)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        name = QLabel("")
        name.setWordWrap(True)
        name.setStyleSheet("font-weight:700;")
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        meta = QLabel("")
        meta.setObjectName("Faint")
        meta.setWordWrap(True)
        meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tv.addWidget(name)
        tv.addWidget(meta)
        tv.addStretch()
        row.addLayout(tv, 1)
        v.addLayout(row)
        return img, name, meta

    def _set_img(self, lab, ean):
        import product_images as PI
        PI.set_label_image(lab, ean, 72, self.settings)
        if lab.isHidden():                      # 没图：留着位置，别让文字跳来跳去
            lab.show()

    # ------------------------------------------------------------ 表格
    def _text(self, i, c):
        r, ch = self.rows[i], self.choice.get(i)
        f = COLS[c][0]
        old = r["old"]
        if f == "pick":
            return "✓" if i in self.checked else ""
        if f == "old":
            return old.get("name") or old["ean"]
        if f == "size0":
            return (ch or {}).get("size_from") or ""
        if f == "new":
            return ch["name"] if ch else (tr("— 不替代") if r["cands"] else tr("— 没找到像的"))
        if f == "size1":
            return (ch or {}).get("size_to") or ""
        if f == "score":
            return f"{pct(ch)}%" if ch else ""
        if f == "n":
            return str(ch["n"]) if ch else ""
        if f == "best":
            return _money(ch["best"]) if ch else ""
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
            for c in range(len(COLS)):
                f = COLS[c][0]
                it = QTableWidgetItem("" if f == "pick" else self._text(i, c))
                it.setData(Qt.UserRole, i)
                if f == "pick":
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.ItemIsUserCheckable if ch else Qt.NoItemFlags))
                    it.setCheckState(Qt.Checked if i in self.checked else Qt.Unchecked)
                    if not ch:
                        it.setToolTip(tr("先在右边选一个替代品"))
                elif c in NUM:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if f == "old":
                    old = self.rows[i]["old"]
                    it.setToolTip(f"{old.get('name') or ''}\n{old['ean']}")
                elif f == "new":
                    if ch:
                        it.setToolTip(f"{ch['name']}\n{ch['ean']}")
                    else:
                        it.setForeground(QColor(T("faint")))
                elif f == "score" and ch:
                    p = pct(ch)
                    it.setForeground(QColor(T("ok" if p >= 80 else "warn" if p >= 55 else "err")))
                elif f in ("size0", "size1") and ch and ch.get("size_from") != ch.get("size_to"):
                    it.setForeground(QColor(T("accent")))       # 规格变了（40 → 50 洗）：蓝字
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
        if self._filling or it.column() != 0:
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
        import griglia_replace as GR
        if on:
            self.checked |= {i for i, c in self.choice.items() if c and c["score"] >= GR.SURE}
        else:
            self.checked.clear()
        for i in range(len(self.rows)):
            self._refresh_row(i)
        self._update_foot()

    def _update_foot(self):
        n = len(self.checked)
        self.b_add.setText(tr("加进 Griglia（{0}）", n))
        self.b_add.setEnabled(n > 0)
        changed = sum(1 for i in self.checked if (self.choice[i].get("size_from") or "") != (self.choice[i].get("size_to") or ""))
        self.status.setText(tr("勾了 {0} 个 · 其中 {1} 个换了规格", n, changed) if n else tr("勾上要加进 Griglia 的"))

    # ------------------------------------------------------------ 右边
    def _show(self):
        i = self._current()
        if i is None:
            return
        old = self.rows[i]["old"]
        self._set_img(self.old_img, old["ean"])
        self.old_name.setText(old.get("name") or "")
        bits = [old["ean"]]
        if old.get("code"):
            bits.append(tr("编号 {0}", old["code"]))
        if old.get("pack"):
            bits.append(tr("{0} 件/箱", old["pack"]))
        if old.get("supplier"):
            bits.append(old["supplier"])
        p = old.get("best") or old.get("price")
        if p:
            bits.append(_money(p))
        self.old_meta.setText(" · ".join(str(b) for b in bits))
        self._show_new(i)
        self.q.blockSignals(True)
        self.q.clear()
        self.q.blockSignals(False)
        self._fill_cands()

    def _show_new(self, i):
        ch = self.choice.get(i)
        if not ch:
            self.new_img.clear()
            self.new_name.setText(tr("— 不替代"))
            self.new_meta.setText("")
            return
        self._set_img(self.new_img, ch["ean"])
        self.new_name.setText(ch["name"])
        bits = [ch["ean"]]
        if ch.get("size_from") or ch.get("size_to"):
            bits.append(f"{ch.get('size_from') or '?'} → {ch.get('size_to') or '?'}")
        if ch.get("pack"):
            bits.append(tr("{0} 件/箱", ch["pack"]))
        prices = sorted((ch.get("prices") or {}).items(), key=lambda kv: kv[1])
        line2 = " · ".join(f"{k} {_money(v)}" for k, v in prices)
        self.new_meta.setText(" · ".join(str(b) for b in bits) + ("\n" + line2 if line2 else ""))

    def _fill_cands(self):
        import griglia_replace as GR
        i = self._current()
        self.cands.clear()
        if i is None:
            return
        old = self.rows[i]["old"]
        q = self.q.text().strip()
        if len(q) >= 2:
            ws = [w for w in GR.words(q)] or [q.upper()]
            hits = [c for c in self.pool if c["ean"].startswith(q) or all(w in c["name"].upper() for w in ws)]
            cands = sorted((GR.rescore(old, c) for c in hits[:400]), key=lambda c: -c["score"])[:40]
            if not cands:
                it = QListWidgetItem(tr("这周报价里没有"))
                it.setFlags(Qt.NoItemFlags)
                self.cands.addItem(it)
                return
        else:
            cands = self.rows[i]["cands"]
        none = QListWidgetItem(tr("— 不替代"))
        none.setData(Qt.UserRole, None)
        self.cands.addItem(none)
        cur = self.choice.get(i)
        for c in cands:
            size = f"{c.get('size_from') or '?'} → {c.get('size_to') or '?'} · " if (c.get("size_from") or c.get("size_to")) else ""
            text = f"{c['name']}\n{c['ean']} · {size}{tr('像不像')} {pct(c)}% · {tr('{0} 家', c['n'])} · {_money(c['best'])}"
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, c)
            if c.get("fresh"):
                it.setToolTip(tr("这周新上架的"))
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
