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
