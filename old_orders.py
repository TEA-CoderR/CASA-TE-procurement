"""
历史未结订单核对（2026-11-26 用户同意第 3 条）：以前（默认 11 月以前）下的单还开着（已下单 / 已确认 / 卡住了…），
多半是货早到了、没人在程序里登记收货。按数据库入库记录查一遍：
  · 查得到入库（同一家供应商、同一家门店 / BMS 仓库、下单以后 MATCH_DAYS 天内有供应商送货 CA）→ 列出来，人确认后登记到货
    （收货记录写「入库记录（历史补登）」+ 入库日期 / 行数 / 件数 / 金额，不冒充哪个卸货人）；
  · 查不到 → 列出来，人决定取消订单还是继续跟踪（继续跟踪的记 old_ok，以后不再列）。
以后新的单照旧要门店的人点「确认收货」、选卸货人（用户：到货一定要人标、要有收货人），这里只清以前的旧账。不依赖界面。
"""
from datetime import date, datetime, timedelta

import order_tracking as OT

MATCH_DAYS = 60                 # 下单以后多少天内的入库算这张单的
BY = "入库记录（历史补登）"
ARRIVAL_TYPES = ("CA",)         # 供应商送货（调货 CI / UI 不算到货）
STORE_NEG = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "BMS": 0}


def default_before(today=None):
    """默认看这个月以前的单（这个月的还在正常走流程）"""
    t = today or date.today()
    return date(t.year, t.month, 1)


def candidates(st, before, rows=None):
    """还开着、下单日期在 before 以前、没被标过「继续跟踪」的单（DETER、待登记、作废的不算）"""
    rows = rows if rows is not None else OT.rows(st)
    out = []
    for r in rows:
        if r["status"] not in OT.OPEN or not r["date"] or r["date"] >= before:
            continue
        if (st.orders.get(r["id"]) or {}).get("old_ok"):
            continue
        out.append(r)
    return sorted(out, key=lambda r: (r["date"], r["supplier"]))


def _like(a, b):
    return a == b or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a))


def supplier_ids(rows, db_names):
    """订单跟踪的供应商（合并后的键）→ 数据库供应商编号 [..]（名字去掉 SRL / SPA 后一样，或一个包含另一个）"""
    keys = {r["sup_key"] for r in rows}
    dbk = [(int(i), OT.sup_key(n)) for i, n in (db_names or {}).items() if n]
    out = {}
    for k in keys:
        exact = [i for i, n in dbk if n == k]
        out[k] = exact or [i for i, n in dbk if _like(k, n)]
    return out


def match(rows, receipts, sup_ids, days=MATCH_DAYS):
    """
    receipts = [{"sup": 数据库供应商编号, "neg": 门店号, "day": date, "n": 行数, "q": 件数, "v": 金额}]（一天一家一行）
    每张单找下单当天到 days 天内、最早那次入库；同一次入库只给一张单（按下单日期先后分，免得两张单都算到同一车货上）。
    返回 {单号: 那次入库}
    """
    by = {}
    for x in receipts:
        by.setdefault((int(x["sup"]), int(x["neg"])), []).append(x)
    for v in by.values():
        v.sort(key=lambda x: x["day"])
    used, out = set(), {}
    for r in sorted(rows, key=lambda r: r["date"]):
        neg = STORE_NEG.get(str(r["store"]))
        if neg is None:
            continue
        best = None
        for sid in sup_ids.get(r["sup_key"]) or []:
            for x in by.get((int(sid), neg), []):
                k = (int(sid), neg, x["day"])
                if k in used or x["day"] < r["date"] or x["day"] > r["date"] + timedelta(days=days):
                    continue
                if best is None or x["day"] < best[1]["day"]:
                    best = (k, x)
                break
        if best:
            used.add(best[0])
            out[r["id"]] = dict(best[1], sup=best[0][0])
    return out


def query_receipts(db, sup_ids, since):
    """数据库（只读）：这几家供应商 since 以后每天每家门店的供应商送货（CA）"""
    import pandas as pd
    ids = sorted({int(i) for v in sup_ids.values() for i in v})
    if not ids:
        return []
    out = []
    tp = ", ".join(f"'{t}'" for t in ARRIVAL_TYPES)
    for i in range(0, len(ids), 500):
        ch = ids[i:i + 500]
        ph = ", ".join(["%s"] * len(ch))
        df = pd.read_sql(
            f"SELECT Fornitore AS sup, Negozio AS neg, DATE(Cdata) AS d, COUNT(*) AS n, SUM(Quantita) AS q, "
            f"SUM(Quantita * Prezzo_A) AS v FROM caricolistalist WHERE Fornitore IN ({ph}) AND Cdata >= %s "
            f"AND Quantita > 0 AND Tipo IN ({tp}) GROUP BY Fornitore, Negozio, DATE(Cdata)",
            db.engine, params=tuple(ch) + (since.isoformat(),))
        for x in df.to_dict("records"):
            try:
                d = x["d"] if isinstance(x["d"], date) else datetime.fromisoformat(str(x["d"])[:10]).date()
                if isinstance(d, datetime):
                    d = d.date()
                out.append({"sup": int(x["sup"]), "neg": int(x["neg"]), "day": d, "n": int(x["n"] or 0),
                            "q": float(x["q"] or 0), "v": float(x["v"] or 0)})
            except (TypeError, ValueError):
                continue
    return out


def check(st, db, before, log=print):
    """查一遍 → {"found": [(行, 入库)], "none": [行], "no_supplier": [行]}"""
    import reorder_service as R
    rows = candidates(st, before)
    if not rows:
        return {"found": [], "none": [], "no_supplier": []}
    names = R.supplier_names(db)
    ids = supplier_ids(rows, names)
    since = min(r["date"] for r in rows)
    rec = query_receipts(db, ids, since)
    got = match(rows, rec, ids)
    found = [(r, got[r["id"]]) for r in rows if r["id"] in got]
    no_sup = [r for r in rows if r["id"] not in got and not ids.get(r["sup_key"])]
    none = [r for r in rows if r["id"] not in got and ids.get(r["sup_key"])]
    for r, x in found:
        x["sup_name"] = names.get(int(x["sup"]), "")
    log(f"[订单跟踪] 历史未结订单 {len(rows)} 张：查得到入库 {len(found)} 张，查不到 {len(none)} 张，"
        f"数据库里对不上供应商 {len(no_sup)} 张")
    return {"found": found, "none": none, "no_supplier": no_sup}


def receipt_note(x):
    return (f"历史补登：数据库入库 {x['day']:%d/%m/%Y} · {x['n']} 行 · {x['q']:g} 件"
            + (f" · €{x['v']:,.2f}" if x.get("v") else ""))


def apply_arrived(st, found, pc=""):
    """查得到入库的 → 登记到货（收货记录写明是按入库记录补登的）；返回撤销用的 [(单号, 改之前的字段)]"""
    import receipt as RC
    ch, undo = [], []
    for r, x in found:
        o = st.orders.get(r["id"])
        if not o or OT.status(o) not in OT.OPEN:
            continue
        f = RC.changes(o, {"at": datetime(x["day"].year, x["day"].month, x["day"].day), "by": BY, "result": "full",
                           "note": receipt_note(x)}, pc=pc)
        undo.append((r["id"], {k: o.get(k) for k in f}))
        ch.append((r["id"], f))
    st.set_many(ch)
    return undo


def keep_tracking(st, ids):
    """查不到入库、但还要跟：记下来，以后不再列（订单本身不改）"""
    ch = [(oid, {"old_ok": date.today().isoformat()}) for oid in ids if oid in st.orders]
    st.set_many(ch)
    return [(oid, {"old_ok": None}) for oid, _f in ch]


def cancel(st, ids, note="历史单核对：数据库查不到入库，取消"):
    ch, undo = [], []
    for oid in ids:
        o = st.orders.get(oid)
        if not o:
            continue
        old = str(o.get("note") or "")
        undo.append((oid, {"state": o.get("state"), "note": o.get("note")}))
        ch.append((oid, {"state": "cancel", "note": (old + " · " if old else "") + note}))
    st.set_many(ch)
    return undo
