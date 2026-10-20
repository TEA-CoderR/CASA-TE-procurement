"""
报价表和数据库对比（只读）：回答「数据库的数据够不够代替供应商报价表」。

把 distribuire 里每份供应商报价表和数据库商品表（prodotti）对一遍：
  · 报价表里的条码，数据库里找得到几个
  · 数据库登记的供应商，是不是就是报这个价的批发商（按名称粗略对）
  · 数据库进价（PrezzoForn，每件）和报价表的件价差多少
  · 箱规（qtasmallbox）和报价表的每箱件数一样不一样
  · 同一个条码有几家批发商报价（数据库每个商品只有一个供应商、一个价，这部分数据库代替不了）
数据库只读，不写库。
"""
import re

TOL = 0.05          # 进价和报价差 5% 以内算「接近」


def db_items_for_eans(db, eans):
    """条码 → {"code", "name", "price"（每件进价，没有 = None）, "pack"（箱规，没有 = None）, "supplier"（供应商名称）}"""
    import pandas as pd
    import reorder_service as RS
    by_ean = RS.codes_from_eans(db, eans)
    codes = sorted({int(c) for c in by_ean.values()})
    info = {}
    for i in range(0, len(codes), 2000):
        chunk = codes[i:i + 2000]
        ph = ", ".join(["%s"] * len(chunk))
        df = pd.read_sql(f"SELECT p.CodArticolo, COALESCE(p.Descrizione,'') AS Descrizione, p.PrezzoForn, p.qtasmallbox, "
                         f"COALESCE(f.Nome,'') AS NomeFornitore FROM prodotti p "
                         f"LEFT JOIN fornitore f ON p.IDFornitore = f.IDFornitore "
                         f"WHERE p.CodArticolo IN ({ph})", db.engine, params=tuple(chunk))
        for _, r in df.iterrows():
            code = str(int(r["CodArticolo"]))
            if code not in info:
                info[code] = {"code": code, "name": str(r["Descrizione"] or "").strip(), "price": _num(r["PrezzoForn"]),
                              "pack": _num(r["qtasmallbox"]), "supplier": str(r["NomeFornitore"] or "").strip()}
    return {e: info[c] for e, c in by_ean.items() if c in info}


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v and v > 0 else None


def _words(name):
    """名称里有意义的词（4 个字母以上，大写），用来粗略判断两个供应商名称是不是同一家"""
    return {w for w in re.split(r"[^A-Z0-9]+", str(name or "").upper()) if len(w) >= 4 and w not in _STOP}


_STOP = {"FORMATTATO", "LISTINO", "PREZZI", "OFFERTA", "SPA", "SRL", "SNC", "XLSX", "CASH", "CARRY", "GROUP", "ITALIA"}


def same_supplier(label, db_name):
    """
    报价表的供应商名称和数据库登记的供应商是不是同一家（粗略）：有共同的词，
    或者数据库名称里的词（5 个字母以上）出现在连写的报价表名称里（例如 FlliPetrillo ↔ F.LLI PETRILLO）
    """
    a, b = _words(label), _words(db_name)
    if a and b and (a & b):
        return True
    flat = re.sub(r"[^A-Z0-9]", "", str(label or "").upper())
    return any(len(w) >= 5 and w in flat for w in b)


def compare(files, db_items, tol=TOL):
    """
    files:    {文件名称: {条码: {"price", "pack", "desc"}}}（service.read_price_file 的结果，名称用供应商名称）
    db_items: {条码: {"code", "name", "price", "pack", "supplier"}}（db_items_for_eans 的结果）
    返回 {"files": [每份报价表的统计], "total": {...}, "rows": [明细]}
    """
    out_files, rows = [], []
    quotes = {}                                      # 条码 → [(名称, 件价)]
    for label, items in files.items():
        st = {"name": label, "items": len(items), "in_db": 0, "same_sup": 0, "price_close": 0, "price_far": 0,
              "no_db_price": 0, "pack_same": 0, "pack_diff": 0, "db_suppliers": {}}
        for ean, q in items.items():
            quotes.setdefault(ean, []).append((label, q["price"]))
            d = db_items.get(ean)
            row = {"file": label, "ean": ean, "desc": q.get("desc") or "", "price": q["price"], "pack": q.get("pack"),
                   "in_db": bool(d), "db_name": "", "db_supplier": "", "db_price": None, "db_pack": None,
                   "diff": None, "same_sup": False}
            if d:
                st["in_db"] += 1
                sup = d.get("supplier") or "—"
                st["db_suppliers"][sup] = st["db_suppliers"].get(sup, 0) + 1
                row.update(db_name=d.get("name") or "", db_supplier=d.get("supplier") or "", db_price=d.get("price"),
                           db_pack=d.get("pack"))
                if same_supplier(label, d.get("supplier")):
                    st["same_sup"] += 1
                    row["same_sup"] = True
                if d.get("price") and q["price"]:
                    diff = (d["price"] - q["price"]) / q["price"]
                    row["diff"] = diff
                    st["price_close" if abs(diff) <= tol else "price_far"] += 1
                else:
                    st["no_db_price"] += 1
                if d.get("pack") and q.get("pack"):
                    st["pack_same" if abs(d["pack"] - q["pack"]) < 1e-9 else "pack_diff"] += 1
            rows.append(row)
        st["top_suppliers"] = sorted(st["db_suppliers"].items(), key=lambda kv: -kv[1])[:3]
        out_files.append(st)
    multi = {e: v for e, v in quotes.items() if len({n for n, _ in v}) >= 2}
    spread = sum(1 for v in multi.values() if min(p for _, p in v) > 0 and
                 (max(p for _, p in v) - min(p for _, p in v)) / min(p for _, p in v) > tol)
    eans = set(quotes)
    total = {"eans": len(eans), "in_db": sum(1 for e in eans if e in db_items), "multi": len(multi), "spread": spread,
             "files": len(files)}
    for r in rows:
        r["quotes"] = len(quotes.get(r["ean"], []))
    return {"files": out_files, "total": total, "rows": rows}


def conclusion(res):
    """一句话结论（中文原文，界面用 tr 翻译各部分）"""
    from i18n import tr
    t = res["total"]
    pct = t["in_db"] / t["eans"] if t["eans"] else 0
    lines = [tr("{0} 份报价表，一共 {1:,} 个条码，数据库里找得到 {2:,} 个（{3:.0%}）", t["files"], t["eans"], t["in_db"], pct)]
    if t["multi"]:
        lines.append(tr("{0:,} 个商品有两家以上批发商报价，其中 {1:,} 个各家价格差超过 5%；数据库每个商品只有一个供应商、一个进价，比不了价",
                        t["multi"], t["spread"]))
    close = sum(f["price_close"] for f in res["files"])
    far = sum(f["price_far"] for f in res["files"])
    nop = sum(f["no_db_price"] for f in res["files"])
    if close + far:
        lines.append(tr("数据库进价和报价接近（差 5% 以内）的 {0:,} 个，差得多的 {1:,} 个；数据库没有进价的 {2:,} 个",
                        close, far, nop))
    ps = sum(f["pack_same"] for f in res["files"])
    pd_ = sum(f["pack_diff"] for f in res["files"])
    if ps + pd_:
        lines.append(tr("箱规一样的 {0:,} 个，不一样的 {1:,} 个", ps, pd_))
    return lines


def export(res, path):
    """明细导出 Excel：每份报价表一行汇总 + 每个条码一行"""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "汇总"
    ws.append(["报价表", "条码数", "数据库有", "供应商对得上", "进价接近(±5%)", "进价差得多", "数据库没进价", "箱规一样",
               "箱规不一样", "数据库登记最多的供应商"])
    for f in res["files"]:
        ws.append([f["name"], f["items"], f["in_db"], f["same_sup"], f["price_close"], f["price_far"], f["no_db_price"],
                   f["pack_same"], f["pack_diff"], "、".join(f"{n} {c}" for n, c in f["top_suppliers"])])
    ws.append([])
    for line in conclusion(res):
        ws.append([line])
    for c in ws[1]:
        c.font = Font(bold=True)
    d = wb.create_sheet("明细")
    d.append(["报价表", "条码", "报价品名", "报价件价", "报价每箱件数", "几家报价", "数据库有", "数据库品名", "数据库供应商",
              "供应商对得上", "数据库进价", "进价差 %", "数据库箱规"])
    for r in res["rows"]:
        d.append([r["file"], r["ean"], r["desc"], r["price"], r["pack"], r["quotes"], "是" if r["in_db"] else "否",
                  r["db_name"], r["db_supplier"], "是" if r["same_sup"] else "", r["db_price"],
                  round(r["diff"] * 100, 1) if r["diff"] is not None else None, r["db_pack"]])
    for c in d[1]:
        c.font = Font(bold=True)
    d.freeze_panes = "A2"
    d.auto_filter.ref = d.dimensions
    wb.save(path)


def best_quotes(files):
    """
    几份报价表 → 每个条码最便宜那家：{条码: {"price", "pack", "supplier"}}
    （检查门店订货清单时还没比价，按最便宜那家估：比价默认就挑它）
    """
    best = {}
    for label, items in files.items():
        for ean, q in items.items():
            p = q.get("price")
            if not p or p <= 0:
                continue
            if ean not in best or p < best[ean]["price"]:
                best[ean] = {"price": float(p), "pack": float(q.get("pack") or 1), "supplier": label}
    return best


def apply_quotes(rows, quotes):
    """
    检查清单的行改用报价的件价和每箱件数（调货门槛、箱数显示、估算金额用）；报价里没有的保留数据库的。
    原来数据库的记在 db_pack / db_price，报价是哪家记在 quote_sup。清单件数（file_pcs）已经按数据库箱规折好，不动。
    返回改了几行
    """
    n = 0
    for x in rows:
        q = quotes.get(str(x.get("ean") or "").strip()) if x.get("in_db", True) else None
        if not q:
            continue
        x["db_pack"], x["db_price"] = x.get("pack"), x.get("price")
        x["pack"], x["price"], x["quote_sup"] = q["pack"], q["price"], q["supplier"]
        n += 1
    return n
