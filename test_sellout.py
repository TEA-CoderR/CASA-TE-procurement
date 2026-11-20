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


def test_export_summary_store_comparison_and_one_sheet_per_store(monkeypatch):
    res = _run(monkeypatch)
    path = os.path.join(tempfile.mkdtemp(), "s.xlsx")
    S.export(res, res["rows"], path, url="https://x/{ean}.webp")
    wb = load_workbook(path)
    assert wb.sheetnames == ["NEGOZI", "TUTTI PDV", "FOIANO", "MARLIA", "BMS"]
    ws = wb["TUTTI PDV"]
    T, F = S.TOP, S.FIRST
    assert ws.cell(T - 1, 7).value == "采购" and ws.cell(T - 1, 10).value == "销售量" and ws.cell(T - 1, 19).value == "剩余库存"
    assert [ws.cell(T, c).value for c in (6, 7, 13, 14, 15, 16, 19, 20)] == \
           ["现有库存", "采购量", "Qt", "Valore", "差额率", "销售-采购", "剩余量", "剩余额"]
    assert "IMAGE" in ws.cell(F, 2).value
    last = F + len(res["rows"])
    assert ws.cell(last, 7).value == f"=SUM(G{F}:G{last - 1})" and ws.cell(last, 5).value == "TOTALE"
    assert ws.freeze_panes == f"F{F}" and "Vendite" in ws["A3"].value
    # 门店那一页只算这家店：FOIANO 商品 1 进 40（调入）卖 30
    fo = wb["FOIANO"]
    row = next(r for r in range(F, fo.max_row) if fo.cell(r, 3).value == 1)
    assert fo.cell(row, 7).value == 40 and fo.cell(row, 10).value == 30 and fo.cell(row, 6).value == 10
    # 门店对比：一家店一行，店名能点到那一页
    ng = wb["NEGOZI"]
    names = [ng.cell(r, 1).value for r in range(T + 1, T + 4)]
    assert names == ["FOIANO", "MARLIA", "BMS"] and "FOIANO" in ng.cell(T + 1, 1).hyperlink.location
    assert ng.cell(T + 4, 1).value == "TOTALE"


def test_export_one_store_has_one_sheet(monkeypatch):
    res = _run(monkeypatch, stores=[1])
    path = os.path.join(tempfile.mkdtemp(), "s.xlsx")
    S.export(res, res["rows"], path)
    assert load_workbook(path).sheetnames == ["FOIANO"]


def test_store_rows_use_store_numbers(monkeypatch):
    res = _run(monkeypatch)
    sr = {r["code"]: r for r in S.store_rows(res["rows"], 1)}
    assert sr[1]["pq"] == 40 and sr[1]["sq"] == 30 and sr[1]["stock"] == 10 and sr[1]["rem_q"] == 10
    assert 3 not in sr
    g = S.group(S.store_rows(res["rows"], 1), "sup")
    assert g[0]["n"] == 2


def test_preset_ranges():
    t = date(2026, 11, 16)
    assert S.preset_range("month", t) == (date(2026, 11, 1), t)
    assert S.preset_range("last_month", t) == (date(2026, 10, 1), date(2026, 10, 31))
    assert S.preset_range("d30", t)[0] == date(2026, 10, 18)
    assert S.preset_range("custom", t) is None


def test_switching_store_view_equals_querying_that_store(monkeypatch):
    """查一次全部，点门店切换（不再读数据库）= 直接按那几家门店查的结果"""
    full = _run(monkeypatch)
    for stores in ([1], [2], [1, 2], [0, 1]):
        v = S.view(full, stores)
        q = _run(monkeypatch, stores=stores)
        key = lambda rows: {r["code"]: (r["stock"], r["pq"], round(r["pv"], 6), r["sq"], round(r["sv"], 6),
                                        round(r["cost"], 6), r["bucket"]) for r in rows}
        assert key(v["rows"]) == key(q["rows"]), stores
        assert [(d, round(a, 6), round(b, 6), round(c, 6), round(e, 6)) for d, a, b, c, e in v["days"]] == \
               [(d, round(a, 6), round(b, 6), round(c, 6), round(e, 6)) for d, a, b, c, e in q["days"]], stores
        assert v["scope"] == q["scope"]
    assert S.view(full, []) is full and S.view(full, list(S.ALL_SCOPE)) is full


def test_erp_formulas_margin_at_sales_cost_and_no_purchase_rows_blank():
    """2026-11-19 和 ERP 进销存报表逐行对过（DUE ESSE、SAIS · FOIANO）：毛利 = 销售额 − 销售成本；
    采购量 ≤ 0 的剩余空着、不算进合计；差额有采购（负的也算）才写，合计 = 全部销售额 − 全部采购额"""
    a = S.metrics(dict(stock=3, pq=88, pv=751.87, sq=178, sv=1780.84, cost=1514.34))     # SAIS 194835 VENTILATORE
    assert abs(a["profit"] - 266.50) < 1e-6 and a["rem_q"] == -90 and abs(a["rem_v"] + 90 * 751.87 / 88) < 1e-6
    assert abs(a["diff"] - 1028.97) < 1e-6
    b = S.metrics(dict(stock=37, pq=0, pv=0.0, sq=90, sv=82.88, cost=45.0))              # 没进货
    assert abs(b["profit"] - 37.88) < 1e-9
    assert b["rem_q"] is None and b["rem_v"] is None and b["diff"] is None and b["diff_rate"] is None
    c = S.metrics(dict(stock=5, pq=-25, pv=-137.5, sq=68, sv=484.85, cost=390.5))        # SAIS 174331 只有调出
    assert c["p_avg"] is None and c["rem_q"] is None and abs(c["diff"] - 622.35) < 1e-6
    t = S.total([a, b, c])
    assert t["rem_q"] == -90 and abs(t["diff"] - (t["sv"] - t["pv"])) < 1e-9
    assert abs(t["profit"] - (266.50 + 37.88 + 94.35)) < 1e-6
    assert "MG" in S.PURCHASE_TYPES


def test_margin_is_sales_minus_recorded_cost_returns_subtract():
    """2026-11-19 诊断 56：S.FILIPPO PANTOFOLA 每笔 TotAcquisto 3.030 → 18 件 54.54，毛利 39.40 和 ERP 一样；
    TAZZONE 卖 2 退 2 再卖 2：成本 5.50 −2.75 −2.75 +2.75 +2.75 = 5.50（ERP 报表把退货成本当正的加，写 16.50，是 ERP 的错）"""
    a = S.metrics(dict(stock=0, pq=18, pv=54.45, sq=18, sv=93.94, cost=54.54))
    assert abs(a["profit"] - 39.40) < 1e-6
    b = S.metrics(dict(stock=0, pq=12, pv=33.0, sq=2, sv=10.31, cost=5.50))
    assert abs(b["profit"] - 4.81) < 1e-6


def test_transfer_rows_use_their_own_price(monkeypatch):
    """ERP 调货行（CI / UI）按那一行自己的进价算采购额，没有进价才按 CA 平均进价"""
    raw = {"start": date(2026, 10, 1), "end": date(2026, 10, 2), "scope": (1,), "tree": {"subs": {}, "cats": {}},
           "sales": {}, "stock": {}, "prods": {}, "sups": {}, "filters": {},
           "purch": [("2026-10-01", 9, 0, "CA", 10.0, 70.0, 10.0),          # 仓库 CA 10 件 @7
                     ("2026-10-01", 9, 1, "CI", 4.0, 34.0, 4.0),            # 调入 4 件，自己的进价 8.5
                     ("2026-10-02", 9, 1, "CI", 2.0, 0.0, 0.0)]}            # 没有进价 → CA 平均 7
    r = S.build(raw)["rows"][0]
    assert r["pq"] == 6 and abs(r["pv"] - (34.0 + 2 * 7.0)) < 1e-9
