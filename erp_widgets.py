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

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QRectF, QPointF, QSize, QEvent, QEventLoop
from PySide6.QtGui import QKeySequence
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QFontMetrics
from PySide6.QtWidgets import (QToolTip, QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QDialog,
                               QProgressBar, QStyledItemDelegate, QSizePolicy, QMainWindow, QStyleOptionViewItem,
                               QStyle, QApplication, QTableView, QHeaderView)

from i18n import tr
from theme import T, NUM_FONT, icon


# =====================================================================
# 短任务的「处理中」窗口（读大文件、重算、打开文件…）
# =====================================================================
class _BusySig(QObject):
    done = Signal()


# ---- 现在在跑的任务（卡顿记录 hang.log 里写「卡住时正在做什么」，main._start_hang_watchdog 读）----
RUNNING = {}            # id → 任务名


def task_started(key, title):
    RUNNING[key] = title


def task_ended(key):
    RUNNING.pop(key, None)


# ---- 等界面空下来再做（2026-11-07 用户：后台任务完成、跳回结果页时会卡一下）----
LAST_INPUT = [0.0]      # 最后一次点鼠标 / 按键的时间（main._ClickRecorder 更新）


def when_idle(fn, quiet_ms=1500, max_ms=60000):
    """
    不急的活（提前建订货明细窗口、刷新工作台…）：等没有弹窗、用户 quiet_ms 内没点没按键时再做，
    免得刚点完「查看结果」就和跳页面抢时间。最多等 max_ms。只用一个 300 毫秒的定时器，做完就停
    """
    from PySide6.QtWidgets import QApplication
    t0 = time.time()
    tm = QTimer()
    tm.setInterval(300)

    def check():
        now = time.time()
        busy = QApplication.activeModalWidget() is not None or (now - LAST_INPUT[0]) * 1000 < quiet_ms
        if busy and (now - t0) * 1000 < max_ms:
            return
        tm.stop()
        _IDLE.discard(tm)
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
    tm.timeout.connect(check)
    _IDLE.add(tm)                    # 留个引用，不然定时器会被回收
    tm.start()
    return tm


_IDLE = set()


class BusyDialog(QDialog):
    """
    一句话 + 来回走的进度条，后台线程跑 func，跑完自动关掉，再在界面线程调用 on_done(结果, 错误)。
    BusyDialog.run(parent, "读取订单明细", "正在读取 xxx.xlsx…", func, on_done)
    很快就完成的（< 0.3 秒）几乎看不到窗口，不会闪一下
    """
    def __init__(self, parent, title, text):
        super().__init__(parent)
        self.setObjectName("Dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(12)
        lab = QLabel(text)
        lab.setWordWrap(True)
        v.addWidget(lab)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        v.addWidget(bar)

    def reject(self):                    # Esc 不能关：任务还在跑
        pass

    @staticmethod
    def run(parent, title, text, func, on_done=None):
        dlg = BusyDialog(parent, title, text)
        sig = _BusySig(dlg)
        sig.done.connect(dlg.accept)
        box = {}

        def work():
            try:
                box["r"] = func()
            except Exception as e:
                box["e"] = e
            sig.done.emit()
        t = threading.Thread(target=work, daemon=True)
        task_started(id(dlg), f"{title}：{text}")
        t.start()
        t.join(0.3)
        if t.is_alive():
            dlg.exec()
        t.join()
        task_ended(id(dlg))
        if on_done:
            on_done(box.get("r"), box.get("e"))
        return box.get("r"), box.get("e")


class StepsDialog(QDialog):
    """
    界面线程里一步步做、每步都要动界面的活（换深浅色：套样式、刷新每一页…）：
    一个有百分比的进度条 + 现在做哪一步；每步之间让界面画一下（进度条看得见在走）。
    StepsDialog.run(parent, "正在换颜色", [(说明, 函数), …], on_done)。
    设计规范（用户 11-18）：凡是会卡住的地方都要有进度条 —— 能放后台线程的用 BusyDialog / TaskDialog，
    只能在界面线程做的拆成几步用它。
    """
    def __init__(self, parent, title, n):
        super().__init__(parent)
        self.setObjectName("Dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        self.lab = QLabel(title)
        v.addWidget(self.lab)
        self.bar = QProgressBar()
        self.bar.setRange(0, max(1, n))
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        v.addWidget(self.bar)

    def reject(self):
        pass

    def step(self, i, text):
        self.lab.setText(text)
        self.bar.setValue(i)
        self.repaint()
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)   # 只画界面，不处理点击 / 按键

    @staticmethod
    def run(parent, title, steps, on_done=None, show=True):
        """按顺序做完 steps（同步，做完才返回）；一步出错记下来接着做下一步"""
        dlg = StepsDialog(parent, title, len(steps)) if show and steps else None
        if dlg is not None:
            task_started(id(dlg), title)
            dlg.show()
            dlg.step(0, title)
        errors = []
        for i, (text, fn) in enumerate(steps):
            if dlg is not None:
                dlg.step(i, f"{title} · {text}")
            try:
                fn()
            except Exception as e:
                import traceback
                traceback.print_exc()
                errors.append((text, e))
        if dlg is not None:
            dlg.step(len(steps), title)
            task_ended(id(dlg))
            dlg.hide()
            dlg.deleteLater()
        if on_done:
            on_done(errors)
        return errors


def open_file(parent, path, label=None):
    """用系统默认程序（Excel）打开文件，打开的这几秒显示「正在打开」；打不开弹提示。返回 True = 已打开"""
    import sys
    from PySide6.QtWidgets import QMessageBox
    if not path or not os.path.exists(path):
        QMessageBox.warning(parent, tr("打开订单文件"), tr("找不到文件：\n{0}", path))
        return False

    def go():
        if sys.platform == "win32":
            os.startfile(path)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        time.sleep(1.5)                   # 给 Excel 一点启动时间，窗口别一闪就没
    _r, err = BusyDialog.run(parent, tr("打开订单文件"), tr("正在用 Excel 打开 {0}…\n文件较大时需要几秒钟", label or os.path.basename(path)), go)
    if err:
        QMessageBox.warning(parent, tr("打开订单文件"), tr("打开失败：{0}", err))
        return False
    return True


class CountButton(QPushButton):
    """
    按钮 + 右边一个数字小徽章（用户 11-18：「差异商品（209）」这种写法不正式）。
    CountButton(tr("差异商品"), 209)；set_count(n) 改数字（None / 0 = 不显示徽章，show_zero=True 时 0 也显示）。
    """
    def __init__(self, text="", count=None, tone="accent", parent=None, show_zero=False):
        super().__init__(text, parent)
        self._n, self._tone, self._zero, self._css = None, tone, show_zero, ""
        self.set_count(count)

    def setStyleSheet(self, css):          # 自己的样式（例如字的颜色）和徽章要的右边留白合在一起
        self._css = css or ""
        self._apply_css()

    def _apply_css(self):
        pad = f"padding-right:{self._badge()[0] + 22}px;" if self._n is not None else ""
        super().setStyleSheet(self._css + (" " if self._css and pad else "") + pad)

    def set_count(self, n, tone=None):
        if tone:
            self._tone = tone
        self._n = n if (n is not None and n != "" and (n or self._zero)) else None
        self._apply_css()
        self.update()

    def count(self):
        return self._n

    def _badge(self):
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, (f.pointSizeF() if f.pointSizeF() > 0 else 9.0) - 1.0))
        f.setBold(True)
        return QFontMetrics(f).horizontalAdvance(f"{self._n:,}" if isinstance(self._n, int) else str(self._n)) + 14, f

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._n is None:
            return
        w, f = self._badge()
        h = 18
        r = QRectF(self.width() - w - 12, (self.height() - h) / 2, w, h)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        primary = self.objectName() == "Primary" or (self.isCheckable() and self.isChecked() and self.objectName() == "Chip")
        col = QColor(T("on_accent") if primary else T(self._tone if self.isEnabled() else "faint"))
        bg = QColor(col)
        bg.setAlpha(60 if primary else 34)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, h / 2, h / 2)
        p.setPen(col)
        p.setFont(f)
        p.drawText(r, Qt.AlignCenter, f"{self._n:,}" if isinstance(self._n, int) else str(self._n))
        p.end()


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


class FlowBar(QWidget):
    """
    一页一个流程（设计稿：DETERSIVO 四步）：一格一步，圆点 + 名字 + 一行说明；完成绿 ✓、现在这步蓝底 + 底下蓝线、没到灰。
    和 StepBar 一样的用法（set_state / set_current / restyle），多一个 clicked(i)：点一格切到那一步。
    """
    clicked = Signal(int)

    def __init__(self, steps, parent=None):
        super().__init__(parent)
        self.names = [t for t, _s in steps]
        self.subs = [s for _t, s in steps]
        self.states = ["todo"] * len(steps)
        self.shown = 0                     # 现在在看哪一步（底下蓝线）
        self.hover = -1
        self.setFixedHeight(68)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_state(self, i, state, sub=None):
        self.states[i] = state
        if sub is not None:
            self.subs[i] = sub
        self.update()

    def set_current(self, i):
        for k in range(len(self.states)):
            self.states[k] = "done" if k < i else "now" if k == i else "todo"
        self.update()

    def set_shown(self, i):
        self.shown = i
        self.update()

    def restyle(self):
        self.update()

    def _col(self, x):
        n = len(self.names)
        return max(0, min(n - 1, int(x / max(1, self.width() / n))))

    def mouseMoveEvent(self, e):
        c = self._col(e.position().x())
        if c != self.hover:
            self.hover = c
            self.update()

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self._col(e.position().x()))

    def paintEvent(self, _e):
        from PySide6.QtGui import QFontMetrics
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.names)
        cw = self.width() / n
        h = self.height()
        p.fillRect(self.rect(), QColor(T("surface")))
        for i, name in enumerate(self.names):
            st = self.states[i]
            x0 = i * cw
            on = i == self.shown
            if on:
                c = QColor(T("accent"))
                c.setAlpha(26)
                p.fillRect(QRectF(x0, 0, cw, h), c)
                p.fillRect(QRectF(x0, h - 2, cw, 2), QColor(T("accent")))
            elif i == self.hover:
                p.fillRect(QRectF(x0, 0, cw, h), QColor(T("hover")))
            if i < n - 1:
                p.fillRect(QRectF(x0 + cw - 1, 0, 1, h), QColor(T("line")))
            r = 15
            cx, cy = x0 + 24 + r, h / 2
            if st == "now":
                g = QColor(T("accent"))
                g.setAlpha(50)
                p.setPen(Qt.NoPen)
                p.setBrush(g)
                p.drawEllipse(QPointF(cx, cy), r + 5, r + 5)
            if st == "done":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("ok")))
            elif st == "now":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T("accent")))
            else:
                p.setPen(QPen(QColor(T("border")), 1.2))
                p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r, r)
            f = QFont(NUM_FONT)
            f.setPixelSize(13)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(T("on_accent") if st == "now" else ("#04140D" if T("ok") == "#34D399" else "#FFFFFF")
                            if st == "done" else T("muted")))
            p.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, "✓" if st == "done" else str(i + 1))
            tx = cx + r + 12
            tw = x0 + cw - tx - 12
            f2 = self.font()
            f2.setPixelSize(14)
            f2.setBold(True)
            p.setFont(f2)
            p.setPen(QColor(T("muted") if st == "todo" and not on else T("text")))
            p.drawText(QRectF(tx, cy - 19, tw, 20), Qt.AlignLeft | Qt.AlignVCenter, name)
            f3 = self.font()
            f3.setPixelSize(12)
            f3.setBold(False)
            p.setFont(f3)
            p.setPen(QColor(T("accent") if st == "now" else T("muted")))
            p.drawText(QRectF(tx, cy + 2, tw, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(f3).elidedText(self.subs[i] or "", Qt.ElideRight, int(tw)))
        p.fillRect(QRectF(0, h - 1, self.width(), 1), QColor(T("line")))
        if 0 <= self.shown < n:
            p.fillRect(QRectF(self.shown * cw, h - 2, cw, 2), QColor(T("accent")))
        p.end()


class PillDelegate(QStyledItemDelegate):
    """格子里的文字画成彩色小胶囊（设计稿的判断 / 状态标签）：颜色 = 格子的文字颜色，底 = 同色淡一点；没颜色的照原样画"""

    def paint(self, p, opt, idx):
        from PySide6.QtGui import QFontMetrics
        text = idx.data(Qt.DisplayRole) or ""
        fg = idx.data(Qt.ForegroundRole)
        if not text or fg is None:
            return super().paint(p, opt, idx)
        o = QStyleOptionViewItem(opt)
        self.initStyleOption(o, idx)
        o.text = ""
        st = o.widget.style() if o.widget else QApplication.style()
        st.drawControl(QStyle.CE_ItemViewItem, o, p, o.widget)
        c = fg.color() if hasattr(fg, "color") else QColor(fg)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(opt.font)
        f.setPixelSize(12)
        f.setBold(True)
        fm = QFontMetrics(f)
        w = min(opt.rect.width() - 12, fm.horizontalAdvance(text) + 20)
        h = 22
        r = QRectF(opt.rect.x() + 6, opt.rect.center().y() - h / 2 + 1, w, h)
        bg = QColor(c)
        bg.setAlpha(40)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, h / 2, h / 2)
        p.setFont(f)
        p.setPen(c)
        p.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignCenter, fm.elidedText(text, Qt.ElideRight, int(w - 16)))
        p.restore()


class EmbedHost(QWidget):
    """和 ToolWindow 一样的用法（setCentralWidget / bring_up / setWindowTitle / close），但放在页面里：
    DETERSIVO 第 2 步「审核清单」（设计稿：不再单独开窗口）。bring_up / close 交给 on_show / on_close"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self.title = ""
        self.on_show = None
        self.on_close = None

    def setCentralWidget(self, w):
        self._lay.addWidget(w)

    def setWindowTitle(self, t):
        self.title = t

    def bring_up(self):
        if self.on_show:
            self.on_show()

    def close(self):
        if self.on_close:
            self.on_close()
        return True


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
        task_started(id(self), f"{title}：{subtitle}")
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
                    self._on_step(k, "done", self.rows[k].get("count", ""), "")
        r = self.rows[i]
        if state == "running" and r["t0"] is None:
            r["t0"] = time.time()
        if state in ("done", "error") and r["t0"] is not None:
            r["tm"].setText(self._fmt(time.time() - r["t0"]))
        r["state"] = state
        r["count"], r["text"] = count, detail
        if self.in_background:            # 藏着的时候只记下来，不重画（换样式表很费时，后台运行时界面要省着用）
            r["dirty"] = True
            return
        self._show_row(i)

    def _show_row(self, i):
        r = self.rows[i]
        r["cnt"].setText(r.get("count", ""))
        r["detail"].setText(r.get("text", ""))
        r["detail"].setVisible(bool(r.get("text")) and r["state"] == "running")
        self._paint_row(i)
        r["dirty"] = False

    def _on_progress(self, pct):
        self.pct = max(0.0, min(100.0, pct))
        if self.in_background:
            return
        self.bar.setValue(int(self.pct * 10))
        self.pct_lbl.setText(f"{self.pct:.0f}%")

    def current_step(self):
        """现在在做哪一步（后台任务条显示用）"""
        for r in self.rows:
            if r["state"] == "running":
                return r["name"].text()
        return ""

    @staticmethod
    def _fmt(sec):
        sec = int(max(0, sec))
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def _tick(self):
        if self.in_background and not self.is_done:
            return
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
        task_ended(id(self))
        self.timer.stop()
        self._tick()
        if ok:
            for i, r in enumerate(self.rows):
                if r["state"] in ("running", "todo"):
                    self._on_step(i, "done", r.get("count", ""), "")
            self._on_progress(100)
        else:
            for i, r in enumerate(self.rows):
                if r["state"] == "running":
                    self._on_step(i, "error", r.get("count", ""), "")
        _bg_drop(self)
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
        RUNNING[id(self)] = RUNNING.get(id(self), self.windowTitle()) + "（后台运行）"
        self.in_background = True
        # 顺序不能反：先藏再改成不独占。显示中先 setModal(False) 的话，Qt 藏窗口时不会把它从「独占窗口」名单里拿掉
        # → 别的窗口全部点不了，任务完成也不解除（2026-11-06 用户：点后台运行后整个程序点不动）
        self.hide()
        self.setModal(False)
        if self not in BG_TASKS:
            BG_TASKS.append(self)
        BgTaskPill.kick()
        self.went_background.emit()

    def to_foreground(self):
        """后台任务条点一下：把进度窗口拿回来（不独占，照样能点别的；再点「后台运行」又藏起来）"""
        if self.is_done:
            return
        self.in_background = False
        _bg_drop(self)
        RUNNING[id(self)] = RUNNING.get(id(self), "").replace("（后台运行）", "")
        for i, r in enumerate(self.rows):
            if r.get("dirty"):
                self._show_row(i)
        self._on_progress(self.pct)
        self._tick()
        self.show()
        self.raise_()
        self.activateWindow()

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
# ---- 后台运行中的任务 + 顶栏的进度条（2026-11-07 用户：后台运行看不到完成进度）----
BG_TASKS = []           # 点了「后台运行」、还没做完的 TaskDialog


def _bg_drop(dlg):
    if dlg in BG_TASKS:
        BG_TASKS.remove(dlg)
    BgTaskPill.kick()


class BgTaskPill(QPushButton):
    """
    顶栏上的小进度条：「⏳ 正在计算订货量 · 45%」，底色按进度填满；点一下把进度窗口拿回来
    （几个任务时弹菜单选）。没有后台任务时藏起来、定时器也停，不占界面时间；有任务时每 0.7 秒看一眼进度，
    只重画这个小控件
    """
    _all = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BgPill")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)
        self.setMinimumWidth(150)
        self.setMaximumWidth(280)
        self.clicked.connect(self._open)
        self._pct, self._text = 0.0, ""
        self._manual = None              # 界面线程自己分步做的活（打开程序时准备页面）：(文字, 百分比, 说明)
        self.timer = QTimer(self, interval=700, timeout=self._refresh)
        self.hide()
        BgTaskPill._all.append(self)
        self.destroyed.connect(lambda *_: BgTaskPill._all.remove(self) if self in BgTaskPill._all else None)

    @classmethod
    def kick(cls):
        for p in list(cls._all):
            try:
                p._refresh()
            except RuntimeError:              # 窗口已经关了
                cls._all.remove(p)

    def set_manual(self, text, pct, tip=""):
        """没有任务窗口、界面线程一步步做的活（打开程序时一页页准备）：在这里显示进度；text=None 去掉"""
        self._manual = None if text is None else (text, pct, tip)
        self._refresh()
        if self._manual is not None:
            self.repaint()               # 马上画出来（下一步又要占着界面线程）

    def _refresh(self):
        tasks = [d for d in BG_TASKS if not d.is_done]
        if not tasks and self._manual is not None:
            self.timer.stop()
            text, pct, tip = self._manual
            if text != self._text:
                fm = self.fontMetrics()
                self.setFixedWidth(max(150, min(280, fm.horizontalAdvance(text) + 46)))
            self._text, self._pct = text, pct
            self.setToolTip(tip)
            self.update()
            if not self.isVisible():
                self.show()
            return
        if not tasks:
            self.timer.stop()
            self.hide()
            return
        if not self.timer.isActive():
            self.timer.start()
        pct = min(d.pct for d in tasks)
        if len(tasks) == 1:
            d = tasks[0]
            step = d.current_step()
            text = f"{d.windowTitle()} · {pct:.0f}%"
            tip = d.windowTitle() + (f"\n{step}" if step else "") + "\n" + tr("已用 {0}", d._fmt(time.time() - d.t0)) \
                + "\n" + tr("点一下看进度")
        else:
            text = tr("{0} 个后台任务 · {1:.0f}%", len(tasks), pct)
            tip = "\n".join(f"{d.windowTitle()} · {d.pct:.0f}%" for d in tasks) + "\n" + tr("点一下选一个看进度")
        if text != self._text or abs(pct - self._pct) >= 0.5:
            self._text, self._pct = text, pct
            fm = self.fontMetrics()
            self.setFixedWidth(max(150, min(280, fm.horizontalAdvance(text) + 46)))
        self.setToolTip(tip)
        self.update()                    # 小控件，重画很便宜（转动的小圆点也靠它动）
        if not self.isVisible():
            self.show()

    def _open(self):
        tasks = [d for d in BG_TASKS if not d.is_done]
        if len(tasks) == 1:
            tasks[0].to_foreground()
        elif tasks:
            from PySide6.QtWidgets import QMenu
            m = QMenu(self)
            for d in tasks:
                m.addAction(f"{d.windowTitle()} · {d.pct:.0f}%", d.to_foreground)
            m.exec(self.mapToGlobal(self.rect().bottomLeft()))
        self.kick()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = r.height() / 2
        p.setPen(QPen(QColor(T("accent")), 1))
        p.setBrush(QColor(T("surface")))
        p.drawRoundedRect(r, rad, rad)
        if self._pct > 0:                         # 进度：底色从左往右填
            p.save()
            from PySide6.QtGui import QPainterPath
            clip = QPainterPath()
            clip.addRoundedRect(r, rad, rad)
            p.setClipPath(clip)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T("accent_soft")))
            p.drawRect(QRectF(r.left(), r.top(), r.width() * self._pct / 100.0, r.height()))
            p.restore()
        # 左边一个转动的小圆点表示还在跑（跟着 0.7 秒的刷新换位置，不另开动画）
        cx, cy = r.left() + 15, r.center().y()
        p.setPen(QPen(QColor(T("line")), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 6, 6)
        p.setPen(QPen(QColor(T("accent")), 2))
        start = int((time.time() * 2 % 4) * 90 * 16)
        p.drawArc(QRectF(cx - 6, cy - 6, 12, 12), start, 120 * 16)
        p.setPen(QColor(T("text")))
        p.drawText(QRectF(r.left() + 28, r.top(), r.width() - 36, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   self.fontMetrics().elidedText(self._text, Qt.ElideRight, int(r.width() - 36)))
        p.end()


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
    def copy_text(self):
        """右键「复制」：每周的日期和销量"""
        out = []
        for i, v in enumerate(self.values):
            try:
                a, b = self.week_range(i)
                out.append(f"{a:%d/%m}-{b:%d/%m}\t{v:g}")
            except Exception:
                out.append(f"{i + 1}\t{v}")
        return "\n".join(out)

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


class MonthGrid(QWidget):
    """
    近两年每月销量（1–12 月方框）：今年一块（上）、去年一块（下），每块 2 行 × 6 个月（1–6 月、7–12 月）。
    方框左上角小字写月份，中间写件数（0 写淡淡的「–」）；卖得越多底色越深（浅色，字一直看得清）；
    本月描边并带 *（到今天为止），今年还没到的月份是虚线空框。悬停显示具体件数和同比。
    set_data(values, keys)：values 每月销量（从旧到新），keys 对应的 (年, 月)。
    """

    LABEL_H, BOX_H, GAP, BLOCK_GAP = 18, 31, 4, 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data, self.year, self.cur_month, self.message, self.hover = {}, None, None, "", None
        self.setMinimumHeight(2 * (self.LABEL_H + 2 * self.BOX_H + self.GAP) + self.BLOCK_GAP + 4)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, values, keys):
        self.data = {k: float(v or 0) for k, v in zip(keys or [], values or [])}
        self.year, self.cur_month = (keys[-1] if keys else (None, None))
        self.message, self.hover = "", None
        self.update()

    def set_message(self, text):
        self.data, self.year, self.message, self.hover = {}, None, text, None
        self.update()

    def ytd(self):
        """(今年 1 月到本月合计, 去年同期合计, 去年全年合计)；没有数据返回 None"""
        if self.year is None:
            return None
        y, m = self.year, self.cur_month
        cur = sum(self.data.get((y, k), 0) for k in range(1, m + 1))
        prev = sum(self.data.get((y - 1, k), 0) for k in range(1, m + 1))
        full = sum(self.data.get((y - 1, k), 0) for k in range(1, 13))
        return cur, prev, full

    def _layout(self):
        """[(年, 月, 方框)]、[(年, 年份标题位置)]：今年在上、去年在下"""
        bw = (self.width() - 5 * self.GAP) / 6.0
        cells, titles = [], []
        top = 0.0
        for y in (self.year, self.year - 1):
            titles.append((y, QRectF(0, top, self.width(), self.LABEL_H)))
            top += self.LABEL_H
            for half in range(2):
                for j in range(6):
                    cells.append((y, half * 6 + j + 1, QRectF(j * (bw + self.GAP), top, bw, self.BOX_H)))
                top += self.BOX_H + (self.GAP if half == 0 else 0)
            top += self.BLOCK_GAP
        return cells, titles

    def mouseMoveEvent(self, e):
        if self.year is None:
            return
        pos = e.position()
        cells, _ = self._layout()
        hit = next(((y, m) for y, m, r in cells if r.contains(pos)), None)
        if hit != self.hover:
            self.hover = hit
            self.update()
        if not hit or hit > (self.year, self.cur_month):
            QToolTip.hideText()
            return
        y, m = hit
        v = self.data.get((y, m), 0)
        text = tr("{0}-{1:02d}：{2:g} 件", y, m, round(v, 1))
        if (y, m) == (self.year, self.cur_month):
            text += "  " + tr("（本月到今天）")
        if y == self.year:
            text += "\n" + tr("去年同月 {0:g} 件", round(self.data.get((y - 1, m), 0), 1))
        elif (y + 1, m) <= (self.year, self.cur_month):
            text += "\n" + tr("今年同月 {0:g} 件", round(self.data.get((y + 1, m), 0), 1))
        QToolTip.showText(e.globalPosition().toPoint(), text, self)

    def leaveEvent(self, _):
        self.hover = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.year is None:
            p.setPen(QColor(T("muted")))
            p.drawText(self.rect(), Qt.AlignCenter, self.message or tr("没有销量数据"))
            return
        cells, titles = self._layout()
        top_v = max([v for (y, m), v in self.data.items() if y >= self.year - 1] + [0])
        f = QFont(self.font())
        small = QFont(f)
        small.setPointSizeF(max(7.0, f.pointSizeF() - 2))
        num = QFont(f)
        try:
            from theme import NUM_FONT
            num.setFamilies([NUM_FONT] + list(f.families()))   # 数字用设计稿的等宽字体
        except Exception:
            pass
        num.setWeight(QFont.DemiBold)
        title = QFont(f)
        title.setBold(True)
        for y, r in titles:
            p.setFont(title)
            p.setPen(QColor(T("text") if y == self.year else T("muted")))
            p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, str(y))
        accent = QColor(T("accent"))
        for y, m, r in cells:
            future = (y, m) > (self.year, self.cur_month)
            now = (y, m) == (self.year, self.cur_month)
            hov = (y, m) == self.hover and not future
            v = self.data.get((y, m), 0)
            if future:
                p.setPen(QPen(QColor(T("line")), 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
            else:
                if v > 0 and top_v > 0:
                    bg = QColor(accent)
                    bg.setAlpha(int(18 + 82 * (v / top_v)))       # 浅色底：卖得越多越深，最深也不挡字
                else:
                    bg = QColor(T("line"))
                    bg.setAlpha(40)
                p.setPen(QPen(QColor(T("accent") if (now or hov) else T("line")), 1.6 if (now or hov) else 0.8))
                p.setBrush(bg)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
            p.setFont(small)
            p.setPen(QColor(T("muted")))
            p.drawText(r.adjusted(5, 2, -3, 0), Qt.AlignLeft | Qt.AlignTop, f"{m}" + tr("月"))
            if future:
                continue
            if v > 0:
                p.setFont(num)
                p.setPen(QColor(T("text")))
                txt = f"{round(v):g}" if v >= 10 else f"{round(v, 1):g}"
            else:
                p.setFont(small)
                p.setPen(QColor(T("faint")))
                txt = "–"
            p.drawText(r.adjusted(2, 10, -2, 0), Qt.AlignCenter, txt + ("*" if now else ""))


# =====================================================================
# 一排小色块（表格单元格里）
# =====================================================================
DOTS_ROLE = Qt.UserRole + 20


class StepperDelegate(QStyledItemDelegate):
    """
    「订货箱数」这种能改的数字格：左边「−」右边「＋」两个小按钮，点一下减 / 加 1（不用双击再打字）；双击照旧可以直接输入。
    2026-11-07 用户：所有列表的订货箱数都要加减器。
    只在能改的格子（ItemIsEditable）上画按钮。点了以后（等这次鼠标事件处理完）调用 on_step(格子, 新值)；
    没给 on_step 就把新值写进格子文字 → 表格原来的 itemChanged 处理（和手动输入一样，校验 / 重算 / 撤销都照旧）。
    value(格子) 取现在的值（默认读格子文字开头的数字）；最小 0。
    base：原来这一列用的委托（QtyDelegate 之类）——双击输入时的编辑框照用它的。
    """
    BTN = 24          # 两头按钮的宽度（= 胶囊高度）
    PILL_W = 96       # 整个加减器最宽多少（列再宽也不拉长）

    def __init__(self, view, on_step=None, value=None, base=None, minimum=0):
        super().__init__(view)
        self.view, self.on_step, self.value_of, self.base, self.minimum = view, on_step, value, base, minimum
        self.last_step = 0.0          # 刚点过加减（别的「点一下就进编辑」的逻辑可以据此跳过）
        self._hover = None            # (行, 列, "-" / "+" / None)：鼠标在哪个格子、哪个按钮上
        view.setMouseTracking(True)
        view.viewport().setMouseTracking(True)
        view.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Leave and self._hover is not None:     # 鼠标离开表格：去掉悬停效果
            self._hover = None
            obj.setCursor(Qt.ArrowCursor)
            obj.update()
        return False

    # ---- 编辑框：交给原来的委托 ----
    def createEditor(self, parent, opt, idx):
        return self.base.createEditor(parent, opt, idx) if self.base else super().createEditor(parent, opt, idx)

    def setEditorData(self, ed, idx):
        return self.base.setEditorData(ed, idx) if self.base else super().setEditorData(ed, idx)

    def setModelData(self, ed, model, idx):
        return self.base.setModelData(ed, model, idx) if self.base else super().setModelData(ed, model, idx)

    def updateEditorGeometry(self, ed, opt, idx):
        if self.base:
            return self.base.updateEditorGeometry(ed, opt, idx)
        return super().updateEditorGeometry(ed, opt, idx)

    # ---- 值 ----
    def current(self, idx):
        if self.value_of:
            try:
                return float(self.value_of(idx) or 0)
            except (TypeError, ValueError):
                return 0.0
        import re
        m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(idx.data() or "").replace(",", ""))     # 1,234 → 1234
        return float(m.group(1)) if m else 0.0

    @staticmethod
    def _editable(idx):
        return bool(idx.flags() & Qt.ItemIsEditable)

    def _pill(self, r):
        """格子正中一个胶囊：[ − | 数字 | ＋ ]，返回 (胶囊, 减, 加)"""
        from PySide6.QtCore import QRect
        h = min(self.BTN, max(16, r.height() - 8))
        w = min(self.PILL_W, r.width() - 10)
        x = r.left() + (r.width() - w) // 2
        y = r.top() + (r.height() - h) // 2
        pill = QRect(x, y, w, h)
        return pill, QRect(x, y, h, h), QRect(x + w - h, y, h, h)

    def _rects(self, r):
        _, minus, plus = self._pill(r)
        return minus, plus

    def _fits(self, r):
        return r.width() >= 2 * self.BTN + 34

    # ---- 画 ----
    def paint(self, p, opt, idx):
        from PySide6.QtWidgets import QStyleOptionViewItem, QApplication, QStyle
        from PySide6.QtGui import QPainterPath
        bg = idx.data(Qt.BackgroundRole)
        if not self._editable(idx) or not self._fits(opt.rect):
            if bg is not None:
                p.fillRect(opt.rect, bg)
            return super().paint(p, opt, idx)
        o = QStyleOptionViewItem(opt)
        self.initStyleOption(o, idx)
        o.state &= ~QStyle.State_HasFocus
        text = o.text
        o.text = ""
        if bg is not None:
            p.fillRect(opt.rect, bg)
        w = opt.widget
        (w.style() if w else QApplication.style()).drawPrimitive(QStyle.PE_PanelItemViewItem, o, p, w)

        pill, minus, plus = self._pill(opt.rect)
        hov = self._hover
        here = hov is not None and hov[0] == idx.row() and hov[1] == idx.column()
        on = hov[2] if here else None
        can_minus = self.current(idx) > self.minimum
        selected = bool(opt.state & QStyle.State_Selected)

        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rf = QRectF(pill).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = rf.height() / 2
        path = QPainterPath()
        path.addRoundedRect(rf, rad, rad)
        # 底：没有底色的格子填白；有底色（例如改过标黄）的不填，底色照样看得见
        if bg is None:
            p.fillPath(path, QColor(T("surface")))
        # 悬停的那个按钮：浅蓝底（裁在胶囊里）
        if on and (on == "+" or can_minus):
            p.save()
            p.setClipPath(path)
            p.fillRect(minus if on == "-" else plus, QColor(T("accent_soft")))
            p.restore()
        # 框：鼠标在这格 / 选中的行 → 蓝框，平时浅灰
        border = QColor(T("accent")) if (here or selected) else QColor(T("far") if bg is not None else T("line"))
        p.setPen(QPen(border, 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        # 两条分隔线
        sep = QColor(T("line"))
        p.setPen(QPen(sep, 1))
        for x in (minus.right() + 1, plus.left()):
            p.drawLine(QPointF(x + 0.5, rf.top() + 4), QPointF(x + 0.5, rf.bottom() - 4))
        # − / ＋：用线画（不靠字体，两边一样粗、正中）
        arm = max(3.0, minus.height() * 0.17)
        for rect, which, ok in ((minus, "-", can_minus), (plus, "+", True)):
            c = QColor(T("accent") if (ok and (on == which or here or selected)) else T("muted"))
            if not ok:
                c = QColor(T("faint"))
                c.setAlpha(110)
            p.setPen(QPen(c, 1.6, Qt.SolidLine, Qt.RoundCap))
            cx, cy = QRectF(rect).center().x(), QRectF(rect).center().y()
            p.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
            if which == "+":
                p.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))
        # 数字：正中，颜色 / 粗细照格子原来的（标红、加粗都保留）
        fg = idx.data(Qt.ForegroundRole)
        col = fg.color() if hasattr(fg, "color") else (QColor(fg) if fg is not None else QColor(T("text")))
        f = QFont(o.font)
        p.setFont(f)
        p.setPen(col)
        mid = QRectF(minus.right() + 2, pill.top(), plus.left() - minus.right() - 3, pill.height())
        p.drawText(mid, Qt.AlignCenter, p.fontMetrics().elidedText(text, Qt.ElideRight, int(mid.width())))
        p.restore()

    def sizeHint(self, opt, idx):
        s = super().sizeHint(opt, idx)
        return QSize(max(s.width() + 2 * self.BTN + 20, self.MIN_WIDTH), s.height())

    # ---- 点 ----
    def _which(self, opt, idx, pos):
        if not self._editable(idx) or not self._fits(opt.rect):
            return None
        minus, plus = self._rects(opt.rect)
        if minus.adjusted(-2, -4, 2, 4).contains(pos):
            return "-"
        if plus.adjusted(-2, -4, 2, 4).contains(pos):
            return "+"
        return None

    def editorEvent(self, ev, model, opt, idx):
        from PySide6.QtCore import QEvent, QPersistentModelIndex
        t = ev.type()
        if t == QEvent.MouseMove:
            which = self._which(opt, idx, ev.position().toPoint() if hasattr(ev, "position") else ev.pos())
            hov = (idx.row(), idx.column(), which) if self._editable(idx) and self._fits(opt.rect) else None
            if hov != self._hover:
                self._hover = hov
                self.view.viewport().update()
                self.view.viewport().setCursor(Qt.PointingHandCursor if which else Qt.ArrowCursor)
            return False
        if t in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.MouseButtonRelease):
            pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
            which = self._which(opt, idx, pos)
            if not which or ev.button() != Qt.LeftButton:
                return super().editorEvent(ev, model, opt, idx)
            if t == QEvent.MouseButtonRelease:
                cur = self.current(idx)
                new = cur + (1 if which == "+" else -1)
                if new < self.minimum:
                    return True
                new = int(new) if float(new).is_integer() else round(new, 2)
                self.last_step = time.time()
                pidx = QPersistentModelIndex(idx)

                def go():
                    if not pidx.isValid():
                        return
                    i2 = model.index(pidx.row(), pidx.column())
                    if self.on_step:
                        self.on_step(i2, new)
                    else:
                        model.setData(i2, str(new))
                # 这次鼠标事件处理完再改（改的时候表格可能整行重画，鼠标事件中途换掉格子会卡住）
                QTimer.singleShot(0, go)
            self.last_step = time.time()
            return True                    # 加减按钮上的点击不选中、不进编辑
        return super().editorEvent(ev, model, opt, idx)

    def just_stepped(self, within=0.4):
        return time.time() - self.last_step < within

    MIN_WIDTH = 104

    @classmethod
    def install(cls, view, col, **kw):
        """放到 view 的第 col 列；列太窄（放不下两个按钮 + 数字）时放宽一次（之后用户自己拖窄就照用户的）"""
        d = cls(view, **kw)
        view.setItemDelegateForColumn(col, d)

        def widen():
            try:
                if not view.isColumnHidden(col) and view.columnWidth(col) < cls.MIN_WIDTH:
                    view.setColumnWidth(col, cls.MIN_WIDTH)
            except RuntimeError:
                pass
        QTimer.singleShot(0, widen)
        QTimer.singleShot(800, widen)          # 列宽设置 / 第一次填数据自动量宽之后再看一次
        return d


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
        s.hide()                        # 界面要简洁：说明放进鼠标提示
        self.setToolTip(text)
        v.addWidget(ic, 0, Qt.AlignCenter)
        v.addWidget(t)
        v.addWidget(s, 0, Qt.AlignCenter)
        self.buttons = QHBoxLayout()
        self.buttons.setAlignment(Qt.AlignCenter)
        v.addLayout(self.buttons)



def ask_return(win, dlg, text, go):
    """任务做完：没点过「后台运行」→ 直接 go()（打开结果）；点过 → 弹窗问要不要回到任务页面（用户 2026-11-06：
    后台运行完只弹右下角通知不够，要能确认回去）。选「稍后」只留一条通知，结果页之后照常能打开"""
    if dlg is None or not getattr(dlg, "in_background", False):
        go()
        return
    dlg.in_background = False                   # 只问一次
    from PySide6.QtWidgets import QMessageBox
    if win.isMinimized():
        win.showNormal()
    win.raise_()
    win.activateWindow()
    box = QMessageBox(win)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle(tr("后台任务完成"))
    box.setText(text)
    box.setInformativeText(tr("现在回到任务页面查看结果吗？"))
    yes = box.addButton(tr("查看结果"), QMessageBox.AcceptRole)
    box.addButton(tr("稍后"), QMessageBox.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    if box.clickedButton() is yes:
        go()
    elif hasattr(win, "show_toast"):
        win.show_toast(text)


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


def tip_label(text, tip=None):
    """设置界面的短标签：有说明时标签后加 ⓘ，说明放在鼠标提示里（界面要简洁，不放一段段字）"""
    lb = QLabel(text + ("  ⓘ" if tip else ""))
    if tip:
        lb.setToolTip(tip)
    return lb


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



class Ring(QWidget):
    """一圈进度（订单跟踪的预算、Griglia 的覆盖率共用）：底圈 track，弧线颜色 = tone（ok / warn / err / accent），中间写百分比"""

    def __init__(self, size=84):
        super().__init__()
        self.p, self.tone, self.color_text = None, "ok", False
        self.setFixedSize(size, size)

    def set(self, p, tone, color_text=None):
        """color_text：中间的百分比也用 tone 的颜色（默认：tone 不是 ok 才上色）"""
        self.p, self.tone = p, tone
        self.color_text = (tone != "ok") if color_text is None else color_text
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = 8
        r = QRectF(w / 2 + 1, w / 2 + 1, self.width() - w - 2, self.height() - w - 2)
        pen = QPen(QColor(T("track")), w)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 0, 360 * 16)
        if self.p:
            pen.setColor(QColor(T(self.tone)))
            p.setPen(pen)
            p.drawArc(r, 90 * 16, -int(min(self.p, 1.0) * 360 * 16))
        f = QFont(NUM_FONT)
        f.setPixelSize(17 if self.width() >= 80 else 14)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(T(self.tone) if self.p and self.color_text else T("text")))
        p.drawText(self.rect(), Qt.AlignCenter, f"{self.p:.0%}" if self.p is not None else "—")
        p.end()


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
def _row_index(view, r):
    """行号（int，表格）或第 0 列的 QModelIndex（树形列表的子行）→ 第 0 列的 QModelIndex"""
    if isinstance(r, int):
        return view.model().index(r, 0)
    return r.sibling(r.row(), 0)


def _row_path(idx):
    """树里的位置（父行号…, 行号），用来按屏幕顺序排序"""
    out = []
    while idx.isValid():
        out.insert(0, idx.row())
        idx = idx.parent()
    return tuple(out)


def _table_text(view, rows, cols):
    """rows：行号或 QModelIndex（树形列表的子行也行）；cols：按屏幕顺序的列号 → 制表符分隔的文字（贴到 Excel 正好分列）"""
    m = view.model()
    out = []
    for r in rows:
        ri = _row_index(view, r)
        vals = []
        for c in cols:
            v = m.index(ri.row(), c, ri.parent()).data(Qt.DisplayRole)
            if (v is None or v == "") and hasattr(view, "cellWidget") and not ri.parent().isValid():
                w = view.cellWidget(ri.row(), c)        # 状态列这种放了小标签控件的格子
                if w is not None:
                    v = " ".join(x.text() for x in w.findChildren(QLabel) + w.findChildren(QPushButton) if x.text())
            vals.append("" if v is None else str(v).replace("\t", " ").replace("\n", " "))
        out.append("\t".join(vals))
    return "\n".join(out)


def _header_of(view):
    if hasattr(view, "horizontalHeader"):
        return view.horizontalHeader()
    if hasattr(view, "header"):
        return view.header()          # QTreeView / QTreeWidget
    return None


def _visible_cols(view):
    hh = _header_of(view)
    n = view.model().columnCount()
    if hh is None:
        return list(range(n))
    cols = [hh.logicalIndex(v) for v in range(n)]
    return [c for c in cols if not hh.isSectionHidden(c)]


def _visible_rows(view):
    """看得到的行：表格是行号；树形列表是每一行（含展开的子行）的 QModelIndex"""
    m = view.model()
    if hasattr(view, "isExpanded"):
        out = []

        def walk(parent):
            for r in range(m.rowCount(parent)):
                if hasattr(view, "isRowHidden") and view.isRowHidden(r, parent):
                    continue
                idx = m.index(r, 0, parent)
                out.append(idx)
                if m.rowCount(idx) and view.isExpanded(idx):
                    walk(idx)
        walk(view.rootIndex())
        return out
    return [r for r in range(m.rowCount()) if not (hasattr(view, "isRowHidden") and view.isRowHidden(r))]


def _selected_rows(view, sel):
    """选中的格子 → 行（表格：行号；树：QModelIndex），按屏幕顺序"""
    if hasattr(view, "isExpanded"):
        seen, rows = set(), []
        for i in sel:
            ri = i.sibling(i.row(), 0)
            key = _row_path(ri)
            if key not in seen:
                seen.add(key)
                rows.append(ri)
        return sorted(rows, key=_row_path)
    rows = sorted({i.row() for i in sel})
    vis = set(_visible_rows(view))
    return [r for r in rows if r in vis] or rows


def _header_text(view, cols):
    m = view.model()
    return "\t".join(str(m.headerData(c, Qt.Horizontal, Qt.DisplayRole) or "").replace(" ✎", "")
                     .replace(" ⏷", "").replace(" ↑", "").replace(" ↓", "") for c in cols)


def copy_view_selection(view, whole_rows=False):
    from PySide6.QtWidgets import QApplication
    view = getattr(view, "_copy_target", None) or view          # 冻结列的小表格：照大表格复制
    func = getattr(view, "_copy_func", None)                    # 页面自己给的复制（按数据，没画出来的行也有）
    if func is not None and not whole_rows:
        text = func()
        if text:
            QApplication.clipboard().setText(text)
            return True
    sel = view.selectionModel().selectedIndexes() if view.selectionModel() else []
    if not sel and view.currentIndex().isValid():
        sel = [view.currentIndex()]
    if not sel:
        return False
    rows = _selected_rows(view, sel)
    vis = _visible_cols(view)
    picked = {i.column() for i in sel}
    cols = vis if whole_rows or picked >= set(vis) else [c for c in vis if c in picked]
    QApplication.clipboard().setText(_table_text(view, rows, cols))
    return True


def _plain(text):
    """标签里的文字（去掉 HTML）"""
    t = str(text or "")
    if "<" in t and ">" in t:
        from PySide6.QtGui import QTextDocument
        d = QTextDocument()
        d.setHtml(t)
        t = d.toPlainText()
    return t.strip()


def _copy_menu(gpos, items):
    """items = [(菜单文字, 要复制的文字)]"""
    from PySide6.QtWidgets import QMenu, QApplication
    items = [(a, b) for a, b in items if b]
    if not items:
        return False
    menu = QMenu()
    acts = {}
    for label, text in items:
        acts[menu.addAction(label)] = text
    got = menu.exec(gpos)
    if got in acts:
        QApplication.clipboard().setText(acts[got])
    return True


def _short(t, n=24):
    t = " ".join(str(t).split())
    return t if len(t) <= n else t[: n - 1] + "…"


class CopyHelper(QObject):
    """装在 QApplication 上，让程序里看得到的文字都能复制：
      · 文字标签（QLabel）：可以用鼠标选中复制。按钮里的、可点的卡片里的标签选不了（免得点不动），
        但右键都有「复制」
      · 按钮、开关：右键「复制文字」
      · 表格、树形列表（历史记录）：Ctrl+C 复制选中的；右键「复制这一格 / 这一行 / 选中的行 / 整个表格」
      · 表头：右键「复制列名」（自己有右键菜单的表头在它的菜单里）
      · 自己画出来的控件（起订量进度条等）：右键「复制」，控件提供 copy_text() 方法
    不想让某个控件里的文字可选：给它或它的上级设属性 noTextSelect=True。"""

    TYPES = (QEvent.Show, QEvent.KeyPress, QEvent.ContextMenu)

    def eventFilter(self, obj, ev):
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
            if self._other_menu(obj, ev):
                return True
        return False

    def _other_menu(self, obj, ev):
        from PySide6.QtWidgets import QAbstractButton, QHeaderView, QLineEdit, QTextEdit, QPlainTextEdit
        if not isinstance(obj, QWidget) or obj.contextMenuPolicy() not in (Qt.DefaultContextMenu,):
            return False
        if isinstance(obj, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return False                                  # 自带复制菜单
        gpos = ev.globalPos()
        if isinstance(obj, QHeaderView):
            view = obj.parent()
            if view is None or not hasattr(view, "model") or view.model() is None:
                return False
            c = obj.logicalIndexAt(ev.pos())
            cols = _visible_cols(view) if obj.orientation() == Qt.Horizontal else []
            items = []
            if c >= 0 and obj.orientation() == Qt.Horizontal:
                items.append((tr("复制列名「{0}」", _short(_header_text(view, [c]))), _header_text(view, [c])))
            if cols:
                items.append((tr("复制全部列名"), _header_text(view, cols)))
            return _copy_menu(gpos, items)
        if isinstance(obj, QLabel):
            full = getattr(obj, "full", None)             # 放不下被截短（…）的文字：复制完整的
            if isinstance(full, str) and full.strip() and full != obj.text():
                return _copy_menu(gpos, [(tr("复制「{0}」", _short(full)), full.strip())])
            if obj.textInteractionFlags() & Qt.TextSelectableByMouse:
                return False                              # Qt 自带「复制 / 全选」
            txt = _plain(obj.text())
            return _copy_menu(gpos, [(tr("复制「{0}」", _short(txt)), txt)] + self._block_items(obj, txt))
        if isinstance(obj, QAbstractButton):
            txt = _plain(obj.text())
            return _copy_menu(gpos, [(tr("复制「{0}」", _short(txt)), txt)] + self._block_items(obj, txt))
        w = obj
        while w is not None:                              # 自己画出来的控件
            f = getattr(w, "copy_text", None)
            if callable(f):
                try:
                    txt = f()
                except Exception:
                    txt = ""
                return _copy_menu(gpos, [(tr("复制"), txt)])
            if w.isWindow():
                break
            w = w.parentWidget()
        return False

    @staticmethod
    def _block_items(obj, own):
        """标签在一块卡片里（例如 KPI、方案卡片）：再给一个「复制这一块」"""
        p = obj.parentWidget()
        if p is None or p.isWindow():
            return []
        texts = [_plain(x.text()) for x in p.findChildren(QLabel) if x.isVisible() and _plain(x.text())]
        if len(texts) < 2:
            return []
        all_txt = "\n".join(texts)
        return [(tr("复制这一块的文字"), all_txt)] if all_txt != own else []

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
                return                  # 整块可以点的卡片（例如侧边栏的数据库状态）：右键复制
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
        sel = view.selectionModel().selectedIndexes() if view.selectionModel() else []
        rows_sel = _selected_rows(view, sel) if sel else []
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
        row_of_idx = idx if hasattr(view, "isExpanded") else idx.row()
        if got == a_cell:
            cb.setText(_table_text(view, [row_of_idx], [idx.column()]))
        elif got == a_row:
            cb.setText(_table_text(view, [row_of_idx], cols))
        elif a_sel is not None and got == a_sel:
            cb.setText(_table_text(view, rows_sel, cols))
        elif got == a_all:
            cb.setText(_header_text(view, cols) + "\n" + _table_text(view, _visible_rows(view), cols))


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
        self.btn_pick = QPushButton(tr("选择文件"))
        self.btn_pick.setObjectName("Link")
        self.btn_pick.setCursor(Qt.PointingHandCursor)
        self.btn_pick.clicked.connect(self._pick)
        self.btn_open = QPushButton(tr("打开文件夹"))
        self.btn_open.setObjectName("Link")
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.clicked.connect(lambda: self.open_folder.emit())
        top.addWidget(self.btn_pick)
        top.addWidget(self.btn_open)
        self._top = top
        v.addLayout(top)
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(2)
        v.addLayout(self.list_box)
        self.hint = QLabel(hint)
        self.hint.setObjectName("Faint")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        v.addStretch()

    def add_link(self, btn):
        """标题行右边（「选择文件…」前面）再放一个链接按钮，例如供应商报价的「✉ 邮件」"""
        btn.setObjectName(btn.objectName() or "Link")
        btn.setCursor(Qt.PointingHandCursor)
        self._top.insertWidget(self._top.indexOf(self.btn_pick), btn)

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
        # 界面要简洁（2026-11-16）：说明放进标题的鼠标提示（标题后面 ⓘ），格子里只在空着时写一句「拖到这里」
        if note:
            self.title.setText(self.title_text + "  ⓘ")
            self.title.setToolTip(note)
        self.hint.setText(tr("把文件拖到这里"))
        self.hint.setVisible(not files)

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


def accept_file_drops(widget, callback, exts=(".xlsx", ".xls", ".xlsm"), multi=False):
    """
    让一个现成的控件（例如显示文件路径的输入框）也能拖文件进来：拖进来的第一个文件交给 callback(路径)；
    multi=True 时一次拖好几个，callback([路径, …])
    """
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
            callback(files if multi else files[0])

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
    pct = t.endswith("%")
    try:
        return (0, float(t[:-1] if pct else t), "")
    except ValueError:
        return (1, 0.0, s.lower())


SORT_ROLE = Qt.UserRole + 40          # 格子里可以放一个专门排序用的值（不放就按显示的文字排）


def header_text(table, c):
    """列名（去掉排序 / 筛选标记，导出 Excel 用）"""
    it = table.horizontalHeaderItem(c)
    return (it.text() if it else "").replace(" ⏷", "").replace(" ↑", "").replace(" ↓", "")


def remember_columns(table, cfg, key, fields, save, ver=1):
    """
    表格的列可以拖动换位置、拖宽度；顺序和宽度记在 cfg[key + "_columns" / "_widths"]，下次打开照旧。
      fields[c] = 第 c 列（逻辑列）的固定编号（存设置用；以后加列、改默认顺序不会认错列）
      save()    = 保存设置；ver = 默认顺序版本，改了默认顺序就加 1（用户拖过的顺序作废一次，列宽保留）
    返回 reset()：恢复默认顺序
    """
    hh = table.horizontalHeader()
    table.setProperty("_cols_remembered", True)        # 全程序的 WidthMemory 不再管这张表
    hh.setSectionsMovable(True)
    hh.setFirstSectionMovable(True)
    # 列编号可以是数字也可以是名字（「old_name」这种）：一律按文字比（用户 11-18：拖过的列宽下次打开没记住——名字当编号的表以前读不回来）
    col_of = {str(f): c for c, f in enumerate(fields)}
    if cfg.get(key + "_columns_ver") != ver:
        cfg.pop(key + "_columns", None)
        cfg[key + "_columns_ver"] = ver
        save()
    hh.blockSignals(True)
    for f, wd in (cfg.get(key + "_widths") or {}).items():
        c = col_of.get(str(f))
        if c is not None and wd and wd > 20:
            table.setColumnWidth(c, int(wd))
    order = [str(f) for f in (cfg.get(key + "_columns") or []) if str(f) in col_of]
    for v, f in enumerate(order):
        cur = hh.visualIndex(col_of[f])
        if cur != v and cur >= 0:
            hh.moveSection(cur, v)
    hh.blockSignals(False)
    timer = QTimer(table)
    timer.setSingleShot(True)
    timer.setInterval(400)

    def store():
        n = table.columnCount()
        cfg[key + "_columns"] = [fields[hh.logicalIndex(v)] for v in range(n)]
        old = cfg.get(key + "_widths") or {}
        # 藏起来的列宽度是 0：记原来的宽度（不然再显示出来时列宽回到默认）
        cfg[key + "_widths"] = {str(fields[c]): (old.get(str(fields[c]), 0) if table.isColumnHidden(c)
                                                 else table.columnWidth(c)) for c in range(n)}
        save()

    timer.timeout.connect(store)
    hh.sectionMoved.connect(lambda *_: timer.start())
    hh.sectionResized.connect(lambda *_: timer.start())

    def reset():
        hh.blockSignals(True)
        for c in range(table.columnCount()):
            cur = hh.visualIndex(c)
            if cur != c:
                hh.moveSection(cur, c)
        hh.blockSignals(False)
        cfg.pop(key + "_columns", None)
        save()

    return reset


class FillColumn(QObject):
    """
    让一列（通常是「商品」名称列）铺满表格剩下的宽度，但拖别的列宽时方向是对的。
    2026-11-19（用户：审核清单的列拖动是反的）：以前用 QHeaderView.Stretch —— 铺满的列在左边时，
    往右拖别的列的右边线，铺满的列跟着变窄，那条边线留在原地、这一列反而往左长，看起来就是「反的」。
    现在：这一列是普通可拖的列，只在表格变大变小（窗口缩放、侧栏开关）时重新算它的宽度；
    最后一列铺满（setStretchLastSection），拖宽别的列时右边的列往右挪、最后一列变窄，和 Excel 一样。
    用户自己拖了这一列的宽度以后就不再自动改它（下次打开照用户拖的）。
    """

    def __init__(self, table, col, min_w=220, last_w=110, cfg=None, key=None, save=None):
        super().__init__(table)
        self.t, self.col, self.min_w, self.last_w = table, col, min_w, last_w
        self.cfg, self.key, self.save = cfg, key, save
        self.user = bool(cfg is not None and key and cfg.get(key))
        self._busy = False
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(col, QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        hh.sectionResized.connect(self._resized)
        table.viewport().installEventFilter(self)

    def _resized(self, c, _old, _new):
        if c == self.col and not self._busy and QApplication.mouseButtons() & Qt.LeftButton:
            self.user = True                  # 用户自己拖了：以后不再自动铺满（记在设置里，下次打开也照用户的）
            if self.cfg is not None and self.key and not self.cfg.get(self.key):
                self.cfg[self.key] = True
                if self.save:
                    self.save()

    def reset(self):
        """恢复默认列宽时：又变回自动铺满"""
        self.user = False
        if self.cfg is not None and self.key:
            self.cfg.pop(self.key, None)
        self.fit()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize and ev.size().width() != ev.oldSize().width() \
                and not QApplication.mouseButtons() & Qt.LeftButton:     # 只管表格变宽变窄（拖列时出现滚动条不算）
            QTimer.singleShot(0, self.fit)
        return False

    def fit(self, force=False):
        try:
            t, hh = self.t, self.t.horizontalHeader()
            hh.setStretchLastSection(True)            # 表格第一次填好时 TableSortFilter 会关掉它，这里再打开
            if (self.user and not force) or t.isColumnHidden(self.col):
                return
            vis = [hh.logicalIndex(v) for v in range(t.columnCount())]
            vis = [c for c in vis if not t.isColumnHidden(c)]
            if not vis:
                return
            last = vis[-1]
            others = sum(t.columnWidth(c) for c in vis if c not in (self.col, last))
            if last != self.col:
                others += self.last_w
                if t.columnWidth(last) != self.last_w:      # 最后一列先放回默认宽（铺满时会再变宽，但不会自己变窄）
                    self._busy = True
                    hh.setStretchLastSection(False)
                    t.setColumnWidth(last, self.last_w)
                    hh.setStretchLastSection(True)
                    self._busy = False
            w = max(self.min_w, t.viewport().width() - others)
            if w != t.columnWidth(self.col):
                self._busy = True
                t.setColumnWidth(self.col, w)
                self._busy = False
        except RuntimeError:
            pass


def fit_to_screen(w, margin=12):
    """窗口比屏幕（可用区域，去掉任务栏）大、或者跑到屏幕外面时：缩小并挪回屏幕里"""
    from PySide6.QtGui import QGuiApplication
    try:
        scr = w.screen() or QGuiApplication.primaryScreen()
    except RuntimeError:
        return
    if scr is None:
        return
    av = scr.availableGeometry()
    g, fg = w.geometry(), w.frameGeometry()
    ew, eh = fg.width() - g.width(), fg.height() - g.height()        # 标题栏、边框
    max_w, max_h = av.width() - ew - 2 * margin, av.height() - eh - 2 * margin
    if g.width() > max_w or g.height() > max_h:
        w.resize(min(g.width(), max_w), min(g.height(), max_h))
        fg = w.frameGeometry()
    x = min(max(fg.x(), av.x()), av.x() + av.width() - fg.width())
    y = min(max(fg.y(), av.y()), av.y() + av.height() - fg.height())
    if (x, y) != (fg.x(), fg.y()):
        w.move(max(x, av.x()), max(y, av.y()))


class ScreenFit(QObject):
    """全程序：每个对话框 / 小窗口打开时都检查一次，不会比屏幕大、不会跑出屏幕（按钮被挡住就点不到了）"""
    TYPES = (QEvent.Show,)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, (QDialog, QMainWindow)) and obj.isWindow() \
                and not obj.isMaximized() and not obj.isFullScreen():
            def later(o=obj):
                try:
                    if o.isVisible():
                        fit_to_screen(o)
                except RuntimeError:          # 窗口已经关掉了
                    pass
            QTimer.singleShot(0, later)
        return False



class AppEventHub(QObject):
    """
    全程序只装这一个事件过滤器（2026-11-18，用户：打开程序、换深浅色都很卡）。
    以前装了 5 个（滚轮防误触、复制、窗口不超出屏幕、列宽记住、记最后点的按钮），每个界面事件
    （换颜色时几万个）都要进 5 次 Python，还在里面 import → 很卡。现在：先按事件类型查一下表，
    只有要管的类型才交给对应的那几个；换深浅色时 pause()，样式刷新期间一个都不进。
    add(处理者, 事件类型) —— 处理者有 eventFilter(obj, ev) 方法、返回 True = 吃掉这个事件。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.routes = {}
        self.paused = 0

    def add(self, handler, types=None):
        for t in (types if types is not None else handler.TYPES):
            self.routes.setdefault(t, []).append(handler.eventFilter)
        return handler

    def pause(self):
        self.paused += 1

    def resume(self):
        self.paused = max(0, self.paused - 1)

    def eventFilter(self, obj, ev):
        if self.paused:
            return False
        fns = self.routes.get(ev.type())
        if not fns:
            return False
        for fn in fns:
            try:
                if fn(obj, ev):
                    return True
            except RuntimeError:          # 控件刚被删掉
                pass
        return False

class WidthMemory(QObject):
    """
    全程序的表格：拖过的列宽都记住，下次打开照旧（用户 11-18「每次我拖动了列宽都要记住」）。
    已经用 remember_columns 自己记的表格不管。别的表格第一次有内容时把「按内容自动宽」的列改成可以拖
    （宽度按内容，最宽 420），用户拖了就记在 cfg[表格的名字] = {列号: 宽度}；
    表格的名字 = 窗口类名 + 列名（同一个窗口里几张表按列名分开）。
    """
    MAX_W = 420

    def __init__(self, parent, cfg, save):
        super().__init__(parent)
        self.cfg, self.save = cfg, save
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.save)

    @staticmethod
    def key_of(t):
        m = t.model()
        if m is None:
            return None
        heads = [str(m.headerData(c, Qt.Horizontal) or "") for c in range(m.columnCount())]
        if not any(heads):
            return None
        try:
            w = t.window().__class__.__name__
        except Exception:
            w = ""
        return w + "|" + "|".join(heads)

    TYPES = (QEvent.Show,)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QTableView) and not obj.property("_wm_on") \
                and not obj.property("_cols_remembered"):
            obj.setProperty("_wm_on", True)
            QTimer.singleShot(0, lambda t=obj: self._attach(t))
        return False

    def _attach(self, t):
        try:
            if t.property("_cols_remembered") or t.model() is None:
                return
            m, hh = t.model(), t.horizontalHeader()
        except RuntimeError:
            return
        st = {"key": None, "busy": False}

        def check():
            try:
                if t.property("_cols_remembered"):
                    return
                self._apply(t, st)
            except RuntimeError:
                pass

        later = QTimer(t)
        later.setSingleShot(True)
        later.setInterval(0)
        later.timeout.connect(check)
        for sig in (m.rowsInserted, m.modelReset, m.layoutChanged, m.columnsInserted):
            sig.connect(lambda *_: later.start())
        hh.sectionResized.connect(lambda c, _o, w: self._resized(t, st, c, w))
        check()

    def _apply(self, t, st):
        from PySide6.QtWidgets import QHeaderView
        hh = t.horizontalHeader()
        key = self.key_of(t)
        if key is None:
            return
        saved = self.cfg.get(key) or {}
        # 只改「按内容自动宽」的列；铺满剩下宽度的那一列（Stretch）照旧铺满，拖它旁边的列它跟着变
        auto = [c for c in range(t.model().columnCount()) if hh.sectionResizeMode(c) == QHeaderView.ResizeToContents]
        if key == st["key"] and not auto:
            return
        if not auto and not saved:
            st["key"] = key
            return
        if auto and not saved and t.model().rowCount() == 0:
            return                                   # 等有内容了再按内容定宽度
        st["busy"] = True
        try:
            for c in auto:
                hh.setSectionResizeMode(c, QHeaderView.Interactive)
            for c in auto:
                if str(c) in saved:
                    continue
                t.resizeColumnToContents(c)
                t.setColumnWidth(c, min(max(t.columnWidth(c), 56), self.MAX_W))
            for c, w in saved.items():
                try:
                    c = int(c)
                except ValueError:
                    continue
                if 0 <= c < t.model().columnCount() and w and w > 20 \
                        and hh.sectionResizeMode(c) == QHeaderView.Interactive:
                    t.setColumnWidth(c, int(w))
        finally:
            st["busy"] = False
        st["key"] = key

    def _resized(self, t, st, c, w):
        from PySide6.QtWidgets import QApplication
        if st["busy"] or t.property("_cols_remembered") or not (QApplication.mouseButtons() & Qt.LeftButton):
            return
        key = self.key_of(t)
        if key is None or w <= 20:
            return
        self.cfg.setdefault(key, {})[str(c)] = int(w)
        self._timer.start()


class ColumnChooser(QObject):
    """
    「显示哪些列」：勾选的列才显示，其他的藏起来（数据多的时候表格不乱）。记在 cfg[key + "_hidden"]。
      fields[c]      第 c 列（逻辑列）的固定编号（存设置用）
      default_hidden 默认藏起来的列（编号）；ver 改了默认就加 1（用户以前的选择作废一次）
      forced()       程序自己要藏的列（例如只有一家门店时的「门店」列），用户选了也不显示
    """

    def __init__(self, table, cfg, key, fields, save, default_hidden=(), ver=1, forced=None):
        super().__init__(table)
        self.t, self.cfg, self.key, self.fields, self.save = table, cfg, key, list(fields), save
        self.default = [f for f in default_hidden if f in self.fields]
        self.forced = forced or (lambda: set())
        if cfg.get(key + "_hidden_ver") != ver:
            cfg[key + "_hidden"] = list(self.default)
            cfg[key + "_hidden_ver"] = ver
            save()
        self.apply()

    def hidden(self):
        return set(self.cfg.get(self.key + "_hidden") or [])

    def apply(self):
        h, f2 = self.hidden(), set(self.forced() or ())
        for c, f in enumerate(self.fields):
            self.t.setColumnHidden(c, f in h or f in f2)

    def _set(self, hidden):
        self.cfg[self.key + "_hidden"] = sorted(hidden)
        self.save()
        self.apply()

    def button(self, text=None):
        b = QPushButton(text or tr("显示哪些列…"))
        b.setToolTip(tr("勾选要在表格里显示的列，其他的藏起来"))
        b.clicked.connect(lambda: self.menu(b.mapToGlobal(b.rect().bottomLeft())))
        return b

    def menu(self, gpos):
        from PySide6.QtWidgets import QMenu, QWidgetAction, QListWidget, QListWidgetItem
        m = QMenu(self.t)
        box = QWidget()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(8, 6, 8, 6)
        bv.setSpacing(4)
        tip = QLabel(tr("勾选要显示的列（拖动表头可以换位置）"))
        tip.setObjectName("Faint")
        bv.addWidget(tip)
        lst = QListWidget()
        lst.setMinimumWidth(240)
        lst.setMinimumHeight(min(520, 26 * len(self.fields) + 8))
        hh = self.t.horizontalHeader()
        forced = set(self.forced() or ())
        h = self.hidden()
        for v in range(len(self.fields)):
            c = hh.logicalIndex(v)
            f = self.fields[c]
            if f in forced:
                continue
            it = QListWidgetItem(header_text(self.t, c).replace(" ✎", "") or "—")
            it.setData(Qt.UserRole, f)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked if f in h else Qt.Checked)
            lst.addItem(it)
        bv.addWidget(lst)
        row = QHBoxLayout()
        b_all, b_def = QPushButton(tr("全部显示")), QPushButton(tr("恢复默认"))
        for b in (b_all, b_def):
            b.setObjectName("Link")
            row.addWidget(b)
        row.addStretch()
        bv.addLayout(row)

        def changed(_=None):
            self._set({lst.item(k).data(Qt.UserRole) for k in range(lst.count())
                       if lst.item(k).checkState() != Qt.Checked})

        def set_all(hidden):
            lst.blockSignals(True)
            for k in range(lst.count()):
                it = lst.item(k)
                it.setCheckState(Qt.Unchecked if it.data(Qt.UserRole) in hidden else Qt.Checked)
            lst.blockSignals(False)
            self._set(set(hidden))

        lst.itemChanged.connect(changed)
        b_all.clicked.connect(lambda: set_all(set()))
        b_def.clicked.connect(lambda: set_all(set(self.default)))
        wa = QWidgetAction(m)
        wa.setDefaultWidget(box)
        m.addAction(wa)
        m.exec(gpos)


class RowSorter(QObject):
    """
    给「一次填好的表格」加点表头排序（点一下升序，再点一下降序）。
      pin_last  最后一行（合计）永远留在最后
      refill    表格里有按钮 / 小标签这种控件时用：传 refill(order) 由调用方按新顺序重画
                （order[k] = 原来第几行现在放到第 k 行）；不传就直接搬动格子
      on_sorted 排完以后调用（例如重新套用搜索框的筛选）
    调用方每次重新填完表格后调用 apply()，保持用户选的排序。
    数字按数字排（€、逗号、%、「3 箱」都认），格子有 SORT_ROLE 数据时按它排。
    """

    def __init__(self, table, pin_last=False, refill=None, on_sorted=None, skip=()):
        super().__init__(table)
        self.t, self.pin_last, self.refill, self.on_sorted = table, pin_last, refill, on_sorted
        self.skip = set(skip)               # 不能排序的列（例如勾选框）
        self.col, self.desc = None, False
        self.titles = [header_text(table, c) for c in range(table.columnCount())]
        hh = table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(False)
        hh.sectionClicked.connect(self._clicked)
        table.setSortingEnabled(False)
        if not hh.toolTip():
            hh.setToolTip(tr("点表头排序（再点一次反过来）"))

    def _clicked(self, c):
        if c in self.skip:
            return
        if self.col != c:
            self.col, self.desc = c, False
        else:
            self.desc = not self.desc
        self.apply()

    def reset(self):
        self.col, self.desc = None, False
        self._marks()

    def _key(self, r):
        it = self.t.item(r, self.col)
        if it is None:
            return (2, 0.0, "")
        v = it.data(SORT_ROLE)
        if v is not None:
            return (0, float(v), "") if isinstance(v, (int, float)) else (1, 0.0, str(v).lower())
        k = _sort_key(it.text())
        return k if it.text().strip() not in ("", "—", "-") else (2, 0.0, "")   # 空的放最后

    def apply(self):
        """按现在选的列重新排一次（没选列就什么都不做）"""
        self._marks()
        t = self.t
        n = t.rowCount() - (1 if self.pin_last and t.rowCount() else 0)
        if self.col is None or n <= 1:
            return
        keys = [self._key(r) for r in range(n)]
        filled = [r for r in range(n) if keys[r][0] != 2]
        empty = [r for r in range(n) if keys[r][0] == 2]
        order = sorted(filled, key=lambda r: keys[r], reverse=self.desc) + empty
        if order == list(range(n)):
            return
        if self.refill:
            self.refill(order)
        else:
            blocked = t.signalsBlocked()
            t.blockSignals(True)
            cols = t.columnCount()
            with frozen(t):
                rows = [[t.takeItem(r, c) for c in range(cols)] for r in range(n)]
                hidden = [t.isRowHidden(r) for r in range(n)]
                for k, r in enumerate(order):
                    for c in range(cols):
                        if rows[r][c] is not None:
                            t.setItem(k, c, rows[r][c])
                    t.setRowHidden(k, hidden[r])
            t.blockSignals(blocked)
            t.viewport().update()
        if self.on_sorted:
            self.on_sorted()

    def _marks(self):
        flt = getattr(self, "filtered", ())          # HeaderFilter 筛选了的列：保留 ⏷
        for c, title in enumerate(self.titles):
            it = self.t.horizontalHeaderItem(c)
            if it is not None:
                it.setText(title + (" ⏷" if c in flt else "") +
                           ((" ↓" if self.desc else " ↑") if c == self.col else ""))

    def set_title(self, c, text):
        if 0 <= c < len(self.titles):
            self.titles[c] = text
            self._marks()


class HeaderFilter(QObject):
    """
    右键表头按这一列筛选（像 Excel）：给「一次填好、用隐藏行来筛选」的表格用（订货明细、缺失商品等）。
      页面自己还有筛选（搜索框、下拉）时：传 apply = 页面的筛选函数，它在判断每一行时加上 `not hf.accepts(r)`；
        base_hidden(r) = 只按页面自己的筛选这一行藏不藏（列出可选的值用，和 Excel 一样只列看得到的）。
      页面没有别的筛选：不传 apply，这里自己藏行。
      pin_last：最后一行（合计）不参与筛选；skip：不能筛选的列（勾选框等）。
      sorter：表格的 RowSorter（菜单里的升序 / 降序用它排；表头 ⏷ 标记和它的箭头一起显示）。
      extra：菜单最后再加的项 [(文字, 函数)]；own_menu=False 时由页面自己在表头右键里调用 menu()。
    """
    changed = Signal()

    def __init__(self, table, apply=None, base_hidden=None, pin_last=False, skip=(), sorter=None, extra=None,
                 own_menu=True, sort=None):
        super().__init__(table)
        self.t, self.apply_cb, self.base_hidden = table, apply, base_hidden
        self.pin_last, self.skip, self.sorter, self.extra, self.sort_cb = pin_last, set(skip), sorter, extra or [], sort
        self.filters = {}            # {列: 允许的文字集合}
        if own_menu:
            hh = table.horizontalHeader()
            hh.setContextMenuPolicy(Qt.CustomContextMenu)
            hh.customContextMenuRequested.connect(lambda p: self.menu(hh.logicalIndexAt(p), hh.mapToGlobal(p)))
            tip = hh.toolTip()
            hh.setToolTip((tip + "；" if tip else "") + tr("右键点表头按这一列筛选"))

    # ---- 给页面用 ----
    def _n(self):
        n = self.t.rowCount()
        return n - 1 if self.pin_last and n else n

    def text(self, r, c):
        it = self.t.item(r, c)
        if it is not None:
            return it.text().strip()
        w = self.t.cellWidget(r, c)
        return w.text().strip() if w is not None and hasattr(w, "text") else ""

    def accepts(self, r, skip_col=None):
        """这一行能不能通过表头筛选（合计行永远可以）"""
        if self.pin_last and r == self.t.rowCount() - 1:
            return True
        return all(self.text(r, c) in a for c, a in self.filters.items() if c != skip_col)

    def active(self):
        return bool(self.filters)

    def clear(self, refresh=True):
        self.filters = {}
        if refresh:
            self._changed()

    def set_filter(self, c, values=None):
        if values:
            self.filters[c] = set(values)
        else:
            self.filters.pop(c, None)
        self._changed()

    def reapply(self):
        """没有页面筛选时：按表头筛选藏 / 显示行（表格重新填过以后调用）"""
        t = self.t
        n = self._n()
        for r in range(n):
            hide = (self.base_hidden(r) if self.base_hidden else False) or not self.accepts(r)
            t.setRowHidden(r, hide)

    def _changed(self):
        self._marks()
        if self.apply_cb:
            self.apply_cb()
        else:
            self.reapply()
        self.changed.emit()

    def _marks(self):
        if self.sorter is not None:
            self.sorter.filtered = set(self.filters)
            self.sorter._marks()
            return
        for c in range(self.t.columnCount()):
            it = self.t.horizontalHeaderItem(c)
            if it is None:
                continue
            base = it.text().replace(" ⏷", "")
            it.setText(base + (" ⏷" if c in self.filters else ""))

    def values_of(self, c):
        """这一列可选的值和行数：只算其他条件（页面筛选 + 别的列）之后还看得到的行"""
        vals = {}
        for r in range(self._n()):
            if self.base_hidden and self.base_hidden(r):
                continue
            if not self.accepts(r, skip_col=c):
                continue
            v = self.text(r, c)
            vals[v] = vals.get(v, 0) + 1
        return vals

    def menu(self, c, gpos):
        from PySide6.QtWidgets import QMenu, QWidgetAction, QLineEdit, QListWidget, QListWidgetItem
        if c is None or c < 0:
            return
        m = QMenu(self.t)
        a_up = a_down = None
        if self.sorter is not None or self.sort_cb is not None:
            a_up = m.addAction(tr("升序排序"))
            a_down = m.addAction(tr("降序排序"))
            m.addSeparator()
        can = c not in self.skip
        lst = search = None
        keys = []
        if can:
            vals = self.values_of(c)
            keys = sorted(vals, key=_sort_key)
            allowed = self.filters.get(c)
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
            for v in keys[:3000]:
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
                m.close()
                if len(picked) == len(keys) and not text:
                    self.set_filter(c, None)
                else:
                    self.filters[c] = picked
                    self._changed()

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
        extra = []
        if self.extra:
            m.addSeparator()
            extra = [(m.addAction(txt), fn) for txt, fn in self.extra]
        if search is not None:
            search.setFocus()
        self.last_pos = gpos
        got = m.exec(gpos)
        if got is None:
            return
        if got in (a_up, a_down) and got is not None:
            desc = got == a_down
            if self.sort_cb is not None:
                self.sort_cb(c, desc)
            else:
                self.sorter.col, self.sorter.desc = c, desc
                self.sorter.apply()
        elif got == a_clear:
            self.set_filter(c, None)
        elif got == a_clear_all:
            self.clear()
        else:
            for a, fn in extra:
                if got == a:
                    fn()
                    break


class frozen:
    """
    with frozen(表格): 填很多行 / 藏很多行 / 换行顺序时先停刷新，做完一次画出来
    （2026-11-04 实测：3000 行 × 38 列从 4.3 秒降到 2.2 秒）。可以套着用，最外层结束才恢复
    """

    def __init__(self, t):
        self.t = t

    def __enter__(self):
        self.was = self.t.updatesEnabled()
        self.t.setUpdatesEnabled(False)
        return self.t

    def __exit__(self, *exc):
        self.t.setUpdatesEnabled(self.was)
        return False


class TableSortFilter(QObject):
    """
    管一张 QTableWidget 的排序和按列筛选（数据行 i ↔ 表格行 k 的对应由它维护）：
      text(i, c)  → 数据行 i 第 c 列显示的文字（筛选、导出、默认排序用）
      fill(i, k)  → 把数据行 i 画到表格第 k 行（调用方自己设颜色、可编辑等）
      key(i, c)   → 可选，排序用的值
      hide(i)     → 可选，页面上其他筛选条件（搜索框、开关）要藏起来的行
    左键点表头：排序（再点反过来）；右键点表头：这一列的筛选菜单（勾选要显示的值 / 搜索）。
    列宽：第一次填好数据后，按内容量好的列（ResizeToContents / Stretch）改成可以手动拖宽（_fit，最宽 420）；
    remember(cfg, key, fields, save)：列可以拖动换位置，顺序 / 宽度 / 显示哪些列记在设置里，右键菜单里有「显示哪些列」「恢复默认」。
    以后新表格都用它（用户 2026-10-28：列表都按之前好的经验来，列宽要能调）。
    """
    rendered = Signal(int, int)          # (显示行数, 总行数)
    MAX_AUTO_W = 420
    LAZY_FIRST, LAZY_CHUNK = 60, 40      # lazy：先画这么多行，剩下的每次画这么多行

    def __init__(self, table, count, text, fill, key=None, hide=None, parent=None, numeric=(), lazy=False):
        """
        lazy=True（2026-11-04 提速，用在一行要算很多东西的大表格：检查清单）：排序 / 筛选 / 搜索时只马上画屏幕上看得见的行，
        剩下的一小批一小批地画（每批之间界面照常响应）；往下拉到还没画的行马上画。
        这时直接读格子（table.item(k, c)）之前先 ensure(k)；要一次画完用 flush()；数据变了要重画的行用 invalidate()
        """
        super().__init__(parent or table)
        self.lazy, self._filled, self._next = lazy, set(), 0
        if lazy:
            self._lazy_timer = QTimer(self)
            self._lazy_timer.setSingleShot(True)
            self._lazy_timer.setInterval(0)
            self._lazy_timer.timeout.connect(self._fill_chunk)
            table.verticalScrollBar().valueChanged.connect(lambda _v: self._fill_view())
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
        self._fitted, self._auto_cols, self._saved_w = False, None, set()
        self._chooser, self._reset_layout = None, None

    # ---- 列宽 / 列顺序 ----
    def _fit(self):
        """按内容定一次列宽，然后改成可以手动拖（只管原来是自动宽度的列；页面自己设了宽度的列不动）"""
        from PySide6.QtWidgets import QHeaderView
        t, hh = self.t, self.t.horizontalHeader()
        if self._auto_cols is None:
            self._auto_cols = [c for c in range(t.columnCount())
                               if hh.sectionResizeMode(c) in (QHeaderView.ResizeToContents, QHeaderView.Stretch)]
            self._stretch = {c for c in self._auto_cols if hh.sectionResizeMode(c) == QHeaderView.Stretch}
        for c in self._auto_cols:
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c in self._auto_cols:
            if c in self._saved_w:
                continue
            t.resizeColumnToContents(c)
            w = min(max(t.columnWidth(c), 56), self.MAX_AUTO_W)
            if c in self._stretch:
                w = max(w, 260)
            t.setColumnWidth(c, w)
        self._fitted = True

    def remember(self, cfg, key, save, fields=None, default_hidden=(), ver=1):
        """列顺序 / 列宽 / 显示哪些列记在 cfg（fields = 每一列的固定编号，默认 0, 1, 2 …）"""
        fields = list(fields if fields is not None else range(self.t.columnCount()))
        self._saved_w = {c for c, f in enumerate(fields) if str(f) in (cfg.get(key + "_widths") or {})}
        reset = remember_columns(self.t, cfg, key, fields, save, ver)
        self._chooser = ColumnChooser(self.t, cfg, key + "_cols", fields, save, default_hidden, ver=ver)

        def reset_all():
            reset()
            cfg.pop(key + "_widths", None)
            save()
            self._saved_w = set()
            self._fit()
        self._reset_layout = reset_all

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

    # ---- lazy：先画看得见的行 ----
    def _fill_one(self, k):
        if k in self._filled or not 0 <= k < len(self.visible):
            return
        self._filled.add(k)
        self.fill(self.visible[k], k)

    def ensure(self, k):
        """表格第 k 行还没画就马上画（lazy 时直接读格子之前用）"""
        if self.lazy and k not in self._filled:
            t = self.t
            b = t.signalsBlocked()
            t.blockSignals(True)
            self._fill_one(k)
            t.blockSignals(b)

    def view_rows(self):
        """屏幕上看得见的表格行（上下各多算一点）"""
        t, n = self.t, len(self.visible)
        top = t.rowAt(0)
        top = 0 if top < 0 else top
        bottom = t.rowAt(max(0, t.viewport().height() - 1))
        bottom = top + 40 if bottom < 0 else bottom
        return range(max(0, top - 10), min(n, bottom + 30))

    def _fill_view(self):
        if not self.lazy:
            return
        todo = [k for k in self.view_rows() if k not in self._filled]
        if not todo:
            return
        t = self.t
        b = t.signalsBlocked()
        t.blockSignals(True)
        for k in todo:
            self._fill_one(k)
        t.blockSignals(b)

    def _fill_chunk(self):
        n, t = len(self.visible), self.t
        b = t.signalsBlocked()
        t.blockSignals(True)
        done = 0
        while self._next < n and done < self.LAZY_CHUNK:
            if self._next not in self._filled:
                self._fill_one(self._next)
                done += 1
            self._next += 1
        t.blockSignals(b)
        if self._next < n:
            self._lazy_timer.start()

    def flush(self):
        """剩下没画的行一次画完（要读整张表格子之前用；平时不用）"""
        if not self.lazy:
            return
        self._lazy_timer.stop()
        t = self.t
        b = t.signalsBlocked()
        t.blockSignals(True)
        t.setUpdatesEnabled(False)
        for k in range(len(self.visible)):
            self._fill_one(k)
        t.setUpdatesEnabled(True)
        t.blockSignals(b)

    def invalidate(self, ks=None):
        """数据变了：这些表格行（None = 全部）要重画。看得见的马上画，其余的一批一批重画"""
        if not self.lazy:
            return False
        if ks is None:
            self._filled.clear()
        else:
            self._filled.difference_update(ks)
        self._next = 0
        self._fill_view()
        self._lazy_timer.start()
        return True

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
        if self.lazy:
            self._lazy_timer.stop()
            t.clearContents()                      # 旧的格子不能留着（还没画的行会显示成别的商品）
        t.setRowCount(len(vis))
        self.visible, self.pos = vis, {i: k for k, i in enumerate(vis)}
        if self.lazy:
            self._filled, self._next = set(), 0
            for k in range(min(len(vis), self.LAZY_FIRST)):
                self._fill_one(k)
            for k in self.view_rows():
                self._fill_one(k)
            if len(vis) > len(self._filled):
                self._lazy_timer.start()
        else:
            for k, i in enumerate(vis):
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
        if not self._fitted and vis:
            self._fit()
        self.rendered.emit(len(vis), n)

    def set_title(self, c, text, tip=None):
        """改列名（排序 / 筛选标记会自动加在后面）"""
        if 0 <= c < len(self.titles):
            self.titles[c] = text
            it = self.t.horizontalHeaderItem(c)
            if it is not None:
                it.setText(text)
                if tip is not None:
                    it.setToolTip(tip)

    def set_filter(self, c, values=None):
        """这一列只显示 values 里的值；values 为 None / 空 = 不筛选这一列"""
        if values:
            self.filters[c] = set(values)
        else:
            self.filters.pop(c, None)
        self.render()

    def values_of(self, c):
        """这一列现在可选的值和行数（只算其他筛选条件之后还看得到的行，和 Excel 一样）"""
        others = {cc: a for cc, a in self.filters.items() if cc != c}
        vals = {}
        for i in range(self.count()):
            if self.hide and self.hide(i):
                continue
            if all(self.text(i, cc) in a for cc, a in others.items()):
                v = self.text(i, c)
                vals[v] = vals.get(v, 0) + 1
        return [(v, vals[v]) for v in sorted(vals, key=_sort_key)]

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
        m.addSeparator()
        a_copy_col = m.addAction(tr("复制这一列（显示的行）"))
        a_copy_name = m.addAction(tr("复制列名"))
        a_cols = a_reset = None
        if self._chooser is not None:
            m.addSeparator()
            a_cols = m.addAction(tr("显示哪些列…"))
            a_reset = m.addAction(tr("恢复默认列宽和顺序"))
        search.setFocus()
        got = m.exec(gpos)
        if got is not None and got == a_cols:
            self._chooser.menu(gpos)
            return
        if got is not None and got == a_reset:
            self._reset_layout()
            return
        if got == a_up:
            self.set_sort(c, False)
        elif got == a_down:
            self.set_sort(c, True)
        elif got == a_clear:
            self.filters.pop(c, None)
            self.render()
        elif got == a_clear_all:
            self.clear_filters()
        elif got == a_copy_col:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText("\n".join(self.text(i, c) for i in self.visible))
        elif got == a_copy_name:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self.titles[c] if c < len(self.titles) else "")

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
        open_file(parent, path, title or tr("导出 Excel"))      # 导出完直接打开（2026-11-07 用户）
        return path


class ColumnFilterBox(QWidget):
    """
    表格上方的筛选下拉框：「最低价供应商：[全部 ▾]」，选 MEGA 就只看 MEGA 的行。
    和右键表头的筛选是同一个筛选条件（那边改了，这里跟着变）。
    """

    def __init__(self, ctl, col, label, short=None, parent=None):
        from PySide6.QtWidgets import QComboBox
        super().__init__(parent)
        self.ctl, self.col, self.short = ctl, col, short or (lambda v: v)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        lb = QLabel(label)
        lb.setObjectName("Muted")
        h.addWidget(lb)
        self.box = QComboBox()
        self.box.setMinimumWidth(150)
        self.box.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.box.currentIndexChanged.connect(self._picked)
        h.addWidget(self.box)
        ctl.rendered.connect(lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        allowed = self.ctl.filters.get(self.col)
        self.box.blockSignals(True)
        self.box.clear()
        self.box.addItem(tr("全部"), None)
        vals = self.ctl.values_of(self.col)
        for v, n in vals:
            self.box.addItem(f"{self.short(v) if v != '' else tr('（空白）')}  ({n})", v)
        cur = 0
        if allowed:
            if len(allowed) == 1:
                v = next(iter(allowed))
                k = self.box.findData(v)
                if k < 0:
                    self.box.addItem(f"{self.short(v) if v != '' else tr('（空白）')}  (0)", v)
                    k = self.box.count() - 1
                cur = k
            else:
                self.box.addItem(tr("多个值（右键表头修改）"), "__multi__")
                cur = self.box.count() - 1
        self.box.setCurrentIndex(cur)
        self.box.blockSignals(False)

    def _picked(self, k):
        v = self.box.itemData(k)
        if v == "__multi__":
            return
        self.ctl.set_filter(self.col, None if v is None else {v})


class TransferEditDialog(QDialog):
    """
    人工改一个商品的调货：从哪家门店调、调几件（门店配货的订货明细、检查门店订货清单共用）。
      title    商品名；info 一句话（哪家店、要几件、一箱几件）
      stores   [{"sid", "name", "inv"(库存), "daily", "surplus"(留够自己后多出来的), "note"}]（不含调入门店本身）
      cur      现在的调货 (调出门店, 件数) 或 None；need 要的件数；pack 每箱件数；colli 系统建议的箱数
    确定后 self.value = (调出门店, 件数)；点「不调」= (None, 0)
    """

    def __init__(self, parent, title, info, stores, cur=None, need=0, pack=1, colli=0):
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QSpinBox, QAbstractItemView, QHeaderView
        super().__init__(parent)
        self.setWindowTitle(tr("调货"))
        self.value = None
        self.need, self.pack, self.colli = float(need or 0), float(pack or 1) or 1.0, int(colli or 0)
        self.stores = sorted(stores, key=lambda s: (-(s.get("surplus") or 0), -(s.get("inv") or 0), s["sid"]))
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(10)
        h = QLabel(title)
        h.setObjectName("PageTitle")
        h.setStyleSheet("font-size:16px;")
        h.setWordWrap(True)
        v.addWidget(h)
        lb = QLabel(info)
        lb.setObjectName("Muted")
        lb.setWordWrap(True)
        v.addWidget(lb)
        v.addWidget(QLabel(tr("① 从哪家门店调（多出来 = 库存 − 留够自己一个周期的销量）")))
        t = QTableWidget(len(self.stores), 5)
        t.setHorizontalHeaderLabels([tr("门店"), tr("库存"), tr("日均"), tr("多出来"), ""])
        t.verticalHeader().hide()
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        for r, s in enumerate(self.stores):
            vals = [s["name"], f"{float(s.get('inv') or 0):g}", f"{float(s.get('daily') or 0):.2f}",
                    f"{int(s.get('surplus') or 0)}", s.get("note") or ""]
            for c, x in enumerate(vals):
                it = QTableWidgetItem(x)
                if c in (1, 2, 3):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 4 or not s.get("surplus"):
                    it.setForeground(QColor(T("muted")))
                t.setItem(r, c, it)
        t.setMinimumHeight(min(260, 34 + 32 * max(1, len(self.stores))))
        t.setMinimumWidth(460)
        self.t = t
        v.addWidget(t)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("② 调几件")))
        self.sb = QSpinBox()
        self.sb.setRange(0, 99999)
        self.sb.setSuffix(tr(" 件"))
        self.sb.setMinimumWidth(120)
        self.sb.setMinimumHeight(30)
        row.addWidget(self.sb)
        self.after = QLabel("")
        self.after.setObjectName("Muted")
        row.addWidget(self.after, 1)
        v.addLayout(row)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color:{T('warn')};")
        v.addWidget(self.warn)
        bb = QHBoxLayout()
        b_none = QPushButton(tr("不调"))
        b_none.setToolTip(tr("这个商品不调货，照常订（比如过季品）"))
        bb.addWidget(b_none)
        bb.addStretch()
        b_cancel = QPushButton(tr("取消"))
        b_ok = QPushButton(tr("确定"))
        b_ok.setObjectName("Primary")
        b_ok.setDefault(True)
        bb.addWidget(b_cancel)
        bb.addWidget(b_ok)
        v.addLayout(bb)
        b_none.clicked.connect(lambda: self._done(None, 0))
        b_cancel.clicked.connect(self.reject)
        b_ok.clicked.connect(self._ok)
        t.itemSelectionChanged.connect(self._picked)
        self.sb.valueChanged.connect(lambda _: self._hint())
        t.itemDoubleClicked.connect(lambda _: self._ok())
        start = 0
        if cur and cur[0] is not None:
            start = next((i for i, s in enumerate(self.stores) if s["sid"] == cur[0]), 0)
        if self.stores:
            t.setCurrentCell(start, 0)
        if cur and cur[1]:
            self.sb.setValue(int(cur[1]))
        self._hint()
        self.sb.setFocus()
        self.sb.selectAll()

    def _store(self):
        r = self.t.currentRow()
        return self.stores[r] if 0 <= r < len(self.stores) else None

    def _picked(self):
        s = self._store()
        if not s:
            return
        default = int(min(math_ceil(self.need), int(s.get("surplus") or 0)))
        self.sb.setValue(max(0, default))
        self._hint()

    def _hint(self):
        s = self._store()
        p = self.sb.value()
        left = max(0.0, self.need - p)
        c2 = min(self.colli, math_ceil(left / self.pack)) if self.colli else math_ceil(left / self.pack)
        self.after.setText(tr("调完还要订 {0} 箱（原来 {1} 箱）", c2, self.colli))
        msg = ""
        if s and p > float(s.get("inv") or 0):
            msg = tr("{0} 只有 {1:g} 件库存，最多调这么多", s["name"], float(s.get("inv") or 0))
        elif s and p > int(s.get("surplus") or 0):
            msg = tr("超过 {0} 多出来的件数：调完它自己可能不够卖一个周期", s["name"])
        self.warn.setText(msg)
        self.warn.setVisible(bool(msg))

    def _ok(self):
        s = self._store()
        p = self.sb.value()
        if not s or p <= 0:
            self._done(None, 0)
            return
        inv = int(float(s.get("inv") or 0))
        if inv <= 0:
            self.warn.setText(tr("{0} 没有库存，调不了", s["name"]))
            self.warn.setVisible(True)
            return
        self._done(s["sid"], min(p, inv))

    def _done(self, sid, pieces):
        self.value = (sid if pieces else None, int(pieces))
        self.accept()


def math_ceil(x):
    import math
    return int(math.ceil(float(x) - 1e-9))


# =====================================================================
# 用 Excel（COM）的后台活：放在同一条一直不退出的线程里做
# =====================================================================
class _ExcelWorker:
    """
    2026-11-16 用户截图：一批门店比价，FOIANO 比完、开始 MARLIA 时整个程序卡死（hang.log：界面线程停在
    threading.Thread.start() 等新线程起来）。原因：每家店开一条新线程，比完的那条线程用过 Excel（COM），
    退出时 Windows 要拆掉它的 COM 环境，这一步拿着系统的「加载锁」又在等界面线程；同一时间界面线程开下一家的
    新线程要同一把锁 → 互相等，死锁。
    做法：Excel 的活（比价、修改分配、换方案、读报价…）都交给一条一直活着的线程排队做，线程不退出就不会拆 COM，
    下一家也不用开新线程。
    """

    def __init__(self):
        import queue
        self.q = queue.Queue()
        self.t = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.t is None:
                self.t = threading.Thread(target=self._loop, daemon=True, name="excel-worker")
                self.t.start()

    def submit(self, fn):
        self.start()
        self.q.put(fn)

    def busy(self):
        return not self.q.empty()

    def _loop(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        while True:
            fn = self.q.get()
            try:
                fn()
            except Exception:
                import logging
                logging.getLogger("procurement").exception("后台 Excel 任务出错")


EXCEL_WORKER = _ExcelWorker()


def run_excel_job(fn):
    """把要用 Excel 的后台活排进同一条线程（不阻塞界面，按顺序一件件做）"""
    EXCEL_WORKER.submit(fn)


def excel_call(fn):
    """在后台线程里（例如 BusyDialog.run 的 func）调用：交给 Excel 那条线程做完再返回结果（出错照样抛出）。
    别在界面线程、也别在 Excel 那条线程自己里面调用"""
    ev, box = threading.Event(), {}

    def job():
        try:
            box["r"] = fn()
        except BaseException as e:
            box["e"] = e
        finally:
            ev.set()
    EXCEL_WORKER.submit(job)
    ev.wait()
    if "e" in box:
        raise box["e"]
    return box.get("r")
