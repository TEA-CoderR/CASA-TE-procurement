"""
DETERSIVO采购 →「上周没到的…」（2026-11-03 用户：DETERSIVO 一周一订，把上一周的订单列出来，人勾哪些没到货，
代替手动放扣减文件；跑通之前上传扣减文件的地方留着）。

每家店读最近一次（今天以前、14 天内）比价结果里的确认订单（order_record.deter_candidates / read_deter_order：
程序的「4 比价结果」和 RECORD ORDINE 的 07 文件夹），勾「没到」→ 保存成这家店「3 扣减文件」里的
「Non arrivati ddmm.xlsx」（和 eliminare 一样的格式，比价时从主订单扣掉）。再打开窗口还是勾着的；全部取消勾 → 那个文件挪进 _旧文件。
"""
import os
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QButtonGroup,
                               QHeaderView)

from i18n import tr
import order_record as REC
import workspace as W


COLS = [(0, "没到", 56), (1, "商品", 320), (2, "条码", 124), (3, "箱数", 60), (4, "批发商", 120)]


def load_week(settings, ws_root, today=None):
    """每家店上一次 DETERSIVO 订单：{门店号: {"date", "path", "lines", "checked"}}（找不到的店不在里面）"""
    roots = REC.deter_roots(settings, ws_root)
    out = {}
    for sid, name in W.STORES.items():
        c = REC.deter_candidates(roots, name, today)
        if not c:
            continue
        day, path = c[0]
        try:
            lines = REC.read_deter_order(path)
        except Exception as e:
            out[sid] = {"date": day, "path": path, "lines": [], "checked": set(), "error": str(e)}
            continue
        had = REC.read_elim_eans(W.elim_dir(ws_root, sid))
        out[sid] = {"date": day, "path": path, "lines": lines, "checked": {x["ean"] for x in lines if x["ean"] in had}}
    return out


def save_week(data, ws_root):
    """勾了的 → 每家店一个扣减文件（以前生成的挪进 _旧文件）。返回 [(门店名, 几种, 几箱)]"""
    done = []
    for sid, d in data.items():
        folder = W.elim_dir(ws_root, sid)
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if f.startswith(REC.ELIM_PREFIX) and f.lower().endswith(".xlsx"):
                    W.archive(os.path.join(folder, f), os.path.join(folder, W.OLD))
        lines = [x for x in d["lines"] if x["ean"] in d["checked"]]
        if lines:
            REC.write_elim(lines, os.path.join(folder, REC.elim_name(d["date"])))
            done.append((W.STORES[sid], len(lines), sum(x["colli"] for x in lines)))
    return done


class DeterArrivalDialog(QDialog):
    def __init__(self, win, settings, ui, ws_root):
        super().__init__(win)
        self.win, self.settings, self.ui, self.root = win, settings, ui, ws_root
        self.setWindowTitle(tr("上周没到的"))
        self.data = load_week(settings, ws_root)
        self.sid = next(iter(self.data), None)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(6)
        t = QLabel(tr("上周没到的") + "  ⓘ")
        t.setObjectName("Section")
        t.setToolTip(tr("每家店上一次 DETERSIVO 订单（比价结果里的确认订单）。勾上还没到货的，保存后变成这家店的扣减文件，"
                        "下次比价从门店清单里扣掉，不会重复订"))
        top.addWidget(t)
        top.addSpacing(10)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.chips = {}
        for sid, name in W.STORES.items():
            d = self.data.get(sid)
            b = QPushButton(name + (f" · {d['date']:%d/%m}" if d else ""))
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setEnabled(bool(d))
            if not d:
                b.setToolTip(tr("最近两周没找到这家店的 DETERSIVO 订单"))
            elif d.get("error"):
                b.setToolTip(tr("读不了：{0}", d["error"]))
            else:
                b.setToolTip(d["path"])
            b.clicked.connect(lambda _=False, s=sid: self.set_store(s))
            self.group.addButton(b)
            self.chips[sid] = b
            top.addWidget(b)
        top.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("商品、条码"))
        self.search.setFixedWidth(180)
        top.addWidget(self.search)
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        top.addWidget(self.count)
        v.addLayout(top)

        self.t = ui.make_table([tr(h) for _f, h, _w in COLS], numeric_cols=(3,))
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, (_f, _h, wd) in enumerate(COLS):
            self.t.setColumnWidth(c, wd)
        self.t.horizontalHeaderItem(0).setToolTip(tr("点一下勾上 / 取消：还没到货的勾上"))
        from erp_widgets import TableSortFilter, fit_to_screen
        import settings as ST
        self.ctl = TableSortFilter(self.t, lambda: len(self._lines()), self._text, self._fill, key=self._key,
                                   hide=self._hidden, numeric=(3,))
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(tr("{0:,} / {1:,}", n, tot)))
        cfg = settings.setdefault("deter_arrival", {})
        self.ctl.remember(cfg, "lines", lambda: ST.save_settings(settings), [f for f, _h, _w in COLS])
        self.t.cellClicked.connect(self._clicked)
        self.search.textChanged.connect(lambda _: self.ctl.render())
        v.addWidget(self.t, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        b_all = QPushButton(tr("全部没到"))
        b_all.setToolTip(tr("显示出来的行全部勾上（可以先右键表头按批发商筛选）"))
        b_all.clicked.connect(lambda: self._set_visible(True))
        b_none = QPushButton(tr("全部到了"))
        b_none.setToolTip(tr("显示出来的行全部取消勾"))
        b_none.clicked.connect(lambda: self._set_visible(False))
        foot.addWidget(b_all)
        foot.addWidget(b_none)
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        foot.addWidget(self.status, 1)
        cancel, ok = QPushButton(tr("取消")), QPushButton(tr("保存为扣减文件"))
        ok.setObjectName("Primary")
        ok.setEnabled(bool(self.data))
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._ok)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        v.addLayout(foot)
        self.resize(860, 600)
        fit_to_screen(self)
        if self.sid is not None:
            self.chips[self.sid].setChecked(True)
        self.ctl.render()
        self._update_status()
        self.saved = []

    # ---- 数据 ----
    def _lines(self):
        return (self.data.get(self.sid) or {}).get("lines") or []

    def _checked(self):
        return (self.data.get(self.sid) or {}).get("checked", set())

    def set_store(self, sid):
        self.sid = sid
        self.ctl.render()
        self._update_status()

    def _text(self, i, c):
        x = self._lines()[i]
        f = COLS[c][0]
        if f == 0:
            return tr("没到") if x["ean"] in self._checked() else ""
        if f == 1:
            return x["desc"]
        if f == 2:
            return x["ean"]
        if f == 3:
            return f"{x['colli']:g}"
        if f == 4:
            return x["supplier"]
        return ""

    def _key(self, i, c):
        x = self._lines()[i]
        f = COLS[c][0]
        if f == 0:
            return 0 if x["ean"] in self._checked() else 1
        if f == 3:
            return x["colli"]
        return None

    def _hidden(self, i):
        q = self.search.text().strip().lower()
        if not q:
            return False
        x = self._lines()[i]
        return q not in x["desc"].lower() and q not in x["ean"]

    def _fill(self, i, k):
        x = self._lines()[i]
        vals = [self._text(i, c) for c in range(len(COLS))]
        vals[0] = ""
        self.ui.table_set_row(self.t, k, vals)
        it = self.t.item(k, 0)
        if it is not None:
            on = x["ean"] in self._checked()
            it.setData(Qt.CheckStateRole, Qt.Checked if on else Qt.Unchecked)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if on:
                for c in range(len(COLS)):
                    ci = self.t.item(k, c)
                    if ci is not None:
                        ci.setForeground(QColor(self.ui.T("warn")))

    def _clicked(self, k, c):
        if COLS[c][0] != 0:
            return
        i = self.ctl.row_at(k)
        if i is None:
            return
        e = self._lines()[i]["ean"]
        ch = self._checked()
        ch.symmetric_difference_update({e})
        self._fill(i, k)
        self._update_status()

    def _set_visible(self, on):
        ch = self._checked()
        for i in self.ctl.visible:
            e = self._lines()[i]["ean"]
            if on:
                ch.add(e)
            else:
                ch.discard(e)
        self.ctl.render()
        self._update_status()

    def _update_status(self):
        parts = []
        for sid, d in self.data.items():
            n = sum(1 for x in d["lines"] if x["ean"] in d["checked"])
            if n:
                c = sum(x["colli"] for x in d["lines"] if x["ean"] in d["checked"])
                parts.append(tr("{0} {1} 种 · {2:g} 箱", W.STORES[sid], n, c))
            b = self.chips.get(sid)
            if b is not None:
                b.setText(W.STORES[sid] + f" · {d['date']:%d/%m}" + (f" · {n}" if n else ""))
        if not self.data:
            self.status.setText(tr("最近两周没找到 DETERSIVO 订单"))
        else:
            self.status.setText(tr("没到：{0}", "；".join(parts)) if parts else tr("都到了"))

    def _ok(self):
        try:
            self.saved = save_week(self.data, self.root)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, tr("上周没到的"), tr("保存不了：{0}", e))
            return
        self.accept()
