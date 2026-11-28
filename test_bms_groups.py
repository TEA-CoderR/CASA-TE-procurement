"""BMS 凑单（2026-11-28 用户定 B）：PIANO 里标 CEN 的几家店 = 一张 BMS 单；程序生成的 BMS 单发出后拆成每家店一行"""
import os
import time
import tempfile
from datetime import date, datetime

import order_tracking as OT
import excel_sync as XS
import bms_check as BC


def _piano(path, rows):
    """NOV 表三块：FOIANO（A）、S.FILIPPO（J）、BMS（S）。rows = [(块, 日期, 供应商, 金额, 确认, 到货, Gg.cons.)]"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "NOV"
    starts = {"1": 1, "3": 10, "BMS": 19}
    titles = {"1": "Gli ordini per Foiano", "3": "Gli ordini per San Filippo", "BMS": "Gli ordini per BMS"}
    for k, c in starts.items():
        ws.cell(1, c).value = titles[k]
        ws.cell(1, c + 2).value = "BADGET ACQUISTO"
        ws.cell(1, c + 5).value = 50000
        for i, h in enumerate(["Data Ordine", "Fornitore", "Ordine Da", "Importo", "Data confermato", "Data arrivato",
                               "Gg. cons."]):
            ws.cell(3, c + i).value = h
    nxt = {k: 4 for k in starts}
    for blk, d, sup, amt, conf, arr, gg in rows:
        c, r = starts[blk], nxt[blk]
        nxt[blk] += 1
        vals = (d, sup, "LUCAS", amt, conf, arr, gg)
        for i, v in enumerate(vals):
            ws.cell(r, c + i).value = v
    wb.save(path)


def _store(rows):
    d = tempfile.mkdtemp()
    x = os.path.join(d, "PIANO ACQUISTO MESILE  NUOVO.xlsx")
    _piano(x, rows)
    os.utime(x, (time.time() - 3600,) * 2)
    st = OT.Store(d, me="pc1", host="h1").reload()
    OT.import_file(st, x)
    return d, x, st


def test_cen_rows_are_one_bms_order_and_amounts_stay_with_each_store():
    d1 = datetime(2026, 11, 19)
    d, x, st = _store([("1", d1, "Marchetti", 336.9, d1, None, "CEN"),
                       ("3", d1, "Marchetti", 122.0, None, None, "CEN"),
                       ("1", d1, "SAIS", 500.0, None, None, None)])            # 门店自己的单（没标 CEN）
    assert any(o.get("origin") == OT.GROUP_SRC for o in st.orders.values())    # 同步时建好了 BMS 单
    rs = OT.rows(st, today=date(2026, 11, 20))
    us = OT.units(rs)
    assert len(us) == 2                                                         # BMS 单 + SAIS
    b = next(r for r in us if r["store"] == "BMS")
    assert b["group"] and b["store_name"] == "BMS (FO, FI)" and b["amount"] == 458.9
    assert b["status"] == "confirmed" and b["shares"] == {"1": 336.9, "3": 122.0}
    shares = [r for r in rs if r["share"]]
    assert len(shares) == 2 and all(r["status"] == "confirmed" and r["part_of"] == b["id"] for r in shares)
    # 金额：每家店那一份算进那家店，BMS 单本身不算（不重复）
    m = "2026-11"
    assert OT.summary(rs, st, m)["spent"] == 958.9
    assert OT.summary(rs, st, m, store="1")["spent"] == 836.9
    assert OT.summary(rs, st, m, store="3")["spent"] == 122.0
    assert OT.summary(rs, st, m, store="BMS")["spent"] == 0
    ms = OT.month_summary(rs, st, 2026, 11)
    assert ms["n"] == 2 and ms["amount"] == 958.9
    assert OT.month_summary(rs, st, 2026, 11, "3")["amount"] == 122.0
    # 按门店看：S.FILIPPO 看得到它参与的 BMS 单
    assert [r["store"] for r in OT.units(rs, "3")] == ["BMS"]
    # 没有要写进 Excel 的（程序建的 BMS 单不写）
    assert XS.pending(st, "pc1", everyone=True) == []


def test_confirm_on_bms_order_goes_to_every_store_row_and_back_to_excel():
    d1 = datetime(2026, 11, 19)
    d, x, st = _store([("1", d1, "Marchetti", 336.9, None, None, "CEN"),
                       ("3", d1, "Marchetti", 122.0, None, None, "CEN")])
    gid = next(g for g in OT.groups(st))
    st.set(gid, {"arrived": "2026-11-25"})
    for m in OT.groups(st)[gid]["members"]:
        assert st.orders[m]["arrived"] == "2026-11-25"
    assert XS.writeback(st, x, "pc1")["status"] == "ok"
    rows, _b, _s = OT.read_excel(x)
    assert all(r["arrived"] == "2026-11-25" and r["cen"] for r in rows)        # Gg.cons. 还是 CEN，没被天数盖掉
    # 一家店到了、一家没到 = 部分到货
    st2 = OT.Store(d, me="pc2", host="h2").reload()
    m3 = next(m for m in OT.groups(st2)[gid]["members"] if st2.orders[m]["store"] == "3")
    st2.set(gid, {"arrived": None})                                           # 撤回：BMS 单和每家店都空
    assert all(not st2.orders[m].get("arrived") for m in OT.groups(st2)[gid]["members"])
    st2.set(m3, {"arrived": "2026-11-26"}, src="excel")                       # Excel 里只有 S.FILIPPO 那一行填了到货
    b = next(r for r in OT.rows(st2) if r["id"] == gid)
    assert b["status"] == "partial"


def test_bms_row_in_bms_block_is_the_order_and_only_extra_amount_counts_for_bms():
    d1 = datetime(2026, 10, 8)
    d, x, st = _store([("3", d1, "HD", 203.64, None, None, "CEN"),
                       ("BMS", d1, "HD", 289.14, d1, None, None)])
    gs = OT.groups(st)
    assert len(gs) == 1
    gid, g = next(iter(gs.items()))
    assert st.orders[gid].get("origin") == "excel" and st.orders[gid].get("store") == "BMS"
    assert not any(o.get("origin") == OT.GROUP_SRC for o in st.orders.values())
    rs = OT.rows(st, today=date(2026, 10, 10))
    b = next(r for r in rs if r["id"] == gid)
    assert b["amount"] == 289.14 and b["status"] == "confirmed"
    m = "2026-10"
    assert round(OT.summary(rs, st, m)["spent"], 2) == 289.14                  # 289.14 = 203.64（S.FILIPPO）+ 85.50（BMS）
    assert round(OT.summary(rs, st, m, store="BMS")["spent"], 2) == 85.5


def test_program_bms_order_split_into_store_rows_with_cen_when_sent():
    d, x, st = _store([("1", datetime(2026, 11, 2), "SAIS", 100.0, None, None, None)])
    written = [("f.xlsx", "FACCO", "BMS", 20, 1159.76, "bms", "s.xlsx", ("1", "3"), {"1": 700.0, "3": 459.76})]
    import order_tracking as OT2
    items = [{"store": "BMS", "supplier": "FACCO", "colli": 20, "amount": 1159.76, "file": "f.xlsx",
              "bms_of": ["1", "3"], "shares": written[0][8]}]
    OT2.register_drafts(st, "store_orders", items, today=date(2026, 11, 25))
    pid = next(oid for oid, o in st.orders.items() if o.get("supplier") == "FACCO")
    assert not any(o.get("part_of") for o in st.orders.values())                # 待登记：还不拆
    OT.confirm_drafts(st, [pid], today=date(2026, 11, 26))
    kids = {o["store"]: (oid, o) for oid, o in st.orders.items() if o.get("part_of") == pid}
    assert set(kids) == {"1", "3"} and kids["1"][1]["amount"] == 700.0 and kids["1"][1]["cen"]
    assert kids["3"][1]["date"] == "2026-11-26"
    ops = XS.pending(st, "pc1", everyone=True)
    assert sorted((k, st.orders[o]["store"]) for k, o, _v in ops) == [("append", "1"), ("append", "3"), ("append", "BMS")]
    bms_v = next(v for k, o, v in ops if st.orders[o]["store"] == "BMS")
    assert bms_v["amount"] is None                                               # BMS 那一行金额空着，免得重复算
    assert XS.writeback(st, x, "pc1")["status"] == "ok"
    rows, _b, _s = OT.read_excel(x)
    f = [r for r in rows if r["supplier"] == "FACCO"]
    assert sorted((r["store"], r["amount"], bool(r["cen"])) for r in f) == \
        [("1", 700.0, True), ("3", 459.76, True), ("BMS", None, False)]
    # 另一台电脑读回来：不多单、不再写、还是一张 BMS 单
    st2 = OT.Store(d, me="pc2", host="h2").reload()
    r2 = OT.import_file(st2, x)
    assert r2["new"] == 0 and XS.pending(st2, "pc2", everyone=True) == []
    us = [r for r in OT.units(OT.rows(st2)) if r["supplier"] == "FACCO"]
    assert len(us) == 1 and us[0]["id"] == pid and us[0]["amount"] == 1159.76 and us[0]["status"] == "ordered"
    rs = OT.rows(st2)
    assert OT.summary(rs, st2, "2026-11", store="1")["spent"] == 800.0          # SAIS 100 + FACCO 那一份 700


def test_bms_check_lists_unmarked_rows_matching_a_bms_file_exactly():
    d1 = datetime(2026, 11, 2)
    d, x, st = _store([("1", d1, "MISS SPACE", 141.75, None, None, "CEN"),
                       ("3", d1, "MISS SPACE", 236.25, None, None, None),          # 忘了标 CEN
                       ("3", d1, "SAIS", 236.25, None, None, None),                # 金额一样，供应商不一样
                       ("1", None, "COLZANI", 42.0, None, None, "CEN")])           # 标了 CEN、日期空着
    files = [{"rel": "NOVEMBRE/Ordine MISS SPACE BMS 0211.xlsx", "path": "/x", "store": "C",
              "date": date(2026, 11, 2), "words": ["MISS", "SPACE", "BMS"]},
             {"rel": "OTTOBRE/Ordine COLZANI  BMS 1910.xlsx", "path": "/y", "store": "C",
              "date": date(2026, 10, 19), "words": ["COLZANI", "BMS"]},
             {"rel": "NOVEMBRE/Ordine_SAIS_0211.xlsx", "path": "/z", "store": "C",         # 名字没写 BMS：不算
              "date": date(2026, 11, 2), "words": ["SAIS"]}]
    lines = {"/x": {"1": [{"colli": 3, "pack": 1, "price": 47.25}], "3": [{"colli": 5, "pack": 1, "price": 47.25}]},
             "/y": {"1": [{"colli": 2, "pack": 1, "price": 21.0}]},
             "/z": {"3": [{"colli": 1, "pack": 1, "price": 236.25}]}}

    class Idx:
        def lines(self, f, log=None):
            return lines[f["path"]]
    res = BC.find(st, files, Idx())
    assert [(x["store"], x["supplier"]) for x in res["add"]] == [("3", "MISS SPACE")]
    assert res["add"][0]["cen_others"] and res["add"][0]["sure"]
    assert [(x["supplier"], x["new_date"]) for x in res["date"]] == [("COLZANI", date(2026, 10, 19))]
    before = BC.apply(st, add_ids=[res["add"][0]["id"]])
    g = [g for g in OT.groups(st).values() if len(g["members"]) == 2]
    assert len(g) == 1                                                            # 合成一张 BMS 单
    assert any(k == "update" and v.get("cen") for k, _o, v in XS.pending(st, "pc1", everyone=True))
    BC.undo(st, before)
    assert not st.orders[res["add"][0]["id"]].get("cen")


def test_bms_names():
    assert BC.bms_named("Ordine MARCHETTI BMS 1911.xlsx") and BC.bms_named("Ordine_EURSHOSE_BMS_2611.xlsx")
    assert BC.bms_named("Ordine BAMA 5PDV1011.xlsx") and BC.bms_named("Ordine COLZANI 3 PDV 1511.xlsx")
    assert not BC.bms_named("Ordine_BIACCHI ETTORE SRL_0411.xlsx") and not BC.bms_named("Ordine E-ENERGY S.Filippo 1111.xlsx")
