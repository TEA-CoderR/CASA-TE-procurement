"""邮件摸底（mail_client）：用假的 IMAP 服务器测，回复格式照 imaplib 真实返回的样子"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mail_client as M  # noqa: E402

HDR1 = (b"From: Ordine Foiano <ordine.foiano@casa-te.com>\r\nTo: info@casa-te.com\r\n"
        b"Subject: =?UTF-8?B?T3JkaW5lIGRldGVyc2l2aSAzMS8xMA==?=\r\n"
        b"Date: Fri, 31 Oct 2026 09:15:00 +0100\r\nMessage-ID: <a1@casa-te.com>\r\n\r\n")
HDR2 = (b"From: MELONI <listini@meloni.it>\r\nTo: info@casa-te.com\r\nSubject: Re: Listino settimana 44\r\n"
        b"Date: Mon, 03 Nov 2026 08:00:00 +0100\r\nMessage-ID: <b2@meloni.it>\r\nIn-Reply-To: <x@casa-te.com>\r\n\r\n")
BS1 = (b'(("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 20 1 NIL NIL NIL)'
       b'("APPLICATION" "VND.OPENXMLFORMATS-OFFICEDOCUMENT.SPREADSHEETML.SHEET" ("NAME" "FOIANO - DETER1.xlsx") NIL NIL '
       b'"BASE64" 50000 NIL ("ATTACHMENT" ("FILENAME" "FOIANO - DETER1.xlsx")) NIL) "MIXED" ("BOUNDARY" "xyz") NIL NIL)')
# 第二封：附件名是 RFC 2231 编码，另外 BODYSTRUCTURE 里有一个 literal
BS2_HEAD = (b'(("TEXT" "HTML" ("CHARSET" "utf-8") NIL NIL "QUOTED-PRINTABLE" 900 12 NIL NIL NIL)'
            b'("APPLICATION" "PDF" ("NAME" {21}')
BS2_LIT = b"Listino Meloni 44.pdf"
BS2_TAIL = (b') NIL NIL "BASE64" 9000 NIL ("ATTACHMENT" ("FILENAME*" "utf-8\'\'Listino%20Meloni%2044.pdf")) NIL)'
            b' "MIXED" ("BOUNDARY" "q") NIL NIL)')


class FakeIMAP:
    def __init__(self, host, port):
        self.host, self.port, self.calls, self.selected = host, port, [], None

    def login(self, user, pw):
        self.calls.append(("login", user))
        if pw != "good":
            raise M.imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] invalid")
        return "OK", [b"ok"]

    def _simple_command(self, *a):
        self.calls.append(("cmd",) + a)
        return "OK", [b""]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "&XfJT0ZAB-"',
                      b'(\\HasNoChildren \\Trash) "/" "&XfJSIJZk-"']

    def select(self, name, readonly=False):
        self.calls.append(("select", name, readonly))
        self.selected = name
        return "OK", [b"2"]

    def uid(self, cmd, *args):
        self.calls.append(("uid", cmd) + args)
        if cmd == "SEARCH":
            return "OK", [b"101 102" if self.selected == '"INBOX"' else b""]
        assert cmd == "FETCH"
        return "OK", [(b"1 (UID 101 RFC822.SIZE 52340 BODYSTRUCTURE " + BS1 + b" BODY[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO)] {%d}" % len(HDR1), HDR1),
                      b")",
                      (b"2 (UID 102 RFC822.SIZE 9100 BODYSTRUCTURE " + BS2_HEAD, BS2_LIT),
                      (BS2_TAIL + b" BODY[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO)] {%d}" % len(HDR2), HDR2),
                      b")"]

    def close(self):
        self.calls.append(("close",))

    def logout(self):
        self.calls.append(("logout",))


def test_folder_names_utf7():
    assert M.decode_utf7("&XfJT0ZAB-") == "已发送"
    assert M.decode_utf7("Posta &- Varie") == "Posta & Varie"


def test_survey_reads_headers_only_and_never_changes_anything(tmp_path):
    made = []

    def factory(h, p):
        made.append(FakeIMAP(h, p))
        return made[-1]
    acc = {"address": "info@casa-te.com", "host": M.DEFAULT_HOST, "port": 993}
    rows, summary, errors = M.survey([acc], lambda a: "good", days=28, log=lambda *_: None, factory=factory,
                                     today=date(2026, 11, 6))
    assert errors == []
    assert [r["uid"] for r in rows] == ["101", "102"]
    r1, r2 = rows
    assert r1["from"] == "ordine.foiano@casa-te.com" and r1["subject"] == "Ordine detersivi 31/10"
    assert r1["attachments"] == ["FOIANO - DETER1.xlsx"] and r1["size"] == 52340 and r1["folder"] == "INBOX"
    assert r2["attachments"] == ["Listino Meloni 44.pdf"]          # literal 里的名字 + RFC 2231 的同名，只算一次
    assert r2["in_reply_to"] == "<x@casa-te.com>"
    calls = made[0].calls
    # 只读：文件夹都用 readonly 打开；只有 SEARCH / FETCH，FETCH 只用 BODY.PEEK；没有 STORE / COPY / EXPUNGE
    assert all(c[2] is True for c in calls if c[0] == "select")
    assert {c[1] for c in calls if c[0] == "uid"} == {"SEARCH", "FETCH"}
    assert all("BODY.PEEK[" in c[3] and "BODY[" not in c[3].replace("BODY.PEEK[", "") for c in calls if c[:2] == ("uid", "FETCH"))
    assert ("select", '"&XfJT0ZAB-"', True) in calls                     # 已发送也看了
    assert ("select", '"&XfJSIJZk-"', True) not in calls                 # 已删除不看
    assert ("uid", "SEARCH", None, "SINCE", "09-Oct-2026") in calls
    assert summary[0]["count"] == 1 and summary[0]["exts"] == "xlsx×1"


def test_wrong_password_is_explained():
    acc = {"address": "info@casa-te.com", "host": M.DEFAULT_HOST, "port": 993}
    with pytest.raises(M.MailError) as e:
        M.test_account(acc, "bad", factory=FakeIMAP)
    assert "第三方客户端安全密码" in str(e.value)
    with pytest.raises(M.MailError):
        M.test_account(acc, "", factory=FakeIMAP)
    assert M.test_account(acc, "good", factory=FakeIMAP) == (3, 2)


def test_survey_continues_after_one_mailbox_fails(tmp_path):
    accs = [{"address": "a@casa-te.com", "host": "h", "port": 993}, {"address": "b@casa-te.com", "host": "h", "port": 993}]
    rows, summary, errors = M.survey(accs, lambda a: "bad" if a.startswith("a") else "good", log=lambda *_: None,
                                     factory=FakeIMAP, today=date(2026, 11, 6))
    assert len(errors) == 1 and errors[0].startswith("a@casa-te.com") and {r["mailbox"] for r in rows} == {"b@casa-te.com"}
    path = M.write_survey(str(tmp_path / "s.xlsx"), rows, summary, 28)
    from openpyxl import load_workbook
    wb = load_workbook(path)
    assert wb.sheetnames == ["汇总", "全部邮件", "说明"] and wb["汇总"].max_row == 3 and wb["全部邮件"].max_row == 3


def test_subject_pattern():
    assert M.subject_pattern("RE: R: Ordine 1234 del 31/10") == "Ordine # del #/#"
    assert M.subject_pattern("回复：报价 44") == "报价 #"
