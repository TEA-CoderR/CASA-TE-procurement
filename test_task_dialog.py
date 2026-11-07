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


def test_background_pill_shows_progress_and_reopens_dialog():
    """2026-11-07 用户：后台运行看不到进度 → 顶栏小进度条；藏着时不重画步骤，拿回来时补上"""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from erp_widgets import TaskDialog, BgTaskPill, BG_TASKS
    win = QtWidgets.QMainWindow()
    pill = BgTaskPill(win)
    win.show()
    d = TaskDialog(win, "计算", "s", ["读取", "查数据库"])
    d.show()
    assert not pill.isVisible()
    d.to_background()
    d.post_step(1, "running", "3 / 5", "FOIANO")
    d.post_progress(42)
    app.processEvents()
    pill._refresh()                                                       # 程序里每 0.7 秒看一次
    assert d in BG_TASKS and pill.isVisible() and "42%" in pill._text
    assert d.rows[1].get("dirty") and d.rows[1]["cnt"].text() == ""        # 藏着：只记下来
    assert d.current_step() == "查数据库"
    pill.click()                                                          # 点一下：进度窗口回来
    app.processEvents()
    assert d.isVisible() and not d.in_background and d not in BG_TASKS
    assert d.rows[1]["cnt"].text() == "3 / 5" and d.pct_lbl.text() == "42%"
    assert QGuiApplication.modalWindow() is None                          # 拿回来不独占
    d.to_background()
    d.post_finished(True, "ok")
    app.processEvents()
    assert not BG_TASKS and not pill.isVisible() and not pill.timer.isActive()
    win.close()


def test_when_idle_waits_for_quiet_and_no_popup():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    import time
    from PySide6.QtCore import QTimer, QEventLoop
    import erp_widgets as EW
    ran = []
    EW.LAST_INPUT[0] = time.time()                 # 刚点过
    EW.when_idle(lambda: ran.append(time.time()), quiet_ms=500)
    t0 = time.time()
    loop = QEventLoop()
    QTimer.singleShot(1500, loop.quit)
    loop.exec()
    assert ran and ran[0] - t0 >= 0.4              # 等用户停手 0.5 秒才做


def test_finish_compute_in_worker_gives_same_previews(monkeypatch):
    """调货 + 起订量检查的模拟配货挪到计算线程：界面线程拿来直接用，不再重算一遍"""
    pytest.importorskip("PySide6")
    import store_order_page as P
    import store_orders as S
    calls = []
    monkeypatch.setattr(S, "apply_transfers", lambda calc, *a: calc["base"]["/a"]["1"].update({5: 1}))
    monkeypatch.setattr(S, "plan_supplier", lambda sc, *a, **k: calls.append(sc["path"]) or {"stores": {}, "bms": None})
    monkeypatch.setattr(S, "sc_moq", lambda sc: 300)
    calc = {"schede": [{"path": "/a"}, {"path": "/b"}], "base": {"/a": {"1": {3: 2}}, "/b": {}},
            "demand": {}, "stores": [1]}
    P.finish_compute(calc, {}, [], {}, {"x": 1}, {})
    ui = calc["_ui"]
    assert calls == ["/a", "/b"] and ui["colli"] == {"/a": {"1": {3: 2, 5: 1}}, "/b": {}}
    assert ui["colli"] is not calc["base"]                                # 复制的，改箱数不动建议值
    key = P._preview_key(calc["schede"][0], calc, ui["colli"]["/a"], {"x": 1}, None)
    assert key in ui["pv"]                                                # 界面线程按同一个键能取到
