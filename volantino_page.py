# -*- coding: utf-8 -*-
"""
销售分析 → 促销分析（2026-11-15 VOLANTINO；同一天加了圣诞货，数据和计算见 volantino.py）

  上面：标题 · 类型（VOLANTINO / 圣诞货，下拉只列这一类）· 这一期（下拉：导入的名单 + 数据库里的促销单）· 导入名单… · 修改… · 刷新 · 导出 Excel
  门店按钮：全部门店 / 每家店（整页跟着门店）
  报告（volantino_report.Report，可以往下滚）：KPI 五张卡 · 每天销售额与毛利率 · 对当月毛利率的影响 · 门店排名 ·
    销售额前 8 名商品；下面「明细」标签页：门店对比（五家店一行一家）/ 商品（每个商品；全部门店时多五列各店销量）
"""
import os
import threading
from datetime import date, datetime

from PySide6.QtCore import Qt, QObject, Signal, QDate
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
                               QTabWidget, QFileDialog, QStackedWidget, QFrame, QDialog, QCheckBox, QButtonGroup,
                               QDateEdit, QGridLayout, QMessageBox, QHeaderView)

from i18n import tr

STORES = [(1, "FOIANO"), (2, "MARLIA"), (3, "S.FILIPPO"), (4, "S.MARCO"), (5, "S.CONCORDIO")]
NAMES = dict(STORES)


def _eur(v, dec=0):
    return f"€ {v:,.{dec}f}" if v is not None else "—"


def _pct(v):
    return f"{v * 100:.1f}%" if v is not None else "—"


class _Sig(QObject):
    done = Signal(object)         # (结果, 错误)
    periods = Signal(object)      # 数据库里的促销单读好了


# =====================================================================
# 一期 VOLANTINO：名称、开始 / 结束、哪几家店（导入名单 / 修改）
# =====================================================================
class PeriodDialog(QDialog):
    def __init__(self, parent, period, n_items, can_delete=False, can_reset=False):
        super().__init__(parent)
        from erp_widgets import tip_label
        self.setWindowTitle(tr("这一期促销"))
        self.setMinimumWidth(600)
        self.deleted = False
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(10)
        import volantino as V
        g.addWidget(tip_label(tr("类型"), tr("VOLANTINO（促销传单）还是圣诞货；页面上面按类型分开看")), 0, 0)
        self.kind = QComboBox()
        for k, n in V.KINDS:
            self.kind.addItem(tr(n), k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(V.kind_of(period))))
        g.addWidget(self.kind, 0, 1)
        g.addWidget(QLabel(tr("名称")), 1, 0)
        self.name = QLineEdit(period.get("name") or "")
        g.addWidget(self.name, 1, 1, 1, 3)
        g.addWidget(tip_label(tr("日期"), tr("这一期从哪天到哪天（两天都算）。文件里写了日期的已经认出来了，请核对")), 2, 0)
        self.d1, self.d2 = QDateEdit(), QDateEdit()
        for w, val in ((self.d1, period.get("start")), (self.d2, period.get("end"))):
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd/MM/yyyy")
            d = val if isinstance(val, date) else None
            if d is None and val:
                try:
                    d = datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
                except ValueError:
                    d = None
            d = d or date.today()
            w.setDate(QDate(d.year, d.month, d.day))
        g.addWidget(self.d1, 2, 1)
        g.addWidget(QLabel("–"), 2, 2, Qt.AlignCenter)
        g.addWidget(self.d2, 2, 3)
        g.addWidget(tip_label(tr("门店"), tr("这一期在哪几家店做")), 3, 0, Qt.AlignTop)
        sw = QWidget()
        sl = QHBoxLayout(sw)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        self.boxes = {}
        on = set(period.get("stores") or [s for s, _ in STORES])
        for sid, name in STORES:
            cb = QCheckBox(name)
            cb.setChecked(sid in on)
            self.boxes[sid] = cb
            sl.addWidget(cb)
        sl.addStretch()
        g.addWidget(sw, 3, 1, 1, 3)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        v.addLayout(g)
        info = QLabel(tr("名单里 {0} 个商品", n_items))
        info.setObjectName("Faint")
        v.addWidget(info)
        foot = QHBoxLayout()
        if can_delete:
            de = QPushButton(tr("删除这一期"))
            de.setObjectName("Danger")
            de.clicked.connect(self._delete)
            foot.addWidget(de)
        self.reset = False
        if can_reset:
            rs = QPushButton(tr("恢复数据库原样"))
            rs.setToolTip(tr("去掉在程序里改的名称 / 类型 / 日期 / 门店，照数据库里促销单的原样"))
            rs.clicked.connect(self._reset)
            foot.addWidget(rs)
        foot.addStretch()
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("保存"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self._ok)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        v.addLayout(foot)

    def _ok(self):
        if self.d2.date() < self.d1.date():
            QMessageBox.information(self, tr("这一期促销"), tr("结束日期不能早于开始日期"))
            return
        if not self.stores():
            QMessageBox.information(self, tr("这一期促销"), tr("至少选一家门店"))
            return
        self.accept()

    def _delete(self):
        if QMessageBox.question(self, tr("删除这一期"), tr("只删除程序里记的这一期（名单和日期），原来的 Excel 文件不动。删除吗？")) \
                == QMessageBox.Yes:
            self.deleted = True
            self.accept()

    def _reset(self):
        self.reset = True
        self.accept()

    def values(self):
        to = lambda q: date(q.year(), q.month(), q.day()).isoformat()      # noqa: E731
        return {"name": self.name.text().strip(), "start": to(self.d1.date()), "end": to(self.d2.date()),
                "stores": self.stores(), "kind": self.kind.currentData()}

    def stores(self):
        return [sid for sid, cb in self.boxes.items() if cb.isChecked()]


class _Spread(QObject):
    """行很少的表（门店对比）：列宽按比例铺满整个宽度，表格变宽变窄跟着变（用户自己拖过列宽就不管）"""

    def __init__(self, table, weights, enabled):
        super().__init__(table)
        self.t, self.weights, self.enabled = table, weights, enabled
        table.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Resize and self.enabled():
            self.spread()
        return False

    def spread(self):
        W = self.t.viewport().width()
        cols = [c for c in range(self.t.columnCount()) if not self.t.isColumnHidden(c)]
        tot = sum(self.weights[c] for c in cols) or 1
        for c in cols:
            self.t.setColumnWidth(c, max(56, int(W * self.weights[c] / tot)))


# =====================================================================
# 页面
# =====================================================================
class VolantinoPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cfg = settings.setdefault("volantino", {})
        self.sig = _Sig()
        self.sig.done.connect(self._done)
        self.sig.periods.connect(self._db_periods_arrived)
        self.res = None
        self.store = None
        self.running = False
        self._loaded = False
        self._db_list = []
        self.report = None
        self.kind = self.cfg.get("kind") or "volantino"

    # ---------- 界面 ----------
    def build(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(12)
        # 第一行：标题 + 这一期的说明 · 右边操作
        head = QHBoxLayout()
        head.setSpacing(8)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        t = QLabel(tr("促销分析") + "  ⓘ")
        t.setObjectName("PageTitle")
        t.setToolTip(tr("每一期 VOLANTINO（促销传单）/ 圣诞货的销售情况：各门店卖了多少、毛利多少、毛利率，五家店汇总，"
                        "以及让利把整个月的毛利率拉低了多少。\n名单可以导入 Excel（条码 / 商品编号 + 日期），"
                        "也可以直接选数据库里的促销单。数据库只读"))
        tv.addWidget(t)
        self.status = QLabel("")
        self.status.setObjectName("PageSub")
        tv.addWidget(self.status)
        head.addLayout(tv)
        head.addStretch()
        self.b_imp = QPushButton(tr("导入名单…"))
        self.b_imp.setToolTip(tr("选这一期的名单 Excel：有条码或商品编号就行；促销价、日期能认就自动认"))
        self.b_imp.clicked.connect(self.import_file)
        self.b_edit = QPushButton(tr("修改…"))
        self.b_edit.setToolTip(tr("改这一期的名称、类型、日期、门店；导入的名单还能删除。数据库里的促销单只在程序里改，数据库不动"))
        self.b_edit.clicked.connect(self.edit_period)
        self.b_ref = QPushButton(tr("刷新"))
        self.b_ref.setToolTip(tr("重新从数据库读销售"))
        self.b_ref.clicked.connect(self.run)
        self.b_exp = QPushButton(tr("导出 Excel"))
        self.b_exp.setObjectName("Primary")
        self.b_exp.clicked.connect(self.export)
        for b in (self.b_imp, self.b_edit, self.b_ref, self.b_exp):
            head.addWidget(b, 0, Qt.AlignVCenter)
        lay.addLayout(head)

        # 第二行：类型 · 这一期 · 门店
        import volantino as V
        from erp_widgets import fit_chip
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.kind_group = QButtonGroup(w)
        self.kind_group.setExclusive(True)
        self.kind_btns = {}
        for k, n in V.KINDS:
            b = QPushButton(tr(n))
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(k == self.kind)
            b.setCursor(Qt.PointingHandCursor)
            fit_chip(b, 30)
            b.clicked.connect(lambda _=False, k=k: self.set_kind(k))
            self.kind_group.addButton(b)
            self.kind_btns[k] = b
            bar.addWidget(b)
        bar.addSpacing(4)
        self.period = QComboBox()
        self.period.setMinimumWidth(300)
        self.period.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.period.setToolTip(tr("哪一期：上面是导入的名单，下面是数据库里的促销单"))
        self.period.currentIndexChanged.connect(self._period_changed)
        bar.addWidget(self.period, 1)
        bar.addSpacing(12)
        sep = QFrame()
        sep.setFixedSize(1, 22)
        sep.setObjectName("VpSep")
        bar.addWidget(sep)
        bar.addSpacing(4)
        self.store_group = QButtonGroup(w)
        self.store_group.setExclusive(True)
        self.store_btns = {}
        for code, text in [(None, tr("全部门店"))] + STORES:
            b = QPushButton(text)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(code is None)
            b.setCursor(Qt.PointingHandCursor)
            fit_chip(b, 28)
            b.clicked.connect(lambda _=False, c=code: self.set_store(c))
            self.store_group.addButton(b)
            self.store_btns[code] = b
            bar.addWidget(b)
        lay.addLayout(bar)
        self._seps = [sep]

        self.stack = QStackedWidget()
        from erp_widgets import EmptyState
        self.empty = EmptyState("volantino", tr("这一类还没有名单"),
                                tr("点「导入名单…」选这一期的名单 Excel，或在上面的下拉里选数据库里的促销单"))
        b = QPushButton(tr("导入名单…"))
        b.setObjectName("Primary")
        b.clicked.connect(self.import_file)
        self.empty.buttons.addWidget(b)
        self.stack.addWidget(self.empty)
        # 报告（可以往下滚）：KPI · 走势 · 影响 · 门店排名 · 前几名商品 · 明细表
        from PySide6.QtWidgets import QScrollArea
        from volantino_report import Report
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("VpBody")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 4, 4, 8)
        bv.setSpacing(14)
        self.report = Report()
        self.report.store_picked.connect(self.set_store)
        self.report.show_items.connect(self._jump_items)
        bv.addWidget(self.report)
        dh = QHBoxLayout()
        dt = QLabel(tr("明细"))
        dt.setObjectName("Section")
        dh.addWidget(dt)
        dh.addStretch()
        bv.addLayout(dh)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_stores_tab(), tr("门店对比"))
        self.tabs.addTab(self._build_items_tab(), tr("商品"))
        self.tabs.setMinimumHeight(480)
        bv.addWidget(self.tabs)
        scroll.setWidget(body)
        self.scroll = scroll
        self.stack.addWidget(scroll)
        self.busy = QLabel(tr("正在读销售数据…"))
        self.busy.setAlignment(Qt.AlignCenter)
        self.busy.setObjectName("Faint")
        self.stack.addWidget(self.busy)
        lay.addWidget(self.stack, 1)
        self._style_bits()
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self._first_show())
        self.page = w
        self._fill_periods()
        return w

    def _build_stores_tab(self):
        import settings as ST
        from erp_widgets import TableSortFilter
        heads = [tr("门店"), tr("销量"), tr("销售额"), tr("毛利"), tr("毛利率"), tr("让利"), tr("卖出商品"),
                 tr("占当月销售额"), tr("当月毛利率"), tr("没有让利"), tr("影响")]
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 8, 0, 0)
        self.t_st = self.ui.make_table(heads, stretch_col=None, numeric_cols=tuple(range(1, len(heads))))
        self._st_tips()
        self.t_st.verticalHeader().setDefaultSectionSize(40)
        hh = self.t_st.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        self._st_spread = _Spread(self.t_st, (1.5, 0.8, 1.2, 1.1, 0.9, 1.1, 0.9, 1.1, 1.0, 1.0, 0.9),
                                  lambda: not (self.cfg.get("stores_cols_widths") or {}))
        v.addWidget(self.t_st, 1)
        self.st_rows = []
        self.ctl_st = TableSortFilter(self.t_st, lambda: len(self.st_rows), lambda i, c: self.st_rows[i][1][c],
                                      self._fill_st, key=lambda i, c: self.st_rows[i][2][c], numeric=range(1, len(heads)))
        self.ctl_st.remember(self.cfg, "stores_cols", lambda: ST.save_settings(self.settings))
        self.t_st.doubleClicked.connect(lambda ix: self._st_double(ix.row()))
        self.t_st.setToolTip(tr("双击一家店：整页只看这家店"))
        return box

    def _build_items_tab(self):
        import settings as ST
        import product_images as PI
        from erp_widgets import TableSortFilter
        self.item_heads = [tr("条码"), tr("商品"), tr("促销价"), tr("销量"), tr("销售额"), tr("毛利"), tr("毛利率"),
                           tr("让利"), tr("活动前销量"), tr("变化")] + [n for _, n in STORES]
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(8)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("条码或名称"))
        self.search.setFixedWidth(240)
        bar.addWidget(self.search)
        self.show_sel = QComboBox()
        for key, text in (("all", tr("全部商品")), ("sold", tr("卖出的")), ("unsold", tr("没卖出的")),
                          ("loss", tr("毛利为负的"))):
            self.show_sel.addItem(text, key)
        bar.addWidget(self.show_sel)
        bar.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        v.addLayout(bar)
        numeric = tuple(range(2, len(self.item_heads)))
        self.t_it = self.ui.make_table(self.item_heads, stretch_col=None, numeric_cols=numeric)
        hh = self.t_it.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        thumb = PI.thumb_size(self.settings) if self.settings.get("show_images", True) else 0
        widths = [130, 270 + (thumb + 10 if thumb else 0), 72, 66, 100, 90, 74, 90, 86, 84] + [74] * len(STORES)
        for c, wd in enumerate(widths):
            self.t_it.setColumnWidth(c, wd)
        if thumb:
            self.t_it.setItemDelegateForColumn(1, PI.ThumbDelegate(self.t_it, 0, None, self.settings))
            self.t_it.verticalHeader().setDefaultSectionSize(PI.row_height(self.settings))
        tips = {7: tr("让利 = (原价 − 实际售价) × 数量，不含税。原价用名单里写的原价（Prezzo Continuo / 促销单原价），名单没写的用收银记录里的原价"), 8: tr("活动开始前同样天数里的销量"),
                9: tr("活动期间销量比活动前多 / 少几成")}
        for c, tp in tips.items():
            self.t_it.horizontalHeaderItem(c).setToolTip(tp)
        for k, (_sid, n) in enumerate(STORES):
            self.t_it.horizontalHeaderItem(10 + k).setToolTip(tr("{0} 的销量", n))
        v.addWidget(self.t_it, 1)
        self.it_rows = []
        self.ctl_it = TableSortFilter(self.t_it, lambda: len(self.it_rows), lambda i, c: self.it_rows[i][1][c],
                                      self._fill_it, key=lambda i, c: self.it_rows[i][2][c], hide=self._it_hidden,
                                      numeric=numeric, lazy=True)
        self.ctl_it.remember(self.cfg, "items_cols", lambda: ST.save_settings(self.settings), default_hidden=[0])
        self.ctl_it.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        self.search.textChanged.connect(lambda _: self.ctl_it.render())
        self.show_sel.currentIndexChanged.connect(lambda _: self.ctl_it.render())
        return box

    def _st_tips(self):
        import volantino as V
        k = V.kind_label(self.kind)
        k = tr(k)
        tips = {4: tr("{0} 商品的毛利率", k), 5: tr("让利 = (原价 − 实际售价) × 数量，不含税。原价用名单里写的原价（Prezzo Continuo / 促销单原价），名单没写的用收银记录里的原价"),
                7: tr("{0} 商品销售额 ÷ 这家店当月全部销售额", k), 8: tr("这家店当月所有商品的毛利率"),
                9: tr("假设 {0} 商品按原价卖时当月的毛利率", k), 10: tr("实际 − 没有让利，单位百分点；负数 = 让利拉低了毛利率")}
        for c, tp in tips.items():
            self.t_st.horizontalHeaderItem(c).setToolTip(tp)

    def set_kind(self, k):
        """VOLANTINO / 圣诞货：下拉里只列这一类的几期"""
        self.kind = k
        self.cfg["kind"] = k
        b = self.kind_btns.get(k)
        if b is not None:
            b.setChecked(True)
        import settings as ST
        ST.save_settings(self.settings)
        self._st_tips()
        self.res = None
        self._fill_periods()
        if self.current():
            self.run()

    def restyle(self):
        if self.report is not None:
            self.report.restyle()
            self._style_bits()
        if self.res is not None:
            self.update_view()

    def _style_bits(self):
        T = self.ui.T
        for sp in getattr(self, "_seps", []):
            sp.setStyleSheet(f"background:{T('line')};")
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.scroll.widget().setStyleSheet("#VpBody { background: transparent; }")

    def _jump_items(self, what):
        """报告里点了「没卖出 n」/ 双击一个商品 → 下面的商品明细"""
        self.tabs.setCurrentIndex(1)
        if what in ("sold", "unsold", "loss"):
            self.search.clear()
            self.show_sel.setCurrentIndex(max(0, self.show_sel.findData(what)))
        else:
            self.show_sel.setCurrentIndex(0)
            self.search.setText(what)
        self.scroll.ensureWidgetVisible(self.tabs, 0, 0)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(min(bar.maximum(), self.tabs.mapTo(self.scroll.widget(), self.tabs.rect().topLeft()).y() - 8))

    # ---------- 几期 ----------
    def _base(self):
        from settings import app_dir
        return app_dir()

    def _files(self):
        import volantino as V
        return V.load_periods(self._base())

    def _fill_periods(self, select=None):
        import volantino as V
        files = sorted([p for p in self._files() if V.kind_of(p) == self.kind], key=lambda p: p["start"], reverse=True)
        dbl = [p for p in self._db_list if V.kind_of(p) == self.kind]
        sel = select or self.cfg.get("last")
        self.period.blockSignals(True)
        self.period.clear()
        self._plist = []
        for p in files:
            self.period.addItem("📄 " + V.label(p), p["id"])
            self._plist.append(p)
        if dbl:
            if files:
                self.period.insertSeparator(self.period.count())
                self._plist.append(None)
            for p in dbl:
                self.period.addItem(tr("🗄 数据库 · {0}（{1} 个商品）", V.label(p), p.get("n", 0)), p["id"])
                self._plist.append(p)
        i = self.period.findData(sel) if sel else -1
        if i < 0 and self.period.count():
            i = 0
        self.period.setCurrentIndex(i)
        self.period.blockSignals(False)
        self.b_edit.setEnabled(bool(self.current()))
        has = self.period.count() > 0
        for b in (self.b_ref, self.b_exp):
            b.setEnabled(has)
        if not has:
            self.stack.setCurrentIndex(0)

    def current(self):
        i = self.period.currentIndex()
        return self._plist[i] if 0 <= i < len(getattr(self, "_plist", [])) else None

    def _first_show(self):
        if self._loaded:
            return
        self._loaded = True
        self._load_db_periods()
        if self.current():
            self.run()

    def _load_db_periods(self):
        dbs = self.settings.get("db") or None

        def work():
            import reorder_service as R
            import volantino as V
            try:
                db = R.make_db(dbs)
                try:
                    ps = V.db_periods(db)
                finally:
                    db.engine.dispose()
            except Exception:
                ps = []
            self.sig.periods.emit(ps)
        threading.Thread(target=work, daemon=True).start()

    def _db_periods_arrived(self, ps):
        had = self.current()
        import volantino as V
        self._db_raw = ps
        self._db_list = V.apply_overrides(ps, V.load_overrides(self._base()))
        self._fill_periods(had["id"] if had else None)
        if not had and self.current():
            self.run()

    def _period_changed(self, _i):
        p = self.current()
        if not p:
            return
        self.cfg["last"] = p["id"]
        import settings as ST
        ST.save_settings(self.settings)
        self.b_edit.setEnabled(True)
        self.run()

    def import_file(self):
        import volantino as V
        path, _ = QFileDialog.getOpenFileName(self.win, tr("导入 {0} 名单", tr(V.kind_label(self.kind))), self.cfg.get("last_dir") or "",
                                              tr("Excel 文件 (*.xlsx *.xlsm)"))
        if not path:
            return
        self.cfg["last_dir"] = os.path.dirname(path)
        from erp_widgets import BusyDialog
        got, err = BusyDialog.run(self.win, tr("导入名单"), tr("正在读 {0}…", os.path.basename(path)), lambda: V.read_file(path))
        if err:
            QMessageBox.warning(self.win, tr("导入名单"), tr("读不了这个文件：{0}", err))
            return
        if not got["items"]:
            QMessageBox.information(self.win, tr("导入名单"), tr("文件里没找到商品（要有条码或商品编号列）"))
            return
        items = got["items"]
        for k, pr in (got.get("prices") or {}).items():
            if 0 <= k < len(items):
                try:
                    items[k]["price"] = float(pr) if pr not in (None, "") else None
                except (TypeError, ValueError):
                    pass
        p = {"name": got["name"], "start": got["start"] or date.today(), "end": got["end"] or date.today(),
             "stores": [s for s, _ in STORES], "kind": "natale" if V.kind_of({"name": got["name"]}) == "natale" else self.kind}
        dlg = PeriodDialog(self.win, p, len(items))
        if not dlg.exec():
            return
        periods = V.load_periods(self._base())
        new = dict(dlg.values(), id=V.new_id(periods), source="file", file=path, items=items,
                   created=datetime.now().isoformat(timespec="seconds"))
        periods.append(new)
        V.save_periods(self._base(), periods)
        self.win.log(tr("[促销分析] 导入名单 {0}（{1}）：{2} 个商品，{3} – {4}", os.path.basename(path),
                        got.get("sheet") or "", len(items), new["start"], new["end"]))
        self._show_new(new)

    def _show_new(self, new):
        """导入的这一期：类型和现在看的不一样就切过去"""
        self.cfg["last"] = new["id"]
        if new.get("kind") and new["kind"] != self.kind:
            self.set_kind(new["kind"])
            return
        self._fill_periods(new["id"])
        self.run()

    def edit_period(self):
        import volantino as V
        cur = self.current()
        if not cur:
            return
        if cur.get("source") == "db":
            dlg = PeriodDialog(self.win, cur, cur.get("n") or len(cur.get("items") or []), can_reset=bool(cur.get("orig")))
            if not dlg.exec():
                return
            V.save_override(self._base(), cur["id"], None if dlg.reset else dlg.values())
            self._db_list = V.apply_overrides(getattr(self, "_db_raw", []), V.load_overrides(self._base()))
            new = next((p for p in self._db_list if p["id"] == cur["id"]), cur)
            self.cfg["last"] = cur["id"]
            if V.kind_of(new) != self.kind:
                self.set_kind(V.kind_of(new))
                return
            self._fill_periods(cur["id"])
            self.run()
            return
        dlg = PeriodDialog(self.win, cur, len(cur.get("items") or []), can_delete=True)
        if not dlg.exec():
            return
        periods = V.load_periods(self._base())
        if dlg.deleted:
            periods = [p for p in periods if p["id"] != cur["id"]]
            sel = None
        else:
            for p in periods:
                if p["id"] == cur["id"]:
                    p.update(dlg.values())
            sel = cur["id"]
        V.save_periods(self._base(), periods)
        if not dlg.deleted and dlg.values()["kind"] != self.kind:
            self.cfg["last"] = sel
            self.set_kind(dlg.values()["kind"])
            return
        self._fill_periods(sel)
        if self.current():
            self.run()
        else:
            self.res = None

    # ---------- 计算 ----------
    def run(self):
        p = self.current()
        if not p or self.running:
            return
        self.running = True
        self.stack.setCurrentIndex(2)
        self.status.setText("")
        dbs = self.settings.get("db") or None
        log = self.win.log

        def work():
            import reorder_service as R
            import volantino as V
            try:
                db = R.make_db(dbs)
                try:
                    try:
                        upto = R.sales_latest(db)
                        upto = upto.date() if hasattr(upto, "date") else upto
                    except Exception:
                        upto = None
                    res = V.analyze(db, p, upto=upto, log=log)
                    res["upto"] = upto
                finally:
                    db.engine.dispose()
                self.sig.done.emit((res, None))
            except Exception as e:
                self.sig.done.emit((None, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, payload):
        res, err = payload
        self.running = False
        if err:
            self.stack.setCurrentIndex(0)
            self.win.log(tr("❌ 促销分析失败：{0}", err))
            self.win.show_toast(tr("促销分析失败：{0}", err))
            return
        self.res = res
        if self.store is not None and self.store not in res["stores"]:
            self.set_store(None)
            return
        for code, b in self.store_btns.items():
            b.setEnabled(code is None or code in res["stores"])
        self.stack.setCurrentIndex(1)
        self.update_view()

    def set_store(self, code):
        self.store = code
        b = self.store_btns.get(code)
        if b is not None:
            b.setChecked(True)
        if self.res is not None:
            self.update_view()

    def _st_double(self, k):
        i = self.ctl_st.row_at(k) if hasattr(self.ctl_st, "row_at") else k
        if i is not None and 0 <= i < len(self.st_rows):
            self.set_store(self.st_rows[i][0])

    def update_view(self):
        import volantino as V
        res = self.res
        kname = tr(V.kind_label(V.kind_of(res["period"])))
        self.report.show(res, self.store, kname, NAMES)
        upto = res.get("upto")
        bits = [res["period"].get("name") or kname,
                tr("{0:%d/%m} – {1:%d/%m/%Y} · {2} 天", res["start"], res["end"], (res["end"] - res["start"]).days + 1),
                tr("{0} 家门店", len(res["stores"])) if self.store is None else NAMES.get(self.store, "")]
        if upto:
            bits.append(tr("销售数据到 {0:%d/%m}", upto))
        if res["matched"] < res["total"]["items"]:
            bits.append(tr("⚠ {0} 个商品数据库里没找到", res["total"]["items"] - res["matched"]))
        if res.get("no_cost"):
            bits.append(tr("⚠ {0} 个商品没有进价", res["no_cost"]))
        self.status.setText(" · ".join(bits))
        self.status.setToolTip(tr("没有进价的商品毛利按 0 成本算，会偏高；数据库里没找到的商品不算销售"))
        self._fill_stores()
        self._fill_items()

    # ---------- 门店对比 ----------
    def _fill_stores(self):
        res = self.res
        rows = []
        for sid in res["stores"]:
            a = res["store"][sid]
            im = a.get("impact") or {}
            disp = [NAMES[sid], f"{a['qty']:,.0f}", _eur(a["rev"], 2), _eur(a["profit"], 2), _pct(a.get("margin")),
                    _eur(a["disc"], 2), f"{a['sold']}", _pct(a.get("share")), _pct(im.get("margin")),
                    _pct(im.get("margin_wo")), f"{im['impact']:+.2f}" if im.get("impact") is not None else "—"]
            keys = [sid, a["qty"], a["rev"], a["profit"], a.get("margin") or 0, a["disc"], a["sold"], a.get("share") or 0,
                    im.get("margin") or 0, im.get("margin_wo") or 0, im.get("impact") or 0]
            rows.append((sid, disp, keys))
        self.st_rows = rows
        self.ctl_st.render()
        if self._st_spread.enabled():
            self._st_spread.spread()

    def _fill_st(self, i, k):
        T = self.ui.T
        sid, disp, keys = self.st_rows[i]
        sel = self.store == sid
        self.ui.table_set_row(self.t_st, k, disp, bold=sel)
        it = self.t_st.item(k, 0)
        f = it.font()
        f.setBold(True)
        it.setFont(f)
        if sel:
            it.setForeground(QColor(T("accent")))
        m = keys[4]
        self.t_st.item(k, 4).setForeground(QColor(T("err") if m < 0 else T("text")))
        imp = keys[10]
        c = self.t_st.item(k, 10)
        c.setForeground(QColor(T("err") if imp <= -0.5 else T("warn") if imp < 0 else T("ok")))
        f = c.font()
        f.setBold(True)
        c.setFont(f)

    # ---------- 商品 ----------
    def _fill_items(self):
        import volantino as V
        res = self.res
        rows = []
        for r in V.item_rows(res, self.store):
            before, qty = r.get("before", 0.0), r.get("qty", 0.0)
            ch = (qty - before) / before if before else None
            price = r.get("price")
            try:
                price = float(price) if price not in (None, "") else None
            except (TypeError, ValueError):
                price = None
            per = [r["per_store"].get(s) for s, _ in STORES]
            disp = [r["ean"], r["name"], f"{price:.2f}" if price else "", f"{qty:,.0f}", _eur(r["rev"], 2),
                    _eur(r["profit"], 2), _pct(r.get("margin")), _eur(r["disc"], 2), f"{before:,.0f}",
                    (f"{ch:+.0%}" if ch is not None else (tr("新") if qty > 0 else "")) ] + \
                   [("" if v is None else f"{v:,.0f}") for v in per]
            keys = [r["ean"], (r["name"] or "").lower(), price or 0, qty, r["rev"], r["profit"],
                    r["margin"] if r.get("margin") is not None else -9, r["disc"], before,
                    ch if ch is not None else (9 if qty > 0 else -9)] + [v or 0 for v in per]
            rows.append((r, disp, keys))
        self.it_rows = rows
        # 只看一家店时，各店销量那几列藏起来；这一期没做的店也藏（程序藏的，用户在「显示哪些列」里选了也不显示）
        ch_ = self.ctl_it._chooser
        if ch_ is not None:
            ch_.forced = lambda: {10 + k for k, (sid, _n) in enumerate(STORES)
                                  if self.store is not None or sid not in res["stores"]}
            ch_.apply()
        if self.ctl_it.sort_col is None:
            self.ctl_it.sort_col, self.ctl_it.sort_desc = 4, True          # 默认按销售额从高到低
        self.ctl_it.render()

    def _it_hidden(self, i):
        r, disp, _ = self.it_rows[i]
        q = self.search.text().strip().lower()
        if q and q not in f"{disp[0]} {disp[1]} {r.get('code') or ''}".lower():
            return True
        sel = self.show_sel.currentData()
        qty = r.get("qty", 0.0)
        return (sel == "sold" and qty <= 0) or (sel == "unsold" and qty > 0) or \
               (sel == "loss" and not (r.get("margin") is not None and r["margin"] < 0))

    def _fill_it(self, i, k):
        T = self.ui.T
        r, disp, keys = self.it_rows[i]
        self.ui.table_set_row(self.t_it, k, disp)
        it = self.t_it.item(k, 1)
        it.setToolTip(f"{disp[1]}\n{disp[0]} · {r.get('code') or ''}")
        if r.get("qty", 0) <= 0:
            for c in range(3, 10):
                cell = self.t_it.item(k, c)
                if cell is not None:
                    cell.setForeground(QColor(T("faint")))
            return
        m = r.get("margin")
        if m is not None and m < 0:
            self.t_it.item(k, 6).setForeground(QColor(T("err")))
            self.t_it.item(k, 5).setForeground(QColor(T("err")))
        ch = keys[9]
        c = self.t_it.item(k, 9)
        if ch not in (9, -9):
            c.setForeground(QColor(T("ok") if ch > 0 else T("warn") if ch < 0 else T("muted")))
        else:
            c.setForeground(QColor(T("accent")))

    # ---------- 导出 ----------
    def export(self):
        if self.res is None:
            return
        import volantino as V
        p = self.res["period"]
        name = "".join(ch for ch in (p.get("name") or "VOLANTINO") if ch not in '\\/:*?"<>|').strip() or "VOLANTINO"
        path, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"),
                                              os.path.join(self.cfg.get("last_dir") or "",
                                                           f"Volantino_{name}_{self.res['start']:%d%m}.xlsx"),
                                              tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        from erp_widgets import BusyDialog, open_file
        res = self.res
        url = self.settings.get("image_url") or None
        _, err = BusyDialog.run(self.win, tr("导出 Excel"), tr("正在写…"), lambda: V.export(res, path, url=url, names=NAMES))
        if err:
            QMessageBox.warning(self.win, tr("导出 Excel"), tr("导出失败：{0}", err))
            return
        open_file(self.win, path, tr("导出 Excel"))
