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
        for text, key in ((tr("新上架"), "fresh"), (tr("我们的品牌"), "brand"), (tr("全部"), "all")):
            self.scope.addItem(text, key)
        self.scope.setToolTip(tr("新上架 = 批发商报价里第一次出现的（和以前读过的报价比，30 天内）；我们的品牌 = 名字里有 Griglia 里的品牌；全部 = 这几家报价里我们这几类、Griglia 里没有的"))
        self.new_bar = QWidget()
        nb = QHBoxLayout(self.new_bar)
        nb.setContentsMargins(0, 0, 0, 0)
        nb.addWidget(self.scope)
        nb.addWidget(self.min_n)
        self.t_gone = _Tab(self, [tr("条码"), tr("名称"), tr("编号"), tr("品牌"), tr("Griglia 供应商"), tr("Griglia 价")],
                           numeric=(5,))
        self.t_new = None                         # 列跟着这周的批发商变，统计完再建
        self.new_box = QWidget()
        QVBoxLayout(self.new_box).setContentsMargins(0, 0, 0, 0)
        self.t_price = None
        self.price_box = QWidget()
        QVBoxLayout(self.price_box).setContentsMargins(0, 0, 0, 0)
        self.t_odd = _Tab(self, [tr("条码"), tr("名称"), tr("供应商"), tr("报价价"), tr("别家中间价")], numeric=(3, 4))
        self.tabs.addTab(self.t_gone.w, tr("都不做了"))
        self.tabs.addTab(self.new_box, tr("新品"))
        self.tabs.addTab(self.price_box, tr("价格"))
        self.tabs.addTab(self.t_odd.w, tr("价格可疑"))
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
        nbr = sum(1 for x in st["new"] if x["brand"] and x["n"] >= 2)
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
        # 都不做了
        self.t_gone.set([[x["ean"], x.get("name") or "", x.get("code") or "", x.get("brand") or "", x.get("supplier") or "",
                          _p(x.get("best") or x.get("price"))] for x in st["gone"]],
                        [[x["ean"], (x.get("name") or "").lower(), x.get("code") or "", x.get("brand") or "",
                          x.get("supplier") or "", x.get("best") or x.get("price") or 0] for x in st["gone"]])
        # 新品（列：条码 名称 部门 几家 最低价 最便宜 + 各家价格）
        self._rebuild_new(labels)
        self._rebuild_price(labels)
        self.t_odd.set([[x["ean"], x.get("name") or "", x["supplier"], _p(x["price"]), _p(x["others"])] for x in st["odd"]],
                       [[x["ean"], (x.get("name") or "").lower(), x["supplier"], x["price"], x["others"] or 0] for x in st["odd"]])
        self.stack.setCurrentIndex(1)

    def _swap(self, box, tab):
        lay = box.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        lay.addWidget(tab.w)

    def _rebuild_new(self, labels):
        st = self.st
        heads = [tr("条码"), tr("名称"), tr("部门"), tr("几家在做"), tr("最低价"), tr("最便宜")] + labels
        if self.t_new is not None:
            self.new_bar.setParent(None)
        self.t_new = _Tab(self, heads, numeric=tuple([3, 4] + list(range(6, 6 + len(labels)))), extra=self.new_bar)
        for cb in (self.min_n, self.scope):
            try:
                cb.currentIndexChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            cb.currentIndexChanged.connect(lambda _: self.t_new.ctl.render())
        # 默认：有以前的报价记录 → 新上架（几家都行）；第一次 → 我们的品牌、≥ 2 家
        self.scope.blockSignals(True)
        self.min_n.blockSignals(True)
        self.scope.setCurrentIndex(0 if st["history"] else 1)
        self.min_n.setCurrentIndex(0 if st["history"] else 1)
        self.scope.blockSignals(False)
        self.min_n.blockSignals(False)
        rows, keys, colors = [], [], {}
        for i, x in enumerate(st["new"]):
            rows.append([x["ean"], x["name"], str(x["dept"] or ""), str(x["n"]), _p(x["best"]), x["best_sup"] or ""] +
                        [_p(x["prices"].get(l)) for l in labels])
            keys.append([x["ean"], x["name"].lower(), str(x["dept"] or ""), x["n"], x["best"] or 0, x["best_sup"] or ""] +
                        [x["prices"].get(l) or 0 for l in labels])
            if x["best_sup"] in labels:
                colors.setdefault(i, []).append((6 + labels.index(x["best_sup"]), "accent"))
        def hide(i):
            x = st["new"][i]
            sc = self.scope.currentData()
            return x["n"] < (self.min_n.currentData() or 1) or (sc == "fresh" and not x["fresh"]) or \
                (sc == "brand" and not x["brand"])
        self.t_new.hide_fn = hide
        self._swap(self.new_box, self.t_new)
        self.t_new.set(rows, keys, colors)

    def _rebuild_price(self, labels):
        st = self.st
        heads = [tr("条码"), tr("名称"), tr("编号"), tr("Griglia 供应商"), tr("Griglia 价"), tr("现在最低"), tr("最便宜"),
                 tr("变化 %")] + labels
        self.t_price = _Tab(self, heads, numeric=tuple([4, 5, 7] + list(range(8, 8 + len(labels)))))
        rows, keys, colors = [], [], {}
        for i, x in enumerate(st["prices"]):
            ch = x["change"]
            rows.append([x["ean"], x.get("name") or "", x.get("code") or "", x.get("supplier") or "", _p(x["ref"]),
                         _p(x["best"]), x["best_sup"] or "", f"{ch * 100:+.1f}" if ch is not None else ""] +
                        [_p(x["prices"].get(l)) for l in labels])
            keys.append([x["ean"], (x.get("name") or "").lower(), x.get("code") or "", x.get("supplier") or "",
                         x["ref"] or 0, x["best"] or 0, x["best_sup"] or "", ch if ch is not None else 0] +
                        [x["prices"].get(l) or 0 for l in labels])
            if ch is not None and abs(ch) >= 0.05:
                colors.setdefault(i, []).append((7, "err" if ch > 0 else "ok"))
            if x["moved"]:
                colors.setdefault(i, []).append((6, "warn"))
            if x["best_sup"] in labels:
                colors.setdefault(i, []).append((8 + labels.index(x["best_sup"]), "accent"))
        self.t_price.t.horizontalHeaderItem(7).setToolTip(tr("现在最低价比 Griglia 记的价（PA MIGLIORE）高（红）或低（绿）多少"))
        self.t_price.t.horizontalHeaderItem(6).setToolTip(tr("黄色 = 现在最便宜的不是 Griglia 登记的供应商"))
        self._swap(self.price_box, self.t_price)
        self.t_price.set(rows, keys, colors)

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
