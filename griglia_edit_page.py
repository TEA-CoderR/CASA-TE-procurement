# -*- coding: utf-8 -*-
"""
Griglia 第 2 步（2026-11-24）：在程序里改 Griglia。数据和规则见 griglia_db.py（「编辑」那一段）。

  ProductDialog   改一个商品 / 新增商品：常用的几项摆在上面（名称 · 编号 · 品牌 · 箱规 · 分类三级 · 直厂 · 状态），
                  原表其余能改的列收在「更多字段」里；保存前过合理性检查（红的不让存，黄的写明改前改后再确认）
  BatchDialog     选了几行一起改一项：先预览每个商品改前 → 改后，确认才写
  HistoryPage     改动记录：谁 · 哪台电脑 · 什么时候 · 哪个商品 · 哪一项 · 改前 → 改后；每一条能撤销（撤销也记一条）
  CategoryPage    分类表：大类 › 中类 › 小类，每个类几个商品；新加 / 改名（改成已有的名字 = 合并）
每一次保存只写这台电脑自己的改动记录（同步文件夹，和订单跟踪一样不会冲突）；写完让商品目录重新读。
"""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox, QPushButton,
                               QDialog, QGridLayout, QMessageBox, QScrollArea, QFrame, QTreeWidgetItem,
                               QHeaderView, QInputDialog, QCheckBox)

from i18n import tr

STATUS_CHOICES = (("active", "在售"), ("new", "新品"), ("off", "停用"), ("replaced", "被替换"))


def _D():
    import griglia_db as D
    return D


# ------------------------------------------------------------------ 保存（检查 → 确认 → 写记录 → 撤销）
def confirm_and_save(page, items_changes, title, add=None):
    """
    items_changes = [(商品 x 或 None, [{"f", "old", "new"}])]；add = (条码, values, cats, status) 新增时。
    红的不让存；黄的 / 改了几个商品先列出来确认。存好后商品目录重新读、右下角提示带「撤销」。返回 True = 存了
    """
    D = _D()
    cat, win = page.cat, page.win
    reds, yellows = [], []
    if not add:
        items_changes = [(x, [c for c in ch if D._norm_v(c.get("old")) != D._norm_v(c.get("new"))]) for x, ch in items_changes]
        items_changes = [(x, ch) for x, ch in items_changes if ch]
    db_codes = page._db_codes_for(items_changes, add)
    for x, ch in items_changes:
        for lv, text in D.check(cat, x, ch, new_ean=(add[0] if add else None), db_codes=db_codes):
            (reds if lv == "red" else yellows).append(text)
    if reds:
        QMessageBox.warning(win, title, tr("没有保存：") + "\n\n" + "\n".join("• " + r for r in dict.fromkeys(reds)))
        return False
    evs = []
    lines = []
    if add:
        ean, values, cats, status = add
        evs.append(D.make_add(cat, ean, values, cats, status))
        nm = values.get(cat.col.get("name")) or ean
        lines.append(tr("新增商品 {0} · {1}", ean, nm))
    else:
        for x, ch in items_changes:
            ev = D.make_edit(cat, x["ean"], ch)
            if ev:
                evs.append(ev)
                for c in ev["changes"]:
                    lines.append(f"{x['name']} · {tr(D.field_label(cat, c['f']))}：{D.show_value(c['f'], c.get('old'))} → "
                                 f"{D.show_value(c['f'], c.get('new'))}")
    if not evs:
        win.show_toast(tr("没有改动"))
        return False
    msg = "\n".join(lines[:15]) + (("\n" + tr("…还有 {0} 项", len(lines) - 15)) if len(lines) > 15 else "")
    if yellows:
        msg += "\n\n" + tr("请注意：") + "\n" + "\n".join("• " + y for y in list(dict.fromkeys(yellows))[:10])
    msg += "\n\n" + tr("存在同步文件夹，办公室每台电脑都看得到；改动记录里能撤销。")
    if (yellows or len(evs) > 1 or add) and QMessageBox.question(win, title, msg) != QMessageBox.Yes:
        return False
    try:
        saved = D.save(page._root(), evs)
    except OSError as e:
        QMessageBox.warning(win, title, tr("没有保存（写不进同步文件夹）：{0}", e))
        return False
    page._log(tr("Griglia 改动：{0}", "；".join(lines[:5]) + ("…" if len(lines) > 5 else "")))

    def undo():
        for e in saved:
            D.undo(page._root(), e["id"])
        page.reload(then_price=False)
        win.show_toast(tr("已撤销"))
    win.show_toast(tr("已保存 {0} 项改动", len(lines)), action=(tr("撤销"), undo))
    page.reload(then_price=False)
    return True


# ------------------------------------------------------------------ 分类三级下拉
class CatPicker(QWidget):
    """大类 › 中类 › 小类：只能从分类表里选（新类在「分类」页加）"""

    def __init__(self, cat, value=None, parent=None):
        super().__init__(parent)
        self.paths = cat.category_paths()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.boxes = [QComboBox() for _ in range(3)]
        for b in self.boxes:
            b.setMinimumWidth(130)
            h.addWidget(b, 1)
        self.boxes[0].currentIndexChanged.connect(lambda _i: self._fill(1))
        self.boxes[1].currentIndexChanged.connect(lambda _i: self._fill(2))
        self._fill(0)
        self.set_value(value or [])

    def _opts(self, k):
        pre = tuple(self.value()[:k]) if k else ()
        return sorted({p[k] for p in self.paths if len(p) == k + 1 and p[:k] == pre})

    def _fill(self, k):
        if k > 0 and not self.boxes[k - 1].currentData():
            opts = []
        else:
            opts = self._opts(k)
        b = self.boxes[k]
        cur = b.currentData()
        b.blockSignals(True)
        b.clear()
        b.addItem(tr("（不选）") if k else tr("（选大类）"), "")
        for o in opts:
            b.addItem(o, o)
        i = b.findData(cur)
        b.setCurrentIndex(max(0, i))
        b.blockSignals(False)
        if k < 2:
            self._fill(k + 1)

    def set_value(self, v):
        v = (list(v or []) + ["", "", ""])[:3]
        for k in range(3):
            i = self.boxes[k].findData(v[k])
            self.boxes[k].setCurrentIndex(max(0, i))
            if k < 2:
                self._fill(k + 1)

    def value(self):
        return [b.currentData() or "" for b in self.boxes]


# ------------------------------------------------------------------ 改一个商品 / 新增商品
class ProductDialog(QDialog):
    def __init__(self, page, x=None, prefill=None):
        super().__init__(page.win)
        from erp_widgets import fit_to_screen
        D = _D()
        self.page, self.x = page, x
        cat = page.cat
        self.cat, col = cat, cat.col
        self.setWindowTitle(tr("编辑商品") if x else tr("新增商品"))
        self.resize(760, 640)
        v = QVBoxLayout(self)
        v.setSpacing(10)
        head = QLabel((x["name"] + "  ·  " + x["ean"]) if x else tr("新增商品"))
        head.setObjectName("Section")
        head.setWordWrap(True)
        v.addWidget(head)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        self.fields = {}          # f → (取值函数, 原值)
        row = 0
        if not x:
            self.ean = QLineEdit((prefill or {}).get("ean", ""))
            self.ean.setPlaceholderText(tr("扫条码或输入"))
            b = QPushButton(tr("从数据库预填"))
            b.setToolTip(tr("按条码查销售数据库（名称、编号、箱规、供应商），只填空着的格子"))
            b.clicked.connect(self._prefill)
            hb = QHBoxLayout()
            hb.addWidget(self.ean, 1)
            hb.addWidget(b)
            g.addWidget(QLabel(tr("条码")), row, 0)
            g.addLayout(hb, row, 1, 1, 3)
            row += 1
        main = [("name", tr("名称")), ("code", "Cod.Art."), ("cod_forn", "Codice Fornitore"), ("brand", tr("品牌")),
                ("pack", tr("箱规")), ("direct", tr("直厂")), ("ruolo_promo", "RUOLO PROMOZIONE"),
                ("strato", "Strato"), ("pallet", "Qta x Pallet")]
        shown = set()
        k = 0
        for key, label in main:
            c = col.get(key)
            if c is None:
                continue
            shown.add(c)
            w = self._editor(c, key)
            if key == "name":                         # 名称长：占一整行
                g.addWidget(QLabel(label), row, 0)
                g.addWidget(w, row, 1, 1, 3)
                row += 1
                continue
            g.addWidget(QLabel(label), row + k // 2, (k % 2) * 2)
            g.addWidget(w, row + k // 2, (k % 2) * 2 + 1)
            k += 1
        row += (k + 1) // 2
        g.addWidget(QLabel(tr("分类")), row, 0)
        self.cats = CatPicker(cat, x["cats"] if x else (prefill or {}).get("cats"))
        g.addWidget(self.cats, row, 1, 1, 3)
        row += 1
        g.addWidget(QLabel(tr("状态")), row, 0)
        self.status = QComboBox()
        for s, t in STATUS_CHOICES:
            self.status.addItem(tr(t), s)
        st_now = D.current(cat, x, "status") if x else "new"
        st, _, ne = st_now.partition(":")
        self.status.setCurrentIndex(max(0, self.status.findData(st)))
        self.new_ean = QLineEdit(ne)
        self.new_ean.setPlaceholderText(tr("新商品条码"))
        self.status.currentIndexChanged.connect(lambda _i: self.new_ean.setVisible(self.status.currentData() == "replaced"))
        self.new_ean.setVisible(st == "replaced")
        hs = QHBoxLayout()
        hs.addWidget(self.status)
        hs.addWidget(self.new_ean, 1)
        hs.addStretch()
        g.addLayout(hs, row, 1, 1, 3)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        g.setColumnMinimumWidth(1, 230)
        g.setColumnMinimumWidth(3, 230)
        v.addLayout(g)
        # 更多字段：原表其余能改的列
        more = [c for c in cat.editable_columns() if c not in shown and c != col.get("name")]
        self.more_btn = QPushButton(tr("更多字段"))
        self.more_btn.setCheckable(True)
        self.more_btn.setToolTip(tr("原表其余能改的列（竞争对手价、门店、陈列、季节…）"))
        v.addWidget(self.more_btn, 0, Qt.AlignLeft)
        box = QWidget()
        mg = QGridLayout(box)
        mg.setHorizontalSpacing(12)
        mg.setVerticalSpacing(6)
        for i, c in enumerate(more):
            name = cat.columns[c]["name"].replace("\n", " ").strip() or f"#{c + 1}"
            lb = QLabel(name)
            lb.setToolTip(cat.columns[c].get("group") or "")
            mg.addWidget(lb, i // 2, (i % 2) * 2)
            mg.addWidget(self._editor(c), i // 2, (i % 2) * 2 + 1)
        mg.setColumnStretch(1, 1)
        mg.setColumnStretch(3, 1)
        box.setObjectName("MoreBox")
        box.setStyleSheet("QWidget#MoreBox { background: transparent; }")
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.viewport().setAutoFillBackground(False)
        sc.setStyleSheet("QScrollArea { background: transparent; }")
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(box)
        sc.setVisible(False)
        self.more_btn.toggled.connect(sc.setVisible)
        v.addWidget(sc, 1)
        v.addStretch()
        bb = QHBoxLayout()
        bb.addStretch()
        b = QPushButton(tr("取消"))
        b.clicked.connect(self.reject)
        bb.addWidget(b)
        ok = QPushButton(tr("保存") if x else tr("新增"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self._save)
        bb.addWidget(ok)
        v.addLayout(bb)
        if prefill:
            self._apply_prefill(prefill)
        fit_to_screen(self)

    def _editor(self, c, key=None):
        D = _D()
        x, cat = self.x, self.cat
        old = D.current(cat, x, f"c{c}") if x else None
        if key == "direct":
            w = QComboBox()
            w.setEditable(True)
            for name, _n in cat.suppliers("direct"):
                if name:
                    w.addItem(name)
            w.setCurrentText("" if old is None else str(old))
            get = lambda w=w: w.currentText().strip()
        else:
            w = QLineEdit("" if old is None else (f"{old:g}" if isinstance(old, float) else str(old)))
            w.setCursorPosition(0)
            get = lambda w=w: w.text().strip()
        self.fields[f"c{c}"] = (get, old, w)
        return w

    def _value(self, f, text):
        """输入框的字 → 存的值（数字列存数字）"""
        if text == "":
            return None
        c = int(f[1:])
        if c == self.cat.col.get("pack") or self.page._is_num_col(c):
            n = _D()._num_or_none(text)
            if n is not None:
                return int(n) if n.is_integer() else n
        return text

    def changes(self):
        D = _D()
        out = []
        for f, (get, old, _w) in self.fields.items():
            new = self._value(f, get())
            out.append({"f": f, "old": old, "new": new})
        cats = self.cats.value()
        out.append({"f": "cats", "old": D.current(self.cat, self.x, "cats") if self.x else None, "new": cats})
        st = self.status.currentData()
        if st == "replaced":
            st += ":" + self.new_ean.text().strip()
        out.append({"f": "status", "old": D.current(self.cat, self.x, "status") if self.x else None, "new": st})
        return out

    def _save(self):
        D = _D()
        ch = self.changes()
        if self.x:
            ok = confirm_and_save(self.page, [(self.x, ch)], self.windowTitle())
        else:
            ean = self.ean.text().strip()
            values = {int(c["f"][1:]): c["new"] for c in ch if str(c["f"])[1:].isdigit() and c["new"] not in (None, "")}
            cats = next(c["new"] for c in ch if c["f"] == "cats")
            st = next(c["new"] for c in ch if c["f"] == "status")
            checks = [c for c in ch if c["new"] not in (None, "", [])] + [{"f": "cats", "old": None, "new": cats}]
            if not values.get(self.cat.col.get("name")):
                QMessageBox.warning(self, self.windowTitle(), tr("没有保存：") + "\n\n• " + tr("名称不能空"))
                return
            ok = confirm_and_save(self.page, [(None, checks)], self.windowTitle(), add=(ean, values, cats, st))
        if ok:
            self.accept()

    def _prefill(self):
        from erp_widgets import BusyDialog
        ean = self.ean.text().strip()
        if not ean:
            return
        res, err = BusyDialog.run(self, tr("从数据库预填"), tr("正在查"), lambda: self.page.lookup(ean))
        if err or not res:
            QMessageBox.information(self, tr("从数据库预填"), tr("数据库里没找到这个条码") if not err else str(err))
            return
        self._apply_prefill(res)

    def _apply_prefill(self, p):
        col = self.cat.col
        for key in ("name", "code", "pack", "direct", "cod_forn", "brand"):
            c = col.get(key)
            if c is None or p.get(key) in (None, ""):
                continue
            get, _old, w = self.fields.get(f"c{c}", (None, None, None))
            if w is None or get():
                continue
            val = p[key]
            val = f"{val:g}" if isinstance(val, float) else str(val)
            (w.setCurrentText if isinstance(w, QComboBox) else w.setText)(val)


# ------------------------------------------------------------------ 批量改
BATCH_FIELDS = (("cats", "分类"), ("direct", "直厂"), ("brand", "品牌"), ("status", "状态"), ("ruolo_promo", "RUOLO PROMOZIONE"),
                ("pack", "箱规"))


class BatchDialog(QDialog):
    def __init__(self, page, items):
        super().__init__(page.win)
        from erp_widgets import fit_to_screen
        self.page, self.items, self.cat = page, items, page.cat
        self.setWindowTitle(tr("批量修改"))
        self.resize(820, 560)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(tr("选了 {0} 个商品，一起改一项", len(items))))
        h = QHBoxLayout()
        h.addWidget(QLabel(tr("改哪一项")))
        self.field = QComboBox()
        for k, t in BATCH_FIELDS:
            if k in ("cats", "status") or self.cat.col.get(k) is not None:
                self.field.addItem(tr(t), k)
        h.addWidget(self.field)
        h.addWidget(QLabel(tr("改成")))
        self.slot = QHBoxLayout()
        h.addLayout(self.slot, 1)
        v.addLayout(h)
        self.editor, self.get = None, None
        self.field.currentIndexChanged.connect(lambda _i: (self._make_editor(), self._preview()))
        self.t = page.ui.make_table([tr("商品"), tr("条码"), tr("改前"), tr("改后")], stretch_col=0)
        hh = self.t.horizontalHeader()
        for c, wd in ((1, 130), (2, 190), (3, 190)):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.t.setColumnWidth(c, wd)
        v.addWidget(self.t, 1)
        bb = QHBoxLayout()
        self.note = QLabel("")
        self.note.setObjectName("Faint")
        bb.addWidget(self.note, 1)
        b = QPushButton(tr("取消"))
        b.clicked.connect(self.reject)
        bb.addWidget(b)
        ok = QPushButton(tr("确认修改"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self._save)
        bb.addWidget(ok)
        v.addLayout(bb)
        self._make_editor()
        self._preview()
        fit_to_screen(self)

    def _key(self):
        return self.field.currentData()

    def _f(self):
        k = self._key()
        return k if k in ("cats", "status") else f"c{self.cat.col[k]}"

    def _make_editor(self):
        while self.slot.count():
            w = self.slot.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
        k = self._key()
        if k == "cats":
            w = CatPicker(self.cat)
            for b in w.boxes:
                b.currentIndexChanged.connect(lambda _i: self._preview())
            self.get = w.value
        elif k == "status":
            w = QComboBox()
            for s, t in STATUS_CHOICES[:3]:
                w.addItem(tr(t), s)
            w.currentIndexChanged.connect(lambda _i: self._preview())
            self.get = w.currentData
        elif k == "direct":
            w = QComboBox()
            w.setEditable(True)
            for name, _n in self.cat.suppliers("direct"):
                if name:
                    w.addItem(name)
            w.setCurrentText("")
            w.currentTextChanged.connect(lambda _t: self._preview())
            self.get = lambda: w.currentText().strip() or None
        else:
            w = QLineEdit()
            w.textChanged.connect(lambda _t: self._preview())
            self.get = lambda: w.text().strip() or None
        self.editor = w
        self.slot.addWidget(w)

    def _new(self):
        v = self.get() if self.get else None
        if self._key() == "pack" and v is not None:
            n = _D()._num_or_none(v)
            v = (int(n) if n is not None and n.is_integer() else n) if n is not None else v
        return v

    def changes(self):
        D = _D()
        f, new = self._f(), self._new()
        out = []
        for x in self.items:
            old = D.current(self.cat, x, f)
            out.append((x, [{"f": f, "old": old, "new": new}]))
        return out

    def _preview(self):
        from main import table_set_row
        D = _D()
        f, new = self._f(), self._new()
        rows = []
        n = 0
        for x, ch in self.changes():
            o = ch[0]["old"]
            same = D._norm_v(o) == D._norm_v(new)
            n += 0 if same else 1
            rows.append([x["name"], x["ean"], D.show_value(f, o), "=" if same else D.show_value(f, new)])
        self.t.setRowCount(len(rows))
        for k, r in enumerate(rows):
            table_set_row(self.t, k, r)
        self.note.setText(tr("{0} 个会改，{1} 个已经是这个值", n, len(rows) - n))

    def _save(self):
        if confirm_and_save(self.page, self.changes(), self.windowTitle()):
            self.accept()


# ------------------------------------------------------------------ 改动记录
class HistoryPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.rows = []
        self.page = None

    def _cat_page(self):
        return getattr(self.win, "grid_db", None)

    def build(self):
        from page_kit import Hero, Card
        from erp_widgets import TableSortFilter
        import settings as ST
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 20)
        lay.setSpacing(14)
        self.hero = Hero(tr("Griglia 改动记录"), "",
                         tip=tr("每一次改动：谁 · 哪台电脑 · 什么时候 · 哪个商品 · 哪一项 · 改前 → 改后。撤销也记一条，原来的不删"))
        self.b_undo = QPushButton(tr("撤销这一条"))
        self.b_undo.setToolTip(tr("把选中的这一次保存撤销（同一次保存里的几项一起撤销）"))
        self.b_undo.clicked.connect(self.undo)
        self.hero.add(self.b_undo)
        lay.addWidget(self.hero)
        card = Card(margins=(16, 12, 16, 12))
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("商品 / 条码 / 谁 / 哪一项"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(300)
        self.search.textChanged.connect(lambda _t: self.ctl.render())
        bar.addWidget(self.search)
        self.who = QComboBox()
        self.who.currentIndexChanged.connect(lambda _i: self.ctl.render())
        bar.addWidget(self.who)
        self.show_undone = QCheckBox(tr("显示已撤销的"))
        self.show_undone.setChecked(True)
        self.show_undone.toggled.connect(lambda _b: self.ctl.render())
        bar.addWidget(self.show_undone)
        bar.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        card.v.addLayout(bar)
        heads = [tr("时间"), tr("谁"), tr("电脑"), tr("商品"), tr("条码"), tr("哪一项"), tr("改前"), tr("改后"), tr("状态")]
        self.t = self.ui.make_table(heads, stretch_col=None)
        self.t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for c, wd in enumerate((130, 90, 110, 260, 120, 140, 160, 160, 80)):
            self.t.setColumnWidth(c, wd)
        self.t.itemSelectionChanged.connect(self._sel)
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, hide=self._hidden)
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        self.ctl.remember(self.settings.setdefault("griglia_db_cols", {}), "history",
                          lambda: ST.save_settings(self.settings), list(range(len(heads))))
        card.v.addWidget(self.t, 1)
        lay.addWidget(card, 1)
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self.refresh())
        self.page = w
        self._sel()
        return w

    def _text(self, i, c):
        r = self.rows[i]
        vals = ((r["t"] or "")[:16].replace("T", " "), r["user"], r["pc"], r["name"], r["ean"], tr(r["field"]),
                r["old"], r["new"], tr("已撤销") if r["undone"] else "")
        return str(vals[c] or "")

    def _fill(self, i, k):
        from main import table_set_row
        table_set_row(self.t, k, [self._text(i, c) for c in range(9)])
        if self.rows[i]["undone"]:
            for c in range(9):
                it = self.t.item(k, c)
                if it is not None:
                    it.setForeground(QColor(self.ui.T("faint")))
                    f = it.font()
                    f.setStrikeOut(c in (6, 7))
                    it.setFont(f)

    def _hidden(self, i):
        r = self.rows[i]
        if r["undone"] and not self.show_undone.isChecked():
            return True
        who = self.who.currentData()
        if who and r["user"] != who:
            return True
        q = self.search.text().strip().lower()
        return bool(q) and q not in " ".join(str(r.get(k) or "") for k in ("name", "ean", "user", "field", "old", "new")).lower()

    def refresh(self):
        cp = self._cat_page()
        cat = cp.cat if cp else None
        if cat is None:
            self.rows = []
            self.hero.set_sub(tr("还没有导入 Griglia"))
            self.ctl.render()
            return
        self.rows = _D().history(cat)
        users = sorted({r["user"] for r in self.rows if r["user"]})
        cur = self.who.currentData()
        self.who.blockSignals(True)
        self.who.clear()
        self.who.addItem(tr("所有人"), "")
        for u in users:
            self.who.addItem(u, u)
        self.who.setCurrentIndex(max(0, self.who.findData(cur)))
        self.who.blockSignals(False)
        n_ev = len({r["id"] for r in self.rows})
        self.hero.set_sub(tr("{0} 次保存 · {1} 项改动 · 从 {2} 导入以后", n_ev, len(self.rows),
                             (cat.imp.get("t") or "")[:10]))
        self.ctl.render()

    def _picked(self):
        rows = self.t.selectionModel().selectedRows() if self.t.selectionModel() else []
        if not rows:
            return None
        k = rows[0].row()
        return self.rows[self.ctl.visible[k]] if 0 <= k < len(self.ctl.visible) else None

    def _sel(self):
        r = self._picked()
        self.b_undo.setEnabled(bool(r and not r["undone"]))

    def undo(self):
        r = self._picked()
        cp = self._cat_page()
        if not r or r["undone"] or cp is None:
            return
        same = [x for x in self.rows if x["id"] == r["id"]]
        text = "\n".join(f"• {x['name'] or x['ean']} · {tr(x['field'])}：{x['old']} → {x['new']}" for x in same[:10])
        if QMessageBox.question(self.win, tr("撤销这一条"),
                                tr("撤销 {0} {1} 的这次保存：", r["user"], (r["t"] or "")[:16].replace("T", " "))
                                + "\n\n" + text + "\n\n" + tr("改回改前的值；撤销也会记一条，办公室每台电脑都看得到。")) != QMessageBox.Yes:
            return
        _D().undo(cp._root(), r["id"])
        cp.reload(then_price=False)
        self.win.show_toast(tr("已撤销"))

    def restyle(self):
        self.ctl.render()


# ------------------------------------------------------------------ 分类表
class CategoryPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.page = None

    def _cat_page(self):
        return getattr(self.win, "grid_db", None)

    def build(self):
        from page_kit import Hero, Card
        from griglia_catalog_page import _two_col
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 20)
        lay.setSpacing(14)
        self.hero = Hero(tr("Griglia 分类"), "",
                         tip=tr("大类 › 中类 › 小类。改名会把这个类下面所有商品一起改；改成同一级已有的名字 = 合并。商品只能从这张表里选分类"))
        self.b_add = QPushButton(tr("新加分类"))
        self.b_add.setToolTip(tr("在选中的类下面加一个（什么都不选 = 加大类）"))
        self.b_add.clicked.connect(self.add)
        self.b_ren = QPushButton(tr("改名 / 合并"))
        self.b_ren.clicked.connect(self.rename)
        for b in (self.b_add, self.b_ren):
            self.hero.add(b)
        lay.addWidget(self.hero)
        card = Card(margins=(16, 12, 16, 12))
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜分类"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(300)
        self.search.textChanged.connect(lambda _t: self.refresh())
        card.v.addWidget(self.search)
        self.tree = _two_col(root=True)
        self.tree.itemSelectionChanged.connect(self._sel)
        card.v.addWidget(self.tree, 1)
        lay.addWidget(card, 1)
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self.refresh())
        self.page = w
        self._sel()
        return w

    def refresh(self):
        from griglia_catalog_page import _row
        cp = self._cat_page()
        cat = cp.cat if cp else None
        self.tree.clear()
        if cat is None:
            self.hero.set_sub(tr("还没有导入 Griglia"))
            return
        from collections import Counter
        cnt = Counter()
        for x in cat.items:
            c = [s for s in (x["cats"] + ["", "", ""])[:3]]
            for k in range(3):
                if c[k]:
                    cnt[tuple(c[:k + 1])] += 1
        paths = cat.category_paths()
        q = self.search.text().strip().lower()
        nodes = {}
        for p in sorted(paths, key=lambda p: (len(p), [s.upper() for s in p])):
            if q and not any(q in s.lower() for s in p) and not any(q in s.lower() for s in
                                                                   (pp for x in paths if x[:len(p)] == p for pp in x)):
                continue
            parent = nodes.get(p[:-1]) if len(p) > 1 else None
            if len(p) > 1 and parent is None:
                continue
            it = _row(self.tree, parent, p[-1], cnt.get(p, 0), list(p), self.ui.T)
            nodes[p] = it
            if q:
                it.setExpanded(True)
        n1 = sum(1 for p in paths if len(p) == 1)
        n2 = sum(1 for p in paths if len(p) == 2)
        n3 = sum(1 for p in paths if len(p) == 3)
        self.hero.set_sub(tr("{0} 个大类 · {1} 个中类 · {2} 个小类", n1, n2, n3))

    def _path(self):
        it = self.tree.currentItem()
        return list(it.data(0, Qt.UserRole) or []) if it and it.isSelected() else []

    def _sel(self):
        self.b_ren.setEnabled(bool(self._path()))
        p = self._path()
        self.b_add.setText(tr("新加分类") if len(p) < 3 else tr("新加分类"))
        self.b_add.setEnabled(len(p) < 3)

    def add(self):
        cp = self._cat_page()
        if cp is None or cp.cat is None:
            return
        p = self._path()
        level = [tr("大类"), tr("中类"), tr("小类")][len(p)]
        where = (" › ".join(p) + " › ") if p else ""
        name, ok = QInputDialog.getText(self.win, tr("新加分类"), tr("{0}{1}名称", where, level))
        name = (name or "").strip().upper()
        if not ok or not name:
            return
        path = p + [name]
        if tuple(path) in cp.cat.category_paths():
            QMessageBox.information(self.win, tr("新加分类"), tr("这个分类已经有了"))
            return
        D = _D()
        D.save(cp._root(), [{"op": "cat_add", "id": D.new_id(), "base": cp.cat.imp.get("base"), "path": path}])
        cp.reload(then_price=False)
        self.win.show_toast(tr("已加分类 {0}", " › ".join(path)))

    def rename(self):
        cp = self._cat_page()
        p = self._path()
        if cp is None or cp.cat is None or not p:
            return
        name, ok = QInputDialog.getText(self.win, tr("改名 / 合并"), tr("「{0}」改成", " › ".join(p)), text=p[-1])
        name = (name or "").strip().upper()
        if not ok or not name or name == p[-1].upper():
            return
        cat = cp.cat
        n = sum(1 for x in cat.items if [s.upper() for s in x["cats"][:len(p)]] == [s.upper() for s in p])
        merge = tuple(p[:-1] + [name]) in {tuple(s.upper() for s in q) for q in cat.category_paths()}
        msg = tr("「{0}」改成「{1}」：{2} 个商品的分类跟着改。", " › ".join(p), name, n)
        if merge:
            msg += "\n\n" + tr("同一级已经有「{0}」：两个类会合并成一个。", name)
        msg += "\n\n" + tr("存在同步文件夹，办公室每台电脑都看得到；改动记录里能撤销。")
        if QMessageBox.question(self.win, tr("改名 / 合并"), msg) != QMessageBox.Yes:
            return
        D = _D()
        D.save(cp._root(), [{"op": "cat_rename", "id": D.new_id(), "base": cat.imp.get("base"), "path": p, "to": name}])
        cp.reload(then_price=False)
        self.win.show_toast(tr("已改名：{0} 个商品", n))

    def restyle(self):
        self.refresh()
