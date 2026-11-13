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
    assert load_workbook(out).sheetnames == ["停售", "新品", "价格", "价格可疑"]


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
    rows[1]["brand"] = "DOPLA"
    rows.append({"store": 1, "code": "6", "ean": "8000000000066", "name": "Z", "in_db": True, "pack": 6, "in_grid": False,
                 "in_quote": True, "lbox": 1, "fbox": 1})
    d = tempfile.mkdtemp()
    p = OC.write_problem_report(rows, os.path.join(d, "a.xlsx"), store=1, sources=["MELONI", "MEGA CASH"])   # 发给 FOIANO
    wb = load_workbook(p)
    assert wb.sheetnames == ["FOIANO"]
    ws = wb["FOIANO"]
    vals = [[c.value for c in r] for r in ws.iter_rows()]
    flat = [v for r in vals for v in r if v is not None]
    eans = [r[1] for r in vals if r[1] and str(r[1]).startswith("800")]
    # ① Griglia 没货的在最上面；② 批发商有的在前（品牌供应商有的更前），批发商都没有的最后
    assert eans == ["8000000000035", "8000000000028", "8000000000066", "8000000000011"], eans
    assert '=IFERROR(_xlfn.IMAGE("https://img.casaete.top/prodotti/8000000000011/01.webp","",3,48,48),"")' in flat   # 照片 48 像素
    assert "EAN alternativo" in flat and "Disp. grossisti" in flat and "Disp. fornitore diretto" in flat and "Consiglio" in flat
    assert "Fornitore" in flat and "Ordinabile" not in flat
    row_a = next(r for r in vals if r[1] == "8000000000011")                     # 订不到（No）：清单 2 箱、确定 2 箱
    assert row_a[7] == "No" and row_a[6] == 0 and row_a[8] == "No" and row_a[9].startswith("Non disponibile")
    row_b = next(r for r in vals if r[1] == "8000000000028")
    assert row_b[7] == "Sì" and row_b[8] == "Sì" and row_b[9] == "Ordinare direttamente da DOPLA"
    assert next(r for r in vals if r[1] == "8000000000066")[9].startswith("Sconsigliato")
    assert any("MELONI · MEGA CASH" in str(v) for v in flat)                      # 写明是哪几家批发商订不到
    assert not any("箱规" in str(v) for v in flat)                               # 不放无关的（没有箱规）
    titles = [str(v) for v in flat if str(v).startswith("Articoli ") and "(" in str(v)]
    assert titles[0].startswith("Articoli Griglia non disponibili") and titles[1].startswith("Articoli non in Griglia")
    assert len(ws.tables) == 2 and all(t.ref.startswith("B") for t in ws.tables.values()) and ws.freeze_panes   # 两段都带筛选
    p2 = OC.write_problem_report(rows, os.path.join(d, "b.xlsx"))                 # 不指定门店：每家店一页
    assert load_workbook(p2).sheetnames == ["FOIANO", "MARLIA"]
    assert OC.write_problem_report([rows[4]], os.path.join(d, "c.xlsx")) is None
    assert OC.write_problem_report([rows[2]], os.path.join(d, "e.xlsx"))          # 只有 Griglia 没货的：也出报告
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
    assert OC._ORDER.index(OC.SITU) == OC._ORDER.index(0) + 1 and sorted(OC._ORDER) == list(range(32))
    assert OC._ORDER.index(OC.TRANSIT) == OC._ORDER.index(9) + 1          # 在途在现在够卖后面


def test_scheda_index_brand_supplier():
    import store_orders as SO
    from openpyxl import Workbook
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "周二"))
    wb = Workbook()
    ws = wb.active
    ws.title = "FOIANO"
    ws.append(["COD.ART.", "CODICE EAN", "DESCRIZIONE", "PZ*COLLI", "COLLI"])
    ws.append([123, 8000000000028, "B", 6, None])
    ws.append([456, None, "X", 6, None])
    f = os.path.join(root, "周二", "SCHEDA DOPLA 0110.xlsx")
    wb.save(f)
    cache = os.path.join(root, "c", "idx.json")
    idx = SO.scheda_index(root, cache)
    assert idx["files"] == 1 and os.path.exists(cache)
    assert SO.brand_supplier(idx, "08000000000028", "") == "DOPLA"          # 条码前面的 0 不影响
    assert SO.brand_supplier(idx, "", "456") == "DOPLA" and SO.brand_supplier(idx, "1", "9") is None
    assert SO.scheda_index(root, cache)["ean"] == idx["ean"]                # 第二次用缓存
    assert SO.brand_item(idx, "8000000000028", "") == ("DOPLA", 6.0, 0.0)  # 每箱件数 / 件价（报告右边统计用）
    assert SO.brand_item(SO.scheda_index(root, cache), "", "456")[0] == "DOPLA"


def test_report_shows_substitutes_note_and_direct_supplier_totals():
    """用户 11-11：① 换成替代商品的右边写替代商品、没找到的右边四格红底；② 标题、NOTA 列；右边直厂按供应商统计"""
    import check_rules as OC
    from openpyxl import load_workbook
    rows = [{"store": 2, "code": "1", "ean": "8000000000011", "name": "VECCHIO 40", "in_db": True, "pack": 6,
             "in_grid": True, "in_quote": True, "lbox": 2, "fbox": 3, "quote_sup": "MELONI", "sup_name": "MELONI WALTER",
             "repl": {"old_ean": "8000000000099", "old_name": "VECCHIO 40", "old_code": "9", "new_code": ""}},
            {"store": 2, "code": "2", "ean": "8000000000028", "name": "SPARITO", "in_db": True, "pack": 6,
             "in_grid": True, "in_quote": False, "lbox": 1, "fbox": 1},
            {"store": 2, "code": "3", "ean": "8000000000035", "name": "FADOR A", "in_db": True, "pack": 6,
             "in_grid": False, "in_quote": True, "lbox": 4, "fbox": 4, "brand": "NEW FADOR", "brand_pack": 6,
             "brand_price": 2.0, "brand_moq": {"amount": 100.0}},
            {"store": 2, "code": "4", "ean": "8000000000042", "name": "FADOR B", "in_db": True, "pack": 6,
             "in_grid": False, "in_quote": False, "lbox": 1, "fbox": 0, "brand": "NEW FADOR", "brand_pack": 12,
             "brand_price": 1.0, "brand_moq": {"amount": 100.0}},
            {"store": 2, "code": "5", "ean": "8000000000059", "name": "HENKEL A", "in_db": True, "pack": 6,
             "in_grid": False, "in_quote": True, "lbox": 2, "fbox": 2, "brand": "HENKEL", "brand_pack": 6,
             "brand_price": 1.0, "brand_moq": {"colli": 10.0}}]
    summ = OC.direct_summary(rows[2:])
    assert [(d["sup"], d["sku"], d["colli"], round(d["amount"], 2), d["ok"]) for d in summ] == \
        [("NEW FADOR", 2, 5, 60.0, False), ("HENKEL", 1, 2, 12.0, False)]
    assert summ[0]["short"] == "€ 40" and summ[1]["short"] == "8 colli" and summ[1]["moq_text"] == "10 colli"
    assert OC.direct_summary([dict(rows[2], brand_moq={"pallets": 1.0})])[0]["ok"] is None     # 按板、没填每板几箱
    # 排序：Sì → Da verificare → No（同一类金额大的在前）
    mix = [dict(rows[2], brand="NO BIG", brand_moq={"amount": 1000.0}),
           dict(rows[4], brand="SI SMALL", brand_moq={}),
           dict(rows[2], brand="CHECK", brand_moq={"pallets": 1.0}),
           dict(rows[2], brand="SI BIG", brand_moq={"amount": 10.0})]
    assert [d["sup"] for d in OC.direct_summary(mix)] == ["SI BIG", "SI SMALL", "CHECK", "NO BIG"]
    d = tempfile.mkdtemp()
    p = OC.write_problem_report(rows, os.path.join(d, "a.xlsx"), store=2)
    ws = load_workbook(p).active
    vals = [[c.value for c in r] for r in ws.iter_rows()]
    flat = [v for r in vals for v in r if v is not None]
    ra = next(r for r in vals if r[1] == "8000000000099")                  # 原商品的条码 / 名称，右边替代商品
    # 右边替代商品和左边一一对应：照片 · 条码 · 编号 · 名称 · 批发商 · 箱数
    assert ra[2] == "9" and ra[3] == "VECCHIO 40" and "8000000000011" in ra[6] and \
        ra[7:12] == ["8000000000011", None, "VECCHIO 40", "MELONI", 3]
    assert [c.value for c in ws[next(i for i, r in enumerate(vals, 1) if r[1] == "EAN")]][6:12] == \
        ["Foto alternativo", "EAN alternativo", "Cod. Art. alternativo", "Descrizione alternativo", "Grossista", "Colli ordinati"]
    rb_i = next(i for i, r in enumerate(vals, 1) if r[1] == "8000000000028")  # 没找到：右边六格红底
    assert vals[rb_i - 1][7] is None and vals[rb_i - 1][9] == "Nessuna alternativa trovata"
    assert all(ws.cell(rb_i, j).fill.fgColor.rgb.endswith("FEE2E2") for j in range(7, 13))   # 红 = 没有替代
    assert not ws.cell(rb_i, 6).fill.fgColor.rgb.endswith("FEE2E2")
    ra_i = vals.index(ra) + 1
    assert all(ws.cell(ra_i, j).fill.fgColor.rgb.endswith("EFF6FF") for j in range(7, 13))   # 蓝 = 替代商品
    assert vals.index(ra) < rb_i - 1                                         # 找到替代的排前面
    assert any(str(v).startswith("Articoli non in Griglia – si consiglia di non ordinarli, salvo urgenze") for v in flat)
    assert "NOTA" in flat and "Fornitore diretto" in flat and "Minimo raggiunto" in flat
    rf = next(r for r in vals if r[13] == "NEW FADOR")
    assert rf[14:18] == [2, 5, 60.0, "€ 100"] and rf[18] == "No – mancano € 40"
    assert next(r for r in vals if r[13] == "Totale")[14:17] == [3, 7, 72.0]


def test_overview_counts_carried_and_cheapest():
    import griglia as G
    st = {"labels": ["MELONI", "MEGA", "PETRILLO"], "gone": [{"ean": "9"}],
          "prices": [{"prices": {"MELONI": 1.0, "MEGA": 0.9}, "best_sup": "MEGA"},
                     {"prices": {"MELONI": 2.0}, "best_sup": "MELONI"},
                     {"prices": {"MELONI": 3.0, "MEGA": 2.5}, "best_sup": "MEGA"}]}
    o = G.overview(st, 4)
    assert o["covered"] == 3 and abs(o["share"] - 0.75) < 1e-9
    assert [(d["label"], d["carried"], d["cheapest"]) for d in o["sups"]] == \
        [("MEGA", 2, 2), ("MELONI", 3, 1), ("PETRILLO", 0, 0)]
    assert abs(o["sups"][1]["share"] - 0.75) < 1e-9
    assert G.overview({"labels": []}, 0)["share"] is None


def test_ean_and_cat3_columns_shown_once():
    import griglia_page as GP
    cfg = {"price_cols_hidden": [1, 3, 4, 7], "price_cols_hidden_ver": 2}
    assert GP._show_once(cfg, "price") == [1, 7] and cfg["price_cols_hidden"] == [3, 4]
    cfg["price_cols_hidden"] = [1, 3, 7]              # 用户之后自己又藏起来：照用户的
    assert GP._show_once(cfg, "price") == [] and cfg["price_cols_hidden"] == [1, 3, 7]
    old = {"gone_cols_hidden": [1, 7], "gone_ean_shown": True}   # 上一版已经放出过条码的：条码照用户的，只放出小类
    assert GP._show_once(old, "gone") == [7] and old["gone_cols_hidden"] == [1]
    assert 1 not in GP._HIDDEN and 7 not in GP._HIDDEN


def test_griglia_found_on_any_sheet(tmp_path):
    """2026-11-07 用户换成 SCHEDA_DETERSIVI：第一页是供应商资料，GRIGLIA 在第三页 → 以前提示找不到 Codice EAN"""
    from openpyxl import Workbook
    import griglia as G
    wb = Workbook()
    wb.active.title = "SCHEDA ANAGRAFICA FORNITORI"
    wb.active.append(["SCHEDA ANAGRAFICA FORNITORI"])
    wb.create_sheet("STORICO").append(["NEGOZIO", "VENDITE 2025"])
    ws = wb.create_sheet("GRIGLIA ")
    ws.append(["Fatturazione :"])
    ws.append(["NO.", "Cod.Art.", "Codice EAN", "Descrizione Prodotto", "PZ*COLLI", "PREZZO\n NETTO"])
    ws.append([1, "179132", 8004395125531, "ADORN SHAMPOO", 6, 1.8])
    p = tmp_path / "SCHEDA_DETERSIVI_061026.xlsx"
    wb.save(p)
    g = G.load(str(p))
    assert list(g["items"]) == ["8004395125531"] and g["items"]["8004395125531"]["price"] == 1.8


def test_export_chosen_tables_styled_with_photos(tmp_path):
    """2026-11-07 用户：可以只导出几张表；带 IMAGE 照片、颜色、表头筛选"""
    from openpyxl import load_workbook
    import griglia as G
    st = {"labels": ["MELONI", "MEGA CASH"], "history": True,
          "gone": [{"ean": "8000000000011", "code": "11", "name": "SAPONE", "cats": ["CASA", "", ""], "supplier": "MELONI",
                    "best": 1.2}],
          "new": [], "odd": [],
          "prices": [{"ean": "8000000000028", "code": "28", "name": "ACE", "cats": ["", "", ""], "supplier": "MELONI SPA",
                      "ref": 2.0, "best": 2.3, "best_sup": "MEGA CASH", "change": 0.15, "moved": True,
                      "prices": {"MELONI": 2.5, "MEGA CASH": 2.3}}]}
    out = str(tmp_path / "g.xlsx")
    G.export(st, out, which=["gone"], grid_name="SCHEDA.xlsx")
    wb = load_workbook(out)
    assert wb.sheetnames == ["停售"]
    ws = wb["停售"]
    assert "Griglia" in ws["A1"].value and ws["A1"].fill.fgColor.rgb.endswith("1F3A5F")
    assert ws["A1"].font.name == ws.cell(6, 4).font.name == "Microsoft YaHei"          # 清楚的字体（用户 11-07）
    hr = next(r for r in range(1, 8) if ws.cell(r, 2).value == "EAN")
    assert ws.cell(hr + 1, 1).value.startswith('=IFERROR(_xlfn.IMAGE("https://img.casaete.top/prodotti/8000000000011/')   # 没图不显示 #VALUE!
    assert ws.cell(hr + 1, 2).value == "8000000000011" and list(ws.tables)          # 表头自带筛选
    out2 = str(tmp_path / "p.xlsx")
    G.export(st, out2, which=["prices", "gone"], rows={"prices": st["prices"], "gone": []})
    wb = load_workbook(out2)
    assert wb.sheetnames == ["停售", "价格"]                                    # 顺序照表的顺序
    ws = wb["价格"]
    hr = next(r for r in range(1, 8) if ws.cell(r, 2).value == "EAN")
    heads = [c.value for c in ws[hr]]
    var = ws.cell(hr + 1, heads.index("Variazione") + 1)
    assert abs(var.value - 0.15) < 1e-9 and var.fill.fgColor.rgb.endswith("FDECEC")    # 涨 15% 红
    cheap = ws.cell(hr + 1, heads.index("MEGA CASH") + 1)
    assert cheap.fill.fgColor.rgb.endswith("E7F5EC")                                   # 最便宜绿
    assert ws.cell(hr + 1, heads.index("Più economico") + 1).fill.fgColor.rgb.endswith("FFF4E0")   # 换了供应商橙


def test_right_category_set_preferred():
    """2026-11-07 用户定：SCHEDA_DETERSIVI 有两套分类，用右边 CAT.1 / CAT.2 / CAT.3；右边空的用左边补（同一大类才补）"""
    import griglia as G
    head = ["Codice EAN", "Descrizione Prodotto", "CAT 3", "CAT 2", "CAT.1", "CAT.1", "CAT.2", "CAT.3"]
    rows = [head,
            ["8000000000011", "CASACETO MULTIUSO", None, "BUCATO", "DETERSIVI E CURACASA", "DETERSIVI E CURACASA", "DETERSIVI SUPERFICI", " "],
            ["8000000000028", "STREP", None, None, None, "CURA PERSONE", "RASATURA E DEPILAZIONE", None],
            ["8000000000035", "SHAMPOO X", "SHAMPOO", "CAPELLI ", "CURA PERSONE", "CURA PERSONE", None, None],
            ["8000000000042", "PAMPERS", "PANNOLINI", "BAMBINI", "DETERSIVI E CURACASA", "CURA PERSONE", None, None],
            ["8000000000059", "VECCHIO", "X3", "X2", "X1", None, None, None]]
    it = G.parse_rows(rows)["items"]
    assert it["8000000000011"]["cats"] == ["DETERSIVI E CURACASA", "DETERSIVI SUPERFICI", ""]
    assert it["8000000000028"]["cats"] == ["CURA PERSONE", "RASATURA E DEPILAZIONE", ""]
    assert it["8000000000035"]["cats"] == ["CURA PERSONE", "CAPELLI", "SHAMPOO"]        # 右边空：同一大类用左边补
    assert it["8000000000042"]["cats"] == ["CURA PERSONE", "", ""]                    # 大类不一样：不混
    assert it["8000000000059"]["cats"] == ["X1", "X2", "X3"]                          # 右边整行空：用左边


def test_shifted_rows_ignored_and_reported():
    """2026-11-07：SCHEDA 最下面几行从 Marca 开始往左挪了 9 格 → 大类里是 IVA 22、供应商里是小数；这几行只用前面几列，并报出行号"""
    import griglia as G
    head = ["NO.", "Cod.Art.", "Codice EAN", "Descrizione Prodotto", "PREZZO\n NETTO", "Marca", "Fornitore", "CAT.1", "MELONI"]
    rows = [["x"], head,
            [1, "11", "8000000000011", "BUONO", 1.0, "ACE", "MELONI SPA", "CASA", 1.1],
            [2, "28", "8000000000028", "SPOSTATO", 0.6, None, 0.975, 22, 1.19],
            [3, "35", "8000000000035", "SPOSTATO 2", 0.7, None, 1.05, 22, None]]
    g = G.parse_rows(rows)
    bad = g["items"]["8000000000028"]
    assert bad["supplier"] == "" and bad["cats"] == ["", "", ""] and bad["prices"] == {} and bad["price"] == 0.6
    assert g["items"]["8000000000011"]["cats"][0] == "CASA" and g["bad_rows"] == [4, 5]
    assert G.bad_rows_text([2759, 2760, 2761, 2770]) == "2759–2761、2770"


def test_struck_rows_are_not_griglia_items(tmp_path=None):
    """用户 11-11：不做了的商品挪到最下面、用线划掉 → 不算 Griglia 商品，不再出现在「都不做了」"""
    import tempfile
    from openpyxl import Workbook
    from openpyxl.styles import Font
    d = tempfile.mkdtemp()
    p = os.path.join(d, "G.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "GRIGLIA"
    ws.append(["Codice EAN", "Descrizione prodotto"])
    ws.append(["8001111000014", "COCCOLINO 50 LAV"])
    ws.append(["8001111000021", "COCCOLINO 40 LAV"])
    for c in ws[3]:
        c.font = Font(strike=True)
    wb.save(p)
    g = G.load(p)
    assert set(g["items"]) == {"8001111000014"} and set(g["struck"]) == {"8001111000021"}
