"""
商品详情窗口（2026-11-01 用户：检查清单右边详情栏太小，双击商品打开一个窗口看更多，入库记录最重要）。

上面：商品图 + 名称（促销中标红）+ 资料（编号、条码、商家、商家编码、Iva、箱规、进价、售价、类别…）+ 各店库存 / 近 30 天销量；
下面三个标签页：入库记录（caricolistalist，默认）/ 每天销量（近 60 天，五家店，本店实际卖价）/ 每月销量（近两年）。
数据库只读，后台线程查（reorder_service.product_card），查完用 Signal 回到界面。表格按「表格一律按这套做」：
TableSortFilter（点表头排序、右键筛选）+ 列可拖、记住（settings reorder.card_*）。
"""
import threading
import zlib
from datetime import date, datetime

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QWidget, QLabel, QLineEdit, QHBoxLayout, QVBoxLayout, QGridLayout, QFrame,
                               QTabWidget, QHeaderView)

from i18n import tr
import reorder_service as R

DAYS = 60
_TAG_FID = {"date": 1, "qty": 2, "type": 3, "sup": 4, "doc": 5, "price": 6, "store": 7}
_TAG_W = {"date": 100, "qty": 70, "type": 70, "sup": 240, "doc": 150, "price": 80, "store": 100}


def _fid(col, tag):
    return _TAG_FID[tag] if tag in _TAG_FID else 100 + zlib.crc32(str(col).encode("utf-8")) % 100000


def _blank(v):
    return v is None or (isinstance(v, float) and v != v) or str(v).strip() in ("", "nan", "NaT", "None")


def fmt_cell(v, tag=None):
    """入库记录一格 → (显示文字, 排序值)"""
    if _blank(v):
        return "", ""
    if hasattr(v, "strftime"):
        try:
            t = v.strftime("%d/%m/%Y")
            if isinstance(v, datetime) and (v.hour or v.minute):
                t += v.strftime(" %H:%M")
            return t, v.isoformat()
        except (ValueError, AttributeError):
            return str(v), str(v)
    if tag == "store":
        try:
            s = int(float(v))
            return R.STORES.get(s, str(s)), s
        except (TypeError, ValueError):
            pass
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = float(v)
        if tag == "price":
            return f"€{f:.3f}", f
        return (f"{f:g}" if abs(f) < 1e15 else str(int(f))), f
    return str(v).strip(), str(v).strip().lower()


def default_hidden_cols(cols):
    """内部编号一类的列默认藏起来（右键表头「显示哪些列…」可以打开）"""
    out = []
    for c, _h, tag in cols:
        n = str(c).lower()
        if tag is None and (n == "id" or n.startswith("id") or n.endswith("id")):
            out.append(_fid(c, tag))
    return out


class _Sig(QObject):
    done = Signal(object)


class _Grid:
    """一张表 + 搜索框 + 「n / 总数」：rows[i] = 显示文字列表，keys[i] = 排序值列表，colors = {(i, c): 颜色记号}"""

    def __init__(self, ui, heads, numeric, cfg, key, save, fids, hidden=(), widths=None):
        self.ui, self.rows, self.keys, self.colors, self.tips = ui, [], [], {}, {}
        self.w = QWidget()
        v = QVBoxLayout(self.w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(6)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索"))
        self.search.setFixedWidth(220)
        bar.addWidget(self.search)
        bar.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        v.addLayout(bar)
        self.t = ui.make_table(heads, stretch_col=None, numeric_cols=numeric)
        self.t.verticalHeader().setDefaultSectionSize(30)
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, f in enumerate(fids):
            self.t.setColumnWidth(c, (widths or {}).get(f, 90))
        v.addWidget(self.t, 1)
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), lambda i, c: self.rows[i][c], self._fill,
                                   key=lambda i, c: self.keys[i][c], hide=self._hidden, numeric=numeric)
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        self.ctl.remember(cfg, key, save, fids, default_hidden=list(hidden), ver=1)
        self.search.textChanged.connect(lambda _: self.ctl.render())

    def _hidden(self, i):
        q = self.search.text().strip().lower()
        return bool(q) and q not in " ".join(self.rows[i]).lower()

    def _fill(self, i, k):
        T = self.ui.T
        self.ui.table_set_row(self.t, k, self.rows[i])
        for c in range(self.t.columnCount()):
            tok = self.colors.get((i, c))
            it = self.t.item(k, c)
            if it is None:
                continue
            if tok:
                it.setForeground(QColor(T(tok)))
            tip = self.tips.get((i, c))
            if tip:
                it.setToolTip(tip)

    def set(self, rows, keys, colors=None, tips=None):
        self.rows, self.keys, self.colors, self.tips = rows, keys, colors or {}, tips or {}
        self.ctl.render()


class ProductWindow(QDialog):
    """
    一个窗口反复用：show_item(x) 换成另一个商品。x = 检查清单的一行（code / ean / name / store / promo …），
    months = (每月销量, (年, 月) 列表) 或 None（检查清单已经读好的近两年每月销量）
    """

    def __init__(self, parent, ui, settings):
        super().__init__(parent, Qt.Window)
        self.ui, self.settings = ui, settings
        self.cfg = settings.setdefault("reorder", {})
        self.resize(1120, 720)
        self.sig = _Sig()
        self.sig.done.connect(self._arrived)
        self._token = 0
        self.x = None
        T = ui.T
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        # ---- 上面：图 | 名称 + 资料 | 各店库存 ----
        top = QHBoxLayout()
        top.setSpacing(16)
        self.img = QLabel()
        self.img.setFixedSize(150, 150)
        self.img.setAlignment(Qt.AlignCenter)
        top.addWidget(self.img, 0, Qt.AlignTop)
        mid = QVBoxLayout()
        mid.setSpacing(6)
        self.title = QLabel("")
        self.title.setStyleSheet("font-size:16px; font-weight:700;")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        mid.addWidget(self.title)
        self.promo = QLabel("")
        self.promo.setStyleSheet(f"color:{T('err')}; font-weight:700;")
        self.promo.hide()
        mid.addWidget(self.promo)
        self.info = QGridLayout()
        self.info.setHorizontalSpacing(14)
        self.info.setVerticalSpacing(4)
        mid.addLayout(self.info)
        mid.addStretch()
        top.addLayout(mid, 1)
        self.stock_box = QFrame()
        self.stock_box.setObjectName("MiniCard")
        self.stock = QGridLayout(self.stock_box)
        self.stock.setContentsMargins(12, 10, 12, 10)
        self.stock.setHorizontalSpacing(14)
        self.stock.setVerticalSpacing(4)
        top.addWidget(self.stock_box, 0, Qt.AlignTop)
        root.addLayout(top)
        # ---- 下面：入库记录 / 每天销量 / 每月销量 ----
        self.tabs = QTabWidget()
        self.rec_box, self.day_box = QWidget(), QWidget()
        for b in (self.rec_box, self.day_box):
            QVBoxLayout(b).setContentsMargins(0, 0, 0, 0)
        from erp_widgets import MonthGrid
        mw = QWidget()
        mv = QVBoxLayout(mw)
        mv.setContentsMargins(0, 10, 0, 0)
        self.months = MonthGrid()
        self.months.setMaximumWidth(560)
        mv.addWidget(self.months)
        self.months_note = QLabel("")
        self.months_note.setObjectName("Faint")
        mv.addWidget(self.months_note)
        mv.addStretch()
        self.tabs.addTab(self.rec_box, tr("入库记录"))
        self.tabs.addTab(self.day_box, tr("每天销量"))
        self.tabs.addTab(mw, tr("每月销量"))
        root.addWidget(self.tabs, 1)
        self.msg = QLabel("")
        self.msg.setObjectName("Faint")
        root.addWidget(self.msg)
        self.rec = self.day = None

    # ---------- 换一个商品 ----------
    def show_item(self, x, months=None):
        import product_images as PI
        self.x = x
        self._token += 1
        name = x.get("name") or str(x.get("code"))
        self.setWindowTitle(tr("商品详情 — {0}", name))
        self.title.setText(name)
        PI.set_label_image(self.img, x.get("ean"), 150, self.settings)
        self.img.show()
        self._show_promo(x.get("promo"))
        self._clear(self.info)
        self._clear(self.stock)
        self._set_info([("编号", str(x.get("code") or "")), ("条码", str(x.get("ean") or "")),
                        ("商家", x.get("sup_name") or ""), ("商家编码", x.get("sup_code") or "")])
        self._placeholder(self.rec_box, tr("正在读取…"))
        self._placeholder(self.day_box, tr("正在读取…"))
        self.tabs.setTabText(0, tr("入库记录"))
        if months:
            vals, keys = months
            self.months.set_data(vals, keys)
            cur, prev, full = self.months.ytd()
            self.months_note.setText(tr("{0} · 今年 1–{1} 月 {2:g} 件 · 去年全年 {3:g} 件", R.STORES.get(x.get("store"), ""),
                                        keys[-1][1], round(cur, 1), round(full, 1)))
        else:
            self.months.set_message(tr("没读到（数据库）"))
            self.months_note.setText("")
        self.msg.setText("")
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        code = str(x.get("code") or "")
        if not code.isdigit():
            self.msg.setText(tr("数据库里没有这个商品"))
            self._placeholder(self.rec_box, tr("数据库里没有这个商品"))
            self._placeholder(self.day_box, tr("数据库里没有这个商品"))
            return
        token, dbs = self._token, self.settings.get("db") or None

        def work():
            try:
                db = R.make_db(dbs)
                try:
                    res = R.product_card(db, code, DAYS)
                finally:
                    db.engine.dispose()
            except Exception as e:
                res = {"errors": [str(e)]}
            self.sig.done.emit((token, res))
        threading.Thread(target=work, daemon=True).start()

    def _show_promo(self, p):
        if p:
            self.promo.setText(tr("促销中 −{0:.0%} · 标价 €{1:.2f} → 实卖 €{2:.2f}", p["pct"], p["list"], p["sold"]))
            self.promo.setToolTip(tr("近 {0} 天本店卖出的 {1:g} 件里 {2:.0%} 是打折价", R.PROMO_DAYS, p["qty"], p["share"]))
            self.promo.show()
            self.title.setStyleSheet(f"font-size:16px; font-weight:700; color:{self.ui.T('err')};")
        else:
            self.promo.hide()
            self.title.setStyleSheet("font-size:16px; font-weight:700;")

    @staticmethod
    def _clear(lay):
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.deleteLater()

    @staticmethod
    def _placeholder(box, text):
        lay = box.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        lb = QLabel(text)
        lb.setObjectName("Faint")
        lb.setAlignment(Qt.AlignCenter)
        lay.addWidget(lb)

    def _set_info(self, fields):
        """资料：每行两对「标签 值」"""
        self._clear(self.info)
        for n, (a, b) in enumerate(fields):
            r, c = divmod(n, 2)
            la = QLabel(tr(a))
            la.setObjectName("Faint")
            lb = QLabel(str(b))
            lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.info.addWidget(la, r, c * 2)
            self.info.addWidget(lb, r, c * 2 + 1)
        self.info.setColumnStretch(1, 1)
        self.info.setColumnStretch(3, 1)

    # ---------- 查完了 ----------
    def _arrived(self, payload):
        token, res = payload
        if token != self._token or self.x is None:
            return
        x = self.x
        prod = res.get("prod") or {}
        if prod:
            self._set_info(R.card_fields(prod))
            raw = "\n".join(f"{k}: {v}" for k, v in prod.items() if not _blank(v))
            self.title.setToolTip(tr("数据库里这个商品的全部字段：") + "\n" + raw)
        self._fill_stock(res.get("stock") or {}, res.get("daily") or {})
        self._fill_receipts(*(res.get("receipts") or ([], [])))
        self._fill_daily(res.get("daily") or {}, prod)
        errs = res.get("errors") or []
        self.msg.setText(tr("有些没读到：{0}", "；".join(errs)) if errs else "")

    def _fill_stock(self, stock, daily):
        """各店库存 + 近 30 天销量（本店加粗）"""
        T = self.ui.T
        self._clear(self.stock)
        sid = self.x.get("store")
        since = date.today().toordinal() - 30
        a, b = QLabel(tr("库存")), QLabel(tr("近 30 天"))
        for w in (a, b):
            w.setObjectName("Faint")
        self.stock.addWidget(a, 1, 0)
        self.stock.addWidget(b, 2, 0)
        for c, s in enumerate(sorted(R.STORES), 1):
            h = QLabel(R.STORES[s])
            h.setObjectName("Faint")
            h.setAlignment(Qt.AlignRight)
            inv = stock.get(s)
            v = QLabel("—" if inv is None else f"{inv:g}")
            v.setAlignment(Qt.AlignRight)
            sold = sum(q for d, (q, _t) in (daily.get(s) or {}).items() if d.toordinal() > since)
            w = QLabel("" if s == 0 else f"{sold:g}")
            w.setAlignment(Qt.AlignRight)
            bold = "font-weight:700;" if s == sid else ""
            v.setStyleSheet(bold + (f"color:{T('err')};" if inv is not None and inv < 0 else ""))
            w.setStyleSheet(bold)
            if s == sid:
                h.setStyleSheet(f"font-weight:700; color:{T('accent')};")
            self.stock.addWidget(h, 0, c)
            self.stock.addWidget(v, 1, c)
            self.stock.addWidget(w, 2, c)

    def _save(self):
        from settings import save_settings
        save_settings(self.settings)

    def _fill_receipts(self, cols, rows):
        if not cols:
            self._placeholder(self.rec_box, tr("没有入库记录"))
            return
        heads = [tr(h) if tag else h for _c, h, tag in cols]
        fids = [_fid(c, tag) for c, _h, tag in cols]
        numeric = tuple(k for k, (c, _h, tag) in enumerate(cols)
                        if tag in ("qty", "price") or (tag is None and rows and isinstance(rows[0].get(c), (int, float))
                                                       and not hasattr(rows[0].get(c), "strftime")))
        lay = self.rec_box.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        widths = {_fid(c, tag): _TAG_W.get(tag, 110) for c, _h, tag in cols}
        self.rec = _Grid(self.ui, heads, numeric, self.cfg, "card_rec", self._save, fids, default_hidden_cols(cols), widths)
        lay.addWidget(self.rec.w)
        out, keys, colors = [], [], {}
        qcol = next((k for k, (_c, _h, tag) in enumerate(cols) if tag == "qty"), None)
        for i, r in enumerate(rows):
            t, kk = [], []
            for c, _h, tag in cols:
                a, b = fmt_cell(r.get(c), tag)
                t.append(a)
                kk.append(b)
            if qcol is not None and isinstance(kk[qcol], float) and kk[qcol] < 0:
                colors[(i, qcol)] = "warn"           # 退货 / 出库
            out.append(t)
            keys.append(kk)
        self.rec.set(out, keys, colors)
        self.tabs.setTabText(0, tr("入库记录（{0}）", len(rows)) if len(rows) < 300 else tr("入库记录（最近 300 条）"))

    def _fill_daily(self, daily, prod):
        stores = [s for s in sorted(R.STORES) if s != 0]
        sid = self.x.get("store")
        heads = [tr("日期")] + [R.STORES[s] for s in stores] + [tr("合计"), tr("本店卖价")]
        fids = [1] + [10 + s for s in stores] + [2, 3]
        numeric = tuple(range(1, len(heads)))
        lay = self.day_box.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        widths = {1: 110, 2: 70, 3: 90}
        widths.update({10 + s: 90 for s in stores})
        self.day = _Grid(self.ui, heads, numeric, self.cfg, "card_day", self._save, fids, (), widths)
        lay.addWidget(self.day.w)
        try:
            lp = float(prod.get("PrezzoVedi1") or 0)
            lp = lp if lp == lp else 0
        except (TypeError, ValueError):
            lp = 0
        wd = [tr("周一"), tr("周二"), tr("周三"), tr("周四"), tr("周五"), tr("周六"), tr("周日")]
        out, keys, colors, tips = [], [], {}, {}
        for i, (d, q, pr) in enumerate(R.daily_sales_table(daily, DAYS)):
            row = [f"{d.strftime('%d/%m')} {wd[d.weekday()]}"] + [f"{q[s]:g}" if s in q else "" for s in stores]
            tot = sum(q.values())
            p = pr.get(sid)
            row += [f"{tot:g}", f"€{p:.2f}" if p else ""]
            out.append(row)
            keys.append([d.isoformat()] + [q.get(s, 0) for s in stores] + [tot, p or 0])
            if sid in stores:
                colors[(i, 1 + stores.index(sid))] = "accent"
            if p and lp and p < lp * (1 - R.PROMO_MIN):
                colors[(i, len(row) - 1)] = "err"
                tips[(i, len(row) - 1)] = tr("比标价 €{0:.2f} 便宜 {1:.0%}", lp, 1 - p / lp)
        self.day.set(out, keys, colors, tips)
