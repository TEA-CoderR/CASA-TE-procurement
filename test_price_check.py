import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import price_check as P


FILES = {
    "MEGA CASH": {"1": {"price": 1.00, "pack": 12, "desc": "A"}, "2": {"price": 2.00, "pack": 6, "desc": "B"},
                  "9": {"price": 3.00, "pack": 1, "desc": "X"}},
    "MELONI": {"1": {"price": 0.90, "pack": 6, "desc": "A"}, "3": {"price": 5.00, "pack": 1, "desc": "C"}},
}
DB = {"1": {"code": "10", "name": "A", "price": 1.02, "pack": 12, "supplier": "MEGA CASH & CARRY SRL"},
      "2": {"code": "20", "name": "B", "price": 2.50, "pack": 12, "supplier": "ALTRO"},
      "3": {"code": "30", "name": "C", "price": None, "pack": None, "supplier": "MELONI SPA"}}


def test_compare_counts():
    res = P.compare(FILES, DB)
    mega, meloni = res["files"]
    assert (mega["items"], mega["in_db"], mega["same_sup"]) == (3, 2, 1)
    assert (mega["price_close"], mega["price_far"], mega["pack_same"], mega["pack_diff"]) == (1, 1, 1, 1)
    assert (meloni["in_db"], meloni["same_sup"], meloni["no_db_price"]) == (2, 1, 1)
    assert meloni["price_far"] == 1                  # 条码 1：数据库 1.02 和 MELONI 0.90 差 13%
    t = res["total"]
    assert (t["eans"], t["in_db"], t["multi"], t["spread"]) == (4, 3, 1, 1)
    assert [r["quotes"] for r in res["rows"] if r["ean"] == "1"] == [2, 2]


def test_same_supplier():
    assert P.same_supplier("MEGA CASH", "MEGA CASH & CARRY SRL")
    assert not P.same_supplier("formattato_listino", "LISTINO SRL")
    assert not P.same_supplier("MEGA", "")
    assert P.same_supplier("Ordine FlliPetrillo Foiano 1810", "F.LLI PETRILLO & C. S.R.L.")
    assert not P.same_supplier("Ordine Meloni Foiano 1810", "MEGA CASH & CARRY SRL")


def test_conclusion_and_export():
    res = P.compare(FILES, DB)
    lines = P.conclusion(res)
    assert lines[0].startswith("2 份报价表，一共 4 个条码，数据库里找得到 3 个")
    assert "1 个商品有两家以上批发商报价" in lines[1]
    path = os.path.join(tempfile.mkdtemp(), "x.xlsx")
    P.export(res, path)
    from openpyxl import load_workbook
    wb = load_workbook(path)
    assert wb.sheetnames == ["汇总", "明细"] and wb["明细"].max_row == 6


def test_best_and_apply():
    best = P.best_quotes(FILES)
    assert {k: best["1"][k] for k in ("price", "pack", "supplier")} == {"price": 0.90, "pack": 6.0, "supplier": "MELONI"}
    assert best["2"]["supplier"] == "MEGA CASH"
    rows = [{"ean": "1", "pack": 12, "price": 1.02, "in_db": True, "file_pcs": 24},
            {"ean": "77", "pack": 4, "price": 2.0, "in_db": True},
            {"ean": "2", "pack": 0, "price": 0, "in_db": False}]
    assert P.apply_quotes(rows, best) == 2
    # 2026-11-19：门店清单的箱是数据库箱规 → 有数据库箱规就照用，报价的箱规记在 quote_pack
    assert rows[0]["pack"] == 12 and rows[0]["quote_pack"] == 6.0 and rows[0]["db_pack"] == 12
    assert rows[0]["quote_sup"] == "MELONI"
    assert rows[0]["file_pcs"] == 24                 # 清单件数不动
    assert rows[1]["pack"] == 4 and "quote_sup" not in rows[1]
    assert rows[2]["pack"] == best["2"]["pack"] and rows[2]["quote_sup"] == "MEGA CASH"   # 数据库没有的也去报价里找


def test_duplicate_ean_header_picks_filled_column():
    """报价表头有两列「CODICE EAN」、前一列是空的（Petrillo）：用有条码的那一列"""
    from openpyxl import Workbook
    import utils
    ws = Workbook().active
    ws.append(["Codice", "ORDINE", "DESCRIZIONE", "PZ", "PREZZO NETTO", "CODICE EAN", "FOTO", "CODICE EAN"])
    for k in range(5):
        ws.append([k, 0, "X", 12, 1.5, None, None, f"80000000000{k}  "])
    assert utils.find_col_best(ws, "CODICE EAN") == (8, 1)
    assert utils.find_col_best(ws, "PREZZO NETTO") == (5, 1)
    assert utils.find_col_best(ws, "NIENTE") == (None, None)


def test_db_pack_far_bigger_than_every_wholesaler_not_used():
    """2026-11-22 bug：VIT BOBINA DOPPIA 数据库每箱 56 件、几家批发商都是 1 件一卖 → 门店清单 60 订成 3360 件 €14,253。
    数据库箱规 ≥ 3 倍于每家批发商的箱规 → 用批发商的箱规，标 pack_far"""
    import price_check as PC
    files = {"A": {"801": {"price": 4.3, "pack": 1}, "802": {"price": 1.0, "pack": 12}, "803": {"price": 2.0, "pack": 6}},
             "B": {"801": {"price": 4.2, "pack": 1}, "803": {"price": 2.1, "pack": 12}}}
    rows = [{"ean": "801", "pack": 56, "price": 3.7}, {"ean": "802", "pack": 12, "price": 0.9},
            {"ean": "803", "pack": 24, "price": 1.9}]
    PC.apply_quotes(rows, PC.best_quotes(files))
    assert rows[0]["pack"] == 1 and rows[0]["pack_far"] == 56
    assert rows[1]["pack"] == 12 and "pack_far" not in rows[1]
    assert rows[2]["pack"] == 24 and "pack_far" not in rows[2]       # 有一家 12 件一箱：24 不算差很多
