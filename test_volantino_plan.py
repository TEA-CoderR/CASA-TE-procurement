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
