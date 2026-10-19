"""
CASA&TE采购系统 · 通用界面组件
  StepBar      分步条（① 选择 → ② 计算 → …）
  TaskDialog   任务进度窗口（分步骤、总进度、已用 / 预计剩余、后台运行、取消）
  Sparkline    小折线图（周销量）
  DotsDelegate 表格里画一排小色块（每家门店是否达到起订量）
  EmptyState   空状态（图标 + 标题 + 说明 + 按钮）
  ToolWindow   独立窗口的基类（订货明细、检查清单 …）
"""
import os
import time
import threading

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QRectF, QPointF, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import (QToolTip, QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QDialog,
                               QProgressBar, QStyledItemDelegate, QSizePolicy, QMainWindow)

from i18n import tr
from theme import T, NUM_FONT, icon


# =====================================================================
# 分步条
# =====================================================================
class StepBar(QFrame):
    """steps: [(标题, 说明)]；set_state(i, 'done'|'now'|'todo', 说明)"""

    def __init__(self, steps, parent=None):
        super().__init__(parent)
        self.setObjectName("StepBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.cells = []
        for i, (title, sub) in enumerate(steps):
            cell = QFrame()
            cell.setObjectName("StepCell")
            h = QHBoxLayout(cell)
            h.setContentsMargins(14, 10, 14, 10)
            h.setSpacing(10)
            dot = QLabel(str(i + 1))
            dot.setFixedSize(24, 24)
            dot.setAlignment(Qt.AlignCenter)
            col = QVBoxLayout()
            col.setSpacing(1)
            t = QLabel(title)
            t.setObjectName("StepTitle")
            s = QLabel(sub)
            s.setObjectName("StepSub")
            col.addWidget(t)
            col.addWidget(s)
            h.addWidget(dot)
            h.addLayout(col, 1)
            lay.addWidget(cell, 1)
            self.cells.append((cell, dot, t, s))
        self.states = ["todo"] * len(steps)
        for i in range(len(steps)):
            self.set_state(i, "todo")

    def set_state(self, i, state, sub=None):
        cell, dot, t, s = self.cells[i]
        self.states[i] = state
        if sub is not None:
            s.setText(sub)
        cell.setObjectName("StepCellOn" if state == "now" else "StepCell")
        cell.style().unpolish(cell)      # objectName 变了要重新套样式
        cell.style().polish(cell)
        if state == "done":
            dot.setText("✓")
            dot.setStyleSheet(f"background:{T('ok')}; color:#FFFFFF; border-radius:12px; font-weight:700;")
            t.setStyleSheet("")
        elif state == "now":
            dot.setText(str(i + 1))
            dot.setStyleSheet(f"background:{T('accent')}; color:{T('on_accent')}; border-radius:12px; font-weight:700;")
            t.setStyleSheet(f"color:{T('accent')};")
        else:
            dot.setText(str(i + 1))
            dot.setStyleSheet(f"background:{T('track')}; color:{T('muted')}; border-radius:12px; font-weight:600;")
            t.setStyleSheet(f"color:{T('muted')};")

    def set_current(self, i):
        """i 之前的都算完成，i 是当前，之后的待办"""
        for k in range(len(self.cells)):
            self.set_state(k, "done" if k < i else "now" if k == i else "todo")

    def restyle(self):
        for i, st in enumerate(self.states):
            self.set_state(i, st)


# =====================================================================
# 任务进度窗口
# =====================================================================
class Cancelled(Exception):
    pass


class _TaskSig(QObject):
    step = Signal(int, str, str, str)      # 下标, 状态, 计数, 说明
    progress = Signal(float)
    finished = Signal(bool, str)


class TaskDialog(QDialog):
    """
    用法：
        dlg = TaskDialog(win, "正在计算订货量", "周三 · 15 家供应商", ["读取…", "查询…"], cancellable=True)
        dlg.show()
        # 后台线程里（线程安全）：
        dlg.post_step(2, "running", "3 / 5", "S.FILIPPO")
        dlg.post_progress(62)
        if dlg.cancel_event.is_set(): raise Cancelled
        dlg.post_finished(True, "完成")
    「后台运行」= 隐藏窗口，任务继续；完成后由调用方提醒。
    """
    went_background = Signal()

    def __init__(self, parent, title, subtitle, steps, cancellable=True, auto_close=True):
        super().__init__(parent)
        self.setObjectName("Dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.cancel_event = threading.Event()
        self.auto_close = auto_close
        self.t0 = time.time()
        self.pct = 0.0
        self.is_done = False
        self.in_background = False
        self.sig = _TaskSig()
        self.sig.step.connect(self._on_step)
        self.sig.progress.connect(self._on_progress)
        self.sig.finished.connect(self._on_finished)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QWidget()
        hv = QVBoxLayout(head)
        hv.setContentsMargins(22, 20, 22, 14)
        hv.setSpacing(12)
        top = QHBoxLayout()
        ic = QLabel()
        ic.setFixedSize(38, 38)
        ic.setAlignment(Qt.AlignCenter)
        ic.setPixmap(icon("suggest", T("accent"), 20).pixmap(20, 20))
        ic.setStyleSheet(f"background:{T('accent_soft')}; border-radius:10px;")
        tc = QVBoxLayout()
        tc.setSpacing(2)
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("WinTitle")
        self.sub_lbl = QLabel(subtitle)
        self.sub_lbl.setObjectName("Faint")
        tc.addWidget(self.title_lbl)
        tc.addWidget(self.sub_lbl)
        self.pct_lbl = QLabel("0%")
        self.pct_lbl.setFont(QFont(NUM_FONT, 18, QFont.DemiBold))
        self.pct_lbl.setStyleSheet(f"color:{T('accent')};")
        top.addWidget(ic)
        top.addSpacing(6)
        top.addLayout(tc, 1)
        top.addWidget(self.pct_lbl)
        hv.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        hv.addWidget(self.bar)
        tl = QHBoxLayout()
        self.elapsed_lbl = QLabel(tr("已用 {0}", "00:00"))
        self.elapsed_lbl.setObjectName("Faint")
        self.eta_lbl = QLabel("")
        self.eta_lbl.setObjectName("Faint")
        tl.addWidget(self.elapsed_lbl)
        tl.addStretch()
        tl.addWidget(self.eta_lbl)
        hv.addLayout(tl)
        lay.addWidget(head)

        body = QWidget()
        self.steps_lay = QVBoxLayout(body)
        self.steps_lay.setContentsMargins(22, 0, 22, 12)
        self.steps_lay.setSpacing(0)
        self.rows = []
        for label in steps:
            row = QFrame()
            row.setStyleSheet(f"QFrame{{border-top:1px solid {T('line')};}} QLabel{{border:none;}}")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 9, 0, 9)
            h.setSpacing(12)
            dot = QLabel()
            dot.setFixedSize(22, 22)
            dot.setAlignment(Qt.AlignCenter)
            col = QVBoxLayout()
            col.setSpacing(3)
            line1 = QHBoxLayout()
            name = QLabel(label)
            cnt = QLabel("")
            cnt.setFont(QFont(NUM_FONT, 9))
            cnt.setStyleSheet(f"color:{T('muted')};")
            line1.addWidget(name)
            line1.addWidget(cnt)
            line1.addStretch()
            col.addLayout(line1)
            detail = QLabel("")
            detail.setObjectName("Faint")
            detail.hide()
            col.addWidget(detail)
            tm = QLabel("")
            tm.setFont(QFont(NUM_FONT, 9))
            tm.setStyleSheet(f"color:{T('muted')};")
            h.addWidget(dot)
            h.addLayout(col, 1)
            h.addWidget(tm)
            self.steps_lay.addWidget(row)
            self.rows.append({"dot": dot, "name": name, "cnt": cnt, "detail": detail, "tm": tm,
                              "state": "todo", "t0": None})
            self._paint_row(len(self.rows) - 1)
        lay.addWidget(body)

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setContentsMargins(22, 0, 22, 10)
        self.msg.hide()
        lay.addWidget(self.msg)

        foot = QFrame()
        foot.setObjectName("DialogFoot")
        fh = QHBoxLayout(foot)
        fh.setContentsMargins(22, 12, 22, 12)
        self.foot_hint = QLabel(tr("可以切到其他页面，完成后会提醒"))
        self.foot_hint.setObjectName("Faint")
        fh.addWidget(self.foot_hint, 1)
        self.btn_bg = QPushButton(tr("后台运行"))
        self.btn_bg.clicked.connect(self.to_background)
        self.btn_cancel = QPushButton(tr("取消"))
        self.btn_cancel.setObjectName("Danger")
        self.btn_cancel.clicked.connect(self.request_cancel)
        self.btn_cancel.setVisible(cancellable)
        self.btn_close = QPushButton(tr("关闭"))
        self.btn_close.setObjectName("Primary")
        self.btn_close.clicked.connect(self.accept)
        self.btn_close.hide()
        fh.addWidget(self.btn_bg)
        fh.addWidget(self.btn_cancel)
        fh.addWidget(self.btn_close)
        lay.addWidget(foot)

        self.timer = QTimer(self, interval=500, timeout=self._tick)
        self.timer.start()

    # ---- 线程安全的入口 ----
    def post_step(self, i, state="running", count="", detail=""):
        self.sig.step.emit(int(i), state, str(count or ""), str(detail or ""))

    def post_progress(self, pct):
        self.sig.progress.emit(float(pct))

    def post_finished(self, ok=True, message=""):
        self.sig.finished.emit(bool(ok), str(message or ""))

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    # ---- 界面 ----
    def _paint_row(self, i):
        r = self.rows[i]
        st = r["state"]
        if st == "done":
            r["dot"].setText("✓")
            r["dot"].setStyleSheet(f"background:{T('ok')}; color:#FFFFFF; border-radius:11px; font-weight:700;")
            r["name"].setStyleSheet(f"color:{T('text')};")
        elif st == "running":
            r["dot"].setText("")
            r["dot"].setStyleSheet(f"background:{T('surface')}; border:2px solid {T('accent')}; border-radius:11px;")
            r["name"].setStyleSheet(f"color:{T('accent')}; font-weight:600;")
        elif st == "error":
            r["dot"].setText("!")
            r["dot"].setStyleSheet(f"background:{T('err')}; color:#FFFFFF; border-radius:11px; font-weight:700;")
            r["name"].setStyleSheet(f"color:{T('err')}; font-weight:600;")
        else:
            r["dot"].setText("")
            r["dot"].setStyleSheet(f"background:{T('track')}; border-radius:11px;")
            r["name"].setStyleSheet(f"color:{T('muted')};")

    def _on_step(self, i, state, count, detail):
        if not (0 <= i < len(self.rows)):
            return
        # 前面还在跑的步骤自动标为完成
        if state == "running":
            for k in range(i):
                if self.rows[k]["state"] == "running":
                    self._on_step(k, "done", self.rows[k]["cnt"].text(), "")
        r = self.rows[i]
        if state == "running" and r["t0"] is None:
            r["t0"] = time.time()
        if state in ("done", "error") and r["t0"] is not None:
            r["tm"].setText(self._fmt(time.time() - r["t0"]))
        r["state"] = state
        r["cnt"].setText(count)
        r["detail"].setText(detail)
        r["detail"].setVisible(bool(detail) and state == "running")
        self._paint_row(i)

    def _on_progress(self, pct):
        self.pct = max(0.0, min(100.0, pct))
        self.bar.setValue(int(self.pct * 10))
        self.pct_lbl.setText(f"{self.pct:.0f}%")

    @staticmethod
    def _fmt(sec):
        sec = int(max(0, sec))
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def _tick(self):
        el = time.time() - self.t0
        self.elapsed_lbl.setText(tr("已用 {0}", self._fmt(el)))
        if self.is_done:
            self.eta_lbl.setText("")
        elif self.pct >= 3:
            self.eta_lbl.setText(tr("预计还要 {0}", self._fmt(el * (100 - self.pct) / self.pct)))
        else:
            self.eta_lbl.setText(tr("正在估算剩余时间…"))
        for r in self.rows:
            if r["state"] == "running" and r["t0"]:
                r["tm"].setText(self._fmt(time.time() - r["t0"]))

    def _on_finished(self, ok, message):
        self.is_done = True
        self.timer.stop()
        self._tick()
        if ok:
            for i, r in enumerate(self.rows):
                if r["state"] in ("running", "todo"):
                    self._on_step(i, "done", r["cnt"].text(), "")
            self._on_progress(100)
        else:
            for i, r in enumerate(self.rows):
                if r["state"] == "running":
                    self._on_step(i, "error", r["cnt"].text(), "")
        if message:
            self.msg.setText(message)
            self.msg.setStyleSheet(f"color:{T('ok') if ok else T('err')};")
            self.msg.show()
        self.btn_bg.hide()
        self.btn_cancel.hide()
        self.btn_close.show()
        self.foot_hint.setText(tr("完成") if ok else tr("没有完成"))
        if ok and self.auto_close and not self.in_background:
            QTimer.singleShot(700, self.accept)
        elif self.in_background:
            self.accept()

    def to_background(self):
        self.in_background = True
        self.setModal(False)
        self.hide()
        self.went_background.emit()

    def request_cancel(self):
        self.cancel_event.set()
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText(tr("正在取消…"))
        self.foot_hint.setText(tr("等当前这一步结束后停止"))

    def reject(self):
        if not self.is_done:          # Esc / 关闭按钮：任务没完时当作「后台运行」
            self.to_background()
            return
        super().reject()


# =====================================================================
# 小折线图
# =====================================================================
class Sparkline(QWidget):
    """周销量柱状图：左边纵轴刻度、下面日期、悬停显示那一周的日期和销量。
    values 从旧到新；end_date 是最后一周的最后一天（计算当天）。"""

    PAD_L, PAD_R, PAD_T, PAD_B = 30, 6, 8, 20

    def __init__(self, values=None, parent=None, height=150):
        super().__init__(parent)
        self.values = list(values or [])
        self.ref = None
        self.end_date = None
        self.hover = -1
        self.setMinimumHeight(height)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_values(self, values, ref=None, end_date=None):
        self.values = list(values or [])
        self.ref = ref
        if end_date is not None:
            self.end_date = end_date
        self.hover = -1
        self.update()

    # ---- 周 → 日期 ----
    def week_range(self, i):
        import datetime as _dt
        end = self.end_date or _dt.date.today()
        k = len(self.values) - 1 - i          # 0 = 最近一周
        last = end - _dt.timedelta(days=7 * k)
        return last - _dt.timedelta(days=6), last

    @staticmethod
    def _nice_max(v):
        if v <= 4:
            return 4
        import math
        mag = 10 ** int(math.floor(math.log10(v)))
        for m in (1, 2, 2.5, 5, 10):
            if v <= m * mag:
                top = m * mag
                return int(top) if top >= 4 else 4
        return int(v)

    def _geom(self):
        w, h = self.width(), self.height()
        x0, x1 = self.PAD_L, w - self.PAD_R
        y0, y1 = self.PAD_T, h - self.PAD_B
        n = max(1, len(self.values))
        return x0, x1, y0, y1, (x1 - x0) / n

    def _index_at(self, x):
        x0, x1, _, _, bw = self._geom()
        if not self.values or x < x0 or x > x1:
            return -1
        return min(len(self.values) - 1, int((x - x0) / bw))

    def mouseMoveEvent(self, e):
        i = self._index_at(e.position().x())
        if i != self.hover:
            self.hover = i
            self.update()
            if i >= 0:
                a, b = self.week_range(i)
                QToolTip.showText(e.globalPosition().toPoint(),
                                  tr("{0:%d/%m} – {1:%d/%m}：{2:g} 件", a, b, round(self.values[i], 1)), self)
            else:
                QToolTip.hideText()

    def leaveEvent(self, _):
        self.hover = -1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.values:
            p.setPen(QColor(T("muted")))
            p.drawText(self.rect(), Qt.AlignCenter, tr("没有销量数据"))
            return
        x0, x1, y0, y1, bw = self._geom()
        top = self._nice_max(max(max(self.values), self.ref or 0))
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        p.setFont(f)

        def ypos(v):
            return y1 - (v / top) * (y1 - y0)

        # 网格 + 纵轴刻度（0、1/2、顶）
        for v in (0, top / 2, top):
            y = ypos(v)
            p.setPen(QPen(QColor(T("line")), 1, Qt.SolidLine if v == 0 else Qt.DotLine))
            p.drawLine(QPointF(x0, y), QPointF(x1, y))
            p.setPen(QColor(T("faint")))
            p.drawText(QRectF(0, y - 8, x0 - 5, 16), Qt.AlignRight | Qt.AlignVCenter, f"{v:g}")
        # 柱子
        gap = max(1.0, bw * 0.22)
        for i, v in enumerate(self.values):
            if v <= 0:
                continue
            c = QColor(T("accent"))
            if self.hover >= 0 and i != self.hover:
                c.setAlpha(110)
            y = ypos(v)
            p.fillRect(QRectF(x0 + i * bw + gap / 2, y, bw - gap, y1 - y), c)
        # 加权平均虚线
        if self.ref:
            y = ypos(self.ref)
            p.setPen(QPen(QColor(T("near")), 1.2, Qt.DashLine))
            p.drawLine(QPointF(x0, y), QPointF(x1, y))
        # 悬停：高亮那一列 + 顶部写数量
        if 0 <= self.hover < len(self.values):
            v = self.values[self.hover]
            cx = x0 + (self.hover + 0.5) * bw
            p.setPen(QPen(QColor(T("muted")), 1, Qt.DotLine))
            p.drawLine(QPointF(cx, y0), QPointF(cx, y1))
            p.setPen(QColor(T("text")))
            fb = QFont(f)
            fb.setBold(True)
            p.setFont(fb)
            ty = max(y0, ypos(v) - 16)
            p.drawText(QRectF(cx - 30, ty, 60, 14), Qt.AlignCenter, f"{round(v, 1):g}")
            p.setFont(f)
        # 横轴日期：每 4 周一个（从最近一周往回数，保证最右边有）
        p.setPen(QColor(T("faint")))
        n = len(self.values)
        for i in range(n - 1, -1, -4):
            a, _b = self.week_range(i)
            cx = x0 + (i + 0.5) * bw
            left = min(max(cx - 22, x0 - 10), self.width() - 44)
            p.drawText(QRectF(left, y1 + 3, 44, 14), Qt.AlignCenter, f"{a:%d/%m}")


# =====================================================================
# 一排小色块（表格单元格里）
# =====================================================================
DOTS_ROLE = Qt.UserRole + 20


class DotsDelegate(QStyledItemDelegate):
    def paint(self, p, opt, idx):
        super().paint(p, opt, idx)
        colors = idx.data(DOTS_ROLE) or []
        if not colors:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = opt.rect
        x = r.left() + 10
        y = r.center().y() - 3
        for c in colors:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c))
            p.drawRoundedRect(QRectF(x, y, 15, 6), 2, 2)
            x += 18
        p.restore()

    def sizeHint(self, opt, idx):
        s = super().sizeHint(opt, idx)
        n = len(idx.data(DOTS_ROLE) or [])
        return QSize(max(s.width(), 20 + n * 18), s.height())


# =====================================================================
# 空状态
# =====================================================================
class EmptyState(QWidget):
    def __init__(self, icon_name, title, text, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 40, 24, 40)
        v.setSpacing(10)
        v.setAlignment(Qt.AlignCenter)
        ic = QLabel()
        ic.setFixedSize(64, 64)
        ic.setAlignment(Qt.AlignCenter)
        ic.setPixmap(icon(icon_name, T("accent"), 30).pixmap(30, 30))
        ic.setStyleSheet(f"background:{T('accent_soft')}; border-radius:16px;")
        t = QLabel(title)
        t.setObjectName("EmptyTitle")
        t.setAlignment(Qt.AlignCenter)
        s = QLabel(text)
        s.setObjectName("Empty")
        s.setWordWrap(True)
        s.setAlignment(Qt.AlignCenter)
        s.setMaximumWidth(560)
        v.addWidget(ic, 0, Qt.AlignCenter)
        v.addWidget(t)
        v.addWidget(s, 0, Qt.AlignCenter)
        self.buttons = QHBoxLayout()
        self.buttons.setAlignment(Qt.AlignCenter)
        v.addLayout(self.buttons)


# =====================================================================
# 独立窗口基类
# =====================================================================
class ToolWindow(QMainWindow):
    """非模态独立窗口：关主窗口时一起关；再次打开时提到最前"""

    def __init__(self, parent, title, size=(1360, 820)):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle(title)
        self.resize(*size)
        try:
            from theme import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass

    def bring_up(self):
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()


def kpi_card(label, value="—", sub=""):
    """小指标卡：返回 (框, 数值标签, 说明标签)"""
    f = QFrame()
    f.setObjectName("Panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 13, 16, 13)
    v.setSpacing(4)
    l = QLabel(label)
    l.setObjectName("KpiLabel")
    val = QLabel(value)
    val.setObjectName("KpiValue")
    s = QLabel(sub)
    s.setObjectName("Faint")
    s.setVisible(bool(sub))
    v.addWidget(l)
    v.addWidget(val)
    v.addWidget(s)
    return f, val, s


def fit_chip(btn, pad=34):
    """可勾选的 Chip 按钮：选中时是粗体，按粗体留宽度，长文字（英文 / 意大利文）不被截断"""
    from PySide6.QtGui import QFontMetrics
    f = btn.font()
    f.setBold(True)
    btn.setMinimumWidth(QFontMetrics(f).horizontalAdvance(btn.text()) + pad)
    btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return btn


def chip(text, kind="accent"):
    """小标签：kind = accent / ok / warn / err / gray"""
    lbl = QLabel(text)
    set_chip(lbl, text, kind)
    return lbl


def set_chip(lbl, text, kind="accent"):
    lbl.setText(text)
    if kind == "gray":
        bg, fg = T("track"), T("muted")
    else:
        bg, fg = T(kind + "_soft"), T(kind)
    lbl.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:11px; padding:0 9px; font-size:12px; font-weight:500;")
    lbl.setFixedHeight(22)
    lbl.setAlignment(Qt.AlignCenter)
    from PySide6.QtWidgets import QSizePolicy as _SP
    lbl.setSizePolicy(_SP.Maximum, _SP.Fixed)


# =====================================================================
# 全程序可复制：文字标签可以用鼠标选中复制；表格 Ctrl+C / 右键复制
# =====================================================================
def _table_text(view, rows, cols):
    """rows / cols：模型里的行号、列号（cols 按屏幕上看到的顺序）→ 制表符分隔的文字（贴到 Excel 正好分列）"""
    m = view.model()
    out = []
    for r in rows:
        vals = []
        for c in cols:
            v = m.index(r, c).data(Qt.DisplayRole)
            if (v is None or v == "") and hasattr(view, "cellWidget"):
                w = view.cellWidget(r, c)        # 状态列这种放了小标签控件的格子
                if w is not None:
                    v = " ".join(x.text() for x in w.findChildren(QLabel) + w.findChildren(QPushButton) if x.text())
            vals.append("" if v is None else str(v).replace("\t", " ").replace("\n", " "))
        out.append("\t".join(vals))
    return "\n".join(out)


def _visible_cols(view):
    hh = view.horizontalHeader() if hasattr(view, "horizontalHeader") else None
    n = view.model().columnCount()
    if hh is None:
        return list(range(n))
    cols = [hh.logicalIndex(v) for v in range(n)]
    return [c for c in cols if not hh.isSectionHidden(c)]


def _visible_rows(view):
    return [r for r in range(view.model().rowCount()) if not (hasattr(view, "isRowHidden") and view.isRowHidden(r))]


def copy_view_selection(view, whole_rows=False):
    from PySide6.QtWidgets import QApplication
    sel = view.selectionModel().selectedIndexes() if view.selectionModel() else []
    if not sel and view.currentIndex().isValid():
        sel = [view.currentIndex()]
    if not sel:
        return False
    rows = sorted({i.row() for i in sel})
    rows = [r for r in rows if r in set(_visible_rows(view))] or rows
    vis = _visible_cols(view)
    picked = {i.column() for i in sel}
    cols = vis if whole_rows or picked >= set(vis) else [c for c in vis if c in picked]
    QApplication.clipboard().setText(_table_text(view, rows, cols))
    return True


class CopyHelper(QObject):
    """装在 QApplication 上：
      · 所有文字标签（QLabel）可以用鼠标选中、右键复制（按钮里的、表格格子里的、可点的卡片里的除外，免得点不动）
      · 所有表格：Ctrl+C 复制选中的行 / 格子；右键菜单「复制这一格 / 这一行 / 选中的行 / 整个表格」
    不想让某个控件里的文字可选：给它或它的上级设属性 noTextSelect=True。"""

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeySequence
        t = ev.type()
        if t == QEvent.Show and isinstance(obj, QLabel):
            self._make_selectable(obj)
        elif t == QEvent.KeyPress and ev.matches(QKeySequence.Copy):
            view = self._view_of(obj, viewport_only=False)
            if view is not None and copy_view_selection(view):
                return True
        elif t == QEvent.ContextMenu:
            view = self._view_of(obj, viewport_only=True)
            if view is not None and view.contextMenuPolicy() == Qt.DefaultContextMenu:
                self._table_menu(view, ev)
                return True
        return False

    @staticmethod
    def _make_selectable(lab):
        if lab.property("_copyDone"):
            return
        lab.setProperty("_copyDone", True)
        from PySide6.QtWidgets import QAbstractButton, QAbstractItemView
        pm = lab.pixmap()
        if pm is not None and not pm.isNull():
            return
        w = lab
        while w is not None:
            if w.property("noTextSelect") or isinstance(w, (QAbstractButton, QAbstractItemView)):
                return
            if w is not lab and w.testAttribute(Qt.WA_SetCursor) and w.cursor().shape() == Qt.PointingHandCursor:
                return                  # 整块可以点的卡片（例如侧边栏的数据库状态）
            if w.isWindow():
                break
            w = w.parentWidget()
        lab.setTextInteractionFlags(lab.textInteractionFlags() | Qt.TextSelectableByMouse)

    @staticmethod
    def _view_of(obj, viewport_only):
        from PySide6.QtWidgets import QAbstractItemView
        p = obj.parent() if hasattr(obj, "parent") else None
        if isinstance(p, QAbstractItemView) and obj is p.viewport():
            return p
        if not viewport_only and isinstance(obj, QAbstractItemView):
            return obj
        return None

    def _table_menu(self, view, ev):
        from PySide6.QtWidgets import QMenu, QApplication
        idx = view.indexAt(ev.pos())
        rows_sel = sorted({i.row() for i in (view.selectionModel().selectedIndexes() if view.selectionModel() else [])})
        menu = QMenu(view)
        a_cell = menu.addAction(tr("复制这一格"))
        a_row = menu.addAction(tr("复制这一行"))
        a_sel = menu.addAction(tr("复制选中的 {0} 行", len(rows_sel))) if len(rows_sel) > 1 else None
        menu.addSeparator()
        a_all = menu.addAction(tr("复制整个表格（显示的行，含表头）"))
        a_cell.setEnabled(idx.isValid())
        a_row.setEnabled(idx.isValid())
        got = menu.exec(ev.globalPos())
        if got is None:
            return
        cols = _visible_cols(view)
        cb = QApplication.clipboard()
        if got == a_cell:
            cb.setText(_table_text(view, [idx.row()], [idx.column()]))
        elif got == a_row:
            cb.setText(_table_text(view, [idx.row()], cols))
        elif a_sel is not None and got == a_sel:
            cb.setText(_table_text(view, rows_sel, cols))
        elif got == a_all:
            m = view.model()
            head = "\t".join(str(m.headerData(c, Qt.Horizontal, Qt.DisplayRole) or "").replace(" ✎", "") for c in cols)
            cb.setText(head + "\n" + _table_text(view, _visible_rows(view), cols))


# =====================================================================
# 文件拖入区域
# =====================================================================
def _local_files(mime, exts):
    if not mime.hasUrls():
        return []
    out = []
    for u in mime.urls():
        p = u.toLocalFile()
        if p and os.path.isfile(p) and (not exts or p.lower().endswith(tuple(exts))):
            out.append(os.path.normpath(p))
    return out


def _set_drag(w, on):
    w.setProperty("drag", bool(on))
    w.style().unpolish(w)
    w.style().polish(w)


class _ElideLabel(QLabel):
    """一行文字，放不下时末尾显示 …（完整的在鼠标提示里）"""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.full = text
        self.setMinimumWidth(10)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.setText(self.fontMetrics().elidedText(self.full, Qt.ElideMiddle, max(10, self.width())))


class DropZone(QFrame):
    """
    一块可以把文件拖进来的区域：标题 + 现在有哪些文件 + 「选择文件… / 打开文件夹」。
    拖进来或选好文件后发 files_dropped(list)。文件列表用 set_files([(名字, 提示)], 说明) 更新，
    每个文件后面可以有 ×（removable=True）→ 发 remove_requested(名字)。
    """
    files_dropped = Signal(list)
    remove_requested = Signal(str)
    open_folder = Signal()

    def __init__(self, title, hint, exts=(".xlsx", ".xls", ".xlsm"), multi=True, removable=False, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setProperty("noTextSelect", True)
        self.exts, self.multi, self.removable = tuple(exts), multi, removable
        self.title_text = title
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight:600;")
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        top.addWidget(self.title)
        top.addWidget(self.count)
        top.addStretch()
        self.btn_pick = QPushButton(tr("选择文件…"))
        self.btn_pick.setObjectName("Link")
        self.btn_pick.setCursor(Qt.PointingHandCursor)
        self.btn_pick.clicked.connect(self._pick)
        self.btn_open = QPushButton(tr("打开文件夹"))
        self.btn_open.setObjectName("Link")
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.clicked.connect(lambda: self.open_folder.emit())
        top.addWidget(self.btn_pick)
        top.addWidget(self.btn_open)
        v.addLayout(top)
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(2)
        v.addLayout(self.list_box)
        self.hint = QLabel(hint)
        self.hint.setObjectName("Faint")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        v.addStretch()

    def set_files(self, files, note=""):
        """files: [(文件名, 鼠标提示)]；note：文件列表下面的一行说明（空 = 显示拖入提示）"""
        while self.list_box.count():
            w = self.list_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for name, tip in files[:8]:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            lab = _ElideLabel("· " + name)
            lab.setToolTip(tip or name)
            h.addWidget(lab, 1)
            if self.removable:
                x = QPushButton("×")
                x.setObjectName("Link")
                x.setCursor(Qt.PointingHandCursor)
                x.setToolTip(tr("从这里拿走（挪进旧文件夹，不删除）"))
                x.setFixedWidth(22)
                x.clicked.connect(lambda _=False, n=name: self.remove_requested.emit(n))
                h.addWidget(x)
            self.list_box.addWidget(row)
        if len(files) > 8:
            more = QLabel(tr("… 还有 {0} 个", len(files) - 8))
            more.setObjectName("Faint")
            self.list_box.addWidget(more)
        self.count.setText(tr("{0} 个", len(files)) if files else "")
        self.hint.setText(note or tr("把文件拖到这里"))

    def _pick(self):
        from PySide6.QtWidgets import QFileDialog
        pat = tr("Excel 文件 ({0})", " ".join("*" + e for e in self.exts))
        if self.multi:
            files, _ = QFileDialog.getOpenFileNames(self, self.title_text, "", pat)
        else:
            f, _ = QFileDialog.getOpenFileName(self, self.title_text, "", pat)
            files = [f] if f else []
        if files:
            self.files_dropped.emit([os.path.normpath(f) for f in files])

    def dragEnterEvent(self, e):
        if _local_files(e.mimeData(), self.exts):
            e.acceptProposedAction()
            _set_drag(self, True)
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        _set_drag(self, False)

    def dropEvent(self, e):
        _set_drag(self, False)
        files = _local_files(e.mimeData(), self.exts)
        if files:
            e.acceptProposedAction()
            self.files_dropped.emit(files)


def accept_file_drops(widget, callback, exts=(".xlsx", ".xls", ".xlsm")):
    """让一个现成的控件（例如显示文件路径的输入框）也能拖文件进来：拖进来的第一个文件交给 callback(路径)"""
    widget.setAcceptDrops(True)

    def enter(e):
        if _local_files(e.mimeData(), exts):
            e.acceptProposedAction()
            _set_drag(widget, True)
        else:
            e.ignore()

    def leave(e):
        _set_drag(widget, False)

    def drop(e):
        _set_drag(widget, False)
        files = _local_files(e.mimeData(), exts)
        if files:
            e.acceptProposedAction()
            callback(files[0])

    def move(e):
        if _local_files(e.mimeData(), exts):
            e.acceptProposedAction()

    widget.dragEnterEvent, widget.dragLeaveEvent, widget.dropEvent, widget.dragMoveEvent = enter, leave, drop, move


# =====================================================================
# 表格的「属性栏」：点表头排序、右键表头按这一列筛选（像 Excel 的筛选）、导出 Excel
# =====================================================================
def _sort_key(text):
    """排序用：能当数字的按数字（1,234.50 / €12 / +3.5），否则按文字（不分大小写）"""
    s = str(text or "").strip()
    t = s.replace("€", "").replace(",", "").replace("×", " ").split(" ")[0].strip()
    try:
        return (0, float(t), "")
    except ValueError:
        return (1, 0.0, s.lower())


class TableSortFilter(QObject):
    """
    管一张 QTableWidget 的排序和按列筛选（数据行 i ↔ 表格行 k 的对应由它维护）：
      text(i, c)  → 数据行 i 第 c 列显示的文字（筛选、导出、默认排序用）
      fill(i, k)  → 把数据行 i 画到表格第 k 行（调用方自己设颜色、可编辑等）
      key(i, c)   → 可选，排序用的值
      hide(i)     → 可选，页面上其他筛选条件（搜索框、开关）要藏起来的行
    左键点表头：排序（再点反过来）；右键点表头：这一列的筛选菜单（勾选要显示的值 / 搜索）。
    """
    rendered = Signal(int, int)          # (显示行数, 总行数)

    def __init__(self, table, count, text, fill, key=None, hide=None, parent=None, numeric=()):
        super().__init__(parent or table)
        self.t, self.count, self.text, self.fill = table, count, text, fill
        self.numeric = set(numeric)          # 导出时这几列写成数字（EAN 这类编号保持文字）
        self.key, self.hide = key, hide
        self.sort_col, self.sort_desc = None, False
        self.filters = {}            # {列: 允许的文字集合}
        self.visible, self.pos = [], {}
        self.titles = [table.horizontalHeaderItem(c).text() if table.horizontalHeaderItem(c) else ""
                       for c in range(table.columnCount())]
        hh = table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(False)
        hh.sectionClicked.connect(self._clicked)
        hh.setContextMenuPolicy(Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(lambda p: self.filter_menu(hh.logicalIndexAt(p), hh.mapToGlobal(p)))
        hh.setToolTip(tr("点表头排序；右键点表头按这一列筛选"))
        table.setSortingEnabled(False)

    # ---- 排序 / 筛选 ----
    def _clicked(self, c):
        if self.sort_col == c:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = c, False
        self.render()

    def set_sort(self, c, desc=False):
        self.sort_col, self.sort_desc = c, desc
        self.render()

    def clear_filters(self):
        self.filters = {}
        self.render()

    def _k(self, i, c):
        if self.key:
            v = self.key(i, c)
            if v is not None:
                return (0, v, "") if isinstance(v, (int, float)) else (1, 0.0, str(v).lower())
        return _sort_key(self.text(i, c))

    def render(self):
        n = self.count()
        order = list(range(n))
        if self.sort_col is not None:
            order.sort(key=lambda i: self._k(i, self.sort_col), reverse=self.sort_desc)
        vis = [i for i in order if not (self.hide and self.hide(i)) and
               all(self.text(i, c) in allowed for c, allowed in self.filters.items())]
        t = self.t
        t.setUpdatesEnabled(False)
        t.blockSignals(True)
        t.setRowCount(len(vis))
        self.visible, self.pos = vis, {}
        for k, i in enumerate(vis):
            self.pos[i] = k
            self.fill(i, k)
        t.blockSignals(False)
        t.setUpdatesEnabled(True)
        hh = t.horizontalHeader()
        for c, title in enumerate(self.titles):
            it = t.horizontalHeaderItem(c)
            if it is None:
                continue
            mark = ""
            if c in self.filters:
                mark += " ⏷"
            if c == self.sort_col:
                mark += " ↓" if self.sort_desc else " ↑"
            it.setText(title + mark)
        hh.viewport().update()
        self.rendered.emit(len(vis), n)

    def update_row(self, i):
        """数据行 i 变了（例如换了供应商）：只重画这一行"""
        if i in self.pos:
            self.t.blockSignals(True)
            self.fill(i, self.pos[i])
            self.t.blockSignals(False)

    def row_at(self, k):
        return self.visible[k] if 0 <= k < len(self.visible) else None

    def filter_menu(self, c, gpos):
        from PySide6.QtWidgets import QMenu, QWidgetAction, QLineEdit, QListWidget, QListWidgetItem
        if c is None or c < 0:
            return
        n = self.count()
        others = {cc: a for cc, a in self.filters.items() if cc != c}
        # 只列出在其他列筛选之后还看得到的值（和 Excel 一样）
        vals = {}
        for i in range(n):
            if self.hide and self.hide(i):
                continue
            if all(self.text(i, cc) in a for cc, a in others.items()):
                v = self.text(i, c)
                vals[v] = vals.get(v, 0) + 1
        keys = sorted(vals, key=_sort_key)
        allowed = self.filters.get(c)
        m = QMenu(self.t)
        a_up = m.addAction(tr("升序排序"))
        a_down = m.addAction(tr("降序排序"))
        m.addSeparator()
        box = QWidget()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(8, 4, 8, 4)
        bv.setSpacing(4)
        search = QLineEdit()
        search.setPlaceholderText(tr("搜索这一列"))
        bv.addWidget(search)
        lst = QListWidget()
        lst.setMinimumWidth(260)
        lst.setMaximumHeight(300)
        for v in keys[:2000]:
            li = QListWidgetItem(f"{v if v != '' else tr('（空白）')}   ({vals[v]})")
            li.setData(Qt.UserRole, v)
            li.setFlags(li.flags() | Qt.ItemIsUserCheckable)
            li.setCheckState(Qt.Checked if (allowed is None or v in allowed) else Qt.Unchecked)
            lst.addItem(li)
        bv.addWidget(lst)
        row = QHBoxLayout()
        b_all, b_none, b_ok = QPushButton(tr("全选")), QPushButton(tr("全不选")), QPushButton(tr("确定"))
        b_ok.setObjectName("Primary")
        for b in (b_all, b_none):
            b.setObjectName("Link")
        row.addWidget(b_all)
        row.addWidget(b_none)
        row.addStretch()
        row.addWidget(b_ok)
        bv.addLayout(row)

        def shown_items():
            return [lst.item(k) for k in range(lst.count()) if not lst.item(k).isHidden()]

        def do_search(text):
            text = text.strip().lower()
            for k in range(lst.count()):
                it = lst.item(k)
                it.setHidden(bool(text) and text not in str(it.data(Qt.UserRole)).lower())

        search.textChanged.connect(do_search)
        b_all.clicked.connect(lambda: [it.setCheckState(Qt.Checked) for it in shown_items()])
        b_none.clicked.connect(lambda: [it.setCheckState(Qt.Unchecked) for it in shown_items()])

        def apply():
            text = search.text().strip()
            picked = {lst.item(k).data(Qt.UserRole) for k in range(lst.count())
                      if lst.item(k).checkState() == Qt.Checked and (not text or not lst.item(k).isHidden())}
            if len(picked) == len(keys) and not text:
                self.filters.pop(c, None)
            else:
                self.filters[c] = picked
            m.close()
            self.render()

        b_ok.clicked.connect(apply)
        search.returnPressed.connect(apply)
        wa = QWidgetAction(m)
        wa.setDefaultWidget(box)
        m.addAction(wa)
        m.addSeparator()
        a_clear = m.addAction(tr("清除这一列的筛选"))
        a_clear.setEnabled(c in self.filters)
        a_clear_all = m.addAction(tr("清除全部筛选"))
        a_clear_all.setEnabled(bool(self.filters))
        search.setFocus()
        got = m.exec(gpos)
        if got == a_up:
            self.set_sort(c, False)
        elif got == a_down:
            self.set_sort(c, True)
        elif got == a_clear:
            self.filters.pop(c, None)
            self.render()
        elif got == a_clear_all:
            self.clear_filters()

    # ---- 导出 ----
    def export(self, parent, default_name, title=None):
        """把现在看得到的行（按现在的顺序和筛选）导出成 Excel"""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getSaveFileName(parent, title or tr("导出 Excel"), default_name, tr("Excel 文件 (*.xlsx)"))
        if not path:
            return None
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        ws = wb.active
        ws.title = (title or "Sheet")[:31].replace("/", "-")
        hh = self.t.horizontalHeader()
        cols = [hh.logicalIndex(v) for v in range(self.t.columnCount()) if not hh.isSectionHidden(hh.logicalIndex(v))]
        ws.append([self.titles[c] for c in cols])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for i in self.visible:
            vals = []
            for c in cols:
                v = self.text(i, c)
                if c in self.numeric:
                    k = _sort_key(v)
                    v = k[1] if (k[0] == 0 and "×" not in str(v)) else v
                vals.append(v)
            ws.append(vals)
        ws.freeze_panes = "A2"
        try:
            wb.save(path)
        except Exception as e:
            QMessageBox.critical(parent, tr("导出"), tr("导出失败：{0}", e))
            return None
        return path
