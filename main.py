"""
CASA&TE采购系统 v3.0 — 主程序（PySide6 / Qt）
采购是其中一个模块（门店配货、批发商比价 …）；销售与库存模块第二期从旧程序移植。
处理逻辑在 service.py / store_orders.py / reorder_service.py / forecast.py …，本文件只负责界面外壳。
"""
import os
import re
import sys
import time
_BOOT_T0 = time.perf_counter()        # 启动用时从这里算（2026-11-06）
import copy
import logging
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QRectF, QSize, QByteArray, QPointF, QPoint, QEvent


def _skip_pyside_source_scan():
    """
    PySide6 每读进一个 Python 文件，都会把它的源代码读一遍、看有没有用 PySide6（为了 __feature__ 功能，本程序没用）。
    从源代码运行时 numpy / pandas / openpyxl 有六百多个文件要读，Windows 上（加上杀毒软件）要好几秒，
    而且这期间别的线程读代码都要排队 —— 2026-11-06 hang.log：打开程序时后台读 SCHEDA 加载 openpyxl，界面线程等了 6 秒。
    改成只检查本程序文件夹里的文件，别的库当作没用 PySide6（结果一样：它们本来就不用 __feature__）。打包成 exe 时这步本来就很快
    """
    try:
        import shibokensupport.feature as _F
        orig = _F._mod_uses_pyside
        here = os.path.dirname(os.path.abspath(__file__)).lower()

        def mine_only(module):
            f = str(getattr(module, "__file__", None) or "").lower()
            return orig(module) if f.startswith(here) else False
        _F._mod_uses_pyside = mine_only
    except Exception:
        pass


_skip_pyside_source_scan()
from PySide6.QtGui import (QColor, QPainter, QPen, QBrush, QFont, QIcon, QPixmap, QFontDatabase,
                           QTextCursor, QPainterPath, QKeySequence, QShortcut, QGuiApplication)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractSpinBox, QToolButton, 
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QLineEdit, QCheckBox,
    QHBoxLayout, QVBoxLayout, QGridLayout, QStackedWidget, QButtonGroup, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog, QMessageBox,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QRadioButton, QComboBox, QProgressBar,
    QStyledItemDelegate, QSizePolicy, QScrollArea, QGraphicsDropShadowEffect, QStyle, QInputDialog,
)

def run_process(*a, **k):
    """比价（service 要加载 openpyxl 和 Excel 自动化，打开程序时先不加载，第一次比价时才加载）"""
    from service import run_process as _run
    return _run(*a, **k)


from order_check import OrderCheck
from store_order_page import StoreOrderPage
import history
def find_missing_items(*a, **k):
    """缺失查找（matcher 要加载 openpyxl：用到时才加载）"""
    from matcher import find_missing_items as _f
    return _f(*a, **k)


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
if not settings.get("ui_light_v3"):         # 2026-11-19 用户：以后以浅色为主（可以切深色），每台电脑只换一次
    settings["theme"] = "light"
    settings["theme_follow"] = False
    settings["ui_dark_v2"] = True
    settings["ui_light_v3"] = True
    try:
        save_settings(settings)
    except Exception:
        pass
theme.set_theme(settings.get("theme", "dark"))
theme.set_accent(settings.get("accent") or "blue")
theme.set_density(settings.get("density") or "normal")


def _scale_row_heights():
    """表格密度：所有表格的默认行高按「紧凑 / 标准 / 舒适」放大缩小（原来的行高记在表头上，换密度时重新算）"""
    from PySide6.QtWidgets import QHeaderView
    if getattr(QHeaderView, "_casate_scaled", False):
        return
    orig = QHeaderView.setDefaultSectionSize

    def scaled(self, n):
        if self.orientation() == Qt.Vertical:
            self.setProperty("base_row", int(n))
            n = theme.row_px(n)
        orig(self, n)
    QHeaderView.setDefaultSectionSize = scaled
    QHeaderView._casate_orig_dss = orig
    QHeaderView._casate_scaled = True


def apply_density_now():
    """换了密度：已经打开的表格马上按新行高"""
    from PySide6.QtWidgets import QHeaderView
    orig = getattr(QHeaderView, "_casate_orig_dss", None)
    if orig is None:
        return
    for w in QApplication.allWidgets():
        if isinstance(w, QHeaderView) and w.orientation() == Qt.Vertical and w.property("base_row"):
            orig(w, theme.row_px(int(w.property("base_row"))))


_scale_row_heights()


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

    def copy_text(self):
        """右键「复制」：每个起订供应商的状态和数量（画出来的文字没法用鼠标选）"""
        out = []
        for ru in self.rules:
            out.append(f"{ru.get('name')}\t{tr(ru.get('status', ''))}")
            if ru.get("min_colli"):
                out.append(tr("  箱数\t{0:,.0f} → {1:,.0f} / {2:,.0f}", ru.get("before_colli") or 0,
                              ru.get("after_colli") or 0, ru["min_colli"]))
            if ru.get("min_amount"):
                out.append(tr("  金额\t€{0:,.2f} → €{1:,.2f} / €{2:,.2f}", ru.get("before_amount") or 0,
                              ru.get("after_amount") or 0, ru["min_amount"]))
        return "\n".join(out)

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
        p.drawLine(opt.rect.topRight(), opt.rect.bottomRight())        # 列之间的细竖线（和样式表里的一样）
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


def _friendly_error(e):
    """出错信息说人话：文件被 Excel / WPS 开着（Windows 上写不进去）最常见"""
    if isinstance(e, PermissionError):
        name = os.path.basename(getattr(e, "filename", "") or "") or str(e)
        return tr("「{0}」正在被 Excel / WPS 打开（或被别的程序占用），写不进去。关掉这个文件后再点「重新比价」。", name)
    return str(e)


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
    """
    errors：不能开始的问题；need_order：还没有能用的主订单（要先在 ② 检查门店清单生成），也不能开始，
    但这是每次比价的正常第一步，界面上不当成错误显示。
    """
    info = {"ok": False, "errors": [], "warnings": [], "ordine": None, "ordine_old": [], "need_order": None,
            "eliminare": [], "suppliers": [], "temp": []}
    if not root_path or not os.path.isdir(root_path):
        info["errors"].append(tr("还没有门店比价文件夹：点「开始」检查门店清单，确认后自动建"))
        return info
    import reorder_service as RS
    good, old = RS.checked_main_orders(os.path.join(root_path, ORDINE_FOLDER))
    elim = _excel_files(os.path.join(root_path, ELIMINARE_FOLDER))
    dist = _excel_files(os.path.join(root_path, DISTRIBUTUIRE_FOLDER))
    info["ordine_old"] = old
    if good:
        info["ordine"] = good[0]
        if len(good) > 1:
            info["warnings"].append(tr("{0} 里有 {1} 个主订单，用最新的：{2}", ORDINE_FOLDER, len(good), good[0]))
    elif old:
        info["need_order"] = tr("ORDINE 里的「{0}」不是检查门店清单生成的（旧系统的文件），不能再直接比价。请把门店清单放进「门店清单」，点「开始」。",
                                old[0])
    else:
        info["need_order"] = tr("还没有主订单：把门店清单放进「门店清单」，点「开始」。")
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
    info["ok"] = not info["errors"] and not info["need_order"]
    return info


class SupplierDropDialog(QDialog):
    """
    拖进来的供应商报价：每个文件选好是哪家供应商（2026-11-07 用户：程序常常认不出，放进去的时候就选好）。
    认得出的已经选好；同名的旧报价自动替换；名称和起订规则的供应商名称一样，起订规则就一定对得上。
    """

    def __init__(self, parent, plan, existing):
        super().__init__(parent)
        import supplier_names as SN
        self.setWindowTitle(tr("放进供应商报价"))
        self.plan = plan
        self.labels = SN.labels(existing, settings)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        t = QLabel(tr("每个文件是哪家供应商？") + " ⓘ")
        t.setStyleSheet("font-weight:600;")
        t.setToolTip(tr("选好供应商以后，同一家的旧报价自动挪进 _旧文件（不删除）；名称和起订规则里的供应商名称一样，"
                        "起订规则、方案排除就一定认得出。下拉框里没有的可以直接输入新名字。"))
        lay.addWidget(t)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        for c, h in enumerate((tr("文件"), tr("供应商"), "")):
            hl = QLabel(h)
            hl.setObjectName("Faint")
            g.addWidget(hl, 0, c)
        opts = SN.choices(settings, existing)
        self.combos, self.notes = [], []
        for i, p in enumerate(plan, 1):
            name = os.path.basename(p["src"])
            l = QLabel(name)
            l.setToolTip(p["src"])
            g.addWidget(l, i, 0)
            cb = QComboBox()
            cb.setEditable(True)
            cb.setInsertPolicy(QComboBox.NoInsert)
            cb.addItems(opts)
            rep = p.get("replace")
            pre = (self.labels.get(rep) if rep and SN.recognized(rep, settings) else
                   SN.label_of(name, settings) if SN.recognized(name, settings) else "")
            cb.setCurrentText(pre or "")
            cb.lineEdit().setPlaceholderText(tr("选择或输入供应商"))
            cb.setMinimumWidth(220)
            cb.currentTextChanged.connect(self._update)
            g.addWidget(cb, i, 1)
            note = QLabel("")
            note.setMinimumWidth(200)
            g.addWidget(note, i, 2)
            self.combos.append(cb)
            self.notes.append(note)
        g.setColumnStretch(0, 1)
        lay.addLayout(g)
        self.err = QLabel("")
        self.err.setStyleSheet(f"color:{T('err')};")
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setText(tr("放进去"))
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumWidth(720)
        from erp_widgets import fit_to_screen
        fit_to_screen(self)
        self._update()

    def _targets(self):
        """每一行：(供应商名称, 要替换的旧文件或 None)"""
        import supplier_names as SN
        out, taken = [], set()
        for p, cb in zip(self.plan, self.combos):
            nm = cb.currentText().strip()
            same = os.path.basename(p["src"])
            rep = same if same in self.labels else SN.file_for(nm, self.labels, taken)
            old = p.get("replace")
            if not rep and nm and old and old not in taken and not SN.recognized(old, settings):
                rep = old              # 按文件名认出要替换的旧文件，它自己还没有名字 → 照旧替换它（名字记到新文件上）
            if rep:
                taken.add(rep)
            out.append((nm, rep))
        return out

    def _update(self, *_):
        for (nm, rep), note in zip(self._targets(), self.notes):
            if not nm:
                note.setText(tr("⚠ 请选择供应商"))
                note.setStyleSheet(f"color:{T('warn')};")
            elif rep:
                note.setText(tr("替换 {0}", rep))
                note.setToolTip(tr("{0} 的旧报价挪进 _旧文件", nm))
                note.setStyleSheet(f"color:{T('muted')};")
            else:
                note.setText(tr("新增一家"))
                note.setStyleSheet(f"color:{T('accent')};")
        missing = sum(1 for nm, _ in self._targets() if not nm)
        self.ok.setEnabled(not missing)

    def result_plan(self):
        return [dict(p, replace=rep) for p, (nm, rep) in zip(self.plan, self._targets())]

    def picked(self):
        """{新文件名: 选的供应商名称}"""
        return {os.path.basename(p["src"]): nm for p, (nm, _) in zip(self.plan, self._targets())}

    def _ok(self):
        names = [nm.casefold() for nm, _ in self._targets()]
        if len(names) != len(set(names)):
            self.err.setText(tr("两个文件不能是同一家供应商，请改一下。"))
            return
        self.accept()


def describe_condition(r):
    from min_order_optimizer import effective_limits
    c, a, mode = effective_limits(r.get("min_colli"), r.get("min_amount"), r.get("mode", "all"))
    items = []
    if c:
        items.append(tr("{0:g} 箱", c))
    if a:
        items.append(f"€{a:g}")
    if not items:
        return tr("无条件")
    return (tr(" 且 ") if mode == "all" else tr(" 或 ")).join(items)


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
    price_check_done = Signal(object)


# =====================================================================
# 起订规则编辑
# =====================================================================
class RuleDialog(QDialog):
    def __init__(self, parent, rule, supplier_files, compact=False):
        super().__init__(parent)
        self.setWindowTitle(tr("起订规则"))
        if not compact:
            self.setMinimumWidth(500)
        self.result_rule = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*((20, 16, 20, 8) if compact else (24, 22, 24, 18)))
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

        from erp_widgets import tip_label
        g.addWidget(QLabel(tr("供应商名称")), 0, 0)
        g.addWidget(self.name, 0, 1)
        g.addWidget(tip_label(tr("文件名关键字"), tr("文件名里有这几个字，就算这家。比如 mega。")), 1, 0)
        g.addWidget(self.kw, 1, 1)
        g.addWidget(tip_label(tr("起订箱数"), tr("填 0 就是不限。金额 = 净价 × 箱数。")), 3, 0)
        g.addWidget(self.colli, 3, 1)
        g.addWidget(tip_label(tr("起订金额"), tr("填 0 就是不限。金额 = 净价 × 箱数。")), 4, 0)
        g.addWidget(self.amount, 4, 1)
        lay.addLayout(g)

        # 条件：达到起订箱数 / 达到起订金额 / 都要达到
        mode_box = QHBoxLayout()
        self.m_colli = QRadioButton(tr("箱数") if compact else tr("达到起订箱数"))
        self.m_amount = QRadioButton(tr("金额") if compact else tr("达到起订金额"))
        self.m_all = QRadioButton(tr("都要达到"))
        self.mode_group = QButtonGroup(self)
        for b in (self.m_colli, self.m_amount, self.m_all):
            self.mode_group.addButton(b)
        old_mode = rule.get("mode", "all")
        {"colli": self.m_colli, "amount": self.m_amount, "all": self.m_all}.get(old_mode, self.m_all).setChecked(True)
        mode_box.addWidget(QLabel(tr("起订按") if compact else tr("条件")))
        mode_box.addSpacing(12 if compact else 30)
        mode_box.addWidget(self.m_colli)
        mode_box.addWidget(self.m_amount)
        mode_box.addWidget(self.m_all)
        mode_box.addStretch()
        lay.addLayout(mode_box)
        if old_mode == "any":
            lay.addWidget(hint(tr("这条规则以前是「达到其中一个即可」，现在没有这个选项了，请重新选条件再保存。")))

        def _mode_changed(*_):
            # 只看箱数时金额不起作用（灰掉），只看金额时箱数不起作用
            self.colli.setEnabled(not self.m_amount.isChecked())
            self.amount.setEnabled(not self.m_colli.isChecked())
        self.mode_group.buttonToggled.connect(_mode_changed)
        _mode_changed()

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
        lim.addWidget(tip_label(tr("手动调整线"), tr("多花超过这个数，打开手动调整。") + tr("填 0 就是不用这条线。")), 0, 0)
        lim.addWidget(self.manual_extra, 0, 1)
        lim.addWidget(tip_label(tr("放弃供应商线"), tr("多花超过这个数，问你要不要放弃这家。") + tr("填 0 就是不用这条线。")), 1, 0)
        lim.addWidget(self.drop_extra, 1, 1)
        lim.setColumnStretch(2, 1)
        lay.addLayout(lim)
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
        mode = "colli" if self.m_colli.isChecked() else ("amount" if self.m_amount.isChecked() else "all")
        if mode == "colli" and colli == 0:
            QMessageBox.warning(self, tr("起订规则"), tr("选了「达到起订箱数」，请填起订箱数。"))
            return
        if mode == "amount" and amount == 0:
            QMessageBox.warning(self, tr("起订规则"), tr("选了「达到起订金额」，请填起订金额。"))
            return
        if colli == 0 and amount == 0:
            if QMessageBox.question(self, tr("起订规则"), tr("箱数和金额都是 0，这条规则不会起作用。仍然保存？")) != QMessageBox.Yes:
                return
        m, d = self.manual_extra.value(), self.drop_extra.value()
        if m and d and m >= d:
            QMessageBox.warning(self, tr("起订规则"), tr("手动调整线应该比放弃供应商线低，例如手动 €5、放弃 €30。"))
            return
        self.result_rule = {"enabled": self.enabled.isChecked(), "name": self.name.text().strip() or kw,
                            "keyword": kw, "min_colli": colli, "min_amount": amount,
                            "mode": mode,
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
        import store_orders as SO
        import scenarios as _SC
        items = sorted(items, key=lambda it: (not _SC.urgent(it), it.get("articolo") or ""))   # 不够卖一周的排前面
        lines = [tr("{0}　{1}　{2:g} 箱", it['ean'], it['articolo'][:40], it['qty'])
                 + (tr("　店里够卖 {0} 天", SO.days_text(SO.cover_days(it))) if it.get("daily") is not None else "")
                 + ("　⚠" if _SC.urgent(it) else "") for it in items[:15]]
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
        i = idx.data(Qt.UserRole)
        if i is None:                     # 这一行正在重画
            return
        r = self.dlg.rows[i]
        cur = self.dlg.assign.get(r["row"])
        i = cb.findData(cur)
        if i >= 0:
            cb.setCurrentIndex(i)
        cb.showPopup()

    def setModelData(self, cb, model, idx):
        # 等编辑框关掉以后再改（编辑时重画这一行会让程序卡住闪退）
        i, s = idx.data(Qt.UserRole), cb.currentData()
        if i is not None:
            QTimer.singleShot(0, lambda: self.dlg.set_supplier(i, s))


class QtyDelegate(QStyledItemDelegate):
    """修改分配表格里「箱数」列：点击后输入新数量"""

    def __init__(self, dlg):
        super().__init__(dlg.table)
        self.dlg = dlg

    def createEditor(self, parent, opt, idx):
        sb = QDoubleSpinBox(parent)
        sb.setDecimals(2)
        sb.setRange(0, 999999)
        sb.setButtonSymbols(QAbstractSpinBox.NoButtons)
        sb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return sb

    def setEditorData(self, sb, idx):
        i = idx.data(Qt.UserRole)
        if i is None:                     # 这一行正在重画
            return
        sb.setValue(self.dlg._qty(self.dlg.rows[i]))
        sb.selectAll()

    def setModelData(self, sb, model, idx):
        # 等编辑框关掉以后再改（编辑时重画这一行会让程序卡住闪退）
        sb.interpretText()
        i, q = idx.data(Qt.UserRole), sb.value()
        if i is not None:
            QTimer.singleShot(0, lambda: self.dlg.set_qty(i, q))


class _CandSig(QObject):
    loaded = Signal(object)


class FillItemsDialog(QDialog):
    """
    修改分配 →「加商品凑单」（和门店配货的「加商品凑单」同一个做法）：
    供应商没达到起订条件时，不换供应商，从这家的报价里挑商品多订——已经在订单里的可以多订几箱，
    这次没订的（库存还够、但卖得快的快消品）也可以加进来。表格显示门店的日均、库存、够卖天数；
    「加（箱）」可以自己填，也可以「按销量推荐补齐」。确认后 self.result = {"rows": {行号: 加几箱}, "new": [(商品, 加几箱)]}
    """
    COLS = [tr("名称"), "EAN", tr("在订单里"), tr("日均"), tr("本店库存"), tr("库存够卖（天）"), tr("已订（箱）"),
            tr("每箱件数"), tr("一箱 €"), tr("加（箱）") + " ✎", tr("订后够卖（天）"), tr("加的金额 €")]
    ADD = 9
    WIDTHS = (300, 130, 76, 60, 70, 100, 76, 70, 70, 76, 100, 90)

    def __init__(self, md, rules, target=None):
        super().__init__(md)
        self.md, self.rules, self.result = md, rules, None
        self.items, self.adds = [], {}            # adds: {key: 加几箱}
        self.limit = float((settings.get("store_orders") or {}).get("fill", {}).get("max_stock_days") or 60)
        self._filling = False
        self.setWindowTitle(tr("加商品凑单"))
        self.resize(1320, 760)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(QLabel(tr("供应商")))
        self.sup = QComboBox()
        self.sup.setMinimumWidth(320)
        for ru in rules:
            self.sup.addItem(f"{ru['name']}　（{describe_condition(ru)}）", ru["target"])
        if target:
            self.sup.setCurrentIndex(max(0, self.sup.findData(target)))
        top.addWidget(self.sup)
        top.addSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("条码或名称"))
        self.search.setMaximumWidth(220)
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
        # 提示就显示在这个窗口里
        nb = QHBoxLayout()
        nb.setSpacing(10)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note_btn = QPushButton("")
        self.note_btn.setObjectName("Outline")
        self.note_btn.clicked.connect(lambda: self._note_cb and self._note_cb())
        self._note_cb = None
        nb.addWidget(self.note, 1)
        nb.addWidget(self.note_btn)
        self.note_box = QFrame()
        self.note_box.setLayout(nb)
        self.note_box.hide()
        lay.addWidget(self.note_box)
        self.table = make_table(self.COLS, stretch_col=None, numeric_cols=tuple(range(3, 12)))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, wd in enumerate(self.WIDTHS):
            self.table.setColumnWidth(c, wd)
        import product_images as PI
        PI.add_thumbs(self.table, 0, 1, settings)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.horizontalHeaderItem(5).setToolTip(tr("库存够卖 = 本店库存 ÷ 日均（账面库存是负数时按 0 算）"))
        self.table.horizontalHeaderItem(10).setToolTip(tr("订后够卖 =（本店库存 + 已订和加的箱数 × 每箱件数）÷ 日均"))
        self.table.horizontalHeaderItem(self.ADD).setToolTip(tr("点 − / ＋ 改 1 箱，双击直接输入，0 = 不加"))
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked |
                                   QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.itemChanged.connect(self._edited)
        from erp_widgets import StepperDelegate     # 加几箱：− / ＋ 点一下改 1 箱
        StepperDelegate.install(self.table, self.ADD)
        lay.addWidget(self.table, 1)
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(self.table, lambda: len(self.items), self._text, self._fill, key=self._key,
                                   hide=self._hidden, numeric=tuple(range(3, 12)))
        self.search.textChanged.connect(lambda _: self.ctl.render())
        self.only_sell.toggled.connect(lambda _: self.ctl.render())
        self.only_new.toggled.connect(lambda _: self.ctl.render())
        btns = QHBoxLayout()
        b_sug = QPushButton(tr("按销量推荐补齐起订量"))
        b_sug.setToolTip(tr("先列已经在订单里的，再列报价里这次没订的商品；按日均销量从高到低排。「按销量推荐」只加加完后库存够卖不超过 {0} 天的商品（配货设置里的凑单天数）。新加的商品只在这家下单，会写进主订单和这家的订单。",
                            int(self.limit)))
        b_sug.setObjectName("Outline")
        b_sug.clicked.connect(lambda: self.suggest(False))
        b_clear = QPushButton(tr("全部清零"))
        b_clear.clicked.connect(self.clear)
        btns.addWidget(b_sug)
        btns.addWidget(b_clear)
        btns.addStretch()
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(tr("加进订单"))
        self.ok.setObjectName("Primary")
        self.ok.clicked.connect(self._ok)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)
        self.sup.currentIndexChanged.connect(lambda _: self.load())
        self.load()

    # ---------- 数据 ----------
    def rule(self):
        t = self.sup.currentData()
        return next((ru for ru in self.rules if ru["target"] == t), None)

    def load(self):
        """这家已经在订单里的商品马上列出来；报价里没订的商品要读报价、查数据库，在后台载入后补上"""
        from allocation_plan import _ordered_pieces, _box_pieces, _amt, qty_step
        ru = self.rule()
        if ru is None:
            return
        t = ru["target"]
        md = self.md
        self.adds = {}
        items = []
        for r in md.rows:
            if md.assign.get(r["row"]) != t or md._qty(r) <= 0:
                continue
            pk = _box_pieces(r, t)
            items.append({"key": ("row", r["row"]), "row": r["row"], "name": r.get("articolo", ""), "ean": r.get("ean", ""),
                          "in_order": True, "daily": r.get("daily"), "inv": r.get("inv"),
                          "have": _ordered_pieces(r, t), "ordered": _ordered_pieces(r, t) / pk if pk else 0,
                          "pack": pk, "box": (_amt(r, t) or 0) * qty_step(r, t)})
        self.items = items
        self.ctl.set_sort(3, True) if items else self.ctl.render()
        self._refresh_state()
        cached = md.cand_cache.get(t)
        if cached is not None:
            self._add_new(t, cached)
        elif md.ctx:
            self._show_note(tr("正在读取 {0} 的报价、查门店销量和库存，报价里这次没订的商品稍后列出来…", ru["name"]))
            md.load_candidates(t, lambda res, t=t: self._loaded(t, res))
        else:
            self._show_note(tr("这次结果没有存报价文件的位置，只能在订单里已有的商品上加量。"))

    def _loaded(self, t, res):
        try:
            if not self.isVisible() or self.sup.currentData() != t:
                return
        except RuntimeError:                  # 窗口已经关了
            return
        if isinstance(res, Exception):
            self._show_note(tr("报价里没订的商品载入失败（只能在订单里已有的商品上加量）：{0}", res))
            return
        self._hide_note()
        self._add_new(t, res)

    def _add_new(self, t, cands):
        have = {x["ean"] for x in self.items}
        for c in cands:
            if c["ean"] in have:
                continue
            pk = float(c.get("pack") or 1)
            self.items.append({"key": ("ean", c["ean"]), "cand": c, "name": c.get("name") or c.get("desc") or "",
                               "ean": c["ean"], "in_order": False, "daily": c.get("daily"), "inv": c.get("inv"),
                               "have": 0.0, "ordered": 0, "pack": pk, "box": float(c["price"]) * pk})
        self.ctl.set_sort(3, True)            # 日均从高到低
        self._refresh_state()

    # ---------- 表格 ----------
    def _cover(self, x, boxes=0):
        import store_orders as SO
        if x.get("daily") is None:
            return None
        return SO.cover_days({"inv": x.get("inv") or 0, "daily": x["daily"]}, (x["have"] or 0) + boxes * x["pack"])

    def _text(self, i, c):
        import store_orders as SO
        x = self.items[i]
        n = self.adds.get(x["key"], 0)
        daily = x.get("daily")
        vals = [x["name"], x["ean"], tr("是") if x["in_order"] else "",
                f"{daily:.2f}" if daily else ("0" if daily is not None else ""),
                f"{x['inv']:g}" if isinstance(x.get("inv"), (int, float)) else "",
                SO.days_text(self._cover(x)) if daily is not None else "",
                f"{x['ordered']:g}" if x["ordered"] else "", f"{x['pack']:g}", f"{x['box']:,.2f}",
                f"{n:g}" if n else "",
                SO.days_text(self._cover(x, n)) if (daily is not None and (n or x["in_order"])) else "",
                f"{n * x['box']:,.2f}" if n else ""]
        return vals[c]

    def _key(self, i, c):
        x = self.items[i]
        if c == 3:
            return float(x.get("daily") or 0)
        if c in (5, 10):
            v = self._cover(x, self.adds.get(x["key"], 0) if c == 10 else 0)
            return 1e9 if v is None or v == float("inf") else v
        if c == self.ADD:
            return float(self.adds.get(x["key"], 0))
        return None

    def _hidden(self, i):
        x = self.items[i]
        if self.only_sell.isChecked() and not float(x.get("daily") or 0) > 0 and not self.adds.get(x["key"]):
            return True
        if self.only_new.isChecked() and x["in_order"]:
            return True
        q = self.search.text().strip().lower()
        return bool(q) and q not in f"{x['ean']} {x['name']}".lower()

    def _fill(self, i, k):
        x = self.items[i]
        table_set_row(self.table, k, [self._text(i, c) for c in range(len(self.COLS))])
        for c in range(len(self.COLS)):
            it = self.table.item(k, c)
            if c == self.ADD:
                it.setFlags(it.flags() | Qt.ItemIsEditable)
                it.setData(Qt.UserRole, i)
                it.setBackground(QColor(T("accent_soft")))
                if self.adds.get(x["key"]):
                    it.setForeground(QColor(T("accent")))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
            else:
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.item(k, 0).setToolTip(f"{x['name']}\nEAN {x['ean']}")
        n = self.adds.get(x["key"], 0)
        v = self._cover(x, n)
        if n and v is not None and v > self.limit:
            self.table.item(k, 10).setForeground(QColor(T("warn")))
            self.table.item(k, 10).setToolTip(tr("订完要很久才卖得完（超过 {0} 天），可能压货", int(self.limit)))

    def _edited(self, item):
        if self._filling or item.column() != self.ADD:
            return
        i = self.ctl.row_at(item.row())
        if i is None:
            return
        try:
            n = float(item.text().strip().replace(",", ".") or 0)
            if n < 0 or n != int(n):
                raise ValueError
            n = int(n)
        except ValueError:
            n = None
        key = self.items[i]["key"]

        def apply():
            if n is None:
                self._show_note(tr("箱数要填 0 或正整数"))
            elif n:
                self.adds[key] = n
            else:
                self.adds.pop(key, None)
            self.ctl.update_row(i)
            self._refresh_state()
        QTimer.singleShot(0, apply)            # 等编辑框关掉再更新这一行（编辑中途改格子会卡住）

    def _totals(self):
        """这家现在（加之前）的箱数、金额，和加完后的"""
        ru = self.rule()
        c0, a0 = self.md._sup_totals(ru["target"])
        by = {x["key"]: x for x in self.items}
        c = c0 + sum(self.adds.values())
        a = a0 + sum(n * by[k]["box"] for k, n in self.adds.items() if k in by)
        return c0, a0, c, a

    def _refresh_state(self):
        from min_order_optimizer import _satisfied
        ru = self.rule()
        if ru is None:
            return
        c0, a0, c, a = self._totals()
        ok = _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"])
        gaps = []
        if ru["min_colli"] and c < ru["min_colli"]:
            gaps.append(tr("差 {0:g} 箱", ru["min_colli"] - c))
        if ru["min_amount"] and a < ru["min_amount"]:
            gaps.append(tr("差 €{0:,.2f}", ru["min_amount"] - a))
        txt = tr("<b>{0}</b>　现在 {1:g} 箱 / €{2:,.2f}　起订量 {3}", ru["name"], c0, a0, describe_condition(ru))
        if self.adds:
            txt += "　→　" + tr("加上以后 {0:g} 箱 / €{1:,.2f}", c, a)
        txt += "　<b>" + (tr("已达标") if ok else tr("未达标") + "（" + tr("、").join(gaps) + "）") + "</b>"
        self.state.setText(txt)
        kind = "ok" if ok else "err"
        self.state.setStyleSheet(f"background:{T(kind + '_soft')}; color:{T(kind)};")
        n_new = sum(1 for k in self.adds if k[0] == "ean")
        self.ok.setEnabled(bool(self.adds))
        self.ok.setText(tr("加进订单（{0} 个商品，其中新商品 {1} 个）", len(self.adds), n_new) if self.adds else tr("加进订单"))

    def _show_note(self, text, button=None, cb=None):
        self.note.setText(text)
        self.note_btn.setVisible(bool(button))
        self.note_btn.setText(button or "")
        self._note_cb = cb
        self.note_box.show()

    def _hide_note(self):
        self.note_box.hide()

    # ---------- 操作 ----------
    def suggest(self, ignore_days=False):
        from allocation_plan import suggest_items
        ru = self.rule()
        if ru is None:
            return
        c0, a0, _, _ = self._totals()
        cands = [dict(x, key=x["key"]) for x in self.items if x.get("daily") is not None]
        res = suggest_items(cands, c0, a0, ru, self.limit, ignore_days)
        if not res["boxes"] and not res["ok"]:
            self._show_note(tr("加完库存都会超过 {0} 天（或者没有卖得动的商品），按销量推荐不出来。可以自己在「加（箱）」里填，或者不限天数推荐。", int(self.limit)),
                            tr("仍然按销量推荐"), lambda: self.suggest(True))
            return
        self.adds = dict(res["boxes"])
        self.ctl.render()
        self._refresh_state()
        if res["ok"]:
            self._show_note(tr("已推荐 {0} 个商品、{1:g} 箱（€{2:,.2f}），加上就达标。可以再改「加（箱）」。",
                               len(res["boxes"]), sum(res["boxes"].values()), res["value"]))
        elif not ignore_days:
            self._show_note(tr("加完库存最多够卖 {0} 天的话，只能加到 {1:g} 箱 / €{2:,.2f}，还是不够。", int(self.limit), res["colli"], res["amount"]),
                            tr("仍然按销量推荐"), lambda: self.suggest(True))
        else:
            self._show_note(tr("卖得动的商品全加上也不够起订量：{0:g} 箱 / €{1:,.2f}。", res["colli"], res["amount"]))

    def clear(self):
        self.adds = {}
        self._hide_note()
        self.ctl.render()
        self._refresh_state()

    def _ok(self):
        if not self.adds:
            return
        by = {x["key"]: x for x in self.items}
        self.result = {"target": self.rule()["target"],
                       "rows": {k[1]: n for k, n in self.adds.items() if k[0] == "row"},
                       "new": [(by[k]["cand"], n) for k, n in self.adds.items() if k[0] == "ean" and k in by]}
        self.accept()


class TransferSettingsDialog(QDialog):
    """批发商比价的门店间调货设置（存 settings["wholesale_transfer"]，2026-10-22 起和门店配货分开，默认不启用）"""

    def __init__(self, parent):
        super().__init__(parent)
        import store_orders as SO
        from store_order_page import TransferBox
        self.setWindowTitle(tr("门店间调货"))
        so = settings.setdefault("store_orders", {})
        far = (dict(SO.DEFAULT_FILL, **(so.get("fill") or {}))).get("far_stores") or []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        t = QLabel(tr("门店间调货") + "  ⓘ")
        t.setObjectName("Section")
        t.setToolTip(tr("门店之间调货：要的件数远小于一箱时，从别的门店调多出来的货，少订一箱"))
        lay.addWidget(t)
        import transfers as TR
        self.box = TransferBox(TR.wholesale_raw(settings), far)     # 批发商比价自己的调货设置（和门店配货分开）
        lay.addWidget(self.box)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumWidth(420)

    def save(self):
        import store_orders as SO
        so = settings.setdefault("store_orders", {})
        import transfers as TR
        settings["wholesale_transfer"] = self.box.transfer_values(TR.wholesale_raw(settings))
        save_settings(settings)
        self.accept()


class ManualDialog(QDialog):
    """手动调整每个商品的供应商。确认后返回 self.result（{行号: 供应商}）"""

    def __init__(self, parent, rows, rules, baseline, proposed, notes=None, edit_mode=False, ctx=None):
        super().__init__(parent)
        self.edit_mode = edit_mode
        # ctx（修改分配）：{"ordine_file", "distribuire_path", "headers"}，加商品凑单读报价、查门店库存用
        self.ctx = ctx
        self.cand_cache = {}                      # {供应商: 报价里这次没订的商品}
        self._cand_sig = _CandSig()
        self._cand_sig.loaded.connect(lambda p: p[0](p[1]))
        self._orig_rows = {r["row"] for r in rows}
        self.new_rows = []
        self.setWindowTitle(tr("修改分配") if edit_mode else tr("手动调整供应商"))
        self.resize(1180, 760)
        self.rows = [dict(r) for r in rows]      # 改数量时换成新的行，不动原来的
        self.qty_changed = {}                     # {行号: 新数量}（只在修改分配里能改）
        self.pieces_mode = any(r.get("pieces") is not None for r in rows)
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
        t.setToolTip(tr("点「当前供应商」那一格可以改成别的报价；点「{0}」那一格可以改数量（改成 0 = 这个商品不订）。上面的起订情况和采购总额会实时更新。确认后才会写入主订单和供应商文件。",
                        tr("件数") if self.pieces_mode else tr("箱数")) if edit_mode else
                     tr("点「当前供应商」那一格可以改成别的报价。上面的起订情况和采购总额会实时更新。确认后才会写入主订单和供应商文件。"))
        if notes:
            nb = Banner()
            nb.show_msg(tr("为什么打开这个窗口：{0}。程序已经给出最省钱的凑单方案，你可以按实际情况改，比如把不想换的商品改回原供应商。",
                           "; ".join(notes)), "warn")
            lay.addWidget(nb)

        # 起订情况 + 总额：每行最多 3 个，文字太长自动换行（供应商多的时候窗口不会被撑得比屏幕还宽）
        self.status_box = QGridLayout()
        self.status_box.setHorizontalSpacing(10)
        self.status_box.setVerticalSpacing(8)
        self.status_labels = []
        per_row = 3
        for k, ru in enumerate(rules):
            lb = QLabel()
            lb.setObjectName("Banner")
            lb.setTextFormat(Qt.RichText)
            lb.setWordWrap(True)
            self.status_labels.append((ru, lb))
            self.status_box.addWidget(lb, k // per_row, k % per_row)
        self.total_lbl = QLabel()
        self.total_lbl.setObjectName("Banner")
        self.total_lbl.setWordWrap(True)
        n = len(rules)
        if n % per_row:                               # 最后一行还有空位：总额放在后面
            self.status_box.addWidget(self.total_lbl, n // per_row, n % per_row, 1, per_row - n % per_row)
        else:
            self.status_box.addWidget(self.total_lbl, n // per_row, 0, 1, per_row)
        for c in range(per_row):
            self.status_box.setColumnStretch(c, 1)
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

        self.table = make_table([tr("行"), "EAN", "ARTICOLO", tr("件数") if self.pieces_mode else tr("箱数"),
                                 tr("最低价供应商"), tr("件价"),
                                 tr("当前供应商"), tr("当前件价"), tr("比最低价多花 €"),
                                 tr("库存够卖（天）"), tr("订后够卖（天）")],
                                stretch_col=2, numeric_cols=(0, 3, 5, 7, 8, 9, 10))
        self.has_stock = any(r.get("daily") is not None for r in self.rows)
        self.table.horizontalHeaderItem(9).setToolTip(tr("库存够卖 = 现在的库存按日均还能卖几天"))
        self.table.horizontalHeaderItem(10).setToolTip(tr("订后够卖 =（库存 + 这次从当前供应商订的件数）÷ 日均"))
        for c_ in (9, 10):
            self.table.setColumnHidden(c_, not self.has_stock)     # 旧主订单没有库存数据，不显示这两列
        self.table.setFocusPolicy(Qt.StrongFocus)
        # 只有「当前供应商」这一列能改（而且要有不止一家报价）；其他格子只能看、复制
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.setItemDelegateForColumn(6, SupplierComboDelegate(self))
        import product_images as PI
        PI.add_thumbs(self.table, 2, 1, settings)
        self.qty_stepper = None
        if edit_mode:                                # 箱数：− / ＋ 点一下改 1，点中间照旧输入
            from erp_widgets import StepperDelegate
            self.qty_stepper = StepperDelegate.install(
                self.table, 3, base=QtyDelegate(self), value=lambda idx: self._qty(self.rows[idx.data(Qt.UserRole)]),
                on_step=lambda idx, q: idx.data(Qt.UserRole) is not None and self.set_qty(idx.data(Qt.UserRole), q))
        lay.addWidget(self.table, 1)
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(self.table, lambda: len(self.rows), self._text, self._fill_row,
                                   hide=self._hidden, numeric=(0, 3, 8, 9, 10))
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
        if edit_mode and rules:
            b_fill = QPushButton(tr("加商品凑单"))
            b_fill.setToolTip(tr("没达到起订条件的供应商：不换供应商，从它的报价里挑商品多订（订单里已有的多订几箱，或者加这次没订、但卖得快的商品），和总仓采购的加商品凑单一样"))
            b_fill.clicked.connect(self.add_items)
            btns.addWidget(b_fill)
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
        d0, d1 = self._cover(r, c)
        return [str(r["row"]), r.get("ean", ""), r.get("articolo", ""), f"{q:g}",
                short(b) if b else tr("无报价"), fmt_unit(_unit(r, b), _pack(r, b)),
                short(c) if c else tr("无报价"), fmt_unit(_unit(r, c), _pack(r, c)),
                f"{extra:+,.2f}" if extra else "", d0, d1], n_offers, extra, b, c

    def _cover(self, r, c):
        """库存够卖 / 订后够卖（天）：主订单是「检查门店清单」生成的才有库存和日均"""
        if r.get("daily") is None:
            return "", ""
        import store_orders as SO
        d = {"inv": r.get("inv") or 0, "daily": r.get("daily") or 0}
        if c:
            pieces = ((r.get("colli") or {}).get(c, 0) * (_pack(r, c) or 1)) if r.get("pieces") is not None \
                else self._qty(r) * (_pack(r, c) or 1)
        else:
            pieces = 0
        return SO.days_text(SO.cover_days(d)), (SO.days_text(SO.cover_days(d, pieces)) if c else "")

    def _text(self, i, col):
        return self._vals(i)[0][col]

    def _fill_row(self, i, k):
        vals, n_offers, extra, b, c = self._vals(i)
        if n_offers > 1 and c:
            vals[6] += "　▾"
        table_set_row(self.table, k, vals, color=None)
        self.table.item(k, 2).setToolTip(f"{vals[2]}\nEAN {vals[1]}")
        for col in range(self.table.columnCount()):   # 先全部设成不能改
            it = self.table.item(k, col)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.item(k, 6).setData(Qt.UserRole, i)
        self.table.item(k, 3).setData(Qt.UserRole, i)
        r = self.rows[i]
        if r["row"] not in self._orig_rows:
            self.table.item(k, 2).setForeground(QColor(T("accent")))
            self.table.item(k, 2).setToolTip(tr("加商品凑单新加的商品（原来不在订单里），确认后写进主订单和 {0} 的订单",
                                                short(r.get("added") or "")))
        if self.edit_mode:
            it = self.table.item(k, 3)
            it.setFlags(it.flags() | Qt.ItemIsEditable)
            if "qty_orig" in r:
                it.setText(f"{self._qty(r):g}  ✎")
                it.setForeground(QColor(T("accent")))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                it.setToolTip(tr("改过：原来是 {0:g}。点击修改", float(r["qty_orig"] or 0)))
            else:
                it.setToolTip(tr("点击修改数量"))
        if n_offers > 1:
            it = self.table.item(k, 6)
            it.setFlags(it.flags() | Qt.ItemIsEditable)
            it.setToolTip(tr("点击选择其他供应商"))
        if c and r.get("base_pack") and (r.get("colli") or {}).get(c):
            # 主订单是数据库箱规的箱：这家按件数换算成它自己的箱（2026-11-19）
            pk = float((r.get("packs") or {}).get(c) or 1)
            got, need = r["colli"][c] * pk, float(r.get("need") or 0)
            it = self.table.item(k, 6)
            tip = tr("门店要 {0:g} 件（{1:g} 箱 × {2:g}）→ {3} 订 {4} 箱 × {5:g} = {6:g} 件",
                     need, float(r.get("qty") or 0), float(r["base_pack"]), short(c), r["colli"][c], pk, got)
            if r.get("unfit"):
                tip += "\n" + tr("箱规太大、没有自动选的：{0}", "、".join(short(s_) for s_ in r["unfit"]))
            it.setToolTip((it.toolTip() + "\n" if n_offers > 1 else "") + tip)
            if abs(got - need) > 1e-9:
                it.setForeground(QColor(T("warn")))
        if c != b:
            for col in range(self.table.columnCount()):
                self.table.item(k, col).setBackground(QColor(T("accent_soft")))
        if extra and extra > 0:
            self.table.item(k, 8).setForeground(QColor(T("warn")))
        if vals[10] not in ("", "∞"):
            try:
                limit = float((settings.get("store_orders") or {}).get("fill", {}).get("max_stock_days") or 60)
                if float(vals[10].replace(",", "")) > limit:
                    self.table.item(k, 10).setForeground(QColor(T("warn")))
                    self.table.item(k, 10).setToolTip(tr("订完要很久才卖得完，可能压货"))
            except ValueError:
                pass

    def _set_row(self, i):
        self.ctl.update_row(i)

    def _cell_clicked(self, k, col):
        """点「当前供应商」那一格直接弹出下拉（其他列点了只是选中）"""
        it = self.table.item(k, col)
        if col == 3 and self.qty_stepper is not None and self.qty_stepper.just_stepped():
            return                                   # 点的是 − / ＋，不弹输入框
        if col in (3, 6) and it is not None and it.flags() & Qt.ItemIsEditable:
            self.table.editItem(it)

    def set_qty(self, i, q):
        """改一个商品的数量（修改分配）"""
        from service import set_row_qty
        r = self.rows[i]
        if abs(q - self._qty(r)) < 1e-9:
            return
        self.rows[i] = set_row_qty(r, q)
        if "qty_orig" in self.rows[i]:
            self.qty_changed[r["row"]] = q
        else:
            self.qty_changed.pop(r["row"], None)
        self._set_row(i)
        self.refresh_status()

    def set_supplier(self, i, s):
        r = self.rows[i]
        if s and s != self.assign.get(r["row"]):
            self.assign[r["row"]] = s
            self._set_row(i)
            self.refresh_status()

    def _reset(self, a, msg):
        self.rows = [r for r in self.rows if r["row"] in self._orig_rows]     # 加商品凑单新加的商品也去掉
        self.assign = dict(a)
        self._fill_table()
        self.refresh_status()
        self.apply_filter()
        self.total_lbl.setToolTip(msg)

    def _sup_totals(self, t):
        """供应商 t 现在分到几箱、多少钱（按件数订货时箱数按这家的箱规算）"""
        from allocation_plan import _totals
        return _totals(self.rows, self.assign, t)

    def under_rules(self):
        """分到了货、但没达到起订条件的供应商"""
        from min_order_optimizer import _satisfied
        out = []
        for ru, _ in self.status_labels:
            c, a = self._sup_totals(ru["target"])
            if c > 0 and not _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"]):
                out.append(ru)
        return out

    def add_items(self):
        under = self.under_rules()
        all_rules = [ru for ru, _ in self.status_labels]
        rules = under + [ru for ru in all_rules if ru not in under]     # 没达标的排前面
        dlg = FillItemsDialog(self, rules, under[0]["target"] if under else None)
        if not dlg.exec() or not dlg.result:
            return
        from allocation_plan import qty_step
        import service as SV
        t = dlg.result["target"]
        idx = {r["row"]: i for i, r in enumerate(self.rows)}
        for row, n in dlg.result["rows"].items():
            i = idx.get(row)
            if i is not None:
                self.set_qty(i, self._qty(self.rows[i]) + n * qty_step(self.rows[i], t))
        heads = list((self.rows[0].get("prices") or {}).keys()) if self.rows else []
        nxt = max([r["row"] for r in self.rows] + [1]) + 1
        store = (self.ctx or {}).get("store")
        for item, n in dlg.result["new"]:
            r = SV.make_added_row(nxt, item, n * float(item.get("pack") or 1), t, heads, store)
            self.rows.append(r)
            self.assign[r["row"]] = t
            self.baseline[r["row"]] = t
            nxt += 1
        self.only_changed.setChecked(True)          # 只看改过的，方便核对加了哪些
        self.only_multi.setChecked(False)
        self._fill_table()
        self.refresh_status()

    def load_candidates(self, t, done):
        """后台读供应商 t 的报价、查门店库存：报价里有、主订单里没有的商品 → done(列表或 Exception)"""
        ctx = self.ctx or {}
        rows = list(self.rows)

        def work():
            try:
                import service as SV
                import reorder_service as RS
                from reorder_page import algo_params
                price = SV.read_price_file(os.path.join(ctx["distribuire_path"], t))
                cands = SV.new_item_candidates(price, rows)
                stock = {}
                if cands and ctx.get("store"):
                    db = RS.make_db(settings.get("db") or None)
                    cfg = settings.get("reorder") or {}
                    stock = RS.stock_for_eans(db, ctx["store"], [c["ean"] for c in cands],
                                              cfg.get("days") or RS.DEFAULT_SALES_DAYS, int(cfg.get("cycle", 30) or 30),
                                              algo_params(settings))
                out = []
                for c in cands:
                    d = stock.get(c["ean"])
                    if d:
                        out.append(dict(c, **d))
                    else:                                  # 数据库里没有（没有销量数据），也列出来，可以手动加
                        out.append(dict(c, name=c.get("desc") or "", daily=None, inv=None))
                res = out
            except Exception as e:
                file_logger.error(f"load candidates failed: {e}", exc_info=True)
                res = e
            if not isinstance(res, Exception):
                self.cand_cache[t] = res
            self._cand_sig.loaded.emit((done, res))

        __import__("erp_widgets").run_excel_job(work)      # 用 Excel 的活排进同一条线程（一批门店比价不再卡死）

    def refresh_status(self):
        from min_order_optimizer import _satisfied
        for ru, lb in self.status_labels:
            c, a = self._sup_totals(ru["target"])
            if c == 0:
                kind, st = "info", tr("不下单")
            elif _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"]):
                kind, st = "ok", tr("已达标")
            else:
                kind, st = "err", tr("未达标")
                gaps = []
                if ru["min_colli"] and c < ru["min_colli"]:
                    gaps.append(tr("差 {0:g} 箱", ru["min_colli"] - c))
                if ru["min_amount"] and a < ru["min_amount"]:
                    gaps.append(tr("差 €{0:,.2f}", ru["min_amount"] - a))
                if gaps:
                    st += "（" + tr("、").join(gaps) + "）"
            lb.setText(tr("<b>{0}</b>　{1:,.0f} 箱 / €{2:,.2f}　要求 {3}　<b>{4}</b>", ru['name'], c, a, describe_condition(ru), st))
            lb.setStyleSheet(f"background:{T(kind + '_soft')}; color:{T(kind)};")
        cost = self._money(self.assign)
        diff = self._cost(self.assign) - self.base_cost
        n = sum(1 for r in self.rows if self.assign.get(r["row"]) != self.baseline.get(r["row"]))
        self.total_lbl.setText(tr("采购总额 €{0:,.2f}　比只按最低价 {1:+,.2f}　改动 {2} 个商品", cost, diff, n)
                               + (tr("　改了数量 {0} 个", len(self.qty_changed)) if self.qty_changed else ""))
        self.total_lbl.setStyleSheet(f"background:{T('surface2')}; color:{T('text')}; border:1px solid {T('line')};")

    def _hidden(self, i):
        """搜索框和两个开关（表头的按列筛选另外算）"""
        r = self.rows[i]
        n_offers = sum(1 for p in r["prices"].values() if p is not None)
        if self.only_multi.isChecked() and n_offers < 2 and r["row"] in self._orig_rows:
            return True
        if self.only_changed.isChecked() and self.assign.get(r["row"]) == self.baseline.get(r["row"]) \
                and r["row"] not in self.qty_changed and r["row"] in self._orig_rows:
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
        bad = [ru["name"] for ru in self.under_rules()]
        if bad and QMessageBox.question(self, tr("手动调整"), tr("{0} 还没有达到起订条件。仍然按现在的方案写入？", ", ".join(bad))) \
                != QMessageBox.Yes:
            return
        self.result = dict(self.assign)
        # 加商品凑单新加的商品（数量改成 0 的不要了）；它们的数量跟着行走，不算「改了数量」
        self.new_rows = [r for r in self.rows if r["row"] not in self._orig_rows and self._qty(r) > 0]
        for r in self.rows:
            if r["row"] not in self._orig_rows:
                self.qty_changed.pop(r["row"], None)
                if self._qty(r) <= 0:
                    self.result.pop(r["row"], None)
        self.accept()


# =====================================================================
# 缺失商品列表 / 方案对比 / 方案设置
# =====================================================================
MISSING_REASONS = ("没有任何报价", "只有被排除的供应商有报价", "起订量放弃了有报价的供应商")


class PriceCheckDialog(QDialog):
    """报价表和数据库对比的结果：一句话结论 + 每份报价表一行 + 导出明细"""
    COLS = ("报价表", "条码数", "数据库有", "供应商对得上", "进价接近（±5%）", "进价差得多", "数据库没进价", "箱规一样",
            "箱规不一样", "数据库登记最多的供应商")

    def __init__(self, parent, res):
        super().__init__(parent)
        import price_check as PC
        self.res = res
        self.setWindowTitle(tr("报价表和数据库对比"))
        self.resize(1080, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        t = QLabel(tr("报价表和数据库对比"))
        t.setObjectName("PageTitle")
        t.setStyleSheet("font-size:18px;")
        lay.addWidget(t)
        b = Banner()
        b.setText("<br>".join("· " + x for x in PC.conclusion(res)))
        lay.addWidget(b)
        tb = make_table([tr(c) for c in self.COLS], stretch_col=9, numeric_cols=tuple(range(1, 9)))
        tb.horizontalHeaderItem(3).setToolTip(tr("数据库里这个商品登记的供应商名称，和这份报价表的供应商名称对得上（按名称里的词粗略对）"))
        tb.horizontalHeaderItem(4).setToolTip(tr("数据库进价（每件）和报价表件价差 5% 以内"))
        tb.setRowCount(len(res["files"]))
        for r, f in enumerate(res["files"]):
            pct = (lambda n: f"{n:,}（{n / f['in_db']:.0%}）" if f["in_db"] else f"{n:,}")
            table_set_row(tb, r, [f["name"], f"{f['items']:,}",
                                  f"{f['in_db']:,}（{f['in_db'] / f['items']:.0%}）" if f["items"] else "0",
                                  pct(f["same_sup"]), pct(f["price_close"]), pct(f["price_far"]), pct(f["no_db_price"]),
                                  f"{f['pack_same']:,}", f"{f['pack_diff']:,}",
                                  "、".join(f"{n} {c}" for n, c in f["top_suppliers"])])
        lay.addWidget(tb, 1)
        if res.get("bad"):
            w = QLabel(tr("这些文件读不了，没算进去：") + "\n" + "\n".join(res["bad"]))
            w.setObjectName("Faint")
            w.setWordWrap(True)
            lay.addWidget(w)
        bb = QHBoxLayout()
        bb.addStretch()
        exp = QPushButton(tr("导出明细 Excel"))
        exp.setToolTip(tr("只读检查，不改数据库也不改报价表。百分比按「数据库有」的商品算。导出的明细里每个条码一行，可以在 Excel 里筛选。"))
        exp.clicked.connect(self.export)
        bb.addWidget(exp)
        ok = QPushButton(tr("关闭"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        bb.addWidget(ok)
        lay.addLayout(bb)

    def export(self):
        import price_check as PC
        path, _ = QFileDialog.getSaveFileName(self, tr("导出 Excel"), os.path.join(
            os.path.dirname(self.res.get("folder") or ""), f"报价和数据库对比_{datetime.now():%Y%m%d_%H%M}.xlsx"),
            tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        try:
            PC.export(self.res, path)
            open_path(path)
        except Exception as e:
            QMessageBox.critical(self, tr("导出"), tr("导出失败：{0}", e))


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
        self.search.setPlaceholderText(tr("搜索 EAN、品名、Codice Fornitore 或供应商"))
        self.search.setFixedWidth(280)
        bar.addWidget(self.search)
        bar.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        exp = QPushButton(tr("导出 Excel"))
        bar.addWidget(exp)
        lay.addLayout(bar)
        # 要订数量 = 主订单里这个商品要订多少（COLLI 箱数；主订单按件数订货时是件数），不是库存
        units = {m.get("unit") or "colli" for m in self.items}
        self._mixed = len(units) > 1
        qh = tr("要订（件）") if units == {"pcs"} else tr("要订（箱）") if units == {"colli"} else tr("要订数量")
        self.table = make_table([tr("行"), "EAN", "ARTICOLO", "Codice Fornitore", tr("供应商"), qh, tr("原因"),
                                 tr("有报价的供应商"), tr("库存够卖（天）")],
                                stretch_col=2, numeric_cols=(0, 5, 8))
        import product_images as PI
        PI.add_thumbs(self.table, 2, 1, settings)
        self.table.horizontalHeaderItem(5).setToolTip(
            tr("主订单里这个商品要订的数量（箱数；主订单按件数订货时是件数）——就是这次买不到的量，不是库存"))
        self.table.horizontalHeaderItem(3).setToolTip(tr("供应商自己的货号（数据库商品表里登记的）"))
        self.table.horizontalHeaderItem(4).setToolTip(tr("数据库里登记的这个商品的供应商：缺货时找谁"))
        self.table.horizontalHeaderItem(8).setToolTip(tr("这个商品店里现在的库存还能卖几天（买不到时还能撑多久）"))
        self.table.setColumnHidden(8, not any(m.get("daily") is not None for m in self.items))
        for c, wd in ((3, 120), (4, 160)):
            self.table.setColumnWidth(c, wd)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        lay.addWidget(self.table, 1)
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(self.table, lambda: len(self.items), self._text, self._fill,
                                   hide=self._hidden, numeric=(0, 5, 8))
        self.ctl.rendered.connect(lambda n, tot: self.count.setText(
            tr("显示 {0} / {1} 个 · 点表头排序，右键表头筛选", n, tot)))
        self.search.textChanged.connect(lambda _: self.ctl.render())
        from erp_widgets import ColumnFilterBox
        bar.insertSpacing(1, 14)
        bar.insertWidget(2, ColumnFilterBox(self.ctl, 6, tr("原因")))
        bar.insertSpacing(3, 8)
        bar.insertWidget(4, ColumnFilterBox(self.ctl, 4, tr("供应商")))
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
        if c == 8:
            if m.get("daily") is None:
                return ""
            import store_orders as SO
            return SO.days_text(SO.cover_days({"inv": m.get("inv") or 0, "daily": m.get("daily") or 0}))
        q = f"{float(m.get('qty') or 0):g}"
        if self._mixed:
            q += " " + (tr("件") if m.get("unit") == "pcs" else tr("箱"))
        return [str(m.get("row", "")), m.get("ean", "") or "", str(m.get("articolo") or ""),
                str(m.get("sup_code") or ""), str(m.get("sup_name") or ""), q, tr(m.get("reason") or ""),
                tr("、").join(short(x) for x in (m.get("offers") or []))][c]

    def _fill(self, i, k):
        table_set_row(self.table, k, [self._text(i, c) for c in range(9)])
        self.table.item(k, 2).setToolTip(str(self.items[i].get("articolo") or ""))
        if self.items[i].get("reason") == MISSING_REASONS[1]:
            self.table.item(k, 6).setForeground(QColor(T("warn")))

    def _hidden(self, i):
        text = self.search.text().strip().lower()
        m = self.items[i]
        return bool(text) and text not in (f"{m.get('ean', '')} {m.get('articolo', '')} {m.get('sup_code') or ''} "
                                           f"{m.get('sup_name') or ''}").lower()


def _money_txt(v, sign=False):
    if v is None:
        return "—"
    if abs(v) < 0.005:
        return "0.00"
    return f"{v:+,.2f}" if sign else f"{v:,.2f}"


def _sup_label(x):
    return x.get("label") or short(x.get("name"))


def _rule_cond(x):
    items = []
    if x.get("min_colli"):
        items.append(tr("{0:g} 箱", float(x["min_colli"])))
    if x.get("min_amount"):
        items.append(f"€{float(x['min_amount']):g}")
    return (tr(" 且 ") if x.get("mode", "all") == "all" else tr(" 或 ")).join(items) if items else tr("无条件")


def scen_letter(i):
    return "ABCDEFGHIJ"[i] if i < 10 else str(i + 1)


def _SC_URGENT_DAYS():
    import scenarios as _SC
    return _SC.URGENT_DAYS


def scen_items(r, kind):
    """方案对比里能点开的列表：missing 缺失商品 / urgent 紧急缺货商品 / excl 只有被排除供应商有的"""
    import scenarios as _SC
    miss = r.get("missing") or []
    if kind == "urgent":
        return [m for m in miss if _SC.urgent(m)]
    if kind == "excl":
        return [m for m in miss if m.get("reason") == "只有被排除的供应商有报价"]
    return list(miss)


SCEN_LIST_TITLE = {"missing": "缺失商品", "urgent": "紧急缺货商品", "excl": "只有被排除供应商有的商品"}


def fill_scenario_table(t, results, chosen=None, recommended=None, compact=False):
    """
    金额：采购总额、同样商品的价差、少买的商品；商品：缺失、紧急缺货、只有被排除供应商有的（这三行点数字看列表）…
    不放：比 A 多花 / 少花、凑单多花、买得到 / 要订、比 A 换了供应商的（用户说没用）。compact 参数不用了（保留兼容）
    返回 {(表格行, 列): (方案下标, 列表种类)}
    方案对比明细表：每个方案一列（表头「A 全部供应商」），格子里只放数字，说明放在鼠标提示里。
    分四段：金额 / 商品 / 各供应商金额 / 起订量。
    和第一个方案（A，基准）比的差额 = 同样商品的价差 − 少买的商品 + 多买到的商品。
    返回 {(表格行, 列): 方案下标}（缺失商品那一行，点开看列表）
    """
    n = len(results)
    first = tr(results[0]["name"]) if results else ""
    heads = [""]
    for i, r in enumerate(results):
        h = f"{scen_letter(i)}  {tr(r['name'])}"
        if i == chosen:
            h += "  ✔"
        heads.append(h)
    t.clearSpans()
    t.setColumnCount(len(heads))
    t.setHorizontalHeaderLabels(heads)
    new = all("price_diff" in r for r in results)

    sups, sup_label = [], {}
    for r in results:
        for x in r.get("suppliers") or []:
            if x["name"] not in sups:
                sups.append(x["name"])
                sup_label[x["name"]] = _sup_label(x)
    rules = []
    for r in results:
        for x in r.get("rules") or []:
            if x.get("name") not in [y.get("name") for y in rules]:
                rules.append(x)

    def base_only(f):
        return ["—" if i == 0 else f(r) for i, r in enumerate(results)]

    # (种类, 名称, 值, 鼠标提示)
    L = [("sec", tr("金额 €"))]
    L.append(("total", tr("采购总额"), [_money_txt(r["total"]) for r in results],
              tr("这个方案买到的所有商品：箱数 × 每箱件数 × 件价")))
    if new:
        L.append(("price", tr("同样商品的价差"), base_only(lambda r: _money_txt(r["price_diff"], True)),
                  tr("两个方案都买得到的商品，换了供应商以后贵了（+）还是便宜了（−）。这才是真正的价格差。")))
        L.append(("lost", tr("少买的商品"), base_only(lambda r: _money_txt(-r["lost_value"], True)),
                  tr("A 里买得到、这个方案买不到的商品，在 A 里要花的钱。总额变低常常是因为少买了货，不是更便宜。")))
        if any(r.get("gained_rows") for r in results):
            L.append(("gained", tr("多买到的商品"), base_only(lambda r: _money_txt(r["gained_value"], True)), ""))

    L.append(("sec", tr("商品")))
    L.append(("missing", tr("缺失商品（点数字看列表）"), [f"{len(r['missing']):,}" for r in results],
              tr("点数字看缺了哪些商品")))
    if any(m.get("daily") is not None for r in results for m in r["missing"]):
        L.append(("urgent", tr("　紧急缺货商品"), [f"{len(scen_items(r, 'urgent')):,}" for r in results],
                  tr("买不到、店里库存又不够卖 {0} 天的商品：这次不订很可能断货。点数字看列表", _SC_URGENT_DAYS())))
    L.append(("excl", tr("　只有被排除供应商有的"), [f"{len(scen_items(r, 'excl')):,}" for r in results],
              tr("这些商品只有被排除的那家供应商有报价：不用它就买不到。点数字看列表")))
    if new:
        L.append(("colli", tr("采购箱数"), [f"{r.get('colli', 0):,.0f}" for r in results], ""))
        L.append(("mqty", tr("缺失箱数"), [f"{r.get('missing_qty', 0):,.0f}" for r in results], ""))
    L.append(("changes", tr("凑单换供应商的"), [f"{r.get('changes', 0):,}" for r in results],
              tr("为了起订量，没有分给最低价供应商的商品")))

    L.append(("sec", tr("各供应商金额 €（占比）")))
    sup_tips = {}
    for sname in sups:
        vals, tips = [], []
        for r in results:
            x = next((y for y in r.get("suppliers") or [] if y["name"] == sname), None)
            if x and x.get("excluded"):
                vals.append(tr("排除"))
                tips.append("")
            elif x and x["rows"]:
                share = x["amount"] / r["total"] * 100 if r["total"] else 0
                vals.append(f"{x['amount']:,.2f}  ({share:.0f}%)")
                tips.append(tr("{0:,.0f} 箱 · {1} 个商品", x["colli"], x["rows"]))
            else:
                vals.append("—")
                tips.append("")
        sup_tips[len(L)] = tips
        L.append(("sup", sup_label.get(sname) or short(sname), vals, tr("文件：{0}", short(sname))))
    rule_tips = {}
    if rules:
        L.append(("sec", tr("起订量")))
        for ru in rules:
            vals, tips = [], []
            for r in results:
                x = next((y for y in r.get("rules") or [] if y.get("name") == ru.get("name")), None)
                if not x:
                    vals.append(tr("排除"))
                    tips.append("")
                    continue
                vals.append(tr(x["status"]))
                tips.append(tr("{0:,.0f} 箱 / €{1:,.2f}", x.get("after_colli") or 0, x.get("after_amount") or 0))
            rule_tips[len(L)] = tips
            L.append(("rule", ru.get("name") or "", vals, tr("起订条件：{0}", _rule_cond(ru))))

    t.setRowCount(len(L))
    links = {}
    for row, ln in enumerate(L):
        kind = ln[0]
        if kind == "sec":
            for c in range(n + 1):
                it = QTableWidgetItem(ln[1] if c == 0 else "")
                f = it.font()
                f.setBold(True)
                f.setPointSize(max(8, f.pointSize() - 1))
                it.setFont(f)
                it.setForeground(QColor(T("faint")))
                it.setFlags(Qt.ItemIsEnabled)
                t.setItem(row, c, it)
            t.setSpan(row, 0, 1, n + 1)
            t.setRowHeight(row, 30)
            continue
        _, label, vals, tip = ln
        table_set_row(t, row, [label] + list(vals), bold=kind == "total")
        t.item(row, 0).setForeground(QColor(T("text") if kind in ("total", "base") else T("muted")))
        if tip:
            t.item(row, 0).setToolTip(tip)
        for c in range(1, n + 1):
            it = t.item(row, c)
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            f = QFont(NUM_FONT, 10)
            f.setBold(kind == "total")
            it.setFont(f)
            r = results[c - 1]
            txt = it.text()
            if row in sup_tips and sup_tips[row][c - 1]:
                it.setToolTip(sup_tips[row][c - 1])
            if row in rule_tips and rule_tips[row][c - 1]:
                it.setToolTip(rule_tips[row][c - 1])
            if kind in ("missing", "urgent", "excl"):
                links[(row, c)] = (c - 1, kind)
                it.setToolTip(tr("点开看列表"))
                if txt != "0":
                    it.setForeground(QColor(T("err") if kind == "urgent" else T("warn")))
                    f.setUnderline(True)
                    f.setBold(kind == "urgent")
                    it.setFont(f)
            elif kind in ("vs", "price", "lost", "gained") and c > 1 and txt not in ("—", "0.00"):
                good = txt.startswith("-") if kind in ("vs", "price") else False
                if kind == "lost":
                    it.setForeground(QColor(T("warn")))
                elif kind == "gained":
                    it.setForeground(QColor(T("ok")))
                else:
                    it.setForeground(QColor(T("ok") if good else T("err")))
            elif kind == "rule":
                st = next((y.get("status") for y in r.get("rules") or [] if y.get("name") == label), None)
                if st == "未达标":
                    it.setForeground(QColor(T("err")))
                elif st in ("已达标", "已凑单达标"):
                    it.setForeground(QColor(T("ok")))
            if txt in (tr("排除"), "—"):
                it.setForeground(QColor(T("faint")))
    if chosen is not None:
        for row in range(t.rowCount()):
            if L[row][0] != "sec":
                t.item(row, chosen + 1).setBackground(QColor(T("accent_soft")))
    hh = t.horizontalHeader()
    hh.setStretchLastSection(False)
    hh.setSectionResizeMode(0, QHeaderView.Stretch)
    for c in range(1, n + 1):
        hh.setSectionResizeMode(c, QHeaderView.Fixed)
        t.setColumnWidth(c, 190)
    t.verticalHeader().setDefaultSectionSize(34)
    return links


def scenario_warnings(results):
    """方案对比的提醒：排除的供应商没找到、两个方案完全一样"""
    out = []
    for i, r in enumerate(results):
        if r.get("unmatched"):
            out.append(tr("「{0}」没找到要排除的供应商（{1}），这个方案什么都没排除。点「供应商名称」给文件起名后会重新对比。",
                          tr(r["name"]), "、".join(r["unmatched"])))
        elif r.get("same_as") is not None:
            out.append(tr("「{0}」和「{1}」结果完全一样。", tr(r["name"]), tr(results[r["same_as"]]["name"])))
    return out


def scenario_advice(results, rec):
    """推荐说明（一句话）"""
    if rec is None:
        return ""
    r = results[rec]
    txt = tr("★ 推荐 {0}「{1}」：缺的商品最少，其中总额最低。", scen_letter(rec), tr(r["name"]))
    cheaper = [(i, x) for i, x in enumerate(results) if i != rec and x["total"] < r["total"] - 0.005
               and "lost_value" in x]
    if cheaper:
        i, x = min(cheaper, key=lambda p: p[1]["total"])
        txt += tr(" {0} 总额低 €{1:,.2f}，是因为少买了 {2} 个商品。", scen_letter(i), r["total"] - x["total"],
                  x["lost_rows"])
    return txt


class ScenarioCard(QFrame):
    """方案卡片：总额、和 A 比、缺失商品。点卡片（或「选这个方案」）选方案；卡片上的文字可以选中复制"""

    def __init__(self, parent, i, r, rec, on_pick, on_missing):
        super().__init__(parent)
        self.i, self.on_pick = i, on_pick
        self.setObjectName("ScenCard")
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(4)
        top = QHBoxLayout()
        self.title = QLabel(f"{scen_letter(i)}  {tr(r['name'])}")
        self.title.setStyleSheet("font-weight:700; font-size:14px; background:transparent;")
        top.addWidget(self.title)
        top.addStretch()
        self.base_tag = tr("★ 推荐") if i == rec else ""
        self.tag = QLabel(self.base_tag)
        self.tag.setStyleSheet(f"color:{T('accent')}; font-weight:600; background:transparent;")
        top.addWidget(self.tag)
        v.addLayout(top)
        tot = QLabel(f"€{r['total']:,.2f}")
        tot.setStyleSheet(f"font-family:'{NUM_FONT}'; font-size:22px; font-weight:700; background:transparent;")
        v.addWidget(tot)
        if i == 0:
            d_txt, d_col = tr("基准方案"), T("faint")
        else:
            # 和表格的算式一样：同样商品的价差 + 少买的商品（不显示「比 A 多花」，那个数含了少买的货）
            if "price_diff" in r:
                d = r["price_diff"]
                d_txt = tr("同样商品 {0}", (f"+€{d:,.2f}" if d > 0.005 else (f"−€{-d:,.2f}" if d < -0.005 else tr("一样"))))
            else:
                d = r.get("vs_first", 0)
                d_txt = tr("比 A {0}", (f"+€{d:,.2f}" if d > 0.005 else (f"−€{-d:,.2f}" if d < -0.005 else tr("一样"))))
            d_col = T("err") if d > 0.005 else (T("ok") if d < -0.005 else T("faint"))
            if r.get("lost_rows"):
                d_txt += tr("，少买 {0} 个（€{1:,.2f}）", r["lost_rows"], r.get("lost_value") or 0)
        dl = QLabel(d_txt)
        dl.setStyleSheet(f"color:{d_col}; background:transparent;")
        v.addWidget(dl)
        nm = len(r["missing"])
        import scenarios as _SC
        nu = _SC.urgent_count(r["missing"])
        mb = QPushButton(tr("缺失 {0} 个商品（{1} 个店里不够卖一周）›", nm, nu) if nu else tr("缺失 {0} 个商品 ›", nm))
        if nu:
            mb.setToolTip(tr("这些商品这次买不到，店里的库存又不够卖 {0} 天，很可能断货", _SC.URGENT_DAYS))
        mb.setObjectName("Link")
        mb.setStyleSheet(f"text-align:left; padding:2px 0; color:{T('err') if nu else T('warn') if nm else T('muted')};")
        mb.clicked.connect(lambda: on_missing(i))
        v.addWidget(mb, 0, Qt.AlignLeft)
        self.pick_btn = QPushButton(tr("选这个方案"))
        self.pick_btn.clicked.connect(lambda: on_pick(i))
        v.addWidget(self.pick_btn, 0, Qt.AlignLeft)
        # 文字可以选中复制；单击文字（没有选中文字时）也算选这个方案
        for lb in (self.title, self.tag, tot, dl):
            lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lb.installEventFilter(self)
        self.set_selected(False)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.MouseButtonRelease and isinstance(obj, QLabel) and not obj.hasSelectedText():
            self.on_pick(self.i)
        return False

    def set_selected(self, on):
        bd = T("accent") if on else T("line")
        bg = T("accent_soft") if on else T("surface")
        self.setStyleSheet(f"#ScenCard {{ background:{bg}; border:{2 if on else 1}px solid {bd}; border-radius:10px; }}")
        self.tag.setText("  ".join(x for x in (self.base_tag, ("✔ " + tr("选用")) if on else "") if x))
        if hasattr(self, "pick_btn"):
            self.pick_btn.setText(tr("✔ 已选这个方案") if on else tr("选这个方案"))
            self.pick_btn.setObjectName("Primary" if on else "")
            self.pick_btn.style().unpolish(self.pick_btn)
            self.pick_btn.style().polish(self.pick_btn)

    def mousePressEvent(self, e):
        self.on_pick(self.i)
        super().mousePressEvent(e)


class ScenarioDiffDialog(QDialog):
    """
    各方案的差异商品：每行一个商品，每个方案一列显示分给哪家供应商（最便宜的标绿、买不到的标黄），
    最后一列是最贵和最便宜方案的差价。可以排序、筛选、导出。
    """

    def __init__(self, parent, results, diff, title=None):
        super().__init__(parent)
        self.results, self.diff = results, list(diff or [])
        self.n = len(results)
        self.setWindowTitle(title or tr("差异商品"))
        self.resize(1180, 700)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        t = QLabel(title or tr("差异商品"))
        t.setObjectName("PageTitle")
        t.setStyleSheet("font-size:18px;")
        lay.addWidget(t)
        legend = "　".join(f"{scen_letter(i)} = {tr(r['name'])}" for i, r in enumerate(results))
        t.setToolTip(tr("在不同方案里分给了不同供应商、或者有的方案买不到的商品。{0}。绿色 = 这几个方案里最便宜，黄色 = 买不到；鼠标停在格子上看金额。", legend))
        sub = QLabel(legend)
        sub.setObjectName("Faint")
        lay.addWidget(sub)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索 EAN 或品名"))
        self.search.setFixedWidth(240)
        bar.addWidget(self.search)
        bar.addSpacing(12)
        self.kind = QComboBox()
        for key, text in (("all", tr("全部差异商品")), ("lost", tr("只看有方案买不到的")),
                          ("switch", tr("只看换了供应商的"))):
            self.kind.addItem(text, key)
        self.kind.currentIndexChanged.connect(lambda _: self.ctl.render())
        bar.addWidget(self.kind)
        bar.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        bar.addWidget(self.count)
        exp = QPushButton(tr("导出 Excel"))
        bar.addWidget(exp)
        lay.addLayout(bar)
        heads = ["EAN", "ARTICOLO", tr("箱数")] + [f"{scen_letter(i)}  {tr(r['name'])}" for i, r in enumerate(results)] \
            + [tr("差价 €")]
        self.c_gap = 3 + self.n
        # 有门店库存快照时：买不到的商品店里还能撑几天
        self.c_cover = self.c_gap + 1 if any(m.get("daily") is not None for m in self.diff) else None
        if self.c_cover:
            heads.append(tr("库存够卖（天）"))
        self.table = make_table(heads, stretch_col=1, numeric_cols=(2, self.c_gap) + ((self.c_cover,) if self.c_cover else ()))
        import product_images as PI
        PI.add_thumbs(self.table, 1, 0, settings)
        if self.c_cover:
            self.kind.addItem(tr("只看买不到、店里又不够卖一周的"), "urgent")
            self.table.horizontalHeaderItem(self.c_cover).setToolTip(
                tr("店里现在的库存还能卖几天：有方案买不到、又不够卖一周的标红"))
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        hh = self.table.horizontalHeader()
        for c in range(3, 3 + self.n):
            hh.setSectionResizeMode(c, QHeaderView.Fixed)
            self.table.setColumnWidth(c, 160)
        self.table.setColumnWidth(0, 130)
        lay.addWidget(self.table, 1)
        from erp_widgets import TableSortFilter
        self.ctl = TableSortFilter(self.table, lambda: len(self.diff), self._text, self._fill, key=self._key,
                                   hide=self._hidden, numeric=(2, self.c_gap) + ((self.c_cover,) if self.c_cover else ()))
        self.ctl.rendered.connect(lambda k, tot: self.count.setText(
            tr("显示 {0} / {1} 个 · 点表头排序，右键表头筛选", k, tot)))
        self.search.textChanged.connect(lambda _: self.ctl.render())
        exp.clicked.connect(lambda: self.ctl.export(self, "方案差异商品.xlsx", tr("差异商品")))
        bb = QHBoxLayout()
        bb.addStretch()
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.accept)
        bb.addWidget(close)
        lay.addLayout(bb)
        self.ctl.set_sort(self.c_gap, True)          # 差价大的排前面

    def _gap(self, m):
        a = [x for x in m["amounts"] if x is not None]
        return (max(a) - min(a)) if len(a) > 1 else None

    def _text(self, i, c):
        m = self.diff[i]
        if c < 3:
            return [m.get("ean", "") or "", str(m.get("articolo") or ""), f"{float(m.get('qty') or 0):g}"][c]
        if c < self.c_gap:
            s_ = m["sups"][c - 3] if c - 3 < len(m["sups"]) else ""
            return s_ or tr("买不到")
        if c == self.c_cover:
            import store_orders as SO
            return SO.days_text(SO.cover_days(m)) if m.get("daily") is not None else ""
        g = self._gap(m)
        return "" if g is None else f"{g:,.2f}"

    def _key(self, i, c):
        if c == self.c_cover:
            import store_orders as SO
            v = SO.cover_days(self.diff[i]) if self.diff[i].get("daily") is not None else None
            return 1e9 if v is None or v == float("inf") else v
        if c == self.c_gap:
            g = self._gap(self.diff[i])
            return -1.0 if g is None else g
        return None

    def _fill(self, i, k):
        vals = [self._text(i, c) for c in range(self.table.columnCount())]
        table_set_row(self.table, k, vals)
        m = self.diff[i]
        amts = [a for a in m["amounts"] if a is not None]
        lo = min(amts) if len(set(round(a, 2) for a in amts)) > 1 else None
        for kk, a in enumerate(m["amounts"]):
            it = self.table.item(k, 3 + kk)
            if a is None:
                it.setForeground(QColor(T("warn")))
                it.setBackground(QColor(T("warn_soft")))
            else:
                it.setToolTip(f"€{a:,.2f}")
                if lo is not None and abs(a - lo) < 0.005:
                    it.setForeground(QColor(T("ok")))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
        if self.c_cover and any(not s_ for s_ in m["sups"]):
            import scenarios as _SC
            if _SC.urgent(m):
                it = self.table.item(k, self.c_cover)
                it.setForeground(QColor(T("err")))
                it.setToolTip(tr("有方案买不到，店里库存又不够卖 {0} 天", _SC.URGENT_DAYS))

    def _hidden(self, i):
        m = self.diff[i]
        kind = self.kind.currentData()
        lost = any(not s_ for s_ in m["sups"])
        if kind == "lost" and not lost:
            return True
        if kind == "switch" and lost:
            return True
        if kind == "urgent":
            import scenarios as _SC
            if not (lost and _SC.urgent(m)):
                return True
        q = self.search.text().strip().lower()
        if q and q not in str(m.get("ean", "")).lower() and q not in str(m.get("articolo", "")).lower():
            return True
        return False


class SupplierNamesDialog(QDialog):
    """给供应商报价文件起名字（MEGA 的文件叫 formattato_…，起名 MEGA CASH 以后方案和起订规则都能认出来）"""

    def __init__(self, parent, headers):
        super().__init__(parent)
        import supplier_names as SN
        self.headers = [h for h in headers if h]
        self.setWindowTitle(tr("供应商名称"))
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        self.setToolTip(tr("供应商文件名每周会变，有的文件名里也看不出是哪家（比如 formattato_059603）。在这里给每个文件起个名字："
                           "「管理方案」里排除的关键词、起订规则都会同时按文件名和这个名字找。拖入新文件替换旧文件时，名字会自动带到新文件上。"))
        self.table = QTableWidget(len(self.headers), 2)
        self.table.setHorizontalHeaderLabels([tr("供应商文件"), tr("供应商名称")])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i, h in enumerate(self.headers):
            a = QTableWidgetItem(h)
            a.setFlags(a.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, a)
            cb = QComboBox()                       # 和拖入报价时一样：选或输入供应商名称
            cb.setEditable(True)
            cb.setInsertPolicy(QComboBox.NoInsert)
            cb.addItems(SN.choices(settings, self.headers))
            cb.setCurrentText(SN.label_of(h, settings) if SN.recognized(h, settings) else "")
            cb.lineEdit().setPlaceholderText(tr("选择或输入供应商"))
            if SN.guessed(h, settings):
                cb.setToolTip(tr("程序猜的名字，可以改"))
            self.table.setCellWidget(i, 1, cb)
        lay.addWidget(self.table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _save(self):
        import supplier_names as SN
        names = settings.setdefault("supplier_names", {})
        for i, h in enumerate(self.headers):
            w = self.table.cellWidget(i, 1)
            v = (w.currentText() if w is not None else "").strip()
            if v and v != SN.stem(h):
                names[h] = v
            else:
                names.pop(h, None)
        save_settings(settings)
        self.accept()


class ScenarioDialog(QDialog):
    """
    几种方案的对比，选一种写入。self.chosen = 选中的方案下标；"again" = 改了供应商名称，要重新对比。
    布局：上面每个方案一张卡片（总额、和 A 比、缺失商品，点卡片选方案），下面是明细表。
    """

    def __init__(self, parent, results):
        super().__init__(parent)
        import scenarios as SC
        self.results = results
        self.rec = SC.recommend(results)
        self.chosen = self.rec if self.rec is not None else 0
        self.setWindowTitle(tr("选择方案"))
        self.resize(1180, 820)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel(tr("选择方案"))
        t.setObjectName("PageTitle")
        t.setStyleSheet("font-size:20px;")
        head.addWidget(t)
        head.addStretch()
        diff = (results[0].get("diff") if results else None) or []
        from erp_widgets import CountButton
        self.b_diff = CountButton(tr("差异商品"), len(diff))
        self.b_diff.setToolTip(tr("在不同方案里分给了不同供应商、或者买不到的商品，逐个对比"))
        self.b_diff.setEnabled(bool(diff))
        self.b_diff.clicked.connect(lambda: ScenarioDiffDialog(self, results, diff).exec())
        head.addWidget(self.b_diff)
        b_names = QPushButton(tr("供应商名称"))
        b_names.setToolTip(tr("给供应商文件起名字（比如 formattato_… 叫 MEGA CASH），保存后重新对比"))
        b_names.clicked.connect(self._names)
        head.addWidget(b_names)
        lay.addLayout(head)

        msgs = scenario_warnings(results)
        adv = scenario_advice(results, self.rec)
        if msgs:
            wb = Banner()
            wb.show_msg("⚠ " + "　".join(msgs), "warn")
            lay.addWidget(wb)
        if adv:
            nb = Banner()
            nb.show_msg(adv, "info")
            lay.addWidget(nb)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.cards = []
        for i, r in enumerate(results):
            c = ScenarioCard(self, i, r, self.rec, self._pick, self._missing)
            self.cards.append(c)
            cards.addWidget(c, 1)
        lay.addLayout(cards)

        self.table = make_table([""], stretch_col=None)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.cellClicked.connect(self._clicked)
        lay.addWidget(self.table, 1)
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
        self._pick(self.chosen)

    def _names(self):
        heads = []
        for r in self.results:
            for x in r.get("suppliers") or []:
                if x["name"] not in heads:
                    heads.append(x["name"])
        if SupplierNamesDialog(self, heads).exec():
            self.chosen = "again"
            self.done(QDialog.Accepted)

    def _missing(self, i):
        MissingDialog(self, self.results[i]["missing"],
                      tr("缺失商品 · {0} {1}", scen_letter(i), tr(self.results[i]["name"]))).exec()

    def _pick(self, i):
        if i == self.chosen and getattr(self, "links", None) is not None:
            return
        self.chosen = i
        for c in self.cards:
            c.set_selected(c.i == i)
        self.links = fill_scenario_table(self.table, self.results, i, self.rec)
        self.ok.setText(tr("用 {0}「{1}」写入", scen_letter(i), tr(self.results[i]["name"])))

    def _clicked(self, row, col):
        if (row, col) in self.links:
            i, kind = self.links[(row, col)]
            MissingDialog(self, scen_items(self.results[i], kind),
                          tr("{0} · {1} {2}", tr(SCEN_LIST_TITLE[kind]), scen_letter(i), tr(self.results[i]["name"]))).exec()

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
            ws.append([(t.item(r, c).text() if t.item(r, c) else "") for c in range(t.columnCount())])
        for c in ws[1]:
            c.font = Font(bold=True)
        diff = (self.results[0].get("diff") if self.results else None) or []
        if diff:
            ws3 = wb.create_sheet("差异商品")
            head = ["EAN", "ARTICOLO", tr("箱数")]
            for i, r in enumerate(self.results):
                head += [f"{scen_letter(i)} {tr(r['name'])}", f"{scen_letter(i)} €"]
            ws3.append(head)
            for m in diff:
                line = [m.get("ean", ""), m.get("articolo", ""), m.get("qty")]
                for s_, a in zip(m["sups"], m["amounts"]):
                    line += [s_ or tr("买不到"), None if a is None else round(a, 2)]
                ws3.append(line)
            for c in ws3[1]:
                c.font = Font(bold=True)
        for i, r in enumerate(self.results):
            ws2 = wb.create_sheet((f"缺失-{scen_letter(i)}"))
            ws2.append([tr("行"), "EAN", "ARTICOLO", tr("数量"), tr("原因"), tr("有报价的供应商")])
            for m in r["missing"]:
                ws2.append([m["row"], m.get("ean", ""), m.get("articolo", ""), m.get("qty"), tr(m["reason"]),
                            "、".join(short(x) for x in m.get("offers") or [])])
        try:
            wb.save(path)
        except Exception as e:
            QMessageBox.critical(self, tr("导出"), tr("导出失败：{0}", e))
            return
        from erp_widgets import open_file
        open_file(self, path, tr("导出对比"))


class ScenarioSettingsDialog(QDialog):
    """管理方案：每个方案一个名字 + 排除哪些供应商（文件名里的关键词，逗号分开）"""

    def __init__(self, parent, defs, suppliers=()):
        super().__init__(parent)
        self.setWindowTitle(tr("管理方案"))
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        tip = tr("每个方案排除哪些供应商：写供应商名称或文件名里的关键词，几个用逗号分开（不分大小写）。第一个方案是对比的基准，一般不排除任何供应商。")
        if suppliers:
            import supplier_names as SN
            tip += "\n\n" + tr("这次的供应商：{0}", tr("、").join(
                f"{SN.label_of(h, settings)}（{short(h)}）" if SN.label_of(h, settings) != short(h) else short(h)
                for h in suppliers))
        self.setToolTip(tip)
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


def _preload_libs():
    """
    打开程序时窗口先出来，读 Excel（openpyxl）、比价（service：Excel 自动化）、分货 / 导入（pandas）这些大的库
    在后台先读进来（2026-11-04 提速），用到时不用再等
    """
    import importlib
    for name in ("openpyxl", "service", "matcher", "pandas"):
        try:
            importlib.import_module(name)
        except Exception:
            pass


def _all_pages(side, sub_pages):
    """侧边栏的页面 + 批发商比价里的标签页（不在侧边栏上的）。
    注意：不能在 class 里直接写这个列表推导式——class 里的变量在推导式里看不到，启动就会报错闪退。"""
    on_side = {k for k, _ in side}
    return list(side) + [(k, t) for subs in sub_pages.values() for k, t in subs if k not in on_side]


class _SearchBox(QLineEdit):
    """顶栏搜索框：右边一个「Ctrl K」小标；点一下 / 打字 = 打开 Ctrl K 指令面板（2026-11-16 设计稿）"""
    opened = Signal(str)

    def __init__(self):
        super().__init__()
        self.hint = QLabel("Ctrl K", self)
        self.hint.setObjectName("KeyHint")
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        self.opened.emit("")

    def keyPressEvent(self, e):
        t = e.text()
        if t and t.isprintable() and not (e.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.opened.emit(t)
            return
        super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.hint.adjustSize()
        self.hint.move(self.width() - self.hint.width() - 10, (self.height() - self.hint.height()) // 2)


class MainWindow(QMainWindow):
    # 货物分配系统是主系统；采购管理是其中一个模块
    NAV_GROUPS = [
        (tr("概览"), [("home", tr("驾驶舱"))]),
        (tr("采购管理"), [("store_orders", tr("LUCAS定制采购")), ("order", tr("DETERSIVO采购")),
                        ("tracking", tr("采购订单跟踪"))]),
        (tr("销售分析"), [("volantino", tr("促销分析")), ("sellout", tr("SELL OUT 分析"))]),
        (tr("分货管理"), [("distribution", tr("分货数据处理")), ("import", tr("导入 / 导出"))]),
        (tr("系统"), [("settings", tr("系统设置")), ("log", tr("运行日志"))]),
    ]
    # 批发商比价里面的几页：侧边栏只放「批发商比价」，进去以后顶部一排标签切换
    # LUCAS定制采购（2026-11-03 用户定名，原来的「门店配货」）：总仓采购 = 原来的门店配货（Centrale Acquisti 的 SCHEDA，
    # 直送 / 并 BMS）；门店采购 = 门店自己采购的供应商（PDV 文件夹，一张单一家店）
    SUB_PAGES = {"store_orders": [("store_orders", tr("总仓采购")), ("store_pdv", tr("门店采购"))],
                 "order": [("order", tr("比价处理")), ("result", tr("比价结果")), ("history", tr("历史记录")),
                           ("rules", tr("起订规则")), ("search", tr("缺失查找")),
                           ("griglia", tr("Griglia"))]}
    SUB_PARENT = {k: parent for parent, subs in SUB_PAGES.items() for k, _ in subs}
    NAV_SIDE = [item for _, items in NAV_GROUPS for item in items]          # 侧边栏上的
    NAV = _all_pages(NAV_SIDE, SUB_PAGES)                                    # 所有页面（含标签页）
    NAV_SECTION = {k: g for g, items in NAV_GROUPS for k, _ in items}
    SOON = set()          # 还没做的模块（侧边栏上标「二期」）
    # 2026-11-15 第 2 轮（用户：全面重构、要科技感、流程简洁）：左边一条图标栏，六个入口；
    # 一个入口里有几页的，顶栏下面一排标签切换。NAV / SUB_PAGES 照旧（页面编号、别的代码都按它们）
    RAIL = [("home", tr("驾驶舱"), "home", ["home"]),
            ("buy", tr("采购"), "cart",
             ["store_orders", "store_pdv", "order", "result", "history", "rules", "search"]),
            ("tracking", tr("订单"), "truck", ["tracking"]),
            ("analysis", tr("分析"), "result", ["sellout", "volantino", "griglia"]),
            ("data", tr("数据"), "db", ["distribution", "import"]),
            ("settings", tr("设置"), "rules", ["settings", "log"])]
    # 2026-11-16 用户：点图标栏弹出的小菜单只放这一级的页面——比价结果 / 历史记录 / 起订规则 / 缺失查找是 DETERSIVO采购
    # 里面的，在 DETERSIVO 页顶栏右边进；Griglia 挪到「分析」；设置不弹菜单（运行日志在系统设置里）
    RAIL_MENU = {"buy": ["store_orders", "store_pdv", "order"], "settings": ["settings"]}
    DETER_PAGES = ("order", "result", "history", "rules", "search")

    def __init__(self):
        super().__init__()
        self._boot_times = []                   # 启动用时：每页建了多久（_finish_startup 写进日志）
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setWindowIcon(app_icon())
        theme.load_fonts()                       # 程序自带的 Inter / JetBrains Mono（设计稿的字体）
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
        self.bridge.price_check_done.connect(lambda res: PriceCheckDialog(self, res).exec())
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
        self._last_sub = {}
        rv.addWidget(self._build_topbar())
        rv.addWidget(self._build_tabstrip())
        self.stack = QStackedWidget()
        self.stack.setObjectName("Content")
        rv.addWidget(self.stack, 1)
        h.addWidget(right, 1)
        self._root_h = h                         # 采购助手面板（第一次打开时才建）放在最右边
        self.agent_panel = None

        from dashboard_page import DashboardPage
        from legacy_pages import LegacyPage
        from settings_page import SettingsPage
        self.store_orders = StoreOrderPage(self, settings, _UI)
        from store_order_page import PdvOrderPage
        self.store_pdv = PdvOrderPage(self, settings, _UI)
        self.order_check = OrderCheck(self, settings, _UI)
        self.dashboard = DashboardPage(self, settings)
        self.settings_page = SettingsPage(self, settings)
        self.distribution = self.import_page = None     # 分货 / 导入页要用 pandas（加载要 1 秒左右）：第一次建页面时才加载
        from griglia_page import GrigliaPage
        self.griglia_page = GrigliaPage(self, settings, _UI)
        from tracking_page import TrackingPage
        self.tracking = TrackingPage(self, settings, _UI)
        from volantino_page import VolantinoPage
        self.volantino = VolantinoPage(self, settings, _UI)
        from sellout_page import SelloutPage
        self.sellout = SelloutPage(self, settings, _UI)
        builders = {
            "distribution": lambda: (self._make_distribution().build(), True),
            "import": lambda: (self._make_import().build(), True),
            "home": lambda: (self.dashboard.build(), True),
            "store_orders": lambda: (self.store_orders.build(), False),
            "store_pdv": lambda: (self.store_pdv.build(), False),
            "order": lambda: (self._page_order(), False),
            "result": lambda: (self._page_result(), True),
            "history": lambda: (self._page_history(), True),
            "rules": lambda: (self._page_rules(), True),
            "search": lambda: (self._page_missing(), True),
            "griglia": lambda: (self.griglia_page.build(), False),
            "tracking": lambda: (self.tracking.build(), False),
            "volantino": lambda: (self.volantino.build(), False),
            "sellout": lambda: (self.sellout.build(), False),
            "settings": lambda: (self.settings_page.build(), True),
            "log": lambda: (self._page_log(), True),
        }
        # 打开程序时只建工作台（和它要用的总仓采购），窗口先出来；其他页面等窗口出来以后一页一页建（2026-11-04 提速）。
        # 窗口出来以前 / 页面没建完时点了别的页面：先把剩下的一次建完（_ensure_all）再切过去
        self._builders = builders
        self._LegacyPage = LegacyPage
        self.pages = [None] * len(self.NAV)
        self.legacy_pages = []
        self._startup_done = False
        for _ in self.NAV:
            self.stack.addWidget(QWidget())                 # 先占位
        if settings.get("theme_follow"):                  # 外观「跟随系统」：打开时按 Windows 现在的深浅色
            settings["theme"] = self._system_scheme()
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._scheme_changed)
        except Exception:
            pass
        theme.set_theme(settings.get("theme", "dark"))     # 主题先套上：第一页出来就是对的样子
        QApplication.instance().setStyleSheet(build_qss())
        self._qss_theme = settings.get("theme", "dark")
        keys = [k for k, _ in self.NAV]
        for key in ("store_orders", "home"):
            self._build_page(keys.index(key))

        self.toast = QLabel(self)
        self.toast.setObjectName("Toast")
        self.toast.hide()
        self.toast_timer = QTimer(self, singleShot=True, timeout=self.toast.hide)

        self.tick = QTimer(self, interval=1000, timeout=self._tick)

        QShortcut(QKeySequence("Ctrl+O"), self, activated=lambda: open_path(self.ws_root()))
        QShortcut(QKeySequence("F5"), self, activated=lambda: (self._ensure_all(), self.start_check()))
        QShortcut(QKeySequence("F6"), self, activated=lambda: (self._ensure_all(), self.start()))

        self.go_page("home")
        QTimer.singleShot(0, self._build_next)
        # 提前读大库（openpyxl / pandas …）放到页面全建完以后（_finish_startup）：启动时和建页面、读 SCHEDA 抢时间（11-06 实测）

    # ---------- 页面分批建（打开程序时窗口先出来）----------
    def _build_page(self, i):
        if self.pages[i] is not None:
            return
        key = self.NAV[i][0]
        t_page = time.perf_counter()
        if key in self._builders:
            pg, scroll = self._builders[key]()
        else:
            lp = self._LegacyPage(self, settings, key)
            self.legacy_pages.append(lp)
            pg, scroll = lp.build(), True
        self.pages[i] = pg
        # 设计稿：页面名字只在顶栏写一次；页面里自己的大标题藏起来（说明 ⓘ 挪到顶栏标题的鼠标提示）
        self._page_tips = getattr(self, "_page_tips", {})
        for lb in pg.findChildren(QLabel, "PageTitle"):
            self._page_tips[key] = lb.toolTip()
            lb.hide()
        if key in self.FROZEN_PAGES:                   # 用户 2026-11-15：这两页很喜欢，照旧样式
            theme.freeze(pg)
        getattr(self, "_boot_times", []).append((key, time.perf_counter() - t_page))
        cur = self.stack.currentIndex()
        old = self.stack.widget(i)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.stack.insertWidget(i, self._scroll(pg) if scroll else pg)
        if cur == i:
            self.stack.setCurrentIndex(i)

    FROZEN_PAGES = ()     # 2026-11-15 第 3 轮（用户：总仓 / 门店采购也照新设计）：不再套旧样式
    LAZY_PAGES = ("distribution", "import")      # 要加载 pandas 的页面：点进去才建（后台先把 pandas 读好）

    def _build_next(self):
        """窗口出来以后：一次建一页，每页之间让界面喘口气；全建完再做启动时的其余工作"""
        if self._startup_done:
            return
        todo = [i for i, pg in enumerate(self.pages) if pg is None and self.NAV[i][0] not in self.LAZY_PAGES]
        total = sum(1 for k, _ in self.NAV if k not in self.LAZY_PAGES)
        if todo:
            done = total - len(todo)
            nm = self._tab_label(self.NAV[todo[0]][0])
            self.bg_pill.set_manual(tr("正在准备页面 {0}/{1}", done + 1, total), 100.0 * done / max(1, total),
                                    tr("打开程序后一页页准备，现在：{0}\n准备好以前点别的页面会先等它准备完", nm))
            self._build_page(todo[0])
            QTimer.singleShot(0, self._build_next)
            return
        self.bg_pill.set_manual(tr("正在准备页面 {0}/{1}", total, total), 100.0, "")
        self._finish_startup()
        self.bg_pill.set_manual(None, 0)

    def _log_boot_times(self, steps):
        """启动用时写进日志（只写给维护的人看，不翻译）：总共多久、窗口什么时候出来的、每页建了多久、收尾每一步"""
        total = time.perf_counter() - _BOOT_T0
        shown = getattr(self, "_boot_shown", None)
        pages = sorted(getattr(self, "_boot_times", []), key=lambda x: -x[1])
        slow = " ".join(f"{k} {v:.1f}s" for k, v in pages if v >= 0.1) or "都 < 0.1s"
        tail = " ".join(f"{k} {v:.1f}s" for k, v in steps if v >= 0.1) or "都 < 0.1s"
        file_logger.info(f"启动用时 {total:.1f} 秒"
                         + (f"（窗口 {shown:.1f} 秒出来）" if shown is not None else "")
                         + f" · 建页面 {sum(v for _k, v in pages):.1f} 秒：{slow} · 收尾：{tail}")

    def _ensure_all(self):
        """要用到别的页面了（切页面、快捷键、从别处调过来）：没建完的一次建完"""
        if self._startup_done:
            return
        from erp_widgets import StepsDialog
        todo = [i for i, pg in enumerate(self.pages) if pg is None and self.NAV[i][0] not in self.LAZY_PAGES]
        steps = [(self._tab_label(self.NAV[i][0]), lambda i=i: self._build_page(i)) for i in todo]
        steps.append((tr("收尾"), self._finish_startup))
        # 页面还没准备完就点了别的页面：一次准备完，看得见进度（11-18 设计规范：会卡的地方都要有进度条）
        StepsDialog.run(self, tr("正在准备页面"), steps, show=self.isVisible() and len(steps) > 2)
        if hasattr(self, "bg_pill"):
            self.bg_pill.set_manual(None, 0)

    def _finish_startup(self):
        if self._startup_done:
            return
        self._startup_done = True
        steps = []
        t = time.perf_counter()

        def mark(name):
            nonlocal t
            now = time.perf_counter()
            steps.append((name, now - t))
            t = now
        import reorder_service as _RS
        if settings.get("last_folder") and os.path.isdir(settings["last_folder"]) and _RS.batch_root_of(settings["last_folder"]):
            self.folder_edit.setText(settings["last_folder"])      # 上次在看的门店比价文件夹
        mark("上次文件夹")
        self.refresh_folder()
        mark("refresh_folder")
        self._purge_history()
        mark("purge_history")
        self._load_saved_result()
        mark("load_saved_result")
        self.refresh_history()
        mark("refresh_history")
        # 样式表开头已经套过（套一次要零点几秒）；这期间换过深浅色才再套
        self.apply_theme(qss=settings.get("theme", "dark") != self._qss_theme)
        mark("apply_theme")
        QTimer.singleShot(5000, lambda: threading.Thread(target=_preload_libs, daemon=True).start())
        __import__("erp_widgets").EXCEL_WORKER.start()      # Excel 后台线程开程序时就起好，以后不再新开
        self.log(tr("{0} {1} 启动 {2:%Y-%m-%d %H:%M:%S}", APP_NAME, APP_VERSION, datetime.now()))
        self._log_boot_times(steps)
        QTimer.singleShot(300, self.check_db)
        self.db_timer = QTimer(self, interval=5 * 60 * 1000, timeout=self.check_db)
        self.db_timer.start()
        from update_ui import Updater
        self.updater = Updater(self, settings)
        self.updater.start_auto()
        if self.NAV[self.stack.currentIndex()][0] == "home":
            self.dashboard.refresh()

    def _make_distribution(self):
        from distribution_page import DistributionPage
        self.distribution = DistributionPage(self, settings, _UI)
        return self.distribution

    def _make_import(self):
        from import_page import ImportPage
        self.import_page = ImportPage(self, settings, _UI)
        return self.import_page

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
        lay.setContentsMargins(28, 24, 28, 28)
        lay.setSpacing(16)
        t = QLabel(title + ("  ⓘ" if sub else ""))      # 说明收进鼠标提示（界面要简洁）
        t.setObjectName("PageTitle")
        if sub:
            t.setToolTip(sub)
        head = QVBoxLayout()
        head.setSpacing(3)
        head.addWidget(t)
        lay.addLayout(head)
        lay.addSpacing(2)
        return w, lay

    def _tab_label(self, key):
        """标签 / 顶栏标题用的页面名字"""
        if key == "order":
            return tr("DETERSIVO采购")
        for subs in self.SUB_PAGES.values():
            for k, t in subs:
                if k == key:
                    return t
        return dict(self.NAV).get(key, key)

    def _rail_of(self, key):
        for rid, _t, _i, pages in self.RAIL:
            if key in pages:
                return rid
        return "home"

    def _build_sidebar(self):
        """左边图标栏（76 宽）：品牌方块 + 六个入口（设置放最下面）"""
        side = QFrame()
        side.setObjectName("Rail")
        side.setFixedWidth(76)
        v = QVBoxLayout(side)
        v.setContentsMargins(8, 14, 8, 14)
        v.setSpacing(4)
        logo = QLabel("C&&T".replace("&&", "&"))
        logo.setObjectName("RailLogo")
        logo.setFixedSize(52, 42)
        logo.setAlignment(Qt.AlignCenter)
        logo.setToolTip(f"{APP_NAME} {APP_VERSION}")
        v.addWidget(logo, 0, Qt.AlignHCenter)
        v.addSpacing(14)
        self.rail_group = QButtonGroup(self)
        self.rail_group.setExclusive(True)
        self.rail_btns = {}
        self.nav_badges = {}
        for rid, text, _ic, pages in self.RAIL:
            if rid == "settings":
                v.addStretch()
            b = QToolButton()
            b.setObjectName("RailBtn")
            b.setText(text)
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(22, 22))
            b.setFixedSize(60, 58)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(" · ".join(self._tab_label(k) for k in pages))
            b.clicked.connect(lambda _=False, r=rid: self._rail_clicked(r))
            self.rail_group.addButton(b)
            self.rail_btns[rid] = b
            badge = QLabel("", b)
            badge.setObjectName("RailBadge")
            badge.setFixedHeight(18)
            badge.setMinimumWidth(18)
            badge.setAlignment(Qt.AlignCenter)
            badge.move(38, 2)
            badge.hide()
            for k in pages:
                self.nav_badges[k] = badge
            v.addWidget(b, 0, Qt.AlignHCenter)
        # 最下面：头像（点开：通知 / 深浅色 / 语言）——设计稿里顶栏只放搜索、数据库状态、助手
        name = settings.get("user_name") or tr("采购部")
        self.avatar = QToolButton()
        self.avatar.setObjectName("RailAvatar")
        self.avatar.setText(name[:1])
        self.avatar.setFixedSize(36, 36)
        self.avatar.setCursor(Qt.PointingHandCursor)
        self.avatar.setToolTip(f"{name} · {settings.get('user_site') or tr('BMS 总仓')}")
        self.avatar.clicked.connect(self._avatar_menu)
        v.addSpacing(6)
        v.addWidget(self.avatar, 0, Qt.AlignHCenter)
        self.bell_dot = QLabel(self.avatar)
        self.bell_dot.setFixedSize(9, 9)
        self.bell_dot.move(27, 0)
        self.bell_dot.hide()
        return side

    def _avatar_menu(self):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        n = len(self.notes)
        m.addAction(icon("spark", T("muted"), 16), tr("采购助手"), lambda: self.toggle_agent(True))
        m.addAction(icon("bell", T("muted"), 16), tr("通知（{0}）", n) if n else tr("通知"), self._show_notes)
        dark = settings.get("theme") == "dark"
        m.addAction(icon("sun" if dark else "moon", T("muted"), 16), tr("浅色模式") if dark else tr("深色模式"),
                    self.toggle_theme)
        lm = m.addMenu(tr("语言"))
        for code, label in LANGS:
            a = lm.addAction(label, lambda c=code: self.lang_box.setCurrentIndex(self.lang_box.findData(c)))
            a.setCheckable(True)
            a.setChecked(code == get_language())
        m.addSeparator()
        m.addAction(icon("settings", T("muted"), 16), tr("系统设置"), lambda: self.go_page("settings"))
        m.exec(self.avatar.mapToGlobal(self.avatar.rect().topRight()))

    def _paint_rail(self):
        for rid, _t, ic, _p in self.RAIL:
            b = self.rail_btns[rid]
            b.setIcon(icon(ic, "#FFFFFF" if b.isChecked() else T("side_text"), 22))

    def _rail_clicked(self, rid):
        """一个入口只有一页：直接过去；有几页（采购 / 数据 / 设置）：旁边弹出一个小菜单选哪一页（设计稿里没有那排标签）"""
        pages = self._rail_menu(rid)
        cur = self.NAV[self.stack.currentIndex()][0]
        self.rail_btns[self._rail_of(cur)].setChecked(True)      # 还没换页：选中的还是现在这个入口
        if len(pages) == 1:
            self.go_page(pages[0])
            return
        b = self.rail_btns[rid]
        self._flyout(pages, b.mapToGlobal(b.rect().topRight()) + QPoint(10, 0))

    def _flyout(self, pages, pos):
        from PySide6.QtWidgets import QMenu
        cur = self.NAV[self.stack.currentIndex()][0]
        m = QMenu(self)
        m.setObjectName("Flyout")
        m.setWindowFlags(m.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        m.setAttribute(Qt.WA_TranslucentBackground)
        for k in pages:
            label = tr("本周 DETERSIVO 采购") if (k == "order" and pages[0] == "order") else self._tab_label(k)
            a = m.addAction(label, lambda kk=k: self.go_page(kk))
            a.setCheckable(True)
            a.setChecked(k == cur or (k == "order" and cur in self.DETER_PAGES and "result" not in pages))
        m.exec(pos)

    def _rail_menu(self, rid):
        """点图标栏这个入口时小菜单里列哪几页"""
        return self.RAIL_MENU.get(rid) or dict((r, p) for r, _t, _i, p in self.RAIL)[rid]

    def _crumb_menu(self):
        cur = self.NAV[self.stack.currentIndex()][0]
        if cur in self.DETER_PAGES:                 # DETERSIVO 里面：列它自己的几页
            pages = list(self.DETER_PAGES)
        else:
            pages = self._rail_menu(self._rail_of(cur))
        if len(pages) > 1:
            self._flyout(pages, self.top_eyebrow.mapToGlobal(self.top_eyebrow.rect().bottomLeft()) + QPoint(0, 6))

    def _deter_more_menu(self):
        """DETERSIVO 页顶栏「⋯」：历史记录 / 缺失查找 / 起订规则"""
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        for k in ("history", "search", "rules"):
            m.addAction(self._tab_label(k), lambda kk=k: self.go_page(kk))
        b = self.top_deter_more
        m.exec(b.mapToGlobal(b.rect().bottomRight()) - QPoint(m.sizeHint().width(), -6))

    def _build_tabstrip(self):
        """一个入口里有几页（采购 / 数据 / 设置）：顶栏下面一排标签"""
        bar = QFrame()
        bar.setObjectName("TabStrip")
        bar.setFixedHeight(44)
        h = QHBoxLayout(bar)
        h.setContentsMargins(28, 0, 28, 0)
        h.setSpacing(26)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.tab_btns = {}
        for _rid, _t, _i, pages in self.RAIL:
            if len(pages) < 2:
                continue
            for k in pages:
                b = QPushButton(self._tab_label(k).replace("&", "&&"))
                b.setObjectName("TabBtn")
                b.setCheckable(True)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, kk=k: self.go_page(kk))
                self.tab_group.addButton(b)
                self.tab_btns[k] = b
                h.addWidget(b)
                b.hide()
        h.addStretch()
        self.tabstrip = bar
        return bar

    def _build_topbar(self):
        from PySide6.QtWidgets import QCompleter, QMenu, QSizePolicy
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(64)
        h = QHBoxLayout(bar)
        h.setContentsMargins(28, 0, 20, 0)
        h.setSpacing(12)
        tc = QVBoxLayout()
        tc.setSpacing(1)
        self.top_eyebrow = QPushButton("")
        self.top_eyebrow.setObjectName("TopCrumb")
        self.top_eyebrow.clicked.connect(self._crumb_menu)
        self.top_title = QLabel("")
        self.top_title.setObjectName("TopTitle")
        tc.addWidget(self.top_eyebrow)
        tc.addWidget(self.top_title)
        tw = QWidget()
        tw.setLayout(tc)
        tw.setMinimumWidth(150)
        h.addWidget(tw)
        h.addSpacing(8)
        self.search = _SearchBox()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText(tr("搜页面，或直接告诉助手要做什么…"))
        self.search.setToolTip(tr("输入页面名字回车直接过去；输入别的（例如「今天该订什么」）回车交给采购助手"))
        self.search.setMinimumWidth(240)
        self.search.setMaximumWidth(460)
        self.search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.search_icon = QLabel(self.search)
        self.search_icon.setFixedSize(16, 16)
        self.search_icon.move(13, 12)
        self.search_key = self.search.hint
        comp = QCompleter([t for _, t in self.NAV], self.search)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.activated.connect(self._search_go)
        self.search.setCompleter(comp)
        self.search.returnPressed.connect(lambda: self._search_go(self.search.text()))
        h.addWidget(self.search, 1)
        self.search.opened.connect(self.open_palette)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: self.open_palette(""))
        h.addStretch()
        from erp_widgets import BgTaskPill
        self.bg_pill = BgTaskPill(bar)          # 后台运行中的任务：进度 + 点一下看详情（没有时藏起来）
        h.addWidget(self.bg_pill)
        # 数据库状态（以前在侧边栏左下角）：一个小胶囊，点了去系统设置
        self.db_pill = QFrame()
        self.db_pill.setObjectName("DbPill")
        self.db_pill.setFixedHeight(32)
        self.db_pill.setCursor(Qt.PointingHandCursor)
        self.db_pill.mousePressEvent = lambda _e: self.go_page("settings")
        dp = QHBoxLayout(self.db_pill)
        dp.setContentsMargins(12, 0, 14, 0)
        dp.setSpacing(8)
        self.db_dot = QLabel()
        self.db_dot.setFixedSize(8, 8)
        self.db_title = QLabel(tr("正在检查数据库…"))
        self.db_title.setObjectName("DbTitle")
        self.db_sub = QLabel("")                # 数据库地址：放进鼠标提示
        self.db_sub.hide()
        dp.addWidget(self.db_dot)
        dp.addWidget(self.db_title)
        self.top_slot = QWidget()                # 页面自己放进顶栏的东西（订单跟踪：同步状态 · 视图切换 · 邮件核对 · ⋯）
        self.top_slot_lay = QHBoxLayout(self.top_slot)
        self.top_slot_lay.setContentsMargins(0, 0, 0, 0)
        self.top_slot_lay.setSpacing(10)
        self.top_slot.hide()
        h.addWidget(self.top_slot)
        h.addWidget(self.db_pill)
        self.agent_btn = QPushButton(tr("✦ 助手"))
        self.agent_btn.setObjectName("AgentBtn")
        self.agent_btn.setCursor(Qt.PointingHandCursor)
        self.agent_btn.setToolTip(tr("让助手查供应商、算建议量、生成采购订单、查订单、核对邮件；改数据前都会问你"))
        self.agent_btn.clicked.connect(lambda: self.toggle_agent())
        h.addWidget(self.agent_btn)
        self.top_set_btn = QPushButton(tr("设置"))
        self.top_set_btn.setMinimumHeight(36)
        self.top_set_btn.setCursor(Qt.PointingHandCursor)
        self.top_set_btn.setToolTip(tr("比价开关、起订规则、供应商名称、门店间调货、检查用的订货周期和算法"))
        self.top_set_btn.clicked.connect(lambda: (self._ensure_all(), self.open_cmp_settings()))
        self.top_set_btn.hide()
        # DETERSIVO 里面的几页（2026-11-16 用户：不跟总仓 / 门店采购排在一起）：比价结果 · ⋯（历史记录 / 缺失查找 / 起订规则）
        self.top_deter_back = QPushButton("←  " + tr("本周采购"))
        self.top_deter_back.setMinimumHeight(36)
        self.top_deter_back.setCursor(Qt.PointingHandCursor)
        self.top_deter_back.setToolTip(tr("回到本周 DETERSIVO 采购（四步）"))
        self.top_deter_back.clicked.connect(lambda: self.go_page("order"))
        self.top_deter_res = QPushButton(tr("比价结果"))
        self.top_deter_res.setMinimumHeight(36)
        self.top_deter_res.setCursor(Qt.PointingHandCursor)
        self.top_deter_res.setToolTip(tr("这一批每家门店的比价结果：分给哪家批发商、缺失、订单文件"))
        self.top_deter_res.clicked.connect(lambda: self.go_page("result"))
        self.top_deter_more = QPushButton("⋯")
        self.top_deter_more.setFixedSize(40, 36)
        self.top_deter_more.setCursor(Qt.PointingHandCursor)
        self.top_deter_more.setToolTip(tr("历史记录 · 缺失查找 · 起订规则"))
        self.top_deter_more.clicked.connect(self._deter_more_menu)
        for b in (self.top_deter_back, self.top_deter_res, self.top_deter_more):
            b.hide()
            h.addWidget(b)
        h.addWidget(self.top_set_btn)

        # 通知 / 深浅色 / 语言挪到左下角头像的菜单里（对象留着，别的代码还在用）
        self.bell = QPushButton(bar)
        self.bell.hide()
        self.bell_menu = QMenu(self)
        self.theme_btn = QPushButton(bar)
        self.theme_btn.hide()
        self.lang_box = QComboBox(bar)
        for code, label in LANGS:
            self.lang_box.addItem(label, code)
        self.lang_box.setCurrentIndex(max(0, self.lang_box.findData(get_language())))
        self.lang_box.currentIndexChanged.connect(self.change_language)
        self.lang_box.hide()
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
        # 不是页面名字：交给采购助手（用户 2026-11-15 定的新界面：一个框，搜页面或直接说要做什么）
        raw = self.search.text().strip()
        self.search.clear()
        self.search.clearFocus()
        self.toggle_agent(True)
        if self.agent_panel is not None and raw:
            self.agent_panel.send(raw)

    # ---------- Ctrl K 指令面板 ----------
    def _palette_actions(self):
        return [("mail_check", tr("邮件核对"), tr("读邮箱最近 21 天，看哪些单发了 / 确认了 / 发货了"), "邮件 email mail 核对 确认 发货"),
                ("sync", tr("同步 Excel"), tr("订单跟踪 ↔ PIANO 表，马上同步一次"), "excel piano 同步 sync"),
                ("new_order", tr("登记新订单…"), tr("在订单跟踪里登记一张单"), "新 订单 登记 order"),
                ("start_check", tr("开始本周 DETERSIVO 采购"), tr("收集文件 → 审核清单 → 比价分配 → 下单发送"), "detersivo 检查 清单 比价"),
                ("store_orders", tr("计算总仓采购"), tr("读 SCHEDA 和销量，算每家供应商的订货量"), "总仓 计算 scheda lucas"),
                ("theme", tr("换深色 / 浅色"), "", "主题 深色 浅色 theme dark light"),
                ("db", tr("测试数据库连接"), "", "数据库 db 连接"),
                ("agent_open", tr("打开采购助手"), tr("会改数据的每一步都先问你"), "助手 agent ai")]

    def _palette_rows(self):
        tp = getattr(self, "tracking", None)
        if tp is None or getattr(tp, "st", None) is None:
            return []
        if tp.rows:
            return tp.rows
        import order_tracking as OT
        return OT.rows(tp.st)

    def open_palette(self, text=""):
        from command_palette import CommandPalette
        rail = {r: t for r, t, _i, _p in self.RAIL}
        pages = [(k, t, rail.get(self._rail_of(k), "")) for k, t in self.NAV]
        dlg = CommandPalette(self, pages, self._palette_rows, self._palette_actions(), self._palette_run,
                             self._palette_ask, text)
        dlg.exec()

    def _palette_ask(self, text):
        self.toggle_agent(True)
        if self.agent_panel is not None and text:
            self.agent_panel.send(text)

    def _palette_run(self, it):
        key, data = it["key"], it.get("data")
        self._ensure_all()
        tp = getattr(self, "tracking", None)
        if key == "page":
            self.go_page(data)
        elif key == "agent":
            self._palette_ask(data)
        elif key == "agent_open":
            self.toggle_agent(True)
        elif key == "theme":
            self.toggle_theme()
        elif key == "db":
            self.check_db()
            self.show_toast(tr("正在测试数据库连接…"))
        elif key == "start_check":
            self.go_page("order")
            if hasattr(self, "_cmp_show"):
                self._cmp_show(0)
        elif key == "store_orders":
            self.go_page("store_orders")
        elif tp is not None:
            self.go_page("tracking")
            if key == "send" and data:
                tp.send_order(data)
            elif key in ("transit", "supplier"):
                tp._show_view(1)
                tp.week_sel = None
                tp.f_month.setCurrentIndex(0)
                tp.search.setText(data or "")
                i = tp.f_view.findData("open" if key == "transit" else "all")
                if i >= 0:
                    tp.f_view.setCurrentIndex(i)
            elif key == "mail_check":
                tp.check_mail()
            elif key == "sync":
                tp.sync_excel(manual=True)
            elif key == "new_order":
                tp.new_order()

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
        m.exec(self.avatar.mapToGlobal(self.avatar.rect().topRight()))

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
        b.adjustSize()
        b.setFixedWidth(max(18, b.sizeHint().width()))
        b.move(60 - b.width() - 2, 2)
        b.setVisible(bool(value))

    # ---------- 数据库状态 ----------
    def _set_db_state(self, ok, sub=None):
        self._db_ok = ok
        import reorder_service as R
        upto = getattr(self, "_db_sales_upto", None) if ok else None
        late = R.stale_days(upto)
        stale = late is not None and late > R.STALE_DAYS
        color = (T("warn") if stale else T("ok")) if ok else ("#E7A23A" if ok is None else T("err"))
        self.db_dot.setStyleSheet(f"background:{color}; border-radius:4px;")
        c = QColor(color)
        self.db_pill.setStyleSheet(f"#DbPill {{ background: rgba({c.red()},{c.green()},{c.blue()},0.10); "
                                   f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.35); border-radius: 16px; }}"
                                   f"#DbTitle {{ color: {color}; font-size: 12px; font-weight: 600; }}")
        if stale:            # 连上了，但数据停在几天前（诊断 36：同步停了一周，配货 / 检查清单都会用旧销量、旧库存）
            self.db_title.setText(tr("⚠ 数据只到 {0}", upto.strftime("%d/%m")))
            self.db_title.setToolTip(tr("数据库里最新的销售是 {0}，已经 {1} 天没同步：销量、库存都是那天的，配货和检查清单会按旧数据算。请联系负责数据库同步的人。",
                                        upto.strftime("%d/%m/%Y %H:%M"), late))
        else:
            self.db_title.setText(tr("数据库已连接") if ok else (tr("正在检查数据库…") if ok is None else tr("数据库未连接")))
            self.db_title.setToolTip("")
        if sub is not None:
            self.db_sub.setText(sub)
        self.db_pill.setToolTip("\n".join(x for x in (self.db_title.toolTip(), self.db_sub.text()) if x))

    def _on_db_state(self, ok):
        self._set_db_state(ok)
        import reorder_service as R
        upto = getattr(self, "_db_sales_upto", None)
        late = R.stale_days(upto) if ok else None
        if late is not None and late > R.STALE_DAYS and not getattr(self, "_stale_warned", False):
            self._stale_warned = True
            self.log(tr("⚠ 数据库里最新的销售是 {0}，已经 {1} 天没同步：销量和库存都是那天的", upto.strftime("%d/%m/%Y %H:%M"), late))
            try:
                self.show_toast(tr("⚠ 数据库数据只到 {0}（{1} 天没同步）", upto.strftime("%d/%m"), late))
            except Exception:
                pass
        if hasattr(self, "_log_health"):
            __import__("log_page")._health(self)
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
                try:                                   # 数据库是不是停止同步了：最新一条销售是哪天
                    self._db_sales_upto = R.sales_latest(db)
                except Exception:
                    self._db_sales_upto = None
                db.engine.dispose()
                ok = True
            except Exception:
                ok = False
            self.bridge.db_state.emit(ok)

        threading.Thread(target=work, daemon=True).start()

    def toggle_agent(self, show=None):
        """右边的采购助手面板：打开 / 收起（第一次打开时才建）"""
        if self.agent_panel is None:
            if show is False:
                return
            self._ensure_all()
            from agent_panel import AgentPanel
            self.agent_panel = AgentPanel(self, settings)
            self._root_h.addWidget(self.agent_panel)
            self.agent_panel.show()
            self._fit_topbar()
            self.agent_panel.input.setFocus()
            return
        vis = (not self.agent_panel.isVisible()) if show is None else bool(show)
        self.agent_panel.setVisible(vis)
        self._fit_topbar()
        if vis:
            self.agent_panel.input.setFocus()

    def _fit_topbar(self):
        """助手面板开着时顶栏窄了：搜索框缩短（1366 宽的笔记本也放得下）"""
        on = self.agent_panel is not None and self.agent_panel.isVisible()
        self.search.setMaximumWidth(260 if on else 460)

    def go_page(self, key):
        self.go([k for k, _ in self.NAV].index(key))

    def go(self, i):
        """i = 页面在 NAV 里的位置"""
        if self.NAV[i][0] == "result" and getattr(self, "_redirecting", False) is False:
            # 比价结果是 DETERSIVO 的第 4 步（2026-11-18）：到 DETERSIVO 页、切到那一步
            self._redirecting = True
            try:
                self.go_page("order")
                self._cmp_show(3)
            finally:
                self._redirecting = False
            return
        if not self._startup_done and self.NAV[i][0] != "home":
            self._ensure_all()
        if self.pages[i] is None:
            self._build_page(i)
        if self.NAV[i][0] in getattr(self, "_theme_due", ()):      # 换过颜色以后第一次来这一页：先刷新颜色
            self._restyle_key(self.NAV[i][0])
        self.stack.setCurrentIndex(i)
        key, text = self.NAV[i]
        if key == "log" and getattr(self, "_log_dirty", False):
            QTimer.singleShot(0, self._render_log)
        if key == "history" and getattr(self, "_hist_dirty", False):
            __import__("history_page").render(self, force=True)
        rid = self._rail_of(key)
        rail = {r: (t, p) for r, t, _i, p in self.RAIL}
        self.rail_btns[rid].setChecked(True)
        pages = rail[rid][1]
        deter = key in self.DETER_PAGES
        multi = len(self._rail_menu(rid)) > 1
        for k, b in self.tab_btns.items():
            b.setVisible(multi and k in pages)
        if key in self.tab_btns:
            self.tab_btns[key].setChecked(True)
        self.tabstrip.setVisible(False)             # 设计稿没有这一排：换页点左边图标（弹小菜单）或顶栏小标
        if key == "home":
            _wd = [tr("周一"), tr("周二"), tr("周三"), tr("周四"), tr("周五"), tr("周六"), tr("周日")][datetime.now().weekday()]
            self.top_eyebrow.setText(f"{_wd} · {datetime.now():%d/%m/%Y}")
        else:
            sub = "DETERSIVO" if deter else self._tab_label(key)
            self.top_eyebrow.setText(rail[rid][0] + (f" / {sub}  ▾" if multi else ""))
        self.top_eyebrow.setCursor(Qt.PointingHandCursor if multi else Qt.ArrowCursor)
        self.top_eyebrow.setToolTip(tr("点一下换到这个入口里的别的页面") if multi else "")
        title = tr("本周 DETERSIVO 采购 · {0:%d/%m}", datetime.now()) if key == "order" else self._tab_label(key)
        self.top_title.setText(title + ("  ⓘ" if getattr(self, "_page_tips", {}).get(key) else ""))
        own = deter                                # 设计稿：DETERSIVO 页顶栏右边只有「设置」（+ 里面几页的入口）
        self.top_deter_back.setVisible(deter and key != "order")
        self.top_deter_res.setVisible(False)          # 比价结果现在是第 4 步，顶栏不再单独放
        self.top_deter_more.setVisible(deter)
        tw = getattr(getattr(self, "tracking", None), "top_widget", None) if key == "tracking" else None
        for k in range(self.top_slot_lay.count()):
            wdg = self.top_slot_lay.itemAt(k).widget()
            if wdg is not None and wdg is not tw:
                wdg.hide()
        if tw is not None:
            if tw.parent() is not self.top_slot:
                self.top_slot_lay.addWidget(tw)
            tw.show()
        self.top_slot.setVisible(tw is not None)
        self.top_set_btn.setVisible(own)
        self.db_pill.setVisible(not own and tw is None)     # 设计稿：订单页顶栏右边是 Excel 同步状态 + 邮件核对
        self.agent_btn.setVisible(not own and tw is None)
        self.top_title.setToolTip(getattr(self, "_page_tips", {}).get(key, ""))
        self._last_sub[rid] = key
        self._paint_rail()
        if key == "home" and hasattr(self, "dashboard"):
            self.dashboard.refresh()
        if key == "order" and hasattr(self, "price_mail"):
            self.price_mail.maybe_check(self.ws_root())      # 后台看邮件里有没有新报价（30 分钟最多一次）

    # 每一页的「页面对象」（有 restyle()）：换颜色时只马上刷新正在看的这一页，别的页第一次切过去时再刷新
    PAGE_OBJ = {"home": "dashboard", "store_orders": "store_orders", "store_pdv": "store_pdv",
                "settings": "settings_page", "order": "order_check", "distribution": "distribution",
                "import": "import_page", "griglia": "griglia_page", "tracking": "tracking",
                "volantino": "volantino", "sellout": "sellout"}

    def apply_theme(self, qss=True):
        """
        换深浅色 / 主色（2026-11-18 用户：换颜色会卡住、还没有进度条）：
        ① 套全局样式时把全程序的事件过滤器（AppEventHub）停掉 —— 以前几万个刷新事件每个都进 5 次 Python；
        ② 只马上刷新顶栏 / 图标栏和正在看的这一页，别的页记在 _theme_due，切过去时（go）再刷新；
        ③ 一步步做，进度窗口看得见（StepsDialog）。
        """
        if not getattr(self, "_startup_done", True):
            return                                       # 页面还没建完：建完时会套一次
        theme.set_theme(settings.get("theme", "dark"))
        cur = self.NAV[self.stack.currentIndex()][0]
        self._theme_due = {k for k, _ in self.NAV}
        steps = []
        if qss:
            steps.append((tr("套用新颜色"), self._theme_qss))
        steps.append((tr("顶栏和图标栏"), self._theme_chrome))
        steps.append((self._tab_label(cur), lambda: self._restyle_key(cur)))
        from erp_widgets import StepsDialog
        StepsDialog.run(self, tr("正在换颜色"), steps, show=qss and self.isVisible())

    def _theme_qss(self):
        hub = getattr(QApplication.instance(), "_event_hub", None)
        if hub is not None:
            hub.pause()
        try:
            QApplication.instance().setStyleSheet(build_qss())
            theme.refresh_frozen()
        finally:
            if hub is not None:
                hub.resume()

    def _theme_chrome(self):
        self._paint_rail()
        dark = settings.get("theme") == "dark"
        self.theme_btn.setIcon(icon("sun" if dark else "moon", T("muted"), 16))
        self.theme_btn.setToolTip(tr("浅色模式") if dark else tr("深色模式"))
        self.bell.setIcon(icon("bell", T("muted"), 16))
        self.search_icon.setPixmap(icon("search", T("muted"), 15).pixmap(15, 15))
        self.bell_dot.setStyleSheet(f"background:{T('err')}; border-radius:4px; border:1px solid {T('side')};")
        self._set_db_state(getattr(self, "_db_ok", None))
        ap = getattr(self, "agent_panel", None)
        if ap is not None and hasattr(ap, "restyle"):
            ap.restyle()
        self._dark_titlebar()

    def _restyle_key(self, key):
        """刷新一页的颜色（页面自己画的颜色、指标卡小图标…）；全局样式已经套过了"""
        due = getattr(self, "_theme_due", None)
        if due is not None:
            due.discard(key)
        if key == "order" and "result" in (due or ()):
            self._restyle_key("result")               # 比价结果放在 DETERSIVO 第 4 步里
        keys = [k for k, _ in self.NAV]
        i = keys.index(key) if key in keys else -1
        if i < 0 or self.pages[i] is None:
            return
        obj = getattr(self, self.PAGE_OBJ.get(key, ""), None) if key in self.PAGE_OBJ else None
        if obj is not None and hasattr(obj, "restyle"):
            obj.restyle()
        if key == "order":
            for b in (self.banner_folder, self.banner_result, self.banner_rules):
                if b.isVisible():
                    b.restyle()
            self.btn_folder.setIcon(icon("folder", T("text")))
            self.gauge.update()
        mods = {"result": ("_res_qss_page", "result_page"), "history": ("_hist_page", "history_page"),
                "search": ("_miss_page", "missing_page"), "log": ("_log_page", "log_page")}
        if key in mods:
            pg = getattr(self, mods[key][0], None)
            if pg is not None:
                from page_kit import set_qss
                set_qss(pg, __import__(mods[key][1]).qss())
        if key == "search" and hasattr(self, "_miss_items"):
            __import__("missing_page")._render_list(self)
        if key == "log":
            self._render_log()
        if key == "result" and self.last_summary:
            self.show_summary(self.last_summary)
        if key == "rules":
            self.refresh_rules_table()
        from page_kit import Stat
        host = self._res_wrap if key == "result" and getattr(self, "_res_wrap", None) is not None else self.stack.widget(i)
        for st in host.findChildren(Stat):     # 指标卡的小图标颜色
            st.restyle()

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
        settings["theme_follow"] = False
        save_settings(settings)
        self.apply_theme()

    def set_theme_mode(self, mode):
        """系统设置 → 外观：深色 / 浅色 / 跟随系统（Windows 换深浅色时跟着换）"""
        settings["theme_follow"] = mode == "system"
        settings["theme"] = self._system_scheme() if mode == "system" else mode
        save_settings(settings)
        self.apply_theme()

    def _system_scheme(self):
        try:
            cs = QGuiApplication.styleHints().colorScheme()
            if cs == Qt.ColorScheme.Dark:
                return "dark"
            if cs == Qt.ColorScheme.Light:
                return "light"
        except Exception:
            pass
        return settings.get("theme") or "dark"

    def _scheme_changed(self, *_):
        if settings.get("theme_follow"):
            want = self._system_scheme()
            if want != settings.get("theme"):
                settings["theme"] = want
                save_settings(settings)
                self.apply_theme()

    def apply_density(self):
        apply_density_now()

    def show_toast(self, text, ms=2600, action=None):
        """右下角提示；action = (按钮文字, 点了做什么)，例如 (「撤销」, undo)"""
        self.notify(text)
        self._toast_action = action[1] if action else None
        if action:
            import html as _html
            self.toast.setTextFormat(Qt.RichText)
            self.toast.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
            self.toast.setText(f"{_html.escape(text)}&nbsp;&nbsp;<a href='act' style='color:{T('accent')}; font-weight:600;"
                               f" text-decoration:none;'>{_html.escape(action[0])}</a>")
            if not getattr(self, "_toast_linked", False):
                self.toast.linkActivated.connect(self._toast_clicked)
                self._toast_linked = True
        else:
            self.toast.setTextFormat(Qt.PlainText)
            self.toast.setText(text)
        self.toast.adjustSize()
        self.toast.move(self.width() - self.toast.width() - 24, self.height() - self.toast.height() - 76)
        self.toast.raise_()
        self.toast.show()
        self.toast_timer.start(ms)

    def _toast_clicked(self, _link):
        fn, self._toast_action = getattr(self, "_toast_action", None), None
        self.toast.hide()
        if fn:
            fn()

    # ---------- 页面：订单处理 ----------
    def _page_order(self):
        # 2026-11-15 第 3 轮（设计稿「DETERSIVO 采购 · 四步流程」）：顶上四步 收集文件 → 审核清单 → 比价分配 → 下单发送，
        # 下面一步一屏（cmp_stack）；审核清单不再单独开窗口（order_check.window = EmbedHost）
        from erp_widgets import FlowBar, DropZone
        w = QWidget()
        w.setObjectName("Content")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # 2026-11-18（用户）：五步 —— 比价结果也是一步（看完没问题再下单发送）
        self.cmp_steps = FlowBar([(tr("收集文件"), ""), (tr("审核清单"), ""), (tr("比价分配"), ""), (tr("比价结果"), ""),
                                  (tr("下单发送"), "")])
        self.cmp_steps.setToolTip(tr("几家批发商卖的商品差不多：按最低价给每个商品选供应商，帮有起订量的供应商凑单，把订购数量写回各家报价文件。点一步看那一步"))
        self.cmp_steps.clicked.connect(self._cmp_show)
        outer.addWidget(self.cmp_steps)
        self.cmp_stack = QStackedWidget()
        outer.addWidget(self.cmp_stack, 1)
        p0 = QWidget()
        lay = QVBoxLayout(p0)
        lay.setContentsMargins(28, 18, 28, 22)
        lay.setSpacing(14)
        self._S = settings
        import deter_steps as DS
        hold0 = QWidget(p0)                   # 老的工作文件夹卡 / 三个拖放区藏在这里（refresh_ws 照旧填，老代码照用）
        hold0.hide()

        # ---- 工作文件夹：固定一个，程序自动建好四个子文件夹（见 workspace.py）----
        p1 = Panel(tr("工作文件夹"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.ws_label = QLabel("")
        self.ws_label.setObjectName("Faint")
        self.ws_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.ws_label, 1)
        b_open = QPushButton(tr("打开文件夹"))
        b_open.setToolTip(tr("也可以直接在文件夹里放文件：1 门店清单、2 供应商报价、3 扣减文件（每家店一个子文件夹）"))
        b_open.clicked.connect(lambda: open_path(self.ws_root()))
        self.btn_folder = b_open
        row.addWidget(b_open)
        b_move = QPushButton(tr("更改位置"))
        b_move.setObjectName("Link")
        b_move.clicked.connect(self.change_ws_root)
        row.addWidget(b_move)
        b_set = QPushButton(tr("设置"))
        b_set.setToolTip(tr("比价开关、起订规则、供应商名称、门店间调货、检查用的订货周期和算法"))
        b_set.clicked.connect(self.open_cmp_settings)
        row.addWidget(b_set)
        p1.lay.addLayout(row)
        # 当前在看 / 在比价的门店文件夹（程序自己切换，不用选）
        self.folder_edit = QLineEdit()
        self.folder_edit.hide()

        # 三个放文件的地方：拖进来就放进对应的子文件夹（原来的挪进 _旧文件，不删除）
        zones = QHBoxLayout()
        zones.setSpacing(12)
        self.zone_lists = DropZone(tr("门店清单"), "", multi=True, removable=True)
        self.zone_sup = DropZone(tr("供应商报价"), "", multi=True, removable=True)
        self.zone_elim = DropZone(tr("扣减文件 · eliminare"), "", multi=True, removable=True)
        for z, kind in ((self.zone_lists, "lists"), (self.zone_sup, "prices"), (self.zone_elim, "elim")):
            z.setMinimumHeight(130)
            z.open_folder.connect(lambda kind=kind: self._open_ws(kind))
            z.remove_requested.connect(lambda n, kind=kind: self._remove_ws_file(kind, n))
            zones.addWidget(z, 1)
        self.zone_lists.files_dropped.connect(self._drop_lists)
        self.zone_sup.files_dropped.connect(self._drop_suppliers)
        # 邮件里批发商的新价目表（mail_price_dialog）：「✉ 邮件」/「✉ 新报价 n」
        import mail_price_dialog as MPD
        self.price_mail = MPD.PriceMailWatcher(self, settings)
        self.btn_price_mail = QPushButton(tr("✉ 邮件"))
        self.btn_price_mail.setObjectName("Link")
        self.btn_price_mail.clicked.connect(lambda: MPD.open_dialog(self, settings, _UI, self.price_mail, self.ws_root(),
                                                                     after=self.refresh_ws))
        self.price_mail.changed.connect(lambda: MPD.style_button(self.btn_price_mail, self.price_mail, T))
        self.zone_sup.add_link(self.btn_price_mail)
        self.btn_sup_names = QPushButton(tr("供应商名称"))
        self.btn_sup_names.setObjectName("Link")
        self.btn_sup_names.setToolTip(tr("改每个报价文件对应哪家供应商（起订规则、方案排除都按这个名字认）"))
        self.btn_sup_names.clicked.connect(self.edit_supplier_names)
        self.zone_sup.add_link(self.btn_sup_names)
        MPD.style_button(self.btn_price_mail, self.price_mail, T)
        self.zone_elim.files_dropped.connect(self._drop_eliminare)
        p1.lay.addLayout(zones)
        zr = QHBoxLayout()
        b_miss = QPushButton(tr("上周未到货"))
        b_miss.setObjectName("Link")
        b_miss.setToolTip(tr("列出每家店上一次的 DETERSIVO 订单，勾上还没到货的 → 自动变成这家店的扣减文件（不用再手动放）"))
        b_miss.clicked.connect(self.open_deter_arrival)
        zr.addWidget(b_miss)
        zr.addStretch()
        b_chk = QPushButton(tr("报价与数据库核对"))
        b_chk.setObjectName("Link")
        b_chk.setToolTip(tr("只读检查：报价表里的商品数据库有没有、登记的供应商对不对、进价和箱规一样不一样"))
        b_chk.clicked.connect(self.check_prices_vs_db)
        zr.addWidget(b_chk)
        p1.lay.addLayout(zr)
        self.banner_folder = Banner()
        p1.lay.addWidget(self.banner_folder)
        p1.setParent(hold0)
        DS.build_collect(self, lay)            # 设计稿「收集文件」：三张能拖文件进来的卡 + 准备好了吗

        # ---- 一个按钮：检查门店清单 → 确认 → 自动一家一家比价 ----
        foot0 = QFrame()                      # 设计稿：底部一条，左边写现在的状态，右边一个主按钮
        foot0.setObjectName("StatusBar")
        go = QHBoxLayout(foot0)
        go.setContentsMargins(28, 12, 28, 12)
        go.setSpacing(16)
        self.btn_start = QPushButton(tr("开始检查") + "  →")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.setToolTip(tr("检查 1 门店清单 里的所有清单；你核对后点「确认，开始比价」，程序自动一家一家比价（F5）"))
        self.btn_start.clicked.connect(self.start_check)
        self.ws_status = QLabel("")
        self.ws_status.setWordWrap(True)
        go.addWidget(self.ws_status, 1)
        go.addWidget(self.btn_start)
        oc_panel = self.order_check.build()          # 检查清单的部件：参数在里面；审核表格在第 2 步
        oc_panel.hide()
        self._oc_panel = oc_panel
        lay.addWidget(self.order_check.msg)
        lay.addWidget(self.order_check.summary_row)
        p0box = QWidget()
        p0v = QVBoxLayout(p0box)
        p0v.setContentsMargins(0, 0, 0, 0)
        p0v.setSpacing(0)
        p0v.addWidget(self._scroll(p0), 1)
        p0v.addWidget(foot0)
        self.cmp_stack.addWidget(p0box)

        # ---- 第 2 步：审核清单（检查完的表格；还没检查时一个空状态）----
        p1 = QStackedWidget()
        from erp_widgets import EmptyState
        es = QWidget()
        ev = QVBoxLayout(es)
        ev.addStretch()
        e1 = QLabel(tr("还没有检查门店清单"))
        e1.setObjectName("EmptyTitle")
        e1.setAlignment(Qt.AlignCenter)
        ev.addWidget(e1)
        eb = QPushButton(tr("开始检查"))
        eb.setObjectName("Primary")
        eb.setCursor(Qt.PointingHandCursor)
        eb.clicked.connect(self.start_check)
        ebl = QHBoxLayout()
        ebl.addStretch()
        ebl.addWidget(eb)
        ebl.addStretch()
        ev.addSpacing(10)
        ev.addLayout(ebl)
        ev.addStretch()
        p1.addWidget(es)
        oc_host = self.order_check.window
        oc_host.on_show = lambda: (self.go_page("order"), self._cmp_show(1))
        p1.addWidget(oc_host)
        self._review_stack = p1
        self.cmp_stack.addWidget(p1)

        # ---- 第 3 步：比价分配（进度 + 处理过程）----
        p2 = QWidget()
        lay = QVBoxLayout(p2)
        lay.setContentsMargins(28, 20, 28, 24)
        lay.setSpacing(16)
        hold2 = QWidget(p2)
        hold2.hide()

        # 这一批的门店：比完的点一下看结果（放在第 4 步）
        self.batch_box = QWidget()
        self.batch_lay = QHBoxLayout(self.batch_box)
        self.batch_lay.setContentsMargins(0, 0, 0, 0)
        self.batch_lay.setSpacing(8)
        self.batch_box.hide()

        # 比价的开关和规则：收进「设置」窗口（平时不用改）
        self._build_cmp_options()

        run = QHBoxLayout()
        run.setSpacing(20)
        self.btn_run = QPushButton(tr("重新比价这家门店"))
        self.btn_run.setToolTip(tr("只把现在选中的这家门店再比一次价（F6）"))
        self.btn_run.clicked.connect(self.start)
        self.btn_run.hide()
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
        hold2.setLayout(run)
        self.feed = QTextEdit()
        self.feed.setReadOnly(True)
        DS.build_compare(self, lay)            # 设计稿「比价分配」：进度大卡 + 每家门店一行 + 右边正在做
        self.cmp_stack.addWidget(p2)

        # ---- 第 4 步：比价结果（「比价结果」页面整页放进来，_embed_result）----
        self._res_slot = QWidget()
        self._res_slot_lay = QVBoxLayout(self._res_slot)
        self._res_slot_lay.setContentsMargins(0, 0, 0, 0)
        self._res_slot_lay.setSpacing(0)
        self.cmp_stack.addWidget(self._res_slot)

        # ---- 第 5 步：下单发送（设计稿：这一批的订单按供应商分组 + 右边订单邮件）----
        p4box = QWidget()
        p4v = QVBoxLayout(p4box)
        p4v.setContentsMargins(0, 0, 0, 0)
        p4v.setSpacing(0)
        p4 = QWidget()
        lay = QVBoxLayout(p4)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(14)
        hold4 = QWidget(p4)
        hold4.hide()
        h4 = QVBoxLayout(hold4)
        h4.addWidget(self.batch_box)
        self.done_note = QLabel(tr("比价完成后，每家店的结果在这里"))
        h4.addWidget(self.done_note)
        DS.build_send(self, lay)
        p4v.addWidget(p4, 1)
        foot4 = QFrame()
        foot4.setObjectName("StatusBar")
        acts = QHBoxLayout(foot4)
        acts.setContentsMargins(28, 12, 28, 12)
        acts.setSpacing(10)
        n4 = QLabel(tr("发之前会再列一遍发给谁、几封"))
        n4.setObjectName("Faint")
        acts.addWidget(n4)
        acts.addStretch()
        b_res = QPushButton("← " + tr("比价结果"))
        b_res.clicked.connect(lambda: self._cmp_show(3))
        b_rep = QPushButton(tr("门店报告"))
        b_rep.clicked.connect(lambda: self.open_store_report())
        b_trk = QPushButton(tr("订单跟踪"))
        b_trk.setToolTip(tr("发出去以后在采购订单跟踪里看确认 / 发货 / 到货"))
        b_trk.clicked.connect(lambda: self.go_page("tracking"))
        self._sd_all = __import__("erp_widgets").CountButton(tr("全部发送"))
        self._sd_all.setObjectName("Primary")
        self._sd_all.setMinimumHeight(40)
        self._sd_all.setEnabled(False)
        self._sd_all.clicked.connect(lambda: DS.send_all(self))
        for b_ in (b_res, b_rep, b_trk, self._sd_all):
            acts.addWidget(b_)
        p4v.addWidget(foot4)
        self.cmp_stack.addWidget(p4box)
        self._cmp_show(0)
        return w

    def _cmp_show(self, i):
        """DETERSIVO 五步：切到第 i 步那一屏（顶上蓝线跟着）：0 收集文件 · 1 审核清单 · 2 比价分配 · 3 比价结果 · 4 下单发送"""
        if not hasattr(self, "cmp_stack"):
            return
        if i == 1:
            self._review_stack.setCurrentIndex(1 if self.order_check.rows else 0)
        if i == 2:
            __import__("deter_steps").render_compare(self)
        if i == 3:
            self._embed_result()
            QTimer.singleShot(0, self._fill_result_now)
        if i == 4:
            __import__("deter_steps").refresh_send(self)
        self.cmp_stack.setCurrentIndex(i)
        self.cmp_steps.set_shown(i)

    def _embed_result(self):
        """「比价结果」页面放进 DETERSIVO 第 4 步（只做一次；以后 go_page("result") 都跳到这一步）"""
        if getattr(self, "_res_wrap", None) is not None or not hasattr(self, "_res_slot_lay"):
            return
        keys = [k for k, _ in self.NAV]
        i = keys.index("result")
        if self.pages[i] is None:
            self._build_page(i)
        wrap = self.stack.widget(i)
        self.stack.removeWidget(wrap)
        self.stack.insertWidget(i, QWidget())          # 原位置留个空页（页面编号不变）
        self._res_slot_lay.addWidget(wrap)
        wrap.show()
        self._res_wrap = wrap

    def _to_send_step(self):
        """比价结果看完 →「去发送订单」：第 4 步打勾，到第 5 步"""
        if hasattr(self, "cmp_steps"):
            self.cmp_steps.set_state(3, "done", "")
            self.cmp_steps.set_current(4)
        self.go_page("order")
        self._cmp_show(4)

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
        """比价结果（2026-11-16 照设计稿「⑩ 比价结果」重做，版面在 result_page.py）。
        老的指标卡 / 方案表 / 起订量条 / 各供应商采购量表照旧建，放在藏起来的 _res_hold 里，别的代码照用"""
        from page_kit import Stat, Card, button
        from erp_widgets import RowSorter, HeaderFilter
        import result_page as RP
        w, lay = self._page(tr("分配结果"), tr("最近一次处理的结果；以前的结果在「历史记录」里打开。起订量调整的每一行明细写在主订单的 AC 列。"))
        hold = QWidget(w)
        hold.hide()
        self._res_hold = hold
        hl = QVBoxLayout(hold)
        self.btn_missing = button(tr("缺失商品"), self.show_missing)
        self.btn_transfers = button(tr("调货单"), self.show_transfers)
        self.btn_edit = button(tr("修改分配"), self.edit_allocation,
                               tip=tr("已经写入以后，还可以改商品的供应商并重新写回文件"))
        self.btn_edit.setEnabled(False)

        class _Act:                                   # 老代码 btn_backup.setEnabled 照用
            def setEnabled(self, _v):
                pass
        self.btn_backup = _Act()
        self.k_rows, self.k_priced, self.k_missing = Stat(tr("商品行")), Stat(tr("有报价")), Stat(tr("没有报价"))
        self.k_changes, self.k_amount, self.k_cost, self.k_time = (Stat(tr("改了供应商")), Stat(tr("采购金额")),
                                                                   Stat(tr("比只按最低价多花")), Stat(tr("用时")))
        self.res_hero = __import__("page_kit").Hero("")
        self.banner_result = Banner()
        for x in (self.btn_missing, self.btn_transfers, self.k_rows, self.k_priced, self.k_missing, self.k_changes,
                  self.k_amount, self.k_cost, self.k_time, self.res_hero, self.banner_result):
            hl.addWidget(x)

        self.scen_panel = Card(tr("方案对比"), tr("✔ 是选用的方案。点「缺失商品」的数字看缺了哪些商品；鼠标停在左边的项目上看说明。"))
        self.scen_table = make_table([""], stretch_col=None)
        self.scen_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.scen_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.scen_table.setMinimumHeight(200)
        self.scen_table.cellClicked.connect(self._scen_clicked)
        self.scen_panel.v.addWidget(self.scen_table)
        self.scen_switch = QWidget()
        self.scen_switch_lay = QHBoxLayout(self.scen_switch)
        self.scen_switch_lay.setContentsMargins(0, 4, 0, 0)
        self.scen_switch_lay.setSpacing(8)
        self.scen_panel.v.addWidget(self.scen_switch)
        hl.addWidget(self.scen_panel)
        self.gauge = MinOrderGauge()
        hl.addWidget(self.gauge)
        self.sum_table = make_table([tr("供应商文件"), tr("商品数"), tr("最低价箱数"), tr("最终箱数"), tr("箱数变化"),
                                     tr("最低价金额 €"), tr("最终金额 €"), tr("金额变化 €"), tr("起订条件"), tr("状态")],
                                    stretch_col=None, numeric_cols=(1, 2, 3, 4, 5, 6, 7))
        self.sum_table.setItemDelegateForColumn(9, StatusDelegate(self.sum_table))
        self.sum_sorter = RowSorter(self.sum_table, pin_last=True)
        hl.addWidget(self.sum_table)

        self.chg_filter = QLineEdit()
        self.chg_filter.setPlaceholderText(tr("筛选 EAN、品名或供应商"))
        self.chg_filter.setFixedWidth(240)
        self.chg_filter.setClearButtonEnabled(True)
        self.chg_filter.textChanged.connect(self._filter_changes)
        self.chg_table = make_table([tr("行"), "EAN", "ARTICOLO", tr("箱数"), tr("原供应商"), tr("原件价"), tr("新供应商"), tr("新件价"),
                                     tr("多花 €"), tr("原因")], stretch_col=2, numeric_cols=(0, 3, 5, 7, 8))
        self.chg_table.setItemDelegateForColumn(9, StatusDelegate(self.chg_table))
        import product_images as PI
        PI.add_thumbs(self.chg_table, 2, 1, settings)
        self.chg_table.setMinimumHeight(260)
        self.chg_sorter = RowSorter(self.chg_table)
        self.chg_hf = HeaderFilter(self.chg_table, apply=lambda: self._filter_changes(self.chg_filter.text()),
                                   sorter=self.chg_sorter, base_hidden=lambda r: self._chg_base(r))
        self.chg_empty = QLabel(tr("全部按最低价，没有换供应商的商品"))
        self.chg_empty.setObjectName("Muted")
        RP.build(self, lay, hold)
        lay.addStretch()
        return w

    def _chg_base(self, r):
        text = self.chg_filter.text().strip().lower()
        t = self.chg_table
        hay = " ".join(t.item(r, c).text() for c in (1, 2, 4, 6) if t.item(r, c)).lower()
        return bool(text) and text not in hay

    def _filter_changes(self, text):
        t = self.chg_table
        hf = getattr(self, "chg_hf", None)
        for r in range(t.rowCount()):
            t.setRowHidden(r, self._chg_base(r) or (hf is not None and not hf.accepts(r)))

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
            from erp_widgets import header_text
            ws.append([header_text(t, c) for c in range(t.columnCount())])
            for r in range(t.rowCount()):
                ws.append([tr(t.item(r, c).text()) if t.item(r, c) else "" for c in range(t.columnCount())])
            ws2 = wb.create_sheet(tr("各供应商"))
            st = self.sum_table
            ws2.append([header_text(st, c) for c in range(st.columnCount())])
            for r in range(st.rowCount()):
                ws2.append([tr(st.item(r, c).text()) if st.item(r, c) else "" for c in range(st.columnCount())])
            for sh in (ws, ws2):
                for col in sh.columns:
                    width = max((len(str(c.value)) for c in col if c.value), default=8)
                    sh.column_dimensions[col[0].column_letter].width = min(width + 2, 60)
            wb.save(path)
            self.log(tr("✔ 已导出改动明细：{0}", path))
            from erp_widgets import open_file                  # 导出完直接打开（2026-11-07 用户）
            open_file(self, path, tr("导出"))
        except Exception as e:
            QMessageBox.critical(self, tr("导出"), tr("导出失败：{0}", e))

    # ---------- 页面：历史记录 ----------
    def _page_history(self):
        """历史记录（2026-11-16 照设计稿「⑫ 历史记录」重做，版面在 history_page.py）。
        老的 hist_tree（藏着）照旧填，view_history / delete_history 用 history_page 选中的那条（_hist_sel）"""
        from page_kit import Hero
        import history_page as HP
        w, lay = self._page(tr("历史记录"), tr("每次处理和修改分配后自动保存一份结果，关掉程序以后也能随时打开查看。双击一行打开。"))
        hold = QWidget(w)
        hold.hide()
        hl = QVBoxLayout(hold)
        self.hist_hero = Hero("")
        hl.addWidget(self.hist_hero)
        self.hist_filter = QLineEdit()
        self.hist_filter.setPlaceholderText(tr("搜门店、主订单、供应商"))
        self.hist_filter.setClearButtonEnabled(True)
        self.hist_filter.setMinimumHeight(36)
        self.keep_box = QComboBox()
        self.keep_box.setToolTip(tr("历史记录保留多久：更早的自动删除（只删记录，不动订单和供应商文件）"))
        for days, label in history.KEEP_OPTIONS:
            self.keep_box.addItem(tr("保留 {0}", tr(label)) if days else tr(label), days)
        idx = self.keep_box.findData(int(settings.get("history_keep_days", history.DEFAULT_KEEP_DAYS)))
        self.keep_box.setCurrentIndex(max(0, idx))
        self.keep_box.currentIndexChanged.connect(self._set_keep)
        from PySide6.QtWidgets import QTreeWidget
        self.hist_tree = QTreeWidget()
        self.hist_tree.setColumnCount(len(HIST_COLS))
        hl.addWidget(self.hist_tree)
        from erp_widgets import EmptyState
        self.hist_empty = EmptyState("history", tr("还没有记录"), tr("每次比价、修改分配后自动存一份"))
        self.hist_info = QLabel()
        HP.build(self, lay)
        return w

    def _hist_menu(self, pos):
        """右键（和页头按钮一样的操作，只是快捷方式）"""
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction(tr("打开查看"), self.view_history)
        m.addAction(tr("打开这次的文件夹"), self.open_history_folder)
        m.addSeparator()
        m.addAction(tr("删除选中"), self.delete_history)
        m.exec(self.hist_tree.viewport().mapToGlobal(pos))

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
        stores = sorted({e.get("store") for e in runs if e.get("store")})
        self.hist_hero.set_sub(tr("{0} 次比价 · {1} 家门店 · 占用 {2:.1f} MB · {3}", len(runs), len(stores), mb,
                                  tr("超过 {0} 天的自动删除", keep) if keep else tr("永久保留")))
        self._filter_history(self.hist_filter.text())
        self._mark_shown_history()
        __import__("history_page").render(self)

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
        sel = getattr(self, "_hist_sel", None)              # 新版历史页：右边详情那一条
        if sel is not None:
            return [e for e in runs if e.get("id") == sel][:1]
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
        """缺失查找（2026-11-16 重排）：页头（结果一句话）+ 右边 开始查找 · ⋯；卡片标题行放 搜索 · 匹配阈值 · 网络搜索"""
        from page_kit import Hero, Card, button, more_menu
        tip = tr("对没有供应商报价的商品，在供应商文件里按条码前缀和品名找可能的替代品。需要先完成一次订单处理。")
        w, lay = self._page(tr("缺失查找"), tip)
        hero = Hero(tr("缺失查找"), tr("还没有查找结果。"), tip=tip)
        self.miss_status = hero.sub
        self.btn_find = button(tr("开始查找"), self.run_missing, primary=True)
        self.btn_web = button(tr("网络搜索选中行"), self.run_web, tip=tr("只搜表里选中的几行（网络搜索比较慢）"))
        hold = QWidget(w)
        hold.hide()
        hero.setParent(hold)

        p = Card(tr("没有报价的商品"))
        self.miss_filter = QLineEdit()
        self.miss_filter.setPlaceholderText(tr("筛选 EAN 或品名"))
        self.miss_filter.setFixedWidth(220)
        self.miss_filter.setClearButtonEnabled(True)
        self.miss_filter.textChanged.connect(self._filter_missing)
        p.add_head(self.miss_filter)
        lt = QLabel(tr("匹配阈值") + "  ⓘ")
        lt.setObjectName("Muted")
        lt.setToolTip(tr("0 到 1，越大越严格"))
        p.add_head(lt)
        self.thr = QDoubleSpinBox()
        self.thr.setFixedWidth(80)
        self.thr.setRange(0.1, 1.0)
        self.thr.setSingleStep(0.05)
        self.thr.setValue(float(settings.get("match_threshold", 0.6)))
        self.thr.setToolTip(tr("0 到 1，越大越严格"))
        p.add_head(self.thr)
        self.t_web = Toggle(tr("网络搜索"), settings.get("use_web", False))
        self.t_web.setToolTip(tr("开始查找时同时上网搜（较慢）"))
        p.add_head(self.t_web)
        self.miss_table = make_table(["EAN", "ARTICOLO", tr("数量"), tr("综合匹配"), tr("品名匹配"), tr("网络搜索结果"),
                                      tr("搜索时间"), tr("可能的替代品")], stretch_col=7, numeric_cols=(2, 3, 4))
        self.miss_table.setMinimumHeight(460)
        self.miss_table.setWordWrap(True)
        from erp_widgets import RowSorter, HeaderFilter
        self.miss_sorter = RowSorter(self.miss_table)
        self.miss_hf = HeaderFilter(self.miss_table, apply=lambda: self._filter_missing(self.miss_filter.text()),
                                    sorter=self.miss_sorter, base_hidden=lambda r: self._miss_base(r))
        p.v.addWidget(self.miss_table, 1)
        from erp_widgets import EmptyState
        self.miss_empty = EmptyState("search", tr("还没有查找结果"), tip)
        b = button(tr("开始查找"), self.run_missing, primary=True)
        self.miss_empty.buttons.addWidget(b)
        p.v.addWidget(self.miss_empty, 1)
        self.miss_table.hide()
        p.setParent(hold)
        __import__("missing_page").build(self, lay, hold)
        return w

    # ---------- 页面：起订量 ----------
    def _page_rules(self):
        """起订规则（2026-11-16 重排）：页头（几家 · 几家启用 · 几家这次对上文件）+ 新增规则；
        「凑单」卡两个开关；「供应商规则」卡标题行放 编辑 · 启用 / 停用 · 删除"""
        from page_kit import Hero, Card, button
        tip = tr("按最低价分到的货不够某家起订量时，程序自动算出最省钱的凑单方案。凑单多花超过「手动调整线」会打开手动调整窗口；超过「放弃供应商线」、或者凑单比放弃这家还贵时，会弹窗问你继续凑单还是放弃。修改后自动保存。")
        w, lay = self._page(tr("起订量"), tip)
        self.rules_hero = Hero(tr("起订规则"), " ", tip=tip)
        self.banner_rules = Banner()

        hold = QWidget(w)
        hold.hide()
        self.rules_hero.setParent(hold)
        self.t_min2 = Toggle(tr("起订量凑单"), settings.get("min_order_enabled", True))
        self.t_min2.setToolTip(tr("关闭后只按最低价选供应商，不管起订量"))
        self.t_min2.toggled.connect(lambda on: self.t_min.setChecked(on))
        self.t_min2.toggled.connect(lambda _on: self.refresh_rules_table())
        self.t_partial = Toggle(tr("允许部分商品不采购"), settings.get("allow_partial_purchase", False))
        self.t_partial.setToolTip(tr("关闭（默认）：所有商品都必须订到，只有某家有货的商品，不能因为放弃这家而不买。\n打开：可以放弃起订供应商，只有它有货的商品这次就不采购。"))
        self.t_partial.toggled.connect(lambda on: (settings.__setitem__("allow_partial_purchase", on),
                                                   save_settings(settings),
                                                   self.show_toast(tr("已允许部分商品不采购") if on else tr("所有商品都必须采购"))))
        self.rules_table = make_table([tr("供应商"), tr("文件名关键字"), tr("起订条件"), tr("手动调整线"), tr("放弃供应商线"), tr("本次匹配的文件"), tr("状态")],
                                      stretch_col=5)
        self.rules_table.setParent(hold)
        from erp_widgets import RowSorter
        self.rules_sorter = RowSorter(self.rules_table)
        __import__("rules_page").build(self, lay)
        return w

    # ---------- 页面：日志 ----------
    def _page_log(self):
        """运行日志（2026-11-16 重排）：页头（几条 · 几个提醒 · 几个错误）+ 右边 全部 / 提醒和错误 / 只看错误 · ⋯；等宽字的日志卡片"""
        from page_kit import Hero, Card, Seg, more_menu
        tip = tr("本次打开程序以来的全部记录。完整历史在日志文件里。")
        w, lay = self._page(tr("运行日志"), tip)
        self.log_hero = Hero(tr("运行日志"), " ", tip=tip)
        self.log_seg = Seg([("all", tr("全部")), ("warn", tr("提醒和错误")), ("err", tr("只看错误"))], "all")
        self.log_seg.changed.connect(self._set_log_filter)
        self.log_btns = self.log_seg.group
        self.log_hero.add(self.log_seg)
        more, _a = more_menu([(tr("打开日志文件"), lambda: open_path(os.path.abspath(os.path.join(LOG_DIR, LOG_FILE))), None),
                              None,
                              (tr("清空"), self._clear_log, tr("只清空这里显示的，日志文件不动"))])
        self.log_hero.add(more)
        hold = QWidget(w)
        hold.hide()
        self.log_hero.setParent(hold)
        self.log_view = QTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        __import__("log_page").build(self, lay)
        QTimer.singleShot(0, self._render_log)
        return w

    def _open_log_file(self):
        open_path(os.path.abspath(os.path.join(LOG_DIR, LOG_FILE)))

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
        """
        日志先攒起来，每 0.2 秒一次性写进界面（后台计算时一秒几百行，一行一行写会把界面卡住）。
        日志框最多留最近几千行，越积越多也不会变慢
        """
        q = self.__dict__.setdefault("_log_q", [])
        q.append(msg)
        t = getattr(self, "_log_timer", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(200)
            t.timeout.connect(self._flush_log)
            self._log_timer = t
        if not t.isActive():
            t.start()

    def _flush_log(self):
        if not (hasattr(self, "log_view") and hasattr(self, "feed")):      # 界面还没建好：等一下再写
            self._log_timer.start()
            return
        q, self._log_q = getattr(self, "_log_q", []), []
        if not q:
            return
        log_html, feed_html = [], []
        for msg in q:
            for line in msg.split("\n"):
                lvl = log_level(line)
                self.log_entries.append((lvl, line, datetime.now()))
                if line.strip() and not set(line.strip()) <= {"="}:
                    self.feed_entries.append((lvl, line))
                    feed_html.append(self._fmt_line(lvl, line))
        for lst, n in ((self.log_entries, 20000), (self.feed_entries, 5000)):
            if len(lst) > n:
                del lst[:len(lst) - n]
        lp = getattr(self, "_log_page", None)
        if lp is not None and lp.isVisible():
            __import__("log_page").render(self)
        else:
            self._log_dirty = True
        if feed_html:
            self.feed.append("<br>".join(feed_html))
            self.feed.moveCursor(QTextCursor.End)
        self._log_counts()

    def _log_counts(self):
        """运行日志页头：几条 · 几个提醒 · 几个错误；分段按钮上也写个数"""
        if not hasattr(self, "log_hero"):
            return
        n_w = sum(1 for e in self.log_entries if e[0] == "warn")
        n_e = sum(1 for e in self.log_entries if e[0] == "err")
        sub = tr("本次打开以来 {0} 条", len(self.log_entries))
        if n_w:
            sub += f" · <span style='color:{T('warn')}'>" + tr("提醒 {0}", n_w) + "</span>"
        if n_e:
            sub += f" · <span style='color:{T('err')}'>" + tr("错误 {0}", n_e) + "</span>"
        self.log_hero.set_sub(sub)
        self.log_seg.set_text("warn", tr("提醒和错误") + (f"  {n_w + n_e}" if n_w + n_e else ""))
        self.log_seg.set_text("err", tr("只看错误") + (f"  {n_e}" if n_e else ""))

    def _log_visible(self, lvl):
        return self.log_filter == "all" or (self.log_filter == "warn" and lvl in ("warn", "err")) or \
            (self.log_filter == "err" and lvl == "err")

    def _set_log_filter(self, k):
        self.log_filter = k
        self._render_log()

    def _render_log(self):
        if not hasattr(self, "log_view"):
            return
        self._log_dirty = False
        if not __import__("log_page").render(self):
            self.log_view.setHtml("<br>".join(self._fmt_line(e[0], e[1]) for e in self.log_entries if self._log_visible(e[0])))
            self.log_view.moveCursor(QTextCursor.End)
        self.feed.setHtml("<br>".join(self._fmt_line(l, t) for l, t in self.feed_entries))
        self.feed.moveCursor(QTextCursor.End)

    def _clear_log(self):
        self.log_entries.clear()
        self._render_log()

    # ---------- 文件夹 ----------
    # ---------- 工作文件夹（workspace.py）：固定一个根目录，四个子文件夹程序建好 ----------
    def ws_root(self):
        """工作文件夹：设置里记的；没有就用以前比价的根目录；再没有就是 我的文档\\批发商比价。第一次用时建好子文件夹"""
        import workspace as W
        import reorder_service as RS
        last = settings.get("last_folder") or ""
        old_root = last if last and os.path.isdir(os.path.join(last, DISTRIBUTUIRE_FOLDER)) and not RS.batch_root_of(last) else ""
        r = settings.get("wholesale_root") or RS.batch_root_of(last) or old_root or W.default_root()
        r = os.path.normpath(r)
        if getattr(self, "_ws_ready", None) != r:
            try:
                notes = W.ensure(r)
                if notes:
                    self.log(tr("[工作文件夹] {0}：建好 / 整理了 {1}", r, "、".join(notes)))
                self._ws_ready = r
            except Exception as e:
                self.log(tr("⚠ 工作文件夹建不了：{0}（{1}）", r, e))
            if settings.get("wholesale_root") != r:
                settings["wholesale_root"] = r
                save_settings(settings)
        return r

    def change_ws_root(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择工作文件夹"), self.ws_root())
        if not path:
            return
        settings["wholesale_root"] = os.path.normpath(path)
        save_settings(settings)
        self.ws_root()
        self.refresh_ws()
        self.show_toast(tr("工作文件夹：{0}", settings["wholesale_root"]))

    def _price_root(self, path=None):
        """（兼容）共用报价所在的根目录"""
        return self.ws_root()

    def sync_prices(self, path):
        """门店比价文件夹里的报价副本跟共用的「2 供应商报价」同步（共用的更新了就复制过来）"""
        import reorder_service as RS
        import file_inbox as FI
        import workspace as W
        r = RS.batch_root_of(path) if path else None
        if not r or not os.path.isdir(path):
            return
        shared = W.price_dir(r) if os.path.isdir(W.price_dir(r)) else os.path.join(r, DISTRIBUTUIRE_FOLDER)
        try:
            c, a = FI.sync_shared_prices(shared, os.path.join(path, DISTRIBUTUIRE_FOLDER),
                                         os.path.join(path, "_备份", "报价来源.json"))
            if c or a:
                self.log(tr("[文件] {0}：从共用报价复制 {1} 个、挪走 {2} 个", os.path.basename(path), len(c), len(a)))
        except Exception as e:
            self.log(tr("⚠ 共用报价没同步过来：{0}", e))

    @staticmethod
    def _file_tip(folder, name):
        p = os.path.join(folder, name)
        try:
            t = datetime.fromtimestamp(os.path.getmtime(p))
            return t, tr("{0}\n更新于 {1:%Y-%m-%d %H:%M}", name, t)
        except OSError:
            return None, name

    def refresh_ws(self):
        """三个放文件的地方显示现在有什么；下面一行说明能不能开始"""
        import workspace as W
        if not hasattr(self, "zone_lists"):
            return
        root = self.ws_root()
        self.ws_label.setText(root)
        self.ws_label.setToolTip(root)
        self._dedupe_old_once(root)
        st = W.scan(root)
        self._ws_scan = st
        lists = []
        for sid, f in st["lists"]:
            t, tip = self._file_tip(W.lists_dir(root), f)
            lists.append(((W.STORES.get(sid) if sid else tr("⚠ 哪家店？")) + " · " + f + (f"  ({t:%d/%m})" if t else ""), tip))
        self.zone_lists.set_files(lists, tr("把门店发来的订货清单拖到这里（一家店一份，新的替换旧的）。清单会一直留着：想重新比价，直接再点「开始」"))
        pr = []
        import supplier_names as SN
        for f in st["prices"]:
            t, tip = self._file_tip(W.price_dir(root), f)
            lb = SN.label_of(f, settings) if SN.recognized(f, settings) else tr("⚠ 哪家？")
            pr.append((f"{lb} · {f}" + (f"  ({t:%d/%m})" if t else ""), tip))
        self.zone_sup.set_files(pr, tr("每周把新的报价拖到这里，自动替换同一家的旧报价（五家店共用）"))
        el = []
        for sid, fs in sorted(st["elim"].items()):
            for f in fs:
                t, tip = self._file_tip(W.elim_dir(root, sid), f)
                el.append((f"{W.STORES[sid]} · {f}", tip))
        for f in st["elim_loose"]:
            el.append((tr("⚠ 哪家店？") + " · " + f, tr("直接放在「3 扣减文件」下面，不知道是哪家店的：拖到这里重新放一次，或者挪进对应门店的子文件夹")))
        self.zone_elim.set_files(el, tr("每家店已处理 / 已下单的文件（可以没有）；拖进来时认不出门店会问你"))
        # 能不能开始
        n_l = len(st["lists"])
        unknown = sum(1 for sid, _ in st["lists"] if not sid)
        if not n_l:
            txt, color = tr("先把门店清单拖到左边「门店清单」里。"), T("muted")
        elif not st["prices"]:
            txt, color = tr("{0} 份门店清单。还没有供应商报价：拖到中间「供应商报价」里再开始。", n_l), T("warn")
        else:
            txt = tr("{0} 份门店清单（{1}）· {2} 家供应商报价", n_l, "、".join(W.STORES.get(sid) or "?" for sid, _ in st["lists"]),
                     len(st["prices"]))
            if st["elim"]:
                txt += " · " + tr("扣减文件：{0}", "、".join(W.STORES[sid] for sid in sorted(st["elim"])))
            if unknown:
                txt += "　" + tr("（{0} 份认不出门店，开始时会问你）", unknown)
            txt += "\n" + tr("点「开始」检查清单，核对后点「确认，开始比价」，程序自动一家一家比完。")
            color = T("text")
        self.ws_status.setText(txt)
        self.ws_status.setStyleSheet(f"color:{color};")
        self.btn_start.setEnabled(bool(n_l) and bool(st["prices"]) and not self.running)
        if hasattr(self, "cmp_steps") and not self.running:
            ready = bool(n_l) and bool(st["prices"])
            self.cmp_steps.set_state(0, "done" if ready else "now", tr("{0} 份清单 · {1} 家报价", n_l, len(st["prices"])))
            self.cmp_steps.set_state(1, "now" if ready else "todo", "")
        try:
            __import__("deter_steps").render_collect(self, st, root)
        except Exception as e:
            file_logger.error(f"收集文件页刷新失败：{e}")

    def _busy_drop(self):
        if self.running:
            QMessageBox.information(self, tr("DETERSIVO采购"), tr("正在处理中，等处理完再放文件。"))
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
        self.refresh_ws()
        self.show_toast(msg)

    def _ask_store(self, name, what):
        """文件名里看不出门店：问一下（返回门店编号或 None = 跳过）"""
        import workspace as W
        names = [W.STORES[k] for k in W.STORES]
        got, ok = QInputDialog.getItem(self, what, tr("「{0}」是哪家门店的？", name), names, 0, False)
        if not ok:
            return None
        return next(k for k, v in W.STORES.items() if v == got)

    @staticmethod
    def _looks_like_price(path):
        """报价表：前几行里有 PREZZO NETTO 这一列"""
        if not path.lower().endswith((".xlsx", ".xlsm")):
            return False
        try:
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            try:
                for r in wb.active.iter_rows(min_row=1, max_row=15, values_only=True):
                    if any(str(v or "").strip().upper() == "PREZZO NETTO" for v in r):
                        return True
            finally:
                wb.close()
        except Exception:
            return False
        return False

    def _drop_lists(self, files):
        """门店清单 → 1 门店清单（文件名里没有门店名的问一下、加上门店名；同一家店新的替换旧的）"""
        import workspace as W
        if self._busy_drop():
            return
        root = self.ws_root()
        price, done, bad = [], [], []

        def check():
            """先在后台把拖进来的文件都看一遍（打开 Excel 要时间，几个文件一起拖进来以前会卡住）：
            {文件: "price" / None（门店清单，读得了）/ 读不了的原因}"""
            import reorder_service as RS_
            out = {}
            for f in files:
                if self._looks_like_price(f):
                    out[f] = "price"
                    continue
                try:
                    RS_.parse_order_file(f)
                    out[f] = None
                except Exception as e:
                    out[f] = str(e) or e.__class__.__name__
            return out
        from erp_widgets import BusyDialog
        kinds, err = BusyDialog.run(self, tr("门店清单"), tr("正在读取 {0} 个文件…", len(files)), check)
        kinds = kinds or {}
        for f in files:
            kind = kinds.get(f, err or "?")
            if kind == "price":
                price.append(f)                 # 放错地方的报价表：放到供应商报价里
                continue
            if kind is not None:
                bad.append(f"{os.path.basename(f)}：{kind}")
                continue
            sid = W.guess_store(f) or self._ask_store(os.path.basename(f), tr("门店清单"))
            if not sid:
                continue
            try:
                W.place_list(root, f, sid)
                done.append(W.STORES[sid])
            except Exception as e:
                self._file_error(e)
                return
        if bad:
            QMessageBox.warning(self, tr("门店清单"), tr("这些文件读不出商品，不像门店订货清单，没放进去：") + "\n\n" + "\n".join(bad))
        if done:
            self._after_drop(tr("门店清单放好了：{0}", "、".join(done)))
        if price:
            self._drop_suppliers(price)

    def open_deter_arrival(self):
        """上周 DETERSIVO 订单里勾还没到货的 → 每家店的扣减文件（deter_arrival.py）"""
        from deter_arrival import DeterArrivalDialog
        if self._busy_drop():
            return
        dlg = DeterArrivalDialog(self, settings, _UI, self.ws_root())
        if not dlg.exec():
            return
        if dlg.saved:
            self._after_drop(tr("没到货的做成扣减文件了：{0}", "；".join(tr("{0} {1} 种 · {2:g} 箱", *x) for x in dlg.saved)))
        else:
            self._after_drop(tr("上周的都到了：没有扣减文件"))

    def _drop_eliminare(self, files):
        """扣减文件 → 3 扣减文件\\门店（按文件名认门店，认不出问一下）"""
        import workspace as W
        if self._busy_drop():
            return
        root = self.ws_root()
        done = []
        for f in files:
            sid = W.guess_store(f) or self._ask_store(os.path.basename(f), tr("扣减文件"))
            if not sid:
                continue
            try:
                W.place_elim(root, f, sid)
                done.append(f"{W.STORES[sid]} · {os.path.basename(f)}")
            except Exception as e:
                self._file_error(e)
                return
        if done:
            self._after_drop(tr("扣减文件放好了：{0}", "；".join(done)))

    def _dedupe_old_once(self, root):
        """工作文件夹里 _旧文件 / _旧订单 一模一样的文件只留一份（以前没防重复留下的）：每次打开程序做一次，后台"""
        done = getattr(self, "_deduped", set())
        self._deduped = done
        if not root or root in done:
            return
        done.add(root)
        import workspace as W

        def work():
            try:
                res = W.dedupe_old(root)
            except Exception as e:
                res = []
                logging.getLogger("procurement").warning("旧文件去重失败：%s", e)
            n = sum(len(g) for _, g in res)
            if n:
                msg = tr("旧文件夹去重：删掉 {0} 个一模一样的重复文件（每个只留一份）", n)
                logging.getLogger("procurement").info(msg + " · " + "；".join(f"{os.path.relpath(d, root)}: {', '.join(g)}" for d, g in res))
        threading.Thread(target=work, daemon=True).start()

    def _drop_suppliers(self, files):
        """供应商报价 → 2 供应商报价（按文件名认出是哪家，替换那一家的旧报价；认不出的问一下）"""
        import file_inbox as FI
        import workspace as W
        if self._busy_drop():
            return
        root = self.ws_root()
        folder = W.price_dir(root)
        try:
            plan = FI.plan_suppliers(root, files, [r.get("keyword") for r in settings.get("rules", [])], folder=folder)
        except Exception as e:
            self._file_error(e)
            return
        existing = W.scan(root)["prices"]
        dlg = SupplierDropDialog(self, plan, existing)       # 每个文件选好是哪家供应商（认得出的已经选好）
        if not dlg.exec():
            return
        plan, picked = dlg.result_plan(), dlg.picked()
        import supplier_names as SN
        try:
            done = FI.apply_suppliers(root, plan, folder=folder)
        except Exception as e:
            self._file_error(e)
            return
        if SN.apply_names(settings, done, picked):          # 记下每个文件的供应商名称
            save_settings(settings)
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

    def _ws_file_path(self, kind, label):
        """三个放文件区里一行的文字 → 文件路径"""
        import workspace as W
        root = self.ws_root()
        name = re.sub(r"\s+\(\d\d/\d\d\)$", "", label)       # 去掉后面的「(日期)」
        if kind == "prices":
            return os.path.join(W.price_dir(root), name.partition(" · ")[2] or name)     # 去掉前面的「供应商 · 」
        store, _, f = name.partition(" · ")
        if kind == "lists":
            return os.path.join(W.lists_dir(root), f)
        sid = next((k for k, v in W.STORES.items() if v == store), None)
        return os.path.join(W.elim_dir(root, sid) if sid else os.path.join(root, W.ELIM), f)

    def _remove_ws_file(self, kind, label):
        import workspace as W
        if self._busy_drop():
            return
        p = self._ws_file_path(kind, label)
        if not os.path.exists(p):
            self.refresh_ws()
            return
        if QMessageBox.question(self, tr("拿走文件"), tr("把「{0}」拿走？\n文件会挪进同一个文件夹里的 {1}，不会删除。",
                                                         os.path.basename(p), W.OLD)) != QMessageBox.Yes:
            return
        try:
            W.archive(p, os.path.join(os.path.dirname(p), W.OLD))
        except Exception as e:
            self._file_error(e)
            return
        self._after_drop(tr("已拿走 {0}", os.path.basename(p)))

    def _open_ws(self, kind):
        import workspace as W
        root = self.ws_root()
        open_path({"lists": W.lists_dir(root), "prices": W.price_dir(root)}.get(kind, os.path.join(root, W.ELIM)))

    def start_check(self):
        """「开始」：检查 1 门店清单 里的全部清单（认不出门店的先问），检查窗口里确认后自动一家一家比价"""
        import workspace as W
        if self.running or self.order_check.running:
            return
        root = self.ws_root()
        st = W.scan(root)
        if not st["lists"]:
            QMessageBox.information(self, tr("开始"), tr("「门店清单」里还没有清单。把门店发来的订货清单拖进去。"))
            return
        if not st["prices"]:
            QMessageBox.information(self, tr("开始"), tr("「供应商报价」里还没有报价。把这周的批发商报价拖进去。"))
            return
        items = []
        for sid, f in st["lists"]:
            p = os.path.join(W.lists_dir(root), f)
            if not sid:
                sid = self._ask_store(f, tr("门店清单"))
                if not sid:
                    continue
                try:
                    p, _ = W.place_list(root, p, sid)            # 文件名前面加上门店名，原文件挪进 _旧文件
                    W.archive(os.path.join(W.lists_dir(root), f), os.path.join(W.lists_dir(root), W.OLD))
                except Exception as e:
                    self._file_error(e)
                    return
            items.append((sid, p))
        self.refresh_ws()
        if st["elim_loose"]:
            self.log(tr("⚠ 「3 扣减文件」下面有 {0} 个文件不在门店子文件夹里，这次不会扣减", len(st["elim_loose"])))
        if items:
            self.cmp_steps.set_current(1)
            self.order_check.run_lists(items)

    def _cmp_went_bg(self):
        if getattr(self, "_in_batch", False):
            if not getattr(self, "_batch_bg", False):
                self.show_toast(tr("比价在后台进行，全部门店比完会提醒"))
            self._batch_bg = True
        else:
            self.show_toast(tr("比价在后台进行，完成后会提醒"))

    def run_batch(self, folders, msg="", warn=False):
        """检查清单确认后：几家门店的比价文件夹一家一家自动比价（不再弹确认；方案对比按推荐的选）"""
        self._batch_queue = list(folders)
        self._batch_results = []
        self._in_batch = True
        self._batch_bg = False
        self._batch_info = {}
        if msg:
            self.banner_folder.show_msg(msg, "warn" if warn else "ok")
        self.refresh_ws()
        self._run_next_in_batch()

    def _run_next_in_batch(self):
        q = getattr(self, "_batch_queue", None)
        if q is None:
            return
        if not q:
            self._batch_queue = None
            self._finish_batch()
            return
        folder = q.pop(0)
        self._switch_folder(folder, load=False)
        QTimer.singleShot(50, lambda: __import__("deter_steps").render_compare(self))
        if not self.start(auto=True):
            self._batch_results.append((os.path.basename(folder), False, tr("没能开始")))
            QTimer.singleShot(0, self._run_next_in_batch)

    def _finish_batch(self):
        self._in_batch = False
        QTimer.singleShot(0, lambda: __import__("deter_steps").render_compare(self))
        res = getattr(self, "_batch_results", []) or []
        ok = [n for n, good, _ in res if good]
        bad = [f"{n}（{why}）" for n, good, why in res if not good]
        msg = tr("{0} 家门店比价完成：{1}", len(ok), "、".join(ok)) if ok else tr("比价没有完成")
        if bad:
            msg += "；" + tr("没完成：{0}", "、".join(bad))
        msg += "。" + tr("点下面的门店看各家的结果。")
        self.banner_folder.show_msg(msg, "warn" if bad else "ok")
        self.refresh_folder()
        # 比完先看比价结果（用户 11-18：确定没问题再去下单发送）；第 4 步「下单发送」等用户自己点过去
        self.cmp_steps.set_state(4, "todo", "")
        if ok:
            self.cmp_steps.set_state(2, "done", "")
            self.cmp_steps.set_current(3)
        bg = getattr(self, "_batch_bg", False)
        self._batch_bg = False
        if ok and bg:                                   # 点过「后台运行」：问要不要回去看结果
            from erp_widgets import ask_return
            ask_return(self, type("_Bg", (), {"in_background": True})(), msg.split("。")[0],
                       lambda: self.go_page("result"))
            return
        self.show_toast(msg.split("。")[0])
        if ok:
            self.go_page("result")

    def _refresh_batch(self, path):
        """① 下面一行：这一批的门店（点了切换总文件夹），每家显示「待比价 / 已比价」"""
        import reorder_service as RS
        while self.batch_lay.count():
            it = self.batch_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        batch = (settings.get("wholesale_batch") or {}).get("stores") or {}
        if hasattr(self, "done_note"):
            self.done_note.setVisible(not batch)
        if not batch:
            self.batch_box.hide()
            return
        lb = QLabel(tr("门店"))
        lb.setObjectName("Faint")
        self.batch_lay.addWidget(lb)
        cur = os.path.normcase(os.path.abspath(path)) if path else ""
        for sid, folder in sorted(batch.items(), key=lambda kv: int(kv[0])):
            has, done = self._batch_state(folder)
            prices = [f for f in (_excel_files(os.path.join(folder, DISTRIBUTUIRE_FOLDER)) or [])
                      if ".calc." not in f.lower() and "缺失报告" not in f]
            st = tr("没有主订单") if not has else (tr("✔ 已比价") if done else
                                                 (tr("● 待比价") if prices else tr("● 缺供应商报价")))
            b = QPushButton(f"{RS.STORES.get(int(sid), sid)}  {st}")
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(os.path.normcase(os.path.abspath(folder)) == cur)
            b.setToolTip(folder)
            b.clicked.connect(lambda _=False, f=folder: self._show_store_result(f))
            self.batch_lay.addWidget(b)
        self.batch_lay.addStretch()
        self.batch_box.show()

    @staticmethod
    def _batch_state(folder):
        """(有没有检查清单生成的主订单, 这份主订单比过价没有)：比价结果比主订单的库存快照新 = 比过了"""
        import reorder_service as RS
        good, _ = RS.checked_main_orders(os.path.join(folder, ORDINE_FOLDER))
        if not good:
            return False, False
        try:
            done = os.path.getmtime(os.path.join(folder, "_备份", "last_allocation.json")) > \
                os.path.getmtime(RS.snapshot_path(os.path.join(folder, ORDINE_FOLDER, good[0])))
        except OSError:
            done = False
        return True, done

    def _switch_folder(self, folder, load=True):
        if self.running:
            return
        if not os.path.isdir(folder):
            QMessageBox.information(self, tr("DETERSIVO采购"), tr("找不到这个文件夹：\n{0}", folder))
            return
        self.folder_edit.setText(os.path.normpath(folder))
        settings["last_folder"] = self.folder_edit.text()
        save_settings(settings)
        self.refresh_folder()

    def _show_store_result(self, folder):
        """点这一批里的一家门店：切换过去，比过价的打开它的结果"""
        from service import load_state
        if self.running:
            return
        self._switch_folder(folder)
        data = load_state(folder)
        if data and data.get("ok"):
            self.last_summary = data
            self.show_summary(data)
            self.go_page("result")
        else:
            self.show_toast(tr("这家门店还没比价：点「重新比价这家门店」"))

    def refresh_folder(self):
        """现在选中的门店比价文件夹（程序自己切换）：同步报价、更新门店状态和工作文件夹"""
        path = self.folder_edit.text().strip()
        if not self.running and path:
            self.sync_prices(path)
        self.info = info = scan_folder(path)
        if hasattr(self, "batch_box"):
            self._refresh_batch(path)
        if hasattr(self, "btn_run"):
            self.btn_run.setVisible(bool(path) and bool(info.get("ordine")) and not self.running)
            if info.get("ordine"):
                self.btn_run.setText(tr("重新比价 {0}", os.path.basename(os.path.normpath(path))))
        self.refresh_rule_line()
        self.refresh_rules_table()
        if hasattr(self, "order_check"):
            self.order_check.folder_changed()
        self.refresh_ws()

    # ---------- 比价设置（平时不用改，收在「设置」窗口里）----------
    def _build_cmp_options(self):
        """比价开关、方案、调货、供应商名称、起订规则：放在 self.cmp_opts_box（「设置」窗口打开时借过去显示）"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        g = QGridLayout()
        g.setHorizontalSpacing(30)
        g.setVerticalSpacing(8)
        self.t_backup = Toggle(tr("处理前自动备份原文件"), settings.get("auto_backup", True))
        self.t_min = Toggle(tr("起订量凑单"), settings.get("min_order_enabled", True))
        self.t_min.toggled.connect(lambda on: (settings.__setitem__("min_order_enabled", on),
                                               save_settings(settings), self.refresh_rule_line(),
                                               self.t_min2.setChecked(on), self.refresh_rules_table()))
        self.t_backup.toggled.connect(lambda on: (settings.__setitem__("auto_backup", on), save_settings(settings)))
        self.t_review = Toggle(tr("写入前人工确认分配"), settings.get("review_before_write", False))
        self.t_review.setToolTip(tr("打开后，每次写入文件前都会先打开「手动调整供应商」窗口让你检查"))
        self.t_review.toggled.connect(lambda on: (settings.__setitem__("review_before_write", on), save_settings(settings)))
        self.t_scen = Toggle(tr("多方案对比"), settings.get("scenarios_enabled", True))
        self.t_scen.setToolTip(tr("同时算几种方案（全部供应商 / 不含某家 …），自动比价时按推荐的选，结果页上可以改用别的"))
        self.t_scen.toggled.connect(lambda on: (settings.__setitem__("scenarios_enabled", on), save_settings(settings),
                                                self.refresh_rule_line()))
        for i, t in enumerate((self.t_backup, self.t_min, self.t_review, self.t_scen)):
            g.addWidget(t, i // 2, i % 2)
        v.addLayout(g)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        b_scen = QPushButton(tr("管理方案"))
        b_scen.clicked.connect(self.edit_scenarios)
        b_tr = QPushButton(tr("门店间调货"))
        b_tr.setToolTip(tr("DETERSIVO采购的门店间调货（和总仓采购分开设，默认不启用）"))
        b_tr.clicked.connect(self.edit_transfers)
        b_names = QPushButton(tr("供应商名称"))
        b_names.setToolTip(tr("给供应商文件起名字（比如 formattato_… 叫 MEGA CASH），方案排除和起订规则都按名字认"))
        b_names.clicked.connect(self.edit_supplier_names)
        edit_rules = QPushButton(tr("管理起订规则"))
        edit_rules.clicked.connect(lambda: (self._cmp_settings_dlg.accept() if getattr(self, "_cmp_settings_dlg", None) else None,
                                            self.go_page("rules")))
        for b in (b_scen, b_tr, b_names, edit_rules):
            btns.addWidget(b)
        btns.addStretch()
        v.addLayout(btns)
        self.rule_line = QLabel()
        self.rule_line.setObjectName("Muted")
        self.rule_line.setWordWrap(True)
        v.addWidget(self.rule_line)
        self._opts_holder = QWidget()                 # 平时放在这里（不显示）
        hv = QVBoxLayout(self._opts_holder)
        hv.addWidget(box)
        self._opts_holder.hide()
        self.cmp_opts_box = box

    def open_cmp_settings(self):
        """「设置」：比价开关和规则 + 检查门店清单用的订货周期、算法、数据库（两块部件借过来显示，关掉还回去）"""
        oc = self.order_check
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("DETERSIVO采购设置"))
        dlg.setMinimumWidth(720)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(12)
        p1 = Panel(tr("比价"))
        p1.lay.addWidget(self.cmp_opts_box)
        lay.addWidget(p1)
        p2 = Panel(tr("检查门店清单"))
        p2.lay.addWidget(oc.params_box)
        lay.addWidget(p2)
        bb = QHBoxLayout()
        bb.addStretch()
        ok = QPushButton(tr("关闭"))
        ok.setObjectName("Primary")
        ok.clicked.connect(dlg.accept)
        bb.addWidget(ok)
        lay.addLayout(bb)
        self._cmp_settings_dlg = dlg
        self.refresh_rule_line()
        try:
            dlg.exec()
        finally:
            self._cmp_settings_dlg = None
            self._opts_holder.layout().addWidget(self.cmp_opts_box)      # 还回去（窗口关掉时部件不跟着销毁）
            self._oc_panel.layout().addWidget(oc.params_box)
            dlg.deleteLater()

    def edit_scenarios(self):
        import scenarios as SC
        dlg = ScenarioSettingsDialog(self, SC.scenario_defs(settings), (self.info or {}).get("suppliers") or [])
        if dlg.exec():
            defs = dlg.defs()
            if defs:
                settings["scenarios"] = defs
                save_settings(settings)
                self.refresh_rule_line()

    def edit_transfers(self):
        if TransferSettingsDialog(self).exec():          # 只改批发商比价的调货（门店配货在「配货设置」里单独设）
            self.refresh_rule_line()
            self.show_toast(tr("调货设置已保存"))

    def _transfer_line(self):
        """现在的主订单会不会算门店间调货（显示在比价分配那一栏）"""
        import reorder_service as RS
        import service as SV
        name = (self.info or {}).get("ordine")
        if not name:
            return ""
        try:
            snap = RS.load_snapshot(os.path.join(self.folder_edit.text().strip(), ORDINE_FOLDER, name))
            return SV.transfer_status(snap, settings)[1]
        except Exception:
            return ""

    def check_prices_vs_db(self):
        """报价表和数据库对比（只读）：distribuire 里的每份报价表 ↔ 数据库商品表"""
        import workspace as W
        d = W.price_dir(self.ws_root())
        files = sorted(f for f in os.listdir(d) if f.lower().endswith((".xlsx", ".xls")) and not f.startswith(("~", "$"))
                       and ".calc." not in f.lower()) if d and os.path.isdir(d) else []
        if not files:
            QMessageBox.information(self, tr("报价表和数据库对比"), tr("「供应商报价」里还没有报价文件，先把报价拖进去。"))
            return
        from erp_widgets import TaskDialog
        dlg = TaskDialog(self, tr("报价表和数据库对比"), tr("{0} 份报价表", len(files)),
                         [tr("读取报价表"), tr("查询数据库"), tr("对比")], cancellable=False)
        dlg.show()
        log = self.log

        def work():
            try:
                import service as SV
                import price_check as PC
                import reorder_service as RS
                import supplier_names as SN
                dlg.post_step(0, "running", "", "")
                data, bad = {}, []
                for k, f in enumerate(files):
                    try:
                        name = SN.label_of(f, settings)
                        data[name if name not in data else f"{name} ({SN.stem(f)})"] = SV.read_price_file(os.path.join(d, f))
                    except Exception as e:
                        bad.append(f"{f}：{e}")
                    dlg.post_progress(5 + 45 * (k + 1) / len(files))
                dlg.post_step(0, "done", str(len(data)), "")
                dlg.post_step(1, "running", "", "")
                db = RS.make_db(settings.get("db") or None)
                items = PC.db_items_for_eans(db, {e for v in data.values() for e in v})
                db.engine.dispose()
                dlg.post_step(1, "done", str(len(items)), "")
                dlg.post_progress(90)
                dlg.post_step(2, "running", "", "")
                res = PC.compare(data, items)
                res["bad"], res["folder"] = bad, d
                dlg.post_step(2, "done", "", "")
                dlg.post_progress(100)
                dlg.post_finished(True, tr("完成"))
                log(tr("[报价对比] {0} 份报价表，{1} 个条码", len(data), res["total"]["eans"]))
                self.bridge.price_check_done.emit(res)
            except Exception as e:
                import traceback
                log(tr("❌ 报价表和数据库对比失败：{0}", e))
                log(traceback.format_exc())
                dlg.post_finished(False, tr("{0}\n\n数据库连不上的话，到「系统设置」检查地址和密码。", e))

        __import__("erp_widgets").run_excel_job(work)      # 用 Excel 的活排进同一条线程（一批门店比价不再卡死）

    def edit_supplier_names(self):
        heads = (self.info or {}).get("suppliers") or []
        try:
            import workspace as W
            heads = W.scan(self.ws_root())["prices"] or heads          # 「2 供应商报价」里现在的文件
        except Exception:
            pass
        if not heads:
            QMessageBox.information(self, tr("供应商名称"), tr("「供应商报价」里还没有报价文件，先把报价拖进去。"))
            return
        if SupplierNamesDialog(self, heads).exec():
            self.refresh_rule_line()
            if hasattr(self, "rules_table"):
                self.refresh_rules_table()
            self.show_toast(tr("供应商名称已保存"))

    def _snapshot_line(self, ordine_file):
        """开始处理前的提示：主订单是哪家门店的清单生成的（会算门店间调货和够卖天数）"""
        import reorder_service as RS
        try:
            snap = RS.load_snapshot(ordine_file)
        except Exception:
            snap = None
        if snap:
            import service as SV
            return "\n\n" + tr("门店库存：{0}（会算够卖天数）", RS.STORES.get(snap.get("store"), "")) + "\n" + \
                SV.transfer_status(snap, settings)[1]
        return ""

    def _scenario_line(self):
        if not settings.get("scenarios_enabled", True):
            return ""
        import scenarios as SC
        defs = SC.scenario_defs(settings)
        if len(defs) < 2:
            return ""
        return tr("多方案对比：{0}（处理时对比后由你选）", tr("、").join(tr(d["name"]) for d in defs))

    def refresh_rule_line(self):
        """设置窗口里只显示一行短的（几条起订规则），完整说明放在鼠标提示里（界面要简洁）"""
        self._refresh_rule_line()
        full = self.rule_line.text()
        for extra in (self._scenario_line(), self._transfer_line()):
            if extra:
                full += "\n" + extra
        n = sum(1 for r in settings["rules"] if r.get("enabled", True))
        short = tr("起订量凑单：关") if not self.t_min.isChecked() else tr("起订规则 {0} 条", n)
        self.rule_line.setText(short + "  ⓘ")
        self.rule_line.setToolTip(full.replace("　　", "\n"))

    def _refresh_rule_line(self):
        if not self.t_min.isChecked():
            self.rule_line.setText(tr("起订量凑单已关闭，只按最低价选供应商。"))
            return
        enabled = [r for r in settings["rules"] if r.get("enabled", True)]
        if not enabled:
            self.rule_line.setText(tr("还没有启用的起订规则。到「起订量」页添加。"))
            return
        import supplier_names as SN
        matched, _ = match_rules(enabled, self.info.get("suppliers") or [],
                                 SN.labels(self.info.get("suppliers") or [], settings))
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
        """打开这家店的订单文件夹 Ordini（只有采购了的供应商）；还没生成就打开 distribuire"""
        import service as SV
        d = self._shown_state().get("distribuire_path") or \
            os.path.join(self.folder_edit.text().strip(), DISTRIBUTUIRE_FOLDER)
        o = os.path.join(os.path.dirname(os.path.abspath(d)), SV.ORDERS_DIR)
        open_path(o if os.path.isdir(o) else d)

    def open_store_report(self):
        """打开这家店的门店报告（Articoli_da_verificare_门店_日月.xlsx，检查清单确认时写在比价文件夹里，取最新的）"""
        import glob
        d = self._shown_state().get("distribuire_path")
        folder = os.path.dirname(os.path.abspath(d)) if d else self.folder_edit.text().strip()
        # 2026-11-15 起五家店合成一个文件（每店一页），放在这一天的文件夹（门店文件夹的上一层）
        files = sorted((glob.glob(os.path.join(folder, "Articoli_da_verificare_*.xlsx")) +
                        glob.glob(os.path.join(os.path.dirname(folder), "Articoli_da_verificare_*.xlsx"))) if folder else [],
                       key=os.path.getmtime)
        if files:
            open_path(files[-1])
        else:
            QMessageBox.information(self, APP_NAME, tr("这家店没有门店报告：清单里没有非 Griglia 商品，或者这次不是从检查清单确认的。"))

    def open_main_order(self):
        p = self._shown_state().get("ordine_file")
        if p and os.path.exists(p):
            open_path(p)
        elif self.info.get("ordine"):
            open_path(os.path.join(self.folder_edit.text().strip(), ORDINE_FOLDER, self.info["ordine"]))
        else:
            QMessageBox.information(self, APP_NAME, tr("还没有比价过的门店。"))

    # ---------- 处理 ----------
    def start(self, auto=False):
        """比价这一家门店（现在选中的文件夹）。auto=True：一批里自动接着比，不弹确认。返回有没有开始"""
        if self.running:
            return False
        self.refresh_folder()
        info = self.info
        if info["errors"] or info.get("need_order"):
            why = "\n".join(info["errors"]) or info.get("need_order")
            if auto:
                self.log(tr("❌ {0}：不能比价：{1}", os.path.basename(self.folder_edit.text().strip()), why))
            else:
                QMessageBox.warning(self, tr("不能开始"), why)
            return False
        root_path = self.folder_edit.text().strip()
        n_rules = len([r for r in settings["rules"] if r.get("enabled", True)]) if self.t_min.isChecked() else 0
        msg = (tr("主订单：{0}\n扣减文件：{1} 个　供应商：{2} 家\n起订量凑单：{3}\n\n这次处理会按 eliminare 扣减主订单数量并覆盖保存主订单，然后把订购数量写进各供应商文件的 ORDINE 列。\n\n", info['ordine'], len(info['eliminare']), len(info['suppliers']), tr("开启，{0} 条规则", n_rules) if self.t_min.isChecked() else tr("关闭"))
               + (tr("处理前会自动备份到 总文件夹\\_备份。") if self.t_backup.isChecked()
                  else tr("自动备份已关闭，建议先手动备份。"))
               + tr("\n\n重复处理同一批文件时，会先恢复成原文件再算，不会重复扣减，结果和上次一样。")
               + (("\n\n" + self._scenario_line()) if self._scenario_line() else "")
               + self._snapshot_line(os.path.join(root_path, ORDINE_FOLDER, info["ordine"])))
        if not auto:
            box = QMessageBox(self)
            box.setWindowTitle(tr("重新比价"))
            box.setText(tr("重新比价这家门店？"))
            box.setInformativeText(msg)
            go = box.addButton(tr("开始处理"), QMessageBox.AcceptRole)
            box.addButton(tr("取消"), QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not go:
                return False

        settings.update(last_folder=root_path, auto_backup=self.t_backup.isChecked(),
                        min_order_enabled=self.t_min.isChecked())
        save_settings(settings)
        self.running = True
        self.btn_run.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.stage.setText(tr("正在比价：{0}", os.path.basename(os.path.normpath(root_path))))
        self.bar.setValue(0)
        if not (auto and getattr(self, "_batch_results", None)):      # 一批里接着比：前面几家的过程留着
            self.feed.clear()
            self.feed_entries.clear()
        self.run_start = time.time()
        self.tick.start()
        self._tick()
        from erp_widgets import TaskDialog
        self._cmp_task = TaskDialog(self, tr("正在比价分配"), tr("{0} · {1} 家供应商",
                                                              os.path.basename(os.path.normpath(root_path)), len(info["suppliers"])),
                                    [tr(x) for _, x in CMP_STAGES], cancellable=False)
        self._cmp_task.went_background.connect(self._cmp_went_bg)
        self._cmp_task.show()
        if auto and getattr(self, "_batch_bg", False):    # 一批里前面那家点过「后台运行」：后面几家也在后台比，不再一家弹一次
            self._cmp_task.to_background()
        self.cmp_steps.set_current(2)
        self._cmp_show(2)

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
                summary = {"ok": False, "error": _friendly_error(e)}
            self.bridge.done.emit(summary)

        __import__("erp_widgets").run_excel_job(work)      # 用 Excel 的活排进同一条线程（一批门店比价不再卡死）
        return True

    def _choose_from_worker(self, results):
        """在后台线程里调用：把几种方案交给界面对比，等用户选好后返回方案下标（None = 取消）"""
        ev = threading.Event()
        box = {}
        self.bridge.choose.emit((results, ev, box))
        ev.wait()
        return box.get("idx")

    def _on_choose(self, payload):
        results, ev, box = payload
        if getattr(self, "_in_batch", False):           # 一批自动比价：按推荐的方案，不弹窗（结果页上可以改用别的方案）
            import scenarios as SC
            box["idx"] = SC.recommend(results) or 0
            self.log(tr("[方案] 自动选用推荐的方案：{0}", tr(results[box["idx"]].get("name") or "")))
            ev.set()
            return
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
        import reorder_service as RS
        snap = RS.load_snapshot(st.get("ordine_file") or "") if st.get("ordine_file") else None
        ctx = {"ordine_file": st.get("ordine_file"), "distribuire_path": st.get("distribuire_path"),
               "store": (snap or {}).get("store")} if st.get("distribuire_path") else None
        dlg = ManualDialog(self, st["rows"], st["rules"], st["baseline"], cur, edit_mode=True, ctx=ctx)
        if not dlg.exec() or dlg.result is None:
            return
        new = dlg.result
        qty = dict(dlg.qty_changed)
        added = list(dlg.new_rows)
        changed = [r["row"] for r in st["rows"] if new.get(r["row"]) != cur.get(r["row"])]
        if not changed and not qty and not added:
            self.show_toast(tr("没有改动"))
            return
        parts = []
        if changed:
            parts.append(tr("{0} 个商品换供应商", len(changed)))
        if qty:
            parts.append(tr("{0} 个商品改数量", len(qty)))
        if added:
            parts.append(tr("新加 {0} 个商品", len(added)))
        if QMessageBox.question(self, tr("修改分配"),
                                tr("要把 {0}，并重新写入主订单和供应商文件。写入前会自动备份。继续？",
                                   tr("、").join(parts))) != QMessageBox.Yes:
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
                                            progress=lambda p, t: self.bridge.progress.emit(int(p), t), qty=qty,
                                            new_rows=added)
                self.bridge.rewrite_done.emit((out, n, None))
            except Exception as e:
                file_logger.error(f"rewrite failed: {e}", exc_info=True)
                self.log(tr("❌ 处理失败：{0}", e))
                self.bridge.rewrite_done.emit((None, 0, str(e)))

        __import__("erp_widgets").run_excel_job(work)      # 用 Excel 的活排进同一条线程（一批门店比价不再卡死）

    def _on_rewrite_done(self, payload):
        out, n, err = payload
        if getattr(self, "tracking", None) is not None:
            self.tracking.check_badge()
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
        if time.time() - getattr(self, "_cp_last", 0) > 0.4:      # 第 3 步每家门店那几行（别画得太勤）
            self._cp_last = time.time()
            __import__("deter_steps").render_compare(self)
        dlg = getattr(self, "_cmp_task", None)
        if dlg is not None and not dlg.is_done:
            i = max(k for k, (lo, _) in enumerate(CMP_STAGES) if pct >= lo)
            dlg.post_step(i, "running", "", text)
            dlg.post_progress(pct)

    def _on_done(self, summary):
        if getattr(self, "tracking", None) is not None:   # 比价生成了 Ordini → 订单跟踪的「待登记」个数
            from erp_widgets import when_idle               # 要读共享记录：等界面空下来（别和跳结果页抢）
            when_idle(self.tracking.check_badge, quiet_ms=1500)
        dlg = getattr(self, "_cmp_task", None)
        if dlg is not None and not dlg.is_done:
            ok = bool(summary and summary.get("ok"))
            dlg.post_finished(ok, tr("处理完成") if ok else (summary or {}).get("error") or tr("处理没有完成"))
        self.running = False
        self.tick.stop()
        self.btn_run.setEnabled(True)
        self.elapsed.setText(tr("用时 {0:.0f} 秒", time.time() - self.run_start))
        batch = getattr(self, "_in_batch", False)
        store = os.path.basename(os.path.normpath(self.folder_edit.text().strip()))
        if summary and summary.get("cancelled"):
            self.bar.setValue(0)
            self.stage.setText(tr("已取消，文件没有改动"))
            self.show_toast(tr("已取消，文件没有改动"))
            if batch:                                   # 取消了：后面的门店也不比了
                left = [os.path.basename(f) for f in (self._batch_queue or [])]
                self._batch_queue = None
                self._batch_results.append((store, False, tr("取消了")))
                self._finish_batch()
                if left:
                    self.banner_folder.show_msg(tr("取消了 {0}，后面没比的：{1}。点下面的门店、再点「重新比价」可以单独比。",
                                                   store, "、".join(left)), "warn")
            self.refresh_folder()
            return
        if not summary or not summary.get("ok"):
            self.bar.setValue(0)
            self.stage.setText(tr("处理没有完成"))
            err = (summary or {}).get("error") or tr("未知错误")
            if batch:                                   # 一批里有一家失败：记下来，接着比下一家
                self._batch_results.append((store, False, err))
                QTimer.singleShot(300, self._run_next_in_batch)
            else:
                QMessageBox.critical(self, tr("处理没有完成"), tr("{0}\n\n详细信息在「运行日志」页。", err))
                self.refresh_folder()
            return
        self.bar.setValue(100)
        self.stage.setText(tr("{0} 比价完成，主订单和供应商文件已保存", store))
        sups = summary.get("suppliers") or []
        self.__dict__.setdefault("_batch_info", {})[store] = (
            sum(sp.get("amount") or 0 for sp in sups), summary.get("total_rows") or 0, summary.get("priced_rows") or 0,
            sum(1 for sp in sups if (sp.get("colli") or 0) > 0), summary.get("missing") or 0, summary.get("elapsed"))
        self.last_summary = summary
        self.show_summary(summary)
        self.refresh_history()
        self.cmp_steps.set_current(3)
        self.cmp_steps.set_state(2, "done", "")
        self.cmp_steps.set_state(4, "todo", "")
        if batch:
            self._batch_results.append((store, True, ""))
            self._refresh_batch(self.folder_edit.text().strip())
            QTimer.singleShot(300, self._run_next_in_batch)
            return
        self.refresh_folder()
        from erp_widgets import ask_return
        ask_return(self, dlg, tr("{0} 比价完成", store),
                   lambda: (self.go_page("result"), self.show_toast(tr("{0} 比价完成", store))))

    def show_transfers(self, *_):
        s = self.last_summary or {}
        ts = list(s.get("transfers") or []) + list(s.get("transfers_out") or [])     # 调进来的 + 这家店要调出去的
        if not ts:
            return
        from store_order_page import TransferDialog
        TransferDialog(self, ts, _UI).exec()

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
        st = s.get("state") or {}
        if can_switch_scenario(s):
            import scenarios as SC
            from erp_widgets import CountButton
            diff = SC.diff_items(st.get("rows") or [], st["scenario_assigns"], st.get("labels") or {})
            bd = CountButton(tr("差异商品"), len(diff))
            bd.setEnabled(bool(diff))
            bd.clicked.connect(lambda: ScenarioDiffDialog(self, res, diff).exec())
            self.scen_switch_lay.addWidget(bd)
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

        __import__("erp_widgets").run_excel_job(work)      # 用 Excel 的活排进同一条线程（一批门店比价不再卡死）

    def _scen_clicked(self, row, col):
        res = (self.last_summary or {}).get("scenarios") or []
        got = getattr(self, "_scen_links", {}).get((row, col))
        if got is not None and got[0] < len(res):
            i, kind = got
            MissingDialog(self, scen_items(res[i], kind), tr("{0} · {1}", tr(SCEN_LIST_TITLE[kind]), tr(res[i]["name"]))).exec()

    def show_summary(self, s):
        mo = s.get("min_order", {})
        res = s.get("scenarios") or []
        self.scen_panel.setVisible(bool(res))
        if res:
            chosen = next((i for i, r in enumerate(res) if r.get("chosen")), None)
            self._scen_links = fill_scenario_table(self.scen_table, res, chosen)
            self.scen_table.setFixedHeight(min(620, 44 + 36 * self.scen_table.rowCount()))
        self._fill_scen_switch(s, res)
        self.btn_missing.setText(tr("缺失商品 ({0})", len(s.get("missing_items") or [])) if s.get("missing_items") is not None
                                 else tr("缺失商品"))
        ts = list(s.get("transfers") or []) + list(s.get("transfers_out") or [])
        self.btn_transfers.setVisible(bool(ts))
        self.btn_transfers.setText(tr("调货单 ({0})", len(ts)))
        self.k_rows.set(f"{s.get('total_rows', 0):,}")
        self.k_priced.set(f"{s.get('priced_rows', 0):,}")
        self.k_missing.set(f"{s.get('missing', 0):,}", T("warn") if s.get("missing") else None)
        n_ch = len(s.get("changes") or [])
        self.k_changes.set(f"{n_ch:,}")
        extra = mo.get("extra_cost", 0) or 0
        self.k_cost.set(f"€{extra:,.2f}", T("warn") if extra > 0.005 else None)       # 不显示了（用户说没意义），留着不出错
        self.k_time.set(tr("{0:.0f} 秒", s.get('elapsed', 0)))
        self.k_missing.set_tone("warn" if s.get("missing") else "ok")
        self.k_missing.set(f"{s.get('missing', 0):,}", T("warn") if s.get("missing") else None,
                           sub=tr("点开看缺了哪些") if s.get("missing") else tr("全部都有报价"))
        tr_rows = s.get("total_rows", 0) or 0
        self.k_priced.set(f"{s.get('priced_rows', 0):,}",
                          sub=tr("占 {0:.0f}%", 100 * (s.get("priced_rows", 0) or 0) / tr_rows) if tr_rows else " ")
        self.k_changes.set(f"{n_ch:,}", sub=tr("为了起订量 / 手动调整") if n_ch else tr("全部按最低价"))
        sups0 = s.get("suppliers", []) or []
        amt = sum(sp.get("amount", 0) or 0 for sp in sups0)
        self.k_amount.set(f"€{amt:,.0f}", sub=tr("{0} 家供应商", sum(1 for sp in sups0 if (sp.get("colli") or 0) > 0)))
        try:
            from history import _store_of
            store = _store_of(s)
        except Exception:
            store = ""
        hm = (s.get("_history") or {}).get("time") or ""
        try:
            when = datetime.strptime(hm[:16], "%Y-%m-%d %H:%M") if hm else None
        except ValueError:
            when = None
        if when is None:
            try:
                when = datetime.fromtimestamp(os.path.getmtime((s.get("state") or {}).get("ordine_file") or ""))
            except (OSError, TypeError):
                when = None
        self.res_hero.set_title(" · ".join(x for x in (store, f"{when:%d/%m}" if when else "") if x)
                                or tr("比价结果"))
        sub = [s.get("main_file") or ""]
        if s.get("scenario"):
            sub.append(tr("方案：{0}", tr(s["scenario"])))
        sub.append(tr("用时 {0:.0f} 秒", s.get("elapsed", 0)))
        if s.get("history_id") and not self._is_current(s):
            sub.append(f"<span style='color:{T('warn')}'>" + tr("历史记录 · 只能查看") + "</span>")
        self.res_hero.set_sub(" · ".join(x for x in sub if x))

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
        if s.get("transfer_note") and current:
            msgs.append(s["transfer_note"])
        if s.get("transfers"):
            ts = s["transfers"]
            msgs.append(tr("门店间调货：{0} 个商品，少订 {1} 箱（货值 €{2:,.2f}），调货单在总文件夹里：{3}",
                           len(ts), sum(t["colli_before"] - t["colli_after"] for t in ts), sum(t["value"] for t in ts),
                           os.path.basename(s.get("transfers_file") or "")))
        if s.get("transfers_out"):
            to = s["transfers_out"]
            msgs.append(tr("这家店要调出 {0} 个商品给其他门店（货值 €{1:,.2f}），见调货单", len(to), sum(t["value"] for t in to)))
        if mo.get("warning"):
            msgs.append(mo["warning"])
            kind = "err"
            if current and any(r.get("daily") is not None for r in ((s.get("state") or {}).get("rows") or [])):
                msgs.append(tr("没达标的供应商可以点「修改分配…」→「加商品凑单…」：不换供应商，从它的报价里挑卖得快的商品多订（订单里没有的也能加）。"))
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
        self.sum_sorter.apply()

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
        self.chg_sorter.apply()
        self._filter_changes(self.chg_filter.text())
        self.btn_backup.setEnabled(bool(s.get("backup_dir")))
        self.btn_edit.setEnabled(bool(s.get("state")) and current and not self.running)
        self.btn_edit.setToolTip(tr("已经写入以后，还可以改商品的供应商并重新写回文件") if current
                                 else tr("只能修改每个文件夹最近一次的分配"))
        if hasattr(self, "hist_tree"):
            self._mark_shown_history()
            if hasattr(self, "_hist_days"):
                self._hist_sel = s.get("history_id") or self._hist_sel
                __import__("history_page").render(self)
        self._res_fill_due = s
        host = getattr(self, "_res_wrap", None)
        if host is None:
            keys = [k for k, _ in self.NAV]
            host = self.stack.widget(keys.index("result")) if "result" in keys else None
        if host is None or host.isVisible():
            self._fill_result_now()

    def _fill_result_now(self):
        """比价结果页面（门店胶囊、批发商卡…）：看不见时只记下来，切到第 4 步再画（2026-11-20 stall.log）"""
        s = getattr(self, "_res_fill_due", None)
        if s is None:
            return
        self._res_fill_due = None
        try:
            __import__("result_page").fill(self, s)
        except Exception as e:
            file_logger.error(f"result page fill failed: {e}", exc_info=True)

    # ---------- 起订规则 ----------
    def refresh_rules_table(self):
        if not hasattr(self, "rules_table"):
            return
        rules = settings["rules"]
        sups = self.info.get("suppliers") or []
        import supplier_names as SN
        matched, _ = match_rules([r for r in rules if r.get("enabled", True)], sups, SN.labels(sups, settings))
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
            t.item(i, 0).setData(Qt.UserRole, i)          # 排序后也能找到是第几条规则
        self.rules_sorter.apply()
        if hasattr(self, "rules_hero"):
            on = [r for r in rules if r.get("enabled", True)]
            sub = tr("{0} 家供应商 · {1} 家启用", len(rules), len(on))
            if sups:
                sub += " · " + tr("这次对上 {0} 家的报价文件", len(hit))
            if not self.t_min2.isChecked():
                sub += f" · <span style='color:{T('warn')}'>" + tr("起订量凑单已关") + "</span>"
            self.rules_hero.set_sub(sub)
        try:
            __import__("rules_page").render(self, rules, hit if sups else {})
        except Exception as e:
            file_logger.error(f"起订规则页刷新失败：{e}")
        if not rules:
            self.banner_rules.show_msg(tr("还没有起订规则。点「新增供应商规则」，填上供应商的起订箱数或金额。"), "info")
        else:
            self.banner_rules.hide()

    def _rule_dialog(self, rule):
        return RuleDialog(self, rule, self.info.get("suppliers") or [], compact=True)

    def _set_rule(self, i, rule):
        settings["rules"][i] = rule
        self._save_rules(tr("已保存规则"))

    def _sel_rule(self):
        sel = getattr(self, "_rule_sel", None)
        if hasattr(self, "_rules_grid"):
            if isinstance(sel, int) and 0 <= sel < len(settings["rules"]):
                return sel
            QMessageBox.information(self, tr("起订量"), tr("先选一条规则。"))
            return None
        rows = self.rules_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, tr("起订量"), tr("先在表格里选一条规则。"))
            return None
        it = self.rules_table.item(rows[0].row(), 0)
        i = it.data(Qt.UserRole) if it is not None else None
        return i if isinstance(i, int) and 0 <= i < len(settings["rules"]) else None

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
            self._rule_sel = len(settings["rules"]) - 1
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
            self._rule_sel = min(i, len(settings["rules"]) - 1) if settings["rules"] else None
            self._save_rules(tr("已删除规则"))

    # ---------- 缺失查找 ----------
    def run_missing(self):
        root_path = self.folder_edit.text().strip()
        dist = os.path.join(root_path, DISTRIBUTUIRE_FOLDER)
        if not root_path or not os.path.isdir(dist):
            QMessageBox.information(self, tr("缺失查找"), tr("先完成一次比价（DETERSIVO采购页点「开始」）。"))
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

        __import__("erp_widgets").run_excel_job(work)      # 用 Excel 的活排进同一条线程（一批门店比价不再卡死）

    def _on_missing_done(self, results, error, use_web):
        self.btn_find.setEnabled(True)
        self.btn_find.setText(tr("开始查找"))
        if error:
            self.miss_status.setText(tr(error))
            return
        if not results:
            self.miss_status.setText(tr("没有缺失的商品。"))
            __import__("missing_page").render(self, [])
            return
        t = self.miss_table
        t.setUpdatesEnabled(False)            # 几百上千行：填完再一次画出来
        QTimer.singleShot(0, lambda: t.setUpdatesEnabled(True))     # 万一中途出错也会恢复
        t.setRowCount(len(results))
        t.show()
        self.miss_empty.hide()
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
        t.setUpdatesEnabled(True)
        msg = tr("{0} 个缺失商品，本地找到可能的替代品 {1} 个", len(results), found)
        if use_web and web_found:
            msg += tr("，网络找到 {0} 个", web_found)
        self.miss_status.setText(msg + "。")
        self.log(tr("✔ 缺失查找完成：{0}", msg))
        self.miss_sorter.apply()
        self._filter_missing(self.miss_filter.text())
        __import__("missing_page").render(self, results)
        self.go_page("search")

    def _miss_base(self, r):
        text = self.miss_filter.text().strip().lower()
        t = self.miss_table
        row_text = " ".join((t.item(r, c).text() if t.item(r, c) else "") for c in (0, 1, 7)).lower()
        return bool(text) and text not in row_text

    def _filter_missing(self, text):
        t = self.miss_table
        hf = getattr(self, "miss_hf", None)
        from erp_widgets import frozen
        with frozen(t):
            for r in range(t.rowCount()):
                t.setRowHidden(r, self._miss_base(r) or (hf is not None and not hf.accepts(r)))

    def run_web(self):
        from matcher import search_web_ean
        cur = __import__("missing_page").selected(self)
        if not cur:
            QMessageBox.information(self, tr("网络搜索"), tr("先在左边选一个商品。"))
            return
        ean, art = cur
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
        self.btn_web.setText(tr("网络搜索这个商品"))
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
            from erp_widgets import header_text
            ws.append([header_text(t, c) for c in range(t.columnCount())])
            for r in range(t.rowCount()):
                ws.append([tr(t.item(r, c).text()) if t.item(r, c) else "" for c in range(t.columnCount())])
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col if c.value), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(width + 2, 60)
            wb.save(path)
            self.log(tr("✔ 已导出：{0}", path))
            from erp_widgets import open_file                  # 导出完直接打开（2026-11-07 用户）
            open_file(self, path, tr("导出"))
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

    TYPES = (QEvent.Polish, QEvent.Wheel, QEvent.Enter, QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.Leave)
    _KINDS = None

    @staticmethod
    def _view_of(obj):
        """obj 是表格 / 列表 / 文本框（或它们的 viewport）→ 返回那个控件，否则 None"""
        kinds = WheelGuard._KINDS
        if kinds is None:
            from PySide6.QtWidgets import QAbstractItemView, QTextEdit, QPlainTextEdit
            kinds = WheelGuard._KINDS = (QAbstractItemView, QTextEdit, QPlainTextEdit)
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


HANG_SEC = 5            # 界面这么多秒没反应就记一笔（以前 15 秒，大多数卡顿记不下来）
HANG_SEC_BOOT = 2       # 刚打开的 90 秒内 2 秒就记（用户 11-06：启动时非常卡，但每次都不到 5 秒）
BOOT_WINDOW = 90


class _ClickRecorder(QObject):
    """记下最后点的按钮 / 菜单（卡顿记录里写「卡之前点了什么」）+ 最后一次点鼠标 / 按键的时间（erp_widgets.when_idle 用）。
    每个界面事件都会经过这里：只比一下事件类型就返回（2026-11-07 以前每次都 import，后台线程忙时界面会更卡）"""
    last = ("", 0.0)
    _INPUT = None

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in _ClickRecorder._INPUT:
            return False
        try:
            now = time.time()
            _LAST_INPUT[0] = now
            if t == _ClickRecorder._RELEASE and isinstance(obj, QAbstractButton):
                txt = obj.text() or obj.toolTip()[:30] or obj.objectName()
                if txt:
                    _ClickRecorder.last = (txt.replace("\n", " "), now)
        except Exception:
            pass
        return False


def _init_click_recorder():
    from PySide6.QtCore import QEvent
    from erp_widgets import LAST_INPUT
    global _LAST_INPUT, QAbstractButton
    from PySide6.QtWidgets import QAbstractButton
    _LAST_INPUT = LAST_INPUT
    _ClickRecorder._RELEASE = QEvent.MouseButtonRelease
    _ClickRecorder._INPUT = frozenset((QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.KeyPress, QEvent.Wheel))


def _event_hub(app):
    hub = getattr(app, "_event_hub", None)
    if hub is None:
        from erp_widgets import AppEventHub
        hub = app._event_hub = AppEventHub(app)
        app.installEventFilter(hub)
    return hub


STALL_SEC = 0.6          # 2026-11-19：界面停了这么久（用户能感觉到的小卡）就记进 logs/stall.log
STALL_TICK = 0.1


def _start_stall_sampler(app):
    """
    小卡顿记录（2026-11-19 用户：很多地方点了以后卡一会、没有进度条，都是以前遗留的 → 全面排查）。
    hang.log 只记 5 秒以上的；这里界面每 0.1 秒报一次平安，停了 0.4 秒以上就开始每 0.1 秒看一次界面线程正在跑
    本程序的哪一行，恢复时超过 STALL_SEC 就写一行：卡了几秒 · 最后点的按钮 · 卡最久的几处代码（按被看到的次数）。
    只看界面线程、只记本程序文件夹里的代码，开销很小。logs/stall.log 超过 2 MB 换新文件（旧的留一份 .old）。
    """
    try:
        from datetime import datetime as _dt
        path = os.path.join(LOG_DIR, "stall.log")
        try:
            if os.path.getsize(path) > 2_000_000:
                os.replace(path, path + ".old")
        except OSError:
            pass
        f = open(path, "a", encoding="utf-8")
        f.write(f"\n=== {APP_NAME} {APP_VERSION} 启动 {_dt.now():%Y-%m-%d %H:%M:%S}\n")
        f.flush()
        app._stall_file = f
        main_id = threading.main_thread().ident
        here = os.path.dirname(os.path.abspath(__file__)).lower()
        beat = {"t": time.time()}

        def tick():
            beat["t"] = time.time()
        tm = QTimer(app)
        tm.setInterval(int(STALL_TICK * 1000))
        tm.timeout.connect(tick)
        tm.start()
        app._stall_timer = tm

        def own_stack():
            fr = sys._current_frames().get(main_id)
            out = []
            while fr is not None and len(out) < 4:
                fn = fr.f_code.co_filename
                if fn.lower().startswith(here):
                    out.append(f"{os.path.basename(fn)}:{fr.f_lineno} {fr.f_code.co_name}")
                fr = fr.f_back
            return " ← ".join(out) or "（不是本程序的代码：Qt 画界面 / 套样式，或别的库）"

        def run():
            from erp_widgets import RUNNING
            last = time.time()
            while True:
                time.sleep(STALL_TICK)
                now = time.time()
                if now - last > 2:                       # 电脑睡眠醒来
                    beat["t"] = now
                last = now
                if now - beat["t"] < 0.4:
                    continue
                start, seen = beat["t"], {}
                while time.time() - beat["t"] > 0.25 and time.time() - start < 120:
                    k = own_stack()
                    seen[k] = seen.get(k, 0) + 1
                    time.sleep(STALL_TICK)
                dur = beat["t"] - start
                if dur < STALL_SEC:
                    continue
                click, ct = _ClickRecorder.last
                top = sorted(seen.items(), key=lambda kv: -kv[1])[:3]
                try:
                    f.write(f"[{_dt.now():%Y-%m-%d %H:%M:%S}] 卡 {dur:.1f} 秒"
                            f" · 最后点的：{('「' + click + '」 ' + format(start - ct, '.1f') + ' 秒前') if click else '—'}"
                            f" · 进度窗口：{'、'.join(RUNNING.values()) or '没有'}\n")
                    for k, n in top:
                        f.write(f"    {n * STALL_TICK:4.1f}s  {k}\n")
                    f.flush()
                except Exception:
                    pass
        threading.Thread(target=run, daemon=True, name="stall-sampler").start()
    except Exception as e:
        file_logger.info(f"小卡顿记录没开：{e}")


def _start_hang_watchdog(app):
    """
    界面卡住时自动记下「卡在哪一行代码」（logs/hang.log，不会关掉程序）。
    2026-11-04 改：界面每秒报一次平安；超过 HANG_SEC（5 秒）没报 → 写下卡住时间、当时在跑的任务、最后点的按钮、
    所有线程正在执行的代码；恢复后再写一句「共卡了 n 秒」。电脑睡眠 / 休眠醒来不算卡（看门狗自己也睡过头了）。
    另外 60 秒的兜底（faulthandler 自己的线程，后台代码一直占着 Python 时也能记下来）
    """
    try:
        import faulthandler
        from datetime import datetime as _dt
        os.makedirs(LOG_DIR, exist_ok=True)
        f = open(os.path.join(LOG_DIR, "hang.log"), "a", encoding="utf-8")
        f.write(f"\n=== {APP_NAME} {APP_VERSION} 启动 {_dt.now():%Y-%m-%d %H:%M:%S}"
                f"（下面「界面卡住」那几行就是卡的时候在做什么）\n")
        f.flush()
        app._hang_file = f
        # 程序闪退（Qt / Excel 那层崩了，Python 来不及报错）时也记下当时每条线程在跑哪一行：logs/crash.log
        cf = open(os.path.join(LOG_DIR, "crash.log"), "a", encoding="utf-8")
        cf.write(f"\n=== {APP_NAME} {APP_VERSION} 启动 {_dt.now():%Y-%m-%d %H:%M:%S}（下面有内容 = 这次闪退了）\n")
        cf.flush()
        faulthandler.enable(file=cf, all_threads=True)
        app._crash_file = cf

        def _hook(tp, val, tb, _old=sys.excepthook):
            try:
                import traceback
                cf.write(f"[{_dt.now():%H:%M:%S}] 没处理的错误：\n" + "".join(traceback.format_exception(tp, val, tb)))
                cf.flush()
            except Exception:
                pass
            _old(tp, val, tb)
        sys.excepthook = _hook

        def _thook(a, _old=threading.excepthook):
            try:
                import traceback
                cf.write(f"[{_dt.now():%H:%M:%S}] 后台线程 {a.thread.name if a.thread else ''} 出错：\n"
                         + "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
                cf.flush()
            except Exception:
                pass
            _old(a)
        threading.excepthook = _thook
        state = {"beat": time.time()}

        def beat():
            state["beat"] = time.time()
            faulthandler.cancel_dump_traceback_later()
            faulthandler.dump_traceback_later(60, repeat=False, file=f, exit=False)

        t = QTimer(app)
        t.setInterval(1000)
        t.timeout.connect(beat)
        t.start()
        app._hang_timer = t
        _init_click_recorder()
        rec = _ClickRecorder(app)
        _event_hub(app).add(rec, _ClickRecorder._INPUT)
        app._click_rec = rec
        beat()

        def watch():
            from erp_widgets import RUNNING
            last_loop, hung_since = time.time(), None
            started = time.time()
            while True:
                time.sleep(1)
                now = time.time()
                if now - last_loop > HANG_SEC:          # 看门狗自己也停了这么久 = 电脑睡眠 / 休眠，不算界面卡
                    state["beat"], hung_since = now, None
                last_loop = now
                gap = now - state["beat"]
                limit = HANG_SEC_BOOT if now - started < BOOT_WINDOW else HANG_SEC
                if hung_since is None and gap >= limit:
                    hung_since = state["beat"]
                    click, ct = _ClickRecorder.last
                    try:
                        f.write(f"\n[{_dt.now():%Y-%m-%d %H:%M:%S}] 界面卡住 {gap:.0f} 秒"
                                f" · 正在跑：{'、'.join(RUNNING.values()) or '（没有任务窗口）'}"
                                f" · 最后点的：{('「' + click + '」 ' + str(int(now - ct)) + ' 秒前') if click else '—'}\n")
                        faulthandler.dump_traceback(file=f, all_threads=True)
                        f.flush()
                    except Exception:
                        pass
                elif hung_since is not None and gap < 2:
                    try:
                        f.write(f"[{_dt.now():%Y-%m-%d %H:%M:%S}] 恢复：共卡了 {state['beat'] - hung_since:.0f} 秒\n")
                        f.flush()
                    except Exception:
                        pass
                    hung_since = None
        threading.Thread(target=watch, daemon=True, name="hang-watchdog").start()
    except Exception as e:
        file_logger.info(f"卡顿记录没开：{e}")


def main():
    if getattr(sys, "frozen", False):
        threading.Thread(target=_cleanup_old_temp, daemon=True).start()
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("procurement.system.v25")
        except Exception:
            pass
    # 后台线程（计算、比价）一直占着 Python 时，界面线程要排队等：把轮换间隔从默认 5 毫秒调到 1 毫秒，
    # 点「后台运行」以后界面还能正常点
    sys.setswitchinterval(0.001)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    _start_hang_watchdog(app)
    _start_stall_sampler(app)
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
    hub = _event_hub(app)                       # 全程序只装一个事件过滤器（AppEventHub），按事件类型分给下面几个
    app._wheel_guard = hub.add(WheelGuard(app))
    from erp_widgets import CopyHelper
    app._copy_helper = hub.add(CopyHelper(app))  # 程序里的文字都可以选中复制，表格 Ctrl+C / 右键复制
    from erp_widgets import ScreenFit
    app._screen_fit = hub.add(ScreenFit(app))    # 窗口打开时不会比屏幕大、不会跑出屏幕
    from erp_widgets import WidthMemory
    app._width_memory = hub.add(WidthMemory(app, settings.setdefault("table_widths", {}),
                                            lambda: save_settings(settings)))     # 所有表格拖过的列宽都记住
    theme.load_fonts()
    f = QFont()
    f.setFamilies([theme.LATIN_FONT, UI_FONT])
    f.setPointSize(10)
    app.setFont(theme.crisp(f))
    win = MainWindow()
    win.show()
    win._boot_shown = time.perf_counter() - _BOOT_T0
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
