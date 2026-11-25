"""订单邮件一一关联（2026-11-25）：按收货抬头 / 地址认门店、对不上的进「待处理邮件」、人选了以后记住"""
import io
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mail_link as ML  # noqa: E402
import mail_match as MM  # noqa: E402
import order_tracking as OT  # noqa: E402

OWN = {"info@bmspsrl.com", "ordine.foiano@casa-te.com"}
KS = ML.keys(profiles={
    "1": {"name": "CASA & TE SRL", "address": "Via d'Arezzo 159/r, 52045 Foiano della Chiana (AR)"},
    "2": {"name": "CASA & TE GROUP SRL", "address": "Viale Europa 434, 55012 Capannori (LU)"},
    "3": {"name": "CASA & TE S.FILIPPO SRL", "address": "Via di Tiglio 1893, 55100 Lucca (LU)"},
    "4": {"name": "CASA & TE S.MARCO SRL", "address": "Via del Brennero 844, 55100 Lucca (LU)"},
    "5": {"name": "CASA & TE S.CONCORDIO SRL", "address": "Via Savonarola 50, 55100 Lucca (LU)"},
    "BMS": {"name": "BMS PARTNERS SRL", "address": "Via di Tiglio 1893, 55100 Lucca (LU)"}})


def test_store_from_delivery_name_and_address():
    # 开票的是 GROUP，送货地址是 S.MARCO：先看收货那几行
    t = ("Intestatario: CASA & TE GROUP S.R.L. Viale Europa 434 55012 Capannori\n"
         "Luogo di destinazione:\nCASA E TE S.MARCO SRL\nVIA DEL BRENNERO, 844\n55100 LUCCA LU\n")
    assert ML.store_of(t, KS)[0] == "4"
    # 只写了地址（没有抬头）
    assert ML.store_of("Consegna presso: Via d'Arezzo n. 159 - 52045 Foiano d.C.", KS)[0] == "1"
    # BMS 和 S.FILIPPO 同一个地址：抬头决定；只有地址说不清
    assert ML.store_of("Destinazione: BMS PARTNERS SRL Via di Tiglio 1893 Lucca", KS)[0] == "BMS"
    assert ML.store_of("Destinazione: CASA&TE S. FILIPPO SRL - Via di Tiglio 1893", KS)[0] == "3"
    code, cands = ML.store_of("Destinazione: Via di Tiglio 1893, 55100 Lucca", KS)
    assert code is None and set(cands) == {"3", "BMS"}
    assert ML.store_of("Grazie per l'ordine", KS) == (None, [])


def test_attachment_text_from_pdf_and_xlsx():
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["Destinazione", "CASA & TE S.CONCORDIO SRL"])
    wb.active.append(["Indirizzo", "Via Savonarola 50"])
    b = io.BytesIO()
    wb.save(b)
    t = ML.attachment_text("conferma.xlsx", b.getvalue())
    assert ML.store_of(t, KS)[0] == "5"
    assert ML.attachment_text("x.pdf", b"not a pdf") == ""
    assert ML.wanted([["2", "DDT.pdf", "BASE64", 1000], ["3", "foto.jpg", "BASE64", 10],
                      ["4", "big.pdf", "BASE64", 99 * 1024 * 1024]]) == [["2", "DDT.pdf", "BASE64", 1000]]


def _st(tmp_path):
    return OT.Store(str(tmp_path), me="t", host="h").reload()


def _mail(**kw):
    m = {"mailbox": "info@bmspsrl.com", "folder": "INBOX", "uid": 1, "to": "info@bmspsrl.com", "cc": "",
         "attachments": [], "message_id": "<m1@x>", "in_reply_to": "", "from_name": ""}
    m.update(kw)
    return m


def test_one_mail_one_order_pending_then_linked(tmp_path):
    st = _st(tmp_path)
    for code in ("1", "4"):
        st.set("s" + code, {"date": "2026-11-20", "store": code, "supplier": "SAIS"})
    ddt = _mail(**{"from": "ddt@sais.it", "subject": "DDT SAIS 0029001 del 23-11-2026",
                   "date": datetime(2026, 11, 23, 10, 0)})
    res = MM.analyze([ddt], st, OWN, ks=KS)
    assert res["suggest"] == [] and len(res["pending"]) == 1
    p = res["pending"][0]
    assert sorted(st.orders[o]["store"] for o in p["candidates"]) == ["1", "4"] and p["why"] == "many"
    # 附件里写了送到 S.MARCO → 只对这一张，有建议、不进待处理
    ddt.update(body="Gentile cliente", att_text="Luogo di destinazione\nCASA & TE S.MARCO SRL\nVia del Brennero 844",
               _texts=True)
    res = MM.analyze([ddt], st, OWN, ks=KS)
    assert res["pending"] == [] and {(s["id"], s["field"]) for s in res["suggest"]} == {("s4", "confirmed"),
                                                                                         ("s4", "shipped")}
    # 人在待处理里选了 FOIANO 那张 → 写上去，以后这封信就认这张（不再进待处理、也不给别的单建议）
    ddt.pop("att_text")
    ddt["_texts"] = False
    p = MM.analyze([ddt], st, OWN, ks=KS)["pending"][0]
    sugs = MM.link_suggestions(st, p, "s1")
    assert {s["field"] for s in sugs} == {"confirmed", "shipped"}
    MM.apply(st, sugs)
    assert st.orders["s1"]["shipped"] == "2026-11-23" and st.orders["s1"]["mails"][0]["id"] == "<m1@x>"
    res = MM.analyze([ddt], st, OWN, ks=KS)
    assert res["pending"] == [] and res["suggest"] == []
    # 没有要改的状态 → 只把信记到单上
    st.set("s9", {"date": "2026-11-20", "store": "5", "supplier": "SAIS", "confirmed": "2026-11-21",
                  "shipped": "2026-11-22"})
    p["_m"]["message_id"] = "<m2@x>"
    s9 = MM.link_suggestions(st, p, "s9")
    assert [s["field"] for s in s9] == ["link"]
    MM.apply(st, s9)
    assert st.orders["s9"]["mails"][0]["id"] == "<m2@x>" and st.orders["s9"]["shipped"] == "2026-11-22"


def test_fetch_texts_reads_body_and_attachment_read_only():
    import base64
    from test_mail_client import FakeIMAP
    import mail_client as MC
    pdf = open(os.path.join(os.path.dirname(__file__), "pypdf", "LICENSE"), "rb").read()   # 不是 PDF：读不出来就跳过
    raw = (b"Subject: DDT\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nLuogo di destinazione: CASA & TE S.MARCO SRL\r\n")

    class F(FakeIMAP):
        def uid(self, cmd, *args):
            if cmd == "FETCH" and "BODY.PEEK[]<0." in args[1]:
                self.calls.append(("uid", cmd) + args)
                return "OK", [(b"1 (UID 7 BODY[]<0> {%d}" % len(raw), raw), b")"]
            if cmd == "FETCH" and "BODY.PEEK[2]" in args[1]:
                self.calls.append(("uid", cmd) + args)
                return "OK", [(b"1 (UID 7 BODY[2] {%d}" % len(pdf), base64.b64encode(pdf)), b")"]
            return super().uid(cmd, *args)
    made = []
    row = {"folder": "INBOX", "folder_raw": "INBOX", "uid": 7, "parts": [["2", "ddt.pdf", "BASE64", 500]]}
    out = MC.fetch_texts({"address": "a@b.it", "host": "h", "port": 993}, "good", [row],
                         factory=lambda h, p: made.append(F(h, p)) or made[-1])
    t = out[("INBOX", "7")]
    assert "S.MARCO" in t["b"] and t["a"] == ""
    assert ML.store_of(t["b"], KS)[0] == "4"
    assert all(c[2] is True for c in made[0].calls if c[0] == "select")
