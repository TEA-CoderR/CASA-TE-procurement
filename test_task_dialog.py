"""进度窗口点「后台运行」后，主窗口必须能点（2026-11-06：以前藏起来的进度窗口还占着「独占窗口」，整个程序点不动）"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtGui import QGuiApplication


def test_background_releases_modal_block():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from erp_widgets import TaskDialog
    win = QtWidgets.QMainWindow()
    win.show()
    dlg = TaskDialog(win, "t", "s", ["a"])
    dlg.show()
    app.processEvents()
    assert QGuiApplication.modalWindow() is not None          # 显示时挡住别的窗口（正常）
    dlg.to_background()
    app.processEvents()
    assert not dlg.isVisible()
    assert QGuiApplication.modalWindow() is None              # 后台运行：不再挡
    dlg.post_finished(True, "ok")
    app.processEvents()
    assert QGuiApplication.modalWindow() is None              # 完成后也不挡
    win.close()


def _answer(app, label):
    """等弹窗出来，点名字是 label 的按钮"""
    from PySide6.QtCore import QTimer

    def click():
        box = app.activeModalWidget()
        for b in box.buttons():
            if b.text() == label:
                b.click()
                return
    QTimer.singleShot(0, click)


def test_finished_in_background_asks_before_going_back():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from erp_widgets import TaskDialog, ask_return
    from i18n import tr
    win = QtWidgets.QMainWindow()
    win.show()
    went = []
    # 没点后台运行：直接打开结果，不弹窗
    d0 = TaskDialog(win, "t", "s", ["a"])
    d0.show()
    ask_return(win, d0, "完成", lambda: went.append("direct"))
    assert went == ["direct"]
    d0.post_finished(True, "")
    d0.accept()                                       # 程序里 0.7 秒后自己关
    # 点了后台运行：问一次，选「查看结果」才回去
    for label, expect in ((tr("查看结果"), ["yes"]), (tr("稍后"), [])):
        went.clear()
        d = TaskDialog(win, "t", "s", ["a"])
        d.show()
        d.to_background()
        d.post_finished(True, "ok")
        app.processEvents()
        _answer(app, label)
        ask_return(win, d, "完成", lambda: went.append("yes"))
        assert went == expect
        assert QGuiApplication.modalWindow() is None       # 问完不挡任何窗口
    win.close()
