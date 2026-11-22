"""DETERSIVO 订单文件人手改过 → 找出来、同步回程序；以前比价留下的数量清掉（2026-11-22）"""
import os
import time

from openpyxl import Workbook, load_workbook

import order_edits as OE


HEAD = ["CODICE EAN", "DESCRIZIONE", "PEZZI_X_CARTONE", "PREZZO NETTO", "ORDINE"]


def _quote(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _setup(tmp_path, monkeypatch):
    import reorder_service as RS
    import service
    from config import SUPPLIER_RESULT_COL
    monkeypatch.setattr(service, "_save_history", lambda *a, **k: None)
    monkeypatch.setattr(service, "_backup_inputs", lambda *a, **k: None)
    monkeypatch.setattr(service, "track_orders", lambda *a, **k: None)
    monkeypatch.setattr(service, "load_settings", lambda: {})
    root = tmp_path / "2026-11-22" / "S.CONCORDIO"
    (root / "ORDINE").mkdir(parents=True)
    (root / "distribuire").mkdir()
    (root / "_备份").mkdir()
    main = root / "ORDINE" / "确认订单.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(RS.ORDER_HEADERS)
    ws.append(["8000000000011", "SAPONE", 2, None, "1", 6, "S.CONCORDIO"])
    ws.cell(1, 31).value = "MEGA.xlsx"
    ws.cell(2, SUPPLIER_RESULT_COL).value = "MEGA.xlsx"
    wb.save(main)
    dist = root / "distribuire"
    _quote(dist / "MEGA.xlsx", [["8000000000011", "SAPONE", 6, 1.0, 2],
                                ["8000000000028", "DETERSIVO", 4, 2.0, None],
                                ["8000000000035", "VECCHIO", 12, 0.5, 1],        # 以前比价留下的
                                [None, "TANICA OLIO LT.5", 16, 1.9, None]])      # 没条码（MEGA 有的商品是这样）
    row = service._price_row(2, 2, {"MEGA.xlsx": 1.0}, {"MEGA.xlsx": 6}, "8000000000011", "SAPONE")
    data = {"state": {"rows": [row], "rules": [], "baseline": {2: "MEGA.xlsx"}, "assignment": {2: "MEGA.xlsx"},
                      "sources": {}, "ordine_file": str(main), "distribuire_path": str(dist),
                      "labels": {"MEGA.xlsx": "MEGA CASH"}},
            "suppliers": [{"name": "MEGA.xlsx", "colli": 2, "amount": 12, "rows": 1}]}
    service.export_orders(str(dist), {"MEGA.xlsx"}, labels={"MEGA.xlsx": "MEGA CASH"}, settings={})
    service.save_state(data)
    order = root / "Ordini" / "Ordine_S.CONCORDIO_MEGA CASH_2211.xlsx"
    return data, root, order


def _edit(order, values):
    wb = load_workbook(order)
    ws = wb.active
    for r, v in values.items():
        ws.cell(r, 5).value = v
    wb.save(order)
    t = time.time() + 10
    os.utime(order, (t, t))


def test_hand_edits_and_leftovers_found(tmp_path, monkeypatch):
    data, root, order = _setup(tmp_path, monkeypatch)
    assert order.exists() and (root / "distribuire" / OE.LOG_NAME).exists()
    assert OE.find(data) == []                         # 刚生成、没人改过：不读
    _edit(order, {2: 5, 3: 3, 5: 8})                   # SAPONE 2 → 5、DETERSIVO 加 3 箱、没条码的 TANICA 加 8 箱
    got = {e["desc"]: e for e in OE.find(data)}
    assert set(got) == {"SAPONE", "DETERSIVO", "VECCHIO", "TANICA OLIO LT.5"}
    assert got["SAPONE"]["kind"] == "manual" and (got["SAPONE"]["old"], got["SAPONE"]["new"]) == (2, 5)
    assert got["SAPONE"]["row"] == 2
    assert got["DETERSIVO"]["row"] is None and got["DETERSIVO"]["price"] == 2.0 and got["DETERSIVO"]["pack"] == 4
    assert got["TANICA OLIO LT.5"]["ean"] == "" and got["TANICA OLIO LT.5"]["new"] == 8
    assert got["VECCHIO"]["kind"] == "stale" and (got["VECCHIO"]["old"], got["VECCHIO"]["new"]) == (0, 1)
    assert OE.file_totals(str(order)) == (17.0, round(5 * 6 * 1.0 + 3 * 4 * 2.0 + 1 * 12 * 0.5 + 8 * 16 * 1.9, 2))


def test_sync_writes_edits_into_program_and_clears_leftovers(tmp_path, monkeypatch):
    import service
    from config import SUPPLIER_RESULT_COL
    data, root, order = _setup(tmp_path, monkeypatch)
    _edit(order, {2: 5, 3: 3, 5: 8})
    out, n = OE.apply(data, OE.find(data), lambda *a: None)
    assert n == 4
    m = load_workbook(root / "ORDINE" / "确认订单.xlsx").active
    assert m.cell(2, 3).value == 5                                            # 主订单 COLLI
    assert m.cell(3, 1).value == "8000000000028" and m.cell(3, SUPPLIER_RESULT_COL).value == "MEGA.xlsx"
    w = load_workbook(root / "distribuire" / "MEGA.xlsx").active
    assert [w.cell(r, 5).value for r in range(2, 6)] == [5, 3, None, 8]       # VECCHIO 清掉，TANICA 照订单文件
    st = out["state"]
    assert st["manual"] == {"MEGA.xlsx": {"#TANICA OLIO LT.5": 8.0}} and st["reconciled"]
    o = load_workbook(order).active                                           # 订单文件按程序重新生成
    assert [o.cell(r, 5).value for r in range(2, 6)] == [5, 3, None, 8]
    assert OE.find(out, check_all=True) == []
    # 之后修改分配 / 换方案：人手加的没条码那行照样保留
    out2, _n = service.rewrite_assignment(out, dict(st["assignment"]), {}, lambda *a: None, force=True)
    w = load_workbook(root / "distribuire" / "MEGA.xlsx").active
    assert [w.cell(r, 5).value for r in range(2, 6)] == [5, 3, None, 8]


def test_order_file_open_in_excel_is_not_synced(tmp_path, monkeypatch):
    import pytest
    data, root, order = _setup(tmp_path, monkeypatch)
    _edit(order, {2: 5})
    (root / "Ordini" / ("~$" + order.name[2:])).write_text("x")
    with pytest.raises(ValueError):
        OE.apply(data, OE.find(data), lambda *a: None)


def test_reconcile_reports_only_quantities_program_did_not_write(tmp_path):
    import service
    import quote_format as QF
    p = tmp_path / "Q.xlsx"
    _quote(p, [["8000000000011", "A", 6, 1.0, 2], ["8000000000028", "B", 4, 2.0, 1], ["8000000000035", "C", 6, 1.0, 3]])
    wb = load_workbook(p)
    ws = QF.order_sheet(wb)
    col_ord, index, oi = service._supplier_index(ws, str(p))
    s_n, c_n, stale = service._reconcile_orders(ws, str(p), col_ord, index, oi, {"8000000000011": 4}, {},
                                                {"8000000000011": 2, "8000000000028": 1})
    assert (s_n, c_n) == (1, 2)
    assert stale == [("C", 3.0)]                       # B 是上次程序写的（换了供应商）→ 正常清掉，不报
    assert [ws.cell(r, 5).value for r in range(2, 5)] == [4, None, None]


def test_tracking_reads_detersivo_order_file(tmp_path, monkeypatch):
    from datetime import date
    import order_record as REC
    data, root, order = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(OE, "_totals_cache_path", lambda: str(tmp_path / "cache" / "t.json"))
    OE._TOTALS.clear()
    _edit(order, {2: 5})
    o = {"origin": "wholesale", "file": str(order), "date": date.today().isoformat()}
    colli, amt, _mt, f = REC._wholesale_total(o, date.today(), 60, None)
    assert (colli, amt) == (6.0, 36.0) and f == str(order)
    assert REC._wholesale_total(dict(o, state="cancel"), date.today(), 60, None) is None
