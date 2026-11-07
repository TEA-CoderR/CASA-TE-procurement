"""邮件 → 订单跟踪（mail_match）+ 读信头的缓存（mail_client.collect）"""
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mail_match as MM  # noqa: E402
import order_tracking as OT  # noqa: E402

OWN = {"info@bmspsrl.com", "infolucca@casa-te.com", "info.s.filippo@casa-te.com"}


def _st(tmp_path):
    return OT.Store(str(tmp_path), me="tester", host="pc").reload()


def _mail(**kw):
    m = {"mailbox": "info@bmspsrl.com", "folder": "INBOX", "from": "", "from_name": "", "to": "", "subject": "",
         "attachments": [], "message_id": "", "in_reply_to": "", "date": datetime(2026, 11, 3, 10, 0)}
    m.update(kw)
    return m


def test_kind_store_and_date_from_subject():
    assert MM.kind_of(_mail(**{"from": "info@bmspsrl.com", "subject": "Ordine SAIS Capannori 0311"}), OWN) == "order"
    assert MM.kind_of(_mail(**{"from": "info@bmspsrl.com", "subject": "Re: conferma ordine"}), OWN) is None
    assert MM.kind_of(_mail(**{"from": "x@lampa.it", "subject": "LAMPA - CONFERMA ORDINI"}), OWN) == "confirm"
    assert MM.kind_of(_mail(**{"from": "x@lampa.it", "subject": "Bolla  2026/01/78905"}), OWN) == "ship"
    assert MM.kind_of(_mail(**{"from": "x@sais.it", "subject": "R: Ordine SAIS S.Marco 2010"}), OWN) == "confirm"
    assert MM.kind_of(_mail(**{"from": "x@lampa.it", "subject": "Sollecito di pagamento fattura"}), OWN) is None
    assert MM.kind_of(_mail(**{"from": "info@bmspsrl.com", "subject": "RESO COSWELL 4PDV"}), OWN) is None   # 退货不算下单
    assert MM.stores_in("Ordine SAIS Capannori 0311") == ["2"]
    assert MM.stores_in("proposte ordini MORANDO - S. FILIPPO, S, MARCO, MARLIA") == ["2", "3", "4"]
    assert MM.stores_in("La Briantina - Conferma Ordine BMS PARTNERS SRL") == []         # 公司名，不是 BMS 仓库
    assert MM.stores_in("Ordine COSWELL BMS 0411") == ["BMS"]
    assert MM.subject_day("R: Ordine SAIS S.Marco 2010", date(2026, 11, 2)) == date(2026, 10, 20)
    assert MM.subject_day("Ordine n. 6446 del 04/11/2026", date(2026, 11, 4), full=True) == date(2026, 11, 4)
    assert MM.subject_day("DDT SAIS 0028677 del 21-10-2026", date(2026, 10, 21)) is None    # 只认完整日期时才看
    assert MM.default_store("info.s.filippo@casa-te.com") == "3"
    assert MM.default_store("infolucca@casa-te.com") == "2"
    assert MM.default_store("INFO@BMSPSRL.COM") is None


def test_sent_order_then_reply_then_ddt(tmp_path):
    st = _st(tmp_path)
    # 程序生成的待登记（11-03）+ Excel 手填的另一家（10-28，已下单）
    st.set("a1", {"date": "2026-11-03", "store": "2", "supplier": "SAIS", "draft": True, "origin": "store_orders",
                  "amount": 500.0})
    st.set("x1", {"date": "2026-10-28", "store": "3", "supplier": "LAMPA", "origin": "excel"})
    mails = [
        _mail(**{"mailbox": "infolucca@casa-te.com", "folder": "已发送", "from": "infolucca@casa-te.com",
                 "to": "matteo@sais.it", "subject": "Ordine SAIS Capannori 0311", "message_id": "<o1@casa-te.com>",
                 "attachments": ["Ordine SAIS Capannori 0311.xlsx"], "date": datetime(2026, 11, 3, 15, 0)}),
        # 供应商回复（主题没写门店、发件人只在我们发过订单的地址里）
        _mail(**{"mailbox": "infolucca@casa-te.com", "from": "matteo@sais.it", "subject": "R: Ordine SAIS Capannori 0311",
                 "in_reply_to": "<o1@casa-te.com>", "date": datetime(2026, 11, 4, 9, 0)}),
        # 发货：采购办公室邮箱、没写门店 → 最近的那一批单
        _mail(**{"from": "documenti@lampa.it", "subject": "Bolla 2026/01/74982", "date": datetime(2026, 11, 2, 8, 0)}),
    ]
    res = MM.analyze(mails, st, OWN, {"infolucca@casa-te.com": "2"})
    got = {(s["id"], s["field"]): s["value"] for s in res["suggest"]}
    assert got == {("a1", "draft"): "2026-11-03", ("a1", "confirmed"): "2026-11-04", ("x1", "shipped"): "2026-11-02",
                   ("x1", "confirmed"): "2026-11-02"}
    assert not res["missing"]
    n, undo = MM.apply(st, res["suggest"])
    assert n == 2
    a1, x1 = st.orders["a1"], st.orders["x1"]
    assert not a1.get("draft") and a1["confirmed"] == "2026-11-04" and a1["buyer"] == "tester"
    assert OT.status(a1) == "confirmed" and OT.status(x1) == "shipped" and x1["shipped"] == "2026-11-02"
    assert [m["k"] for m in a1["mails"]] == ["order", "confirm"]
    assert st.meta["a1"]["confirmed"][2] == "mail"                       # 修改记录里写着「邮件」
    assert "shipped" in OT.OPEN
    # 再核对一次：已经改过的不再提
    assert MM.analyze(mails, st.reload(), OWN, {"infolucca@casa-te.com": "2"})["suggest"] == []
    MM.undo(st, undo)
    st.reload()
    assert st.orders["a1"].get("draft") is True and not st.orders["a1"].get("confirmed")
    assert OT.status(st.orders["x1"]) == "ordered"


def test_ignored_and_deter_orders_left_alone(tmp_path):
    st = _st(tmp_path)
    st.set("d1", {"date": "2026-11-01", "store": "1", "supplier": "Meloni", "kind": "deter", "origin": "excel"})
    st.set("o1", {"date": "2026-11-01", "store": "1", "supplier": "Trevidea", "origin": "excel"})
    mails = [_mail(**{"from": "info@bmspsrl.com", "folder": "已发送", "subject": "Ordine MELONI 4PDV",
                      "date": datetime(2026, 11, 1, 10, 0)}),
             _mail(**{"from": "infodeambrosi@gmail.com", "subject": "CONFERMA ORDINE TREVIDEA",
                      "date": datetime(2026, 11, 2, 10, 0), "mailbox": "info@casa-te.com"})]
    res = MM.analyze(mails, st, OWN | {"info@casa-te.com"}, {"info@casa-te.com": "1"})
    assert [s["id"] for s in res["suggest"]] == ["o1"] and not res["missing"]     # DETERSIVO 批发商的单不管
    res = MM.analyze(mails, st, OWN | {"info@casa-te.com"}, {"info@casa-te.com": "1"}, ignored={"o1|confirmed"})
    assert res["suggest"] == []


def test_duplicate_drafts_merged_and_not_generated_again(tmp_path):
    st = _st(tmp_path)
    day = date.today()
    OT.register_drafts(st, "store_orders", [{"store": "1", "supplier": "F.LLI ZAMBETTI SRL", "amount": 320.5, "colli": 8,
                                             "file": r"D:\x\Ordine_ZAMBETTI.xlsx"}])
    st.set("x9", {"date": day.isoformat(), "store": "1", "supplier": "ZAMBETTI", "origin": "excel"})
    st.set("x8", {"date": day.isoformat(), "store": "2", "supplier": "ZAMBETTI", "origin": "excel"})   # 别的门店不算
    dups = MM.dup_suggestions(st)
    assert len(dups) == 1 and dups[0]["twin"] == "x9"
    did = dups[0]["id"]
    n, undo = MM.merge_duplicates(st, dups)
    assert n == 1 and did not in st.orders
    assert st.orders["x9"]["file"].endswith("Ordine_ZAMBETTI.xlsx") and st.orders["x9"]["amount"] == 320.5
    # 同一天重新配货：合并掉的不再生成
    r = OT.register_drafts(st, "store_orders", [{"store": "1", "supplier": "F.LLI ZAMBETTI SRL", "amount": 330.0}])
    assert r["new"] == 0 and did not in st.orders
    MM.undo(st, undo)
    st.reload()
    assert st.orders[did]["draft"] is True and st.orders["x9"].get("file") is None


def test_sent_order_missing_in_tracking_can_be_added(tmp_path):
    st = _st(tmp_path)
    st.set("o1", {"date": "2026-10-26", "store": "2", "supplier": "FAR", "origin": "excel"})
    st.set("o2", {"date": "2026-10-01", "store": "BMS", "supplier": "Gicos", "origin": "excel"})
    mails = [_mail(**{"mailbox": "infolucca@casa-te.com", "folder": "已发送", "from": "infolucca@casa-te.com",
                      "subject": "Ordine FAR Capannori 0211", "date": datetime(2026, 11, 2, 10, 0), "message_id": "<f@x>"}),
             _mail(**{"folder": "已发送", "from": "info@bmspsrl.com", "subject": "Ordine_GICOS_BMS_2110",
                      "date": datetime(2026, 10, 22, 10, 0), "message_id": "<g@x>"})]
    res = MM.analyze(mails, st, OWN, {"infolucca@casa-te.com": "2"})
    miss = {(s["store"], s["supplier"]): s for s in res["missing"]}
    assert set(miss) == {("2", "FAR"), ("BMS", "Gicos")}
    assert miss[("2", "FAR")]["near"] == date(2026, 10, 26)            # 附近已有一张，可能就是它
    assert miss[("BMS", "Gicos")]["value"] == "2026-10-21"              # 下单日期按主题里的 2110
    n, undo = MM.add_missing(st, [miss[("BMS", "Gicos")]])
    assert n == 1
    new = [o for o in st.orders.values() if o.get("origin") == "mail"]
    assert len(new) == 1 and new[0]["date"] == "2026-10-21" and OT.status(new[0]) == "ordered"
    MM.undo(st, undo)
    assert not [o for o in st.reload().orders.values() if o.get("origin") == "mail"]


# ---------------------------------------------------------------- 读信头：读过的不再向服务器要
def test_collect_uses_cache(tmp_path):
    import mail_client as MC
    from test_mail_client import FakeIMAP

    class F(FakeIMAP):
        def response(self, code):
            return code, [b"777"]
    made = []

    def factory(h, p):
        made.append(F(h, p))
        return made[-1]
    acc = {"address": "info@casa-te.com", "host": MC.DEFAULT_HOST, "port": 993, "store": "1"}
    rows, errors = MC.collect([acc], lambda a: "good", days=21, cache_dir=str(tmp_path), log=lambda *_: None,
                              factory=factory, today=date(2026, 11, 7))
    assert not errors and len(rows) == 2 and {r["folder"] for r in rows} == {"INBOX"}
    assert any(c[:2] == ("uid", "FETCH") for c in made[0].calls)
    rows2, _ = MC.collect([acc], lambda a: "good", days=21, cache_dir=str(tmp_path), log=lambda *_: None,
                          factory=factory, today=date(2026, 11, 7))
    assert not any(c[:2] == ("uid", "FETCH") for c in made[1].calls)       # 两封都在缓存里
    assert sorted(r["subject"] for r in rows2) == sorted(r["subject"] for r in rows)
    assert all(isinstance(r["date"], datetime) for r in rows2)
    assert all(c[2] is True for c in made[1].calls if c[0] == "select")     # 还是只读打开
