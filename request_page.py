# -*- coding: utf-8 -*-
"""
门店申请页（Griglia 入口下面）：门店在扫码枪上问的商品，每个条码一行（几家店问的合在一起）。
右边：门店说的（原因 · 箱数 · 备注 · 照片）+ 程序先查好的（数据库 / 这周报价 / 直厂 / Griglia 里最像的）+ 一排回复按钮。
规则和发给门店的话见 product_requests.py。会改门店清单 / Griglia 的回复先确认，做完右下角提示带「撤销」。
"""
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from i18n import tr
import product_requests as PR

CTX_MAX_AGE = 30 * 60          # Griglia + 这周报价最多这么久重读一次
POLL_MS = 10 * 60 * 1000       # 图标栏的数字多久查一次
FILTERS = (("open", "待回复"), ("info", "等门店补充"), ("answered", "已回复"), ("all", "全部"))
HEADS = ("状态", "商品", "条码", "门店", "想订", "门店说的", "等了", "程序建议")


class _Sig(QObject):
    loaded = Signal(object)
    count = Signal(int)


def _T(k):
    from theme import T
    return T(k)


class RequestPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.reqs, self.groups, self.checks, self.db = [], [], {}, {}
        self.ctx, self.ctx_t = None, 0.0
        self.waiting = 0
        self.filter = "open"
        self.page = None
        self.cur = None
        self._loaded_once = False
        self.sig = _Sig()
        self.sig.count.connect(self._set_count)
        self.timer = QTimer(win, interval=POLL_MS, timeout=self.poll)
        self.timer.start()
        QTimer.singleShot(90 * 1000, self.poll)

    # ------------------------------------------------------------------ 图标栏的数字（后台）
    def poll(self):
        import booking_api as BA
        if not BA.ready(self.settings):
            return

        def work():
            try:
                self.sig.count.emit(PR.waiting_count(PR.fetch(self.settings, "open", 60)))
            except Exception:
                pass
        threading.Thread(target=work, name="requests-poll", daemon=True).start()

    def _set_count(self, n):
        self.waiting = int(n or 0)
        self.win.set_badge("grid_req", self.waiting or "")

    # ------------------------------------------------------------------ 页面
    def build(self):
        from page_kit import Hero, Card
        from erp_widgets import TableSortFilter, CountButton
        import settings as ST
        import product_images as PI
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 20)
        lay.setSpacing(14)
        self.hero = Hero(tr("门店申请"), "", tip=tr(
            "门店在扫码枪上问的商品（查不到的、Griglia 已停用的、批发商都没货的）。程序先查好数据库、这周报价、直厂和 "
            "Griglia 里最像的商品；选一种回复，门店扫码枪上马上看得到，不用打电话"))
        self.b_refresh = QPushButton(tr("刷新"))
        self.b_refresh.clicked.connect(lambda: self.refresh(force=True))
        self.hero.add(self.b_refresh)
        lay.addWidget(self.hero)

        body = QHBoxLayout()
        body.setSpacing(14)
        card = Card(margins=(16, 12, 16, 12))
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.chips = {}
        grp = QButtonGroup(w)
        grp.setExclusive(True)
        for key, label in FILTERS:
            b = CountButton(tr(label), None)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(key == self.filter)
            b.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            grp.addButton(b)
            self.chips[key] = b
            bar.addWidget(b)
        bar.addSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("商品 / 条码 / 门店"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(lambda _t: self.ctl.render())
        bar.addWidget(self.search)
        bar.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        card.v.addLayout(bar)
        heads = [tr(h) for h in HEADS]
        self.t = self.ui.make_table(heads, stretch_col=None)
        self.t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for c, wd in enumerate((96, 250, 120, 130, 56, 200, 64, 150)):
            self.t.setColumnWidth(c, wd)
        self.t.itemSelectionChanged.connect(self._sel)
        self.ctl = TableSortFilter(self.t, lambda: len(self.groups), self._text, self._fill, hide=self._hidden,
                                   key=self._key)
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        self.ctl.remember(self.settings.setdefault("request_cols", {}), "list",
                          lambda: ST.save_settings(self.settings), list(range(len(heads))))
        PI.add_thumbs(self.t, 1, 2, self.settings)
        card.v.addWidget(self.t, 1)
        body.addWidget(card, 1)

        self.side = QScrollArea()
        self.side.setWidgetResizable(True)
        self.side.setFrameShape(QFrame.NoFrame)
        self.side.setFixedWidth(420)
        self.side.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.side.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("ReqSide")
        inner.setStyleSheet("#ReqSide{background:transparent;}")
        self.dv = QVBoxLayout(inner)
        self.dv.setContentsMargins(4, 0, 4, 0)
        self.dv.setSpacing(10)
        self.side.setWidget(inner)
        body.addWidget(self.side)
        lay.addLayout(body, 1)
        w.showEvent = lambda e: (QWidget.showEvent(w, e), self._on_show())
        self.page = w
        self._show(None)
        return w

    def _on_show(self):
        if not self._loaded_once or time.time() - self.ctx_t > 5 * 60:
            QTimer.singleShot(0, lambda: self.refresh(force=False))

    # ------------------------------------------------------------------ 读数据
    def refresh(self, force=False):
        import booking_api as BA
        from erp_widgets import BusyDialog
        if not BA.ready(self.settings):
            self.hero.set_sub(tr("还没连预约平台（系统设置 → 数据连接 → 预约平台）"))
            return
        reuse = (not force) and self.ctx is not None and time.time() - self.ctx_t < CTX_MAX_AGE
        res, err = BusyDialog.run(self.win, tr("门店申请"), tr("正在读门店申请和这周报价"),
                                  lambda: self._work(reuse))
        self._loaded_once = True
        if err:
            self.hero.set_sub(tr("读不了门店申请：{0}", err))
            self.win.log(tr("⚠ 门店申请读不了：{0}", err))
            return
        self._apply(res)

    def _work(self, reuse):
        """后台：申请 + （Griglia、这周报价、直厂）+ 数据库里查新条码"""
        reqs = PR.fetch(self.settings, "open,info,answered", 60)
        ctx = self.ctx if reuse else self._load_ctx()
        eans = {PR._ean(r.get("ean")) for r in reqs if r.get("status") in ("open", "info")}
        new = [e for e in eans if e and e not in self.db]
        if new:
            self.db.update(self._db_info(new))
        return reqs, ctx

    def _load_ctx(self):
        import commands as CMD
        import griglia as G
        import workspace as W
        import scanner_catalog as SC
        from erp_widgets import excel_call
        root = self.settings.get("wholesale_root") or ""
        grid, quotes = None, {}
        if root:
            def read():
                return G.load_for(root), CMD.read_quotes(self.settings, W.price_dir(root), lambda *_: None) or {}
            try:
                grid, quotes = excel_call(read)
            except Exception as e:
                self.win.bridge.log.emit(tr("⚠ 门店申请：Griglia / 报价读不了：{0}", e))
        scheda = SC.direct_index(self.settings, log=lambda *_: None)
        offers = PR.offers_index(quotes)
        best = {}
        for e, os_ in offers.items():
            sup, p, pk = min(os_, key=lambda o: o[1])
            best[e] = {"price": p, "pack": pk, "supplier": sup, "desc": ""}
        names = {}
        for sup, qs in quotes.items():
            for k, q in (qs or {}).items():
                d = str((q or {}).get("desc") or "").strip()
                if d and not d.startswith("="):
                    names.setdefault(PR._ean(k), d)
        for e, b in best.items():
            b["desc"] = names.get(e, "")
        best["_offers"] = offers
        self.ctx_t = time.time()
        return {"grid": grid, "best": best, "scheda": scheda}

    def _db_info(self, eans):
        """数据库（只读）：名称 / 编号 / 箱规 / 直厂 + 每家店 30 天销量"""
        import griglia_db as D
        import reorder_service as R
        import scanner_catalog as SC
        out = {}
        try:
            db = R.make_db(self.settings.get("db") or None)
        except Exception:
            return out
        for e in eans:
            try:
                out[e] = D.db_lookup(db, e) or {}
            except Exception:
                out[e] = {}
        codes = [x["code"] for x in out.values() if x.get("code")]
        if codes:
            for sid in (1, 2, 3, 4, 5):
                try:
                    nums = SC.light_numbers(db, sid, codes)
                except Exception:
                    continue
                for x in out.values():
                    n = nums.get(str(x.get("code")))
                    if n is not None:
                        x.setdefault("sales", {})[sid] = (n.get("stock"), n.get("s30"))
        return out

    def _apply(self, res):
        self.reqs, self.ctx = res
        self.groups = PR.group(self.reqs)
        self.checks = {}
        c = self.ctx or {}
        for g in self.groups:
            if g["status"] != "answered":
                self.checks[g["ean"]] = PR.checks(g, c.get("grid"), c.get("best"), c.get("scheda"), self.db)
        n = {k: 0 for k, _l in FILTERS}
        for g in self.groups:
            n[g["status"]] += 1
            n["all"] += 1
        for k, b in self.chips.items():
            b.set_count(n[k] if k != "all" else None)
        self._set_count(n["open"])
        late = sum(1 for g in self.groups if g["late"])
        self.hero.set_sub(tr("{0} 个待回复", n["open"]) + (tr(" · {0} 个超过 2 个工作日", late) if late else "")
                          + tr(" · {0} 个等门店补充", n["info"]) + tr(" · 最近 60 天共 {0} 个", n["all"]))
        keep = self.cur["ean"] if self.cur else None
        self.ctl.render()
        if keep:
            self._select(keep)
        elif self.ctl.visible:
            self.t.selectRow(0)
        else:
            self._show(None)

    # ------------------------------------------------------------------ 表格
    def _set_filter(self, k):
        self.filter = k
        self.ctl.render()
        if self.ctl.visible:
            self.t.selectRow(0)
        else:
            self._show(None)

    def _hidden(self, i):
        g = self.groups[i]
        if self.filter != "all" and g["status"] != self.filter:
            return True
        q = self.search.text().strip().lower()
        if not q:
            return False
        hay = " ".join([g["ean"], g["name"], (self.checks.get(g["ean"]) or {}).get("name") or ""]
                       + [PR.store_name(s) for s in g["stores"]] + [str(r.get("note") or "") for r in g["reqs"]])
        return q not in hay.lower()

    def _name(self, g):
        return g["name"] or (self.checks.get(g["ean"]) or {}).get("name") or tr("（门店不知道是什么）")

    def _said(self, g):
        rs = g["open"] or g["reqs"]
        # 2026-11-27 起扫码枪不再选原因（门店都选「其他」再自己写）：「其他」不写出来
        reasons = sorted({tr(PR.REASON_ZH.get(r.get("reason"), r.get("reason") or "")) for r in rs if r.get("reason") != "other"})
        notes = [str(r.get("note") or "") for r in rs if r.get("note")]
        return " · ".join(reasons + notes[:1])

    def _text(self, i, c):
        g = self.groups[i]
        if c == 0:
            if g["status"] == "answered":
                k = g["reqs"][-1].get("answerKind")
                return tr(PR.ANSWER_ZH.get(k, "已回复"))
            return tr(PR.STATUS_ZH[g["status"]])
        if c == 1:
            return self._name(g)
        if c == 2:
            return g["ean"]
        if c == 3:
            return "、".join(PR.store_name(s) for s in g["stores"])
        if c == 4:
            return str(g["boxes"]) if g["boxes"] else ""
        if c == 5:
            return self._said(g)
        if c == 6:
            return (tr("{0} 天", g["waited"]) if g["waited"] else tr("今天")) if g["status"] == "open" else ""
        if c == 7:
            ch = self.checks.get(g["ean"])
            return tr(PR.ANSWER_ZH[ch["suggest"]]) if ch else ""
        return ""

    def _key(self, i, c):
        g = self.groups[i]
        if c == 4:
            return g["boxes"]
        if c == 6:
            return g["waited"]
        return self._text(i, c)

    def _fill(self, i, k):
        from main import table_set_row
        g = self.groups[i]
        table_set_row(self.t, k, [self._text(i, c) for c in range(len(HEADS))])
        tone = {"open": "err" if g["late"] else "accent", "info": "warn", "answered": "faint"}[g["status"]]
        it = self.t.item(k, 0)
        if it is not None:
            it.setForeground(QColor(_T(tone)))
            f = it.font()
            f.setBold(g["status"] != "answered")
            it.setFont(f)
        for c in (4, 6):
            x = self.t.item(k, c)
            if x is not None:
                x.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if g["late"]:
            x = self.t.item(k, 6)
            if x is not None:
                x.setForeground(QColor(_T("err")))
                x.setToolTip(tr("超过 {0} 个工作日没回复", PR.LATE_DAYS))
        if len(g["stores"]) > 1:
            x = self.t.item(k, 3)
            if x is not None:
                x.setToolTip(tr("{0} 家店在问", len(g["stores"])))

    def _picked(self):
        rows = self.t.selectionModel().selectedRows() if self.t.selectionModel() else []
        if not rows:
            return None
        k = rows[0].row()
        return self.groups[self.ctl.visible[k]] if 0 <= k < len(self.ctl.visible) else None

    def _sel(self):
        self._show(self._picked())

    def _select(self, ean):
        for k, i in enumerate(self.ctl.visible):
            if self.groups[i]["ean"] == ean:
                self.t.selectRow(k)
                return
        self._show(None)

    # ------------------------------------------------------------------ 右边详情
    def _clear(self):
        while self.dv.count():
            it = self.dv.takeAt(0)
            if it.widget() is not None:
                it.widget().hide()
                it.widget().deleteLater()
            elif it.layout() is not None:
                QWidget().setLayout(it.layout())

    def _lbl(self, text, size=13, tone=None, bold=False, wrap=True):
        lb = QLabel(text)
        lb.setWordWrap(wrap)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        css = f"font-size:{size}px;"
        if tone:
            css += f"color:{_T(tone)};"
        if bold:
            css += "font-weight:600;"
        lb.setStyleSheet(css)
        return lb

    def _section(self, title):
        from page_kit import Card
        c = Card(title, margins=(14, 12, 14, 12))
        self.dv.addWidget(c)
        return c.v

    def _show(self, g):
        import product_images as PI
        self.cur = g
        self._clear()
        if g is None:
            self.dv.addWidget(self._lbl(tr("选一行看门店说的和程序查到的"), 13, "faint"))
            self.dv.addStretch()
            return
        ch = self.checks.get(g["ean"]) or {}
        head = QHBoxLayout()
        img = QLabel()
        img.setFixedSize(84, 84)
        img.setAlignment(Qt.AlignCenter)
        PI.set_label_image(img, g["ean"], 84, self.settings)
        head.addWidget(img, 0, Qt.AlignTop)
        hv = QVBoxLayout()
        hv.addWidget(self._lbl(self._name(g), 15, bold=True))
        hv.addWidget(self._lbl(g["ean"] + "  ·  " + "、".join(tr(PR.ITEM_ZH.get(k, k)) for k in g["kinds"]), 12, "faint"))
        st = tr(PR.STATUS_ZH[g["status"]])
        if g["status"] == "open":
            st += tr(" · 等了 {0} 个工作日", g["waited"]) if g["waited"] else tr(" · 今天提的")
        hv.addWidget(self._lbl(st, 12, "err" if g["late"] else ("warn" if g["status"] == "info" else "accent"), True))
        hv.addStretch()
        head.addLayout(hv, 1)
        hw = QWidget()
        hw.setLayout(head)
        self.dv.addWidget(hw)

        if g["open"]:
            v = self._section(tr("回复门店"))
            if ch:
                v.addWidget(self._lbl(tr("建议：{0}", tr(PR.ANSWER_ZH[ch["suggest"]])) + " · " + tr(ch["why"]), 12, "accent"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            tips = {"use": "门店扫的是新条码 / 有一样的商品：以后扫这个条码显示该订的那个",
                    "griglia": "在程序里的 Griglia 新加这个商品（先填商品资料、过检查）",
                    "once": "这周照订，不加进 Griglia（算非 Griglia 商品）",
                    "direct": "门店自己向直厂订",
                    "no": "写原因；以后再扫这个条码，门店看得到这个回复",
                    "info": "门店在扫码枪上补一句或补照片，申请回到待回复"}
            for n, kind in enumerate(("use", "griglia", "once", "direct", "no", "info")):
                b = QPushButton(tr(PR.ANSWER_ZH[kind]))
                if ch and ch["suggest"] == kind:
                    b.setObjectName("Primary")
                b.setToolTip(tr(tips[kind]))
                b.clicked.connect(lambda _=False, k=kind: self._answer(k))
                grid.addWidget(b, n // 2, n % 2)
            v.addLayout(grid)
        v = self._section(tr("门店说的"))
        for r in g["reqs"]:
            line = " · ".join(x for x in (
                PR.store_name(r.get("entityCode")),
                tr(PR.REASON_ZH.get(r.get("reason"), r.get("reason") or "")) if r.get("reason") != "other" else "",
                tr("{0} 箱", r["boxes"]) if r.get("boxes") else "", (r.get("createdAt") or "")[:16].replace("T", " ")) if x)
            v.addWidget(self._lbl(line, 13, bold=r.get("status") in ("open", "info")))
            msgs = r.get("messages") or []
            if msgs:                    # 整段对话（平台 0009 起）：门店说的、办公室回的，按先后
                for m in msgs:
                    who = tr("门店") if m.get("by") != "office" else tr("办公室")
                    extra = " · ".join(x for x in ((m.get("at") or "")[5:16].replace("T", " "),
                                                    tr("{0} 箱", m["boxes"]) if m.get("boxes") else "",
                                                    tr("照片") if m.get("photo") else "") if x)
                    v.addWidget(self._lbl(f"{who}：{m.get('text') or '—'}" + (f"（{extra}）" if extra else ""), 12,
                                          "accent" if m.get("by") != "office" else "faint"))
            elif r.get("note"):
                v.addWidget(self._lbl("“" + r["note"] + "”", 12))
            if r.get("storeReply") and not msgs:
                v.addWidget(self._lbl(tr("门店补充：{0}", r["storeReply"]), 12, "accent"))
            if r.get("status") in ("answered", "info") and r.get("answer") and not msgs:
                v.addWidget(self._lbl(tr("已回复（{0}）：{1}", tr(PR.ANSWER_ZH.get(r.get("answerKind"), "")), r["answer"]),
                                      12, "faint"))
            if r.get("hasPhoto"):
                b = QPushButton(tr("门店照片"))
                b.setObjectName("Link")
                b.clicked.connect(lambda _=False, rid=r["id"], s=PR.store_name(r.get("entityCode")): self._photo(rid, s))
                v.addWidget(b, 0, Qt.AlignLeft)

        if ch:
            v = self._section(tr("程序查到的"))
            gs = {"griglia": tr("在 Griglia 里"), "off": tr("Griglia 已停用"), "": tr("不在 Griglia")}[ch["grid"]]
            v.addWidget(self._lbl(tr("Griglia：{0}", gs), 13))
            d = ch.get("db") or {}
            if d:
                v.addWidget(self._lbl(tr("数据库：{0} · 编号 {1}", d.get("name") or "?", d.get("code") or "?")
                                      + (tr(" · 每箱 {0}", d["pack"]) if d.get("pack") else ""), 13))
                sales = d.get("sales") or {}
                if sales:
                    txt = " · ".join(f"{PR.store_name(s)} {int(v_[1] or 0)}" for s, v_ in sorted(sales.items()))
                    v.addWidget(self._lbl(tr("30 天销量：{0}", txt), 12, "faint"))
            else:
                v.addWidget(self._lbl(tr("数据库：没有这个条码"), 13, "warn"))
            if ch["offers"]:
                for sup, p, pk in ch["offers"][:4]:
                    v.addWidget(self._lbl(f"{sup} · €{p:.2f}" + (tr(" · 每箱 {0}", int(pk) if pk and float(pk).is_integer() else pk)
                                                               if pk else ""), 13))
            else:
                v.addWidget(self._lbl(tr("这周批发商：都没有"), 13, "warn"))
            v.addWidget(self._lbl(tr("直厂：{0}", ch["direct"]) if ch["direct"] else tr("直厂：没有"), 13))
            if ch["similar"]:
                v.addWidget(self._lbl(tr("Griglia 里最像的："), 12, "faint"))
                for s in ch["similar"]:
                    v.addWidget(self._lbl(f"{s['score']:.0%} · {s['name']} · {s['ean']}", 12,
                                          "ok" if s["score"] >= PR.SURE else None))

        answered = [r for r in g["reqs"] if r.get("status") in ("answered", "info") and r.get("answerKind")]
        if answered:
            b = QPushButton(tr("撤销回复"))
            b.setToolTip(tr("回复去掉、申请回到待回复；放进门店清单的箱数改回原来的"))
            b.clicked.connect(lambda: self._reopen(g))
            self.dv.addWidget(b, 0, Qt.AlignLeft)
        self.dv.addStretch()
        QTimer.singleShot(0, lambda: self.side.verticalScrollBar().setValue(0))

    def _photo(self, rid, store):
        from erp_widgets import BusyDialog
        data, err = BusyDialog.run(self.win, tr("门店照片"), tr("正在下载"), lambda: PR.photo(self.settings, rid))
        if err or not data:
            QMessageBox.information(self.win, tr("门店照片"), tr("下载不了照片"))
            return
        pm = QPixmap()
        pm.loadFromData(data)
        dlg = QDialog(self.win)
        dlg.setWindowTitle(tr("门店照片 · {0}", store))
        lay = QVBoxLayout(dlg)
        lb = QLabel()
        lb.setPixmap(pm.scaled(720, 720, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(lb)
        dlg.exec()

    # ------------------------------------------------------------------ 回复
    def _answer(self, kind):
        g = self.cur
        if not g or not g["open"]:
            return
        ch = self.checks.get(g["ean"]) or {}
        if kind == "griglia" and not self._add_to_griglia(g, ch):
            return
        dlg = AnswerDialog(self, g, kind, ch)
        if dlg.exec() != QDialog.Accepted:
            return
        self._send(g, kind, dlg.result_data())

    def _add_to_griglia(self, g, ch):
        cp = getattr(self.win, "grid_db", None)
        if cp is None or getattr(cp, "cat", None) is None:
            QMessageBox.information(self.win, tr("加进 Griglia"),
                                    tr("程序里还没有 Griglia：先在「Griglia 商品目录」导入一次"))
            return False
        x = cp.cat.by_ean.get(g["ean"])
        if x is not None and x.get("status") not in ("off", "replaced"):
            return True                      # 已经在程序的 Griglia 里了（别的店先问过）：直接回复
        from griglia_edit_page import ProductDialog
        d = dict(ch.get("db") or {})
        d.pop("sales", None)
        if not d.get("name"):
            d["name"] = ch.get("name") or g["name"]
        d["ean"] = g["ean"]
        return ProductDialog(cp, None, prefill=d).exec() == QDialog.Accepted

    def _send(self, g, kind, data):
        from erp_widgets import BusyDialog
        by = PR.by_name()
        todo = [(r, data["boxes"].get(r["id"]) or None) for r in g["open"]]

        def work():
            done, errs = [], []
            for r, b in todo:
                try:
                    PR.answer(self.settings, r["id"], kind, data["text"], ean=data.get("ean"), name=data.get("name"),
                              boxes=b if kind in PR.LIST_KINDS else None, by=by)
                    done.append(r["id"])
                except Exception as e:
                    errs.append(f"{PR.store_name(r.get('entityCode'))}：{e}")
            return done, errs
        res, err = BusyDialog.run(self.win, tr("回复门店"), tr("正在发送"), work)
        done, errs = res or ([], [str(err)] if err else [])
        if errs:
            QMessageBox.warning(self.win, tr("回复门店"), tr("这几家没发出去：") + "\n" + "\n".join(errs))
        if done:
            names = "、".join(PR.store_name(r.get("entityCode")) for r, _b in todo if r["id"] in done)
            self.win.log(tr("门店申请 {0} {1}：{2}（{3}）", g["ean"], self._name(g), tr(PR.ANSWER_ZH[kind]), names))

            def undo():
                BusyDialog.run(self.win, tr("撤销回复"), tr("正在撤销"),
                               lambda: [PR.reopen(self.settings, rid) for rid in done])
                self.refresh(force=False)
                self.win.show_toast(tr("已撤销回复"))
            self.win.show_toast(tr("已回复 {0} 家门店：{1}", len(done), tr(PR.ANSWER_ZH[kind])), action=(tr("撤销"), undo))
        self.refresh(force=False)

    def _reopen(self, g):
        from erp_widgets import BusyDialog
        rs = [r for r in g["reqs"] if r.get("status") in ("answered", "info") and r.get("answerKind")]
        listed = [r for r in rs if r.get("addedToList")]
        msg = tr("撤销给 {0} 的回复，申请回到待回复。", "、".join(PR.store_name(r.get("entityCode")) for r in rs))
        if listed:
            msg += "\n" + tr("放进清单的箱数改回原来的：{0}", "、".join(PR.store_name(r.get("entityCode")) for r in listed))
        if any(r.get("answerKind") == "griglia" for r in rs):
            msg += "\n" + tr("加进 Griglia 的商品不会删掉（要删在「Griglia 改动记录」里撤销）。")
        if QMessageBox.question(self.win, tr("撤销回复"), msg) != QMessageBox.Yes:
            return
        _r, err = BusyDialog.run(self.win, tr("撤销回复"), tr("正在撤销"),
                                 lambda: [PR.reopen(self.settings, r["id"]) for r in rs])
        if err:
            QMessageBox.warning(self.win, tr("撤销回复"), str(err))
        self.refresh(force=False)

    def restyle(self):
        if self.page is not None:
            self.ctl.render()
            self._show(self.cur)


class AnswerDialog(QDialog):
    """一种回复的细节：换成哪个商品 / 不做的原因 / 要问什么 · 放进清单几箱（每家店）· 发给门店的话（意大利文，可以改）"""

    def __init__(self, page, g, kind, ch):
        from erp_widgets import fit_to_screen
        super().__init__(page.win)
        self.page, self.g, self.kind, self.ch = page, g, kind, ch
        self.setWindowTitle(tr(PR.ANSWER_ZH[kind]) + " · " + page._name(g)[:40])
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel(tr("回复 {0}", "、".join(PR.store_name(r.get("entityCode")) for r in g["open"]))))
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        row = 0
        self.target = self.direct = self.reason = None
        if kind == "use":
            self.target = QComboBox()
            self.target.setEditable(True)
            for s in ch.get("similar") or []:
                self.target.addItem(f"{s['name']} · {s['ean']} · {s['score']:.0%}", (s["ean"], s["name"]))
            self.target.lineEdit().setPlaceholderText(tr("Griglia 商品的条码"))
            self.target.currentTextChanged.connect(lambda _t: self._regen())
            form.addWidget(QLabel(tr("该订的商品")), row, 0)
            form.addWidget(self.target, row, 1)
            row += 1
        elif kind == "direct":
            self.direct = QLineEdit(ch.get("direct") or "")
            self.direct.textChanged.connect(lambda _t: self._regen())
            form.addWidget(QLabel(tr("直厂")), row, 0)
            form.addWidget(self.direct, row, 1)
            row += 1
        elif kind in ("no", "info"):
            self.reason = QComboBox()
            for zh, it in (PR.NO_REASONS if kind == "no" else PR.INFO_ASKS):
                self.reason.addItem(tr(zh), it)
            self.reason.addItem(tr("其他（下面直接写）"), "")
            self.reason.currentIndexChanged.connect(lambda _i: self._regen())
            form.addWidget(QLabel(tr("原因") if kind == "no" else tr("要问什么")), row, 0)
            form.addWidget(self.reason, row, 1)
            row += 1
        lay.addLayout(form)

        self.spins, self.warns = {}, {}
        self.put = None
        if kind in PR.LIST_KINDS:
            self.put = QCheckBox(tr("把门店要的箱数放进这周清单"))
            self.put.setChecked(any(r.get("boxes") for r in g["open"]))
            self.put.toggled.connect(lambda _b: self._regen())
            lay.addWidget(self.put)
            box = QGridLayout()
            box.setHorizontalSpacing(10)
            for n, r in enumerate(g["open"]):
                box.addWidget(QLabel(PR.store_name(r.get("entityCode"))), n, 0)
                sp = QSpinBox()
                sp.setRange(0, 99)
                sp.setValue(int(r.get("boxes") or 0))
                sp.setSuffix(tr(" 箱"))
                sp.valueChanged.connect(lambda _v: self._regen())
                box.addWidget(sp, n, 1)
                w = QLabel("")
                w.setWordWrap(True)
                box.addWidget(w, n, 2)
                self.spins[r["id"]], self.warns[r["id"]] = sp, w
            box.setColumnStretch(2, 1)
            lay.addLayout(box)

        lay.addWidget(QLabel(tr("发给门店的话（意大利文，门店扫码枪上看得到）")))
        self.text = QPlainTextEdit()
        self.text.setFixedHeight(84)
        self._auto = ""
        lay.addWidget(self.text)
        bb = QDialogButtonBox()
        self.ok = bb.addButton(tr("发送回复"), QDialogButtonBox.AcceptRole)
        self.ok.setObjectName("Primary")
        bb.addButton(tr("取消"), QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._regen()
        fit_to_screen(self)

    def _target(self):
        if self.target is None:
            return None, None
        d = self.target.currentData()
        txt = self.target.currentText().strip()
        if d and txt == self.target.itemText(self.target.currentIndex()):
            return d
        e = PR._ean(txt.split("·")[0] if not txt.replace(" ", "").isdigit() else txt)
        grid = ((self.page.ctx or {}).get("grid") or {}).get("items") or {}
        x = next((v for k, v in grid.items() if PR._ean(k) == e), None)
        return (e, (x or {}).get("name") or "") if e.isdigit() and len(e) >= 6 else (None, None)

    def _price(self):
        """放进清单的商品每箱几件、件价（合理性检查用）"""
        best = (self.page.ctx or {}).get("best") or {}
        e = self._target()[0] if self.kind == "use" else self.g["ean"]
        b = best.get(e or "") or {}
        pack = b.get("pack") or (self.ch.get("db") or {}).get("pack")
        return pack, b.get("price")

    def boxes(self):
        if not self.put or not self.put.isChecked():
            return {}
        return {rid: sp.value() for rid, sp in self.spins.items() if sp.value() > 0}

    def _regen(self):
        pack, price = self._price()
        self.block = []
        for rid, sp in self.spins.items():
            lv, txt = PR.box_check(sp.value() if self.put and self.put.isChecked() else 0, pack, price)
            w = self.warns[rid]
            w.setText(tr(txt) if txt else "")
            w.setStyleSheet(f"color:{_T('err' if lv == 'block' else ('warn' if lv == 'warn' else 'faint'))};")
            if lv == "block":
                self.block.append(rid)
        bx = self.boxes()
        one = next(iter(bx.values())) if len(bx) == 1 else None
        if self.kind == "use":
            arg = dict(name=self._target()[1] or self._target()[0] or "")
        elif self.kind == "direct":
            arg = dict(supplier=self.direct.text().strip())
        elif self.kind in ("no", "info"):
            arg = dict(reason=self.reason.currentData() or "")
        else:
            arg = {}
        auto = PR.message(self.kind, boxes=one, **arg)
        if self.text.toPlainText().strip() in ("", self._auto):
            self.text.setPlainText(auto)
        self._auto = auto

    def result_data(self):
        e, n = self._target()
        return {"text": self.text.toPlainText().strip(), "ean": e, "name": n or (self.direct.text().strip() if self.direct else None),
                "boxes": self.boxes()}

    def _accept(self):
        if not self.text.toPlainText().strip():
            QMessageBox.warning(self, self.windowTitle(), tr("写一句发给门店的话"))
            return
        if self.kind == "use" and not self._target()[0]:
            QMessageBox.warning(self, self.windowTitle(), tr("选一个该订的商品（或写它的条码）"))
            return
        if self.block:
            QMessageBox.warning(self, self.windowTitle(), tr("箱数太多，先改小（可能把件数写成了箱数）"))
            return
        bx = self.boxes()
        msg = tr("回复 {0} 家门店：{1}", len(self.g["open"]), tr(PR.ANSWER_ZH[self.kind]))
        if bx:
            msg += "\n\n" + tr("放进这周的扫码枪清单：") + "\n" + "\n".join(
                f"• {PR.store_name(r.get('entityCode'))}：{bx[r['id']]} " + tr("箱") for r in self.g["open"] if r["id"] in bx)
        warns = [self.warns[rid].text() for rid in bx if self.warns[rid].text() and "≈" in self.warns[rid].text()
                 and self.warns[rid].styleSheet().find(_T("warn")) >= 0]
        if warns:
            msg += "\n\n" + tr("请注意：") + "\n" + "\n".join("• " + w for w in warns)
        msg += "\n\n" + tr("门店扫码枪上马上看得到这句话；做完右下角能撤销。")
        if QMessageBox.question(self, self.windowTitle(), msg) == QMessageBox.Yes:
            self.accept()
