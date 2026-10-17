"""
分货管理 · 导入 / 导出（从旧程序 Importa Excel 移植）
导入商品清单 → 查最近 N 天各门店销量 + 当前库存 → 调货判断（哪家门店能把货调给要补货的门店）→ 导出报告
"""
import os
import threading

import pandas as pd
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
                               QComboBox, QFileDialog, QMessageBox, QButtonGroup, QStackedWidget, QApplication)

from i18n import tr
from settings import save_settings
from theme import T, open_path
from erp_widgets import StepBar, TaskDialog, kpi_card, EmptyState, fit_chip
from distribution_page import fill_table, _panel
import distribution_service as D

STORES = list(D.STORE_NAMES.items())


class _Sig(QObject):
    done = Signal(object)


class ImportPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cfg = settings.setdefault("import_page", {})
        self.path = None
        self.df_in = None
        self.codes = []
        self.code_col = None
        self.data = None            # 销量 + 库存
        self.info = None            # 条码 / 供应商
        self.rs_rows = None         # 调货结果
        self.days_used = None
        self.running = False
        self.sig = _Sig()
        self.sig.done.connect(self._done)

    # -----------------------------------------------------------------
    def build(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(14)
        t = QLabel(tr("导入 / 导出"))
        t.setObjectName("PageTitle")
        s = QLabel(tr("导入一份商品清单，查各门店最近的销量和库存，判断哪家门店能调货给要补货的门店，再导出完整报告。"))
        s.setObjectName("PageSub")
        lay.addWidget(t)
        lay.addWidget(s)
        self.steps = StepBar([(tr("导入清单"), ""), (tr("查销量和库存"), ""), (tr("调货判断"), tr("可选")),
                              (tr("导出报告"), "")])
        lay.addWidget(self.steps)

        # ---- 操作面板 ----
        cp, cv = _panel()
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addWidget(QLabel(tr("商品清单")))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText(tr("Excel，里面要有一列商品编号（CodArticolo / Codice / 商品编号 …）"))
        r.addWidget(self.file_edit, 1)
        b = QPushButton(tr("选择文件…"))
        b.clicked.connect(self.choose_file)
        r.addWidget(b)
        cv.addLayout(r)
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addWidget(QLabel(tr("销量天数")))
        self.days = QComboBox()
        for d in (1, 3, 7, 14, 30, 60, 90):
            self.days.addItem(tr("最近 {0} 天", d), d)
        self.days.setCurrentIndex(max(0, self.days.findData(int(self.cfg.get("days", 7)))))
        r.addWidget(self.days)
        self.btn_query = QPushButton(tr("查询销量和库存"))
        self.btn_query.setObjectName("Primary")
        self.btn_query.clicked.connect(self.start)
        r.addWidget(self.btn_query)
        clr = QPushButton(tr("清空"))
        clr.clicked.connect(self.clear)
        r.addWidget(clr)
        r.addSpacing(10)
        self.file_info = QLabel("")
        self.file_info.setObjectName("Faint")
        self.file_info.setWordWrap(True)
        r.addWidget(self.file_info, 1)
        cv.addLayout(r)
        lay.addWidget(cp)

        k = QGridLayout()
        k.setSpacing(12)
        c1, self.k_n, self.k_n_sub = kpi_card(tr("商品"), "—", " ")
        c2, self.k_sales, self.k_sales_sub = kpi_card(tr("总销量"), "—", " ")
        c3, self.k_inv, self.k_inv_sub = kpi_card(tr("总库存"), "—", " ")
        c4, self.k_rs, self.k_rs_sub = kpi_card(tr("可以调货"), "—", " ")
        for i, c in enumerate((c1, c2, c3, c4)):
            k.addWidget(c, 0, i)
        lay.addLayout(k)

        # ---- 结果面板 ----
        tp, tv = _panel()
        th = QHBoxLayout()
        th.setSpacing(8)
        self.view_group = QButtonGroup(w)
        self.view_btns = {}
        for key, text in (("sales", tr("销量与库存")), ("restock", tr("调货判断"))):
            vb = QPushButton(text)
            vb.setObjectName("Chip")
            vb.setCheckable(True)
            vb.setProperty("view", key)
            fit_chip(vb)
            self.view_group.addButton(vb)
            self.view_btns[key] = vb
            th.addWidget(vb)
        self.view_btns["sales"].setChecked(True)
        self.view_group.buttonClicked.connect(lambda b: self._set_view(b.property("view")))
        th.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("筛选编号或名称"))
        self.search.setMaximumWidth(220)
        self.search.textChanged.connect(self._filter)
        th.addWidget(self.search)
        self.btn_export = QPushButton(tr("导出报告"))
        self.btn_export.setObjectName("Primary")
        self.btn_export.clicked.connect(self.export)
        th.addWidget(self.btn_export)
        tv.addLayout(th)

        # 调货参数（只在「调货判断」显示）
        self.rs_bar = QWidget()
        rb = QHBoxLayout(self.rs_bar)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(8)
        rb.addWidget(QLabel(tr("要补货的门店")))
        self.need_store = QComboBox()
        for sid, name in STORES:
            self.need_store.addItem(f"{sid} · {name}", sid)
        self.need_store.setCurrentIndex(max(0, self.need_store.findData(int(self.cfg.get("need_store", 4)))))
        rb.addWidget(self.need_store)
        rb.addSpacing(14)
        rb.addWidget(QLabel(tr("从哪家调（按顺序试）")))
        self.order_boxes = []
        for i, default in enumerate(self.cfg.get("order") or [3, 2, 1]):
            lb = QLabel(f"{i + 1}°")
            lb.setObjectName("Faint")
            rb.addWidget(lb)
            cb = QComboBox()
            for sid, name in STORES:
                cb.addItem(f"{sid} · {name}", sid)
            cb.setCurrentIndex(max(0, cb.findData(int(default))))
            rb.addWidget(cb)
            self.order_boxes.append(cb)
        self.btn_rs = QPushButton(tr("判断调货"))
        self.btn_rs.setObjectName("Primary")
        self.btn_rs.clicked.connect(self.run_restock)
        rb.addWidget(self.btn_rs)
        rb.addStretch()
        rule = QLabel(tr("规则：供货门店库存 > 补货门店销量 + 供货门店自己的销量，才从这家调"))
        rule.setObjectName("Faint")
        rule.setWordWrap(True)
        self.rs_box = QWidget()
        rbv = QVBoxLayout(self.rs_box)
        rbv.setContentsMargins(0, 0, 0, 0)
        rbv.setSpacing(4)
        rbv.addWidget(self.rs_bar)
        rbv.addWidget(rule)
        tv.addWidget(self.rs_box)

        self.tstack = QStackedWidget()
        self.tstack.addWidget(EmptyState("import", tr("还没有数据"),
                                         tr("选择商品清单，点「查询销量和库存」。结果在这里按门店显示，可以接着做调货判断和导出。")))
        self.table = self.ui.make_table(["—"])
        self.table.horizontalHeader().setSectionResizeMode(self.table.horizontalHeader().ResizeMode.Interactive)
        self.table.setMinimumHeight(500)
        self.tstack.addWidget(self.table)
        tv.addWidget(self.tstack, 1)
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        tv.addWidget(self.status)
        lay.addWidget(tp, 1)
        self.view = "sales"
        self._update_state()
        return w

    # -----------------------------------------------------------------
    def _update_state(self):
        st = self.steps
        st.set_state(0, "done" if self.codes else "now",
                     tr("{0} 个商品", len(self.codes)) if self.codes else tr("还没有选择"))
        if self.data is not None:
            st.set_state(1, "done", tr("最近 {0} 天", self.days_used))
        else:
            st.set_state(1, "now" if self.codes else "todo", tr("点「查询销量和库存」"))
        if self.rs_rows is not None:
            st.set_state(2, "done", tr("{0} 个能调", sum(1 for r in self.rs_rows if r["ok"])))
        else:
            st.set_state(2, "todo", tr("可选"))
        st.set_state(3, "now" if self.data is not None else "todo", "")
        self.btn_query.setEnabled(bool(self.codes) and not self.running)
        self.btn_rs.setEnabled(self.data is not None and not self.running)
        self.btn_export.setEnabled(self.data is not None and not self.running)
        self.rs_box.setVisible(self.view == "restock")

    def restyle(self):
        self.steps.restyle()
        if self.data is not None:
            self._show()

    # -----------------------------------------------------------------
    def choose_file(self):
        start = os.path.dirname(self.cfg.get("last_file", "")) or ""
        p, _ = QFileDialog.getOpenFileName(self.win, tr("选择商品清单"), start, tr("Excel 文件 (*.xlsx *.xls)"))
        if not p:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            df = pd.read_excel(p)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self.win, tr("导入 / 导出"), tr("读不了这个文件：\n{0}", e))
            return
        QApplication.restoreOverrideCursor()
        codes, col = D.detect_codes(df)
        if not codes:
            QMessageBox.warning(self.win, tr("导入 / 导出"),
                                tr("没找到商品编号列。\n文件里的列：{0}", ", ".join(map(str, df.columns[:15]))))
            return
        self.path, self.df_in, self.codes, self.code_col = p, df, codes, col
        self.data = self.info = self.rs_rows = None
        self.cfg["last_file"] = p
        save_settings(self.settings)
        self.file_edit.setText(os.path.normpath(p))
        self.file_info.setText(tr("商品编号列「{0}」· {1} 个商品 · 前几个：{2}", col, len(codes),
                                  ", ".join(map(str, codes[:8])) + (" …" if len(codes) > 8 else "")))
        self.tstack.setCurrentIndex(0)
        self._kpis()
        self._update_state()

    def clear(self):
        self.path = self.df_in = self.data = self.info = self.rs_rows = None
        self.codes = []
        self.file_edit.clear()
        self.file_info.setText("")
        self.search.clear()
        self.tstack.setCurrentIndex(0)
        self.status.setText("")
        self._kpis()
        self._update_state()

    # -----------------------------------------------------------------
    def start(self):
        if self.running or not self.codes:
            return
        days = self.days.currentData()
        self.cfg["days"] = days
        save_settings(self.settings)
        codes = list(self.codes)
        dbs = self.settings.get("db") or None
        log = self.win.log
        self.running = True
        self._update_state()
        dlg = TaskDialog(self.win, tr("正在查询销量和库存"), tr("{0} 个商品 · 最近 {1} 天", len(codes), days),
                         [tr("连接数据库"), tr("查询各门店销量和库存"), tr("查询条码和供应商")], cancellable=False)
        dlg.went_background.connect(lambda: self.win.show_toast(tr("在后台查询，完成后会提醒")))
        dlg.show()

        def work():
            try:
                import reorder_service as R
                dlg.post_step(0, "running", "", "")
                dlg.post_progress(5)
                db = R.make_db(dbs)
                dlg.post_step(0, "done", "", "")
                dlg.post_step(1, "running", str(len(codes)), "")
                dlg.post_progress(20)
                data = D.query_sales_inventory(db.engine, codes, days)
                dlg.post_step(1, "done", str(len(data)), "")
                dlg.post_step(2, "running", "", "")
                dlg.post_progress(75)
                info = D.query_product_info(db.engine, codes)
                db.engine.dispose()
                dlg.post_step(2, "done", str(len(info)), "")
                dlg.post_progress(100)
                dlg.post_finished(True, tr("完成：{0} 个商品", len(data)))
                log(tr("[导入 / 导出] {0} 个商品，最近 {1} 天", len(codes), days))
                self.sig.done.emit((data, info, days, None))
            except Exception as e:
                import traceback
                log(tr("❌ 查询失败：{0}", e))
                log(traceback.format_exc())
                dlg.post_finished(False, tr("{0}\n\n数据库连不上的话，到「系统设置」检查地址和密码。", e))
                self.sig.done.emit((None, None, days, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, payload):
        data, info, days, err = payload
        self.running = False
        if err:
            self._update_state()
            self.win.notify(tr("查询销量和库存失败：{0}", err))
            return
        if info is not None and not info.empty:
            names = dict(zip(info["CodArticolo"].astype(int), info["Descrizione"]))
            data["Descrizione"] = [d or names.get(int(c), "") for c, d in zip(data["CodArticolo"], data["Descrizione"])]
        self.data, self.info, self.days_used, self.rs_rows = data, info, days, None
        self._kpis()
        self._set_view("sales")
        self.win.show_toast(tr("查询完成：{0} 个商品", len(data)))

    # -----------------------------------------------------------------
    def run_restock(self):
        if self.data is None:
            return
        need = self.need_store.currentData()
        order = [cb.currentData() for cb in self.order_boxes]
        self.cfg.update(need_store=need, order=order)
        save_settings(self.settings)
        self.rs_rows = D.restock(self.data, need, order)
        self._kpis()
        self._show()
        self._update_state()
        ok = sum(1 for r in self.rs_rows if r["ok"])
        self.win.show_toast(tr("调货判断：{0} / {1} 个商品能调", ok, len(self.rs_rows)))

    def _kpis(self):
        if self.data is None:
            self.k_n.setText(f"{len(self.codes):,}" if self.codes else "—")
            self.k_n_sub.setText(tr("清单里的商品") if self.codes else " ")
            for v, s_ in ((self.k_sales, self.k_sales_sub), (self.k_inv, self.k_inv_sub), (self.k_rs, self.k_rs_sub)):
                v.setText("—")
                v.setStyleSheet("")
                s_.setText(" ")
            return
        d = self.data
        self.k_n.setText(f"{len(d):,}")
        no_sales = int((d["Total_Sales"] <= 0).sum())
        self.k_n_sub.setText(tr("{0} 个最近没卖", no_sales))
        self.k_sales.setText(f"{d['Total_Sales'].sum():,.0f}")
        best = max(range(1, 6), key=lambda i: d[f"Neg{i}_Sales"].sum())
        self.k_sales_sub.setText(tr("最近 {0} 天 · 卖得最多：{1}", self.days_used, D.STORE_NAMES[best]))
        self.k_inv.setText(f"{d['Total_Inv'].sum():,.0f}")
        zero = int((d["Total_Inv"] <= 0).sum())
        self.k_inv_sub.setText(tr("{0} 个五家店都没库存", zero))
        self.k_inv.setStyleSheet("")
        if self.rs_rows is None:
            self.k_rs.setText("—")
            self.k_rs_sub.setText(tr("在「调货判断」里选门店"))
            self.k_rs.setStyleSheet("")
        else:
            ok = sum(1 for r in self.rs_rows if r["ok"])
            self.k_rs.setText(f"{ok} / {len(self.rs_rows)}")
            self.k_rs.setStyleSheet(f"color:{T('ok')};")
            self.k_rs_sub.setText(tr("补货给 {0}", D.STORE_NAMES[self.rs_rows[0]["need_store"]]) if self.rs_rows else " ")

    def _set_view(self, key):
        self.view = key
        self.view_btns[key].setChecked(True)
        self._show()
        self._update_state()

    def _show(self):
        if self.data is None:
            return
        self.tstack.setCurrentIndex(1)
        if self.view == "sales":
            d = self.data
            df = pd.DataFrame({tr("商品编号"): d["CodArticolo"], tr("名称"): d["Descrizione"]})
            for i, name in D.STORE_NAMES.items():
                df[f"{name}\n" + tr("销量")] = d[f"Neg{i}_Sales"]
                df[f"{name}\n" + tr("库存")] = d[f"Neg{i}_Inv"]
            df[tr("合计销量")] = d["Total_Sales"]
            df[tr("合计库存")] = d["Total_Inv"]
            colors = [T("track") if s <= 0 else (T("err_soft") if inv <= 0 else None)
                      for s, inv in zip(d["Total_Sales"], d["Total_Inv"])]
            fill_table(self.table, df, row_color=lambda i: colors[i])
            self.status.setText(tr("灰色 = 最近 {0} 天五家店都没卖 · 红色 = 有销量但五家店都没库存", self.days_used))
        else:
            if self.rs_rows is None:
                self.table.setRowCount(0)
                self.table.setColumnCount(1)
                self.table.setHorizontalHeaderLabels([tr("选好门店和顺序，点「判断调货」")])
                self.status.setText("")
                return
            rows = self.rs_rows
            df = pd.DataFrame({
                tr("商品编号"): [r["code"] for r in rows],
                tr("名称"): [r["desc"] for r in rows],
                tr("补货门店"): [r["need_store_name"] for r in rows],
                tr("补货门店销量"): [r["need"] for r in rows],
                tr("补货门店库存"): [r["need_inv"] for r in rows],
                tr("从哪家调"): [D.STORE_NAMES.get(r["supplier"], "") if r["supplier"] else "" for r in rows],
                tr("那家库存"): [r["supplier_inv"] for r in rows],
                tr("调完还剩"): [r["available"] for r in rows],
                tr("结果"): [tr("能调") if r["ok"] else tr("调不了") for r in rows],
            })
            fill_table(self.table, df, row_color=lambda i: T("ok_soft") if rows[i]["ok"] else T("err_soft"))
            self.status.setText(tr("绿色 = 能从这家调；红色 = 按顺序试过的门店库存都不够（显示第一家试的门店）"))
        hh = self.table.horizontalHeader()
        hh.resizeSections(hh.ResizeMode.ResizeToContents)
        for j in range(self.table.columnCount()):
            self.table.setColumnWidth(j, min(max(self.table.columnWidth(j), 70), 320))
        self._filter()

    def _filter(self, *_):
        text = self.search.text().strip().lower()
        t = self.table
        for i in range(t.rowCount()):
            a, b = t.item(i, 0), t.item(i, 1)
            hit = not text or any(x is not None and text in x.text().lower() for x in (a, b))
            t.setRowHidden(i, not hit)

    # -----------------------------------------------------------------
    def export(self):
        if self.data is None:
            return
        default = D.default_export_path(self.path, "report")
        p, _ = QFileDialog.getSaveFileName(self.win, tr("导出报告"), default, tr("Excel 文件 (*.xlsx)"))
        if not p:
            return
        if not p.lower().endswith(".xlsx"):
            p += ".xlsx"
        try:
            D.export_report(p, self.codes, self.data, self.info, self.rs_rows, self.days_used)
        except PermissionError:
            QMessageBox.warning(self.win, tr("导出报告"), tr("文件被占用（可能在 Excel 里开着），关掉后再导出。"))
            return
        except Exception as e:
            QMessageBox.warning(self.win, tr("导出报告"), tr("导出失败：{0}", e))
            return
        self.steps.set_state(3, "done", os.path.basename(p))
        self.win.show_toast(tr("已导出：{0}", os.path.basename(p)))
        box = QMessageBox(self.win)
        box.setWindowTitle(tr("导出报告"))
        box.setText(tr("已导出：\n{0}", p))
        ob = box.addButton(tr("打开文件"), QMessageBox.AcceptRole)
        box.addButton(tr("关闭"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is ob:
            open_path(p, self.win)
