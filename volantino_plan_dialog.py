# -*- coding: utf-8 -*-
"""
DETERSIVO 第 1 步「VOLANTINO 备货」（2026-11-22）：选这一期的 VOLANTINO 名单 → 程序按以前几期 VOLANTINO 各门店的销量算出
每家店要备几箱（volantino_plan.compute）→ 窗口里一家店一个商品一行，可以改「改成」的箱数 →「写进门店清单」先确认 → 改 DETER1–5
（原文件备份，可撤销）。之后照常「开始检查」：这些行判断后面写「VOLANTINO」，照清单订、一定要审核。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QHeaderView, QAbstractItemView,
                               QMessageBox, QComboBox, QFileDialog, QLineEdit)

from i18n import tr
import volantino_plan as VP

BASIS = {"hist": "以前 {0} 期", "est": "估算 ×{0:g}", "none": "最近没卖"}
VIEWS = (("change", "要改的"), ("all", "全部"), ("est", "估算的"), ("warn", "有提醒的"))
# 列：(字段, 表头, 宽度)
COLS = [("on", "", 34), ("store", "门店", 96), ("name", "商品", 240), ("ean", "条码", 120), ("basis", "依据", 96),
        ("per_day", "促销每天", 76), ("expected", "预计卖", 70), ("stock", "库存", 64), ("transit", "在途（箱）", 86),
        ("pack", "每箱", 56), ("list", "清单原来", 76), ("sugg", "建议", 60), ("new", "改成", 104), ("amount", "金额", 86),
        ("warn", "提醒", 160)]
F = [c[0] for c in COLS]
NUM = {F.index(k) for k in ("per_day", "expected", "stock", "transit", "pack", "list", "sugg", "new", "amount")}


def _ws_root(settings):
    return (settings or {}).get("wholesale_root") or ""


def open_plan(win):
    """入口：选名单 → 算 → 窗口"""
    import reorder_service as R
    from erp_widgets import BusyDialog, excel_call
    settings = getattr(win, "_S", None) or {}
    ui = win.order_check.ui
    root = VP.promo_root(settings)
    start = os.path.dirname(VP.newest_list(root)) if root else ""
    path, _ = QFileDialog.getOpenFileName(win, tr("选这一期的 VOLANTINO 名单"), start or os.path.expanduser("~"),
                                          "Excel (*.xlsx *.xlsm)")
    if not path:
        return
    ws = _ws_root(settings)
    if not ws or not os.path.isdir(ws):
        QMessageBox.warning(win, tr("VOLANTINO 备货"), tr("还没设好 DETERSIVO 工作文件夹"))
        return
    if not root:
        QMessageBox.warning(win, tr("VOLANTINO 备货"), tr("找不到促销文件夹（同步文件夹里的 5 PROMOZIONE MESILE\\PROMO MESILE），"
                                                       "没有以前几期的名单就算不了"))
        return
    log = getattr(win, "log", print)

    def job():
        import commands as CMD
        import order_record as REC
        import price_check as PC
        import workspace as W
        new = VP.read_list(path, marks=True)
        new["file"] = path
        if not new.get("start") or not new.get("end"):
            raise ValueError(tr("名单里看不出促销是哪几天（start / end 两列）"))
        db = R.make_db(settings.get("db") or None)
        try:
            hist = VP.history(root, new, db, log=log)
            files = CMD.read_quote_files(settings, W.price_dir(ws), log) if os.path.isdir(W.price_dir(ws)) else {}
            quotes = PC.best_quotes(files)
            paths = W.store_list_files(ws)
            lists = {}
            for sid, p in paths.items():
                try:
                    lists[sid] = R.parse_order_file(p)["rows"]
                except Exception as e:
                    log(tr("⚠ 门店清单读不了：{0}（{1}）", os.path.basename(p), e))
            try:
                transit = REC.cached(settings) if REC.tracking_folder(settings) else None
            except Exception:
                transit = None
            on = bool((settings.get("volantino") or {}).get("plan_deduct", False))
            res = VP.compute(db, new, hist, quotes, lists, transit, log=log, deduct_transit=on)
            res["deduct"] = on
            res["paths"], res["file"] = paths, path
            return res
        finally:
            try:
                db.engine.dispose()
            except Exception:
                pass

    res, e = BusyDialog.run(win, tr("VOLANTINO 备货"), tr("正在读以前几期的 VOLANTINO 和各门店的销量…"),
                            lambda: excel_call(job))
    if e:
        QMessageBox.warning(win, tr("VOLANTINO 备货"), str(e))
        return
    log(tr("[VOLANTINO 备货] {0}：{1} – {2}，{3} 天；用了以前 {4} 期；放大倍数 {5}",
           res["name"], res["start"], res["end"], res["days"], len(res["periods"]),
           " · ".join(f"{R.STORES.get(s, s)} ×{v:g}" for s, v in res["lift"].items())))
    PlanDialog(win, settings, ui, res).exec()


class PlanDialog(QDialog):
    def __init__(self, win, settings, ui, res):
        super().__init__(win)
        import reorder_service as R
        import settings as ST
        import product_images as PI
        from erp_widgets import TableSortFilter, fit_to_screen, StepperDelegate, CountButton
        self.win, self.settings, self.ui, self.res = win, settings, ui, res
        self.rows = res["rows"]
        for r in self.rows:
            r["_on"] = r["new"] > (r["list"] or 0)
        self._undo = None
        self.setObjectName("Dialog")
        self.setWindowTitle(tr("VOLANTINO 备货"))
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel(tr("VOLANTINO 备货") + "  ⓘ")
        t.setObjectName("Section")
        per = "\n".join(f"· {n}（{s} – {e}）" for n, s, e in res["periods"]) or "—"
        t.setToolTip(tr("每家店每个商品：以前上 VOLANTINO 时每天卖几件（最近 {0} 期的平均）× 这一期 {1} 天 = 预计卖几件；"
                        "减掉现在库存，按数据库箱规换成箱，要订就至少 1 箱。\n"
                        "以前没上过的：最近 90 天平时每天卖几件 × 这家店的放大倍数（别的商品促销期比平时多卖几倍），标「估算」。\n"
                        "在途：勾「扣掉在途」这里就扣（写进清单时注明，审核不再扣），不勾就留给「开始检查」扣。\n"
                        "清单里已经写了更多的，照门店写的（不砍）。\n\n用到的以前几期：\n{2}", VP.MAX_PERIODS, res["days"], per))
        head.addWidget(t)
        info = QLabel(tr("{0} · {1} – {2} · {3} 天", res["name"], res["start"][8:10] + "/" + res["start"][5:7],
                         res["end"][8:10] + "/" + res["end"][5:7], res["days"]))
        info.setObjectName("Muted")
        head.addWidget(info)
        head.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("名称 / 条码"))
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(lambda _t: self.ctl.render())
        head.addWidget(self.search)
        self.store_box = QComboBox()
        self.store_box.addItem(tr("全部门店"), 0)
        for sid in sorted({r["store"] for r in self.rows}):
            self.store_box.addItem(R.STORES.get(sid, str(sid)), sid)
        self.store_box.currentIndexChanged.connect(lambda _i: self.ctl.render())
        head.addWidget(self.store_box)
        self.view = QComboBox()
        for k, lab in VIEWS:
            self.view.addItem(tr(lab), k)
        self.view.currentIndexChanged.connect(lambda _i: self.ctl.render())
        head.addWidget(self.view)
        from PySide6.QtWidgets import QCheckBox
        self.deduct = QCheckBox(tr("扣掉在途"))
        self.deduct.setToolTip(tr("勾上：建议箱数再减掉已经订了还没到的货（在途件数），写进门店清单时注明，"
                                  "「开始检查」时这几行不再扣一次。\n不勾：这里不扣，「开始检查」时照常扣掉。\n人手改过「改成」的行不变"))
        self.deduct.setChecked(bool(res.get("deduct", False)))
        self.deduct.toggled.connect(self._toggle_deduct)
        head.addWidget(self.deduct)
        v.addLayout(head)

        self.t = ui.make_table([tr(h) if h else "" for _f, h, _w in COLS], numeric_cols=tuple(NUM))
        self.t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.t.setEditTriggers(QAbstractItemView.DoubleClicked)
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, (_f, _h, w) in enumerate(COLS):
            self.t.setColumnWidth(c, w)
        self._filling = False
        self.ctl = TableSortFilter(self.t, lambda: len(self.rows), self._text, self._fill, key=self._key,
                                   hide=self._hidden, numeric=NUM)
        self.ctl.remember(settings.setdefault("volantino", {}), "plan_cols", lambda: ST.save_settings(settings), F,
                          default_hidden=("ean",))
        PI.add_thumbs(self.t, F.index("name"), F.index("ean"), settings)
        StepperDelegate.install(self.t, F.index("new"), on_step=self._step,
                                value=lambda idx: self._val(idx))
        self.t.itemChanged.connect(self._changed)
        v.addWidget(self.t, 1)

        foot = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        foot.addWidget(self.status, 1)
        self.b_skip = CountButton(tr("批发商没有的商品"), len(res["skipped"]))
        self.b_skip.setToolTip(tr("名单里这周四家批发商报价都没有的（小家电、玩具等）、数据库里没有的：不放进门店清单"))
        self.b_skip.clicked.connect(self._show_skipped)
        self.b_skip.setVisible(bool(res["skipped"]))
        foot.addWidget(self.b_skip)
        b_exp = QPushButton(tr("导出"))
        b_exp.clicked.connect(self._export)
        foot.addWidget(b_exp)
        self.b_undo = QPushButton(tr("撤销"))
        self.b_undo.setToolTip(tr("把门店清单换回写之前的样子（之后有人改过的不换）"))
        self.b_undo.clicked.connect(self.undo)
        self.b_undo.hide()
        foot.addWidget(self.b_undo)
        self.b_go = CountButton(tr("写进门店清单"), 0)
        self.b_go.setObjectName("Primary")
        self.b_go.clicked.connect(self.apply)
        foot.addWidget(self.b_go)
        b_close = QPushButton(tr("关闭"))
        b_close.clicked.connect(self.accept)
        foot.addWidget(b_close)
        v.addLayout(foot)
        self.resize(1300, 720)
        fit_to_screen(self)
        self.ctl.set_sort(F.index("amount"), desc=True)
        self.ctl.render()
        self._update()

    # ---------- 表格 ----------
    def _hidden(self, i):
        r = self.rows[i]
        sid = self.store_box.currentData()
        if sid and r["store"] != sid:
            return True
        k = self.view.currentData()
        if k == "change" and not r["new"] > (r["list"] or 0):
            return True
        if k == "est" and r["basis"] != "est":
            return True
        if k == "warn" and not r["warn"]:
            return True
        q = self.search.text().strip().lower()
        return bool(q) and q not in (r["name"] or "").lower() and q not in r["ean"]

    def _text(self, i, c):
        import reorder_service as R
        r = self.rows[i]
        f = F[c]
        if f == "on":
            return ""
        if f == "store":
            return R.STORES.get(r["store"], str(r["store"]))
        if f == "basis":
            return tr(BASIS[r["basis"]], len(r["periods"]) if r["basis"] == "hist" else r["lift"])
        if f == "per_day":
            return f"{r['per_day']:.1f}"
        if f in ("expected", "stock", "transit", "pack"):
            return f"{r[f]:g}" if f != "expected" else f"{r[f]:.0f}"
        if f == "list":
            return "" if r["list"] is None else f"{r['list']:g}"
        if f in ("sugg", "new"):
            return str(r[f])
        if f == "amount":
            return f"€{r['amount']:,.2f}"
        if f == "warn":
            return " · ".join(r["warn"])
        return str(r.get(f) or "")

    def _key(self, i, c):
        r = self.rows[i]
        f = F[c]
        if f == "list":
            return -1 if r["list"] is None else r["list"]
        if f in ("per_day", "expected", "stock", "transit", "pack", "sugg", "new", "amount"):
            return r[f]
        return None

    def _fill(self, i, k):
        T = self.ui.T
        r = self.rows[i]
        self._filling = True
        try:
            self.ui.table_set_row(self.t, k, [self._text(i, c) for c in range(len(COLS))])
            ck = self.t.item(k, 0)
            ck.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            ck.setCheckState(Qt.Checked if r["_on"] else Qt.Unchecked)
            for c in range(1, len(COLS)):
                it = self.t.item(k, c)
                if it is not None and F[c] != "new":
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            it = self.t.item(k, F.index("new"))
            it.setFlags(it.flags() | Qt.ItemIsEditable)
            if r["new"] > (r["list"] or 0):
                it.setForeground(QColor(T("accent")))
                fn = it.font()
                fn.setBold(True)
                it.setFont(fn)
            if r["amount"] >= VP.BIG_LINE_EUR:
                self.t.item(k, F.index("amount")).setForeground(QColor(T("err")))
            if r["warn"]:
                self.t.item(k, F.index("warn")).setForeground(QColor(T("warn")))
            b = self.t.item(k, F.index("basis"))
            if r["basis"] == "hist":
                b.setToolTip("\n".join(tr("{0}（{1} – {2}）：促销期每天 {3:.1f} 件，促销前每天 {4:.1f} 件", *p) for p in r["periods"]))
            elif r["basis"] == "est":
                b.setForeground(QColor(T("warn")))
                b.setToolTip(tr("以前没上过 VOLANTINO：平时每天 {0:.1f} 件 × 这家店的放大倍数 {1:g}，请核对", r["normal"], r["lift"]))
            else:
                b.setForeground(QColor(T("faint")))
                b.setToolTip(tr("以前没上过 VOLANTINO，最近 90 天这家店也没卖过：不建议订"))
            lt = self.t.item(k, F.index("list"))
            if r["list"] is None:
                lt.setToolTip(tr("门店清单里没有，写进去时加在最后"))
            tr_ = self.t.item(k, F.index("transit"))
            if r["transit"]:
                tr_.setToolTip(tr("已经订了还没到：{0:g} 件，建议里已经扣掉", r.get("transit_pcs") or 0)
                               if self.deduct.isChecked() else tr("已经订了还没到：这里不扣，「开始检查」时照常扣掉"))
        finally:
            self._filling = False

    def _val(self, idx):
        i = self.ctl.row_at(idx.row())
        return self.rows[i]["new"] if i is not None else 0

    def _set_new(self, i, n):
        r = self.rows[i]
        r["new"] = max(0, int(n))
        r["edited"] = True
        VP.refresh_row(r)
        r["_on"] = r["new"] > (r["list"] or 0)
        self.ctl.update_row(i)
        self._update()

    def _toggle_deduct(self, on):
        import settings as ST
        VP.set_deduct(self.rows, on)
        for r in self.rows:
            r["_on"] = r["new"] > (r["list"] or 0)
        self.res["deduct"] = on
        self.settings.setdefault("volantino", {})["plan_deduct"] = bool(on)
        ST.save_settings(self.settings)
        self.ctl.render()
        self._update()

    def _step(self, idx, n):
        i = self.ctl.row_at(idx.row())
        if i is not None:
            self._set_new(i, n)

    def _changed(self, it):
        if self._filling:
            return
        i = self.ctl.row_at(it.row())
        if i is None:
            return
        if it.column() == 0:
            self.rows[i]["_on"] = it.checkState() == Qt.Checked
            self._update()
        elif F[it.column()] == "new":
            try:
                n = int(float(it.text().replace(",", ".")))
            except ValueError:
                n = self.rows[i]["new"]
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._set_new(i, n))

    def _picked(self):
        return [r for r in self.rows if r["_on"] and r["new"] > (r["list"] or 0)]

    def _update(self):
        p = self._picked()
        self.b_go.set_count(len(p))
        self.b_go.setEnabled(bool(p) and self._undo is None)
        add = sum(1 for r in p if not r["in_list"])
        eur = sum((r["new"] - (r["list"] or 0)) * r["pack"] * r["unit"] for r in p)
        big = sum(1 for r in p if r["amount"] >= VP.BIG_LINE_EUR)
        txt = tr("选了 {0} 行：改大 {1} 个 · 新加 {2} 个 · 多订约 €{3:,.0f}", len(p), len(p) - add, add, eur)
        if big:
            txt += tr(" · {0} 个金额 ≥ €{1:,}", big, VP.BIG_LINE_EUR)
        if not self._undo:
            self.status.setText(txt)

    # ---------- 写进清单 ----------
    def apply(self):
        import reorder_service as R
        from erp_widgets import BusyDialog
        p = self._picked()
        if not p:
            return
        lines = []
        for sid in sorted({r["store"] for r in p}):
            rs = [r for r in p if r["store"] == sid]
            add = sum(1 for r in rs if not r["in_list"])
            eur = sum((r["new"] - (r["list"] or 0)) * r["pack"] * r["unit"] for r in rs)
            lines.append(tr("• {0}：改大 {1} 个、新加 {2} 个，多订约 €{3:,.0f}", R.STORES.get(sid, sid), len(rs) - add, add, eur))
        big = [r for r in p if r["amount"] >= VP.BIG_LINE_EUR]
        if big:
            lines.append("")
            lines.append(tr("金额特别大的，请再看一眼："))
            lines += [f"  {R.STORES.get(r['store'], r['store'])} · {r['name']} · {r['new']} × {r['pack']:g} = €{r['amount']:,.0f}"
                      for r in big[:10]]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("写进门店清单"))
        box.setText(tr("把 {0} 个商品的箱数写进门店清单（1 门店清单 里的 DETER 文件）：", len(p)))
        box.setInformativeText("\n".join(lines) + "\n\n" + tr(
            "确认后：\n· 清单里已有的改成新的箱数（只会改大），没有的加在最后；MOTIVO 写「{0}」；\n"
            "· 原文件先备份到「1 门店清单\\_旧文件」，可以点「撤销」换回来；\n"
            "· 同步文件夹里的同事也会看到改过的清单；\n· 之后点「开始检查」：这些商品照清单订，判断后面写「VOLANTINO」，要审核。",
            VP.reason_text(self.res, self.deduct.isChecked())))
        ok = box.addButton(tr("写进去"), QMessageBox.AcceptRole)
        box.addButton(tr("取消"), QMessageBox.RejectRole)
        box.setDefaultButton(ok)
        box.exec()
        if box.clickedButton() != ok:
            return
        undo, e = BusyDialog.run(self, tr("写进门店清单"), tr("正在改门店清单…"),
                                 lambda: VP.write_lists(self.res["paths"], p, VP.reason_text(self.res, self.deduct.isChecked())))
        if e:
            QMessageBox.warning(self, tr("写进门店清单"), str(e))
            return
        self._undo = undo
        for r in p:
            r["list"], r["in_list"], r["_on"] = float(r["new"]), True, False
        log = getattr(self.win, "log", print)
        for u in undo:
            log(tr("[VOLANTINO 备货] {0}：{1} 个商品写进 {2}（原文件备份 {3}）", R.STORES.get(u["store"], u["store"]),
                   u["n"], os.path.basename(u["path"]), os.path.basename(u["backup"])))
        self.ctl.render()
        self.status.setText(tr("✓ 已写进 {0} 家店的门店清单，共 {1} 个商品。接着点「开始检查」", len(undo), len(p)))
        self.b_undo.show()
        self._update()
        self._refresh_ws()

    def undo(self):
        if not self._undo:
            return
        if QMessageBox.question(self, tr("撤销"), tr("把门店清单换回写之前的样子？")) != QMessageBox.Yes:
            return
        left = VP.undo_lists(self._undo)
        if left:
            QMessageBox.warning(self, tr("撤销"), tr("这几份清单写完以后又被改过或正开着，没有换回去（备份在 _旧文件）：\n{0}",
                                                    "\n".join(os.path.basename(u["path"]) for u in left)))
        self._undo = None
        self.b_undo.hide()
        self.status.setText(tr("已撤销，门店清单换回原来的了"))
        self._refresh_ws()

    def _refresh_ws(self):
        fn = getattr(self.win, "refresh_ws", None)
        if fn:
            try:
                fn()
            except Exception:
                pass

    # ---------- 其他 ----------
    def _show_skipped(self):
        from PySide6.QtWidgets import QTableWidgetItem
        d = QDialog(self)
        d.setObjectName("Dialog")
        d.setWindowTitle(tr("批发商没有的商品"))
        v = QVBoxLayout(d)
        t = self.ui.make_table([tr("条码"), tr("商品"), tr("原因")])
        t.setRowCount(len(self.res["skipped"]))
        for k, s in enumerate(self.res["skipped"]):
            for c, val in enumerate((s["ean"], s["name"], tr(s["why"]))):
                t.setItem(k, c, QTableWidgetItem(str(val)))
        t.setColumnWidth(0, 130)
        t.setColumnWidth(1, 380)
        t.horizontalHeader().setStretchLastSection(True)
        v.addWidget(t)
        b = QPushButton(tr("关闭"))
        b.clicked.connect(d.accept)
        v.addWidget(b, 0, Qt.AlignRight)
        d.resize(760, 460)
        from erp_widgets import fit_to_screen
        fit_to_screen(d)
        d.exec()

    def _export(self):
        from erp_widgets import open_file
        name = f"VOLANTINO_{self.res['start'][8:10]}{self.res['start'][5:7]}_proposta.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, tr("导出"), os.path.join(os.path.expanduser("~"), "Desktop", name),
                                              "Excel (*.xlsx)")
        if not path:
            return
        try:
            VP.export(self.res, path)
        except Exception as e:
            QMessageBox.warning(self, tr("导出"), str(e))
            return
        open_file(self, path)
