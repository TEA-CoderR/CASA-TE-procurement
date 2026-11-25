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


# ---------------------------------------------------------------- 邮件里的批发商价目表 → 2 供应商报价
def _pm(frm, subj, atts, when, to="", mailbox="info@bmspsrl.com", mid=None, uid="7"):
    return {"mailbox": mailbox, "folder": "INBOX", "folder_raw": "INBOX", "from": frm, "from_name": "", "to": to,
            "subject": subj, "attachments": atts, "parts": [[str(i + 2), a, "BASE64", 50000] for i, a in enumerate(atts)],
            "message_id": mid or f"<{subj}@{when}>", "in_reply_to": "", "date": when, "uid": uid}


def test_price_list_mails_found_per_wholesaler():
    import mail_prices as MP
    own = {"info@bmspsrl.com"}
    labels = ["MEGA CASH", "MELONI", "NEW GRIECO", "PETRILLO"]
    mails = [
        # 我们发 Meloni 的订单给他 → 认得他（他发的价目表主题里没写 Meloni）
        _pm("info@bmspsrl.com", "Ordine Meloni 4PDV", ["Ordine Meloni Foiano 0111.xlsx"], datetime(2026, 11, 1, 15),
            to="Alessandro <alessandro.gianchecchi78@gmail.com>"),
        _pm("alessandro.gianchecchi78@gmail.com", "Fwd: Listino 1 con offerta", ["image001.jpg", "Listino_01 - Offerta.xlsx"],
            datetime(2026, 10, 29, 17, 51)),
        _pm("alessandro.gianchecchi78@gmail.com", "Meloni listino", ["Listino_01 - Offerta.xlsx"], datetime(2026, 11, 5, 14, 48)),
        _pm("andrea.comegna2@gmail.com", "AGGIORNAMENTO LISTINO NEW GRIECO CON OFFERTE", ["Canvass 46 New Grieco.xls"],
            datetime(2026, 11, 5, 22, 15)),
        _pm("francesco.fratellirana@gmail.com", "*** Listini F.lli Petrillo Dal 03/11 - Al 09/11 ***", ["Listino.xls"],
            datetime(2026, 11, 2, 9, 0)),
        # 别的供应商的价目表、没有 Excel 的都不算
        _pm("infodeambrosi@gmail.com", "LISTINI GIRMI/G3F AGGIORNATI", ["Listino GIRMI.xlsx"], datetime(2026, 11, 3)),
        _pm("robertorota.lucca@gmail.com", "LISTINO BIACCHI ETTORE", ["LISTINO BIACCHI.pdf"], datetime(2026, 11, 5)),
    ]
    cur = {"MELONI": ("Listino_01 - Offerta.xlsx", datetime(2026, 10, 30, 9)),
           "NEW GRIECO": ("Canvass 46 New Grieco.xls", datetime(2026, 11, 6, 8)),     # 已经手动放过了
           "PETRILLO": ("petrillo.xls", datetime(2026, 10, 27)), "MEGA CASH": ("formattato.xlsx", datetime(2026, 11, 1))}
    found = {b["label"]: b for b in MP.find(mails, labels, own, current=cur)}
    assert set(found) == {"MELONI", "NEW GRIECO", "PETRILLO"}
    assert found["MELONI"]["mail"]["subject"] == "Meloni listino" and found["MELONI"]["part"][1] == "Listino_01 - Offerta.xlsx"
    assert found["MELONI"]["newer"] and found["PETRILLO"]["newer"] and not found["NEW GRIECO"]["newer"]
    done = [found["MELONI"]["mail"]["message_id"]]
    assert not {b["label"]: b for b in MP.find(mails, labels, own, done=done, current=cur)}["MELONI"]["newer"]


def test_import_price_list_replaces_old_and_can_undo(tmp_path, monkeypatch):
    import base64
    import commands as CMD
    import workspace as W
    from test_mail_client import FakeIMAP
    monkeypatch.setattr("settings.app_dir", lambda: str(tmp_path / "app"))
    monkeypatch.setattr(CMD, "mail_password", lambda a: "good")
    root = str(tmp_path / "ws")
    W.ensure(root)
    old = os.path.join(W.price_dir(root), "petrillo vecchio.xls")
    with open(old, "wb") as f:
        f.write(b"OLD" * 100)
    content = b"NEWLISTINO" * 100

    class F(FakeIMAP):
        def uid(self, cmd, *args):
            if cmd == "FETCH" and "BODY.PEEK[2]" in args[1]:
                self.calls.append(("uid", cmd) + args)
                return "OK", [(b"1 (UID 7 BODY[2] {%d}" % len(content), base64.b64encode(content)), b")"]
            return super().uid(cmd, *args)
    made = []
    settings = {"mail": {"accounts": [{"address": "info@bmspsrl.com"}]}, "supplier_names": {"petrillo vecchio.xls": "PETRILLO"}}
    pick = {"label": "PETRILLO", "current": "petrillo vecchio.xls", "part": ["2", "Listino 03-11.xls", "BASE64", 1000],
            "mail": _pm("francesco.fratellirana@gmail.com", "Listini F.lli Petrillo", ["Listino 03-11.xls"], datetime(2026, 11, 2))}
    done, undo, skipped = CMD.mail_import_prices(settings, root, [pick], factory=lambda h, p: made.append(F(h, p)) or made[-1],
                                                 check=lambda p: 500)
    assert skipped == []
    assert done == [("Listino 03-11.xls", "petrillo vecchio.xls")]
    folder = W.price_dir(root)
    assert sorted(f for f in os.listdir(folder) if f.endswith(".xls")) == ["Listino 03-11.xls"]
    assert open(os.path.join(folder, "Listino 03-11.xls"), "rb").read() == content
    assert settings["supplier_names"] == {"Listino 03-11.xls": "PETRILLO"}         # 名称带到新文件
    assert all(c[2] is True for c in made[0].calls if c[0] == "select")            # 只读打开
    assert pick["mail"]["message_id"] in CMD._prices_done()
    CMD.mail_undo_prices(settings, undo)
    assert sorted(f for f in os.listdir(folder) if f.endswith(".xls")) == ["petrillo vecchio.xls"]
    assert settings["supplier_names"] == {"petrillo vecchio.xls": "PETRILLO"}
    assert pick["mail"]["message_id"] not in CMD._prices_done()


def test_attachment_parts_sections():
    import mail_client as MC
    nested = (b'((("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 20 1 NIL NIL NIL)("TEXT" "HTML" ("CHARSET" "utf-8") NIL NIL'
              b' "7BIT" 20 1 NIL NIL NIL) "ALTERNATIVE" ("BOUNDARY" "a") NIL NIL)("IMAGE" "JPEG" ("NAME" "image001.jpg") "<x>"'
              b' NIL "BASE64" 300 NIL ("INLINE" ("FILENAME" "image001.jpg")) NIL)("APPLICATION" "VND.MS-EXCEL" ("NAME"'
              b' "Canvass 46 New Grieco.xls") NIL NIL "BASE64" 2600000 NIL ("ATTACHMENT" ("FILENAME" "Canvass 46 New Grieco.xls"))'
              b' NIL) "MIXED" ("BOUNDARY" "b") NIL NIL)')
    bs, _ = MC._parse(MC._tokens([("txt", nested)]))
    assert MC.attachment_parts(bs) == [["2", "image001.jpg", "BASE64", 300], ["3", "Canvass 46 New Grieco.xls", "BASE64", 2600000]]


def test_unreadable_price_list_is_not_put_in(tmp_path, monkeypatch):
    """邮件里的原文件比价读不了（表头不一样）→ 不放进去，旧的留着（2026-11-07 用户第一次用就遇到）"""
    import base64
    import pytest
    import commands as CMD
    import workspace as W
    from test_mail_client import FakeIMAP
    monkeypatch.setattr("settings.app_dir", lambda: str(tmp_path / "app"))
    monkeypatch.setattr(CMD, "mail_password", lambda a: "good")
    root = str(tmp_path / "ws")
    W.ensure(root)
    old = os.path.join(W.price_dir(root), "Listino_01 - Offerta.xlsx")
    with open(old, "wb") as f:
        f.write(b"OLD" * 100)

    class F(FakeIMAP):
        def uid(self, cmd, *args):
            if cmd == "FETCH" and "BODY.PEEK[2]" in args[1]:
                return "OK", [(b"1 (UID 7 BODY[2] {4}", base64.b64encode(b"RAW" * 100)), b")"]
            return super().uid(cmd, *args)

    def check(p):
        if open(p, "rb").read(3) == b"RAW":
            raise ValueError("Listino_01 - Offerta.xlsx 缺少必要列")
        return 900
    settings = {"mail": {"accounts": [{"address": "info@bmspsrl.com"}]}, "supplier_names": {}}
    pick = {"label": "MELONI", "current": "Listino_01 - Offerta.xlsx", "part": ["2", "Listino_01 - Offerta.xlsx", "BASE64", 1000],
            "mail": _pm("a@gmail.com", "Meloni listino", ["Listino_01 - Offerta.xlsx"], datetime(2026, 11, 5))}
    with pytest.raises(RuntimeError, match="没有放进去"):
        CMD.mail_import_prices(settings, root, [pick], factory=F, check=check)
    assert open(old, "rb").read() == b"OLD" * 100
    assert pick["mail"]["message_id"] not in CMD._prices_done()


def test_missing_order_uses_spelling_from_the_mail():
    """Excel 里有手误的写法（MARLYAMARCHETTI ANGIOLO）：补登记用和信里最对得上的写法"""
    import mail_match as MM
    import order_tracking as OT

    class FakeSt:
        orders = {"a": {"supplier": "MARLYAMARCHETTI ANGIOLO"}, "b": {"supplier": "Marchetti Angiolo"},
                  "c": {"supplier": "MAGIC"}, "d": {"supplier": "MAGIC MACCIONI SAS"}}

        @staticmethod
        def canon(k):
            return k
    idx = MM.SupplierIndex(FakeSt())
    t = "Ordine MARCHETTI ANGIOLO S.Filippo 0311"
    assert idx.best_name(idx.find(t), t) == "Marchetti Angiolo"
    t = "Ordine MAGIC MACCIONI BMS 0511"
    assert idx.best_name(idx.find(t), t) == "MAGIC MACCIONI SAS"
    assert OT.sup_key("x") is not None


def test_ddt_store_from_body_and_unsure_otherwise(tmp_path):
    """2026-11-07 用户：一张 DDT 只管一家店，门店写在正文「Ragione sociale」；说不清的默认不勾；
    确认信不配到还没发出去的待登记上（Royal 8044 = BMS 那张）"""
    st = _st(tmp_path)
    for code in ("1", "2", "4"):
        st.set("s" + code, {"date": "2026-10-20", "store": code, "supplier": "SAIS", "confirmed": "2026-10-20"})
    st.set("rb", {"date": "2026-11-05", "store": "BMS", "supplier": "ROYAL"})
    for code in ("1", "2", "4"):
        st.set("r" + code, {"date": "2026-11-04", "store": code, "supplier": "ROYAL OFFICE SRL", "draft": True,
                            "origin": "store_orders"})
    ddt = _mail(**{"from": "notifiche.sais@gmail.com", "subject": "DDT SAIS 0028677 del 21-10-2026",
                   "date": datetime(2026, 10, 21, 13, 0), "uid": 7})
    pre = _mail(**{"from": "support@synaptico.com", "subject": "Preordine Nr. 8044 del 05/11/2026 - Royal Office Srl",
                   "date": datetime(2026, 11, 5, 13, 0)})
    res = MM.analyze([ddt, pre], st, OWN)
    sais = [s for s in res["suggest"] if s["supplier"] == "SAIS"]
    # 2026-11-25 一一关联：几家店都对得上 → 不给建议，进「待处理邮件」，先读正文 / 附件
    assert sais == []
    pend = [p for p in res["pending"] if p["supplier"].upper().startswith("SAIS")]
    assert len(pend) == 1 and sorted(st.orders[o]["store"] for o in pend[0]["candidates"]) == ["1", "2", "4"]
    assert any(e["row"] is ddt for e in res["events"] if e.get("need_text"))
    royal = [s for s in res["suggest"] if "ROYAL" in s["supplier"]]
    assert [(s["store"], s["field"]) for s in royal] == [("BMS", "confirmed")]
    # 正文里写了收货门店 → 只配 FOIANO，不再「不确定」
    ddt["body"] = ("Codice cliente di spedizione: 207569\nRagione sociale: CASA & TE S.R.L. - FOIANO\n"
                   "DDT numero: 0028677 del 21/10/2026\nPer supporto contattare info@sais.it")
    res = MM.analyze([ddt, pre], st, OWN)
    sais = [s for s in res["suggest"] if s["supplier"] == "SAIS"]
    assert [(s["store"], s["unsure"]) for s in sais] == [("1", False)]
    assert MM.stores_in_body("Ragione sociale: CASA & TE SAN MARCO S.R.L") == ["4"]
    assert MM.stores_in_body("Destinazione: FOIANO\nSedi: Foiano, Marlia, S.Marco") == ["1"]
    assert MM.stores_in_body("Grazie, a presto") == []


def test_body_text_from_raw_mail():
    import mail_client as MC
    raw = (b"From: a@b.it\r\nSubject: DDT\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=XX\r\n\r\n"
           b"--XX\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Ragione sociale: <b>CASA &amp; TE SAN MARCO</b></p>\r\n"
           b"--XX\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=a.pdf\r\n\r\nAAAA\r\n--XX--\r\n")
    t = MC.body_text(raw)
    assert "CASA & TE SAN MARCO" in t and "AAAA" not in t


def test_order_mail_tab_finds_events_sent_mail_and_replies():
    """订单窗口「邮件」页：单上记的事件 + 程序发的那封 + 回信，同一封信两份只留一份，缓存里没有的也列出来"""
    import mail_match as MM
    sent = {"mailbox": "info@casa-te.com", "folder": "已发送", "uid": 1, "message_id": "<a@x>", "subject": "Ordine SAIS Foiano 0311",
            "from": "info@casa-te.com", "date": "2026-11-03T09:00:00", "parts": [["2", "Ordine.xlsx", "BASE64", 9000]]}
    copy = dict(sent, mailbox="INFO@BMSPSRL.COM", folder="INBOX", uid=7, parts=None)       # 抄送收件箱里的同一封
    conf = {"mailbox": "info@casa-te.com", "folder": "INBOX", "uid": 2, "message_id": "<b@sais>", "in_reply_to": "<a@x>",
            "subject": "R: Ordine SAIS Foiano 0311", "from": "ordini@sais.it", "date": "2026-11-04T10:30:00"}
    ddt = {"mailbox": "info@casa-te.com", "folder": "INBOX", "uid": 3, "message_id": "<c@sais>",
           "subject": "DDT 0028677", "from": "ddt@sais.it", "date": "2026-11-06 08:15:00"}
    other = {"mailbox": "info@casa-te.com", "folder": "INBOX", "uid": 4, "message_id": "<d@x>", "subject": "Fattura",
             "from": "x@y.it", "date": "2026-11-05T08:00:00"}
    o = {"sent": {"mid": "<a@x>", "subject": "Ordine SAIS Foiano 0311", "at": "2026-11-03T09:00:00", "from": "info@casa-te.com"},
         "mails": [{"k": "order", "d": "2026-11-03 09:00", "s": "Ordine SAIS Foiano 0311", "f": "info@casa-te.com", "id": "<a@x>"},
                   {"k": "ship", "d": "2026-11-06 08:15", "s": "DDT 0028677", "f": "ddt@sais.it"},            # 老事件没有 id
                   {"k": "confirm", "d": "2026-09-01 10:00", "s": "Conferma vecchia", "f": "a@b.it"}]}       # 不在缓存里
    rows = MM.related(o, [copy, sent, conf, ddt, other])
    assert [(r["kind"], r["subject"]) for r in rows] == [
        ("confirm", "Conferma vecchia"), ("order", "Ordine SAIS Foiano 0311"), ("reply", "R: Ordine SAIS Foiano 0311"),
        ("ship", "DDT 0028677")]
    assert rows[0]["mail"] is None and rows[1]["mail"]["parts"]              # 有附件段号的那份
    assert rows[3]["mail"]["uid"] == 3


def test_excel_sync_absorbs_drafts_already_written_by_hand(tmp_path):
    """2026-11-09：Excel 里已经手填了的单，程序的「待登记」同步时自动合并；DETERSIVO 的对 DETER 那几行，别的不混"""
    st = _st(tmp_path)
    day = date.today()
    OT.register_drafts(st, "store_orders", [{"store": "1", "supplier": "FACCO GIUSEPPE & C. SPA", "amount": 874.9,
                                             "file": r"D:\x\Ordine_FACCO.xlsx"},
                                            {"store": "1", "supplier": "MELONI", "amount": 50.0}])   # 不是 DETERSIVO 的单
    OT.register_drafts(st, "wholesale", [{"store": "1", "supplier": "MELONI", "amount": 1738.9}])
    st.set("xF", {"date": day.isoformat(), "store": "1", "supplier": "FACCO", "amount": 879.7, "origin": "excel"})
    st.set("xM", {"date": day.isoformat(), "store": "1", "supplier": "Meloni", "amount": 2258.75, "origin": "excel",
                  "kind": "deter"})
    n, _undo, pairs = MM.absorb_drafts(st)
    assert n == 2 and sorted(b for _a, b in pairs) == ["xF", "xM"]
    st.reload()
    assert st.orders["xF"]["amount"] == 879.7 and st.orders["xF"]["file"].endswith("Ordine_FACCO.xlsx")
    left = [o for o in st.orders.values() if o.get("draft")]
    assert len(left) == 1 and left[0]["amount"] == 50.0
    # 重新配货不再生成合并掉的待登记
    r = OT.register_drafts(st, "store_orders", [{"store": "1", "supplier": "FACCO GIUSEPPE & C. SPA", "amount": 900.0},
                                                 {"store": "1", "supplier": "MELONI", "amount": 50.0}])
    assert r["new"] == 0
