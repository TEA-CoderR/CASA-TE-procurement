# -*- coding: utf-8 -*-
"""
SELL OUT 分析（进销存，2026-11-16 用户：照 ERP「进销存报表」做，筛选时间、供应商、大 / 中 / 小类、门店…，导出照 SELLOUT_HALLOWEEN.xlsx）

不依赖界面。数据库只读。

一行 = 一个商品在选的日期、门店范围里：
  现有库存  giacenza G0..G5（选的门店合计；「全部」含仓库）
  采购      caricolistalist：CA = 供应商进货（有进价 Prezzo_A），CI = 调入（+），UI = 调出（−）；UI-N 等别的类型不算
            选几家门店时，它们之间的调货一进一出抵掉；只看一家店时，从仓库 / 别的店调进来的也算这家店的进货
            调货没有进价 → 按这个商品这段时间 CA 的平均进价算，没有就用销售记录的进价，再没有用商品表的 PrezzoForn
  销售      vendite_list_<门店>：销量 Qta，销售额 = TotRighe − TotCodIva（不含税），成本 = TotAcquisto
  销售率    件数 = 销售量 ÷ 采购量；金额 = 销售额 ÷ 采购额
  销采率    差额 = 销售额 − 采购额；差额率 = 差额 ÷ 销售额
  毛利      毛利额 = 销售额 − 成本；毛利率 = 毛利额 ÷ 销售额
  剩余库存  剩余量 = 采购量 − 销售量；剩余额 = 剩余量 × 平均进价（和模板一样，是这批货还剩多少，不是现在的库存）

分类：ERP 的 categorie 只有两级（Categoria 大类 / Sottocategoria 小类）。小类的名字多半写成「中类 - 小类」
（例：FESTE SPECIALI - ACC.CARNEVALE E HALLOWEEN、CAFFETTIERE E ACCESSORI - BOLLITORI）→ 按第一个「 - 」拆成中类和小类；
没有「 - 」的整个名字当中类，小类空着。
"""
import re
from datetime import date, datetime, timedelta

STORES = ((1, "FOIANO"), (2, "MARLIA"), (3, "S.FILIPPO"), (4, "S.MARCO"), (5, "S.CONCORDIO"))
WAREHOUSE = 0
ALL_SCOPE = (0, 1, 2, 3, 4, 5)
NAMES = dict(STORES)
NAMES[WAREHOUSE] = "BMS"
PURCHASE_TYPES = ("CA", "CI", "UI")
SUMS = ("stock", "pq", "pv", "sq", "sv", "cost", "rem_v")
GROUPS = ("item", "sup", "cat", "mid", "small", "store")
CHUNK = 1500
_DASH = re.compile(r"\s+-\s+")


def _f(v):
    try:
        x = float(v)
        return 0.0 if x != x else x
    except (TypeError, ValueError):
        return 0.0


def _d(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 日期
PRESETS = (("month", "本月"), ("last_month", "上月"), ("d30", "最近 30 天"), ("d90", "最近 90 天"),
           ("year", "今年"), ("custom", "自定义"))


def preset_range(key, today=None):
    """快捷日期 → (开始, 结束)，含当天"""
    t = today or date.today()
    if key == "month":
        return t.replace(day=1), t
    if key == "last_month":
        end = t.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if key == "d30":
        return t - timedelta(days=29), t
    if key == "d90":
        return t - timedelta(days=89), t
    if key == "year":
        return date(t.year, 1, 1), t
    return None


# ---------------------------------------------------------------- 分类
def split_sub(name):
    """小类名称 → (中类, 小类)"""
    s = re.sub(r"\s+", " ", str(name or "")).strip()
    if not s:
        return "", ""
    parts = _DASH.split(str(name).strip(), maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return re.sub(r"\s+", " ", parts[0]).strip(), re.sub(r"\s+", " ", parts[1]).strip()
    return s, ""


def build_tree(rows):
    """
    categorie 表的行 [(Categorie, SottoCategorie 或 None, Descrizione)] →
      {"cats": {大类号: 名称}, "subs": {(大类号, 小类号): {"cat", "sub", "mid", "small", "name"}}}
    """
    cats, subs = {}, {}
    for c, s, n in rows:
        c = _i(c)
        if c is None:
            continue
        name = re.sub(r"\s+", " ", str(n or "")).strip()
        s = _i(s)
        if s is None:
            cats[c] = name
        else:
            mid, small = split_sub(name)
            subs[(c, s)] = {"cat": c, "sub": s, "mid": mid, "small": small, "name": name}
    return {"cats": cats, "subs": subs}


_TREE = {}


def category_tree(db):
    """categorie 表（一小时读一次）"""
    import time
    import pandas as pd
    key = str(getattr(getattr(db, "engine", None), "url", ""))
    hit = _TREE.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    df = pd.read_sql("SELECT Categorie, SottoCategorie, Descrizione FROM categorie", db.engine)
    tree = build_tree(zip(df["Categorie"], df["SottoCategorie"], df["Descrizione"]))
    _TREE[key] = (time.time(), tree)
    return tree


def mids_of(tree, cats=()):
    """中类列表 [(大类号, 中类名)]（选了大类只列那几个大类的）"""
    seen = []
    for (c, _s), x in sorted(tree["subs"].items(), key=lambda kv: (kv[0][0], kv[1]["mid"])):
        if cats and c not in cats:
            continue
        k = (c, x["mid"])
        if x["mid"] and k not in seen:
            seen.append(k)
    return seen


def subs_of(tree, cats=(), mids=()):
    """小类列表 [(大类号, 小类号)]：有小类名的（选了大类 / 中类只列里面的）"""
    out = []
    for (c, s), x in sorted(tree["subs"].items(), key=lambda kv: (kv[0][0], kv[1]["mid"], kv[1]["small"])):
        if cats and c not in cats:
            continue
        if mids and (c, x["mid"]) not in mids:
            continue
        if x["small"]:
            out.append((c, s))
    return out


def sub_pairs(tree, f):
    """筛选里的分类 → 允许的 (大类号, 小类号)；None = 不按小类筛"""
    subs = [tuple(x) for x in (f.get("subs") or [])]
    if subs:
        return set(subs)
    mids = {tuple(x) for x in (f.get("mids") or [])}
    if mids:
        return {k for k, x in tree["subs"].items() if (k[0], x["mid"]) in mids}
    return None


def prod_where(f, tree, alias="p"):
    """筛选 → (SQL 条件, 参数)；没有任何商品条件 → ("", ())"""
    conds, params = [], []
    sups = [int(x) for x in (f.get("sups") or [])]
    if sups:
        conds.append(f"{alias}.IDFornitore IN ({', '.join(['%s'] * len(sups))})")
        params += sups
    cats = [int(x) for x in (f.get("cats") or [])]
    if cats:
        conds.append(f"{alias}.Categoria IN ({', '.join(['%s'] * len(cats))})")
        params += cats
    pairs = sub_pairs(tree, f)
    if pairs is not None:
        if not pairs:
            conds.append("1 = 0")
        else:
            ors = []
            for c, s in sorted(pairs):
                ors.append(f"({alias}.Categoria = %s AND {alias}.Sottocategoria = %s)")
                params += [c, s]
            conds.append("(" + " OR ".join(ors) + ")")
    codes = [int(x) for x in (f.get("codes") or [])]
    if codes:
        conds.append(f"{alias}.CodArticolo IN ({', '.join(['%s'] * len(codes))})")
        params += codes
    return " AND ".join(conds), tuple(params)


def scope_of(f):
    """选的门店 → 要算的地点（空 = 全部，含仓库）"""
    st = [int(x) for x in (f.get("stores") or [])]
    return tuple(sorted(set(st))) if st else ALL_SCOPE


# ---------------------------------------------------------------- 读数据库
_COST = "CASE WHEN v.TotAcquisto IS NOT NULL AND v.TotAcquisto <> 0 THEN v.TotAcquisto ELSE v.PrezzoAcquisto * v.Qta END"


def _sales_store(db, sid, d1, d2, where, params):
    import pandas as pd
    t = f"vendite_list_{int(sid)}"
    join = " JOIN prodotti p ON p.CodArticolo = v.CodArticolo" if where else ""
    w = f"v.Data >= %s AND v.Data < %s" + (f" AND {where}" if where else "")
    prm = (d1, d2 + timedelta(days=1)) + params
    items = pd.read_sql(f"SELECT v.CodArticolo AS c, SUM(v.Qta) AS q, SUM(v.TotRighe - v.TotCodIva) AS rev, "
                        f"SUM({_COST}) AS cost FROM {t} v{join} WHERE {w} GROUP BY v.CodArticolo",
                        db.engine, params=prm)
    days = pd.read_sql(f"SELECT DATE(v.Data) AS d, SUM(v.Qta) AS q, SUM(v.TotRighe - v.TotCodIva) AS rev "
                       f"FROM {t} v{join} WHERE {w} GROUP BY DATE(v.Data)", db.engine, params=prm)
    return {"items": {int(r["c"]): (_f(r["q"]), _f(r["rev"]), _f(r["cost"])) for r in items.to_dict("records")
                      if _i(r["c"]) is not None},
            "days": {str(r["d"])[:10]: (_f(r["q"]), _f(r["rev"])) for r in days.to_dict("records")}}


def _purchases(db, d1, d2, where, params):
    """入库每天每个商品每个地点每种类型一行（所有地点都读：CA 平均进价按全部算，调货在 Python 里按范围挑）"""
    import pandas as pd
    join = " JOIN prodotti p ON p.CodArticolo = l.CodArticolo" if where else ""
    w = "l.Cdata >= %s AND l.Cdata <= %s" + (f" AND {where}" if where else "")
    df = pd.read_sql("SELECT l.Cdata AS d, l.CodArticolo AS c, l.Negozio AS s, l.Tipo AS t, SUM(l.Quantita) AS q, "
                     "SUM(CASE WHEN l.Prezzo_A > 0 THEN l.Quantita * l.Prezzo_A ELSE 0 END) AS v, "
                     "SUM(CASE WHEN l.Prezzo_A > 0 THEN l.Quantita ELSE 0 END) AS qp "
                     f"FROM caricolistalist l{join} WHERE {w} GROUP BY l.Cdata, l.CodArticolo, l.Negozio, l.Tipo",
                     db.engine, params=(d1, d2) + params)
    out = []
    for r in df.to_dict("records"):
        c, s = _i(r["c"]), _i(r["s"])
        if c is None or s is None:
            continue
        out.append((str(r["d"])[:10], c, s, str(r["t"] or "").strip().upper(), _f(r["q"]), _f(r["v"]), _f(r["qp"])))
    return out


def _chunks(codes):
    codes = sorted(codes)
    for i in range(0, len(codes), CHUNK):
        yield codes[i:i + CHUNK]


def _stock(db, codes, where, params, scope):
    """{商品: {地点: 库存}}：要的商品 + （有商品筛选时）筛选里所有有库存的商品"""
    import pandas as pd
    cols = ", ".join(f"g.G{s}" for s in ALL_SCOPE)
    out = {}

    def take(df):
        for r in df.to_dict("records"):
            c = _i(r["CodArticolo"])
            if c is None:
                continue
            out[c] = {s: _f(r.get(f"G{s}")) for s in ALL_SCOPE}
    for ch in _chunks(codes):
        take(pd.read_sql(f"SELECT g.CodArticolo, {cols} FROM giacenza g WHERE g.CodArticolo IN "
                         f"({', '.join(['%s'] * len(ch))})", db.engine, params=tuple(ch)))
    if where:
        nz = " OR ".join(f"g.G{s} <> 0" for s in scope)
        take(pd.read_sql(f"SELECT g.CodArticolo, {cols} FROM giacenza g JOIN prodotti p ON p.CodArticolo = g.CodArticolo "
                         f"WHERE {where} AND ({nz})", db.engine, params=params))
    return out


def _products(db, codes):
    import pandas as pd
    out = {}
    for ch in _chunks(codes):
        df = pd.read_sql("SELECT CodArticolo, CodBarre, Descrizione, IDFornitore, Categoria, Sottocategoria, PrezzoForn "
                         f"FROM prodotti WHERE CodArticolo IN ({', '.join(['%s'] * len(ch))})", db.engine, params=tuple(ch))
        for r in df.to_dict("records"):
            c = _i(r["CodArticolo"])
            if c is not None:
                out[c] = r
    return out


def query(db, f, log=None, on_store=None):
    """读数据库 → 原始数据（给 build 用）"""
    import reorder_service as R
    log = log or (lambda *_: None)
    d1, d2 = _d(f["start"]), _d(f["end"])
    if not d1 or not d2 or d2 < d1:
        raise ValueError("开始 / 结束日期不对")
    tree = category_tree(db)
    where, params = prod_where(f, tree)
    scope = scope_of(f)
    shops = [s for s in scope if s != WAREHOUSE]
    sales = R.per_store(shops, lambda sid: _sales_store(db, sid, d1, d2, where, params), on_store) if shops else {}
    purch = _purchases(db, d1, d2, where, params)
    codes = {c for s in sales.values() for c in s["items"]} | {r[1] for r in purch if r[2] in scope}
    stock = _stock(db, codes, where, params, scope)
    codes |= {c for c, g in stock.items() if any(g.get(s) for s in scope)}
    prods = _products(db, codes)
    try:
        sups = R.supplier_names(db)
    except Exception:
        sups = {}
    return {"start": d1, "end": d2, "scope": scope, "tree": tree, "sales": sales, "purch": purch, "stock": stock,
            "prods": prods, "sups": sups, "filters": dict(f)}


# ---------------------------------------------------------------- 计算（纯函数）
def _blank():
    return {"stock": 0.0, "pq": 0.0, "pv": 0.0, "sq": 0.0, "sv": 0.0, "cost": 0.0}


def metrics(r):
    """一行的合计 → 加上平均进价 / 售价、销售率、差额率、毛利、剩余（r 里要有 stock pq pv sq sv cost；rem_v 可以先给）"""
    pq, pv, sq, sv, cost = r["pq"], r["pv"], r["sq"], r["sv"], r["cost"]
    r["p_avg"] = pv / pq if pq else None
    r["s_avg"] = sv / sq if sq else None
    r["rate_q"] = sq / pq if pq > 0 else None
    r["rate_v"] = sv / pv if pv > 0 else None
    r["diff"] = sv - pv
    r["diff_rate"] = (sv - pv) / sv if sv else None
    r["profit"] = sv - cost
    r["margin"] = (sv - cost) / sv if sv else None
    r["rem_q"] = pq - sq
    if "rem_v" not in r:
        unit = r["p_avg"] if r["p_avg"] is not None else (cost / sq if sq else 0.0)
        r["rem_v"] = r["rem_q"] * unit
    return r


def bucket(r):
    """卖得怎么样：out 卖完 / good 卖得好（70–100%）/ mid 一般（30–70%）/ slow 滞销（< 30%）/ none 没卖出 /
       nobuy 没进货只有销售 / stock 只有库存（这段时间没进没卖）"""
    pq, sq = r["pq"], r["sq"]
    if pq <= 0:
        return "nobuy" if sq > 0 else "stock"
    if sq <= 0:
        return "none"
    rq = sq / pq
    return "out" if rq >= 1 else "good" if rq >= 0.7 else "mid" if rq >= 0.3 else "slow"


BUCKETS = (("out", "卖完", "ok"), ("good", "卖得好", "cyan"), ("mid", "一般", "accent"), ("slow", "滞销", "warn"),
           ("none", "没卖出", "err"), ("nobuy", "没进货", "violet"), ("stock", "只有库存", "muted"))


def build(raw):
    """原始数据 → {"rows": [商品行], "days": [(日期, 销量, 销售额, 进货量, 进货额)], "other_types": {类型: 件数}, …}"""
    scope = set(raw["scope"])
    tree, prods, sups = raw["tree"], raw["prods"], raw["sups"]
    rows = {}

    def row(c):
        if c not in rows:
            p = prods.get(c) or {}
            cat, sub = _i(p.get("Categoria")), _i(p.get("Sottocategoria"))
            sx = tree["subs"].get((cat, sub)) or {}
            sid = _i(p.get("IDFornitore"))
            rows[c] = dict(_blank(), code=c, ean=str(p.get("CodBarre") or "").strip(),
                           name=str(p.get("Descrizione") or "").strip(), sup=sid, sup_name=sups.get(sid, "") if sid else "",
                           cat=cat, sub=sub, cat_name=tree["cats"].get(cat, ""), mid=sx.get("mid", ""),
                           small=sx.get("small", ""), forn=_f(p.get("PrezzoForn")), per={})
        return rows[c]

    def per(r, s):
        return r["per"].setdefault(s, _blank())
    # 销售
    for s, res in raw["sales"].items():
        for c, (q, rev, cost) in res["items"].items():
            r = row(c)
            x = per(r, s)
            x["sq"] += q
            x["sv"] += rev
            x["cost"] += cost
    # 进货：先算每个商品的 CA 平均进价（全部地点）
    ca = {}
    for _d_, c, _s, t, q, v, qp in raw["purch"]:
        if t == "CA" and qp > 0:
            a = ca.setdefault(c, [0.0, 0.0])
            a[0] += v
            a[1] += qp
    other, pdays = {}, {}

    def unit(c):
        if c in ca and ca[c][1]:
            return ca[c][0] / ca[c][1]
        r = rows.get(c)
        if r:
            sq = sum(x["sq"] for x in r["per"].values())
            if sq > 0:
                return sum(x["cost"] for x in r["per"].values()) / sq
            if r["forn"] > 0:
                return r["forn"]
        p = prods.get(c) or {}
        return _f(p.get("PrezzoForn"))
    for d, c, s, t, q, v, qp in raw["purch"]:
        if s not in scope:
            continue
        if t not in PURCHASE_TYPES:
            other[t] = other.get(t, 0.0) + q
            continue
        r = row(c)
        val = v + (q - qp) * unit(c) if t == "CA" else q * unit(c)
        x = per(r, s)
        x["pq"] += q
        x["pv"] += val
        a = pdays.setdefault(d, [0.0, 0.0])
        a[0] += q
        a[1] += val
    # 库存
    for c, g in raw["stock"].items():
        if any(g.get(s) for s in scope) or c in rows:
            r = row(c)
            for s in scope:
                if g.get(s):
                    per(r, s)["stock"] += g[s]
    out = []
    for c, r in rows.items():
        for k in ("stock", "pq", "pv", "sq", "sv", "cost"):
            r[k] = sum(x[k] for x in r["per"].values())
        for s, x in r["per"].items():
            unit_ = (x["pv"] / x["pq"]) if x["pq"] else (r["pv"] / r["pq"] if r["pq"] else
                                                       (x["cost"] / x["sq"] if x["sq"] else 0.0))
            x["rem_v"] = (x["pq"] - x["sq"]) * unit_
        metrics(r)
        r["bucket"] = bucket(r)
        out.append(r)
    out.sort(key=lambda r: -r["sv"])
    sdays = {}
    for res in raw["sales"].values():
        for d, (q, rev) in res["days"].items():
            a = sdays.setdefault(d, [0.0, 0.0])
            a[0] += q
            a[1] += rev
    days = []
    d = raw["start"]
    while d <= raw["end"]:
        k = d.isoformat()
        sq, sv = sdays.get(k, (0.0, 0.0))
        pq, pv = pdays.get(k, (0.0, 0.0))
        days.append((d, sq, sv, pq, pv))
        d += timedelta(days=1)
    return {"rows": out, "days": days, "other_types": other, "start": raw["start"], "end": raw["end"],
            "scope": raw["scope"], "filters": raw.get("filters") or {}}


def total(rows):
    t = _blank()
    t["rem_v"] = 0.0
    for r in rows:
        for k in SUMS:
            t[k] += r.get(k) or 0.0
    t["n"] = len(rows)
    return metrics(t)


GROUP_LABEL = {"item": "商品", "sup": "供应商", "cat": "大类", "mid": "中类", "small": "小类", "store": "门店"}


def group(rows, by):
    """按供应商 / 大类 / 中类 / 小类 / 门店 合计 → 行（name = 这一组的名字，n = 几个商品）"""
    if by == "item":
        return rows
    g = {}
    if by == "store":
        for r in rows:
            for s, x in r["per"].items():
                a = g.setdefault(s, dict(_blank(), rem_v=0.0, key=s, name=NAMES.get(s, str(s)), n=0, codes=set()))
                for k in SUMS:
                    a[k] += x.get(k) or 0.0
                a["codes"].add(r["code"])
        out = []
        for s, a in sorted(g.items()):
            a["n"] = len(a.pop("codes"))
            out.append(metrics(a))
        out.sort(key=lambda r: -r["sv"])
        return out
    for r in rows:
        if by == "sup":
            key, name = r["sup"], r["sup_name"] or (str(r["sup"]) if r["sup"] else "—")
        elif by == "cat":
            key, name = r["cat"], r["cat_name"] or "—"
        elif by == "mid":
            key, name = (r["cat"], r["mid"]), r["mid"] or "—"
        else:
            key, name = (r["cat"], r["sub"]), r["small"] or r["mid"] or "—"
        a = g.setdefault(key, dict(_blank(), rem_v=0.0, key=key, name=name, n=0, cat_name=r["cat_name"],
                                   mid=r["mid"] if by in ("mid", "small") else "",
                                   small=r["small"] if by == "small" else ""))
        for k in SUMS:
            a[k] += r.get(k) or 0.0
        a["n"] += 1
    out = [metrics(a) for a in g.values()]
    out.sort(key=lambda r: -r["sv"])
    return out


def bucket_counts(rows):
    """[(键, 名称, 颜色, 商品数, 剩余额)]"""
    c = {k: [0, 0.0] for k, _n, _t in BUCKETS}
    for r in rows:
        a = c[r["bucket"]]
        a[0] += 1
        a[1] += r["rem_v"]
    return [(k, n, t, c[k][0], c[k][1]) for k, n, t in BUCKETS]


def scope_label(scope):
    scope = tuple(scope)
    if scope == ALL_SCOPE:
        return "TUTTI PDV"
    return " + ".join(NAMES.get(s, str(s)) for s in scope)


# ---------------------------------------------------------------- 导出（照 SELLOUT_HALLOWEEN.xlsx）
HEAD2 = ("No", "FOTO", "Codice", "Ean", "Descrizione", "现有库存", "采购量", "平均进价", "采购额", "销售量", "平均售价",
         "销售额", "Qt", "Valore", "差额率", "销售-采购", "毛利率", "毛利额", "剩余量", "剩余额", "商家", "大类", "中类", "小类")
BANDS = ((7, 9, "采购"), (10, 12, "销售量"), (13, 14, "销售率"), (15, 16, "销采率"), (17, 18, "毛利"), (19, 20, "剩余库存"),
         (21, 24, "分类"))
HINTS = {15: "(销售-采购）/销售", 16: "销售-采购", 17: "（单品毛利*件数）/总销售额", 18: "单品毛利额*销售件数",
         19: "采购量-销售量", 20: "剩余量*平均进价"}
WIDTHS = (6, 9, 13, 16, 44, 10, 9, 9, 11, 9, 9, 11, 8, 8, 9, 11, 9, 11, 9, 11, 26, 22, 24, 30)
BAND_FILL = {"采购": "EDE9FE", "销售量": "DBEAFE", "销售率": "CFFAFE", "销采率": "FEF3C7", "毛利": "DCFCE7",
             "剩余库存": "FFE4E6", "分类": "F3F4F6"}


def export(res, rows, path, title="", url=None, by="item"):
    """写 Excel：第 1 行分组表头、第 2 行列名、第 3 行公式说明、第 4 行起数据、最后一行合计（SUM 公式）"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as L
    import xlsx_report as X
    wb = Workbook()
    ws = wb.active
    ws.title = (scope_label(res["scope"]) if by != "store" else "PDV")[:31].replace("/", "-")
    thin = Side(style="thin", color="D0D5DD")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    font = lambda **k: Font(name=X.XL_FONT, size=k.pop("size", 10), **k)        # noqa: E731
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    item = by == "item"
    for c in range(1, 25):
        ws.column_dimensions[L(c)].width = WIDTHS[c - 1]
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 22
    ws.row_dimensions[3].height = 30
    # 第 1 行左边：标题
    ws.cell(1, 1, title or f"SELL OUT · {res['start']:%d/%m/%Y} – {res['end']:%d/%m/%Y}")
    ws.cell(1, 1).font = font(size=12, bold=True, color="1F3A5F")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=6)
    for a, b, text in BANDS:
        ws.merge_cells(start_row=1, start_column=a, end_row=1, end_column=b)
        cell = ws.cell(1, a, text)
        for c in range(a, b + 1):
            ws.cell(1, c).fill = PatternFill("solid", fgColor=BAND_FILL[text])
            ws.cell(1, c).border = box
        cell.font = font(bold=True, color="1F2937")
        cell.alignment = center
    heads = list(HEAD2)
    if not item:
        heads[2], heads[3], heads[4] = "", "N. articoli", GROUP_LABEL.get(by, "")
    for c, h in enumerate(heads, 1):
        cell = ws.cell(2, c, h)
        band = next((t for a, b, t in BANDS if a <= c <= b), None)
        cell.fill = PatternFill("solid", fgColor=BAND_FILL[band] if band else "F3F4F6")
        cell.font = font(bold=True, color="1F2937")
        cell.alignment = center
        cell.border = box
    for c, h in HINTS.items():
        cell = ws.cell(3, c, h)
        cell.font = font(size=8, color="6B7280")
        cell.alignment = center
    first = 4
    neg = Font(name=X.XL_FONT, size=10, color="B42318")
    fmts = {6: "#,##0", 7: "#,##0", 8: "#,##0.00", 9: "#,##0.00", 10: "#,##0", 11: "#,##0.00", 12: "#,##0.00",
            13: "0.0%", 14: "0.0%", 15: "0.0%", 16: "#,##0.00", 17: "0.0%", 18: "#,##0.00", 19: "#,##0", 20: "#,##0.00"}
    for i, r in enumerate(rows):
        y = first + i
        if item:
            img = f'=IFERROR(_xlfn.IMAGE("{(url or _img_url()).format(ean=r["ean"])}","",3,48,48),"")' if r["ean"] else ""
            ids = [i + 1, img, r["code"], int(r["ean"]) if r["ean"].isdigit() and len(r["ean"]) < 16 else r["ean"],
                   r["name"]]
            tail = [r.get("sup_name") or "", r.get("cat_name") or "", r.get("mid") or "", r.get("small") or ""]
        else:
            ids = [i + 1, "", "", r["n"], r["name"]]
            tail = ["", r.get("cat_name") or "", r.get("mid") or "", r.get("small") or ""]
        vals = ids + [r["stock"], r["pq"], r["p_avg"], r["pv"], r["sq"], r["s_avg"], r["sv"], r["rate_q"], r["rate_v"],
                      r["diff_rate"], r["diff"], r["margin"], r["profit"], r["rem_q"], r["rem_v"]] + tail
        for c, v in enumerate(vals, 1):
            if isinstance(v, float):
                v = round(v, 4)
            cell = ws.cell(y, c, v)
            cell.border = box
            cell.font = font()
            if c in fmts:
                cell.number_format = fmts[c]
                cell.alignment = Alignment(horizontal="right", vertical="center")
                if isinstance(v, (int, float)) and v < 0:
                    cell.font = neg
            elif c == 4:
                cell.number_format = "0"
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif c <= 3:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(vertical="center", wrap_text=c == 5)
        ws.row_dimensions[y].height = 40 if item else 20
    # 合计
    y = first + len(rows)
    last = y - 1
    S = lambda c: f"=SUM({L(c)}{first}:{L(c)}{last})"           # noqa: E731
    tot = {5: "TOTALE", 6: S(6), 7: S(7), 8: f"=IFERROR(I{y}/G{y},\"\")", 9: S(9), 10: S(10),
           11: f"=IFERROR(L{y}/J{y},\"\")", 12: S(12), 13: f"=IFERROR(J{y}/G{y},\"\")", 14: f"=IFERROR(L{y}/I{y},\"\")",
           15: f"=IFERROR((L{y}-I{y})/L{y},\"\")", 16: f"=L{y}-I{y}", 17: f"=IFERROR(R{y}/L{y},\"\")", 18: S(18),
           19: S(19), 20: S(20)}
    for c in range(1, 25):
        cell = ws.cell(y, c, tot.get(c))
        cell.fill = PatternFill("solid", fgColor="1F3A5F")
        cell.font = font(bold=True, color="FFFFFF")
        cell.border = box
        if c in fmts:
            cell.number_format = fmts[c]
            cell.alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[y].height = 22
    ws.freeze_panes = "F4"
    ws.auto_filter.ref = f"C2:X{max(last, 2)}"
    ws.sheet_view.zoomScale = 90
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    wb.save(path)
    return path


def _img_url():
    try:
        import product_images as PI
        return PI.DEFAULT_URL
    except Exception:
        return "https://img.casaete.top/prodotti/{ean}/01.webp"
