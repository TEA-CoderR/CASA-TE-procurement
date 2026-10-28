"""
Griglia（2026-10-28）：我们自己的 DETERSIVI 订货商品总表（ELENCO_DETERSIVI.xlsx），和批发商报价对比。

  放在工作文件夹的「0 Griglia」里（放最新的一份；程序用修改时间最新的那个 Excel）。
  · 检查门店清单：不在 Griglia 里的商品单独列出来（门店没注意发了别的区域的商品，这几家批发商多半没有 → 缺失）；
  · 统计页（批发商比价 → Griglia）：
      都不做了 = Griglia 里有、这周所有批发商报价里都没有；
      新品     = 报价里有（在我们这几类里）、Griglia 里没有：几家在做、各家价格；
      价格     = Griglia 里的商品现在各家的价格、最便宜的是哪家，和 Griglia 记的价比。
  「我们这几类」：报价里有部门列的（Listino 的 Descr.Reparto、formattato 的 descrizione_reparto），
  看 Griglia 的商品落在哪些部门（≥ 10 个、占这个部门 ≥ 2%），只算这些部门；没有部门列的报价（PETRILLO、NEW GRIECO 本来就是做这类的）全算。
不依赖界面，可以单独测试。
"""
import os
import re
import json
import hashlib

from utils import clean_ean

GRID_DIR = "0 Griglia"
CACHE_VER = 1
STORES = ("FOIANO", "MARLIA", "S.FILIPPO", "S.MARCO", "S.CONCORDIO")
EXCEL = (".xlsx", ".xlsm")


def grid_dir(root):
    return os.path.join(root, GRID_DIR)


def find_file(root):
    """「0 Griglia」里修改时间最新的 Excel；没有返回 None"""
    d = grid_dir(root) if root else ""
    if not d or not os.path.isdir(d):
        return None
    fs = [os.path.join(d, f) for f in os.listdir(d)
          if f.lower().endswith(EXCEL) and not f.startswith(("~", "$")) and os.path.isfile(os.path.join(d, f))]
    return max(fs, key=os.path.getmtime) if fs else None


def _norm(h):
    return re.sub(r"\s+", " ", str(h or "")).strip().upper()


def _key(s):
    """供应商名比较用：只留字母数字（F.LLI PETRILLO → FLLIPETRILLO）"""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _num(v):
    try:
        f = float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None
    return f if f and f > 0 else None


# Griglia 的表头 → 字段（大写、空格合并后比较）
_FIELDS = {"COD.ART.": "code", "CODICE EAN": "ean", "DESCRIZIONE PRODOTTO": "name", "PZ*COLLI": "pack",
           "PREZZO NETTO": "price", "MARCA": "brand", "FORNITORE": "supplier", "CAT.1": "cat1", "CAT 2": "cat2",
           "CAT 3": "cat3", "PA MIGLIORE": "best", "PVP CASA & TE": "pvp"}
# 「PA INGROSSISTI」下面的几家批发商价格列
_SUP_COLS = ("MELONI", "MEGA CASH", "F.LLI PETRILLO", "NEW GRIECO")
MIN_PRICE = 0.05            # 件价低于这个当成报价表写错了（可疑价格）
ODD_RATIO = 0.3             # 比别家中间价的 30% 还低也算可疑


def parse_rows(rows):
    """
    Griglia 的所有行（值）→ {"items": {条码: {...}}, "suppliers": [价格列的批发商], "by_code": {商品编号: 条码}}。
    表头在前 10 行里找有「Codice EAN」的那一行
    """
    rows = list(rows)
    hr = next((i for i, r in enumerate(rows[:10]) if any(_norm(v) == "CODICE EAN" for v in r)), None)
    if hr is None:
        raise ValueError("Griglia 里找不到「Codice EAN」表头")
    heads = [_norm(v) for v in rows[hr]]
    col = {}
    for i, h in enumerate(heads):
        f = _FIELDS.get(h)
        if f and f not in col:
            col[f] = i
    sup_cols = {h: i for i, h in enumerate(heads) if h in _SUP_COLS}
    store_cols = {h: i for i, h in enumerate(heads) if h in STORES}
    items, by_code = {}, {}
    for r in rows[hr + 1:]:
        def g(f):
            i = col.get(f)
            return r[i] if i is not None and i < len(r) else None
        e = clean_ean(g("ean"))
        if not e:
            continue
        code = str(g("code") or "").strip()
        if code.endswith(".0"):
            code = code[:-2]
        it = {"ean": e, "code": code, "name": str(g("name") or "").strip(), "brand": str(g("brand") or "").strip(),
              "supplier": str(g("supplier") or "").strip(), "price": _num(g("price")), "best": _num(g("best")),
              "pack": _num(g("pack")), "cat": " / ".join(str(g(c) or "").strip() for c in ("cat1", "cat2", "cat3")
                                                         if str(g(c) or "").strip()),
              "prices": {h: _num(r[i]) for h, i in sup_cols.items() if i < len(r) and _num(r[i])},
              "stores": [h for h, i in store_cols.items() if i < len(r) and _num(r[i])]}
        items[e] = it
        if code:
            by_code[code] = e
    return {"items": items, "suppliers": list(sup_cols), "by_code": by_code}


def load(path, cache_dir=None):
    """读 Griglia（同一个文件读过就用缓存）：parse_rows 的结果 + path / name / mtime"""
    st = os.stat(path)
    cp = None
    if cache_dir:
        key = f"{CACHE_VER}|{os.path.normcase(os.path.abspath(path))}|{st.st_size}|{st.st_mtime_ns}"
        cp = os.path.join(cache_dir, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")
        if os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        out = parse_rows(wb.worksheets[0].iter_rows(values_only=True))
    finally:
        wb.close()
    out.update(path=path, name=os.path.basename(path), mtime=st.st_mtime)
    if cp:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cp + ".tmp", "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
            os.replace(cp + ".tmp", cp)
        except Exception:
            pass
    return out


def load_for(root):
    """工作文件夹里的 Griglia（没有返回 None）"""
    p = find_file(root)
    if not p:
        return None
    try:
        from settings import app_dir
        cache = os.path.join(app_dir(), "cache", "griglia")
    except Exception:
        cache = None
    return load(p, cache)


def contains(grid, ean=None, code=None):
    """这个商品在不在 Griglia 里（按条码，或按我们的商品编号）"""
    if not grid:
        return None
    e = clean_ean(ean)
    if e and e in grid["items"]:
        return True
    c = str(code or "").strip()
    return bool(c and c in grid.get("by_code", {}))


def relevant_depts(grid_eans, quotes):
    """
    报价里有部门的：哪些部门算「我们这几类」（Griglia 的商品在这个部门 ≥ 10 个、占 ≥ 2%）。
    返回 {报价名: 部门集合}；没有部门列的报价不在里面（全算）
    """
    out = {}
    for label, items in quotes.items():
        tot, hit = {}, {}
        for e, q in items.items():
            d = q.get("dept")
            if d is None:
                continue
            tot[d] = tot.get(d, 0) + 1
            hit[d] = hit.get(d, 0) + (e in grid_eans)
        if tot:
            out[label] = {d for d in tot if hit.get(d, 0) >= 10 and hit[d] / tot[d] >= 0.02}
    return out


def stats(grid, quotes):
    """
    Griglia 和这周报价对比。quotes = {批发商名: {条码: {"price", "pack", "desc", "dept"?}}}
    返回 {"gone": [...], "new": [...], "prices": [...], "labels": [批发商名]}
    """
    items = grid["items"]
    labels = list(quotes)
    eans = set(items)
    rel = relevant_depts(eans, quotes)
    offer = {}                                   # 条码 → {批发商: 报价}
    for label, qs in quotes.items():
        for e, q in qs.items():
            offer.setdefault(e, {})[label] = q

    def best(o):
        ok = {k: v for k, v in o.items() if not v.get("_odd")}
        if not ok:
            return None, None
        lab = min(ok, key=lambda k: ok[k]["price"])
        return ok[lab]["price"], lab

    odd = []                                     # 可疑价格：报价表里的明显错误（洗衣液 €0.02），不拿来比最低价
    for e, o in offer.items():
        for lab, q in o.items():
            others = sorted(v["price"] for k, v in o.items() if k != lab)
            med = others[len(others) // 2] if others else None
            if q["price"] < MIN_PRICE or (med and q["price"] < ODD_RATIO * med):
                o[lab] = dict(q, _odd=True)
                odd.append({"ean": e, "supplier": lab, "price": q["price"], "others": med,
                            "name": q.get("desc") or (items.get(e) or {}).get("name", "")})

    gone = [dict(it) for e, it in items.items() if e not in offer]
    new = []
    for e, o in offer.items():
        if e in eans:
            continue
        # 有部门的报价：这个条码只要在一家报价里属于「我们这几类」就算；只在没部门的报价里出现的也算
        ok = False
        for lab, q in o.items():
            if lab not in rel or q.get("dept") in rel[lab]:
                ok = True
                break
        if not ok:
            continue
        p, lab = best(o)
        desc = next((q.get("desc") for q in o.values() if q.get("desc")), "")
        dept = next((q.get("dept") for q in o.values() if q.get("dept")), "")
        new.append({"ean": e, "name": desc, "dept": dept, "n": len(o), "best": p, "best_sup": lab,
                    "pack": o[lab].get("pack") if lab else None, "prices": {k: v["price"] for k, v in o.items()}})
    prices = []
    for e, it in items.items():
        o = offer.get(e)
        if not o:
            continue
        p, lab = best(o)
        ref = it.get("best") or it.get("price")
        prices.append({"ean": e, "code": it.get("code"), "name": it.get("name"), "brand": it.get("brand"),
                       "supplier": it.get("supplier"), "ref": ref, "best": p, "best_sup": lab, "n": len(o),
                       "change": (p / ref - 1) if (p and ref) else None,
                       "moved": bool(lab and it.get("supplier") and _key(lab) not in _key(it["supplier"])
                                     and _key(it["supplier"]) not in _key(lab)),
                       "prices": {k: v["price"] for k, v in o.items()}})
    new.sort(key=lambda x: (-x["n"], x["best"] or 0))
    gone.sort(key=lambda x: (x.get("supplier") or "", x.get("name") or ""))
    prices.sort(key=lambda x: -(abs(x["change"]) if x["change"] is not None else 0))
    return {"gone": gone, "new": new, "prices": prices, "labels": labels, "odd": odd}


def export(st, path, labels=None):
    """统计导出 Excel：三个工作表（都不做了 / 新品 / 价格）"""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    labels = labels or st["labels"]
    wb = Workbook()
    sheets = [("都不做了", ["EAN", "Cod.Art.", "Descrizione", "Marca", "Fornitore", "Prezzo Griglia"],
               [[x["ean"], x.get("code"), x.get("name"), x.get("brand"), x.get("supplier"), x.get("best") or x.get("price")]
                for x in st["gone"]]),
              ("新品", ["EAN", "Descrizione", "Reparto", "N. fornitori", "Miglior prezzo", "Fornitore"] + labels,
               [[x["ean"], x["name"], x["dept"], x["n"], x["best"], x["best_sup"]] + [x["prices"].get(l) for l in labels]
                for x in st["new"]]),
              ("价格", ["EAN", "Cod.Art.", "Descrizione", "Fornitore Griglia", "Prezzo Griglia", "Miglior prezzo",
                        "Fornitore", "Variazione %"] + labels,
               [[x["ean"], x["code"], x["name"], x["supplier"], x["ref"], x["best"], x["best_sup"],
                 round(x["change"] * 100, 1) if x["change"] is not None else None] + [x["prices"].get(l) for l in labels]
                for x in st["prices"]])]
    first = True
    for title, heads, rows in sheets:
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = title
        ws.append(heads)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append(r)
        ws.freeze_panes = "A2"
    wb.save(path)
    return path
