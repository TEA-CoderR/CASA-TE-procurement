"""
分货管理 · 分货数据处理（从旧程序 Distribuzione 移植）
① 选择 Excel → ② 处理方式（补齐 EAN / 双表格提取 / 一体化）→ ③ 处理（进度窗口）→ ④ 查看、导出
"""
import os
import threading

import pandas as pd
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
                               QFrame, QRadioButton, QButtonGroup, QFileDialog, QMessageBox, QTableWidgetItem,
                               QApplication)

from i18n import tr
from settings import save_settings
from theme import T, open_path
from erp_widgets import StepBar, TaskDialog, kpi_card, EmptyState, fit_chip
import distribution_service as D

MAX_ROWS = 5000


# =====================================================================
# 小工具：DataFrame → 表格（数字按数值排序）
# =====================================================================
class SortItem(QTableWidgetItem):
    def __lt__(self, other):
        a, b = self.data(Qt.UserRole), other.data(Qt.UserRole)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return self.text() < other.text()


def _cell_text(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "", None
    if isinstance(v, float) and v.is_integer():
        return str(int(v)), v
    if isinstance(v, (int, float)):
        return (f"{v:g}" if isinstance(v, float) else str(v)), v
    return str(v), None


def fill_table(t, df, text_cols=(), max_rows=MAX_ROWS, row_color=None):
    """把 DataFrame 放进 QTableWidget。text_cols 里的列（条码）按文字显示；row_color(i) → 颜色或 None"""
    t.setSortingEnabled(False)
    t.clear()
    cols = [str(c) for c in df.columns]
    t.setColumnCount(len(cols))
    t.setHorizontalHeaderLabels(cols)
    n = min(len(df), max_rows)
    t.setRowCount(n)
    tc = {str(c) for c in text_cols}
    for i in range(n):
        color = row_color(i) if row_color else None
        for j, c in enumerate(cols):
            v = df.iat[i, j]
            if c in tc:
                s, num = D.normalize_ean(v), None
            else:
                s, num = _cell_text(v)
            it = SortItem(s)
            if num is not None:
                it.setData(Qt.UserRole, float(num))
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if color:
                it.setBackground(QColor(color))
            t.setItem(i, j, it)
    t.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)     # 保持文件里的顺序，点表头再排序
    t.setSortingEnabled(True)
    return n


def _panel(title=None):
    f = QFrame()
    f.setObjectName("Panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 14, 16, 14)
    v.setSpacing(10)
    if title:
        t = QLabel(title)
        t.setObjectName("Section")
        v.addWidget(t)
    return f, v


class _Sig(QObject):
    done = Signal(object)


MODES = [("unified", "一体化（EAN + Sheet1）", "补齐 EAN 条码，同时从 Sheet1 提取 PZ*COLLI 和五家门店数量；Sheet1 没匹配上的行放到导出文件第二张表。最常用。"),
         ("dual", "从 Sheet1 提取（双表格）", "首页按 商品编号 ↔ Cod.Art. 匹配 Sheet1（找不到再用 商品条码 ↔ Codice EAN），取 PZ*COLLI 和门店数量（固定 K/N/Q/T/W 列），最后算 差值 = 数量 − 五店合计。"),
         ("ean", "只补齐 EAN 条码", "按数据库 codbarre 表，在商品编号后面加 Codice EAN / 2 / 3（66、55 开头的内部码不算）。")]


class DistributionPage:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cfg = settings.setdefault("distribution", {})
        self.path = None
        self.sheets = None
        self.result = None
        self.view = "out"
        self.running = False
        self.sig = _Sig()
        self.sig.done.connect(self._done)

    # -----------------------------------------------------------------
    def build(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(14)
        head = QHBoxLayout()
        tl = QVBoxLayout()
        tl.setSpacing(2)
        t = QLabel(tr("分货数据处理"))
        t.setObjectName("PageTitle")
        s = QLabel(tr("处理分货表格：补齐 EAN 条码、从 Sheet1 提取各门店数量，导出新的 Excel（原文件不改）。"))
        s.setObjectName("PageSub")
        tl.addWidget(t)
        tl.addWidget(s)
        head.addLayout(tl, 1)
        lay.addLayout(head)
        self.steps = StepBar([(tr("选择文件"), ""), (tr("处理方式"), ""), (tr("处理"), ""), (tr("查看、导出"), "")])
        lay.addWidget(self.steps)

        body = QHBoxLayout()
        body.setSpacing(14)
        # ---- 左：文件 + 方式 ----
        left = QVBoxLayout()
        left.setSpacing(14)
        fp, fv = _panel(tr("① 选择 Excel"))
        r = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText(tr("还没有选择文件"))
        b = QPushButton(tr("选择文件…"))
        b.clicked.connect(self.choose_file)
        self.btn_pick = b
        r.addWidget(self.file_edit, 1)
        r.addWidget(b)
        fv.addLayout(r)
        self.info = QGridLayout()
        self.info.setHorizontalSpacing(14)
        self.info.setVerticalSpacing(4)
        self.info_vals = {}
        for i, (k, label) in enumerate((("sheets", tr("工作表")), ("rows", tr("首页行数")), ("col", tr("商品编号列")),
                                        ("sheet1", "Sheet1"))):
            a = QLabel(label)
            a.setObjectName("Faint")
            v = QLabel("—")
            v.setWordWrap(True)
            self.info.addWidget(a, i, 0)
            self.info.addWidget(v, i, 1)
            self.info_vals[k] = v
        self.info.setColumnStretch(1, 1)
        fv.addLayout(self.info)
        left.addWidget(fp)

        mp, mv = _panel(tr("② 处理方式"))
        self.mode_group = QButtonGroup(w)
        self.mode_cards = {}
        for mode, title, desc in MODES:
            card = QFrame()
            card.setObjectName("MiniCard")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(12, 10, 12, 10)
            cv.setSpacing(3)
            rb = QRadioButton(tr(title))
            rb.setStyleSheet("font-weight:600;")
            rb.setProperty("mode", mode)
            d = QLabel(tr(desc))
            d.setWordWrap(True)
            d.setObjectName("Faint")
            d.setContentsMargins(24, 0, 0, 0)
            cv.addWidget(rb)
            cv.addWidget(d)
            self.mode_group.addButton(rb)
            self.mode_cards[mode] = (card, rb)
            card.mousePressEvent = lambda _e, rb=rb: rb.setChecked(True)
            card.setProperty("noTextSelect", True)      # 整张卡片可以点，里面的字不做成可选（不然点字没反应）
            mv.addWidget(card)
        cur = self.cfg.get("mode", "unified")
        self.mode_cards.get(cur, self.mode_cards["unified"])[1].setChecked(True)
        self.mode_group.buttonToggled.connect(self._mode_changed)
        left.addWidget(mp)

        self.btn_run = QPushButton(tr("开始处理"))
        self.btn_run.setObjectName("Primary")
        self.btn_run.setMinimumHeight(40)
        self.btn_run.clicked.connect(self.start)
        left.addWidget(self.btn_run)
        left.addStretch()
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(400)
        body.addWidget(lw)

        # ---- 右：结果 ----
        right = QVBoxLayout()
        right.setSpacing(14)
        k = QGridLayout()
        k.setSpacing(12)
        self.kpis = []
        for i in range(4):
            c, val, sub = kpi_card("—", "—", " ")
            k.addWidget(c, 0, i)
            self.kpis.append((c, val, sub))
        right.addLayout(k)
        tp, tv = _panel()
        th = QHBoxLayout()
        th.setSpacing(8)
        tt = QLabel(tr("处理结果"))
        tt.setObjectName("Section")
        th.addWidget(tt)
        th.addSpacing(8)
        self.view_btns = {}
        self.view_group = QButtonGroup(w)
        for key, text in (("out", tr("分货表")), ("unmatched", tr("Sheet1 未匹配"))):
            vb = QPushButton(text)
            vb.setObjectName("Chip")
            vb.setCheckable(True)
            vb.setProperty("view", key)
            fit_chip(vb)
            self.view_group.addButton(vb)
            self.view_btns[key] = vb
            th.addWidget(vb)
        self.view_btns["out"].setChecked(True)
        self.view_group.buttonClicked.connect(lambda b: self._set_view(b.property("view")))
        self.only_miss = QPushButton(tr("只看没匹配上的"))
        self.only_miss.setObjectName("Chip")
        self.only_miss.setCheckable(True)
        fit_chip(self.only_miss)
        self.only_miss.toggled.connect(lambda _=False: self._show_table())
        th.addWidget(self.only_miss)
        th.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("筛选编号、条码或名称"))
        self.search.setMaximumWidth(220)
        self.search.textChanged.connect(self._filter)
        th.addWidget(self.search)
        self.btn_export = QPushButton(tr("导出 Excel"))
        self.btn_export.setObjectName("Primary")
        self.btn_export.clicked.connect(self.export)
        th.addWidget(self.btn_export)
        tv.addLayout(th)
        from PySide6.QtWidgets import QStackedWidget
        self.tstack = QStackedWidget()
        es = EmptyState("distribution", tr("还没有处理"),
                        tr("左边选择分货 Excel 和处理方式，然后点「开始处理」。处理结果显示在这里，确认后导出新的 Excel。"))
        self.tstack.addWidget(es)
        self.table = self.ui.make_table(["—"])
        self.table.setSelectionMode(self.table.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(self.table.horizontalHeader().ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(110)
        self.table.setMinimumHeight(520)
        self.tstack.addWidget(self.table)
        tv.addWidget(self.tstack, 1)
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        self.status.setWordWrap(True)
        tv.addWidget(self.status)
        right.addWidget(tp, 1)
        body.addLayout(right, 1)
        lay.addLayout(body, 1)
        self._reset_kpis()
        self._update_state()
        return w

    # -----------------------------------------------------------------
    def _mode(self):
        b = self.mode_group.checkedButton()
        return b.property("mode") if b else "unified"

    def _mode_changed(self, *_):
        self.cfg["mode"] = self._mode()
        save_settings(self.settings)
        self._update_state()

    def _update_state(self):
        st = self.steps
        st.set_state(0, "done" if self.sheets else "now", os.path.basename(self.path) if self.path else tr("还没有选择"))
        mode_name = dict((m, tr(t)) for m, t, _ in MODES)[self._mode()]
        st.set_state(1, "done" if self.sheets else "todo", mode_name)
        if self.result:
            st.set_state(2, "done", tr("{0} 行", len(self.result["out"])))
            st.set_state(3, "now", tr("确认后导出"))
        else:
            st.set_state(2, "now" if self.sheets else "todo", tr("点「开始处理」"))
            st.set_state(3, "todo", "")
        self.btn_run.setEnabled(bool(self.sheets) and not self.running)
        self.btn_export.setEnabled(bool(self.result) and not self.running)
        has_um = bool(self.result) and self.result.get("unmatched") is not None and not self.result["unmatched"].empty
        self.view_btns["unmatched"].setVisible(has_um)
        self.only_miss.setVisible(bool(self.result) and "row_status" in self.result["stats"] and self.view == "out")

    def restyle(self):
        self.steps.restyle()
        if self.result:
            self._show_table()

    # -----------------------------------------------------------------
    def choose_file(self):
        start = os.path.dirname(self.cfg.get("last_file", "")) or ""
        p, _ = QFileDialog.getOpenFileName(self.win, tr("选择分货 Excel"), start, tr("Excel 文件 (*.xlsx *.xls)"))
        if p:
            self.load_file(p)

    def load_file(self, p):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            sheets = D.read_workbook(p)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self.win, tr("分货数据处理"), tr("读不了这个文件：\n{0}", e))
            return
        QApplication.restoreOverrideCursor()
        self.path, self.sheets, self.result = p, sheets, None
        self.cfg["last_file"] = p
        save_settings(self.settings)
        self.file_edit.setText(os.path.normpath(p))
        names = list(sheets.keys())
        home = sheets[names[0]]
        col = D.find_column(home, D.HOME_PRODUCT_COLUMN_NAMES) or D.detect_product_column(home)
        s1_name, _s1 = D.find_sheet1(sheets)
        self.info_vals["sheets"].setText(tr("{0} 张：{1}", len(names), "、".join(str(n) for n in names[:4]) +
                                            ("…" if len(names) > 4 else "")))
        self.info_vals["rows"].setText(tr("{0} 行（{1}）", len(home), names[0]))
        self.info_vals["col"].setText(str(col) if col is not None else tr("没找到"))
        self.info_vals["col"].setStyleSheet("" if col is not None else f"color:{T('err')};")
        self.info_vals["sheet1"].setText(str(s1_name) if s1_name is not None else tr("没有（只能补齐 EAN）"))
        self.info_vals["sheet1"].setStyleSheet("" if s1_name is not None else f"color:{T('warn')};")
        if s1_name is None and self._mode() != "ean":
            self.mode_cards["ean"][1].setChecked(True)
        self._reset_kpis()
        self.tstack.setCurrentIndex(0)
        self.status.setText("")
        self._update_state()

    # -----------------------------------------------------------------
    def start(self):
        if self.running or not self.sheets:
            return
        mode = self._mode()
        sheets = self.sheets
        dbs = self.settings.get("db") or None
        log = self.win.log
        self.running = True
        self._update_state()
        steps = [tr("读取首页，匹配 Sheet1") if mode != "ean" else tr("读取首页"),
                 tr("查询 codbarre 条码") if mode != "dual" else tr("计算差值"), tr("整理结果")]
        dlg = TaskDialog(self.win, tr("正在处理分货表"), os.path.basename(self.path), steps, cancellable=False)
        dlg.went_background.connect(lambda: self.win.show_toast(tr("分货表在后台处理，完成后会提醒")))
        dlg.show()

        def factory():
            import reorder_service as R
            db = R.make_db(dbs)
            return db.engine, db.engine.dispose

        def work():
            try:
                res = D.process(sheets, mode, factory if mode != "dual" else None,
                                step=lambda i, st, cnt, det, pct: (dlg.post_step(i, st, cnt, det), dlg.post_progress(pct)))
                if mode == "dual":
                    dlg.post_step(1, "done", "", "")
                dlg.post_step(2, "done", "", "")
                dlg.post_progress(100)
                dlg.post_finished(True, tr("完成：{0} 行", len(res["out"])))
                log(tr("[分货数据处理] {0}：{1} 行", os.path.basename(self.path), len(res["out"])))
                self.sig.done.emit((res, None))
            except Exception as e:
                import traceback
                log(tr("❌ 分货数据处理失败：{0}", e))
                log(traceback.format_exc())
                dlg.post_finished(False, str(e))
                self.sig.done.emit((None, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, payload):
        res, err = payload
        self.running = False
        if err:
            self._update_state()
            self.win.notify(tr("分货数据处理失败：{0}", err))
            return
        res["mode"] = self._mode()
        self.result = res
        self.view = "out"
        self.view_btns["out"].setChecked(True)
        self.only_miss.setChecked(False)
        self._fill_kpis()
        self._show_table()
        self._update_state()
        self.win.show_toast(tr("分货表处理完成：{0} 行", len(res["out"])))

    # -----------------------------------------------------------------
    def _reset_kpis(self):
        for (c, val, sub), label in zip(self.kpis, (tr("行数"), tr("Cod.Art. 匹配"), tr("条码回退匹配"), tr("没匹配上"))):
            c.findChild(QLabel, "KpiLabel") and c.findChild(QLabel, "KpiLabel").setText(label)
            val.setText("—")
            val.setStyleSheet("")
            sub.setText(" ")

    def _set_kpi(self, i, label, value, sub="", color=None):
        c, val, s = self.kpis[i]
        lab = c.findChild(QLabel, "KpiLabel")
        if lab:
            lab.setText(label)
        val.setText(str(value))
        val.setStyleSheet(f"color:{T(color)};" if color else "")
        s.setText(sub or " ")

    def _fill_kpis(self):
        st = self.result["stats"]
        n = st.get("rows", len(self.result["out"]))
        self._set_kpi(0, tr("行数"), f"{n:,}", self.result["stats"].get("product_col") and
                      tr("商品编号列：{0}", st["product_col"]) or "")
        if "match_art" in st:
            self._set_kpi(1, tr("Cod.Art. 匹配"), st["match_art"], tr("按商品编号对上 Sheet1"), "ok")
            self._set_kpi(2, tr("条码回退匹配"), st["match_ean"], tr("编号没对上，按条码对上"), "accent")
            sub = tr("Sheet1 里还有 {0} 行没用到", st.get("unmatched", 0)) if st.get("unmatched") else tr("全部对上")
            self._set_kpi(3, tr("没匹配上"), st["miss"], sub, "warn" if st["miss"] else "ok")
        else:
            self._set_kpi(1, "Codice EAN", st.get("ean1", 0), tr("找到第 1 个条码"), "ok")
            self._set_kpi(2, "Codice EAN 2", st.get("ean2", 0), tr("有第 2 个条码"), "accent")
            self._set_kpi(3, "Codice EAN 3", st.get("ean3", 0), tr("有第 3 个条码"))
        if "ean1" in st and "match_art" in st:
            self._set_kpi(0, tr("行数"), f"{n:,}", tr("找到 EAN 条码 {0} 行", st["ean1"]))

    def _set_view(self, key):
        self.view = key
        self._show_table()
        self._update_state()

    def _show_table(self):
        if not self.result:
            return
        self.tstack.setCurrentIndex(1)
        st = self.result["stats"]
        if self.view == "unmatched":
            df = self.result["unmatched"]
            colors = None
        else:
            df = self.result["out"]
            rs = st.get("row_status")
            if rs and self.only_miss.isChecked():
                keep = [i for i, x in enumerate(rs) if x == "miss"]
                df = df.iloc[keep]
                rs = [rs[i] for i in keep]
            colors = (lambda i: T("warn_soft") if rs[i] == "miss" else None) if rs else None
        n = fill_table(self.table, df, D.TEXT_BARCODE_COLUMNS, row_color=colors)
        new_cols = {"Codice EAN", "Codice EAN 2", "Codice EAN 3", "PZ*COLLI", "差值", *D.STORE_NAMES.values()}
        for j in range(self.table.columnCount()):
            h = self.table.horizontalHeaderItem(j)
            if h and h.text() in new_cols:
                h.setForeground(QColor(T("accent")))
                f = h.font()
                f.setBold(True)
                h.setFont(f)
        self.table.horizontalHeader().resizeSections(self.table.horizontalHeader().ResizeMode.ResizeToContents)
        for j in range(self.table.columnCount()):
            self.table.setColumnWidth(j, min(max(self.table.columnWidth(j), 70), 280))
        more = tr("（只显示前 {0} 行，导出的文件是完整的）", MAX_ROWS) if len(df) > MAX_ROWS else ""
        txt = tr("显示 {0} 行", n) + more
        if self.view == "out" and st.get("absent"):
            txt += " · " + tr("Sheet1 缺少：{0}", "、".join(D.STORE_NAMES.get(a, str(a)) if isinstance(a, int) else a
                                                       for a in st["absent"]))
        if self.view == "out" and st.get("row_status"):
            txt += " · " + tr("橙色行 = Sheet1 里没找到")
        self.status.setText(txt)
        self._filter()

    def _filter(self, *_):
        text = self.search.text().strip().lower()
        t = self.table
        for i in range(t.rowCount()):
            if not text:
                t.setRowHidden(i, False)
                continue
            hit = any(t.item(i, j) is not None and text in t.item(i, j).text().lower() for j in range(t.columnCount()))
            t.setRowHidden(i, not hit)

    # -----------------------------------------------------------------
    def export(self):
        if not self.result:
            return
        default = D.default_export_path(self.path, tr("分货"))
        p, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"), default, tr("Excel 文件 (*.xlsx)"))
        if not p:
            return
        if not p.lower().endswith(".xlsx"):
            p += ".xlsx"
        try:
            D.export_distribution(p, self.result["out"],
                                  self.result.get("unmatched") if self.result.get("mode") == "unified" else None)
        except PermissionError:
            QMessageBox.warning(self.win, tr("导出 Excel"), tr("文件被占用（可能在 Excel 里开着），关掉后再导出。"))
            return
        except Exception as e:
            QMessageBox.warning(self.win, tr("导出 Excel"), tr("导出失败：{0}", e))
            return
        self.steps.set_state(3, "done", os.path.basename(p))
        self.win.show_toast(tr("已导出：{0}", os.path.basename(p)))
        box = QMessageBox(self.win)
        box.setWindowTitle(tr("导出 Excel"))
        box.setText(tr("已导出：\n{0}", p))
        ob = box.addButton(tr("打开文件"), QMessageBox.AcceptRole)
        box.addButton(tr("关闭"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is ob:
            open_path(p, self.win)
