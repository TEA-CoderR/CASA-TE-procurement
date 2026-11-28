"""
驾驶舱（首页，2026-11-15 第 2 轮：用户要科技感、交互感、流程简洁明了）：
  问候 + 一句话（今天几家供应商、几件事要处理）· 右边主按钮（继续今日采购）
  今日采购流程：同步数据 → 计算订货量 → 审核建议 → 生成订单 → 发送订单，一眼看到卡在哪一步，点一步直接过去
  四张指标卡：今日采购金额（直送 / BMS）· 本月已下单 vs 预算（圆环）· 在途订单 · 需要处理
  今日供应商（各店颜色和起订量检查一样：绿 = 直送 · 橙 = BMS · 灰 = 这次不订）· 需要处理（每行带按钮）· 采购助手
"""
import threading
from datetime import datetime
import os

from PySide6.QtCore import Qt, QObject, Signal, QRectF, QPointF, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QFontMetrics
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QStyledItemDelegate,
                               QSizePolicy)

from i18n import tr
from theme import T, num_font, NUM_FONT, current as _cur_theme
from erp_widgets import RowSorter, SORT_ROLE, DotsDelegate, DOTS_ROLE, Ring
import store_orders as S

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# 今日供应商的状态：(文字, 颜色, 排序)
STATES = {"done": ("已配货", "ok", 0), "calc": ("已计算", "accent", 1), "skip": ("这次不订", "muted", 3),
          "off": ("不配货", "faint", 4), "todo": ("待计算", "warn", 2)}
TONE_ROLE = Qt.UserRole + 41


class _Sig(QObject):
    moq_loaded = Signal()


def theme_dark():
    return _cur_theme() == "dark"


def _soft(tone):
    if tone in ("muted", "faint"):
        return T("track")
    return T("accent_soft") if tone == "accent" else T(tone + "_soft")


def _panel(title=None, tip=None):
    f = QFrame()
    f.setObjectName("Panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(20, 16, 20, 16)
    v.setSpacing(12)
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


class DashboardPage:
    def __init__(self, win, settings):
        self.win, self.settings = win, settings
        self.sig = _Sig()
        self.sig.moq_loaded.connect(self.refresh)
        self._moq_loading = False
        self._today = {"files": 0, "todo": 0}
        self._n_attn = 0

    # =================================================================
    # 界面
    # =================================================================
    def build(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 24, 28, 28)
        lay.setSpacing(18)

        # ---- 问候 + 主按钮 ----
        head = QHBoxLayout()
        head.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.date_lbl = QLabel("")
        self.date_lbl.setObjectName("PageEyebrow")
        self.greet = QLabel("")
        self.greet.setObjectName("HeroTitle")
        self.hello = QLabel("")
        self.hello.setObjectName("PageSub")
        self.hello.setTextFormat(Qt.RichText)
        self.date_lbl.hide()                      # 日期在顶栏（设计稿）
        col.addWidget(self.greet)
        col.addWidget(self.hello)
        head.addLayout(col, 1)
        b1 = QPushButton(tr("查看在途订单"))
        b1.setMinimumHeight(40)
        b1.setCursor(Qt.PointingHandCursor)
        b1.clicked.connect(lambda: self.win.go_page("tracking"))
        self.btn_start = QPushButton(tr("开始今日采购"))
        self.btn_start.setObjectName("Primary")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self.start_today)
        head.addWidget(b1, 0, Qt.AlignBottom)
        head.addWidget(self.btn_start, 0, Qt.AlignBottom)
        lay.addLayout(head)

        # ---- 今日采购流程 ----
        pp, pv, ph = _panel()
        pv.setContentsMargins(22, 16, 22, 10)
        t = QLabel(tr("今日采购流程").upper())
        t.setObjectName("Eyebrow")
        t.setToolTip(tr("总仓采购今天的进度；点一步直接过去"))
        self.flow_info = QLabel("")
        self.flow_info.setObjectName("Faint")
        hh = QHBoxLayout()
        hh.addWidget(t, 1)
        hh.addWidget(self.flow_info)
        pv.addLayout(hh)
        self.flow = _Pipeline([tr("同步数据"), tr("计算订货量"), tr("审核建议"), tr("生成订单"), tr("发送订单")])
        self.flow.clicked.connect(self._flow_clicked)
        pv.addWidget(self.flow)
        lay.addWidget(pp)

        # ---- 四张指标卡 ----
        g = QGridLayout()
        g.setSpacing(14)
        c1, v1 = self._card()
        self.k_amt_l = self._label(tr("今日采购金额"), v1)
        self.k_amt = self._value(v1)
        self.amt_bar = _SplitBar()
        v1.addWidget(self.amt_bar)
        self.k_amt_sub = self._sub(v1)
        c1.setToolTip(tr("今天总仓采购配好的金额：直接送店 + 并进 BMS；还没配货时是估算"))

        c2 = QFrame()
        c2.setObjectName("Panel")
        h2 = QHBoxLayout(c2)
        h2.setContentsMargins(18, 16, 18, 16)
        h2.setSpacing(14)
        self.ring = Ring(76)
        h2.addWidget(self.ring)
        v2 = QVBoxLayout()
        v2.setSpacing(4)
        self._label(tr("本月已下单"), v2)
        self.k_month = self._value(v2, 22)
        self.k_month_sub = self._sub(v2)
        h2.addLayout(v2, 1)
        c2.setToolTip(tr("采购订单跟踪里本月已下单的金额（不含税），和本月预算比"))
        c2.setCursor(Qt.PointingHandCursor)
        c2.mousePressEvent = lambda _e: self.win.go_page("tracking")

        c3, v3 = self._card()
        self._label(tr("在途订单"), v3)
        self.k_open = self._value(v3)
        self.spark = _Spark()
        v3.addWidget(self.spark)
        self.k_open_sub = self._sub(v3)
        c3.setToolTip(tr("已下单、还没到货的订单"))
        c3.setCursor(Qt.PointingHandCursor)
        c3.mousePressEvent = lambda _e: self.win.go_page("tracking")

        self.c_attn, v4 = self._card("AttnCard")
        self.k_attn_l = self._label(tr("需要处理"), v4)
        self.k_attn = self._value(v4)
        self.k_attn_sub = self._sub(v4)
        self.attn_go = QPushButton(tr("去处理") + " →")
        self.attn_go.setObjectName("Link")
        self.attn_go.setCursor(Qt.PointingHandCursor)
        self.attn_go.clicked.connect(lambda: self.win.go_page("tracking"))
        v4.insertWidget(v4.count() - 1, self.attn_go, 0, Qt.AlignLeft)
        for i, c in enumerate((c1, c2, c3, self.c_attn)):
            g.addWidget(c, 0, i)
            g.setColumnStretch(i, 1)
        lay.addLayout(g)

        # ---- 今日供应商 + 需要处理 / 助手 ----
        row = QHBoxLayout()
        row.setSpacing(14)
        left, lv, lh = _panel(tr("今日供应商"), tr("每家供应商今天的状态和各门店去向；双击一行进入总仓采购"))
        self.sup_title = left.title
        heads = [tr("供应商"), tr("起订量"), tr("五家门店"), tr("状态"), tr("金额")]
        self.sup_table = QTableWidget(0, len(heads))
        self.sup_table.setHorizontalHeaderLabels(heads)
        self.sup_table.horizontalHeaderItem(2).setToolTip(
            " · ".join(f"{k} {n}" for k, n in S.STORE_SHEETS.items()))
        self.sup_table.setObjectName("CleanTable")
        self.sup_table.verticalHeader().setVisible(False)
        self.sup_table.setShowGrid(False)
        self.sup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sup_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sup_table.setFocusPolicy(Qt.NoFocus)
        self.sup_table.verticalHeader().setDefaultSectionSize(42)
        self.sup_table.setItemDelegateForColumn(2, _SquaresDelegate(self.sup_table))
        self.sup_table.setItemDelegateForColumn(3, _ChipDelegate(self.sup_table))
        hh = self.sup_table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        self.sup_table.setMinimumHeight(320)
        self.sup_table.cellDoubleClicked.connect(lambda *_: self.start_today())
        self.sup_sorter = RowSorter(self.sup_table)
        self.sup_empty = QLabel(tr("今天没有要下单的供应商"))
        self.sup_empty.setObjectName("Empty")
        self.sup_empty.setAlignment(Qt.AlignCenter)
        lv.addWidget(self.sup_table, 1)
        lv.addWidget(self.sup_empty, 1)
        leg = QHBoxLayout()
        leg.setSpacing(6)
        self.legend = []
        for kind, text in (("ok", tr("够起订量直送")), ("near", tr("并进 BMS")), ("far", tr("这次不订"))):
            dot = QLabel()
            dot.setFixedSize(10, 10)
            lab = QLabel(text)
            lab.setObjectName("Faint")
            leg.addWidget(dot)
            leg.addWidget(lab)
            leg.addSpacing(10)
            self.legend.append((dot, kind))
        leg.addStretch()
        lv.addLayout(leg)
        row.addWidget(left, 3)

        rcol = QVBoxLayout()
        rcol.setSpacing(14)
        ap, self.attn, _ = _panel(tr("需要处理"), tr("从采购订单跟踪、数据库状态汇总；点一行或右边的按钮直接过去"))
        self.attn.setSpacing(8)
        rcol.addWidget(ap)

        self.agent_card = QFrame()
        self.agent_card.setObjectName("AgentCard")
        av = QVBoxLayout(self.agent_card)
        av.setContentsMargins(18, 16, 18, 16)
        av.setSpacing(10)
        self.agent_head = QLabel(tr("✦  采购助手").upper())
        self.agent_head.setObjectName("AgentHead")
        at = QLabel(tr("查供应商、算建议量、生成订单、核对邮件，直接说就行；改数据前都会先问你。"))
        at.setWordWrap(True)
        at.setObjectName("AgentText")
        av.addWidget(self.agent_head)
        av.addWidget(at)
        qb = QHBoxLayout()
        qb.setSpacing(8)
        for q in (tr("今天该订什么？"), tr("哪些订单超期了？")):
            b = QPushButton(q)
            b.setObjectName("AgentQuick")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, q=q: self._ask(q))
            qb.addWidget(b)
        qb.addStretch()
        av.addLayout(qb)
        rcol.addWidget(self.agent_card)
        rcol.addStretch()
        row.addLayout(rcol, 2)
        lay.addLayout(row, 1)

        self._attn_timer = QTimer(w)              # 订单跟踪 / 数据库状态变了：页面开着时一分钟刷新一次
        self._attn_timer.timeout.connect(lambda: w.isVisible() and self.refresh_attention())
        self._attn_timer.start(60000)
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self.refresh_attention())
        self.page = w
        self.restyle()                   # 套颜色并填数据
        return w

    def _card(self, name="Panel"):
        f = QFrame()
        f.setObjectName(name)
        v = QVBoxLayout(f)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(6)
        return f, v

    def _label(self, text, v):
        l = QLabel(text)
        l.setObjectName("KpiLabel")
        v.addWidget(l)
        return l

    def _value(self, v, size=None):
        val = QLabel("—")
        val.setObjectName("KpiValue")
        if size:
            val.setStyleSheet(f"font-size:{size}px;")
        v.addWidget(val)
        return val

    def _sub(self, v):
        sub = QLabel(" ")
        sub.setObjectName("Faint")
        sub.setTextFormat(Qt.RichText)
        sub.setWordWrap(True)
        v.addWidget(sub)
        v.addStretch()
        return sub

    def _ask(self, q):
        self.win.toggle_agent(True)
        if getattr(self.win, "agent_panel", None) is not None:
            self.win.agent_panel.send(q)

    def _flow_clicked(self, i):
        if i == 0:
            self.win.go_page("settings")
        elif i == 4:
            self.win.go_page("tracking")
        else:
            self.start_today()

    # ---------- 需要处理 ----------
    def _attention(self):
        """[(颜色, 标题, 说明, 按钮文字, 去哪一页, 数量)]"""
        out = []
        tp = getattr(self.win, "tracking", None)
        st = getattr(tp, "st", None) if tp is not None else None
        self._month = None
        if st is not None:
            try:
                import order_tracking as OT
                rows = OT.rows(st)
                s = OT.summary(rows, st, datetime.now().strftime("%Y-%m"))
                rows = OT.units(rows)                 # BMS 凑单算一张（每家店那一份不单独数）
                self._month = s
                try:                             # 在途卡片的小走势：最近 10 周每周下了几张单
                    today = datetime.now().date()
                    wk = [0] * 10
                    for r in rows:
                        d = r.get("date")
                        d = d.date() if hasattr(d, "date") else d
                        if d is None or r["status"] == "draft":
                            continue
                        k = (today - d).days // 7
                        if 0 <= k < 10:
                            wk[9 - k] += 1
                    self._weeks = wk
                except Exception:
                    self._weeks = []

                def names(pick):
                    xs = []
                    for r in rows:
                        if pick(r) and r.get("supplier") and r["supplier"] not in xs:
                            xs.append(str(r["supplier"]))
                    return " · ".join(xs[:3]) + (" …" if len(xs) > 3 else "")
                if s["draft"]:
                    out.append(("accent", tr("{0} 张订单待登记", s["draft"]),
                                names(lambda r: r["status"] == "draft") or tr("还没发给供应商"), tr("发送订单"), "tracking", s["draft"]))
                if s["overdue"]:
                    out.append(("err", tr("{0} 张订单已超期", s["overdue"]),
                                names(lambda r: r["overdue"]) or tr("过了预计到货日还没到"), tr("查看"), "tracking", s["overdue"]))
                if s["pending"]:
                    out.append(("warn", tr("{0} 张等供应商确认", s["pending"]),
                                names(lambda r: r["pending"]) or tr("供应商还没确认"), tr("查看"), "tracking", s["pending"]))
                if s["stuck"]:
                    out.append(("err", tr("{0} 张订单卡住了", s["stuck"]),
                                names(lambda r: r["status"] == "stuck"), tr("查看"), "tracking", s["stuck"]))
            except Exception:
                self._month = None
        ok = getattr(self.win, "_db_ok", None)
        if ok is False:
            out.append(("err", tr("数据库连不上"), tr("销量、库存读不到"), tr("检查"), "settings", 1))
        else:
            try:
                import reorder_service as R
                upto = getattr(self.win, "_db_sales_upto", None)
                late = R.stale_days(upto) if ok else 0
            except Exception:
                late, upto = 0, None
            if late and late > getattr(R, "STALE_DAYS", 2):
                out.append(("warn", tr("销售数据只到 {0}", upto.strftime("%d/%m")),
                            tr("{0} 天没同步", late), tr("详情"), "settings", 1))
        return out

    def refresh_attention(self):
        if not hasattr(self, "attn"):
            return
        items = self._attention()
        while self.attn.count() > 1:
            it = self.attn.takeAt(1)
            if it.widget():
                it.widget().deleteLater()
        if not items:
            box = QFrame()
            box.setObjectName("ActRow")
            bh = QHBoxLayout(box)
            bh.setContentsMargins(12, 12, 12, 12)
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background:{T('ok')}; border-radius:4px;")
            t1 = QLabel(tr("一切正常 · 没有待登记、待确认或超期的订单"))
            t1.setStyleSheet(f"color:{T('ok')}; font-weight:600;")
            bh.addWidget(dot)
            bh.addWidget(t1, 1)
            self.attn.addWidget(box)
        for tone, title, sub, cta, page, _n in items:
            self.attn.addWidget(_ActRow(tone, title, sub, cta, lambda p=page: self.win.go_page(p)))
        self._n_attn = sum(n for *_x, n in items)
        tones = [x[0] for x in items]
        self.k_attn.setText(str(self._n_attn) if items else "0")
        self.k_attn_sub.setText(" · ".join(x[1] for x in items[:3]) if items else tr("一切正常"))
        self._attn_tone = "warn" if items else "ok"           # 设计稿：有事就是琥珀色卡片
        self.attn_go.setVisible(bool(items))
        if items:
            self.attn_go.clicked.disconnect()
            self.attn_go.clicked.connect(lambda _=False, pg=items[0][4]: self.win.go_page(pg))
        self._style_attn_card()
        m = self._month
        if m is None:
            self.k_month.setText("—")
            self.k_month_sub.setText(tr("打开采购订单跟踪后显示"))
            self.ring.set(None, "accent")
            self.k_open.setText("—")
            self.k_open_sub.setText(" ")
        else:
            self.k_month.setText(f"€{m['spent']:,.0f}")
            if m["budget"]:
                p = m["spent"] / m["budget"]
                left = m["budget"] - m["spent"]
                self.k_month_sub.setText(tr("预算 €{0:,.0f} · 还剩 €{1:,.0f}", m["budget"], left) if left >= 0
                                         else tr("预算 €{0:,.0f} · 超出 €{1:,.0f}", m["budget"], -left))
                self.ring.set(p, "err" if p > 1 else "warn" if p >= 0.9 else "ok", True)
            else:
                self.k_month_sub.setText(tr("这个月还没设预算"))
                self.ring.set(None, "accent")
            self.k_open.setText(tr("{0} 张", m.get("open", 0)))
            self.spark.set(getattr(self, "_weeks", []))
            self.spark.setToolTip(tr("最近 10 周每周下了几张单"))
            self.k_open_sub.setText(f"<span style='color:{T('err')}'>" + tr("{0} 张已超期", m["overdue"]) + "</span>"
                                    if m["overdue"] else tr("都没超期"))
        self._update_hello()

    def _style_attn_card(self):
        tone = getattr(self, "_attn_tone", "ok")
        c = QColor(T(tone))
        self.c_attn.setStyleSheet(
            f"#AttnCard {{ background: rgba({c.red()},{c.green()},{c.blue()},0.08); "
            f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.30); border-radius: 12px; }}")
        self.k_attn.setStyleSheet(f"color:{T(tone)};")
        self.k_attn_l.setStyleSheet(f"color:{T(tone)};")

    def _update_hello(self):
        n, todo = self._today["files"], self._today["todo"]
        if not self._today.get("day"):
            first = tr("今天周末，没有要下单的供应商")
        elif self._today.get("nofolder"):
            first = tr("先在系统设置里选好采购配单文件夹")
        else:
            first = tr("今天 <b>{0} 家供应商</b>要下单", n)
            if todo:
                first += tr("，<b style='color:{0}'>{1} 家</b>还没计算", T("warn"), todo)
        if self._n_attn:
            first += tr("，另有 <b style='color:{0}'>{1} 件事</b>需要处理", T("err"), self._n_attn)
        self.hello.setText(first + tr("。"))

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
        self.db_ok = ok
        if hasattr(self, "flow"):
            self.refresh()

    def _load_moq(self, paths):
        """今天的供应商起订量还没读过（门店配货页选的是别的星期）：后台读，读完刷新"""
        so = self.win.store_orders
        if self._moq_loading or not hasattr(so, "moq"):
            return
        self._moq_loading = True

        def work():
            for p in paths:
                S.load_moq_into(so.moq, p)              # 总仓采购同时在读同一个文件时只读一次
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

    def _drafts_today(self):
        """今天程序生成、还没发出去的单（待登记）有几张；读不到 = None"""
        tp = getattr(self.win, "tracking", None)
        st = getattr(tp, "st", None) if tp is not None else None
        if st is None:
            return None
        try:
            import order_tracking as OT
            today = datetime.now().date()
            return sum(1 for r in OT.rows(st) if r["status"] == "draft"
                       and (r.get("date") is None or getattr(r["date"], "date", lambda: r["date"])() == today))
        except Exception:
            return None

    def refresh(self):
        if not hasattr(self, "flow"):
            return
        now = datetime.now()
        wd = now.weekday()
        self.date_lbl.setText(tr("{0} · {1:%d/%m/%Y}", tr(WEEKDAYS[wd]), now).upper())
        so = self.win.store_orders
        day = self._today_folder()
        root = (self.settings.get("store_orders") or {}).get("folder") or ""
        files = S.list_schede(root, day) if day and root and os.path.isdir(root) else []
        skip = set((self.settings.get("store_orders") or {}).get("skip") or [])
        files = [(d, p) for d, p in files if S.supplier_short_name(p) not in skip]
        hour = now.hour
        who = (self.settings.get("user_name") or "").strip()
        if who:
            self.greet.setText(tr("早上好，{0}", who) if hour < 12 else tr("下午好，{0}", who) if hour < 18
                               else tr("晚上好，{0}", who))
        else:
            self.greet.setText(tr("早上好") if hour < 12 else tr("下午好") if hour < 18 else tr("晚上好"))
        self.win.set_badge("store_orders", len(files) if files else "")
        todo_moq = [p for _, p in files if p not in getattr(so, "moq", {})]
        if todo_moq:
            self._load_moq(todo_moq)

        rows = self._supplier_rows(files)
        n_done = sum(1 for r in rows if r[8] == "res")
        n_calc = sum(1 for r in rows if r[8] == "calc")
        n_todo = sum(1 for r in rows if r[8] is None)
        has_res = n_done > 0
        self._today = {"files": len(files), "todo": n_todo, "day": bool(day),
                       "nofolder": bool(day) and not (root and os.path.isdir(root))}
        self.btn_start.setText((tr("查看配货结果") if has_res else tr("继续今日采购") if n_calc else tr("开始今日采购")) + "  →")
        self.btn_start.setEnabled(bool(day))

        # ---- 金额 ----
        direct = bms = wait_amt = 0.0
        n_wait = n_urg = 0
        for name, moq, state, dots, tips, amount, sc, plan, src in rows:
            if not plan or state == "off":
                continue
            for sid, st in plan["stores"].items():
                if st["status"] == "none":
                    continue
                k = so._plan_state(plan, sid)
                if k in ("ok", "fill"):
                    direct += st["amount"]
                elif k == "bms_ok":
                    pass
                else:
                    n_wait += 1
                    wait_amt += st["amount"]
                    n_urg += so._urgent_of(sc, plan, sid)[0] >= 0.5
            b = plan.get("bms")
            if S.bms_ordered(b):
                bms += b["amount"]
        total = direct + bms
        any_plan = any(r[7] for r in rows)
        self.k_amt.setText(f"€{total:,.0f}" if any_plan else "—")
        self.k_amt_l.setText(tr("今日采购金额") + ("" if has_res or not any_plan else tr("（估算）")))
        self.amt_bar.set(direct, bms)
        if any_plan:
            sub = (f"<span style='color:{T('accent')}'>●</span> " + tr("直送 €{0:,.0f}", direct)
                   + f" · <span style='color:{T('cyan')}'>●</span> " + tr("BMS €{0:,.0f}", bms))
            if n_wait:
                sub += "<br>" + (f"<span style='color:{T('err')}'>" if n_urg else "") + \
                    tr("这次不订 {0} 张单 · €{1:,.0f}", n_wait, wait_amt) + ("</span>" if n_urg else "")
            self.k_amt_sub.setText(sub)
        else:
            self.k_amt_sub.setText(tr("今天还没有计算"))

        # ---- 今日采购流程 ----
        f = self.flow
        ok = getattr(self.win, "_db_ok", None)
        try:
            import reorder_service as R
            upto = getattr(self.win, "_db_sales_upto", None)
            late = R.stale_days(upto) if ok else None
            stale = late is not None and late > R.STALE_DAYS
        except Exception:
            upto, stale = None, False
        if ok is None:
            f.set(0, "now", tr("正在检查数据库…"))
        elif ok is False:
            f.set(0, "err", tr("数据库连不上"))
        elif stale:
            f.set(0, "warn", tr("数据只到 {0}", upto.strftime("%d/%m")))
        else:
            f.set(0, "done", tr("销售到 {0}", upto.strftime("%d/%m")) if upto else tr("数据库已连接"))
        n_files = len(S.written_files((getattr(so, "result", None) or {}).get("written", []))) if has_res else 0
        ed = len(getattr(so, "edited", set()))
        drafts = self._drafts_today()
        if not files:
            f.set(1, "todo", tr("今天没有要下单的") if day else tr("周末"))
            for i in (2, 3, 4):
                f.set(i, "todo", "")
        else:
            calc_done = n_todo < len(files)
            f.set(1, "done" if calc_done else "now",
                  tr("{0} 家供应商", len(files) - n_todo) if calc_done else tr("{0} 家待计算", len(files)))
            if has_res:
                f.set(2, "done", tr("改了 {0} 处", ed))
                f.set(3, "done", tr("{0} 个订单文件", n_files))
                if drafts:
                    f.set(4, "now", tr("{0} 张待发送", drafts))
                elif drafts == 0:
                    f.set(4, "done", tr("都已发送"))
                else:
                    f.set(4, "now", tr("在订单跟踪里发送"))
            elif calc_done:
                f.set(2, "now", tr("改了 {0} 处", ed) if ed else tr("核对订货明细"))
                f.set(3, "todo", tr("直送 / 并进 BMS"))
                f.set(4, "todo", tr("一键发给供应商"))
            else:
                f.set(2, "todo", tr("核对订货明细"))
                f.set(3, "todo", tr("直送 / 并进 BMS"))
                f.set(4, "todo", tr("一键发给供应商"))
        done = sum(1 for s_, _x in f.states if s_ == "done")
        self.flow_info.setText(tr("{0} / 5 步完成", done))

        # ---- 今日供应商 ----
        t = self.sup_table
        t.setRowCount(len(rows))
        for i, (name, moq, state, dots, tips, amount, sc, plan, src) in enumerate(rows):
            text, kind, rank = STATES[state]
            vals = [name, moq, "", tr(text), f"€{amount:,.2f}" if amount else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    fo = it.font()
                    fo.setBold(state in ("done", "calc"))
                    it.setFont(fo)
                    if state in ("skip", "off"):
                        it.setForeground(QColor(T("muted")))
                if c == 1:
                    it.setForeground(QColor(T("muted")))
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    it.setFont(num_font(10))
                if c == 2:
                    it.setData(DOTS_ROLE, dots)
                    it.setToolTip(tips)
                    it.setData(SORT_ROLE, sum(1 for d in dots if d == T("ok")) * 10 + sum(1 for d in dots if d == T("near")))
                if c == 3:
                    it.setData(SORT_ROLE, rank)
                    it.setData(TONE_ROLE, kind)
                if c == 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    it.setFont(num_font(10, True))
                    it.setData(SORT_ROLE, float(amount or 0))
                t.setItem(i, c, it)
        self.sup_sorter.apply()
        self.sup_table.setVisible(bool(rows))
        self.sup_empty.setVisible(not rows)
        self.sup_title.setText(tr("今日供应商（{0}）", tr(day)) + "  ⓘ" if day else tr("今日供应商") + "  ⓘ")
        self._update_hello()

    def restyle(self):
        if hasattr(self, "page"):
            self.page.setStyleSheet(
                f"#ActRow {{ background:{T('surface2')}; border:1px solid {T('line')}; border-radius:12px; }} "
                f"#ActRow:hover {{ border-color:{T('accent')}; background:{T('hover')}; }} "
                f"#ActTitle {{ font-weight:600; }} "
                f"#HeroTitle {{ font-size:28px; font-weight:700; color:{T('text')}; }} "
                f"#Eyebrow {{ color:{T('muted')}; font-size:11px; font-weight:700; letter-spacing:1px; }} "
                f"#AgentCard {{ background:{T('accent_soft')}; border:1px solid {T('accent_ring')}; border-radius:12px; }} "
                f"#AgentHead {{ color:{T('accent')}; font-size:11px; font-weight:700; letter-spacing:1px; }} "
                f"#AgentText {{ color:{T('text')}; }} "
                f"QPushButton#AgentQuick {{ background:transparent; border:1px solid {T('accent_ring')}; border-radius:8px; "
                f"padding:6px 12px; color:{T('accent')}; font-size:12px; }} "
                f"QPushButton#AgentQuick:hover {{ background:{T('accent')}; color:{T('on_accent')}; border-color:{T('accent')}; }}")
        for dot, kind in getattr(self, "legend", []):
            dot.setStyleSheet(f"background:{T(kind)}; border-radius:3px;")
        if hasattr(self, "c_attn"):
            self._style_attn_card()
        if hasattr(self, "sup_table"):
            self.flow.update()
            self.amt_bar.update()
            self.refresh()
            self.refresh_attention()


class _Pipeline(QWidget):
    """今日采购流程：一步一列，圆点 + 连线 + 名字 + 一行说明；完成绿、现在这步蓝（外圈发光）、没到灰；点一列发 clicked(i)"""
    clicked = Signal(int)

    def __init__(self, names):
        super().__init__()
        self.names = names
        self.states = [("todo", "")] * len(names)
        self.hover = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(92)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, i, state, sub):
        self.states = list(self.states)
        self.states[i] = (state, sub)
        self.update()

    def _col(self, x):
        n = len(self.names)
        return max(0, min(n - 1, int(x / max(1, self.width() / n))))

    def mouseMoveEvent(self, e):
        c = self._col(e.position().x())
        if c != self.hover:
            self.hover = c
            self.update()

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self._col(e.position().x()))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.names)
        cw = self.width() / n
        tone = {"done": T("ok"), "now": T("accent"), "warn": T("warn"), "err": T("err"), "todo": T("border")}
        cy, r = 22, 14
        for i, name in enumerate(self.names):
            st, sub = self.states[i]
            x0 = i * cw
            if i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("hover")))
                p.drawRoundedRect(QRectF(x0 + 2, 0, cw - 8, self.height()), 10, 10)
            cx = x0 + 10 + r
            col = QColor(tone.get(st, T("border")))
            # 连线：到下一步
            if i < n - 1:
                nxt = self.states[i + 1][0]
                line = QColor(T("ok")) if st == "done" and nxt != "todo" else QColor(T("track"))
                if st == "done" and nxt == "todo":
                    line = QColor(T("track"))
                p.setPen(QPen(line, 2))
                p.drawLine(QPointF(cx + r + 8, cy), QPointF(x0 + cw - 4, cy))
            if st in ("now", "warn", "err"):
                glow = QColor(col)
                glow.setAlpha(55)
                p.setPen(Qt.NoPen)
                p.setBrush(glow)
                p.drawEllipse(QPointF(cx, cy), r + 5, r + 5)
            if st == "todo":
                p.setPen(QPen(QColor(T("border")), 1.5))
                p.setBrush(QColor(T("surface2")))
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(col)
            p.drawEllipse(QPointF(cx, cy), r, r)
            f = QFont(NUM_FONT)
            f.setPixelSize(13)
            f.setBold(True)
            p.setFont(f)
            fg = QColor(T("muted")) if st == "todo" else QColor(
                T("on_accent") if st == "now" else "#06140E" if st == "done" and theme_dark() else
                "#FFFFFF" if st in ("done", "err") else "#1A0E02")
            p.setPen(fg)
            mark = "✓" if st == "done" else "!" if st in ("warn", "err") else str(i + 1)
            p.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, mark)
            f2 = self.font()
            f2.setPixelSize(14)
            f2.setBold(True)
            p.setFont(f2)
            p.setPen(QColor(T("muted") if st == "todo" else T("text")))
            tx = x0 + 10
            p.drawText(QRectF(tx, cy + r + 8, cw - 20, 22), Qt.AlignLeft | Qt.AlignVCenter, name)
            f3 = self.font()
            f3.setPixelSize(12)
            p.setFont(f3)
            sc = {"now": T("accent"), "warn": T("warn"), "err": T("err")}.get(st, T("muted"))
            p.setPen(QColor(sc))
            el = QFontMetrics(f3).elidedText(sub or "", Qt.ElideRight, int(cw - 20))
            p.drawText(QRectF(tx, cy + r + 30, cw - 20, 20), Qt.AlignLeft | Qt.AlignVCenter, el)
        p.end()


class _Spark(QWidget):
    """小走势线（设计稿「在途订单」卡片里那条）"""

    def __init__(self):
        super().__init__()
        self.vals = []
        self.setFixedHeight(30)

    def set(self, vals):
        self.vals = list(vals or [])
        self.update()

    def paintEvent(self, _e):
        if len(self.vals) < 2 or not max(self.vals):
            return
        from PySide6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        mx, mn = max(self.vals), min(self.vals)
        span = (mx - mn) or 1
        w, h = self.width(), self.height() - 6
        path = QPainterPath()
        for i, v in enumerate(self.vals):
            x = w * i / (len(self.vals) - 1)
            y = 3 + h - h * (v - mn) / span
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        c = QColor(T("accent"))
        c.setAlpha(210)
        p.setPen(QPen(c, 2))
        p.drawPath(path)
        p.end()


class _SplitBar(QWidget):
    """一条分成两段的细条：直送（主色）· BMS（青色）"""

    def __init__(self):
        super().__init__()
        self.a = self.b = 0.0
        self.setFixedHeight(6)

    def set(self, a, b):
        self.a, self.b = float(a or 0), float(b or 0)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        w = self.width()
        tot = self.a + self.b
        if not tot:
            p.setBrush(QColor(T("track")))
            p.drawRoundedRect(QRectF(0, 0, w, 6), 3, 3)
        else:
            wa = w * self.a / tot
            if self.a:
                p.setBrush(QColor(T("accent")))
                p.drawRoundedRect(QRectF(0, 0, max(6, wa - (2 if self.b else 0)), 6), 3, 3)
            if self.b:
                p.setBrush(QColor(T("cyan")))
                p.drawRoundedRect(QRectF(wa + (2 if self.a else 0), 0, max(6, w - wa - (2 if self.a else 0)), 6), 3, 3)
        p.end()


class _SquaresDelegate(QStyledItemDelegate):
    """五家门店：一排 14 像素的圆角小方块（颜色和起订量检查一样）"""

    def paint(self, p, opt, idx):
        super().paint(p, opt, idx)
        cols = idx.data(DOTS_ROLE) or []
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        x = opt.rect.x() + 10
        y = opt.rect.center().y() - 7
        for c in cols:
            p.setBrush(QColor(c))
            p.drawRoundedRect(QRectF(x, y, 14, 14), 4, 4)
            x += 19
        p.restore()

    def sizeHint(self, opt, idx):
        s = super().sizeHint(opt, idx)
        s.setWidth(max(s.width(), 10 + 19 * 5 + 10))
        return s


class _ChipDelegate(QStyledItemDelegate):
    """状态列：彩色小胶囊"""

    def paint(self, p, opt, idx):
        text = idx.data(Qt.DisplayRole) or ""
        tone = idx.data(TONE_ROLE) or "muted"
        if not text:
            return super().paint(p, opt, idx)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(opt.font)
        f.setPixelSize(12)
        f.setBold(tone in ("ok", "accent"))
        fm = QFontMetrics(f)
        w = fm.horizontalAdvance(text) + 20
        h = 22
        r = QRectF(opt.rect.x() + 8, opt.rect.center().y() - h / 2 + 1, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(_soft(tone)))
        p.drawRoundedRect(r, h / 2, h / 2)
        p.setFont(f)
        p.setPen(QColor(T(tone)))
        p.drawText(r, Qt.AlignCenter, text)
        p.restore()

    def sizeHint(self, opt, idx):
        s = super().sizeHint(opt, idx)
        s.setWidth(s.width() + 28)
        return s


class _ActRow(QFrame):
    """需要处理的一行：彩色点 + 标题 / 一行说明 + 右边按钮；点哪里都过去"""

    def __init__(self, tone, title, sub, cta, fn):
        super().__init__()
        self.fn = fn
        self.setObjectName("ActRow")
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 10, 10, 10)
        h.setSpacing(12)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{T(tone)}; border-radius:4px;")
        c = QVBoxLayout()
        c.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("ActTitle")
        s = QLabel(sub)
        s.setObjectName("Faint")
        c.addWidget(t)
        if sub:
            c.addWidget(s)
        b = QPushButton(cta + "  ›")
        b.setObjectName("Link")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        h.addWidget(dot)
        h.addLayout(c, 1)
        h.addWidget(b)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.fn()
