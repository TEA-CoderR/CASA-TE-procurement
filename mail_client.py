"""
邮件（第 1 阶段，2026-11-06）：用 IMAP 只读连接公司邮箱（阿里云企业邮箱），不依赖界面。

只读保证：
· 文件夹一律用 EXAMINE 打开（select(readonly=True)），服务器不会改任何「已读 / 未读」等标记；
· 只取信头（BODY.PEEK[HEADER.FIELDS …]）和结构（BODYSTRUCTURE，用来看附件名），不下载正文和附件；
· 不删、不移动、不发信。

密码：登录密码或网页版生成的「第三方客户端安全密码」（用户 11-06 定：先用登录密码，以后换安全密码），存在本机保管处（credentials.py，名字 mail.<地址>），
不写进 settings.json、不进共享文件夹。设置里只存地址 / 服务器 / 端口（settings["mail"]["accounts"]）。
"""
import email
import email.policy
import imaplib
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

DEFAULT_HOST = "imap.qiye.aliyun.com"       # 阿里云企业邮箱（老地址 imap.mxhichina.com 也行）
DEFAULT_PORT = 993
TIMEOUT = 30
HEADER_FIELDS = "FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO"
BATCH = 100

imaplib.Commands.setdefault("ID", ("NONAUTH", "AUTH", "SELECTED"))     # 有的国内邮箱登录后要先报客户端名字


class MailError(Exception):
    """给人看的错误（已经写成业务话）"""


def cred_name(address):
    return "mail." + (address or "").strip().lower()


def accounts(settings):
    """设置里的邮箱 [{"address", "host", "port"}]"""
    out = []
    for a in ((settings.get("mail") or {}).get("accounts") or []):
        addr = (a.get("address") or "").strip()
        if addr:
            out.append({"address": addr, "host": (a.get("host") or DEFAULT_HOST).strip(),
                        "port": int(a.get("port") or DEFAULT_PORT)})
    return out


# ===================================================================== 连接
def connect(acc, password, factory=None):
    """登录，返回连接。factory 测试用（假的 IMAP）"""
    if not password:
        raise MailError(f"{acc['address']}：还没填密码")
    try:
        conn = (factory or (lambda h, p: imaplib.IMAP4_SSL(h, p, timeout=TIMEOUT)))(acc["host"], acc["port"])
    except OSError as e:
        raise MailError(f"连不上邮件服务器 {acc['host']}:{acc['port']}（{e}）")
    try:
        conn.login(acc["address"], password)
    except imaplib.IMAP4.error as e:
        a = e.args[0] if e.args else ""
        msg = a.decode("utf-8", "replace") if isinstance(a, bytes) else str(a)
        raise MailError(f"{acc['address']}：登录失败。请检查：① 密码对不对（登录密码，或网页版生成的「第三方客户端安全密码」）；"
                        f"② 管理员已给这个账号开通 IMAP、允许第三方客户端。（服务器回复：{msg}）")
    try:
        conn._simple_command("ID", '("name" "CASATE-procurement" "version" "1")')
    except Exception:
        pass
    return conn


def close(conn):
    for f in (getattr(conn, "close", None), getattr(conn, "logout", None)):
        try:
            f()
        except Exception:
            pass


# ===================================================================== 文件夹（IMAP 的改良 UTF-7 编码）
def decode_utf7(s):
    """IMAP 文件夹名：&XfJT0ZAB- → 已发送"""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "&":
            j = s.index("-", i)
            if j == i + 1:
                out.append("&")
            else:
                b64 = s[i + 1:j].replace(",", "/")
                b64 += "=" * (-len(b64) % 4)
                import base64
                out.append(base64.b64decode(b64).decode("utf-16-be"))
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) (?P<delim>"[^"]*"|NIL) (?P<name>.+)$')


def folders(conn):
    """[(原名, 显示名, 标记集合)]"""
    typ, data = conn.list()
    out = []
    for line in data or []:
        if isinstance(line, tuple):            # 名字是 literal
            head, lit = line
            line = head.rsplit(b" ", 1)[0] + b' "' + lit + b'"'
        if not line:
            continue
        m = _LIST_RE.match(line)
        if not m:
            continue
        name = m.group("name").decode("utf-8", "replace").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"')
        flags = {f.lower() for f in m.group("flags").decode("ascii", "replace").split()}
        out.append((name, decode_utf7(name), flags))
    return out


def pick_folders(fs):
    """摸底要看的：收件箱 + 已发送（有 \\Sent 标记，或名字像已发送）"""
    inbox = [f for f in fs if f[0].upper() == "INBOX"]
    sent = [f for f in fs if "\\sent" in f[2]] or \
           [f for f in fs if re.search(r"sent|已发送|inviat", f[1], re.I)]
    return (inbox[:1] or fs[:1]) + sent[:1]


def _quote(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ===================================================================== 取信头
def _tokens(parts):
    """把一封信的 FETCH 回复（字节片 + literal）拆成记号：( ) 字符串 数字 NIL 原子；literal 原样当字符串"""
    toks = []
    for kind, data in parts:
        if kind == "lit":
            toks.append(("s", data))
            continue
        i, n = 0, len(data)
        while i < n:
            c = data[i:i + 1]
            if c in b" \r\n":
                i += 1
            elif c in b"()":
                toks.append((c.decode(), None))
                i += 1
            elif c == b'"':
                j, buf = i + 1, bytearray()
                while j < n and data[j:j + 1] != b'"':
                    if data[j:j + 1] == b"\\":
                        j += 1
                    buf += data[j:j + 1]
                    j += 1
                toks.append(("s", bytes(buf)))
                i = j + 1
            elif c == b"{":                      # literal 记号：内容在下一片
                i = data.index(b"}", i) + 1
            else:
                j = i
                depth = 0
                while j < n:
                    ch = data[j:j + 1]
                    if ch == b"[":
                        depth += 1
                    elif ch == b"]":
                        depth -= 1
                    elif depth == 0 and ch in b" ()\r\n":
                        break
                    j += 1
                toks.append(("a", data[i:j]))
                i = j
    return toks


def _parse(toks, i=0):
    """记号 → 嵌套 list；返回 (值, 下一个位置)"""
    kind, val = toks[i]
    if kind == "(":
        out, i = [], i + 1
        while i < len(toks) and toks[i][0] != ")":
            v, i = _parse(toks, i)
            out.append(v)
        return out, i + 1
    if kind == "a":
        return (None if val.upper() == b"NIL" else val.decode("ascii", "replace")), i + 1
    return val, i + 1


def _text(v):
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return v


def _rfc2231(raw):
    """utf-8''Listino%20Meloni.pdf → Listino Meloni.pdf"""
    from urllib.parse import unquote
    m = re.match(r"^([^']*)'[^']*'(.*)$", raw)
    if not m:
        return unquote(raw)
    try:
        return unquote(m.group(2), encoding=m.group(1) or "utf-8", errors="replace")
    except LookupError:
        return unquote(m.group(2))


def attachment_names(bs):
    """BODYSTRUCTURE 里所有附件名（NAME / FILENAME / FILENAME*，按 RFC 2047 / 2231 解码）"""
    names = []

    def walk(x):
        if not isinstance(x, list):
            return
        if len(x) % 2 == 0 and x and all(isinstance(k, (bytes, str)) for k in x[::2]):
            for k, v in zip(x[::2], x[1::2]):
                key = (k.decode() if isinstance(k, bytes) else k).upper()
                if key in ("NAME", "FILENAME") and v:
                    names.append(_text(v))
                elif key in ("NAME*", "FILENAME*") and v:
                    raw = v.decode("utf-8", "replace") if isinstance(v, bytes) else v
                    names.append(_rfc2231(raw))
        for y in x:
            walk(y)
    walk(bs)
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _split_messages(data):
    """imaplib 的 FETCH 回复 → 每封信一组 [(kind, bytes)]：以「序号 (」开头的片段开始一封新信，其余接在后面"""
    msgs = []
    for item in data or []:
        if item is None:
            continue
        head, lit = item if isinstance(item, tuple) else (item, None)
        if re.match(rb"^\d+ \(", head) or not msgs:
            msgs.append([])
        msgs[-1].append(("txt", head))
        if lit is not None:
            msgs[-1].append(("lit", lit))
    return msgs


def _parse_message(parts):
    toks = _tokens(parts)
    # 第一个是序号，后面是 ( KEY 值 KEY 值 … )
    lst, _ = _parse(toks, 1)
    d = {}
    for k, v in zip(lst[::2], lst[1::2]):
        key = (k or "").upper()
        if key.startswith("BODY["):
            d["HEADER"] = v if isinstance(v, bytes) else (v or "").encode()
        else:
            d[key] = v
    return d


def fetch_headers(conn, folder, since, log=None, cancel=None):
    """一个文件夹里 since 以后的信：[{uid, date, from, from_name, to, subject, attachments, size, message_id, in_reply_to}]
    只读（EXAMINE + BODY.PEEK）"""
    typ, _ = conn.select(_quote(folder), readonly=True)
    if typ != "OK":
        raise MailError(f"打不开文件夹 {decode_utf7(folder)}")
    typ, data = conn.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"))
    uids = (data[0] or b"").split() if data else []
    out = []
    for k in range(0, len(uids), BATCH):
        if cancel is not None and cancel():
            break
        chunk = b",".join(uids[k:k + BATCH]).decode()
        typ, data = conn.uid("FETCH", chunk, f"(UID RFC822.SIZE BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
        for parts in _split_messages(data):
            try:
                d = _parse_message(parts)
            except Exception:
                continue
            h = email.message_from_bytes(d.get("HEADER") or b"", policy=email.policy.default)
            frm = getaddresses([str(h.get("From") or "")])
            when = None
            try:
                when = parsedate_to_datetime(str(h.get("Date"))) if h.get("Date") else None
            except Exception:
                pass
            out.append({
                "uid": d.get("UID"),
                "date": when.replace(tzinfo=None) if when else None,
                "from": (frm[0][1] if frm else "").lower(),
                "from_name": frm[0][0] if frm else "",
                "to": ", ".join(a for _, a in getaddresses([str(h.get("To") or ""), str(h.get("Cc") or "")]) if a),
                "subject": str(h.get("Subject") or "").strip(),
                "attachments": attachment_names(d.get("BODYSTRUCTURE")),
                "size": int(d.get("RFC822.SIZE") or 0),
                "message_id": str(h.get("Message-ID") or "").strip(),
                "in_reply_to": str(h.get("In-Reply-To") or "").strip(),
            })
        if log:
            log(f"    {decode_utf7(folder)}：{min(k + BATCH, len(uids))} / {len(uids)}")
    return out


# ===================================================================== 摸底汇总
def subject_pattern(s):
    """主题的规律：去掉 Re/Fwd、数字换成 #，方便看出同一类信"""
    s = re.sub(r"^\s*((re|r|fw|fwd|i|inoltra|回复|转发)\s*[:：]\s*)+", "", s or "", flags=re.I)
    s = re.sub(r"\d+", "#", s)
    return re.sub(r"\s+", " ", s).strip()[:80]


def _ext(name):
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", name or "")
    return m.group(1).lower() if m else "?"


def summarize(rows):
    """按 (邮箱, 文件夹, 发件人) 汇总"""
    g = defaultdict(list)
    for r in rows:
        g[(r["mailbox"], r["folder"], r["from"])].append(r)
    out = []
    for (mb, folder, frm), rs in g.items():
        pats = Counter(subject_pattern(r["subject"]) for r in rs)
        exts = Counter(_ext(a) for r in rs for a in r["attachments"])
        atts = []
        for r in rs:
            for a in r["attachments"]:
                if a not in atts:
                    atts.append(a)
        dates = [r["date"] for r in rs if r["date"]]
        out.append({"mailbox": mb, "folder": folder, "from": frm,
                    "from_name": next((r["from_name"] for r in rs if r["from_name"]), ""),
                    "count": len(rs), "with_att": sum(1 for r in rs if r["attachments"]),
                    "patterns": " | ".join(f"{p}（{n}）" if n > 1 else p for p, n in pats.most_common(3)),
                    "exts": " ".join(f"{e}×{n}" for e, n in exts.most_common()),
                    "examples": " | ".join(atts[:3]),
                    "first": min(dates) if dates else None, "last": max(dates) if dates else None})
    out.sort(key=lambda x: (x["mailbox"], x["folder"] != "INBOX", -x["count"]))
    return out


LABELS = ["门店订货清单", "批发商报价", "我们发出的订单", "供应商确认 / 回复", "发票 / DDT", "广告 / 通知", "内部", "其他"]


def write_survey(path, rows, summary, days):
    """摸底结果：汇总（按发件人，最后一列「这是什么」下拉选）+ 全部邮件"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = "汇总"
    head = ["邮箱", "文件夹", "发件人", "名字", "封数", "带附件", "主题规律（最常见 3 种）", "附件类型", "附件例子",
            "最早", "最近", "这是什么（请选）"]
    ws.append(head)
    for s in summary:
        ws.append([s["mailbox"], s["folder"], s["from"], s["from_name"], s["count"], s["with_att"], s["patterns"],
                   s["exts"], s["examples"], s["first"], s["last"], ""])
    dv = DataValidation(type="list", formula1='"' + ",".join(LABELS) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    if summary:
        dv.add(f"L2:L{len(summary) + 1}")
    ws2 = wb.create_sheet("全部邮件")
    ws2.append(["邮箱", "文件夹", "日期", "发件人", "名字", "收件人", "主题", "附件", "大小 KB", "Message-ID", "回复的是"])
    for r in sorted(rows, key=lambda r: (r["mailbox"], r["folder"], r["date"] or datetime.min), reverse=False):
        ws2.append([r["mailbox"], r["folder"], r["date"], r["from"], r["from_name"], r["to"], r["subject"],
                    " | ".join(r["attachments"]), round(r["size"] / 1024), r["message_id"], r["in_reply_to"]])
    hf = PatternFill("solid", fgColor="1F3864")
    for sh, widths in ((ws, [26, 10, 30, 22, 7, 8, 60, 18, 50, 16, 16, 20]),
                       (ws2, [26, 10, 16, 30, 22, 30, 60, 50, 9, 30, 30])):
        for c, w in zip(sh[1], widths):
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = hf
            c.alignment = Alignment(vertical="center")
            sh.column_dimensions[c.column_letter].width = w
        sh.freeze_panes = "A2"
        sh.auto_filter.ref = sh.dimensions
        for row in sh.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, datetime):
                    c.number_format = "dd/mm/yyyy hh:mm"
    ws["L1"].fill = PatternFill("solid", fgColor="C55A11")
    note = wb.create_sheet("说明")
    for line in (f"最近 {days} 天的邮件，只读了发件人、收件人、主题、日期、附件名，没读正文、没下载附件、没改已读状态。",
                 "「汇总」每行一个发件人：请在最后一列选「这是什么」，程序按这个写自动分类规则。",
                 "发给别人之前请先看一遍，确认没有不方便给别人看的内容。"):
        note.append([line])
    note.column_dimensions["A"].width = 110
    wb.save(path)
    return path


def survey(accs, get_password, days=28, log=print, progress=None, cancel=None, factory=None, today=None):
    """每个邮箱：收件箱 + 已发送，最近 days 天的信头 → (rows, summary, errors)"""
    since = (today or date.today()) - timedelta(days=days)
    rows, errors = [], []
    for i, acc in enumerate(accs):
        if progress:
            progress.post_step(i, "running", "", acc["address"])
            progress.post_progress(100 * i / max(1, len(accs)))
        if cancel is not None and cancel():
            break
        conn = None
        try:
            conn = connect(acc, get_password(acc["address"]), factory=factory)
            n0 = len(rows)
            for raw, shown, _flags in pick_folders(folders(conn)):
                label = "INBOX" if raw.upper() == "INBOX" else shown
                for r in fetch_headers(conn, raw, since, log=log, cancel=cancel):
                    r["mailbox"], r["folder"] = acc["address"], label
                    rows.append(r)
            log(f"  ✔ {acc['address']}：{len(rows) - n0} 封")
            if progress:
                progress.post_step(i, "done", str(len(rows) - n0), "")
        except MailError as e:
            errors.append(str(e))
            log(f"  ✘ {e}")
            if progress:
                progress.post_step(i, "error", "", str(e))
        except Exception as e:
            errors.append(f"{acc['address']}：{e}")
            log(f"  ✘ {acc['address']}：{e}")
            if progress:
                progress.post_step(i, "error", "", str(e))
        finally:
            if conn is not None:
                close(conn)
    return rows, summarize(rows), errors


def test_account(acc, password, factory=None):
    """测试连接：(文件夹数, 收件箱信数)；失败抛 MailError"""
    conn = connect(acc, password, factory=factory)
    try:
        fs = folders(conn)
        typ, data = conn.select("INBOX", readonly=True)
        n = int((data or [b"0"])[0] or 0) if typ == "OK" else 0
        return len(fs), n
    finally:
        close(conn)
