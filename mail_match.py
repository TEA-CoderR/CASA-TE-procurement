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


def _bms_only(st, cand):
    """
    我们发出的 BMS / nPDV 订单信能对上的单：BMS 那张（几家店凑的一张单）；没有 BMS 单、对上的是 DETERSIVO 批发商的单
    （origin wholesale：一封信写五家店、每家店各送各的）→ 每家店那张。别的（总仓采购的门店直送单）一律不算
    """
    b = [o for o in cand if str(st.orders[o].get("store")) == "BMS"]
    if b:
        return b
    return [o for o in cand if st.orders[o].get("origin") == "wholesale" or st.orders[o].get("kind") == "deter"]


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


_DEST_LINE = re.compile(r"(ragione\s+sociale|destinat|destinazione|consegna|spedizione|cliente|intestat|negozio|punto\s+vendita)",
                       re.I)


def stores_in_body(body):
    """
    正文里写的是哪家店（发货 / 确认信主题里不写门店，正文写「Ragione sociale: CASA & TE SAN MARCO S.R.L」）。
    先看「Ragione sociale / Destinazione / Consegna…」那几行，只认出一家才算；整封信只提到一家也算。认不出返回 []。
    """
    lines = str(body or "").splitlines()
    key = [l for l in lines if _DEST_LINE.search(l)]
    for chunk in (key, lines):
        found = []
        for l in chunk:
            for c in stores_in(l):
                if c not in found:
                    found.append(c)
        if len(found) == 1:
            return found
        if len(found) > 1 and chunk is key:
            return []                       # 收货那几行就写了好几家：说不清
    return []


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
        self.spell = spell

    def best_name(self, ks, text):
        """几个认出来的供应商里，名字在 text 里出现得最完整的那一家的写法"""
        got = [self._scored(k, text) for k in ks]
        got = [g for g in got if g]
        if got:
            return max(got)[1]
        return self.display.get(ks[0], ks[0]) if ks else ""

    def _scored(self, k, text):
        tw = set(words(text))
        best = None
        for n, cnt in (self.spell.get(k) or {}).items():
            ws = [w for w in words(n) if len(w) >= 3]
            if not ws:
                continue
            hit = sum(1 for w in ws if w in tw)
            score = (hit, hit == len(ws), cnt, n != n.upper())     # 对上的词最多 → 全对上 → 最常见
            if hit and (best is None or score > best[0]):
                best = (score, n)
        return best

    def name_in(self, k, text):
        """在 text（邮件主题 / 附件名）里出现得最完整的那种写法；都对不上用最常见的。
        （Excel 里有手误的写法，例如「MARLYAMARCHETTI ANGIOLO」，信里写的是「MARCHETTI ANGIOLO」）"""
        g = self._scored(k, text)
        return g[1] if g else self.display.get(k, k)

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


def analyze(mails, st, own, mailbox_store=None, today=None, ignored=None, ks=None):
    """
    mails = fetch_headers 的结果（每封多 mailbox / folder）；st = 订单跟踪 Store；own = 自己的邮箱地址集合；
    mailbox_store = {邮箱地址小写: 门店代码}（没写门店时用）。
    ks = mail_link.keys(...)（每家店的收货抬头 / 地址）：正文 / 附件读过的信（m["body"] / m["att_text"]）按地址认门店。
    一封供应商的确认 / 发货信只对一张单（2026-11-25 用户：一定要一一关联）：对不上、或者好几张都对得上的
    不给建议，放进 pending（「待处理邮件」，人选是哪一张）。
    返回 {"suggest": [建议], "pending": [待处理], "unmatched": [没对上的订单信], "events": [每封认出来的信]}
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
            ms.append(dict(m, _date=d, _kind=k, _orig=m))
    ms.sort(key=lambda m: m["_date"])
    learned = {}                               # sender_key → canon 供应商键
    thread = {}                                # Message-ID → [单号]
    events, suggest, unmatched = [], {}, []
    by_sup, deter = {}, set()
    twins = {d for d, _m in duplicates(st)}      # 和 Excel 手填重复的待登记：邮件算在手填的那张上，待登记另外建议合并
    import mail_link as ML
    if ks is None:
        try:
            ks = ML.keys(None)
        except Exception:
            ks = {}
    linked = {}                                # 已经关联过的信（单上 mails 记着 Message-ID）→ 单
    for oid, o in st.orders.items():
        for e in o.get("mails") or []:
            if isinstance(e, dict) and e.get("id"):
                linked.setdefault(str(e["id"]).strip(), [])
                if oid not in linked[str(e["id"]).strip()]:
                    linked[str(e["id"]).strip()].append(oid)
    pending = []
    by_no = {}                                 # 程序发信时记的订单号 → 单（2026-11-21：回信 / DDT 里写了订单号 = 准确对上）
    for oid, o in st.orders.items():
        for k in (o.get("no"), o.get("gno")):
            if k:
                by_no.setdefault(str(k).upper(), []).append(oid)
    by_void = {}
    try:                                       # BMS 凑单里每家店那一份（PIANO 标 CEN 的）：邮件只对 BMS 单，不对每家店
        members = {m_ for g in OT.groups(st).values() for m_ in g["members"]}
    except Exception:
        members = set()
    by_member = {}                             # BMS 凑单里每家店那一份：只有信里写了这家门店才对它（不然对 BMS 单）
    for oid, o in st.orders.items():
        if oid in members or o.get("part_of"):
            if oid not in twins and o.get("state") != "cancel":
                by_member.setdefault(st.canon(OT.sup_key(o.get("supplier"))), []).append(oid)
            continue
        if oid in twins:
            continue
        if o.get("kind") == "deter":                  # DETERSIVO 批发商的单不跟踪确认 / 到货
            deter.add(st.canon(OT.sup_key(o.get("supplier"))))
        if o.get("void") and OT.is_draft(dict(o, state=None)):
            # 作废的待登记（2026-11-26）：只给「我们发出的订单信」对 —— 说明其实用邮件软件发出去了，应用后回到正常的单
            by_void.setdefault(st.canon(OT.sup_key(o.get("supplier"))), []).append(oid)
            continue
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
        # 我们发出的「Ordine X 4PDV / BMS」= 几家店凑成的一张 BMS 单（2026-11-28 用户：L'OREAL 4PDV 是一张 BMS 单，
        # 邮件核对却给五家门店的待登记都对上了）→ 只对 BMS 那张单；DETERSIVO 批发商的 nPDV 信才是每家店一张（照旧）
        bms_sent = sent and (multi or "BMS" in stores)
        src = "name" if stores else ""
        addr_cands = []
        if not sent and ks and (m.get("body") or m.get("att_text")):
            # 正文 / 附件里的收货抬头和地址（最准：DDT、确认单上一定写送到哪）
            code, info = ML.store_of("\n".join([m.get("body") or "", m.get("att_text") or ""]), ks)
            if code:
                stores, src, multi = [code], "addr", False
            else:
                addr_cands = info
        if not stores and not multi and not sent and m.get("body"):
            stores = stores_in_body(m["body"])          # 正文写了收货门店（SAIS 的 DDT 信）
            src = "body" if stores else src
        if not stores and not multi:
            dflt = mailbox_store.get((m.get("mailbox") or "").lower())
            if dflt:
                stores, src = [dflt], "mailbox"
        want = subject_day(subj, day, full=(kind == "confirm"))
        oids = []
        parent = thread.get((m.get("in_reply_to") or "").strip())
        by_code = list(dict.fromkeys(oid for c in OT.find_order_nos(" ".join([subj] + atts + [m.get("body") or ""]))
                                     for oid in by_no.get(c, [])))
        how = ""
        covered = False
        mid = (m.get("message_id") or "").strip()
        if mid and mid in linked and not sent:
            oids, how = list(linked[mid]), "link"          # 以前已经关联过（人选的 / 应用过的）
        elif by_code and not sent:
            oids, how = by_code, "no"                      # 写了我们的订单号：就是这几张，不用再猜
            stores = stores or list(dict.fromkeys(str(st.orders[o].get("store")) for o in oids))
        elif parent and not sent:
            oids, how = list(parent), "thread"
        elif sups:
            if want:
                lo, hi = want - timedelta(days=2), want + timedelta(days=2)
            elif sent:
                lo, hi = day - timedelta(days=ORDER_WINDOW[0]), day + timedelta(days=ORDER_WINDOW[1])
            else:
                lo, hi = day - timedelta(days=FOLLOW_WINDOW), day
            shop = [s_ for s_ in stores if s_ != "BMS"]
            for sk in sups:
                cand = []
                for k2, ids in list(by_sup.items()) + (list(by_member.items()) if shop else []):
                    if k2 == sk or (len(sk) >= 4 and len(k2) >= 4 and (sk in k2 or k2 in sk)):
                        cand += ids
                cand = [oid for oid in cand if lo <= (OT.to_date(st.orders[oid].get("date")) or date.min) <= hi
                        and (not stores or str(st.orders[oid].get("store")) in stores)]
                hand = []
                if bms_sent:
                    # 同事已经在 PIANO 各门店那一块手填了这张单的每家店那一份（没标 CEN）：不再把作废的 BMS 待登记
                    # 恢复出来（不然 BMS 单 + 每家店的又算一遍）；要标 CEN 用「BMS 凑单核对」
                    hand = [o for o in cand if str(st.orders[o].get("store")) != "BMS" and not st.orders[o].get("draft")
                            and st.orders[o].get("origin") != "wholesale"]
                    cand = _bms_only(st, cand)
                    if hand and not cand:
                        covered = True
                if sent and not cand and not hand:
                    for k2, ids in by_void.items():
                        if k2 == sk or (len(sk) >= 4 and len(k2) >= 4 and (sk in k2 or k2 in sk)):
                            cand += [oid for oid in ids if lo <= (OT.to_date(st.orders[oid].get("date")) or date.min) <= hi
                                     and (not stores or str(st.orders[oid].get("store")) in stores)]
                    if bms_sent:
                        cand = _bms_only(st, cand)
                if not sent and any(not st.orders[oid].get("draft") for oid in cand):
                    # 供应商的确认 / 发货信只可能是发出去了的单：有已下单的就不算待登记（Royal 8044 = BMS 那张，不是三张待登记）
                    cand = [oid for oid in cand if not st.orders[oid].get("draft")]
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
        # 没写门店、却对上了好几家门店的单：一张 DDT / 确认通常只管一家店 → 标「门店不确定」（默认不勾，要先读正文）
        unsure = (not sent and not stores and not multi and
                  len({str(st.orders[o].get("store")) for o in oids}) > 1)
        ev = {"date": day, "kind": kind, "subject": subj, "from": frm, "mailbox": m.get("mailbox"),
              "message_id": m.get("message_id"), "suppliers": sups, "stores": stores, "orders": oids,
              "unsure": unsure, "need_body": False, "row": m.get("_orig", m), "how": how, "store_src": src}
        follow = not sent and kind in ("confirm", "ship")
        if follow and sups and how not in ("link", "no", "thread") and not m.get("_texts") \
                and (len(oids) != 1 or src in ("", "mailbox")):
            ev["need_text"] = True                     # 读正文 + 附件看收货地址（commands.mail_tracking 读完再对一次）
        if follow and sups and how != "link" and len(oids) != 1:
            # 一一关联：对不上 / 好几张都对得上 → 不自动改，放进「待处理邮件」
            cand = list(oids)
            if not cand:
                lo = day - timedelta(days=FOLLOW_WINDOW)
                for k2, ids in by_sup.items():
                    if any(k2 == sk or (len(sk) >= 4 and len(k2) >= 4 and (sk in k2 or k2 in sk)) for sk in sups):
                        cand += [oid for oid in ids if lo <= (OT.to_date(st.orders[oid].get("date")) or date.min) <= day]
                cand = sorted(set(cand), key=lambda o_: OT.to_date(st.orders[o_].get("date")) or date.min, reverse=True)
            cand = [o_ for o_ in cand if _would_change(st.orders[o_], kind)]
            key = f"pend|{mid or subj}"
            if cand and key not in ignored:
                pending.append({
                    "key": key, "kind": "pending", "mkind": kind, "candidates": cand[:12], "stores": stores,
                    "store_src": src, "addr_cands": addr_cands,
                    "supplier": idx.best_name(sups, " ".join([subj] + atts)) if hasattr(idx, "best_name") else sups[0],
                    "why": "many" if oids else "none", "store": stores[0] if len(stores) == 1 else "",
                    "body": (m.get("body") or "")[:3000], "att_text": (m.get("att_text") or "")[:3000],
                    "mail": {"date": m["_date"], "subject": subj, "from": frm, "mailbox": m.get("mailbox") or "",
                             "message_id": mid, "attachments": atts},
                    "_m": {k_: m.get(k_) for k_ in ("_date", "subject", "from", "mailbox", "message_id", "attachments")}})
            oids = []                                  # 不给建议
            ev["orders"] = []
            ev["pending"] = True
        events.append(ev)
        if sent and m.get("message_id"):
            thread[m["message_id"].strip()] = oids
        if sent and not oids and sups and not covered and not any(_like(sups[0], d) for d in deter):
            ev["supplier_name"] = idx.best_name(sups, " ".join([subj] + atts))
            ev["multi"] = multi
            ev["want"] = want
            ev["attachments"] = atts
            unmatched.append(ev)
        n_before = len(suggest)
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
                    suggest[key] = dict(_sg(key, oid, o, "ship", "shipped", day.isoformat(), m, st), unsure=unsure)
            if sg:
                field, _old, val = sg
                key = f"{oid}|{field}"
                if key not in ignored and key not in suggest:
                    suggest[key] = dict(_sg(key, oid, o, "order" if field == "draft" else kind, field, val, m, st),
                                        unsure=unsure)
        if unsure and len(suggest) > n_before and not m.get("body"):
            ev["need_body"] = True                     # 有要改的单、门店又说不清：读一下正文
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
            "pending": sorted(pending, key=lambda x: x["mail"]["date"], reverse=True),
            "missing": missing, "unmatched": unmatched, "events": events}


def _would_change(o, kind):
    """这封确认 / 发货信会让这张单往前走一步吗（已经确认 / 发货 / 到货 / 取消了的不用再关联）"""
    if not o or o.get("state") == "cancel" or o.get("arrived"):
        return False
    s = OT.status(o)
    if kind == "confirm":
        return s in ("ordered", "draft") and not o.get("confirmed")
    if kind == "ship":
        return not o.get("shipped")
    return False


def link_suggestions(st, pend, oid):
    """「待处理邮件」里人选了这一张 → 要写的建议（确认 / 发货；没有要改的状态就只把这封信记到单上）"""
    o = st.orders.get(oid) or {}
    m = dict(pend["_m"])
    kind = pend["mkind"]
    day = m["_date"].date()
    out = []
    s = OT.status(o)
    if kind in ("confirm", "ship") and s in ("ordered", "draft") and not o.get("confirmed"):
        out.append(_sg(f"{oid}|confirmed", oid, o, kind, "confirmed", day.isoformat(), m, st))
    if kind == "ship" and not o.get("shipped") and not o.get("arrived"):
        out.append(_sg(f"{oid}|shipped", oid, o, "ship", "shipped", day.isoformat(), m, st))
    if not out:
        out.append(_sg(f"{oid}|link", oid, o, kind, "link", "", m, st))
    for x in out:
        x["pend_key"] = pend["key"]
    return out


def _like(a, b):
    return a == b or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a))


def duplicates(st, days=3):
    """
    程序生成的「待登记」和 Excel 里手填的是同一张单（同一家门店、供应商名字一个包含另一个（F.LLI ZAMBETTI SRL / ZAMBETTI）、
    下单日期前后 days 天内）→ [(待登记单号, 手填单号)]。2026-11-07 真实数据：73 张待登记里 17 张是这样重复的。
    """
    man = [(oid, o) for oid, o in st.orders.items()
           if not o.get("draft") and o.get("state") != "cancel"]
    out = []
    for oid, o in st.orders.items():
        if not o.get("draft") or o.get("state") == "cancel":
            continue
        deter = o.get("origin") == "wholesale"                # DETERSIVO 比价的待登记 ↔ Excel 里写 DETER 的那几行
        a, d = st.canon(OT.sup_key(o.get("supplier"))), OT.to_date(o.get("date"))
        if not d:
            continue
        best = None
        for m_id, m in man:
            d2 = OT.to_date(m.get("date"))
            if not d2 or str(m.get("store")) != str(o.get("store")) or abs((d2 - d).days) > days:
                continue
            if (m.get("kind") == "deter") != deter:
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


def absorb_drafts(st):
    """
    同步 Excel 时自动做（2026-11-09 用户：表里明明已经有 9 号的单，程序还挂着「待登记」= 没同步进来）：
    程序生成的待登记，Excel 里已经手填了同一张（duplicates：同一家门店、供应商名字对得上、前后 3 天）→
    合并（订单文件 / 金额挂到手填那张，待登记删掉）。手填的就是已经下了的单，以 Excel 为准。
    返回 (几张, 撤销用, [(待登记, 手填)])
    """
    pairs = duplicates(st)
    if not pairs:
        return 0, [], []
    sugs = [{"id": a, "twin": b} for a, b in pairs]
    n, undo = merge_duplicates(st, sugs, src="merged")
    return n, undo, pairs


def merge_duplicates(st, sugs, src="mail"):
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
    st.set_many(ch, src=src)
    st.delete(gone, src=src)
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
                         "buyer": OT.piano_buyer(st, st.me, {"store": s["store"], "supplier": s["supplier"]}),
                         "mails": [dict({"k": "order", "d": m["date"].strftime("%Y-%m-%d %H:%M"),
                                                          "s": m["subject"][:120], "f": m["from"]},
                                                         **({"id": m["message_id"]} if m.get("message_id") else {}))]}))
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
        elif s["field"] == "link":
            pass                                     # 只把这封信记到单上（人在「待处理邮件」里选的）
        else:
            f[s["field"]] = s["value"]
            if s.get("status") == "draft":           # 供应商都确认 / 发货了：肯定已经下单
                f["draft"] = None
        ev = {"k": s["kind"], "d": s["mail"]["date"].strftime("%Y-%m-%d %H:%M") if isinstance(s["mail"]["date"], datetime)
              else str(s["mail"]["date"]), "s": s["mail"]["subject"][:120], "f": s["mail"]["from"]}
        if s["mail"].get("message_id"):              # 订单窗口「邮件」页按它找回这封信
            ev["id"] = s["mail"]["message_id"]
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
        f.pop("buyer", None)
        if f.get("draft", 0) is None and not OT.is_buyer_name(o.get("buyer")):
            b = OT.piano_buyer(st, o.get("buyer") or st.me, o)     # 照同事在 PIANO 的写法，不写「采购部」
            if b:
                f["buyer"] = b
        undo.append(("set", oid, {k: o.get(k) for k in f}))
        out.append((oid, f))
    st.set_many(out, src=src)
    return len(out), undo


# ===================================================================== 订单窗口「邮件」页（2026-11-08）
def _minute(v):
    d = _parse_date(v)
    return d.strftime("%Y-%m-%d %H:%M") if d else ""


def related(o, mails):
    """
    一张单的邮件：单上记的事件（o["mails"]：邮件核对 / 程序发信时记的）+ 程序发的那封（o["sent"]）+ 回这几封的信（In-Reply-To）。
    mails = 本机缓存的信头。返回按日期排的 [{"kind", "date", "subject", "from", "mail"(缓存里那封，找不到 None)}]，
    同一封信（Message-ID 一样，比如发件箱和抄送收件箱各一份）只留一份，优先「已发送」/ 有附件段号的那份
    """
    by_mid, by_key = {}, {}
    for m in mails:
        mid = (m.get("message_id") or "").strip()
        if mid:
            cur = by_mid.get(mid)
            if cur is None or (not cur.get("parts") and m.get("parts")):
                by_mid[mid] = m
        k = ((m.get("subject") or "")[:120], (m.get("from") or "").lower(), _minute(m.get("date")))
        by_key.setdefault(k, m)
    out, seen = [], set()

    def add(kind, m, ev):
        mid = (m or {}).get("message_id") or ""
        sig = mid or (m and (m.get("mailbox"), m.get("folder"), m.get("uid"))) or (ev.get("s"), ev.get("d"))
        if sig in seen:
            return
        seen.add(sig)
        out.append({"kind": kind, "date": _parse_date(m.get("date")) if m else _parse_date(ev.get("d")),
                    "subject": (m or {}).get("subject") or ev.get("s") or "", "from": ((m or {}).get("from") or ev.get("f") or "").lower(),
                    "mail": m})
    for ev in o.get("mails") or []:
        m = by_mid.get(ev.get("id")) if ev.get("id") else None
        if m is None:
            m = by_key.get(((ev.get("s") or "")[:120], (ev.get("f") or "").lower(), ev.get("d") or ""))
        add(ev.get("k") or "order", m, ev)
    sent = o.get("sent") or {}
    if sent.get("mid"):
        m = by_mid.get(sent["mid"])
        add("order", m, {"s": sent.get("subject"), "d": (sent.get("at") or "").replace("T", " ")[:16], "f": sent.get("from")})
    mids = {r["mail"]["message_id"] for r in out if r["mail"] and r["mail"].get("message_id")}
    for _ in range(2):                                    # 回信，和回信的回信
        new = [m for m in by_mid.values() if (m.get("in_reply_to") or "").strip() in mids and m["message_id"] not in mids]
        for m in new:
            add("reply", m, {})
            mids.add(m["message_id"])
    out.sort(key=lambda r: r["date"] or datetime.min)
    return out
