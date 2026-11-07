"""
从程序发订单邮件（2026-11-07 用户：点「待登记」→ 邮件模板 → 审核 → 发送 → 订单自动变「已下单」）。不依赖界面。

照现在大家发订单的习惯（从 11 月的「已发送」里看出来的）：
  · 一家店的单从这家店的邮箱发：Foiano ← info@casa-te.com、Capannori ← infolucca@、S.Filippo ← info.s.filippo@、
    S.Marco ← info.s.marco@；BMS 的单、几家店一起的单、没有自己邮箱的店（S.Concordio）从 INFO@BMSPSRL 发
  · 主题「Ordine 供应商 门店 ddmm」（门店写 Foiano / Capannori / S.Filippo / S.Marco / S.Concordio / BMS），
    几家店一起「Ordine 供应商 4PDV ddmm」；附件改名成和主题一样（Ordine SAIS Capannori 2010.xlsx）
  · 收件人 = 以前发给这家的地址（从「已发送」里学的，或者人手改过存进共享通讯录的）；公司里一直一起收的同事
    （matteohe@ / benamar@）放抄送
安全：
  · 收件人只用通讯录里的地址（程序不会照着收到的邮件里写的地址发）；人手在发送窗口里改了，发完存进通讯录
  · 同一张单发过一次就不再发（单上记着 sent）；要重发得人手在窗口里确认
  · 发出去的信撤不回，所以一定先弹窗口给人看、点「发送」才发
"""
import json
import os
import re
from datetime import date, datetime

import mail_match as MM
import order_tracking as OT

SMTP_PORT = 465
SMTP_FOR = {"imap.qiye.aliyun.com": "smtp.qiye.aliyun.com", "imap.mxhichina.com": "smtp.mxhichina.com"}
STORE_LABEL = {"1": "Foiano", "2": "Capannori", "3": "S.Filippo", "4": "S.Marco", "5": "S.Concordio", "BMS": "BMS"}
CONTACTS_FILE = "supplier_contacts.json"          # 放在订单跟踪的共享数据文件夹里（大家共用）
COMPANY_DOMAIN = "casa-te.com"
DEFAULT_BODY = ("Buongiorno,\n\n"
                "in allegato il nostro ordine {supplier} per {dest}.\n"
                "Vi chiediamo gentilmente di confermare la ricezione dell'ordine e la data di consegna.\n\n"
                "Cordiali saluti\n"
                "Ufficio Acquisti\n"
                "CASA & TE")


# ===================================================================== 一封信管哪几张单
def group_of(st, oid):
    """
    和这张待登记一起发的单：同一个订单文件、同一家供应商、也还是待登记的（总仓采购一家供应商一个文件，
    直送门店 + BMS 都在里面 → 一封信）。没有订单文件的就自己一张。按门店排好
    """
    o = st.orders.get(oid) or {}
    f = str(o.get("file") or "").strip()
    sk = st.canon(OT.sup_key(o.get("supplier")))
    ids = [oid]
    if f:
        ids = [k for k, x in st.orders.items()
               if x.get("draft") and str(x.get("file") or "").strip() == f and st.canon(OT.sup_key(x.get("supplier"))) == sk]
        if oid not in ids:
            ids.append(oid)
    order = {c: i for i, c in enumerate(["1", "2", "3", "4", "5", "BMS"])}
    return sorted(ids, key=lambda k: order.get(str(st.orders[k].get("store")), 9))


def stores_of(st, ids):
    out = []
    for k in ids:
        s = str((st.orders.get(k) or {}).get("store") or "")
        if s and s not in out:
            out.append(s)
    return out


def dest_label(stores):
    """主题里写的去向：一家店写店名，几家写「4PDV」"""
    if len(stores) == 1:
        return STORE_LABEL.get(stores[0], stores[0])
    return f"{len(stores)}PDV"


def subject_for(supplier, stores, day=None):
    day = day or date.today()
    return f"Ordine {str(supplier or '').strip()} {dest_label(stores)} {day:%d%m}"


def attachment_name(subject, path):
    ext = os.path.splitext(path or "")[1] or ".xlsx"
    return re.sub(r'[\\/:*?"<>|]+', "_", subject).strip() + ext


# ===================================================================== 用哪个邮箱发
def sender_for(stores, accounts):
    """一家店 → 这家店自己的邮箱（设置里选的门店 / 按地址猜的）；BMS、几家店一起、没有邮箱的店 → BMS 采购办公室的邮箱"""
    accounts = list(accounts or [])
    if not accounts:
        return None
    bms = next((a for a in accounts if "bmspsrl" in a["address"].lower()), None)
    if len(stores) == 1 and stores[0] != "BMS":
        own = next((a for a in accounts if str(a.get("store") or "") == stores[0]), None) or \
            next((a for a in accounts if MM.default_store(a["address"]) == stores[0]), None)
        if own:
            return own
    return bms or accounts[0]


def smtp_of(acc):
    host = (acc.get("smtp") or SMTP_FOR.get((acc.get("host") or "").lower()) or "smtp.qiye.aliyun.com").strip()
    return host, int(acc.get("smtp_port") or SMTP_PORT)


# ===================================================================== 通讯录（以前发给谁）
def sent_history(mails, st, own):
    """
    「已发送」里我们发过的订单信 → [{"sup": canon 键, "to": [外部地址], "cc_in": [公司里的地址], "date", "mailbox",
    "folder", "folder_raw", "uid", "subject"}]，新的在前
    """
    own = {a.lower() for a in own}
    idx = MM.SupplierIndex(st)
    out = []
    for m in mails:
        if (m.get("from") or "").lower() not in own:
            continue
        if MM.kind_of(m, own) != "order":
            continue
        text = " ".join([m.get("subject") or ""] + list(m.get("attachments") or []))
        sups = idx.find(text)
        if not sups:
            continue
        addrs = MM._addrs(m.get("to")) + MM._addrs(m.get("cc"))
        ext = [a for a in addrs if a not in own and not a.endswith("@" + COMPANY_DOMAIN)]
        inside = [a for a in addrs if a.endswith("@" + COMPANY_DOMAIN) and a not in own]
        if not ext:
            continue
        out.append({"sup": sups[0], "to": ext, "cc_in": inside, "date": str(m.get("date") or ""),
                    "mailbox": m.get("mailbox"), "folder": m.get("folder"), "folder_raw": m.get("folder_raw"),
                    "uid": m.get("uid"), "subject": m.get("subject") or ""})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def usual_cc(history, min_share=0.5):
    """公司里一直一起收订单信的同事（一半以上的订单信都有）"""
    if not history:
        return []
    cnt = {}
    for h in history:
        for a in set(h["cc_in"]):
            cnt[a] = cnt.get(a, 0) + 1
    return sorted(a for a, n in cnt.items() if n >= max(1, min_share * len(history)))


def contacts_path(st):
    return os.path.join(st.dir, CONTACTS_FILE)


def load_contacts(st):
    try:
        with open(contacts_path(st), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_contact(st, supplier, to, me=None):
    """人手确认过的收件人存进共享通讯录（读最新的再写，别人同时存的不丢）"""
    sk = st.canon(OT.sup_key(supplier))
    d = load_contacts(st)
    d[sk] = {"name": str(supplier or ""), "to": list(to), "by": me or st.me, "at": datetime.now().isoformat(timespec="seconds")}
    os.makedirs(st.dir, exist_ok=True)
    tmp = contacts_path(st) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, contacts_path(st))


def recipients(st, supplier, history, contacts=None):
    """收件人：共享通讯录里存过的 > 以前最近一次发给这家的；都没有返回 []（发送窗口里人手填）"""
    sk = st.canon(OT.sup_key(supplier))
    contacts = load_contacts(st) if contacts is None else contacts
    c = contacts.get(sk)
    if c and c.get("to"):
        return list(c["to"]), "contacts"
    for h in history:
        if h["sup"] == sk or MM._like(h["sup"], sk):
            return list(h["to"]), "history"
    return [], ""


def last_sent(history, st, supplier, mailbox=None):
    """以前发给这家的最近一封订单信（拿它的正文当模板）；没有就这个邮箱最近发的任何一封订单信"""
    sk = st.canon(OT.sup_key(supplier))
    for h in history:
        if h["sup"] == sk or MM._like(h["sup"], sk):
            return h
    if mailbox:
        for h in history:
            if (h["mailbox"] or "").lower() == mailbox.lower():
                return h
    return None


def body_from_last(body, old_subject, new_subject, supplier):
    """上次的正文当模板：去掉引用的旧信（「Il … ha scritto:」/「-----Original」以下），旧主题换成新主题"""
    t = str(body or "")
    t = re.split(r"\n\s*(?:-{3,}\s*(?:Original|Messaggio)|Il .{5,80}ha scritto:|On .{5,80}wrote:|Da: .+\nInviato:)", t,
                 maxsplit=1)[0].rstrip()
    if old_subject and old_subject in t:
        t = t.replace(old_subject, new_subject)
    return t


def default_body(supplier, stores):
    names = [STORE_LABEL.get(s, s) for s in stores]
    dest = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]
    return DEFAULT_BODY.format(supplier=str(supplier or "").strip(), dest=dest)


# ===================================================================== 写信、发信
def build(frm, to, cc, subject, body, files, domain=None):
    """EmailMessage；files = [(路径, 附件名)]"""
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid
    import mimetypes
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain or (frm.split("@")[-1] if "@" in frm else None))
    msg.set_content(body or "")
    for path, name in files or []:
        ctype, _ = mimetypes.guess_type(name)
        main, sub = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=main, subtype=sub, filename=name)
    return msg


def valid_address(a):
    return bool(re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", (a or "").strip()))


def split_addrs(text):
    return [a.strip() for a in re.split(r"[;,\s]+", text or "") if a.strip()]


def send(acc, password, msg, smtp_factory=None, imap_factory=None):
    """
    用这个邮箱的 SMTP 发出去（SSL 465），再在服务器「已发送」里放一份（IMAP APPEND，标已读），
    大家在网页邮箱 / 以后的邮件核对里都看得到。放「已发送」失败不算发送失败。返回 (Message-ID, 放进已发送了没有)
    """
    import smtplib
    host, port = smtp_of(acc)
    s = (smtp_factory or (lambda h, p: smtplib.SMTP_SSL(h, p, timeout=60)))(host, port)
    try:
        s.login(acc["address"], password)
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except Exception:
            pass
    saved = False
    try:
        import mail_client as MC
        import imaplib
        conn = MC.connect(acc, password, factory=imap_factory)
        try:
            sent = [f for f in MC.pick_folders(MC.folders(conn)) if f[0].upper() != "INBOX"]
            if sent:
                typ, _ = conn.append(MC._quote(sent[0][0]), "(\\Seen)", imaplib.Time2Internaldate(datetime.now().timestamp()),
                                     msg.as_bytes())
                saved = typ == "OK"
        finally:
            MC.close(conn)
    except Exception:
        saved = False
    return msg["Message-ID"], saved


def mark_sent(st, ids, mail, today=None):
    """发出去了：待登记 → 已下单（下单日期 = 今天），单上记发了什么（同一张单不会再发一次）"""
    day = (today or date.today()).isoformat()
    ch = []
    for oid in ids:
        o = st.orders.get(oid)
        if not o:
            continue
        f = {"draft": None, "date": day, "sent": {"at": datetime.now().isoformat(timespec="seconds"),
                                                   "to": mail["to"], "from": mail["from"], "subject": mail["subject"],
                                                   "mid": mail.get("mid") or ""}}
        if not o.get("buyer"):
            f["buyer"] = st.me
        mails = list(o.get("mails") or [])
        mails.append({"k": "order", "d": datetime.now().strftime("%Y-%m-%d %H:%M"), "s": mail["subject"][:120],
                      "f": mail["from"]})
        f["mails"] = mails
        ch.append((oid, f))
    st.set_many(ch, src="mail")
    return len(ch)
