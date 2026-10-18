"""
工作台（首页）：今天要订哪些供应商、采购流程进度、金额概览、门店、最近记录
"""
import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)

from i18n import tr
from theme import T, num_font
from erp_widgets import StepBar, kpi_card, chip, set_chip
import store_orders as S
import history

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class DashboardPage:
    def __init__(self, win, settings):
        self.win, self.settings = win, settings

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
        b2 = QPushButton(tr("开始今日采购"))
        b2.setObjectName("Primary")
        b2.clicked.connect(self.start_today)
        self.btn_start = b2
        head.addWidget(b1, 0, Qt.AlignBottom)
        head.addWidget(b2, 0, Qt.AlignBottom)
        lay.addLayout(head)

        g = QGridLayout()
        g.setSpacing(14)
        c1, self.k_sup, self.k_sup_sub = kpi_card(tr("今日供应商"), "—", " ")
        c2, self.k_store, self.k_store_sub = kpi_card(tr("门店配货金额"), "—", " ")
        c3, self.k_cmp, self.k_cmp_sub = kpi_card(tr("最近一次比价"), "—", " ")
        c4, self.k_db, self.k_db_sub = kpi_card(tr("数据库"), "—", " ")
        for i, c in enumerate((c1, c2, c3, c4)):
            g.addWidget(c, 0, i)
        lay.addLayout(g)

        p = QFrame()
        p.setObjectName("Panel")
        pv = QVBoxLayout(p)
        pv.setContentsMargins(18, 14, 18, 16)
        pv.setSpacing(12)
        ph = QHBoxLayout()
        t = QLabel(tr("今日采购流程 · 门店配货"))
        t.setObjectName("Section")
        ph.addWidget(t, 1)
        go = QPushButton(tr("进入工作台 →"))
        go.setObjectName("Link")
        go.clicked.connect(lambda: self.win.go_page("store_orders"))
        ph.addWidget(go)
        pv.addLayout(ph)
        self.steps = StepBar([(tr("读取供应商清单"), ""), (tr("计算订货量"), ""), (tr("核对明细"), ""),
                              (tr("配货与凑单"), ""), (tr("生成订单"), "")])
        pv.addWidget(self.steps)
        lay.addWidget(p)

        row = QHBoxLayout()
        row.setSpacing(16)
        left = QFrame()
        left.setObjectName("Panel")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 12, 0, 0)
        lv.setSpacing(8)
        lh = QHBoxLayout()
        lh.setContentsMargins(16, 0, 16, 0)
        self.sup_title = QLabel(tr("今日供应商"))
        self.sup_title.setObjectName("Section")
        lh.addWidget(self.sup_title, 1)
        lv.addLayout(lh)
        self.sup_table = QTableWidget(0, 4)
        self.sup_table.setHorizontalHeaderLabels([tr("供应商"), tr("起订量"), tr("状态"), tr("金额")])
        self.sup_table.verticalHeader().setVisible(False)
        self.sup_table.setShowGrid(False)
        self.sup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sup_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.sup_table.setFocusPolicy(Qt.NoFocus)
        self.sup_table.verticalHeader().setDefaultSectionSize(38)
        hh = self.sup_table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        self.sup_table.setMinimumHeight(300)
        lv.addWidget(self.sup_table)
        row.addWidget(left, 3)

        right = QFrame()
        right.setObjectName("Panel")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 12, 16, 14)
        rv.setSpacing(8)
        t2 = QLabel(tr("门店与仓库"))
        t2.setObjectName("Section")
        rv.addWidget(t2)
        fill = dict(S.DEFAULT_FILL, **((self.settings.get("store_orders") or {}).get("fill") or {}))
        far = set(fill.get("far_stores") or [])
        colo = fill.get("colocated_store")
        self.store_rows = []
        for sid, name in list(S.STORE_SHEETS.items()) + [(0, "BMS")]:
            fr = QFrame()
            fr.setObjectName("MiniCard")
            h = QHBoxLayout(fr)
            h.setContentsMargins(10, 8, 10, 8)
            h.setSpacing(10)
            badge = QLabel(str(sid))
            badge.setFixedSize(28, 28)
            badge.setAlignment(Qt.AlignCenter)
            badge.setFont(num_font(10, True))
            c = QVBoxLayout()
            c.setSpacing(1)
            n = QLabel(name if sid else tr("BMS 总仓"))
            n.setStyleSheet("font-weight:600;")
            if sid == 0:
                note, tag, kind = tr("独立公司 · 合并订货后分货"), tr("总仓"), "gray"
            elif sid in far:
                note, tag, kind = tr("离总仓远 · 不借货"), tr("远店"), "warn"
            elif sid == colo:
                note, tag, kind = tr("和 BMS 同一个仓库"), tr("BMS 同仓"), "ok"
            else:
                note, tag, kind = tr("同城"), tr("同城"), "accent"
            nt = QLabel(note)
            nt.setObjectName("Faint")
            c.addWidget(n)
            c.addWidget(nt)
            tg = chip(tag, kind)
            h.addWidget(badge)
            h.addLayout(c, 1)
            h.addWidget(tg)
            rv.addWidget(fr)
            self.store_rows.append((badge, tg, tag, kind))
        rv.addStretch()
        row.addWidget(right, 2)
        lay.addLayout(row)

        rec = QFrame()
        rec.setObjectName("Panel")
        rcv = QVBoxLayout(rec)
        rcv.setContentsMargins(16, 12, 16, 12)
        rcv.setSpacing(6)
        rh = QHBoxLayout()
        t3 = QLabel(tr("最近的比价记录"))
        t3.setObjectName("Section")
        rh.addWidget(t3, 1)
        more = QPushButton(tr("全部历史记录 →"))
        more.setObjectName("Link")
        more.clicked.connect(lambda: self.win.go_page("history"))
        rh.addWidget(more)
        rcv.addLayout(rh)
        self.recent = QVBoxLayout()
        self.recent.setSpacing(0)
        rcv.addLayout(self.recent)
        lay.addWidget(rec)
        lay.addStretch()
        self.restyle()
        self.refresh()
        return w

    # ---------- 数据 ----------
    def _today_folder(self):
        wd = datetime.now().weekday()
        return S.DAY_FOLDERS[wd] if wd < 5 else None

    def start_today(self):
        so = self.win.store_orders
        day = self._today_folder()
        if day and hasattr(so, "day"):
            i = so.day.findData(day)
            if i >= 0:
                so.day.setCurrentIndex(i)
        self.win.go_page("store_orders")

    def set_db(self, ok):
        set_chip(self.k_db_sub, tr("ysoft 已连接") if ok else tr("连不上，去系统设置检查"), "ok" if ok else "err")
        self.k_db.setText(tr("正常") if ok else tr("未连接"))
        self.k_db.setStyleSheet(f"color:{T('ok') if ok else T('err')};")

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
        self.btn_start.setEnabled(bool(day))
        self.win.set_badge("store_orders", len(files) if files else "")

        calc = getattr(so, "calc", None)
        res = getattr(so, "result", None)
        calc_paths = {sc["path"] for sc in calc["schede"]} if calc else set()
        res_by = {}
        if res:
            for _, sc, plan in res["results"]:
                t = S.plan_totals([("", sc, plan)])
                res_by[sc["path"]] = t["total"]
        self.k_sup.setText(str(len(files)) if day else "—")
        self.k_sup_sub.setText(tr("{0} 家已计算 · {1} 家已生成订单", len(calc_paths & {p for _, p in files}),
                                  len(set(res_by) & {p for _, p in files})) if files else tr("周一到周五按文件夹"))
        if res:
            tot = S.plan_totals(res["results"])
            self.k_store.setText(f"€{tot['total']:,.2f}")
            self.k_store_sub.setText(tr("直接送店 €{0:,.0f} · BMS €{1:,.0f}", tot["direct"], tot["bms"]))
        elif calc:
            amt = 0.0
            for sc in calc["schede"]:
                items = {it["row"]: it for it in sc["items"]}
                for lines in so.colli.get(sc["path"], {}).values():
                    amt += sum(n * items[r]["pack"] * items[r]["price"] for r, n in lines.items() if r in items)
            self.k_store.setText(f"€{amt:,.2f}")
            self.k_store_sub.setText(tr("配货前估算"))
        else:
            self.k_store.setText("—")
            self.k_store_sub.setText(tr("还没有计算"))
        runs = history.list_runs()
        if runs:
            r0 = runs[0]
            self.k_cmp.setText(f"€{r0.get('final_cost', 0):,.2f}")
            self.k_cmp_sub.setText(tr("{0} · {1} 家供应商", r0.get("time", "")[:16], r0.get("suppliers", 0)))
        else:
            self.k_cmp.setText("—")
            self.k_cmp_sub.setText(tr("还没有比价记录"))

        # 流程进度
        st = self.steps
        if not files:
            st.set_current(0)
            st.set_state(0, "now", tr("没有今天的文件"))
        else:
            st.set_state(0, "done", tr("{0} 个 SCHEDA", len(files)))
            if calc:
                st.set_state(1, "done", tr("{0} 家供应商", len(calc["schede"])))
                ed = len(getattr(so, "edited", set()))
                if res:
                    st.set_state(2, "done", tr("改了 {0} 处", ed))
                    st.set_state(3, "done", "")
                    st.set_state(4, "done", tr("{0} 个文件", len(res["written"])))
                else:
                    st.set_state(2, "now", tr("改了 {0} 处", ed))
                    st.set_state(3, "todo", tr("直接送店 / 并入 BMS"))
                    st.set_state(4, "todo", tr("每店一个文件 + BMS"))
            else:
                st.set_state(1, "now", tr("点「开始今日采购」"))
                st.set_state(2, "todo", tr("可手动修改箱数"))
                st.set_state(3, "todo", tr("直接送店 / 并入 BMS"))
                st.set_state(4, "todo", tr("每店一个文件 + BMS"))

        # 今日供应商
        t = self.sup_table
        t.setRowCount(len(files))
        for i, (_, p) in enumerate(files):
            name = S.supplier_short_name(p)
            cells = [name, S.moq_label(so.moq_of(p), short=True) if hasattr(so, "moq_of") else "…", "", ""]
            for c, v in enumerate(cells):
                it = QTableWidgetItem(v)
                if c == 0:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if c == 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    it.setFont(num_font(10))
                t.setItem(i, c, it)
            if p in res_by:
                text, kind, amt = tr("已生成订单"), "ok", f"€{res_by[p]:,.2f}"
            elif p in calc_paths:
                text, kind, amt = tr("已计算"), "accent", ""
            else:
                text, kind, amt = tr("待计算"), "gray", ""
            t.setCellWidget(i, 2, self._chip_cell(text, kind))
            t.item(i, 3).setText(amt)
        self.sup_title.setText(tr("今日供应商（{0}）", tr(day)) if day else tr("今日供应商"))

        # 最近记录
        while self.recent.count():
            it = self.recent.takeAt(0)
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
            h.setContentsMargins(0, 8, 0, 8)
            a = QLabel(r.get("time", "")[:16])
            a.setFont(num_font(9))
            a.setObjectName("Faint")
            b = QLabel(r.get("main_file") or "")
            c = QLabel(f"€{r.get('final_cost', 0):,.2f}")
            c.setFont(num_font(10, True))
            h.addWidget(a)
            h.addSpacing(12)
            h.addWidget(b, 1)
            h.addWidget(c)
            self.recent.addWidget(fr)

    def _chip_cell(self, text, kind):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 0, 8, 0)
        h.addWidget(chip(text, kind))
        h.addStretch()
        return w

    def restyle(self):
        for badge, tg, tag, kind in getattr(self, "store_rows", []):
            badge.setStyleSheet(f"background:{T('accent_soft')}; color:{T('accent')}; border-radius:7px;")
            set_chip(tg, tag, kind)
        if hasattr(self, "steps"):
            self.steps.restyle()
