"""
工作台（首页）：今天要订哪些供应商、门店配货做到哪一步、今天的采购金额、各门店情况、最近的比价记录。
门店颜色和起订量检查一样：绿 = 直送 · 橙 = BMS · 灰 = 这次不订。
"""
import threading
from datetime import datetime
import os

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)

from i18n import tr
from theme import T, num_font
from erp_widgets import StepBar, kpi_card, RowSorter, SORT_ROLE, DotsDelegate, DOTS_ROLE
import store_orders as S
import history

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# 今日供应商的状态：(文字, 颜色, 排序)
STATES = {"done": ("已配货", "ok", 0), "calc": ("已计算", "accent", 1), "skip": ("这次不订", "muted", 3),
          "off": ("不配货", "faint", 4), "todo": ("待计算", "muted", 2)}


class _Sig(QObject):
    moq_loaded = Signal()


def _panel(title=None, tip=None):
    f = QFrame()
    f.setObjectName("Panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 12, 16, 14)
    v.setSpacing(8)
    head = None
    if title:
        head = QHBoxLayout()
        t = QLabel(title + ("  ⓘ" if tip else ""))
        t.setObjectName("Section")
        if tip:
            t.setToolTip(tip)
        head.addWidget(t, 1)
        v.addLayout(head)
        f.title = t
    return f, v, head


def _link(text, fn):
    b = QPushButton(text)
    b.setObjectName("Link")
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(fn)
    return b


class DashboardPage:
    def __init__(self, win, settings):
        self.win, self.settings = win, settings
        self.sig = _Sig()
        self.sig.moq_loaded.connect(self.refresh)
        self._moq_loading = False

    # =================================================================
    # 界面
    # =================================================================
    def build(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(16)

        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(4)
        self.date_lbl = QLabel("")
        self.date_lbl.setObjectName("Faint")
        self.hello = QLabel("")
        self.hello.setObjectName("PageTitle")
        col.addWidget(self.date_lbl)
        col.addWidget(self.hello)
        head.addLayout(col, 1)
        b1 = QPushButton(tr("批发商比价"))
        b1.clicked.connect(lambda: self.win.go_page("order"))
        self.btn_start = QPushButton(tr("开始今日采购"))
        self.btn_start.setObjectName("Primary")
        self.btn_start.clicked.connect(self.start_today)
        head.addWidget(b1, 0, Qt.AlignBottom)
        head.addWidget(self.btn_start, 0, Qt.AlignBottom)
        lay.addLayout(head)

        g = QGridLayout()
        g.setSpacing(14)
        c1, self.k_sup, self.k_sup_sub = kpi_card(tr("今日供应商"), "—", " ")
        c2, self.k_amt, self.k_amt_sub = kpi_card(tr("今日采购金额"), "—", " ")
        c3, self.k_wait, self.k_wait_sub = kpi_card(tr("这次不订"), "—", " ")
        c4, self.k_cmp, self.k_cmp_sub = kpi_card(tr("最近一次比价"), "—", " ")
        c3.setToolTip(tr("单独不够起订量、也没并进 BMS 的门店单；紧急缺货多的可以在起订量检查里「BMS 调整」并进 BMS"))
        for i, c in enumerate((c1, c2, c3, c4)):
            g.addWidget(c, 0, i)
        lay.addLayout(g)

        p, pv, ph = _panel(tr("门店配货进度"))
        ph.addWidget(_link(tr("进入门店配货 →"), lambda: self.win.go_page("store_orders")))
        self.steps = StepBar([(tr("读取供应商清单"), ""), (tr("计算订货量"), ""), (tr("核对明细"), ""),
                              (tr("配货与凑单"), ""), (tr("生成订单"), "")])
        pv.addWidget(self.steps)
        lay.addWidget(p)

        row = QHBoxLayout()
        row.setSpacing(16)
        left, lv, lh = _panel(tr("今日供应商"), tr("每家供应商今天的状态和各门店去向；双击一行进入门店配货"))
        self.sup_title = left.title
        self.legend = []
        for kind, text in (("ok", tr("直送")), ("near", "BMS"), ("far", tr("这次不订"))):
            dot = QLabel()
            dot.setFixedSize(10, 10)
            lab = QLabel(text)
            lab.setObjectName("Faint")
            lh.addWidget(dot)
            lh.addWidget(lab)
            lh.addSpacing(6)
            self.legend.append((dot, kind))
        heads = [tr("供应商"), tr("起订量"), tr("各店") + "  " + " · ".join(str(k) for k in S.STORE_SHEETS), tr("状态"), tr("金额")]
        self.sup_table = QTableWidget(0, len(heads))
        self.sup_table.setHorizontalHeaderLabels(heads)
        self.sup_table.horizontalHeaderItem(2).setToolTip(
            " · ".join(f"{k} {n}" for k, n in S.STORE_SHEETS.items()))
        self.sup_table.verticalHeader().setVisible(False)
        self.sup_table.setShowGrid(False)
        self.sup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sup_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sup_table.setFocusPolicy(Qt.NoFocus)
        self.sup_table.verticalHeader().setDefaultSectionSize(36)
        self.sup_table.setItemDelegateForColumn(2, DotsDelegate(self.sup_table))
        hh = self.sup_table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        self.sup_table.setMinimumHeight(360)
        self.sup_table.cellDoubleClicked.connect(lambda *_: self.start_today())
        self.sup_sorter = RowSorter(self.sup_table)
        lv.addWidget(self.sup_table)
        row.addWidget(left, 3)

        rcol = QVBoxLayout()
        rcol.setSpacing(16)
        sp, sv, _ = _panel(tr("门店"), tr("今天已配货的金额：直送 / 并进 BMS / 这次不订。参与 BMS 凑单的门店在「配货设置」里改"))
        self.store_rows = {}
        for sid, name in list(S.STORE_SHEETS.items()) + [(0, "BMS")]:
            fr = QFrame()
            fr.setObjectName("MiniCard")
            h = QHBoxLayout(fr)
            h.setContentsMargins(10, 6, 10, 6)
            h.setSpacing(10)
            badge = QLabel(str(sid))
            badge.setFixedSize(26, 26)
            badge.setAlignment(Qt.AlignCenter)
            badge.setFont(num_font(10, True))
            c = QVBoxLayout()
            c.setSpacing(0)
            n = QLabel(name if sid else tr("BMS 总仓"))
            n.setStyleSheet("font-weight:600;")
            note = QLabel("")
            note.setObjectName("Faint")
            c.addWidget(n)
            c.addWidget(note)
            amt = QLabel("")
            amt.setFont(num_font(10, True))
            amt.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            h.addWidget(badge)
            h.addLayout(c, 1)
            h.addWidget(amt)
            sv.addWidget(fr)
            self.store_rows[sid] = (badge, note, amt)
        rcol.addWidget(sp)

        rp, self.recent, rh = _panel(tr("最近的比价"))
        self.recent.setSpacing(0)
        rh.addWidget(_link(tr("全部 →"), lambda: self.win.go_page("history")))
        rcol.addWidget(rp, 1)
        row.addLayout(rcol, 2)
        lay.addLayout(row, 1)
        self.restyle()                   # 套颜色并填数据
        return w

    # =================================================================
    # 数据
    # =================================================================
    def _today_folder(self):
        wd = datetime.now().weekday()
        return S.DAY_FOLDERS[wd] if wd < 5 else None

    def start_today(self):
        so = self.win.store_orders
        day = self._today_folder()
        if day and hasattr(so, "day") and not getattr(so, "calc", None):
            i = so.day.findData(day)
            if i >= 0:
                so.day.setCurrentIndex(i)
        self.win.go_page("store_orders")

    def set_db(self, ok):
        """数据库状态在左下角显示，工作台不再单独占一张卡"""
        self.db_ok = ok

    def _load_moq(self, paths):
        """今天的供应商起订量还没读过（门店配货页选的是别的星期）：后台读，读完刷新"""
        so = self.win.store_orders
        if self._moq_loading or not hasattr(so, "moq"):
            return
        self._moq_loading = True

        def work():
            for p in paths:
                try:
                    so.moq[p] = S.parse_moq(p)
                except Exception as e:
                    so.moq[p] = dict(S.read_moq(None), raw=tr("读不出来：{0}", e))
            self._moq_loading = False
            self.sig.moq_loaded.emit()

        threading.Thread(target=work, daemon=True).start()

    def _supplier_rows(self, files):
        """今天每家供应商：(名字, 起订量, 状态, 各店颜色, 各店说明, 金额, SCHEDA, 计划, 来源 res / calc / None)"""
        so = self.win.store_orders
        calc = getattr(so, "calc", None)
        res = getattr(so, "result", None)
        res_by = {sc["path"]: (sc, plan) for _, sc, plan in (res or {}).get("results", [])}
        sc_by = {sc["path"]: sc for sc in (calc or {}).get("schede", [])}
        plans = getattr(so, "_plans", {}) or {}
        alloc_on = getattr(so, "alloc_on", set())
        colors = {"ok": T("ok"), "fill": T("ok"), "bms_ok": T("near"), "short": T("far"), "none": T("track")}
        names = {"ok": tr("直送"), "fill": tr("直送"), "bms_ok": "BMS", "short": tr("这次不订")}
        out = []
        for _, p in files:
            moq = S.moq_label(so.moq_of(p), short=True) if hasattr(so, "moq_of") and so.moq_of(p) else "—"
            sc, plan, state = None, None, "todo"
            if p in res_by:
                sc, plan = res_by[p]
                state = "done"
            elif p in sc_by:
                sc, plan = sc_by[p], plans.get(p)
                state = "calc" if p in alloc_on else "off"
            dots, tips = [], []
            amount = None
            if plan:
                for sid in S.STORE_SHEETS:
                    st = so._plan_state(plan, sid)
                    dots.append(colors[st])
                    if st in names:
                        tips.append(f"{S.STORE_SHEETS[sid]}：{names[st]}")
                amount = S.plan_totals([("", sc, plan)])["total"]
                if state in ("done", "calc") and not amount:
                    state = "skip"
            src = "res" if p in res_by else "calc" if p in sc_by else None
            out.append((S.supplier_short_name(p), moq, state, dots, "\n".join(tips), amount, sc, plan, src))
        return out

    def refresh(self):
        now = datetime.now()
        wd = now.weekday()
        self.date_lbl.setText(tr("{0} · {1:%Y-%m-%d}", tr(WEEKDAYS[wd]), now))
        so = self.win.store_orders
        day = self._today_folder()
        root = (self.settings.get("store_orders") or {}).get("folder") or ""
        files = S.list_schede(root, day) if day and root and os.path.isdir(root) else []
        skip = set((self.settings.get("store_orders") or {}).get("skip") or [])
        files = [(d, p) for d, p in files if S.supplier_short_name(p) not in skip]
        if not day:
            self.hello.setText(tr("今天周末，没有要下单的供应商"))
        elif not root or not os.path.isdir(root):
            self.hello.setText(tr("先在系统设置里选好采购配单文件夹"))
        else:
            self.hello.setText(tr("今天有 {0} 家供应商要下单", len(files)))
        self.win.set_badge("store_orders", len(files) if files else "")
        todo_moq = [p for _, p in files if p not in getattr(so, "moq", {})]
        if todo_moq:
            self._load_moq(todo_moq)

        rows = self._supplier_rows(files)
        n_done = sum(1 for r in rows if r[8] == "res")
        n_calc = sum(1 for r in rows if r[8] == "calc")
        n_todo = sum(1 for r in rows if r[8] is None)
        has_res = n_done > 0
        self.btn_start.setText(tr("查看配货结果") if has_res else tr("继续门店配货") if n_calc else tr("开始今日采购"))
        self.btn_start.setEnabled(bool(day))

        # 卡片
        self.k_sup.setText(str(len(files)) if day else "—")
        self.k_sup_sub.setText(tr("{0} 家已配货 · {1} 家已计算 · {2} 家待计算", n_done, n_calc, n_todo)
                               if files else tr("周一到周五按文件夹"))
        direct = bms = wait_amt = 0.0
        n_wait = n_urg = 0
        per_store = {sid: [0.0, 0.0, 0.0] for sid in list(S.STORE_SHEETS) + [0]}
        for name, moq, state, dots, tips, amount, sc, plan, src in rows:
            if not plan or state == "off":
                continue
            for sid, st in plan["stores"].items():
                if st["status"] == "none":
                    continue
                k = so._plan_state(plan, sid)
                if k in ("ok", "fill"):
                    direct += st["amount"]
                    per_store[sid][0] += st["amount"]
                elif k == "bms_ok":
                    per_store[sid][1] += st["amount"]
                else:
                    n_wait += 1
                    wait_amt += st["amount"]
                    per_store[sid][2] += st["amount"]
                    n_urg += so._urgent_of(sc, plan, sid)[0] >= 0.5
            b = plan.get("bms")
            if S.bms_ordered(b):
                bms += b["amount"]
                per_store[0][1] += b["amount"]
        total = direct + bms
        any_plan = any(r[7] for r in rows)
        self.k_amt.setText(f"€{total:,.2f}" if any_plan else "—")
        self.k_amt.setStyleSheet(f"color:{T('accent')};" if any_plan else "")
        self.k_amt_sub.setText(tr("直送 €{0:,.0f} · BMS €{1:,.0f}", direct, bms) + ("" if has_res else tr("（估算）"))
                               if any_plan else tr("还没有计算"))
        self.k_wait.setText(tr("{0} 张单", n_wait) if any_plan else "—")
        self.k_wait.setStyleSheet(f"color:{T('err')};" if n_urg else "")
        self.k_wait_sub.setText((tr("€{0:,.0f} · {1} 张紧急缺货多", wait_amt, n_urg) if n_urg else f"€{wait_amt:,.0f}")
                                if any_plan else " ")
        runs = history.list_runs()
        if runs:
            r0 = runs[0]
            self.k_cmp.setText(f"€{r0.get('final_cost', 0):,.2f}")
            self.k_cmp_sub.setText(tr("{0} · {1} 家供应商", r0.get("time", "")[:16], r0.get("suppliers", 0)))
        else:
            self.k_cmp.setText("—")
            self.k_cmp_sub.setText(tr("还没有比价记录"))

        # 进度（只看今天的供应商）
        st = self.steps
        n_files = len(S.written_files((getattr(so, "result", None) or {}).get("written", []))) if has_res else 0
        if not files:
            st.set_current(0)
            st.set_state(0, "now", tr("没有今天的文件"))
            for i in range(1, 5):
                st.set_state(i, "todo", "")
        else:
            st.set_state(0, "done", tr("{0} 个 SCHEDA", len(files)))
            calc_done = n_todo < len(files)
            ed = len(getattr(so, "edited", set()))
            if has_res:
                st.set_state(1, "done", tr("{0} 家供应商", len(files) - n_todo))
                st.set_state(2, "done", tr("改了 {0} 处", ed))
                st.set_state(3, "done", tr("直送 / BMS"))
                st.set_state(4, "done", tr("{0} 个文件", n_files))
            elif calc_done:
                st.set_state(1, "done", tr("{0} 家供应商", len(files) - n_todo))
                st.set_state(2, "now", tr("改了 {0} 处", ed))
                st.set_state(3, "todo", tr("直送 / BMS"))
                st.set_state(4, "todo", tr("每家供应商一个文件"))
            else:
                st.set_state(1, "now", tr("点「开始今日采购」"))
                st.set_state(2, "todo", "")
                st.set_state(3, "todo", tr("直送 / BMS"))
                st.set_state(4, "todo", tr("每家供应商一个文件"))

        # 今日供应商
        t = self.sup_table
        t.setRowCount(len(rows))
        for i, (name, moq, state, dots, tips, amount, sc, plan, src) in enumerate(rows):
            text, kind, rank = STATES[state]
            vals = [name, moq, "", tr(text), f"€{amount:,.2f}" if amount else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    f = it.font()
                    f.setBold(state in ("done", "calc"))
                    it.setFont(f)
                    if state in ("skip", "off"):
                        it.setForeground(QColor(T("muted")))
                if c == 1:
                    it.setForeground(QColor(T("muted")))
                if c == 2:
                    it.setData(DOTS_ROLE, dots)
                    it.setToolTip(tips)
                    it.setData(SORT_ROLE, sum(1 for d in dots if d == T("ok")) * 10 + sum(1 for d in dots if d == T("near")))
                if c == 3:
                    it.setData(SORT_ROLE, rank)
                    it.setForeground(QColor(T(kind)))
                    f = it.font()
                    f.setBold(state in ("done", "calc"))
                    it.setFont(f)
                if c == 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    it.setFont(num_font(10, True))
                    it.setData(SORT_ROLE, float(amount or 0))
                t.setItem(i, c, it)
        self.sup_sorter.apply()
        self.sup_title.setText(tr("今日供应商（{0}）", tr(day)) + "  ⓘ" if day else tr("今日供应商") + "  ⓘ")

        # 门店
        fill = dict(S.DEFAULT_FILL, **((self.settings.get("store_orders") or {}).get("fill") or {}))
        far = set(fill.get("far_stores") or [])
        for sid, (badge, note, amt) in self.store_rows.items():
            d, b, w_ = per_store[sid]
            if sid == 0:
                note.setText(tr("合并订货后分货"))
                amt.setText(f"€{b:,.0f}" if b else "—")
                amt.setStyleSheet(f"color:{T('near') if b else T('faint')};")
                continue
            note.setText(tr("不参与 BMS 凑单") if sid in far else tr("参与 BMS 凑单"))
            parts = []
            if d:
                parts.append(f"<span style='color:{T('ok')}'>€{d:,.0f}</span>")
            if b:
                parts.append(f"<span style='color:{T('near')}'>€{b:,.0f}</span>")
            if w_:
                parts.append(f"<span style='color:{T('faint')}'>€{w_:,.0f}</span>")
            amt.setText(" · ".join(parts) if parts else f"<span style='color:{T('faint')}'>—</span>")
            amt.setToolTip(tr("直送 €{0:,.2f}\nBMS €{1:,.2f}\n这次不订 €{2:,.2f}", d, b, w_))

        # 最近的比价
        while self.recent.count() > 1:
            it = self.recent.takeAt(1)
            if it.widget():
                it.widget().deleteLater()
        if not runs:
            l = QLabel(tr("还没有记录"))
            l.setObjectName("Faint")
            self.recent.addWidget(l)
        for r in runs[:5]:
            fr = QFrame()
            fr.setStyleSheet(f"QFrame{{border-top:1px solid {T('line')};}} QLabel{{border:none;}}")
            h = QHBoxLayout(fr)
            h.setContentsMargins(0, 7, 0, 7)
            a = QLabel(r.get("time", "")[5:16])
            a.setFont(num_font(9))
            a.setObjectName("Faint")
            b = QLabel(_short_file(r.get("main_file") or ""))
            b.setToolTip(r.get("main_file") or "")
            c = QLabel(f"€{r.get('final_cost', 0):,.0f}")
            c.setFont(num_font(10, True))
            h.addWidget(a)
            h.addSpacing(10)
            h.addWidget(b, 1)
            h.addWidget(c)
            self.recent.addWidget(fr)
        self.recent.addStretch()

    def restyle(self):
        for badge, note, amt in getattr(self, "store_rows", {}).values():
            badge.setStyleSheet(f"background:{T('accent_soft')}; color:{T('accent')}; border-radius:7px;")
        for dot, kind in getattr(self, "legend", []):
            dot.setStyleSheet(f"background:{T(kind)}; border-radius:3px;")
        if hasattr(self, "steps"):
            self.steps.restyle()
        if hasattr(self, "sup_table"):
            self.refresh()


def _short_file(name):
    """确认订单_MARLIA_20261021_1418.xlsx → MARLIA"""
    base = os.path.splitext(name)[0]
    parts = base.split("_")
    return parts[1] if len(parts) >= 3 and parts[0].startswith("确认订单") else base
