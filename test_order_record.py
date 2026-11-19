"""在途（order_record）：读 RECORD ORDINE 订单文件、和订单跟踪对单、DETERSIVO 上周订单 → 扣减文件"""
import os
import json
import tempfile
from datetime import date, datetime

import order_record as R

HDR = ["Cod.Art.", "Codice EAN", "Descrizione", "PZ*COLLI", "Colli", "Prezzo netto"]


def _ws(ws, lines, title_rows=4):
    for _ in range(title_rows):
        ws.append([])
    ws.append(HDR)
    for code, ean, desc, pack, colli in lines:
        ws.append([code, ean, desc, pack, colli, 1.0])


def _store_file(path, per_store):
    """总仓那种：每家店一页"""
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, lines in per_store.items():
        _ws(wb.create_sheet(name), lines)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)


def _grid_file(path, lines, dest=None):
    """门店自己订的：一页 GRIGLIA"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "GRIGLIA "
    ws.append(["", "", "", "", "Luogo di destinazione", dest or ""])
    for _ in range(3):
        ws.append([])
    ws.append(HDR)
    for code, ean, desc, pack, colli in lines:
        ws.append([code, ean, desc, pack, colli, 1.0])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)


def _touch(path, d):
    t = datetime(d.year, d.month, d.day, 12).timestamp()
    os.utime(path, (t, t))


def test_file_date_and_words():
    mt = datetime(2026, 10, 29, 10).timestamp()
    assert R.file_date("Ordine SAIS Foiano 2010.xlsx", mt) == date(2026, 10, 20)
    assert R.file_date("ordine SAIS san marco 281026.xlsx", mt) == date(2026, 10, 28)
    assert R.file_date("ordine  TAVOLA SPA07.08.xlsx", datetime(2026, 8, 8).timestamp()) == date(2026, 8, 7)
    # 模板版本号（0726、2308）不是下单日 → 用修改日期
    assert R.file_date("SCHEDA STEFANPLAST 2308_batch.xlsx", mt) == date(2026, 10, 29)
    assert R.file_date("ORDINE MARLYA 2810 CUKI confresco SRL 0726_batch.xlsx", mt) == date(2026, 10, 28)
    assert R.name_words("Ordine_COLZANI S.P.A_2910.xlsx") == ["COLZANI"]
    assert "CUKI" in R.name_words("ORDINE MARLYA 2810 CUKI confresco SRL 0726_batch.xlsx")
    assert R.supplier_score("Belli e Forte", R.name_words("Ordine BELLI E FORTI S.Marco 2610.xlsx")) >= 0.8
    assert R.supplier_score("FHP(GIMI)", R.name_words("Ordine FHP (GIMI) BMS 1610.xlsx")) >= 0.8
    assert R.supplier_score("SAIS", R.name_words("Ordine MONGE Foiano 2010.xlsx")) < 0.8
    assert R.folder_store("06 RECORD CENTRALE ACQUISTI/OTTOBRE/x.xlsx") == "C"
    assert R.folder_store("02 MARLIA/OTTOBRE/x.xlsx") == "2"


def test_match_orders_and_in_transit():
    today = date(2026, 11, 3)
    with tempfile.TemporaryDirectory() as root:
        # 门店 2 自己订的 SAIS（GRIGLIA 页）
        p1 = os.path.join(root, "02 MARLIA", "OTTOBRE", "Ordine SAIS Capannori 2010.xlsx")
        _grid_file(p1, [("100", "8001", "SAIS A", 6, 2), ("101", "8002", "SAIS B", 12, 0)])
        _touch(p1, date(2026, 10, 20))
        # 总仓 BMS 单，各门店页
        p2 = os.path.join(root, "06 RECORD CENTRALE ACQUISTI", "NOVEMBRE", "Ordine MISS SPACE BMS 0211.xlsx")
        _store_file(p2, {"FOIANO": [("", "8033", "BAULETTO", 27, 1)], "MARLIA": [("", "8033", "BAULETTO", 27, 2)],
                         "GRIGLIA PER TUTTI PDV": [("", "8033", "BAULETTO", 27, 3)]})
        _touch(p2, date(2026, 11, 2))
        # 太旧的文件（不读）
        p3 = os.path.join(root, "01 FOIANO", "GIU", "Ordine SAIS Foiano 0806.xlsx")
        _grid_file(p3, [("100", "8001", "SAIS A", 6, 5)])
        _touch(p3, date(2026, 6, 8))
        # 另一家供应商（对不上）
        p4 = os.path.join(root, "02 MARLIA", "OTTOBRE", "Ordine MONGE Capannori 2010.xlsx")
        _grid_file(p4, [("200", "9001", "MONGE", 6, 1)])
        _touch(p4, date(2026, 10, 20))
        files = R.list_files(root, today)
        assert {f["rel"] for f in files} == {"02 MARLIA/OTTOBRE/Ordine SAIS Capannori 2010.xlsx",
                                             "06 RECORD CENTRALE ACQUISTI/NOVEMBRE/Ordine MISS SPACE BMS 0211.xlsx",
                                             "02 MARLIA/OTTOBRE/Ordine MONGE Capannori 2010.xlsx"}
        rows = [
            {"id": "a", "date": date(2026, 10, 20), "store": "2", "supplier": "SAIS", "status": "confirmed"},
            {"id": "b", "date": date(2026, 11, 2), "store": "1", "supplier": "MISS SPACE", "status": "ordered"},
            {"id": "c", "date": date(2026, 11, 2), "store": "2", "supplier": "MISS SPACE", "status": "ordered"},
            {"id": "d", "date": date(2026, 11, 2), "store": "BMS", "supplier": "Miss Space", "status": "ordered"},
            {"id": "e", "date": date(2026, 10, 20), "store": "2", "supplier": "SAIS", "status": "arrived"},
            {"id": "f", "date": date(2026, 8, 1), "store": "2", "supplier": "SAIS", "status": "ordered"},  # 超过 60 天
            {"id": "g", "date": date(2026, 10, 21), "store": "3", "supplier": "LUANCE", "status": "ordered"},
            {"id": "h", "date": date(2026, 10, 20), "store": "2", "supplier": "MONGE", "status": "ordered",
             "record": R.NO_FILE},                                                    # 手动标了没有文件
            {"id": "x", "date": date(2026, 10, 30), "store": "1", "supplier": "DETER", "status": "deter"},
        ]
        orders = R.open_orders(rows, today)
        assert [o["id"] for o in orders] == ["a", "b", "c", "d", "g", "h"]
        res = R.resolve(orders, files, root)
        assert res["a"][0]["rel"].endswith("SAIS Capannori 2010.xlsx") and res["a"][1] == "auto"
        assert res["b"][0]["rel"].endswith("MISS SPACE BMS 0211.xlsx")
        assert res["d"][0]["rel"].endswith("MISS SPACE BMS 0211.xlsx")
        assert res["g"] == (None, "none") and res["h"] == (None, "none")
        idx = R.Index(os.path.join(root, "cache.json"))
        t = R.in_transit(orders, res, idx)
        assert R.lookup(t, 2, "8001")["colli"] == 2               # GRIGLIA 页算文件夹那家店，0 箱的行不算
        assert R.lookup(t, 2, "8002") is None
        assert R.lookup(t, "2", code="100")["colli"] == 2
        # MISS SPACE：门店单 + BMS 单对到同一个文件，不重复算
        assert R.lookup(t, 1, "8033")["colli"] == 1
        assert R.lookup(t, 2, "8033")["colli"] == 2
        assert R.lookup(t, 1, "8033")["pcs"] == 27
        assert t["orders"]["d"]["n"] == 0 and t["orders"]["b"]["n"] == 1
        assert "MISS SPACE" in R.transit_text(R.lookup(t, 1, "8033"))
        idx.save()
        assert R.Index(os.path.join(root, "cache.json")).get(res["a"][0]) is not None   # 缓存记住了
        # 手动指定文件
        rows2 = [dict(rows[6], record="02 MARLIA/OTTOBRE/Ordine MONGE Capannori 2010.xlsx", store="2")]
        res2 = R.resolve(R.open_orders(rows2, today), files, root)
        assert res2["g"][1] == "manual"
        t2 = R.in_transit(R.open_orders(rows2, today), res2, idx)
        assert R.lookup(t2, 2, "9001")["colli"] == 1


def test_grid_in_central_folder_uses_destination():
    with tempfile.TemporaryDirectory() as root:
        p = os.path.join(root, "06 RECORD CENTRALE ACQUISTI", "x.xlsx")
        _grid_file(p, [("1", "8001", "A", 6, 4)], dest="CASA & TE S.FILIPPO\nVia …")
        assert R.read_lines(p, "C") == {"3": [{"ean": "8001", "code": "1", "desc": "A", "colli": 4.0, "pack": 6.0, "price": 1.0}]}


def test_deter_last_order_to_elim_file():
    from openpyxl import Workbook, load_workbook
    with tempfile.TemporaryDirectory() as root:
        res = os.path.join(root, "4 比价结果")
        od = os.path.join(res, "2026-10-25", "FOIANO", "ORDINE")
        os.makedirs(od)
        bk = os.path.join(res, "2026-10-25", "FOIANO", "_备份")
        os.makedirs(bk)
        with open(os.path.join(bk, "last_allocation.json"), "w", encoding="utf-8") as f:
            json.dump({"supplier_labels": {"Listino_01 - Offerta.xlsx": "MELONI"}}, f)
        wb = Workbook()
        ws = wb.active
        ws.title = "ORDINE"
        hdr = ["CODICE EAN", "ARTICOLO", "COLLI"] + [None] * 25 + ["供应商调整", None, "Listino_01\xa0-\xa0Offerta.xlsx"]
        ws.append(hdr)
        ws.append(["8001", "A", 2] + [None] * 25 + [None, "Listino_01\xa0-\xa0Offerta.xlsx", 1.2])
        ws.append(["8002", "B", 1] + [None] * 25 + [None, None, None])               # 哪家都没有 → 没订
        ws.append(["8003", "C", 0] + [None] * 25 + [None, "Listino_01\xa0-\xa0Offerta.xlsx", 1.0])
        p = os.path.join(od, "确认订单_FOIANO_20261025_1335.xlsx")
        wb.save(p)
        # 太新（今天）/ 太旧的日期文件夹不算
        os.makedirs(os.path.join(res, "2026-11-03", "FOIANO", "ORDINE"))
        c = R.deter_candidates([res, os.path.join(root, "nope")], "FOIANO", date(2026, 11, 1))
        assert c == [(date(2026, 10, 25), p)]
        assert R.deter_candidates([res], "MARLIA", date(2026, 11, 1)) == []
        lines = R.read_deter_order(p)
        assert lines == [{"ean": "8001", "desc": "A", "colli": 2.0, "supplier": "MELONI"}]
        out = R.write_elim(lines, os.path.join(root, "3 扣减文件", "FOIANO", "x.xlsx"))
        ws2 = load_workbook(out).active
        assert [c.value for c in ws2[1]][:3] == ["CODICE EAN", "ARTICOLO", "COLLI"]
        assert [c.value for c in ws2[2]][:3] == ["8001", "A", 2]


def test_transit_subtracted_from_suggestions():
    """2026-11-03 用户：在途箱数从建议量里扣"""
    import store_orders as SO
    import order_check as OC
    transit = {"by_ean": {("2", "8001"): {"colli": 1.0, "pcs": 12.0, "orders": [("2026-11-01", "SAIS", 1.0)], "desc": ""}},
               "by_code": {("2", "100"): {"colli": 1.0, "pcs": 12.0, "orders": [], "desc": ""}}, "orders": {}}
    sc = {"path": "p", "name": "SAIS", "sheets": {2: "MARLIA", 3: "S.FILIPPO"},
          "items": [{"row": 5, "code": "100", "db_code": "100", "ean": "8001", "desc": "A", "pack": 6, "price": 1.0}]}
    demand = {2: {"100": {"sugg": 30, "need": 30, "inv": 0, "daily": 1.0}},
              3: {"100": {"sugg": 30, "need": 30, "inv": 0, "daily": 1.0}}, "_algo": None}
    assert SO.apply_transit(demand, [sc], transit) == 1
    assert demand[2]["100"]["transit"] == 12 and demand[2]["100"]["need"] == 18
    assert "transit" not in demand[3]["100"]
    # 订货箱数 = 建议箱数 − 在途箱数：建议 30 件 = 5 箱，在途 12 件 = 2 箱 → 3 箱；别的店不扣
    assert SO.base_colli(sc, demand) == {2: {5: 3}, 3: {5: 5}}
    assert SO.base_colli(sc, demand, transit=False) == {2: {5: 5}, 3: {5: 5}}
    assert SO.transit_boxes({"transit": 13}, 6) == 2 and SO.transit_boxes({}, 6) == 0
    # 检查清单：整箱扣，零头不扣
    row = {"in_db": True, "pack": 6, "file_qty": 4, "suggested": 24, "inv0": 0, "daily0": 1.0, "daily": 1.0,
           "cycle": 30, "tier": "B", "sales": [30, 60, 90, 180], "transit_pcs": 13.0}
    OC.init_boxes([row])
    assert row["transit_cut"] == 2 and row["sbox"] == 2 and row["fbox"] == 2
    row2 = dict(row, transit_pcs=5.0)
    OC.init_boxes([row2])
    assert row2["sbox"] == 4 and "transit_cut" not in row2


def test_deduct_switch_default_on():
    assert R.deduct_on({}) and R.deduct_on({"tracking": {}})
    assert not R.deduct_on({"tracking": {"deduct_transit": False}})


def test_reapply_transit_is_idempotent():
    """标了到货再算一次：上次扣的先还原，不会越扣越多"""
    import store_orders as SO
    sc = {"path": "p", "name": "SAIS", "sheets": {2: "MARLIA"},
          "items": [{"row": 5, "code": "100", "db_code": "100", "ean": "8001", "desc": "A", "pack": 6, "price": 1.0}]}
    demand = {2: {"100": {"sugg": 30, "need": 30, "inv": 0, "daily": 1.0}}, "_algo": None}
    t1 = {"by_ean": {("2", "8001"): {"colli": 1.0, "pcs": 12.0, "orders": [("2026-11-01", "SAIS", 1.0, "o1")], "desc": ""}},
          "by_code": {}, "orders": {}}
    SO.apply_transit(demand, [sc], t1)
    SO.apply_transit(demand, [sc], t1)
    assert demand[2]["100"]["need"] == 18
    assert R.order_ids(demand[2]["100"]["transit_info"]) == ["o1"]
    SO.apply_transit(demand, [sc], {"by_ean": {}, "by_code": {}, "orders": {}})     # 到了：不再扣
    assert "transit" not in demand[2]["100"] and demand[2]["100"]["need"] == 30
    assert SO.base_colli(sc, demand) == {2: {5: 5}}


def test_order_file_edits_sync_back_to_tracking():
    """2026-11-04 用户：在生成的订单文件里减少 / 砍掉箱数 → 订单跟踪的箱数、金额跟着改；程序里后来手改的不动"""
    import time
    import order_tracking as OT
    from openpyxl import load_workbook
    d = tempfile.mkdtemp()
    p = os.path.join(d, "out", "Ordine_SAIS_0411.xlsx")
    # 一个文件：FOIANO 直送；MARLIA + S.FILIPPO 进 BMS。单价 1.0 → 金额 = 箱 × 每箱件数
    _store_file(p, {"FOIANO": [("A1", "8001", "x", 6, 2), ("A2", "8002", "y", 12, 1)],
                    "MARLIA": [("A1", "8001", "x", 6, 3)], "S.FILIPPO": [("A2", "8002", "y", 12, 1)]})
    st = OT.Store(d, me="pc1").reload()
    today = date.today()
    OT.register_drafts(st, "store_orders", [
        {"store": "1", "supplier": "SAIS", "colli": 3, "amount": 24.0, "file": p},
        {"store": "BMS", "supplier": "SAIS", "colli": 4, "amount": 30.0, "file": p}], today=today)
    assert R.sync_files(st, {}) == []                                   # 没改过：不动
    t0 = time.time() - 2
    wb = load_workbook(p)
    wb["FOIANO"].cell(7, 5).value = 0                                    # 砍掉 A2
    wb["MARLIA"].cell(6, 5).value = 1                                    # BMS 里 MARLIA 3 → 1 箱
    wb.save(p)
    os.utime(p, (t0, t0))
    ch = {st.orders[o]["store"]: c for o, c, _r in R.sync_files(st, {})}
    assert ch == {"1": {"colli": 2, "amount": 12.0}, "BMS": {"colli": 2, "amount": 18.0}}
    rows = {r["store"]: r for r in OT.rows(st)}
    assert rows["1"]["from_file"] and rows["1"]["amount"] == 12.0
    # 程序里有人手改了金额（比文件晚）→ 不再被文件盖掉
    foiano = OT.draft_id("store_orders", today.isoformat(), "1", "SAIS")
    st.set(foiano, {"amount": 15.0})
    assert R.sync_files(st, {}) == [] and st.orders[foiano]["amount"] == 15.0
    # 重新配货（新文件）：文件读回来的值照样被重新生成的更新
    bms = OT.draft_id("store_orders", today.isoformat(), "BMS", "SAIS")
    OT.register_drafts(st, "store_orders", [
        {"store": "1", "supplier": "SAIS", "colli": 3, "amount": 24.0, "file": p},
        {"store": "BMS", "supplier": "SAIS", "colli": 5, "amount": 40.0, "file": p}], today=today)
    assert st.orders[bms]["amount"] == 40.0


def test_transit_tags_do_not_contradict_verdict():
    """2026-11-19：库存 0、在途够了 → 不订；详情栏不能再挂「库存少 · 照清单」「缺货保护」（和不订矛盾），只说在途"""
    import order_check as OC
    row = {"in_db": True, "pack": 6, "file_qty": 2, "suggested": 12, "inv0": 0, "daily0": 0.16, "daily": 0.16,
           "cycle": 30, "tier": "C", "sales": [5, 10, 15, 30], "transit_pcs": 60.0}
    OC.init_boxes([row])
    assert row["sbox"] == 0 and row["transit_cut"] == 2
    tags = [t for t, _ in OC.key_tags(row, 10)]
    assert tags == ["在途 10 箱 · 已经够了"]
    # 只扣了一部分：在途放第一个，写少订几箱
    row2 = dict(row, file_qty=4, transit_pcs=6.0)
    OC.init_boxes([row2])
    assert row2["sbox"] == 3 and row2["transit_cut"] == 1
    assert OC.key_tags(row2, 1)[0][0] == "在途 1 箱 · 少订 1 箱"
