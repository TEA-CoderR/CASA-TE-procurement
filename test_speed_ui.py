"""2026-11-18 提速：全程序一个事件过滤器、换颜色只刷新当前页、比价报价读一次、Excel 每条线程一个、比价进度不重建"""
import os
import shutil
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget, QDialog

app = QApplication.instance() or QApplication([])


class _H(QObject):
    TYPES = (QEvent.Show,)

    def __init__(self):
        super().__init__()
        self.seen = []

    def eventFilter(self, obj, ev):
        self.seen.append(ev.type())
        return False


def test_event_hub_routes_by_type_and_pauses():
    from erp_widgets import AppEventHub
    hub = AppEventHub(app)
    h = _H()
    hub.add(h)
    app.installEventFilter(hub)
    try:
        w = QWidget()
        w.show()
        app.processEvents()
        assert h.seen and set(h.seen) == {QEvent.Show}          # 只收到它要的类型
        n = len(h.seen)
        hub.pause()
        w2 = QWidget()
        w2.show()
        app.processEvents()
        assert len(h.seen) == n                                  # 停掉时一个都不进
        hub.resume()
        w3 = QWidget()
        w3.show()
        app.processEvents()
        assert len(h.seen) > n
        for x in (w, w2, w3):
            x.close()
    finally:
        app.removeEventFilter(hub)


def test_steps_dialog_runs_all_steps_and_keeps_going_after_error():
    from erp_widgets import StepsDialog
    done = []

    def bad():
        raise ValueError("x")
    errs = StepsDialog.run(None, "t", [("a", lambda: done.append(1)), ("b", bad), ("c", lambda: done.append(3))])
    assert done == [1, 3]
    assert [e[0] for e in errs] == ["b"]
    assert not [w for w in app.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()]


def _quote(path, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["CODICE EAN", "DESCRIZIONE", "PZ X CT", "PREZZO NETTO", "ORDINE"])
    for r in rows:
        ws.append(r)
    wb.save(path)


def test_quote_matrix_read_once_for_copies_in_every_store(tmp_path, monkeypatch):
    import settings
    import service
    monkeypatch.setattr(settings, "app_dir", lambda: str(tmp_path))
    service._QM_MEM.clear()
    src = tmp_path / "Listino MELONI.xlsx"
    _quote(src, [["8001", "A", 6, 1.5, None], ["8002", "B", 12, "2,10", None], ["8001", "A2", 6, 1.4, None]])
    stores = []
    for s in ("1 FOIANO", "2 MARLIA"):
        d = tmp_path / s
        d.mkdir()
        shutil.copy2(src, d / src.name)                        # 比价时每家门店复制一份（修改时间不变）
        stores.append(str(d / src.name))
    calls = []
    real = service.open_quote
    monkeypatch.setattr(service, "open_quote", lambda p, log: (calls.append(p), real(p, log))[1])
    a = service.quote_matrix(stores[0], print)
    service._QM_MEM.clear()                                    # 换了程序进程也一样：读磁盘缓存
    b = service.quote_matrix(stores[1], print)
    assert len(calls) == 1
    assert a == b and a["ok"]
    assert a["price"] == {"8001": 1.4, "8002": 2.1}
    assert a["count"] == {"8001": 2, "8002": 1}
    assert a["pack"]["8002"] == 12
    # 文件改了 → 重新读
    _quote(stores[1], [["8003", "C", 1, 3.0, None]])
    c = service.quote_matrix(stores[1], print)
    assert len(calls) == 2 and c["price"] == {"8003": 3.0}


def test_excel_instance_is_per_thread(monkeypatch):
    import excel_pool as EP
    made = []

    class Fake:
        Visible = False

        def Quit(self):
            pass
    monkeypatch.setattr(EP, "_new", lambda: (made.append(1), Fake())[1])
    EP._local.__dict__.clear()
    e1, new1 = EP.get_excel()
    e2, new2 = EP.get_excel()
    assert e1 is e2 and new1 and not new2
    box = {}
    t = threading.Thread(target=lambda: box.update(e=EP.get_excel()[0]))
    t.start()
    t.join()
    assert box["e"] is not e1                                  # 别的线程拿不到这条线程的 Excel
    assert len(made) == 2
    EP.close_pool()


def test_render_compare_updates_rows_in_place():
    import deter_steps as D
    from PySide6.QtWidgets import QVBoxLayout, QLabel, QProgressBar, QLineEdit, QPushButton, QTextEdit

    class Win:
        pass
    win = Win()
    page = QWidget()
    lay = QVBoxLayout(page)
    win._S = {"wholesale_batch": {"stores": {"1": "/x/1 FOIANO", "2": "/x/2 MARLIA"}}}
    win.bar, win.stage, win.elapsed = QProgressBar(), QLabel(), QLabel()
    win.btn_run, win.feed = QPushButton(), QTextEdit()
    win.folder_edit = QLineEdit("/x/1 FOIANO")
    win.running = True
    win._batch_queue = ["/x/2 MARLIA"]
    win._batch_results = []
    win._batch_info = {}
    D.build_compare(win, lay)
    win.stage.setText("读取报价 1/4")
    D.render_compare(win)
    first = list(win._cp_items)
    assert first[0][0].text() == "读取报价 1/4"
    win.stage.setText("读取报价 2/4")
    win.bar.setValue(40)
    D.render_compare(win)
    assert win._cp_items == first                              # 状态没变：同一批控件，只改文字
    assert first[0][0].text() == "读取报价 2/4"
    win._batch_results = [("1 FOIANO", True, "")]
    win._batch_info = {"1 FOIANO": (100.0, 10, 9, 2, 1, 30)}
    win.folder_edit.setText("/x/2 MARLIA")
    win._batch_queue = []
    D.render_compare(win)
    assert win._cp_items != first                              # 一家比完：重建
