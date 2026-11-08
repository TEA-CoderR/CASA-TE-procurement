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
    assert d["subject"] == "Ordine Lampa Capannori 0711" and d["body_src"] == "template" and d["files"] == []


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


def test_draft_opens_without_network_and_reads_last_body_later(tmp_path, monkeypatch):
    """发订单窗口不用等邮箱：草稿只用本机缓存；上次那封信的正文读过的直接用，没读过的标 need_last 窗口打开后再读"""
    import json
    import commands as CMD
    import settings as SET
    monkeypatch.setattr(SET, "app_dir", lambda: str(tmp_path))
    (tmp_path / "cache" / "mail").mkdir(parents=True)
    rows = {"5": {"from": "infolucca@casa-te.com", "mailbox": "infolucca@casa-te.com", "folder": "已发送", "uid": 5,
                  "subject": "Ordine LAMPA Capannori 2010", "attachments": ["Ordine LAMPA Capannori 2010.xlsx"],
                  "date": "2026-10-20 10:00:00", "to": "ordini@lampa.it"}}
    (tmp_path / "cache" / "mail" / "lucca.json").write_text(json.dumps({"Sent": {"validity": 1, "rows": rows}}),
                                                             encoding="utf-8")
    st = _st(tmp_path / "tr")
    st.set("L2", {"date": "2026-11-07", "store": "2", "supplier": "LAMPA", "draft": True})
    calls = []
    monkeypatch.setattr(CMD, "mail_view_body", lambda s, m, factory=None: calls.append(m["uid"]) or
                        "Buongiorno,\nordine Ordine LAMPA Capannori 2010\nSaluti")
    st_ = {"mail": {"accounts": ACCS}}
    d = CMD.order_mail_draft(st_, st, "L2", fetch_body=False, today=date(2026, 11, 7))
    assert d["need_last"] and d["body_src"] == "template" and calls == []          # 没连网
    assert "Capannori 0711" in CMD.order_mail_last_body(st_, d) and calls == [5]
    CMD._bodies_save({"view|" + CMD._body_key(d["last"]): "Ciao\nOrdine LAMPA Capannori 2010"})
    d2 = CMD.order_mail_draft(st_, st, "L2", fetch_body=False, today=date(2026, 11, 7))
    assert d2["body_src"] == "last" and not d2["need_last"] and "0711" in d2["body"] and calls == [5]
