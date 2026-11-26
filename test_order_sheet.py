"""订货明细 · 订单表看法（order_sheet）：输入 / 粘贴 / 清空 / 撤销都改到 page.colli"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_parse_boxes_and_block():
    import order_sheet as OS
    assert OS.parse_boxes("") == 0 and OS.parse_boxes(" 3 ") == 3 and OS.parse_boxes("2,0") == 2
    assert OS.parse_boxes("-1") is None and OS.parse_boxes("1.5") is None and OS.parse_boxes("x") is None
    assert OS.parse_block("1\t2\r\n3\t\n") == [["1", "2"], ["3", ""]]


def test_route_of_plan():
    import order_sheet as OS
    plan = {"stores": {1: {"status": "direct", "colli": 3}, 2: {"status": "bms", "colli": 1},
                       3: {"status": "wait", "colli": 2}, 4: {"status": "none", "colli": 0}},
            "bms": {"status": "short"}}
    assert OS.route_of(plan, 1)[0] == "DIRETTO"
    assert OS.route_of(plan, 2) == ("BMS", "warn")
    assert OS.route_of(plan, 3)[1] == "faint" and OS.route_of(plan, 4)[0] == "—"


def _sheet():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import order_sheet as OS
    items = [{"row": 6 + i, "code": str(100 + i), "ean": f"80000000{i}", "desc": f"ART {i}", "pack": 4, "price": 2.5}
             for i in range(4)]
    sc = {"path": "p.xlsx", "name": "ACME", "day": "LUNEDI", "items": items, "sheets": {1: "FOIANO", 2: "MARLIA"},
          "moq_colli": 0, "moq_amount": 100}

    class Page:
        settings, cfg = {}, {}
        calc = {"schede": [sc], "stores": [1, 2], "days": [30, 60, 90, 180],
                "demand": {1: {}, 2: {}}, "base": {"p.xlsx": {1: {6: 2}, 2: {}}}}
        colli = {"p.xlsx": {1: {6: 2}, 2: {}}}
        edited = set()

        def set_qty(self, sc, sid, row, n, refresh=True):
            ln = self.colli.setdefault(sc["path"], {}).setdefault(sid, {})
            if n:
                ln[row] = n
            else:
                ln.pop(row, None)

        def lines_changed(self, path=None):
            pass

        def _preview(self, sc):
            return None

    class Win:
        page = Page()
        detail, _loading = [], False
        msgs = []

        def _orig(self, sc, sid, row):
            return self.page.calc["base"]["p.xlsx"].get(sid, {}).get(row, 0)

        def _verdict(self, sc, it, sid, n=None):
            return "same", "照建议订"

        def flash(self, t):
            self.msgs.append(t)

    w = Win()
    sh = OS.OrderSheet(w)
    sh.reload()
    return app, sh, w.page


def test_type_paste_clear_and_undo_change_page_colli():
    from PySide6.QtGui import QGuiApplication
    app, sh, page = _sheet()
    assert sh.tab == 1 and [sh.tabs.tabText(i) for i in range(sh.tabs.count())][-1]
    t = sh.table
    t.item(1, sh._colli_col).setText("3")        # 门店页 Colli 列
    app.processEvents()
    assert page.colli["p.xlsx"][1][7] == 3
    t.setCurrentCell(2, sh._colli_col)
    QGuiApplication.clipboard().setText("5\n1\n")
    sh.paste()
    assert page.colli["p.xlsx"][1] == {6: 2, 7: 3, 8: 5, 9: 1}
    sh.undo()
    assert page.colli["p.xlsx"][1] == {6: 2, 7: 3}
    t.setRangeSelected(__import__("PySide6.QtWidgets", fromlist=["QTableWidgetSelectionRange"])
                       .QTableWidgetSelectionRange(0, sh._colli_col, 1, sh._colli_col), True)
    sh.clear_selected()
    assert page.colli["p.xlsx"][1] == {}
    # 汇总页：每家店 库存 + Colli，MARLIA 的 Colli 能改
    sh.tabs.setCurrentIndex(sh.tabs.count() - 1)
    assert sh.tab == "sum"
    marlia = next(c for c in range(t.columnCount()) if "MARLIA" in str(sh.band.bands.get(c)) and "Colli" in t.horizontalHeaderItem(c).text())
    t.item(0, marlia).setText("2")
    app.processEvents()
    assert page.colli["p.xlsx"][2] == {6: 2}
