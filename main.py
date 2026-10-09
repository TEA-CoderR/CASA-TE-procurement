"""
采购系统 v2.5 — 桌面界面（PySide6 / Qt）
处理逻辑在 service.py / min_order_optimizer.py / matcher.py，本文件只负责界面。
"""
import os
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
from matcher import find_missing_items
from settings import load_settings, save_settings, match_rules, SETTINGS_PATH
from i18n import tr, kind_of, set_language, get_language, LANGS
from config import (ORDINE_FOLDER, ELIMINARE_FOLDER, DISTRIBUTUIRE_FOLDER,
                    LOG_DIR, LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT)

settings = load_settings()
set_language(settings.get("language", "zh"))
APP_NAME = tr("采购系统")
APP_VERSION = "v2.5"


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
# 主题
# =====================================================================
THEMES = {
    "light": {
        "bg": "#F3F4F1", "surface": "#FFFFFF", "surface2": "#F8F9F7", "line": "#DADDD6",
        "text": "#1C2430", "muted": "#66707C", "faint": "#9AA2AB",
        "accent": "#0E6E5C", "accent_hover": "#0B5B4C", "accent_soft": "#E2F0EC", "on_accent": "#FFFFFF",
        "side": "#18212B", "side_text": "#C9D1D9", "side_muted": "#7D8894", "side_active": "#243140",
        "ok": "#2F7D4A", "ok_soft": "#E5F2E9", "warn": "#B26B00", "warn_soft": "#FBF0DC",
        "err": "#B42318", "err_soft": "#FBE7E5", "info": "#2B5C8A", "info_soft": "#E4EDF6",
        "ghost": "#B9D7CF", "track": "#ECEEEA",
    },
    "dark": {
        "bg": "#12171D", "surface": "#1A2129", "surface2": "#151B22", "line": "#2A333D",
        "text": "#E3E7EA", "muted": "#9AA5B0", "faint": "#6B7682",
        "accent": "#3FB89C", "accent_hover": "#56C9AE", "accent_soft": "#173A33", "on_accent": "#0D1A16",
        "side": "#0E1318", "side_text": "#C9D1D9", "side_muted": "#6F7B87", "side_active": "#1C2630",
        "ok": "#5CC48A", "ok_soft": "#183424", "warn": "#E9A93B", "warn_soft": "#3A2C12",
        "err": "#F1786C", "err_soft": "#3D1B18", "info": "#7FB2E5", "info_soft": "#1B2C3D",
        "ghost": "#2C5A50", "track": "#232B34",
    },
}


def T(key):
    return THEMES["dark" if settings.get("theme") == "dark" else "light"][key]


UI_FONT = "Microsoft YaHei UI"
NUM_FONT = "Bahnschrift"


def build_qss():
    t = THEMES["dark" if settings.get("theme") == "dark" else "light"]
    return f"""
    * {{ font-family: "{UI_FONT}", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans CJK TC", sans-serif; font-size: 13px; color: {t['text']}; }}
    QMainWindow, #Content {{ background: {t['bg']}; }}
    QDialog {{ background: {t['surface']}; }}
    #Sidebar {{ background: {t['side']}; }}
    #Brand {{ color: #FFFFFF; font-size: 19px; font-weight: 700; }}
    #BrandSub {{ color: {t['side_muted']}; font-size: 11px; }}
    #NavBtn {{ background: transparent; color: {t['side_text']}; border: none; border-radius: 8px;
               text-align: left; padding: 10px 14px; font-size: 14px; }}
    #NavBtn:hover {{ background: {t['side_active']}; }}
    #NavBtn:checked {{ background: {t['side_active']}; color: #FFFFFF; font-weight: 600; }}
    #SideFoot {{ color: {t['side_muted']}; font-size: 11px; }}
    QComboBox#LangBox {{ background: {t['side_active']}; color: {t['side_text']}; border: none; padding: 5px 8px; }}
    QComboBox#LangBox QAbstractItemView {{ background: {t['side']}; color: {t['side_text']};
        selection-background-color: {t['side_active']}; }}
    #PageTitle {{ font-size: 24px; font-weight: 700; }}
    #PageSub {{ color: {t['muted']}; font-size: 13px; }}
    #Panel {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 10px; }}
    #PanelTitle {{ font-size: 14px; font-weight: 600; }}
    #Muted {{ color: {t['muted']}; }}
    #Faint {{ color: {t['faint']}; font-size: 12px; }}
    #Hint {{ color: {t['muted']}; font-size: 12px; }}
    QLineEdit, QDoubleSpinBox, QComboBox {{
        background: {t['surface2']}; border: 1px solid {t['line']}; border-radius: 6px;
        padding: 7px 10px; selection-background-color: {t['accent']}; }}
    QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {t['accent']}; }}
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['line']};
        selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}
    QPushButton {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 6px;
                   padding: 7px 14px; }}
    QPushButton:hover {{ border-color: {t['accent']}; color: {t['accent']}; }}
    QPushButton:disabled {{ color: {t['faint']}; border-color: {t['line']}; }}
    QPushButton:checked {{ background: {t['accent_soft']}; color: {t['accent']}; border-color: {t['accent']}; font-weight: 600; }}
    QPushButton#Primary {{ background: {t['accent']}; color: {t['on_accent']}; border: none; font-weight: 600; }}
    QPushButton#Primary:hover {{ background: {t['accent_hover']}; color: {t['on_accent']}; }}
    QPushButton#Primary:disabled {{ background: {t['line']}; color: {t['faint']}; }}
    QPushButton#Run {{ background: {t['accent']}; color: {t['on_accent']}; border: none; border-radius: 8px;
                       font-size: 16px; font-weight: 700; padding: 12px 34px; }}
    QPushButton#Run:hover {{ background: {t['accent_hover']}; }}
    QPushButton#Run:disabled {{ background: {t['line']}; color: {t['muted']}; }}
    QPushButton#Link {{ background: transparent; border: none; color: {t['accent']}; padding: 4px 6px; }}
    QPushButton#Link:hover {{ text-decoration: underline; }}
    QPushButton#Danger:hover {{ border-color: {t['err']}; color: {t['err']}; }}
    QPushButton#DangerSolid {{ background: {t['err']}; color: #FFFFFF; border: none; font-weight: 600; }}
    QPushButton#DangerSolid:hover {{ background: {t['err']}; color: #FFFFFF; border: 2px solid {t['text']}; }}
    QProgressBar {{ background: {t['track']}; border: none; border-radius: 4px; height: 8px; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
    QTextEdit {{ background: {t['surface2']}; border: 1px solid {t['line']}; border-radius: 8px; padding: 6px;
                 font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; }}
    QTableWidget {{ background: {t['surface']}; border: none; gridline-color: transparent;
                    selection-background-color: {t['accent_soft']}; selection-color: {t['text']};
                    alternate-background-color: {t['surface2']}; }}
    QTableWidget::item {{ padding: 4px 8px; border-bottom: 1px solid {t['line']}; }}
    QHeaderView::section {{ background: {t['surface']}; color: {t['muted']}; border: none;
                            border-bottom: 1px solid {t['line']}; padding: 8px; font-weight: 600; font-size: 12px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['faint']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t['line']}; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollArea {{ background: transparent; border: none; }}
    QRadioButton {{ spacing: 8px; }}
    QToolTip {{ background: {t['side']}; color: #FFFFFF; border: none; padding: 6px; }}
    #Banner {{ border-radius: 8px; padding: 10px 14px; }}
    #KpiValue {{ font-family: "{NUM_FONT}", "{UI_FONT}"; font-size: 26px; font-weight: 600; }}
    #KpiLabel {{ color: {t['muted']}; font-size: 12px; }}
    #Toast {{ background: {t['side']}; color: #FFFFFF; border-radius: 8px; padding: 10px 16px; }}
    """


# =====================================================================
# 图标（线条风格，按主题着色）
# =====================================================================
ICONS = {
    "order": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 13h6M10 17h6"/>',
    "result": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "rules": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "log": '<path d="M5 4h14v16H5z"/><path d="M9 9h6M9 13h6M9 17h3"/>',
    "folder": '<path d="M3 7h6l2 2h10v10H3z"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/>',
}


def icon(name, color, size=20):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r.render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def app_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#0E6E5C"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QPen(QColor("#FFFFFF"), 5, Qt.SolidLine, Qt.RoundCap))
    for i, h in enumerate((16, 28, 22)):
        x = 20 + i * 12
        p.drawLine(x, 48, x, 48 - h)
    p.end()
    return QIcon(pm)


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


def make_table(headers, stretch_col=None, numeric_cols=()):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setShowGrid(False)
    t.setAlternatingRowColors(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setDefaultSectionSize(38)
    t.setFocusPolicy(Qt.NoFocus)
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
    web_done = Signal(object)
    rewrite_done = Signal(object)


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
                cb.addItem(f"{short(s)}   €{p:,.2f}", s)
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
        lay.addLayout(bar)

        self.table = make_table([tr("行"), "EAN", "ARTICOLO", tr("箱数"), tr("最低价供应商"), tr("最低价"),
                                 tr("当前供应商"), tr("当前单价"), tr("比最低价多花 €")],
                                stretch_col=2, numeric_cols=(0, 3, 5, 7, 8))
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.setEditTriggers(QAbstractItemView.CurrentChanged | QAbstractItemView.DoubleClicked |
                                   QAbstractItemView.SelectedClicked)
        self.table.setItemDelegateForColumn(6, SupplierComboDelegate(self))
        lay.addWidget(self.table, 1)
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

    def _qty(self, r):
        try:
            return max(0.0, float(r["qty"] or 0))
        except (TypeError, ValueError):
            return 0.0

    def _fill_table(self):
        t = self.table
        t.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            self._set_row(i)

    def _set_row(self, i):
        r = self.rows[i]
        q = self._qty(r)
        b = self.baseline.get(r["row"])
        c = self.assign.get(r["row"])
        pb = r["prices"].get(b) if b else None
        pc = r["prices"].get(c) if c else None
        extra = (pc - pb) * q if (pb is not None and pc is not None) else None
        n_offers = sum(1 for p in r["prices"].values() if p is not None)
        vals = [r["row"], r.get("ean", ""), r.get("articolo", ""), f"{q:g}",
                short(b) if b else tr("无报价"), f"{pb:,.2f}" if pb is not None else "",
                (short(c) + ("　▾" if n_offers > 1 else "")) if c else tr("无报价"),
                f"{pc:,.2f}" if pc is not None else "",
                f"{extra:+,.2f}" if extra else ""]
        table_set_row(self.table, i, vals, color=None)
        self.table.item(i, 6).setData(Qt.UserRole, i)
        flags = self.table.item(i, 6).flags()
        if n_offers > 1:
            self.table.item(i, 6).setFlags(flags | Qt.ItemIsEditable)
            self.table.item(i, 6).setToolTip(tr("点击选择其他供应商"))
        if c != b:
            for col in range(9):
                self.table.item(i, col).setBackground(QColor(T("accent_soft")))
        if extra and extra > 0:
            self.table.item(i, 8).setForeground(QColor(T("warn")))

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
                    a += q * (r["prices"].get(ru["target"]) or 0)
            if c == 0:
                kind, st = "info", tr("不下单")
            elif _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"]):
                kind, st = "ok", tr("已达标")
            else:
                kind, st = "err", tr("未达标")
            lb.setText(tr("<b>{0}</b>　{1:,.0f} 箱 / €{2:,.2f}　要求 {3}　<b>{4}</b>", ru['name'], c, a, describe_condition(ru), st))
            lb.setStyleSheet(f"background:{T(kind + '_soft')}; color:{T(kind)};")
        cost = self._cost(self.assign)
        diff = cost - self.base_cost
        n = sum(1 for r in self.rows if self.assign.get(r["row"]) != self.baseline.get(r["row"]))
        self.total_lbl.setText(tr("采购总额 €{0:,.2f}　比只按最低价 {1:+,.2f}　改动 {2} 个商品", cost, diff, n))
        self.total_lbl.setStyleSheet(f"background:{T('surface2')}; color:{T('text')}; border:1px solid {T('line')};")

    def apply_filter(self, *_):
        text = self.filter.text().strip().lower()
        for i, r in enumerate(self.rows):
            n_offers = sum(1 for p in r["prices"].values() if p is not None)
            hide = False
            if self.only_multi.isChecked() and n_offers < 2:
                hide = True
            if self.only_changed.isChecked() and self.assign.get(r["row"]) == self.baseline.get(r["row"]):
                hide = True
            if text and not hide:
                hay = f"{r.get('ean', '')} {r.get('articolo', '')} {self.assign.get(r['row'], '')}".lower()
                hide = text not in hay
            self.table.setRowHidden(i, hide)

    def _ok(self):
        from min_order_optimizer import _satisfied
        bad = []
        for ru, _ in self.status_labels:
            c = a = 0.0
            for r in self.rows:
                if self.assign.get(r["row"]) == ru["target"]:
                    q = self._qty(r)
                    c += q
                    a += q * (r["prices"].get(ru["target"]) or 0)
            if c > 0 and not _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"]):
                bad.append(ru["name"])
        if bad and QMessageBox.question(self, tr("手动调整"), tr("{0} 还没有达到起订条件。仍然按现在的方案写入？", ", ".join(bad))) \
                != QMessageBox.Yes:
            return
        self.result = dict(self.assign)
        self.accept()


# =====================================================================
# 主窗口
# =====================================================================
class MainWindow(QMainWindow):
    NAV = [("order", tr("订单处理")), ("result", tr("分配结果")), ("search", tr("缺失查找")),
           ("rules", tr("起订量")), ("log", tr("运行日志"))]

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
        self.bridge.rewrite_done.connect(self._on_rewrite_done)

        self.log_entries = []      # [(level, text)]
        self.feed_entries = []     # 订单处理页的「处理过程」
        self.log_filter = "all"
        self.running = False
        self.run_start = 0.0
        self.info = {}
        self.last_summary = None

        root = QWidget()
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.setObjectName("Content")
        h.addWidget(self.stack, 1)
        self.pages = [self._page_order(), self._page_result(), self._page_missing(),
                      self._page_rules(), self._page_log()]
        for pg in self.pages:
            self.stack.addWidget(self._scroll(pg))

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
        self._load_saved_result()
        self.apply_theme()
        self.go(0)
        self.log(tr("{0} {1} 启动 {2:%Y-%m-%d %H:%M:%S}", APP_NAME, APP_VERSION, datetime.now()))

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
        lay.setContentsMargins(36, 30, 36, 30)
        lay.setSpacing(16)
        t = QLabel(title)
        t.setObjectName("PageTitle")
        s = QLabel(sub)
        s.setObjectName("PageSub")
        s.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(s)
        lay.addSpacing(4)
        return w, lay

    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(208)
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 22, 14, 16)
        v.setSpacing(4)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(36, 36))
        brand = QLabel(APP_NAME)
        brand.setObjectName("Brand")
        sub = QLabel(tr("订单扣减与供应商分配"))
        sub.setObjectName("BrandSub")
        sub.setWordWrap(True)
        top = QHBoxLayout()
        top.setContentsMargins(6, 0, 0, 0)
        top.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(brand)
        col.addWidget(sub)
        top.addLayout(col)
        top.addStretch()
        v.addLayout(top)
        v.addSpacing(26)

        self.nav_group = QButtonGroup(self)
        self.nav_btns = []
        for i, (ic, text) in enumerate(self.NAV):
            b = QPushButton(f"  {text}")
            b.setObjectName("NavBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(18, 18))
            b.clicked.connect(lambda _=False, i=i: self.go(i))
            self.nav_group.addButton(b, i)
            self.nav_btns.append((b, ic))
            b.toggled.connect(lambda on, b=b, ic=ic: b.setIcon(icon(ic, "#FFFFFF" if on else T("side_text"))))
            v.addWidget(b)
        v.addStretch()

        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("NavBtn")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.clicked.connect(self.toggle_theme)
        v.addWidget(self.theme_btn)
        lang_row = QHBoxLayout()
        lang_row.setContentsMargins(14, 6, 6, 0)
        ll = QLabel("🌐")
        ll.setObjectName("SideFoot")
        self.lang_box = QComboBox()
        self.lang_box.setObjectName("LangBox")
        for code, label in LANGS:
            self.lang_box.addItem(label, code)
        self.lang_box.setCurrentIndex(max(0, self.lang_box.findData(get_language())))
        self.lang_box.currentIndexChanged.connect(self.change_language)
        lang_row.addWidget(ll)
        lang_row.addWidget(self.lang_box, 1)
        v.addLayout(lang_row)
        foot = QLabel(APP_VERSION)
        foot.setObjectName("SideFoot")
        foot.setContentsMargins(14, 8, 0, 0)
        v.addWidget(foot)
        return side

    def go(self, i):
        self.stack.setCurrentIndex(i)
        self.nav_group.button(i).setChecked(True)

    def apply_theme(self):
        QApplication.instance().setStyleSheet(build_qss())
        for b, ic in self.nav_btns:
            b.setIcon(icon(ic, "#FFFFFF" if b.isChecked() else T("side_text")))
        dark = settings.get("theme") == "dark"
        self.theme_btn.setIcon(icon("sun" if dark else "moon", T("side_text")))
        self.theme_btn.setText(tr("  浅色模式") if dark else tr("  深色模式"))
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
        self.toast.setText(text)
        self.toast.adjustSize()
        self.toast.move(self.width() - self.toast.width() - 28, self.height() - self.toast.height() - 28)
        self.toast.raise_()
        self.toast.show()
        self.toast_timer.start(ms)

    # ---------- 页面：订单处理 ----------
    def _page_order(self):
        w, lay = self._page(tr("订单处理"), tr("选好总文件夹，确认起订规则，然后开始处理。程序会扣减已处理数量、按最低价选供应商、帮起订供应商凑单，最后把订购数量写回各供应商文件。"))

        p1 = Panel(tr("总文件夹"))
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

        facts = QHBoxLayout()
        facts.setSpacing(28)
        self.fact_order = self._fact(tr("主订单"), "—")
        self.fact_elim = self._fact(tr("扣减文件"), "—")
        self.fact_sup = self._fact(tr("供应商报价"), "—")
        for f in (self.fact_order, self.fact_elim, self.fact_sup):
            facts.addLayout(f[0])
        facts.addStretch()
        p1.lay.addLayout(facts)
        self.banner_folder = Banner()
        p1.lay.addWidget(self.banner_folder)
        lay.addWidget(p1)

        p2 = Panel(tr("处理选项"))
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
        opts.addStretch()
        edit_rules = QPushButton(tr("管理起订规则"))
        edit_rules.clicked.connect(lambda: self.go(3))
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
        w, lay = self._page(tr("分配结果"), tr("最近一次处理的结果。起订量调整的每一行明细写在主订单的 AC 列。"))
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
        b2.clicked.connect(lambda: open_path(os.path.join(self.folder_edit.text().strip(), DISTRIBUTUIRE_FOLDER)))
        self.btn_backup = QPushButton(tr("打开备份"))
        self.btn_backup.clicked.connect(lambda: self.last_summary and open_path(self.last_summary.get("backup_dir")))
        self.btn_backup.setEnabled(False)
        self.btn_edit = QPushButton(tr("修改分配…"))
        self.btn_edit.setToolTip(tr("已经写入以后，还可以改商品的供应商并重新写回文件"))
        self.btn_edit.clicked.connect(self.edit_allocation)
        self.btn_edit.setEnabled(False)
        acts.addWidget(b1)
        acts.addWidget(self.btn_edit)
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
        self.chg_table = make_table([tr("行"), "EAN", "ARTICOLO", tr("箱数"), tr("原供应商"), tr("原单价"), tr("新供应商"), tr("新单价"),
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

    def refresh_folder(self):
        path = self.folder_edit.text().strip()
        self.info = info = scan_folder(path)
        self.fact_order[1].setText(info["ordine"] or "—")
        self.fact_elim[1].setText(tr("{0} 个文件", len(info['eliminare'])) if path and os.path.isdir(path) else "—")
        self.fact_sup[1].setText(tr("{0} 家", len(info['suppliers'])) if info["suppliers"] else "—")
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

    def refresh_rule_line(self):
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

    def open_main_order(self):
        if self.info.get("ordine"):
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
               + tr("\n\n同一批文件重复处理会再扣减一次。"))
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
                                      settings=run_settings, decide=self._decide_from_worker)
            except Exception as e:
                file_logger.error(tr("处理失败: {0}", e), exc_info=True)
                self.log(tr("❌ 处理失败：{0}", e))
                summary = {"ok": False, "error": str(e)}
            self.bridge.done.emit(summary)

        threading.Thread(target=work, daemon=True).start()

    def _decide_from_worker(self, plan, rows, rules):
        """在后台线程里调用：把方案交给界面，等操作员决定后返回"""
        ev = threading.Event()
        box = {}
        self.bridge.decide.emit((plan, rows, rules, ev, box))
        ev.wait()
        return box.get("result")

    def _on_decide(self, payload):
        plan, rows, rules, ev, box = payload
        try:
            box["result"] = self._run_decision(plan, rows, rules)
        except Exception as e:
            file_logger.error(tr("决定方案出错: {0}", e), exc_info=True)
            QMessageBox.critical(self, APP_NAME, tr("计算方案时出错：{0}\n\n已改用程序自动算出的方案。", e))
            from allocation_plan import auto_assignment
            box["result"] = {"assignment": auto_assignment(rows, rules), "sources": {}}
        finally:
            ev.set()

    def _run_decision(self, plan, rows, rules):
        from allocation_plan import apply_decisions
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
        """打开程序时，载入这个文件夹上次的分配结果（可以继续修改）"""
        from service import load_state
        path = self.folder_edit.text().strip()
        if not path or self.last_summary:
            return
        data = load_state(path)
        if data and data.get("ok"):
            self.last_summary = data
            self.show_summary(data)

    def edit_allocation(self):
        from service import load_state
        if self.running:
            return
        data = self.last_summary if (self.last_summary or {}).get("state") else load_state(self.folder_edit.text().strip())
        if not data or not data.get("state"):
            QMessageBox.information(self, tr("修改分配"), tr("没有可以修改的分配。先完成一次订单处理。"))
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
        self.go(0)
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
        self.go(1)
        self.show_toast(tr("已重新写入 {0} 个商品", n))

    def _tick(self):
        self.elapsed.setText(tr("已用时 {0:.0f} 秒", time.time() - self.run_start))

    def _on_progress(self, pct, text):
        self.bar.setValue(pct)
        self.stage.setText(text)

    def _on_done(self, summary):
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
        self.go(1)
        self.show_toast(tr("处理完成"))

    def show_summary(self, s):
        mo = s.get("min_order", {})
        self.k_rows.set(f"{s.get('total_rows', 0):,}")
        self.k_priced.set(f"{s.get('priced_rows', 0):,}")
        self.k_missing.set(f"{s.get('missing', 0):,}", T("warn") if s.get("missing") else None)
        n_ch = len(s.get("changes") or [])
        self.k_changes.set(f"{n_ch:,}")
        extra = mo.get("extra_cost", 0) or 0
        self.k_cost.set(f"€{extra:,.2f}", T("warn") if extra > 0.005 else None)
        self.k_time.set(tr("{0:.0f} 秒", s.get('elapsed', 0)))

        msgs, kind = [], "ok"
        d, u = s.get("eliminare", (0, 0))
        msgs.append(tr("{0} 已处理：eliminare 删除 {1} 行、更新 {2} 行。", s.get('main_file'), d, u))
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
                                  f"{ch['old_price']:,.2f}" if ch["old_price"] is not None else "",
                                  short(ch["new"]) if ch["new"] else tr("不采购"),
                                  f"{ch['new_price']:,.2f}" if ch["new_price"] is not None else "",
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
        self.btn_edit.setEnabled(bool(s.get("state")) and not self.running)

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
        self.go(2)

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
            self.toast.move(self.width() - self.toast.width() - 28, self.height() - self.toast.height() - 28)


def main():
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
    f = QFont(UI_FONT, 10)
    app.setFont(f)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
