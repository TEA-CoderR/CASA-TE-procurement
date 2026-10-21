"""
CASA&TE采购系统 · 设计规范（颜色、字体、样式表、图标）

浅色企业风为主（白底卡片、深蓝主色、深蓝导航），可切换深色。
所有界面模块都从这里取颜色：T("accent")、T("surface") …
"""
import os
import sys

from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QColor, QPainter, QPen, QIcon, QPixmap, QLinearGradient, QFont
from PySide6.QtSvg import QSvgRenderer

APP_VERSION = "v3.1.1"
UI_FONT = "Microsoft YaHei UI"
NUM_FONT = "Bahnschrift"

THEMES = {
    "light": {
        "bg": "#F3F5F9", "surface": "#FFFFFF", "surface2": "#F7F9FC", "line": "#E2E7EF",
        "text": "#0F172A", "muted": "#55657D", "faint": "#8592A6",
        "accent": "#1D5BD8", "accent_hover": "#1748B0", "accent_soft": "#E9F0FE", "on_accent": "#FFFFFF",
        "side": "#0B1B36", "side_text": "#B9C5DA", "side_muted": "#7F8EA8", "side_active": "#18325F",
        "side_bar": "#3B7BF0",
        "ok": "#0B7D5F", "ok_soft": "#E2F4EE", "warn": "#A85A07", "warn_soft": "#FDF1E0",
        "err": "#C02626", "err_soft": "#FDECEC", "info": "#1D5BD8", "info_soft": "#E9F0FE",
        "ghost": "#C7D7F7", "track": "#E8ECF2", "near": "#E7A23A", "far": "#B9C3D3",
        "shadow": "#0F172A",
    },
    "dark": {
        "bg": "#0A111E", "surface": "#111A2B", "surface2": "#0E1625", "line": "#1E2A3E",
        "text": "#E6EBF3", "muted": "#9AA8BE", "faint": "#66768F",
        "accent": "#6A9BF4", "accent_hover": "#86AEF7", "accent_soft": "#172848", "on_accent": "#0A111E",
        "side": "#060C17", "side_text": "#93A3BD", "side_muted": "#62728C", "side_active": "#152542",
        "side_bar": "#6A9BF4",
        "ok": "#3CC7A3", "ok_soft": "#0F2E28", "warn": "#EBA845", "warn_soft": "#32250E",
        "err": "#F27A7A", "err_soft": "#3A1717", "info": "#6A9BF4", "info_soft": "#172848",
        "ghost": "#24406E", "track": "#1A2436", "near": "#EBA845", "far": "#45536B",
        "shadow": "#000000",
    },
}

_state = {"theme": "light"}


def set_theme(name):
    _state["theme"] = "dark" if name == "dark" else "light"


def current():
    return _state["theme"]


def T(key):
    return THEMES[_state["theme"]][key]


def num_font(size=10, bold=False):
    f = QFont(NUM_FONT, size)
    f.setBold(bold)
    return f


def _arrow_svg(color):
    """下拉框箭头：按颜色生成一个小 SVG 放在临时目录（样式表只能引用文件）"""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"erp_arrow_{color.strip('#')}.svg")
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M1.5 3.5L5 7l3.5-3.5" '
                        f'fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')
        except OSError:
            return ""
    return path.replace("\\", "/")


def build_qss():
    t = THEMES[_state["theme"]]
    return f"""
    * {{ font-family: "{UI_FONT}", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans CJK TC", sans-serif; font-size: 13px; color: {t['text']}; }}
    QMainWindow, #Content {{ background: {t['bg']}; }}
    QDialog {{ background: {t['surface']}; }}
    QMenu {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 8px; padding: 6px; }}
    QMenu::item {{ padding: 7px 14px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {t['accent_soft']}; color: {t['accent']}; }}
    QMenu::separator {{ height: 1px; background: {t['line']}; margin: 4px 6px; }}

    /* ---------- 导航栏 ---------- */
    #Sidebar {{ background: {t['side']}; }}
    #Brand {{ color: #FFFFFF; font-size: 15px; font-weight: 700; }}
    #BrandSub {{ color: {t['side_muted']}; font-size: 11px; font-family: "{NUM_FONT}"; }}
    #NavGroup {{ color: {t['side_muted']}; font-size: 11px; padding: 10px 12px 3px 12px; }}
    #NavBtn {{ background: transparent; color: {t['side_text']}; border: none; border-radius: 7px;
               text-align: left; padding: 0 10px; min-height: 34px; max-height: 34px; font-size: 13px; }}
    #NavBtn:hover {{ background: {t['side_active']}; color: #FFFFFF; }}
    #NavBtn:checked {{ background: {t['side_active']}; color: #FFFFFF; font-weight: 600;
                       border-left: 3px solid {t['side_bar']}; padding-left: 7px; }}
    #NavBadge {{ background: #2E6BEA; color: #FFFFFF; border-radius: 8px; font-size: 11px; padding: 0 6px;
                 font-family: "{NUM_FONT}"; }}
    #NavBtn[open="true"] {{ color: #FFFFFF; font-weight: 600; }}
    #NavArrow {{ color: {t['side_muted']}; font-size: 14px; background: transparent; }}
    #NavSubBox {{ background: transparent; border: none; border-left: 1px solid {t['side_active']}; }}
    #NavSub {{ background: transparent; color: {t['side_text']}; border: none; border-radius: 6px;
               text-align: left; padding: 0 12px; min-height: 30px; max-height: 30px; font-size: 12px; margin-left: 6px; }}
    #NavSub:hover {{ background: {t['side_active']}; color: #FFFFFF; }}
    #NavSub:checked {{ background: {t['side_active']}; color: #FFFFFF; font-weight: 600; }}
    #NavSoon {{ background: rgba(255,255,255,0.08); color: {t['side_muted']}; border-radius: 8px; font-size: 10px; padding: 0 6px; }}
    #DbBox {{ background: rgba(255,255,255,0.05); border-radius: 9px; }}
    #DbTitle {{ color: #E6EBF3; font-size: 12px; }}
    #DbSub {{ color: {t['side_muted']}; font-size: 11px; font-family: "{NUM_FONT}"; }}

    /* ---------- 顶栏 ---------- */
    #TopBar {{ background: {t['surface']}; border-bottom: 1px solid {t['line']}; }}
    #Crumb {{ color: {t['muted']}; }}
    #CrumbCur {{ font-weight: 600; }}
    QPushButton#CrumbLink {{ font-weight: 600; background: transparent; border: none; padding: 0; color: {t['text']}; }}
    QPushButton#CrumbLink:hover {{ color: {t['accent']}; text-decoration: underline; }}
    QLineEdit#Search {{ background: {t['surface2']}; border: 1px solid {t['line']}; border-radius: 8px; padding: 6px 10px 6px 30px; }}
    QPushButton#TopBtn {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 8px; padding: 0;
                          min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; }}
    QPushButton#TopBtn:hover {{ border-color: {t['accent']}; }}
    QComboBox#LangBox {{ min-height: 20px; }}
    #Avatar {{ background: {t['accent_soft']}; color: {t['accent']}; border-radius: 15px; font-weight: 700; font-size: 12px; }}
    #UserName {{ font-weight: 600; font-size: 12px; }}
    #UserSub {{ color: {t['muted']}; font-size: 11px; }}

    /* ---------- 页面 ---------- */
    #PageTitle {{ font-size: 20px; font-weight: 700; }}
    #PageSub {{ color: {t['muted']}; font-size: 12px; }}
    #Panel {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 12px; }}
    #PanelTitle {{ font-size: 14px; font-weight: 600; }}
    #Muted {{ color: {t['muted']}; }}
    #Faint {{ color: {t['muted']}; font-size: 12px; }}
    #Hint {{ color: {t['muted']}; font-size: 12px; }}
    #Section {{ font-size: 15px; font-weight: 600; }}
    #ActionBar {{ background: {t['surface']}; border-top: 1px solid {t['line']}; }}
    #StepBar {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 10px; }}
    #StepCell {{ border-right: 1px solid {t['line']}; }}
    #StepCellOn {{ background: {t['accent_soft']}; border-right: 1px solid {t['line']}; border-bottom: 2px solid {t['accent']}; }}
    #StepTitle {{ font-size: 13px; font-weight: 600; }}
    #StepSub {{ color: {t['muted']}; font-size: 11px; }}
    #Empty {{ color: {t['muted']}; font-size: 13px; }}
    #EmptyTitle {{ font-size: 16px; font-weight: 600; }}

    /* ---------- 输入 ---------- */
    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
        background: {t['surface2']}; border: 1px solid {t['line']}; border-radius: 7px;
        padding: 6px 10px; selection-background-color: {t['accent']}; selection-color: #FFFFFF; }}
    QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {t['accent']}; }}
    QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {t['faint']}; }}
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
    QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
    QComboBox::down-arrow {{ image: url("{_arrow_svg(t['muted'])}"); width: 10px; height: 10px; margin-right: 8px; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['line']};
        selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}
    QCheckBox {{ spacing: 7px; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; }}

    /* ---------- 按钮 ---------- */
    QPushButton {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 8px;
                   padding: 7px 14px; }}
    QPushButton:hover {{ border-color: {t['accent']}; color: {t['accent']}; }}
    QPushButton:disabled {{ color: {t['faint']}; border-color: {t['line']}; }}
    QPushButton:checked {{ background: {t['accent_soft']}; color: {t['accent']}; border-color: {t['accent']}; font-weight: 600; }}
    QPushButton#Primary {{ background: {t['accent']}; color: {t['on_accent']}; border: none; font-weight: 600; padding: 8px 18px; }}
    QPushButton#Primary:hover {{ background: {t['accent_hover']}; color: {t['on_accent']}; }}
    QPushButton#Primary:disabled {{ background: {t['line']}; color: {t['faint']}; }}
    QPushButton#Outline {{ border: 1px solid {t['accent']}; color: {t['accent']}; font-weight: 600; }}
    QPushButton#Outline:hover {{ background: {t['accent_soft']}; }}
    QPushButton#Run {{ background: {t['accent']}; color: {t['on_accent']}; border: none; border-radius: 8px;
                       font-size: 15px; font-weight: 700; padding: 11px 30px; }}
    QPushButton#Run:hover {{ background: {t['accent_hover']}; }}
    QPushButton#Run:disabled {{ background: {t['line']}; color: {t['muted']}; }}
    QPushButton#Link {{ background: transparent; border: none; color: {t['accent']}; padding: 4px 6px; }}
    QPushButton#Link:hover {{ text-decoration: underline; }}
    QPushButton#Chip {{ border-radius: 14px; padding: 4px 12px; font-size: 12px; color: {t['muted']}; }}
    QPushButton#Chip:checked {{ background: {t['accent_soft']}; color: {t['accent']}; border-color: {t['accent']}; }}
    QPushButton#Danger:hover {{ border-color: {t['err']}; color: {t['err']}; }}
    QPushButton#DangerSolid {{ background: {t['err']}; color: #FFFFFF; border: none; font-weight: 600; }}
    QPushButton#DangerSolid:hover {{ background: {t['err']}; color: #FFFFFF; border: 2px solid {t['text']}; }}

    /* ---------- 进度 / 表格 / 其他 ---------- */
    QProgressBar {{ background: {t['track']}; border: none; border-radius: 4px; max-height: 8px; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
    QTextEdit {{ background: {t['surface2']}; border: 1px solid {t['line']}; border-radius: 8px; padding: 6px;
                 font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; }}
    QTableWidget, QListWidget, QTreeWidget {{ background: {t['surface']}; border: none; gridline-color: transparent;
                    selection-background-color: {t['accent_soft']}; selection-color: {t['text']};
                    alternate-background-color: {t['surface2']}; outline: none; }}
    QTableWidget::item {{ padding: 4px 10px; border-bottom: 1px solid {t['line']}; }}
    QListWidget::item {{ padding: 8px 10px; border-bottom: 1px solid {t['line']}; }}
    QTreeWidget::item {{ padding: 7px 6px; border-bottom: 1px solid {t['line']}; }}
    QTreeWidget::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
    QListWidget::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
    QHeaderView::section {{ background: {t['surface2']}; color: {t['muted']}; border: none;
                            border-bottom: 1px solid {t['line']}; padding: 9px 10px; font-weight: 500; font-size: 12px; }}
    QTableCornerButton::section {{ background: {t['surface2']}; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['faint']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t['line']}; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollArea {{ background: transparent; border: none; }}
    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{ background: transparent; padding: 7px 14px; margin-right: 2px; border-radius: 7px; color: {t['muted']}; }}
    QTabBar::tab:selected {{ background: {t['surface']}; color: {t['text']}; font-weight: 600; }}
    QRadioButton {{ spacing: 8px; }}
    QToolTip {{ background: {t['side']}; color: #FFFFFF; border: none; padding: 6px 8px; border-radius: 6px; }}
    #Banner {{ border-radius: 8px; padding: 10px 14px; }}
    #KpiValue {{ font-family: "{NUM_FONT}", "{UI_FONT}"; font-size: 24px; font-weight: 600; }}
    #KpiLabel {{ color: {t['muted']}; font-size: 12px; }}
    #Toast {{ background: {t['side']}; color: #FFFFFF; border-radius: 10px; padding: 11px 16px; font-weight: 600; }}
    #Dialog {{ background: {t['surface']}; }}
    #DialogFoot {{ background: {t['surface2']}; border-top: 1px solid {t['line']}; }}
    #WinTitle {{ font-size: 16px; font-weight: 700; }}
    #StatusBar {{ background: {t['surface2']}; border-top: 1px solid {t['line']}; }}
    #StatusText {{ color: {t['muted']}; font-size: 12px; }}
    #Drawer {{ background: {t['surface2']}; border-left: 1px solid {t['line']}; }}
    #MiniCard {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 9px; }}
    #DropZone {{ background: {t['surface2']}; border: 1px dashed {t['faint']}; border-radius: 10px; }}
    #DropZone[drag="true"] {{ background: {t['accent_soft']}; border: 1px solid {t['accent']}; }}
    QLineEdit[drag="true"] {{ background: {t['accent_soft']}; border: 1px solid {t['accent']}; }}
    #MiniLabel {{ color: {t['muted']}; font-size: 11px; }}
    #MiniValue {{ font-family: "{NUM_FONT}"; font-size: 18px; font-weight: 600; }}
    """


# =====================================================================
# 图标（线条风格）
# =====================================================================
ICONS = {
    "home": '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
    "extract": '<path d="M4 6h16M4 12h10M4 18h7M17 15l3 3-3 3"/>',
    "suggest": '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
    "warehouse": '<path d="M3 9l9-5 9 5v11H3z"/><path d="M8 20v-6h8v6"/>',
    "distribution": '<path d="M5 6h6v6H5zM13 12h6v6h-6z"/><path d="M11 9h3a2 2 0 0 1 2 2v1"/>',
    "import": '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
    "store_orders": '<path d="M3 7h18l-2 12H5z"/><path d="M8 7V5a4 4 0 0 1 8 0v2"/>',
    "order": '<path d="M7 4v16M17 4v16M3 8h8M13 16h8"/>',
    "result": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "tracking": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "rules": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "log": '<path d="M5 4h14v16H5z"/><path d="M9 9h6M9 13h6M9 17h3"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "folder": '<path d="M3 7h6l2 2h10v10H3z"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v4h4"/><path d="M12 7v5l3 2"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>',
    "check": '<path d="M5 12l5 5 9-10"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
    "table": '<path d="M4 5h16v14H4zM4 9h16M9 9v10"/>',
    "file": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>',
    "db": '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14-4.5L4 9M4 4v5h5M4 13a8 8 0 0 0 14 4.5l2-2.5M20 20v-5h-5"/>',
    "play": '<path d="M7 5l12 7-12 7z"/>',
    "box": '<path d="M3 8l9-5 9 5v8l-9 5-9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
}


def icon(name, color, size=20):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{ICONS.get(name, ICONS["file"])}</svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r.render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def app_icon():
    """深蓝圆角方块 + CT 字样"""
    pm = QPixmap(128, 128)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 128, 128)
    g.setColorAt(0, QColor("#2E6BEA"))
    g.setColorAt(1, QColor("#1748B0"))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(6, 6, 116, 116, 26, 26)
    f = QFont(NUM_FONT, 40)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QPen(QColor("#FFFFFF")))
    p.drawText(pm.rect(), Qt.AlignCenter, "CT")
    p.end()
    return QIcon(pm)


def open_path(path, parent=None):
    from PySide6.QtWidgets import QMessageBox
    from i18n import tr
    if not path or not os.path.exists(path):
        QMessageBox.information(parent, tr("CASA&TE采购系统"), tr("找不到：\n{0}", path))
        return
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        QMessageBox.warning(parent, tr("CASA&TE采购系统"), tr("打开失败：{0}", e))
