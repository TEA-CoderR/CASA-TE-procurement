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


def read_file(path):
    """上传的 VOLANTINO 名单 → {"items": [...], "start", "end", "name"}（日期认不出是 None，界面里让人选）"""
    import reorder_service as R
    info = R.parse_order_file(path)
    items = [{"code": x.get("code") or "", "ean": x.get("ean") or "", "name": x.get("name") or ""} for x in info["rows"]]
    texts = [os.path.splitext(os.path.basename(path))[0]]
    prices = {}
    try:                                             # 促销价列、文件最上面几行写的日期（读不到不要紧）
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb[info["sheet"]]
            if hasattr(ws, "reset_dimensions"):
                ws.reset_dimensions()
            hdr = info["header_row"]
            pc = None
            for r, row in enumerate(ws.iter_rows(values_only=True), 1):
                if r < hdr:
                    texts += [str(v) for v in row if v not in (None, "")]
                    continue
                if r == hdr:
                    for c, v in enumerate(row):
                        n = re.sub(r"[\s._\-]+", "", str(v or "")).lower()
                        if n and any(n.startswith(h) for h in PRICE_HEADS):
                            pc = c
                            break
                    if pc is None:
                        break
                    continue
                k = r - hdr - 1
                prices[k] = row[pc] if pc < len(row) else None
        finally:
            wb.close()
    except Exception:
        pass
    start, end = guess_dates(texts)
    return {"items": items, "start": start, "end": end, "name": os.path.splitext(os.path.basename(path))[0],
            "prices": prices}


# ---------------------------------------------------------------- 保存的几期
def store_path(base):
    return os.path.join(base, "cache", "volantino", "periods.json")


def load_periods(base):
    try:
        with open(store_path(base), encoding="utf-8") as f:
            data = json.load(f)
        return [p for p in data.get("periods") or [] if p.get("start") and p.get("end")]
    except (OSError, ValueError):
        return []


def save_periods(base, periods):
    p = store_path(base)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump({"periods": [x for x in periods if x.get("source") != "db"]}, f, ensure_ascii=False, indent=0)
    os.replace(tmp, p)


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
    """数据库里的促销单（最近的在前）：[{… source "db", promo, items 空（选中时再读）}]；查不了返回 []"""
    import pandas as pd
    since = since or (date.today() - timedelta(days=400))
    try:
        df = pd.read_sql("SELECT p.l, p.Data_a, p.Data_f, p.negozio, COUNT(pl.CodArticolo) AS n "
                         "FROM promozioni p LEFT JOIN promozioni_list pl ON pl.promozioni_l = p.l "
                         "AND (pl.invalid IS NULL OR pl.invalid = 0) "
                         "WHERE p.Data_f >= %s GROUP BY p.l, p.Data_a, p.Data_f, p.negozio", db.engine, params=(since,))
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
        s, e = _d(r["Data_a"]), _d(r["Data_f"])
        if not s or not e or not r.get("n"):
            continue
        l_ = int(r["l"])
        out.append({"id": f"db{l_}", "promo": l_, "source": "db", "start": s.isoformat(), "end": e.isoformat(),
                    "stores": _stores_of(r.get("negozio")), "items": [], "n": int(r["n"]),
                    "name": titles.get(l_) or f"Promozione {l_}"})
    out.sort(key=lambda p: p["start"], reverse=True)
    return out


def db_items(db, promo):
    """一张促销单的商品：[{code, ean, name, price}]"""
    import pandas as pd
    df = pd.read_sql("SELECT pl.CodArticolo, pl.Prezzo_p, p.CodBarre, p.Descrizione FROM promozioni_list pl "
                     "LEFT JOIN prodotti p ON p.CodArticolo = pl.CodArticolo "
                     "WHERE pl.promozioni_l = %s AND (pl.invalid IS NULL OR pl.invalid = 0)", db.engine, params=(int(promo),))
    out = []
    for r in df.to_dict("records"):
        try:
            code = str(int(r["CodArticolo"]))
        except (TypeError, ValueError):
            continue
        out.append({"code": code, "ean": str(r.get("CodBarre") or "").strip(), "name": str(r.get("Descrizione") or "").strip(),
                    "price": r.get("Prezzo_p")})
    return out


# ---------------------------------------------------------------- 计算
def month_range(start, end, upto=None):
    """周期跨过的整月：开始那个月 1 号 → 结束那个月最后一天（不超过 upto）"""
    m1 = start.replace(day=1)
    nxt = (end.replace(day=28) + timedelta(days=4)).replace(day=1)
    m2 = nxt - timedelta(days=1)
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
    out = {"items": {}, "before": {}, "days": {}, "month": {}}
    nxt = d2 + timedelta(days=1)
    if codes:
        for i in range(0, len(codes), 2000):
            chunk = codes[i:i + 2000]
            ph = ", ".join(["%s"] * len(chunk))
            df = pd.read_sql(f"SELECT CodArticolo, {_SUMS} FROM {t} WHERE CodArticolo IN ({ph}) AND Data >= %s AND Data < %s "
                             f"GROUP BY CodArticolo", db.engine, params=tuple(chunk) + (d1, nxt))
            for r in df.to_dict("records"):
                out["items"][str(int(r["CodArticolo"]))] = _money(r)
            df = pd.read_sql(f"SELECT CodArticolo, SUM(Qta) AS q FROM {t} WHERE CodArticolo IN ({ph}) "
                             f"AND Data >= %s AND Data < %s GROUP BY CodArticolo", db.engine, params=tuple(chunk) + (b1, d1))
            for r in df.to_dict("records"):
                out["before"][str(int(r["CodArticolo"]))] = _f(r.get("q"))
            df = pd.read_sql(f"SELECT DATE(Data) AS d, {_SUMS} FROM {t} WHERE CodArticolo IN ({ph}) AND Data >= %s AND Data < %s "
                             f"GROUP BY DATE(Data)", db.engine, params=tuple(chunk) + (d1, nxt))
            for r in df.to_dict("records"):
                k = str(r["d"])[:10]
                m = _money(r)
                cur = out["days"].setdefault(k, {"rev": 0.0, "profit": 0.0, "qty": 0.0})
                for f in cur:
                    cur[f] += m[f]
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
      "store": {门店: {qty rev cost profit margin disc erp sold(卖出几个商品) share(占当月销售额) month{…} impact{…}}},
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
    out_store, total, no_cost = {}, {"sold": 0}, set()
    total_month = {}
    for sid in stores:
        res = per[sid]
        agg = {"sold": 0}
        for c, m in res["items"].items():
            row = by_code.get(c)
            if row is None:
                continue
            m = summarize(dict(m, before=res["before"].get(c, 0.0)))
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
        agg["impact"] = month_impact(agg["month"], agg["disc"])
        agg["share"] = agg["rev"] / agg["month"]["rev"] if agg["month"]["rev"] else None
        out_store[sid] = agg
        _add(total, agg)
        _add(total_month, agg["month"])
    for f in ("qty", "rev", "cost", "profit", "disc", "erp"):
        total.setdefault(f, 0.0)
        total_month.setdefault(f, 0.0)
    summarize(total)
    total["month"] = summarize(total_month)
    total["impact"] = month_impact(total_month, total["disc"])
    total["share"] = total["rev"] / total_month["rev"] if total_month["rev"] else None
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
    log(f"[VOLANTINO] {period.get('name') or ''} {d1:%d/%m}–{d2:%d/%m}：{len(rows)} 个商品（{len(codes)} 个对上数据库），"
        f"卖出 {total['sold']} 个；销售额 €{total['rev']:,.2f}、毛利 €{total['profit']:,.2f}（ERP 毛利合计 €{total['erp']:,.2f}）")
    return {"period": period, "start": d1, "end": d2, "month": (m1, m2), "before": (b1, d1 - timedelta(days=1)),
            "stores": stores, "store": out_store, "total": total, "items": rows,
            "days": {sid: per[sid]["days"] for sid in stores}, "no_cost": len(no_cost),
            "matched": len(codes)}


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
    sub = f"{res['start']:%d/%m/%Y} – {res['end']:%d/%m/%Y} · " \
          f"mese {res['month'][0]:%d/%m} – {res['month'][1]:%d/%m/%Y}"
    cols = [X.Col("Negozio", 16), X.Col("Pezzi", 9, "num"), X.Col("Vendite € (netto IVA)", 14, "price"),
            X.Col("Margine €", 12, "price"), X.Col("Margine %", 10, "rate"), X.Col("Sconto € (volantino)", 14, "price"),
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
    X.add_sheet(wb, "Negozi", f"Volantino – {p.get('name') or ''}", sub, cols, rows, marks=marks)
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
    X.add_sheet(wb, "Articoli", f"Volantino – {p.get('name') or ''}", sub, cols2, rows2, marks=marks2,
                photo_eans=[r[0] for r in rows2], url=url)
    wb.save(path)
    return path
