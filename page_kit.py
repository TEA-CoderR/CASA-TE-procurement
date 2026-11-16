"""
页面零件（2026-11-16 用户：设计稿里没有的页面也要照设计稿的样子做）：
  Hero      页头：小标（灰）· 大标题 26 粗 · 一行说明；右边放按钮（和促销分析一样）
  Stat      指标卡：标题 + 右上角彩色小图标 · 等宽大数字 · 一行小字（可以点）
  Card      卡片：标题（ⓘ 说明在鼠标提示）+ 右边一排小控件 + 内容
  Seg       分段按钮（全部 / 提醒 / 错误 这种几选一）
  more_menu 「⋯」按钮：不常用的操作收进去
颜色一律 theme.T()，深浅色都对；样式在 theme.build_qss（#HeroTitle #HeroSub #StatVal #StatIcon）。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QMenu, QButtonGroup,
                               QSizePolicy, QWidget)

from theme import T, icon
from i18n import tr


def _soft(tone):
    if tone in ("muted", "faint", "text"):
        return T("track")
    return T("accent_soft") if tone == "accent" else T(tone + "_soft")


class Hero(QWidget):
    """页头：大标题 + 一行说明，右边 actions（QHBoxLayout）放按钮"""

    def __init__(self, title, sub="", tip=""):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        v = QVBoxLayout()
        v.setSpacing(6)
        self._tip = tip
        self.title = QLabel()
        self.title.setObjectName("HeroTitle")
        self.title.setTextFormat(Qt.RichText)
        self.title.setToolTip(tip)
        self.set_title(title)
        v.addWidget(self.title)
        self.sub = QLabel(sub)
        self.sub.setObjectName("HeroSub")
        self.sub.setTextFormat(Qt.RichText)
        v.addWidget(self.sub)
        h.addLayout(v, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        h.addLayout(self.actions)

    def set_title(self, text):
        import html
        t = html.escape(text)
        if self._tip:                         # ⓘ 小一点、灰色（不抢标题）
            t += f"<span style='font-size:15px; font-weight:400; color:{T('muted')}'>&nbsp;&nbsp;ⓘ</span>"
        self.title.setText(t)

    def set_sub(self, text):
        self.sub.setText(text)

    def add(self, w):
        self.actions.addWidget(w, 0, Qt.AlignBottom)
        return w


def button(text, fn=None, primary=False, tip="", name=None):
    b = QPushButton(text)
    if primary:
        b.setObjectName("Primary")
    elif name:
        b.setObjectName(name)
    b.setMinimumHeight(36)
    if tip:
        b.setToolTip(tip)
    if fn is not None:
        b.clicked.connect(lambda _=False: fn())
    return b


def more_menu(items, tip=None):
    """「⋯」按钮：items = [(文字, 函数, 鼠标提示) 或 None（分隔线）]；返回 (按钮, {文字: QAction})"""
    b = QPushButton("⋯")
    b.setFixedSize(40, 36)
    b.setToolTip(tip or tr("更多"))
    m = QMenu(b)
    m.setToolTipsVisible(True)
    acts = {}
    for it in items:
        if it is None:
            m.addSeparator()
            continue
        text, fn, *rest = it
        a = m.addAction(text, fn)
        if rest and rest[0]:
            a.setToolTip(rest[0])
        acts[text] = a
    b.setMenu(m)
    b.setStyleSheet("QPushButton::menu-indicator { image: none; width: 0; }")
    return b, acts


class Stat(QFrame):
    """指标卡：标题 · 右上角彩色小图标 · 大数字 · 一行小字。set(值, 颜色=None, sub=None)（和旧 Kpi 一样能 set(v, color)）"""
    clicked = Signal()

    def __init__(self, title, icon_name="table", tone="accent", tip="", clickable=False):
        super().__init__()
        self.setObjectName("Panel")
        self.icon_name, self.tone = icon_name, tone
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.cap = QLabel(title + ("  ⓘ" if tip else ""))
        self.cap.setObjectName("KpiLabel")
        self.cap.setToolTip(tip)
        top.addWidget(self.cap, 1)
        self.ic = QLabel()
        self.ic.setObjectName("StatIcon")
        self.ic.setFixedSize(32, 32)
        self.ic.setAlignment(Qt.AlignCenter)
        top.addWidget(self.ic, 0, Qt.AlignTop)
        v.addLayout(top)
        self.value = QLabel("—")
        self.value.setObjectName("StatVal")
        v.addWidget(self.value)
        self.subl = QLabel(" ")
        self.subl.setObjectName("Faint")
        self.subl.setTextFormat(Qt.RichText)
        v.addWidget(self.subl)
        self.clickable = clickable
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
        self.restyle()

    def mousePressEvent(self, e):
        if self.clickable and e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)

    def set(self, v, color=None, sub=None):
        self.value.setText(str(v))
        self.value.setStyleSheet(f"color:{color};" if color else "")
        if sub is not None:
            self.subl.setText(sub or " ")

    def set_tone(self, tone):
        self.tone = tone
        self.restyle()

    def restyle(self):
        self.ic.setPixmap(icon(self.icon_name, T(self.tone), 18).pixmap(18, 18))
        self.ic.setStyleSheet(f"background:{_soft(self.tone)}; border-radius:9px;")


class Card(QFrame):
    """卡片：标题行（标题 + 右边 head 放小控件）+ 内容 v"""

    def __init__(self, title="", tip="", margins=(18, 16, 18, 16)):
        super().__init__()
        self.setObjectName("Panel")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(*margins)
        self.v.setSpacing(12)
        self.head = QHBoxLayout()
        self.head.setSpacing(8)
        self._tip = tip
        self.title = QLabel(title + ("  ⓘ" if tip else ""))
        self.title.setObjectName("Section")
        self.title.setToolTip(tip)
        self.head.addWidget(self.title)
        self.head.addStretch()
        if title:
            self.v.addLayout(self.head)

    def set_title(self, text):
        self.title.setText(text + ("  ⓘ" if self._tip else ""))

    def add_head(self, w):
        self.head.addWidget(w)
        return w


class Seg(QFrame):
    """分段按钮：items = [(键, 文字)]；changed(键)"""
    changed = Signal(str)

    def __init__(self, items, current=None, tip=""):
        super().__init__()
        self.setObjectName("Seg")
        self.setFixedHeight(36)
        if tip:
            self.setToolTip(tip)
        h = QHBoxLayout(self)
        h.setContentsMargins(3, 3, 3, 3)
        h.setSpacing(2)
        self.group = QButtonGroup(self)
        self.btns = {}
        cur = current if current is not None else (items[0][0] if items else None)
        for k, text in items:
            b = QPushButton(text)
            b.setObjectName("SegBtn")
            b.setCheckable(True)
            b.setChecked(k == cur)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=k: self.changed.emit(k))
            self.group.addButton(b)
            self.btns[k] = b
            h.addWidget(b)

    def set_text(self, k, text):
        if k in self.btns:
            self.btns[k].setText(text)

    def set_current(self, k):
        if k in self.btns:
            self.btns[k].setChecked(True)


def kpi_row(*stats):
    row = QHBoxLayout()
    row.setSpacing(12)
    for s in stats:
        row.addWidget(s)
    return row


class FlowCard(QFrame):
    """
    四步流程条（和 DETERSIVO 一样的 FlowBar）放在一张圆角卡片里；接口和旧 StepBar 一样：
    set_state(i, 'done' | 'now' | 'todo', 说明) / set_current(i) / restyle()。现在这步底下画蓝线
    """

    def __init__(self, steps):
        super().__init__()
        from erp_widgets import FlowBar
        self.setObjectName("Panel")
        h = QHBoxLayout(self)
        h.setContentsMargins(1, 1, 1, 1)
        self.bar = FlowBar(steps)
        self.bar.setCursor(Qt.ArrowCursor)
        h.addWidget(self.bar)

    def set_state(self, i, state, sub=None):
        self.bar.set_state(i, state, sub)
        if state == "now":
            self.bar.set_shown(i)

    def set_current(self, i):
        self.bar.set_current(i)
        self.bar.set_shown(i)

    def restyle(self):
        self.bar.restyle()
