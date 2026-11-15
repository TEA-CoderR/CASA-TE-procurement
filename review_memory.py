# -*- coding: utf-8 -*-
"""
审核记录：检查门店订货清单时人定下来的确定箱数记下来，清单改了 / 程序更新后重新计算，审核过的不用再审一遍。
（2026-11-15 用户：同事审完了这周的清单，发现有新加的商品程序没读到，改程序重新计算后又得从头审核一遍。）

不依赖界面。文件：工作文件夹\\1 门店清单\\_审核记录\\<门店>.json（每台电脑审核的人自己那份工作文件夹）
  {"store": 1, "updated": 时间, "by": 谁, "confirmed": 点「确认，开始比价」的时间 / null,
   "items": {商品: {"lbox": 当时清单箱数, "fbox": 定下的箱数, "ok": 确认过, "name"}}}
  商品 = 清单里的条码（换成替代商品的用原条码），没有条码用 "#商品编号"。

重新计算后（apply）：
  - 记过、清单箱数没变 → 用记下的箱数（review = "kept"），不用再审
  - 记过、清单箱数改了 → 用系统建议，标「清单改过」（review = "changed"）要审
  - 没记过、上次已经确认过整张清单 → 标「新加」（review = "new"）要审
  - 没记过、上次还没确认（审到一半）→ 照常（还没审到）
记录超过 KEEP_DAYS 天不用（下一周的新清单不能沿用这一周的审核）。

以前的版本没有审核记录：用上次「确认，开始比价」生成的主订单（确认订单_门店_时间.xlsx）补一份（from_main_order）。
"""
import glob
import json
import os
import re
from datetime import datetime, timedelta

KEEP_DAYS = 5
DIR = "_审核记录"


def _stores():
    import reorder_service as R
    return R.STORES


def key_of(x):
    """一行清单商品的标识：清单里的条码（换了替代商品的用原条码）> #商品编号"""
    rp = x.get("repl") or {}
    e = str(rp.get("old_ean") or x.get("ean") or "").strip()
    if e:
        return e
    c = str(rp.get("old_code") or x.get("code") or "").strip()
    return "#" + c if c else ""


def _keys(x):
    """找记录时依次试：清单条码、现在的条码（替代商品）、#编号"""
    out = [key_of(x)]
    for k in (str(x.get("ean") or "").strip(), "#" + str(x.get("code") or "").strip()):
        if k and k != "#" and k not in out:
            out.append(k)
    return [k for k in out if k]


def path_for(root, store):
    import workspace as W
    return os.path.join(W.lists_dir(root), DIR, f"{_stores().get(store, store)}.json")


def load(root, store, now=None):
    """这家店的审核记录；没有 / 读不了 / 太旧（超过 KEEP_DAYS 天）→ None"""
    if not root:
        return None
    try:
        with open(path_for(root, store), encoding="utf-8") as f:
            mem = json.load(f)
    except (OSError, ValueError):
        return None
    if not fresh(mem, now):
        return None
    return mem


def fresh(mem, now=None):
    try:
        t = datetime.fromisoformat(mem.get("updated") or "")
    except (TypeError, ValueError):
        return False
    return (now or datetime.now()) - t <= timedelta(days=KEEP_DAYS)


def _box(v):
    try:
        return int(round(float(v or 0)))
    except (TypeError, ValueError):
        return 0


def build(rows, store, prev=None, confirmed=False, edited=None, who=""):
    """
    这家店现在的审核记录（rows = 检查窗口里这家店的行）：
      confirmed=True（点了「确认，开始比价」）：每一行都记，都算确认过
      平时：只记手动改过的行（edited(x)）和沿用上次审核的行（review = kept，保留上次确认过没有）
    """
    items = {}
    for x in rows:
        k = key_of(x)
        if not k or x.get("fbox") is None:
            continue
        kept = x.get("review") == "kept"
        if not (confirmed or kept or (edited and edited(x))):
            continue
        ok = True if confirmed else bool(kept and x.get("review_ok"))
        items[k] = {"lbox": _box(x.get("lbox")), "fbox": _box(x.get("fbox")), "ok": ok,
                    "name": str(x.get("name") or "")[:60]}
    now = datetime.now().isoformat(timespec="seconds")
    return {"store": store, "updated": now, "by": who,
            "confirmed": now if confirmed else (prev or {}).get("confirmed"),
            "source": "app", "items": items}


def save(root, store, mem):
    """写文件（先写临时文件再换，写一半断电不会坏）；没有工作文件夹不写"""
    if not root or mem is None:
        return None
    p = path_for(root, store)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(mem, f, ensure_ascii=False, indent=0)
    os.replace(tmp, p)
    return p


def clear(root, store):
    try:
        os.remove(path_for(root, store))
    except OSError:
        pass


def apply(rows, mem, set_box):
    """
    重新计算后的行套用审核记录（只改这家店的行）。set_box(x, 箱数) = 改确定箱数（同步件数）。
    返回 {"kept": n, "changed": n, "new": n}
    """
    out = {"kept": 0, "changed": 0, "new": 0}
    for x in rows:
        x.pop("review", None)
        x.pop("review_prev", None)
    if not mem:
        return out
    items = mem.get("items") or {}
    approved = bool(mem.get("confirmed"))
    for x in rows:
        it = next((items[k] for k in _keys(x) if k in items), None)
        if it is not None:
            if _box(it.get("lbox")) == _box(x.get("lbox")):
                set_box(x, _box(it.get("fbox")))
                x["review"] = "kept"
                x["review_ok"] = bool(it.get("ok"))
            else:
                x["review"] = "changed"
                x["review_prev"] = _box(it.get("lbox"))
        elif approved:
            x["review"] = "new"
        if x.get("review"):
            out[x["review"]] += 1
    return out


# ---------------------------------------------------------------- 以前的版本：从上次确认的主订单补
def latest_main_order(root, store, now=None):
    """KEEP_DAYS 天里最近一次「确认，开始比价」给这家店生成的主订单（确认订单_门店_时间.xlsx）→ (路径, 时间) 或 None"""
    import reorder_service as R
    name = _stores().get(store, store)
    now = now or datetime.now()
    best = None
    for p in glob.glob(os.path.join(root, R.BATCH_DIR, "*", str(name), "ORDINE", "**", f"确认订单_{name}_*.xlsx"),
                       recursive=True):
        m = re.search(r"_(\d{8}_\d{4})\.xlsx$", p)
        if not m:
            continue
        t = datetime.strptime(m.group(1), "%Y%m%d_%H%M")
        if now - t > timedelta(days=KEEP_DAYS) or t > now + timedelta(minutes=5):
            continue
        if best is None or t > best[1]:
            best = (p, t)
    return best


def from_main_order(root, store, list_path, now=None):
    """
    以前的版本没存审核记录：用上次确认的主订单（每个商品确认的箱数）补一份。
      - 主订单里有的 → 那个箱数（有门店间调货的加回调货前的箱数）
      - 以前的程序读得到、主订单里没有的 → 当时定的是 0（主订单不写 0 箱的）
        清单在确认之后又改过（文件修改时间晚）→ 这些行不当成审过（可能是后来加的），让人再看
      - 以前的程序读不到的行（例如 FOIANO 清单最后只写了条码的新商品）→ 新加，要审
    没有主订单 → None
    """
    import reorder_service as R
    got = latest_main_order(root, store, now)
    if not got:
        return None
    path, when = got
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        if hasattr(ws, "reset_dimensions"):
            ws.reset_dimensions()
        it = ws.iter_rows(values_only=True)
        head = [str(v or "").strip().upper() for v in next(it, [])]
        ci = {h: i for i, h in enumerate(head)}
        if "CODICE EAN" not in ci or "COLLI" not in ci:
            return None
        boxes, codes = {}, {}
        for r in it:
            e = R._clean_code(r[ci["CODICE EAN"]] if ci["CODICE EAN"] < len(r) else None)
            if not e:
                continue
            boxes[e] = boxes.get(e, 0) + _box(r[ci["COLLI"]] if ci["COLLI"] < len(r) else 0)
            c = R._clean_code(r[ci["CODARTICOLO"]]) if "CODARTICOLO" in ci and ci["CODARTICOLO"] < len(r) else ""
            if c:
                codes[c] = e
    finally:
        wb.close()
    snap = R.load_snapshot(path) or {}
    for m in snap.get("planned_transfers") or []:        # 调货少订的：加回调货前的箱数（重新计算时调货会再算一遍）
        if m.get("to") == store or str(m.get("to")) == str(store):
            e = R._clean_code(m.get("ean")) or codes.get(str(m.get("code")))
            if e:
                boxes[e] = max(boxes.get(e, 0), _box(m.get("colli_before")))
    try:
        old = R.parse_order_file(list_path, legacy=True)["rows"] if list_path else []
    except Exception:
        old = []
    try:
        changed_after = os.path.getmtime(list_path) > when.timestamp() + 60
    except (OSError, TypeError):
        changed_after = True
    old_eans = {R._clean_code(x.get("ean")) for x in old if x.get("ean")}
    old_codes = {str(x.get("code")) for x in old if x.get("code")}
    return {"store": store, "updated": when.isoformat(timespec="seconds"), "by": os.path.basename(path),
            "confirmed": when.isoformat(timespec="seconds"), "source": "main_order",
            "boxes": boxes, "codes": codes, "old_eans": sorted(old_eans), "old_codes": sorted(old_codes),
            "zero_ok": not changed_after}


def apply_main_order(rows, mo, set_box):
    """from_main_order 的结果套到重新计算的行上（清单箱数不知道，不比）：返回 {"kept", "changed", "new"}"""
    out = {"kept": 0, "changed": 0, "new": 0}
    for x in rows:
        x.pop("review", None)
        x.pop("review_prev", None)
    if not mo:
        return out
    boxes, codes = mo["boxes"], mo["codes"]
    old_eans, old_codes = set(mo["old_eans"]), set(mo["old_codes"])
    for x in rows:
        rp = x.get("repl") or {}
        eans = [str(v).strip() for v in (rp.get("old_ean"), x.get("ean")) if v]
        code = str(rp.get("old_code") or x.get("code") or "")
        e = next((v for v in eans if v in boxes), None) or (codes.get(code) if code in codes else None)
        if e is not None:
            set_box(x, boxes[e])
            x["review"], x["review_ok"] = "kept", True
        elif any(v in old_eans for v in eans) or (code and code in old_codes):
            if mo.get("zero_ok"):
                set_box(x, 0)                    # 当时定的是不订
                x["review"], x["review_ok"] = "kept", True
            else:
                x["review"] = "new"
        else:
            x["review"] = "new"
        out[x["review"]] += 1
    return out


def restore(root, rows_by_store, lists_by_store, set_box, now=None):
    """
    重新计算完：每家店套用审核记录（没有就从上次确认的主订单补）。
    返回 {门店: {"kept", "changed", "new", "source", "when", "by"}}（没有可沿用的店不在里面）
    """
    out = {}
    for sid, rows in rows_by_store.items():
        mem = load(root, sid, now)
        if mem:
            n = apply(rows, mem, set_box)
            src = mem.get("source") or "app"
        else:
            mo = from_main_order(root, sid, lists_by_store.get(sid), now) if root else None
            if not mo:
                continue
            n = apply_main_order(rows, mo, set_box)
            mem = mo
            src = "main_order"
        if any(n.values()):
            out[sid] = dict(n, source=src, when=mem.get("updated"), by=mem.get("by") or "",
                            confirmed=mem.get("confirmed"))
    return out
