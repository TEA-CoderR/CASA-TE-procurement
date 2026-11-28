"""用程序发订单 → 自动登记 PIANO（2026-11-28 用户：以后统一用程序发订单邮件、自动登记 PIANO ACQUISTO 表）"""
from datetime import date, datetime

from openpyxl import load_workbook

import order_tracking as OT
import excel_sync as XS
import mail_send as MS
from test_bms_groups import _store, _piano


MAIL = {"to": ["ordini@x.it"], "from": "info@casa-te.com", "subject": "Ordine", "mid": "<m1>"}


def _draft(st, source, store, supplier, amount, day):
    OT.register_drafts(st, source, [{"store": store, "supplier": supplier, "amount": amount, "colli": 3,
                                     "file": "f.xlsx"}], today=day)
    return next(oid for oid, o in st.orders.items() if o.get("origin") == source and o.get("supplier") == supplier)


def _cells(x, ref):
    ws = load_workbook(x)[ref.split("!")[0]]
    r = int("".join(ch for ch in ref.split("!")[1] if ch.isdigit()))
    return ws, r


def test_sent_order_written_with_buyer_and_piano_spelling():
    d0 = datetime(2026, 11, 3)
    d, x, st = _store([("1", d0, "Facco", 100.0, None, None, None),
                       ("1", d0, "Facco", 120.0, None, None, None),
                       ("3", d0, "SAIS", 50.0, None, None, None)])
    assert OT.buyers(st) == ["LUCAS"]
    oid = _draft(st, "store_orders", "1", "FACCO", 812.5, date(2026, 11, 25))
    MS.mark_sent(st, [oid], MAIL, today=date(2026, 11, 26), buyer="Pamela")
    o = st.orders[oid]
    assert o["buyer"] == "Pamela" and o["supplier"] == "Facco" and not o.get("draft") and o["date"] == "2026-11-26"
    ops = XS.pending(st, "pc1", everyone=True)
    assert [(k, v["supplier"], v["buyer"]) for k, _o, v in ops] == [("append", "Facco", "Pamela")]
    assert XS.writeback(st, x, "pc1")["status"] == "ok"
    rows, _b, _s = OT.read_excel(x)
    r = next(r for r in rows if r["amount"] == 812.5)
    assert r["store"] == "1" and r["buyer"] == "Pamela" and r["date"] == "2026-11-26"
    assert st.orders[oid]["ref"] == r["ref"]
    # 另一台电脑读回来：不多单、不再写
    st2 = OT.Store(d, me="pc2", host="h2").reload()
    assert OT.import_file(st2, x)["new"] == 0 and XS.pending(st2, "pc2", everyone=True) == []


def test_detersivo_order_written_with_deter_like_colleagues():
    d0 = datetime(2026, 11, 3)
    d, x, st = _store([("1", d0, "Petrillo", 900.0, "DETER", "DETER", None)])
    oid = _draft(st, "wholesale", "1", "PETRILLO", 1322.15, date(2026, 11, 22))
    MS.mark_sent(st, [oid], MAIL, today=date(2026, 11, 22), buyer="LUCAS")
    assert st.orders[oid]["kind"] == "deter" and st.orders[oid]["supplier"] == "Petrillo"
    assert XS.writeback(st, x, "pc1")["status"] == "ok"
    ws, r = _cells(x, st.orders[oid]["ref"])
    assert ws.cell(r, 5).value == "DETER" and ws.cell(r, 6).value == "DETER"
    st2 = OT.Store(d, me="pc2", host="h2").reload()
    res = OT.import_file(st2, x)
    assert res["new"] == 0 and XS.pending(st2, "pc2", everyone=True) == []
    assert OT.status(st2.orders[oid]) == "deter"


def test_hand_written_copy_of_registered_order_is_reported():
    d0 = datetime(2026, 11, 3)
    d, x, st = _store([("1", d0, "SAIS", 50.0, None, None, None)])
    oid = _draft(st, "store_orders", "1", "BIACCHI", 300.0, date(2026, 11, 25))
    OT.confirm_drafts(st, [oid], today=date(2026, 11, 26), buyer="LUCAS")
    assert XS.writeback(st, x, "pc1")["status"] == "ok"
    # 同事不知道，又手填了一行一样的
    wb = load_workbook(x)
    ws = wb["NOV"]
    r = next(r for r in range(4, 40) if ws.cell(r, 2).value is None)
    for i, v in enumerate((datetime(2026, 11, 26), "Biacchi", "LUCAS", 300.0)):
        ws.cell(r, 1 + i).value = v
    wb.save(x)
    import os
    import time
    os.utime(x, (time.time() + 60,) * 2)
    res = OT.import_file(st, x)
    assert len(res["hand_dups"]) == 1 and res["hand_dups"][0][1] == oid
    assert st.orders[res["hand_dups"][0][0]]["ref"] == f"NOV!B{r}"


def test_buyer_spellings_merged():
    d0 = datetime(2026, 11, 3)
    d, x, st = _store([("1", d0, "A", 1.0, None, None, None)])
    for i, b in enumerate(["Pres.", "Pres", "pres.", "Pres.", "Elena"]):
        st.add({"date": "2026-11-10", "store": "3", "supplier": f"S{i}", "buyer": b, "origin": "excel"})
    names = OT.buyers(st, today=date(2026, 11, 28))
    assert names[0] == "Pres." and "Pres" not in names and "Elena" in names


def test_piano_never_gets_purchasing_dept_and_learns_colleague_names():
    """2026-11-28 用户：PIANO 表里不要用「采购部」，学同事的写法"""
    d0 = datetime(2026, 11, 10)
    d, x, st = _store([("1", d0, "Facco", 100.0, None, None, None),
                       ("1", d0, "Facco", 120.0, None, None, None),
                       ("3", d0, "SAIS", 50.0, None, None, None)])
    for i, b in enumerate(["Monica", "Monica", "Pamela"]):
        st.add({"date": "2026-11-12", "store": "3", "supplier": f"MELONI{i and ' WALTER' or ''}", "buyer": b,
                "origin": "excel"})
    assert not OT.is_buyer_name("采购部") and not OT.is_buyer_name("XH") and OT.is_buyer_name("Pres.")
    assert OT.piano_buyer(st, "lucas") == "LUCAS" and OT.piano_buyer(st, "Giulia") == "Giulia"
    oid = _draft(st, "wholesale", "3", "MELONI", 400.0, date(2026, 11, 25))
    st.me = "采购部"
    OT.confirm_drafts(st, [oid], today=date(2026, 11, 26))               # 没选下单人：照同事平时谁下这家店这家的单
    assert st.orders[oid]["buyer"] == "Monica"
    o2 = _draft(st, "store_orders", "1", "FACCO", 300.0, date(2026, 11, 25))
    MS.mark_sent(st, [o2], MAIL, today=date(2026, 11, 26), buyer="采购部")
    assert st.orders[o2]["buyer"] == "LUCAS"
    import send_order_dialog as SD
    assert SD.checked_buyer(st, "采购部") is None and SD.checked_buyer(st, "pamela") == "Pamela"
