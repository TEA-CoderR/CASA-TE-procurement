"""
门店扫码枪（Zebra TC22 上的「CASA&TE Scanner」，2026-11-21 用户：门店扫要订的 DETERSIVO 商品，当场知道
在不在 Griglia、这周批发商能不能订到，不用再发门店报告让他们自己看）。不依赖界面。

程序把「目录」发布到预约平台那台服务器（/api/erp/catalog），扫码枪下载下来离线查：
  每个商品 [条码, 名称, Griglia 状态, 哪几家批发商有, 替代商品条码, 替代商品名称, 直厂供应商]
  Griglia 状态：0 不在 Griglia · 1 在 Griglia · 2 Griglia 已停用（划线）
  条码一律去掉前面的 0（和 utils.clean_ean 一样，扫码枪那边也这么比）
价格不发（门店只看能不能订）。扫码枪账号也在这里管（一台一个，配对码只显示一次）。
"""
import hashlib
import json
import os
import uuid

import booking_api as BA

PART = 4000                   # 每次上传几个商品（服务器前面的 nginx 一次最多 2 MB）


def _ean(v):
    from utils import clean_ean
    s = clean_ean(v)
    if s.endswith(".0"):
        s = s[:-2]
    return s if s.isdigit() else ""


def build(grid, quotes, subs=None, scheda=None, week=""):
    """
    grid = griglia.load 的结果（可以 None）；quotes = {批发商: {条码: {desc, price…}}}；
    subs = griglia_replace.substitutes（旧条码 → {ean, name}）；scheda = store_orders.scheda_index（直厂）。
    → (meta, items)
    """
    grid = grid or {}
    quotes = quotes or {}
    sources = sorted(quotes)
    src_idx = {s: i for i, s in enumerate(sources)}
    directs, d_idx = [], {}
    items = {}

    def get(e):
        it = items.get(e)
        if it is None:
            it = items[e] = {"name": "", "g": 0, "w": set(), "s": "", "sn": "", "d": -1}
        return it

    for e, it in (grid.get("struck") or {}).items():
        k = _ean(e)
        if k:
            x = get(k)
            x["g"], x["name"] = 2, it.get("name") or x["name"]
    for e, it in (grid.get("items") or {}).items():
        k = _ean(e)
        if k:
            x = get(k)
            x["g"], x["name"] = 1, it.get("name") or x["name"]
    for sup in sources:
        for e, q in (quotes.get(sup) or {}).items():
            k = _ean(e)
            if not k:
                continue
            try:
                ok = float((q or {}).get("price") or 0) > 0
            except (TypeError, ValueError):
                ok = False
            if not ok:                                # 没价格的行（赠品说明行之类）不算有货
                continue
            x = get(k)
            x["w"].add(src_idx[sup])
            if not x["name"]:
                x["name"] = str((q or {}).get("desc") or "").strip()
    for old, s in (subs or {}).items():
        k = _ean(old)
        new = _ean((s or {}).get("ean"))
        if k and new and new != k:
            x = get(k)
            x["s"], x["sn"] = new, (s or {}).get("name") or ""
    for e, sup in ((scheda or {}).get("ean") or {}).items():
        k = _ean(e)
        if not k or not sup:
            continue
        if sup not in d_idx:
            d_idx[sup] = len(directs)
            directs.append(sup)
        get(k)["d"] = d_idx[sup]
    rows = [[e, x["name"][:80], x["g"], sorted(x["w"]), x["s"], x["sn"][:80], x["d"]]
            for e, x in sorted(items.items())]
    meta = {"week": str(week or ""), "sources": sources, "directs": directs, "griglia": os.path.basename(grid.get("path") or ""),
            "counts": {"griglia": sum(1 for r in rows if r[2] == 1), "stopped": sum(1 for r in rows if r[2] == 2),
                       "items": len(rows)}}
    return meta, rows


def fingerprint(meta, rows):
    h = hashlib.sha1()
    h.update(json.dumps({k: v for k, v in meta.items() if k != "week"}, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for r in rows:
        h.update(json.dumps(r, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def publish(settings, meta, rows, by, session=None, progress=None):
    """分几次上传，最后一次服务器合成新目录。返回 {"id", "items"}"""
    upload = uuid.uuid4().hex
    parts = [rows[i:i + PART] for i in range(0, len(rows), PART)] or [[]]
    res = None
    for k, part in enumerate(parts, 1):
        body = {"upload": upload, "part": k, "parts": len(parts), "items": part, "by": by or "?"}
        if k == 1:
            body["meta"] = meta
        res = BA._call(settings, "POST", "/api/erp/catalog", body=body, session=session)
        if progress:
            progress(k, len(parts))
    if not (res or {}).get("complete"):
        raise BA.BookingApiError("INCOMPLETE", "目录没传完整，请再发布一次")
    return res


def _state_path():
    try:
        from settings import app_dir
        return os.path.join(app_dir(), "cache", "scanner_catalog.json")
    except Exception:
        return None


def publish_if_changed(settings, meta, rows, by, session=None, log=print, state_path=None):
    """内容和上次发布的一样就不发（Griglia 页每次刷新都会叫它）。返回 发布结果 / None（没变 / 没连平台）"""
    if not BA.ready(settings):
        return None
    path = state_path or _state_path()
    fp = fingerprint(meta, rows)
    last = {}
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                last = json.load(f)
        except (OSError, ValueError):
            last = {}
    if last.get("fp") == fp and last.get("url") == BA.base_url(settings):
        return None
    res = publish(settings, meta, rows, by, session=session)
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            json.dump({"fp": fp, "url": BA.base_url(settings), "id": res.get("id")}, f)
    log(f"扫码枪目录已发布：{len(rows)} 个商品（Griglia {meta['counts']['griglia']} 个）")
    return res


def build_from_workspace(settings, log=print):
    """从 DETERSIVO 工作文件夹现在的 Griglia + 报价 + 替换记录 + 直厂 SCHEDA 生成目录（要用 Excel 的话在 Excel 那条线程里叫）"""
    import commands as CMD
    import griglia as G
    import griglia_replace as GR
    import workspace as W
    from datetime import date
    root = settings.get("wholesale_root") or ""
    if not root or not os.path.isdir(root):
        raise RuntimeError("DETERSIVO 工作文件夹找不到（DETERSIVO采购 → 收集文件 → 工作文件夹）")
    grid = G.load_for(root)
    if not grid:
        raise RuntimeError("工作文件夹「0 Griglia」里没有 Griglia")
    quotes = CMD.read_quotes(settings, W.price_dir(root), log) or {}
    GR.set_shared((settings.get("griglia") or {}).get("source") or "")
    subs = GR.substitutes(GR.load_done(os.path.dirname(grid["path"])), grid)
    return build(grid, quotes, subs, direct_index(settings, log), week=date.today().strftime("%G-W%V"))


def direct_index(settings, log=print):
    """直厂（总仓采购 + 门店采购的 SCHEDA）有哪些商品；读不了 None"""
    try:
        import store_orders as SO
        from settings import app_dir
        roots = [r for r in ((settings.get("store_orders") or {}).get("folder") or "",
                             (settings.get("store_pdv") or {}).get("folder") or SO.PDV_DEFAULT_FOLDER)
                 if r and os.path.isdir(r)]
        if not roots:
            return None
        return SO.scheda_index(roots, os.path.join(app_dir(), "cache", "schede_index.json"), log)
    except Exception as e:
        log(f"⚠ 直厂 SCHEDA 读不了：{e}")
        return None


# ===================================================================== 扫码枪账号
STORE_OF = {0: "BMS", 1: "1", 2: "2", 3: "3", 4: "4", 5: "5"}


def devices(settings, session=None):
    """→ (扫码枪列表, 最新目录信息)"""
    data = BA._call(settings, "GET", "/api/erp/scanners", session=session)
    return data.get("devices") or [], data.get("catalog")


def add_device(settings, name, store, session=None):
    """新扫码枪 → (扫码枪, 配对码)；store = 门店号 "1"–"5" / "BMS" / None"""
    body = {"name": str(name or "").strip(), "entityCode": BA.entity_of(store)}
    data = BA._call(settings, "POST", "/api/erp/scanners", body=body, session=session)
    return data["device"], data["pairCode"]


def renew_code(settings, device_id, session=None):
    data = BA._call(settings, "POST", f"/api/erp/scanners/{int(device_id)}", body={"action": "renew"}, session=session)
    return data["device"], data["pairCode"]


def set_active(settings, device_id, active, session=None):
    data = BA._call(settings, "POST", f"/api/erp/scanners/{int(device_id)}",
                    body={"action": "activate" if active else "deactivate"}, session=session)
    return data["device"]


def pretty_code(code):
    c = str(code or "")
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c
