"""旧的待登记作废（2026-11-26 用户：谨慎再谨慎）"""
import os
from datetime import date, timedelta

import order_tracking as OT

D1 = date(2026, 11, 4)


def _st(tmp, me="a", host="PC1"):
    os.makedirs(os.path.join(tmp, OT.DATA_DIR), exist_ok=True)
    return OT.Store(str(tmp), me=me, host=host).reload()


def _reg(st, day, items, source="store_orders", scope=None):
    return OT.register_drafts(st, source, items, in_scope=scope, today=day)


def _ids(st, **kw):
    return {oid for oid, o in st.orders.items() if all(o.get(k) == v for k, v in kw.items())}


def test_newer_run_of_same_supplier_voids_older_unsent_drafts():
    import tempfile
    tmp = tempfile.mkdtemp()
    st = _st(tmp)
    _reg(st, D1, [{"store": "1", "supplier": "METALTEX", "amount": 100}, {"store": "BMS", "supplier": "METALTEX", "amount": 50},
                  {"store": "2", "supplier": "FAR", "amount": 70}])
    old = _ids(st, supplier="METALTEX")
    res = _reg(st, D1 + timedelta(days=1), [{"store": "BMS", "supplier": "METALTEX", "amount": 80}],
               scope=lambda o: OT.sup_key(o.get("supplier")) == OT.sup_key("METALTEX"))
    assert res["voided"] == 2                         # 前一天直送门店 1 那张也算被取代：新的配货里门店 1 不订
    st2 = _st(tmp)                                    # 重新读也一样（别的电脑看到的）
    for oid in old:
        assert OT.status(st2.orders[oid]) == "void"
        assert not OT.is_draft(st2.orders[oid])
    far = next(iter(_ids(st2, supplier="FAR")))
    assert OT.status(st2.orders[far]) == "draft"      # 别家供应商不动
    rs = OT.rows(st2, today=D1 + timedelta(days=1))
    assert sum(1 for r in rs if r["status"] == "draft") == 2
    assert all(r["status"] in OT.NOT_ORDERED for r in rs)


def test_touched_sent_or_mailed_drafts_never_voided():
    import tempfile
    st = _st(tempfile.mkdtemp())
    _reg(st, D1, [{"store": "1", "supplier": "A", "amount": 1}, {"store": "2", "supplier": "A", "amount": 2},
                  {"store": "3", "supplier": "A", "amount": 3}, {"store": "4", "supplier": "A", "amount": 4}])
    by = {st.orders[o]["store"]: o for o in st.orders}
    st.set(by["1"], {"note": "aspetta"})                                     # 人改过
    st.set(by["2"], {"sent": {"at": "2026-11-04T10:00"}}, src="auto")         # 发过邮件
    st.set(by["3"], {"mails": [{"id": "<x>"}]}, src="mail")                   # 关联过邮件
    out = OT.stale_drafts(st, today=D1 + timedelta(days=30))
    assert [x["id"] for x in out] == [by["4"]]


def test_expired_after_keep_days_and_zero_disables():
    import tempfile
    st = _st(tempfile.mkdtemp())
    _reg(st, D1, [{"store": "1", "supplier": "A", "amount": 1}])
    assert OT.stale_drafts(st, today=D1 + timedelta(days=OT.DRAFT_KEEP_DAYS)) == []
    out = OT.stale_drafts(st, today=D1 + timedelta(days=OT.DRAFT_KEEP_DAYS + 1))
    assert [x["why"] for x in out] == ["expired"]
    assert OT.stale_drafts(st, today=D1 + timedelta(days=90), keep_days=0) == []


def test_wholesale_scope_is_the_store_not_the_supplier():
    import tempfile
    st = _st(tempfile.mkdtemp())
    _reg(st, D1, [{"store": "1", "supplier": "MELONI", "amount": 1}, {"store": "1", "supplier": "PETRILLO", "amount": 1}],
         source="wholesale", scope=lambda o: o.get("store") == "1")
    _reg(st, D1 + timedelta(days=2), [{"store": "2", "supplier": "MELONI", "amount": 1}],
         source="wholesale", scope=lambda o: o.get("store") == "2")
    assert all(OT.is_draft(o) for o in st.orders.values())       # 别的门店的比价不取代这家店的
    res = _reg(st, D1 + timedelta(days=7), [{"store": "1", "supplier": "MELONI", "amount": 2}],
               source="wholesale", scope=lambda o: o.get("store") == "1")
    assert res["voided"] == 2                                     # 下一周同一家店重新比价：上周两张都被取代（PETRILLO 这次不订）


def test_confirming_one_store_later_does_not_supersede_its_sibling():
    """确认下单会把下单日期改成发出那天：比的是第一次生成的日期，同一批的另一张不算被取代"""
    import tempfile
    st = _st(tempfile.mkdtemp())
    _reg(st, D1, [{"store": "1", "supplier": "A", "amount": 1}, {"store": "2", "supplier": "A", "amount": 2}])
    by = {st.orders[o]["store"]: o for o in st.orders}
    OT.confirm_drafts(st, [by["1"]], today=D1 + timedelta(days=3))
    assert OT.stale_drafts(st, today=D1 + timedelta(days=3)) == []


def test_restore_keeps_it_and_regenerating_same_day_unvoids():
    import tempfile
    st = _st(tempfile.mkdtemp())
    _reg(st, D1, [{"store": "1", "supplier": "A", "amount": 1}])
    oid = next(iter(st.orders))
    OT.void_drafts(st, OT.stale_drafts(st, today=D1 + timedelta(days=20)), today=D1 + timedelta(days=20))
    assert OT.status(st.orders[oid]) == "void"
    OT.restore_drafts(st, [oid])
    assert OT.status(st.orders[oid]) == "draft"
    assert OT.stale_drafts(st, today=D1 + timedelta(days=40)) == []          # 人拿回来的不再自动作废
    st2 = _st(__import__("tempfile").mkdtemp())
    _reg(st2, D1, [{"store": "1", "supplier": "A", "amount": 1}])
    o2 = next(iter(st2.orders))
    OT.void_drafts(st2, [{"id": o2, "why": "expired", "by": None}])
    _reg(st2, D1, [{"store": "1", "supplier": "A", "amount": 5}])          # 同一天又配了一次
    assert OT.status(st2.orders[o2]) == "draft" and st2.orders[o2]["amount"] == 5


def test_two_computers_send_wins_over_void():
    """A 电脑作废的时候还不知道 B 电脑已经发出去了 / B 后来发了 → 一律算发出去的单"""
    import tempfile, mail_send as MS
    tmp = tempfile.mkdtemp()
    a = _st(tmp, "a", "PCA")
    _reg(a, D1, [{"store": "1", "supplier": "A", "amount": 1}, {"store": "2", "supplier": "A", "amount": 2}])
    by = {a.orders[o]["store"]: o for o in a.orders}
    b = _st(tmp, "b", "PCB")
    MS.mark_sent(b, [by["1"]], {"to": ["x@y.it"], "from": "f@casa-te.com", "subject": "Ordine", "mid": "<m1>"},
                 today=D1 + timedelta(days=1))                 # B 先发出 1 号店那张
    OT.void_drafts(a, [{"id": by["1"], "why": "expired", "by": None}, {"id": by["2"], "why": "expired", "by": None}])
    MS.mark_sent(b, [by["2"]], {"to": ["x@y.it"], "from": "f@casa-te.com", "subject": "Ordine", "mid": "<m2>"},
                 today=D1 + timedelta(days=10))                # B 后来又发了 2 号店那张（不知道已被作废）
    st = _st(tmp, "c", "PCC")
    for s_ in ("1", "2"):
        o = st.orders[by[s_]]
        assert OT.status(o) == "ordered", (s_, o)
        assert not o.get("void")


def test_order_mail_finds_voided_draft_and_applying_restores_it():
    """作废的待登记其实用邮件软件发出去了：邮件核对能对上，应用后回到正常的单"""
    import tempfile, mail_match as MM
    st = _st(tempfile.mkdtemp())
    _reg(st, D1, [{"store": "1", "supplier": "METALTEX", "amount": 100}])
    oid = next(iter(st.orders))
    OT.void_drafts(st, [{"id": oid, "why": "expired", "by": None}])
    st.set(oid, {"draft": None, "date": "2026-11-06"}, src="mail")
    assert OT.status(st.orders[oid]) == "ordered" and not st.orders[oid].get("void")
    assert st.orders[oid].get("state") is None
    assert MM is not None


def test_old_orders_matched_to_receipts_once_and_registered_as_backfill():
    import tempfile, old_orders as OO
    st = _st(tempfile.mkdtemp())
    a = st.add({"date": "2026-09-02", "store": "1", "supplier": "COLZANI SRL", "amount": 400, "origin": "excel"})
    b = st.add({"date": "2026-09-10", "store": "1", "supplier": "Colzani", "amount": 300, "origin": "excel"})
    c = st.add({"date": "2026-09-05", "store": "BMS", "supplier": "SAIS", "amount": 900, "origin": "excel"})
    d = st.add({"date": "2026-08-01", "store": "2", "supplier": "NESSUNO", "amount": 10, "origin": "excel"})
    st.add({"date": "2026-11-20", "store": "1", "supplier": "Colzani", "amount": 5, "origin": "excel"})   # 这个月的不看
    rows = OO.candidates(st, date(2026, 11, 1), OT.rows(st, today=date(2026, 11, 26)))
    assert {r["id"] for r in rows} == {a, b, c, d}
    ids = OO.supplier_ids(rows, {11: "COLZANI S.R.L.", 22: "SAIS SPA", 33: "ALTRO"})
    k = {r["id"]: r["sup_key"] for r in rows}
    assert ids[k[a]] == [11] and ids[k[c]] == [22] and not ids[k[d]]
    rec = [{"sup": 11, "neg": 1, "day": date(2026, 9, 12), "n": 8, "q": 96, "v": 380.0},     # 一车货只算给一张单
           {"sup": 22, "neg": 0, "day": date(2026, 9, 9), "n": 20, "q": 300, "v": 880.0},
           {"sup": 22, "neg": 1, "day": date(2026, 9, 9), "n": 3, "q": 9, "v": 30.0}]        # 别的门店不算
    got = OO.match(rows, rec, ids)
    assert set(got) == {a, c} and got[c]["neg"] == 0
    found = [(r, got[r["id"]]) for r in rows if r["id"] in got]
    undo = OO.apply_arrived(st, found)
    o = st.orders[a]
    assert o["arrived"] == "2026-09-12" and o["receipts"][-1]["by"] == OO.BY and "96 件" in o["receipts"][-1]["note"]
    assert OT.status(st.orders[b]) != "arrived"
    st.set_many(undo)
    assert not st.orders[a].get("arrived")
    OO.keep_tracking(st, [b])
    OO.cancel(st, [d])
    left = OO.candidates(st, date(2026, 11, 1), OT.rows(st, today=date(2026, 11, 26)))
    assert {r["id"] for r in left} == {a, c}
