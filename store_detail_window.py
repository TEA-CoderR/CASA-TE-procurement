"""
门店配货 · 订货明细（独立窗口）
每个商品 × 门店：建议箱数、订货箱数（双击改）、金额、判断、等级、库存、销量、日均、目标库存 …
右侧详情栏：选中商品的关键数字、近 26 周销量曲线、「为什么建议这么多箱」、其他门店情况。
"""
import math
import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QFont
from PySide6.QtWidgets import (QSizePolicy, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox,
                               QLineEdit, QFrame, QTableWidgetItem, QAbstractItemView, QHeaderView, QButtonGroup,
                               QScrollArea, QFileDialog, QMessageBox, QMenu, QDialog, QCheckBox, QProgressBar,
                               QStyledItemDelegate, QSpinBox, QAbstractSpinBox)

from i18n import tr
from settings import save_settings
from theme import T, num_font, NUM_FONT
from erp_widgets import ToolWindow, MonthGrid
import store_orders as S
from reorder_page import short_decision
import reorder_service as R
import transfers as TR

ALL = "__all__"
# 明细表列（逻辑顺序）→ 显示顺序：常看、要改的列放前面
# 22~31 是和订单文件附加列一样的：月均、需求、GIA.0~GIA.5（各门店库存，0 = BMS）、GIA-period、period_sales
# 32 = 订后够卖（天）
# 默认列顺序（2026-10-19 重排，按看一行时的顺序）：
#   是哪个商品：门店、供应商、名称
#   要不要订：判断、等级
#   订多少：建议箱数、订货箱数 ✎、每箱件数、金额
#   够不够卖：本店库存、日均、库存够卖、订后够卖
#   编号：商品编号、条码
#   怎么算出来的：建议件数、目标库存、周期、件价
#   销量：30 / 60 / 90 / 180 天、月均、需求
#   各店库存：GIA.0 ~ GIA.5、GIA-period、period_sales
# 2026-10-20 加了 33 调货、34 变化、35 原因，审核时一眼看出：系统建议多少 → 调货少订多少 → 最后订多少、为什么：
#   门店 供应商 名称 | 判断 等级 | 建议箱数 调货 订货箱数 ✎ 变化 | 每箱件数 金额 | 本店库存 日均 库存够卖 订后够卖 | 原因
#   其他列默认藏起来（「显示哪些列」里打开）
# 2026-10-20 第二版（用户：要简洁、改前改后一眼看出）：
#   「判断」只写结论：不订 / 照建议订 / 多订 N 箱 / 少订 N 箱 / 建议调货（为什么建议放在鼠标提示里）
#   紧接着「原建议 → 订货箱数 → 调货」三列对比改前改后和调货件数；「变化」列默认藏起来（判断里已经写了）
# 2026-10-20 第三版：按用户自己排好的顺序做默认（调货只显示调进来的，从哪家调；调出的不显示）
#   条码 等级 周期 本店库存 日均 库存够卖 需求 每箱件数 | 建议 判断 订货箱数 调货 | 到货后够卖 件价 金额 原因
# 2026-10-21 加了 36 Codice Fornitore（供应商自己的货号，SCHEDA 里有这一列才有），默认放在条码后面
# 2026-11-03 加了 37 在途箱数（已经订了、还没到货的箱数，order_record；用户同一天定：从建议量里扣，store_orders.apply_transit），放在库存够卖后面
_ORDER = [4, 36, 18, 19, 6, 20, 11, 37, 23, 12, 15, 2, 16, 33, 32, 13, 17, 35,
          1, 0, 5, 3, 34, 14, 21, 7, 8, 9, 10, 22, 24, 25, 26, 27, 28, 29, 30, 31]
TRANSIT_K = 37
_LAYOUT_VER = 5           # 改了默认顺序就加 1：用户以前拖过的顺序作废一次，换成新的默认顺序
_EXTRA = list(range(22, 32))
TRANSFER_COL_K, CHANGE_COL_K, REASON_COL_K = 33, 34, 35
# 默认只显示审核要看的：门店 供应商 名称 判断 | 原建议 订货箱数 调货 | 金额 本店库存 库存够卖 订后够卖
# （原因放在「判断」的鼠标提示里；其他列在「更多 → 显示哪些列」里打开）
_DEFAULT_HIDDEN = [1, 0, 5, 3, 34, 14, 21, 7, 8, 9, 10, 22, 24, 25, 26, 27, 28, 29, 30, 31]
VERDICT_TOKEN = {"transfer": "ok", "more": "accent", "less": "warn", "none": "faint", "same": "text", "nodb": "err"}


def _col(k):
    return _ORDER.index(k)


def _perm(vals):
    return [vals[k] for k in _ORDER]


EDIT_COL = _col(16)
TRANSFER_COL = _col(33)
CHANGE_COL = _col(34)
REASON_COL = _col(35)
TRANSIT_COL = _col(TRANSIT_K)
AMOUNT_COL = _col(17)
AFTER_COL = _col(32)
DEC_COL = _col(2)
DI_ROLE = Qt.UserRole + 1       # 第 0 列存 detail 下标（排序后靠它找到行）
KEY_ROLE = Qt.UserRole + 2      # 排序用的值
_TIER_RANK = {"A": 0, "B": 1, "C": 2}
# 「看哪些」下拉框
VIEWS = (("order", "只看要订的"), ("all", "全部商品"), ("changed", "只看有变化的"), ("transfer", "只看调货"),
         ("edited", "只看手动改过的"), ("urgent", "只看紧急"), ("many", "只看箱数多的"))


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


def pack_warning(x, it):
    """数据库箱规和 SCHEDA 每箱件数不一样时的提醒（没问题返回空字符串）"""
    dbp = (x or {}).get("db_pack")
    try:
        dbp = float(dbp or 0)
    except (TypeError, ValueError):
        dbp = 0
    if x and dbp > 1 and abs(dbp - float(it["pack"] or 1)) > 1e-9:
        return tr("注意：数据库箱规是每箱 {0:g} 件，SCHEDA 是每箱 {1:g} 件，不一样，请核对。", dbp, it["pack"] or 1)
    return ""


class AddItemsDialog(QDialog):
    """
    加商品凑单：同一个供应商、同一家门店，把没被选进来（库存还够）但卖得快的商品加进订单，凑够起订箱数 / 金额。
    上面实时显示现在多少、起订量、还差多少；表格按日均销量从高到低排；「按销量推荐」一键补齐差额；
    改的都是「加几箱」，确认后才写进订货箱数（标成手动改过，可以「恢复建议值」）。
    """
    COLS = [tr("名称"), "EAN", tr("商品编号"), tr("等级"), tr("日均"), tr("本店库存"), tr("库存够卖（天）"), tr("已订（箱）"),
            tr("每箱件数"), tr("一箱 €"), tr("加（箱）") + " ✎", tr("订后够卖（天）"), tr("加的金额 €")]
    ADD = 10
    WIDTHS = (300, 130, 80, 50, 60, 70, 100, 70, 70, 70, 76, 100, 90)

    def __init__(self, win, path=None, sid=None):
        super().__init__(win)
        self.win, self.page = win, win.page
        self.adds = {}                     # {(路径, 门店, 行号): 加几箱}
        self.cands = []
        self._filling = False
        self.setWindowTitle(tr("加商品凑单"))
        self.resize(1320, 760)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.c_sup = QComboBox()
        self.c_sup.setMinimumWidth(240)
        for sc in self.page.calc["schede"]:
            self.c_sup.addItem(f"{sc['name']}（{tr(sc.get('day') or '')}）", sc["path"])
        self.c_store = QComboBox()
        for k, name in S.STORE_SHEETS.items():
            if k in self.page.calc["stores"]:
                self.c_store.addItem(name, k)
        if path not in (None, ALL):
            self.c_sup.setCurrentIndex(max(0, self.c_sup.findData(path)))
        if sid not in (None, ALL):
            self.c_store.setCurrentIndex(max(0, self.c_store.findData(sid)))
        top.addWidget(QLabel(tr("供应商")))
        top.addWidget(self.c_sup)
        top.addSpacing(8)
        top.addWidget(QLabel(tr("门店")))
        top.addWidget(self.c_store)
        top.addSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("编号、条码或名称"))
        self.search.setMaximumWidth(200)
        top.addWidget(self.search)
        self.only_sell = QCheckBox(tr("只看卖得动的"))
        self.only_sell.setChecked(True)
        self.only_new = QCheckBox(tr("只看这次没订的"))
        top.addWidget(self.only_sell)
        top.addWidget(self.only_new)
        top.addStretch()
        lay.addLayout(top)
        self.state = QLabel("")
        self.state.setObjectName("Banner")
        self.state.setTextFormat(Qt.RichText)
        self.state.setWordWrap(True)
        lay.addWidget(self.state)
        # 提示消息就显示在这个窗口里（不用主窗口底部的提示，会被这个窗口挡住）
        nb = QHBoxLayout()
        nb.setSpacing(10)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.RichText)
        self.note_btn = QPushButton("")
        self.note_btn.setObjectName("Outline")
        self.note_btn.clicked.connect(self._note_action)
        self._note_cb = None
        nb.addWidget(self.note, 1)
        nb.addWidget(self.note_btn)
        self.note_box = QFrame()
        self.note_box.setLayout(nb)
        self.note_box.hide()
        lay.addWidget(self.note_box)
        self.table = self.page.ui.make_table(self.COLS, stretch_col=None, numeric_cols=tuple(range(4, 13)))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)     # 列宽可以拖；名称默认够宽，鼠标停上去看全称
        hh.setStretchLastSection(False)
        for c, wd in enumerate(self.WIDTHS):
            need = hh.fontMetrics().horizontalAdvance(self.COLS[c]) + 28
            self.table.setColumnWidth(c, max(wd, need))
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.horizontalHeaderItem(6).setToolTip(tr("库存够卖 = 现在的库存按日均还能卖几天"))
        self.table.horizontalHeaderItem(11).setToolTip(tr("订后够卖 =（库存 + 已订和加的箱数 × 每箱件数）÷ 日均"))
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked |
                                   QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.horizontalHeaderItem(self.ADD).setToolTip(tr("双击填要加几箱，0 = 不加"))
        self.table.itemChanged.connect(self._edited)
        from erp_widgets import HeaderFilter        # 右键表头按列筛选（和搜索框、两个勾选一起生效）
        self._base_hide = {}
        self.hf = HeaderFilter(self.table, apply=self._filter,
                               base_hidden=lambda r: self._base_hide.get(r, False))
        lay.addWidget(self.table, 1)
        btns = QHBoxLayout()
        b_sug = QPushButton(tr("按销量推荐补齐起订量"))
        b_sug.setToolTip(tr("按日均销量从高到低排：卖得快、加了以后也不会压太久的排前面。「按销量推荐」只加加完后库存够卖不超过 {0} 天的商品（配货设置里的凑单天数）。",
                            int(self.page.fill_cfg().get("max_stock_days") or 60)))
        b_sug.setObjectName("Outline")
        b_sug.clicked.connect(self.suggest)
        b_clear = QPushButton(tr("清空这家的"))
        b_clear.clicked.connect(self.clear)
        btns.addWidget(b_sug)
        btns.addWidget(b_clear)
        btns.addStretch()
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(tr("加入订单"))
        self.ok.setObjectName("Primary")
        self.ok.clicked.connect(self.apply)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)
        self.c_sup.currentIndexChanged.connect(lambda _: self.load())
        self.c_store.currentIndexChanged.connect(lambda _: self.load())
        self.search.textChanged.connect(lambda _: self._filter())
        self.only_sell.toggled.connect(lambda _: self._filter())
        self.only_new.toggled.connect(lambda _: self._filter())
        self.load()

    # ---- 数据 ----
    def _sc(self):
        p = self.c_sup.currentData()
        return next((x for x in self.page.calc["schede"] if x["path"] == p), None)

    def _lines(self, sc, sid):
        return self.page.colli.get(sc["path"], {}).get(sid, {})

    def load(self):
        sc, sid = self._sc(), self.c_store.currentData()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.cands = []
        if not sc or sid is None:
            self._update_state()
            return
        if sid not in sc["sheets"]:
            self.state.setText(tr("这个 SCHEDA 里没有 {0} 的页，这家店不能从这个供应商订货", S.STORE_SHEETS.get(sid, sid)))
            return
        self.cands = S.add_candidates(sc, sid, self.page.calc["demand"], self._lines(sc, sid))
        self._filling = True
        self.table.setRowCount(len(self.cands))
        for r, c in enumerate(self.cands):
            self._fill_row(r, c, sc, sid)
        self._filling = False
        self.table.setSortingEnabled(True)
        self._filter()
        self._update_state()

    def _fill_row(self, r, c, sc, sid):
        it = c["item"]
        add = self.adds.get((sc["path"], sid, c["row"]), 0)
        da = S.days_after(c, add)

        def days(v):
            return "∞" if v == float("inf") else f"{v:.0f}"

        ds = c.get("days_stock")
        total = c["ordered"] + add
        vals = [(it["desc"], it["desc"]), (str(it.get("ean") or ""), str(it.get("ean") or "")),
                (str(S.item_code(it)), str(S.item_code(it))),
                (c["tier"], _TIER_RANK.get(c["tier"], 9)), (f"{c['daily']:.2f}", c["daily"]), (f"{c['inv']:g}", c["inv"]),
                (S.days_text(ds), 1e12 if ds is None or math.isinf(ds) else ds),
                (f"{c['ordered']:g}" if c["ordered"] else "", c["ordered"]),
                (f"{it['pack']:g}", it["pack"]), (f"{c['box']:,.2f}", c["box"]), (f"{add:g}" if add else "", add),
                (days(da) if total else "", (1e12 if math.isinf(da) else da) if total else -1),
                (f"{add * c['box']:,.2f}" if add else "", add * c["box"])]
        for k, (text, key) in enumerate(vals):
            item = _SortItem(text)
            item.setData(KEY_ROLE, key)
            flags = item.flags() & ~Qt.ItemIsEditable
            if k == self.ADD:
                flags |= Qt.ItemIsEditable
                item.setToolTip(tr("双击填要加几箱"))
                if add:
                    item.setForeground(QColor(T("accent")))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
            if k >= 3:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFlags(flags)
            if k == 0:
                item.setData(DI_ROLE, c["row"])
                item.setToolTip(f"{it['desc']}\nEAN {it.get('ean') or '—'} · {tr('编号')} {S.item_code(it)}")
            self.table.setItem(r, k, item)
        if add and da > float(self.page.fill_cfg().get("max_stock_days") or 60):
            self.table.item(r, self.ADD + 1).setForeground(QColor(T("warn")))
            self.table.item(r, self.ADD + 1).setToolTip(tr("加完以后库存要很久才卖得完"))

    def _filter(self):
        q = self.search.text().strip().lower()
        sc, sid = self._sc(), self.c_store.currentData()
        by_row = {c["row"]: c for c in self.cands}
        base = {}
        for r in range(self.table.rowCount()):
            c = by_row.get(self.table.item(r, 0).data(DI_ROLE))
            if c is None:
                continue
            it = c["item"]
            added = self.adds.get((sc["path"], sid, c["row"]), 0) if sc else 0
            hide = False
            if self.only_sell.isChecked() and c["daily"] <= 0 and not added:
                hide = True
            if self.only_new.isChecked() and c["ordered"] > 0 and not added:
                hide = True
            if q and q not in f"{S.item_code(it)} {it.get('ean', '')} {it['desc']}".lower():
                hide = True
            base[r] = hide
            if not hide and self.hf.active():
                hide = not self.hf.accepts(r)
            self.table.setRowHidden(r, hide)
        self._base_hide = base

    def _edited(self, item):
        if self._filling or item.column() != self.ADD:
            return
        row = self.table.item(item.row(), 0).data(DI_ROLE)
        text = item.text().strip().replace(",", ".")
        try:
            n = float(text or 0)
            if n < 0 or n != int(n):
                raise ValueError
            n = int(n)
        except ValueError:
            self.msg(tr("箱数要填 0 或正整数"), "err")
            n = None
        # 等编辑框关掉以后再重画这一行（编辑中途换掉格子会让程序卡住）
        QTimer.singleShot(0, lambda: self._set_add(row, n))

    def _set_add(self, row, n):
        sc, sid = self._sc(), self.c_store.currentData()
        if not sc:
            return
        key = (sc["path"], sid, row)
        if n is not None:
            if n > 0:
                self.adds[key] = n
            else:
                self.adds.pop(key, None)
        self._refresh_row(row)
        self._update_state()

    def _refresh_row(self, row):
        sc, sid = self._sc(), self.c_store.currentData()
        c = next((x for x in self.cands if x["row"] == row), None)
        if c is None:
            return
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0) and self.table.item(r, 0).data(DI_ROLE) == row:
                sorting = self.table.isSortingEnabled()
                self.table.setSortingEnabled(False)
                self._filling = True
                self._fill_row(r, c, sc, sid)
                self._filling = False
                self.table.setSortingEnabled(sorting)
                break

    def _update_state(self):
        sc, sid = self._sc(), self.c_store.currentData()
        if not sc or sid is None:
            self.state.setText("")
            return
        items = {it["row"]: it for it in sc["items"]}
        cur = dict(self._lines(sc, sid))
        c0, a0 = S._totals(cur, items)
        new = dict(cur)
        n_add = 0
        for (p, s_, row), n in self.adds.items():
            if p == sc["path"] and s_ == sid:
                new[row] = new.get(row, 0) + n
                n_add += n
        c1, a1 = S._totals(new, items)
        mc, ma = sc.get("moq_colli") or 0, sc.get("moq_amount") or 0
        need = S.moq_label(S.sc_moq(sc), mark=False)
        ok = not math.isinf(mc) and S._satisfied(c1, a1, mc, ma)
        gap = []
        if mc and not math.isinf(mc) and c1 < mc:
            gap.append(tr("{0:g} 箱", mc - c1))
        if ma and a1 < ma:
            gap.append(f"€{ma - a1:,.2f}")
        txt = tr("<b>{0} · {1}</b>　现在 {2:g} 箱 / €{3:,.2f}　起订 {4}", S.STORE_SHEETS.get(sid, sid), sc["name"], c0, a0, need)
        if n_add:
            txt += tr("　→　加 {0:g} 箱后 <b>{1:g} 箱 / €{2:,.2f}</b>", n_add, c1, a1)
        txt += "　" + (tr("<b>✔ 够起订量</b>") if ok else tr("还差 <b>{0}</b>", tr(" 和 ").join(gap)) if gap else "")
        self.state.setText(txt)
        kind = "ok" if ok else "warn"
        self.state.setStyleSheet(f"background:{T(kind + '_soft')}; color:{T(kind)}; padding:8px 12px; border-radius:8px;")
        total = sum(self.adds.values())
        self.ok.setText(tr("加入订单（{0} 箱）", total) if total else tr("加入订单"))
        self.ok.setEnabled(bool(total))

    def msg(self, text, kind="info", action=None, callback=None):
        """在窗口里显示一条提示；action / callback：提示旁边的按钮"""
        self.note.setText(text)
        self.note_box.setStyleSheet(f"QFrame {{ background:{T(kind + '_soft')}; border-radius:8px; }}"
                                    f"QLabel {{ color:{T(kind)}; background:transparent; padding:6px 10px; }}")
        self._note_cb = callback
        self.note_btn.setVisible(bool(action))
        if action:
            self.note_btn.setText(action)
        self.note_box.show()

    def _note_action(self):
        cb, self._note_cb = self._note_cb, None
        self.note_btn.hide()
        if cb:
            cb()

    def _apply_suggestion(self, sc, sid, got):
        for row, n in got.items():
            key = (sc["path"], sid, row)
            self.adds[key] = self.adds.get(key, 0) + n
        self.load()
        self.msg(tr("已按销量推荐加 {0} 个商品、{1:g} 箱，表里标蓝的就是，可以再改。", len(got), sum(got.values())), "ok")

    # ---- 按钮 ----
    def suggest(self):
        sc, sid = self._sc(), self.c_store.currentData()
        if not sc or sid not in sc["sheets"]:
            return
        cur = dict(self._lines(sc, sid))
        for (p, s_, row), n in self.adds.items():
            if p == sc["path"] and s_ == sid:
                cur[row] = cur.get(row, 0) + n
        limit = float(self.page.fill_cfg().get("max_stock_days") or 60)
        got = S.suggest_fill(sc, sid, self.page.calc["demand"], cur, limit)
        if got == {}:
            self.msg(tr("已经够起订量了，不用再加。"), "ok")
            return
        if got:
            self._apply_suggestion(sc, sid, got)
            return
        # 天数限制内凑不齐：不管天数再推荐一次（还是先加加完最快卖完的），告诉用户要压多久
        loose = S.suggest_fill(sc, sid, self.page.calc["demand"], cur, float("inf"))
        if not loose:
            self.msg(tr("这家店在这个供应商卖得动的商品不够凑起订量，可以自己在表里加，或者让它并到 BMS。"), "warn")
            return
        cands = {c["row"]: c for c in self.cands}
        worst = max((S.days_after(cands[r], n) for r, n in loose.items() if r in cands), default=0)
        self.msg(tr("在「加完最多够卖 {0:g} 天」的限制内凑不齐：这家店这些商品库存都还很足。不管天数的话，推荐加 {1} 个商品、{2:g} 箱，加完最久要 <b>{3:.0f} 天</b>才卖得完。",
                    limit, len(loose), sum(loose.values()), worst), "warn",
                 tr("仍然按销量推荐"), lambda: self._apply_suggestion(sc, sid, loose))

    def clear(self):
        sc, sid = self._sc(), self.c_store.currentData()
        if not sc:
            return
        for key in [k for k in self.adds if k[0] == sc["path"] and k[1] == sid]:
            self.adds.pop(key)
        self.load()

    def apply(self):
        by_path = {sc["path"]: sc for sc in self.page.calc["schede"]}
        paths = set()
        for (path, sid, row), n in self.adds.items():
            sc = by_path.get(path)
            if not sc or n <= 0:
                continue
            cur = self.page.colli.get(path, {}).get(sid, {}).get(row, 0)
            self.page.set_qty(sc, sid, row, cur + n, refresh=False)
            paths.add(path)
        for p in paths:
            self.page.lines_changed(p)
        self.accept()


class DetailWindow(ToolWindow):
    months_ready = Signal(object)        # 近两年每月销量读好了（后台线程 → 界面）
    transit_ready = Signal(object)       # 在途读好了（order_record）

    def __init__(self, page):
        super().__init__(page.win, tr("订货明细"), (1400, 860))
        self.page = page
        import reorder_service as _R
        self._m24, self._m24_state, self._m24_keys, self._m24_token = {}, {}, _R.month_keys(24), 0
        self.months_ready.connect(self._months_arrived)
        self.transit_ready.connect(self._transit_arrived)
        self.transit, self._tr_token = None, 0
        self.ui = page.ui
        self.detail = []
        self.pos = []
        self.sort_col, self.sort_order = 0, Qt.AscendingOrder
        self._filling = False
        self._shown = 0
        self.cur_di = None
        self._loading = False         # 正在分批填表（填完前不筛选、不排序）
        self._load_token = 0
        self._pending_filter = None

        root = QWidget()
        root.setObjectName("Dialog")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ---- 工具条（一行）----
        # 门店 | 供应商 | 搜索 | 看哪些（下拉） ……  加商品凑单 | 恢复建议值 | 更多 ▾（显示哪些列、导出 Excel）
        # 按钮少、筛选收进一个下拉框，一行放得下，窗口不会被撑得比屏幕宽
        bar = QFrame()
        bar.setObjectName("TopBar")
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(16, 8, 16, 8)
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
        for c in self.store_group.buttons():
            c.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)   # 文字长（英文）时不被挤掉
            bf = c.font()
            bf.setBold(True)       # 选中时是粗体，按粗体留宽度
            c.setMinimumWidth(QFontMetrics(bf).horizontalAdvance(c.text()) + 28)
        tb.addSpacing(6)
        self.f_sup = QComboBox()
        self.f_sup.setMinimumWidth(130)
        self.f_sup.setMaximumWidth(240)
        self.f_sup.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.f_sup.setMinimumContentsLength(10)
        self.f_sup.currentIndexChanged.connect(self._filter)
        tb.addWidget(self.f_sup)
        self.f_text = QLineEdit()
        self.f_text.setPlaceholderText(tr("编号、条码或名称"))
        self.f_text.setMinimumWidth(90)
        self.f_text.setMaximumWidth(200)
        self.f_text.textChanged.connect(self._filter)
        tb.addWidget(self.f_text)
        # 看哪些：一个下拉框代替原来四个开关
        self.f_view = QComboBox()
        for key, text in VIEWS:
            self.f_view.addItem(tr(text), key)
        self.f_view.setToolTip(tr("只看要订的：要订、系统建议过、改过或有调货的商品"))
        self.f_view.currentIndexChanged.connect(self._filter)
        tb.addWidget(self.f_view)
        tb.addStretch()
        b_add = QPushButton(tr("加商品凑单"))
        b_add.setToolTip(tr("同一个供应商、同一家店：把没被选进来但卖得快的商品加进订单，凑够起订箱数或金额"))
        b_add.clicked.connect(self.add_items)
        tb.addWidget(b_add)
        self.btn_reset = QPushButton(tr("恢复建议值"))
        self.btn_reset.setToolTip(tr("把当前筛选出来的行恢复成系统建议的箱数"))
        self.btn_reset.clicked.connect(self.reset_shown)
        tb.addWidget(self.btn_reset)
        more = QPushButton(tr("更多") + " ▾")
        more.setToolTip(tr("显示哪些列、导出 Excel"))
        more.clicked.connect(lambda: self._more_menu(more))
        tb.addWidget(more)
        v.addWidget(bar)
        # 载入进度：上万行分批填，窗口不卡；填完自动隐藏
        lb = QFrame()
        lh = QHBoxLayout(lb)
        lh.setContentsMargins(16, 6, 16, 6)
        lh.setSpacing(10)
        self.load_lbl = QLabel(tr("正在载入订货明细…"))
        self.load_lbl.setObjectName("Faint")
        self.load_bar = QProgressBar()
        self.load_bar.setTextVisible(False)
        self.load_bar.setFixedHeight(6)
        lh.addWidget(self.load_lbl)
        lh.addWidget(self.load_bar, 1)
        self.load_box = lb
        lb.hide()
        v.addWidget(lb)

        # ---- 表格 + 详情栏 ----
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.table = self.ui.make_table(_perm([tr("供应商"), tr("门店"), tr("判断"), tr("商品编号"), tr("条码"), tr("名称"),
                                               tr("本店库存"), "30", "60", "90", "180", tr("库存够卖（天）"), tr("每箱件数"),
                                               tr("件价"), tr("建议件数"), tr("建议（箱）"), tr("订货箱数"), tr("金额"),
                                               tr("等级"), tr("周期"), tr("日均"), tr("目标库存"),
                                               tr("月均"), tr("需求（件）")] + [f"GIA.{i}" for i in range(6)] +
                                              ["GIA-period", "period_sales", tr("到货后够卖（天）"),
                                               tr("调货"), tr("变化（箱）"), tr("原因"), "Codice Fornitore", tr("在途箱数")]),
                                        numeric_cols=[_col(k) for k in list(range(6, 18)) + [19, 20, 21, 32, 34, 37] + _EXTRA])
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setFocusPolicy(Qt.StrongFocus)
        import product_images as PI
        self.table.verticalHeader().setDefaultSectionSize(PI.row_height(self.page.settings))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        import product_images as PI          # 条码那一格左边放商品小图（只下载看得见的行）
        self.table.setItemDelegateForColumn(_col(4), PI.ThumbDelegate(self.table, _col(4), None, self.page.settings))
        for c, wd in enumerate(_perm((130, 92, 84, 80, PI.thumb_size(self.page.settings) + 120, 270, 70, 50, 50, 50, 50, 70, 66, 62, 70, 66, 76, 80, 46, 46, 60, 70,
                                      56, 56, 54, 54, 54, 54, 54, 54, 80, 86, 96, 190, 110, 380, 110, 60))):
            hdr = self.table.horizontalHeaderItem(c)
            need = hh.fontMetrics().horizontalAdvance(hdr.text() if hdr else "") + 26
            self.table.setColumnWidth(c, max(wd, need))
        self.table.horizontalHeaderItem(EDIT_COL).setText(tr("订货箱数") + " ✎")
        self.table.horizontalHeaderItem(EDIT_COL).setToolTip(tr("双击修改箱数，0 = 不订"))
        self.table.horizontalHeaderItem(_col(18)).setToolTip(tr("销量等级：A 卖得快，B 一般，C 卖得慢"))
        self.table.horizontalHeaderItem(_col(15)).setToolTip(tr("系统按销量和库存算出来的箱数（调货、手动改之前），和「订货箱数」对比"))
        self.table.horizontalHeaderItem(DEC_COL).setToolTip(
            tr("这一行怎么处理：不订 / 照建议订 / 多订 / 少订（和原建议比）/ 建议调货。鼠标停在格子上看为什么"))
        self.table.horizontalHeaderItem(TRANSFER_COL).setToolTip(
            tr("门店间调货：从哪家门店调进几件（调进来的就少订）。双击可以改：选调出门店、调几件，或者不调（比如过季品）"))
        self.table.cellDoubleClicked.connect(self._edit_transfer)     # 双击「调货」：选调出门店、填件数
        self.table.cellDoubleClicked.connect(self._open_transit)      # 双击「在途箱数」：看是哪几张单、标到货
        self.table.horizontalHeaderItem(TRANSFER_COL).setText(tr("调货") + " ✎")
        self.table.horizontalHeaderItem(CHANGE_COL).setToolTip(tr("订货箱数 − 建议箱数，括号里是为什么变：调货 / 手动改"))
        self.table.horizontalHeaderItem(REASON_COL).setToolTip(tr("系统为什么这么建议：库存够卖几天、订货周期里大约卖多少"))
        self.table.horizontalHeaderItem(TRANSIT_COL).setToolTip(
            tr("在途箱数：已经订了、还没到货的箱数（采购订单跟踪里没到货的单 + RECORD ORDINE 里的订单文件）。"
               "订单跟踪设置里「建议量扣掉在途」打开时（默认）已经从建议里扣掉了；鼠标停在格子上看是哪几张单"))
        self.table.horizontalHeaderItem(_col(11)).setToolTip(tr("库存够卖 = 本店库存 ÷ 日均（账面库存是负数时按 0 算）。总仓采购、检查订货清单、DETERSIVO采购都是这个算法"))
        self.table.horizontalHeaderItem(AFTER_COL).setToolTip(
            tr("到货后够卖 =（库存 + 订的货 + 调进来的 − 调给别的店的）÷ 日均：这次订的货和调的货都到了以后能卖几天；超过凑单天数标黄，可能压货"))
        for k, tip in ((22, tr("月均：新算法 = 日均（加权 + 季节）× 30；原程序算法 = 最长周期销量 ÷ 月数（默认 180 天 ÷ 6）")),
                       (23, tr("需求：新算法 = 日均 × 要备的天数（订货周期 × 等级系数 + 到货天数），不含安全库存；原程序算法 = 90 天日均 × 订货周期")),
                       (24, tr("GIA.0 = BMS 总仓库存；GIA.1 ~ GIA.5 = 各门店库存")),
                       (30, tr("GIA-period = 本店库存（负数按 0）− period_sales")),
                       (31, tr("period_sales = 第 3 个周期（默认 90 天）的销量"))):
            self.table.horizontalHeaderItem(_col(k)).setToolTip(tip)
        for i in range(1, 6):
            self.table.horizontalHeaderItem(_col(24 + i)).setToolTip(
                tr("GIA.{0} = {1} 的库存", i, S.STORE_SHEETS.get(i, "")))
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
        hh.setToolTip(tr("点表头排序；右键表头按这一列筛选；拖动表头可以换列的位置"))
        from erp_widgets import ColumnChooser
        self.cols = ColumnChooser(self.table, self.page.cfg, "detail_cols", _ORDER, lambda: save_settings(self.page.settings),
                                  _DEFAULT_HIDDEN, ver=4)
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)
        hh.setSortIndicator(0, Qt.AscendingOrder)
        hh.sectionClicked.connect(self._sort_by)
        # 右键表头：按这一列筛选（像 Excel），和上面的门店按钮 / 搜索 / 「看哪些」一起生效
        from erp_widgets import HeaderFilter
        self._base_hide = {}
        self.hf = HeaderFilter(self.table, apply=self._filter, own_menu=False, sort=self._sort_menu,
                               base_hidden=lambda r: self._base_hide.get(r, False),
                               extra=[(tr("恢复默认列顺序"), self._reset_order),
                                      (tr("显示哪些列…"), lambda: self.cols.menu(self.hf.last_pos))])
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
        self.status_r = QLabel("")
        self.status_r.setObjectName("StatusText")
        sh.addWidget(self.status_l, 1)
        sh.addWidget(self.status_r)
        v.addWidget(sb)

    # ---------- 列顺序 / 宽度（记住） ----------
    def _restore_columns(self):
        cfg = self.page.cfg
        if cfg.get("detail_columns_ver") != _LAYOUT_VER:
            cfg.pop("detail_columns", None)        # 默认顺序重排过：旧的拖动顺序不用了（列宽保留）
            cfg["detail_columns_ver"] = _LAYOUT_VER
            save_settings(self.page.settings)
        saved = cfg.get("detail_columns")
        if isinstance(saved, list) and saved and TRANSIT_K not in saved:   # 2026-11-03 新加的「在途」：插在库存够卖后面
            saved.insert(saved.index(11) + 1 if 11 in saved else len(saved), TRANSIT_K)
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
        hh = self.table.horizontalHeader()
        if self._loading:
            self.flash(tr("还在载入，稍等一下"))
            return
        self.hf.menu(hh.logicalIndexAt(pos), hh.mapToGlobal(pos))

    def _reset_order(self):
        hh = self.table.horizontalHeader()
        hh.blockSignals(True)
        for c in range(self.table.columnCount()):
            cur = hh.visualIndex(c)
            if cur != c:
                hh.moveSection(cur, c)
        hh.blockSignals(False)
        self.page.cfg.pop("detail_columns", None)
        save_settings(self.page.settings)

    def _sort_menu(self, col, desc):
        if not self.detail or self._loading:
            return
        self.sort_col = col
        self.sort_order = Qt.DescendingOrder if desc else Qt.AscendingOrder
        self.table.setUpdatesEnabled(False)
        self._apply_sort()
        self.table.setUpdatesEnabled(True)
        self._filter()

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
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)     # 详情栏只上下滚，内容自动换行
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
        self.d_codes.setWordWrap(True)            # 编号一行放不下就换行（不然把详情栏撑宽，右边被切掉）
        self.d_over.setWordWrap(True)
        self.d_img = QLabel()                     # 商品图（公司图片服务器上有这个条码的图才显示）
        self.d_img.setAlignment(Qt.AlignCenter)
        self.d_img.setMinimumHeight(180)
        self.d_img.hide()
        v.addWidget(self.d_img)
        v.addWidget(self.d_over)
        v.addWidget(self.d_title)
        v.addWidget(self.d_codes)
        g = QGridLayout()
        g.setSpacing(8)
        self.minis = []
        for i, lab in enumerate((tr("库存"), tr("月销"), tr("现在够卖"))):
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
            g.addWidget(f, 0, i)
            self.minis.append((b, c))
        v.addLayout(g)
        sp = QFrame()
        sp.setObjectName("MiniCard")
        spv = QVBoxLayout(sp)
        spv.setContentsMargins(12, 10, 12, 10)
        hl = QHBoxLayout()
        t = QLabel(tr("近两年每月销量"))
        t.setStyleSheet("font-weight:600; font-size:12px;")
        u = QLabel(tr("件 · 颜色越深卖得越多"))
        u.setObjectName("MiniLabel")
        hl.addWidget(t, 1)
        hl.addWidget(u)
        spv.addLayout(hl)
        self.months_w = MonthGrid()               # 1–12 月方框：今年在上、去年在下（和检查门店订货清单一样）
        spv.addWidget(self.months_w)
        self.months_note = QLabel("")
        self.months_note.setObjectName("MiniLabel")
        self.months_note.setWordWrap(True)
        spv.addWidget(self.months_note)
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
        self.d_over.setText(f"{S.STORE_SHEETS[sid]} · {sc['name']}")
        self.d_title.setText(it["desc"])
        import product_images as PI
        PI.set_label_image(self.d_img, it.get("ean"), 180, self.page.settings)
        fc = f" · Cod.Forn. {it['sup_code']}" if it.get("sup_code") else ""
        self.d_codes.setText(f"{S.item_code(it)} · {it['ean']}{fc}\n{it['pack']:g} {tr('件/箱')} · €{it['price']:.3f}/{tr('件')}")
        (a1, a2), (b1, b2), (c1, c2) = self.minis
        if x:
            inv = float(x.get("inv_raw", x["inv"]) or 0)
            a1.setText(f"{inv:g}")      # 显示原值（计算时负数当 0）
            a2.setText(tr("件") + ("  " + tr("（账面负数）") if inv < 0 else ""))
            try:
                mo = S.monthly_and_need(x, calc["days"])[0]
            except Exception:
                mo = (x.get("daily") or 0) * 30
            b1.setText(f"{mo:g}" if isinstance(mo, (int, float)) else "—")
            b2.setText(tr("件 / 月"))
            c1.setText(S.days_text(S.cover_days(x)) or "0")
            cyc = x.get("cycle")
            c2.setText(tr("天 · 周期 {0:g} 天", cyc) if isinstance(cyc, (int, float)) else tr("天"))
        else:
            for m1, m2 in self.minis:
                m1.setText("—")
                m2.setText("")
        self._show_months(sid, it.get("db_code") or it["code"], bool(x))
        n = self._cur(sc, sid, it["row"])
        orig = self._sugg(sc, sid, it["row"])
        self.why_t.setText(tr("为什么建议 {0:g} 箱", orig) if orig else tr("为什么不用订"))
        lines = self._reason_lines(sc, it, sid)
        if (sc["path"], sid, it["row"]) in self.page.edited:
            lines.append(tr("你手动改成了 {0:g} 箱", n))
        lines += ["⚠ " + m[2] for m in self._marks(sc, it, sid, n)]
        warn = pack_warning(x, it)
        self.why.setText("\n".join("· " + l for l in lines) + (("\n" + warn) if warn else ""))
        while self.others.count():
            w = self.others.takeAt(0).widget()
            if w:
                w.deleteLater()
        for osid in calc["stores"]:
            if osid == sid or osid not in sc["sheets"]:
                continue
            ox = calc["demand"].get(osid, {}).get(it.get("db_code") or it["code"])
            on = self._cur(sc, osid, it["row"])
            if ox:
                dv = S.cover_days(ox)
                body = tr("库存 {0:g} · 够卖 {1} 天 · 订 {2:g} 箱", ox.get("inv_raw", ox["inv"]), S.days_text(dv) or "—", on)
            else:
                body = tr("数据库没有")
            l = QLabel(f"{S.STORE_SHEETS[osid]}    " + body)
            l.setObjectName("Faint" if not on else "")
            self.others.addWidget(l)

    # ---------- 近两年每月销量（后台按门店读，只读数据库）----------
    def _show_months(self, sid, code, in_db=True):
        code = str(code)
        if not in_db or code.startswith(S.DUP_PREFIX):
            self.months_w.set_message(tr("数据库里没有这个商品"))
            self.months_note.setText("")
            return
        st = self._m24_state.get(sid)
        if st is None:
            self._load_months([sid])
            st = "loading"
        if st == "loading":
            self.months_w.set_message(tr("正在读取…"))
            self.months_note.setText("")
            return
        if st != "done":
            self.months_w.set_message(tr("没读到（数据库）"))
            self.months_note.setText(str(st))
            return
        vals = self._m24.get((sid, code)) or [0.0] * len(self._m24_keys)
        self.months_w.set_data(vals, self._m24_keys)
        cur, prev, full = self.months_w.ytd()
        m = self._m24_keys[-1][1]
        note = tr("今年 1–{0} 月 {1:g} 件", m, round(cur, 1))
        note += " · " + (tr("去年同期 {0:g} 件（{1:+.0%}）", round(prev, 1), cur / prev - 1) if prev else tr("去年同期没卖"))
        note += "\n" + tr("去年全年 {0:g} 件 · * 本月到今天", round(full, 1))
        self.months_note.setText(note)

    def _load_months(self, stores):
        """这几家门店这次订货明细里的商品，最近 24 个月每月销量（一家店一条查询，后台线程）"""
        import threading
        import reorder_service as R
        calc = self.page.calc or {}
        todo = [sid for sid in stores if self._m24_state.get(sid) is None]
        if not todo:
            return
        for sid in todo:
            self._m24_state[sid] = "loading"
        codes = {sid: [c for c in (calc.get("demand", {}).get(sid) or {}) if str(c).isdigit()] for sid in todo}
        dbs = self.page.settings.get("db") or None
        token = self._m24_token

        def work():
            try:
                db = R.make_db(dbs)
            except Exception as e:
                for sid in todo:
                    self.months_ready.emit((token, sid, None, str(e)))
                return
            for sid in todo:
                try:
                    self.months_ready.emit((token, sid, R.query_monthly_sales(db, sid, codes[sid]), None))
                except Exception as e:
                    self.months_ready.emit((token, sid, None, str(e)))
            try:
                db.engine.dispose()
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _months_arrived(self, payload):
        import reorder_service as R
        token, sid, data, err = payload
        if token != self._m24_token:
            return
        if err:
            self._m24_state[sid] = err
        else:
            for c, by in data.items():
                self._m24[(sid, c)] = R.monthly_series(by, self._m24_keys)
            self._m24_state[sid] = "done"
        if self.cur_di is not None and 0 <= self.cur_di < len(self.detail):
            sc, it, csid = self.detail[self.cur_di]
            if csid == sid:
                x = self.page.calc["demand"].get(sid, {}).get(it.get("db_code") or it["code"])
                self._show_months(sid, it.get("db_code") or it["code"], bool(x))

    # ---------- 填表 ----------
    def _warn_boxes(self):
        """建议超过几箱标黄（算法设置「箱数提醒」，2026-11-07 用户定 6 箱；只提醒、不砍）。原程序算法不提醒"""
        try:
            from reorder_page import algo_params
            used = ((self.page.calc or {}).get("demand") or {}).get("_algo") or {}
            if used.get("engine") != "v2":
                return 0
            return float(algo_params(self.page.settings).get("warn_boxes") or 0)
        except Exception:
            return 0

    def _many(self, sug):
        n = getattr(self, "_warn_n", None)
        if n is None:
            n = self._warn_n = self._warn_boxes()
        return bool(n) and (sug or 0) > n

    def load(self):
        """
        填订货明细表。上万行（17 家供应商 × 5 家门店）一次填完要好几秒、窗口会卡住，
        所以分批填：每批 CHUNK 行，批与批之间让界面喘口气，上面显示进度条；填完再筛选、排序。
        """
        calc = self.page.calc
        stores = calc["stores"]
        d = calc["days"]
        self._warn_n = self._warn_boxes()
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
        import reorder_service as _R
        self._m24, self._m24_state, self._m24_keys = {}, {}, _R.month_keys(24)     # 重新配货：每月销量重新读
        self._m24_token += 1
        self.sup_index = {sc["path"]: k for k, sc in enumerate(calc["schede"])}
        for sc in calc["schede"]:
            for it in sc["items"]:
                for sid in stores:
                    if sid in sc["sheets"]:
                        self.detail.append((sc, it, sid))
        t = self.table
        self._load_token += 1
        self._loading = True
        self._filling = True
        t.setSortingEnabled(False)
        t.setRowCount(0)
        t.setRowCount(len(self.detail))
        self.pos = list(range(len(self.detail)))
        self._num_cols = set(t._numeric)
        self._num_font = QFont(NUM_FONT, 10)
        self.load_bar.setRange(0, max(1, len(self.detail)))
        self.load_bar.setValue(0)
        self.load_box.setVisible(len(self.detail) > self.CHUNK)
        self._load_chunk(0, self._load_token)

    CHUNK = 300               # 每批行数：小一点，后台预先载入时主窗口也不卡

    def _load_chunk(self, start, token):
        if token != self._load_token:
            return                                  # 又重新载入了，这一批作废
        t = self.table
        end = min(len(self.detail), start + self.CHUNK)
        t.setUpdatesEnabled(False)
        for i in range(start, end):
            self._fill_row(i)
            self._show_qty(i)
        t.setUpdatesEnabled(True)
        self.load_bar.setValue(end)
        self.load_lbl.setText(tr("正在载入订货明细… {0:,} / {1:,} 行", end, len(self.detail)))
        if end < len(self.detail):
            QTimer.singleShot(0, lambda: self._load_chunk(end, token))
            return
        self._finish_load()

    def _finish_load(self):
        self._loading = False
        self._load_months(list(self.page.calc.get("stores") or []))      # 详情栏的近两年每月销量：后台先读好
        self._load_transit()                                               # 在途列：后台读
        self.load_box.hide()
        t = self.table
        t.setUpdatesEnabled(False)
        if self.sort_col != 0 or self.sort_order != Qt.AscendingOrder:
            self._apply_sort()
        t.setUpdatesEnabled(True)
        self._filling = False
        if self._pending_filter is not None:
            path, sid = self._pending_filter
            self._pending_filter = None
            self.set_filter(path, sid)
        else:
            self._filter()

    def _fill_row(self, i):
        """直接建好每一格（以前先建一遍再复制一遍，慢一倍）"""
        calc = self.page.calc
        d = calc["days"]
        t = self.table
        sc, it, sid = self.detail[i]
        x = calc["demand"].get(sid, {}).get(it.get("db_code") or it["code"])
        sug_c = self._orig(sc, sid, it["row"])          # 系统建议（调货之前、扣了在途）；调货少订的在「调货」「变化」两列
        sug_s = self._sugg(sc, sid, it["row"])          # 「建议（箱）」列：没扣在途
        # 库存够卖 = 库存 ÷ 日均（负库存按 0 算），和「订后够卖」、检查订货清单同一个算法
        inv_days = S.days_text(S.cover_days(x)) if x else ""
        vals = _perm([
            sc["name"], S.STORE_SHEETS[sid], short_decision(x["decision"]) if x else (tr("同上（重复编号）") if it.get("dup_of") else tr("数据库没有")),
            S.item_code(it), it["ean"], it["desc"],
            f"{x.get('inv_raw', x['inv']):g}" if x else "", *([f"{v:g}" for v in x["sales"]] if x else [""] * 4),
            inv_days or "", f"{it['pack']:g}", f"{it['price']:.3f}".rstrip("0").rstrip("."),
            f"{x['sugg']:,}" if x else "", f"{sug_s:g}" if sug_s else "", "", "",
            (x["tier"] or "") if x else "",
            f"{x['cycle']:g}" if x and isinstance(x.get("cycle"), (int, float)) else "",
            f"{x['daily']:.2f}" if x and x.get("daily") else "",
            f"{x['target']:.0f}" if x and isinstance(x.get("target"), (int, float)) else ""] +
            self._extra_texts(x, sug_c, d) + ["", self._transfer_text(sc, it, sid), "",
                                              "；".join(self._reason_lines(sc, it, sid)), it.get("sup_code") or "",
                                              self._transit_text(it, sid)])
        keys = _perm(self._sort_keys(i, x, sug_c) + self._extra_keys(x, sug_c, d) +
                     [0.0, self._transfer_key(sc, it, sid), 0.0, vals[REASON_COL].lower(),
                      str(it.get("sup_code") or ""), self._transit_boxes(it, sid)])
        num_cols, nf = self._num_cols, self._num_font
        ro = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        for c in range(len(vals)):
            item = _SortItem("" if vals[c] is None else str(vals[c]))
            if c in num_cols:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setFont(nf)
            item.setData(KEY_ROLE, keys[c])
            if c == EDIT_COL:
                item.setFlags(ro | Qt.ItemIsEditable)
                item.setToolTip(tr("双击修改箱数，0 = 不订"))
            else:
                item.setFlags(ro)
            t.setItem(i, c, item)
        tier = (x["tier"] or "") if x else ""
        if tier:
            tc = t.item(i, _col(18))
            tc.setForeground(QColor(T("ok") if tier == "A" else T("accent") if tier == "B" else T("muted")))
            f = tc.font()
            f.setBold(True)
            tc.setFont(f)
        t.item(i, 0).setData(DI_ROLE, i)
        self._style_transit(i, i)
        self._style_transfer(i, i)

    # ---------- 在途（已经订了、还没到货）----------
    def _transit_of(self, it, sid):
        import order_record as REC
        x = ((self.page.calc or {}).get("demand") or {}).get(sid, {}).get(it.get("db_code") or it.get("code"))
        if x and x.get("transit_info"):                 # 计算时已经扣掉的在途
            return x["transit_info"]
        return REC.lookup(self.transit, sid, it.get("ean"), it.get("db_code") or it.get("code"))

    def _transit_boxes(self, it, sid):
        """在途箱数这一格：扣过的显示扣了几箱（建议 − 在途 = 订货）；只显示的显示订单文件里的箱数"""
        x = ((self.page.calc or {}).get("demand") or {}).get(sid, {}).get(it.get("db_code") or it.get("code"))
        if x and x.get("transit_info"):
            return float(S.transit_boxes(x, it.get("pack")))
        t = self._transit_of(it, sid)
        return float(t["colli"]) if t else 0.0

    def _transit_text(self, it, sid):
        n = self._transit_boxes(it, sid)
        return f"{n:g}" if n else ""

    def _style_transit(self, i, r):
        import order_record as REC
        sc, it, sid = self.detail[i]
        tri = self._transit_of(it, sid)
        ti = self.table.item(r, TRANSIT_COL)
        if ti is None:
            return
        tb = self._transit_boxes(it, sid)
        ti.setText(f"{tb:g}" if tb else "")
        ti.setData(KEY_ROLE, tb)
        if tri:
            ti.setForeground(QColor(T("accent")))
            ti.setToolTip(tr("已经订了、还没到货 {0:g} 箱（{1:g} 件）：", tri["colli"], tri["pcs"]) + "\n" + REC.transit_text(tri)
                          + "\n" + tr("双击看订单、标到货"))
        else:
            ti.setToolTip("")

    def _load_transit(self):
        """采购订单跟踪里没到货的单 + RECORD ORDINE 订单文件 → 在途列（后台线程）"""
        import threading
        import order_record as REC
        if (self.page.calc or {}).get("transit") is not None:
            self.transit = self.page.calc["transit"]       # 计算订货量时已经读好（建议量里扣过了）
            return
        if not REC.tracking_folder(self.page.settings):
            return
        self._tr_token += 1
        token, settings = self._tr_token, self.page.settings

        def work():
            try:
                self.transit_ready.emit((token, REC.cached(settings), None))
            except Exception as e:
                self.transit_ready.emit((token, None, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _transit_arrived(self, payload):
        token, res, err = payload
        if token != self._tr_token:
            return
        if err:
            self.page.win.log(tr("⚠ 在途（RECORD ORDINE）读不了：{0}", err))
            return
        self.transit = res
        if self._loading:
            return                                    # 还在分批填：填的时候会用新的
        t = self.table
        was = self._filling
        self._filling = True
        t.setUpdatesEnabled(False)
        for r in range(t.rowCount()):
            it0 = t.item(r, 0)
            i = it0.data(DI_ROLE) if it0 is not None else None
            if i is not None:
                self._style_transit(i, r)
        t.setUpdatesEnabled(True)
        self._filling = was

    def _style_transfer(self, i, r):
        """「调货」格（可以改件数）和跟着调货变的「原因」"""
        t = self.table
        sc, it, sid = self.detail[i]
        key = (sc["path"], sid, it["row"])
        mi = self._moves()[0]
        ov = (self.page.calc.get("transfer_overrides") or {}).get(key)
        ti = t.item(r, TRANSFER_COL)
        m = mi.get(key)
        ro = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if m:
            ti.setText(self._transfer_text(sc, it, sid) + ("  ✎" if m.get("manual") else ""))
            ti.setForeground(QColor(T("warn") if m.get("manual") else T("ok")))
            ti.setToolTip(self._transfer_tip(sc, it, sid) + "\n" + tr("双击修改：换调出门店、改件数或者不调"))
        elif ov is not None and not TR.norm_override(ov)["auto"]:   # 人工改成不调了（或者调出门店不够）
            o = TR.norm_override(ov)
            ti.setText(tr("不调") + "  ✎" if not o["pieces"] else tr("调不了 {0} 件", o["pieces"]))
            ti.setForeground(QColor(T("faint")))
            ti.setToolTip(tr("人工改过：双击重新选调出门店和件数；「恢复建议值」恢复系统算的调货"))
        else:                                   # 没调货的写「不调」
            ordered = bool(self._orig(sc, sid, it["row"]))
            ti.setText(tr("不调"))                 # 没调货的一律写「不调」
            ti.setForeground(QColor(T("faint")))
            ti.setToolTip(tr("双击：这个商品改成从别的门店调货") if ordered else "")
        ti.setFlags(ro)
        ti.setData(KEY_ROLE, self._transfer_key(sc, it, sid))
        ri = t.item(r, REASON_COL)
        ri.setForeground(QColor(T("muted")))
        lines = self._reason_lines(sc, it, sid)
        ri.setText("；".join(lines))
        why = "\n".join(lines)
        ri.setToolTip(why)
        t.item(r, DEC_COL).setToolTip(why)          # 鼠标停在「判断」上也能看为什么
        t.item(r, DEC_COL).setData(Qt.UserRole + 3, why)

    def _open_transit(self, r, c):
        """双击「在途箱数」：这个商品在途的是哪几张单（transit_dialog），到了没标的直接标到货 → 重新扣"""
        if c != TRANSIT_COL or not self.page.calc:
            return
        item = self.table.item(r, 0)
        i = item.data(DI_ROLE) if item is not None else None
        if i is None:
            return
        sc, it, sid = self.detail[i]
        t = self._transit_of(it, sid)
        if not t:
            return
        from transit_dialog import TransitOrdersDialog
        TransitOrdersDialog(self, self.page.settings, self.ui, f"{it['desc']} · {S.STORE_SHEETS.get(sid, sid)}", t,
                            on_change=self.page.reapply_transit,
                            tracking=getattr(self.page.win, "tracking", None)).exec()

    def _edit_transfer(self, r, c):
        """双击「调货」格：选从哪家门店调、调几件（或者不调）。只改这一行，别的行不跟着变"""
        if c != TRANSFER_COL or not self.page.calc:
            return
        item = self.table.item(r, 0)
        i = item.data(DI_ROLE) if item is not None else None
        if i is None:
            return
        sc, it, sid = self.detail[i]
        calc = self.page.calc
        colli = self._orig(sc, sid, it["row"])
        if not colli:
            self.flash(tr("这一行系统没建议订货，不用调货"))
            return
        code = it.get("db_code") or it["code"]
        d = calc["demand"].get(sid, {}).get(code) or {}
        cfg = self.page.transfer_cfg()
        far = set(self.page.transfer_far())
        src = calc.get("base_orig") or calc["base"]
        stores = []
        for s_ in calc.get("stores") or []:
            x = calc["demand"].get(s_, {}).get(code)
            if s_ == sid or not isinstance(x, dict):
                continue
            notes = []
            if s_ in far:
                notes.append(tr("不参与调货"))
            if src.get(sc["path"], {}).get(s_, {}).get(it["row"], 0) > 0:
                notes.append(tr("自己也要订"))
            stores.append({"sid": s_, "name": S.STORE_SHEETS.get(s_, str(s_)), "inv": x.get("inv_raw", x.get("inv")),
                           "daily": x.get("daily"), "surplus": TR.surplus(x, cfg.get("keep_cycles")),
                           "note": "、".join(notes)})
        if not stores:
            self.flash(tr("其他门店没有这个商品的库存数据"))
            return
        key = (sc["path"], sid, it["row"])
        m = self._moves()[0].get(key)
        need = d.get("need", d.get("sugg", 0)) or 0
        from erp_widgets import TransferEditDialog
        dlg = TransferEditDialog(self, it["desc"],
                                 tr("{0} 这次要 {1:g} 件，一箱 {2:g} 件，系统建议订 {3:g} 箱",
                                    S.STORE_SHEETS.get(sid, sid), float(need), it["pack"], colli),
                                 stores, (m["from"], m["pieces"]) if m else None, need, it["pack"], colli)
        if not dlg.exec() or dlg.value is None:
            return
        frm, pcs = dlg.value
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.page.set_transfer(key, {"from": frm, "pieces": pcs} if pcs else 0)
            self.refresh_transfers(rows={(sc["path"], it["row"])})
        finally:
            QApplication.restoreOverrideCursor()
        m = self._moves()[0].get(key)
        if pcs and not m:
            self.flash(tr("{0} 库存不够，这次调不了", S.STORE_SHEETS.get(frm, frm)))
        elif m:
            self.flash(tr("已改调货：{0} ← {1} {2} 件", S.STORE_SHEETS.get(sid, sid), S.STORE_SHEETS.get(m["from"], m["from"]),
                          m["pieces"]))
        else:
            self.flash(tr("已改调货：{0} {1}", S.STORE_SHEETS.get(sid, sid), tr("不调")))

    def refresh_transfers(self, rows=None):
        """调货重算以后：调货、原因、订货箱数刷新。rows = {(SCHEDA, 行号)}：只刷新这几个商品（各门店）的行，快"""
        if not self.detail or self._loading:
            return
        was = self._filling
        self._filling = True
        self.table.setUpdatesEnabled(False)
        for i in range(len(self.detail)):
            if rows is not None and (self.detail[i][0]["path"], self.detail[i][1]["row"]) not in rows:
                continue
            self._style_transfer(i, self.pos[i])
            self._style_transit(i, self.pos[i])
            self._show_qty(i)
        self.table.setUpdatesEnabled(True)
        self._filling = was
        self._filter()
        if self.cur_di is not None:
            self._select_row(self.pos[self.cur_di])

    # ---------- 调货 / 变化 ----------
    def _sugg(self, sc, sid, row):
        """「建议（箱）」列：系统建议的箱数，没扣在途（订货箱数 = 建议 − 在途箱数）"""
        calc = self.page.calc
        src = calc.get("base_sugg")
        if src is None:
            return self._orig(sc, sid, row)
        return src.get(sc["path"], {}).get(sid, {}).get(row, 0)

    def _orig(self, sc, sid, row):
        """系统建议的箱数（调货之前）"""
        calc = self.page.calc
        src = calc.get("base_orig") or calc["base"]
        return src.get(sc["path"], {}).get(sid, {}).get(row, 0)

    def _moves(self):
        """{(SCHEDA, 门店, 行): 调进来的}、{(SCHEDA, 门店, 行): [调出去的]}（按 calc["transfers"] 建一次，调货重算后自动换）"""
        ts = self.page.calc.get("transfers") or []
        if getattr(self, "_mv_src", None) is not ts:
            mi, mo = {}, {}
            for t in ts:
                mi[(t["path"], t["to"], t["row"])] = t
                mo.setdefault((t["path"], t["from"], t["row"]), []).append(t)
            self._mv_src, self._mv = ts, (mi, mo)
        return self._mv

    def _transfer_text(self, sc, it, sid):
        mi, mo = self._moves()
        t = mi.get((sc["path"], sid, it["row"]))
        if t:                                     # 短：← 从哪家 几件（详细的在鼠标提示里）
            return f"← {S.STORE_SHEETS.get(t['from'], t['from'])} {t['pieces']}" + tr("件")
        return ""                                 # 调出去的不显示（审核只要知道从哪调进来）

    def _reason_lines(self, sc, it, sid):
        """为什么：直白的几句话（「原因」列、鼠标提示、右边详情栏共用）"""
        calc = self.page.calc
        code = it.get("db_code") or it["code"]
        x = calc["demand"].get(sid, {}).get(code)
        if not x and it.get("dup_of"):
            first = next((y for y in sc["items"] if y["row"] == it["dup_of"]), None)
            return [tr("和「{0}」是同一个商品编号，销量和建议量只算在那一行；要订这一行（例如指定颜色）自己填箱数",
                       (first or {}).get("desc", "")[:30])]
        if not x:
            return [tr("数据库里找不到这个商品，没有建议量；需要的话手动填箱数")]
        tin = None
        t = self._moves()[0].get((sc["path"], sid, it["row"]))
        if t:
            dx = calc["demand"].get(t["from"], {}).get(code)
            have = max(0.0, dx.get("inv_raw", dx["inv"])) if dx else 0
            tin = {"name": S.STORE_SHEETS.get(t["from"], t["from"]), "pieces": t["pieces"], "inv": have,
                   "days": S.cover_days({"inv": have, "daily": dx.get("daily")}) if dx else None,
                   "saved": t["colli_before"] - t["colli_after"]}
        lines = R.plain_reason(x.get("inv_raw", x.get("inv")), x.get("daily"), x.get("cycle"), x.get("sugg"),
                               boxes=self._sugg(sc, sid, it["row"]) or None, target=x.get("target"), transfer=tin,
                               pack=it.get("pack"))
        tb = S.transit_boxes(x, it.get("pack"))
        if tb:
            lines.append(tr("在途 {0:g} 箱还没到货：订货箱数 = 建议 {1:g} − 在途 {0:g} = {2:g} 箱", tb,
                            self._sugg(sc, sid, it["row"]), self._orig(sc, sid, it["row"])))
        return lines

    def _marks(self, sc, it, sid, n=None):
        """判断后面的提醒（不改订多少）：新算法订了、一箱到货后要卖半年以上 → 慢销；库存是负数 → 库存不准"""
        x = self.page.calc["demand"].get(sid, {}).get(it.get("db_code") or it["code"])
        if not x:
            return []
        if n is None:
            n = self._cur(sc, sid, it["row"])
        mi, mo = self._moves()
        k_ = (sc["path"], sid, it["row"])
        moved = (mi[k_]["pieces"] if k_ in mi else 0) - sum(t_["pieces"] for t_ in mo.get(k_, []))
        v = S.cover_days(x, n * it["pack"] + moved + float(x.get("transit") or 0))
        return R.review_marks(x.get("target") is not None, n > 0, v, x.get("inv_raw", x.get("inv")))

    def _transfer_tip(self, sc, it, sid):
        mi, mo = self._moves()
        t = mi.get((sc["path"], sid, it["row"]))
        if t:
            return tr("调进 {0} 件 ← {1}，少订 {2} 箱", t["pieces"], S.STORE_SHEETS.get(t["from"], t["from"]),
                      t["colli_before"] - t["colli_after"])
        return ""

    def _transfer_key(self, sc, it, sid):
        mi, mo = self._moves()
        t = mi.get((sc["path"], sid, it["row"]))
        return float(t["pieces"]) if t else 0.0          # 只算调进来的

    def _change(self, sc, sid, row, n):
        """订货箱数比系统建议多 / 少几箱，为什么：(文字, 数字)"""
        orig = self._orig(sc, sid, row)
        after_tr = self.page.calc["base"].get(sc["path"], {}).get(sid, {}).get(row, 0)
        d = n - orig
        if not d:
            return "", 0.0, ""
        parts = []
        if after_tr != orig:
            parts.append(tr("调货 {0:+g}", after_tr - orig))
        if n != after_tr:
            parts.append(tr("手动 {0:+g}", n - after_tr))
        kind = "transfer" if n == after_tr else "manual"
        return f"{d:+g}", float(d), "，".join(parts), kind

    def _verdict(self, sc, it, sid, n=None):
        """(类别, 文字)：这一行「判断」显示什么"""
        x = self.page.calc["demand"].get(sid, {}).get(it.get("db_code") or it["code"])
        if n is None:
            n = self._cur(sc, sid, it["row"])
        return R.review_verdict(self._orig(sc, sid, it["row"]), n, bool(self._transfer_key(sc, it, sid)), in_db=bool(x))

    def _k0(self, i):
        sc, it, sid = self.detail[i]
        return (self.sup_index[sc["path"]], it["row"], sid)

    def _more_menu(self, btn):
        m = QMenu(self)
        a_cols = m.addAction(tr("显示哪些列…"))
        a_exp = m.addAction(tr("导出 Excel"))
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        got = m.exec(pos)
        if got == a_cols:
            self.cols.menu(pos)
        elif got == a_exp:
            self.export()

    @staticmethod
    def _extra_vals(x, sug_c, days):
        """订单文件附加列里明细表还没有的那几项：月均、需求、GIA.0~5、GIA-period、period_sales"""
        v = S.extra_fields(x, sug_c, days)
        return None if v is None else [v[5], v[8]] + v[10:16] + [v[16], v[17]]

    def _extra_texts(self, x, sug_c, days):
        v = self._extra_vals(x, sug_c, days)
        return [f"{n:g}" for n in v] if v else [""] * len(_EXTRA)

    def _extra_keys(self, x, sug_c, days):
        v = self._extra_vals(x, sug_c, days)
        return [float(n) for n in v] if v else [None] * len(_EXTRA)

    def _sort_keys(self, i, x, sug_c):
        sc, it, sid = self.detail[i]
        k0 = (self.sup_index[sc["path"]], it["row"], sid)        # 默认顺序：供应商 → SCHEDA 行 → 门店
        inv_days = S.cover_days(x) if x else None
        if isinstance(inv_days, (int, float)) and math.isinf(inv_days):
            inv_days = 1e12
        num = lambda v: float(v) if isinstance(v, (int, float)) else None
        return [k0, (sid,) + k0, (_dec_rank(x["decision"]) if x else 9, -(x["sugg"] if x else 0)) + k0,
                S.item_code(it), it["ean"], it["desc"].lower(),
                num(x.get("inv_raw", x["inv"])) if x else None, *([num(v) for v in x["sales"]] if x else [None] * 4),
                num(inv_days), float(it["pack"]), float(it["price"]),
                float(x["sugg"]) if x else None, float(self._sugg(sc, sid, it["row"])), 0.0, 0.0,
                (_TIER_RANK.get(x["tier"], 9),) + k0 if x else (9,) + k0,
                float(x["cycle"]) if x and isinstance(x.get("cycle"), (int, float)) else None,
                float(x["daily"]) if x and x.get("daily") else None,
                float(x["target"]) if x and isinstance(x.get("target"), (int, float)) else None]

    def _sort_by(self, col):
        if not self.detail or self._loading:
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
        # 订后够卖：随订货箱数一起变
        x = self.page.calc["demand"].get(sid, {}).get(it.get("db_code") or it["code"])
        mi, mo = self._moves()
        k_ = (sc["path"], sid, it["row"])
        moved = (mi[k_]["pieces"] if k_ in mi else 0) - sum(t_["pieces"] for t_ in mo.get(k_, []))
        v = S.cover_days(x, n * it["pack"] + moved + float((x or {}).get("transit") or 0))     # 在途到了也算
        af = self.table.item(r, AFTER_COL)
        af.setText(S.days_text(v))
        af.setData(KEY_ROLE, None if v is None else (1e12 if math.isinf(v) else v))
        limit = float(self.page.fill_cfg().get("max_stock_days") or 60)
        arrive = n or moved > 0
        af.setForeground(QColor(T("warn") if (arrive and v is not None and v > limit) else T("text") if arrive else T("muted")))
        # 判断：和原建议比，不订 / 照建议订 / 多订 / 少订 / 建议调货
        x_ = self.page.calc["demand"].get(sid, {}).get(it.get("db_code") or it["code"])
        orig = self._orig(sc, sid, it["row"])
        kind, vt = self._verdict(sc, it, sid, n)
        marks = self._marks(sc, it, sid, n)
        dc = self.table.item(r, DEC_COL)
        dc.setText(" · ".join([vt] + [m[1] for m in marks]))
        base = dc.data(Qt.UserRole + 3) or ""
        dc.setToolTip("\n".join([base] + ["⚠ " + m[2] for m in marks]) if marks else base)
        dc.setData(KEY_ROLE, (R.VERDICT_RANK.get(kind, 9), -abs(n - orig)) + self._k0(i))
        urgent = bool(x_ and "🔴" in (x_["decision"] or "")) and n > 0 and kind in ("same", "more")
        dc.setForeground(QColor(T("err") if urgent else T("warn") if (marks and kind in ("same", "more"))
                                else T(VERDICT_TOKEN.get(kind, "text"))))
        f = dc.font()
        f.setBold(kind in ("transfer", "more", "less"))
        dc.setFont(f)
        oc = self.table.item(r, _col(15))
        sug = self._sugg(sc, sid, it["row"])            # 建议（箱）：没扣在途（订货箱数 = 建议 − 在途）
        oc.setText(f"{sug:g}" if sug else ("0" if n else ""))
        oc.setData(KEY_ROLE, float(sug))
        many = self._many(sug)
        oc.setForeground(QColor(T("warn") if many else T("muted") if orig == n else T("text")))
        oc.setBackground(QColor(T("warn_soft")) if many else QColor(0, 0, 0, 0))
        f = oc.font()
        f.setBold(many)
        oc.setFont(f)
        oc.setToolTip(tr("建议 {0:g} 箱，超过 {1:g} 箱：请看一眼要不要订这么多（只提醒，没有自动减少）",
                         sug, self._warn_n) if many else "")
        ch = self.table.item(r, CHANGE_COL)
        res = self._change(sc, sid, it["row"], n)
        txt, d = res[0], res[1]
        ch.setText(txt)
        ch.setData(KEY_ROLE, d)
        # 绿 = 调货少订的；蓝 / 橙 = 手动多订 / 少订
        ch.setForeground(QColor(T("ok") if (d and res[3] == "transfer") else T("accent") if d > 0
                                else T("warn") if d < 0 else T("faint")))
        ch.setToolTip(res[2])
        f = ch.font()
        f.setBold(bool(d))
        ch.setFont(f)
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
            self.flash(tr("箱数要填 0 或正整数"))
            self._show_qty(i)
            return
        self.page.set_qty(sc, sid, it["row"], n)
        self._show_qty(i)
        self._update_status()
        if self.cur_di == i:
            self._select_row(self.pos[i])

    def flash(self, text):
        """提示显示在这个窗口的状态栏（主窗口底部的提示会被这个窗口挡住）"""
        self.status_r.setText(text)
        self.status_r.setStyleSheet(f"color:{T('accent')}; font-weight:600;")
        if not hasattr(self, "_flash_timer"):
            self._flash_timer = QTimer(self)
            self._flash_timer.setSingleShot(True)
            self._flash_timer.timeout.connect(lambda: (
                self.status_r.setText(""),
                self.status_r.setStyleSheet("")))
        self._flash_timer.start(6000)

    def add_items(self):
        if not self.page.calc:
            return
        btn = self.store_group.checkedButton()
        sid = btn.property("sid") if btn else ALL
        path = self.f_sup.currentData()
        if (path in (None, ALL) or sid in (None, ALL)) and self.cur_di is not None:
            sc, _it, s2 = self.detail[self.cur_di]      # 没选具体的供应商 / 门店：用选中的那一行
            path = sc["path"] if path in (None, ALL) else path
            sid = s2 if sid in (None, ALL) else sid
        dlg = AddItemsDialog(self, path, sid)
        if dlg.exec():
            self.refresh_rows()
            n = sum(dlg.adds.values())
            self.flash(tr("已加入 {0} 箱（标成手动改过，可以「恢复建议值」）", n))

    def refresh_rows(self):
        """主窗口那边改了数（比如恢复建议值）→ 刷新显示"""
        if not self.detail or self._loading:
            return                                # 还在填表：填到的时候会用最新的箱数
        for i in range(len(self.detail)):
            self._show_qty(i)
        self._filter()

    def reset_shown(self):
        if not self.page.calc:
            return
        if self._loading:
            self.flash(tr("还在载入，稍等一下"))
            return
        n = 0
        ov = self.page.calc.get("transfer_overrides") or {}
        undo = [(sc["path"], sid, it["row"]) for i, (sc, it, sid) in enumerate(self.detail)
                if not self.table.isRowHidden(self.pos[i]) and (sc["path"], sid, it["row"]) in ov
                and not TR.norm_override(ov[(sc["path"], sid, it["row"])])["auto"]]
        if undo:                                    # 人工改过的调货也恢复成系统建议的
            self.page.reset_transfers(undo)
            self.refresh_transfers()
            n += len(undo)
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
        self.flash(tr("已恢复 {0} 行", n))

    def set_filter(self, path=ALL, sid=ALL):
        if self._loading:
            self._pending_filter = (path, sid)     # 填完表再筛选
            return
        self.f_sup.setCurrentIndex(max(0, self.f_sup.findData(path)))
        for b in self.store_group.buttons():
            if b.property("sid") == sid:
                b.setChecked(True)
        self._filter()

    def _filter(self, *_):
        if not self.detail or self._loading:
            return
        sup = self.f_sup.currentData()
        btn = self.store_group.checkedButton()
        store = btn.property("sid") if btn else ALL
        view = self.f_view.currentData()
        text = self.f_text.text().strip().lower()
        calc = self.page.calc
        shown = 0
        self.table.setUpdatesEnabled(False)
        base = {}
        hf = self.hf if self.hf.active() else None
        for i, (sc, it, sid) in enumerate(self.detail):
            key = (sc["path"], sid, it["row"])
            hide = (sup not in (None, ALL) and sc["path"] != sup) or (store not in (None, ALL) and sid != store)
            if not hide and view == "order":   # 要订的、改过的、系统建议过的（全部靠调货的也在）
                hide = self._cur(sc, sid, it["row"]) <= 0 and key not in self.page.edited and \
                    self._orig(sc, sid, it["row"]) <= 0 and not self._transfer_key(sc, it, sid)
            elif not hide and view == "changed":   # 判断是多订 / 少订 / 建议调货的
                hide = self._verdict(sc, it, sid)[0] not in ("transfer", "more", "less")
            elif not hide and view == "transfer":
                hide = not self._transfer_key(sc, it, sid)
            elif not hide and view == "edited":
                hide = key not in self.page.edited
            elif not hide and view == "many":
                hide = not self._many(self._sugg(sc, sid, it["row"]))
            elif not hide and view == "urgent":
                x = calc["demand"].get(sid, {}).get(it.get("db_code") or it["code"])
                hide = not (x and "🔴" in (x["decision"] or ""))
            if not hide and text:
                hide = text not in f"{S.item_code(it)} {it['ean']} {it['desc']}".lower()
            r = self.pos[i]
            base[r] = hide
            if not hide and hf is not None:
                hide = not hf.accepts(r)                # 右键表头的列筛选
            self.table.setRowHidden(r, hide)
            shown += not hide
        self._base_hide = base
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
        if self._loading:
            self.flash(tr("还在载入，稍等一下"))
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
        cols = [c for c in self._visual_cols() if not self.table.isColumnHidden(c)]   # 按现在看到的列导出
        heads = [self.table.horizontalHeaderItem(c).text().replace(" ✎", "").replace(" ⏷", "") for c in cols]
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
            self.flash(tr("已导出"))
        except Exception as e:
            QMessageBox.critical(self, tr("导出"), tr("导出失败：{0}", e))
            return
        from erp_widgets import open_file
        open_file(self, path, tr("导出 Excel"))
