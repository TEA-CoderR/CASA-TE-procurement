# -*- coding: utf-8 -*-
"""SELL OUT 分析（sqlite 模拟数据库）"""
import os
import sqlite3
import tempfile
import threading
from datetime import date

from openpyxl import load_workbook

import sellout as S

VCOLS = ("CodArticolo INT, Qta REAL, PrezzoAcquisto REAL, TotAcquisto REAL, TotRighe REAL, TotCodIva REAL, Data TEXT")


def _sale(con, sid, code, day, qty, cost, price):
    gross = price * qty * 1.22
    con.execute(f"INSERT INTO vendite_list_{sid} VALUES (?,?,?,?,?,?,?)",
                (code, qty, cost, cost * qty, gross, gross - price * qty, day + " 10:00:00"))


def _db():
    con = sqlite3.connect(":memory:", check_same_thread=False)
    lock = threading.Lock()
    for s in range(1, 6):
        con.execute(f"CREATE TABLE vendite_list_{s} ({VCOLS})")
    con.execute("CREATE TABLE prodotti (CodArticolo INT, CodBarre TEXT, Descrizione TEXT, IDFornitore INT, Categoria INT, "
                "Sottocategoria INT, PrezzoForn REAL)")
    con.execute("CREATE TABLE categorie (Categorie INT, SottoCategorie INT, Descrizione TEXT)")
    con.execute("CREATE TABLE fornitore (IDFornitore INT, Nome TEXT)")
    con.execute("CREATE TABLE caricolistalist (CodArticolo INT, Cdata TEXT, Quantita REAL, Tipo TEXT, Prezzo_A REAL, "
                "Negozio INT)")
    gcols = ", ".join(f"G{s} REAL" for s in range(6))
    con.execute(f"CREATE TABLE giacenza (CodArticolo INT, {gcols})")
    con.executemany("INSERT INTO categorie VALUES (?,?,?)", [
        (6, None, "NATALE E FESTE SPECIALI"), (6, 1, "FESTE SPECIALI -  ACC.CARNEVALE E HALLOWEEN"),
        (6, 2, "FESTE SPECIALI - ADDOBBI"), (6, 3, "LUCI NATALE"), (2, None, "CUCINA"),
        (2, 1, "CAFFETTIERE E ACCESSORI - BOLLITORI")])
    con.executemany("INSERT INTO fornitore VALUES (?,?)", [(1003, "DUE ESSE CHRISTMAS S.R.L."), (5, "ALTRO")])
    con.executemany("INSERT INTO prodotti VALUES (?,?,?,?,?,?,?)", [
        (1, "8053482147512", "LANTERNA HALLOWEEN", 1003, 6, 1, 0.7),
        (2, "8053482350004", "COLLANA ZUCCA", 1003, 6, 1, 0.6),
        (3, "8000000000003", "ADDOBBO", 1003, 6, 2, 1.0),
        (4, "8000000000004", "BOLLITORE", 5, 2, 1, 9.0)])
    # 进货：仓库 CA 100 @0.77 → 调 40 到门店 1；门店 2 直接 CA 8 @0.77；UI-N 不算
    con.executemany("INSERT INTO caricolistalist VALUES (?,?,?,?,?,?)", [
        (1, "2026-10-01", 100, "CA", 0.77, 0), (1, "2026-10-02", -40, "UI", None, 0), (1, "2026-10-02", 40, "CI", None, 1),
        (1, "2026-10-03", 8, "CA", 0.77, 2), (1, "2026-10-03", -8, "UI-N", None, 0),
        (2, "2026-10-01", 50, "CA", 0.6, 1), (4, "2026-10-01", 2, "CA", 9.0, 1),
        (1, "2026-09-01", 999, "CA", 0.77, 0)])                    # 日期外
    _sale(con, 1, 1, "2026-10-10", 30, 0.77, 1.39)
    _sale(con, 2, 1, "2026-10-11", 5, 0.77, 1.39)
    _sale(con, 1, 2, "2026-10-12", 70, 0.6, 0.82)                  # 卖超了
    _sale(con, 1, 4, "2026-10-12", 1, 9.0, 15.0)
    _sale(con, 1, 1, "2026-11-01", 99, 0.77, 1.39)                 # 日期外
    con.executemany(f"INSERT INTO giacenza VALUES (?,?,?,?,?,?,?)", [
        (1, 60, 10, 3, 0, 0, 0), (2, 0, -20, 0, 0, 0, 0), (3, 0, 0, 5, 0, 0, 0), (4, 0, 1, 0, 0, 0, 0)])
    con.commit()
    import pandas as pd
    orig = pd.read_sql

    def fake(q, engine, params=None):
        p = tuple(str(x) if hasattr(x, "strftime") else x for x in (params or ()))
        with lock:
            return orig(q.replace("%s", "?"), con, params=p)
    S._TREE.clear()
    return type("DB", (), {"engine": None})(), fake


def _run(monkeypatch, **f):
    import pandas as pd
    import reorder_service as R
    db, fake = _db()
    monkeypatch.setattr(pd, "read_sql", fake)
    R._SUP_NAMES.clear()
    base = {"start": date(2026, 10, 1), "end": date(2026, 10, 31)}
    base.update(f)
    return S.build(S.query(db, base))


def test_split_sub_into_mid_and_small():
    assert S.split_sub("FESTE SPECIALI -  ACC.CARNEVALE E HALLOWEEN") == ("FESTE SPECIALI", "ACC.CARNEVALE E HALLOWEEN")
    assert S.split_sub("CAFFETTIERE E ACCESSORI - BOLLITORI") == ("CAFFETTIERE E ACCESSORI", "BOLLITORI")
    assert S.split_sub("LUCI NATALE") == ("LUCI NATALE", "")
    assert S.split_sub("ANTI-CALCARE") == ("ANTI-CALCARE", "")          # 没有空格的横线不拆


def test_all_stores_template_formulas(monkeypatch):
    res = _run(monkeypatch)
    by = {r["code"]: r for r in res["rows"]}
    a = by[1]
    # 全部（含仓库）：仓库 CA 100 + 门店 2 CA 8；调货一出一进抵掉；UI-N 不算
    assert a["pq"] == 108 and abs(a["pv"] - 108 * 0.77) < 1e-9 and abs(a["p_avg"] - 0.77) < 1e-9
    assert a["sq"] == 35 and abs(a["sv"] - 35 * 1.39) < 1e-6
    assert abs(a["rate_q"] - 35 / 108) < 1e-9 and abs(a["rate_v"] - a["sv"] / a["pv"]) < 1e-9
    assert abs(a["diff_rate"] - (a["sv"] - a["pv"]) / a["sv"]) < 1e-9
    assert abs(a["profit"] - 35 * (1.39 - 0.77)) < 1e-6 and abs(a["margin"] - a["profit"] / a["sv"]) < 1e-9
    assert a["rem_q"] == 73 and abs(a["rem_v"] - 73 * 0.77) < 1e-9
    assert a["stock"] == 73 and a["sup_name"].startswith("DUE ESSE")
    assert (a["cat_name"], a["mid"], a["small"]) == ("NATALE E FESTE SPECIALI", "FESTE SPECIALI", "ACC.CARNEVALE E HALLOWEEN")
    assert res["other_types"] == {"UI-N": -8}
    assert by[2]["rem_q"] == -20 and by[2]["bucket"] == "out"
    assert 3 not in by                       # 没有商品筛选时，只有库存（没进没卖）的不列（不然全店几万个）
    assert a["bucket"] == "mid"
    days = {d: (sq, sv, pq, pv) for d, sq, sv, pq, pv in res["days"]}
    assert len(res["days"]) == 31 and days[date(2026, 10, 1)][2] == 152
    t = S.total(res["rows"])
    assert t["n"] == 3 and t["sq"] == 106 and abs(t["rem_v"] - sum(r["rem_v"] for r in res["rows"])) < 1e-9


def test_one_store_counts_transfers_in_and_values_them_at_ca_price(monkeypatch):
    res = _run(monkeypatch, stores=[1])
    a = next(r for r in res["rows"] if r["code"] == 1)
    assert a["pq"] == 40 and abs(a["pv"] - 40 * 0.77) < 1e-9       # 调入按 CA 平均进价
    assert a["sq"] == 30 and a["stock"] == 10
    assert all(r["code"] != 3 for r in res["rows"])                 # 门店 1 没有这个商品


def test_filters_supplier_and_categories(monkeypatch):
    res = _run(monkeypatch, sups=[5])
    assert [r["code"] for r in res["rows"]] == [4]
    res = _run(monkeypatch, mids=[(6, "FESTE SPECIALI")])
    assert sorted(r["code"] for r in res["rows"]) == [1, 2, 3]      # 有筛选：只有库存的也列
    r3 = next(r for r in res["rows"] if r["code"] == 3)
    assert r3["bucket"] == "stock" and r3["stock"] == 5
    res = _run(monkeypatch, subs=[(6, 1)])
    assert sorted(r["code"] for r in res["rows"]) == [1, 2]
    res = _run(monkeypatch, cats=[2])
    assert [r["code"] for r in res["rows"]] == [4]
    res = _run(monkeypatch, codes=[2])
    assert [r["code"] for r in res["rows"]] == [2]


def test_tree_lists_and_group_totals(monkeypatch):
    res = _run(monkeypatch)
    tree = S.build_tree([(6, None, "NATALE"), (6, 1, "FESTE SPECIALI - HALLOWEEN"), (6, 2, "FESTE SPECIALI - ADDOBBI"),
                         (6, 3, "LUCI"), (2, None, "CUCINA"), (2, 1, "CAFF - BOLL")])
    assert S.mids_of(tree) == [(2, "CAFF"), (6, "FESTE SPECIALI"), (6, "LUCI")]
    assert S.mids_of(tree, cats=[6]) == [(6, "FESTE SPECIALI"), (6, "LUCI")]
    assert S.subs_of(tree, mids=[(6, "FESTE SPECIALI")]) == [(6, 2), (6, 1)]
    g = S.group(res["rows"], "mid")
    fs = next(x for x in g if x["name"] == "FESTE SPECIALI")
    assert fs["n"] == 2 and fs["sq"] == 105
    st = {x["key"]: x for x in S.group(res["rows"], "store")}
    assert st[0]["pq"] == 60 and st[1]["sq"] == 101 and st[1]["pq"] == 92          # 仓库 100 − 调出 40
    assert abs(sum(x["rem_v"] for x in st.values()) - S.total(res["rows"])["rem_v"]) < 1e-6
    sup = S.group(res["rows"], "sup")
    assert sup[0]["name"].startswith("DUE ESSE") and sup[0]["n"] == 2
    counts = {k: n for k, _nm, _t, n, _v in S.bucket_counts(res["rows"])}
    assert counts["out"] == 1 and counts["mid"] == 2


def test_export_layout_like_template(monkeypatch):
    res = _run(monkeypatch)
    path = os.path.join(tempfile.mkdtemp(), "s.xlsx")
    S.export(res, res["rows"], path, url="https://x/{ean}.webp")
    ws = load_workbook(path).active
    assert ws.title == "TUTTI PDV"
    assert ws["G1"].value == "采购" and ws["J1"].value == "销售量" and ws["S1"].value == "剩余库存"
    assert [ws.cell(2, c).value for c in (6, 7, 13, 14, 15, 16, 19, 20)] == \
           ["现有库存", "采购量", "Qt", "Valore", "差额率", "销售-采购", "剩余量", "剩余额"]
    assert "IMAGE" in ws["B4"].value and "8053482147512" in ws["B4"].value or "8053482350004" in ws["B4"].value
    last = 4 + len(res["rows"])
    assert ws.cell(last, 7).value == f"=SUM(G4:G{last - 1})" and ws.cell(last, 5).value == "TOTALE"
    assert ws.freeze_panes == "F4"


def test_preset_ranges():
    t = date(2026, 11, 16)
    assert S.preset_range("month", t) == (date(2026, 11, 1), t)
    assert S.preset_range("last_month", t) == (date(2026, 10, 1), date(2026, 10, 31))
    assert S.preset_range("d30", t)[0] == date(2026, 10, 18)
    assert S.preset_range("custom", t) is None
