"""发订单邮件（mail_send + commands.send_order）：用哪个邮箱、主题、收件人、发完变已下单、不重发"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mail_send as MS  # noqa: E402
import order_tracking as OT  # noqa: E402

ACCS = [{"address": "info@casa-te.com", "host": "imap.qiye.aliyun.com", "port": 993, "store": "1"},
        {"address": "INFO@BMSPSRL.COM", "host": "imap.qiye.aliyun.com", "port": 993, "store": None},
        {"address": "infolucca@casa-te.com", "host": "imap.mxhichina.com", "port": 993, "store": "2"},
        {"address": "info.s.marco@casa-te.com", "host": "imap.qiye.aliyun.com", "port": 993, "store": "4"}]


@pytest.fixture(autouse=True)
def _own_cred_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CASATE_CRED_DIR", str(tmp_path / "cred"))


def _st(tmp_path):
    return OT.Store(str(tmp_path), me="tester", host="pc").reload()


def test_sender_subject_and_group_follow_current_habits(tmp_path):
    st = _st(tmp_path)
    f = str(tmp_path / "Ordine_SAIS_0711.xlsx")
    for code in ("BMS", "1", "2"):
        st.set("s" + code, {"date": "2026-11-07", "store": code, "supplier": "SAIS", "draft": True, "file": f})
    st.set("x2", {"date": "2026-11-07", "store": "2", "supplier": "LAMPA", "draft": True, "file": str(tmp_path / "L.xlsx")})
    ids = MS.group_of(st, "s2")
    assert ids == ["s1", "s2", "sBMS"]                       # 同一个订单文件的一起发，按门店排
    stores = MS.stores_of(st, ids)
    assert MS.subject_for("SAIS", stores, date(2026, 11, 7)) == "Ordine SAIS 3PDV 0711"
    assert MS.subject_for("LAMPA", ["2"], date(2026, 11, 3)) == "Ordine LAMPA Capannori 0311"
    assert MS.attachment_name("Ordine LAMPA Capannori 0311", "x/Ordine_LAMPA_0311.xlsx") == "Ordine LAMPA Capannori 0311.xlsx"
    assert MS.sender_for(["2"], ACCS)["address"] == "infolucca@casa-te.com"
    assert MS.sender_for(["5"], ACCS)["address"] == "INFO@BMSPSRL.COM"          # S.Concordio 没有自己的邮箱
    assert MS.sender_for(stores, ACCS)["address"] == "INFO@BMSPSRL.COM"
    assert MS.smtp_of(ACCS[2]) == ("smtp.mxhichina.com", 465)


def test_recipients_learned_from_sent_and_shared_contacts(tmp_path):
    st = _st(tmp_path)
    st.set("a", {"date": "2026-10-20", "store": "2", "supplier": "SAIS"})
    own = {a["address"].lower() for a in ACCS}
    mails = [{"from": "infolucca@casa-te.com", "mailbox": "infolucca@casa-te.com", "folder": "已发送", "uid": 5,
              "subject": "Ordine SAIS Capannori 2010", "attachments": ["Ordine SAIS Capannori 2010.xlsx"],
              "to": "g.r.e.g.r.e@tin.it, matteo.micheletti@sais.it, matteohe@casa-te.com, benamar@casa-te.com",
              "date": "2026-10-20 10:00:00"},
             {"from": "info@casa-te.com", "mailbox": "info@casa-te.com", "folder": "已发送", "uid": 9,
              "subject": "Ordine SAIS Foiano 2710", "attachments": [], "to": "matteo.micheletti@sais.it, matteohe@casa-te.com",
              "date": "2026-10-27 10:00:00"}]
    hist = MS.sent_history(mails, st, own)
    assert [h["uid"] for h in hist] == [9, 5]                  # 新的在前
    assert MS.usual_cc(hist) == ["benamar@casa-te.com", "matteohe@casa-te.com"]   # 一半以上的信都有
    assert MS.usual_cc(hist + [dict(hist[0], cc_in=[])] * 2) == ["matteohe@casa-te.com"]
    assert MS.recipients(st, "Sais", hist) == (["matteo.micheletti@sais.it"], "history")
    MS.save_contact(st, "SAIS", ["ordini@sais.it"])
    assert MS.recipients(st, "SAIS", hist) == (["ordini@sais.it"], "contacts")   # 人手确认过的优先
    t = MS.body_from_last("Buongiorno,\nin allegato Ordine SAIS Foiano 2710\nSaluti\n\nIl 20/10/2026 Mario ha scritto:\n> vecchio",
                          "Ordine SAIS Foiano 2710", "Ordine SAIS Foiano 0711", "SAIS")
    assert t == "Buongiorno,\nin allegato Ordine SAIS Foiano 0711\nSaluti"
    assert MS.split_addrs("a@b.it; c@d.it,  e@f.com") == ["a@b.it", "c@d.it", "e@f.com"]
    assert not MS.valid_address("mario@") and MS.valid_address("Mario.Rossi@sais.it")


class _SMTP:
    sent = []

    def __init__(self, host, port):
        self.host, self.port = host, port

    def login(self, u, p):
        assert p == "pw"

    def send_message(self, msg):
        _SMTP.sent.append(msg)

    def quit(self):
        pass


def test_send_order_marks_ordered_saves_contact_and_never_twice(tmp_path):
    import commands as CMD
    import credentials
    st = _st(tmp_path)
    f = tmp_path / "Ordine_SAIS_0711.xlsx"
    f.write_bytes(b"PK fake xlsx")
    for code in ("1", "2"):
        st.set("s" + code, {"date": "2026-11-07", "store": code, "supplier": "SAIS", "draft": True, "file": str(f),
                            "amount": 100.0})
    settings = {"mail": {"accounts": ACCS}}
    credentials.set("mail.info@bmspsrl.com", "pw")
    _SMTP.sent = []

    def no_imap(*a, **k):
        raise OSError("offline")
    res = CMD.send_order(settings, st, ["s1", "s2"], "INFO@BMSPSRL.COM", ["matteo@sais.it"], ["matteohe@casa-te.com"],
                         "Ordine SAIS 2PDV 0711", "Buongiorno", [(str(f), "Ordine SAIS 2PDV 0711.xlsx")],
                         smtp_factory=_SMTP, imap_factory=no_imap, today=date(2026, 11, 7))
    assert res["n"] == 2 and res["saved"] is False and len(_SMTP.sent) == 1
    msg = _SMTP.sent[0]
    assert msg["To"] == "matteo@sais.it" and msg["Cc"] == "matteohe@casa-te.com"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["Ordine SAIS 2PDV 0711.xlsx"]
    st.reload()
    for k in ("s1", "s2"):
        o = st.orders[k]
        assert not o.get("draft") and OT.status(o) == "ordered" and o["sent"]["to"] == ["matteo@sais.it"]
    assert MS.load_contacts(st)[st.canon(OT.sup_key("SAIS"))]["to"] == ["matteo@sais.it"]
    with pytest.raises(RuntimeError):                     # 发过的不再发
        CMD.send_order(settings, st, ["s1"], "INFO@BMSPSRL.COM", ["matteo@sais.it"], [], "x", "y", [],
                       smtp_factory=_SMTP, imap_factory=no_imap)
    with pytest.raises(RuntimeError):                     # 地址不对：什么都不发
        CMD.send_order(settings, st, ["s1"], "INFO@BMSPSRL.COM", ["matteo@"], [], "x", "y", [], force=True,
                       smtp_factory=_SMTP, imap_factory=no_imap)
    assert len(_SMTP.sent) == 1


def test_draft_uses_cached_sent_mail_without_network(tmp_path, monkeypatch):
    import json
    import commands as CMD
    import settings as SET
    monkeypatch.setattr(SET, "app_dir", lambda: str(tmp_path))
    (tmp_path / "cache" / "mail").mkdir(parents=True)
    rows = {"5": {"from": "infolucca@casa-te.com", "mailbox": "infolucca@casa-te.com", "folder": "已发送", "uid": 5,
                  "subject": "Ordine LAMPA Capannori 2010", "attachments": [], "date": "2026-10-20 10:00:00",
                  "to": "ordini@lampa.it", "cc": "matteohe@casa-te.com"}}
    (tmp_path / "cache" / "mail" / "lucca.json").write_text(json.dumps({"Sent": {"validity": 1, "rows": rows}}),
                                                             encoding="utf-8")
    st = _st(tmp_path / "tr")
    st.set("L2", {"date": "2026-11-07", "store": "2", "supplier": "Lampa", "draft": True, "amount": 80.5})
    d = CMD.order_mail_draft({"mail": {"accounts": ACCS}}, st, "L2", fetch_body=False, today=date(2026, 11, 7))
    assert d["ids"] == ["L2"] and d["from"] == "infolucca@casa-te.com"
    assert d["to"] == ["ordini@lampa.it"] and d["cc"] == ["matteohe@casa-te.com"]
    no = d["nos"]["L2"]
    assert no.startswith("MA-0711-") and d["subject"] == f"Ordine {no} · CASA&TE Capannori · Lampa"
    assert d["body_src"] == "template" and d["files"] == []


def test_smtp_test_logs_in_only_and_switches_to_other_aliyun_host():
    import smtplib
    import commands as CMD
    import mail_client as MC
    calls = []

    class OnlyOld:
        def __init__(self, host, port):
            self.host = host
            calls.append((host, port))

        def login(self, u, p):
            if self.host != "smtp.mxhichina.com":
                raise smtplib.SMTPAuthenticationError(535, b"auth failed")

        def send_message(self, msg):                 # 测试连接不许发信
            raise AssertionError("sent a mail")

        def quit(self):
            pass
    settings = {"mail": {"accounts": [dict(ACCS[0])]}}
    acc = MC.accounts(settings)[0]
    r = CMD._smtp_test(settings, acc, "pw", smtp_factory=OnlyOld)
    assert "smtp.mxhichina.com" in r and calls == [("smtp.qiye.aliyun.com", 465), ("smtp.mxhichina.com", 465)]
    acc = MC.accounts(settings)[0]
    assert acc["smtp"] == "smtp.mxhichina.com" and MS.smtp_of(acc) == ("smtp.mxhichina.com", 465)

    class Never(OnlyOld):
        def login(self, u, p):
            raise smtplib.SMTPAuthenticationError(535, b"auth failed")
    with pytest.raises(MC.MailError, match="SMTP"):
        CMD._smtp_test({"mail": {"accounts": [dict(ACCS[0])]}}, MC.accounts({"mail": {"accounts": [ACCS[0]]}})[0],
                       "pw", smtp_factory=Never)

    def blocked(h, p):
        raise OSError("timed out")
    with pytest.raises(MC.MailError, match="465"):
        CMD._smtp_test(settings, acc, "pw", smtp_factory=blocked)


def test_mail_body_and_attachment_cached_locally(tmp_path, monkeypatch):
    import commands as CMD
    import settings as SET
    import credentials
    monkeypatch.setattr(SET, "app_dir", lambda: str(tmp_path))
    credentials.set("mail.info@casa-te.com", "pw")
    m = {"mailbox": "info@casa-te.com", "folder": "INBOX", "folder_raw": "INBOX", "uid": 5}
    raw = (b"From: a@b.it\r\nSubject: x\r\nContent-Type: multipart/mixed; boundary=XX\r\n\r\n--XX\r\n"
           b"Content-Type: text/plain; charset=utf-8\r\n\r\nBuongiorno, confermiamo.\r\n--XX\r\n"
           b"Content-Type: application/pdf; name=ddt.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERg==\r\n--XX--\r\n")
    seen = []

    class IM:
        def __init__(self, h, p):
            pass

        def login(self, u, p):
            return "OK", [b""]

        def _simple_command(self, *a):
            return "OK", [b""]

        def select(self, f, readonly=False):
            assert readonly                                   # 只读
            return "OK", [b"1"]

        def uid(self, cmd, u, what):
            seen.append(what)
            assert "PEEK" in what                             # 不改已读
            if what == "(BODY.PEEK[2])":
                return "OK", [(b"5 (UID 5 BODY[2] {8}", b"JVBERg=="), b")"]
            return "OK", [(b"5 (UID 5 BODY[] {%d}" % len(raw), raw), b")"]

        def logout(self):
            pass

        def close(self):
            pass
    settings = {"mail": {"accounts": [dict(ACCS[0])]}}
    import mail_client as MC
    monkeypatch.setattr(MC, "_send_id", lambda *a, **k: None, raising=False)
    assert "confermiamo" in CMD.mail_view_body(settings, m, factory=IM)
    n = len(seen)
    assert "confermiamo" in CMD.mail_view_body(settings, m, factory=IM) and len(seen) == n     # 第二次不连网
    p = CMD.mail_attachment(settings, m, ["2", "ddt.pdf", "BASE64", 6], factory=IM)
    assert open(p, "rb").read() == b"%PDF" and os.path.basename(p) == "ddt.pdf"
    CMD.mail_attachment(settings, m, ["2", "ddt.pdf", "BASE64", 6], factory=IM)
    assert seen.count("(BODY.PEEK[2])") == 1


def test_draft_body_is_fixed_template_never_last_mail(tmp_path, monkeypatch):
    """2026-11-21：正文一律按订单数据填固定模板（订单号、送货地址、箱数、预约），不再照搬上一封信、也不连网"""
    import json
    import commands as CMD
    import settings as SET
    monkeypatch.setattr(SET, "app_dir", lambda: str(tmp_path))
    (tmp_path / "cache" / "mail").mkdir(parents=True)
    rows = {"5": {"from": "infolucca@casa-te.com", "mailbox": "infolucca@casa-te.com", "folder": "已发送", "uid": 5,
                  "subject": "Ordine LAMPA Capannori 2010", "attachments": [], "date": "2026-10-20 10:00:00",
                  "to": "ordini@lampa.it"}}
    (tmp_path / "cache" / "mail" / "lucca.json").write_text(json.dumps({"Sent": {"validity": 1, "rows": rows}}),
                                                             encoding="utf-8")
    st = _st(tmp_path / "tr")
    st.set("L2", {"date": "2026-11-07", "store": "2", "supplier": "LAMPA", "draft": True, "colli": 6})
    calls = []
    monkeypatch.setattr(CMD, "mail_view_body", lambda *a, **k: calls.append(1) or "vecchio testo")
    cfg = {"mail": {"accounts": ACCS}, "order_mail": {"booking_url": "https://prenota.casaete.top"}}
    d = CMD.order_mail_draft(cfg, st, "L2", fetch_body=True, today=date(2026, 11, 7))
    no = d["nos"]["L2"]
    assert calls == [] and not d["need_last"] and d["body_src"] == "template"
    assert f"ordine n. {no} del 07/11/2026" in d["body"] and "CASA & TE GROUP SRL" in d["body"]
    assert "Viale Europa 434" in d["body"] and "Colli ordinati: 6" in d["body"] and "prenota.casaete.top" in d["body"]
    assert "vecchio" not in d["body"]


def test_fixed_sender_per_store_and_bms_for_multi_store_file(tmp_path):
    """固定六个邮箱：一家店 = 这家店的邮箱，几家店一个文件 = BMS；没设这家就不能发（写清原因）"""
    import commands as CMD
    import mail_send as MS
    st = _st(tmp_path / "tr")
    st.set("F1", {"date": "2026-11-20", "store": "1", "supplier": "ACME", "draft": True, "file": "x.xlsx"})
    st.set("B1", {"date": "2026-11-20", "store": "BMS", "supplier": "ACME", "draft": True, "file": "x.xlsx", "colli": 4})
    st.set("M1", {"date": "2026-11-20", "store": "2", "supplier": "Lampa", "draft": True})
    accs = ACCS + [{"address": "ordine.foiano@casa-te.com"}, {"address": "ordini@bmspsrl.it"}]
    cfg = {"mail": {"accounts": accs},
           "order_mail": {"senders": {"1": "ordine.foiano@casa-te.com", "BMS": "ordini@bmspsrl.it"}}}
    d = CMD.order_mail_draft(cfg, st, "F1", fetch_body=False)
    assert d["from"] == "ordini@bmspsrl.it" and d["from_locked"] and set(d["ids"]) == {"F1", "B1"}
    nb = d["nos"]["B1"]
    assert d["subject"].startswith(f"Ordine {nb} · CASA&TE 2 PDV") and d["nos"]["F1"] in d["body"]
    assert "BMS PARTNERS SRL" in d["body"] and "CASA & TE SRL" in d["body"]
    import pytest
    with pytest.raises(RuntimeError, match="Capannori"):           # MARLIA 的发件邮箱没设：不能发
        CMD.order_mail_draft(cfg, st, "M1", fetch_body=False)
    assert MS.sender_problem(["1"], cfg, MC_accounts(cfg)) is None


def MC_accounts(cfg):
    import mail_client as MC
    return MC.accounts(cfg)


def test_order_no_kept_after_send_and_replies_matched_by_it(tmp_path):
    """发过的单记住订单号（下单日期变了也不换）；供应商回信 / DDT 主题里写了订单号 → 直接对到这张单"""
    import mail_send as MS
    import mail_match as MM
    st = _st(tmp_path / "tr")
    st.set("F1", {"date": "2026-11-18", "store": "1", "supplier": "ACME", "draft": True})
    no = MS.order_no("F1", st.orders["F1"])
    assert no.startswith("FO-1811-") and OT.find_order_nos(f"re: ordine {no.lower()} ok") == [no]
    MS.mark_sent(st, ["F1"], {"to": ["a@acme.it"], "from": "x@casa-te.com", "subject": f"Ordine {no} · CASA&TE Foiano · ACME"},
                 today=date(2026, 11, 20))
    o = st.orders["F1"]
    assert o["date"] == "2026-11-20" and o["no"] == no and MS.order_no("F1", o) == no
    # 另一张 ACME 的单（同一门店、日期更近），信写的是 F1 的号 → 只对 F1
    st.set("F2", {"date": "2026-11-21", "store": "1", "supplier": "ACME", "confirmed": None})
    mails = [{"subject": f"Conferma {no}", "from": "ordini@acme.it", "date": "2026-11-21 09:00:00",
              "mailbox": "x@casa-te.com", "folder": "INBOX", "attachments": [], "message_id": "<m1>"}]
    res = MM.analyze(mails, st, {"x@casa-te.com"}, today=date(2026, 11, 21))
    ev = [e for e in res["events"] if e["kind"] == "confirm"]
    assert ev and ev[0]["orders"] == ["F1"]


def test_deter_orders_one_mail_from_bms_with_each_store_address_boxes_amount(tmp_path, monkeypatch):
    """2026-11-22 用户：DETERSIVO 的单不要确认、不要预约，一家批发商一封信、五家店一起，统一 BMS 邮箱发；
    正文写每家店地址、卸货电话、箱数、金额；称呼用以前那封信的名字；附件 Ordine FOIANO MELONI 2211.xlsx"""
    import json
    import commands as CMD
    import settings as SET
    monkeypatch.setattr(SET, "app_dir", lambda: str(tmp_path))
    (tmp_path / "cache" / "mail").mkdir(parents=True)
    rows = {"578": {"from": "info@bmspsrl.com", "mailbox": "INFO@BMSPSRL.COM", "folder": "已发送", "uid": 578,
                    "subject": "Ordine MELONI 5PDV", "attachments": ["Ordine FOIANO MELONI 1511.xlsx"],
                    "date": "2026-11-15 15:10:00", "to": "alessandro.gianchecchi78@gmail.com",
                    "cc": "matteohe@casa-te.com, benamar@casa-te.com"}}
    (tmp_path / "cache" / "mail" / "bms.json").write_text(json.dumps({"已发送": {"validity": 1, "rows": rows}}),
                                                          encoding="utf-8")
    monkeypatch.setattr(CMD, "mail_view_body", lambda *a, **k: "Buonasera Alessandro, \nIn allegato trovi gli ordine")
    st = _st(tmp_path / "tr")
    for code, c, a in (("4", 118, 1399.83), ("1", 193, 2658.63), ("2", 143, 2285.2)):
        f = tmp_path / f"Ordine_{code}_MELONI_2211.xlsx"
        f.write_bytes(b"x")
        st.set("D" + code, {"date": "2026-11-22", "store": code, "supplier": "MELONI", "draft": True, "colli": c,
                            "amount": a, "file": str(f), "origin": "wholesale"})
    st.set("P1", {"date": "2026-11-22", "store": "1", "supplier": "PETRILLO", "draft": True, "origin": "wholesale"})
    st.set("O1", {"date": "2026-11-15", "store": "3", "supplier": "MELONI", "draft": True, "origin": "wholesale"})
    accs = ACCS + [{"address": "ordine.foiano@casa-te.com"}]
    cfg = {"mail": {"accounts": accs},
           "order_mail": {"senders": {"1": "ordine.foiano@casa-te.com", "BMS": "INFO@BMSPSRL.COM"}}}
    d = CMD.order_mail_draft(cfg, st, "D2", fetch_body=True, today=date(2026, 11, 22))
    assert d["ids"] == ["D1", "D2", "D4"] and d["from"] == "INFO@BMSPSRL.COM"     # 别的批发商 / 上一批的不算
    assert d["subject"] == "Ordine MELONI 3PDV 2211"
    assert d["to"] == ["alessandro.gianchecchi78@gmail.com"]
    assert d["cc"] == ["benamar@casa-te.com", "matteohe@casa-te.com", "lucas@casa-te.com"]
    b = d["body"]
    assert "Alessandro," in b.splitlines()[0] and "3 punti vendita" in b
    assert "FOIANO – CASA & TE SRL" in b and "Contatto scarico: 0575-1782904" in b
    assert "ordine n." not in b
    assert "Colli: 193 · Importo: € 2.658,63" in b and "Totale: 454 colli · € 6.343,66" in b
    assert "prenota" not in b and "conferm" not in b.lower()
    assert [n for _p, n, ok in d["files"]] == ["Ordine FOIANO MELONI 2211.xlsx", "Ordine MARLIA MELONI 2211.xlsx",
                                              "Ordine S.MARCO MELONI 2211.xlsx"]
    # 只有一家店订了也用 BMS 邮箱（不用门店自己的）
    d2 = CMD.order_mail_draft(cfg, st, "P1", fetch_body=False, today=date(2026, 11, 22))
    assert d2["from"] == "INFO@BMSPSRL.COM" and d2["subject"] == "Ordine PETRILLO FOIANO 2211"
    assert "lucas@casa-te.com" in d2["cc"]
    assert MS.greet_name("Buon pomeriggio Andrea, \nx") == "Andrea" and MS.greet_name("In allegato") is None


def test_no_second_copy_in_sent_when_server_already_saved_it():
    """2026-11-22 用户：发一封订单信「已发送」里有两封——阿里邮箱 SMTP 发信自己会存一份，程序再 APPEND 就重复了"""
    from email.message import EmailMessage

    class _Imap:
        def __init__(self, has):
            self.has, self.appended, self.searched = has, [], []

        def login(self, u, p):
            pass

        def _simple_command(self, *a):
            pass

        def list(self):
            return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "&XfJT0ZAB-"']

        def select(self, f, readonly=False):
            return "OK", [b"1"]

        def uid(self, cmd, *args):
            self.searched.append(args)
            return "OK", [b"57" if self.has else b""]

        def append(self, *a):
            self.appended.append(a)
            return "OK", []

        def close(self):
            pass

        def logout(self):
            pass

    acc = {"address": "info@bmspsrl.com", "host": "imap.x", "port": 993}
    for has, n_append in ((True, 0), (False, 1)):
        msg = EmailMessage()
        msg["Message-ID"] = "<abc@BMSPSRL.COM>"
        msg["Subject"] = "Ordine MELONI 5PDV 2211"
        msg.set_content("x")
        imap = _Imap(has)
        mid, saved = MS.send(acc, "pw", msg, smtp_factory=_SMTP, imap_factory=lambda h, p: imap, wait=(0, 0))
        assert saved and len(imap.appended) == n_append
        assert any("<abc@BMSPSRL.COM>" in " ".join(map(str, a)) for a in imap.searched)
