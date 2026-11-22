# -*- coding: utf-8 -*-
"""VOLANTINO 备货（sqlite 模拟数据库 + 临时文件夹里的名单 / 门店清单）"""
import os
import sqlite3
import threading
from datetime import date

from openpyxl import Workbook

import volantino_plan as VP


def _list_file(path, rows, start, end):
    wb = Workbook()
    ws = wb.active
    ws.title = "ELENCO IMPORTO ITO"
    ws.append(["NO.", "Codice Articolo", "Descrizione1- Titolo", "Prezzo Continuo", "Prezzo Offerta", "start", "end"])
    s = (start - date(1899, 12, 30)).days            # 日期写成数字（真实文件就是这样）
    e = (end - date(1899, 12, 30)).days
    for i, (ean, name) in enumerate(rows, 1):
        ws.append([i, int(ean), name, 3.99, 2.99, s, e])
    wb.save(path)


def _db():
    con = sqlite3.connect(":memory:", check_same_thread=False)
    lock = threading.Lock()
    for s in range(1, 6):
        con.execute(f"CREATE TABLE vendite_list_{s} (CodArticolo INT, Qta REAL, Data TEXT)")
    con.execute("CREATE TABLE prodotti (CodArticolo INT, CodBarre TEXT, Descrizione TEXT, qtasmallbox REAL, PrezzoForn REAL)")
    con.execute("CREATE TABLE codbarre (CodArticolo INT, CodBarre TEXT)")
    con.execute("CREATE TABLE giacenza (CodArticolo INT, G1 REAL, G2 REAL, G3 REAL, G4 REAL, G5 REAL)")
    con.execute("INSERT INTO prodotti VALUES (101, '8000000000011', 'ACE CLASSICA 5L', 6, 2.0), "
                "(102, '8000000000028', 'DIXAN 21 LAV', 12, 3.0), (103, '8000000000035', 'FRIGGITRICE', 1, 40)")
    con.execute("INSERT INTO giacenza VALUES (101, 10, 0, 0, 0, 0), (102, 0, 0, 0, 0, 0), (103, 0, 0, 0, 0, 0)")
    ins = lambda code, qty, day: con.execute("INSERT INTO vendite_list_1 VALUES (?,?,?)", (code, qty, day + " 10:00:00"))
    ins(999, 1, "2025-01-01")                         # 门店很早就开了
    # ACE：上一期 VOLANTINO 15/10–01/11（18 天）卖 36 件 = 每天 2 件；促销前 28 天卖 14 件
    for d in range(15, 32):
        ins(101, 2, f"2026-10-{d:02d}")
    ins(101, 2, "2026-11-01")
    ins(101, 14, "2026-09-20")
    # DIXAN：没上过 VOLANTINO，最近 90 天卖 45 件 = 每天 0.5 件
    ins(102, 45, "2026-10-01")
    con.commit()
    import pandas as pd
    orig = pd.read_sql

    def fake(q, engine, params=None):
        p = tuple(str(x) if hasattr(x, "strftime") else x for x in (params or ()))
        with lock:
            return orig(q.replace("%s", "?"), con, params=p)
    return type("DB", (), {"engine": None})(), fake


def test_plan_from_past_volantino_and_estimate_then_write_and_undo(monkeypatch, tmp_path):
    import pandas as pd
    import reorder_service as R
    db, fake = _db()
    monkeypatch.setattr(pd, "read_sql", fake)
    root = tmp_path / "PROMO MESILE"
    (root / "2026" / "OTT 2026").mkdir(parents=True)
    (root / "2026" / "PROMO NATALIZIA").mkdir(parents=True)
    old = [("8000000000011", "ACE CLASSICA 5L")] + [(f"80999{i:08d}", f"ALTRO {i}") for i in range(25)]
    _list_file(str(root / "2026" / "OTT 2026" / "ELENCO VOLANTINO OTTOBRE.xlsx"), old, date(2026, 10, 15), date(2026, 11, 1))
    _list_file(str(root / "2026" / "PROMO NATALIZIA" / "PROMOZIONE NATALIZIA.xlsx"), old, date(2026, 11, 3), date(2026, 11, 20))
    newf = str(tmp_path / "MODULO PROMO DIC 26.xlsx")
    _list_file(newf, [("8000000000011", "ACE"), ("8000000000028", "DIXAN"), ("8000000000035", "FRIGGITRICE"),
                      ("8000000000042", "NON IN DB")], date(2026, 12, 3), date(2026, 12, 20))
    new = VP.read_list(newf)
    new["file"] = newf
    assert (new["start"], new["end"]) == ("2026-12-03", "2026-12-20")
    hist = VP.history(str(root), new, None, log=lambda *_: None, cache_path=str(tmp_path / "c.json"), today=date(2026, 11, 22))
    assert [h["name"] for h in hist] == ["ELENCO VOLANTINO OTTOBRE"]          # 圣诞货不算
    quotes = {"8000000000011": {"price": 2.1, "pack": 6, "supplier": "MELONI", "packs": [6]},
              "8000000000028": {"price": 3.2, "pack": 12, "supplier": "PETRILLO", "packs": [12]},
              "8000000000042": {"price": 1.0, "pack": 1, "supplier": "MELONI", "packs": [1]}}
    lists = {1: [{"code": "101", "ean": "8000000000011", "qty": 2, "reason": ""}]}
    res = VP.compute(db, new, hist, quotes, lists, None, stores=(1,), log=lambda *_: None,
                     today=date(2026, 11, 22), upto=date(2026, 11, 21))
    assert res["days"] == 18
    by = {r["ean"]: r for r in res["rows"]}
    ace, dix = by["8000000000011"], by["8000000000028"]
    assert ace["basis"] == "hist" and abs(ace["per_day"] - 2) < 1e-9 and ace["expected"] == 36
    assert ace["sugg"] == 5 and ace["new"] == 5 and ace["list"] == 2          # (36 − 10) ÷ 6 → 5 箱
    assert dix["basis"] == "est" and abs(dix["normal"] - 0.5) < 1e-9 and dix["lift"] == VP.LIFT_DEFAULT
    assert dix["sugg"] == 2 and dix["list"] is None                         # 0.5 × 2 × 18 = 18 件 ÷ 12 → 2 箱
    why = {s["ean"]: s["why"] for s in res["skipped"]}
    assert why == {"8000000000035": "这周批发商报价里没有", "8000000000042": "数据库里没有这个商品"}
    # 写进门店清单
    lf = str(tmp_path / "DETER1.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.append(["Cod.Art.", "Cod.Barre", "Descrizione", "Quantità"])
    ws.append(["101", "8000000000011", "ACE CLASSICA 5L", 2])
    ws.append(["555", "8000000000555", "ALTRO", 3])
    wb.save(lf)
    before = open(lf, "rb").read()
    undo = VP.write_lists({1: lf}, VP.changes(res["rows"]), VP.reason_text(res), backup_dir=str(tmp_path / "old"))
    got = {x["ean"]: x for x in R.parse_order_file(lf)["rows"]}
    assert got["8000000000011"]["qty"] == 5 and got["8000000000011"]["reason"] == "VOLANTINO 03/12-20/12"
    assert got["8000000000028"]["qty"] == 2 and got["8000000000028"]["code"] == "102"
    assert got["8000000000555"]["qty"] == 3 and got["8000000000555"]["reason"] == ""      # 别的行不动
    assert VP.is_tag(got["8000000000028"]["reason"])
    assert VP.undo_lists(undo) == [] and open(lf, "rb").read() == before


def test_open_list_file_is_not_touched(tmp_path):
    lf = tmp_path / "DETER2.xlsx"
    Workbook().save(str(lf))
    (tmp_path / "~$DETER2.xlsx").write_bytes(b"x")
    import pytest
    with pytest.raises(PermissionError):
        VP.write_lists({2: str(lf)}, [{"store": 2, "code": "1", "ean": "1", "name": "x", "new": 1}], "VOLANTINO")


def test_store_reason_volantino_kept_by_check_rules():
    import check_rules as CR
    x = {"list_reason": "VOLANTINO 03/12-20/12"}
    assert CR.store_reason(x) and VP.is_tag(x["list_reason"]) and not VP.is_tag("Promozione")


def test_excel_number_dates_read():
    import volantino as V
    assert V._day(46359) == date(2026, 12, 3) and V._day("46376") == date(2026, 12, 20)


def test_deduct_transit_option_and_check_does_not_deduct_twice():
    r = {"list": None, "pack": 6, "unit": 2.0, "sugg_gross": 6, "sugg_net": 3, "warn_base": []}
    rows = [r, dict(r, list=5.0), dict(r, edited=True, new=9)]
    VP.set_deduct(rows, True)
    assert [x["new"] for x in rows] == [3, 5, 9] and rows[0]["amount"] == 36
    VP.set_deduct(rows, False)
    assert [x["new"] for x in rows] == [6, 6, 9]
    res = {"start": "2026-12-03", "end": "2026-12-20"}
    net = VP.reason_text(res, True)
    assert net == "VOLANTINO 03/12-20/12 · IN ARRIVO DEDOTTO" and VP.transit_done(net)
    assert not VP.transit_done(VP.reason_text(res)) and VP.is_tag(net)
    assert VP.boxes_for(36, 10, 6, 12) == 3 and VP.boxes_for(36, 10, 6, 30) == 0
    import check_rules as CR
    def row(reason):
        return {"in_db": True, "pack": 6, "store": 1, "cycle": 14, "daily": None, "sales": [30, 60, 90, 180],
                "file_qty": 3, "suggested": 0, "tier": "B", "inv0": 70, "daily0": 0.5, "list_reason": reason,
                "transit_pcs": 12}
    a, b = row(net), row("VOLANTINO 03/12-20/12")
    CR.init_boxes([a, b], None, {"engine": "v1"})
    assert a["fbox"] == 3 and not a.get("transit_cut")              # 已经扣过：不再扣
    assert b["fbox"] == 1 and b["transit_cut"] == 2                  # 没扣过：审核时照常扣


def test_tag_not_confused_with_store_reason_and_appended(tmp_path):
    import reorder_service as R
    assert not VP.is_tag("Promozione o volantino") and VP.is_tag("Promozione o volantino | VOLANTINO 03/12-20/12")
    lf = str(tmp_path / "DETER3.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.append(["Cod.Art.", "Cod.Barre", "Descrizione", "Quantità", "MOTIVO"])
    ws.append(["101", "8000000000011", "ACE", 1, "Richiesto da un cliente"])
    wb.save(lf)
    reason = "VOLANTINO 03/12-20/12 · IN ARRIVO DEDOTTO"
    VP.write_lists({3: lf}, [{"store": 3, "code": "101", "ean": "8000000000011", "name": "ACE", "new": 4}], reason,
                   backup_dir=str(tmp_path / "old"))
    got = R.parse_order_file(lf)["rows"][0]
    assert got["qty"] == 4 and got["reason"] == "Richiesto da un cliente | " + reason and VP.transit_done(got["reason"])


def test_yellow_rows_and_non_ordinabile_not_ordered(tmp_path):
    from openpyxl.styles import PatternFill
    f = str(tmp_path / "DIC.xlsx")
    _list_file(f, [("8000000000011", "ACE"), ("8000000000028", "DIXAN"), ("8000000000035", "PERLANA"),
                   ("8000000000042", "BIANCO")], date(2026, 12, 3), date(2026, 12, 20))
    from openpyxl import load_workbook
    wb = load_workbook(f)
    ws = wb.active
    ws.cell(3, 3).fill = PatternFill("solid", fgColor="FFFFC000")          # 黄：不用订
    ws.cell(4, 8).value = "NON E ORDINABILE"
    ws.cell(5, 3).fill = PatternFill("solid", fgColor="FFFFF2CC")          # 浅米色：照常
    wb.save(f)
    new = VP.read_list(f, marks=True)
    assert {x["ean"]: x.get("skip") for x in new["items"]} == {
        "8000000000011": None, "8000000000028": "名单里标黄 · 不用订", "8000000000035": "NON E ORDINABILE",
        "8000000000042": None}
