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
  销采率    差额 = 销售额 − 采购额；差额率 = 差额 ÷ 销售额（这段时间没有采购量的商品空着；合计 = 全部销售额 − 全部采购额）
  毛利      毛利额 = 销售额 − 销售记录的成本；毛利率 = 毛利额 ÷ 销售额
  剩余库存  剩余量 = 采购量 − 销售量；剩余额 = 剩余量 × 平均进价（采购量 ≤ 0 的商品空着，不算进合计）
  采购额    每行入库都按那一行的进价 Prezzo_A（调货 CI / UI 也有自己的进价）；那一行没有进价才按 CA 平均进价补
  2026-11-19 和 ERP「进销存报表」逐行对过（DUE ESSE 01/08–16/11、SAIS 01/01–16/11 · FOIANO）

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
PURCHASE_TYPES = ("CA", "CI", "UI", "MG")     # MG：ERP 进销存报表也算进货（2026-11-19 对过 FOIANO：差的 8 件就是 MG）
# 合计时直接相加的字段（剩余按每个商品算好再加，采购量 ≤ 0 的不算，和 ERP 合计行一样）
SUMS = ("stock", "pq", "pv", "sq", "sv", "cost", "rem_q", "rem_v")
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


def metrics(r, agg=False):
    """
    一行 → 平均进价 / 售价、销售率、差额、毛利、剩余（照 ERP 进销存报表）。
    agg=False（一个商品 / 一家店的一个商品）：采购量 ≤ 0 的 剩余空着（None），没有采购量的差额空着，不算进合计。
    agg=True（合计、按供应商 / 分类 / 门店合计）：剩余用加好的 rem_q / rem_v，差额 = 销售额 − 采购额。
    """
    pq, pv, sq, sv, cost = r["pq"], r["pv"], r["sq"], r["sv"], r["cost"]
    r["p_avg"] = pv / pq if pq else None
    r["s_avg"] = sv / sq if sq else None
    r["rate_q"] = sq / pq if pq > 0 else None
    r["rate_v"] = sv / pv if pv > 0 else None
    if not agg:
        bought = pq > 0
        if not bought:
            r["p_avg"] = None                         # 采购量 ≤ 0（只有调出 / 退货）：ERP 平均进价空着
        r["rem_q"] = pq - sq if bought else None
        r["rem_v"] = (pq - sq) * r["p_avg"] if bought else None
        r["diff"] = sv - pv if pq else None           # 有采购（负的也算）才写差额，ERP 一样
        r["diff_rate"] = r["diff"] / sv if r["diff"] is not None and sv else None
    else:
        r["diff"] = sv - pv                           # 合计 = 全部销售额 − 全部采购额（ERP 合计行一样）
        r["diff_rate"] = r["diff"] / sv if sv else None
    r["profit"] = sv - cost                           # 毛利 = 销售额 − 销售记录的成本（ERP 一样）
    r["margin"] = r["profit"] / sv if sv else None
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
    other, pdays, days_by = {}, {}, {}           # days_by：{地点: {日期: [销量, 销售额, 进货量, 进货额]}}（切门店时用）

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
        val = v + (q - qp) * unit(c)                  # 有进价的行按自己的进价（调货行也有），没有的按 CA 平均进价补
        x = per(r, s)
        x["pq"] += q
        x["pv"] += val
        a = pdays.setdefault(d, [0.0, 0.0])
        a[0] += q
        a[1] += val
        b = days_by.setdefault(s, {}).setdefault(d, [0.0, 0.0, 0.0, 0.0])
        b[2] += q
        b[3] += val
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
        for x in r["per"].values():
            metrics(x)
        metrics(r)
        r["bucket"] = bucket(r)
        out.append(r)
    out.sort(key=lambda r: -r["sv"])
    sdays = {}
    for sid, res in raw["sales"].items():
        for d, (q, rev) in res["days"].items():
            a = sdays.setdefault(d, [0.0, 0.0])
            a[0] += q
            a[1] += rev
            b = days_by.setdefault(sid, {}).setdefault(d, [0.0, 0.0, 0.0, 0.0])
            b[0] += q
            b[1] += rev
    days = []
    d = raw["start"]
    while d <= raw["end"]:
        k = d.isoformat()
        sq, sv = sdays.get(k, (0.0, 0.0))
        pq, pv = pdays.get(k, (0.0, 0.0))
        days.append((d, sq, sv, pq, pv))
        d += timedelta(days=1)
    return {"rows": out, "days": days, "days_by": days_by, "other_types": other, "start": raw["start"],
            "end": raw["end"], "scope": raw["scope"], "filters": raw.get("filters") or {}}


ITEM_KEYS = ("code", "ean", "name", "sup", "sup_name", "cat", "sub", "cat_name", "mid", "small", "forn")


def view(res, stores):
    """
    查一次「全部」以后，切到几家门店看（2026-11-16 用户：点门店直接切换，不再重新读数据库）：
    每个商品只加这几家的数字（res 里每行的 per[门店]），重新算平均价 / 销售率 / 剩余，每天的走势也只加这几家。
    这几家里没进没卖没库存的商品不列。stores 空 = 全部（原样返回）。
    和直接按这几家查数据库结果一样（调货一进一出照样在这几家之间抵掉）。
    """
    want = tuple(s for s in res["scope"] if s in set(stores or ()))
    if not want or want == tuple(res["scope"]):
        return res
    rows = []
    for r in res["rows"]:
        per = {s: r["per"][s] for s in want if s in r["per"]}
        if not per or not any(x.get(k) for x in per.values() for k in ("stock", "pq", "sq")):
            continue
        d = {k: r.get(k) for k in ITEM_KEYS}
        for k in ("stock", "pq", "pv", "sq", "sv", "cost"):
            d[k] = sum(x[k] for x in per.values())
        d["per"] = per
        metrics(d)
        d["bucket"] = bucket(d)
        rows.append(d)
    rows.sort(key=lambda r: -r["sv"])
    by = res.get("days_by") or {}
    days = []
    for day, *_rest in res["days"]:
        k = day.isoformat()
        a = [0.0, 0.0, 0.0, 0.0]
        for s in want:
            b = (by.get(s) or {}).get(k)
            if b:
                for i in range(4):
                    a[i] += b[i]
        days.append((day, a[0], a[1], a[2], a[3]))
    return dict(res, rows=rows, days=days, scope=want)


def _zero():
    return {k: 0.0 for k in SUMS}


def total(rows):
    t = _zero()
    for r in rows:
        for k in SUMS:
            t[k] += r.get(k) or 0.0
    t["n"] = len(rows)
    return metrics(t, agg=True)


GROUP_LABEL = {"item": "商品", "sup": "供应商", "cat": "大类", "mid": "中类", "small": "小类", "store": "门店"}


def group(rows, by):
    """按供应商 / 大类 / 中类 / 小类 / 门店 合计 → 行（name = 这一组的名字，n = 几个商品）"""
    if by == "item":
        return rows
    g = {}
    if by == "store":
        for r in rows:
            for s, x in r["per"].items():
                a = g.setdefault(s, dict(_zero(), key=s, name=NAMES.get(s, str(s)), n=0, codes=set()))
                for k in SUMS:
                    a[k] += x.get(k) or 0.0
                a["codes"].add(r["code"])
        out = []
        for s, a in sorted(g.items()):
            a["n"] = len(a.pop("codes"))
            out.append(metrics(a, agg=True))
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
        a = g.setdefault(key, dict(_zero(), key=key, name=name, n=0, cat_name=r["cat_name"],
                                   mid=r["mid"] if by in ("mid", "small") else "",
                                   small=r["small"] if by == "small" else ""))
        for k in SUMS:
            a[k] += r.get(k) or 0.0
        a["n"] += 1
    out = [metrics(a, agg=True) for a in g.values()]
    out.sort(key=lambda r: -r["sv"])
    return out


def bucket_counts(rows):
    """[(键, 名称, 颜色, 商品数, 剩余额)]"""
    c = {k: [0, 0.0] for k, _n, _t in BUCKETS}
    for r in rows:
        a = c[r["bucket"]]
        a[0] += 1
        a[1] += r["rem_v"] or 0.0
    return [(k, n, t, c[k][0], c[k][1]) for k, n, t in BUCKETS]


def scope_label(scope):
    scope = tuple(scope)
    if scope == ALL_SCOPE:
        return "TUTTI PDV"
    return " + ".join(NAMES.get(s, str(s)) for s in scope)


# ---------------------------------------------------------------- 导出（照 SELLOUT_HALLOWEEN.xlsx）
HEAD2 = ("No", "FOTO", "Codice", "Ean", "Descrizione", "现有库存", "采购量", "平均进价", "采购额", "销售量", "平均售价",
         "销售额", "Qt", "Valore", "差额率", "销售-采购", "毛利率", "毛利额", "剩余量", "剩余额", "商家", "大类", "中类", "小类",
         "卖得怎样")
BANDS = ((7, 9, "采购"), (10, 12, "销售量"), (13, 14, "销售率"), (15, 16, "销采率"), (17, 18, "毛利"), (19, 20, "剩余库存"),
         (21, 25, "分类"))
HINTS = {13: "销售量/采购量", 14: "销售额/采购额", 15: "(销售-采购）/销售", 16: "销售-采购",
         17: "（单品毛利*件数）/总销售额", 18: "单品毛利额*销售件数", 19: "采购量-销售量", 20: "剩余量*平均进价"}
WIDTHS = (6, 9, 14, 16, 44, 10, 10, 10, 12, 10, 10, 12, 9, 9, 10, 12, 10, 12, 10, 12, 28, 26, 22, 32, 11)
# 颜色（导出用，和界面一样一组一种颜色）
NAVY, INK, MUTED, LINE, ZEBRA = "1F3A5F", "1F2937", "6B7280", "D9DEE7", "F7F9FC"
BAND_FILL = {"采购": ("EDE9FE", "5B21B6"), "销售量": ("DBEAFE", "1D4ED8"), "销售率": ("CFFAFE", "0E7490"),
             "销采率": ("FEF3C7", "A16207"), "毛利": ("DCFCE7", "15803D"), "剩余库存": ("FFE4E6", "BE123C"),
             "分类": ("F3F4F6", "374151")}
BUCKET_FILL = {"out": ("DCFCE7", "15803D"), "good": ("CFFAFE", "0E7490"), "mid": ("DBEAFE", "1D4ED8"),
               "slow": ("FEF3C7", "A16207"), "none": ("FEE2E2", "B91C1C"), "nobuy": ("EDE9FE", "6D28D9"),
               "stock": ("F3F4F6", "6B7280")}
F_QTY = '#,##0;[Red]-#,##0;"–"'
F_EUR = '€ #,##0.00;[Red]-€ #,##0.00;"–"'
F_PCT = '0.0%;[Red]-0.0%;"–"'
FMTS = {6: F_QTY, 7: F_QTY, 8: F_EUR, 9: F_EUR, 10: F_QTY, 11: F_EUR, 12: F_EUR, 13: F_PCT, 14: F_PCT, 15: F_PCT,
        16: F_EUR, 17: F_PCT, 18: F_EUR, 19: F_QTY, 20: F_EUR}
TOP = 6                          # 表头第 6 行（1 标题 · 2 说明 · 3 指标 · 4 空 · 5 分组 · 6 列名 · 7 公式说明）
FIRST = 8


def store_rows(rows, sid):
    """商品行 → 这一家店的数字（每行的 per[门店]）；这家店没进没卖没库存的不要"""
    out = []
    for r in rows:
        x = (r.get("per") or {}).get(sid)
        if not x or not any(x.get(k) for k in ("stock", "pq", "sq")):
            continue
        d = {k: r.get(k) for k in ("code", "ean", "name", "sup", "sup_name", "cat", "sub", "cat_name", "mid", "small")}
        for k in ("stock", "pq", "pv", "sq", "sv", "cost"):
            d[k] = x.get(k) or 0.0
        metrics(d)
        d["bucket"] = bucket(d)
        d["per"] = {sid: x}
        out.append(d)
    out.sort(key=lambda r: -r["sv"])
    return out


def _font(**k):
    from openpyxl.styles import Font
    import xlsx_report as X
    return Font(name=X.XL_FONT, size=k.pop("size", 10), **k)


def _kpi_line(t):
    def e(v):
        return "–" if v is None else f"€ {v:,.0f}"

    def pc(v):
        return "–" if v is None else f"{v * 100:.1f}%"
    return (f"Acquisti {e(t['pv'])} ({t['pq']:,.0f} pz)   ·   Vendite {e(t['sv'])} ({t['sq']:,.0f} pz)   ·   "
            f"Sell-through {pc(t['rate_q'])} pz / {pc(t['rate_v'])} €   ·   Margine {e(t['profit'])} ({pc(t['margin'])})   ·   "
            f"Rimanenza {e(t['rem_v'])} ({t['rem_q']:,.0f} pz)")


def _head(ws, title, sub, t, ncol):
    """第 1–3 行：深蓝标题条 · 灰色说明 · 一行指标"""
    from openpyxl.styles import Alignment, PatternFill
    from openpyxl.utils import get_column_letter as L
    ws.row_dimensions[1].height = 30
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 24
    ws.row_dimensions[4].height = 6
    for c in range(1, ncol + 1):
        ws.cell(1, c).fill = PatternFill("solid", fgColor=NAVY)
        ws.cell(3, c).fill = PatternFill("solid", fgColor="EEF2F7")
    ws.cell(1, 1, title).font = _font(size=15, bold=True, color="FFFFFF")
    ws.cell(1, 1).alignment = Alignment(vertical="center", indent=1)
    ws.cell(2, 1, sub).font = _font(size=9, color=MUTED)
    ws.cell(2, 1).alignment = Alignment(vertical="center", indent=1)
    ws.cell(3, 1, _kpi_line(t)).font = _font(size=10, bold=True, color=NAVY)
    ws.cell(3, 1).alignment = Alignment(vertical="center", indent=1)
    for r in (1, 2, 3):
        ws.merge_cells(f"A{r}:{L(ncol)}{r}")


def _item_sheet(wb, name, title, sub, rows, by, url):
    """一张进销存表（照 SELLOUT 模板的列）"""
    from openpyxl.styles import Alignment, Border, PatternFill, Side
    from openpyxl.utils import get_column_letter as L
    from openpyxl.formatting.rule import DataBarRule
    ws = wb.create_sheet(_sheet_name(wb, name))
    item = by == "item"
    ncol = len(HEAD2) if item else 24
    thin = Side(style="thin", color=LINE)
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    t = total(rows)
    _head(ws, title, sub, t, ncol)
    for c in range(1, ncol + 1):
        ws.column_dimensions[L(c)].width = WIDTHS[c - 1]
    if not item:
        ws.column_dimensions["B"].width = 3
        ws.column_dimensions["C"].width = 3
        ws.column_dimensions["D"].width = 11
    ws.row_dimensions[TOP - 1].height = 20
    ws.row_dimensions[TOP].height = 30
    ws.row_dimensions[TOP + 1].height = 24
    # 分组表头 + 列名
    for a, b, text in BANDS:
        b = min(b, ncol)
        bg, fg = BAND_FILL[text]
        ws.merge_cells(start_row=TOP - 1, start_column=a, end_row=TOP - 1, end_column=b)
        ws.cell(TOP - 1, a, text)
        for c in range(a, b + 1):
            ws.cell(TOP - 1, c).fill = PatternFill("solid", fgColor=fg)
            ws.cell(TOP - 1, c).border = box
        ws.cell(TOP - 1, a).font = _font(bold=True, color="FFFFFF")
        ws.cell(TOP - 1, a).alignment = center
    heads = list(HEAD2[:ncol])
    if not item:
        heads[1], heads[2], heads[3], heads[4] = "", "", "N. articoli", GROUP_LABEL.get(by, "")
    for c, h in enumerate(heads, 1):
        band = next((tx for a, b, tx in BANDS if a <= c <= b), None)
        bg, fg = BAND_FILL[band] if band else ("E5E7EB", INK)
        cell = ws.cell(TOP, c, h)
        cell.fill = PatternFill("solid", fgColor=bg)
        cell.font = _font(bold=True, color=fg if band else INK)
        cell.alignment = center
        cell.border = box
    for c, h in HINTS.items():
        cell = ws.cell(TOP + 1, c, h)
        cell.font = _font(size=7, color="9CA3AF")
        cell.alignment = center
    # 数据
    for i, r in enumerate(rows):
        y = FIRST + i
        zebra = PatternFill("solid", fgColor=ZEBRA) if i % 2 else None
        if item:
            img = f'=IFERROR(_xlfn.IMAGE("{(url or _img_url()).format(ean=r["ean"])}","",3,40,40),"")' if r["ean"] else ""
            ean = int(r["ean"]) if r["ean"].isdigit() and len(r["ean"]) < 16 else r["ean"]
            ids = [i + 1, img, r["code"], ean, r["name"]]
            tail = [r.get("sup_name") or "", r.get("cat_name") or "", r.get("mid") or "", r.get("small") or "",
                    dict((k, n) for k, n, _t in BUCKETS).get(r.get("bucket"), "")]
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
            cell.font = _font(color=INK)
            if zebra is not None:
                cell.fill = zebra
            if c in FMTS:
                cell.number_format = FMTS[c]
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif c == 4:
                cell.number_format = "0"
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif c <= 3:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(vertical="center", wrap_text=c == 5)
        if not item:
            ws.cell(y, 5).font = _font(color=INK, bold=True)
        if item:
            bg, fg = BUCKET_FILL.get(r.get("bucket"), ("FFFFFF", INK))
            cb = ws.cell(y, 25)
            cb.fill = PatternFill("solid", fgColor=bg)
            cb.font = _font(bold=True, color=fg, size=9)
            cb.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[y].height = 34 if item else 20
    # 合计
    y = FIRST + len(rows)
    last = max(y - 1, FIRST)
    S = lambda c: f"=SUM({L(c)}{FIRST}:{L(c)}{last})"           # noqa: E731
    tot = {5: "TOTALE", 6: S(6), 7: S(7), 8: f"=IFERROR(I{y}/G{y},0)", 9: S(9), 10: S(10),
           11: f"=IFERROR(L{y}/J{y},0)", 12: S(12), 13: f"=IFERROR(J{y}/G{y},0)", 14: f"=IFERROR(L{y}/I{y},0)",
           15: f"=IFERROR((L{y}-I{y})/L{y},0)", 16: f"=L{y}-I{y}", 17: f"=IFERROR(R{y}/L{y},0)", 18: S(18),
           19: S(19), 20: S(20)}
    if not item:
        tot[4] = S(4)
    for c in range(1, ncol + 1):
        cell = ws.cell(y, c, tot.get(c))
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = _font(bold=True, color="FFFFFF")
        cell.border = box
        if c in FMTS:
            cell.number_format = FMTS[c].replace("[Red]", "")
            cell.alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[y].height = 24
    # 销售率两列：绿色数据条（一眼看出卖得快慢）
    if rows:
        for col, color in (("M", "34D399"), ("N", "60A5FA")):
            ws.conditional_formatting.add(f"{col}{FIRST}:{col}{last}",
                                          DataBarRule(start_type="num", start_value=0, end_type="num", end_value=1.5,
                                                      color=color, showValue=True))
    ws.freeze_panes = f"F{FIRST}"
    ws.auto_filter.ref = f"C{TOP}:{L(ncol)}{last}"
    ws.sheet_view.zoomScale = 90
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{TOP - 1}:{TOP}"
    return ws


STORE_HEAD = (("Negozio", 18, None), ("现有库存", 11, F_QTY), ("采购量", 11, F_QTY), ("采购额", 14, F_EUR),
              ("销售量", 11, F_QTY), ("销售额", 14, F_EUR), ("销售率 Qt", 11, F_PCT), ("销售率 Valore", 12, F_PCT),
              ("毛利额", 14, F_EUR), ("毛利率", 10, F_PCT), ("剩余量", 11, F_QTY), ("剩余额", 14, F_EUR),
              ("N. articoli", 11, F_QTY))


def _store_sheet(wb, title, sub, items, scope):
    """门店对比：一家店一行 + 合计（门店名可以点，跳到这家店那一页）"""
    from openpyxl.styles import Alignment, Border, PatternFill, Side
    from openpyxl.utils import get_column_letter as L
    from openpyxl.formatting.rule import DataBarRule
    ws = wb.create_sheet(_sheet_name(wb, "NEGOZI"), 0)
    ncol = len(STORE_HEAD)
    thin = Side(style="thin", color=LINE)
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    _head(ws, title, sub, total(items), ncol)
    for c, (h, w, _f) in enumerate(STORE_HEAD, 1):
        ws.column_dimensions[L(c)].width = w
        cell = ws.cell(TOP, c, h)
        cell.fill = PatternFill("solid", fgColor=NAVY if c == 1 else "E5E7EB")
        cell.font = _font(bold=True, color="FFFFFF" if c == 1 else INK)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = box
    ws.row_dimensions[TOP].height = 30
    g = {x["key"]: x for x in group(items, "store")}
    order = [s for s in scope if s in g]
    y = TOP + 1
    for i, s in enumerate(order):
        r = g[s]
        vals = [NAMES.get(s, str(s)), r["stock"], r["pq"], r["pv"], r["sq"], r["sv"], r["rate_q"], r["rate_v"],
                r["profit"], r["margin"], r["rem_q"], r["rem_v"], r["n"]]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(y, c, round(v, 4) if isinstance(v, float) else v)
            cell.border = box
            cell.font = _font(color=INK, bold=c == 1)
            if i % 2:
                cell.fill = PatternFill("solid", fgColor=ZEBRA)
            if STORE_HEAD[c - 1][2]:
                cell.number_format = STORE_HEAD[c - 1][2]
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = Alignment(vertical="center", indent=1)
        link = _sheet_title(wb, NAMES.get(s, str(s)))
        if link:
            from openpyxl.worksheet.hyperlink import Hyperlink
            ws.cell(y, 1).hyperlink = Hyperlink(ref=ws.cell(y, 1).coordinate, location=f"'{link}'!A1")
            ws.cell(y, 1).font = _font(color="1D4ED8", bold=True, underline="single")
        ws.row_dimensions[y].height = 24
        y += 1
    last = y - 1
    S = lambda c: f"=SUM({L(c)}{TOP + 1}:{L(c)}{last})"           # noqa: E731
    tot = {1: "TOTALE", 2: S(2), 3: S(3), 4: S(4), 5: S(5), 6: S(6), 7: f"=IFERROR(E{y}/C{y},0)",
           8: f"=IFERROR(F{y}/D{y},0)", 9: S(9), 10: f"=IFERROR(I{y}/F{y},0)", 11: S(11), 12: S(12),
           13: total(items)["n"]}
    for c in range(1, ncol + 1):
        cell = ws.cell(y, c, tot.get(c))
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = _font(bold=True, color="FFFFFF")
        cell.border = box
        if STORE_HEAD[c - 1][2]:
            cell.number_format = STORE_HEAD[c - 1][2].replace("[Red]", "")
            cell.alignment = Alignment(horizontal="right", vertical="center")
        else:
            cell.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[y].height = 26
    if order:
        ws.conditional_formatting.add(f"F{TOP + 1}:F{last}", DataBarRule(start_type="num", start_value=0,
                                                                         end_type="max", color="60A5FA", showValue=True))
        ws.conditional_formatting.add(f"G{TOP + 1}:G{last}", DataBarRule(start_type="num", start_value=0, end_type="num",
                                                                         end_value=1.5, color="34D399", showValue=True))
    ws.freeze_panes = f"B{TOP + 1}"
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return ws


def _sheet_name(wb, name):
    n = re.sub(r"[\\/*?:\[\]]", "-", str(name))[:31] or "Foglio"
    base, k = n, 2
    while n in wb.sheetnames:
        n = f"{base[:28]} {k}"
        k += 1
    return n


def _sheet_title(wb, name):
    n = re.sub(r"[\\/*?:\[\]]", "-", str(name))[:31]
    return n if n in wb.sheetnames else None


def export(res, rows, path, title="", url=None, by="item", items=None, filters_text=""):
    """
    写 Excel：
      NEGOZI     门店对比（几家店才有）：一家店一行 + 合计，店名点了跳到那一页
      TUTTI PDV  汇总（选的门店合在一起，就是 rows = 现在表格里看到的行）
      每家门店一页（同样的列，数字只算这家店）
    每页：深蓝标题条 · 说明 · 一行指标 · 分组表头（颜色和界面一样）· 斑马纹 · 销售率数据条 · 负数红字 · 最后一行合计公式。
    items = 商品行（按供应商 / 分类看时，各店那几页从商品行重新合计；不给就用 res["rows"]）
    """
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    scope = list(res["scope"])
    shops = [x for x in scope if x != WAREHOUSE] + [x for x in scope if x == WAREHOUSE]      # 仓库放最后
    items = list(items if items is not None else (rows if by == "item" else res["rows"]))
    period = f"{res['start']:%d/%m/%Y} – {res['end']:%d/%m/%Y}"
    sub = " · ".join(x for x in (period, filters_text, f"generato il {date.today():%d/%m/%Y}") if x)
    lab = scope_label(scope)
    multi = len(shops) > 1
    # 汇总
    _item_sheet(wb, "TUTTI PDV" if scope == list(ALL_SCOPE) else lab, title or f"SELL OUT · {lab}",
                sub + f" · {len(rows):,} righe", rows, by, url)
    # 每家门店
    if multi:
        for s in (shops if by != "store" else []):
            sr = store_rows(items, s)
            if not sr:
                continue
            data = sr if by == "item" else group(sr, by)
            _item_sheet(wb, NAMES.get(s, str(s)), f"SELL OUT · {NAMES.get(s, str(s))}",
                        sub + f" · {len(data):,} righe", data, by, url)
        _store_sheet(wb, f"SELL OUT · {lab} · confronto negozi", sub, items, shops)
        wb.active = 0
    wb.save(path)
    return path


def _img_url():
    try:
        import product_images as PI
        return PI.DEFAULT_URL
    except Exception:
        return "https://img.casaete.top/prodotti/{ean}/01.webp"
