"""
DETERSIVO 比价生成的订单文件（门店比价文件夹\\Ordini\\Ordine_门店_供应商_日期.xlsx）被人手改了箱数
→ 找出来、同步回程序（2026-11-22 用户：在 S.CONCORDIO 的 MEGA 订单文件里加了 8 箱 AMBROSIO，程序要同步回来）。

  find(data)          这一家门店的比价结果：订单文件和程序的工作副本（distribuire）比，哪几行箱数不一样
  apply(data, edits)  把这些改动写进程序：主订单 / 工作副本 / 比价结果（state）/ 订单跟踪，之后修改分配、换方案
                      重新生成订单文件时不会再被盖掉
  file_totals(path)   订单文件现在一共几箱、多少钱（订单跟踪读回 DETERSIVO 单的箱数、金额用）

订单文件是从工作副本复制出来的（MELONI 的多两列 件数 / 金额），行的顺序一样；按条码对，没有条码的行
（MEGA CASH 有的商品没条码）按名称对。不依赖界面。
"""
import json
import os
import re

from openpyxl import load_workbook

from i18n import tr
import quote_format as QF
from supplier_layout import find_pack_col, find_order_input, parse_pack
from utils import clean_ean

LOG_NAME = "_ordini.json"      # distribuire 里：每个订单文件从哪个供应商文件来、生成时的大小 / 修改时间


def log_path(dist):
    return os.path.join(dist, LOG_NAME)


def save_log(dist, mapping):
    """export_orders 生成完订单文件后记下来：{订单文件名: {src 供应商文件, name, size, mtime}}"""
    data = {}
    for f, (name, path) in (mapping or {}).items():
        try:
            s = os.stat(path)
        except OSError:
            continue
        data[os.path.basename(path)] = {"src": f, "name": name, "size": s.st_size, "mtime": s.st_mtime}
    tmp = log_path(dist) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, log_path(dist))


def _load_log(dist):
    try:
        with open(log_path(dist), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def files_of(data):
    """这次比价的订单文件：[(供应商文件, 名称, 订单文件路径, 生成时的记录 或 None)]（这台电脑上没有的不算）"""
    import service as SV
    st = (data or {}).get("state") or {}
    dist = st.get("distribuire_path")
    if not dist:
        return []
    store_dir = os.path.dirname(os.path.abspath(dist))
    dest = os.path.join(store_dir, SV.ORDERS_DIR)
    out = []
    log = _load_log(dist)
    if log:
        for fn, x in sorted(log.items()):
            p = os.path.join(dest, fn)
            if os.path.exists(p) and x.get("src"):
                out.append((x["src"], x.get("name") or x["src"], p, x))
        return out
    try:                                            # 以前生成的（还没有记录）：照 export_orders 的规则推文件名
        import supplier_names as SN
        tag = SV._date_tag(store_dir)
        store = os.path.basename(store_dir)
        labels = st.get("labels") or {}
        for f in sorted(SV.purchased_suppliers(st.get("rows") or [], st.get("assignment") or {})):
            name = labels.get(f) or SN.label_of(f, None)
            p = os.path.join(dest, SV.order_file_name(store, name, tag))
            if os.path.exists(p):
                out.append((f, name, p, None))
    except Exception:
        pass
    return out


def _num(v):
    """格子里的箱数：空 = 0，读不出来（公式 / 文字）= None"""
    if v is None or v == "" or isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", ".")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def _desc_col(ws, header, c_ean):
    import service as SV
    for h in SV._DESC_HEADS:
        c, _r = SV.find_col(ws, h)
        if c:
            return c
    c = SV._find_col_like(ws, header, ("DESCRI",), ("REPART", "GRUPP", "OFFERT", "SOTTOGR", "CATEG"))
    return c or SV._guess_desc_col(ws, header, c_ean)


def _key(ean, desc, row):
    if ean:
        return ean
    d = " ".join(str(desc or "").upper().split())
    return "#" + d if d else f"@{row}"


def layout_of(ws, path):
    """订单文件 / 工作副本的结构：箱数写在哪一列（MELONI 订单文件 ORDINE 是 =L*E 公式 → 箱数在 L 列）"""
    c_ean, r_ean, c_ord, r_ord = QF.ean_ord(ws)
    if not c_ean or not c_ord:
        raise ValueError(tr("{0} 找不到条码列或订货列", os.path.basename(path)))
    header = max(r_ean, r_ord)
    lay = QF.layout(ws, path) or {}
    pack_col = lay.get("pack") or find_pack_col(ws, header, c_ord, lay.get("price_col") or 0)[0]
    oi = find_order_input(ws, header, c_ord, pack_col)
    return {"ean": c_ean, "ord": c_ord, "header": header, "order_input": oi, "box": oi[0] if oi else c_ord,
            "pack": pack_col, "price": lay.get("price"), "desc": _desc_col(ws, header, c_ean)}


HEAD_ROWS = 150     # 认结构只看前面这几行（复制一份到内存里认，整份文件只按行读一遍，16000 行的 MEGA CASH 几秒）


class _Cell:
    __slots__ = ("value",)

    def __init__(self, v):
        self.value = v


class _RowView:
    """报价的件价规则（QF.layout 的 price(ws, 行)）要 ws.cell(行, 列)：给它当前这一行的值"""

    def __init__(self, vals):
        self.vals = vals

    def cell(self, _i, c):
        return _Cell(self.vals[c - 1] if 0 < c <= len(self.vals) else None)


def _head_copy(ws):
    from openpyxl import Workbook
    wb = Workbook()
    h = wb.active
    h.title = ws.title
    for r, row in enumerate(ws.iter_rows(min_row=1, max_row=HEAD_ROWS, values_only=True), 1):
        for c, v in enumerate(row, 1):
            if v is not None:
                h.cell(r, c).value = v
    return h


_READ = {}


def read_boxes(path):
    """同一个文件（大小 + 修改时间一样）读过就直接用，见 _read_boxes"""
    try:
        s = os.stat(path)
        key = (os.path.normcase(os.path.abspath(path)), s.st_size, s.st_mtime_ns)
    except OSError:
        key = None
    if key and key in _READ:
        lay, items = _READ[key]
        return lay, {k: dict(v) for k, v in items.items()}
    lay, items = _read_boxes(path)
    if key:
        if len(_READ) > 40:
            _READ.clear()
        _READ[key] = (lay, {k: dict(v) for k, v in items.items()})
    return lay, items


def _read_boxes(path):
    """
    订单文件 / 工作副本里订了货的行：(结构, {键: {box 箱数, row, ean, desc, pack 每箱件数, price 件价}})；
    键 = 条码，没条码的用名称。同一个键几行的箱数加起来
    """
    wb = load_workbook(path, read_only=True)
    try:
        ws = QF.order_sheet(wb)
        title = ws.title
        lay = layout_of(_head_copy(ws), path)
        items, need = {}, []

        def get(row, c):
            return row[c - 1] if c and 0 < c <= len(row) else None
        for j, row in enumerate(ws.iter_rows(min_row=lay["header"] + 1, values_only=True), lay["header"] + 1):
            n = _num(get(row, lay["box"]))
            if n is None:                                  # 箱数格写的是公式：等下用 Excel 存下的结果
                need.append((j, row))
                continue
            if n:
                _add(items, lay, j, row, n, get)
    finally:
        wb.close()
    if need:
        wv = load_workbook(path, read_only=True, data_only=True)
        try:
            want = {j: row for j, row in need}
            wsv = wv[title] if title in wv.sheetnames else wv.active
            for j, vrow in enumerate(wsv.iter_rows(min_row=min(want), max_row=max(want), values_only=True), min(want)):
                if j in want:
                    n = _num(vrow[lay["box"] - 1] if lay["box"] <= len(vrow) else None) or 0.0
                    if n:
                        _add(items, lay, j, want[j], n, lambda row, c: row[c - 1] if c and 0 < c <= len(row) else None)
        finally:
            wv.close()
    return lay, items


def _add(items, lay, j, row, n, get):
    ean = clean_ean(get(row, lay["ean"]))
    if isinstance(ean, str) and ean.startswith("="):
        ean = ""
    desc = str(get(row, lay["desc"]) or "").strip()
    k = _key(ean, desc, j)
    if k in items:
        items[k]["box"] += n
        return
    try:
        price = lay["price"](_RowView(row), j) if lay["price"] else None
    except Exception:
        price = None
    pack = parse_pack(get(row, lay["pack"])) if lay["pack"] else 1
    items[k] = {"box": n, "row": j, "ean": ean or "", "desc": desc, "pack": pack, "price": price}


def _changed_since(path, rec, state_mtime=None):
    """订单文件生成以后有没有被存过。以前生成的（没有记录）：比比价结果（last_allocation.json）存得晚才算"""
    try:
        s = os.stat(path)
    except OSError:
        return False
    if rec:
        return s.st_size != rec.get("size") or abs(s.st_mtime - float(rec.get("mtime") or 0)) > 1
    return state_mtime is None or s.st_mtime > state_mtime + 2


def _state_mtime(st):
    try:
        import service as SV
        return os.path.getmtime(SV._state_path(st["ordine_file"]))
    except Exception:
        return None


def expected(st, f):
    """程序认为供应商 f 的订单里该有的：{键: 箱数}（主订单分给它的商品 + 订单文件里人手加的、没条码的行）"""
    import service as SV
    asg = st.get("assignment") or {}
    want = {}
    for r in st.get("rows") or []:
        if asg.get(r["row"]) == f and r.get("ean"):
            q = SV._order_qty(r, f)                  # 同一个条码几行：取最后一行，和回写一样
            try:
                q = float(q or 0)
            except (TypeError, ValueError):
                q = 0.0
            if q > 0:
                want[r["ean"]] = q
            else:
                want.pop(r["ean"], None)
    for k, v in ((st.get("manual") or {}).get(f) or {}).items():
        if v:
            want[k] = float(v)
    return want


def find(data, log=None, check_all=False, paths=None, strict=False):
    """
    订单文件和程序对不上的行：[{kind, file 供应商文件, supplier 名称, path 订单文件, key, ean, desc,
    old 程序里的箱数, new 订单文件里的箱数, pack, price, row 主订单行号 或 None}]
      kind "manual"：订单文件被人手改过（和程序的工作副本不一样）→ 同步 = 照订单文件写进程序
      kind "stale" ：工作副本里本来就有、程序这次没分给这家（以前比价留下的数量，发出去会重复订货）→ 同步 = 清掉
    生成后没存过的订单文件不读（快）；check_all = 全部都读；check_all="old" = 以前生成的（没有记录）全部读
    （那时候还会留下以前比价的数量，要找出来）
    """
    import service as SV
    log = log or (lambda *a: None)
    st = (data or {}).get("state") or {}
    dist = st.get("distribuire_path")
    rows, asg = st.get("rows") or [], st.get("assignment") or {}
    out = []
    smt = _state_mtime(st)
    want_paths = {os.path.normcase(os.path.abspath(p)) for p in paths} if paths else None
    for f, name, path, rec in files_of(data):
        if want_paths is not None and os.path.normcase(os.path.abspath(path)) not in want_paths:
            continue
        if not (check_all is True or (check_all == "old" and rec is None)) and not _changed_since(path, rec, smt):
            continue
        try:
            _l1, a = read_boxes(SV._supplier_work_path(os.path.join(dist, f)))
            _l2, b = read_boxes(path)
        except Exception as e:
            if strict:                                     # 发信前的检查：读不出来就不能当成没问题
                raise ValueError(tr("订单文件 {0} 没读出来：{1}", os.path.basename(path), e))
            log(tr("⚠ 订单文件 {0} 没读出来：{1}", os.path.basename(path), e))
            continue
        exp = expected(st, f)
        for k in sorted(set(a) | set(b) | set(exp), key=lambda k: ((b.get(k) or a.get(k) or {"row": 10 ** 9})["row"], k)):
            x, y = a.get(k), b.get(k)
            work, new, want = (x or {}).get("box", 0.0), (y or {}).get("box", 0.0), exp.get(k, 0.0)
            if abs(work - new) > 1e-9:
                kind, old = "manual", work
            elif abs(new - want) > 1e-9:
                kind, old = "stale", want
            else:
                continue
            info = y or x or {"ean": k if not k.startswith(("#", "@")) else "", "desc": _row_name(rows, k),
                               "pack": 1, "price": None, "row": None}
            e = {"kind": kind, "file": f, "supplier": name, "path": path, "key": k, "ean": info["ean"],
                 "desc": info["desc"], "old": old, "new": new, "pack": info["pack"], "price": info["price"], "row": None}
            if e["ean"]:
                hit = [r for r in rows if r.get("ean") == e["ean"] and asg.get(r["row"]) == f]
                if hit:
                    e["row"] = hit[-1]["row"]
            out.append(e)
    return out


def _row_name(rows, ean):
    for r in rows:
        if r.get("ean") == ean:
            return r.get("articolo") or ""
    return ""


def qty_for(r, f, boxes):
    """供应商 f 要订 boxes 箱 → 主订单这一行的数量（按件数订货：件数；按数据库箱规换算的：主订单箱数；不然就是箱数）"""
    pk = float(((r.get("packs") or {}).get(f)) or 1)
    if r.get("pieces") is not None:
        return boxes * pk
    if r.get("base_pack"):
        return boxes * pk / float(r["base_pack"])
    return boxes


def _locked(path):
    d, fn = os.path.split(path)
    return os.path.exists(os.path.join(d, "~$" + fn[2:])) or os.path.exists(os.path.join(d, "~$" + fn))


def plan(data, edits, store=None):
    """
    人手改的 → (qty {主订单行号: 新数量}, new_rows 主订单要新加的行, manual {供应商文件: {键: 箱数}} 主订单放不进去的
    （没条码 / 没价格），照订单文件记着、以后回写照样写）。「以前留下的」不用做什么：回写时按程序的分配清掉
    """
    import service as SV
    st = data["state"]
    rows = st.get("rows") or []
    by_row = {r["row"]: r for r in rows}
    heads = list((rows[0].get("prices") or {}).keys()) if rows else []
    nxt = max([r["row"] for r in rows] + [1]) + 1
    manual = {f: dict(v) for f, v in (st.get("manual") or {}).items()}
    qty, new_rows = {}, []
    for e in edits:
        if e.get("kind", "manual") != "manual":
            continue
        f, n = e["file"], float(e["new"] or 0)
        if e.get("row") is not None and e["row"] in by_row:
            qty[e["row"]] = qty_for(by_row[e["row"]], f, n)
        elif e["ean"] and n > 0 and e.get("price") and f in heads:      # 主订单里没有（或分给了别家）：新加一行给这家
            item = {"ean": e["ean"], "price": e["price"], "pack": e.get("pack") or 1, "name": e["desc"]}
            new_rows.append(SV.make_added_row(nxt, item, n * float(e.get("pack") or 1), f, heads, store))
            nxt += 1
        elif n > 0:
            manual.setdefault(f, {})[e["key"]] = n
        else:
            (manual.get(f) or {}).pop(e["key"], None)
    return qty, new_rows, {f: v for f, v in manual.items() if v}


def apply(data, edits, log, progress=None, store=None):
    """
    订单文件里人手改的写进程序（主订单、工作副本、比价结果、订单跟踪），以前留下的数量清掉；订单文件按程序重新生成。
    返回 (新的 summary, 改了几行)。订单文件在 Excel 里开着就不做
    """
    import copy as _copy
    import service as SV
    if not edits:
        return data, 0
    opened = sorted({os.path.basename(e["path"]) for e in edits if _locked(e["path"])})
    if opened:
        raise ValueError(tr("这几个订单文件在 Excel / WPS 里开着，先关掉再同步：{0}", "、".join(opened)))
    data = _copy.deepcopy(data)
    st = data["state"]
    qty, new_rows, manual = plan(data, edits, store)
    log("\n" + "=" * 50)
    log(tr("[同步订单文件] {0} 行箱数和程序不一样", len(edits)))
    for e in edits:
        how = tr("人手改") if e.get("kind", "manual") == "manual" else tr("以前留下的，清掉")
        log(tr("  {0} · {1}：{2:g} → {3:g} 箱（{4}）", e["supplier"], e["desc"] or e["ean"], e["old"], e["new"], how))
    st["manual"] = manual
    sources = dict(st.get("sources") or {})
    for r in new_rows:
        sources[r["row"]] = "订单文件"
    out, _n = SV.rewrite_assignment(data, dict(st["assignment"]), sources, log, progress, force=True,
                                    qty=qty, new_rows=new_rows)
    return out, len(edits)


def send_check(paths, log=None):
    """
    发订单邮件之前最后一道检查（2026-11-22：发出去的单里多了以前比价留下的 93 行、€2,889）：
    附件是 DETERSIVO 比价结果的订单文件（…\\门店\\Ordini\\…）→ 每一行都要和程序这次的分配对得上。
    返回 [对不上的行]（kind stale = 程序没分给这家的数量；manual = 人手改的）；不是比价订单文件的附件不管
    """
    import service as SV
    by_store = {}
    for p in paths:
        d = os.path.dirname(os.path.abspath(p))
        if os.path.basename(d) == SV.ORDERS_DIR:
            by_store.setdefault(os.path.dirname(d), []).append(p)
    out = []
    for store_dir, ps in by_store.items():
        data = SV.load_state(store_dir) or _raw_state(store_dir)
        if not data or not data.get("state"):                # 比价结果的订单文件却找不到比价记录：不能当成没问题
            raise ValueError(tr("{0} 找不到这次比价的记录（_备份\\last_allocation.json），没法核对订单文件",
                                os.path.basename(store_dir)))
        st = data["state"]
        st["distribuire_path"] = os.path.join(store_dir, re.split(r"[\\/]", (st.get("distribuire_path") or "distribuire").rstrip("\\/"))[-1])
        out += find(data, log, check_all=True, paths=ps, strict=True)
    return out


def _raw_state(store_dir):
    """直接读 _备份\\last_allocation.json（主订单路径对不上时 load_state 不给，发信前核对只要分配）"""
    try:
        with open(os.path.join(store_dir, "_备份", "last_allocation.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        st = data["state"]
        for k in ("assignment", "baseline", "sources"):
            st[k] = {int(r): v for r, v in (st.get(k) or {}).items()}
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---- 订单跟踪读回 DETERSIVO 单的箱数、金额 ----
_TOTALS = {}


def _totals_cache_path():
    from settings import app_dir
    return os.path.join(app_dir(), "cache", "ordini_totals.json")


def _load_totals():
    if _TOTALS:
        return _TOTALS
    try:
        with open(_totals_cache_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict):
            _TOTALS.update(d)
    except (OSError, ValueError):
        pass
    return _TOTALS


def file_totals(path):
    """订单文件现在一共几箱、多少钱（箱 × 每箱件数 × 件价；有一行没有价格 → 金额 None）。按文件大小 + 修改时间缓存"""
    s = os.stat(path)
    key = f"{os.path.normcase(os.path.abspath(path))}|{s.st_size}|{s.st_mtime_ns}"
    cache = _load_totals()
    if key in cache:
        c, a = cache[key]
        return c, a
    _lay, items = read_boxes(path)
    colli = round(sum(x["box"] for x in items.values()), 3)
    ok = all(x["price"] for x in items.values())
    amt = round(sum(x["box"] * (x["pack"] or 1) * x["price"] for x in items.values()), 2) if ok else None
    p = os.path.normcase(os.path.abspath(path)) + "|"
    for k in [k for k in cache if k.startswith(p)]:     # 同一个文件旧的记录不要了
        del cache[k]
    cache[key] = [colli, amt]
    try:
        cp = _totals_cache_path()
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        with open(cp + ".tmp", "w", encoding="utf-8", newline="") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(cp + ".tmp", cp)
    except OSError:
        pass
    return colli, amt
