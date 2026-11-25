"""
订单邮件 ↔ 订单一一对上（2026-11-25 用户：「订单一定要一一关联上，不然我们后续全都没法做」）。不依赖界面。

供应商的确认 / 发货 / DDT 信是哪家门店的，按可信度：
  ① 我们的订单号（主题 / 正文 / 附件里）→ 就是那张单（mail_match 里做）；
  ② **收货抬头和地址**：正文和附件（PDF / Excel / Word）里写的 CASA & TE 哪家公司、街道门牌、邮编、城市，
     对「订单发件邮箱」设置里每家店的抬头 / 地址（mail_send.profile）—— 这个模块；
  ③ 主题 / 附件名里的门店名、哪个门店邮箱收到的。
一封信最后只对一张单；对不上或者可能是好几张的，不自动改，放进「待处理邮件」让人选。

store_of(文字, 档案) → (门店代码, 说明) 或 (None, [可能的门店])；attachment_text(文件名, 字节) → 附件里的文字。
"""
import io
import re
import unicodedata

CODES = ("1", "2", "3", "4", "5", "BMS")
TEXT_EXT = (".pdf", ".xlsx", ".xlsm", ".xls", ".docx", ".txt", ".csv", ".htm", ".html")
MAX_ATT_BYTES = 5 * 1024 * 1024          # 附件超过这么大不读
MAX_ATTS = 3                             # 一封信最多读几个附件
PDF_PAGES = 4
_STREET_STOP = {"VIA", "VIALE", "PIAZZA", "PIAZZALE", "LOC", "LOCALITA", "STRADA", "CORSO", "DELLA", "DELLE", "DEL",
                "DEI", "DI", "DA", "D", "SS", "SP", "SR", "STATALE", "PROVINCIALE"}
_NAME_STOP = {"CASA", "TE", "E", "SRL", "SPA", "S", "R", "L", "SNC", "SAS", "SRLS"}
_CITY_STOP = {"DELLA", "DEL", "DI", "AR", "LU", "FI", "PI", "PO", "SI", "PT"}
# 收货那几行（DDT 的「Luogo di destinazione」、确认信的「Indirizzo di consegna」…）：先只看这几行
_DEST = re.compile(r"DESTINAZ|DESTINATARIO|CONSEGNA|SPEDIRE|SPEDIZIONE|RECAPITO|DELIVERY|SHIP\s*TO|"
                   r"LUOGO|PUNTO\s*VENDITA|NEGOZIO|SEDE\s*OPERATIVA|MAGAZZINO")
_BLOCK_LINES = 5


def norm(s):
    """大写、去重音、标点都换成空格（CASA&TE / CASA & TE / Casa e Te 都变成 CASA TE 这种）"""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").upper()
    s = s.replace("&", " ")
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    s = re.sub(r"\bCASA\s+E\s+TE\b", "CASA TE", s)
    s = re.sub(r"\bCASATE\b", "CASA TE", s)
    return re.sub(r"\s+", " ", s).strip()


def _street(addr):
    """「Via d'Arezzo 159/r, 52045 Foiano della Chiana (AR)」→ (["AREZZO"], "159", "52045", ["FOIANO", "CHIANA"])"""
    a = str(addr or "")
    head, _sep, rest = a.partition(",")
    h = norm(head)
    num = re.search(r"\b(\d{1,5})\b", h)
    words = [w for w in h.split() if w.isalpha() and len(w) >= 3 and w not in _STREET_STOP]
    r = norm(rest)
    cap = re.search(r"\b(\d{5})\b", r)
    city = [w for w in r.split() if w.isalpha() and len(w) >= 4 and w not in _CITY_STOP]
    return words, num.group(1) if num else "", cap.group(1) if cap else "", city


def keys(settings=None, profiles=None):
    """
    每家店认地址用的特征：{代码: {"name": 正则（抬头）, "street": [词], "num": 门牌, "cap": 邮编, "city": [词]}}。
    profiles 直接给 {代码: {"name", "address"}}（测试用）；不给就用 mail_send.profile（「订单发件邮箱」设置里改过的优先）
    """
    if profiles is None:
        import mail_send as MS
        profiles = {c: MS.profile(settings, c) for c in CODES}
    out = {}
    for c, p in profiles.items():
        name = norm(p.get("name"))
        dist = [w for w in name.split() if w not in _NAME_STOP]
        if c == "BMS" or name.startswith("BMS"):
            rx = r"\bBMS\s+(?:P\s+)?PARTNERS?\b|\bBMS\s*P\s*SRL\b"
        elif dist:
            # CASA TE S.FILIPPO SRL / CASA TE GROUP SRL：CASA TE 后面几个词里有它自己的那个词
            rx = r"\bCASA\s+TE\s+(?:\w+\s+){0,2}?" + r"\s+".join(map(re.escape, dist)) + r"\b"
        else:
            rx = r"\bCASA\s+TE\s+S\s?R\s?L\b"          # CASA & TE SRL（FOIANO）：后面直接 SRL
        words, num, cap, city = _street(p.get("address"))
        out[str(c)] = {"name": re.compile(rx), "street": words, "num": num, "cap": cap, "city": city,
                       "label": p.get("name") or c}
    return out


def _count(ks, attr):
    from collections import Counter
    cnt = Counter()
    for k in ks.values():
        vals = k[attr] if isinstance(k[attr], list) else [k[attr]]
        for v in set(vals):
            if v:
                cnt[v] += 1
    return cnt


def scores(text, ks):
    """每家店的分数和理由：抬头 3、街道 + 门牌 3（只有街道 1.5）、邮编（只这家用 1.5 / 几家共用 0.5）、城市（1 / 0.3）"""
    t = " " + norm(text) + " "
    caps, cities = _count(ks, "cap"), _count(ks, "city")
    out = {}
    for c, k in ks.items():
        sc, why = 0.0, []
        if k["name"].search(t):
            sc += 3
            why.append("name")
        if k["street"]:
            core = r"\s+".join(map(re.escape, k["street"][-1:]))
            if k["num"] and re.search(r"\b" + core + r"\s+(?:N\s+|NR\s+|NUM\s+|CIVICO\s+)?" + k["num"] + r"\b", t):
                sc += 3
                why.append("street")
            elif re.search(r"\b" + core + r"\b", t):
                sc += 1.5
                why.append("street?")
        if k["cap"] and re.search(r"\b" + k["cap"] + r"\b", t):
            sc += 1.5 if caps[k["cap"]] == 1 else 0.5
            why.append("cap")
        hit = [w for w in k["city"] if re.search(r"\b" + w + r"\b", t)]
        if hit:
            sc += 1 if any(cities[w] == 1 for w in hit) else 0.3
            why.append("city")
        if sc:
            out[c] = (sc, why)
    return out


def _decide(sc, min_score=3, margin=1.5):
    if not sc:
        return None, []
    rank = sorted(sc.items(), key=lambda kv: -kv[1][0])
    top, s1 = rank[0][0], rank[0][1][0]
    s2 = rank[1][1][0] if len(rank) > 1 else 0
    if s1 >= min_score and s1 - s2 >= margin:
        return top, rank[0][1][1]
    return None, [c for c, (s, _w) in rank if s >= min_score]


def dest_blocks(text):
    """收货那几行：带「Destinazione / Consegna / Luogo…」的那一行 + 下面几行"""
    lines = str(text or "").splitlines()
    keep = []
    for i, l in enumerate(lines):
        if _DEST.search(norm(l)):
            keep.extend(lines[i:i + _BLOCK_LINES + 1])
    return "\n".join(keep)


def store_of(text, ks):
    """
    文字（正文 + 附件）里写的是哪家店的收货抬头 / 地址 → (代码, 说明) 或 (None, [可能的几家])。
    先看收货那几行（DDT 上开票的公司和送货的门店常常不一样），认不出再看全文。
    """
    if not str(text or "").strip():
        return None, []
    cands = []
    for chunk in (dest_blocks(text), text):
        if not chunk.strip():
            continue
        code, info = _decide(scores(chunk, ks))
        if code:
            return code, info
        for c in info:
            if c not in cands:
                cands.append(c)
    return None, cands


# ---------------------------------------------------------------- 附件里的文字
def wanted(parts):
    """信头的附件 [[段号, 文件名, 编码, 大小]] → 要读的（PDF / Excel / Word / 文本，不太大，最多 MAX_ATTS 个）"""
    out = []
    for p in parts or []:
        try:
            name, size = str(p[1] or ""), int(p[3] or 0)
        except (IndexError, TypeError, ValueError):
            continue
        if name.lower().endswith(TEXT_EXT) and size <= MAX_ATT_BYTES:
            out.append(p)
    return out[:MAX_ATTS]


def attachment_text(name, data, limit=20000):
    """附件的字节 → 里面的文字（读不出来返回 ""）。PDF 读前几页，Excel 读第一页前 80 行"""
    n = str(name or "").lower()
    try:
        if n.endswith(".pdf"):
            from pypdf import PdfReader
            rd = PdfReader(io.BytesIO(data))
            out = []
            for pg in rd.pages[:PDF_PAGES]:
                try:
                    out.append(pg.extract_text() or "")
                except Exception:
                    continue
            return "\n".join(out)[:limit]
        if n.endswith((".xlsx", ".xlsm")):
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            try:
                rows = []
                for ws in wb.worksheets[:2]:
                    for r in ws.iter_rows(max_row=80, values_only=True):
                        vals = [str(v) for v in r if v not in (None, "")]
                        if vals:
                            rows.append("  ".join(vals))
                return "\n".join(rows)[:limit]
            finally:
                wb.close()
        if n.endswith(".xls"):
            import xlrd
            bk = xlrd.open_workbook(file_contents=data, on_demand=True)
            rows = []
            for sh in bk.sheets()[:2]:
                for i in range(min(sh.nrows, 80)):
                    vals = [str(v) for v in sh.row_values(i) if v not in (None, "")]
                    if vals:
                        rows.append("  ".join(vals))
            return "\n".join(rows)[:limit]
        if n.endswith(".docx"):
            import zipfile
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", "", xml)[:limit]
        if n.endswith((".htm", ".html")):
            import html as _h
            t = data.decode("utf-8", "ignore")
            return _h.unescape(re.sub(r"<[^>]+>", " ", t))[:limit]
        if n.endswith((".txt", ".csv")):
            for enc in ("utf-8", "cp1252", "latin-1"):
                try:
                    return data.decode(enc)[:limit]
                except UnicodeDecodeError:
                    continue
    except Exception:
        return ""
    return ""
