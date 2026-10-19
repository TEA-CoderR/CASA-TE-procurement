"""
CASA&TE采购系统 v3.0 — 主程序（PySide6 / Qt）
采购是其中一个模块（门店配货、批发商比价 …）；销售与库存模块第二期从旧程序移植。
处理逻辑在 service.py / store_orders.py / reorder_service.py / forecast.py …，本文件只负责界面外壳。
"""
import os
import re
import sys
import time
import copy
import logging
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QRectF, QSize, QByteArray, QPointF
from PySide6.QtGui import (QColor, QPainter, QPen, QBrush, QFont, QIcon, QPixmap, QFontDatabase,
                           QTextCursor, QPainterPath, QKeySequence, QShortcut)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractSpinBox, 
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QLineEdit, QCheckBox,
    QHBoxLayout, QVBoxLayout, QGridLayout, QStackedWidget, QButtonGroup, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog, QMessageBox,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QRadioButton, QComboBox, QProgressBar,
    QStyledItemDelegate, QSizePolicy, QScrollArea, QGraphicsDropShadowEffect, QStyle,
)

from service import run_process
from order_check import OrderCheck
from store_order_page import StoreOrderPage
import history
from matcher import find_missing_items
from settings import load_settings, save_settings, match_rules, SETTINGS_PATH
from i18n import tr, kind_of, set_language, get_language, LANGS
from config import (ORDINE_FOLDER, ELIMINARE_FOLDER, DISTRIBUTUIRE_FOLDER,
                    LOG_DIR, LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT)

settings = load_settings()
# 旧版本把数据库密码明文存在 settings.json：启动时改成加密保存
if (settings.get("db") or {}).get("password"):
    import secure_store as _ss
    if not _ss.is_protected(settings["db"]["password"]):
        settings["db"]["password"] = _ss.protect(settings["db"]["password"])
        save_settings(settings)
set_language(settings.get("language", "zh"))
APP_NAME = tr("CASA&TE采购系统")
import theme as _theme_mod  # noqa: E402
APP_VERSION = theme_version = _theme_mod.APP_VERSION


# =====================================================================
# 日志文件
# =====================================================================
def _setup_file_logger():
    os.makedirs(LOG_DIR, exist_ok=True)
    lg = logging.getLogger("procurement")
    lg.setLevel(logging.INFO)
    lg.handlers.clear()
    h = logging.FileHandler(os.path.join(LOG_DIR, LOG_FILE), encoding="utf-8")
    h.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    lg.addHandler(h)
    return lg


file_logger = _setup_file_logger()


# =====================================================================
# 主题（颜色 / 样式表 / 图标在 theme.py）
# =====================================================================
import theme
from theme import T, build_qss, icon, app_icon, UI_FONT, NUM_FONT, ICONS, THEMES
theme.set_theme(settings.get("theme", "light"))


# =====================================================================
# 小组件
# =====================================================================
class Toggle(QCheckBox):
    """开关（替代复选框）"""

    def __init__(self, text="", checked=False, parent=None):
        super().__init__(text, parent)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(26)

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(46 + 10 + fm.horizontalAdvance(self.text()), 26)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        track = QRectF(1, 4, 42, 22)
        p.setPen(Qt.NoPen if on else QPen(QColor(T("faint")), 1.2))
        p.setBrush(QColor(T("accent")) if on else QColor(T("surface")))
        p.drawRoundedRect(track, 11, 11)
        d = 14
        x = track.right() - d - 4 if on else track.left() + 4
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T("on_accent")) if on else QColor(T("muted")))
        p.drawEllipse(QRectF(x, track.center().y() - d / 2, d, d))
        p.setPen(QColor(T("text") if self.isEnabled() else T("faint")))
        p.drawText(QRectF(54, 0, self.width() - 54, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        if self.hasFocus():
            p.setPen(QPen(QColor(T("accent")), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), 13, 13)


class Panel(QFrame):
    def __init__(self, title=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 16, 18, 16)
        self.lay.setSpacing(10)
        if title:
            lbl = QLabel(title)
            lbl.setObjectName("PanelTitle")
            self.lay.addWidget(lbl)


class Banner(QLabel):
    """一行提示条：ok / warn / err / info"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.kind = "info"
        self.hide()

    def show_msg(self, text, kind="info"):
        self.kind = kind
        self.setText(text)
        self.restyle()
        self.show()

    def restyle(self):
        self.setStyleSheet(f"background:{T(self.kind + '_soft')}; color:{T(self.kind)};")


class Kpi(QFrame):
    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(2)
        self.value = QLabel("—")
        self.value.setObjectName("KpiValue")
        self.label = QLabel(label)
        self.label.setObjectName("KpiLabel")
        lay.addWidget(self.value)
        lay.addWidget(self.label)

    def set(self, v, color=None):
        self.value.setText(str(v))
        self.value.setStyleSheet(f"color:{color};" if color else "")


class MinOrderGauge(QWidget):
    """
    起订量进度：每个起订供应商一块，箱数和金额各一条。
    浅色条 = 最低价分配时的量，实色条 = 最终下单量，竖线 = 起订线。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rules = []
        self.setMinimumHeight(60)

    def set_rules(self, rules):
        self.rules = rules or []
        self.setFixedHeight(max(60, sum(self._block_h(r) for r in self.rules)))
        self.update()

    @staticmethod
    def _block_h(ru):
        n = (1 if ru.get("min_colli") else 0) + (1 if ru.get("min_amount") else 0)
        return 30 + 26 * n + 22

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.rules:
            p.setPen(QColor(T("muted")))
            p.drawText(self.rect(), Qt.AlignCenter, tr("没有启用的起订规则，或本次没有对应的供应商文件"))
            return
        w = self.width()
        y = 4
        f_name = QFont(UI_FONT, 11, QFont.DemiBold)
        f_small = QFont(UI_FONT, 9)
        f_num = QFont(NUM_FONT, 10)
        for ru in self.rules:
            y0 = y
            status = ru.get("status", "")
            kind = kind_of(status)
            status = tr(status)
            p.setFont(f_name)
            p.setPen(QColor(T("text")))
            name = f"{ru.get('name')}"
            p.drawText(QRectF(0, y, w * 0.6, 24), Qt.AlignVCenter | Qt.AlignLeft, name)
            # 状态胶囊
            p.setFont(f_small)
            tw = p.fontMetrics().horizontalAdvance(status) + 20
            pill = QRectF(w - tw, y + 2, tw, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T(kind + "_soft")))
            p.drawRoundedRect(pill, 11, 11)
            p.setPen(QColor(T(kind)))
            p.drawText(pill, Qt.AlignCenter, status)
            y += 30
            bars = []
            if ru.get("min_colli"):
                bars.append((tr("箱数"), ru["before_colli"], ru["after_colli"], ru["min_colli"], tr("{:,.0f} 箱")))
            if ru.get("min_amount"):
                bars.append((tr("金额"), ru["before_amount"], ru["after_amount"], ru["min_amount"], "€{:,.2f}"))
            for label, before, after, need, fmt in bars:
                p.setFont(f_small)
                p.setPen(QColor(T("muted")))
                p.drawText(QRectF(0, y, 44, 18), Qt.AlignVCenter | Qt.AlignLeft, label)
                bx, bw = 48, w - 48 - 290
                scale = max(need * 1.25, after, before, 1)
                track = QRectF(bx, y + 4, bw, 10)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("track")))
                p.drawRoundedRect(track, 5, 5)
                p.setBrush(QColor(T("ghost")))
                p.drawRoundedRect(QRectF(bx, y + 4, bw * min(before / scale, 1), 10), 5, 5)
                met = after >= need - 1e-9
                p.setBrush(QColor(T("accent") if met or after == 0 else T("err")))
                p.drawRoundedRect(QRectF(bx, y + 6, bw * min(after / scale, 1), 6), 3, 3)
                tx = bx + bw * need / scale
                p.setPen(QPen(QColor(T("text")), 2))
                p.drawLine(QPointF(tx, y), QPointF(tx, y + 18))
                p.setFont(f_num)
                p.setPen(QColor(T("text")))
                txt = f"{fmt.format(before)}  →  {fmt.format(after)}   / {fmt.format(need)}"
                p.drawText(QRectF(w - 280, y - 2, 280, 22), Qt.AlignVCenter | Qt.AlignRight, txt)
                y += 26
            y = y0 + self._block_h(ru)
            if ru is not self.rules[-1]:
                p.setPen(QPen(QColor(T("line")), 1))
                p.drawLine(0, int(y - 12), w, int(y - 12))


class StatusDelegate(QStyledItemDelegate):
    """表格里的状态胶囊"""

    def paint(self, p, opt, idx):
        text = idx.data() or ""
        if not text:
            return super().paint(p, opt, idx)
        kind = kind_of(text)
        text = tr(text)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if opt.state & QStyle.State_Selected:
            p.fillRect(opt.rect, QColor(T("accent_soft")))
        fm = opt.fontMetrics
        w = min(fm.horizontalAdvance(text) + 20, opt.rect.width() - 8)
        r = QRectF(opt.rect.left() + 6, opt.rect.center().y() - 11, w, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T(kind + "_soft")))
        p.drawRoundedRect(r, 11, 11)
        p.setPen(QColor(T(kind)))
        p.drawText(r, Qt.AlignCenter, fm.elidedText(text, Qt.ElideRight, int(w - 12)))
        p.setPen(QPen(QColor(T("line")), 1))
        p.drawLine(opt.rect.bottomLeft(), opt.rect.bottomRight())
        p.restore()

    def sizeHint(self, opt, idx):
        sz = super().sizeHint(opt, idx)
        return QSize(opt.fontMetrics.horizontalAdvance(idx.data() or "") + 40, sz.height())


class _BgDelegate(QStyledItemDelegate):
    """样式表里的 QTableWidget::item 会盖住单元格自己的底色，这里先把底色画上"""
    def paint(self, p, opt, idx):
        bg = idx.data(Qt.BackgroundRole)
        if bg is not None:
            p.fillRect(opt.rect, bg)
        if opt.state & QStyle.State_HasFocus:      # 表格可以点进去（为了 Ctrl+C 复制），但不画虚线焦点框
            from PySide6.QtWidgets import QStyleOptionViewItem
            opt = QStyleOptionViewItem(opt)
            opt.state &= ~QStyle.State_HasFocus
        super().paint(p, opt, idx)


def make_table(headers, stretch_col=None, numeric_cols=()):
    t = QTableWidget(0, len(headers))
    t.setItemDelegate(_BgDelegate(t))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setShowGrid(False)
    t.setAlternatingRowColors(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setDefaultSectionSize(38)
    t.setWordWrap(False)
    t.setTextElideMode(Qt.ElideRight)
    t.setFocusPolicy(Qt.ClickFocus)       # 点过以后 Ctrl+C 能复制选中的行
    hh = t.horizontalHeader()
    hh.setHighlightSections(False)
    hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    hh.setSectionResizeMode(QHeaderView.ResizeToContents)
    if stretch_col is not None:
        hh.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    for c in numeric_cols:
        t.horizontalHeaderItem(c).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    t._numeric = set(numeric_cols)
    return t


def table_set_row(t, row, values, bold=False, color=None):
    for c, v in enumerate(values):
        it = QTableWidgetItem("" if v is None else str(v))
        if c in t._numeric:
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            f = QFont(NUM_FONT, 10)
            f.setBold(bold)
            it.setFont(f)
        elif bold:
            f = it.font()
            f.setBold(True)
            it.setFont(f)
        if color:
            it.setForeground(QColor(color))
        t.setItem(row, c, it)


# =====================================================================
# 文件夹检查
# =====================================================================
def _excel_files(path):
    if not os.path.isdir(path):
        return None
    return sorted(f for f in os.listdir(path) if f.lower().endswith((".xls", ".xlsx")) and not f.startswith("~$"))


def scan_folder(root_path):
    info = {"ok": False, "errors": [], "warnings": [], "ordine": None, "eliminare": [], "suppliers": [], "temp": []}
    if not root_path or not os.path.isdir(root_path):
        info["errors"].append(tr("请选择总文件夹"))
        return info
    ordine = _excel_files(os.path.join(root_path, ORDINE_FOLDER))
    elim = _excel_files(os.path.join(root_path, ELIMINARE_FOLDER))
    dist = _excel_files(os.path.join(root_path, DISTRIBUTUIRE_FOLDER))
    if ordine is None:
        info["errors"].append(tr("缺少 {0} 文件夹", ORDINE_FOLDER))
    elif not ordine:
        info["errors"].append(tr("{0} 里没有 Excel 文件", ORDINE_FOLDER))
    else:
        info["ordine"] = ordine[0]
        if len(ordine) > 1:
            info["warnings"].append(tr("{0} 里有 {1} 个文件，只处理第一个：{2}", ORDINE_FOLDER, len(ordine), ordine[0]))
    if elim is None:
        info["errors"].append(tr("缺少 {0} 文件夹", ELIMINARE_FOLDER))
    else:
        info["eliminare"] = elim
    if dist is None:
        info["errors"].append(tr("缺少 {0} 文件夹", DISTRIBUTUIRE_FOLDER))
    else:
        for f in dist:
            low = f.lower()
            if ".calc." in low or low.endswith(".xls.xlsx"):
                info["temp"].append(f)
            elif "缺失报告" in f or "missing" in low:
                continue
            else:
                info["suppliers"].append(f)
        if not info["suppliers"]:
            info["errors"].append(tr("{0} 里没有供应商报价文件", DISTRIBUTUIRE_FOLDER))
        if info["temp"]:
            info["warnings"].append(tr("有 {0} 个上次留下的临时文件（.calc.xlsx 等），处理时会自动忽略", len(info['temp'])))
    if info["ordine"] and info["ordine"] in (info["eliminare"] or []):
        info["warnings"].append(tr("eliminare 里有和主订单同名的文件，请确认是否放错"))
    info["ok"] = not info["errors"]
    return info


class SupplierDropDialog(QDialog):
    """拖进来的供应商报价：每个新文件替换哪个旧文件（自动认出的已经选好，认不出的让用户选）"""

    def __init__(self, parent, plan, existing):
        super().__init__(parent)
        self.setWindowTitle(tr("更新供应商报价"))
        self.plan = plan
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        t = QLabel(tr("每个新文件替换哪一家的旧报价？旧文件会挪进 distribuire\\_旧文件（不删除）。"))
        t.setWordWrap(True)
        lay.addWidget(t)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        hd1, hd2 = QLabel(tr("新文件")), QLabel(tr("替换"))
        for h in (hd1, hd2):
            h.setObjectName("Faint")
        g.addWidget(hd1, 0, 0)
        g.addWidget(hd2, 0, 1)
        self.combos = []
        for i, p in enumerate(plan, 1):
            name = os.path.basename(p["src"])
            l = QLabel(name)
            l.setToolTip(p["src"])
            g.addWidget(l, i, 0)
            cb = QComboBox()
            cb.addItem(tr("新供应商（不替换）"), "")
            for e in existing:
                cb.addItem(tr("替换 {0}", e), e)
            if p["replace"] and cb.findData(p["replace"]) < 0:
                cb.addItem(tr("替换 {0}", p["replace"]), p["replace"])
            cb.setCurrentIndex(max(0, cb.findData(p["replace"] or "")))
            cb.setMinimumWidth(280)
            g.addWidget(cb, i, 1)
            note = QLabel(tr("自动认出") if p["auto"] else tr("认不出是哪家，请选"))
            note.setObjectName("Faint" if p["auto"] else "")
            if not p["auto"]:
                note.setStyleSheet(f"color:{T('warn')};")
            g.addWidget(note, i, 2)
            self.combos.append(cb)
        lay.addLayout(g)
        self.err = QLabel("")
        self.err.setStyleSheet(f"color:{T('err')};")
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(tr("放进去"))
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumWidth(640)

    def result_plan(self):
        return [dict(p, replace=cb.currentData() or None) for p, cb in zip(self.plan, self.combos)]

    def _ok(self):
        picks = [cb.currentData() for cb in self.combos if cb.currentData()]
        if len(picks) != len(set(picks)):
            self.err.setText(tr("两个新文件不能替换同一家，请改一下。"))
            return
        self.accept()


def describe_condition(r):
    items = []
    if float(r.get("min_colli") or 0):
        items.append(tr("{0:g} 箱", float(r['min_colli'])))
    if float(r.get("min_amount") or 0):
        items.append(f"€{float(r['min_amount']):g}")
    if not items:
        return tr("无条件")
    return (tr(" 且 ") if r.get("mode", "all") == "all" else tr(" 或 ")).join(items)


def open_path(path):
    if not path or not os.path.exists(path):
        QMessageBox.information(None, APP_NAME, tr("找不到：\n{0}", path))
        return
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        QMessageBox.warning(None, APP_NAME, tr("打开失败：{0}", e))


def log_level(msg):
    """按日志开头的符号判断颜色（和语言无关）"""
    m = msg.strip()
    if m.startswith("❌") or m.startswith("[") and "❌" in m[:20]:
        return "err"
    if "⚠" in m[:6]:
        return "warn"
    if "✔" in m[:40]:
        return "ok"
    if m.startswith("[") and not m.startswith(tr("[监控]")):
        return "info"
    return ""


# =====================================================================
# 后台任务 → 界面 的信号
# =====================================================================
class Bridge(QObject):
    log = Signal(str)
    progress = Signal(int, str)
    done = Signal(object)
    missing_done = Signal(object, object, bool)
    decide = Signal(object)
    choose = Signal(object)
    web_done = Signal(object)
    rewrite_done = Signal(object)
    db_state = Signal(bool)


# =====================================================================
# 起订规则编辑
# =====================================================================
class RuleDialog(QDialog):
    def __init__(self, parent, rule, supplier_files):
        super().__init__(parent)
        self.setWindowTitle(tr("起订规则"))
        self.setMinimumWidth(500)
        self.result_rule = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(14)

        title = QLabel(tr("编辑起订规则") if rule.get("keyword") else tr("新增起订规则"))
        title.setObjectName("PageTitle")
        title.setStyleSheet("font-size:18px;")
        lay.addWidget(title)

        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(10)
        self.name = QLineEdit(rule.get("name", ""))
        self.name.setPlaceholderText(tr("例如 MEGA CASH"))
        self.kw = QComboBox()
        self.kw.setEditable(True)
        self.kw.addItems([os.path.splitext(x)[0] for x in supplier_files])
        self.kw.setCurrentText(rule.get("keyword", ""))
        self.kw.lineEdit().setPlaceholderText(tr("例如 mega"))
        self.kw.activated.connect(lambda _: (not self.name.text().strip()) and self.name.setText(self.kw.currentText()))
        self.colli = QDoubleSpinBox()
        self.colli.setRange(0, 1_000_000)
        self.colli.setDecimals(0)
        self.colli.setSingleStep(10)
        self.colli.setSuffix(tr(" 箱"))
        self.colli.setValue(float(rule.get("min_colli") or 0))
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 10_000_000)
        self.amount.setDecimals(2)
        self.amount.setSingleStep(50)
        self.amount.setPrefix("€ ")
        self.amount.setValue(float(rule.get("min_amount") or 0))

        def hint(t):
            l = QLabel(t)
            l.setObjectName("Hint")
            l.setWordWrap(True)
            return l

        g.addWidget(QLabel(tr("供应商名称")), 0, 0)
        g.addWidget(self.name, 0, 1)
        g.addWidget(QLabel(tr("文件名关键字")), 1, 0)
        g.addWidget(self.kw, 1, 1)
        g.addWidget(hint(tr("文件名里有这几个字，就算这家。比如 mega。")), 2, 1)
        g.addWidget(QLabel(tr("起订箱数")), 3, 0)
        g.addWidget(self.colli, 3, 1)
        g.addWidget(QLabel(tr("起订金额")), 4, 0)
        g.addWidget(self.amount, 4, 1)
        g.addWidget(hint(tr("填 0 就是不限。金额 = 净价 × 箱数。")), 5, 1)
        lay.addLayout(g)

        mode_box = QHBoxLayout()
        self.m_all = QRadioButton(tr("箱数和金额都要达到"))
        self.m_any = QRadioButton(tr("达到其中一个即可"))
        (self.m_all if rule.get("mode", "all") == "all" else self.m_any).setChecked(True)
        mode_box.addWidget(QLabel(tr("条件")))
        mode_box.addSpacing(30)
        mode_box.addWidget(self.m_all)
        mode_box.addWidget(self.m_any)
        mode_box.addStretch()
        lay.addLayout(mode_box)

        def money_box(v):
            b = QDoubleSpinBox()
            b.setRange(0, 1_000_000)
            b.setDecimals(2)
            b.setSingleStep(5)
            b.setPrefix("€ ")
            b.setFixedWidth(150)
            b.setValue(float(v or 0))
            return b

        lim = QGridLayout()
        lim.setHorizontalSpacing(14)
        lim.setVerticalSpacing(8)
        self.manual_extra = money_box(rule.get("manual_extra", 5))
        self.drop_extra = money_box(rule.get("drop_extra", 30))
        lim.addWidget(QLabel(tr("手动调整线")), 0, 0)
        lim.addWidget(self.manual_extra, 0, 1)
        lim.addWidget(hint(tr("多花超过这个数，打开手动调整。")), 0, 2)
        lim.addWidget(QLabel(tr("放弃供应商线")), 1, 0)
        lim.addWidget(self.drop_extra, 1, 1)
        lim.addWidget(hint(tr("多花超过这个数，问你要不要放弃这家。")), 1, 2)
        lim.setColumnStretch(2, 1)
        lay.addLayout(lim)
        lay.addWidget(hint(tr("填 0 就是不用这条线。")))
        self.enabled = Toggle(tr("启用这条规则"), rule.get("enabled", True))
        lay.addWidget(self.enabled)

        bb = QDialogButtonBox()
        ok = bb.addButton(tr("保存规则"), QDialogButtonBox.AcceptRole)
        ok.setObjectName("Primary")
        bb.addButton(tr("取消"), QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addSpacing(6)
        lay.addWidget(bb)

    def _ok(self):
        kw = self.kw.currentText().strip()
        if not kw:
            QMessageBox.warning(self, tr("起订规则"), tr("请填写文件名关键字。"))
            return
        colli, amount = self.colli.value(), self.amount.value()
        if colli == 0 and amount == 0:
            if QMessageBox.question(self, tr("起订规则"), tr("箱数和金额都是 0，这条规则不会起作用。仍然保存？")) != QMessageBox.Yes:
                return
        m, d = self.manual_extra.value(), self.drop_extra.value()
        if m and d and m >= d:
            QMessageBox.warning(self, tr("起订规则"), tr("手动调整线应该比放弃供应商线低，例如手动 €5、放弃 €30。"))
            return
        self.result_rule = {"enabled": self.enabled.isChecked(), "name": self.name.text().strip() or kw,
                            "keyword": kw, "min_colli": colli, "min_amount": amount,
                            "mode": "all" if self.m_all.isChecked() else "any",
                            "allow_drop": True, "manual_extra": self.manual_extra.value(),
                            "drop_extra": self.drop_extra.value()}
        self.accept()


# =====================================================================
# 凑单决定窗口：凑单太贵 / 超过设定值 / 凑不够 时让操作员选择
# =====================================================================
def fmt_unit(price, pack=None):
    """单件价，箱规不是 1 时带上「×每箱件数」"""
    if price is None:
        return ""
    t = f"{price:,.2f}"
    if pack not in (None, 1, 1.0):
        t += f" ×{pack:g}"
    return t


def _unit(r, s):
    return (r.get("unit") or r["prices"]).get(s) if s else None


def _pack(r, s):
    return (r.get("packs") or {}).get(s) if s else None


def _amt(r, s):
    return (r.get("amt") or r["prices"]).get(s) if s else None


def short(name):
    s = str(name or "")
    for ext in (".xlsx", ".xls"):
        if s.lower().endswith(ext):
            s = s[: -len(ext)]
    return s


class DecisionDialog(QDialog):
    """返回 self.choice: 'fill' / 'drop' / 'manual' / None(取消处理)"""

    def __init__(self, parent, info):
        super().__init__(parent)
        self.choice = None
        name = info["name"]
        self.setWindowTitle(tr("{0}：需要你决定", name))
        self.setMinimumWidth(820)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(14)

        t = QLabel(tr("{0} 没有达到起订条件", name))
        t.setObjectName("PageTitle")
        t.setStyleSheet("font-size:19px;")
        lay.addWidget(t)
        need = describe_condition(info)
        sub = QLabel(tr("起订条件 {0}。按最低价只分到 {1:,.0f} 箱 / €{2:,.2f}。", need, info['before_colli'], info['before_amount']))
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        warn = Banner()
        warn.show_msg(tr("；").join(info["reasons"]) + tr("。"), "warn")
        lay.addWidget(warn)

        # 对比表
        grid = QGridLayout()
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)
        fill_ok = info["fill_ok"]
        # 放弃会有商品买不到时，把这些商品的价值算进去再比，否则「放弃」只是看起来便宜
        drop_cmp = info["drop_cmp"]
        drop_allowed = info["drop_allowed"]
        if not drop_allowed:
            cheaper = "fill"
        else:
            cheaper = "drop" if (not fill_ok or drop_cmp < info["fill_total"]) else "fill"
        heads = ["", tr("继续凑单") if fill_ok else tr("按最低价下单"), tr("放弃 {0}", name) + ("" if drop_allowed else tr("（会少买商品）"))]
        rows = [
            (tr("采购总额"), f"€{info['fill_total']:,.2f}", f"€{info['drop_total']:,.2f}"),
            (tr("比只按最低价多花"), f"€{info['fill_extra']:,.2f}" if fill_ok else "—",
             f"€{info['drop_extra']:,.2f}" + (tr("（少买了商品）") if info["lost_count"] else "")),
            (tr("在 {0} 下单", name), tr("{0:,.0f} 箱 / €{1:,.2f}", info['fill_colli'], info['fill_amount'])
             + ("" if fill_ok else tr("（达不到起订量）")), tr("不下单")),
        ]
        if info["lost_count"]:
            rows.append((tr("没有其他供应商的商品"), tr("全部采购"),
                         tr("{0} 个不采购（约 €{1:,.2f}）", info['lost_count'], info['lost_value'])))
        cells = []
        for c, h in enumerate(heads):
            lb = QLabel(h)
            lb.setStyleSheet(f"font-weight:600; color:{T('muted')}; padding:8px 14px;")
            if drop_allowed and ((c == 1 and cheaper == "fill") or (c == 2 and cheaper == "drop")):
                lb.setText(h + tr("　更省钱"))
                lb.setStyleSheet(f"font-weight:700; color:{T('accent')}; padding:8px 14px;")
            grid.addWidget(lb, 0, c)
        for r, (label, a, b) in enumerate(rows, 1):
            for c, txt in enumerate((label, a, b)):
                lb = QLabel(txt)
                lb.setWordWrap(c > 0)
                hl = (c == 1 and cheaper == "fill") or (c == 2 and cheaper == "drop")
                css = f"padding:10px 14px; border-top:1px solid {T('line')};"
                if c == 0:
                    css += f"color:{T('muted')};"
                else:
                    css += f"font-family:'{NUM_FONT}'; font-size:15px;"
                if hl and drop_allowed:
                    css += f"background:{T('accent_soft')};"
                if c == 2 and not drop_allowed:
                    css += f"color:{T('err')};"
                lb.setStyleSheet(css)
                grid.addWidget(lb, r, c)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        box = QFrame()
        box.setObjectName("Panel")
        box.setLayout(grid)
        lay.addWidget(box)

        diff = abs(info["fill_total"] - drop_cmp) if (fill_ok and drop_allowed) else None
        if not drop_allowed:
            nb = Banner()
            nb.show_msg(tr("注意：有 {0} 个商品只有 {1} 有货（约 €{2:,.2f}），放弃 {3} 后这些商品这次就订不到了。仍然可以放弃，点按钮后会先列出这些商品让你确认。", info['lost_count'], name, info['lost_value'], name), "err")
            lay.addWidget(nb)
            if not fill_ok:
                tip = QLabel(tr("怎么凑都达不到 {0} 的起订量。可以按最低价照常在 {1} 下单（之后自己和供应商沟通），或者点「手动调整」看看。", name, name))
                tip.setObjectName("Hint")
                tip.setWordWrap(True)
                lay.addWidget(tip)
        if diff is not None:
            if info["lost_count"]:
                txt = (tr("放弃 {0} 会有 {1} 个商品没有其他供应商、买不到。把它们按 {0} 的价格算进去，继续凑单要多花 €{2:,.2f}。",
                          name, info['lost_count'], diff) if cheaper == "drop" else
                       tr("放弃 {0} 会有 {1} 个商品没有其他供应商、买不到。把它们按 {0} 的价格算进去，放弃要多花 €{2:,.2f}。",
                          name, info['lost_count'], diff))
            else:
                txt = (tr("继续凑单要多花 €{0:,.2f}。", diff) if cheaper == "drop"
                       else tr("放弃该供应商要多花 €{0:,.2f}。", diff))
            tip = QLabel(txt + " " + tr("分配完以后还可以手动调整。"))
            tip.setObjectName("Hint")
            tip.setWordWrap(True)
            lay.addWidget(tip)

        btns = QHBoxLayout()
        cancel = QPushButton(tr("取消处理"))
        cancel.setObjectName("Danger")
        cancel.clicked.connect(self.reject)
        b_fill = QPushButton(tr("继续凑单") if fill_ok else tr("按最低价下单"))
        b_fill.setEnabled(fill_ok or not drop_allowed)
        b_fill.clicked.connect(lambda: self._pick("fill"))
        b_drop = QPushButton(tr("放弃 {0}", name))
        b_drop.setEnabled(info["drop_ok"])
        if not drop_allowed:
            b_drop.setObjectName("DangerSolid")
            b_drop.setToolTip(tr("有 {0} 个商品只有 {1} 有货，放弃后订不到", info['lost_count'], name))
            b_drop.clicked.connect(lambda: self._confirm_drop(info))
        else:
            b_drop.clicked.connect(lambda: self._pick("drop"))
        (b_fill if cheaper == "fill" else b_drop).setObjectName("Primary")
        btns.addWidget(cancel)
        btns.addStretch()
        btns.addWidget(b_drop)
        btns.addWidget(b_fill)
        lay.addLayout(btns)

    def showEvent(self, e):
        super().showEvent(e)
        self.adjustSize()

    def _pick(self, c):
        self.choice = c
        self.accept()

    def _confirm_drop(self, info):
        name = info["name"]
        items = info.get("lost_items") or []
        lines = [tr("{0}　{1}　{2:g} 箱", it['ean'], it['articolo'][:40], it['qty']) for it in items[:15]]
        if len(items) > 15:
            lines.append(tr("……还有 {0} 个", len(items) - 15))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("确认放弃 {0}", name))
        box.setText(tr("放弃 {0} 后，下面 {1} 个商品这次订不到（约 €{2:,.2f}）：", name, len(items), info['lost_value']))
        box.setInformativeText("\n".join(lines) +
                               tr("\n\n这些商品只有 {0} 有报价。它们不会写进任何供应商的订单，会出现在缺失报告里。\n\n确定放弃吗？", name))
        ok = box.addButton(tr("确定放弃，这些商品不采购"), QMessageBox.DestructiveRole)
        ok.setStyleSheet(f"background:{T('err')}; color:#FFFFFF; border:none; font-weight:600; padding:7px 14px;")
        box.addButton(tr("返回"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is ok:
            self._pick("drop")


class SupplierComboDelegate(QStyledItemDelegate):
    """手动调整表格里「当前供应商」列：点击后下拉选择"""

    def __init__(self, dlg):
        super().__init__(dlg.table)
        self.dlg = dlg

    def createEditor(self, parent, opt, idx):
        r = self.dlg.rows[idx.data(Qt.UserRole)]
        cb = QComboBox(parent)
        for s, p in r["prices"].items():
            if p is not None:
                cb.addItem(f"{short(s)}   €{fmt_unit(_unit(r, s), _pack(r, s))}", s)
        cb.activated.connect(lambda _: self.commitData.emit(cb))
        return cb

    def setEditorData(self, cb, idx):
        r = self.dlg.rows[idx.data(Qt.UserRole)]
        cur = self.dlg.assign.get(r["row"])
        i = cb.findData(cur)
        if i >= 0:
            cb.setCurrentIndex(i)
        cb.showPopup()

    def setModelData(self, cb, model, idx):
        self.dlg.set_supplier(idx.data(Qt.UserRole), cb.currentData())


class ManualDialog(QDialog):
    """手动调整每个商品的供应商。确认后返回 self.result（{行号: 供应商}）"""

    def __init__(self, parent, rows, rules, baseline, proposed, notes=None, edit_mode=False):
        super().__init__(parent)
        self.edit_mode = edit_mode
        self.setWindowTitle(tr("修改分配") if edit_mode else tr("手动调整供应商"))
        self.resize(1180, 760)
        self.rows = rows
        self.rules = rules
        self.baseline = dict(baseline)
        self.proposed = dict(proposed)
        self.assign = dict(proposed)
        self.result = None
        self.base_cost = self._cost(self.baseline)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        t = QLabel(tr("修改分配") if edit_mode else tr("手动调整供应商"))
        t.setObjectName("PageTitle")
        t.setStyleSheet("font-size:20px;")
        lay.addWidget(t)
        s = QLabel(tr("点「当前供应商」那一格可以改成别的报价。上面的起订情况和采购总额会实时更新。确认后才会写入主订单和供应商文件。"))
        s.setObjectName("Muted")
        s.setWordWrap(True)
        lay.addWidget(s)
        if notes:
            nb = Banner()
            nb.show_msg(tr("为什么打开这个窗口：{0}。程序已经给出最省钱的凑单方案，你可以按实际情况改，比如把不想换的商品改回原供应商。",
                           "; ".join(notes)), "warn")
            lay.addWidget(nb)

        # 起订情况 + 总额
        self.status_box = QHBoxLayout()
        self.status_box.setSpacing(10)
        self.status_labels = []
        for ru in rules:
            lb = QLabel()
            lb.setObjectName("Banner")
            lb.setTextFormat(Qt.RichText)
            self.status_labels.append((ru, lb))
            self.status_box.addWidget(lb)
        self.total_lbl = QLabel()
        self.total_lbl.setObjectName("Banner")
        self.status_box.addWidget(self.total_lbl)
        self.status_box.addStretch()
        lay.addLayout(self.status_box)

        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(tr("筛选 EAN、品名或供应商"))
        self.filter.setFixedWidth(280)
        self.filter.textChanged.connect(self.apply_filter)
        self.only_multi = Toggle(tr("只看有多个报价的商品"), True)
        self.only_multi.toggled.connect(self.apply_filter)
        self.only_changed = Toggle(tr("只看和最低价不同的"), False)
        self.only_changed.toggled.connect(self.apply_filter)
        bar.addWidget(self.filter)
        bar.addSpacing(10)
        bar.addWidget(self.only_multi)
        bar.addWidget(self.only_changed)
        bar.addStretch()
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("Faint")
        bar.addWidget(self.count_lbl)
        exp = QPushButton(tr("导出 Excel"))
        exp.clicked.connect(lambda: self.ctl.export(self, "分配明细.xlsx", tr("分配明细")))
        bar.addWidget(exp)
        lay.addLayout(bar)

        self.table = make_table([tr("行"), "EAN", "ARTICOLO", tr("箱数"), tr("最低价供应商"), tr("件价"),
                                 tr("当前供应商"), tr("当前件价"), tr("比最低价多花 €")],
                                stretch_col=2, numeric_cols=(0, 3, 5, 7, 8))
        self.table.setFocusPolicy(Qt.StrongFocus)
        # 只有「当前供应商」这一列能改（而且要有不止一家报价）；其他格子只能看、复制
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.setItemDelegateForColumn(6, SupplierComboDelegate(self))
        lay.addWidget(self.table, 1)
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(self.table, lambda: len(self.rows), self._text, self._fill_row,
                                   hide=self._hidden, numeric=(0, 3, 8))
        self.ctl.rendered.connect(lambda n, tot: self.count_lbl.setText(
            tr("显示 {0} / {1} 行 · 点表头排序，右键表头筛选", n, tot)))
        # 按供应商筛选：选 MEGA 就只看 MEGA 的商品
        from erp_widgets import ColumnFilterBox
        fbar = QHBoxLayout()
        fbar.setSpacing(18)
        fbar.addWidget(ColumnFilterBox(self.ctl, 4, tr("最低价供应商")))
        fbar.addWidget(ColumnFilterBox(self.ctl, 6, tr("当前供应商")))
        b_clear = QPushButton(tr("清除筛选"))
        b_clear.setObjectName("Link")
        b_clear.clicked.connect(self.ctl.clear_filters)
        fbar.addWidget(b_clear)
        fbar.addStretch()
        lay.insertLayout(lay.indexOf(self.table), fbar)
        self._fill_table()

        btns = QHBoxLayout()
        b_base = QPushButton(tr("恢复默认（全部按最低价）"))
        b_base.clicked.connect(lambda: self._reset(self.baseline, tr("已恢复为全部按最低价")))
        b_auto = QPushButton(tr("恢复为当前已写入的分配") if edit_mode else tr("恢复程序算出的方案"))
        b_auto.clicked.connect(lambda: self._reset(self.proposed, tr("已恢复为当前已写入的分配") if edit_mode
                                                   else tr("已恢复为程序算出的方案")))
        cancel = QPushButton(tr("取消") if edit_mode else tr("取消处理"))
        if not edit_mode:
            cancel.setObjectName("Danger")
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("确认并重新写入") if edit_mode else tr("确认并写入"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self._ok)
        btns.addWidget(b_base)
        btns.addWidget(b_auto)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)
        self.refresh_status()
        self.apply_filter()

    def _cost(self, a):
        tot = 0.0
        for r in self.rows:
            s = a.get(r["row"])
            if s:
                try:
                    tot += float(r["qty"] or 0) * (r["prices"].get(s) or 0)
                except (TypeError, ValueError):
                    pass
        return tot

    def _money(self, a):
        """真实采购金额：箱数 × 每箱件数 × 件价"""
        tot = 0.0
        for r in self.rows:
            s = a.get(r["row"])
            if s:
                try:
                    tot += float(r["qty"] or 0) * (_amt(r, s) or 0)
                except (TypeError, ValueError):
                    pass
        return tot

    def _qty(self, r):
        try:
            return max(0.0, float(r["qty"] or 0))
        except (TypeError, ValueError):
            return 0.0

    def _fill_table(self):
        self.ctl.render()

    def _vals(self, i):
        r = self.rows[i]
        q = self._qty(r)
        b = self.baseline.get(r["row"])
        c = self.assign.get(r["row"])
        pb = r["prices"].get(b) if b else None
        pc = r["prices"].get(c) if c else None
        extra = (pc - pb) * q if (pb is not None and pc is not None) else None
        n_offers = sum(1 for p in r["prices"].values() if p is not None)
        return [str(r["row"]), r.get("ean", ""), r.get("articolo", ""), f"{q:g}",
                short(b) if b else tr("无报价"), fmt_unit(_unit(r, b), _pack(r, b)),
                short(c) if c else tr("无报价"), fmt_unit(_unit(r, c), _pack(r, c)),
                f"{extra:+,.2f}" if extra else ""], n_offers, extra, b, c

    def _text(self, i, col):
        return self._vals(i)[0][col]

    def _fill_row(self, i, k):
        vals, n_offers, extra, b, c = self._vals(i)
        if n_offers > 1 and c:
            vals[6] += "　▾"
        table_set_row(self.table, k, vals, color=None)
        for col in range(9):                      # 先全部设成不能改
            it = self.table.item(k, col)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.item(k, 6).setData(Qt.UserRole, i)
        if n_offers > 1:
            it = self.table.item(k, 6)
            it.setFlags(it.flags() | Qt.ItemIsEditable)
            it.setToolTip(tr("点击选择其他供应商"))
        if c != b:
            for col in range(9):
                self.table.item(k, col).setBackground(QColor(T("accent_soft")))
        if extra and extra > 0:
            self.table.item(k, 8).setForeground(QColor(T("warn")))

    def _set_row(self, i):
        self.ctl.update_row(i)

    def _cell_clicked(self, k, col):
        """点「当前供应商」那一格直接弹出下拉（其他列点了只是选中）"""
        it = self.table.item(k, col)
        if col == 6 and it is not None and it.flags() & Qt.ItemIsEditable:
            self.table.editItem(it)

    def set_supplier(self, i, s):
        r = self.rows[i]
        if s and s != self.assign.get(r["row"]):
            self.assign[r["row"]] = s
            self._set_row(i)
            self.refresh_status()

    def _reset(self, a, msg):
        self.assign = dict(a)
        self._fill_table()
        self.refresh_status()
        self.apply_filter()
        self.total_lbl.setToolTip(msg)

    def refresh_status(self):
        from min_order_optimizer import _satisfied
        for ru, lb in self.status_labels:
            c = a = 0.0
            for r in self.rows:
                if self.assign.get(r["row"]) == ru["target"]:
                    q = self._qty(r)
                    c += q
                    a += q * (_amt(r, ru["target"]) or 0)
            if c == 0:
                kind, st = "info", tr("不下单")
            elif _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"]):
                kind, st = "ok", tr("已达标")
            else:
                kind, st = "err", tr("未达标")
            lb.setText(tr("<b>{0}</b>　{1:,.0f} 箱 / €{2:,.2f}　要求 {3}　<b>{4}</b>", ru['name'], c, a, describe_condition(ru), st))
            lb.setStyleSheet(f"background:{T(kind + '_soft')}; color:{T(kind)};")
        cost = self._money(self.assign)
        diff = self._cost(self.assign) - self.base_cost
        n = sum(1 for r in self.rows if self.assign.get(r["row"]) != self.baseline.get(r["row"]))
        self.total_lbl.setText(tr("采购总额 €{0:,.2f}　比只按最低价 {1:+,.2f}　改动 {2} 个商品", cost, diff, n))
        self.total_lbl.setStyleSheet(f"background:{T('surface2')}; color:{T('text')}; border:1px solid {T('line')};")

    def _hidden(self, i):
        """搜索框和两个开关（表头的按列筛选另外算）"""
        r = self.rows[i]
        n_offers = sum(1 for p in r["prices"].values() if p is not None)
        if self.only_multi.isChecked() and n_offers < 2:
            return True
        if self.only_changed.isChecked() and self.assign.get(r["row"]) == self.baseline.get(r["row"]):
            return True
        text = self.filter.text().strip().lower()
        if text:
            hay = f"{r.get('ean', '')} {r.get('articolo', '')} {self.assign.get(r['row'], '')}".lower()
            return text not in hay
        return False

    def apply_filter(self, *_):
        if hasattr(self, "ctl"):
            self.ctl.render()

    def _ok(self):
        from min_order_optimizer import _satisfied
        bad = []
        for ru, _ in self.status_labels:
            c = a = 0.0
            for r in self.rows:
                if self.assign.get(r["row"]) == ru["target"]:
                    q = self._qty(r)
                    c += q
                    a += q * (_amt(r, ru["target"]) or 0)
            if c > 0 and not _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"]):
                bad.append(ru["name"])
        if bad and QMessageBox.question(self, tr("手动调整"), tr("{0} 还没有达到起订条件。仍然按现在的方案写入？", ", ".join(bad))) \
                != QMessageBox.Yes:
            return
        self.result = dict(self.assign)
        self.accept()


# =====================================================================
# 缺失商品列表 / 方案对比 / 方案设置
# =====================================================================
MISSING_REASONS = ("没有任何报价", "只有被排除的供应商有报价", "起订量放弃了有报价的供应商")


class MissingDialog(QDialog):
    """没有分到供应商（买不到）的商品：按列排序、右键表头筛选、导出 Excel"""

    def __init__(self, parent, items, title=None):
        super().__init__(parent)
        self.items = list(items or [])
        self.setWindowTitle(title or tr("缺失商品"))
        self.resize(1080, 680)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        t = QLabel(title or tr("缺失商品"))
        t.setObjectName("PageTitle")
        t.setStyleSheet("font-size:18px;")
        lay.addWidget(t)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索 EAN 或品名"))
        self.search.setFixedWidth(280)
        bar.addWidget(self.search)
        bar.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        exp = QPushButton(tr("导出 Excel"))
        bar.addWidget(exp)
        lay.addLayout(bar)
        self.table = make_table([tr("行"), "EAN", "ARTICOLO", tr("数量"), tr("原因"), tr("有报价的供应商")],
                                stretch_col=2, numeric_cols=(0, 3))
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        lay.addWidget(self.table, 1)
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(self.table, lambda: len(self.items), self._text, self._fill,
                                   hide=self._hidden, numeric=(0, 3))
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(
            tr("显示 {0} / {1} 个 · 点表头排序，右键表头筛选", n, tot)))
        self.search.textChanged.connect(lambda _: self.ctl.render())
        from erp_widgets import ColumnFilterBox
        bar.insertSpacing(1, 14)
        bar.insertWidget(2, ColumnFilterBox(self.ctl, 4, tr("原因")))
        exp.clicked.connect(lambda: self.ctl.export(self, "缺失商品.xlsx", tr("缺失商品")))
        bb = QHBoxLayout()
        bb.addStretch()
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.accept)
        bb.addWidget(close)
        lay.addLayout(bb)
        self.ctl.render()

    def _text(self, i, c):
        m = self.items[i]
        return [str(m.get("row", "")), m.get("ean", "") or "", str(m.get("articolo") or ""),
                f"{float(m.get('qty') or 0):g}", tr(m.get("reason") or ""),
                tr("、").join(short(x) for x in (m.get("offers") or []))][c]

    def _fill(self, i, k):
        table_set_row(self.table, k, [self._text(i, c) for c in range(6)])
        if self.items[i].get("reason") == MISSING_REASONS[1]:
            self.table.item(k, 4).setForeground(QColor(T("warn")))

    def _hidden(self, i):
        text = self.search.text().strip().lower()
        m = self.items[i]
        return bool(text) and text not in f"{m.get('ean', '')} {m.get('articolo', '')}".lower()


def fill_scenario_table(t, results, chosen=None, recommended=None):
    """方案对比表：每个方案一列，每个指标一行。返回 {(表格行, 列): 方案下标}（缺失商品那一行，点开看列表）"""
    first = tr(results[0]["name"]) if results else ""
    heads = [tr("项目")] + [tr(r["name"]) + ("  ★" if i == recommended else "") + ("  ✔" if i == chosen else "")
                          for i, r in enumerate(results)]
    t.setColumnCount(len(heads))
    t.setHorizontalHeaderLabels(heads)
    sups = []
    for r in results:
        for x in r.get("suppliers") or []:
            if x["name"] not in sups:
                sups.append(x["name"])
    rules = []
    for r in results:
        for x in r.get("rules") or []:
            if x.get("name") not in rules:
                rules.append(x.get("name"))
    lines = [("total", tr("采购总额 €")), ("vs", tr("和「{0}」比 €", first)), ("priced", tr("有报价的商品")),
             ("missing", tr("缺失商品（点开看列表）")), ("changes", tr("凑单 / 换供应商的商品"))]
    lines += [("sup", s_) for s_ in sups] + [("rule", n) for n in rules]
    t.setRowCount(len(lines))
    links = {}
    for row, (kind, label) in enumerate(lines):
        vals = [short(label) if kind == "sup" else (tr("{0} 起订", label) if kind == "rule" else label)]
        for i, r in enumerate(results):
            if kind == "total":
                v = f"{r['total']:,.2f}"
            elif kind == "vs":
                d = r.get("vs_first", r["total"] - results[0]["total"])
                v = "—" if i == 0 else (f"{d:+,.2f}" if abs(d) >= 0.005 else "0")
            elif kind == "priced":
                v = f"{r['priced']:,}"
            elif kind == "missing":
                v = f"{len(r['missing']):,}"
                links[(row, i + 1)] = i
            elif kind == "changes":
                v = f"{r.get('changes', 0):,}"
            elif kind == "sup":
                x = next((y for y in r.get("suppliers") or [] if y["name"] == label), None)
                v = tr("不含") if (x and x.get("excluded")) else \
                    (tr("€{0:,.2f} · {1:,.0f} 箱", x["amount"], x["colli"]) if x and x["rows"] else "—")
            else:
                x = next((y for y in r.get("rules") or [] if y.get("name") == label), None)
                v = tr(x["status"]) if x else tr("不含")
            vals.append(v)
        table_set_row(t, row, vals, bold=kind in ("total", "missing"))
        for c in range(1, len(vals)):
            it = t.item(row, c)
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if kind == "missing" and results[c - 1]["missing"]:
                it.setForeground(QColor(T("warn")))
                it.setToolTip(tr("点开看缺了哪些商品"))
            if kind == "vs" and c > 1:
                d = results[c - 1].get("vs_first", 0)
                if abs(d) >= 0.005:
                    it.setForeground(QColor(T("ok") if d < 0 else T("err")))
            if kind == "rule" and vals[c] in (tr("未达标"),):
                it.setForeground(QColor(T("err")))
        t.item(row, 0).setForeground(QColor(T("muted")))
    for c in range(1, t.columnCount()):
        if chosen is not None and c - 1 == chosen:
            for row in range(t.rowCount()):
                t.item(row, c).setBackground(QColor(T("accent_soft")))
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(QHeaderView.ResizeToContents)
    hh.setStretchLastSection(False)
    hh.setSectionResizeMode(0, QHeaderView.Stretch)
    return links


class ScenarioDialog(QDialog):
    """几种方案的对比，选一种写入。self.chosen = 选中的方案下标"""

    def __init__(self, parent, results):
        super().__init__(parent)
        import scenarios as SC
        self.results = results
        self.rec = SC.recommend(results)
        self.chosen = self.rec
        self.setWindowTitle(tr("选择方案"))
        self.resize(1100, 720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        t = QLabel(tr("选择方案"))
        t.setObjectName("PageTitle")
        t.setStyleSheet("font-size:20px;")
        lay.addWidget(t)
        sub = QLabel(tr("同时算了 {0} 种方案（被排除的供应商这次不下单）。对比以后选一种，程序按它写入主订单和供应商文件。", len(results)))
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        if self.rec is not None:
            r = results[self.rec]
            nb = Banner()
            nb.show_msg(tr("★ 推荐「{0}」：缺的商品最少（{1} 个），其中采购总额最低（€{2:,.2f}）。最后由你决定。",
                           tr(r["name"]), len(r["missing"]), r["total"]), "info")
            lay.addWidget(nb)
        pick = QHBoxLayout()
        pick.setSpacing(16)
        pl = QLabel(tr("选用："))
        pl.setStyleSheet("font-weight:600;")
        pick.addWidget(pl)
        self.group = QButtonGroup(self)
        for i, r in enumerate(results):
            rb = QRadioButton(tr(r["name"]) + ("  ★" if i == self.rec else ""))
            rb.setChecked(i == self.chosen)
            self.group.addButton(rb, i)
            pick.addWidget(rb)
        pick.addStretch()
        lay.addLayout(pick)
        self.table = make_table([""], stretch_col=None)
        self.table.cellClicked.connect(self._clicked)
        lay.addWidget(self.table, 1)
        hint = QLabel(tr("点某一列选这个方案；点「缺失商品」那一行的数字看缺了哪些商品。"))
        hint.setObjectName("Faint")
        lay.addWidget(hint)
        btns = QHBoxLayout()
        exp = QPushButton(tr("导出对比"))
        exp.clicked.connect(self.export)
        btns.addWidget(exp)
        btns.addStretch()
        cancel = QPushButton(tr("取消处理"))
        cancel.setObjectName("Danger")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton()
        self.ok.setObjectName("Primary")
        self.ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)
        self.group.idClicked.connect(self._pick)
        self._pick(self.chosen if self.chosen is not None else 0)

    def _pick(self, i):
        self.chosen = i
        b = self.group.button(i)
        if b and not b.isChecked():
            b.setChecked(True)
        self.links = fill_scenario_table(self.table, self.results, i, self.rec)
        self.ok.setText(tr("用「{0}」写入", tr(self.results[i]["name"])))

    def _clicked(self, row, col):
        if (row, col) in self.links:
            i = self.links[(row, col)]
            MissingDialog(self, self.results[i]["missing"], tr("缺失商品 · {0}", tr(self.results[i]["name"]))).exec()
        elif col >= 1:
            self._pick(col - 1)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("导出对比"), "方案对比.xlsx", tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        ws = wb.active
        ws.title = "方案对比"
        t = self.table
        ws.append([t.horizontalHeaderItem(c).text() for c in range(t.columnCount())])
        for r in range(t.rowCount()):
            ws.append([t.item(r, c).text() for c in range(t.columnCount())])
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in self.results:
            ws2 = wb.create_sheet(("缺失-" + tr(r["name"]))[:31].replace("/", "-"))
            ws2.append([tr("行"), "EAN", "ARTICOLO", tr("数量"), tr("原因"), tr("有报价的供应商")])
            for m in r["missing"]:
                ws2.append([m["row"], m.get("ean", ""), m.get("articolo", ""), m.get("qty"), tr(m["reason"]),
                            "、".join(short(x) for x in m.get("offers") or [])])
        try:
            wb.save(path)
        except Exception as e:
            QMessageBox.critical(self, tr("导出"), tr("导出失败：{0}", e))


class ScenarioSettingsDialog(QDialog):
    """管理方案：每个方案一个名字 + 排除哪些供应商（文件名里的关键词，逗号分开）"""

    def __init__(self, parent, defs, suppliers=()):
        super().__init__(parent)
        self.setWindowTitle(tr("管理方案"))
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        t = QLabel(tr("每个方案排除哪些供应商：写供应商文件名里的关键词，几个用逗号分开（不分大小写）。第一个方案是对比的基准，一般不排除任何供应商。"))
        t.setWordWrap(True)
        lay.addWidget(t)
        if suppliers:
            sl = QLabel(tr("这次的供应商文件：{0}", tr("、").join(suppliers)))
            sl.setObjectName("Faint")
            sl.setWordWrap(True)
            lay.addWidget(sl)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("方案名"), tr("排除的供应商（关键词）")])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for d in defs:
            self._add(tr(d.get("name", "")), ", ".join(d.get("exclude") or []))
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        b_add = QPushButton(tr("添加方案"))
        b_add.clicked.connect(lambda: self._add(tr("新方案"), ""))
        b_del = QPushButton(tr("删除选中"))
        b_del.clicked.connect(self._delete)
        b_reset = QPushButton(tr("恢复默认"))
        b_reset.clicked.connect(self._reset)
        row.addWidget(b_add)
        row.addWidget(b_del)
        row.addWidget(b_reset)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add(self, name, excl):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(name))
        self.table.setItem(r, 1, QTableWidgetItem(excl))

    def _delete(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def _reset(self):
        import scenarios as SC
        self.table.setRowCount(0)
        for d in SC.DEFAULT_SCENARIOS:
            self._add(tr(d["name"]), ", ".join(d["exclude"]))

    def defs(self):
        out = []
        for r in range(self.table.rowCount()):
            name = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            excl = [x.strip() for x in re.split(r"[,，;；]", self.table.item(r, 1).text() if self.table.item(r, 1) else "")
                    if x.strip()]
            if name:
                out.append({"name": name, "exclude": excl})
        return out


# =====================================================================
# 主窗口
# =====================================================================
class _UI:
    """给 store_order_page / order_check 用的界面小部件"""
    pass


# 历史记录的列
HIST_COLS = ["日期 · 方案", "门店", "主订单", "商品行", "没有报价", "采购总额 €", "和全部供应商比 €", "凑单多花 €", "备注"]

# 比价处理的进度阶段（service.run_process 的百分比）
CMP_STAGES = [(0, "备份、打开主订单"), (10, "扣减已处理数量"), (20, "读取供应商报价"),
              (62, "选供应商、起订量凑单"), (70, "写回供应商文件"), (96, "保存主订单")]


class MainWindow(QMainWindow):
    # 货物分配系统是主系统；采购管理是其中一个模块
    NAV_GROUPS = [
        (tr("概览"), [("home", tr("工作台"))]),
        (tr("采购管理"), [("store_orders", tr("门店配货")), ("order", tr("批发商比价")), ("result", tr("比价结果")),
                        ("tracking", tr("采购订单跟踪")), ("history", tr("历史记录")), ("rules", tr("起订规则")),
                        ("search", tr("缺失查找"))]),
        (tr("分货管理"), [("distribution", tr("分货数据处理")), ("import", tr("导入 / 导出"))]),
        (tr("系统"), [("settings", tr("系统设置")), ("log", tr("运行日志"))]),
    ]
    NAV = [item for _, items in NAV_GROUPS for item in items]
    NAV_SECTION = {k: g for g, items in NAV_GROUPS for k, _ in items}
    SOON = {"tracking"}   # 还没做（采购订单跟踪）

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setWindowIcon(app_icon())
        self.resize(1280, 840)
        self.setMinimumSize(1060, 680)

        self.bridge = Bridge()
        self.bridge.log.connect(self._on_log)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.done.connect(self._on_done)
        self.bridge.missing_done.connect(self._on_missing_done)
        self.bridge.web_done.connect(self._on_web_done)
        self.bridge.decide.connect(self._on_decide)
        self.bridge.choose.connect(self._on_choose)
        self.bridge.rewrite_done.connect(self._on_rewrite_done)
        self.bridge.db_state.connect(self._on_db_state)

        self.log_entries = []      # [(level, text)]
        self.feed_entries = []     # 订单处理页的「处理过程」
        self.log_filter = "all"
        self.running = False
        self.run_start = 0.0
        self.info = {}
        self.last_summary = None

        self.notes = []            # 通知中心：[(时间, 文字)]
        self.unread = 0
        root = QWidget()
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        self.stack.setObjectName("Content")
        rv.addWidget(self.stack, 1)
        h.addWidget(right, 1)

        from dashboard_page import DashboardPage
        from legacy_pages import LegacyPage
        from settings_page import SettingsPage
        self.store_orders = StoreOrderPage(self, settings, _UI)
        self.order_check = OrderCheck(self, settings, _UI)
        self.dashboard = DashboardPage(self, settings)
        self.settings_page = SettingsPage(self, settings)
        from distribution_page import DistributionPage
        from import_page import ImportPage
        self.distribution = DistributionPage(self, settings, _UI)
        self.import_page = ImportPage(self, settings, _UI)
        builders = {
            "distribution": lambda: (self.distribution.build(), True),
            "import": lambda: (self.import_page.build(), True),
            "home": lambda: (self.dashboard.build(), True),
            "store_orders": lambda: (self.store_orders.build(), False),
            "order": lambda: (self._page_order(), True),
            "result": lambda: (self._page_result(), True),
            "history": lambda: (self._page_history(), True),
            "rules": lambda: (self._page_rules(), True),
            "search": lambda: (self._page_missing(), True),
            "settings": lambda: (self.settings_page.build(), True),
            "log": lambda: (self._page_log(), True),
        }
        self.pages = []
        self.legacy_pages = []
        for key, _ in self.NAV:
            if key in builders:
                pg, scroll = builders[key]()
            else:
                lp = LegacyPage(self, settings, key)
                self.legacy_pages.append(lp)
                pg, scroll = lp.build(), True
            self.pages.append(pg)
            self.stack.addWidget(self._scroll(pg) if scroll else pg)

        self.toast = QLabel(self)
        self.toast.setObjectName("Toast")
        self.toast.hide()
        self.toast_timer = QTimer(self, singleShot=True, timeout=self.toast.hide)

        self.tick = QTimer(self, interval=1000, timeout=self._tick)

        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.choose_folder)
        QShortcut(QKeySequence("F5"), self, activated=self.start)

        if settings.get("last_folder") and os.path.isdir(settings["last_folder"]):
            self.folder_edit.setText(settings["last_folder"])
        self.refresh_folder()
        self._purge_history()
        self._load_saved_result()
        self.refresh_history()
        self.apply_theme()
        self.go_page("home")
        self.log(tr("{0} {1} 启动 {2:%Y-%m-%d %H:%M:%S}", APP_NAME, APP_VERSION, datetime.now()))
        QTimer.singleShot(300, self.check_db)
        self.db_timer = QTimer(self, interval=5 * 60 * 1000, timeout=self.check_db)
        self.db_timer.start()
        from update_ui import Updater
        self.updater = Updater(self, settings)
        self.updater.start_auto()

    # ---------- 框架 ----------
    def _scroll(self, w):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(w)
        w.setObjectName("Content")
        return sa

    def _page(self, title, sub):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(14)
        t = QLabel(title)
        t.setObjectName("PageTitle")
        s = QLabel(sub)
        s.setObjectName("PageSub")
        s.setWordWrap(True)
        head = QVBoxLayout()
        head.setSpacing(3)
        head.addWidget(t)
        head.addWidget(s)
        lay.addLayout(head)
        lay.addSpacing(2)
        return w, lay

    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        v = QVBoxLayout(side)
        v.setContentsMargins(0, 0, 0, 10)
        v.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(18, 18, 18, 14)
        top.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(34, 34))
        col = QVBoxLayout()
        col.setSpacing(1)
        brand = QLabel(APP_NAME)
        brand.setObjectName("Brand")
        brand.setWordWrap(True)     # 英文 / 意大利文名字较长时换行
        sub = QLabel(f"{tr('采购 · 分货管理')} · {APP_VERSION}")
        sub.setObjectName("BrandSub")
        sub.setWordWrap(True)
        col.addWidget(brand)
        col.addWidget(sub)
        top.addWidget(logo)
        top.addLayout(col, 1)
        v.addLayout(top)

        nav = QWidget()
        nav.setObjectName("Sidebar")
        nv = QVBoxLayout(nav)
        nv.setContentsMargins(10, 0, 10, 0)
        nv.setSpacing(2)
        self.nav_group = QButtonGroup(self)
        self.nav_btns = []
        self.nav_badges = {}
        idx = 0
        for gname, items in self.NAV_GROUPS:
            gl = QLabel(gname)
            gl.setObjectName("NavGroup")
            nv.addWidget(gl)
            for key, text in items:
                b = QPushButton("  " + text.replace("&", "&&"))
                b.setObjectName("NavBtn")
                b.setCheckable(True)
                b.setCursor(Qt.PointingHandCursor)
                b.setIconSize(QSize(17, 17))
                b.clicked.connect(lambda _=False, i=idx: self.go(i))
                self.nav_group.addButton(b, idx)
                self.nav_btns.append((b, key))
                b.toggled.connect(lambda on, b=b, k=key: b.setIcon(icon(k, "#FFFFFF" if on else T("side_text"))))
                bl = QHBoxLayout(b)
                bl.setContentsMargins(0, 0, 8, 0)
                bl.addStretch()
                if key in self.SOON:
                    tag = QLabel(tr("二期"))
                    tag.setObjectName("NavSoon")
                    tag.setFixedHeight(17)
                    tag.setAlignment(Qt.AlignCenter)
                    bl.addWidget(tag, 0, Qt.AlignVCenter)
                else:
                    badge = QLabel("")
                    badge.setObjectName("NavBadge")
                    badge.setFixedHeight(17)
                    badge.setMinimumWidth(20)
                    badge.setAlignment(Qt.AlignCenter)
                    badge.hide()
                    bl.addWidget(badge, 0, Qt.AlignVCenter)
                    self.nav_badges[key] = badge
                nv.addWidget(b)
                idx += 1
            nv.addSpacing(6)
        nv.addStretch()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(nav)
        sa.setStyleSheet("QScrollArea{background:transparent;}")
        v.addWidget(sa, 1)

        db = QFrame()
        db.setObjectName("DbBox")
        dv = QVBoxLayout(db)
        dv.setContentsMargins(12, 10, 12, 10)
        dv.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.db_dot = QLabel()
        self.db_dot.setFixedSize(8, 8)
        self.db_title = QLabel(tr("正在检查数据库…"))
        self.db_title.setObjectName("DbTitle")
        row.addWidget(self.db_dot)
        row.addWidget(self.db_title, 1)
        dv.addLayout(row)
        self.db_sub = QLabel("")
        self.db_sub.setObjectName("DbSub")
        dv.addWidget(self.db_sub)
        db.setCursor(Qt.PointingHandCursor)
        db.mousePressEvent = lambda _e: self.go_page("settings")
        wrap = QHBoxLayout()
        wrap.setContentsMargins(10, 8, 10, 0)
        wrap.addWidget(db)
        v.addLayout(wrap)
        self._set_db_state(None)
        return side

    def _build_topbar(self):
        from PySide6.QtWidgets import QCompleter, QMenu
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(56)
        h = QHBoxLayout(bar)
        h.setContentsMargins(24, 0, 20, 0)
        h.setSpacing(10)
        self.crumb_sec = QLabel("")
        self.crumb_sec.setObjectName("Crumb")
        sep = QLabel("/")
        sep.setObjectName("Crumb")
        self.crumb_cur = QLabel("")
        self.crumb_cur.setObjectName("CrumbCur")
        h.addWidget(self.crumb_sec)
        h.addWidget(sep)
        h.addWidget(self.crumb_cur)
        h.addStretch()
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText(tr("搜索功能页面…  Ctrl K"))
        self.search.setFixedWidth(320)
        self.search_icon = QLabel(self.search)
        self.search_icon.move(10, 9)
        comp = QCompleter([t for _, t in self.NAV], self.search)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.activated.connect(self._search_go)
        self.search.setCompleter(comp)
        self.search.returnPressed.connect(lambda: self._search_go(self.search.text()))
        h.addWidget(self.search)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: (self.search.setFocus(), self.search.selectAll()))

        self.bell = QPushButton()
        self.bell.setObjectName("TopBtn")
        self.bell.setToolTip(tr("通知"))
        self.bell.setCursor(Qt.PointingHandCursor)
        self.bell_menu = QMenu(self)
        self.bell.clicked.connect(self._show_notes)
        self.bell_dot = QLabel(self.bell)
        self.bell_dot.setFixedSize(8, 8)
        self.bell_dot.move(21, 6)
        self.bell_dot.hide()
        h.addWidget(self.bell)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("TopBtn")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.clicked.connect(self.toggle_theme)
        h.addWidget(self.theme_btn)
        self.lang_box = QComboBox()
        self.lang_box.setObjectName("LangBox")
        for code, label in LANGS:
            self.lang_box.addItem(label, code)
        self.lang_box.setCurrentIndex(max(0, self.lang_box.findData(get_language())))
        self.lang_box.currentIndexChanged.connect(self.change_language)
        h.addWidget(self.lang_box)
        user = QHBoxLayout()
        user.setSpacing(8)
        name = settings.get("user_name") or tr("采购部")
        av = QLabel(name[:1])
        av.setObjectName("Avatar")
        av.setFixedSize(30, 30)
        av.setAlignment(Qt.AlignCenter)
        uc = QVBoxLayout()
        uc.setSpacing(0)
        un = QLabel(name)
        un.setObjectName("UserName")
        us = QLabel(settings.get("user_site") or tr("BMS 总仓"))
        us.setObjectName("UserSub")
        uc.addWidget(un)
        uc.addWidget(us)
        user.addWidget(av)
        user.addLayout(uc)
        uw = QWidget()
        uw.setLayout(user)
        h.addSpacing(4)
        h.addWidget(uw)
        return bar

    def _search_go(self, text):
        text = (text or "").strip().lower()
        if not text:
            return
        for i, (k, t) in enumerate(self.NAV):
            if text in t.lower() or text == k:
                self.go(i)
                self.search.clear()
                self.search.clearFocus()
                return
        self.show_toast(tr("没有找到「{0}」", text))

    def _show_notes(self):
        m = self.bell_menu
        m.clear()
        if not self.notes:
            a = m.addAction(tr("暂时没有通知"))
            a.setEnabled(False)
        for when, text in reversed(self.notes[-20:]):
            a = m.addAction(f"{when:%H:%M}   {text}")
            a.setEnabled(True)
        if self.notes:
            m.addSeparator()
            m.addAction(tr("清空通知"), lambda: (self.notes.clear(), self._update_bell()))
        self.unread = 0
        self._update_bell()
        m.exec(self.bell.mapToGlobal(self.bell.rect().bottomLeft()))

    def _update_bell(self):
        self.bell_dot.setVisible(self.unread > 0)

    def notify(self, text):
        """进通知中心（不弹提示）"""
        self.notes.append((datetime.now(), text))
        self.unread += 1
        self._update_bell()

    def set_badge(self, key, value):
        b = self.nav_badges.get(key)
        if b is None:
            return
        b.setText(str(value) if value else "")
        b.setVisible(bool(value))

    # ---------- 数据库状态 ----------
    def _set_db_state(self, ok, sub=None):
        self._db_ok = ok
        color = T("ok") if ok else ("#E7A23A" if ok is None else T("err"))
        self.db_dot.setStyleSheet(f"background:{color}; border-radius:4px;")
        self.db_title.setText(tr("数据库已连接") if ok else (tr("正在检查数据库…") if ok is None else tr("数据库未连接")))
        if sub is not None:
            self.db_sub.setText(sub)

    def _on_db_state(self, ok):
        self._set_db_state(ok)
        if hasattr(self, "dashboard"):
            self.dashboard.set_db(ok)
        if hasattr(self, "settings_page"):
            self.settings_page.refresh()

    def check_db(self):
        import reorder_service as R
        from fenpei.config import DatabaseConfig as D
        dbs = settings.get("db") or {}
        host = dbs.get("host") or D.HOST
        port = dbs.get("port") or D.PORT
        name = dbs.get("database") or D.DATABASE
        self._set_db_state(None, f"{name} @ {host}:{port}")

        def work():
            ok = False
            try:
                db = R.make_db(dbs or None)
                R.test_connection(db)
                try:
                    self._db_account = R.account_info(db)
                except Exception:
                    self._db_account = None
                db.engine.dispose()
                ok = True
            except Exception:
                ok = False
            self.bridge.db_state.emit(ok)

        threading.Thread(target=work, daemon=True).start()

    def go_page(self, key):
        self.go([k for k, _ in self.NAV].index(key))

    def go(self, i):
        self.stack.setCurrentIndex(i)
        self.nav_group.button(i).setChecked(True)
        key, text = self.NAV[i]
        self.crumb_sec.setText(self.NAV_SECTION.get(key, ""))
        self.crumb_cur.setText(text)
        if key == "home" and hasattr(self, "dashboard"):
            self.dashboard.refresh()

    def apply_theme(self):
        theme.set_theme(settings.get("theme", "light"))
        QApplication.instance().setStyleSheet(build_qss())
        for b, ic in self.nav_btns:
            b.setIcon(icon(ic, "#FFFFFF" if b.isChecked() else T("side_text")))
        dark = settings.get("theme") == "dark"
        self.theme_btn.setIcon(icon("sun" if dark else "moon", T("muted"), 16))
        self.theme_btn.setToolTip(tr("浅色模式") if dark else tr("深色模式"))
        self.bell.setIcon(icon("bell", T("muted"), 16))
        self.search_icon.setPixmap(icon("search", T("muted"), 14).pixmap(14, 14))
        self.bell_dot.setStyleSheet(f"background:{T('err')}; border-radius:4px;")
        self._set_db_state(getattr(self, "_db_ok", None))
        for pg in (getattr(self, "dashboard", None), getattr(self, "store_orders", None),
                   getattr(self, "settings_page", None), getattr(self, "order_check", None),
                   getattr(self, "distribution", None), getattr(self, "import_page", None)):
            if pg is not None and hasattr(pg, "restyle"):
                pg.restyle()
        for b in (self.banner_folder, self.banner_result, self.banner_rules):
            if b.isVisible():
                b.restyle()
        self.btn_folder.setIcon(icon("folder", T("text")))
        self.gauge.update()
        self._render_log()
        self._dark_titlebar()
        if self.last_summary:
            self.show_summary(self.last_summary)
        self.refresh_rules_table()

    def _dark_titlebar(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            val = ctypes.c_int(1 if settings.get("theme") == "dark" else 0)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(int(self.winId()), attr, ctypes.byref(val), 4) == 0:
                    break
        except Exception:
            pass

    def change_language(self, _=None):
        code = self.lang_box.currentData()
        if code == settings.get("language", "zh"):
            return
        if self.running:
            QMessageBox.information(self, APP_NAME, tr("正在处理，处理完成后再切换语言。"))
            self.lang_box.blockSignals(True)
            self.lang_box.setCurrentIndex(self.lang_box.findData(settings.get("language", "zh")))
            self.lang_box.blockSignals(False)
            return
        settings["language"] = code
        save_settings(settings)
        set_language(code)   # 用新语言提问
        if QMessageBox.question(self, APP_NAME, tr("语言已保存，重启程序后生效。现在重启？")) == QMessageBox.Yes:
            from PySide6.QtCore import QProcess
            if getattr(sys, "frozen", False):
                QProcess.startDetached(sys.executable, sys.argv[1:])
            else:
                QProcess.startDetached(sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:])
            QApplication.instance().quit()

    def toggle_theme(self):
        settings["theme"] = "light" if settings.get("theme") == "dark" else "dark"
        save_settings(settings)
        self.apply_theme()

    def show_toast(self, text, ms=2600):
        self.notify(text)
        self.toast.setText(text)
        self.toast.adjustSize()
        self.toast.move(self.width() - self.toast.width() - 24, self.height() - self.toast.height() - 76)
        self.toast.raise_()
        self.toast.show()
        self.toast_timer.start(ms)

    # ---------- 页面：订单处理 ----------
    def _page_order(self):
        w, lay = self._page(tr("批发商比价"), tr("几家批发商卖的商品差不多：按最低价给每个商品选供应商，帮有起订量的供应商凑单，把订购数量写回各家报价文件。"))
        from erp_widgets import StepBar
        self.cmp_steps = StepBar([(tr("总文件夹"), ""), (tr("检查门店清单"), tr("可选")), (tr("比价分配"), ""), (tr("查看结果"), "")])
        lay.addWidget(self.cmp_steps)

        p1 = Panel(tr("① 总文件夹"))
        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText(tr("包含 ORDINE、eliminare、distribuire 三个子文件夹的文件夹"))
        self.folder_edit.editingFinished.connect(self.refresh_folder)
        b1 = QPushButton(tr("选择文件夹…"))
        self.btn_folder = b1
        b1.clicked.connect(self.choose_folder)
        b2 = QPushButton(tr("在资源管理器中打开"))
        b2.clicked.connect(lambda: open_path(self.folder_edit.text().strip()))
        row.addWidget(self.folder_edit, 1)
        row.addWidget(b1)
        row.addWidget(b2)
        p1.lay.addLayout(row)

        # 三个文件区：直接把文件拖进来（或点「选择文件…」），程序放进总文件夹对应的子文件夹；
        # 原来的文件挪进子文件夹里的 _旧订单 / _旧文件，不删除。也照样可以自己去文件夹里放。
        from erp_widgets import DropZone
        zones = QHBoxLayout()
        zones.setSpacing(12)
        self.zone_ordine = DropZone(tr("主订单 · ORDINE"), "", multi=False)
        self.zone_elim = DropZone(tr("扣减文件 · eliminare"), "", multi=True, removable=True)
        self.zone_sup = DropZone(tr("供应商报价 · distribuire"), "", multi=True, removable=True)
        for z, sub in ((self.zone_ordine, ORDINE_FOLDER), (self.zone_elim, ELIMINARE_FOLDER),
                       (self.zone_sup, DISTRIBUTUIRE_FOLDER)):
            z.setMinimumHeight(120)
            z.open_folder.connect(lambda sub=sub: self._open_sub(sub))
            zones.addWidget(z, 1)
        self.zone_ordine.files_dropped.connect(self._drop_ordine)
        self.zone_elim.files_dropped.connect(self._drop_eliminare)
        self.zone_sup.files_dropped.connect(self._drop_suppliers)
        self.zone_elim.remove_requested.connect(lambda n: self._remove_file(ELIMINARE_FOLDER, n))
        self.zone_sup.remove_requested.connect(lambda n: self._remove_file(DISTRIBUTUIRE_FOLDER, n))
        p1.lay.addLayout(zones)
        self.banner_folder = Banner()
        p1.lay.addWidget(self.banner_folder)
        lay.addWidget(p1)

        lay.addWidget(self.order_check.build())

        p2 = Panel(tr("③ 比价分配"))
        opts = QHBoxLayout()
        opts.setSpacing(30)
        self.t_backup = Toggle(tr("处理前自动备份原文件"), settings.get("auto_backup", True))
        self.t_min = Toggle(tr("起订量凑单"), settings.get("min_order_enabled", True))
        self.t_min.toggled.connect(lambda on: (settings.__setitem__("min_order_enabled", on),
                                               save_settings(settings), self.refresh_rule_line(),
                                               self.t_min2.setChecked(on)))
        self.t_backup.toggled.connect(lambda on: (settings.__setitem__("auto_backup", on), save_settings(settings)))
        opts.addWidget(self.t_backup)
        opts.addWidget(self.t_min)
        self.t_review = Toggle(tr("写入前人工确认分配"), settings.get("review_before_write", False))
        self.t_review.setToolTip(tr("打开后，每次写入文件前都会先打开「手动调整供应商」窗口让你检查"))
        self.t_review.toggled.connect(lambda on: (settings.__setitem__("review_before_write", on), save_settings(settings)))
        opts.addWidget(self.t_review)
        self.t_scen = Toggle(tr("多方案对比"), settings.get("scenarios_enabled", True))
        self.t_scen.setToolTip(tr("同时算几种方案（全部供应商 / 不含某家 …），对比后由你选一种写入"))
        self.t_scen.toggled.connect(lambda on: (settings.__setitem__("scenarios_enabled", on), save_settings(settings),
                                                self.refresh_rule_line()))
        opts.addWidget(self.t_scen)
        opts.addStretch()
        b_scen = QPushButton(tr("管理方案"))
        b_scen.clicked.connect(self.edit_scenarios)
        opts.addWidget(b_scen)
        edit_rules = QPushButton(tr("管理起订规则"))
        edit_rules.clicked.connect(lambda: self.go_page("rules"))
        opts.addWidget(edit_rules)
        p2.lay.addLayout(opts)
        self.rule_line = QLabel()
        self.rule_line.setObjectName("Muted")
        self.rule_line.setWordWrap(True)
        p2.lay.addWidget(self.rule_line)
        lay.addWidget(p2)

        run = QHBoxLayout()
        run.setSpacing(20)
        self.btn_run = QPushButton(tr("开始处理"))
        self.btn_run.setObjectName("Run")
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.setToolTip(tr("快捷键 F5"))
        self.btn_run.clicked.connect(self.start)
        run.addWidget(self.btn_run)
        pcol = QVBoxLayout()
        pcol.setSpacing(6)
        top = QHBoxLayout()
        self.stage = QLabel(tr("等待开始"))
        self.elapsed = QLabel("")
        self.elapsed.setObjectName("Faint")
        top.addWidget(self.stage)
        top.addStretch()
        top.addWidget(self.elapsed)
        pcol.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        pcol.addWidget(self.bar)
        run.addLayout(pcol, 1)
        lay.addSpacing(6)
        lay.addLayout(run)
        lay.addSpacing(6)

        p3 = Panel(tr("处理过程"))
        self.feed = QTextEdit()
        self.feed.setReadOnly(True)
        self.feed.setMinimumHeight(220)
        p3.lay.addWidget(self.feed)
        lay.addWidget(p3, 1)
        return w

    def _fact(self, label, value):
        col = QVBoxLayout()
        col.setSpacing(2)
        l = QLabel(label)
        l.setObjectName("Faint")
        v = QLabel(value)
        v.setStyleSheet("font-size:14px; font-weight:600;")
        col.addWidget(l)
        col.addWidget(v)
        return col, v

    # ---------- 页面：分配结果 ----------
    def _page_result(self):
        w, lay = self._page(tr("分配结果"), tr("最近一次处理的结果；以前的结果在「历史记录」里打开。起订量调整的每一行明细写在主订单的 AC 列。"))
        self.banner_result = Banner()
        lay.addWidget(self.banner_result)

        kpis = QHBoxLayout()
        kpis.setSpacing(12)
        self.k_rows = Kpi(tr("商品行"))
        self.k_priced = Kpi(tr("有报价"))
        self.k_missing = Kpi(tr("没有报价"))
        self.k_changes = Kpi(tr("改了供应商的商品"))
        self.k_cost = Kpi(tr("比只按最低价多花"))
        self.k_time = Kpi(tr("用时"))
        for k in (self.k_rows, self.k_priced, self.k_missing, self.k_changes, self.k_cost, self.k_time):
            kpis.addWidget(k)
        lay.addLayout(kpis)
        # 「没有报价」那张卡可以点开看缺失商品列表
        self.k_missing.setCursor(Qt.PointingHandCursor)
        self.k_missing.setToolTip(tr("点开看缺了哪些商品（可以排序、筛选、导出）"))
        self.k_missing.mousePressEvent = lambda _e: self.show_missing()

        self.scen_panel = Panel(tr("方案对比"))
        sh = QLabel(tr("这次同时算的几种方案；✔ 是选用的方案。点「缺失商品」那一行的数字看缺了哪些商品。"))
        sh.setObjectName("Faint")
        self.scen_panel.lay.addWidget(sh)
        self.scen_table = make_table([""], stretch_col=None)
        self.scen_table.setMinimumHeight(200)
        self.scen_table.cellClicked.connect(self._scen_clicked)
        self.scen_panel.lay.addWidget(self.scen_table)
        # 换方案：不重算，直接改用当时算好的另一个方案
        self.scen_switch = QWidget()
        self.scen_switch_lay = QHBoxLayout(self.scen_switch)
        self.scen_switch_lay.setContentsMargins(0, 4, 0, 0)
        self.scen_switch_lay.setSpacing(8)
        self.scen_panel.lay.addWidget(self.scen_switch)
        lay.addWidget(self.scen_panel)
        self.scen_panel.hide()

        pg = Panel(tr("起订量"))
        legend = QLabel(tr("浅色条：只按最低价时的量　实色条：最终下单量　竖线：起订线"))
        legend.setObjectName("Faint")
        pg.lay.addWidget(legend)
        self.gauge = MinOrderGauge()
        pg.lay.addWidget(self.gauge)
        lay.addWidget(pg)

        pt = Panel(tr("各供应商采购量"))
        h = QLabel(tr("「最低价」是只按最低价分配时的量，「最终」是凑单 / 你的决定 / 手动调整之后实际写入的量。"))
        h.setObjectName("Faint")
        pt.lay.addWidget(h)
        self.sum_table = make_table([tr("供应商文件"), tr("商品数"), tr("最低价箱数"), tr("最终箱数"), tr("箱数变化"),
                                     tr("最低价金额 €"), tr("最终金额 €"), tr("金额变化 €"), tr("起订条件"), tr("状态")],
                                    stretch_col=None, numeric_cols=(1, 2, 3, 4, 5, 6, 7))
        self.sum_table.horizontalHeader().setStretchLastSection(True)
        self.sum_table.setItemDelegateForColumn(9, StatusDelegate(self.sum_table))
        self.sum_table.setMinimumHeight(240)
        pt.lay.addWidget(self.sum_table)
        acts = QHBoxLayout()
        b1 = QPushButton(tr("打开主订单"))
        b1.setObjectName("Primary")
        b1.clicked.connect(self.open_main_order)
        b2 = QPushButton(tr("打开供应商文件夹"))
        b2.clicked.connect(self.open_supplier_folder)
        self.btn_missing = QPushButton(tr("缺失商品"))
        self.btn_missing.clicked.connect(self.show_missing)
        self.btn_backup = QPushButton(tr("打开备份"))
        self.btn_backup.clicked.connect(lambda: self.last_summary and open_path(self.last_summary.get("backup_dir")))
        self.btn_backup.setEnabled(False)
        self.btn_edit = QPushButton(tr("修改分配…"))
        self.btn_edit.setToolTip(tr("已经写入以后，还可以改商品的供应商并重新写回文件"))
        self.btn_edit.clicked.connect(self.edit_allocation)
        self.btn_edit.setEnabled(False)
        acts.addWidget(b1)
        acts.addWidget(self.btn_edit)
        acts.addWidget(self.btn_missing)
        acts.addWidget(b2)
        acts.addWidget(self.btn_backup)
        acts.addStretch()
        pt.lay.addLayout(acts)
        lay.addWidget(pt)

        pc = Panel()
        top = QHBoxLayout()
        self.chg_title = QLabel(tr("改了供应商的商品"))
        self.chg_title.setObjectName("PanelTitle")
        top.addWidget(self.chg_title)
        top.addStretch()
        self.chg_filter = QLineEdit()
        self.chg_filter.setPlaceholderText(tr("筛选 EAN、品名或供应商"))
        self.chg_filter.setFixedWidth(240)
        self.chg_filter.textChanged.connect(self._filter_changes)
        top.addWidget(self.chg_filter)
        b = QPushButton(tr("导出 Excel"))
        b.clicked.connect(self.export_changes)
        top.addWidget(b)
        pc.lay.addLayout(top)
        self.chg_table = make_table([tr("行"), "EAN", "ARTICOLO", tr("箱数"), tr("原供应商"), tr("原件价"), tr("新供应商"), tr("新件价"),
                                     tr("多花 €"), tr("原因")], stretch_col=2, numeric_cols=(0, 3, 5, 7, 8))
        self.chg_table.setItemDelegateForColumn(9, StatusDelegate(self.chg_table))
        self.chg_table.setMinimumHeight(320)
        pc.lay.addWidget(self.chg_table)
        self.chg_empty = QLabel(tr("这次没有改动，所有商品都按最低价选的供应商。"))
        self.chg_empty.setObjectName("Muted")
        pc.lay.addWidget(self.chg_empty)
        lay.addWidget(pc)
        lay.addStretch()
        return w

    def _filter_changes(self, text):
        text = text.strip().lower()
        t = self.chg_table
        for r in range(t.rowCount()):
            hay = " ".join(t.item(r, c).text() for c in (1, 2, 4, 6) if t.item(r, c)).lower()
            t.setRowHidden(r, bool(text) and text not in hay)

    def export_changes(self):
        t = self.chg_table
        if t.rowCount() == 0:
            QMessageBox.information(self, tr("导出"), tr("这次没有改动的商品。"))
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("导出改动明细"),
                                              tr("供应商改动明细_{0:%Y%m%d_%H%M%S}.xlsx", datetime.now()), tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = tr("改动明细")
            ws.append([t.horizontalHeaderItem(c).text() for c in range(t.columnCount())])
            for r in range(t.rowCount()):
                ws.append([tr(t.item(r, c).text()) if t.item(r, c) else "" for c in range(t.columnCount())])
            ws2 = wb.create_sheet(tr("各供应商"))
            st = self.sum_table
            ws2.append([st.horizontalHeaderItem(c).text() for c in range(st.columnCount())])
            for r in range(st.rowCount()):
                ws2.append([tr(st.item(r, c).text()) if st.item(r, c) else "" for c in range(st.columnCount())])
            for sh in (ws, ws2):
                for col in sh.columns:
                    width = max((len(str(c.value)) for c in col if c.value), default=8)
                    sh.column_dimensions[col[0].column_letter].width = min(width + 2, 60)
            wb.save(path)
            self.log(tr("✔ 已导出改动明细：{0}", path))
            if QMessageBox.question(self, tr("导出"), tr("已导出到：\n{0}\n\n现在打开？", path)) == QMessageBox.Yes:
                open_path(path)
        except Exception as e:
            QMessageBox.critical(self, tr("导出"), tr("导出失败：{0}", e))

    # ---------- 页面：历史记录 ----------
    def _page_history(self):
        w, lay = self._page(tr("历史记录"), tr("每次处理和修改分配后自动保存一份结果，关掉程序以后也能随时打开查看。双击一行打开。"))
        top = QHBoxLayout()
        self.hist_filter = QLineEdit()
        self.hist_filter.setPlaceholderText(tr("筛选文件夹或主订单名"))
        self.hist_filter.setFixedWidth(260)
        self.hist_filter.textChanged.connect(self._filter_history)
        top.addWidget(self.hist_filter)
        top.addStretch()
        kl = QLabel(tr("保留"))
        kl.setObjectName("Muted")
        top.addWidget(kl)
        self.keep_box = QComboBox()
        self.keep_box.setMinimumWidth(120)
        for days, label in history.KEEP_OPTIONS:
            self.keep_box.addItem(tr(label), days)
        idx = self.keep_box.findData(int(settings.get("history_keep_days", history.DEFAULT_KEEP_DAYS)))
        self.keep_box.setCurrentIndex(max(0, idx))
        self.keep_box.currentIndexChanged.connect(self._set_keep)
        top.addWidget(self.keep_box)
        lay.addLayout(top)

        p = Panel()
        # 按「日期 · 门店」一条一条列出；有多方案对比的，下面一行一个方案，✔ 是选用的方案
        from PySide6.QtWidgets import QTreeWidget
        self.hist_tree = QTreeWidget()
        self.hist_tree.setColumnCount(len(HIST_COLS))
        self.hist_tree.setHeaderLabels([tr(c) for c in HIST_COLS])
        self.hist_tree.setRootIsDecorated(True)
        self.hist_tree.setUniformRowHeights(True)
        self.hist_tree.setAllColumnsShowFocus(True)
        self.hist_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.hist_tree.setMinimumHeight(460)
        hh = self.hist_tree.header()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(len(HIST_COLS) - 1, QHeaderView.Stretch)
        hh.setStretchLastSection(True)
        self.hist_tree.itemDoubleClicked.connect(lambda *_: self.view_history())
        p.lay.addWidget(self.hist_tree)
        self.hist_empty = QLabel(tr("还没有历史记录。完成一次订单处理后会自动出现在这里。"))
        self.hist_empty.setObjectName("Muted")
        p.lay.addWidget(self.hist_empty)
        acts = QHBoxLayout()
        b = QPushButton(tr("打开查看"))
        b.setObjectName("Primary")
        b.clicked.connect(self.view_history)
        acts.addWidget(b)
        b = QPushButton(tr("打开这次的文件夹"))
        b.clicked.connect(self.open_history_folder)
        acts.addWidget(b)
        b = QPushButton(tr("删除选中"))
        b.setObjectName("Danger")
        b.setToolTip(tr("按住 Ctrl 或 Shift 可以一次选多条"))
        b.clicked.connect(self.delete_history)
        acts.addWidget(b)
        b = QPushButton(tr("清空全部"))
        b.setObjectName("Danger")
        b.clicked.connect(self.clear_history)
        acts.addWidget(b)
        acts.addStretch()
        self.hist_info = QLabel()
        self.hist_info.setObjectName("Faint")
        acts.addWidget(self.hist_info)
        p.lay.addLayout(acts)
        lay.addWidget(p)
        lay.addStretch()
        return w

    def refresh_history(self):
        if not hasattr(self, "hist_tree"):
            return
        try:
            runs = history.list_runs()
        except Exception as e:
            file_logger.warning(f"read history failed: {e}")
            runs = []
        self.hist_runs = runs
        from PySide6.QtWidgets import QTreeWidgetItem
        tree = self.hist_tree
        tree.clear()
        num = (3, 4, 5, 6, 7)
        for idx, e in enumerate(runs):
            notes = []
            if e.get("scenario"):
                notes.append(tr("选用：{0}", tr(e["scenario"])))
            if e.get("edits"):
                notes.append(tr("改过 {0} 次", e["edits"]))
            if e.get("warning"):
                notes.append(tr("有起订供应商没达标"))
            try:
                when = datetime.strptime(e["time"][:16], "%Y-%m-%d %H:%M")
                head = f"{when:%m/%d}  {when:%H:%M}"
            except ValueError:
                head = e["time"][:16]
            store = e.get("store") or ""
            top = QTreeWidgetItem([head, store or "—", e.get("main_file") or "",
                                   f"{e.get('total_rows', 0):,}", f"{e.get('missing', 0):,}",
                                   f"{e.get('final_cost', 0):,.2f}", "",
                                   f"{e.get('extra_cost', 0):,.2f}" if abs(e.get("extra_cost", 0)) >= 0.005 else "",
                                   tr("；").join(notes)])
            top.setData(0, Qt.UserRole, idx)
            f = top.font(0)
            f.setBold(True)
            for c in (0, 1):
                top.setFont(c, f)
            for c in num:
                top.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
            top.setToolTip(2, (e.get("folder") or "") + "\n" + (e.get("main_file") or ""))
            if e.get("missing"):
                top.setForeground(4, QColor(T("warn")))
            if e.get("warning"):
                top.setForeground(8, QColor(T("err")))
            if e.get("edits"):
                top.setToolTip(8, tr("最后修改：{0}", (e.get("edited_at") or "")[:16]))
            for sc in e.get("scenarios") or []:
                d = sc.get("vs_first") or 0
                ch = QTreeWidgetItem(["   " + tr("方案：{0}", tr(sc.get("name") or "")), "", "",
                                      f"{sc.get('priced', 0):,}", f"{sc.get('missing', 0):,}", f"{sc.get('total', 0):,.2f}",
                                      (f"{d:+,.2f}" if abs(d) >= 0.005 else ""), "",
                                      tr("✔ 选用") if sc.get("chosen") else
                                      (tr("排除：{0}", tr("、").join(short(x) for x in sc.get("excluded") or []))
                                       if sc.get("excluded") else "")])
                ch.setData(0, Qt.UserRole, idx)
                for c in num:
                    ch.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
                ch.setToolTip(3, tr("有报价的商品"))
                if sc.get("chosen"):
                    fb = ch.font(0)
                    fb.setBold(True)
                    for c in range(len(HIST_COLS)):
                        ch.setFont(c, fb)
                        ch.setForeground(c, QColor(T("accent")))
                else:
                    for c in range(len(HIST_COLS)):
                        ch.setForeground(c, QColor(T("muted")))
                    if abs(d) >= 0.005:
                        ch.setForeground(6, QColor(T("ok") if d < 0 else T("err")))
                top.addChild(ch)
            tree.addTopLevelItem(top)
            top.setExpanded(bool(e.get("scenarios")) and idx < 10)      # 最近 10 条默认展开
        tree.setVisible(bool(runs))
        self.hist_empty.setVisible(not runs)
        keep = int(settings.get("history_keep_days", history.DEFAULT_KEEP_DAYS))
        mb = history.disk_usage() / 1024 / 1024
        self.hist_info.setText(tr("共 {0} 条，占用 {1:.1f} MB，{2}", len(runs), mb,
                                  tr("超过 {0} 天的自动删除", keep) if keep else tr("永久保留")))
        self._filter_history(self.hist_filter.text())
        self._mark_shown_history()

    def _mark_shown_history(self):
        """历史里把结果页正在显示的那条标出来（底色）"""
        hid = (self.last_summary or {}).get("history_id")
        tree = self.hist_tree
        runs = getattr(self, "hist_runs", [])
        for k in range(tree.topLevelItemCount()):
            it = tree.topLevelItem(k)
            idx = it.data(0, Qt.UserRole)
            on = idx is not None and idx < len(runs) and runs[idx]["id"] == hid
            for c in range(tree.columnCount()):
                it.setBackground(c, QColor(T("surface2")) if on else QColor(0, 0, 0, 0))

    def _filter_history(self, text):
        text = (text or "").strip().lower()
        tree = self.hist_tree
        for k in range(tree.topLevelItemCount()):
            it = tree.topLevelItem(k)
            hay = " ".join(it.text(c) + " " + (it.toolTip(c) or "") for c in range(tree.columnCount())).lower()
            hay += " " + " ".join(it.child(j).text(0) for j in range(it.childCount())).lower()
            it.setHidden(bool(text) and text not in hay)

    def _picked_history(self):
        runs = getattr(self, "hist_runs", [])
        out, seen = [], set()
        for it in self.hist_tree.selectedItems():
            idx = it.data(0, Qt.UserRole)
            if idx is not None and idx < len(runs) and idx not in seen and not (it.parent() or it).isHidden():
                seen.add(idx)
                out.append(runs[idx])
        return out

    def _sel_history(self):
        picked = self._picked_history()
        if not picked:
            cur = self.hist_tree.currentItem()
            idx = cur.data(0, Qt.UserRole) if cur else None
            runs = getattr(self, "hist_runs", [])
            if idx is None or idx >= len(runs):
                QMessageBox.information(self, tr("历史记录"), tr("先在表里选一条记录。"))
                return None
            return runs[idx]
        return picked[0]

    def view_history(self):
        e = self._sel_history()
        if not e:
            return
        data = history.load_run(e["id"])
        if not data:
            QMessageBox.warning(self, tr("历史记录"), tr("这条记录读不出来，文件可能已经被删除或损坏。"))
            self.refresh_history()
            return
        self.last_summary = data
        self.show_summary(data)
        self.go_page("result")

    def open_history_folder(self):
        e = self._sel_history()
        if e and e.get("folder"):
            open_path(e["folder"])

    def delete_history(self):
        picked = self._picked_history()
        if not picked:
            QMessageBox.information(self, tr("历史记录"), tr("先在表里选要删除的记录（按住 Ctrl 或 Shift 可以多选）。"))
            return
        if len(picked) == 1:
            msg = tr("删除 {0} 的这条历史记录？只删记录，不动订单和供应商文件。", picked[0]["time"][:16])
        else:
            msg = tr("删除选中的 {0} 条历史记录？只删记录，不动订单和供应商文件。", len(picked))
        if QMessageBox.question(self, tr("删除"), msg) != QMessageBox.Yes:
            return
        n = history.delete_runs([e["id"] for e in picked])
        self.refresh_history()
        self.show_toast(tr("已删除 {0} 条", n))

    def clear_history(self):
        runs = getattr(self, "hist_runs", [])
        if not runs:
            self.show_toast(tr("没有历史记录"))
            return
        if QMessageBox.question(self, tr("清空全部"),
                                tr("删除全部 {0} 条历史记录？只删记录，不动订单和供应商文件。删除后不能恢复。", len(runs))) \
                != QMessageBox.Yes:
            return
        n = history.clear_all()
        self.refresh_history()
        self.show_toast(tr("已删除 {0} 条", n))

    def _set_keep(self, _=None):
        days = int(self.keep_box.currentData())
        n_old = history.count_older(days)
        if n_old and QMessageBox.question(
                self, tr("历史记录"),
                tr("改成「{0}」后，会删除 {1} 条更早的记录。继续？", self.keep_box.currentText(), n_old)) != QMessageBox.Yes:
            prev = int(settings.get("history_keep_days", history.DEFAULT_KEEP_DAYS))
            self.keep_box.blockSignals(True)
            self.keep_box.setCurrentIndex(max(0, self.keep_box.findData(prev)))
            self.keep_box.blockSignals(False)
            return
        settings["history_keep_days"] = days
        save_settings(settings)
        n = self._purge_history()
        self.refresh_history()
        msg = tr("历史记录：{0}", self.keep_box.currentText())
        if n:
            msg += tr("，已删除 {0} 条过期记录", n)
        self.show_toast(msg)

    def _purge_history(self):
        try:
            return history.purge(int(settings.get("history_keep_days", history.DEFAULT_KEEP_DAYS)))
        except Exception as e:
            file_logger.warning(f"purge history failed: {e}")
            return 0

    # ---------- 页面：缺失查找 ----------
    def _page_missing(self):
        w, lay = self._page(tr("缺失查找"), tr("对没有供应商报价的商品，在供应商文件里按条码前缀和品名找可能的替代品。需要先完成一次订单处理。"))
        bar = QHBoxLayout()
        bar.setSpacing(12)
        bar.addWidget(QLabel(tr("匹配阈值")))
        self.thr = QDoubleSpinBox()
        self.thr.setFixedWidth(80)
        self.thr.setRange(0.1, 1.0)
        self.thr.setSingleStep(0.05)
        self.thr.setValue(float(settings.get("match_threshold", 0.6)))
        self.thr.setToolTip(tr("0 到 1，越大越严格"))
        bar.addWidget(self.thr)
        self.t_web = Toggle(tr("同时网络搜索（较慢）"), settings.get("use_web", False))
        bar.addWidget(self.t_web)
        bar.addStretch()
        self.btn_find = QPushButton(tr("开始查找"))
        self.btn_find.setObjectName("Primary")
        self.btn_find.clicked.connect(self.run_missing)
        self.btn_web = QPushButton(tr("网络搜索选中行"))
        self.btn_web.clicked.connect(self.run_web)
        b_exp = QPushButton(tr("导出 Excel"))
        b_exp.clicked.connect(self.export_missing)
        bar.addWidget(self.btn_find)
        bar.addWidget(self.btn_web)
        bar.addWidget(b_exp)
        lay.addLayout(bar)

        p = Panel()
        top = QHBoxLayout()
        self.miss_status = QLabel(tr("还没有查找结果。"))
        self.miss_status.setObjectName("Muted")
        self.miss_filter = QLineEdit()
        self.miss_filter.setPlaceholderText(tr("筛选 EAN 或品名"))
        self.miss_filter.setFixedWidth(240)
        self.miss_filter.textChanged.connect(self._filter_missing)
        top.addWidget(self.miss_status, 1)
        top.addWidget(self.miss_filter)
        p.lay.addLayout(top)
        self.miss_table = make_table(["EAN", "ARTICOLO", tr("数量"), tr("综合匹配"), tr("品名匹配"), tr("网络搜索结果"),
                                      tr("搜索时间"), tr("可能的替代品")], stretch_col=7, numeric_cols=(2, 3, 4))
        self.miss_table.setMinimumHeight(460)
        self.miss_table.setWordWrap(True)
        p.lay.addWidget(self.miss_table)
        lay.addWidget(p, 1)
        return w

    # ---------- 页面：起订量 ----------
    def _page_rules(self):
        w, lay = self._page(tr("起订量"), tr("按最低价分到的货不够某家起订量时，程序自动算出最省钱的凑单方案。凑单多花超过「手动调整线」会打开手动调整窗口；超过「放弃供应商线」、或者凑单比放弃这家还贵时，会弹窗问你继续凑单还是放弃。修改后自动保存。"))
        top = QHBoxLayout()
        self.t_min2 = Toggle(tr("启用起订量凑单（关闭后只按最低价选供应商）"), settings.get("min_order_enabled", True))
        self.t_min2.toggled.connect(lambda on: self.t_min.setChecked(on))
        top.addWidget(self.t_min2)
        top.addSpacing(24)
        self.t_partial = Toggle(tr("允许部分商品不采购"), settings.get("allow_partial_purchase", False))
        self.t_partial.setToolTip(tr("关闭（默认）：所有商品都必须订到，只有某家有货的商品，不能因为放弃这家而不买。\n打开：可以放弃起订供应商，只有它有货的商品这次就不采购。"))
        self.t_partial.toggled.connect(lambda on: (settings.__setitem__("allow_partial_purchase", on),
                                                   save_settings(settings),
                                                   self.show_toast(tr("已允许部分商品不采购") if on else tr("所有商品都必须采购"))))
        top.addWidget(self.t_partial)
        top.addStretch()
        add = QPushButton(tr("新增供应商规则"))
        add.setObjectName("Primary")
        add.clicked.connect(self.add_rule)
        top.addWidget(add)
        lay.addLayout(top)
        self.banner_rules = Banner()
        lay.addWidget(self.banner_rules)

        p = Panel()
        self.rules_table = make_table([tr("供应商"), tr("文件名关键字"), tr("起订条件"), tr("手动调整线"), tr("放弃供应商线"), tr("本次匹配的文件"), tr("状态")],
                                      stretch_col=5)
        self.rules_table.setItemDelegateForColumn(6, StatusDelegate(self.rules_table))
        self.rules_table.setMinimumHeight(300)
        self.rules_table.doubleClicked.connect(lambda _: self.edit_rule())
        p.lay.addWidget(self.rules_table)
        acts = QHBoxLayout()
        for text, fn, name in [(tr("编辑"), self.edit_rule, None), (tr("启用 / 停用"), self.toggle_rule, None),
                               (tr("删除"), self.delete_rule, "Danger")]:
            b = QPushButton(text)
            if name:
                b.setObjectName(name)
            b.clicked.connect(fn)
            acts.addWidget(b)
        acts.addStretch()
        h = QLabel(tr("双击一行也可以编辑。设置文件：{0}", SETTINGS_PATH))
        h.setObjectName("Faint")
        acts.addWidget(h)
        p.lay.addLayout(acts)
        lay.addWidget(p)
        lay.addStretch()
        return w

    # ---------- 页面：日志 ----------
    def _page_log(self):
        w, lay = self._page(tr("运行日志"), tr("本次打开程序以来的全部记录。完整历史在日志文件里。"))
        bar = QHBoxLayout()
        self.log_btns = QButtonGroup(self)
        for i, (key, text) in enumerate([("all", tr("全部")), ("warn", tr("提醒和错误")), ("err", tr("只看错误"))]):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, k=key: self._set_log_filter(k))
            self.log_btns.addButton(b)
            bar.addWidget(b)
        bar.addStretch()
        b1 = QPushButton(tr("清空"))
        b1.clicked.connect(self._clear_log)
        b2 = QPushButton(tr("打开日志文件"))
        b2.clicked.connect(lambda: open_path(os.path.abspath(os.path.join(LOG_DIR, LOG_FILE))))
        bar.addWidget(b1)
        bar.addWidget(b2)
        lay.addLayout(bar)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(560)
        lay.addWidget(self.log_view, 1)
        return w

    # ---------- 日志 ----------
    def log(self, msg):
        """任何线程都可以调用"""
        file_logger.info(msg)
        self.bridge.log.emit(msg)

    def _fmt_line(self, level, text):
        import html
        color = {"ok": T("ok"), "warn": T("warn"), "err": T("err"), "info": T("info")}.get(level, T("text"))
        safe = html.escape(text).replace(" ", "&nbsp;")
        return f'<span style="color:{color}">{safe}</span>'

    def _on_log(self, msg):
        for line in msg.split("\n"):
            lvl = log_level(line)
            self.log_entries.append((lvl, line))
            if self._log_visible(lvl):
                self.log_view.append(self._fmt_line(lvl, line))
            if line.strip() and not set(line.strip()) <= {"="}:
                self.feed_entries.append((lvl, line))
                self.feed.append(self._fmt_line(lvl, line))
        self.feed.moveCursor(QTextCursor.End)

    def _log_visible(self, lvl):
        return self.log_filter == "all" or (self.log_filter == "warn" and lvl in ("warn", "err")) or \
            (self.log_filter == "err" and lvl == "err")

    def _set_log_filter(self, k):
        self.log_filter = k
        self._render_log()

    def _render_log(self):
        if not hasattr(self, "log_view"):
            return
        self.log_view.setHtml("<br>".join(self._fmt_line(l, t) for l, t in self.log_entries if self._log_visible(l)))
        self.log_view.moveCursor(QTextCursor.End)
        self.feed.setHtml("<br>".join(self._fmt_line(l, t) for l, t in self.feed_entries))
        self.feed.moveCursor(QTextCursor.End)

    def _clear_log(self):
        self.log_entries.clear()
        self.log_view.clear()

    # ---------- 文件夹 ----------
    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择总文件夹"), self.folder_edit.text().strip() or "")
        if path:
            self.folder_edit.setText(os.path.normpath(path))
            settings["last_folder"] = self.folder_edit.text()
            save_settings(settings)
            self.refresh_folder()

    # ---------- 文件拖入：ORDINE / eliminare / distribuire ----------
    @staticmethod
    def _file_tip(folder, name):
        p = os.path.join(folder, name)
        try:
            t = datetime.fromtimestamp(os.path.getmtime(p))
            return t, tr("{0}\n更新于 {1:%Y-%m-%d %H:%M}", name, t)
        except OSError:
            return None, name

    def _fill_zones(self, path, info):
        ok = bool(path) and os.path.isdir(path)
        no_root = tr("先在上面选总文件夹；也可以直接拖文件进来，会先让你选")
        if not ok:
            for z in (self.zone_ordine, self.zone_elim, self.zone_sup):
                z.set_files([], no_root)
            return

        def items(sub, names, with_date=False):
            folder = os.path.join(path, sub)
            out = []
            for n in names:
                t, tip = self._file_tip(folder, n)
                out.append((f"{n}  ({t:%d/%m})" if with_date and t else n, tip))
            return out

        self.zone_ordine.set_files(items(ORDINE_FOLDER, [info["ordine"]] if info["ordine"] else []),
                                   tr("拖新的主订单进来会替换现在这个（旧的挪进 ORDINE\\_旧订单）") if info["ordine"]
                                   else tr("把主订单拖到这里"))
        self.zone_elim.set_files(items(ELIMINARE_FOLDER, info["eliminare"] or []),
                                 tr("把已处理 / 已下单的文件拖到这里，可以一次拖好几个"))
        self.zone_sup.set_files(items(DISTRIBUTUIRE_FOLDER, info["suppliers"] or [], True),
                                tr("每周拖新的报价文件进来，自动替换同一家的旧报价"))

    def _zone_name(self, w, name):
        """× 按钮发来的名字带着「(日期)」，还原成文件名"""
        return re.sub(r"\s+\(\d\d/\d\d\)$", "", name)

    def _ensure_root(self):
        root = self.folder_edit.text().strip()
        if root and os.path.isdir(root):
            return root
        QMessageBox.information(self, tr("批发商比价"), tr("先选比价用的总文件夹（ORDINE、eliminare、distribuire 的上一层），文件会放进它里面。"))
        root = QFileDialog.getExistingDirectory(self, tr("选择总文件夹"), root or "")
        if not root:
            return None
        self.folder_edit.setText(os.path.normpath(root))
        settings["last_folder"] = self.folder_edit.text()
        save_settings(settings)
        return os.path.normpath(root)

    def _busy_drop(self):
        if self.running:
            QMessageBox.information(self, tr("批发商比价"), tr("正在处理中，等处理完再放文件。"))
            return True
        return False

    def _file_error(self, e):
        if isinstance(e, PermissionError):
            msg = tr("文件被别的程序打开着（例如在 WPS / Excel 里），关掉以后再试一次。\n\n{0}", e)
        else:
            msg = str(e)
        QMessageBox.warning(self, tr("放入文件"), msg)

    def _after_drop(self, msg):
        self.log("[文件] " + msg)
        self.refresh_folder()
        self.show_toast(msg)

    def _drop_ordine(self, files):
        import file_inbox as FI
        if self._busy_drop():
            return
        if len(files) > 1:
            QMessageBox.information(self, tr("主订单"), tr("主订单一次只能放一个文件。"))
            return
        root = self._ensure_root()
        if not root:
            return
        cur = (self.info or {}).get("ordine")
        new = os.path.basename(files[0])
        if cur and cur != new and QMessageBox.question(
                self, tr("主订单"), tr("把主订单换成「{0}」？\n原来的「{1}」挪进 ORDINE\\_旧订单（不删除）。", new, cur)) != QMessageBox.Yes:
            return
        try:
            r = FI.import_ordine(root, files)
        except Exception as e:
            self._file_error(e)
            return
        self._after_drop(tr("主订单已换成 {0}", os.path.basename(r["file"])))

    def _drop_eliminare(self, files):
        import file_inbox as FI
        if self._busy_drop():
            return
        root = self._ensure_root()
        if not root:
            return
        replace_all = False
        cur = FI.excel_files(os.path.join(root, ELIMINARE_FOLDER))
        if [f for f in cur if f not in {os.path.basename(x) for x in files}]:
            box = QMessageBox(self)
            box.setWindowTitle(tr("扣减文件"))
            box.setText(tr("eliminare 里已经有 {0} 个文件。这 {1} 个新文件：", len(cur), len(files)))
            b_add = box.addButton(tr("一起加进去"), QMessageBox.AcceptRole)
            b_rep = box.addButton(tr("替换原来的"), QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.setInformativeText(tr("「替换原来的」会把现在那几个挪进 eliminare\\_旧文件（不删除）。"))
            box.exec()
            if box.clickedButton() not in (b_add, b_rep):
                return
            replace_all = box.clickedButton() == b_rep
        try:
            r = FI.import_eliminare(root, files, replace_all)
        except Exception as e:
            self._file_error(e)
            return
        self._after_drop(tr("扣减文件放好了：{0} 个", len(r["files"])) +
                         (tr("，原来的 {0} 个挪进了 _旧文件", len(r["archived"])) if r["archived"] else ""))

    def _drop_suppliers(self, files):
        import file_inbox as FI
        if self._busy_drop():
            return
        root = self._ensure_root()
        if not root:
            return
        try:
            plan = FI.plan_suppliers(root, files, [r.get("keyword") for r in settings.get("rules", [])])
        except Exception as e:
            self._file_error(e)
            return
        existing = scan_folder(root).get("suppliers") or []       # 现在的供应商报价（不含临时文件、缺失报告）
        dlg = SupplierDropDialog(self, plan, existing)
        if not dlg.exec():
            return
        try:
            done = FI.apply_suppliers(root, dlg.result_plan())
        except Exception as e:
            self._file_error(e)
            return
        rep = [f"{old} → {new}" for new, old in done if old and old != new]
        add = [new for new, old in done if not old]
        parts = []
        if rep:
            parts.append(tr("替换 {0} 家：{1}", len(rep), tr("、").join(rep)))
        if add:
            parts.append(tr("新增 {0} 家：{1}", len(add), tr("、").join(add)))
        same = [new for new, old in done if old == new]
        if same:
            parts.append(tr("更新 {0} 个同名文件", len(same)))
        self._after_drop(tr("供应商报价已更新") + "：" + "；".join(parts))

    def _remove_file(self, sub, name):
        import file_inbox as FI
        if self._busy_drop():
            return
        root = self.folder_edit.text().strip()
        name = self._zone_name(None, name)
        if not root or not os.path.exists(os.path.join(root, sub, name)):
            self.refresh_folder()
            return
        if QMessageBox.question(self, tr("拿走文件"), tr("把「{0}」从 {1} 拿走？\n文件会挪进 {1}\\{2}，不会删除。",
                                                         name, sub, FI.OLD_DIRS.get(sub, "_旧文件"))) != QMessageBox.Yes:
            return
        try:
            FI.remove_file(root, sub, name)
        except Exception as e:
            self._file_error(e)
            return
        self._after_drop(tr("已拿走 {0}", name))

    def _open_sub(self, sub):
        root = self.folder_edit.text().strip()
        p = os.path.join(root, sub) if root else ""
        if p and os.path.isdir(p):
            open_path(p)
        elif root and os.path.isdir(root):
            open_path(root)
        else:
            QMessageBox.information(self, APP_NAME, tr("还没有选总文件夹。"))

    def refresh_folder(self):
        path = self.folder_edit.text().strip()
        self.info = info = scan_folder(path)
        self._fill_zones(path, info)
        if not path:
            self.banner_folder.show_msg(tr("还没有选择文件夹。点「选择文件夹…」或按 Ctrl+O。"), "info")
        elif info["errors"]:
            self.banner_folder.show_msg(tr("；").join(info["errors"]), "err")
        elif info["warnings"]:
            self.banner_folder.show_msg(tr("；").join(info["warnings"]), "warn")
        else:
            self.banner_folder.hide()
        self.refresh_rule_line()
        self.refresh_rules_table()
        if hasattr(self, "order_check"):
            self.order_check.folder_changed()
        if hasattr(self, "cmp_steps") and not self.running:
            ok = bool(info.get("ok"))
            self.cmp_steps.set_state(0, "done" if ok else "now", info.get("ordine") or tr("选择总文件夹"))
            self.cmp_steps.set_state(1, "todo", tr("可选"))
            self.cmp_steps.set_state(2, "now" if ok else "todo", tr("{0} 家供应商", len(info.get("suppliers") or [])) if ok else "")
            self.cmp_steps.set_state(3, "todo", "")

    def edit_scenarios(self):
        import scenarios as SC
        dlg = ScenarioSettingsDialog(self, SC.scenario_defs(settings), (self.info or {}).get("suppliers") or [])
        if dlg.exec():
            defs = dlg.defs()
            if defs:
                settings["scenarios"] = defs
                save_settings(settings)
                self.refresh_rule_line()

    def _scenario_line(self):
        if not settings.get("scenarios_enabled", True):
            return ""
        import scenarios as SC
        defs = SC.scenario_defs(settings)
        if len(defs) < 2:
            return ""
        return tr("多方案对比：{0}（处理时对比后由你选）", tr("、").join(tr(d["name"]) for d in defs))

    def refresh_rule_line(self):
        self._refresh_rule_line()
        extra = self._scenario_line()
        if extra:
            self.rule_line.setText(self.rule_line.text() + "\n" + extra)

    def _refresh_rule_line(self):
        if not self.t_min.isChecked():
            self.rule_line.setText(tr("起订量凑单已关闭，只按最低价选供应商。"))
            return
        enabled = [r for r in settings["rules"] if r.get("enabled", True)]
        if not enabled:
            self.rule_line.setText(tr("还没有启用的起订规则。到「起订量」页添加。"))
            return
        matched, _ = match_rules(enabled, self.info.get("suppliers") or [])
        hit = {id(r) for r, _ in matched}
        parts = []
        for r in enabled:
            tail = ""
            if self.info.get("suppliers"):
                tail = "" if id(r) in hit else tr("（这次没有它的文件）")
            parts.append(f"{r.get('name') or r.get('keyword')}：{describe_condition(r)}{tail}")
        self.rule_line.setText(tr("起订规则　") + "　　".join(parts))

    def _shown_state(self):
        return (self.last_summary or {}).get("state") or {}

    def open_supplier_folder(self):
        p = self._shown_state().get("distribuire_path") or \
            os.path.join(self.folder_edit.text().strip(), DISTRIBUTUIRE_FOLDER)
        open_path(p)

    def open_main_order(self):
        p = self._shown_state().get("ordine_file")
        if p and os.path.exists(p):
            open_path(p)
        elif self.info.get("ordine"):
            open_path(os.path.join(self.folder_edit.text().strip(), ORDINE_FOLDER, self.info["ordine"]))
        else:
            QMessageBox.information(self, APP_NAME, tr("请先选择包含 ORDINE 的总文件夹。"))

    # ---------- 处理 ----------
    def start(self):
        if self.running:
            return
        self.refresh_folder()
        info = self.info
        if not info["ok"]:
            QMessageBox.warning(self, tr("不能开始"), tr("文件夹检查没有通过：\n\n{0}", "\n".join(info["errors"])))
            return
        root_path = self.folder_edit.text().strip()
        n_rules = len([r for r in settings["rules"] if r.get("enabled", True)]) if self.t_min.isChecked() else 0
        msg = (tr("主订单：{0}\n扣减文件：{1} 个　供应商：{2} 家\n起订量凑单：{3}\n\n这次处理会按 eliminare 扣减主订单数量并覆盖保存主订单，然后把订购数量写进各供应商文件的 ORDINE 列。\n\n", info['ordine'], len(info['eliminare']), len(info['suppliers']), tr("开启，{0} 条规则", n_rules) if self.t_min.isChecked() else tr("关闭"))
               + (tr("处理前会自动备份到 总文件夹\\_备份。") if self.t_backup.isChecked()
                  else tr("自动备份已关闭，建议先手动备份。"))
               + tr("\n\n重复处理同一批文件时，会先恢复成原文件再算，不会重复扣减，结果和上次一样。")
               + (("\n\n" + self._scenario_line()) if self._scenario_line() else ""))
        box = QMessageBox(self)
        box.setWindowTitle(tr("开始处理"))
        box.setText(tr("确认开始处理？"))
        box.setInformativeText(msg)
        go = box.addButton(tr("开始处理"), QMessageBox.AcceptRole)
        box.addButton(tr("取消"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not go:
            return

        settings.update(last_folder=root_path, auto_backup=self.t_backup.isChecked(),
                        min_order_enabled=self.t_min.isChecked())
        save_settings(settings)
        self.running = True
        self.btn_run.setEnabled(False)
        self.btn_run.setText(tr("处理中…"))
        self.bar.setValue(0)
        self.feed.clear()
        self.feed_entries.clear()
        self.run_start = time.time()
        self.tick.start()
        self._tick()
        from erp_widgets import TaskDialog
        self._cmp_task = TaskDialog(self, tr("正在比价分配"), tr("{0} · {1} 家供应商", info["ordine"], len(info["suppliers"])),
                                    [tr(x) for _, x in CMP_STAGES], cancellable=False)
        self._cmp_task.went_background.connect(lambda: self.show_toast(tr("比价在后台进行，完成后会提醒")))
        self._cmp_task.show()
        self.cmp_steps.set_current(2)

        ordine_file = os.path.join(root_path, ORDINE_FOLDER, info["ordine"])
        elim = os.path.join(root_path, ELIMINARE_FOLDER)
        dist = os.path.join(root_path, DISTRIBUTUIRE_FOLDER)
        run_settings = copy.deepcopy(settings)

        def work():
            summary = None
            try:
                self.log(tr("总文件夹：{0}", root_path))
                self.log(tr("主订单：{0}", ordine_file))
                summary = run_process(ordine_file, elim, dist, self.log,
                                      progress=lambda p, t: self.bridge.progress.emit(int(p), t),
                                      settings=run_settings, decide=self._decide_from_worker,
                                      choose=self._choose_from_worker)
            except Exception as e:
                file_logger.error(tr("处理失败: {0}", e), exc_info=True)
                self.log(tr("❌ 处理失败：{0}", e))
                summary = {"ok": False, "error": str(e)}
            self.bridge.done.emit(summary)

        threading.Thread(target=work, daemon=True).start()

    def _choose_from_worker(self, results):
        """在后台线程里调用：把几种方案交给界面对比，等用户选好后返回方案下标（None = 取消）"""
        ev = threading.Event()
        box = {}
        self.bridge.choose.emit((results, ev, box))
        ev.wait()
        return box.get("idx")

    def _on_choose(self, payload):
        results, ev, box = payload
        dlg = getattr(self, "_cmp_task", None)
        was_visible = dlg is not None and dlg.isVisible()
        if was_visible:
            dlg.hide()
        try:
            self.stage.setText(tr("等待你选择方案…"))
            QApplication.alert(self)
            d = ScenarioDialog(self, results)
            box["idx"] = d.chosen if d.exec() else None
            if box["idx"] is None:
                self.log(tr("⚠ 在选择方案时取消了处理"))
        except Exception as e:
            file_logger.error(tr("方案对比出错: {0}", e), exc_info=True)
            QMessageBox.critical(self, APP_NAME, tr("方案对比出错：{0}\n\n改用第一个方案。", e))
            box["idx"] = 0
        finally:
            if was_visible and not dlg.is_done:
                dlg.show()
            ev.set()

    def _decide_from_worker(self, plan, rows, rules):
        """在后台线程里调用：把方案交给界面，等操作员决定后返回"""
        ev = threading.Event()
        box = {}
        self.bridge.decide.emit((plan, rows, rules, ev, box))
        ev.wait()
        return box.get("result")

    def _on_decide(self, payload):
        plan, rows, rules, ev, box = payload
        dlg = getattr(self, "_cmp_task", None)
        was_visible = dlg is not None and dlg.isVisible()
        if was_visible:
            dlg.hide()            # 让决定窗口在最前面
        try:
            box["result"] = self._run_decision(plan, rows, rules)
        except Exception as e:
            file_logger.error(tr("决定方案出错: {0}", e), exc_info=True)
            QMessageBox.critical(self, APP_NAME, tr("计算方案时出错：{0}\n\n已改用程序自动算出的方案。", e))
            from allocation_plan import auto_assignment
            box["result"] = {"assignment": auto_assignment(rows, rules), "sources": {}}
        finally:
            if was_visible and not dlg.is_done:
                dlg.show()
            ev.set()

    def _run_decision(self, plan, rows, rules):
        from allocation_plan import apply_decisions
        if plan.get("fixed") is not None:
            # 已经在方案对比里选好了方案：只打开手动调整窗口确认（写入前人工确认打开时才会到这里）
            final, sources = dict(plan["fixed"]), dict(plan.get("fixed_sources") or {})
            self.stage.setText(tr("等待你确认分配方案…"))
            dlg = ManualDialog(self, rows, rules, plan["baseline"], final)
            if not dlg.exec() or dlg.result is None:
                self.log(tr("⚠ 操作员在手动调整窗口取消了处理"))
                return None
            for r in rows:
                if dlg.result.get(r["row"]) != final.get(r["row"]):
                    sources[r["row"]] = "手动"
            self.stage.setText(tr("写入文件…"))
            return {"assignment": dlg.result, "sources": sources}
        decisions, want_manual = {}, False
        flagged = [i for i in plan["rules"] if i["needs_decision"]]
        if flagged:
            self.stage.setText(tr("等待你决定起订量方案…"))
            QApplication.alert(self)
        for info in plan["rules"]:
            if not info["needs_decision"]:
                decisions[info["target"]] = info["default"]
                continue
            dlg = DecisionDialog(self, info)
            if not dlg.exec() or dlg.choice is None:
                self.log(tr("⚠ 操作员在 {0} 的决定窗口取消了处理", info['name']))
                return None
            choice = dlg.choice
            if choice == "manual":
                want_manual = True
                choice = "fill" if (info["fill_ok"] or not info["drop_allowed"]) else "drop"
            decisions[info["target"]] = choice
            label = {"fill": tr("继续凑单") if info["fill_ok"] else tr("按最低价下单（达不到起订量）"),
                     "drop": tr("放弃该供应商")}[choice]
            self.log(tr("操作员选择：{0} → {1}，并手动调整", info['name'], label) if dlg.choice == "manual"
                     else tr("操作员选择：{0} → {1}", info['name'], label))

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dropped = {t for t, d in decisions.items() if d == "drop"}
            if not rules:
                final = dict(plan["baseline"])
            elif not dropped and plan.get("fill_all_ok", True):
                final = dict(plan["fill_all"])
            else:
                final = apply_decisions(rows, rules, decisions)
        finally:
            QApplication.restoreOverrideCursor()

        sources = {}
        for r in rows:
            b, f = plan["baseline"].get(r["row"]), final.get(r["row"])
            if b != f:
                sources[r["row"]] = "放弃供应商" if b in dropped else "自动凑单"

        # 自动分配完成 → 超过手动调整线才提示要不要手动改
        notes = [n for info in plan["rules"]
                 if decisions.get(info["target"]) == "fill" and info.get("needs_manual")
                 for n in info["manual_reasons"]]
        if notes and not settings.get("review_before_write"):
            for n in notes:
                self.log(tr("⚠ {0}", n))
            QApplication.alert(self)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle(tr("自动分配完成"))
            box.setText(tr("自动分配完成，凑单多花的钱超过了手动调整线："))
            box.setInformativeText("\n".join("• " + n for n in notes) + "\n\n" + tr("要手动调整吗？"))
            b_adj = box.addButton(tr("手动调整…"), QMessageBox.AcceptRole)
            b_adj.setStyleSheet(f"background:{T('accent')}; color:{T('on_accent')}; border:none; "
                                "font-weight:600; padding:7px 14px;")
            b_skip = box.addButton(tr("不用，直接写入"), QMessageBox.RejectRole)
            box.setDefaultButton(b_adj)
            box.exec()
            if box.clickedButton() is b_adj:
                want_manual = True
                self.log(tr("操作员选择手动调整"))
            else:
                self.log(tr("操作员选择不手动调整，直接写入"))
        if want_manual or settings.get("review_before_write"):
            self.stage.setText(tr("等待你确认分配方案…"))
            QApplication.alert(self)
            dlg = ManualDialog(self, rows, rules, plan["baseline"], final, notes)
            if not dlg.exec() or dlg.result is None:
                self.log(tr("⚠ 操作员在手动调整窗口取消了处理"))
                return None
            manual = dlg.result
            for r in rows:
                if manual.get(r["row"]) != final.get(r["row"]):
                    sources[r["row"]] = "手动"
                elif manual.get(r["row"]) == plan["baseline"].get(r["row"]):
                    sources.pop(r["row"], None)
            n = sum(1 for v in sources.values() if v == "手动")
            if n:
                self.log(tr("操作员手动改了 {0} 个商品的供应商", n))
            final = manual
        self.stage.setText(tr("写入文件…"))
        return {"assignment": final, "sources": sources}

    def _load_saved_result(self):
        """打开程序时，载入这个文件夹上次的分配结果（可以继续修改）；没有的话显示最近一条历史记录"""
        from service import load_state, save_state
        if self.last_summary:
            return
        path = self.folder_edit.text().strip()
        data = load_state(path) if path else None
        if data and data.get("ok"):
            if not data.get("history_id"):
                # 旧版本留下的结果：补存进历史记录
                try:
                    st_path = os.path.join(path, "_备份", "last_allocation.json")
                    when = datetime.fromtimestamp(os.path.getmtime(st_path))
                    history.save_run(data, when=when)
                    save_state(data)
                except Exception as e:
                    file_logger.warning(f"import last allocation to history failed: {e}")
            self.last_summary = data
            self.show_summary(data)
            return
        runs = history.list_runs()
        if runs:
            data = history.load_run(runs[0]["id"])
            if data and data.get("ok"):
                self.last_summary = data
                self.show_summary(data)

    def _is_current(self, s):
        """这条结果是不是它那个文件夹最近一次的分配（只有最近一次可以修改分配）"""
        from service import load_state
        st = (s or {}).get("state") or {}
        ordine = st.get("ordine_file")
        if not ordine or not os.path.exists(ordine):
            return False
        cur = load_state(os.path.dirname(os.path.dirname(ordine)))
        if not cur:
            return False
        return cur.get("history_id") == s.get("history_id")

    def edit_allocation(self):
        from service import load_state
        if self.running:
            return
        data = self.last_summary if (self.last_summary or {}).get("state") else load_state(self.folder_edit.text().strip())
        if not data or not data.get("state"):
            QMessageBox.information(self, tr("修改分配"), tr("没有可以修改的分配。先完成一次订单处理。"))
            return
        if not self._is_current(data):
            QMessageBox.information(self, tr("修改分配"),
                                    tr("这是历史记录，之后这个文件夹又处理过（或者文件已经不在了），所以不能再改。只能修改每个文件夹最近一次的分配。"))
            return
        st = data["state"]
        cur = dict(st["assignment"])
        dlg = ManualDialog(self, st["rows"], st["rules"], st["baseline"], cur, edit_mode=True)
        if not dlg.exec() or dlg.result is None:
            return
        new = dlg.result
        changed = [r["row"] for r in st["rows"] if new.get(r["row"]) != cur.get(r["row"])]
        if not changed:
            self.show_toast(tr("没有改动"))
            return
        if QMessageBox.question(self, tr("修改分配"),
                                tr("要把 {0} 个商品换供应商，并重新写入主订单和供应商文件。写入前会自动备份。继续？",
                                   len(changed))) != QMessageBox.Yes:
            return
        sources = dict(st["sources"])
        for r in st["rows"]:
            row = r["row"]
            if new.get(row) == cur.get(row):
                continue
            if new.get(row) == st["baseline"].get(row):
                sources.pop(row, None)
            else:
                sources[row] = "手动"
        self.running = True
        self.btn_run.setEnabled(False)
        self.btn_edit.setEnabled(False)
        self.go_page("order")
        self.bar.setValue(0)
        self.run_start = time.time()
        self.tick.start()

        def work():
            from service import rewrite_assignment
            try:
                out, n = rewrite_assignment(data, new, sources, self.log,
                                            progress=lambda p, t: self.bridge.progress.emit(int(p), t))
                self.bridge.rewrite_done.emit((out, n, None))
            except Exception as e:
                file_logger.error(f"rewrite failed: {e}", exc_info=True)
                self.log(tr("❌ 处理失败：{0}", e))
                self.bridge.rewrite_done.emit((None, 0, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _on_rewrite_done(self, payload):
        out, n, err = payload
        self.running = False
        self.tick.stop()
        self.btn_run.setEnabled(True)
        self.elapsed.setText(tr("用时 {0:.0f} 秒", time.time() - self.run_start))
        if err or not out:
            self.stage.setText(tr("处理没有完成"))
            QMessageBox.critical(self, tr("修改分配"), tr("{0}\n\n详细信息在「运行日志」页。", err or tr("未知错误")))
            if self.last_summary:
                self.btn_edit.setEnabled(True)
            return
        self.bar.setValue(100)
        self.stage.setText(tr("修改分配已写入"))
        out["elapsed"] = (self.last_summary or {}).get("elapsed", 0)
        self.last_summary = out
        self.show_summary(out)
        self.refresh_history()
        self.go_page("result")
        self.show_toast(tr("已重新写入 {0} 个商品", n))

    def _tick(self):
        self.elapsed.setText(tr("已用时 {0:.0f} 秒", time.time() - self.run_start))

    def _on_progress(self, pct, text):
        self.bar.setValue(pct)
        self.stage.setText(text)
        dlg = getattr(self, "_cmp_task", None)
        if dlg is not None and not dlg.is_done:
            i = max(k for k, (lo, _) in enumerate(CMP_STAGES) if pct >= lo)
            dlg.post_step(i, "running", "", text)
            dlg.post_progress(pct)

    def _on_done(self, summary):
        dlg = getattr(self, "_cmp_task", None)
        if dlg is not None and not dlg.is_done:
            ok = bool(summary and summary.get("ok"))
            dlg.post_finished(ok, tr("处理完成") if ok else (summary or {}).get("error") or tr("处理没有完成"))
        self.running = False
        self.tick.stop()
        self.btn_run.setEnabled(True)
        self.btn_run.setText(tr("开始处理"))
        self.elapsed.setText(tr("用时 {0:.0f} 秒", time.time() - self.run_start))
        if summary and summary.get("cancelled"):
            self.bar.setValue(0)
            self.stage.setText(tr("已取消，文件没有改动"))
            self.show_toast(tr("已取消，文件没有改动"))
            return
        if not summary or not summary.get("ok"):
            self.bar.setValue(0)
            self.stage.setText(tr("处理没有完成"))
            err = (summary or {}).get("error") or tr("未知错误")
            QMessageBox.critical(self, tr("处理没有完成"), tr("{0}\n\n详细信息在「运行日志」页。", err))
            return
        self.bar.setValue(100)
        self.stage.setText(tr("处理完成，主订单和供应商文件已保存"))
        self.last_summary = summary
        self.show_summary(summary)
        self.refresh_history()
        self.cmp_steps.set_current(3)
        self.cmp_steps.set_state(3, "done", "")
        self.go_page("result")
        self.show_toast(tr("比价分配完成"))

    def show_missing(self, *_):
        s = self.last_summary or {}
        items = s.get("missing_items")
        if items is None:
            QMessageBox.information(self, tr("缺失商品"), tr("这条结果是旧版本处理的，没有缺失商品明细；可以打开 distribuire 里的「缺失报告.xlsx」。"))
            return
        name = s.get("scenario")
        MissingDialog(self, items, tr("缺失商品 · {0}", s.get("main_file") or "") +
                      (f" · {tr(name)}" if name else "")).exec()

    def _fill_scen_switch(self, s, res):
        """方案对比下面的「改用 xx」按钮（只有每个文件夹最近一次的结果、而且存了各方案分配时才能换）"""
        from service import can_switch_scenario
        while self.scen_switch_lay.count():
            it = self.scen_switch_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not res:
            return
        if not can_switch_scenario(s):
            lb = QLabel(tr("这条结果是旧版本处理的，没有存各方案的分配；要换方案请重新处理。"))
        elif not self._is_current(s):
            lb = QLabel(tr("这是历史记录，之后这个文件夹又处理过（或者文件已经不在了），不能再换方案。"))
        else:
            lb = QLabel(tr("对选的方案不满意？不用重算，直接改用："))
        lb.setObjectName("Faint")
        self.scen_switch_lay.addWidget(lb)
        if can_switch_scenario(s) and self._is_current(s):
            for i, r in enumerate(res):
                if r.get("chosen"):
                    continue
                b = QPushButton(tr("改用「{0}」", tr(r["name"])))
                b.setObjectName("Outline")
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, k=i: self.switch_scenario(k))
                self.scen_switch_lay.addWidget(b)
        self.scen_switch_lay.addStretch(1)

    def switch_scenario(self, idx):
        """结果页换方案：用当时算好的分配，只重新写入换了供应商的商品；历史记录同一条跟着改"""
        from service import can_switch_scenario
        if self.running:
            return
        data = self.last_summary or {}
        res = data.get("scenarios") or []
        if not can_switch_scenario(data) or not self._is_current(data) or not 0 <= idx < len(res):
            return
        st = data["state"]
        new = st["scenario_assigns"][idx]
        n = sum(1 for r in st["rows"] if new.get(r["row"]) != st["assignment"].get(r["row"]))
        manual = sum(1 for v in (st.get("sources") or {}).values() if v == "手动")
        msg = tr("把最终方案换成「{0}」？\n\n采购总额 €{1:,.2f}，缺失商品 {2} 个；有 {3} 个商品要换供应商，会重新写入主订单和供应商文件（写入前自动备份）。不需要重新计算。",
                 tr(res[idx]["name"]), res[idx]["total"], len(res[idx]["missing"]), n)
        if manual:
            msg += tr("\n\n注意：之前在「修改分配」里手动改的 {0} 个商品会按新方案重新分配。", manual)
        msg += tr("\n\n历史记录里这一条也会改成新方案。")
        if QMessageBox.question(self, tr("换方案"), msg) != QMessageBox.Yes:
            return
        self.running = True
        self.btn_run.setEnabled(False)
        self.btn_edit.setEnabled(False)
        self.go_page("order")
        self.bar.setValue(0)
        self.run_start = time.time()
        self.tick.start()

        def work():
            from service import switch_scenario
            try:
                out, k = switch_scenario(data, idx, self.log,
                                         progress=lambda p, t: self.bridge.progress.emit(int(p), t))
                self.bridge.rewrite_done.emit((out, k, None))
            except Exception as e:
                file_logger.error(f"switch scenario failed: {e}", exc_info=True)
                self.log(tr("❌ 处理失败：{0}", e))
                self.bridge.rewrite_done.emit((None, 0, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _scen_clicked(self, row, col):
        res = (self.last_summary or {}).get("scenarios") or []
        i = getattr(self, "_scen_links", {}).get((row, col))
        if i is not None and i < len(res):
            MissingDialog(self, res[i]["missing"], tr("缺失商品 · {0}", tr(res[i]["name"]))).exec()

    def show_summary(self, s):
        mo = s.get("min_order", {})
        res = s.get("scenarios") or []
        self.scen_panel.setVisible(bool(res))
        if res:
            chosen = next((i for i, r in enumerate(res) if r.get("chosen")), None)
            self._scen_links = fill_scenario_table(self.scen_table, res, chosen)
            self.scen_table.setFixedHeight(min(520, 44 + 36 * self.scen_table.rowCount()))
        self._fill_scen_switch(s, res)
        self.btn_missing.setText(tr("缺失商品 ({0})", len(s.get("missing_items") or [])) if s.get("missing_items") is not None
                                 else tr("缺失商品"))
        self.k_rows.set(f"{s.get('total_rows', 0):,}")
        self.k_priced.set(f"{s.get('priced_rows', 0):,}")
        self.k_missing.set(f"{s.get('missing', 0):,}", T("warn") if s.get("missing") else None)
        n_ch = len(s.get("changes") or [])
        self.k_changes.set(f"{n_ch:,}")
        extra = mo.get("extra_cost", 0) or 0
        self.k_cost.set(f"€{extra:,.2f}", T("warn") if extra > 0.005 else None)
        self.k_time.set(tr("{0:.0f} 秒", s.get('elapsed', 0)))

        msgs, kind = [], "ok"
        current = self._is_current(s)
        hmeta = s.get("_history") or {}
        if s.get("history_id") and not current:
            msgs.append(tr("这是历史记录：{0} 处理的结果。文件之后可能又改过，这里显示的是当时的分配，只能查看。",
                           hmeta.get("time") or s["history_id"]))
        d, u = s.get("eliminare", (0, 0))
        msgs.append(tr("{0} 已处理：eliminare 删除 {1} 行、更新 {2} 行。", s.get('main_file'), d, u))
        if s.get("scenario"):
            msgs.append(tr("选用方案：{0}（几种方案的对比在下面「方案对比」）", tr(s["scenario"])))
        if mo.get("warning"):
            msgs.append(mo["warning"])
            kind = "err"
        if mo.get("unmatched"):
            msgs.append(tr("这些规则这次没有找到供应商文件：{0}", tr("、").join(mo["unmatched"])))
            kind = "warn" if kind == "ok" else kind
        lost = [c for c in (s.get("changes") or []) if c.get("new") is None]
        if lost:
            msgs.append(tr("其中 {0} 个商品是因为放弃了供应商而没有采购（在下面「改了供应商的商品」里显示为「不采购」）。「比只按最低价多花」没有算这些商品，所以可能是负数。", len(lost)))
            kind = "err"
        if s.get("missing"):
            msgs.insert(len(msgs) - (1 if lost else 0),
                        tr("有 {0} 个商品没有采购，可以到「缺失查找」页找替代品。", s['missing']))
            kind = "warn" if kind == "ok" else kind
        if kind == "ok" and s.get("history_id") and not current:
            kind = "info"
        self.banner_result.show_msg("\n".join(msgs), kind)

        self.gauge.set_rules(mo.get("rules", []) if mo.get("enabled") else [])

        rule_by = {r["target"]: r for r in mo.get("rules", [])}
        sups = s.get("suppliers", [])
        t = self.sum_table
        t.setRowCount(len(sups) + 1)
        tot = [0, 0.0, 0.0, 0.0, 0.0]

        def sign(v, fmt):
            return "" if abs(v) < 0.005 else (("+" if v > 0 else "") + fmt.format(v))

        for i, sp in enumerate(sups):
            r = rule_by.get(sp["name"])
            bc, ba = sp.get("before_colli", sp["colli"]), sp.get("before_amount", sp["amount"])
            dc, da = sp["colli"] - bc, sp["amount"] - ba
            table_set_row(t, i, [sp["name"], f"{sp['rows']:,}", f"{bc:,.0f}", f"{sp['colli']:,.0f}",
                                 sign(dc, "{:,.0f}"), f"{ba:,.2f}", f"{sp['amount']:,.2f}", sign(da, "{:,.2f}"),
                                 describe_condition(r) if r else "",
                                 r["status"] if r else ""])
            t.item(i, 0).setToolTip(tr("报价 {0:,} 条，主表命中 {1:,} 个商品，写入 {2:,} 行", sp['prices'], sp['hits'],
                                       sp.get('written', 0)))
            for col, v in ((4, dc), (7, da)):
                if abs(v) >= 0.005:
                    t.item(i, col).setForeground(QColor(T("accent") if v > 0 else T("info")))
            tot[0] += sp["rows"]
            tot[1] += bc
            tot[2] += sp["colli"]
            tot[3] += ba
            tot[4] += sp["amount"]
        table_set_row(t, len(sups), [tr("合计"), f"{tot[0]:,}", f"{tot[1]:,.0f}", f"{tot[2]:,.0f}", "",
                                     f"{tot[3]:,.2f}", f"{tot[4]:,.2f}", sign(tot[4] - tot[3], "{:,.2f}"),
                                     "", ""], bold=True)

        chs = s.get("changes") or []
        ct = self.chg_table
        ct.setRowCount(len(chs))
        for i, ch in enumerate(chs):
            table_set_row(ct, i, [ch["row"], ch.get("ean", ""), ch.get("articolo", ""), f"{ch['qty']:g}",
                                  short(ch["old"]) if ch["old"] else tr("无"),
                                  fmt_unit(ch["old_price"], ch.get("old_pack")),
                                  short(ch["new"]) if ch["new"] else tr("不采购"),
                                  fmt_unit(ch["new_price"], ch.get("new_pack")),
                                  (f"{ch['delta']:+,.2f}" if abs(ch["delta"]) >= 0.005 else "0.00") if ch["delta"] is not None else "",
                                  ch.get("source", "")])
            if ch["delta"] and ch["delta"] > 0:
                ct.item(i, 8).setForeground(QColor(T("warn")))
            if ch["new"] is None:
                ct.item(i, 6).setForeground(QColor(T("err")))
                f = ct.item(i, 6).font()
                f.setBold(True)
                ct.item(i, 6).setFont(f)
        self.chg_title.setText(tr("改了供应商的商品（{0}）", len(chs)))
        ct.setVisible(bool(chs))
        self.chg_empty.setVisible(not chs)
        self._filter_changes(self.chg_filter.text())
        self.btn_backup.setEnabled(bool(s.get("backup_dir")))
        self.btn_edit.setEnabled(bool(s.get("state")) and current and not self.running)
        self.btn_edit.setToolTip(tr("已经写入以后，还可以改商品的供应商并重新写回文件") if current
                                 else tr("只能修改每个文件夹最近一次的分配"))
        if hasattr(self, "hist_tree"):
            self._mark_shown_history()

    # ---------- 起订规则 ----------
    def refresh_rules_table(self):
        if not hasattr(self, "rules_table"):
            return
        rules = settings["rules"]
        sups = self.info.get("suppliers") or []
        matched, _ = match_rules([r for r in rules if r.get("enabled", True)], sups)
        hit = {id(r): h for r, h in matched}
        t = self.rules_table
        t.setRowCount(len(rules))
        for i, r in enumerate(rules):
            if not r.get("enabled", True):
                f, st = "", tr("已停用")
            elif not sups:
                f, st = tr("（先在订单处理页选文件夹）"), tr("启用")
            elif id(r) in hit:
                f, st = hit[id(r)], tr("启用")
            else:
                f, st = "", tr("未找到文件")
            table_set_row(t, i, [r.get("name", ""), r.get("keyword", ""), describe_condition(r),
                                 f"€{float(r.get('manual_extra') or 0):,.2f}" if float(r.get("manual_extra") or 0) else tr("不用"),
                                 f"€{float(r.get('drop_extra') or 0):,.2f}" if float(r.get("drop_extra") or 0) else tr("不用"),
                                 f, st],
                          color=T("faint") if not r.get("enabled", True) else None)
        if not rules:
            self.banner_rules.show_msg(tr("还没有起订规则。点「新增供应商规则」，填上供应商的起订箱数或金额。"), "info")
        else:
            self.banner_rules.hide()

    def _sel_rule(self):
        rows = self.rules_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, tr("起订量"), tr("先在表格里选一条规则。"))
            return None
        return rows[0].row()

    def _save_rules(self, msg):
        if save_settings(settings):
            self.show_toast(msg)
        else:
            QMessageBox.warning(self, tr("起订量"), tr("设置写不进文件，关闭程序后会丢失：\n{0}", SETTINGS_PATH))
        self.refresh_rules_table()
        self.refresh_rule_line()

    def add_rule(self):
        d = RuleDialog(self, {"mode": "all", "allow_drop": True, "enabled": True}, self.info.get("suppliers") or [])
        if d.exec() and d.result_rule:
            settings["rules"].append(d.result_rule)
            self._save_rules(tr("已添加规则"))

    def edit_rule(self):
        i = self._sel_rule()
        if i is None:
            return
        d = RuleDialog(self, settings["rules"][i], self.info.get("suppliers") or [])
        if d.exec() and d.result_rule:
            settings["rules"][i] = d.result_rule
            self._save_rules(tr("已保存规则"))

    def toggle_rule(self):
        i = self._sel_rule()
        if i is None:
            return
        r = settings["rules"][i]
        r["enabled"] = not r.get("enabled", True)
        self._save_rules(tr("已启用") if r["enabled"] else tr("已停用"))

    def delete_rule(self):
        i = self._sel_rule()
        if i is None:
            return
        name = settings["rules"][i].get("name")
        if QMessageBox.question(self, tr("删除规则"), tr("删除「{0}」的起订规则？", name)) == QMessageBox.Yes:
            settings["rules"].pop(i)
            self._save_rules(tr("已删除规则"))

    # ---------- 缺失查找 ----------
    def run_missing(self):
        root_path = self.folder_edit.text().strip()
        dist = os.path.join(root_path, DISTRIBUTUIRE_FOLDER)
        if not root_path or not os.path.isdir(dist):
            QMessageBox.information(self, tr("缺失查找"), tr("先在「订单处理」页选好总文件夹。"))
            return
        if not os.path.exists(os.path.join(dist, "缺失报告.xlsx")):
            QMessageBox.information(self, tr("缺失查找"), tr("还没有缺失报告。先完成一次订单处理，再来查找。"))
            return
        thr, use_web = self.thr.value(), self.t_web.isChecked()
        settings.update(match_threshold=thr, use_web=use_web)
        save_settings(settings)
        self.btn_find.setEnabled(False)
        self.btn_find.setText(tr("查找中…"))
        self.miss_status.setText(tr("正在查找，网络搜索会比较慢…") if use_web else tr("正在查找…"))

        def work():
            try:
                self.log(tr("开始缺失查找（阈值 {0:.2f}，网络搜索{1}）", thr, tr("开") if use_web else tr("关")))
                results, error = find_missing_items(dist, thr, use_web, self.log)
                self.bridge.missing_done.emit(results, error, use_web)
            except Exception as e:
                self.log(tr("❌ 查找失败：{0}", e))
                self.bridge.missing_done.emit(None, str(e), use_web)

        threading.Thread(target=work, daemon=True).start()

    def _on_missing_done(self, results, error, use_web):
        self.btn_find.setEnabled(True)
        self.btn_find.setText(tr("开始查找"))
        if error:
            self.miss_status.setText(tr(error))
            return
        if not results:
            self.miss_status.setText(tr("没有缺失的商品。"))
            return
        t = self.miss_table
        t.setRowCount(len(results))
        found = web_found = 0
        for i, (ean, art, count, matches, web_source, web_art, st) in enumerate(results):
            if not matches:
                table_set_row(t, i, [ean, art, count or "", "", "", web_art or "", st or "", tr("没有找到")],
                              color=T("muted"))
            else:
                bt, be, ba, bs, bas, bwa = matches[0]
                found += 1
                web_info = bwa or web_art or ""
                if web_source:
                    web_found += 1
                table_set_row(t, i, [ean, art, count or "", f"{bt:.0%}", f"{bas:.0%}", web_info, st or "",
                                     f"{ba}　EAN {be}　{bs}"])
                t.item(i, 7).setToolTip(tr("EAN: {0}\nARTICOLO: {1}\n供应商: {2}", be, ba, bs) +
                                        (tr("\n来源: {0}", web_source) if web_source and "http" in str(web_source) else ""))
        msg = tr("{0} 个缺失商品，本地找到可能的替代品 {1} 个", len(results), found)
        if use_web and web_found:
            msg += tr("，网络找到 {0} 个", web_found)
        self.miss_status.setText(msg + "。")
        self.log(tr("✔ 缺失查找完成：{0}", msg))
        self._filter_missing(self.miss_filter.text())
        self.go_page("search")

    def _filter_missing(self, text):
        text = text.strip().lower()
        t = self.miss_table
        for r in range(t.rowCount()):
            row_text = " ".join((t.item(r, c).text() if t.item(r, c) else "") for c in (0, 1, 7)).lower()
            t.setRowHidden(r, bool(text) and text not in row_text)

    def run_web(self):
        from matcher import search_web_ean
        rows = self.miss_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, tr("网络搜索"), tr("先在表格里选一行。"))
            return
        r = rows[0].row()
        ean, art = self.miss_table.item(r, 0).text(), self.miss_table.item(r, 1).text()
        self.btn_web.setEnabled(False)
        self.btn_web.setText(tr("搜索中…"))

        def work():
            try:
                self.log(tr("网络搜索 EAN {0}：{1}", ean, art))
                res = search_web_ean(ean, art, self.log)
            except Exception as e:
                self.log(tr("❌ 搜索失败：{0}", e))
                res = (None, str(e), None)
            self.bridge.web_done.emit((ean, res))

        threading.Thread(target=work, daemon=True).start()

    def _on_web_done(self, payload):
        ean, (result, source, st) = payload
        self.btn_web.setEnabled(True)
        self.btn_web.setText(tr("网络搜索选中行"))
        if result:
            QMessageBox.information(self, tr("网络搜索"), tr("EAN {0}\n\n{1}\n\n来源：{2}", ean, result, source))
        else:
            QMessageBox.information(self, tr("网络搜索"), tr("没有找到 EAN {0} 的信息。{1}", ean, source or ''))

    def export_missing(self):
        t = self.miss_table
        if t.rowCount() == 0:
            QMessageBox.information(self, tr("导出"), tr("还没有查找结果。"))
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("导出缺失查找结果"),
                                              tr("缺失查找结果_{0:%Y%m%d_%H%M%S}.xlsx", datetime.now()), tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = tr("缺失查找结果")
            ws.append([t.horizontalHeaderItem(c).text() for c in range(t.columnCount())])
            for r in range(t.rowCount()):
                ws.append([tr(t.item(r, c).text()) if t.item(r, c) else "" for c in range(t.columnCount())])
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col if c.value), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(width + 2, 60)
            wb.save(path)
            self.log(tr("✔ 已导出：{0}", path))
            if QMessageBox.question(self, tr("导出"), tr("已导出到：\n{0}\n\n现在打开？", path)) == QMessageBox.Yes:
                open_path(path)
        except Exception as e:
            QMessageBox.critical(self, tr("导出"), tr("导出失败：{0}", e))

    def closeEvent(self, e):
        if self.running and QMessageBox.question(
                self, APP_NAME, tr("还在处理中，现在关闭可能导致文件没有保存完整。确定关闭？")) != QMessageBox.Yes:
            e.ignore()
            return
        e.accept()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.move(self.width() - self.toast.width() - 24, self.height() - self.toast.height() - 76)


def _cleanup_old_temp():
    """
    旧版是单文件 exe，每次运行都解压到 Temp 下的 _MEIxxxx，偶尔删不掉会留在那里。
    现在改成文件夹版不再产生这些文件夹；启动时顺手把以前留下的清掉
    （只删本程序的：里面有 PySide6 和 scipy，且一小时以前创建；正在用的删不掉会自动跳过）。
    """
    import shutil
    import tempfile
    try:
        tmp = tempfile.gettempdir()
        own = os.path.normcase(os.path.abspath(getattr(sys, "_MEIPASS", "") or "?"))
        for name in os.listdir(tmp):
            p = os.path.join(tmp, name)
            if not name.startswith("_MEI") or not os.path.isdir(p) or os.path.normcase(p) == own:
                continue
            if not (os.path.isdir(os.path.join(p, "PySide6")) and os.path.isdir(os.path.join(p, "scipy"))):
                continue
            if time.time() - os.path.getmtime(p) < 3600:
                continue
            shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


for _n in ("Panel", "Kpi", "Toggle", "make_table", "table_set_row", "T"):
    setattr(_UI, _n, staticmethod(globals()[_n]))


class WheelGuard(QObject):
    """
    鼠标滚轮防误触（全程序）：
      · 数字框 / 下拉框：没点过（没有焦点）时滚轮不改数值，交给外面的页面滚动。
      · 表格 / 列表 / 文本框：只有点过它，或者鼠标在它上面停了一会儿（HOVER_MS）没有滚页面，
        滚轮才滚它自己；否则滚整个页面，避免滑页面时卡进表格里。
    """
    HOVER_MS = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        import time
        self._now = lambda: time.monotonic() * 1000
        self.hover_view = None      # 鼠标当前所在的表格
        self.hover_since = 0.0      # 鼠标停到这个表格上的时间
        self.clicked_view = None    # 点过的表格（鼠标离开后失效）
        self.last_page_wheel = -1e9 # 上一次把滚轮交给页面的时间

    @staticmethod
    def _view_of(obj):
        """obj 是表格 / 列表 / 文本框（或它们的 viewport）→ 返回那个控件，否则 None"""
        from PySide6.QtWidgets import QAbstractItemView, QTextEdit, QPlainTextEdit
        kinds = (QAbstractItemView, QTextEdit, QPlainTextEdit)
        w = obj if isinstance(obj, kinds) else None
        if w is None:
            par = obj.parentWidget() if hasattr(obj, "parentWidget") else None
            if isinstance(par, kinds) and getattr(par, "viewport", lambda: None)() is obj:
                w = par
        if w is None:
            return None
        if w.window().windowType() == Qt.Popup:      # 下拉框弹出的列表照常滚
            return None
        return w

    @staticmethod
    def _outer_scroll(w):
        from PySide6.QtWidgets import QAbstractScrollArea
        p = w.parentWidget()
        while p is not None and not isinstance(p, QAbstractScrollArea):
            p = p.parentWidget()
        return p

    def _to_page(self, w, ev):
        outer = self._outer_scroll(w)
        if outer is None:
            return False                              # 外面没有可滚的页面：照常处理
        self.last_page_wheel = self._now()
        QApplication.sendEvent(outer.viewport(), ev)
        return True

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        t = ev.type()
        if t == QEvent.Polish:
            if isinstance(obj, (QAbstractSpinBox, QComboBox)):
                obj.setFocusPolicy(Qt.StrongFocus)   # 默认 WheelFocus：滚一下就拿到焦点，这里去掉
            return False
        if t == QEvent.Wheel:
            if isinstance(obj, (QAbstractSpinBox, QComboBox)):
                if obj.hasFocus():
                    return False
                self._to_page(obj, ev)
                return True
            v = self._view_of(obj)
            if v is None:
                return False
            now = self._now()
            if v is not self.hover_view:              # 页面滚动时表格「滑到」鼠标下面：从现在开始计时
                self.hover_view, self.hover_since = v, now
            armed = (self.clicked_view is v) or \
                (now - self.hover_since >= self.HOVER_MS and now - self.last_page_wheel >= self.HOVER_MS)
            if armed:
                return False
            return self._to_page(v, ev)
        if t in (QEvent.Enter, QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.Leave):
            v = self._view_of(obj)
            if v is None:
                return False
            if t == QEvent.Leave:
                if self.hover_view is v:
                    self.hover_view = None
                if self.clicked_view is v:
                    self.clicked_view = None
            elif t == QEvent.MouseButtonPress:
                self.clicked_view = v
                self.hover_view = v
            elif v is not self.hover_view:
                self.hover_view, self.hover_since = v, self._now()
        return False


def main():
    if getattr(sys, "frozen", False):
        threading.Thread(target=_cleanup_old_temp, daemon=True).start()
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("procurement.system.v25")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # Qt 自带按钮（是 / 否 / 取消 等）的翻译
    try:
        from PySide6.QtCore import QTranslator, QLibraryInfo
        qt_code = {"zh": "zh_CN", "en": "en", "it": "it"}[get_language()]
        app._qt_tr = QTranslator()
        if app._qt_tr.load("qtbase_" + qt_code, QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(app._qt_tr)
    except Exception:
        pass
    app.setStyle("Fusion")
    app._wheel_guard = WheelGuard(app)
    app.installEventFilter(app._wheel_guard)
    from erp_widgets import CopyHelper
    app._copy_helper = CopyHelper(app)          # 程序里的文字都可以选中复制，表格 Ctrl+C / 右键复制
    app.installEventFilter(app._copy_helper)
    f = QFont(UI_FONT, 10)
    app.setFont(f)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
