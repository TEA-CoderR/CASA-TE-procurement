"""
系统设置（2026-11-16 照设计稿「系统设置」重做）：
  左边一列分类：常用 · 数据连接 · 订货算法 · 起订规则 · 邮箱 · 采购助手 · 运行日志（有要处理的在后面带黄点）
  右边一块块卡片：
    常用      数据连接（销售数据库 / 公司邮箱 / 共享文件夹 · 状态小标签 · 管理）、外观（深色 / 浅色 / 跟随系统、主色、表格密度、语言）、
              公司统一设置（已是最新 / 这台电脑改过几项 · 查看改了什么）、关于（版本、更新）
    数据连接  数据库详情、文件位置、共享文件夹
    订货算法  算法 + 算法设置…、门店与仓库
    起订规则  DETERSIVO 起订规则、总仓采购起订量
    邮箱 / 采购助手 / 运行日志
"""
import os

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
                               QLineEdit, QFileDialog, QComboBox, QCheckBox, QStackedWidget, QScrollArea,
                               QButtonGroup, QMessageBox, QDialog, QTableWidget, QHeaderView, QSizePolicy)

from i18n import tr
from settings import save_settings, SETTINGS_PATH
from theme import APP_VERSION
from erp_widgets import chip, set_chip
import store_orders as S


def _T(k):
    from theme import T
    return T(k)


def _card(title, sub=""):
    f = QFrame()
    f.setObjectName("SetCard")
    v = QVBoxLayout(f)
    v.setContentsMargins(20, 16, 20, 18)
    v.setSpacing(14)
    t = QLabel(title + ("  ⓘ" if sub else ""))
    t.setObjectName("SetCardTitle")
    if sub:
        t.setToolTip(sub)              # 界面要简洁：说明放在鼠标提示里
    v.addWidget(t)
    return f, v


def _row_label(text, tip=""):
    lb = QLabel(text + ("  ⓘ" if tip else ""))
    lb.setObjectName("SetRowLabel")
    lb.setFixedWidth(88)
    if tip:
        lb.setToolTip(tip)
    return lb


def settings_diff(cur, company, path=""):
    """这台电脑和公司统一设置不一样的地方：[(位置, 这台电脑, 公司)]；只比公司设置里有的键"""
    out = []
    for k, v in (company or {}).items():
        p = f"{path}.{k}" if path else str(k)
        mine = (cur or {}).get(k) if isinstance(cur, dict) else None
        if isinstance(v, dict) and isinstance(mine, dict):
            out += settings_diff(mine, v, p)
        elif mine != v:
            out.append((p, mine, v))
    return out


# =====================================================================
class _ThemeCard(QPushButton):
    """外观里的一张主题小卡：上面画一个缩小的界面（侧栏 + 两行），下面名字 + 单选圈"""

    def __init__(self, key, name):
        super().__init__()
        self.key, self.name = key, name
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(126)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setObjectName("ThemeCard")

    def paintEvent(self, _e):
        from theme import THEMES
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        on = self.isChecked()
        if on:
            glow = QColor(_T("accent"))
            glow.setAlpha(45)
            p.setPen(QPen(glow, 4))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 12, 12)
        p.setPen(QPen(QColor(_T("accent") if on else (_T("accent_ring") if self.underMouse() else _T("border"))), 1))
        p.setBrush(QColor(_T("surface2")))
        p.drawRoundedRect(r, 12, 12)
        pv = QRectF(r.left() + 10, r.top() + 10, r.width() - 20, 74)
        d, l = THEMES["dark"], THEMES["light"]
        if self.key == "dark":
            side, body, line, acc = d["side"], d["surface2"], d["border"], d["accent"]
        elif self.key == "light":
            side, body, line, acc = l["canvas"], l["surface"], l["line"], l["accent"]
        else:
            side, body, line, acc = d["side"], l["surface"], "#C9D4EA", d["accent"]
        p.setClipRect(pv)
        p.setPen(QPen(QColor(_T("border")), 1))
        p.setBrush(QColor(body))
        p.drawRoundedRect(pv, 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(side))
        p.drawRect(QRectF(pv.left(), pv.top(), pv.width() * 0.22, pv.height()))
        x0 = pv.left() + pv.width() * 0.22 + 8
        w0 = pv.right() - x0 - 8
        p.setBrush(QColor(acc))
        p.drawRoundedRect(QRectF(x0, pv.top() + 8, w0 * 0.6, 8), 4, 4)
        p.setBrush(QColor(line))
        for k in (1, 2):
            p.drawRoundedRect(QRectF(x0, pv.top() + 8 + 13 * k, w0 * 0.9, 8), 4, 4)
        p.setClipping(False)
        p.setPen(QColor(_T("text")))
        f = self.font()
        f.setWeight(f.Weight.Medium)
        p.setFont(f)
        p.drawText(QRectF(r.left() + 12, pv.bottom() + 8, r.width() - 50, 22), Qt.AlignLeft | Qt.AlignVCenter, self.name)
        c = QRectF(r.right() - 28, pv.bottom() + 11, 16, 16)
        if on:
            p.setPen(QPen(QColor(_T("accent")), 5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c.adjusted(2.5, 2.5, -2.5, -2.5))
        else:
            p.setPen(QPen(QColor(_T("border")), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c.adjusted(1, 1, -1, -1))
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class _Swatch(QPushButton):
    """主色圆点"""

    def __init__(self, key, name):
        super().__init__()
        self.key = key
        self.setCheckable(True)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(name)
        self.setObjectName("Swatch")

    def paintEvent(self, _e):
        from theme import _ACCENT_RGB, current
        c = QColor(_ACCENT_RGB[current()][self.key])
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self.isChecked():
            halo = QColor(c)
            halo.setAlpha(100)
            p.setPen(QPen(halo, 3))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5))
            p.setPen(QPen(QColor(_T("text")), 2))
            p.setBrush(c)
            p.drawEllipse(r.adjusted(3.5, 3.5, -3.5, -3.5))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(r.adjusted(2, 2, -2, -2))
        p.end()


class _Cat(QPushButton):
    """左边分类：名字 + 有要处理的时候一个黄点"""

    def __init__(self, text):
        super().__init__(text)
        self.setObjectName("SetCat")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.dot = False

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.dot:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(_T("warn")))
            p.drawEllipse(QRectF(self.width() - 20, self.height() / 2 - 3.5, 7, 7))
            p.end()


class _ConnRow(QFrame):
    """数据连接里的一行：彩色小方块 · 名称 ⓘ / 小字 · 状态 · 管理"""
    manage = Signal()

    def __init__(self, abbr, tok, name, tip):
        super().__init__()
        self.setObjectName("ConnRow")
        self.tok = tok
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 12, 20, 12)
        h.setSpacing(14)
        self.ic = QLabel(abbr)
        self.ic.setFixedSize(36, 36)
        self.ic.setAlignment(Qt.AlignCenter)
        h.addWidget(self.ic)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        self.name = QLabel(name + "  ⓘ")
        self.name.setObjectName("ConnName")
        self.name.setToolTip(tip)
        self.sub = QLabel("")
        self.sub.setObjectName("ConnSub")
        tv.addWidget(self.name)
        tv.addWidget(self.sub)
        h.addLayout(tv, 1)
        self.state = QLabel("")
        h.addWidget(self.state)
        b = QPushButton(tr("管理"))
        b.setObjectName("SetGhost")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self.manage)
        h.addWidget(b)

    def set(self, sub, state, tone):
        self.sub.setText(sub)
        self.sub.setToolTip(sub)
        self.state.setText(state)
        self._tone = tone
        self.restyle()

    def restyle(self):
        c = QColor(_T(self.tok))
        bg, edge = QColor(c), QColor(c)
        bg.setAlpha(26)
        edge.setAlpha(64)
        rgba = lambda q: f"rgba({q.red()},{q.green()},{q.blue()},{q.alpha()})"
        self.ic.setStyleSheet(f"border-radius:10px; font-weight:700; font-size:12px; color:{c.name()};"
                              f" background:{rgba(bg)}; border:1px solid {rgba(edge)};")
        tone = getattr(self, "_tone", "muted")
        fg = _T(tone) if tone != "muted" else _T("head")
        sbg = _T(tone + "_soft") if tone in ("ok", "warn", "err") else _T("surface2")
        self.state.setStyleSheet(f"padding:3px 10px; border-radius:11px; font-size:12px; color:{fg}; background:{sbg};")


class _DiffDialog(QDialog):
    """「查看改了什么」：这台电脑和公司统一设置不一样的地方"""

    def __init__(self, parent, rows, ver):
        super().__init__(parent)
        from erp_widgets import TableSortFilter, fit_to_screen
        self.setWindowTitle(tr("公司统一设置"))
        self.resize(760, 460)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(10)
        head = QLabel(tr("公司统一设置第 {0} 版 · 这台电脑有 {1} 项不一样", ver, len(rows)) + "  ⓘ")
        head.setObjectName("Section")
        head.setToolTip(tr("发布新版本时，发布人电脑上的业务规则、算法参数、起订规则、供应商名称、表格列设置会同步给所有同事；"
                           "同事更新后第一次打开时套用一次。之后在这台电脑上改过的，在下面列出来"))
        v.addWidget(head)
        t = QTableWidget(0, 3)
        t.setHorizontalHeaderLabels([tr("设置项"), tr("这台电脑"), tr("公司统一")])
        t.verticalHeader().hide()
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.rows = [(p, _short(a), _short(b)) for p, a, b in rows]
        self.ctl = TableSortFilter(t, lambda: len(self.rows), lambda i, c: self.rows[i][c], self._fill)
        self.ctl.render()
        for c, w in enumerate((260, 210, 210)):
            t.setColumnWidth(c, w)
        v.addWidget(t, 1)
        if not rows:
            e = QLabel(tr("和公司统一设置完全一样"))
            e.setObjectName("Faint")
            v.addWidget(e)
        foot = QHBoxLayout()
        foot.addStretch()
        ok = QPushButton(tr("关闭"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        foot.addWidget(ok)
        v.addLayout(foot)
        fit_to_screen(self)

    def _fill(self, i, k):
        from PySide6.QtWidgets import QTableWidgetItem
        t = self.ctl.t
        for c, val in enumerate(self.rows[i]):
            it = QTableWidgetItem(val)
            it.setToolTip(val)
            t.setItem(k, c, it)


def _short(v):
    import json
    try:
        s = json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(v)
    return s if len(s) <= 120 else s[:117] + "…"


# =====================================================================
class SettingsPage:
    CATS = (("common", "常用"), ("conn", "数据连接"), ("algo", "订货算法"), ("rules", "起订规则"),
            ("mail", "邮箱"), ("agent", "采购助手"), ("log", "运行日志"))

    def __init__(self, win, settings):
        self.win, self.settings = win, settings

    def build(self):
        w = QWidget()
        w.setObjectName("SetPage")
        outer = QHBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        t = QLabel(tr("系统设置"))                     # 页面名在顶栏（主窗口藏起这一行）
        t.setObjectName("PageTitle")
        t.hide()
        # 左边分类
        side = QFrame()
        side.setObjectName("SetSide")
        side.setFixedWidth(220)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 24, 14, 24)
        sv.setSpacing(4)
        st = QLabel(tr("设置"))
        st.setObjectName("SetSideTitle")
        sv.addWidget(st)
        sv.addSpacing(10)
        self.cat_group = QButtonGroup(side)
        self.cat_group.setExclusive(True)
        self.cats = {}
        self.stack = QStackedWidget()
        for i, (key, name) in enumerate(self.CATS):
            b = _Cat(tr(name))
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, i=i: self.stack.setCurrentIndex(i))
            self.cat_group.addButton(b, i)
            self.cats[key] = b
            sv.addWidget(b)
        sv.addStretch()
        sv.addWidget(t)
        outer.addWidget(side)
        outer.addWidget(self.stack, 1)
        for key, _n in self.CATS:
            self.stack.addWidget(self._scroll(getattr(self, "_cat_" + key)()))
        self.restyle()
        self.refresh()
        return w

    def _scroll(self, cards):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("SetBody")
        h = QHBoxLayout(body)
        h.setContentsMargins(32, 28, 32, 36)
        col = QWidget()
        col.setMaximumWidth(920)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(18)
        for c in cards:
            v.addWidget(c)
        v.addStretch()
        h.addWidget(col, 1)
        h.addStretch(0)
        sa.setWidget(body)
        return sa

    # ---------------------------------------------------------- 常用
    def _conn_card(self):
        f = QFrame()
        f.setObjectName("SetCard")
        v = QVBoxLayout(f)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(20, 14, 20, 14)
        t = QLabel(tr("数据连接"))
        t.setObjectName("SetCardTitle")
        head.addWidget(t)
        head.addStretch()
        b = QPushButton(tr("全部测试"))
        b.setObjectName("SetGhost")
        b.setToolTip(tr("连一次数据库、看一下共享文件夹在不在（邮箱在「邮箱」里一个个测试）"))
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self.test_all)
        head.addWidget(b)
        v.addLayout(head)
        self.conn_rows = {}
        for key, abbr, tok, name, tip, fn in (
                ("db", "DB", "ok", tr("销售数据库"), tr("销量、库存、商品资料都从这里读（ysoft 数据库）。程序只读数据库，从来不写"), self.edit_db),
                ("mail", "@", "warn", tr("公司邮箱"), tr("读供应商回信、收报价、发订单邮件。密码只加密存在这台电脑"), self.edit_mail),
                ("share", "F", "accent", tr("共享文件夹"), tr("同事共用的同步盘文件夹：订单跟踪的记录、PIANO 表"), self.pick_share)):
            line = QFrame()
            line.setObjectName("SetLine")
            line.setFixedHeight(1)
            v.addWidget(line)
            r = _ConnRow(abbr, tok, name, tip)
            r.manage.connect(fn)
            v.addWidget(r)
            self.conn_rows[key] = r
        return f

    def _look_card(self):
        import theme
        c, v = _card(tr("外观"))
        g = QHBoxLayout()
        g.setSpacing(12)
        self.theme_group = QButtonGroup(c)
        self.theme_group.setExclusive(True)
        self.theme_cards = {}
        for key, name in (("dark", tr("深色")), ("light", tr("浅色")), ("system", tr("跟随系统"))):
            b = _ThemeCard(key, name)
            if key == "system":
                b.setToolTip(tr("Windows 设置里是深色就用深色，浅色就用浅色，跟着自动换"))
            b.clicked.connect(lambda _=False, k=key: self.win.set_theme_mode(k))
            self.theme_group.addButton(b)
            self.theme_cards[key] = b
            g.addWidget(b)
        v.addLayout(g)
        r = QHBoxLayout()
        r.setSpacing(24)
        r.addWidget(_row_label(tr("主色")))
        sw = QHBoxLayout()
        sw.setSpacing(10)
        self.sw_group = QButtonGroup(c)
        self.sw_group.setExclusive(True)
        self.swatches = {}
        for key, name in theme.ACCENTS:
            b = _Swatch(key, tr(name))
            b.clicked.connect(lambda _=False, k=key: self.set_accent(k))
            self.sw_group.addButton(b)
            self.swatches[key] = b
            sw.addWidget(b)
        r.addLayout(sw)
        r.addStretch()
        v.addLayout(r)
        r = QHBoxLayout()
        r.setSpacing(24)
        r.addWidget(_row_label(tr("表格密度"), tr("紧凑：一屏多看几行；舒适：行高一点，适合审核")))
        seg = QFrame()
        seg.setObjectName("Seg")
        seg.setFixedHeight(36)
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(3, 3, 3, 3)
        sh.setSpacing(2)
        self.den_group = QButtonGroup(seg)
        self.den_group.setExclusive(True)
        self.den_btns = {}
        for key, name, _f in theme.DENSITIES:
            b = QPushButton(tr(name))
            b.setObjectName("SegBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_density(k))
            self.den_group.addButton(b)
            self.den_btns[key] = b
            sh.addWidget(b)
        r.addWidget(seg)
        r.addStretch()
        v.addLayout(r)
        r = QHBoxLayout()
        r.setSpacing(24)
        r.addWidget(_row_label(tr("语言")))
        self.lang = QComboBox()
        self.lang.setMinimumWidth(180)
        lb = self.win.lang_box
        for i in range(lb.count()):
            self.lang.addItem(lb.itemText(i), lb.itemData(i))
        self.lang.setCurrentIndex(lb.currentIndex())
        self.lang.currentIndexChanged.connect(lambda i: lb.setCurrentIndex(i))
        r.addWidget(self.lang)
        r.addStretch()
        v.addLayout(r)
        r = QHBoxLayout()
        r.setSpacing(24)
        r.addWidget(_row_label(tr("商品图片"), tr("订货明细、检查清单的条码前面显示商品小图，详情栏显示大图（按条码从公司图片服务器取主图，存在本机，下次不用再下载）")))
        self.img_box = QCheckBox(tr("显示"))
        self.img_box.setChecked(bool(self.settings.get("show_images", True)))
        self.img_box.toggled.connect(self._images_toggled)
        r.addWidget(self.img_box)
        self.img_size = QComboBox()
        for k, t in (("s", tr("小图")), ("m", tr("中图")), ("l", tr("大图"))):
            self.img_size.addItem(t, k)
        self.img_size.setCurrentIndex(max(0, self.img_size.findData(self.settings.get("image_size") or "m")))
        self.img_size.setToolTip(tr("表格里商品图的大小（鼠标停在图上会弹出大图）；重新打开表格窗口后生效"))
        self.img_size.setEnabled(self.img_box.isChecked())
        self.img_size.currentIndexChanged.connect(self._image_size_changed)
        r.addWidget(self.img_size)
        r.addStretch()
        v.addLayout(r)
        return c

    def _company_card(self):
        f = QFrame()
        f.setObjectName("SetCard")
        h = QHBoxLayout(f)
        h.setContentsMargins(20, 16, 20, 16)
        h.setSpacing(16)
        tv = QVBoxLayout()
        tv.setSpacing(4)
        t = QLabel(tr("公司统一设置"))
        t.setObjectName("SetCardTitle")
        s = QLabel(tr("发布新版本时从采购部电脑同步"))
        s.setObjectName("ConnSub")
        tv.addWidget(t)
        tv.addWidget(s)
        h.addLayout(tv, 1)
        self.co_state = QLabel("")
        h.addWidget(self.co_state)
        b = QPushButton(tr("查看改了什么"))
        b.setObjectName("SetGhost")
        b.setMinimumHeight(36)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self.show_company_diff)
        h.addWidget(b)
        return f

    def _about_card(self):
        c, v = _card(tr("关于"))
        g = QGridLayout()
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(10)
        g.addWidget(_row_label(tr("版本")), 0, 0)
        ver = QLabel(f"{tr('CASA&TE采购系统')} {APP_VERSION}")
        g.addWidget(ver, 0, 1)
        g.addWidget(_row_label(tr("更新")), 1, 0)
        self.upd_info = QLabel("")
        self.upd_info.setWordWrap(True)
        g.addWidget(self.upd_info, 1, 1)
        ub = QHBoxLayout()
        self.upd_auto = QCheckBox(tr("启动时自动检查更新"))
        self.upd_auto.setChecked(self.settings.get("update_auto", True))
        self.upd_auto.toggled.connect(self._upd_auto)
        ub.addWidget(self.upd_auto)
        ub.addStretch()
        cb = QPushButton(tr("检查更新"))
        cb.setObjectName("Primary")
        cb.clicked.connect(lambda: self.win.updater.check(silent=False))
        ub.addWidget(cb)
        g.addLayout(ub, 2, 1)
        g.addWidget(_row_label(tr("设置文件")), 3, 0)
        sp = QLabel(SETTINGS_PATH)
        sp.setObjectName("ConnSub")
        sp.setWordWrap(True)
        sp.setTextInteractionFlags(Qt.TextSelectableByMouse)
        g.addWidget(sp, 3, 1)
        g.setColumnStretch(1, 1)
        v.addLayout(g)
        r = QHBoxLayout()
        r.addWidget(_row_label(tr("用户名称"), tr("用户名称显示在驾驶舱的问候里")))
        self.user_name = QLineEdit(self.settings.get("user_name") or tr("采购部"))
        self.user_name.setMaximumWidth(240)
        self.user_name.editingFinished.connect(self._save_user)
        r.addWidget(self.user_name)
        r.addSpacing(16)
        r.addWidget(QLabel(tr("所在位置")))
        self.user_site = QLineEdit(self.settings.get("user_site") or tr("BMS 总仓"))
        self.user_site.setMaximumWidth(200)
        self.user_site.editingFinished.connect(self._save_user)
        r.addWidget(self.user_site)
        r.addStretch()
        v.addLayout(r)
        return c

    def _cat_common(self):
        return [self._conn_card(), self._look_card(), self._company_card(), self._about_card()]

    # ---------------------------------------------------------- 数据连接
    def _cat_conn(self):
        c, v = _card(tr("销售数据库"), tr("销量、库存、商品资料都从这里读（ysoft 数据库）"))
        self.db_info = QLabel("")
        v.addWidget(self.db_info)
        self.db_chip = chip(tr("未检查"), "gray")
        row = QHBoxLayout()
        row.addWidget(self.db_chip)
        self.acc_chip = chip("", "ok")
        self.acc_chip.hide()
        row.addWidget(self.acc_chip)
        row.addStretch()
        b = QPushButton(tr("修改连接"))
        b.clicked.connect(self.edit_db)
        b2 = QPushButton(tr("测试连接"))
        b2.clicked.connect(self.win.check_db)
        row.addWidget(b)
        row.addWidget(b2)
        v.addLayout(row)
        c2, v2 = _card(tr("文件位置"))
        so = self.settings.setdefault("store_orders", {})
        tk = self.settings.setdefault("tracking", {})
        self.paths = {}
        for key, label, getter, setter, is_file in (
                ("so_folder", tr("采购配单文件夹（总仓采购）"), lambda: so.get("folder", ""),
                 lambda p: so.__setitem__("folder", p), False),
                ("cmp_folder", tr("比价总文件夹（DETERSIVO采购）"), lambda: self.settings.get("last_folder", ""),
                 lambda p: self.settings.__setitem__("last_folder", p), False),
                ("share", tr("共享文件夹（订单跟踪）"), lambda: tk.get("folder", ""),
                 None, False),
                ("legacy", tr("旧程序 SistemaIntegrato（第二期之前用）"), lambda: self.settings.get("legacy_exe", ""),
                 lambda p: self.settings.__setitem__("legacy_exe", p), True)):
            l = QLabel(label)
            l.setObjectName("Faint")
            v2.addWidget(l)
            r = QHBoxLayout()
            e = QLineEdit(getter())
            e.setReadOnly(True)
            pb = QPushButton(tr("选择"))
            if setter is None:
                pb.clicked.connect(self.pick_share)
            else:
                pb.clicked.connect(lambda _=False, e=e, setter=setter, is_file=is_file, label=label:
                                   self._pick(e, setter, is_file, label))
            r.addWidget(e, 1)
            r.addWidget(pb)
            v2.addLayout(r)
            self.paths[key] = (e, getter)
        return [c, c2]

    # ---------------------------------------------------------- 订货算法
    def _cat_algo(self):
        c, v = _card(tr("订货算法"), tr("总仓采购和DETERSIVO采购 · 检查清单共用这套设置"))
        self.algo_info = QLabel("")           # 不显示，参数说明放在算法下拉的鼠标提示里
        row = QHBoxLayout()
        row.setSpacing(24)
        row.addWidget(_row_label(tr("算法")))
        self.engine = QComboBox()
        from reorder_page import ENGINES
        for k, label in ENGINES:
            self.engine.addItem(tr(label), k)
        self.engine.currentIndexChanged.connect(self._engine_changed)
        row.addWidget(self.engine)
        row.addStretch()
        b = QPushButton(tr("算法设置"))
        b.clicked.connect(self.edit_algo)
        row.addWidget(b)
        v.addLayout(row)
        c2, v2 = _card(tr("门店与仓库"), tr("总仓采购的「配货设置」里改：哪些门店参与 BMS 凑单、先后顺序"))
        so = self.settings.setdefault("store_orders", {})
        fill = dict(S.DEFAULT_FILL, **(so.get("fill") or {}))
        far = set(fill.get("far_stores") or [])
        self.store_chips = []
        for sid, name in S.STORE_SHEETS.items():
            r = QHBoxLayout()
            r.addWidget(QLabel(f"{sid} · {name}"), 1)
            tag, kind = (tr("不参与 BMS 凑单"), "warn") if sid in far else (tr("参与 BMS 凑单"), "accent")
            ch = chip(tag, kind)
            self.store_chips.append((ch, tag, kind))
            r.addWidget(ch)
            v2.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel(tr("0 · BMS 总仓")), 1)
        ch = chip(tr("独立公司 · 合并订货后分货"), "gray")
        self.store_chips.append((ch, tr("独立公司 · 合并订货后分货"), "gray"))
        r.addWidget(ch)
        v2.addLayout(r)
        return [c, c2]

    # ---------------------------------------------------------- 起订规则
    def _cat_rules(self):
        c, v = _card(tr("起订规则"))
        for name, tip, fn in (
                (tr("DETERSIVO 批发商起订规则"), tr("每家批发商的起订金额、凑单方式、手动调整线、放弃供应商线"),
                 lambda: self.win.go_page("rules")),
                (tr("总仓采购 · 配货设置"), tr("凑单、BMS 不够起订量时、门店之间调货"),
                 lambda: self._open_store_fill()),
                (tr("总仓采购 · 每家供应商的起订量"), tr("在总仓采购页面的起订量检查里点一家供应商改：起订量、订货周期、参与 BMS / 调货的门店"),
                 lambda: self.win.go_page("store_orders"))):
            r = QHBoxLayout()
            r.setSpacing(14)
            lb = QLabel(name + "  ⓘ")
            lb.setToolTip(tip)
            r.addWidget(lb, 1)
            b = QPushButton(tr("打开"))
            b.setObjectName("SetGhost")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            r.addWidget(b)
            v.addLayout(r)
        return [c]

    def _open_store_fill(self):
        self.win._ensure_all()
        pg = getattr(self.win, "store_orders", None)
        if pg is not None and hasattr(pg, "edit_fill"):
            pg.edit_fill()
        else:
            self.win.go_page("store_orders")

    # ---------------------------------------------------------- 邮箱
    def _cat_mail(self):
        c, v = _card(tr("邮箱"), tr("公司邮箱（阿里云企业邮箱）：邮件核对、收报价、发订单邮件。密码只加密存在这台电脑。"))
        r = QHBoxLayout()
        self.mail_info = QLabel("")
        r.addWidget(self.mail_info, 1)
        b = QPushButton(tr("管理邮箱"))
        b.clicked.connect(self.edit_mail)
        r.addWidget(b)
        b = QPushButton(tr("邮件摸底"))
        b.setToolTip(tr("每个邮箱的收件箱和已发送，最近 4 周：按发件人汇总成一张 Excel，放在「文档\\CASATE 邮件摸底」"))
        b.clicked.connect(self.mail_survey)
        r.addWidget(b)
        v.addLayout(r)
        return [c]

    # ---------------------------------------------------------- 采购助手
    def _cat_agent(self):
        c, v = _card(tr("采购助手"), tr("让助手查供应商、算建议量、生成采购订单、查订单、核对邮件；改数据前都会问你"))
        r = QHBoxLayout()
        self.agent_info = QLabel("")
        r.addWidget(self.agent_info, 1)
        b = QPushButton(tr("服务商和密钥"))
        b.clicked.connect(self.edit_agent)
        r.addWidget(b)
        b2 = QPushButton(tr("打开助手"))
        b2.setObjectName("Primary")
        b2.clicked.connect(lambda: self.win.toggle_agent(True))
        r.addWidget(b2)
        v.addLayout(r)
        return [c]

    # ---------------------------------------------------------- 运行日志
    def _cat_log(self):
        c, v = _card(tr("运行日志"), tr("程序做了什么、哪里出错了都记在这里；遇到问题把日志发给负责程序的同事"))
        r = QHBoxLayout()
        r.addStretch()
        b = QPushButton(tr("打开日志文件夹"))
        b.clicked.connect(self._open_logs)
        r.addWidget(b)
        b2 = QPushButton(tr("查看运行日志"))
        b2.setObjectName("Primary")
        b2.clicked.connect(lambda: self.win.go_page("log"))
        r.addWidget(b2)
        v.addLayout(r)
        return [c]

    def _open_logs(self):
        from settings import app_dir
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        d = os.path.join(app_dir(), "logs")
        QDesktopServices.openUrl(QUrl.fromLocalFile(d if os.path.isdir(d) else app_dir()))

    # ---------------------------------------------------------- 刷新
    def refresh(self):
        if not hasattr(self, "db_info"):
            return
        self._mail_refresh()
        from fenpei.config import DatabaseConfig as D
        d = self.settings.get("db") or {}
        self.db_info.setText(tr("{0}@{1}:{2} · 数据库 {3}", d.get("user") or D.USER, d.get("host") or D.HOST,
                                d.get("port") or D.PORT, d.get("database") or D.DATABASE))
        ok = getattr(self.win, "_db_ok", None)
        set_chip(self.db_chip, tr("已连接") if ok else (tr("检查中…") if ok is None else tr("连不上")),
                 "ok" if ok else ("gray" if ok is None else "err"))
        acc = getattr(self.win, "_db_account", None) if ok else None
        if acc:
            set_chip(self.acc_chip, tr("只读账号") if acc["readonly"] else tr("有写入权限"),
                     "ok" if acc["readonly"] else "warn")
            self.acc_chip.setToolTip(acc["user"] + "\n" + "\n".join(acc["grants"]))
            self.acc_chip.show()
        else:
            self.acc_chip.hide()
        upto = getattr(self.win, "_db_sales_upto", None)
        sub = tr("只读") + f" · {d.get('host') or D.HOST}" + (tr(" · 销售到 {0:%d/%m}", upto) if upto else "")
        self.conn_rows["db"].set(sub, tr("正常") if ok else (tr("检查中…") if ok is None else tr("连不上")),
                                 "ok" if ok else ("muted" if ok is None else "err"))
        tk = self.settings.get("tracking") or {}
        fol = tk.get("folder") or ""
        there = bool(fol) and os.path.isdir(fol)
        self.conn_rows["share"].set(fol or tr("还没选"), tr("已连接") if there else (tr("找不到") if fol else tr("未设置")),
                                    "ok" if there else ("err" if fol else "muted"))
        from reorder_page import algo_params, algo_summary
        p = algo_params(self.settings)
        self.engine.blockSignals(True)
        self.engine.setCurrentIndex(max(0, self.engine.findData(p.get("engine", "v2"))))
        self.engine.blockSignals(False)
        self.algo_info.setText(algo_summary(p))
        self.engine.setToolTip(algo_summary(p))
        for e, getter in self.paths.values():
            e.setText(getter())
        self._look_refresh()
        self._company_refresh()
        self._agent_refresh()
        self.refresh_update()

    def _look_refresh(self):
        import theme
        mode = "system" if self.settings.get("theme_follow") else (self.settings.get("theme") or "dark")
        b = self.theme_cards.get(mode)
        if b is not None:
            b.setChecked(True)
        sw = self.swatches.get(theme.accent())
        if sw is not None:
            sw.setChecked(True)
        db = self.den_btns.get(theme.density())
        if db is not None:
            db.setChecked(True)
        self.lang.blockSignals(True)
        self.lang.setCurrentIndex(self.win.lang_box.currentIndex())
        self.lang.blockSignals(False)
        for x in list(self.theme_cards.values()) + list(self.swatches.values()):
            x.update()

    def _company_refresh(self):
        import settings as ST
        rows = settings_diff(self.settings, ST.COMPANY_DEFAULTS)
        latest = self.settings.get("company_defaults_ver") == ST.COMPANY_DEFAULTS_VER
        if not latest:
            txt, tone = tr("等更新后套用"), "warn"
        elif rows:
            txt, tone = tr("这台电脑改过 {0} 项", len(rows)), "accent"
        else:
            txt, tone = tr("已是最新"), "ok"
        fg = _T(tone)
        bg = _T(tone + "_soft")
        self.co_state.setText(txt)
        self.co_state.setStyleSheet(f"padding:3px 10px; border-radius:11px; font-size:12px; color:{fg}; background:{bg};")
        self.co_state.setToolTip(tr("公司统一设置第 {0} 版", ST.COMPANY_DEFAULTS_VER))

    def show_company_diff(self):
        import settings as ST
        _DiffDialog(self.win, settings_diff(self.settings, ST.COMPANY_DEFAULTS), ST.COMPANY_DEFAULTS_VER).exec()

    def _agent_refresh(self):
        import agent as A
        prov = A.provider_of(self.settings)
        has = bool(A.api_key(prov))
        self.agent_info.setText(f"{A.provider_label(prov)} · {A.model_of(self.settings, prov)} · " +
                                (tr("已填密钥") if has else tr("还没填密钥")))
        self.cats["agent"].dot = not has
        self.cats["agent"].update()

    def edit_agent(self):
        from agent_panel import KeyDialog
        KeyDialog(self.win, self.settings).exec()
        self._agent_refresh()

    def refresh_update(self):
        if not hasattr(self, "upd_info"):
            return
        import app_update as U
        cfg = U.load_config(self.settings)
        up = getattr(self.win, "updater", None)
        latest = up.latest if up else None
        if not cfg:
            txt = tr("还没有更新源（发布人发布一次后，程序里会自带）")
        else:
            txt = tr("更新源：GitHub {0}", cfg["repo"])
            if latest:
                txt += " · " + (tr("最新版本 {0}", latest["version"]) if U.is_newer(latest["version"], APP_VERSION)
                                else tr("已经是最新版本"))
        self.upd_info.setText(txt)

    def _upd_auto(self, on):
        self.settings["update_auto"] = bool(on)
        save_settings(self.settings)

    def restyle(self):
        T = _T
        if not hasattr(self, "stack"):
            return
        page = self.stack.parentWidget()
        dark = self.settings.get("theme") == "dark"
        page.setStyleSheet(
            f"#SetSide {{ background:{T('side') if dark else T('surface')}; border:0; border-right:1px solid {T('line')}; }}"
            f"#SetSideTitle {{ font-size:18px; font-weight:700; color:{T('text')}; margin:0 10px; }}"
            f"QPushButton#SetCat {{ text-align:left; min-height:38px; max-height:38px; padding:0 12px; border-radius:9px;"
            f" border:0; background:transparent; color:{T('head')}; font-weight:500; }}"
            f"QPushButton#SetCat:hover {{ background:{T('hover')}; color:{T('text')}; }}"
            f"QPushButton#SetCat:checked {{ background:{T('side_active') if dark else T('accent_soft')};"
            f" color:{'#FFFFFF' if dark else T('accent')}; border-left:3px solid {T('accent')}; padding-left:9px; }}"
            f"#SetBody {{ background:{T('bg')}; }}"
            f"#SetCard {{ background:{T('surface')}; border:1px solid {T('line')}; border-radius:16px; }}"
            f"#SetCardTitle {{ font-size:15px; font-weight:600; color:{T('text')}; background:transparent; }}"
            f"#SetRowLabel {{ color:{T('head')}; }}"
            f"#SetLine {{ background:{T('line')}; border:0; }}"
            f"#ConnRow {{ background:transparent; border:0; }}"
            f"#ConnName {{ font-weight:500; color:{T('text')}; }}"
            f"#ConnSub {{ font-size:12px; color:{T('muted')}; }}"
            f"QPushButton#SetGhost {{ min-height:32px; max-height:32px; padding:0 12px; border-radius:8px;"
            f" border:1px solid {T('border')}; background:transparent; color:{T('head')}; font-size:12px; }}"
            f"QPushButton#SetGhost:hover {{ border-color:{T('accent_ring')}; color:{T('text')}; }}")
        for ch, tag, kind in getattr(self, "store_chips", []):
            set_chip(ch, tag, kind)
        for r in getattr(self, "conn_rows", {}).values():
            r.restyle()
        if hasattr(self, "db_chip"):
            self.refresh()

    # ---------------------------------------------------------- 动作
    def test_all(self):
        self.win.check_db()
        self.refresh()
        self.win.show_toast(tr("正在测试数据库连接…"))

    def set_accent(self, key):
        import theme
        self.settings["accent"] = key
        save_settings(self.settings)
        theme.set_accent(key)
        self.win.apply_theme()

    def set_density(self, key):
        import theme
        self.settings["density"] = key
        save_settings(self.settings)
        theme.set_density(key)
        self.win.apply_density()

    def pick_share(self):
        tp = getattr(self.win, "tracking", None)
        if tp is None:
            self.win._ensure_all()
            tp = getattr(self.win, "tracking", None)
        if tp is not None and hasattr(tp, "choose_folder"):
            tp.choose_folder()
        self.refresh()

    def _images_toggled(self, on):
        self.settings["show_images"] = bool(on)
        save_settings(self.settings)
        import product_images as PI
        PI.store(self.settings)
        self.img_size.setEnabled(bool(on))

    def _image_size_changed(self, _i):
        self.settings["image_size"] = self.img_size.currentData() or "m"
        save_settings(self.settings)

    def _mail_refresh(self):
        import mail_client as MC
        import commands as CMD
        accs = MC.accounts(self.settings)
        if not accs:
            self.mail_info.setText(tr("还没设置邮箱"))
            self.conn_rows["mail"].set(tr("还没设置邮箱"), tr("未设置"), "muted")
            self.cats["mail"].dot = False
            return
        ok = sum(1 for a in accs if CMD.mail_password(a["address"]))
        miss = len(accs) - ok
        self.mail_info.setText(tr("{0} 个邮箱 · {1} 个已填密码", len(accs), ok))
        self.conn_rows["mail"].set(tr("{0} 个邮箱 · {1} 个还没填密码", len(accs), miss) if miss else tr("{0} 个邮箱", len(accs)),
                                   tr("缺密码") if miss else tr("正常"), "warn" if miss else "ok")
        self.cats["mail"].dot = bool(miss)
        self.cats["mail"].update()

    def edit_mail(self):
        from mail_page import MailAccountsDialog
        MailAccountsDialog(self.win, self.settings).exec()
        self._mail_refresh()

    def mail_survey(self):
        from mail_page import run_survey
        run_survey(self.win, self.settings)

    def edit_db(self):
        from reorder_page import DbDialog
        if DbDialog(self.win, self.settings).exec():
            self.refresh()
            self.win.check_db()

    def edit_algo(self):
        from reorder_page import AlgoDialog
        if AlgoDialog(self.win, self.settings).exec():
            self.refresh()

    def _engine_changed(self, *_):
        from reorder_page import algo_params
        p = algo_params(self.settings)
        p["engine"] = self.engine.currentData()
        self.settings["algo"] = p
        save_settings(self.settings)
        self.refresh()

    def _pick(self, edit, setter, is_file, label):
        start = edit.text() or ""
        if is_file:
            path, _ = QFileDialog.getOpenFileName(self.win, label, os.path.dirname(start), tr("程序 (*.exe *.py)"))
        else:
            path = QFileDialog.getExistingDirectory(self.win, label, start)
        if path:
            setter(os.path.normpath(path))
            save_settings(self.settings)
            self.refresh()
            self.win.show_toast(tr("已保存"))

    def _save_user(self):
        name = self.user_name.text().strip() or tr("采购部")
        site = self.user_site.text().strip() or tr("BMS 总仓")
        if (name, site) == (self.settings.get("user_name"), self.settings.get("user_site")):
            return
        self.settings["user_name"], self.settings["user_site"] = name, site
        save_settings(self.settings)
        self.win.show_toast(tr("已保存"))
