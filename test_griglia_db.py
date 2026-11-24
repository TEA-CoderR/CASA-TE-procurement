"""程序里的 Griglia（griglia_db，2026-11-24 第 1 步）：导入 · 状态 · 价格自动更新 · 导出和原表一样 · 和 Excel 对比"""
import os

import openpyxl
from openpyxl.styles import Font

import griglia as G
import griglia_db as D

HEAD = ["NO.", "Immagine", "Cod.Art.", "Codice EAN", "Descrizione Prodotto", "PZ*COLLI", "Colli", "Qtà Ordine",
        "PREZZO NETTO", "Marca", "Fornitore", "CAT 3", "CAT 2", "CAT.1", "STATO PRODOTTO", "PVP CASA & TE", "IVA%",
        "PVI", "Margine", "Mrg%", "MELONI", "MEGA CASH", "F.LLI PETRILLO", "NEW GRIECO", "PA MIGLIORE",
        "FOIANO", "FORNITORI DIRETTO", "CAT.1", "CAT.2", "CAT.3"]
C = {h: i for i, h in enumerate(HEAD)}          # 第一个 CAT.1 是左边那套；最后三列是右边那套
GROUP = {0: "DETTAGLIO  ORDINE ", 9: "CLASSIFICAZIONE", 15: "PVP", 20: "PA INGROSSISTI", 25: "ATTIVO NEGOZIO"}


def _row(n, no, code, ean, name, pack, marca, forn, cats_l, stato, pvp, prices, best, direct, cats_r):
    from openpyxl.utils import get_column_letter as L
    r = [None] * len(HEAD)
    r[C["NO."]], r[C["Cod.Art."]], r[C["Codice EAN"]], r[C["Descrizione Prodotto"]] = no, code, ean, name
    r[C["Immagine"]] = f'=IFERROR(_xlfn.IMAGE("https://x/{ean}.webp"),"")'
    r[C["PZ*COLLI"]], r[C["Marca"]], r[C["Fornitore"]] = pack, marca, forn
    r[C["CAT 3"]], r[C["CAT 2"]], r[11 + 2] = cats_l[2], cats_l[1], cats_l[0]
    r[C["STATO PRODOTTO"]], r[C["PVP CASA & TE"]], r[C["IVA%"]] = stato, pvp, 22
    hi = lambda h: L(C[h] + 1)
    r[C["Qtà Ordine"]] = f"={hi('Colli')}{n}*{hi('PZ*COLLI')}{n}"
    r[C["PREZZO NETTO"]] = f"={hi('PA MIGLIORE')}{n}"
    r[C["PVI"]] = f"={hi('PVP CASA & TE')}{n}/1.22"
    r[C["Margine"]] = f"={hi('PVI')}{n}-{hi('PREZZO NETTO')}{n}"
    r[C["Mrg%"]] = f"={hi('Margine')}{n}/{hi('PVI')}{n}"
    for h, v in prices.items():
        r[C[h]] = v
    r[C["PA MIGLIORE"]], r[C["FOIANO"]], r[C["FORNITORI DIRETTO"]] = best, 1, direct
    r[27], r[28], r[29] = cats_r
    return r


def make_excel(path, extra=None, rename=None):
    wb = openpyxl.Workbook()
    wb.active.title = "SCHEDA ANAGRAFICA FORNITORI"
    wb.active["A1"] = "Ragione sociale"
    wb.active["B1"] = "P&G"
    ws = wb.create_sheet("GRIGLIA ")
    ws["E1"], ws["H1"] = "Fatturazione :", "01032026-SF-01"
    for c, g in GROUP.items():
        ws.cell(4, c + 1, g)
    for c, h in enumerate(HEAD, 1):
        ws.cell(5, c, h)
    rows = [
        (1, 165632, "8410436235549", "GLISS SHAMPOO 250 ML", 6, "GLISS", "MELONI WALTER SPA",
         ("CURA PERSONE", "CAPELLI", "SHAMPOO"), None, 2.49, {"MELONI": 1.38, "MEGA CASH": 1.39}, 1.38,
         "HENKEL ITALIA SRL", ("CURA PERSONE", "CAPELLI", "SHAMPOO")),
        (2, 200001, "8001090000017", "DASH 40 MIS", 4, "DASH", "F.LLI PETRILLO SRL",
         ("DETERSIVI", "BUCATO", ""), "NUOVO", 9.99, {"F.LLI PETRILLO": 6.5}, 6.5,
         "P&G", ("DETERSIVI E CURACASA", "BUCATO", "LIQUIDI")),       # 两套分类大类不一样
        (3, None, "8001090000024", "SVELTO 1 L", 12, "SVELTO", "MEGA CASH & CARRY SRL",
         ("DETERSIVI", "PIATTI", ""), None, 1.0, {"MEGA CASH": 1.2}, 1.2,          # 没有 Cod.Art.；卖价低于进价
         "UNILEVER", ("", "", "")),
        (4, 200003, "8001090000031", "OLD SOAP 100 G", 24, "FELCE", "MELONI WALTER SPA",
         ("CURA PERSONE", "CORPO", ""), None, 0.99, {"MELONI": 0.3}, 0.3, "FELCE", ("CURA PERSONE", "CORPO", "")),
    ]
    for k, r in enumerate(rows + list(extra or [])):
        n = 6 + k
        vals = _row(n, *r)
        if rename and r[2] in rename:
            vals[C["Descrizione Prodotto"]] = rename[r[2]]
        for c, v in enumerate(vals, 1):
            ws.cell(n, c, v)
    for c in (C["Codice EAN"] + 1, C["Descrizione Prodotto"] + 1):          # 最后一个划了删除线 = 停用
        ws.cell(9, c).font = Font(strike=True)
    ws.cell(7, C["PVI"] + 1, "=P7/1.1")                                        # 这一行的公式和别的行不一样
    wb.save(path)
    return path


def _import(tmp_path, name="g.xlsx", **kw):
    src = make_excel(str(tmp_path / name), **kw)
    root = str(tmp_path / "root")
    snap = D.read_excel(src)
    ev = D.save_import(root, snap, src, pc="PC1", usr="anna")
    return root, src, snap, ev


def test_import_keeps_every_column_and_formula_template(tmp_path):
    root, src, snap, ev = _import(tmp_path)
    cols = snap["columns"]
    assert [c["name"] for c in cols] == HEAD and cols[1]["img"]
    assert cols[C["PVI"]]["tpl"] == "=P6/1.22" and cols[C["Mrg%"]]["tpl"] == "=S6/R6"
    assert cols[0]["group"] == "DETTAGLIO ORDINE" and cols[C["MELONI"]]["group"] == "PA INGROSSISTI"
    r7 = next(r for r in snap["rows"] if r["n"] == 7)
    assert r7["f"][str(C["PVI"])] == "=P6/1.1"              # 换到第一行的样子存
    assert "SCHEDA ANAGRAFICA FORNITORI" in snap["others"]
    assert ev["n"] == 4 and os.path.isfile(os.path.join(D.store_dir(root), "base", ev["template"]))


def test_status_and_import_issues(tmp_path):
    root, *_ = _import(tmp_path)
    cat = D.load(root, replaced={"8001090000031": "8001090000999"})
    by = cat.by_ean
    assert by["8410436235549"]["status"] == "active"
    assert by["8001090000017"]["status"] == "new"                   # STATO PRODOTTO = NUOVO
    assert by["8001090000031"]["status"] == "replaced" and by["8001090000031"]["new_ean"] == "8001090000999"
    assert "cats" in by["8001090000017"]["issues"]
    assert by["8001090000017"]["cats"] == ["DETERSIVI E CURACASA", "BUCATO", "LIQUIDI"]   # 右边那套
    assert {"nocode", "below_cost"} <= set(by["8001090000024"]["issues"])
    assert cat.counts() == {"active": 2, "new": 1, "paused": 0, "off": 0, "replaced": 1}
    assert D.load(root, replaced={}).by_ean["8001090000031"]["status"] == "off"     # 划了线
    assert cat.tree()["CURA PERSONE"]["CAPELLI"]["SHAMPOO"] == 1
    assert dict(cat.suppliers("direct"))["HENKEL ITALIA SRL"] == 1


def test_prices_follow_this_weeks_quotes(tmp_path):
    root, *_ = _import(tmp_path)
    cat = D.load(root, replaced={})
    quotes = {"MELONI": {"8410436235549": {"price": 1.45}},
              "MEGA CASH": {"8410436235549": {"price": 1.41}, "8001090000024": {"price": 0.01}},    # 0.01 写错了
              "PETRILLO": {"8001090000017": {"price": 6.2}}}
    auto = D.compute_auto(cat, quotes, {"MELONI": "Listino.xlsx"}, pvp={"8410436235549": 2.59})
    a = auto["items"]["8410436235549"]
    assert a["p"] == {"MELONI": 1.45, "MEGA CASH": 1.41} and a["best"] == 1.41
    assert a["sup"] == "MEGA CASH & CARRY SRL"            # Griglia 里这家的写法
    assert auto["items"]["8001090000017"]["sup"] == "F.LLI PETRILLO SRL"
    assert auto["items"]["8001090000024"]["p"] == {}       # 可疑价不算
    D.save_auto(root, auto)
    hist = os.listdir(os.path.join(D.store_dir(root), "history"))
    assert len(hist) == 1 and hist[0].startswith("prezzi_")
    cat = D.load(root, replaced={})
    x = cat.by_ean["8410436235549"]
    assert x["best"] == 1.41 and x["pvp"] == 2.59 and round(x["mrg"], 3) == round((2.59 / 1.22 - 1.41) / (2.59 / 1.22), 3)
    assert cat.by_ean["8001090000024"]["status"] == "paused"          # 这周没人有货
    assert cat.value(x, C["MEGA CASH"]) == 1.41 and cat.value(x, C["F.LLI PETRILLO"]) is None


def test_export_looks_like_the_original(tmp_path):
    root, src, *_ = _import(tmp_path)
    cat = D.load(root, replaced={})
    D.save_auto(root, D.compute_auto(cat, {"MELONI": {"8410436235549": {"price": 1.5}}}))
    cat = D.load(root, replaced={})
    out = str(tmp_path / "out.xlsx")
    assert D.export(root, cat, out) == 4
    ws = openpyxl.load_workbook(out)["GRIGLIA "]
    assert ws["H1"].value == "01032026-SF-01" and ws.cell(5, 4).value == "Codice EAN"
    assert ws.cell(6, C["PVI"] + 1).value == "=P6/1.22" and ws.cell(7, C["PVI"] + 1).value == "=P7/1.1"
    assert ws.cell(8, C["Mrg%"] + 1).value == "=S8/R8"
    assert ws.cell(6, C["MELONI"] + 1).value == 1.5 and ws.cell(6, C["MEGA CASH"] + 1).value is None
    assert "8410436235549" in ws.cell(6, 2).value                       # 图片 = IMAGE 公式
    assert ws.cell(9, C["Codice EAN"] + 1).font.strike                  # 停用的还是划线
    assert ws.max_row == 9
    assert "SCHEDA ANAGRAFICA FORNITORI" in openpyxl.load_workbook(out).sheetnames
    assert D.compare(cat, G.load(out)) == []                            # 读回来和程序里一样


def test_compare_finds_what_was_changed_in_excel(tmp_path):
    root, *_ = _import(tmp_path)
    cat = D.load(root, replaced={})
    new = make_excel(str(tmp_path / "new.xlsx"), rename={"8410436235549": "GLISS SHAMPOO 300 ML"},
                     extra=[(5, 200009, "8001090000048", "NEW ITEM", 6, "X", "MELONI WALTER SPA",
                             ("CURA PERSONE", "", ""), None, 1, {}, None, "", ("", "", ""))])
    diff = {d["ean"]: d for d in D.compare(cat, G.load(new))}
    assert diff["8410436235549"]["kind"] == "changed"
    assert diff["8410436235549"]["diff"][0][1:] == ("GLISS SHAMPOO 250 ML", "GLISS SHAMPOO 300 ML")
    assert diff["8001090000048"]["kind"] == "only_excel"
    assert len(diff) == 2


def test_latest_import_from_any_pc_wins(tmp_path):
    root, *_ = _import(tmp_path)
    store = D.store_dir(root)
    src = make_excel(str(tmp_path / "g2.xlsx"), extra=[(5, 200009, "8001090000048", "NEW ITEM", 6, "X", "",
                                                       ("A", "", ""), None, 1, {}, None, "", ("", "", ""))])
    from datetime import datetime, timedelta
    D.save_import(root, D.read_excel(src), src, pc="PC2", usr="marco", now=datetime.now() + timedelta(seconds=5))
    D.add_event(store, {"op": "import", "base": "base_x.json.gz", "t": "2099-01-01T00:00:00"}, pc="PC3")  # 文件不在：不算
    ev = D.current_import(store)
    assert ev["pc"] == "PC2" and len(D.load(root, replaced={}).items) == 5
    with open(D.log_path(store, "PC1", "anna"), "a", encoding="utf-8") as f:
        f.write('{"op": "imp')                                              # 同步盘传了一半
    assert D.current_import(store)["pc"] == "PC2"


def test_category_sets_differ_only_on_real_difference():
    assert not D.cats_differ(["CAPELLI ", "", ""], ["capelli", "SHAMPOO", ""])
    assert D.cats_differ(["DETERSIVI", "BUCATO", ""], ["DETERSIVI", "SUPERFICI", ""])


# ------------------------------------------------------------------ 第 2 步：编辑 / 改动记录 / 撤销 / 合理性检查
def _edit(root, cat, ean, f, new, pc="PC1", usr="anna", t=None):
    x = cat.by_ean[ean]
    ev = D.make_edit(cat, ean, [{"f": f, "old": D.current(cat, x, f), "new": new}])
    if t:
        ev["t"] = t
    return D.save(root, [ev], pc=pc, usr=usr)[0]


def test_edits_apply_and_undo(tmp_path):
    root, *_ = _import(tmp_path)
    cat = D.load(root, replaced={})
    e = "8410436235549"
    _edit(root, cat, e, f"c{C['PZ*COLLI']}", 12)
    ev = _edit(root, cat, e, "cats", ["CURA PERSONE", "CAPELLI", ""])
    _edit(root, cat, "8001090000017", "status", "off")
    cat = D.load(root, replaced={})
    x = cat.by_ean[e]
    assert x["pack"] == 12 and x["cats"] == ["CURA PERSONE", "CAPELLI", ""]
    assert cat.by_ean["8001090000017"]["status"] == "off"
    D.undo(root, ev["id"], pc="PC1", usr="anna")
    cat = D.load(root, replaced={})
    assert cat.by_ean[e]["cats"] == ["CURA PERSONE", "CAPELLI", "SHAMPOO"] and cat.by_ean[e]["pack"] == 12
    h = D.history(cat)
    assert len(h) == 3 and [r for r in h if r["f"] == "cats"][0]["undone"]
    # 导出跟着改动：停用的划线、箱规写新的
    out = str(tmp_path / "o.xlsx")
    D.export(root, cat, out)
    g = G.load(out)
    assert g["items"][e]["pack"] == 12 and "8001090000017" in g["struck"]


def test_add_item_and_category_table(tmp_path):
    root, *_ = _import(tmp_path)
    cat = D.load(root, replaced={})
    assert ("CURA PERSONE", "CAPELLI", "SHAMPOO") in cat.category_paths()
    D.save(root, [{"op": "cat_add", "id": D.new_id(), "base": cat.imp["base"], "path": ["CURA PERSONE", "CAPELLI", "BALSAMO"]}])
    cat = D.load(root, replaced={})
    assert ("CURA PERSONE", "CAPELLI", "BALSAMO") in cat.category_paths()
    ean = "8001090000057"
    assert D.valid_ean(ean)
    ch = [{"f": "cats", "old": None, "new": ["CURA PERSONE", "CAPELLI", "BALSAMO"]}]
    assert D.check(cat, None, ch, new_ean=ean) == []
    assert any(l == "red" for l, _ in D.check(cat, None, ch, new_ean="8001090000055"))          # 校验位错
    assert any(l == "red" for l, _ in D.check(cat, None, ch, new_ean="8410436235549"))          # 已经有了
    D.save(root, [D.make_add(cat, ean, {C["Descrizione Prodotto"]: "GLISS BALSAMO", C["PZ*COLLI"]: 6},
                             ["CURA PERSONE", "CAPELLI", "BALSAMO"])])
    cat = D.load(root, replaced={})
    x = cat.by_ean[ean]
    assert x["status"] == "new" and x["added"] and x["name"] == "GLISS BALSAMO" and x["cats"][2] == "BALSAMO"
    # 改名 = 整个类跟着改；改成已有的名字 = 合并
    D.save(root, [{"op": "cat_rename", "id": D.new_id(), "base": cat.imp["base"], "path": ["CURA PERSONE", "CAPELLI"], "to": "HAIR"}])
    cat = D.load(root, replaced={})
    assert cat.by_ean[ean]["cats"][:2] == ["CURA PERSONE", "HAIR"] and cat.by_ean["8410436235549"]["cats"][1] == "HAIR"
    out = str(tmp_path / "o.xlsx")
    assert D.export(root, cat, out) == 5
    assert G.load(out)["items"][ean]["name"] == "GLISS BALSAMO"


def test_checks_red_and_yellow(tmp_path):
    root, *_ = _import(tmp_path)
    cat = D.load(root, replaced={})
    x = cat.by_ean["8410436235549"]
    pc = f"c{C['PZ*COLLI']}"
    lv = lambda ch: [l for l, _ in D.check(cat, x, ch)]
    assert lv([{"f": pc, "old": 6, "new": 0}]) == ["red"]
    assert lv([{"f": pc, "old": 6, "new": 12}]) == ["yellow"]           # 一次改 100%
    assert lv([{"f": pc, "old": 6, "new": 7}]) == []
    assert lv([{"f": "cats", "old": None, "new": ["NUOVA CATEGORIA", "", ""]}]) == ["red"]
    assert lv([{"f": "cats", "old": None, "new": ["", "", ""]}]) == ["red"]
    assert lv([{"f": "status", "old": "active", "new": "replaced:123"}])[0] == "red"
    assert lv([{"f": f"c{C['Descrizione Prodotto']}", "old": "A", "new": " "}]) == ["red"]
    # 批发商箱规差很多倍
    D.save_auto(root, D.compute_auto(cat, {"MELONI": {"8410436235549": {"price": 1.4, "pack": 6}}}))
    cat = D.load(root, replaced={})
    assert "yellow" in [l for l, _ in D.check(cat, cat.by_ean["8410436235549"], [{"f": pc, "old": 6, "new": 24}])]
    # 没改的不记
    assert D.make_edit(cat, "8410436235549", [{"f": pc, "old": 6, "new": "6"}]) is None


def test_reimport_drops_old_edits_and_conflicts_reported(tmp_path):
    root, src, *_ = _import(tmp_path)
    cat = D.load(root, replaced={})
    e = "8410436235549"
    f = f"c{C['Marca']}"
    _edit(root, cat, e, f, "GLISS KUR", pc="PC1", usr="anna", t="2099-01-01T10:00:00")
    _edit(root, cat, e, f, "SCHWARZKOPF", pc="PC2", usr="marco", t="2099-01-01T10:05:00")
    cat = D.load(root, replaced={})
    assert cat.by_ean[e]["brand"] == "SCHWARZKOPF"                    # 后改的算数
    c = D.conflicts(cat, pc="PC1", usr="anna")
    assert len(c) == 1 and c[0]["user"] == "marco" and c[0]["mine"] == "GLISS KUR" and c[0]["theirs"] == "SCHWARZKOPF"
    assert D.conflicts(cat, pc="PC2", usr="marco") == []
    from datetime import datetime, timedelta
    D.save_import(root, D.read_excel(src), src, pc="PC1", usr="anna", now=datetime(2099, 1, 2))
    assert D.load(root, replaced={}).by_ean[e]["brand"] == "GLISS"


def test_ean_check_digit():
    assert D.valid_ean("8410436235549") and D.valid_ean("96385074") and not D.valid_ean("8410436235548")
    assert not D.valid_ean("12345") and not D.valid_ean("84104362355a9")


def test_db_lookup_prefill_and_codes(monkeypatch):
    """新增商品从数据库预填（只读）、保存前核对 Cod.Art."""
    import sqlite3
    import pandas as pd
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE prodotti (CodArticolo INT, CodBarre TEXT, Descrizione TEXT, qtasmallbox INT,"
                " ArticoloForn TEXT, IDFornitore INT)")
    con.execute("CREATE TABLE codbarre (CodArticolo INT, CodBarre TEXT)")
    con.execute("CREATE TABLE fornitore (IDFornitore INT, Nome TEXT)")
    con.execute("INSERT INTO prodotti VALUES (123, '8001090000057', 'PROVA 1L', 6, 'AB-9', 7)")
    con.execute("INSERT INTO fornitore VALUES (7, 'HENKEL ITALIA SRL')")
    con.commit()
    orig = pd.read_sql
    monkeypatch.setattr(pd, "read_sql", lambda q, engine, params=None: orig(q.replace("%s", "?"), con, params=params))
    db = type("DB", (), {"engine": None})()
    p = D.db_lookup(db, "8001090000057")
    assert p == {"code": "123", "name": "PROVA 1L", "pack": 6, "cod_forn": "AB-9", "direct": "HENKEL ITALIA SRL"}
    assert D.db_lookup(db, "8001090000064") == {}
    assert D.db_codes(db, ["123", "999", "", "x"]) == {"123"}
