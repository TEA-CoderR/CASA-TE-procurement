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

APP_VERSION = "v3.3.25"
FONT_DIR = os.path.join(getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__)), "fonts")
# 中文字体：Windows 上用微软雅黑 UI（2026-11-16 用户：有的字看着糊——设计稿的 Noto Sans SC 没有屏幕微调信息，
# 在 Windows 的小字号下发虚；雅黑是专门为 Windows 屏幕调过的）；别的系统才用程序带的 Noto Sans SC
IS_WIN = sys.platform == "win32"
UI_FONT = "Microsoft YaHei UI" if IS_WIN or not os.path.exists(os.path.join(FONT_DIR, "NotoSansSC-Regular.ttf")) \
    else "Noto Sans SC"


def crisp(f):
    """Windows 上字按像素对齐（全微调）：小字更清楚，不发虚"""
    if IS_WIN:
        f.setHintingPreference(QFont.PreferFullHinting)
    return f
# 2026-11-15 第 3 轮（用户：要和设计稿一模一样）：程序自带设计稿的字体——英文 / 数字 Inter，数字列 JetBrains Mono（等宽，表格对得齐）；
# 中文没有自带，照旧微软雅黑（Inter 里没有的字 Qt 自动用后面的字体）
LATIN_FONT = "Inter" if os.path.exists(os.path.join(FONT_DIR, "Inter-Regular.ttf")) else UI_FONT
NUM_FONT = "JetBrains Mono" if os.path.exists(os.path.join(FONT_DIR, "JetBrainsMono-Medium.ttf")) else "Bahnschrift"
_fonts_loaded = []


def load_fonts():
    """把 fonts 文件夹里的字体登记给 Qt（要在 QApplication 建好以后；重复调用没事）"""
    if _fonts_loaded:
        return
    _fonts_loaded.append(True)
    try:
        from PySide6.QtGui import QFontDatabase
        for f in sorted(os.listdir(FONT_DIR)):
            if f.lower().endswith((".ttf", ".otf")) and not (IS_WIN and f.startswith("NotoSans")):
                QFontDatabase.addApplicationFont(os.path.join(FONT_DIR, f))
    except Exception:
        pass

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
        # 2026-11-15 新设计（企业级界面）多用的几个颜色；老的不改，总仓 / 门店采购两页照旧
        "canvas": "#F4F6FA", "border": "#D5DBE5", "head": "#344054", "hover": "#F1F5FB",
        "side2": "#0F2347", "side_line": "#1C3260", "accent_ring": "#BFD3FB",
        "cyan": "#0E7490", "cyan_soft": "#E0F5FA", "violet": "#6D28D9", "violet_soft": "#F1ECFE",
    },
    "dark": {          # 2026-11-15 第 2 轮（用户选的深色科技风）：深海军蓝底 + 电光蓝
        "bg": "#070B14", "surface": "#0C1222", "surface2": "#0E1424", "line": "#172036",
        "text": "#E6EBF5", "muted": "#8B98B0", "faint": "#6B7891",
        "accent": "#4F8CFF", "accent_hover": "#6E9FFF", "accent_soft": "#12204A", "on_accent": "#04101F",
        "side": "#0A0F1C", "side_text": "#8B98B0", "side_muted": "#6B7891", "side_active": "#16213A",
        "side_bar": "#4F8CFF",
        "ok": "#34D399", "ok_soft": "#0F2A20", "warn": "#FBBF24", "warn_soft": "#2A210A",
        "err": "#F87171", "err_soft": "#2A1215", "info": "#4F8CFF", "info_soft": "#12204A",
        "ghost": "#24406E", "track": "#1A2440", "near": "#EBA845", "far": "#45536B",
        "shadow": "#000000",
        "canvas": "#070B14", "border": "#1E2A44", "head": "#A9B6CE", "hover": "#111A2C",
        "side2": "#0A0F1C", "side_line": "#172036", "accent_ring": "#2B4374",
        "cyan": "#22D3EE", "cyan_soft": "#0B2530", "violet": "#A78BFA", "violet_soft": "#1E1838",
    },
}

# 2026-11-15 以前的深色（总仓 / 门店采购两页的旧样式表照这个；浅色没变，两页浅色和以前逐像素一样）
LEGACY_DARK = {
        "bg": "#0A111E", "surface": "#111A2B", "surface2": "#0E1625", "line": "#1E2A3E",
        "text": "#E6EBF3", "muted": "#9AA8BE", "faint": "#66768F",
        "accent": "#6A9BF4", "accent_hover": "#86AEF7", "accent_soft": "#172848", "on_accent": "#0A111E",
        "side": "#060C17", "side_text": "#93A3BD", "side_muted": "#62728C", "side_active": "#152542",
        "side_bar": "#6A9BF4",
        "ok": "#3CC7A3", "ok_soft": "#0F2E28", "warn": "#EBA845", "warn_soft": "#32250E",
        "err": "#F27A7A", "err_soft": "#3A1717", "info": "#6A9BF4", "info_soft": "#172848",
        "ghost": "#24406E", "track": "#1A2436", "near": "#EBA845", "far": "#45536B",
        "shadow": "#000000",
        "canvas": "#0A111E", "border": "#2A3850", "head": "#B4C0D3", "hover": "#16213A",
        "side2": "#0B1426", "side_line": "#18243A", "accent_ring": "#2B4A85",
}

_state = {"theme": "dark", "accent": "blue", "density": "normal"}

# 2026-11-16 设计稿「系统设置 → 外观」：主色五选一（蓝 / 青 / 紫 / 绿 / 橙），每种深浅两套
ACCENTS = (("blue", "蓝"), ("cyan", "青"), ("violet", "紫"), ("green", "绿"), ("orange", "橙"))
_ACCENT_RGB = {"dark": {"blue": "#4F8CFF", "cyan": "#22D3EE", "violet": "#A78BFA", "green": "#34D399", "orange": "#FB923C"},
               "light": {"blue": "#1D5BD8", "cyan": "#0E7490", "violet": "#6D28D9", "green": "#0B7D5F", "orange": "#C2410C"}}
_ACCENT_KEYS = ("accent", "accent_hover", "accent_soft", "on_accent", "info", "info_soft", "side_bar", "accent_ring")
_BASE = {k: {x: v[x] for x in _ACCENT_KEYS} for k, v in THEMES.items()}     # 蓝色（原来的）留一份


def _mix(a, b, t):
    """a 和 b 两种颜色按 t（0–1，b 的比例）混合"""
    a, b = a.lstrip("#"), b.lstrip("#")
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb))


def set_accent(name):
    """换主色：改 THEMES 里跟主色有关的几种颜色（蓝色 = 原样）"""
    name = name if name in _ACCENT_RGB["dark"] else "blue"
    _state["accent"] = name
    for mode, tok in THEMES.items():
        if name == "blue":
            tok.update(_BASE[mode])
            continue
        c = _ACCENT_RGB[mode][name]
        if mode == "dark":
            tok.update({"accent": c, "accent_hover": _mix(c, "#FFFFFF", 0.18), "accent_soft": _mix("#070B14", c, 0.16),
                        "on_accent": "#04101F", "info": c, "info_soft": _mix("#070B14", c, 0.16), "side_bar": c,
                        "accent_ring": _mix("#070B14", c, 0.38)})
        else:
            tok.update({"accent": c, "accent_hover": _mix(c, "#000000", 0.18), "accent_soft": _mix("#FFFFFF", c, 0.09),
                        "on_accent": "#FFFFFF", "info": c, "info_soft": _mix("#FFFFFF", c, 0.09), "side_bar": c,
                        "accent_ring": _mix("#FFFFFF", c, 0.28)})


def accent():
    return _state["accent"]


# 表格密度：紧凑 / 标准 / 舒适 = 行高 × 0.85 / 1 / 1.18（QHeaderView.setDefaultSectionSize 在 main 里统一按它放大缩小）
DENSITIES = (("compact", "紧凑", 0.85), ("normal", "标准", 1.0), ("comfy", "舒适", 1.18))


def set_density(name):
    _state["density"] = name if name in {k for k, _n, _f in DENSITIES} else "normal"


def density():
    return _state["density"]


def row_px(n):
    f = next((f for k, _n, f in DENSITIES if k == _state["density"]), 1.0)
    return max(18, round(n * f))


def set_theme(name):
    _state["theme"] = "dark" if name == "dark" else "light"


def current():
    return _state["theme"]


def T(key):
    return THEMES[_state["theme"]][key]


def _legacy_t():
    """旧样式表用的颜色：浅色 = 现在的浅色，深色 = 以前的深色"""
    return LEGACY_DARK if _state["theme"] == "dark" else THEMES["light"]


def num_font(size=10, bold=False):
    f = QFont(NUM_FONT, size)
    f.setBold(bold)
    return crisp(f)


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


def _check_svg(color):
    """勾选框里的对勾（白色或主题色），和下拉箭头一样生成小 SVG"""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"erp_check_{color.strip('#')}.svg")
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12"><path d="M2.5 6.2l2.4 2.4 4.6-5" '
                        f'fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>')
        except OSError:
            return ""
    return path.replace("\\", "/")


def build_qss_legacy():
    """2026-11-15 以前的样式。总仓采购 / 门店采购两页（用户：这两页很喜欢，不用动）套这一份，别的页面用新的 build_qss"""
    t = _legacy_t()
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
    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QDateTimeEdit {{
        background: {t['surface2']}; border: 1px solid {t['line']}; border-radius: 7px;
        padding: 6px 10px; selection-background-color: {t['accent']}; selection-color: #FFFFFF; }}
    QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus {{ border: 1px solid {t['accent']}; }}
    QDateTimeEdit::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
    QDateTimeEdit::down-arrow {{ image: url("{_arrow_svg(t['muted'])}"); width: 10px; height: 10px; margin-right: 8px; }}
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
    QTableWidget::item {{ padding: 4px 10px; border-bottom: 1px solid {t['line']}; border-right: 1px solid {t['line']}; }}
    QListWidget::item {{ padding: 8px 10px; border-bottom: 1px solid {t['line']}; }}
    QTreeWidget::item {{ padding: 7px 6px; border-bottom: 1px solid {t['line']}; }}
    QTreeWidget::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
    QListWidget::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
    QHeaderView::section {{ background: {t['surface2']}; color: {t['muted']}; border: none;
                            border-bottom: 1px solid {t['line']}; border-right: 1px solid {t['line']};
                            padding: 9px 10px; font-weight: 500; font-size: 12px; }}
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


_FROZEN = []          # 套旧样式的窗口 / 页面（weakref）：总仓采购 / 门店采购和它们的订货明细窗口


def _frozen_qss():
    """旧样式 + 把新样式里旧样式没写的那些属性改回旧的样子（Qt 里页面自己的样式表只在同一个属性上压过全局的，
    旧样式没写的属性会从全局新样式「漏」进来）"""
    t = _legacy_t()
    return build_qss_legacy() + f"""
    QPushButton, QPushButton#Link, QPushButton#Link:hover, #KpiLabel {{ font-weight: normal; }}
    QPushButton:hover, QPushButton:pressed, QPushButton:disabled {{ background: {t['surface']}; }}
    QPushButton#Link, QPushButton#Link:hover, QPushButton#Link:disabled {{ background: transparent; }}
    QPushButton#Danger {{ color: {t['text']}; }}
    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QDateTimeEdit {{ min-height: 0px; }}
    QLineEdit:hover, QDoubleSpinBox:hover, QSpinBox:hover, QComboBox:hover, QDateTimeEdit:hover {{ border: 1px solid {t['line']}; }}
    QComboBox QAbstractItemView {{ border-radius: 0px; padding: 0px; }}
    QToolButton:hover {{ background: transparent; border-color: transparent; }}
    QListWidget::item:hover, QTreeWidget::item:hover {{ background: transparent; }}
    QHeaderView::section:hover {{ background: {t['surface2']}; }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ margin: 0px; }}
    QTabWidget::pane {{ border: none; border-top: none; top: 0px; }}
    QTabBar::tab {{ border: none; border-bottom: none; margin-right: 2px; }}
    QTabBar::tab:selected {{ border-bottom: none; }}
    QTabBar::tab:hover {{ color: {t['muted']}; }}
    QTabBar::tab:selected:hover {{ color: {t['text']}; }}
    #Panel, #StepBar {{ border-bottom: 1px solid {t['line']}; }}
    #MiniCard:hover {{ border-color: {t['line']}; }}
    """


def freeze(widget):
    """这个页面 / 窗口照旧样式（build_qss_legacy）；换深浅色时 refresh_frozen 重新套"""
    import weakref
    widget.setStyleSheet(_frozen_qss())
    _FROZEN.append(weakref.ref(widget))
    return widget


def refresh_frozen():
    alive = []
    for r in _FROZEN:
        w = r()
        if w is None:
            continue
        try:
            w.setStyleSheet(_frozen_qss())
        except RuntimeError:            # C++ 对象已经删掉
            continue
        alive.append(r)
    _FROZEN[:] = alive


def build_qss():
    """
    企业级界面（2026-11-15 用户：其他所有界面做成世界级企业系统；总仓 / 门店采购不动 → 那两页套 build_qss_legacy）。
    规范：页面标题 22 / 区块标题 15 / 正文 13 / 辅助 12 / 表头 12 粗；圆角 卡片 12、按钮 / 输入框 8；
    按钮高 34（主按钮实心、次按钮白底灰边、文字按钮蓝字）；输入框白底灰边、聚焦蓝边；
    表格表头浅灰底深灰粗字、行高 38、浅色分隔线；标签页下划线式；卡片白底细边 + 底边稍深（像阴影）。
    """
    t = THEMES[_state["theme"]]
    dark = _state["theme"] == "dark"
    card_bottom = t["border"] if not dark else t["line"]
    return f"""
    * {{ font-family: "{LATIN_FONT}", "{UI_FONT}", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans CJK TC", sans-serif; font-size: 13px; color: {t['text']}; }}
    QMainWindow, #Content {{ background: {t['canvas']}; }}
    QDialog {{ background: {t['surface']}; }}
    QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 6px; }}
    QMenu::item {{ padding: 8px 16px 8px 12px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {t['hover']}; color: {t['accent']}; }}
    QMenu::item:disabled {{ color: {t['faint']}; }}
    QMenu::separator {{ height: 1px; background: {t['line']}; margin: 5px 8px; }}

    /* ---------- 左边图标栏 ---------- */
    QToolButton#RailAvatar {{ background: {t['side_active'] if dark else t['accent']}; color: {'#C9D4EA' if dark else '#FFFFFF'};
                              border: none; border-radius: 18px; font-weight: 700; font-size: 12px; }}
    QToolButton#RailAvatar:hover {{ background: {t['accent']}; color: {t['on_accent']}; }}
    QToolButton#RailAvatar::menu-indicator {{ image: none; width: 0; }}
    #Rail {{ background: {t['side']}; border-right: 1px solid {t['side_line']}; }}
    #RailLogo {{ background: {t['accent']}; color: {t['on_accent'] if dark else '#FFFFFF'}; border-radius: 12px;
                 font-family: "{NUM_FONT}"; font-size: 15px; font-weight: 700; }}
    QToolButton#RailBtn {{ background: transparent; color: {t['side_text']}; border: 1px solid transparent; border-radius: 12px;
                           font-size: 11px; padding: 6px 0 4px 0; }}
    QToolButton#RailBtn:hover {{ background: rgba(255,255,255,0.06); color: #FFFFFF; }}
    QToolButton#RailBtn:checked {{ background: {t['side_active']}; color: #FFFFFF; font-weight: 600;
                                   border: 1px solid {t['accent_ring'] if dark else t['side_line']}; }}
    #RailBadge {{ background: {t['err']}; color: #FFFFFF; border-radius: 9px; font-size: 10px; font-weight: 700;
                  padding: 0 5px; font-family: "{NUM_FONT}"; }}

    /* ---------- 顶栏 + 标签 ---------- */
    #TopBar {{ background: {t['side'] if dark else t['surface']}; border-bottom: 1px solid {t['line']}; }}
    #TopEyebrow {{ color: {t['muted']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
    #TopTitle {{ color: {t['text']}; font-size: 15px; font-weight: 700; }}
    QLineEdit#Search {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 10px;
                        padding: 9px 64px 9px 36px; color: {t['text']}; }}
    QLineEdit#Search:hover {{ border-color: {t['accent_ring']}; }}
    QLineEdit#Search:focus {{ border: 1px solid {t['accent']}; }}
    #KeyHint {{ color: {t['muted']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 1px 6px;
                font-family: "{NUM_FONT}"; font-size: 11px; background: transparent; }}
    #TabStrip {{ background: {t['side'] if dark else t['surface']}; border-bottom: 1px solid {t['line']}; }}
    QPushButton#TabBtn {{ background: transparent; border: none; border-radius: 0; border-bottom: 2px solid transparent;
                          color: {t['muted']}; padding: 11px 2px 11px 2px; font-weight: 500; min-height: 0; }}
    QPushButton#TabBtn:hover {{ background: transparent; color: {t['text']}; }}
    QPushButton#TabBtn:checked {{ background: transparent; color: {t['text']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}
    QPushButton#TopBtn {{ background: transparent; border: 1px solid transparent; border-radius: 9px; padding: 0;
                          min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; }}
    QPushButton#TopBtn:hover {{ background: {t['hover']}; border-color: {t['line']}; }}
    QPushButton#AgentBtn {{ background: {t['accent_soft']}; color: {t['accent'] if not dark else '#CFE0FF'}; border: 1px solid {t['accent_ring']};
                            border-radius: 10px; padding: 8px 14px; font-weight: 600; }}
    QPushButton#AgentBtn:hover {{ background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']}; }}
    QPushButton#AgentBtn:checked {{ background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']}; }}
    QComboBox#LangBox {{ min-height: 20px; background: transparent; border: 1px solid transparent; }}
    QComboBox#LangBox:hover {{ background: {t['hover']}; border-color: {t['line']}; }}
    #TopSep {{ background: {t['line']}; }}
    #Avatar {{ background: {t['side_active'] if dark else t['accent']}; color: {'#C9D4EA' if dark else '#FFFFFF'}; border-radius: 16px; font-weight: 700; font-size: 13px; }}
    #DbTitle {{ font-size: 12px; font-weight: 600; }}

    /* ---------- 页面 ---------- */
    #PageTitle {{ font-size: 22px; font-weight: 700; color: {t['text']}; }}
    #PageSub {{ color: {t['muted']}; font-size: 13px; }}
    #PageEyebrow {{ color: {t['accent']}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
    #Panel {{ background: {t['surface']}; border: 1px solid {t['line']}; border-bottom: 1px solid {card_bottom}; border-radius: 12px; }}
    #PanelTitle {{ font-size: 15px; font-weight: 600; }}
    #Muted {{ color: {t['muted']}; }}
    #Faint {{ color: {t['muted']}; font-size: 12px; }}
    #Hint {{ color: {t['muted']}; font-size: 12px; }}
    #Section {{ font-size: 15px; font-weight: 600; color: {t['text']}; }}
    #HeroTitle {{ font-size: 26px; font-weight: 700; color: {t['text']}; }}
    #HeroSub {{ color: {t['head']}; font-size: 13px; }}
    #StatVal {{ font-family: "{NUM_FONT}", "{UI_FONT}"; font-size: 24px; font-weight: 600; color: {t['text']}; }}
    QTextEdit#LogView {{ font-family: "{NUM_FONT}", "{UI_FONT}"; font-size: 12px; background: {t['surface']}; border: none; }}
    #ActionBar {{ background: {t['surface']}; border-top: 1px solid {t['line']}; }}
    #StepBar {{ background: {t['surface']}; border: 1px solid {t['line']}; border-bottom: 1px solid {card_bottom}; border-radius: 12px; }}
    #StepCell {{ border-right: 1px solid {t['line']}; }}
    #StepCellOn {{ background: {t['accent_soft']}; border-right: 1px solid {t['line']}; border-bottom: 2px solid {t['accent']}; }}
    #StepTitle {{ font-size: 13px; font-weight: 600; }}
    #StepSub {{ color: {t['muted']}; font-size: 11px; }}
    #Empty {{ color: {t['muted']}; font-size: 13px; }}
    #EmptyTitle {{ font-size: 16px; font-weight: 600; }}

    /* ---------- 输入 ---------- */
    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QDateTimeEdit {{
        background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px;
        padding: 6px 10px; min-height: 20px; selection-background-color: {t['accent']}; selection-color: #FFFFFF; }}
    QLineEdit:hover, QDoubleSpinBox:hover, QSpinBox:hover, QComboBox:hover, QDateTimeEdit:hover {{ border: 1px solid {t['faint']}; }}
    QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus {{ border: 1px solid {t['accent']}; }}
    QLineEdit:read-only {{ background: {t['surface2']}; }}
    QDateTimeEdit::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
    QDateTimeEdit::down-arrow {{ image: url("{_arrow_svg(t['muted'])}"); width: 10px; height: 10px; margin-right: 8px; }}
    QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {{ color: {t['faint']}; background: {t['surface2']}; border-color: {t['line']}; }}
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
    QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
    QComboBox::down-arrow {{ image: url("{_arrow_svg(t['muted'])}"); width: 10px; height: 10px; margin-right: 8px; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 4px;
        selection-background-color: {t['hover']}; selection-color: {t['accent']}; outline: none; }}
    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 1.5px solid {t['border']}; border-radius: 4px; background: {t['surface']}; }}
    QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
    QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url("{_check_svg(t['on_accent'])}"); }}
    QCheckBox::indicator:indeterminate {{ background: {t['accent_soft']}; border-color: {t['accent']}; }}
    QCheckBox::indicator:disabled {{ background: {t['surface2']}; border-color: {t['line']}; }}
    QCheckBox::indicator:checked:disabled {{ background: {t['faint']}; border-color: {t['faint']}; }}
    QRadioButton::indicator {{ width: 16px; height: 16px; border: 1.5px solid {t['border']}; border-radius: 9px; background: {t['surface']}; }}
    QRadioButton::indicator:hover {{ border-color: {t['accent']}; }}
    QRadioButton::indicator:checked {{ border: 5px solid {t['accent']}; background: {t['surface']}; width: 8px; height: 8px; }}
    QRadioButton::indicator:disabled {{ background: {t['surface2']}; border-color: {t['line']}; }}
    QTableView::indicator, QListView::indicator, QTreeView::indicator {{ width: 15px; height: 15px; border: 1.5px solid {t['border']};
        border-radius: 4px; background: {t['surface']}; }}
    QTableView::indicator:checked, QListView::indicator:checked, QTreeView::indicator:checked {{ background: {t['accent']};
        border-color: {t['accent']}; image: url("{_check_svg(t['on_accent'])}"); }}

    /* ---------- 按钮 ---------- */
    QPushButton {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px;
                   padding: 7px 14px; font-weight: 500; }}
    QPushButton:hover {{ background: {t['hover']}; border-color: {t['faint']}; color: {t['text']}; }}
    QPushButton:pressed {{ background: {t['track']}; }}
    QPushButton:disabled {{ color: {t['faint']}; border-color: {t['line']}; background: {t['surface2']}; }}
    QPushButton:checked {{ background: {t['accent_soft']}; color: {t['accent']}; border-color: {t['accent']}; font-weight: 600; }}
    QPushButton#Primary {{ background: {t['accent']}; color: {t['on_accent']}; border: 1px solid {t['accent']}; font-weight: 600; padding: 7px 18px; }}
    QPushButton#Primary:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; color: {t['on_accent']}; }}
    QPushButton#Primary:pressed {{ background: {t['accent_hover']}; }}
    QPushButton#Primary:disabled {{ background: {t['track']}; border-color: {t['track']}; color: {t['faint']}; }}
    QPushButton#Outline {{ border: 1px solid {t['accent']}; color: {t['accent']}; font-weight: 600; background: {t['surface']}; }}
    QPushButton#Outline:hover {{ background: {t['accent_soft']}; color: {t['accent']}; }}
    QPushButton#Run {{ background: {t['accent']}; color: {t['on_accent']}; border: none; border-radius: 10px;
                       font-size: 15px; font-weight: 700; padding: 11px 30px; }}
    QPushButton#Run:hover {{ background: {t['accent_hover']}; }}
    QPushButton#Run:disabled {{ background: {t['track']}; color: {t['faint']}; }}
    /* 次要操作（以前是一行蓝字链接，用户 11-18：不正式）→ 小号描边按钮，和别的按钮一个家族 */
    QPushButton#Link {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 7px; color: {t['accent']};
                        padding: 4px 11px; font-weight: 600; }}
    QPushButton#Link:hover {{ background: {t['accent_soft']}; border-color: {t['accent']}; color: {t['accent']}; }}
    QPushButton#Link:disabled {{ background: {t['surface2']}; border-color: {t['line']}; color: {t['faint']}; }}
    QPushButton#Chip {{ border-radius: 15px; padding: 5px 13px; font-size: 12px; color: {t['muted']}; background: {t['surface']}; }}
    QPushButton#Chip:hover {{ color: {t['text']}; }}
    QPushButton#Chip:checked {{ background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']}; }}
    QPushButton#Danger {{ color: {t['err']}; }}
    QPushButton#Danger:hover {{ border-color: {t['err']}; color: {t['err']}; background: {t['err_soft']}; }}
    QPushButton#DangerSolid {{ background: {t['err']}; color: #FFFFFF; border: 1px solid {t['err']}; font-weight: 600; }}
    QPushButton#DangerSolid:hover {{ background: {t['err']}; color: #FFFFFF; border: 2px solid {t['text']}; }}
    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }}
    QToolButton:hover {{ background: {t['hover']}; border-color: {t['line']}; }}

    /* ---------- 进度 / 表格 / 其他 ---------- */
    QProgressBar {{ background: {t['track']}; border: none; border-radius: 4px; max-height: 8px; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
    QTextEdit, QPlainTextEdit {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 8px;
                 font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; }}
    QTableWidget, QTableView, QListWidget, QTreeWidget {{ background: {t['surface']}; border: none; gridline-color: transparent;
                    selection-background-color: {t['accent_soft']}; selection-color: {t['text']};
                    alternate-background-color: {t['surface2']}; outline: none; }}
    QTableWidget::item {{ padding: 4px 10px; border-bottom: 1px solid {t['line']}; border-right: 1px solid {t['line']}; }}
    QListWidget::item {{ padding: 9px 10px; border-bottom: 1px solid {t['line']}; }}
    QListWidget::item:hover, QTreeWidget::item:hover {{ background: {t['hover']}; }}
    QTreeWidget::item {{ padding: 7px 6px; border-bottom: 1px solid {t['line']}; }}
    QTreeWidget::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
    QListWidget::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
    QHeaderView {{ background: {t['surface2']}; }}
    QHeaderView::section {{ background: {t['surface2']}; color: {t['head']}; border: none;
                            border-bottom: 1px solid {t['border']}; border-right: 1px solid {t['line']};
                            padding: 10px 10px; font-weight: 600; font-size: 12px; }}
    QHeaderView::section:hover {{ background: {t['hover']}; }}
    QTableCornerButton::section {{ background: {t['surface2']}; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 3px; min-height: 30px; margin: 0 2px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['faint']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t['border']}; border-radius: 3px; min-width: 30px; margin: 2px 0; }}
    QScrollBar::handle:horizontal:hover {{ background: {t['faint']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QScrollArea {{ background: transparent; border: none; }}
    QTabWidget::pane {{ border: none; border-top: 1px solid {t['line']}; top: -1px; }}
    QTabWidget::tab-bar {{ left: 0; }}
    QTabBar {{ background: transparent; }}
    QTabBar::tab {{ background: transparent; padding: 9px 2px 10px 2px; margin-right: 22px; border: none;
                    border-bottom: 2px solid transparent; color: {t['muted']}; font-weight: 500; }}
    QTabBar::tab:hover {{ color: {t['text']}; }}
    QTabBar::tab:selected {{ color: {t['accent']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}
    QToolTip {{ background: {t['side']}; color: #FFFFFF; border: none; padding: 7px 10px; border-radius: 7px; }}
    #Banner {{ border-radius: 10px; padding: 11px 14px; }}
    #KpiCard {{ background: {t['surface']}; border: 1px solid {t['line']}; border-bottom: 1px solid {card_bottom}; border-radius: 12px; }}
    #KpiValue {{ font-family: "{NUM_FONT}", "{UI_FONT}"; font-size: 26px; font-weight: 600; }}
    #KpiLabel {{ color: {t['muted']}; font-size: 12px; font-weight: 600; }}
    #Toast {{ background: {t['side']}; color: #FFFFFF; border-radius: 10px; padding: 11px 16px; font-weight: 600; }}
    #Dialog {{ background: {t['surface']}; }}
    #DialogFoot {{ background: {t['surface2']}; border-top: 1px solid {t['line']}; }}
    #WinTitle {{ font-size: 17px; font-weight: 700; }}
    #StatusBar {{ background: {t['side'] if dark else t['surface']}; border-top: 1px solid {t['line']}; }}
    QTableWidget#ReviewTable, QTableWidget#CleanTable {{ background: {t['canvas']}; selection-background-color: {'#101B33' if dark else t['accent_soft']}; }}
    QTableWidget#ReviewTable::item, QTableWidget#CleanTable::item {{ border-right: none; border-bottom: 1px solid {'#121A2D' if dark else t['line']}; padding: 4px 10px; }}
    #ReviewTable QHeaderView::section, #CleanTable QHeaderView::section {{ background: {'#0A1020' if dark else t['surface2']}; color: {t['muted']};
        font-size: 11px; font-weight: 500; border: none; border-bottom: 1px solid {t['line']}; padding: 9px 10px; }}
    #ReviewTable QHeaderView, #CleanTable QHeaderView {{ background: {'#0A1020' if dark else t['surface2']}; }}
    QMenu#Flyout {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 12px; padding: 8px; }}
    QMenu#Flyout::item {{ padding: 9px 28px 9px 14px; border-radius: 8px; font-size: 13px; }}
    QMenu#Flyout::item:selected {{ background: {t['hover']}; color: {t['text']}; }}
    QMenu#Flyout::item:checked {{ color: {t['accent']}; font-weight: 600; }}
    QPushButton#TopCrumb {{ background: transparent; border: none; padding: 0; color: {t['muted']}; font-size: 11px; font-weight: 500; text-align: left; min-height: 0; }}
    QPushButton#TopCrumb:hover {{ background: transparent; color: {t['accent']}; }}
    #StoreSide {{ background: {t['side'] if dark else t['surface2']}; border: none; border-right: 1px solid {t['line']}; }}
    #SideEyebrow {{ color: {t['muted']}; font-size: 11px; font-weight: 700; letter-spacing: 1.5px; margin: 0 4px 4px 4px; }}
    QPushButton#StoreCard {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 12px; padding: 0; text-align: left; }}
    QPushButton#StoreCard:hover {{ border-color: {t['accent_ring']}; background: {t['surface']}; }}
    QPushButton#StoreCard:checked {{ background: {t['accent_soft'] if not dark else '#101B33'}; border: 1px solid {t['accent_ring']}; }}
    #StoreName {{ font-weight: 600; background: transparent; }}
    #StoreSub {{ color: {t['muted']}; font-size: 11px; font-family: "{NUM_FONT}"; background: transparent; }}
    QPushButton#ViewChip {{ background: transparent; border: 1px solid {t['border']}; border-radius: 9px; padding: 6px 12px;
                            font-size: 12px; color: {t['muted']}; font-weight: 500; }}
    QPushButton#ViewChip:hover {{ border-color: {t['accent_ring']}; color: {t['text']}; background: transparent; }}
    QPushButton#ViewChip:checked {{ border-color: {t['accent']}; background: {t['accent_soft']}; color: {t['accent'] if not dark else '#CFE0FF'}; font-weight: 600; }}
    QPushButton#ShopChip {{ background: transparent; border: 1px solid {t['border']}; border-radius: 17px; padding: 0 14px;
                            min-height: 32px; max-height: 34px; font-size: 12px; color: {t['head']}; font-weight: 500; }}
    QPushButton#ShopChip:hover {{ border-color: {t['accent_ring']}; color: {t['text']}; background: transparent; }}
    QPushButton#ShopChip:checked {{ border-color: {t['accent']}; background: {t['accent_soft']}; color: {t['accent'] if not dark else '#CFE0FF'}; font-weight: 600; }}
    #Seg {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 10px; }}
    QPushButton#SegBtn {{ background: transparent; border: none; border-radius: 7px; padding: 0 12px; min-height: 28px; max-height: 28px;
                          font-size: 12px; color: {t['muted']}; font-weight: 500; }}
    QPushButton#SegBtn:hover {{ color: {t['text']}; background: transparent; }}
    QPushButton#SegBtn:checked {{ background: {t['accent_soft']}; color: {t['accent'] if not dark else '#CFE0FF'}; font-weight: 600; }}
    #StatusText {{ color: {t['muted']}; font-size: 12px; }}
    #Drawer {{ background: {t['surface2']}; border-left: 1px solid {t['line']}; }}
    #MiniCard {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 10px; }}
    #MiniCard:hover {{ border-color: {t['border']}; }}
    #DropZone {{ background: {t['surface2']}; border: 1px dashed {t['border']}; border-radius: 12px; }}
    #DropZone[drag="true"] {{ background: {t['accent_soft']}; border: 1px solid {t['accent']}; }}
    QLineEdit[drag="true"] {{ background: {t['accent_soft']}; border: 1px solid {t['accent']}; }}
    #MiniLabel {{ color: {t['muted']}; font-size: 11px; }}
    #MiniValue {{ font-family: "{NUM_FONT}"; font-size: 18px; font-weight: 600; }}
    #Tile {{ background: {t['surface']}; border: 1px solid {t['line']}; border-bottom: 1px solid {card_bottom}; border-radius: 12px; }}
    #Tile:hover {{ border-color: {t['accent_ring']}; background: {t['hover']}; }}
    #TileTitle {{ font-size: 13px; font-weight: 600; }}
    #TileSub {{ color: {t['muted']}; font-size: 11px; }}
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
    "sellout": '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    "volantino": '<path d="M3 10v4h3l7 4V6L6 10z"/><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a7.5 7.5 0 0 1 0 11"/>',
    "griglia": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M4 15h16M10 4v16"/>',
    "store_pdv": '<path d="M4 10l1.5-5h13L20 10"/><path d="M4 10v9h16v-9"/><path d="M9 19v-5h6v5"/>',
    "truck": '<path d="M3 6h11v10H3zM14 9h4l3 3v4h-7"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
    "spark": '<path d="M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z"/>',
    "cart": '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.5 12h12l2-8H6.2"/>',
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
