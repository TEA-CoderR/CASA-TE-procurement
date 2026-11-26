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


# ===================================================================== 订单号、发件邮箱、固定模板（2026-11-21）
# 用户：以后全部用程序发订单邮件，内容一定要准确清楚；固定六个邮箱（五家门店各一个 + BMS），各自的订单用各自的邮箱发。
# 正文不再照搬上一封信（以前内容常对不上），一律按订单数据填固定的意大利文模板；每张单一个订单号，
# 请供应商回信保留主题、DDT 和预约卸货时写上订单号 → 回信、预约都能准确对到这张单。
STORE_ABBR = {"1": "FO", "2": "MA", "3": "FI", "4": "SM", "5": "SC", "BMS": "BMS"}
ORDER_NO_RE = OT.ORDER_NO_RE
BOOKING_URL = "https://prenota.casaete.top"
# 送货抬头 / 地址（来自门店采购 SCHEDA 的「Luogo di destinazione」；在「订单发件邮箱」窗口里可以改）
# 地址 / 卸货电话照以前发给批发商的订单信（2026-11-22 用户发来的 MEGA CASH / MELONI 订单信 + 「已发送」里读过的几封）
DEFAULT_PROFILES = {
    "1": {"name": "CASA & TE SRL", "address": "Via d'Arezzo 159/r, 52045 Foiano della Chiana (AR)", "phone": "0575-1782904"},
    "2": {"name": "CASA & TE GROUP SRL", "address": "Viale Europa 434, 55012 Capannori (LU)", "phone": "0583-1808252"},
    "3": {"name": "CASA & TE S.FILIPPO SRL", "address": "Via di Tiglio 1893, 55100 Lucca (LU)", "phone": "0583-570716"},
    "4": {"name": "CASA & TE S.MARCO SRL", "address": "Via del Brennero 844, 55100 Lucca (LU)", "phone": "0583-323749"},
    "5": {"name": "CASA & TE S.CONCORDIO SRL", "address": "Via Savonarola 50, 55100 Lucca (LU)", "phone": "339 2254525"},
    "BMS": {"name": "BMS PARTNERS SRL", "address": "Via di Tiglio 1893, 55100 Lucca (LU)", "phone": ""},
}
# DETERSIVO 的单（批发商比价生成的，origin wholesale）：2026-11-22 用户定——批发商送货都准时，不要确认信、不要预约；
# 一家批发商一封信，五家门店的单一起，统一用 BMS 的邮箱发，正文写每家店的地址、卸货电话、箱数、金额
DETER_ORIGIN = "wholesale"
STORE_UPPER = {"1": "FOIANO", "2": "MARLIA", "3": "S.FILIPPO", "4": "S.MARCO", "5": "S.CONCORDIO", "BMS": "BMS"}
DETER_CC = ("lucas@casa-te.com",)                 # DETERSIVO 订单信一定抄送（2026-11-22 用户），设置 order_mail.deter_cc 可改


def deter_cc(settings):
    v = order_cfg(settings).get("deter_cc")
    return [a.strip() for a in (v if isinstance(v, list) else DETER_CC) if str(a or "").strip()]
SENDER_CODES = ("1", "2", "3", "4", "5", "BMS")


def order_cfg(settings):
    return (settings or {}).get("order_mail") or {}


def senders(settings):
    """{门店代码: 发件邮箱}：「订单发件邮箱」窗口里填的六个"""
    return {k: str(v).strip() for k, v in (order_cfg(settings).get("senders") or {}).items() if str(v or "").strip()}


def profile(settings, code):
    """送货抬头 + 地址：窗口里改过的优先，没改过用 DEFAULT_PROFILES"""
    p = dict(DEFAULT_PROFILES.get(str(code), {"name": "", "address": ""}))
    p.update({k: v for k, v in ((order_cfg(settings).get("stores") or {}).get(str(code)) or {}).items() if v})
    return p


def booking_url(settings):
    return (order_cfg(settings).get("booking_url") or BOOKING_URL).strip().replace("casa-te.top", "casaete.top")


def order_no(oid, o):
    """
    订单号：门店缩写-下单那天 ddmm-4 位（单号算出来的，几台电脑算出来都一样，不用记流水号），例如 FO-2011-3F9A。
    发过的单记在单上（o["no"]），之后下单日期变了也不换号
    """
    import hashlib
    if o.get("no"):
        return o["no"]
    d = OT.to_date(o.get("date")) or date.today()
    ab = STORE_ABBR.get(str(o.get("store") or ""), "CT")
    return f"{ab}-{d:%d%m}-{hashlib.sha1(str(oid).encode('utf-8')).hexdigest()[:4].upper()}"


find_order_nos = OT.find_order_nos


def is_deter(o):
    """DETERSIVO 的单（批发商比价生成的）"""
    return str((o or {}).get("origin") or "") == DETER_ORIGIN


def sender_problem(stores, settings, accounts, deter=False):
    """要用的发件邮箱有问题时返回给人看的一句话，没问题返回 None（deter = DETERSIVO 的单，一律 BMS 邮箱）"""
    code = "BMS" if deter else stores[0] if len(stores) == 1 else "BMS"
    label = STORE_LABEL.get(code, code)
    addr = senders(settings).get(code)
    if not addr:
        return f"{label}：订单发件邮箱还没设置（系统设置 → 邮箱 → 订单发件邮箱）"
    if not any(a["address"].lower() == addr.lower() for a in accounts or []):
        return f"{label}：发件邮箱 {addr} 不在邮箱设置里（系统设置 → 邮箱 → 订单发件邮箱）"
    return None


def greet_name(text):
    """以前那封信开头称呼的名字（「Buonasera Alessandro,」→ Alessandro）；认不出返回 None"""
    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        m = re.match(r"\s*(?:ciao|salve|gentile|buongiorno|buonasera|buon\s*giorno|buona\s*sera|buon\s+pomeriggio)\s+"
                     r"([A-Za-zÀ-ÿ'’]{2,30})\s*[,!]?\s*$", line, re.I)
        return m.group(1).capitalize() if m else None
    return None


def hello(now=None):
    h = (now or datetime.now()).hour
    return "Buongiorno" if h < 13 else "Buon pomeriggio" if h < 17 else "Buonasera"


def eur_it(v):
    """意大利写法：€ 2.285,20"""
    return "€ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def deter_attachment(o, supplier, path, day=None):
    """DETERSIVO 订单附件名：Ordine FOIANO MELONI 2211.xlsx（照以前手发的叫法）"""
    d = OT.to_date(o.get("date")) or day or date.today()
    ext = os.path.splitext(path or "")[1] or ".xlsx"
    code = str(o.get("store") or "")
    name = f"Ordine {STORE_UPPER.get(code, code)} {str(supplier or '').strip()} {d:%d%m}"
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() + ext


def compose_deter(supplier, orders, settings, day=None, name=None, now=None):
    """
    DETERSIVO 订单信（2026-11-22 用户的模板）：主题「Ordine MELONI 5PDV 2211」；正文问候 + 每家店 地址 · 卸货电话 ·
    箱数 · 金额，最后合计。不要确认、不要预约。返回 (主题, 正文)
    """
    day = day or date.today()
    sup = str(supplier or "").strip()
    order = {c: i for i, c in enumerate(SENDER_CODES)}
    rows = sorted(orders, key=lambda r: order.get(str(r[1].get("store")), 9))
    d0 = min((OT.to_date(o.get("date")) or day) for _i, o in rows) if rows else day
    n = len(rows)
    first = STORE_UPPER.get(str(rows[0][1].get("store")), "") if rows else ""
    # 2026-11-22 18:00 用户：不加订单号（试过一版主题 / 正文带订单号，用户说太麻烦，改回来）
    subject = f"Ordine {sup} {f'{n}PDV' if n > 1 else first} {d0:%d%m}"
    who = f"{hello(now)} {name}," if name else f"{hello(now)},"
    dest = ("tutti i 5 punti vendita" if n == 5 else f"{n} punti vendita" if n > 1 else f"il punto vendita di {first}")
    parts, tc, ta = [], 0.0, 0.0
    for _oid, o in rows:
        code = str(o.get("store") or "")
        p = profile(settings, code)
        c, a = _num(o.get("colli")), _num(o.get("amount"))
        lines = [f"{STORE_UPPER.get(code, code)} – {p['name']}", p["address"]]
        if p.get("phone"):
            lines.append(f"Contatto scarico: {p['phone']}")
        vals = ([f"Colli: {c:g}"] if c else []) + ([f"Importo: {eur_it(a)}"] if a else [])
        if vals:
            lines.append(" · ".join(vals))
        tc += c or 0
        ta += a or 0
        parts.append("\n".join(lines))
    body = f"{who}\nin allegato trovi gli ordini di {sup} per {dest}.\n\n" + "\n\n".join(parts)
    if n > 1 and (tc or ta):
        body += "\n\nTotale: " + " · ".join(([f"{tc:g} colli"] if tc else []) + ([eur_it(ta)] if ta else []))
    body += "\n\nGrazie.\n\nSaluti,\nUfficio Acquisti\nCASA & TE"
    return subject, body


def compose(supplier, orders, settings, day=None, name=None):
    """
    固定模板（意大利文）：orders = [(单号 oid, 单 o)]，一封信管的几张单。返回 (主题, 正文)。
    一家店：主题「Ordine FO-2011-3F9A · CASA&TE Foiano · ACME」；几家店一起（总仓采购一个文件）：
    主题用 BMS 那张（没有就第一张）的订单号，正文按门店分段写每张的订单号和送货地址
    """
    if orders and all(is_deter(o) for _i, o in orders):
        return compose_deter(supplier, orders, settings, day, name=name)
    day = day or date.today()
    sup = str(supplier or "").strip()
    url = booking_url(settings)
    rows = [(oid, o, order_no(oid, o)) for oid, o in orders]
    d0 = min((OT.to_date(o.get("date")) or day) for _i, o, _n in rows) if rows else day

    def colli(o):
        c = o.get("colli")
        try:
            c = float(c)
        except (TypeError, ValueError):
            return ""
        return f"{c:g}" if c else ""
    tail = ("Vi chiediamo cortesemente di:\n"
            "- confermare la ricezione dell'ordine indicando la data di consegna prevista, rispondendo a questa "
            "e-mail senza modificarne l'oggetto;\n"
            "- riportare il numero d'ordine sul documento di trasporto (DDT).\n\n"
            f"Le consegne si ricevono solo su prenotazione: il vettore deve prenotare lo scarico su {url} "
            "indicando il numero d'ordine. Le consegne senza prenotazione verranno respinte.\n\n"
            "Cordiali saluti\nUfficio Acquisti\nCASA & TE")
    if len(rows) == 1:
        oid, o, no = rows[0]
        code = str(o.get("store") or "")
        p = profile(settings, code)
        subject = f"Ordine {no} · CASA&TE {STORE_LABEL.get(code, code)} · {sup}"
        c = colli(o)
        body = (f"Buongiorno,\n\nin allegato il nostro ordine n. {no} del {d0:%d/%m/%Y}.\n\n"
                f"Consegna presso:\n{p['name']}\n{p['address']}\n"
                + (f"\nColli ordinati: {c}\n" if c else "") + "\n" + tail)
        return subject, body
    main = next((r for r in rows if str(r[1].get("store")) == "BMS"), rows[0])
    subject = f"Ordine {main[2]} · CASA&TE {len(rows)} PDV · {sup}"
    parts = []
    for _oid, o, no in rows:
        code = str(o.get("store") or "")
        p = profile(settings, code)
        c = colli(o)
        parts.append(f"- {STORE_LABEL.get(code, code)} – ordine n. {no}" + (f" – colli {c}" if c else "") +
                     f"\n  {p['name']}, {p['address']}")
    body = (f"Buongiorno,\n\nin allegato il nostro ordine del {d0:%d/%m/%Y}. "
            "Ogni punto vendita ha il suo numero d'ordine e la sua consegna:\n\n" + "\n".join(parts) + "\n\n" + tail)
    return subject, body


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
    if is_deter(o):
        # DETERSIVO：同一次比价（同一天）这家批发商几家门店的单一封信（2026-11-22 用户：用 BMS 邮箱一起发五家店）
        ids = [k for k, x in st.orders.items()
               if is_deter(x) and bool(x.get("draft")) == bool(o.get("draft")) and x.get("date") == o.get("date")
               and x.get("state") != "cancel" and st.canon(OT.sup_key(x.get("supplier"))) == sk]
        if oid not in ids:
            ids.append(oid)
    elif f:
        ids = [k for k, x in st.orders.items()
               if OT.is_draft(x) and str(x.get("file") or "").strip() == f and st.canon(OT.sup_key(x.get("supplier"))) == sk]
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
def sender_for(stores, accounts, settings=None, deter=False):
    """
    「订单发件邮箱」设了 → 严格照它：一家店 = 这家店的邮箱，BMS / 几家店一起（总仓采购一个文件）= BMS 的邮箱，
    没设这家 → None（不能发，窗口里写原因）。一个都没设（老设置）→ 照以前的猜法
    """
    accounts = list(accounts or [])
    if not accounts:
        return None
    snd = senders(settings)
    if snd:
        addr = snd.get("BMS" if deter or len(stores) != 1 else stores[0])
        return next((a for a in accounts if addr and a["address"].lower() == addr.lower()), None)
    bms = next((a for a in accounts if "bmspsrl" in a["address"].lower()), None)
    if len(stores) == 1 and stores[0] != "BMS" and not deter:
        own = next((a for a in accounts if str(a.get("store") or "") == stores[0]), None) or \
            next((a for a in accounts if MM.default_store(a["address"]) == stores[0]), None)
        if own:
            return own
    return bms or accounts[0]


SMTP_ALT = {"smtp.qiye.aliyun.com": "smtp.mxhichina.com", "smtp.mxhichina.com": "smtp.qiye.aliyun.com"}


def smtp_check(acc, password, smtp_factory=None, host=None):
    """只登录发信服务器再退出，不发任何信（测试连接用）。失败抛 smtplib / OSError 的错"""
    import smtplib
    h, port = smtp_of(acc)
    s = (smtp_factory or (lambda hh, pp: smtplib.SMTP_SSL(hh, pp, timeout=30)))(host or h, port)
    try:
        s.login(acc["address"], password)
    finally:
        try:
            s.quit()
        except Exception:
            pass
    return host or h


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


def last_sent(history, st, supplier, mailbox=None, strict=False):
    """以前发给这家的最近一封订单信（拿它的正文当模板）；没有就这个邮箱最近发的任何一封订单信（strict 时不找别家的）"""
    sk = st.canon(OT.sup_key(supplier))
    for h in history:
        if h["sup"] == sk or MM._like(h["sup"], sk):
            if not mailbox or not strict or (h["mailbox"] or "").lower() == mailbox.lower():
                return h
    if mailbox and not strict:
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


def already_in(conn, folder, mid, wait=(0, 2, 3)):
    """
    「已发送」里是不是已经有这封信（按 Message-ID 找）。2026-11-22 用户：发一封订单信「已发送」里出现两封——
    阿里企业邮箱用 SMTP 发信时自己会存一份到「已发送」，程序再放一份就重复了。服务器存得慢一点，所以隔几秒多找几次
    """
    import time
    import mail_client as MC
    if not mid:
        return False
    for w in wait:
        if w:
            time.sleep(w)
        try:
            typ, _ = conn.select(MC._quote(folder), readonly=True)
            if typ != "OK":
                return False
            typ, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", f'"{mid}"')
            if typ == "OK" and data and data[0] and data[0].split():
                return True
        except Exception:
            return False
    return False


def send(acc, password, msg, smtp_factory=None, imap_factory=None, wait=(0, 2, 3)):
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
            if sent and already_in(conn, sent[0][0], msg["Message-ID"], wait=wait):
                saved = True             # 服务器自己已经放进「已发送」了（阿里邮箱客户端发信默认保存）：不再放第二份
            elif sent:
                typ, _ = conn.append(MC._quote(sent[0][0]), "(\\Seen)", imaplib.Time2Internaldate(datetime.now().timestamp()),
                                     msg.as_bytes())
                saved = typ == "OK"
        finally:
            MC.close(conn)
    except Exception:
        saved = False
    return msg["Message-ID"], saved


def mark_sent(st, ids, mail, today=None):
    """发出去了：待登记 → 已下单（下单日期 = 今天），单上记发了什么（同一张单不会再发一次）、订单号（之后不再变）"""
    day = (today or date.today()).isoformat()
    gno = (find_order_nos(mail.get("subject")) or [None])[0]
    ch = []
    for oid in ids:
        o = st.orders.get(oid)
        if not o:
            continue
        f = {"draft": None, "date": day, "no": order_no(oid, o), "sent": {"at": datetime.now().isoformat(timespec="seconds"),
                                                   "to": mail["to"], "from": mail["from"], "subject": mail["subject"],
                                                   "mid": mail.get("mid") or ""}}
        if gno and gno != f["no"]:
            f["gno"] = gno                     # 几家店一封信：主题里写的那个号（回信用它也能对上这几张）
        if not o.get("buyer"):
            f["buyer"] = st.me
        mails = list(o.get("mails") or [])
        ev = {"k": "order", "d": datetime.now().strftime("%Y-%m-%d %H:%M"), "s": mail["subject"][:120], "f": mail["from"]}
        if mail.get("mid"):
            ev["id"] = mail["mid"]
        mails.append(ev)
        f["mails"] = mails
        ch.append((oid, f))
    st.set_many(ch, src="mail")
    return len(ch)
