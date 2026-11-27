"""
门店扫码枪（Zebra TC22 上的「CASA&TE Scanner」，2026-11-21 用户：门店扫要订的 DETERSIVO 商品，当场知道
在不在 Griglia、这周批发商能不能订到，不用再发门店报告让他们自己看）。不依赖界面。

程序把「目录」发布到预约平台那台服务器（/api/erp/catalog），扫码枪下载下来离线查：
  每个商品 [条码, 名称, Griglia 状态, 哪几家批发商有, 替代商品条码, 替代商品名称, 直厂供应商]
  Griglia 状态：0 不在 Griglia · 1 在 Griglia · 2 Griglia 已停用（划线）
  条码一律去掉前面的 0（和 utils.clean_ean 一样，扫码枪那边也这么比）
扫码枪账号也在这里管（一台一个，配对码只显示一次）。

2026-11-21 第 2 轮（用户：门店在扫码枪上做 DETERSIVO 清单；目录按门店分开发，价格也发，进价、卖价、打折价都要）：
  每家门店另外一份「门店数据」（只发给那家店的扫码枪）：
    [条码, 库存, 30 天销量, 在途箱数, 建议箱数, 进价, 卖价, 打折价, 打折到哪天, 每箱件数]
  建议箱数 = 检查清单同一套算法（check_rules.init_boxes 的 sys_box）减在途；进价 = 这周批发商最便宜的件价（没有用数据库的）。
  门店清单：每周一张、一家店几台扫码枪共用一张，截止时间（默认星期天 10:00）在预约平台上设；
  下载下来写成现在格式的 Excel 放进「1 门店清单」（write_list_excel / import_lists），后面检查清单、比价照旧。
"""
import hashlib
import math
import json
import re
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


SEND_COST = False      # 进价（批发商最便宜件价 / 数据库进价）发不发给门店扫码枪：2026-11-22 用户定不显示，也不发


def build(grid, quotes, subs=None, scheda=None, week="", aliases=None, added=None):
    """
    grid = griglia.load 的结果（可以 None）；quotes = {批发商: {条码: {desc, price…}}}；
    subs = griglia_replace.substitutes（旧条码 → {ean, name}）；scheda = store_orders.scheda_index（直厂）。
    aliases = 门店申请回复「换成别的商品」的 {门店扫的条码: (该订的条码, 名称)}（product_requests.aliases）：
      这个条码在扫码枪上显示 SOSTITUITO → 该订的那个（它自己在 Griglia 里的不动）；
    added = 程序里的 Griglia 新加、Excel 里还没有的商品 {条码: {name, pack}}（门店申请「加进 Griglia」）：算在 Griglia。
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
            it = items[e] = {"name": "", "g": 0, "w": set(), "s": "", "sn": "", "d": -1, "pk": None, "p": None, "ps": -1}
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
            x["pk"] = _pack(it.get("pack")) or x["pk"]
    for e, it in (added or {}).items():
        k = _ean(e)
        if k and get(k)["g"] != 1:
            x = get(k)
            x["g"], x["name"] = 1, (it or {}).get("name") or x["name"]
            x["pk"] = _pack((it or {}).get("pack")) or x["pk"]
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
            pr = float(q.get("price") or 0)
            if x["p"] is None or pr < x["p"]:
                x["p"], x["ps"] = pr, src_idx[sup]
            if not x["pk"]:
                x["pk"] = _pack(q.get("pack"))
            if not x["name"]:
                x["name"] = str((q or {}).get("desc") or "").strip()
    for old, s in (subs or {}).items():
        k = _ean(old)
        new = _ean((s or {}).get("ean"))
        if k and new and new != k:
            x = get(k)
            x["s"], x["sn"] = new, (s or {}).get("name") or ""
    for e, (new, name) in (aliases or {}).items():
        k, new = _ean(e), _ean(new)
        if k and new and new != k and get(k)["g"] != 1:
            x = get(k)
            x["g"] = 2                                 # 不是 Griglia 商品本身：扫码枪按「已替换」显示（替代 = 该订的）
            x["s"], x["sn"] = new, name or (items.get(new) or {}).get("name") or ""
            x["name"] = x["name"] or x["sn"]
    for e, sup in ((scheda or {}).get("ean") or {}).items():
        k = _ean(e)
        if not k or not sup:
            continue
        if sup not in d_idx:
            d_idx[sup] = len(directs)
            directs.append(sup)
        x = get(k)
        x["d"] = d_idx[sup]
        if not x["pk"]:                               # 直厂商品：箱规用 SCHEDA 里的每箱件数
            v = ((scheda or {}).get("items") or {}).get((sup, k.lstrip("0")))
            x["pk"] = _pack(v[0] if v else None)
    # 2026-11-21 第 8–10 项：每箱件数 · 这周批发商最便宜件价 · 最便宜那家（下标）——门店数据没算到也看得到箱规和价格
    # 2026-11-22 用户：扫码枪上不显示进价 → 进价也不发给门店（位置留着，写空）
    rows = [[e, x["name"][:80], x["g"], sorted(x["w"]), x["s"], x["sn"][:80], x["d"],
             x["pk"], _r2(x["p"]) if SEND_COST else None, x["ps"] if SEND_COST else -1]
            for e, x in sorted(items.items())]
    meta = {"week": str(week or ""), "sources": sources, "directs": directs, "griglia": os.path.basename(grid.get("path") or ""),
            "counts": {"griglia": sum(1 for r in rows if r[2] == 1), "stopped": sum(1 for r in rows if r[2] == 2),
                       "items": len(rows)}}
    return meta, rows


def _pack(v):
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return None
    return (int(f) if f == int(f) else round(f, 2)) if f > 0 else None


def fingerprint(meta, rows):
    h = hashlib.sha1()
    h.update(json.dumps({k: v for k, v in meta.items() if k != "week"}, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for r in rows:
        h.update(json.dumps(r, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def publish(settings, meta, rows, by, session=None, progress=None, stores=None, vol=True):
    """分几次上传，最后一次服务器合成新目录。stores = {门店号: 门店数据行}（每家店只发给它自己的扫码枪）。返回 {"id", "items"}
    vol：带上正在发布的 VOLANTINO（with_volantino；publish_if_changed 自己先带过了传 False）"""
    if vol:
        meta, stores = with_volantino(settings, meta, stores)
    upload = uuid.uuid4().hex
    parts = [(None, rows[i:i + PART]) for i in range(0, len(rows), PART)] or [(None, [])]
    for sid, srows in sorted((stores or {}).items()):
        parts += [(int(sid), srows[i:i + PART]) for i in range(0, len(srows), PART)]
    res = None
    for k, (sid, part) in enumerate(parts, 1):
        body = {"upload": upload, "part": k, "parts": len(parts), "items": part, "by": by or "?"}
        if k == 1:      # 图片地址模板跟着目录发（扫码枪按条码取商品图片）
            body["meta"] = dict(meta, imageUrl=(settings or {}).get("image_url") or "https://img.casaete.top/prodotti/{ean}/01.webp")
        if sid is not None:
            body["store"] = sid
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


def publish_if_changed(settings, meta, rows, by, session=None, log=print, state_path=None, stores=None):
    """内容和上次发布的一样就不发（Griglia 页每次刷新都会叫它）。返回 发布结果 / None（没变 / 没连平台）"""
    if not BA.ready(settings):
        return None
    path = state_path or _state_path()
    meta, stores = with_volantino(settings, meta, stores)
    fp = fingerprint(meta, rows + [[str(k)] + r for k, v in sorted((stores or {}).items()) for r in v])
    last = {}
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                last = json.load(f)
        except (OSError, ValueError):
            last = {}
    if last.get("fp") == fp and last.get("url") == BA.base_url(settings):
        return None
    res = publish(settings, meta, rows, by, session=session, stores=stores, vol=False)
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            json.dump({"fp": fp, "url": BA.base_url(settings), "id": res.get("id")}, f)
    log(f"扫码枪目录已发布：{len(rows)} 个商品（Griglia {meta['counts']['griglia']} 个）"
        + (f"，门店数据 {len(stores)} 家" if stores else ""))
    return res


def build_from_workspace(settings, log=print, with_quotes=False):
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
    meta, rows = build(grid, quotes, subs, direct_index(settings, log), week=date.today().strftime("%G-W%V"),
                       aliases=request_aliases(settings, log), added=program_added(root, grid, log))
    meta["phrases"] = store_phrases(settings, log)
    return (meta, rows, quotes) if with_quotes else (meta, rows)


# 扫码枪「问办公室」的快捷句子（2026-11-28 用户：要实用的，参考门店发来的消息）：
# 五家店发给办公室的话拆成一句一句，写得最多的发给扫码枪；扫码枪上自己写过 2 次以上的排在前面。
PHRASES_MAX = 8
_SPLIT = re.compile(r"[.;!?\n]+|,\s+")


def phrase_pieces(text):
    """一条消息里值得记的句子（3–60 个字，和扫码枪 Phrases.pieces 一样拆）"""
    out = []
    for p in _SPLIT.split(str(text or "")):
        p = re.sub(r"\s+", " ", p.strip().rstrip(",:"))
        if 3 <= len(p) <= 60 and not re.fullmatch(r"[\d\s.,x×]+", p):
            out.append(p)
    return out


def common_phrases(reqs, top=PHRASES_MAX):
    """门店写给办公室的话（每条申请的整段对话里门店说的）→ 写得最多的句子（同一句不分大小写）；
    一条申请里重复的只算一次，至少 2 条申请写过才算"""
    count, spell = {}, {}
    for r in reqs or []:
        texts = [m.get("text") for m in (r.get("messages") or []) if (m.get("by") or "store") != "office"]
        if not texts:
            texts = [r.get("note"), r.get("storeReply")]
        seen = set()
        for t in texts:
            for p in phrase_pieces(t):
                k = p.lower()
                if k in seen:
                    continue
                seen.add(k)
                count[k] = count.get(k, 0) + 1
                spell.setdefault(k, {}).setdefault(p, 0)
                spell[k][p] += 1
    best = sorted((k for k, n in count.items() if n >= 2), key=lambda k: (-count[k], k))[:top]
    return [max(spell[k].items(), key=lambda kv: kv[1])[0] for k in best]


def store_phrases(settings, log=print, path=None):
    """预约平台上近半年全部门店申请 → common_phrases；读不了用上次存的 cache/scanner_phrases.json"""
    from settings import app_dir
    path = path or os.path.join(app_dir(), "cache", "scanner_phrases.json")
    try:
        import product_requests as PR
        reqs = PR.fetch(settings, status="all", days=180)
        ph = common_phrases(reqs)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                json.dump(ph, f, ensure_ascii=False)
        except OSError:
            pass
        if ph:
            log("扫码枪快捷句子（门店写得最多的）：" + " | ".join(ph))
        return ph
    except Exception as e:
        try:
            with open(path, encoding="utf-8") as f:
                return list(json.load(f))
        except (OSError, ValueError):
            if BA.ready(settings):
                log(f"⚠ 门店申请（快捷句子）读不了：{e}")
            return []


def request_aliases(settings, log=print, path=None):
    """门店申请里回复「换成别的商品」的条码（预约平台上读，读不了用上次存的 cache/scanner_aliases.json）"""
    from settings import app_dir
    path = path or os.path.join(app_dir(), "cache", "scanner_aliases.json")
    try:
        import product_requests as PR
        al = PR.aliases(PR.fetch(settings, status="answered", days=3650))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                json.dump({k: list(v) for k, v in al.items()}, f, ensure_ascii=False)
        except OSError:
            pass
        return al
    except Exception as e:
        try:
            with open(path, encoding="utf-8") as f:
                return {k: tuple(v) for k, v in json.load(f).items()}
        except (OSError, ValueError):
            if BA.ready(settings):
                log(f"⚠ 门店申请（换新条码）读不了：{e}")
            return {}


def program_added(root, grid, log=print):
    """程序里的 Griglia（griglia_db）有、Excel Griglia 里还没有的在售 / 新品商品 {条码: {name, pack}}"""
    try:
        import griglia_db as D
        cat = D.load(root)
    except Exception as e:
        log(f"⚠ 程序里的 Griglia 读不了：{e}")
        return {}
    if cat is None:
        return {}
    have = {_ean(e) for e in list((grid or {}).get("items") or {}) + list((grid or {}).get("struck") or {})}
    return {_ean(x["ean"]): {"name": x["name"], "pack": x.get("pack")} for x in cat.items
            if x.get("status") in ("new", "active", "paused") and _ean(x["ean"]) and _ean(x["ean"]) not in have}


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


# ===================================================================== 每家门店的数据
STORE_IDS = (1, 2, 3, 4, 5)
STORE_MAX_AGE = 4 * 3600          # 门店数据（查数据库，几分钟）最多这么久重算一次；手动发布一律重算


def _r2(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return round(f, 2)


def stock_suspect(stock, s30, s90, last_days):
    """账面有货、平时卖得动，却很久一件没卖 → 库存可能不准（和检查清单 phantom_info 一个意思：
    真有货这么久不卖的可能很小）。只给扫码枪提醒门店去清点"""
    try:
        stock = float(stock or 0)
        daily = max(float(s30 or 0) / 30.0, float(s90 or 0) / 90.0)
    except (TypeError, ValueError):
        return False
    if stock <= 0 or daily <= 0 or last_days is None:
        return False
    return last_days >= max(7, math.ceil(3 / daily))


def store_row(ean, m, transit_boxes, cost, price, promo, sys_box=None, pack=None, hist=None):
    """一个商品在一家店的数据行（扫码枪 StoreInfo 的顺序）：
    [条码, 库存, 30 天销量, 在途箱数, 建议箱数, 进价, 卖价, 打折价, 打折到, 每箱件数, 几天没卖, 库存可能不准 0/1,
     到货记录, 每月销量]（store_history；后面 add_others 加第 14 项别的门店库存，publish 加第 15 项 VOLANTINO）
    m = 门店数字 {stock, s30, s90, last_days, pack}；sys_box = 给门店的建议箱数（store_advice，已经扣过在途；
    只有 Griglia 商品 + 替代商品有）；pack = 审核时用的每箱件数（数据库箱规和批发商差太多时是批发商的），
    在途箱数也按它折（在途件数 ÷ pack）"""
    pk = pack if pack else m.get("pack")
    pk = _r2(pk) if pk else None
    sugg = None if sys_box is None else max(0, int(sys_box))
    last = m.get("last_days")
    return [ean, _r2(m.get("stock")), _r2(m.get("s30")), _r2(transit_boxes or 0), sugg,
            _r2(cost), _r2(price), _r2((promo or {}).get("price")), _promo_end(promo), pk,
            None if last is None else int(last),
            1 if stock_suspect(m.get("stock"), m.get("s30"), m.get("s90"), last) else 0,
            (hist or {}).get("arr") or None, (hist or {}).get("months")]


def _promo_end(promo):
    d = (promo or {}).get("end")
    if not d:
        return ""
    try:
        return d.strftime("%d/%m")
    except AttributeError:
        s = str(d)[:10]
        return f"{s[8:10]}/{s[5:7]}" if len(s) == 10 and s[4] == "-" else s


def cheapest(quotes, ean):
    """这周批发商最便宜的件价（没有 None）"""
    best = None
    for q in (quotes or {}).values():
        it = (q or {}).get(ean) or (q or {}).get(str(ean).zfill(13))
        try:
            p = float((it or {}).get("price") or 0)
        except (TypeError, ValueError):
            continue
        if p > 0 and (best is None or p < best):
            best = p
    return best


def _chunks(codes, n=2000):
    for i in range(0, len(codes), n):
        chunk = [int(c) for c in codes[i:i + n]]
        yield chunk, ", ".join(["%s"] * len(chunk))


def _days(d, today):
    if d is None:
        return None
    try:
        import pandas as pd
        if pd.isna(d):
            return None
        d = pd.Timestamp(d).date()
    except Exception:
        return None
    return max(0, (today - d).days)


def light_numbers(db, sid, codes, today=None):
    """一家店这些商品的 库存 / 30 天和 90 天销量 / 最后卖出（几天前），只读、三条查询（不算建议量，几千个商品也快）
    → {编号: {stock, s30, s90, last_days}}"""
    import reorder_service as R
    from datetime import date, timedelta
    today = today or date.today()
    sid = int(sid)
    out = {str(c): {"stock": 0.0, "s30": 0.0, "s90": 0.0, "last_days": None} for c in codes}
    d30, d90, d365 = (today - timedelta(days=k) for k in (30, 90, 365))
    for chunk, ph in _chunks(codes):
        df = R.read_sql_retry(f"SELECT CodArticolo, G{sid} AS g FROM giacenza WHERE CodArticolo IN ({ph})", db,
                              tuple(chunk))
        for r in df.to_dict("records"):
            out[str(int(r["CodArticolo"]))]["stock"] = float(r["g"] or 0)
        df = R.read_sql_retry(f"SELECT CodArticolo, SUM(CASE WHEN Data >= %s THEN Qta ELSE 0 END) AS q30, SUM(Qta) AS q90 "
                              f"FROM vendite_list_{sid} WHERE CodArticolo IN ({ph}) AND Data >= %s GROUP BY CodArticolo",
                              db, (d30, *chunk, d90))
        for r in df.to_dict("records"):
            x = out[str(int(r["CodArticolo"]))]
            x["s30"], x["s90"] = float(r["q30"] or 0), float(r["q90"] or 0)
        df = R.read_sql_retry(f"SELECT CodArticolo, MAX(Data) AS d FROM vendite_list_{sid} WHERE CodArticolo IN ({ph}) "
                              f"AND Qta > 0 AND Data >= %s GROUP BY CodArticolo", db, (*chunk, d365))
        for r in df.to_dict("records"):
            out[str(int(r["CodArticolo"]))]["last_days"] = _days(r["d"], today)
    return out


HIST_SINCE = "2025-01-08"   # 这天以前的入库记录是错的（reorder_service.OLD_RECEIPTS_BEFORE）
HIST_ARRIVALS = 12          # 每个商品最多发最近几次到货
HIST_MONTHS = 12            # 每月销量发几个月（含本月）
OTHER_NAMES = {0: "BMS", 1: "FOIANO", 2: "MARLIA", 3: "S.FILIPPO", 4: "S.MARCO", 5: "S.CONCORDIO"}


def _ymd(d):
    try:
        import pandas as pd
        d = pd.Timestamp(d).date()
        return d.year * 10000 + d.month * 100 + d.day
    except Exception:
        return None


def store_history(db, sid, codes, today=None, sup_names=None):
    """
    扫码枪「In negozio」页面要的：一家店每个商品
      arr    = 最近 HIST_ARRIVALS 次到货 [[yyyymmdd, 件数, 0 供应商 / 1 调货, 从哪来（供应商名 / 门店名 / BMS）]]（新的在前；
               最近那次多第 5 项 = 那天起卖了几件）
               caricolistalist 这家店 Quantita > 0 的 CA（供应商送货）/ CI（调入，Fornitore = 调出的门店号，0 = 仓库 BMS），
               一年内、而且 HIST_SINCE 以后（更早的记录是错的）；同一天同一来源几行合在一起
      months = 最近 HIST_MONTHS 个月每个月卖了几件（从旧到新，最后一个是本月，还没过完）
    → {编号: {"arr": [...], "months": [...]}}（没到过货也没卖过的不在里面）。只读。
    """
    import reorder_service as R
    from datetime import date, timedelta
    today = today or date.today()
    sid = int(sid)
    names = sup_names or {}
    since = max(date.fromisoformat(HIST_SINCE), today - timedelta(days=365))
    keys = R.month_keys(HIST_MONTHS, today)
    m_since = date(keys[0][0], keys[0][1], 1)
    pos = {k: i for i, k in enumerate(keys)}
    out, sold_since = {}, {}
    for chunk, ph in _chunks([int(c) for c in codes], 1000):
        df = R.read_sql_retry(
            f"SELECT CodArticolo AS c, DATE(Cdata) AS d, Tipo AS t, Fornitore AS f, SUM(Quantita) AS q FROM caricolistalist "
            f"WHERE Negozio = %s AND CodArticolo IN ({ph}) AND Cdata >= %s AND Quantita > 0 AND Tipo IN ('CA', 'CI') "
            f"GROUP BY CodArticolo, DATE(Cdata), Tipo, Fornitore", db, (sid, *chunk, since))
        for r in df.to_dict("records"):
            try:
                c, f = str(int(r["c"])), int(r["f"] or 0)
            except (TypeError, ValueError):
                continue
            day = _ymd(r["d"])
            if day is None:
                continue
            tr_ = str(r["t"]).strip().upper() == "CI"
            src = OTHER_NAMES.get(f, str(f)) if tr_ else (names.get(f) or str(f))
            out.setdefault(c, {"arr": [], "months": None})["arr"].append([day, _r2(r["q"]), 1 if tr_ else 0, src])
        df = R.read_sql_retry(
            f"SELECT CodArticolo AS c, YEAR(Data) AS y, MONTH(Data) AS m, SUM(Qta) AS q FROM vendite_list_{sid} "
            f"WHERE CodArticolo IN ({ph}) AND Data >= %s GROUP BY CodArticolo, YEAR(Data), MONTH(Data)",
            db, (*chunk, m_since))
        # 最后一次到货那天起卖了几件（扫码枪「核对库存」：到了多少、卖了多少、系统说还有多少）
        df_s = R.read_sql_retry(
            f"SELECT v.CodArticolo AS c, SUM(v.Qta) AS q FROM vendite_list_{sid} v JOIN ("
            f"SELECT CodArticolo, MAX(DATE(Cdata)) AS d FROM caricolistalist WHERE Negozio = %s AND Cdata >= %s "
            f"AND Quantita > 0 AND Tipo IN ('CA', 'CI') AND CodArticolo IN ({ph}) GROUP BY CodArticolo) a "
            f"ON a.CodArticolo = v.CodArticolo WHERE v.Data >= a.d GROUP BY v.CodArticolo", db, (sid, since, *chunk))
        for r in df_s.to_dict("records"):
            try:
                sold_since[str(int(r["c"]))] = _r2(r["q"]) or 0
            except (TypeError, ValueError):
                pass
        for r in df.to_dict("records"):
            try:
                c, k = str(int(r["c"])), (int(r["y"]), int(r["m"]))
            except (TypeError, ValueError):
                continue
            if k not in pos:
                continue
            x = out.setdefault(c, {"arr": [], "months": None})
            if x["months"] is None:
                x["months"] = [0] * len(keys)
            q = float(r["q"] or 0)
            x["months"][pos[k]] = int(q) if q == int(q) else round(q, 1)
    for c, x in out.items():
        x["arr"].sort(key=lambda a: -a[0])
        del x["arr"][HIST_ARRIVALS:]
        if x["arr"]:                       # 第一次（最近的）到货后面加第 5 项：那天起卖了几件
            x["arr"][0].append(sold_since.get(c, 0))
    return out


def add_others(stores):
    """每家店每个商品后面加上别的门店现在几件（第 15 项 [[门店名, 件数]]，别家都没货就 None）。改 stores 里的行"""
    stock = {sid: {r[0]: r[1] for r in rows} for sid, rows in stores.items()}
    for sid, rows in stores.items():
        for r in rows:
            while len(r) < 14:
                r.append(None)
            oth = [[OTHER_NAMES.get(int(o), str(o)), stock[o].get(r[0])] for o in sorted(stores) if o != sid]
            oth = [o for o in oth if o[1] is not None]
            r[14:15] = [oth if any((o[1] or 0) > 0 for o in oth) else None]
    return stores


def advice_context(settings, db, log=print, rule=None, now=None):
    """建议箱数的日历（store_advice.context）：数据库数据的日期、下一个截止（预约平台的设置，读不到星期天 10:00）、
    到货天数（设置 scanner.lead_days；没设按订单跟踪里 DETERSIVO 单下单到收货的中位数；都没有 3 天）"""
    import reorder_service as R
    import store_advice as SA
    try:
        latest = R.sales_latest(db)
    except Exception as e:
        latest = None
        log(f"⚠ 扫码枪建议：数据库最新销售日期没查到（{e}），按今天算")
    if rule is None:
        try:
            rule = deadline_rule(settings) if BA.ready(settings) else None
        except Exception:
            rule = None
    rule = rule or {"day": 7, "time": "10:00"}
    lead = (settings.get("scanner") or {}).get("lead_days")
    n = 0
    if not lead:
        lead = SA.DEFAULT_LEAD
        try:
            import commands as CMD
            import order_tracking as OT
            lead, n = SA.lead_from_tracking(OT.rows(CMD.tracking_store(settings)))
        except Exception:
            pass
    ctx = SA.context(latest, rule, int(lead), now=now)
    log(f"扫码枪建议：数据 {ctx['dataDate']} · 截止 {ctx['deadline']} · 到货 {ctx['lead']} 天"
        + (f"（订单跟踪 {n} 张单）" if n else "") + f" → 备 {ctx['horizon']} 天的货")
    if ctx["stale"]:
        log(f"⚠ 扫码枪建议：数据库数据只到 {ctx['dataDate']}，门店看到的建议可能不准")
    return ctx


def store_data(settings, rows, quotes, log=print, stores=STORE_IDS, with_ctx=False):
    """
    rows = build 的目录行。每家店：目录里**所有在数据库里的商品**（Griglia 的、不在 Griglia 门店说明原因才订的、直厂的）
    都给 库存 / 销量 / 最后卖出 / 卖价 / 打折价（light_numbers，三条查询）；
    Griglia 商品 + 替代商品另外算给门店的建议箱数（store_advice：一周订一次的节奏 + 检查清单同一套新算法，慢）。
    → {门店号: [门店数据行]}（with_ctx：(数据, 日历)）。查数据库（只读），出错的门店跳过（日志里写）。
    """
    import order_record as REC
    import price_check as PC
    import reorder_service as R
    import store_advice as SA
    import commands as CMD
    from datetime import date
    want_grid = {r[0] for r in rows if r[2] in (1, 2)} | {r[4] for r in rows if r[4]}
    want = sorted({r[0] for r in rows} | want_grid)
    if not want:
        return ({}, None) if with_ctx else {}
    p = CMD.check_params(settings)
    db = R.make_db(settings.get("db") or None)
    try:
        ctx = advice_context(settings, db, log)
        algo = SA.algo_for(p["algo"], ctx)
        best = PC.best_quotes(quotes or {})
        codes = R.codes_from_eans(db, want)
        by_code = {}
        for e, c in codes.items():
            by_code.setdefault(c, e)
        code_list = sorted(by_code)
        grid_codes = sorted({c for e, c in codes.items() if e in want_grid})
        try:
            transit = REC.cached(settings) if REC.tracking_folder(settings) else None
        except Exception as e:
            transit = None
            log(f"⚠ 扫码枪门店数据：在途没读到（{e}）")
        prod = {}
        try:
            for chunk, ph in _chunks(code_list):
                df = R.read_sql_retry(f"SELECT CodArticolo, PrezzoVedi1, PrezzoForn, qtasmallbox FROM prodotti "
                                      f"WHERE CodArticolo IN ({ph})", db, tuple(chunk))
                for r in df.to_dict("records"):
                    prod[str(int(r["CodArticolo"]))] = r
        except Exception as e:
            log(f"⚠ 扫码枪门店数据：卖价 / 进价没读到（{e}）")
        today = date.today()

        try:
            sup_names = R.supplier_names(db)
        except Exception:
            sup_names = {}

        def one(sid):
            nums = light_numbers(db, sid, code_list, today)
            try:
                hist = store_history(db, sid, code_list, today, sup_names)
            except Exception as e:
                hist = {}
                log(f"⚠ 扫码枪门店数据：{R.STORES.get(sid, sid)} 到货记录 / 每月销量没读到（{e}）")
            calc = {}
            if grid_codes:
                # 建议箱数算不出来（数据库慢 / 某个商品数据怪）也照样发这家店的库存 / 销量 / 价格：扫码枪会按销量估一个
                try:
                    df = R.compute_suggestions(db, sid, grid_codes, p["days"], SA.REVIEW, None,
                                               use_supplier_cycles=False, algo=algo)
                    res = R.to_rows(df, [], p["days"])
                    PC.apply_quotes(res, best)          # ① 箱规和检查清单一样
                    calc = {x["code"]: x for x in res}
                except Exception as e:
                    log(f"⚠ 扫码枪门店数据：{R.STORES.get(sid, sid)} 建议箱数没算出来（{e}），只发库存 / 销量 / 价格")
            try:
                promos = R.query_promos(db, sid, code_list)
            except Exception:
                promos = {}
            out, n_cap, n_cens, n_month, big = [], 0, 0, 0, []
            for code in code_list:
                ean = by_code[code]
                pr = prod.get(code) or {}
                m = dict(nums.get(code) or {}, pack=pr.get("qtasmallbox"))
                t = REC.lookup(transit, sid, ean, code) if transit else None
                tpcs = float(t.get("pcs") or 0) if t else 0.0
                x = calc.get(code)
                pack = float((x or {}).get("pack") or m.get("pack") or 0) or None
                tb = round(tpcs / pack, 1) if (tpcs and pack) else (float(t.get("colli") or 0) if t else 0)
                boxes = None
                if x is not None:
                    boxes, flags = SA.advise(x, algo, ctx, tpcs)
                    n_cap += "capped" in flags
                    n_cens += "censored" in flags
                    n_month += "month" in flags
                    price = (best.get(ean) or {}).get("price") or x.get("price") or 0
                    if boxes and pack and price and boxes * pack * float(price) >= BIG_ADVICE_EUR:
                        big.append(f"{x.get('name') or ean} {boxes} × {pack:g} = €{boxes * pack * float(price):,.0f}")
                out.append(store_row(ean, m, tb, (cheapest(quotes, ean) or pr.get("PrezzoForn")) if SEND_COST else None,
                                     pr.get("PrezzoVedi1"), promos.get(code), boxes, pack, hist.get(code)))
            name = R.STORES.get(sid, sid)
            if n_cens or n_cap or n_month:
                log(f"扫码枪建议 {name}：断货补算 {n_cens} 个 · 封顶砍过 {n_cap} 个 · 不到一个月补 1 箱 {n_month} 个")
            for b in big:
                log(f"⚠ 扫码枪建议 {name}：金额很大，看一下 · {b}")
            return out
        from concurrent.futures import ThreadPoolExecutor
        out = {}
        with ThreadPoolExecutor(max_workers=R.DB_WORKERS, thread_name_prefix="scanner-db") as ex:
            futs = {sid: ex.submit(one, sid) for sid in stores}
            for sid, f in futs.items():
                try:
                    out[sid] = f.result()
                except Exception as e:
                    log(f"⚠ 扫码枪门店数据：{R.STORES.get(sid, sid)} 没算出来（{e}）")
        add_others(out)
        return (out, ctx) if with_ctx else out
    finally:
        try:
            db.engine.dispose()
        except Exception:
            pass


BIG_ADVICE_EUR = 300      # 给门店的单个建议金额超过这么多：日志里列出来看一眼（门店会照着订）


def _store_cache_path():
    try:
        from settings import app_dir
        return os.path.join(app_dir(), "cache", "scanner_store_data.json")
    except Exception:
        return None


_STORE_LOCK = __import__("threading").Lock()
STORE_DATA_VER = 2      # 门店数据行的样子改了加 1（2 = 多了到货记录 / 每月销量 / 别的门店库存），旧缓存作废


def cached_store_data(settings, rows, quotes, log=print, force=False, path=None, now=None, with_ctx=False):
    """门店数据 STORE_MAX_AGE 内算过、而且还是同一天算的就用上次的（Griglia 页每次刷新都发布，不用每次都查几分钟数据库；
    过了一天建议要按新的日历重算）。with_ctx：(数据, 建议的日历)"""
    import time
    path = path or _store_cache_path()
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if path and not force and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                c = json.load(f)
            if now - float(c.get("at") or 0) < STORE_MAX_AGE and c.get("data") \
                    and (c.get("day") or day) == day and c.get("ver") == STORE_DATA_VER:
                data = {int(k): v for k, v in c["data"].items()}
                return (data, c.get("ctx")) if with_ctx else data
        except (OSError, ValueError):
            pass
    waited = not _STORE_LOCK.acquire(blocking=False)
    if waited:                        # 自动发布和手动「发布目录」不同时查数据库（两份一起查几千条，数据库会断）
        _STORE_LOCK.acquire()
    try:
        if waited and path and os.path.exists(path):
            try:                      # 等的时候另一边刚算完：直接用它的
                with open(path, encoding="utf-8") as f:
                    c = json.load(f)
                if c.get("data") and (c.get("day") or day) == day and c.get("ver") == STORE_DATA_VER:
                    data = {int(k): v for k, v in c["data"].items()}
                    log("扫码枪门店数据：用刚算好的那份")
                    return (data, c.get("ctx")) if with_ctx else data
            except (OSError, ValueError):
                pass
        return _compute_store_cache(settings, rows, quotes, log, path, now, day, with_ctx)
    finally:
        _STORE_LOCK.release()


def _compute_store_cache(settings, rows, quotes, log, path, now, day, with_ctx):
    import time
    t0 = time.time()
    res = store_data(settings, rows, quotes, log, with_ctx=True)
    data, ctx = res if isinstance(res, tuple) else (res, None)
    log(f"扫码枪门店数据：{len(data)} 家门店，用了 {time.time() - t0:.0f} 秒")
    if path and data:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            json.dump({"at": now, "day": day, "ver": STORE_DATA_VER, "ctx": ctx, "data": {str(k): v for k, v in data.items()}}, f)
    return (data, ctx) if with_ctx else data


# ===================================================================== 门店清单（扫码枪做的）
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期天")
KIND_TEXT = {"griglia": "", "sub": "SOSTITUTO", "urgent": "NON IN GRIGLIA", "remind": "DA RICORDARE", "other": ""}


def deadline_rule(settings, session=None):
    """{"day": 1–7（星期一 … 星期天）, "time": "HH:MM"}"""
    return BA._call(settings, "GET", "/api/erp/store-lists/settings", session=session)


def set_deadline_rule(settings, day, time_text, session=None):
    return BA._call(settings, "PUT", "/api/erp/store-lists/settings", body={"day": int(day), "time": str(time_text)},
                    session=session)


def store_lists(settings, week=None, session=None):
    """→ {"rule", "current", "deadline", "lists": [...]}（不给 week = 这周和上周）"""
    return BA._call(settings, "GET", "/api/erp/store-lists", params={"week": week} if week else None, session=session)


def mark_received(settings, list_id, session=None):
    return BA._call(settings, "POST", f"/api/erp/store-lists/{int(list_id)}", body={"action": "received"}, session=session)


def send_feedback(settings, list_id, feedback, session=None):
    return BA._call(settings, "POST", f"/api/erp/store-lists/{int(list_id)}", body={"action": "feedback", "feedback": feedback},
                    session=session)


def list_file_name(lst):
    """「FOIANO - Lista scanner 2211.xlsx」：文件名里有门店名（workspace.guess_store 认得出）"""
    import workspace as W
    sid = lst.get("entityCode")
    store = W.STORES.get(int(sid), str(sid)) if sid else "BMS"
    w = str(lst.get("week") or "")
    return f"{store} - Lista scanner {w[8:10]}{w[5:7]}.xlsx"


def write_list_excel(lst, path):
    """扫码枪的清单 → 和门店手做的清单一样的 Excel（reorder_service.parse_order_file 读得了），只写要订的（箱数 > 0）。
    多两列：NON IN GRIGLIA（门店要从批发商订的非 Griglia 商品写 SI，2026-11-25 以前叫 URGENZA）、MOTIVO（原因 / 替代了哪个商品），检查清单里会显示。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "GRIGLIA"
    ws.append(["Codice EAN", "Descrizione", "Colli", "NON IN GRIGLIA", "MOTIVO", "Tipo", "Inserito da"])
    for c in ws[1]:
        c.font = Font(bold=True)
    red = PatternFill("solid", fgColor="FEE4E2")
    for l in lst.get("lines") or []:
        if int(l.get("boxes") or 0) <= 0:
            continue
        kind = l.get("kind") or "griglia"
        why = l.get("reason") or ""
        if kind == "sub":
            why = f"Sostituisce {l.get('origEan') or ''} {l.get('origName') or ''}".strip()
        ws.append([str(l.get("ean") or ""), l.get("name") or "", int(l["boxes"]), "SI" if kind == "urgent" else "",
                   why, KIND_TEXT.get(kind, ""), l.get("updatedBy") or ""])
        if kind == "urgent":
            for c in ws[ws.max_row]:
                c.fill = red
        ws.cell(ws.max_row, 1).number_format = "@"
    for col, w in zip("ABCDEFG", (16, 46, 7, 10, 34, 13, 18)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    wb.save(path)
    return path


def import_lists(settings, root, lists, session=None, tmp_dir=None, log=print):
    """把这几张清单写成 Excel、放进「1 门店清单」（同一家店原来的挪进 _旧文件），平台上记成已收到。
    → [(门店号, 新路径, [(原来的清单, 挪到哪)])]。记下哪个文件来自哪张清单（cache/scanner_lists.json），确认比价后回写结果用。"""
    import tempfile
    import workspace as W
    out = []
    tmp_dir = tmp_dir or tempfile.mkdtemp(prefix="scanner_lists_")
    seen = _load_seen()
    for lst in lists:
        sid = lst.get("entityCode")
        if sid not in W.STORES:
            continue
        src = write_list_excel(lst, os.path.join(tmp_dir, list_file_name(lst)))
        folder = W.lists_dir(root)
        before = [os.path.join(folder, f) for f in W._excel(folder) if W.guess_store(f) == sid] if os.path.isdir(folder) else []
        dst, moved = W.place_list(root, src, sid)
        try:
            mark_received(settings, lst["id"], session=session)
        except Exception as e:
            log(f"⚠ {W.STORES[sid]} 清单已放进去，但平台上没记成已收到：{e}")
        seen[str(sid)] = {"id": lst["id"], "week": lst.get("week"), "file": os.path.basename(dst),
                          "lines": {str(l["ean"]): int(l.get("boxes") or 0) for l in lst.get("lines") or []}}
        out.append((sid, dst, list(zip(before, moved))))      # (原来的路径, 挪进 _旧文件 以后的路径)：撤销用
        log(f"扫码枪清单：{W.STORES[sid]} {sum(1 for l in lst.get('lines') or [] if int(l.get('boxes') or 0) > 0)} 个商品 → {dst}")
    _save_seen(seen)
    return out


def _seen_path():
    try:
        from settings import app_dir
        return os.path.join(app_dir(), "cache", "scanner_lists.json")
    except Exception:
        return None


def _load_seen():
    p = _seen_path()
    if p and os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {}


def _save_seen(seen):
    p = _seen_path()
    if p:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            json.dump(seen, f, ensure_ascii=False)


def feedback_for(list_lines, rows):
    """确认比价时每个商品最后定了几箱 → 平台上那张清单的结果 {"lines": {条码: {"boxes", "note"}}}。
    rows = 检查清单的行（store / ean / fbox / repl）；清单里有、检查里找不到的写 0 箱"""
    final = {}
    row_of = {}
    for x in rows:
        e = _ean(x.get("ean"))
        if e:
            final[e] = final.get(e, 0) + int(x.get("fbox") or 0)
            row_of.setdefault(e, x)
        rp = x.get("repl") or {}
        old = _ean(rp.get("old_ean") if isinstance(rp, dict) else "")
        if old and old not in final:
            final[old] = int(x.get("fbox") or 0)
            row_of.setdefault(old, x)
    lines = {}
    for e, asked in (list_lines or {}).items():
        if asked <= 0:
            continue
        got = final.get(_ean(e), 0)
        lines[e] = {"boxes": got}
        note = office_note(row_of.get(_ean(e)), asked, got)
        if note:
            lines[e]["note"] = note
    return {"lines": lines}


def office_note(x, asked, got):
    """采购部最后的决定写成一句意大利文，门店扫码枪上看得到（2026-11-22 用户：门店写了原因要知道接不接受、为什么改）"""
    from check_rules import store_reason
    x = x or {}
    reason = store_reason(x)
    why = []
    cut = int(x.get("transit_cut") or 0)
    if cut and got < asked:
        why.append(f"già in arrivo {cut} {'collo' if cut == 1 else 'colli'}")
    cover = x.get("cover_now")
    try:
        cover = float(cover)
    except (TypeError, ValueError):
        cover = None
    if got < asked and not cut and cover is not None and cover != float("inf") and cover >= 30:
        why.append(f"scorta per ~{int(round(cover))} giorni")
    edited = x and x.get("sbox") is not None and int(x.get("fbox") or 0) != int(x.get("sbox") or 0)
    if got < asked and edited:
        why.append("deciso dall'ufficio acquisti")
    tail = (": " + ", ".join(why)) if why else ""
    if got <= 0:
        return ("Non ordinato" + tail)[:160]
    if got < asked:
        return (f"Ridotto da {asked} a {got}" + tail)[:160]
    if got > asked:
        return f"Aumentato da {asked} a {got} dall'ufficio acquisti"
    if reason:
        return ("Accettato · motivo: " + reason)[:160]
    return ""


def send_check_feedback(settings, rows, list_paths, log=print):
    """确认开始比价后：用了扫码枪清单的门店，把最后定的箱数写回平台（门店扫码枪上看得到订了多少）"""
    if not BA.ready(settings):
        return 0
    seen = _load_seen()
    names = {os.path.basename(p) for p in list_paths or []}
    n = 0
    for sid, info in seen.items():
        if info.get("file") not in names or info.get("sent"):
            continue
        mine = [x for x in rows if str(x.get("store")) == str(sid)]
        try:
            send_feedback(settings, info["id"], feedback_for(info.get("lines"), mine))
            info["sent"] = True
            n += 1
        except Exception as e:
            log(f"⚠ 扫码枪清单结果没写回平台（门店 {sid}）：{e}")
    if n:
        _save_seen(seen)
        log(f"扫码枪：{n} 家门店的清单结果已写回")
    return n


# ===================================================================== 门店报的库存错误
# 门店在扫码枪上清点（货架 + 仓库都看过、写了谁点的、按住确认）后报的真实件数。程序不写数据库：
# 采购部在 ERP 里改好库存后点「已在 ERP 改好」，或者驳回（写原因，门店扫码枪上看得到）。
def stock_reports(settings, status="open", session=None):
    """→ [{id, entityCode, ean, name, systemQty, counted, countedBy, device, status, answer, handledBy, handledAt, createdAt}]"""
    data = BA._call(settings, "GET", "/api/erp/stock-reports", params={"status": status or "all"}, session=session)
    return data.get("reports") or []


def handle_stock_report(settings, report_id, action, answer=None, by=None, session=None):
    """action = fixed（ERP 里改好了）/ rejected（驳回，answer 必填）/ reopen（撤销）"""
    body = {"action": action}
    if answer:
        body["answer"] = str(answer)[:200]
    if by:
        body["by"] = str(by)[:80]
    return BA._call(settings, "POST", f"/api/erp/stock-reports/{int(report_id)}", body=body, session=session)


def open_reports_by_item(reports):
    """{(门店号, 条码去前导 0): 报告}（检查清单在判断后面写「门店报库存」）"""
    out = {}
    for r in reports or []:
        if r.get("status") == "open":
            out[(int(r.get("entityCode") or 0), _ean(r.get("ean")))] = r
    return out


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


def update_device(settings, device_id, name=None, store=None, session=None):
    """改名 / 换门店（配对不受影响）；store = 门店号 "1"–"5" / "BMS"，None = 不改"""
    body = {"action": "update"}
    if name is not None:
        body["name"] = str(name).strip()
    if store is not None:
        body["entityCode"] = BA.entity_of(store)
    data = BA._call(settings, "POST", f"/api/erp/scanners/{int(device_id)}", body=body, session=session)
    return data["device"]


def delete_device(settings, device_id, session=None):
    """删掉扫码枪账号（平台只让删已停用的）"""
    BA._call(settings, "POST", f"/api/erp/scanners/{int(device_id)}", body={"action": "delete"}, session=session)


def waiting(devs):
    """还在等配对的扫码枪个数（启用中、还没配对）"""
    return sum(1 for d in devs or [] if d.get("active") and not d.get("paired"))


def sort_devices(devs):
    """在用的在前（门店 → 名称），停用的放最后"""
    import re

    def nat(name):                       # TC22 S.MARCO 2 排在 TC22 S.MARCO 10 前面
        return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", str(name or "").upper()) if p]
    return sorted(devs or [], key=lambda d: (not d.get("active"), d.get("entityCode") is None,
                                             d.get("entityCode") or 0, nat(d.get("name"))))


def pretty_code(code):
    c = str(code or "")
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c


# ===================================================================== 下一期 VOLANTINO（扫码枪上的备货清单）
# 2026-11-27 用户：门店拿着下一期 VOLANTINO 的表去货架前备货 → 「发布目录」时可以导入这一期的名单一起发布；
# 只放 DETERSIVO 能订的（这周批发商报价里有的，volantino_plan.compute 本来就只算这些），直厂的不发；
# 没导入（或这一期已经结束）时扫码枪上写「办公室还没有上传 VOLANTINO」。
# 发的是每家店每个商品「这一期大概要卖几件」（以前几期促销期每天的销量 × 天数；没上过的用平时销量 × 这家店的放大倍数），
# 扫码枪按自己最新的库存和在途算还差几箱，所以每天自动发布时不用重算。
VOL_BASIS = {"hist": 0, "est": 1, "none": 2}


def _vol_path():
    try:
        from settings import app_dir
        return os.path.join(app_dir(), "cache", "scanner_volantino.json")
    except Exception:
        return None


def volantino_rows(res):
    """volantino_plan.compute 的结果 → {门店号: [[条码, 促销价, 预计卖几件, 0 以前上过 / 1 估算 / 2 没销量, 上一期名称, 上一期卖了几件, 要看 0/1]]}"""
    from datetime import date
    out = {}
    for r in res.get("rows") or []:
        last_name, last_sold = "", None
        if r.get("periods"):
            nm, a, b, per_day, _base = r["periods"][0]
            last_name = nm
            try:
                last_sold = round(per_day * ((date.fromisoformat(b) - date.fromisoformat(a)).days + 1))
            except (TypeError, ValueError):
                last_sold = None
        out.setdefault(int(r["store"]), []).append([
            _ean(r["ean"]), _r2(r.get("price")), _r2(r.get("expected")), VOL_BASIS.get(r.get("basis"), 2),
            last_name, last_sold, 1 if r.get("warn") else 0])
    return out


def compute_volantino(settings, path, log=print, today=None):
    """读这一期名单（Excel）+ 以前几期 + 这周报价 + 各门店销量 → 存好、以后每次发布都带上。返回存的内容"""
    import commands as CMD
    import price_check as PC
    import reorder_service as R
    import volantino_plan as VP
    import workspace as W
    new = VP.read_list(path, marks=True)
    new["file"] = path
    if not new.get("start") or not new.get("end"):
        raise ValueError("名单里看不出促销是哪几天（start / end 两列）")
    root = VP.promo_root(settings)
    ws = (settings or {}).get("wholesale_root") or ""
    db = R.make_db((settings or {}).get("db") or None)
    try:
        hist = VP.history(root, new, db, log=log) if root else []
        files = CMD.read_quote_files(settings, W.price_dir(ws), log) if ws and os.path.isdir(W.price_dir(ws)) else {}
        if not files:
            raise ValueError("DETERSIVO 工作文件夹里没有这周的批发商报价（只发批发商能订到的商品，要先有报价）")
        res = VP.compute(db, new, hist, PC.best_quotes(files), {}, None, log=log, today=today, deduct_transit=False)
    finally:
        try:
            db.engine.dispose()
        except Exception:
            pass
    data = {"on": True, "file": path, "name": res.get("name") or os.path.splitext(os.path.basename(path))[0],
            "start": res["start"], "end": res["end"], "days": res["days"], "periods": len(res.get("periods") or []),
            "skipped": len(res.get("skipped") or []), "stores": {str(k): v for k, v in volantino_rows(res).items()}}
    save_volantino(data)
    n = len({r["ean"] for r in res.get("rows") or []})
    log(f"扫码枪 VOLANTINO：{data['name']} {data['start']} – {data['end']}，批发商能订的 {n} 个商品"
        f"（名单里其他 {data['skipped']} 个不发：直厂 / 数据库没有 / 名单标了不订），以前 {data['periods']} 期可以参考")
    return data


def save_volantino(data, path=None):
    path = path or _vol_path()
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False)


def load_volantino(path=None):
    path = path or _vol_path()
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def set_volantino_on(on, path=None):
    """勾选 / 取消「一起发布 VOLANTINO」（取消 = 以后发布不再带，扫码枪上变回「还没有上传」）"""
    d = load_volantino(path)
    if d is None:
        return None
    d["on"] = bool(on)
    save_volantino(d, path)
    return d


def active_volantino(today=None, path=None):
    """正在发布的那一期：勾着、而且还没结束；没有 None"""
    from datetime import date
    d = load_volantino(path)
    if not d or not d.get("on"):
        return None
    today = today or date.today()
    try:
        if date.fromisoformat(d["end"]) < today:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return d


def with_volantino(settings, meta, stores, vol=None, today=None):
    """发布前把 VOLANTINO 带上：meta["volantino"] = {name, start, end, days, items} 或 None（扫码枪显示「还没有上传」）；
    每家店的门店数据行补到第 16 项 = [促销价, 预计卖几件, 依据, 上一期, 上一期卖了几件, 要看]。不改传进来的行（缓存里的）"""
    vol = vol if vol is not None else active_volantino(today)
    meta = dict(meta or {})
    if not vol:
        meta["volantino"] = None
        return meta, stores
    stores = dict(stores or {})
    eans = set()
    for sid, vrows in (vol.get("stores") or {}).items():
        sid = int(sid)
        rows = [list(r) for r in stores.get(sid) or []]
        idx = {r[0]: r for r in rows}
        for v in vrows:
            r = idx.get(v[0])
            if r is None:
                r = [v[0]]
                rows.append(r)
                idx[v[0]] = r
            while len(r) < 15:
                r.append(None)
            r[15:16] = [v[1:]]
            eans.add(v[0])
        stores[sid] = rows
    meta["volantino"] = {"name": vol.get("name") or "", "start": vol.get("start"), "end": vol.get("end"),
                         "days": vol.get("days"), "items": len(eans)}
    return meta, stores
