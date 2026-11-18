"""用户 11-18：每次拖过的列宽都要记住（全程序的表格），名字当列编号的表也要记得住"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_dragged_widths_remembered_for_any_table():
    from unittest import mock
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView
    from erp_widgets import WidthMemory
    app = _app()
    cfg = {}
    wm = WidthMemory(app, cfg, lambda: None)
    app.installEventFilter(wm)
    try:
        def mk():
            d = QDialog()
            t = QTableWidget(0, 3)
            t.setHorizontalHeaderLabels(["A", "B", "C"])
            t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            QVBoxLayout(d).addWidget(t)
            d.show()
            app.processEvents()
            t.setRowCount(2)
            for r in range(2):
                for c in range(3):
                    t.setItem(r, c, QTableWidgetItem("x" * (c + 3)))
            app.processEvents()
            app.processEvents()
            return d, t
        d, t = mk()
        assert t.horizontalHeader().sectionResizeMode(0) == QHeaderView.Interactive     # 改成可以拖
        t.setColumnWidth(1, 150)                                                         # 程序自己设的不记
        assert cfg == {}
        with mock.patch.object(QApplication, "mouseButtons", return_value=Qt.LeftButton):
            t.setColumnWidth(0, 333)                                                     # 用户拖的
        assert list(cfg.values()) == [{"0": 333}]
        d.close()
        d2, t2 = mk()
        assert t2.columnWidth(0) == 333
        d2.close()
    finally:
        app.removeEventFilter(wm)


def test_remember_columns_with_name_fields():
    from PySide6.QtWidgets import QTableWidget
    from erp_widgets import remember_columns
    _app()
    cfg = {"x_widths": {"old_name": 400}, "x_columns_ver": 1}
    t = QTableWidget(0, 2)
    remember_columns(t, cfg, "x", ["at", "old_name"], lambda: None)
    assert t.columnWidth(1) == 400 and t.property("_cols_remembered")
