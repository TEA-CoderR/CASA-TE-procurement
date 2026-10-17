import pandas as pd

import distribution_service as D


def _sheets():
    home = pd.DataFrame({"商品编号": [101, 102, 103, 104], "商品条码": ["8001", "8002", "8003", "8004"],
                         "名称": ["A", "B", "C", "D"], "数量": [10, 6, 3, 1]})
    cols = ["Cod.Art.", "Codice EAN", "PZ*COLLI"] + [f"x{i}" for i in range(3, 25)]
    rows = []
    for art, ean, pz, q in ((101, "8001", 6, [2, 2, 2, 2, 1]), (999, "8002", 12, [1, 1, 1, 1, 1]),
                            (555, "7777", 4, [0, 1, 0, 0, 0])):
        r = [art, ean, pz] + [None] * 22
        for k, ci in enumerate((10, 13, 16, 19, 22)):
            r[ci] = q[k]
        rows.append(r)
    s1 = pd.DataFrame(rows, columns=cols)
    return {"Foglio": home, "Sheet1": s1}


def test_dual_extract_matches_by_code_then_ean():
    res = D.dual_extract(_sheets())
    out = res["out"]
    assert (res["match_art"], res["match_ean"], res["miss"]) == (1, 1, 2)
    assert list(out.columns[-7:]) == ["PZ*COLLI", "FOIANO", "MARLIA", "S.FILIPPO", "S.MARCO", "S.CONCORDIO", "差值"]
    assert out.loc[0, "FOIANO"] == 2 and out.loc[0, "差值"] == 10 - 9
    assert out.loc[1, "PZ*COLLI"] == 12 and out.loc[1, "差值"] == 6 - 5        # 条码回退匹配
    assert out.loc[2, "FOIANO"] == "" and out.loc[2, "差值"] == 3
    assert list(res["unmatched"]["Cod.Art."]) == [555]
    assert res["status"] == ["art", "ean", "miss", "miss"]


def test_ean_map_excludes_internal_codes():
    df = pd.DataFrame({"CodArticolo": [1, 1, 1, 1, 2], "CodBarre": ["6612345", "8001.0", "5512", "8.002E3", "8001"],
                       "l": [1, 2, 3, 4, 1]})
    m = D.build_ean_map(df)
    assert m == {1: ["8001", "8002"], 2: ["8001"]}
    out = D.add_ean_columns(pd.DataFrame({"Cod.Art.": [1, 2, 3], "n": [0, 0, 0]}), "Cod.Art.", m)
    assert list(out.columns) == ["Cod.Art.", "Codice EAN", "Codice EAN 2", "Codice EAN 3", "n"]
    assert list(out["Codice EAN 2"]) == ["8002", "", ""]


def test_process_unified_and_export(tmp_path):
    res = D.process(_sheets(), "unified", engine_factory=None)
    assert "Codice EAN" in res["out"].columns and res["stats"]["miss"] == 2
    p = D.export_distribution(str(tmp_path / "x.xlsx"), res["out"], res["unmatched"])
    book = pd.read_excel(p, sheet_name=None)
    assert list(book) == ["Distribuzione", "Sheet1 未匹配"]


def test_restock_picks_first_supplier_with_enough_stock():
    df = D.combine_sales_inventory(
        [1, 2],
        pd.DataFrame({"CodArticolo": [1, 1, 2], "Negozio": [4, 3, 4], "SalesQty": [5, 2, 3]}),
        pd.DataFrame({"CodArticolo": [1, 2], "Neg1_Inv": [0, 0], "Neg2_Inv": [9, 1], "Neg3_Inv": [6, 2],
                      "Neg4_Inv": [0, 0], "Neg5_Inv": [0, 0], "Total_Inv": [15, 3]}),
        pd.DataFrame({"CodArticolo": [1, 2], "Descrizione": ["A", "B"]}))
    rows = D.restock(df, 4, [3, 2, 1])
    assert rows[0]["supplier"] == 2 and rows[0]["ok"]           # 3 号店 6 不够 5+2，2 号店 9 > 5
    assert not rows[1]["ok"] and rows[1]["supplier"] == 3


def test_detect_codes():
    codes, col = D.detect_codes(pd.DataFrame({"Codice": ["00123", 456, None, 456], "x": [1, 2, 3, 4]}))
    assert codes == [123, 456] and col == "Codice"
