"""
邮件 → 订单跟踪（2026-11-07 用户：邮件摸底看完，开始做「订单跟踪按邮件自动更新」）。不依赖界面，可以单独测试。

只看信头（主题、发件人、收件人、附件名、日期、Message-ID / In-Reply-To），不读正文。
每封信先认出：哪一类（我们发的订单 / 供应商确认 / 发货 / 发票）、哪家供应商、哪家门店；再对到订单跟踪里的单，
只在会改东西时给出「建议」（待登记 → 已下单、已下单 → 已确认、记下发货日期），人确认后才写（apply）。

怎么认：
· 类别：看主题（去掉 Re: / R: / Fwd:），我们「已发送」里主题以 Ordine / Scheda 开头、或附件叫 Ordine…xlsx 的 = 下单；
  供应商回复我们的订单信（Re: Ordine SAIS …）也算确认。
· 供应商：订单跟踪里出现过的供应商名字出现在主题 / 附件名 / 发件人名字 / 发件域名里（短名字 HD、597 这种要整个词对上）；
  我们发订单时发给了谁 → 记下「这个地址 / 域名 = 这家供应商」，它回的信就认得出（gmail 这类公共邮箱按完整地址记）。
· 门店：主题 / 附件名里的 FOIANO / CAPANNORI / MARLIA / FILIPPO / MARCO / CONCORDIO / BMS；没写就用邮箱默认的门店（邮箱设置里选）。
· 回复同一封订单信的（In-Reply-To）直接对到那封信对上的单。
"""
import hashlib
import re
import unicodedata
from datetime import date, datetime, timedelta

import order_tracking as OT

KINDS = ("order", "confirm", "ship", "invoice")
_RE_PREFIX = re.compile(r"^\s*((re|r|fw|fwd|i|inoltra|aw|wg|rif|回复|转发)\s*[:：]\s*)+", re.I)
_KIND_RULES = (
    ("ship", re.compile(r"\bbolla\b|\bbolle\b|\bddt\b|spedizion|spedit|\bpartit[oa]\b|packing|merce spedita|avviso di merce|"
                        r"\bcmr\b|tracking|consegna", re.I)),
    ("invoice", re.compile(r"fattur|invoice|nota di credito|\bdoc\.?\s*n|documento n|documento contabile|"
                           r"comunicazioni elettroniche|\bfatt\.", re.I)),
    ("confirm", re.compile(r"conferma|proposta d.ordine|preventivo|preordine|pre-ordine|inserimento ordine|ordine di vendita|"
                           r"customer order|ordine n[.°o]|ordine da client|order confirm", re.I)),
)
_SKIP = re.compile(r"sollecit|scadut|scadenz|estratto conto|partite aperte|insolut|mancato pagamento|listin|catalog|"
                   r"novit[aà]|brochure|newsletter|undeliverable|risposta automatica|automatic reply", re.I)
_ORDER_SUBJ = re.compile(r"^(ordine|scheda)(?![a-z])", re.I)      # Reso（退货）不算下单
# 门店关键词（大写、去掉标点后）
STORE_WORDS = (("FOIANO", "1"), ("CAPANNORI", "2"), ("MARLIA", "2"), ("FILIPPO", "3"), ("SFILIPPO", "3"),
               ("MARCO", "4"), ("SMARCO", "4"), ("CONCORDIO", "5"), ("SCONCORDIO", "5"), ("BMS", "BMS"))
FREE_MAIL = {"gmail.com", "libero.it", "icloud.com", "virgilio.it", "hotmail.com", "hotmail.it", "yahoo.com", "yahoo.it",
             "outlook.com", "outlook.it", "live.com", "live.it", "tiscali.it", "alice.it", "tim.it", "email.it", "pec.it"}
# 供应商名字里不算数的词（单独出现不能说明是这家）
_GENERIC = {"SRL", "SPA", "SAS", "SNC", "SRLS", "ITALIA", "ITALY", "ITALIANA", "GROUP", "GRUPPO", "INDUSTRIE", "INDUSTRY",
            "SYSTEM", "SYSTEMS", "COMMERCIALE", "DISTRIBUZIONE", "INTERNATIONAL", "FLLI", "FRATELLI", "TRADING",
            "INVERNALE", "CASA", "CASATE", "PARTNERS", "SERVICE", "SERVIZI", "ORDINE", "ORDINI", "CONFERMA", "FATTURA",
            "NUOVO", "NUOVA", "NEW", "VISION", "IDEA", "MAGIC", "ROYAL", "OFFICE", "BEST", "LUCCA", "SOCIO", "UNICO",
            "AGENTE", "CLIENTE", "INFO", "MAIL", "VENDITE", "AMMINISTRAZIONE", "COMMERCIALE", "ACQUISTI", "PDV",
            "CENTRALE", "BMSP", "BMSPSRL"}
ORDER_WINDOW = (4, 1)       # 发订单的信：单子日期在信前 4 天到后 1 天
FOLLOW_WINDOW = 35          # 确认 / 发货：单子日期在信前 35 天内


def _up(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper()


def words(s):
    return re.findall(r"[A-Z0-9]+", _up(s).replace("F.LLI", "FLLI"))


def clean_subject(s):
    return _RE_PREFIX.sub("", str(s or "")).strip()


def is_reply(s):
    return bool(_RE_PREFIX.match(str(s or "")))


def domain(addr):
    a = (addr or "").strip().lower()
    return a.rsplit("@", 1)[1] if "@" in a else ""


def sender_key(addr):
    """记「这个人 = 这家供应商」用的键：公共邮箱按完整地址，公司邮箱按域名"""
    d = domain(addr)
    return (addr or "").strip().lower() if (not d or d in FREE_MAIL) else d


def _addrs(text):
    return [a.strip().lower() for a in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text or "")]


def kind_of(m, own):
    """一封信是哪一类：order / confirm / ship / invoice / None。own = 我们自己的邮箱地址（小写集合）"""
    subj = str(m.get("subject") or "")
    s = clean_subject(subj)
    atts = m.get("attachments") or []
    sent = (m.get("from") or "").lower() in own
    if sent:
        if not is_reply(subj) and (_ORDER_SUBJ.match(s) or any(re.match(r"ordine", a or "", re.I) for a in atts)):
            return "order"
        return None                                   # 我们发的回复 / 别的信不算
    if _SKIP.search(s):
        return None
    for k, rx in _KIND_RULES:
        if rx.search(s):
            return k
    if is_reply(subj) and _ORDER_SUBJ.match(s):      # 供应商回复我们的订单信
        return "confirm"
    return None


_BMS_CO = re.compile(r"\bBMS\s*(?:P(?:A?R?T?\w*)?\b|PSRL)\.?", re.I)     # BMS PARTNERS（公司名，不是 BMS 仓库）
_PDV = re.compile(r"\b\d\s*PDV\b", re.I)                                  # 「Ordine X 5PDV」= 几家门店一起的单


def stores_in(text):
    ws = words(_BMS_CO.sub(" ", str(text or "")))
    j = "".join(ws)
    out = []
    for w, code in STORE_WORDS:
        hit = w in ws if len(w) <= 5 else (w in j)
        if hit and code not in out:
            out.append(code)
    return out


_STORE_SET = {w for w, _c in STORE_WORDS}


def default_store(address):
    """邮箱地址 → 默认门店（没写门店的信算这家）；BMS 采购办公室的邮箱管所有门店，不设默认"""
    a = (address or "").lower()
    for w, code in (("filippo", "3"), ("marco", "4"), ("concordio", "5"), ("marlia", "2"), ("capannori", "2"),
                    ("lucca", "2"), ("foiano", "1")):
        if w in a:
            return code
    if a.startswith("info@casa-te."):
        return "1"
    return None


def subject_day(subject, day, full=False):
    """主题里的订单日期「Ordine SAIS S.Marco 2010」= 20/10（我们自己的写法：ddmm）。合理的（发信前 90 天内）才算。
    full：也认「Ordine n. 6446 del 04/11/2026」这种完整日期（供应商确认信里的是订单日期；发货信里的是 DDT 日期，不能用）"""
    s = clean_subject(subject)
    if full:
        for dd, mm, yy in re.findall(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(?!\d)", s):
            try:
                y = int(yy) + (2000 if len(yy) == 2 else 0)
                d = date(y, int(mm), int(dd))
            except ValueError:
                continue
            if day - timedelta(days=90) <= d <= day + timedelta(days=1):
                return d
    for t in reversed(re.findall(r"(?<![\d/.-])(\d{4})(?![\d/.-])", s)):
        dd, mm = int(t[:2]), int(t[2:])
        if 1 <= dd <= 31 and 1 <= mm <= 12:
            for y in (day.year, day.year - 1):
                try:
                    d = date(y, mm, dd)
                except ValueError:
                    continue
                if day - timedelta(days=90) <= d <= day + timedelta(days=1):
                    return d
    return None


class SupplierIndex:
    """订单跟踪里的供应商：名字 → 能认它的词"""

    def __init__(self, st):
        self.st = st
        self.names = {}                     # canon 键 → {"keys": {sup_key}, "words": {词}}
        spell = {}
        for o in st.orders.values():
            n = str(o.get("supplier") or "").strip()
            if not n:
                continue
            k = st.canon(OT.sup_key(n))
            spell.setdefault(k, {}).setdefault(n, 0)
            spell[k][n] += 1
            d = self.names.setdefault(k, {"words": set(), "keys": set()})
            sk = OT.sup_key(n)
            d["keys"].add(sk)
            for w in words(n):
                if len(w) >= 4 and w not in _GENERIC and w not in _STORE_SET and not w.isdigit():
                    d["words"].add(w)
        # 显示名：最常见的写法（一样多取有小写、长一点的）
        self.display = {k: max(c.items(), key=lambda kv: (kv[1], kv[0] != kv[0].upper(), len(kv[0])))[0]
                        for k, c in spell.items()}

    def find(self, text):
        """文字里提到的供应商（canon 键的列表，长名字优先）"""
        ws = words(text)
        wset = set(ws)
        j = "".join(ws)
        hits = []
        for k, d in self.names.items():
            ok = False
            for sk in d["keys"]:
                if len(sk) >= 5 and sk in j or len(sk) < 5 and sk in wset:
                    ok = True
                    break
            if not ok:
                ok = any(w in wset or (len(w) >= 6 and w in j) for w in d["words"])
            if ok:
                hits.append(k)
        # 一个名字是另一个的一部分（SAIS / SAISINVERNALE）：都留着，配单时再按日期挑
        return sorted(hits, key=lambda k: -len(k))


def _parse_date(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    try:
        return datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


def analyze(mails, st, own, mailbox_store=None, today=None, ignored=None):
    """
    mails = fetch_headers 的结果（每封多 mailbox / folder）；st = 订单跟踪 Store；own = 自己的邮箱地址集合；
    mailbox_store = {邮箱地址小写: 门店代码}（没写门店时用）。
    返回 {"suggest": [建议], "unmatched": [没对上的订单信], "events": [每封认出来的信]}
    建议 = {"key", "id", "kind", "field", "value", "old", "mail": {...}, "store", "supplier", "order_date", "status"}
    """
    own = {a.lower() for a in own}
    mailbox_store = {k.lower(): v for k, v in (mailbox_store or {}).items()}
    ignored = ignored or set()
    idx = SupplierIndex(st)
    ms = []
    for m in mails:
        d = _parse_date(m.get("date"))
        if d is None:
            continue
        k = kind_of(m, own)
        if k:
            ms.append(dict(m, _date=d, _kind=k))
    ms.sort(key=lambda m: m["_date"])
    learned = {}                               # sender_key → canon 供应商键
    thread = {}                                # Message-ID → [单号]
    events, suggest, unmatched = [], {}, []
    by_sup, deter = {}, set()
    twins = {d for d, _m in duplicates(st)}      # 和 Excel 手填重复的待登记：邮件算在手填的那张上，待登记另外建议合并
    for oid, o in st.orders.items():
        if oid in twins:
            continue
        if o.get("kind") == "deter":                  # DETERSIVO 批发商的单不跟踪确认 / 到货
            deter.add(st.canon(OT.sup_key(o.get("supplier"))))
        if o.get("state") == "cancel" or o.get("kind") == "deter":
            continue
        by_sup.setdefault(st.canon(OT.sup_key(o.get("supplier"))), []).append(oid)
    # 先过一遍我们发的订单：学会「发给谁 = 哪家」
    for m in ms:
        if m["_kind"] != "order":
            continue
        text = " ".join([m.get("subject") or ""] + list(m.get("attachments") or []))
        sups = idx.find(text)
        if sups:
            for a in _addrs(m.get("to")):
                if a not in own and not a.endswith("@casa-te.com"):
                    learned.setdefault(sender_key(a), sups[0])
    for m in ms:
        kind = m["_kind"]
        sent = kind == "order"
        atts = list(m.get("attachments") or [])
        subj = m.get("subject") or ""
        frm = (m.get("from") or "").lower()
        text = " ".join([subj] + atts + ([] if sent else [m.get("from_name") or "", domain(frm).split(".")[0]]))
        sups = idx.find(" ".join([subj] + atts)) or ([] if sent else idx.find(text))
        if not sups and not sent and learned.get(sender_key(frm)):
            sups = [learned[sender_key(frm)]]
        # 门店：主题 + 每个附件
        stores = stores_in(subj)
        for a in atts:
            for s_ in stores_in(a):
                if s_ not in stores:
                    stores.append(s_)
        day = m["_date"].date()
        multi = bool(_PDV.search(subj))
        if not stores and not multi:
            dflt = mailbox_store.get((m.get("mailbox") or "").lower())
            if dflt:
                stores = [dflt]
        want = subject_day(subj, day, full=(kind == "confirm"))
        oids = []
        parent = thread.get((m.get("in_reply_to") or "").strip())
        if parent and not sent:
            oids = list(parent)
        elif sups:
            if want:
                lo, hi = want - timedelta(days=2), want + timedelta(days=2)
            elif sent:
                lo, hi = day - timedelta(days=ORDER_WINDOW[0]), day + timedelta(days=ORDER_WINDOW[1])
            else:
                lo, hi = day - timedelta(days=FOLLOW_WINDOW), day
            for sk in sups:
                cand = []
                for k2, ids in by_sup.items():
                    if k2 == sk or (len(sk) >= 4 and len(k2) >= 4 and (sk in k2 or k2 in sk)):
                        cand += ids
                cand = [oid for oid in cand if lo <= (OT.to_date(st.orders[oid].get("date")) or date.min) <= hi
                        and (not stores or str(st.orders[oid].get("store")) in stores)]
                if not cand:
                    continue
                ref = want or day
                if stores or sent:
                    # 每家门店挑日期最近的那一张（同一天程序待登记 + Excel 手填的两张都算）
                    best = {}
                    for oid in cand:
                        o = st.orders[oid]
                        gap = abs((OT.to_date(o.get("date")) - ref).days)
                        s_ = str(o.get("store"))
                        if s_ not in best or gap < best[s_][0]:
                            best[s_] = (gap, [oid])
                        elif gap == best[s_][0]:
                            best[s_][1].append(oid)
                    for _g, ids in best.values():
                        oids += ids
                else:
                    # 没写门店（采购办公室的确认 / 发货信）：离信最近的那一批（同一天前后 1 天，几家门店一起下的）
                    last = max(OT.to_date(st.orders[oid].get("date")) for oid in cand
                               if OT.to_date(st.orders[oid].get("date")) <= ref) \
                        if any(OT.to_date(st.orders[oid].get("date")) <= ref for oid in cand) else \
                        min(OT.to_date(st.orders[oid].get("date")) for oid in cand)
                    oids = [oid for oid in cand if abs((OT.to_date(st.orders[oid].get("date")) - last).days) <= 1]
                break
        ev = {"date": day, "kind": kind, "subject": subj, "from": frm, "mailbox": m.get("mailbox"),
              "message_id": m.get("message_id"), "suppliers": sups, "stores": stores, "orders": oids}
        events.append(ev)
        if sent and m.get("message_id"):
            thread[m["message_id"].strip()] = oids
        if sent and not oids and sups and not any(_like(sups[0], d) for d in deter):
            ev["supplier_name"] = idx.display.get(sups[0], sups[0])
            ev["multi"] = multi
            ev["want"] = want
            ev["attachments"] = atts
            unmatched.append(ev)
        for oid in oids:
            o = st.orders.get(oid) or {}
            s = OT.status(o)
            sg = None
            if kind == "order" and o.get("draft"):
                sg = ("draft", None, day.isoformat())
            elif kind in ("confirm", "ship") and s in ("ordered", "draft") and not o.get("confirmed"):
                sg = ("confirmed", None, day.isoformat())
            if kind == "ship" and s in ("ordered", "confirmed", "draft") and not o.get("shipped") and not o.get("arrived"):
                # 先确认、再发货：发货信也说明对方收到了订单
                key = f"{oid}|shipped"
                if key not in ignored and key not in suggest:
                    suggest[key] = _sg(key, oid, o, "ship", "shipped", day.isoformat(), m, st)
            if sg:
                field, _old, val = sg
                key = f"{oid}|{field}"
                if key not in ignored and key not in suggest:
                    suggest[key] = _sg(key, oid, o, "order" if field == "draft" else kind, field, val, m, st)
    missing = []
    for ev in unmatched:
        sk = OT.sup_key(ev["supplier_name"])
        for code in (ev["stores"] or [""]):
            key = f"miss|{ev['message_id'] or ev['subject']}|{code}"
            if key in ignored:
                continue
            d0 = ev.get("want") or ev["date"]
            # 同一家、同一门店前后 30 天里已有的单（可能就是这张、只是日期填得不一样）
            near = sorted((abs((OT.to_date(o.get("date")) - d0).days), OT.to_date(o.get("date")))
                          for o in st.orders.values()
                          if OT.to_date(o.get("date")) and (not code or str(o.get("store")) == code)
                          and o.get("state") != "cancel" and _like(st.canon(OT.sup_key(o.get("supplier"))), st.canon(sk))
                          and abs((OT.to_date(o.get("date")) - d0).days) <= 30)
            missing.append({"key": key, "id": None, "kind": "missing", "field": "missing", "value": d0.isoformat(),
                            "near": near[0][1] if near else None,
                            "status": "", "store": code, "supplier": ev["supplier_name"], "order_date": d0,
                            "mail": {"date": datetime(ev["date"].year, ev["date"].month, ev["date"].day),
                                     "subject": ev["subject"], "from": ev["from"], "mailbox": ev["mailbox"] or "",
                                     "message_id": ev["message_id"] or "", "attachments": ev["attachments"]}})
    return {"suggest": sorted(suggest.values(), key=lambda x: (x["mail"]["date"], x["supplier"], x["store"])),
            "missing": missing, "unmatched": unmatched, "events": events}


def _like(a, b):
    return a == b or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a))


def duplicates(st, days=3):
    """
    程序生成的「待登记」和 Excel 里手填的是同一张单（同一家门店、供应商名字一个包含另一个（F.LLI ZAMBETTI SRL / ZAMBETTI）、
    下单日期前后 days 天内）→ [(待登记单号, 手填单号)]。2026-11-07 真实数据：73 张待登记里 17 张是这样重复的。
    """
    man = [(oid, o) for oid, o in st.orders.items()
           if not o.get("draft") and o.get("state") != "cancel" and o.get("kind") != "deter"]
    out = []
    for oid, o in st.orders.items():
        if not o.get("draft") or o.get("state") == "cancel":
            continue
        a, d = st.canon(OT.sup_key(o.get("supplier"))), OT.to_date(o.get("date"))
        if not d:
            continue
        best = None
        for m_id, m in man:
            d2 = OT.to_date(m.get("date"))
            if not d2 or str(m.get("store")) != str(o.get("store")) or abs((d2 - d).days) > days:
                continue
            if not _like(a, st.canon(OT.sup_key(m.get("supplier")))):
                continue
            gap = abs((d2 - d).days)
            if best is None or gap < best[0]:
                best = (gap, m_id)
        if best:
            out.append((oid, best[1]))
    return out


def dup_suggestions(st, ignored=None):
    ignored = ignored or set()
    out = []
    for oid, mid in duplicates(st):
        key = f"{oid}|dup"
        if key in ignored:
            continue
        o, m = st.orders[oid], st.orders[mid]
        out.append({"key": key, "id": oid, "twin": mid, "kind": "dup", "field": "dup", "value": None,
                    "status": "draft", "store": str(o.get("store") or ""), "supplier": str(o.get("supplier") or ""),
                    "order_date": OT.to_date(o.get("date")),
                    "twin_text": f"{m.get('supplier') or ''} · {OT.to_date(m.get('date')).strftime('%d/%m') if OT.to_date(m.get('date')) else ''}",
                    "mail": None})
    return out


def merge_duplicates(st, sugs):
    """
    合并重复的待登记：订单文件 / 箱数 / 金额（手填的没写才补）挂到手填的那张单上，待登记删掉。
    返回 (几张, 撤销用 [("set", 单号, 旧字段) / ("restore", 单号, 全部字段)])
    """
    ch, undo, gone = [], [], []
    for s in sugs:
        o, m = st.orders.get(s["id"]), st.orders.get(s.get("twin"))
        if not o or not m or not o.get("draft"):
            continue
        f = {k: o.get(k) for k in ("file", "colli", "amount") if o.get(k) not in (None, "") and m.get(k) in (None, "")}
        if f:
            undo.append(("set", s["twin"], {k: m.get(k) for k in f}))
            ch.append((s["twin"], f))
        undo.append(("restore", s["id"], dict(o)))
        gone.append(s["id"])
    st.set_many(ch, src="mail")
    st.delete(gone, src="mail")
    return len(gone), undo


def add_missing(st, sugs):
    """邮件里发了订单、跟踪里没有 → 补登记（已下单，下单日期 = 发信那天，来源 mail；金额没有，要的话自己填）。
    门店不清楚的（「5PDV」又没写哪几家）跳过。返回 (几张, 撤销用)"""
    ch, undo_ = [], []
    for s in sugs:
        if not s.get("store"):
            continue
        oid = "m" + hashlib.sha1(s["key"].encode("utf-8")).hexdigest()[:12]
        if oid in st.orders:
            continue
        m = s["mail"]
        ch.append((oid, {"date": s["value"], "store": s["store"], "supplier": s["supplier"], "origin": "mail",
                         "buyer": st.me, "mails": [{"k": "order", "d": m["date"].strftime("%Y-%m-%d %H:%M"),
                                                    "s": m["subject"][:120], "f": m["from"]}]}))
        undo_.append(("drop", oid, None))
    st.set_many(ch, src="mail")
    return len(ch), undo_


def undo(st, items):
    """撤销 apply / merge_duplicates：把旧值写回去、删掉的单恢复"""
    st.set_many([(oid, f) for kind, oid, f in items if kind == "set"], src="mail")
    st.set_many([(oid, f) for kind, oid, f in items if kind == "restore"], src="mail")
    st.delete([oid for kind, oid, _f in items if kind == "drop"], src="mail")


def _sg(key, oid, o, kind, field, value, m, st):
    return {"key": key, "id": oid, "kind": kind, "field": field, "value": value,
            "status": OT.status(o), "store": str(o.get("store") or ""), "supplier": str(o.get("supplier") or ""),
            "order_date": OT.to_date(o.get("date")),
            "mail": {"date": m["_date"], "subject": m.get("subject") or "", "from": (m.get("from") or "").lower(),
                     "mailbox": m.get("mailbox") or "", "message_id": m.get("message_id") or "",
                     "attachments": list(m.get("attachments") or [])}}


def changes_of(sugs, me=None):
    """要写进订单跟踪的修改 [(单号, {字段: 值})]：同一张单的合在一起；待登记 → 已下单 = 去掉 draft、下单日期 = 发信那天"""
    out = {}
    for s in sugs:
        f = out.setdefault(s["id"], {})
        if s["field"] == "draft":
            f["draft"] = None
            f["date"] = s["value"]
            if me:
                f.setdefault("buyer", me)
        else:
            f[s["field"]] = s["value"]
            if s.get("status") == "draft":           # 供应商都确认 / 发货了：肯定已经下单
                f["draft"] = None
        ev = {"k": s["kind"], "d": s["mail"]["date"].strftime("%Y-%m-%d %H:%M") if isinstance(s["mail"]["date"], datetime)
              else str(s["mail"]["date"]), "s": s["mail"]["subject"][:120], "f": s["mail"]["from"]}
        f.setdefault("_mails", []).append(ev)
    return out


def apply(st, sugs, src="mail"):
    """写进订单跟踪（共享文件夹的修改记录，来源 mail）。返回 (几张单, 撤销用 [("set", 单号, {字段: 旧值})]，给 undo)"""
    ch = changes_of(sugs, me=st.me)
    undo, out = [], []
    for oid, f in ch.items():
        o = st.orders.get(oid)
        if o is None:
            continue
        mails = list(o.get("mails") or [])
        for e in f.pop("_mails", []):
            if e not in mails:
                mails.append(e)
        f["mails"] = mails[-20:]
        if "buyer" in f and o.get("buyer"):
            f.pop("buyer")
        undo.append(("set", oid, {k: o.get(k) for k in f}))
        out.append((oid, f))
    st.set_many(out, src=src)
    return len(out), undo
