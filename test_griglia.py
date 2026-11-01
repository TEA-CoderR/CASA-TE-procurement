import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import griglia as G


def _grid():
    rows = [["ELENCO DETERSIVI"], [],
            ["Cod.Art.", "Codice EAN", "Descrizione prodotto", "Marca", "Fornitore", "PA MIGLIORE",
             "MELONI", "F.LLI PETRILLO", "FOIANO", "MARLIA"],
            [101, "8000000000011", "Detersivo A", "X", "F.LLI PETRILLO", 2.0, 2.2, 2.0, 1, None],
            ["102", 8000000000028, "Ammorbidente B", "Y", "MELONI", 1.5, 1.5, None, None, 3],
            ["103", "8000000000035", "Sapone C", "Z", "MELONI", 1.0, None, None, None, None],
            [None, None, "riga vuota"]]
    return G.parse_rows(rows)


def test_parse_rows():
    g = _grid()
    assert set(g["items"]) == {"8000000000011", "8000000000028", "8000000000035"}
    a = g["items"]["8000000000011"]
    assert a["code"] == "101" and a["best"] == 2.0 and a["prices"] == {"MELONI": 2.2, "F.LLI PETRILLO": 2.0}
    assert a["stores"] == ["FOIANO"]
    assert g["by_code"]["102"] == "8000000000028"
    assert g["suppliers"] == ["MELONI", "F.LLI PETRILLO"]


def test_contains():
    g = _grid()
    assert G.contains(g, "8000000000011") is True
    assert G.contains(g, None, "103") is True
    assert G.contains(g, "9999999999999", "999") is False
    assert G.contains(None, "8000000000011") is None          # 没有 Griglia：不判断


def _q(price, dept=None, desc=""):
    d = {"price": price, "pack": 6, "desc": desc}
    if dept is not None:
        d["dept"] = dept
    return d


def test_stats():
    g = _grid()
    quotes = {
        "PETRILLO": {"8000000000011": _q(2.1), "8000000000028": _q(1.2), "7000000000001": _q(3.0, desc="Nuovo")},
        "MELONI": {"8000000000011": _q(1.9, "DETERSIVI"), "7000000000001": _q(2.8, "DETERSIVI", "Nuovo"),
                   "6000000000001": _q(5.0, "ALIMENTARI", "Pasta")},
    }
    st = G.stats(g, quotes)
    assert [x["ean"] for x in st["gone"]] == ["8000000000035"]
    new = {x["ean"]: x for x in st["new"]}
    assert new["7000000000001"]["n"] == 2 and new["7000000000001"]["best_sup"] == "MELONI"
    assert new["7000000000001"]["prices"] == {"PETRILLO": 3.0, "MELONI": 2.8}
    pr = {x["ean"]: x for x in st["prices"]}
    a = pr["8000000000011"]
    assert a["best"] == 1.9 and a["best_sup"] == "MELONI" and a["moved"]          # Griglia 记的是 PETRILLO
    assert abs(a["change"] - (1.9 / 2.0 - 1)) < 1e-9
    b = pr["8000000000028"]
    assert b["best_sup"] == "PETRILLO" and b["moved"]
    assert st["labels"] == ["PETRILLO", "MELONI"]


def test_relevant_depts():
    eans = {f"80000000{i:05d}" for i in range(20)}
    quotes = {"MELONI": {**{e: _q(1, "DETERSIVI") for e in eans},
                         **{f"70000000{i:05d}": _q(1, "DETERSIVI") for i in range(30)},
                         **{f"60000000{i:05d}": _q(1, "ALIMENTARI") for i in range(500)},
                         "50000000000001": _q(1, "CASA")},
              "PETRILLO": {e: _q(1) for e in eans}}
    rel = G.relevant_depts(eans, quotes)
    assert rel == {"MELONI": {"DETERSIVI"}}                     # 没有部门列的 PETRILLO 不在里面（全算）


def test_odd_prices_not_best():
    g = _grid()
    quotes = {"MELONI": {"8000000000011": _q(0.02)}, "PETRILLO": {"8000000000011": _q(2.1)},
              "MEGA": {"8000000000011": _q(2.3)}}
    st = G.stats(g, quotes)
    assert [(x["ean"], x["supplier"]) for x in st["odd"]] == [("8000000000011", "MELONI")]
    p = next(x for x in st["prices"] if x["ean"] == "8000000000011")
    assert p["best"] == 2.1 and p["best_sup"] == "PETRILLO"


def test_find_load_export(tmp_path=None):
    from openpyxl import Workbook, load_workbook
    root = tempfile.mkdtemp()
    assert G.find_file(root) is None and G.load_for(root) is None
    d = G.grid_dir(root)
    os.makedirs(d)
    wb = Workbook()
    ws = wb.active
    ws.append(["Cod.Art.", "Codice EAN", "Descrizione prodotto"])
    ws.append(["1", "8000000000011", "A"])
    p = os.path.join(d, "ELENCO.xlsx")
    wb.save(p)
    assert G.find_file(root) == p
    cache = tempfile.mkdtemp()
    g = G.load(p, cache)
    assert list(g["items"]) == ["8000000000011"] and g["name"] == "ELENCO.xlsx"
    assert G.load(p, cache)["items"] == g["items"]                # 第二次用缓存
    st = G.stats(g, {"X": {"9000000000001": _q(1.0)}})
    out = os.path.join(root, "stat.xlsx")
    G.export(st, out)
    assert load_workbook(out).sheetnames == ["都不做了", "新品", "价格"]


def test_workspace_has_grid_folder():
    import workspace as W
    root = tempfile.mkdtemp()
    W.ensure(root)
    assert os.path.isdir(os.path.join(root, G.GRID_DIR)) and W.GRID == G.GRID_DIR


def test_order_check_nogrid():
    import order_check as OC
    base = {"in_db": True, "pack": 6, "pz_colli": 6}
    assert OC._problem(dict(base, in_grid=False)) == "nogrid"
    assert OC._problem(dict(base, in_grid=True)) is None
    assert OC._problem(dict(base)) is None                         # 没有 Griglia：不判断
    # 数据库没有 / 没有箱规：一定是批发商都没有的，归进对应的「批发商都没有」页，备注写原因
    assert OC._problem(dict(base, in_grid=False, in_db=False)) == "nogrid_noq"
    assert OC._problem(dict(base, in_grid=True, pack=0)) == "noquote" and OC._issue_text(dict(base, pack=0)) == "没有箱规"
    assert OC._problem(dict(base, pack=0)) == "noquote"                                # 没放 Griglia
    assert OC._issue(dict(base, in_db=False, quote_sup="MEGA")) is None                # 报价里补上了箱规：能算


def test_track_fresh():
    q1 = {"A": {"1": {}, "2": {}}, "B": {"1": {}}}
    seen, fresh = G.track({}, q1, "2026-10-01")
    assert fresh == {"A": set(), "B": set()}                     # 第一次只是记下来
    q2 = {"A": {"1": {}, "2": {}, "3": {}}, "B": {"1": {}, "2": {}}, "C": {"9": {}}}
    seen, fresh = G.track(seen, q2, "2026-10-08")
    assert fresh["A"] == {"3"} and fresh["B"] == {"2"} and fresh["C"] == set()
    seen, fresh = G.track(seen, q2, "2026-11-20")                 # 过了 30 天不算新了
    assert fresh["A"] == set()


def test_new_flags():
    g = _grid()
    quotes = {"A": {"7000000000001": _q(1, desc="DASH POWER 20 LAV"), "7000000000002": _q(1, desc="Altro")},
              "B": {"7000000000002": _q(1, desc="Altro")}}
    st = G.stats(g, quotes, {"A": {"7000000000001", "7000000000002"}, "B": set()})
    new = {x["ean"]: x for x in st["new"]}
    assert new["7000000000001"]["fresh"]
    assert not new["7000000000002"]["fresh"]                                         # B 早就有
    assert st["history"] and not G.stats(g, quotes)["history"]


def test_order_check_noquote():
    import order_check as OC
    base = {"in_db": True, "pack": 6}
    assert OC._problem(dict(base, in_quote=False)) == "noquote"
    assert OC._problem(dict(base, in_quote=False, in_grid=False)) == "nogrid_noq"
    assert OC._problem(dict(base, in_quote=True, in_grid=False)) == "nogrid"
    assert OC._problem(dict(base, in_quote=False, in_grid=True)) == "noquote"
    assert OC._problem(dict(base, in_quote=True)) is None


def test_categories_and_direct():
    g = _grid()
    g["items"]["8000000000011"].update(cats=["CURA PERSONE", "CAPELLI", "SHAMPOO"], direct="")
    g["items"]["8000000000028"].update(cats=["CURA PERSONE", "CAPELLI", "SHAMPOO"], direct="HENKEL")
    q = lambda p, **k: dict(_q(p), **k)
    quotes = {"MELONI": {"8000000000011": q(2, grp="CAPELLI", sgrp="SHAMPOO"),
                         "8000000000028": q(1, grp="CAPELLI", sgrp="SHAMPOO"),
                         "7000000000001": q(3, grp="CAPELLI", sgrp="SHAMPOO", desc="Nuovo")},
              "MEGA": {"8000000000011": q(2.1, src="UNILEVER"), "7000000000001": q(3.1, src="L'OREAL")}}
    st = G.stats(g, quotes)
    new = {x["ean"]: x for x in st["new"]}
    assert new["7000000000001"]["cats"] == ["CURA PERSONE", "CAPELLI", "SHAMPOO"]
    assert new["7000000000001"]["direct"] == "L'OREAL"
    pr = {x["ean"]: x for x in st["prices"]}
    assert pr["8000000000011"]["direct"] == "UNILEVER" and pr["8000000000028"]["direct"] == "HENKEL"


def test_parse_direct_and_cats():
    g = G.parse_rows([["Codice EAN", "Descrizione prodotto", "CAT 3", "CAT 2", "CAT.1", "FORNITORI DIRETTO"],
                      ["8000000000011", "A", "SHAMPOO", "CAPELLI", "CURA PERSONE", "HENKEL ITALIA"]])
    it = g["items"]["8000000000011"]
    assert it["cats"] == ["CURA PERSONE", "CAPELLI", "SHAMPOO"] and it["direct"] == "HENKEL ITALIA"


def test_problem_report(tmp_path=None):
    import order_check as OC
    from openpyxl import load_workbook
    rows = [{"store": 1, "code": "1", "ean": "8000000000011", "name": "A", "in_db": True, "pack": 6, "in_grid": False,
             "in_quote": False, "lbox": 2, "fbox": 2},
            {"store": 1, "code": "2", "ean": "8000000000028", "name": "B", "in_db": True, "pack": 6, "in_grid": False,
             "in_quote": True, "lbox": 1, "fbox": 0},
            {"store": 1, "code": "3", "ean": "8000000000035", "name": "C", "in_db": True, "pack": 0, "in_grid": True,
             "lbox": 1, "fbox": 1},
            {"store": 2, "code": "4", "ean": "8000000000042", "name": "D", "in_db": True, "pack": 6, "in_grid": False,
             "in_quote": False, "lbox": 1, "fbox": 1},
            {"store": 1, "code": "5", "ean": "8000000000059", "name": "E", "in_db": True, "pack": 6, "in_grid": True,
             "in_quote": True, "lbox": 1, "fbox": 1}]
    d = tempfile.mkdtemp()
    p = OC.write_problem_report(rows, os.path.join(d, "a.xlsx"), store=1)          # 发给 FOIANO 的意大利文报告
    wb = load_workbook(p)
    assert wb.sheetnames == ["FOIANO"]
    ws = wb["FOIANO"]
    vals = [[c.value for c in r] for r in ws.iter_rows()]
    flat = [v for r in vals for v in r if v is not None]
    eans = [r[1] for r in vals if r[1] and str(r[1]).startswith("800")]
    assert eans == ["8000000000011", "8000000000028", "8000000000035"]           # ① 非 Griglia 两个 ② Griglia 没货一个
    assert '=_xlfn.IMAGE("https://img.casaete.top/prodotti/8000000000011/01.webp")' in flat   # 照片用 IMAGE 函数读网站
    assert "No" in flat and "Sì" in flat                                         # 批发商有没有
    assert not any("箱规" in str(v) for v in flat)                               # 不放无关的（没有箱规）
    assert any(str(v).startswith("① Articoli non in Griglia") for v in flat)
    p2 = OC.write_problem_report(rows, os.path.join(d, "b.xlsx"))                 # 不指定门店：每家店一页
    assert load_workbook(p2).sheetnames == ["FOIANO", "MARLIA"]
    assert OC.write_problem_report([rows[4]], os.path.join(d, "c.xlsx")) is None
    assert OC.report_name(2, __import__("datetime").datetime(2026, 11, 1)) == "Articoli_da_verificare_MARLIA_0111.xlsx"
    assert OC._tab_of("nogrid_noq") == OC._tab_of("nogrid") == "nogrid" and OC._prob_title("nogrid", 3) == "非 Griglia 商品（3）"


def test_situation_and_edited():
    import order_check as OC
    base = {"in_db": True, "pack": 6, "lbox": 2, "fbox": 2, "sbox": 1}
    assert OC._situation(dict(base, in_grid=True, in_quote=False)) == "Griglia 商品 · 批发商都没有"
    assert OC._situation(dict(base, in_grid=False, in_quote=False)) == "非 Griglia 商品 · 批发商都没有"
    assert OC._situation(dict(base, in_grid=False, in_quote=True)) == "非 Griglia 商品 · 批发商有"
    assert OC._situation(dict(base, in_grid=True, in_quote=True)) == ""
    assert OC._situation(dict(base, in_quote=False)) == "批发商都没有"              # 没放 Griglia
    assert OC._edited(base)                                                         # 和系统建议 1 箱比
    assert not OC._edited(dict(base, fbox=1))
    assert OC._edited(dict(base, sbox=None, fbox=0)) and not OC._edited(dict(base, sbox=None))   # 算不了的和清单比
    assert OC._ORDER.index(OC.SITU) == OC._ORDER.index(0) + 1 and sorted(OC._ORDER) == list(range(31))
