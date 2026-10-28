"""
批发商比价 → Griglia（2026-10-28）：我们的 DETERSIVI 总表和这周批发商报价对比（数据见 griglia.py）。
  上面：Griglia 文件 · 换 Griglia / 打开文件夹 / 刷新 / 导出 Excel
  四张卡：Griglia 商品 · 都不做了 · 新品 · 最低价换了供应商（点卡片跳到对应的表）
  标签页：都不做了 / 新品（至少几家在做）/ 价格 / 价格可疑，每张表都能点表头排序、右键表头筛选、搜索
"""
import os
import shutil
import threading

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
                               QTabWidget, QFileDialog, QStackedWidget)

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
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), lambda i, c: self.rows[i][c], self._fill,
                                   key=lambda i, c: self.keys[i][c], hide=self._hidden, numeric=numeric)
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
        from erp_widgets import EmptyState, kpi_card
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
        kr = QHBoxLayout()
        kr.setSpacing(12)
        self.kpis = {}
        for key, label, tab, tip in (("grid", tr("Griglia 商品"), None, ""),
                                     ("gone", tr("都不做了"), 0, tr("Griglia 里有、这周所有批发商报价里都没有")),
                                     ("new", tr("新上架"), 1, tr("批发商报价里第一次出现、Griglia 里没有的商品（和以前读过的报价比，30 天内）。第一次统计只是记下现在有哪些，下次换了新报价才有")),
                                     ("moved", tr("最低价换了供应商"), 2, tr("现在最便宜的批发商不是 Griglia 里登记的那家"))):
            f, val, sub = kpi_card(label)
            if tip:
                f.setToolTip(tip)
            if tab is not None:
                f.setCursor(Qt.PointingHandCursor)
                f.mousePressEvent = lambda _e, k=tab: self.tabs.setCurrentIndex(k)
            kr.addWidget(f)
            self.kpis[key] = (val, sub)
        bv.addLayout(kr)
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
        from datetime import datetime
        self.grid, self.st = grid, st
        self.file_lbl.setText(tr("{0} · {1:,} 个商品 · {2:%Y-%m-%d}", grid["name"], len(grid["items"]),
                                 datetime.fromtimestamp(grid.get("mtime") or 0)))
        labels = st["labels"]
        nf = sum(1 for x in st["new"] if x["fresh"])
        moved = sum(1 for x in st["prices"] if x["moved"])
        vals = {"grid": (f"{len(grid['items']):,}", tr("{0} 家批发商报价", len(labels))),
                "gone": (f"{len(st['gone']):,}", ""),
                "new": (f"{nf:,}" if st["history"] else "—",
                        tr("全部 {0:,}", len(st["new"])) if st["history"] else tr("下周起显示新上架")),
                "moved": (f"{moved:,}", tr("{0:,} 个价格可疑", len(st["odd"])) if st["odd"] else "")}
        for k, (v, sub) in vals.items():
            self.kpis[k][0].setText(v)
            self.kpis[k][1].setText(sub)
            self.kpis[k][1].setVisible(bool(sub))
        self.tabs.setTabText(0, tr("都不做了（{0}）", len(st["gone"])))
        self.tabs.setTabText(1, tr("新品"))
        self.tabs.setTabText(2, tr("价格（{0:,}）", len(st["prices"])))
        self.tabs.setTabText(3, tr("价格可疑（{0}）", len(st["odd"])))
        self._build_tables(labels)
        self.stack.setCurrentIndex(1)

    # ---------- 表格 ----------
    def _tab(self, idx, key, cols, data, extra=None):
        """
        建一张表放进第 idx 个标签页。cols = [(列编号, 表头, 类型, 取值, 颜色)]：
        类型 "txt" 文字 / "num" 整数 / "price" 价格 / "pct" 带正负号的百分数；颜色(x) 返回颜色记号或 None。列编号固定，用来记住拖过的列顺序
        """
        import settings as ST
        numeric = tuple(c for c, col in enumerate(cols) if col[2] != "txt")
        tab = _Tab(self, [col[1] for col in cols], numeric=numeric, extra=extra)
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
        tab.ctl.remember(cfg, key, lambda: ST.save_settings(self.settings), [col[0] for col in cols])
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
                (2, tr("名称"), "txt", lambda x: x.get("name"), None)]
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
