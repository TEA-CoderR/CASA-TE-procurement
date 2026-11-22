# -*- coding: utf-8 -*-
"""
VOLANTINO 销售分析（2026-11-15 老板要：① 各门店 VOLANTINO 商品的销量、毛利、毛利率 ② 所有门店汇总
③ VOLANTINO 周期内的让利对整个月毛利率的影响；用户定：名单可以上传文件，也可以程序自己读数据库；整个月 = 全店所有商品）。

不依赖界面。数据库只读。

一期 VOLANTINO（period）= {"id", "name", "start", "end"（YYYY-MM-DD，含当天）, "stores": [门店号], "source": "file" / "db",
                            "file": 原文件, "promo": 数据库促销单号, "items": [{"code", "ean", "name", "price"}]}
  - 上传的名单（Excel）：用检查门店清单同一套表头识别（商品编号 / 条码 / 名称），促销价列、文件里写的日期能认就认
  - 数据库：promozioni（促销单：开始 / 结束 / 门店）+ promozioni_list（哪些商品、促销价）+ promozioni_volantino（标题）
  保存在本机 cache/volantino/periods.json（数据库来的不存，每次现读）

销售数据：vendite_list_<门店>（每一行一笔销售：Qta、TotRighe 含税金额、TotCodIva 税额、TotAcquisto / PrezzoAcquisto 进价、
          PrezzoVendi 实际售价、PrezzoVendiNormale 原价）
  销售额 = TotRighe − TotCodIva（不含税，和程序别的地方一样）
  成本   = TotAcquisto（没有就 PrezzoAcquisto × Qta）
  毛利   = 销售额 − 成本；毛利率 = 毛利 / 销售额
  让利   = (原价 − 实际售价) × 数量，按这一行的税率折成不含税
  当月毛利率（全店所有商品）：实际 = 当月毛利 / 当月销售额；
  没有 VOLANTINO 让利时 = (当月毛利 + 让利) / (当月销售额 + 让利)；影响 = 实际 − 没让利（百分点，一般是负的）
"""
import json
import os
import re
from datetime import date, datetime, timedelta

STORE_IDS = (1, 2, 3, 4, 5)
_DATE_RE = re.compile(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?")


# ---------------------------------------------------------------- 名单
def _d(v):
    """'2026-11-13' / date / datetime → date；不认识 → None"""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def guess_dates(texts, year=None):
    """一堆文字（文件名、表头上面几行）里找「从哪天到哪天」：两个日期，第二个不早于第一个 → (开始, 结束) 或 (None, None)
    认 13/11/2026、13.11.26、13-11（没写年用 year / 今年）"""
    year = year or date.today().year
    found = []
    for t in texts:
        for m in _DATE_RE.finditer(str(t or "")):
            d_, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
            y = int(y) + (2000 if y and len(y) == 2 else 0) if y else year
            try:
                found.append(date(y, mo, d_))
            except ValueError:
                continue
    for i in range(len(found) - 1):
        a, b = found[i], found[i + 1]
        if a <= b <= a + timedelta(days=62):
            return a, b
    return None, None


PRICE_HEADS = ("prezzovolantino", "prezzopromo", "prezzopromozione", "prezzoofferta", "offerta", "promo", "prezzop",
               "促销价", "特价")
NORMAL_HEADS = ("prezzocontinuo", "prezzonormale", "prezzolistino", "prezzopieno", "prezzovendita", "原价", "正常价")
EAN_HEADS = ("codiceean", "ean", "codbarre", "codicebarre", "barcode", "barre", "条码")
CODE_HEADS = ("codicearticolo", "codarticolo", "codart", "articolo", "codice", "商品编号", "编号")
NAME_HEADS = ("descrizione", "descr", "nome", "商品名称", "名称", "商品")
START_HEADS = ("start", "inizio", "datainizio", "dal", "开始")
END_HEADS = ("end", "fine", "datafine", "al", "结束")
KINDS = (("volantino", "VOLANTINO"), ("natale", "圣诞货"))
_NATALE = re.compile(r"natal|xmas|christmas|圣诞", re.I)
NATALE_SHARE = 0.25      # 数据库促销单：商品名里有 NATAL / LED / ALBERO… 的占这么多 → 当圣诞货（诊断 54：41 号单 38%）
_NATALE_SQL = ("p.Descrizione LIKE '%%NATAL%%' OR p.Descrizione LIKE '%%LED%%' OR p.Descrizione LIKE '%%ALBERO%%' "
               "OR p.Descrizione LIKE '%%PRESEP%%' OR p.Descrizione LIKE '%%XMAS%%' OR p.Descrizione LIKE '%%BEFANA%%' "
               "OR p.Descrizione LIKE '%%PALLINE%%' OR p.Descrizione LIKE '%%ADDOBB%%'")


def kind_of(p):
    """一期是 VOLANTINO 还是圣诞货：记了就用记的，没记按名称（NATALE / 圣诞 → 圣诞货）"""
    k = (p or {}).get("kind")
    if k in dict(KINDS):
        return k
    if _NATALE.search(str((p or {}).get("name") or "")):
        return "natale"
    return "natale" if (p or {}).get("natale_share", 0) >= NATALE_SHARE else "volantino"


def kind_label(k):
    return dict(KINDS).get(k, "VOLANTINO")


def _h(v):
    return re.sub(r"[\s._\-:]+", "", str(v or "")).lower()


def _col(head, heads, exact=False):
    """表头里第一个对得上的列（先找完全一样的，再找开头一样的）"""
    for c, h in enumerate(head):
        if h and h in heads:
            return c
    if exact:
        return None
    for c, h in enumerate(head):
        if h and any(h.startswith(x) for x in heads if len(x) > 2):
            return c
    return None


def _num(v):
    try:
        x = float(str(v).replace("€", "").replace(",", ".").strip()) if isinstance(v, str) else float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _day(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    n = _num(v) if isinstance(v, (int, float, str)) else None
    if n is not None and 30000 <= n <= 70000 and float(n).is_integer():      # Excel 日期没设格式：存的是数字（46359 = 03/12/2026）
        return date(1899, 12, 30) + timedelta(days=int(n))
    a, _b = guess_dates([v, v])
    return a


def _scan(rows):
    """一个工作表（前面的行）→ {"items", "texts", "dates"}；没有像名单的表头 → None
    一行一个商品：条码列照条码；编号列里是长数字（≥ 8 位）的当条码（VOLANTINO 名单的
    「Codice Articolo」里写的其实是条码）；没写编号也没写条码的行（分组标题）跳过"""
    import reorder_service as R
    rows = list(rows)
    hdr = None
    for i, row in enumerate(rows[:15]):
        head = [_h(v) for v in row]
        if _col(head, EAN_HEADS) is not None or _col(head, CODE_HEADS, exact=True) is not None:
            hdr = i
            break
    if hdr is None:
        return None
    head = [_h(v) for v in rows[hdr]]
    ce, cc = _col(head, EAN_HEADS), _col(head, CODE_HEADS, exact=True)
    if cc is None:
        cc = _col(head, CODE_HEADS)
    if cc == ce:
        cc = None
    cn = _col(head, NAME_HEADS)
    cp, cr = _col(head, PRICE_HEADS), _col(head, NORMAL_HEADS)
    cs, cx = _col(head, START_HEADS, exact=True), _col(head, END_HEADS, exact=True)
    get = lambda r, c: r[c] if c is not None and c < len(r) else None      # noqa: E731
    items, seen, dates = [], set(), []
    for r in rows[hdr + 1:]:
        ean, code = R._clean_code(get(r, ce)), R._clean_code(get(r, cc))
        if code and len(code) >= 8 and code.isdigit():         # 编号列里写的是条码
            ean, code = ean or code, ""
        ean = ean if ean.isdigit() else ""
        code = code if code.isdigit() else ""
        if not ean and not code:
            continue
        key = ean or "#" + code
        if key in seen:
            continue
        seen.add(key)
        items.append({"code": code, "ean": ean, "name": str(get(r, cn) or "").strip(),
                      "price": _num(get(r, cp)), "normal": _num(get(r, cr))})
        s, e = _day(get(r, cs)), _day(get(r, cx))
        if s and e and s <= e:
            dates.append((s, e))
    texts = [str(v) for row in rows[:hdr] for v in row if v not in (None, "")]
    return {"items": items, "texts": texts, "dates": dates}


def read_file(path):
    """
    上传的名单（VOLANTINO / 圣诞货）→ {"items": [{code, ean, name, price, normal}], "start", "end", "name", "sheet",
                                         "prices": {第几个商品: 促销价}}（日期认不出是 None，界面里让人选）
    一个文件好几页都是名单（例如 BOZZA ELENCO 草稿 / ELENCO 定稿 / ORDINE PROMO）：用商品最多的那几页里
    名字不是草稿（BOZZA）的那页；日期 = 每行 start / end 里最常见的（几页一起数，年份离今年超过 1 年的不算），
    没有就看文件名和表头上面几行。
    """
    from collections import Counter
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    found = []
    try:
        for i, ws in enumerate(wb.worksheets):
            if hasattr(ws, "reset_dimensions"):
                ws.reset_dimensions()
            got = _scan(ws.iter_rows(values_only=True))
            if got and got["items"]:
                found.append((i, ws.title, got))
    finally:
        wb.close()
    name = os.path.splitext(os.path.basename(path))[0]
    if not found:
        return {"items": [], "start": None, "end": None, "name": name, "sheet": None, "prices": {}}
    most = max(len(g["items"]) for _, _, g in found)
    good = [f for f in found if len(f[2]["items"]) >= most * 0.8]
    draft = re.compile(r"bozza|draft|草稿", re.I)
    best = next((f for f in good if not draft.search(f[1])), good[0])
    items = best[2]["items"]
    this = date.today().year
    cnt = Counter(d for f in good for d in f[2]["dates"] if abs(d[0].year - this) <= 1 and (d[1] - d[0]).days <= 200)
    if not cnt:              # 定稿那页没写日期，草稿页写了（ELENCO VOLANTINO SETTEMBRE：只有 BOZZA ELENCO 有 start / end）
        cnt = Counter(d for f in found for d in f[2]["dates"] if abs(d[0].year - this) <= 1 and (d[1] - d[0]).days <= 200)
    if cnt:
        start, end = cnt.most_common(1)[0][0]
    else:
        start, end = guess_dates([name] + best[2]["texts"])
    return {"items": items, "start": start, "end": end, "name": name, "sheet": best[1],
            "prices": {k: x["price"] for k, x in enumerate(items) if x.get("price") is not None}}


# ---------------------------------------------------------------- 保存的几期
def store_path(base):
    return os.path.join(base, "cache", "volantino", "periods.json")


def load_periods(base):
    return [p for p in _read_store(base).get("periods") or [] if p.get("start") and p.get("end")]


def _read_store(base):
    try:
        with open(store_path(base), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_store(base, data):
    p = store_path(base)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    os.replace(tmp, p)


def save_periods(base, periods):
    data = _read_store(base)
    data["periods"] = [x for x in periods if x.get("source") != "db"]
    _write_store(base, data)


DB_EDITABLE = ("name", "kind", "start", "end", "stores")


def load_overrides(base):
    """数据库促销单在程序里改过的（名称 / 类型 / 日期 / 门店）：{"db41": {...}}，数据库本身不动"""
    return _read_store(base).get("db") or {}


def save_override(base, pid, values):
    data = _read_store(base)
    db = data.setdefault("db", {})
    if values:
        db[pid] = {k: values[k] for k in DB_EDITABLE if k in values}
    else:
        db.pop(pid, None)
    _write_store(base, data)


def apply_overrides(periods, ov):
    """数据库来的几期套上程序里改过的（原来的值记在 orig 里，界面能「恢复数据库原样」）"""
    out = []
    for p in periods:
        o = ov.get(p.get("id"))
        if o:
            p = dict(p, orig={k: p.get(k) for k in DB_EDITABLE}, **o)
        out.append(p)
    return out


def new_id(periods):
    n = max([int(str(p.get("id", "f0"))[1:] or 0) for p in periods if str(p.get("id", "")).startswith("f")] + [0])
    return f"f{n + 1}"


def label(p):
    """下拉框里一期的名字：「VOLANTINO 13–26 NOV · 13/11 – 26/11」"""
    s, e = _d(p["start"]), _d(p["end"])
    span = f"{s:%d/%m} – {e:%d/%m/%Y}" if s and e else ""
    return f"{p.get('name') or ''} · {span}" if p.get("name") else span


# ---------------------------------------------------------------- 数据库里的促销单
def _stores_of(neg):
    """promozioni.negozio：「0,1,2,3」/「2」/ 空（= 全部门店）→ 门店号列表（0 是仓库，不算）"""
    s = [int(x) for x in re.findall(r"\d+", str(neg or ""))]
    s = [x for x in s if x in STORE_IDS]
    return s or list(STORE_IDS)


def db_periods(db, since=None):
    """
    数据库里的促销单（最近的在前）：[{… source "db", promo, name, natale_share, items 空（选中时再读）}]；查不了返回 []
    名称用 promozioni.Descrizione（诊断 43：促销单自己有名称），没有再看 promozioni_volantino 的标题
    """
    import pandas as pd
    since = since or (date.today() - timedelta(days=400))
    try:
        df = pd.read_sql("SELECT pr.*, x.n, x.natale FROM promozioni pr JOIN ("
                         "SELECT pl.promozioni_l AS pid, COUNT(*) AS n, "
                         f"SUM(CASE WHEN {_NATALE_SQL} THEN 1 ELSE 0 END) AS natale "
                         "FROM promozioni_list pl LEFT JOIN prodotti p ON p.CodArticolo = pl.CodArticolo "
                         "WHERE pl.invalid IS NULL OR pl.invalid = 0 GROUP BY pl.promozioni_l) x ON x.pid = pr.l "
                         "WHERE pr.Data_f >= %s", db.engine, params=(since,))
    except Exception:
        return []
    titles = {}
    try:
        tv = pd.read_sql("SELECT promozioni_id, promozioni_title FROM promozioni_volantino", db.engine)
        titles = {int(r["promozioni_id"]): str(r["promozioni_title"] or "").strip()
                  for r in tv.to_dict("records") if r.get("promozioni_id") is not None}
    except Exception:
        pass
    out = []
    for r in df.to_dict("records"):
        s_, e = _d(r["Data_a"]), _d(r["Data_f"])
        n = int(_f(r.get("n")))
        if not s_ or not e or not n:
            continue
        l_ = int(r["l"])
        name = str(r.get("Descrizione") or "").strip() or titles.get(l_) or f"Promozione {l_}"
        out.append({"id": f"db{l_}", "promo": l_, "source": "db", "start": s_.isoformat(), "end": e.isoformat(),
                    "stores": _stores_of(r.get("negozio")), "items": [], "n": n,
                    "natale_share": _f(r.get("natale")) / n, "name": name})
    out.sort(key=lambda p: p["start"], reverse=True)
    return out


def db_items(db, promo):
    """一张促销单的商品：[{code, ean, name, price 促销价, normal 原价}]（诊断 44 / 53：Prezzo_a 促销价、Prezzo_p 原价）"""
    import pandas as pd
    import reorder_service as R
    df = pd.read_sql("SELECT pl.*, p.CodBarre, p.Descrizione, p.PrezzoVedi1 FROM promozioni_list pl "
                     "LEFT JOIN prodotti p ON p.CodArticolo = pl.CodArticolo "
                     "WHERE pl.promozioni_l = %s AND (pl.invalid IS NULL OR pl.invalid = 0)", db.engine, params=(int(promo),))
    out = []
    for r in df.to_dict("records"):
        try:
            code = str(int(r["CodArticolo"]))
        except (TypeError, ValueError):
            continue
        normal = _f(r.get("Prezzo_p")) or _f(r.get("PrezzoVedi1")) or None
        out.append({"code": code, "ean": str(r.get("CodBarre") or "").strip(), "name": str(r.get("Descrizione") or "").strip(),
                    "price": R.promo_list_price(r, r.get("PrezzoVedi1")) or None, "normal": normal})
    return out


# ---------------------------------------------------------------- 计算
def month_range(start, end, upto=None):
    """
    活动的「当月」：活动天数最多的那个月（一样多取前面的），1 号 → 月底（不超过 upto）。
    2026-11-15 核对：以前是开始那个月 1 号 → 结束那个月月底，VOLANTINO 15/10 – 01/11 就成了 10 月 + 半个 11 月
    """
    from collections import Counter
    cnt = Counter()
    d = start
    while d <= end:
        cnt[(d.year, d.month)] += 1
        d += timedelta(days=1)
    y, m = max(cnt, key=lambda k: (cnt[k], -k[0], -k[1])) if cnt else (start.year, start.month)
    m1 = date(y, m, 1)
    m2 = (m1.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if upto and m2 > upto:
        m2 = max(upto, m1)
    return m1, m2


def _f(v):
    try:
        x = float(v or 0)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


_SUMS = ("SUM(Qta) AS q, SUM(TotRighe) AS gross, SUM(TotCodIva) AS iva, "
         "SUM(CASE WHEN TotAcquisto IS NOT NULL AND TotAcquisto <> 0 THEN TotAcquisto ELSE PrezzoAcquisto * Qta END) AS cost, "
         "SUM(CASE WHEN PrezzoVendiNormale > PrezzoVendi THEN (PrezzoVendiNormale - PrezzoVendi) * Qta ELSE 0 END) AS disc, "
         "SUM(Guadagno) AS erp")


def _money(r):
    """一组合计 → 不含税的销售额 / 成本 / 毛利 / 让利"""
    gross, iva = _f(r.get("gross")), _f(r.get("iva"))
    rev = gross - iva
    cost = _f(r.get("cost"))
    ratio = rev / gross if gross else 1 / 1.22               # 让利（含税）折成不含税
    return {"qty": _f(r.get("q")), "rev": rev, "cost": cost, "profit": rev - cost, "disc": _f(r.get("disc")) * ratio,
            "erp": _f(r.get("erp"))}


def _sql_store(db, sid, codes, d1, d2, m1, m2, b1):
    """一家店：VOLANTINO 商品（周期内、活动前同样天数）每个商品的合计、每天的合计，全店当月合计"""
    import pandas as pd
    t = f"vendite_list_{int(sid)}"
    out = {"items": {}, "before": {}, "days": {}, "month": {}, "lines": []}
    nxt = d2 + timedelta(days=1)
    if codes:
        for i in range(0, len(codes), 2000):
            chunk = codes[i:i + 2000]
            ph = ", ".join(["%s"] * len(chunk))
            df = pd.read_sql(f"SELECT CodArticolo, {_SUMS} FROM {t} WHERE CodArticolo IN ({ph}) AND Data >= %s AND Data < %s "
                             f"GROUP BY CodArticolo", db.engine, params=tuple(chunk) + (d1, nxt))
            for r in df.to_dict("records"):
                out["items"][str(int(r["CodArticolo"]))] = _money(r)
            df = pd.read_sql(f"SELECT CodArticolo, {_SUMS} FROM {t} WHERE CodArticolo IN ({ph}) "
                             f"AND Data >= %s AND Data < %s GROUP BY CodArticolo", db.engine, params=tuple(chunk) + (b1, d1))
            for r in df.to_dict("records"):
                out["before"][str(int(r["CodArticolo"]))] = _money(r)
            df = pd.read_sql(f"SELECT DATE(Data) AS d, CodArticolo, {_SUMS} FROM {t} WHERE CodArticolo IN ({ph}) "
                             f"AND Data >= %s AND Data < %s GROUP BY DATE(Data), CodArticolo", db.engine,
                             params=tuple(chunk) + (d1, nxt))
            for r in df.to_dict("records"):            # 每天每个商品一行：让利按名单原价算（analyze 里）
                out["lines"].append((str(r["d"])[:10], str(int(r["CodArticolo"])), r))
    df = pd.read_sql(f"SELECT {_SUMS} FROM {t} WHERE Data >= %s AND Data < %s", db.engine,
                     params=(m1, m2 + timedelta(days=1)))
    rows = df.to_dict("records")
    out["month"] = _money(rows[0] if rows else {})
    return out


def _codes(db, items):
    """名单 → 商品编号（只有条码的到数据库里查）"""
    import reorder_service as R
    need = [x["ean"] for x in items if not x.get("code") and x.get("ean")]
    m = R.codes_from_eans(db, need) if need else {}
    for x in items:
        if not x.get("code") and x.get("ean") in m:
            x["code"] = m[x["ean"]]
    return sorted({int(x["code"]) for x in items if str(x.get("code") or "").isdigit()})


def _names(db, codes):
    import pandas as pd
    out = {}
    for i in range(0, len(codes), 2000):
        chunk = codes[i:i + 2000]
        ph = ", ".join(["%s"] * len(chunk))
        try:
            df = pd.read_sql(f"SELECT CodArticolo, CodBarre, Descrizione FROM prodotti WHERE CodArticolo IN ({ph})",
                             db.engine, params=tuple(chunk))
        except Exception:
            return out
        for r in df.to_dict("records"):
            out[str(int(r["CodArticolo"]))] = (str(r.get("CodBarre") or "").strip(), str(r.get("Descrizione") or "").strip())
    return out


def pct_txt(v):
    return f"{v * 100:.2f}%" if v is not None else "—"


def margin(profit, rev):
    return profit / rev if rev else None


def summarize(part):
    """合计 → 加上毛利率"""
    part["margin"] = margin(part["profit"], part["rev"])
    return part


def _add(a, b, fields=("qty", "rev", "cost", "profit", "disc", "erp")):
    for f in fields:
        a[f] = a.get(f, 0.0) + b.get(f, 0.0)
    return a


def month_impact(month, vol_disc):
    """当月全店：实际毛利率、没有 VOLANTINO 让利时的毛利率、影响（百分点）"""
    rev, profit = month.get("rev", 0.0), month.get("profit", 0.0)
    act = margin(profit, rev)
    wo = margin(profit + vol_disc, rev + vol_disc)
    return {"margin": act, "margin_wo": wo, "impact": (act - wo) * 100 if act is not None and wo is not None else None}


def analyze(db, period, today=None, upto=None, log=None, on_store=None):
    """
    一期 VOLANTINO 的分析 → {
      "period", "start", "end", "month": (m1, m2), "stores": [门店号],
      "store": {门店: {qty rev cost profit margin disc erp sold(卖出几个商品) share(占当月销售额) month{…} impact{…}
                       prev{qty rev cost profit margin}（名单商品在活动前同样天数里）}},
      "total": 同上（五家合计）,
      "items": [{code ean name price stores{门店: {qty rev profit margin disc before}} + 合计字段}],
      "days": {门店: {日期: {rev profit qty}}},
      "no_cost": 有销售但没进价的商品数 }
    upto = 销售数据最新到哪天（月底超过它就算到它）
    """
    import reorder_service as R
    log = log or (lambda *_: None)
    today = today or date.today()
    d1, d2 = _d(period["start"]), _d(period["end"])
    if not d1 or not d2 or d2 < d1:
        raise ValueError("这一期的开始 / 结束日期不对")
    items = [dict(x) for x in period.get("items") or []]
    if period.get("source") == "db" and not items:
        items = db_items(db, period["promo"])
    codes = _codes(db, items)
    names = _names(db, codes)
    normal = {}                                       # 商品编号 → 原价（含税，名单里的 Prezzo Continuo / 促销单 Prezzo_p）
    for x in items:
        v = _f(x.get("normal"))
        if v > 0 and x.get("code"):
            normal.setdefault(str(x["code"]), v)
    m1, m2 = month_range(d1, d2, upto or today)
    b1 = d1 - (d2 - d1 + timedelta(days=1))               # 活动前同样天数
    stores = [s for s in (period.get("stores") or STORE_IDS) if s in STORE_IDS]
    per = R.per_store(stores, lambda sid: _sql_store(db, sid, codes, d1, d2, m1, m2, b1), on_store)
    # 名单里的商品（同一个编号只留一行）
    by_code, order = {}, []
    for x in items:
        c = str(x.get("code") or "")
        key = c or "#" + str(x.get("ean") or "")
        if key in by_code:
            continue
        e, n = names.get(c, ("", ""))
        by_code[key] = {"code": c, "ean": x.get("ean") or e, "name": x.get("name") or n, "price": x.get("price"),
                        "stores": {}}
        order.append(key)
    in_month = lambda k: m1.isoformat() <= k <= m2.isoformat()        # noqa: E731
    by_list = set()                                   # 让利按名单原价算的商品（其余按收银记录的原价）
    for sid in stores:                               # 每天每个商品 → 让利（名单有原价：原价 × 件数 − 实收；没有：收银记录的原价）
        res_ = per[sid]
        items_disc, days, mdisc = {}, {}, 0.0
        for k, c, r in res_["lines"]:
            m = _money(r)
            nv = normal.get(c)
            if nv:
                gross = _f(r.get("gross"))
                ratio = m["rev"] / gross if gross else 1 / 1.22
                m["disc"] = max(0.0, nv * m["qty"] - gross) * ratio
                by_list.add(c)
            items_disc[c] = items_disc.get(c, 0.0) + m["disc"]
            cur = days.setdefault(k, {"rev": 0.0, "profit": 0.0, "qty": 0.0, "disc": 0.0})
            for f in cur:
                cur[f] += m[f]
            if in_month(k):
                mdisc += m["disc"]
        res_["days"] = days
        res_["mdisc"] = mdisc
        res_["mrev"] = sum(v["rev"] for k, v in days.items() if in_month(k))
        for c, v in items_disc.items():
            if c in res_["items"]:
                res_["items"][c]["disc"] = v
    out_store, total, no_cost = {}, {"sold": 0}, set()
    total_month, total_prev = {}, {}
    for sid in stores:
        res = per[sid]
        agg = {"sold": 0}
        prev = {}
        for c, m in res["before"].items():            # 活动前同样天数（名单里的商品）：用来比「多卖了几成」
            if c in by_code:
                _add(prev, m, ("qty", "rev", "cost", "profit"))
        for f in ("qty", "rev", "cost", "profit"):
            prev.setdefault(f, 0.0)
        for c, m in res["items"].items():
            row = by_code.get(c)
            if row is None:
                continue
            m = summarize(dict(m, before=(res["before"].get(c) or {}).get("qty", 0.0)))
            row["stores"][sid] = m
            _add(agg, m)
            if m["qty"] > 0:
                agg["sold"] += 1
            if m["rev"] > 0 and m["cost"] <= 0:
                no_cost.add(c)
        for f in ("qty", "rev", "cost", "profit", "disc", "erp"):
            agg.setdefault(f, 0.0)
        summarize(agg)
        agg["month"] = summarize(dict(res["month"]))
        agg["disc_month"], agg["rev_month"] = res["mdisc"], res["mrev"]     # 只算活动在「当月」里的那几天
        agg["impact"] = month_impact(agg["month"], agg["disc_month"])
        agg["share"] = agg["rev_month"] / agg["month"]["rev"] if agg["month"]["rev"] else None
        agg["prev"] = summarize(prev)
        out_store[sid] = agg
        _add(total_prev, prev, ("qty", "rev", "cost", "profit"))
        _add(total, agg)
        _add(total_month, agg["month"], ("qty", "rev", "cost", "profit", "erp"))
    for f in ("qty", "rev", "cost", "profit", "disc", "erp"):
        total.setdefault(f, 0.0)
        total_month.setdefault(f, 0.0)
    summarize(total)
    total["month"] = summarize(total_month)
    total["disc_month"] = sum(out_store[s_]["disc_month"] for s_ in stores)
    total["rev_month"] = sum(out_store[s_]["rev_month"] for s_ in stores)
    total["impact"] = month_impact(total_month, total["disc_month"])
    total["share"] = total["rev_month"] / total_month["rev"] if total_month["rev"] else None
    for f in ("qty", "rev", "cost", "profit"):
        total_prev.setdefault(f, 0.0)
    total["prev"] = summarize(total_prev)
    rows = []
    for key in order:
        row = by_code[key]
        s = {}
        for m in row["stores"].values():
            _add(s, m, ("qty", "rev", "cost", "profit", "disc", "before"))
        for f in ("qty", "rev", "cost", "profit", "disc", "before"):
            s.setdefault(f, 0.0)
        row.update(summarize(s))
        rows.append(row)
    total["sold"] = sum(1 for r in rows if r["qty"] > 0)
    total["items"] = len(rows)
    log(f"[促销分析] {period.get('name') or ''} {d1:%d/%m}–{d2:%d/%m}：{len(rows)} 个商品（{len(codes)} 个对上数据库），"
        f"卖出 {total['sold']} 个；销售额 €{total['rev']:,.2f}、毛利 €{total['profit']:,.2f}（ERP 毛利合计 €{total['erp']:,.2f}）；"
        f"让利 €{total['disc']:,.2f}（{len(by_list)} 个商品按名单原价算，其余按收银记录的原价）；"
        f"当月 {m1:%d/%m}–{m2:%d/%m} 全店毛利率 {pct_txt(total['impact'].get('margin'))}，"
        f"没有让利 {pct_txt(total['impact'].get('margin_wo'))}")
    return {"period": period, "start": d1, "end": d2, "month": (m1, m2), "before": (b1, d1 - timedelta(days=1)),
            "stores": stores, "store": out_store, "total": total, "items": rows,
            "days": {sid: per[sid]["days"] for sid in stores}, "no_cost": len(no_cost),
            "matched": len(codes), "by_list": len(by_list)}


def change(cur, prev):
    """比活动前多 / 少几成：(现在 − 以前) / 以前；以前是 0 → None"""
    return (cur - prev) / prev if prev else None


def top_items(res, store=None, n=8, key="rev"):
    """销售额（或别的字段）最高的几个商品"""
    rows = [r for r in item_rows(res, store) if r.get(key, 0) > 0]
    rows.sort(key=lambda r: r.get(key, 0), reverse=True)
    return rows[:n]


def day_values(res, field, store=None):
    """周期里每天某个字段（rev / profit / qty / disc）的合计：[(date, 值)]"""
    d, out = res["start"], []
    stores = [store] if store else res["stores"]
    while d <= res["end"]:
        k = d.isoformat()
        out.append((d, sum(res["days"].get(s, {}).get(k, {}).get(field, 0.0) for s in stores)))
        d += timedelta(days=1)
    return out


def day_series(res, store=None):
    """每天的销售额 / 毛利（一家店或全部）：[(date, rev, profit)]，周期里每天都有（没卖的 0）"""
    d, out = res["start"], []
    stores = [store] if store else res["stores"]
    while d <= res["end"]:
        k = d.isoformat()
        rev = sum(res["days"].get(s, {}).get(k, {}).get("rev", 0.0) for s in stores)
        pr = sum(res["days"].get(s, {}).get(k, {}).get("profit", 0.0) for s in stores)
        out.append((d, rev, pr))
        d += timedelta(days=1)
    return out


def view(res, store=None):
    """界面要显示的那一份：store=None 合计；否则这家店"""
    return res["total"] if store is None else res["store"].get(store) or {}


def item_rows(res, store=None):
    """商品表：store=None 每个商品五家合计；否则这家店（名单里有、这家店没卖的也列，数字 0）"""
    out = []
    for r in res["items"]:
        if store is None:
            m = r
        else:
            m = r["stores"].get(store) or {"qty": 0.0, "rev": 0.0, "cost": 0.0, "profit": 0.0, "disc": 0.0, "before": 0.0,
                                             "margin": None}
        out.append(dict(m, code=r["code"], ean=r["ean"], name=r["name"], price=r.get("price"),
                        per_store={s: (r["stores"].get(s) or {}).get("qty", 0.0) for s in res["stores"]}))
    return out


def export(res, path, url=None, names=None):
    """导出 Excel：门店对比 + 商品（五家合计，每家销量一列）。用 xlsx_report 的统一样式"""
    import xlsx_report as X
    names = names or {}
    wb = X.new_book()
    p = res["period"]
    kind = "Natale" if kind_of(p) == "natale" else "Volantino"
    sub = f"{res['start']:%d/%m/%Y} – {res['end']:%d/%m/%Y} · " \
          f"mese {res['month'][0]:%d/%m} – {res['month'][1]:%d/%m/%Y}"
    cols = [X.Col("Negozio", 16), X.Col("Pezzi", 9, "num"), X.Col("Vendite € (netto IVA)", 14, "price"),
            X.Col("Margine €", 12, "price"), X.Col("Margine %", 10, "rate"), X.Col("Sconto € (promo)", 14, "price"),
            X.Col("Quota sul mese %", 12, "rate"), X.Col("Margine mese %", 12, "rate", "warn"),
            X.Col("Margine mese senza sconto %", 16, "rate", "warn"), X.Col("Impatto (punti)", 12, "dec", "warn")]
    rows, marks = [], {}

    def line(nm, a):
        im = a.get("impact") or {}
        pct = lambda v: None if v is None else round(v * 100, 2)          # noqa: E731
        return [nm, round(a.get("qty", 0)), round(a.get("rev", 0), 2), round(a.get("profit", 0), 2), pct(a.get("margin")),
                round(a.get("disc", 0), 2), pct(a.get("share")), pct(im.get("margin")), pct(im.get("margin_wo")),
                None if im.get("impact") is None else round(im["impact"], 2)]
    for sid in res["stores"]:
        rows.append(line(names.get(sid, str(sid)), res["store"][sid]))
    rows.append(line("TOTALE", res["total"]))
    for k, r in enumerate(rows):
        if r[9] is not None and r[9] < 0:
            marks[(k, 9)] = "RED"
    X.add_sheet(wb, "Negozi", f"{kind} – {p.get('name') or ''}", sub, cols, rows, marks=marks)
    st = res["stores"]
    cols2 = [X.Col("EAN", 15, "ean"), X.Col("Descrizione", 38), X.Col("Prezzo promo", 10, "price"),
             X.Col("Pezzi", 9, "num"), X.Col("Vendite €", 12, "price"), X.Col("Margine €", 11, "price"),
             X.Col("Margine %", 10, "rate"), X.Col("Sconto €", 11, "price"), X.Col("Pezzi prima", 10, "num")] + \
            [X.Col(names.get(s, str(s)), 10, "num", "sup") for s in st]
    rows2, marks2 = [], {}
    for k, r in enumerate(item_rows(res)):
        rows2.append([r["ean"], r["name"], _f(r.get("price")) or None, round(r["qty"]), round(r["rev"], 2),
                      round(r["profit"], 2), None if r["margin"] is None else round(r["margin"] * 100, 2),
                      round(r["disc"], 2), round(r["before"])] + [round(r["per_store"][s]) for s in st])
        if r["margin"] is not None and r["margin"] < 0:
            marks2[(k, 6)] = "RED"
        if r["qty"] <= 0:
            marks2[(k, 3)] = "GREY"
    X.add_sheet(wb, "Articoli", f"{kind} – {p.get('name') or ''}", sub, cols2, rows2, marks=marks2,
                photo_eans=[r[0] for r in rows2], url=url)
    wb.save(path)
    return path
