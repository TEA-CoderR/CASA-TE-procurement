"""在 Excel 里改箱数 → 读回程序（store_orders.write_edit_copy / excel_sources / excel_changes / mark_read）"""
import os
import time

import store_orders as S


def _src(tmp_path):
    from openpyxl import Workbook
    src = tmp_path / "SCHEDA T.xlsx"
    wb = Workbook()
    wb.active.title = "SCHEDA ANAGRAFICA FORNITORI"
    for name in ("FOIANO", "MARLIA", "S.FILIPPO"):
        ws = wb.create_sheet(name)
        ws.append(["NO.", "Cod.Art.", "Codice EAN", "Descrizione", "PZ*COLLI", "Colli", "PREZZO NETTO", "IMPORTO"])
        for i in range(3):
            ws.append([i + 1, 101 + i, f"80{i}", f"ART {i}", 6, None, 2.0, f"=F{i + 2}*E{i + 2}*G{i + 2}"])
    wb.save(src)
    sc = S.parse_scheda(str(src))
    for it in sc["items"]:
        it["db_code"] = it["code"]
    return sc


def _edit(path, sheet, row, value):
    from openpyxl import load_workbook
    wb = load_workbook(path)
    wb[sheet].cell(row, 6).value = value
    wb.save(path)
    os.utime(path, (time.time() + 5, time.time() + 5))     # 修改时间一定变


def test_edit_copy_round_trip_changes_and_read_once(tmp_path):
    sc = _src(tmp_path)
    lines = {1: {2: 2}, 2: {3: 1}}
    out = str(tmp_path / "out")
    p = S.edit_copy_path(out, sc, "2611")
    S.write_edit_copy(sc, lines, p)
    assert os.path.basename(os.path.dirname(p)) == S.EDIT_FOLDER
    assert S.read_colli(sc, p) == {1: {2: 2, 3: 0, 4: 0}, 2: {2: 0, 3: 1, 4: 0}, 3: {2: 0, 3: 0, 4: 0}}
    assert not S.edit_changed(p)
    src = S.excel_sources(sc, out, None, "2611", log_dir=str(tmp_path / "log"))
    assert [k for _p, _s, k, _b in src] == ["edit"]
    assert S.excel_changes(sc, lines, src) == []           # 没改过

    _edit(p, "FOIANO", 2, 5)       # 2 → 5
    _edit(p, "MARLIA", 3, None)    # 1 → 清空
    _edit(p, "S.FILIPPO", 4, "x")  # 写错
    assert S.edit_changed(p) and S.excel_pending(src) == [p]
    # 程序里写出以后又改了 FOIANO 第 2 行（2 → 3）：以 Excel 为准，但提醒
    cur = {1: {2: 3}, 2: {3: 1}}
    ch = {(c["sid"], c["row"]): c for c in S.excel_changes(sc, cur, src)}
    assert ch[(1, 2)]["new"] == 5 and ch[(1, 2)]["now"] == 3 and ch[(1, 2)]["both"]
    assert ch[(2, 3)]["new"] == 0 and not ch[(2, 3)]["both"]
    assert ch[(3, 4)]["bad"] and ch[(3, 4)]["new"] is None
    S.mark_read(sc, src, str(tmp_path / "log"))
    assert not S.edit_changed(p)
    # 读回以后程序里再改：Excel 里的旧数不会再改回去
    src = S.excel_sources(sc, out, None, "2611", log_dir=str(tmp_path / "log"))
    assert [c for c in S.excel_changes(sc, {1: {2: 1}}, src) if not c["bad"]] == []


def test_order_files_read_back_against_plan_and_bms_only_members(tmp_path):
    sc = _src(tmp_path)
    out = str(tmp_path / "gen")
    os.makedirs(out)
    plan = {"stores": {1: {"status": "direct", "lines": {2: 2}, "colli": 2, "amount": 24},
                       2: {"status": "bms", "lines": {3: 1}}, 3: {"status": "wait", "lines": {4: 1}}},
            "bms": {"status": "direct", "colli": 1, "amount": 12, "per_store": {2: {3: 1}}}}
    fo, bms = os.path.join(out, "fo.xlsx"), os.path.join(out, "bms.xlsx")
    S.write_order_file(sc, {1: {2: 2}}, fo)
    S.write_order_file(sc, {2: {3: 1}}, bms)
    written = [(fo, sc["name"], "FOIANO", 2, 24, "direct", sc["path"]),
               (bms, sc["name"], S.BMS, 1, 12, "direct", sc["path"], ["2"])]
    res = {"results": [("LUNEDI", sc, plan)], "written": written, "mtimes": S.file_mtimes(written)}
    log = str(tmp_path / "log")
    src = S.excel_sources(sc, None, res, log_dir=log)
    assert {(k, frozenset(s)) for _p, s, k, _b in src} == {("order", frozenset({1})), ("order", frozenset({2}))}
    assert S.excel_pending(src, res, log) == []
    # 程序现在：FOIANO 2 箱、MARLIA 凑进 BMS 1 箱
    cur = {1: {2: 2}, 2: {3: 1}}
    _edit(bms, "MARLIA", 3, 4)
    _edit(bms, "S.FILIPPO", 4, 9)                          # 不在 BMS 里的门店页：不读
    assert S.excel_pending(src, res, log) == [bms]
    ch = S.excel_changes(sc, cur, src)
    assert [(c["sid"], c["row"], c["new"]) for c in ch] == [(2, 3, 4)]
    S.mark_read(sc, src, log)
    src = S.excel_sources(sc, None, res, log_dir=log)
    assert S.excel_pending(src, res, log) == [] and S.excel_changes(sc, {1: {2: 2}, 2: {3: 4}}, src) == []


def test_sheet_reads_back_with_undo(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import order_sheet as OS
    sc = _src(tmp_path)
    out = str(tmp_path / "out")

    class Page:
        settings, cfg, result, edited = {}, {}, None, set()
        calc = {"schede": [sc], "stores": [1, 2, 3], "demand": {}, "base": {sc["path"]: {}}}
        colli = {sc["path"]: {1: {2: 2}}}

        def _out_root(self):
            return out

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
        detail, _loading, msgs = [], False, []

        def _orig(self, sc, sid, row):
            return 0

        def _verdict(self, *a, **k):
            return "same", ""

        def flash(self, t):
            self.msgs.append(t)

    w = Win()
    sh = OS.OrderSheet(w)
    monkeypatch.setattr(OS.OrderSheet, "_xl_log", staticmethod(lambda: str(tmp_path / "log")))
    monkeypatch.setattr("erp_widgets.open_file", lambda *a, **k: True)
    sh.reload()
    sh.excel_edit()
    p = S.edit_copy_path(out, sc)
    assert os.path.exists(p)
    _edit(p, "MARLIA", 3, 2)
    monkeypatch.setattr(OS.ReadBackDialog, "exec", lambda self: 1)
    assert sh.excel_read() == 1
    assert w.page.colli[sc["path"]] == {1: {2: 2}, 2: {3: 2}}
    sh.undo()
    assert w.page.colli[sc["path"]][2] == {}
    app.processEvents()
