"""采购订单跟踪：共享文件夹里的修改记录、从 PIANO ACQUISTO MESILE 同步、状态 / 超期 / 预算"""
import os
import sys
import json
import shutil
import tempfile
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import order_tracking as OT


def test_names_dates_amounts():
    assert OT.sup_key("ZAMbetti") == OT.sup_key("ZAMBETTI") == "ZAMBETTI"
    assert OT.sup_key("S.N Commerciale") == OT.sup_key("S.N commerciale")
    assert OT.sup_key("SAIS SRL") == OT.sup_key("Sais") and OT.sup_key("F.LLI Petrillo") == OT.sup_key("FlliPetrillo")
    assert OT.sup_key("Mega Cash") == OT.sup_key("MEGACASH")
    assert OT.parse_date("10/06/2026") == (date(2026, 6, 10), None)
    assert OT.parse_date("07/05", 2026) == (date(2026, 5, 7), None)
    assert OT.parse_date(datetime(2026, 10, 1)) == (date(2026, 10, 1), None)
    assert OT.parse_date("DETER") == (None, "DETER") and OT.parse_date(46164)[0] == date(2026, 5, 22)
    assert OT.parse_amount("311.10 €") == (311.10, None) and OT.parse_amount("695.89") == (695.89, None)
    assert OT.parse_amount("1.234,56") == (1234.56, None) and OT.parse_amount(5167) == (5167.0, None)
    assert OT.parse_amount("abc")[0] is None


def test_two_writers_merge_and_history():
    d = tempfile.mkdtemp()
    a = OT.Store(d, me="Lucas", host="PC1")
    b = OT.Store(d, me="Pamela", host="PC2")
    a.reload()
    oid = a.add({"date": "2026-10-01", "store": "1", "supplier": "SAIS", "amount": 100.0})
    assert os.path.basename(a.file) == "log_PC1_Lucas.jsonl" and not os.path.exists(b.file)
    b.reload()
    assert b.orders[oid]["supplier"] == "SAIS"
    assert a.changed() is False
    b.set(oid, {"confirmed": "2026-10-02"})                 # 另一台电脑改：写在它自己的文件里
    assert a.changed()
    a.reload()
    assert a.orders[oid]["confirmed"] == "2026-10-02" and a.meta[oid]["confirmed"][1] == "Pamela"
    assert len(a.hist[oid]) == 2
    # 同步盘的冲突副本：同一条修改出现两次只算一次；坏掉的最后一行跳过
    shutil.copy(b.file, os.path.join(a.dir, "log_PC2_Pamela (Conflict).jsonl"))
    with open(b.file, "a", encoding="utf-8") as f:
        f.write('{"e": "broken", "t"')
    a.reload()
    assert len(a.hist[oid]) == 2
    a.delete([oid])
    b.reload()
    assert oid not in b.orders and b.deleted[oid] == "app"


def test_status_overdue_pending_leads():
    d = tempfile.mkdtemp()
    st = OT.Store(d, me="t", host="h")
    for k in range(3):                                       # SAIS 以前 10 天到货
        st.add({"date": f"2026-09-0{k + 1}", "store": "1", "supplier": "SAIS", "arrived": f"2026-09-{k + 11}"})
    late = st.add({"date": "2026-10-01", "store": "2", "supplier": "Sais srl"})
    fresh = st.add({"date": "2026-10-30", "store": "2", "supplier": "SAIS", "confirmed": "2026-10-31"})
    pend = st.add({"date": "2026-10-28", "store": "3", "supplier": "NUOVO"})
    deter = st.add({"date": "2026-10-01", "store": "3", "supplier": "Meloni", "kind": "deter"})
    canc = st.add({"date": "2026-10-01", "store": "3", "supplier": "X", "state": "cancel", "amount": 50})
    rs = {r["id"]: r for r in OT.rows(st, today=date(2026, 11, 2))}
    assert rs[late]["expected"] == date(2026, 10, 11) and rs[late]["overdue"] and rs[late]["late_by"] == 22
    assert rs[late]["supplier"] == "SAIS"                     # 显示最常见的写法
    assert rs[fresh]["status"] == "confirmed" and not rs[fresh]["overdue"]
    assert rs[pend]["pending"] and rs[pend]["status"] == "ordered" and rs[pend]["lead"] is None
    assert rs[deter]["status"] == "deter" and not rs[deter]["overdue"] and not rs[deter]["open"]
    assert rs[canc]["status"] == "cancel"
    st.set(late, {"state": "partial", "arrived": "2026-10-20"})
    r = {r["id"]: r for r in OT.rows(st, today=date(2026, 11, 2))}[late]
    assert r["status"] == "partial" and r["open"] and r["days"] == 19
    st.set(late, {"expected": "2026-11-05"})                   # 手动填的预计到货
    r = {r["id"]: r for r in OT.rows(st, today=date(2026, 11, 2))}[late]
    assert r["expected"] == date(2026, 11, 5) and not r["overdue"] and r["expected_manual"]
    s = OT.summary(OT.rows(st, today=date(2026, 11, 2)), st, "2026-10")
    assert s["pending"] == 1 and s["stuck"] == 0


def test_merge_suppliers_and_budget():
    d = tempfile.mkdtemp()
    st = OT.Store(d, me="t", host="h")
    st.add({"date": "2026-10-01", "store": "1", "supplier": "ZAMBETTI", "amount": 100})
    st.add({"date": "2026-10-02", "store": "1", "supplier": "Zambetti invernale", "amount": 50})
    assert OT.suggest_merges(st) == [("ZAMBETTI", ["ZAMBETTIINVERNALE"])]
    st.merge_suppliers(["ZAMBETTIINVERNALE"], "ZAMBETTI")
    rs = OT.rows(st)
    assert {r["supplier"] for r in rs} == {"ZAMBETTI"} and OT.supplier_stats(rs)[0]["n"] == 2
    st.unmerge("ZAMBETTIINVERNALE")
    assert len({r["sup_key"] for r in OT.rows(st)}) == 2
    st.set_budget("2026-10", "1", 1000)
    b = {(x["month"], x["store"]): x for x in OT.budget_rows(OT.rows(st), st)}
    assert len(b) == 6 and abs(b[("2026-10", "1")]["amount_iva"] - 183.0) < 1e-9
    assert abs(b[("2026-10", "1")]["pct"] - 0.15) < 1e-9 and b[("2026-10", "2")]["budget"] is None


def _piano(path):
    """模仿 PIANO ACQUISTO MESILE 的一张月份表：两块（Foiano / Capannori），第 8 行表头"""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    wb = Workbook()
    ws = wb.active
    ws.title = "OTT"
    for c0, name, budget in ((1, "Gli ordini \nper Foiano", 55000), (9, "Gli ordini \nper Capannori", 180000)):
        ws.cell(1, c0 - 1 if c0 > 1 else c0, name)       # 真实文件：Capannori 的标题在 H 列、表头从 I 列开始
        ws.cell(1, c0 + 2, "BADGET ACQUISTO IVATO")
        ws.cell(1, c0 + 5, budget)
        for k, h in enumerate(["Data\nOrdine", "Fornitore", "Ordine\nDa", "Importo\nInponibile", "Data \nconfermato",
                               "Data \narrivato", "Gg.\ncons."]):
            ws.cell(8, c0 + k, h)
    rows = [(datetime(2026, 10, 1), "TecnoAir System", "Marco", 5167, datetime(2026, 10, 1), datetime(2026, 10, 5)),
            (datetime(2026, 10, 4), "Meloni", "Pamela", 4027.01, "DETER", "DETER"),
            (datetime(2026, 10, 8), "SUNLUX", "Silvio", "311.10 €", None, "卡住了"),
            (datetime(2026, 10, 9), "Stefanplast", "LUCAS", 197.6, datetime(2026, 10, 12), None)]
    for i, r in enumerate(rows):
        for k, v in enumerate(r):
            ws.cell(9 + i, 1 + k, v)
    ws.cell(9, 6).comment = Comment("到了一部分", "x")
    ws.cell(9, 9, datetime(2026, 10, 1))
    ws.cell(9, 10, "TecnoAir System")
    ws.cell(9, 12, 5167)
    wb.create_sheet("ELENCO FORNITORI ").cell(1, 1, "FORNITORE")
    wb.save(path)


def test_read_and_sync_excel():
    d = tempfile.mkdtemp()
    x = os.path.join(d, "PIANO ACQUISTO MESILE  NUOVO_COPIA.xlsx")
    _piano(x)
    assert OT.find_excel(d) == x
    rows, budgets, sheets = OT.read_excel(x)
    assert sheets == ["OTT"] and len(rows) == 5
    assert sorted(budgets) == [("2026-10", "1", 55000.0), ("2026-10", "2", 180000.0)]
    by = {(r["store"], r["supplier"]): r for r in rows}
    t = by[("1", "TecnoAir System")]
    assert t["arrived"] == "2026-10-05" and t["state"] == "partial" and t["ref"] == "OTT!B9"   # 批注「到了一部分」
    assert by[("1", "Meloni")]["kind"] == "deter" and by[("1", "SUNLUX")]["state"] == "stuck"
    assert by[("1", "SUNLUX")]["amount"] == 311.1 and by[("2", "TecnoAir System")]["store"] == "2"
    st = OT.Store(d, me="t", host="h")
    res = OT.import_file(st, x)
    assert res["new"] == 5 and res["budgets"] == 2
    assert OT.import_file(st, x)["new"] == 0                  # 再同步一次：没有变化
    oid = by[("1", "Stefanplast")]["id"]
    st.set(oid, {"arrived": "2026-10-20"})                    # 程序里填了到货
    sun = by[("1", "SUNLUX")]["id"]
    st.delete([sun])                                         # 程序里删掉的
    # Excel 里：Stefanplast 改了金额、Meloni 那行删了、加了一行
    from openpyxl import load_workbook
    wb = load_workbook(x)
    ws = wb["OTT"]
    ws.cell(12, 4, 200.0)
    ws.cell(12, 6, datetime(2026, 10, 25))                   # Excel 里也填了到货，而且存得比程序改得晚（2026-11-04 起：谁最后改听谁）
    for k in range(1, 8):
        ws.cell(10, k).value = None
    for k, v in enumerate([datetime(2026, 10, 30), "Colzani", "LUCAS", 42]):
        ws.cell(13, 1 + k, v)
    wb.save(x)
    st2 = OT.Store(d, me="other", host="h2")
    res = OT.import_file(st2, x)
    assert res["new"] == 1 and res["removed"] == 1 and res["updated"] == 1, res
    o = st2.orders[oid]
    assert o["amount"] == 200.0 and o["arrived"] == "2026-10-25"
    assert sun not in st2.orders                             # 程序里删掉的不加回来
    assert by[("1", "Meloni")]["id"] not in st2.orders
    out = os.path.join(d, "exp.xlsx")
    OT.export_excel(OT.rows(st2), out)
    assert load_workbook(out).active.max_row == 5


def test_auto_register_drafts():
    """程序生成订单 → 待登记；同一天再生成更新金额、删掉没有了的；确认后算正式下单"""
    d = tempfile.mkdtemp()
    settings = {"tracking": {"folder": d, "me": "Lucas"}}
    written = [("/x/Ordine_DOPLA_0211.xlsx", "DOPLA", "FOIANO", 3, 120.0, "direct", "s"),
               ("/x/Ordine_DOPLA_0211.xlsx", "DOPLA", "BMS", 5, 300.0, "direct", "s"),
               ("/x/Ordine_LAMPA_0211.xlsx", "LAMPA", "MARLIA", 2, 80.0, "direct", "s")]
    res = OT.register_store_orders(settings, written)
    assert res["new"] == 3 and res["drafts"] == 3
    st = OT.Store(d, me="x").reload()
    rs = {(r["store"], r["supplier"]): r for r in OT.rows(st)}
    assert rs[("1", "DOPLA")]["status"] == "draft" and rs[("BMS", "DOPLA")]["amount"] == 300.0
    assert rs[("2", "LAMPA")]["origin"] == "store_orders" and rs[("2", "LAMPA")]["file"].endswith("LAMPA_0211.xlsx")
    s = OT.summary(OT.rows(st), st, date.today().strftime("%Y-%m"))
    assert s["draft"] == 3 and s["spent"] == 0                 # 待登记的不算花费
    # 重新配货：DOPLA FOIANO 金额变了、BMS 不订了；LAMPA 这次没配（不在这批供应商里 → 不删）
    res = OT.register_store_orders(settings, [("/x/Ordine_DOPLA_0211.xlsx", "DOPLA", "FOIANO", 4, 150.0, "direct", "s")])
    assert res["updated"] == 1 and res["removed"] == 1 and res["drafts"] == 2
    st.reload()
    rs = {(r["store"], r["supplier"]): r for r in OT.rows(st)}
    assert rs[("1", "DOPLA")]["amount"] == 150.0 and ("BMS", "DOPLA") not in rs and ("2", "LAMPA") in rs
    assert OT.confirm_drafts(st, [rs[("1", "DOPLA")]["id"]]) == 1
    r = {x["id"]: x for x in OT.rows(st)}[rs[("1", "DOPLA")]["id"]]
    assert r["status"] == "ordered" and r["buyer"] == "x"
    # 比价：门店名认得出才登记
    assert OT.register_wholesale(settings, "S.FILIPPO", [{"supplier": "MELONI", "amount": 900, "colli": 20, "file": "f"}])["new"] == 1
    assert OT.register_wholesale(settings, "???", [{"supplier": "MELONI", "amount": 1}]) is None
    assert OT.store_code("Capannori") == "2" and OT.store_code("S.CONCORDIO") == "5" and OT.store_code("BMS") == "BMS"
    assert OT.register_store_orders({"tracking": {"folder": d, "auto_register": False}}, written) is None


def test_export_piano_same_layout():
    """导出成原表的样子，再读回来：单、预算、每周单数都对得上；原文件里别的工作表留着"""
    from openpyxl import load_workbook
    d = tempfile.mkdtemp()
    x = os.path.join(d, "PIANO.xlsx")
    _piano(x)
    wb = load_workbook(x)
    for k, v in enumerate(["1ª settimana", "2ª settimana", "3ª settimana", "4ª settimana", "5ª settimana"]):
        for c0 in (1, 9):
            wb["OTT"].cell(3 + k, c0 + 2, v)
    for c0 in (1, 9):
        wb["OTT"].cell(2, c0 + 2, "Totale del mese")
    wb.save(x)
    st = OT.Store(d, me="t", host="h")
    OT.import_file(st, x)
    st.add({"date": "2026-11-03", "store": "2", "supplier": "NUOVO", "amount": 50.0})      # 新的月份
    st.add({"date": "2026-11-03", "store": "1", "supplier": "BOZZA", "amount": 9.0, "draft": True})   # 待登记不导出
    out = os.path.join(d, "out.xlsx")
    done = dict(OT.export_piano(OT.rows(st), st, x, out))
    assert done == {"NOV": 1, "OTT": 5}, done
    wb = load_workbook(out)
    assert wb.sheetnames == ["NOV", "OTT", "ELENCO FORNITORI "]
    ws = wb["OTT"]
    assert ws["F1"].value == 55000 and ws["E3"].value == 2 and ws["F3"].value == "=SUM(D9:D10)"   # 10-01、10-04 是第 1 周
    assert ws["F2"].value == "=SUM(F3:F7)" and ws["F11"].value == "卡住了" and ws["F9"].comment is not None
    rows, _b, _s = OT.read_excel(out)
    assert len(rows) == 6 and {r["supplier"] for r in rows if r["store"] == "2"} == {"TecnoAir System", "NUOVO"}
    assert OT._week_of(date(2026, 10, 4)) == 0 and OT._week_of(date(2026, 10, 5)) == 1 and OT._week_of(date(2026, 8, 31)) == 4


def test_budget_iva_detected():
    """6–9 月的表按含税算（每周合计 ×1.22）：预算换成不含税；10 月起不乘的照原样"""
    from openpyxl import load_workbook
    d = tempfile.mkdtemp()
    x = os.path.join(d, "PIANO.xlsx")
    _piano(x)
    wb = load_workbook(x)
    ws = wb["OTT"]
    for k in range(3):
        ws.cell(3 + k, 6, f"=SUM(D{9 + k}:D{9 + k})*1.22")      # Foiano：含税
        ws.cell(3 + k, 14, f"=SUM(L{9 + k}:L{9 + k})")          # Capannori：不含税
    ws.cell(2, 6, "=SUM(F3:G7)")                                # 本月合计不算
    ws["F1"] = 122000
    wb.save(x)
    _rows, budgets, _s = OT.read_excel(x)
    assert sorted(budgets) == [("2026-10", "1", 100000.0), ("2026-10", "2", 180000.0)]
    assert OT._is_iva(1, 2, 5, 6) and not OT._is_iva(1, 2, 1, 6) and not OT._is_iva(0, 0, 0, 0)


def test_month_summary_weeks():
    w = OT.week_ranges(2026, 10)                       # 10-01 是周四
    assert [(a.day, b.day) for a, b in w] == [(1, 4), (5, 11), (12, 18), (19, 25), (26, 31)]
    assert [(a.day, b.day) for a, b in OT.week_ranges(2026, 8)][-1] == (24, 31)   # 8 月有 6 周：最后一周并进第 5 周
    assert len(OT.week_ranges(2027, 2)) == 4                                        # 2027-02-01 周一、28 天
    d = tempfile.mkdtemp()
    st = OT.Store(d, me="t", host="h")
    st.add({"date": "2026-10-01", "store": "1", "supplier": "A", "amount": 100})
    st.add({"date": "2026-10-04", "store": "2", "supplier": "B", "amount": 50})
    st.add({"date": "2026-10-20", "store": "1", "supplier": "C", "amount": 30})
    st.add({"date": "2026-10-21", "store": "1", "supplier": "D", "amount": 999, "draft": True})
    st.set_budget("2026-10", "1", 1000)
    st.set_budget("2026-10", "2", 500)
    rs = OT.rows(st)
    a = OT.month_summary(rs, st, 2026, 10)
    assert a["budget"] == 1500 and a["n"] == 3 and a["amount"] == 180 and [w["n"] for w in a["weeks"]] == [2, 0, 0, 1, 0]
    f = OT.month_summary(rs, st, 2026, 10, store="1")
    assert f["budget"] == 1000 and f["amount"] == 130 and f["weeks"][0]["amount"] == 100
    assert OT.month_summary(rs, st, 2026, 11)["budget"] is None


def test_block_title_typo_and_unknown_block_kept():
    """11 月表 S.Concordio 的标题写成「San S.Conrdio」→ 以前整块没读；认不出的块不能把那家店以前的单删掉"""
    import openpyxl
    import order_tracking as OT
    assert OT.title_store("Gli ordini \nper San S.Conrdio") == "5"
    assert OT.title_store("Gli ordini per S.Filippo") == "3"
    assert OT.title_store("Gli ordini per Capannori") == "2"
    assert OT.title_store("Gli ordini per Pisa") is None
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "NOV"
    for c0, title in ((1, "Gli ordini per Foiano"), (9, "Gli ordini per San S.Conrdio")):
        ws.cell(1, c0, title)
        for i, h in enumerate(("Data Ordine", "Fornitore", "Ordine Da", "Importo Inponibile", "Data confermato", "Data arrivato")):
            ws.cell(8, c0 + i, h)
        ws.cell(9, c0, datetime(2026, 11, 1))
        ws.cell(9, c0 + 1, "Meloni")
        ws.cell(9, c0 + 3, 100.0)
    p = os.path.join(tempfile.mkdtemp(), "p.xlsx")
    wb.save(p)
    info = {}
    rows, _b, _s = OT.read_excel(p, info)
    assert sorted(r["store"] for r in rows) == ["1", "5"] and info["unknown"] == []
    # 标题改成认不出的：那一块进 unknown，同步时也不删这家店以前的单
    d = tempfile.mkdtemp()
    st = OT.Store(d, me="t")
    st.reload()
    OT.sync_excel(st, rows, [], ["NOV"], {("NOV", "1"), ("NOV", "5")})
    ws.cell(1, 9, "Gli ordini per ???")
    wb.save(p)
    info = {}
    rows2, _b, sheets = OT.read_excel(p, info)
    assert [r["store"] for r in rows2] == ["1"] and info["unknown"] and info["unknown"][0][0] == "NOV"
    res = OT.sync_excel(st, rows2, [], sheets, info["blocks"])
    assert res["removed"] == 0 and sum(1 for o in st.orders.values() if o.get("store") == "5") == 1


def test_find_excel_prefers_nuovo_original():
    d = tempfile.mkdtemp()
    for f in ("PIANO ACQUISTO MESILE  NUOVO_COPIA.xlsx", "PIANO ACQUISTO MESILE  NUOVO.xlsx", "PIANO ACQUISTO MESILE .xlsx",
              "~$PIANO ACQUISTO MESILE  NUOVO.xlsx"):
        open(os.path.join(d, f), "wb").close()
    assert os.path.basename(OT.find_excel(d)) == "PIANO ACQUISTO MESILE  NUOVO.xlsx"
    cp = os.path.join(d, "PIANO ACQUISTO MESILE  NUOVO_COPIA.xlsx")
    assert OT.prefer_original(cp) == os.path.join(d, "PIANO ACQUISTO MESILE  NUOVO.xlsx")
    assert OT.prefer_original(os.path.join(d, "PIANO ACQUISTO MESILE  NUOVO.xlsx")) is None
    os.remove(os.path.join(d, "PIANO ACQUISTO MESILE  NUOVO.xlsx"))
    assert OT.prefer_original(cp) is None
