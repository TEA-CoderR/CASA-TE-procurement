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
CACHE_VER = 6
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
DEPT_SHARE = 0.05           # 报价的部门里 Griglia 商品占 ≥ 5% 才算「我们这几类」
FRESH_DAYS = 30             # 「新上架」：这家报价里第一次出现的条码，30 天内都算


def _shifted(g):
    """列错位：本来写文字的供应商 / 品牌 / 大类格子里是数字"""
    return any(isinstance(g(f), (int, float)) and not isinstance(g(f), bool) for f in ("supplier", "brand", "cat1"))


def bad_rows_text(rows):
    """[2759, 2760, …, 2775] → 「2759–2775」（连着的合成一段）"""
    out, start, prev = [], None, None
    for n in sorted(rows):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}–{prev}" if prev > start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}–{prev}" if prev > start else str(start))
    return "、".join(out)


def _cats(r, col, right):
    """
    大类 / 中类 / 小类：右边那套有大类就用右边的；右边空着的中类 / 小类，左边大类一样才拿左边的补（不混两个大类）；
    右边整行都空 → 用左边的
    """
    def v(i):
        return str(r[i] if i is not None and i < len(r) and r[i] is not None else "").strip()
    left = [v(col.get(f)) for f in ("cat1", "cat2", "cat3")]
    rt = [v(right.get(f)) for f in ("cat1", "cat2", "cat3")]
    if not rt[0]:
        return left if not any(rt) else [left[0]] + [rt[k] or (left[k] if left[0] else "") for k in (1, 2)]
    same = left[0].upper() == rt[0].upper()
    return [rt[0]] + [rt[k] or (left[k] if same else "") for k in (1, 2)]


def parse_rows(rows, struck=()):
    """
    struck = 划了线的 Excel 行号（用户 11-11：Griglia 里不做了的商品是挪到最下面、用线划掉的）：
    这些行不算 Griglia 商品（不进 items，不会再出现在「都不做了」），放进 out["struck"]。

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
        f = _FIELDS.get(h) or ("direct" if h.startswith("FORNITOR") and "DIRETT" in h else None)
        if f and f not in col:
            col[f] = i
    # 第二套分类（2026-11-07 用户定：SCHEDA_DETERSIVI 最右边 CAT.1 / CAT.2 / CAT.3 比左边 CAT.1 / CAT 2 / CAT 3 全、准，优先用它）
    right = {}
    for f, h in (("cat1", "CAT.1"), ("cat2", "CAT.2"), ("cat3", "CAT.3")):
        idx = [i for i, x in enumerate(heads) if x == h and i != col.get(f)]
        if idx:
            right[f] = idx[-1]
    sup_cols = {h: i for i, h in enumerate(heads) if h in _SUP_COLS}
    store_cols = {h: i for i, h in enumerate(heads) if h in STORES}
    items, by_code, bad, struck_items = {}, {}, [], {}
    struck = set(struck or ())
    for n, r in enumerate(rows[hr + 1:], hr + 2):
        def g(f):
            i = col.get(f)
            return r[i] if i is not None and i < len(r) else None
        e = clean_ean(g("ean"))
        if not e:
            continue
        if _shifted(g):
            # 这一行在 Excel 里列错位了（2026-11-07：SCHEDA_DETERSIVI 最下面 17 行从 Marca 开始往左挪了 9 格，
            # 「大类」里是 IVA 22、「Fornitore」里是小数）：只留错位前面那几列（编号 / 名称 / 箱规 / 价格），其余当空
            bad.append(n)
            r = list(r[:col.get("brand", len(r))]) if col.get("brand") is not None else list(r)
        code = str(g("code") or "").strip()
        if code.endswith(".0"):
            code = code[:-2]
        it = {"ean": e, "code": code, "name": str(g("name") or "").strip(), "brand": str(g("brand") or "").strip(),
              "supplier": str(g("supplier") or "").strip(), "price": _num(g("price")), "best": _num(g("best")),
              "pack": _num(g("pack")), "cats": _cats(r, col, right),
              "direct": str(g("direct") or "").strip(),
              "prices": {h: _num(r[i]) for h, i in sup_cols.items() if i < len(r) and _num(r[i])},
              "stores": [h for h, i in store_cols.items() if i < len(r) and _num(r[i])], "row": n}
        if n in struck:
            struck_items[e] = it
            continue
        items[e] = it
        if code:
            by_code[code] = e
    # 列在哪（0 开始）、表头第几行（Excel 行号）：往 Griglia 里写替代商品时用（griglia_replace.write_rows）
    return {"items": items, "suppliers": list(sup_cols), "by_code": by_code, "bad_rows": bad,
            "cols": col, "sup_cols": sup_cols, "header_row": hr + 1,
            "struck": {e: it for e, it in struck_items.items() if e not in items}}


def struck_rows(ws, head):
    """划了线的行（条码格或名称格是删除线）：Excel 行号集合。head = 前 10 行的值"""
    hr = next((i for i, r in enumerate(head) if any(_norm(v) == "CODICE EAN" for v in r)), None)
    if hr is None:
        return set()
    heads = [_norm(v) for v in head[hr]]
    cols = sorted({i for i, h in enumerate(heads) if h == "CODICE EAN" or _FIELDS.get(h) == "name"})
    if not cols:
        return set()
    out = set()
    try:
        for n, row in enumerate(ws.iter_rows(min_row=hr + 2, min_col=cols[0] + 1, max_col=cols[-1] + 1), hr + 2):
            for c in cols:
                cell = row[c - cols[0]] if c - cols[0] < len(row) else None
                f = getattr(cell, "font", None) if cell is not None else None
                if f is not None and f.strike and getattr(cell, "value", None) not in (None, ""):
                    out.add(n)
                    break
    except Exception:
        return set()                                     # 读不了格式（只读模式不支持等）：当没有划线
    return out


def parse_rows_any(wb):
    """
    找 Griglia 在哪个工作表（2026-11-07：用户换成 SCHEDA_DETERSIVI，第一页是供应商资料，GRIGLIA 在第三页 →
    以前只读第一页，提示找不到 Codice EAN）：先找名字是 GRIGLIA 的页，再按顺序找表头里有 Codice EAN 的页
    """
    sheets = sorted(wb.worksheets, key=lambda ws: 0 if _norm(ws.title) == "GRIGLIA" else 1)
    for ws in sheets:
        head = list(ws.iter_rows(max_row=10, values_only=True))      # 先只看前 10 行，不是这页就不整页读
        if any(_norm(v) == "CODICE EAN" for r in head for v in r):
            out = parse_rows(ws.iter_rows(values_only=True), struck=struck_rows(ws, head))
            out["sheet"] = ws.title
            return out
    raise ValueError("Griglia 里找不到「Codice EAN」表头（所有工作表都看过了；表头要在前 10 行）")


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
        out = parse_rows_any(wb)
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


def sync_from_source(root, source):
    """
    Griglia 的原件在同步文件夹（「换 Griglia…」时记下的 source）：原件比「0 Griglia」那份新（同事改过）就重新复制进来，
    旧的挪进 _旧文件。返回复制进来的路径；原件不在 / 没变返回 None
    """
    import shutil
    if not source or not os.path.isfile(source):
        return None
    d = grid_dir(root)
    os.makedirs(d, exist_ok=True)
    if os.path.normcase(os.path.dirname(os.path.abspath(source))) == os.path.normcase(os.path.abspath(d)):
        return None
    cur = find_file(root)
    dst = os.path.join(d, os.path.basename(source))
    if cur and os.path.normcase(cur) == os.path.normcase(dst) and \
            os.path.getsize(cur) == os.path.getsize(source) and os.path.getmtime(source) <= os.path.getmtime(cur) + 2:
        return None
    import file_inbox as FI
    for f in os.listdir(d):
        fp = os.path.join(d, f)
        if os.path.isfile(fp) and f.lower().endswith(EXCEL) and not f.startswith(("~", "$")) and \
                os.path.normcase(fp) != os.path.normcase(dst):
            try:
                FI.archive(fp, os.path.join(d, "_旧文件"))
            except OSError:
                pass
    tmp = dst + ".tmp"
    shutil.copy2(source, tmp)
    os.replace(tmp, dst)
    return dst


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
    报价里有部门的：哪些部门算「我们这几类」（Griglia 的商品在这个部门 ≥ 10 个、占 ≥ DEPT_SHARE）。
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
            out[label] = {d for d in tot if hit.get(d, 0) >= 10 and hit[d] / tot[d] >= DEPT_SHARE}
    return out


def track(seen, quotes, today):
    """
    记下每家报价里出现过的条码，找出「新上架」的（这家报价以前没有、最近 FRESH_DAYS 天第一次出现）。
    seen = {批发商: {"since": 第一次记录的日期, "eans": {条码: [第一次, 最后一次]}}}（日期是 YYYY-MM-DD 文字）；
    一家批发商第一次出现时它的条码全部当成原有的（since = 今天），不算新上架。
    返回 (更新后的 seen, {批发商: 新上架的条码集合})；180 天没再出现的条码删掉
    """
    from datetime import date, timedelta
    t = date.fromisoformat(today)
    lim = (t - timedelta(days=FRESH_DAYS)).isoformat()
    old = (t - timedelta(days=180)).isoformat()
    seen = dict(seen or {})
    fresh = {}
    for lab, qs in quotes.items():
        rec = seen.get(lab)
        if not rec:
            rec = {"since": today, "eans": {}}
        eans = {e: v for e, v in rec["eans"].items() if v[1] >= old}
        for e in qs:
            if e in eans:
                eans[e] = [eans[e][0], max(eans[e][1], today)]
            else:
                eans[e] = [today, today]
        rec = {"since": rec["since"], "eans": eans}
        seen[lab] = rec
        fresh[lab] = {e for e in qs if eans[e][0] > rec["since"] and eans[e][0] >= lim}
    return seen, fresh


def load_seen(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_seen(path, seen):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(seen, f)
        os.replace(path + ".tmp", path)
    except Exception:
        pass


def category_mapper(items, offer):
    """
    新品（Griglia 里没有）的类别也用 Griglia 的叫法（CAT.1 大类 / CAT 2 中类 / CAT 3 小类）：
    看 Griglia 里的商品在报价里属于哪个组（MELONI 的 Descr.Gruppo + Sottogruppo、或部门），
    同一个组的 Griglia 商品最常见的是哪一类，新品就算那一类。
    依次用 组 + 小组 → 三级都有；只有组 → 大类 + 中类；只有部门 → 大类。都对不上留空
    """
    from collections import Counter
    by_sub, by_grp, by_dept = {}, {}, {}
    for e, it in items.items():
        c = it.get("cats") or ["", "", ""]
        if not c[0] or e not in offer:
            continue
        for q in offer[e].values():
            if q.get("grp") and q.get("sgrp"):
                by_sub.setdefault((q["grp"], q["sgrp"]), Counter())[tuple(c)] += 1
            if q.get("grp"):
                by_grp.setdefault(q["grp"], Counter())[(c[0], c[1])] += 1
            if q.get("dept"):
                by_dept.setdefault(q["dept"], Counter())[c[0]] += 1
    MIN = 2                                       # 至少 2 个 Griglia 商品对上才算数

    def top(cnt):
        if not cnt:
            return None
        k, n = cnt.most_common(1)[0]
        return k if n >= MIN else None

    def cat_of(o):
        for q in o.values():
            k = q.get("grp") and q.get("sgrp") and top(by_sub.get((q["grp"], q["sgrp"])))
            if k:
                return list(k)
        for q in o.values():
            k = q.get("grp") and top(by_grp.get(q["grp"]))
            if k:
                return [k[0], k[1], ""]
        for q in o.values():
            k = q.get("dept") and top(by_dept.get(q["dept"]))
            if k:
                return [k, "", ""]
        return ["", "", ""]
    return cat_of


def stats(grid, quotes, fresh=None):
    """
    Griglia 和这周报价对比。quotes = {批发商名: {条码: {"price", "pack", "desc", "dept"?}}}
    fresh = track() 给的 {批发商: 新上架的条码}（不给 = 没有记录）
    返回 {"gone": [...], "new": [...], "prices": [...], "labels": [批发商名], "odd": [...], "history": 有没有记录}
    新品每行多一个标记：fresh = 在做的几家都是刚上架（别家早就有的不算新）
    """
    items = grid["items"]
    for it in items.values():                    # 旧缓存兼容
        it.setdefault("cats", ["", "", ""])
        it.setdefault("direct", "")
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

    cat_of = category_mapper(items, offer)

    def direct(e, o):
        """源头供应商：Griglia 里写了用 Griglia 的，没写用报价里的（MEGA CASH 的 ragione_sociale）"""
        d = (items.get(e) or {}).get("direct")
        return d or next((q["src"] for q in (o or {}).values() if q.get("src")), "")

    gone = [dict(it, direct=direct(e, None)) for e, it in items.items() if e not in offer]
    full = full_names(items, labels)
    for x in gone:                               # 用户 11-20：停售的也要知道以前是哪家批发商的（分表导出）
        x["wholesaler"], x["sup_guess"] = gone_wholesaler(x, labels)
        if x["sup_guess"]:                       # 写成 Griglia 里同一家的写法（筛选里不多出一项）
            x["supplier_fill"] = full.get(x["wholesaler"]) or x["wholesaler"]
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
                    "cats": cat_of(o), "direct": direct(e, o),
                    "fresh": bool(fresh) and all(e in fresh.get(k, ()) for k in o),
                    "pack": o[lab].get("pack") if lab else None, "prices": {k: v["price"] for k, v in o.items()}})
    prices = []
    for e, it in items.items():
        o = offer.get(e)
        if not o:
            continue
        p, lab = best(o)
        ref = it.get("best") or it.get("price")
        prices.append({"ean": e, "code": it.get("code"), "name": it.get("name"), "brand": it.get("brand"),
                       "cats": it["cats"], "direct": direct(e, o),
                       "supplier": it.get("supplier"), "ref": ref, "best": p, "best_sup": lab, "n": len(o),
                       "change": (p / ref - 1) if (p and ref) else None,
                       "moved": bool(lab and it.get("supplier") and _key(lab) not in _key(it["supplier"])
                                     and _key(it["supplier"]) not in _key(lab)),
                       "prices": {k: v["price"] for k, v in o.items()}})
    new.sort(key=lambda x: (-x["n"], x["best"] or 0))
    gone.sort(key=lambda x: (x.get("supplier") or "", x.get("name") or ""))
    prices.sort(key=lambda x: -(abs(x["change"]) if x["change"] is not None else 0))
    return {"gone": gone, "new": new, "prices": prices, "labels": labels, "odd": odd,
            "history": fresh is not None}



def overview(st, n_grid):
    """
    Griglia 页顶上的总览（2026-11-02 用户：Griglia 统计也做得好看点）。
    返回 {"grid": Griglia 商品数, "covered": 这周至少一家批发商有的, "share": covered / grid,
          "sups": [{"label", "carried": 有几个 Griglia 商品, "cheapest": 其中几个它最便宜, "share": carried / grid}]}
    sups 按最便宜的个数从多到少（一样多按有几个），批发商一个都没有的也列出来（0）
    """
    n_grid = max(0, int(n_grid or 0))
    covered = max(0, n_grid - len(st.get("gone") or ()))
    carried = {l: 0 for l in st.get("labels") or ()}
    cheapest = dict.fromkeys(carried, 0)
    for x in st.get("prices") or ():
        for l in (x.get("prices") or {}):
            carried[l] = carried.get(l, 0) + 1
            cheapest.setdefault(l, 0)
        b = x.get("best_sup")
        if b:
            cheapest[b] = cheapest.get(b, 0) + 1
            carried.setdefault(b, 0)
    sups = [{"label": l, "carried": carried[l], "cheapest": cheapest.get(l, 0),
             "share": carried[l] / n_grid if n_grid else 0.0} for l in carried]
    sups.sort(key=lambda d: (-d["cheapest"], -d["carried"], d["label"]))
    return {"grid": n_grid, "covered": covered, "share": covered / n_grid if n_grid else None, "sups": sups}


def _words_of(s):
    return set(re.findall(r"[A-Z0-9]{3,}", str(s or "").upper()))


def match_label(name, labels):
    """Griglia 里写的供应商 / 价格列名（F.LLI PETRILLO SRL、MEGA CASH & CARRY SRL）→ 这周报价的批发商名（PETRILLO、MEGA CASH）"""
    ws = _words_of(name)
    if not ws:
        return None
    best = None
    for l in labels:
        lw = _words_of(l)
        if lw and lw <= ws and (best is None or len(lw) > len(_words_of(best))):
            best = l
    if best is None:                              # 反过来：报价名比 Griglia 写得长（PETRILLO SRL ↔ F.LLI PETRILLO）
        for l in labels:
            if ws & _words_of(l) and (_words_of(l) - ws) <= {"SRL", "SPA", "SNC", "CARRY", "CASH"}:
                return l
    return best


def full_names(items, labels):
    """报价批发商名 → Griglia 里这家最常见的写法（PETRILLO → F.LLI PETRILLO SRL）"""
    from collections import Counter
    cnt = {}
    for it in items.values():
        sup = it.get("supplier")
        lab = match_label(sup, labels) if sup else None
        if lab:
            cnt.setdefault(lab, Counter())[sup] += 1
    return {l: c.most_common(1)[0][0] for l, c in cnt.items()}


def gone_wholesaler(x, labels):
    """
    停售商品以前是哪家批发商的 → (报价批发商名 或 None, 是不是推出来的)。
    先看 Griglia 的 Fornitore；那一格空着（用户 11-20：LADY VENEZIA / PASTA DEL CAPITANO 没写）就用
    Griglia 里几家批发商价格列最便宜的那家
    """
    lab = match_label(x.get("supplier"), labels)
    if lab:
        return lab, False
    pr = {k: v for k, v in (x.get("prices") or {}).items() if v}
    for k in sorted(pr, key=lambda k: pr[k]):
        lab = match_label(k, labels)
        if lab:
            return lab, not x.get("supplier")
    return None, False


# 导出的四张表（2026-11-07 用户：可以只导出其中几张；带照片、要美观专业，参考门店报告）
EXPORT_TABLES = (("gone", "停售"), ("new", "新品"), ("prices", "价格"), ("odd", "价格可疑"))
# 导出文件里一律意大利文（用户 11-20）：工作表名 / 文件名
SHEETS = {"gone": "Fuori listino", "new": "Nuovi articoli", "prices": "Prezzi", "odd": "Prezzi sospetti"}


def export(st, path, labels=None, which=None, rows=None, url=None, grid_name=None, when=None):
    """
    统计导出 Excel（xlsx_report 统一样式）。which = 要哪几张表（EXPORT_TABLES 的键，默认全部）；
    rows = {键: [要导出的行]}（页面传「筛选后看得到的、按现在的排序」，不给 = st 里的全部）；
    每张表第一列是商品照片（=IMAGE，Excel 365 显示）。最便宜的批发商价格绿底、价格涨红跌绿、换了供应商的橙色
    """
    from datetime import datetime
    import xlsx_report as X
    from xlsx_report import Col
    labels = list(labels or st["labels"])
    which = [k for k, _ in EXPORT_TABLES if k in (which or [k for k, _ in EXPORT_TABLES])]
    rows = rows or {}
    when = when or datetime.now()
    src = " · ".join(labels)
    wb = X.new_book()

    def sub(n):
        return " · ".join(x for x in ("CASA&TE", f"{when:%d/%m/%Y}", f"{n:,} articoli",
                                      f"Griglia: {grid_name}" if grid_name else "", f"Listini: {src}" if src else "") if x)

    def cats(x):
        c = list(x.get("cats") or ["", "", ""]) + ["", "", ""]
        return [c[0] or None, c[1] or None, c[2] or None, x.get("direct") or None]
    CATS = [Col("CAT.1", 16, group="cat"), Col("CAT 2", 16, group="cat"), Col("CAT 3", 16, group="cat"),
            Col("Fornitore diretto", 22, group="cat")]
    SUPS = [Col(l, 12, "price", group="sup") for l in labels]

    def sup_vals(x, marks, k, start):
        """几家批发商的价格；最便宜那家绿底"""
        out = []
        for j, l in enumerate(labels):
            out.append((x.get("prices") or {}).get(l))
            if x.get("best_sup") == l and out[-1] is not None:
                marks[(k, start + j)] = "GREEN"
        return out

    for key in which:
        xs = rows.get(key)
        xs = list(st.get(key) or []) if xs is None else list(xs)
        marks = {}
        if key == "gone":
            cols = [Col("EAN", 16, "ean"), Col("Cod. Art.", 10, "ean"), Col("Descrizione", 40, bold=True),
                    Col("Marca", 16)] + CATS + [Col("Fornitore Griglia", 22, group="sup"),
                                                Col("Prezzo Griglia", 12, "price", group="price")]

            def gone_sheet(name, title, part):
                data = []
                for x in part:
                    # Griglia 没写供应商：按 Griglia 里最便宜的批发商价格推，写法和别的行一样、不标颜色（用户 11-20）
                    sup = x.get("supplier") or x.get("supplier_fill") or x.get("wholesaler") or None
                    data.append([x["ean"], x.get("code") or None, x.get("name"), x.get("brand") or None] + cats(x) +
                                [sup, x.get("best") or x.get("price")])
                X.add_sheet(wb, name, title, sub(len(part)), cols, data,
                            photo_eans=[x["ean"] for x in part], url=url, tone="RED",
                            note=f"Articoli presenti in Griglia ma assenti da tutti i listini di questa settimana ({src}).")
            gone_sheet(SHEETS["gone"], "Griglia · Articoli non più trattati dai grossisti", xs)
            for l in labels:                         # 用户 11-20：每家批发商一张子表
                part = [x for x in xs if (x.get("wholesaler") or gone_wholesaler(x, labels)[0]) == l]
                gone_sheet(f"{SHEETS['gone']} · {l}", f"Griglia · Non più trattati · {l}", part)
        elif key == "new":
            cols = [Col("EAN", 16, "ean"), Col("Descrizione", 40, bold=True)] + CATS + \
                [Col("Reparto", 18), Col("N. grossisti", 10, "num"), Col("Miglior prezzo", 12, "price", group="price"),
                 Col("Più economico", 16, group="price"), Col("Nuovo", 8, left=False)] + SUPS
            data = []
            for k, x in enumerate(xs):
                data.append([x["ean"], x.get("name")] + cats(x) +
                            [x.get("dept") or None, x.get("n"), x.get("best"), x.get("best_sup"),
                             "Sì" if x.get("fresh") else None] + sup_vals(x, marks, k, 11))
                if x.get("fresh"):
                    marks[(k, 10)] = "BLUE"
                marks[(k, 8)] = "GREEN" if x.get("best") is not None else None
            marks = {kk: v for kk, v in marks.items() if v}
            X.add_sheet(wb, SHEETS["new"], "Griglia · Nuovi articoli dai grossisti", sub(len(xs)), cols, data,
                        marks=marks, photo_eans=[x["ean"] for x in xs], url=url, tone="BLUE",
                        note="Articoli presenti nei listini ma non in Griglia (solo le nostre categorie). "
                             "Nuovo = appena inseriti da tutti i grossisti che lo trattano; verde = prezzo più basso.")
        elif key == "prices":
            cols = [Col("EAN", 16, "ean"), Col("Cod. Art.", 10, "ean"), Col("Descrizione", 40, bold=True)] + CATS + \
                [Col("Fornitore Griglia", 20, group="sup"), Col("Prezzo Griglia", 12, "price", group="price"),
                 Col("Miglior prezzo", 12, "price", group="price"), Col("Più economico", 16, group="price"),
                 Col("Variazione", 11, "pct", group="warn")] + SUPS
            data = []
            for k, x in enumerate(xs):
                ch = x.get("change")
                data.append([x["ean"], x.get("code") or None, x.get("name")] + cats(x) +
                            [x.get("supplier") or None, x.get("ref"), x.get("best"), x.get("best_sup"),
                             round(ch * 100, 1) if ch is not None else None] + sup_vals(x, marks, k, 12))
                if x.get("moved"):
                    marks[(k, 10)] = "AMBER"                   # 最便宜的不是 Griglia 登记的供应商
                if ch is not None and abs(ch) >= 0.05:
                    marks[(k, 11)] = "RED" if ch > 0 else "GREEN"
            X.add_sheet(wb, SHEETS["prices"], "Griglia · Confronto prezzi", sub(len(xs)), cols, data, marks=marks,
                        photo_eans=[x["ean"] for x in xs], url=url, tone="GREY",
                        note="Miglior prezzo attuale confrontato con il prezzo in Griglia (PA MIGLIORE). Variazione: "
                             "rosso = più caro ≥ 5%, verde = più economico ≥ 5%; arancione = il più economico non è il "
                             "fornitore della Griglia; nei prezzi dei grossisti verde = il più basso.")
        elif key == "odd":
            cols = [Col("EAN", 16, "ean"), Col("Descrizione", 40, bold=True), Col("Grossista", 18, group="sup"),
                    Col("Prezzo listino", 12, "price", group="warn"), Col("Prezzo medio altri", 14, "price", group="price")]
            data = [[x["ean"], x.get("name"), x.get("supplier"), x.get("price"), x.get("others")] for x in xs]
            marks = {(k, 3): "RED" for k in range(len(xs))}
            X.add_sheet(wb, SHEETS["odd"], "Griglia · Prezzi sospetti nei listini", sub(len(xs)), cols, data, marks=marks,
                        photo_eans=[x["ean"] for x in xs], url=url, tone="AMBER",
                        note=f"Prezzo sotto €{MIN_PRICE:.2f} o inferiore al {ODD_RATIO:.0%} del prezzo medio degli altri "
                             "grossisti: probabilmente errato nel listino, escluso dal confronto.")
    if not wb.sheetnames:
        raise ValueError("没有选要导出的表")
    wb.save(path)
    return path
