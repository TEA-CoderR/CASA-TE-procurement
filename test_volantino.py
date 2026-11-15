# -*- coding: utf-8 -*-
"""VOLANTINO 销售分析（sqlite 模拟数据库）"""
import os
import sqlite3
import tempfile
import threading
from datetime import date

from openpyxl import Workbook, load_workbook

import volantino as V

COLS = ("CodArticolo INT, CodBarre TEXT, Qta REAL, PrezzoAcquisto REAL, PrezzoVendi REAL, PrezzoVendiNormale REAL, "
        "TotAcquisto REAL, TotRighe REAL, Guadagno REAL, TotCodIva REAL, Data TEXT")


def _line(con, sid, code, day, qty, cost, price, normal):
    gross = price * qty
    iva = gross - gross / 1.22
    con.execute(f"INSERT INTO vendite_list_{sid} VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (code, f"80{code}", qty, cost, price, normal, cost * qty, gross, gross - iva - cost * qty, iva, day + " 10:00:00"))


def _db():
    con = sqlite3.connect(":memory:", check_same_thread=False)
    lock = threading.Lock()
    for s in range(1, 6):
        con.execute(f"CREATE TABLE vendite_list_{s} ({COLS})")
    con.execute("CREATE TABLE prodotti (CodArticolo INT, CodBarre TEXT, Descrizione TEXT)")
    con.execute("CREATE TABLE codbarre (CodArticolo INT, CodBarre TEXT)")
    con.execute("CREATE TABLE promozioni (l INT, Data_a TEXT, Data_f TEXT, negozio TEXT)")
    con.execute("CREATE TABLE promozioni_list (promozioni_l INT, CodArticolo INT, Prezzo_p REAL, sconto INT, invalid INT)")
    con.execute("CREATE TABLE promozioni_volantino (promozioni_id INT, promozioni_title TEXT, promozioni_details TEXT)")
    for c, n in ((1, "ACE 5L"), (2, "DIXAN 21"), (3, "ALTRO")):
        con.execute("INSERT INTO prodotti VALUES (?,?,?)", (c, f"80{c}", n))
    # 门店 1：ACE 在周期里卖 10 件（原价 3.66 → 2.44），周期前卖 2 件；DIXAN 1 件；别的商品当月卖不打折
    _line(con, 1, 1, "2026-11-13", 10, 1.0, 2.44, 3.66)
    _line(con, 1, 1, "2026-11-05", 2, 1.0, 3.66, 3.66)
    _line(con, 1, 2, "2026-11-14", 1, 3.0, 4.88, 4.88)
    _line(con, 1, 3, "2026-11-02", 50, 1.0, 2.44, 2.44)
    _line(con, 1, 3, "2026-10-30", 99, 1.0, 2.44, 2.44)          # 上个月：不算
    # 门店 2：ACE 3 件
    _line(con, 2, 1, "2026-11-20", 3, 1.0, 2.44, 3.66)
    con.execute("INSERT INTO promozioni VALUES (17, '2026-11-13', '2026-11-26', '1,2')")
    con.execute("INSERT INTO promozioni VALUES (5, '2025-01-01', '2025-01-10', '')")          # 太旧
    con.execute("INSERT INTO promozioni_list VALUES (17, 1, 2.44, 0, 0), (17, 2, 0, 0, 0), (17, 3, 1, 0, 1)")
    con.execute("INSERT INTO promozioni_volantino VALUES (17, 'Volantino Natale', '')")
    con.commit()
    import pandas as pd
    orig = pd.read_sql

    def fake(q, engine, params=None):
        p = tuple(str(x) if hasattr(x, "strftime") else x for x in (params or ()))
        with lock:
            return orig(q.replace("%s", "?"), con, params=p)
    return type("DB", (), {"engine": None})(), fake


def test_analyze_store_margin_discount_and_month_impact(monkeypatch):
    import pandas as pd
    db, fake = _db()
    monkeypatch.setattr(pd, "read_sql", fake)
    ps = V.db_periods(db, since=date(2026, 1, 1))
    assert [(p["promo"], p["name"], p["stores"], p["n"]) for p in ps] == [(17, "Volantino Natale", [1, 2], 2)]
    res = V.analyze(db, ps[0], today=date(2026, 11, 30))
    assert res["month"] == (date(2026, 11, 1), date(2026, 11, 30)) and res["stores"] == [1, 2]
    s1 = res["store"][1]
    assert round(s1["qty"]) == 11 and abs(s1["rev"] - (24.4 + 4.88) / 1.22) < 1e-6
    assert abs(s1["profit"] - (s1["rev"] - 13.0)) < 1e-6
    assert abs(s1["disc"] - 12.2 / 1.22) < 1e-6                     # 让利：(3.66 − 2.44) × 10，不含税 = 10
    m = s1["month"]                                                 # 当月全店（门店 1）：不含上个月
    assert abs(m["rev"] - (24.4 + 7.32 + 4.88 + 122) / 1.22) < 1e-6
    im = s1["impact"]
    assert im["impact"] < 0 and abs(im["margin_wo"] - (m["profit"] + 10) / (m["rev"] + 10)) < 1e-9
    ace = next(r for r in res["items"] if r["code"] == "1")
    assert ace["qty"] == 13 and ace["stores"][1]["before"] == 2 and ace["stores"][2]["qty"] == 3
    assert res["total"]["sold"] == 2 and res["total"]["items"] == 2      # 名单里作废的（invalid）不算
    assert len(V.day_series(res)) == 14 and V.day_series(res, 2)[7][1] > 0
    rows = V.item_rows(res, 2)
    assert [r["qty"] for r in rows] == [3.0, 0.0]                   # 这家店没卖的也列
    p = V.export(res, os.path.join(tempfile.mkdtemp(), "v.xlsx"), names={1: "FOIANO", 2: "MARLIA"})
    wb = load_workbook(p)
    assert wb.sheetnames == ["Negozi", "Articoli"]


def test_file_list_dates_prices_and_saved_periods(monkeypatch):
    d = tempfile.mkdtemp()
    wb = Workbook()
    ws = wb.active
    ws.append(["VOLANTINO NATALE dal 13/11/2026 al 26/11/2026"])
    ws.append([])
    ws.append(["Cod.Art.", "Cod.Barre", "Descrizione", "Prezzo Promo"])
    ws.append(["1", "801", "ACE 5L", 2.44])
    ws.append([None, "802", "DIXAN 21", 3.99])
    path = os.path.join(d, "Volantino natale.xlsx")
    wb.save(path)
    got = V.read_file(path)
    assert (got["start"], got["end"]) == (date(2026, 11, 13), date(2026, 11, 26))
    assert [x["ean"] for x in got["items"]] == ["801", "802"] and got["prices"] == {0: 2.44, 1: 3.99}
    assert V.guess_dates(["VOLANTINO 13-11 / 26-11"], 2026) == (date(2026, 11, 13), date(2026, 11, 26))
    assert V.guess_dates(["Ordine 1810"]) == (None, None)
    ps = [{"id": "f1", "name": "A", "start": "2026-11-13", "end": "2026-11-26", "source": "file", "items": []},
          {"id": "db17", "source": "db", "start": "2026-11-13", "end": "2026-11-26"}]
    V.save_periods(d, ps)
    assert [p["id"] for p in V.load_periods(d)] == ["f1"] and V.new_id(ps) == "f2"
    assert "13/11" in V.label(ps[0])
    assert V.month_range(date(2026, 10, 28), date(2026, 11, 10), upto=date(2026, 11, 15)) == \
        (date(2026, 10, 1), date(2026, 11, 15))
    assert V._stores_of("0,1,2,3") == [1, 2, 3] and V._stores_of("") == [1, 2, 3, 4, 5]


def test_real_volantino_file_layout_and_christmas_kind():
    """ELENCO_VOLANTINO_OTTOBRE 那种文件：几页一样的表头（BOZZA 草稿 / ELENCO 定稿），「Codice Articolo」里写的是条码，
    分组标题行没编号，start / end 每行一个（定稿那页往下拖成了 2027、2028…）"""
    d = tempfile.mkdtemp()
    wb = Workbook()
    head = ["NO.", "PAGINA", "NO. ART.", "FOTO", "Codice Articolo", "Descrizione1- Titolo", "Descrizione2- Brand",
            "Prezzo Continuo", "Prezzo Offerta", "NOTE", "start", "end"]
    rows = [(8034116410967, "TERMOVENTILATORE", 9.99), (None, "SVELTO", None), ("5420020007134", "COMBUSTIBILE 9L", 47.99),
            (8720182982780, "SVELTO PIATTI ACETO", 1.79)]
    y0 = date.today().year
    for title, offer, years in (("BOZZA ELENCO.", 0, [y0] * 4), ("ELENCO importo ITO", 1, [y0 + i for i in range(4)])):
        ws = wb.create_sheet(title)
        ws.append(head)
        for (ean, name, price), y in zip(rows, years):
            ws.append([1, 1, 1, None, ean, name, None, price, price and price - offer, None, f"15/10/{y}", f"01/11/{y}"])
    del wb["Sheet"]
    path = os.path.join(d, "ELENCO_VOLANTINO_OTTOBRE.xlsx")
    wb.save(path)
    got = V.read_file(path)
    assert got["sheet"] == "ELENCO importo ITO"
    assert [x["ean"] for x in got["items"]] == ["8034116410967", "5420020007134", "8720182982780"]
    assert got["items"][0]["price"] == 8.99 and got["items"][0]["normal"] == 9.99 and got["items"][0]["code"] == ""
    assert (got["start"], got["end"]) == (date(date.today().year, 10, 15), date(date.today().year, 11, 1))
    assert V.kind_of({"name": "ELENCO NATALE 2026"}) == "natale" and V.kind_of({"name": "Volantino"}) == "volantino"
    assert V.kind_of({"name": "Natale", "kind": "volantino"}) == "volantino"
