"""
门店配货 · 订货明细（独立窗口）
每个商品 × 门店：建议箱数、订货箱数（双击改）、金额、判断、等级、库存、销量、日均、目标库存 …
右侧详情栏：选中商品的关键数字、近 26 周销量曲线、「为什么建议这么多箱」、其他门店情况。
"""
import math
import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (QSizePolicy, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox,
                               QLineEdit, QFrame, QTableWidgetItem, QAbstractItemView, QHeaderView, QButtonGroup,
                               QScrollArea, QFileDialog, QMessageBox, QMenu)

from i18n import tr
from settings import save_settings
from theme import T, num_font
from erp_widgets import ToolWindow, Sparkline
import store_orders as S
from reorder_page import short_decision

ALL = "__all__"
# 明细表列（逻辑顺序）→ 显示顺序：常看、要改的列放前面
_ORDER = [0, 1, 5, 15, 16, 17, 2, 18, 6, 20, 21, 7, 8, 9, 10, 11, 14, 19, 12, 13, 3, 4]


def _col(k):
    return _ORDER.index(k)


def _perm(vals):
    return [vals[k] for k in _ORDER]


EDIT_COL = _col(16)
AMOUNT_COL = _col(17)
DEC_COL = _col(2)
DI_ROLE = Qt.UserRole + 1       # 第 0 列存 detail 下标（排序后靠它找到行）
KEY_ROLE = Qt.UserRole + 2      # 排序用的值
_TIER_RANK = {"A": 0, "B": 1, "C": 2}


class _SortItem(QTableWidgetItem):
    def __lt__(self, other):
        a, b = self.data(KEY_ROLE), other.data(KEY_ROLE)
        if a is None or b is None:
            return (a is None) and (b is not None)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


def _dec_rank(text):
    for mark, r in (("🔴", 0), ("🟡", 1), ("🟠", 2), ("🟢", 3)):
        if mark in (text or ""):
            return r
    return 4


def explain(x, it, algo):
    """「为什么建议这么多箱」的说明文字"""
    if not x:
        return tr("数据库里找不到这个商品，没有建议量；需要的话手动填箱数。")
    pack = it["pack"] or 1
    if x.get("target") is None:       # 原程序算法：完全按原程序算，这里只说明换成 SCHEDA 箱数
        return tr("原程序算法（严格按原程序计算，没有任何调整）：日均 {0:.2f} 件 × 订货周期 {1} 天 × 系数，减去本店库存 {2:g} 件，"
                  "按数据库箱规取整，得到建议 {3} 件；按 SCHEDA 每箱 {4:g} 件换成箱数（向上取整）。",
                  x.get("daily") or 0, x.get("cycle") or "?", x.get("inv_raw", x["inv"]), x["sugg"], pack)
    R = (algo or {}).get("review_days", 7)
    L = (algo or {}).get("lead_days", 5)
    inv = max(0.0, x["inv"])
    mu = (x.get("daily") or 0) * (R + L)
    need = x["sugg"]
    boxes = S.colli_for(x, pack, algo)
    txt = tr("下次到货前（下单间隔 {0} 天 + 到货 {1} 天）预计卖 {2:.1f} 件，加安全库存 {3:.1f} 件，目标库存 {4:.0f} 件；",
             R, L, mu, x.get("safety") or 0, x["target"])
    txt += tr("现有库存 {0:g} 件", x.get("inv_raw", x["inv"]))
    if x.get("inv_raw", 0) < 0:
        txt += tr("（负数按 0 算）")
    txt += tr("，缺 {0} 件。", need) if need > 0 else tr("，够用，不用订。")
    if need > 0:
        full, rem = divmod(need, pack)
        txt += tr("每箱 {0:g} 件 → {1:g} 箱（零头 {2:g} 件", pack, boxes, rem)
        thr = (algo or {}).get("pack_threshold", 0.3)
        txt += tr("，≥ 一箱的 {0:.0%} 才多订一箱）。", thr)
    return txt


class DetailWindow(ToolWindow):
    def __init__(self, page):
        super().__init__(page.win, tr("订货明细"), (1400, 860))
        self.page = page
        self.ui = page.ui
        self.detail = []
        self.pos = []
        self.sort_col, self.sort_order = 0, Qt.AscendingOrder
        self._filling = False
        self._shown = 0
        self.cur_di = None

        root = QWidget()
        root.setObjectName("Dialog")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ---- 工具条 ----
        bar = QFrame()
        bar.setObjectName("TopBar")
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(16, 10, 16, 10)
        tb.setSpacing(8)
        self.store_group = QButtonGroup(self)
        self.store_group.setExclusive(True)
        b = QPushButton(tr("全部门店"))
        b.setObjectName("Chip")
        b.setCheckable(True)
        b.setChecked(True)
        self.store_group.addButton(b)
        b.setProperty("sid", ALL)
        tb.addWidget(b)
        for sid, name in S.STORE_SHEETS.items():
            b = QPushButton(name)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setProperty("sid", sid)
            self.store_group.addButton(b)
            tb.addWidget(b)
        self.store_group.buttonClicked.connect(lambda _b: self._filter())
        tb.addSpacing(8)
        self.f_sup = QComboBox()
        self.f_sup.setMinimumWidth(180)
        self.f_sup.currentIndexChanged.connect(self._filter)
        tb.addWidget(self.f_sup)
        self.f_only = self._chip(tr("只看要订的"), True)
        self.f_edit = self._chip(tr("只看改过的"), False)
        self.f_urgent = self._chip(tr("只看紧急"), False)
        for c in (self.f_only, self.f_edit, self.f_urgent):
            tb.addWidget(c)
        for c in self.store_group.buttons() + [self.f_only, self.f_edit, self.f_urgent]:
            c.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)   # 文字长（英文）时不被挤掉
            bf = c.font()
            bf.setBold(True)       # 选中时是粗体，按粗体留宽度
            c.setMinimumWidth(QFontMetrics(bf).horizontalAdvance(c.text()) + 34)
        tb.addStretch()
        self.f_text = QLineEdit()
        self.f_text.setPlaceholderText(tr("编号、条码或名称"))
        self.f_text.setMinimumWidth(150)
        self.f_text.setMaximumWidth(200)
        self.f_text.textChanged.connect(self._filter)
        tb.addWidget(self.f_text)
        self.btn_reset = QPushButton(tr("恢复建议值"))
        self.btn_reset.setToolTip(tr("把当前筛选出来的行恢复成系统建议的箱数"))
        self.btn_reset.clicked.connect(self.reset_shown)
        tb.addWidget(self.btn_reset)
        exp = QPushButton(tr("导出 Excel"))
        exp.clicked.connect(self.export)
        tb.addWidget(exp)
        v.addWidget(bar)

        # ---- 表格 + 详情栏 ----
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.table = self.ui.make_table(_perm([tr("供应商"), tr("门店"), tr("判断"), tr("商品编号"), tr("条码"), tr("名称"),
                                               tr("本店库存"), "30", "60", "90", "180", tr("库存天数"), tr("每箱件数"),
                                               tr("件价"), tr("建议件数"), tr("建议箱数"), tr("订货箱数"), tr("金额"),
                                               tr("等级"), tr("周期"), tr("日均"), tr("目标库存")]),
                                        numeric_cols=[_col(k) for k in list(range(6, 18)) + [19, 20, 21]])
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.verticalHeader().setDefaultSectionSize(34)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, wd in enumerate(_perm((130, 92, 84, 80, 120, 270, 70, 50, 50, 50, 50, 70, 66, 62, 70, 66, 76, 80, 46, 46, 60, 70))):
            hdr = self.table.horizontalHeaderItem(c)
            need = hh.fontMetrics().horizontalAdvance(hdr.text() if hdr else "") + 26
            self.table.setColumnWidth(c, max(wd, need))
        self.table.horizontalHeaderItem(EDIT_COL).setText(tr("订货箱数") + " ✎")
        self.table.horizontalHeaderItem(EDIT_COL).setToolTip(tr("双击修改箱数，0 = 不订"))
        self.table.horizontalHeaderItem(_col(18)).setToolTip(tr("销量等级：A 卖得快，B 一般，C 卖得慢"))
        # 列可以拖动换位置（例如把「每箱件数」拖到前面）；顺序和宽度记在 settings.json，下次打开照旧
        hh.setSectionsMovable(True)
        hh.setFirstSectionMovable(True)
        self._col_timer = QTimer(self)
        self._col_timer.setSingleShot(True)
        self._col_timer.setInterval(400)
        self._col_timer.timeout.connect(self._save_columns)
        self._restore_columns()
        hh.sectionMoved.connect(lambda *_: self._col_timer.start())
        hh.sectionResized.connect(lambda *_: self._col_timer.start())
        hh.setContextMenuPolicy(Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        hh.setToolTip(tr("拖动表头可以换列的位置，下次打开保持；右键可以恢复默认顺序"))
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)
        hh.setSortIndicator(0, Qt.AscendingOrder)
        hh.sectionClicked.connect(self._sort_by)
        self.table.itemChanged.connect(self._cell_edited)
        self.table.currentCellChanged.connect(lambda r, c, pr, pc: self._select_row(r))
        mid.addWidget(self.table, 1)
        mid.addWidget(self._build_drawer())
        mw = QWidget()
        mw.setLayout(mid)
        v.addWidget(mw, 1)

        # ---- 状态栏 ----
        sb = QFrame()
        sb.setObjectName("StatusBar")
        sh = QHBoxLayout(sb)
        sh.setContentsMargins(16, 7, 16, 7)
        self.status_l = QLabel("")
        self.status_l.setObjectName("StatusText")
        self.status_r = QLabel(tr("双击「订货箱数」修改 · Enter 确认 · Esc 取消"))
        self.status_r.setObjectName("StatusText")
        sh.addWidget(self.status_l, 1)
        sh.addWidget(self.status_r)
        v.addWidget(sb)

    # ---------- 列顺序 / 宽度（记住） ----------
    def _restore_columns(self):
        cfg = self.page.cfg
        hh = self.table.horizontalHeader()
        hh.blockSignals(True)
        for field, wd in (cfg.get("detail_widths") or {}).items():
            try:
                c = _col(int(field))
            except ValueError:
                continue
            if wd and wd > 20:
                self.table.setColumnWidth(c, int(wd))
        order = [int(f) for f in (cfg.get("detail_columns") or []) if str(f).lstrip("-").isdigit() and int(f) in _ORDER]
        for v, field in enumerate(order):
            cur = hh.visualIndex(_col(field))
            if cur != v and 0 <= cur:
                hh.moveSection(cur, v)
        hh.blockSignals(False)

    def _save_columns(self):
        hh = self.table.horizontalHeader()
        n = self.table.columnCount()
        self.page.cfg["detail_columns"] = [_ORDER[hh.logicalIndex(v)] for v in range(n)]
        self.page.cfg["detail_widths"] = {str(_ORDER[c]): self.table.columnWidth(c) for c in range(n)}
        save_settings(self.page.settings)

    def _header_menu(self, pos):
        m = QMenu(self)
        a = m.addAction(tr("恢复默认列顺序"))
        if m.exec(self.table.horizontalHeader().mapToGlobal(pos)) == a:
            hh = self.table.horizontalHeader()
            hh.blockSignals(True)
            for c in range(self.table.columnCount()):
                cur = hh.visualIndex(c)
                if cur != c:
                    hh.moveSection(cur, c)
            hh.blockSignals(False)
            self.page.cfg.pop("detail_columns", None)
            save_settings(self.page.settings)

    def _visual_cols(self):
        hh = self.table.horizontalHeader()
        return [hh.logicalIndex(v) for v in range(self.table.columnCount())]

    def _chip(self, text, on):
        b = QPushButton(text)
        b.setObjectName("Chip")
        b.setCheckable(True)
        b.setChecked(on)
        b.toggled.connect(self._filter)
        return b

    # ---------- 详情栏 ----------
    def _build_drawer(self):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFixedWidth(350)
        sa.setFrameShape(QFrame.NoFrame)
        d = QWidget()
        d.setObjectName("Drawer")
        v = QVBoxLayout(d)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)
        self.d_over = QLabel("")
        self.d_over.setObjectName("Faint")
        self.d_title = QLabel(tr("选中一行查看详情"))
        self.d_title.setStyleSheet("font-size:16px; font-weight:700;")
        self.d_title.setWordWrap(True)
        self.d_codes = QLabel("")
        self.d_codes.setObjectName("Faint")
        self.d_codes.setFont(num_font(9))
        v.addWidget(self.d_over)
        v.addWidget(self.d_title)
        v.addWidget(self.d_codes)
        g = QGridLayout()
        g.setSpacing(8)
        self.minis = []
        for i, lab in enumerate((tr("本店库存"), tr("加权日均"), tr("目标库存"), tr("服务水平"))):
            f = QFrame()
            f.setObjectName("MiniCard")
            fv = QVBoxLayout(f)
            fv.setContentsMargins(10, 8, 10, 8)
            fv.setSpacing(1)
            a = QLabel(lab)
            a.setObjectName("MiniLabel")
            b = QLabel("—")
            b.setObjectName("MiniValue")
            c = QLabel("")
            c.setObjectName("MiniLabel")
            fv.addWidget(a)
            fv.addWidget(b)
            fv.addWidget(c)
            g.addWidget(f, i // 2, i % 2)
            self.minis.append((b, c))
        v.addLayout(g)
        sp = QFrame()
        sp.setObjectName("MiniCard")
        spv = QVBoxLayout(sp)
        spv.setContentsMargins(12, 10, 12, 10)
        hl = QHBoxLayout()
        t = QLabel(tr("近 26 周销量"))
        t.setStyleSheet("font-weight:600; font-size:12px;")
        u = QLabel(tr("件 / 周"))
        u.setObjectName("MiniLabel")
        hl.addWidget(t, 1)
        hl.addWidget(u)
        spv.addLayout(hl)
        self.spark = Sparkline(height=150)
        spv.addWidget(self.spark)
        self.spark_note = QLabel("")
        self.spark_note.setObjectName("MiniLabel")
        self.spark_note.setWordWrap(True)
        spv.addWidget(self.spark_note)
        v.addWidget(sp)
        why = QFrame()
        why.setObjectName("MiniCard")
        wv = QVBoxLayout(why)
        wv.setContentsMargins(12, 10, 12, 10)
        self.why_t = QLabel(tr("为什么这么建议"))
        self.why_t.setStyleSheet("font-weight:600; font-size:12px;")
        self.why = QLabel("")
        self.why.setWordWrap(True)
        self.why.setObjectName("Faint")
        wv.addWidget(self.why_t)
        wv.addWidget(self.why)
        v.addWidget(why)
        oth = QFrame()
        oth.setObjectName("MiniCard")
        ov = QVBoxLayout(oth)
        ov.setContentsMargins(12, 10, 12, 10)
        ot = QLabel(tr("其他门店"))
        ot.setStyleSheet("font-weight:600; font-size:12px;")
        ov.addWidget(ot)
        self.others = QVBoxLayout()
        self.others.setSpacing(4)
        ov.addLayout(self.others)
        v.addWidget(oth)
        v.addStretch()
        sa.setWidget(d)
        return sa

    def _select_row(self, r):
        if r < 0 or self.table.item(r, 0) is None:
            return
        di = self.table.item(r, 0).data(DI_ROLE)
        if di is None:
            return
        self.cur_di = di
        sc, it, sid = self.detail[di]
        calc = self.page.calc
        x = calc["demand"].get(sid, {}).get(it.get("db_code") or it["code"])
        algo = calc["demand"].get("_algo") if isinstance(calc["demand"], dict) else None
        self.d_over.setText(f"{S.STORE_SHEETS[sid]} · {sc['name']}")
        self.d_title.setText(it["desc"])
        self.d_codes.setText(f"{it.get('db_code') or it['code']} · {it['ean']} · {it['pack']:g} {tr('件/箱')} · €{it['price']:.3f}/{tr('件')}")
        (a1, a2), (b1, b2), (c1, c2), (d1, d2) = self.minis
        if x:
            inv_raw = x.get("inv_raw", x["inv"])
            a1.setText(f"{inv_raw:g}")
            a1.setStyleSheet(f"color:{T('err')};" if inv_raw < 0 else "")
            v1 = x.get("target") is None
            a2.setText(tr("计算时按 0") if inv_raw < 0 and not v1 else tr("件"))
            b1.setText(f"{(x.get('daily') or 0):.2f}")
            b2.setText(tr("季节系数 {0:.2f}", x.get("season") or 1) if x.get("season") else tr("件 / 天"))
            c1.setText(f"{x['target']:.0f}" if isinstance(x.get("target"), (int, float)) else "—")
            c2.setText(tr("含安全库存 {0:.0f}", x.get("safety") or 0) if x.get("target") is not None else tr("原程序算法"))
            svc = x.get("service")
            d1.setText(f"{svc:.1%}" if isinstance(svc, (int, float)) else "—")
            d2.setText(tr("{0} 类商品", x.get("tier") or "?"))
            wk = x.get("weekly") or []
            self.spark.set_values(wk, (x.get("daily") or 0) * 7, self.page.calc.get("date"))
            if wk:
                last4 = sum(wk[-4:])
                imax = max(range(len(wk)), key=lambda i: wk[i])
                a, b = self.spark.week_range(imax)
                self.spark_note.setText(tr("最近 4 周 {0:g} 件 · 26 周合计 {1:g} 件 · 最多 {2:g} 件（{3:%d/%m}–{4:%d/%m}）",
                                           round(last4, 1), round(sum(wk), 1), round(wk[imax], 1), a, b) + "\n" +
                                        tr("虚线：加权平均 {0:.1f} 件 / 周 · 鼠标移到柱子上看那一周", (x.get("daily") or 0) * 7))
            else:
                self.spark_note.setText(tr("原程序算法没有周销量"))
        else:
            for m1, m2 in self.minis:
                m1.setText("—")
                m2.setText("")
            self.spark.set_values([])
            self.spark_note.setText("")
        n = self._cur(sc, sid, it["row"])
        self.why_t.setText(tr("为什么建议 {0:g} 箱", calc["base"][sc["path"]].get(sid, {}).get(it["row"], 0)))
        self.why.setText(explain(x, it, algo) + ("\n" + tr("现在订 {0:g} 箱（手动改过）", n)
                                                 if (sc["path"], sid, it["row"]) in self.page.edited else ""))
        while self.others.count():
            w = self.others.takeAt(0).widget()
            if w:
                w.deleteLater()
        for osid in calc["stores"]:
            if osid == sid or osid not in sc["sheets"]:
                continue
            ox = calc["demand"].get(osid, {}).get(it.get("db_code") or it["code"])
            on = self._cur(sc, osid, it["row"])
            l = QLabel(f"{S.STORE_SHEETS[osid]}    " + (tr("库存 {0:g} · 订 {1:g} 箱", ox.get("inv_raw", ox["inv"]), on) if ox
                                                       else tr("数据库没有")))
            l.setObjectName("Faint" if not on else "")
            self.others.addWidget(l)

    # ---------- 填表 ----------
    def load(self):
        calc = self.page.calc
        stores = calc["stores"]
        d = calc["days"]
        for i in range(4):
            self.table.horizontalHeaderItem(_col(7 + i)).setText(tr("{0}天", d[i]))
            self.table.horizontalHeaderItem(_col(7 + i)).setToolTip(tr("最近 {0} 天销量", d[i]))
        self.f_sup.blockSignals(True)
        self.f_sup.clear()
        self.f_sup.addItem(tr("全部供应商"), ALL)
        for sc in calc["schede"]:
            self.f_sup.addItem(f"{sc['name']}（{sc['day']}）", sc["path"])
        self.f_sup.blockSignals(False)
        self.setWindowTitle(tr("订货明细 — {0} 家供应商 · {1} 家门店", len(calc["schede"]), len(stores)))

        self.detail = []
        self.sup_index = {sc["path"]: k for k, sc in enumerate(calc["schede"])}
        for sc in calc["schede"]:
            for it in sc["items"]:
                for sid in stores:
                    if sid in sc["sheets"]:
                        self.detail.append((sc, it, sid))
        t = self.table
        self._filling = True
        t.setUpdatesEnabled(False)
        t.setSortingEnabled(False)
        t.setRowCount(len(self.detail))
        for i, (sc, it, sid) in enumerate(self.detail):
            x = calc["demand"].get(sid, {}).get(it.get("db_code") or it["code"])
            sug_c = calc["base"][sc["path"]].get(sid, {}).get(it["row"], 0)
            inv_days = x["inv_days"] if x else None
            if isinstance(inv_days, float) and math.isinf(inv_days):
                inv_days = "∞"
            elif isinstance(inv_days, (int, float)):
                inv_days = f"{inv_days:,.1f}"
            self.ui.table_set_row(t, i, _perm([
                sc["name"], S.STORE_SHEETS[sid], short_decision(x["decision"]) if x else tr("数据库没有"),
                it.get("db_code") or it["code"], it["ean"], it["desc"],
                f"{x.get('inv_raw', x['inv']):g}" if x else "", *([f"{v:g}" for v in x["sales"]] if x else [""] * 4),
                inv_days or "", f"{it['pack']:g}", f"{it['price']:.3f}".rstrip("0").rstrip("."),
                f"{x['sugg']:,}" if x else "", f"{sug_c:g}" if sug_c else "", "", "",
                (x["tier"] or "") if x else "",
                f"{x['cycle']:g}" if x and isinstance(x.get("cycle"), (int, float)) else "",
                f"{x['daily']:.2f}" if x and x.get("daily") else "",
                f"{x['target']:.0f}" if x and isinstance(x.get("target"), (int, float)) else ""]))
            keys = _perm(self._sort_keys(i, x, sug_c))
            for c in range(t.columnCount()):
                src = t.item(i, c)
                item = _SortItem(src.text())
                item.setTextAlignment(src.textAlignment())
                item.setFont(src.font())
                item.setForeground(src.foreground())
                item.setData(KEY_ROLE, keys[c])
                if c == EDIT_COL:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                    item.setToolTip(tr("双击修改箱数，0 = 不订"))
                else:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                t.setItem(i, c, item)
            if x:
                dec = x["decision"] or ""
                col = T("err") if "🔴" in dec else T("warn") if "🟡" in dec else T("accent") if "🟠" in dec \
                    else T("ok") if "🟢" in dec else T("muted")
                t.item(i, DEC_COL).setForeground(QColor(col))
                t.item(i, DEC_COL).setToolTip(dec)
                inv_raw = x.get("inv_raw", x["inv"])
                if inv_raw < 0:
                    t.item(i, _col(6)).setForeground(QColor(T("err")))
            else:
                t.item(i, DEC_COL).setForeground(QColor(T("err")))
            tier = (x["tier"] or "") if x else ""
            if tier:
                tc = t.item(i, _col(18))
                tc.setForeground(QColor(T("ok") if tier == "A" else T("accent") if tier == "B" else T("muted")))
                f = tc.font()
                f.setBold(True)
                tc.setFont(f)
            t.item(i, 0).setData(DI_ROLE, i)
        self.pos = list(range(len(self.detail)))
        for i in range(len(self.detail)):
            self._show_qty(i)
        if self.sort_col != 0 or self.sort_order != Qt.AscendingOrder:
            self._apply_sort()
        t.setUpdatesEnabled(True)
        self._filling = False
        self._filter()

    def _sort_keys(self, i, x, sug_c):
        sc, it, sid = self.detail[i]
        k0 = (self.sup_index[sc["path"]], it["row"], sid)        # 默认顺序：供应商 → SCHEDA 行 → 门店
        inv_days = x["inv_days"] if x else None
        if isinstance(inv_days, (int, float)) and math.isinf(inv_days):
            inv_days = 1e12
        num = lambda v: float(v) if isinstance(v, (int, float)) else None
        return [k0, (sid,) + k0, (_dec_rank(x["decision"]) if x else 9, -(x["sugg"] if x else 0)) + k0,
                it.get("db_code") or it["code"], it["ean"], it["desc"].lower(),
                num(x.get("inv_raw", x["inv"])) if x else None, *([num(v) for v in x["sales"]] if x else [None] * 4),
                num(inv_days), float(it["pack"]), float(it["price"]),
                float(x["sugg"]) if x else None, float(sug_c), 0.0, 0.0,
                (_TIER_RANK.get(x["tier"], 9),) + k0 if x else (9,) + k0,
                float(x["cycle"]) if x and isinstance(x.get("cycle"), (int, float)) else None,
                float(x["daily"]) if x and x.get("daily") else None,
                float(x["target"]) if x and isinstance(x.get("target"), (int, float)) else None]

    def _sort_by(self, col):
        if not self.detail:
            return
        if col == self.sort_col:
            self.sort_order = Qt.DescendingOrder if self.sort_order == Qt.AscendingOrder else Qt.AscendingOrder
        else:
            self.sort_col = col
            self.sort_order = Qt.DescendingOrder if col in self.table._numeric else Qt.AscendingOrder
        self.table.setUpdatesEnabled(False)
        self._apply_sort()
        self.table.setUpdatesEnabled(True)
        self._filter()

    def _apply_sort(self):
        t = self.table
        was = self._filling
        self._filling = True
        t.sortItems(self.sort_col, self.sort_order)
        t.horizontalHeader().setSortIndicator(self.sort_col, self.sort_order)
        self.pos = [0] * len(self.detail)
        for r in range(t.rowCount()):
            self.pos[t.item(r, 0).data(DI_ROLE)] = r
        self._filling = was

    def _cur(self, sc, sid, row):
        return self.page.colli.get(sc["path"], {}).get(sid, {}).get(row, 0)

    def _show_qty(self, i):
        sc, it, sid = self.detail[i]
        r = self.pos[i]
        n = self._cur(sc, sid, it["row"])
        was = self._filling
        self._filling = True
        c = self.table.item(r, EDIT_COL)
        c.setText(f"{n:g}" if n else "0")
        c.setData(KEY_ROLE, float(n))
        f = c.font()
        f.setBold(n > 0)
        c.setFont(f)
        edited = (sc["path"], sid, it["row"]) in self.page.edited
        c.setForeground(QColor(T("warn") if edited else T("accent") if n > 0 else T("faint")))
        c.setBackground(QColor(T("warn_soft")) if edited else QColor(0, 0, 0, 0))
        amt = self.table.item(r, AMOUNT_COL)
        amt.setText(f"{n * it['pack'] * it['price']:,.2f}" if n else "")
        amt.setData(KEY_ROLE, n * it["pack"] * it["price"])
        self._filling = was

    def _cell_edited(self, item):
        if self._filling or item.column() != EDIT_COL:
            return
        i = self.table.item(item.row(), 0).data(DI_ROLE)
        sc, it, sid = self.detail[i]
        text = item.text().strip().replace(",", ".")
        try:
            n = float(text or 0)
            if n < 0 or n != int(n):
                raise ValueError
            n = int(n)
        except ValueError:
            self.page.win.show_toast(tr("箱数要填 0 或正整数"))
            self._show_qty(i)
            return
        self.page.set_qty(sc, sid, it["row"], n)
        self._show_qty(i)
        self._update_status()
        if self.cur_di == i:
            self._select_row(self.pos[i])

    def refresh_rows(self):
        """主窗口那边改了数（比如恢复建议值）→ 刷新显示"""
        if not self.detail:
            return
        for i in range(len(self.detail)):
            self._show_qty(i)
        self._filter()

    def reset_shown(self):
        if not self.page.calc:
            return
        n = 0
        for i, (sc, it, sid) in enumerate(self.detail):
            key = (sc["path"], sid, it["row"])
            if self.table.isRowHidden(self.pos[i]) or key not in self.page.edited:
                continue
            base = self.page.calc["base"][sc["path"]].get(sid, {}).get(it["row"], 0)
            self.page.set_qty(sc, sid, it["row"], base, refresh=False)
            self._show_qty(i)
            n += 1
        self.page.lines_changed()
        self._update_status()
        self.page.win.show_toast(tr("已恢复 {0} 行", n))

    def set_filter(self, path=ALL, sid=ALL):
        self.f_sup.setCurrentIndex(max(0, self.f_sup.findData(path)))
        for b in self.store_group.buttons():
            if b.property("sid") == sid:
                b.setChecked(True)
        self._filter()

    def _filter(self, *_):
        if not self.detail:
            return
        sup = self.f_sup.currentData()
        btn = self.store_group.checkedButton()
        store = btn.property("sid") if btn else ALL
        only = self.f_only.isChecked()
        only_edit = self.f_edit.isChecked()
        urgent = self.f_urgent.isChecked()
        text = self.f_text.text().strip().lower()
        calc = self.page.calc
        shown = 0
        self.table.setUpdatesEnabled(False)
        for i, (sc, it, sid) in enumerate(self.detail):
            key = (sc["path"], sid, it["row"])
            hide = (sup not in (None, ALL) and sc["path"] != sup) or (store not in (None, ALL) and sid != store)
            if not hide and only:
                hide = self._cur(sc, sid, it["row"]) <= 0 and key not in self.page.edited and \
                    calc["base"][sc["path"]].get(sid, {}).get(it["row"], 0) <= 0
            if not hide and only_edit:
                hide = key not in self.page.edited
            if not hide and urgent:
                x = calc["demand"].get(sid, {}).get(it.get("db_code") or it["code"])
                hide = not (x and "🔴" in (x["decision"] or ""))
            if not hide and text:
                hide = text not in f"{it.get('db_code') or it['code']} {it['ean']} {it['desc']}".lower()
            self.table.setRowHidden(self.pos[i], hide)
            shown += not hide
        self.table.setUpdatesEnabled(True)
        self._shown = shown
        self._update_status()

    def _update_status(self):
        colli = amt = 0.0
        for i, (sc, it, sid) in enumerate(self.detail):
            if self.table.isRowHidden(self.pos[i]):
                continue
            n = self._cur(sc, sid, it["row"])
            colli += n
            amt += n * it["pack"] * it["price"]
        self.status_l.setText(tr("显示 {0} / {1} 行 · 已改 {2} 处 · 当前筛选合计 {3:g} 箱 · €{4:,.2f}",
                                 self._shown, len(self.detail), len(self.page.edited), colli, amt))

    def export(self):
        if not self.detail:
            return
        from openpyxl import Workbook
        from openpyxl.styles import Font
        path, _ = QFileDialog.getSaveFileName(self, tr("导出 Excel"), os.path.join(
            self.page.folder.text(), f"订货明细_{datetime.now():%Y%m%d_%H%M}.xlsx"), tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        wb = Workbook()
        ws = wb.active
        ws.title = "订货明细"
        cols = self._visual_cols()          # 按现在看到的列顺序导出
        heads = [self.table.horizontalHeaderItem(c).text().replace(" ✎", "") for c in cols]
        ws.append(heads)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in range(self.table.rowCount()):
            if self.table.isRowHidden(r):
                continue
            ws.append([self.table.item(r, c).text() for c in cols])
        ws.freeze_panes = "A2"
        try:
            wb.save(path)
            self.page.win.show_toast(tr("已导出"))
        except Exception as e:
            QMessageBox.critical(self, tr("导出"), tr("导出失败：{0}", e))
