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
    for e, sup in ((scheda or {}).get("ean") or {}).items():
        k = _ean(e)
        if not k or not sup:
            continue
        if sup not in d_idx:
            d_idx[sup] = len(directs)
            directs.append(sup)
        get(k)["d"] = d_idx[sup]
    # 2026-11-21 第 8–10 项：每箱件数 · 这周批发商最便宜件价 · 最便宜那家（下标）——门店数据没算到也看得到箱规和价格
    rows = [[e, x["name"][:80], x["g"], sorted(x["w"]), x["s"], x["sn"][:80], x["d"],
             x["pk"], _r2(x["p"]), x["ps"]]
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


def publish(settings, meta, rows, by, session=None, progress=None, stores=None):
    """分几次上传，最后一次服务器合成新目录。stores = {门店号: 门店数据行}（每家店只发给它自己的扫码枪）。返回 {"id", "items"}"""
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
    res = publish(settings, meta, rows, by, session=session, stores=stores)
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
    meta, rows = build(grid, quotes, subs, direct_index(settings, log), week=date.today().strftime("%G-W%V"))
    return (meta, rows, quotes) if with_quotes else (meta, rows)


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


def store_row(ean, m, transit_boxes, cost, price, promo, sys_box=None):
    """一个商品在一家店的数据行（扫码枪 StoreInfo 的顺序）：
    [条码, 库存, 30 天销量, 在途箱数, 建议箱数, 进价, 卖价, 打折价, 打折到, 每箱件数, 几天没卖, 库存可能不准 0/1]
    m = 门店数字 {stock, s30, s90, last_days, pack}；sys_box = 检查清单算法算的箱数（只有 Griglia 商品有）"""
    pack = _r2(m.get("pack")) if m.get("pack") else None
    sugg = None if sys_box is None else max(0, int(sys_box) - int(transit_boxes or 0))
    last = m.get("last_days")
    return [ean, _r2(m.get("stock")), _r2(m.get("s30")), _r2(transit_boxes or 0), sugg,
            _r2(cost), _r2(price), _r2((promo or {}).get("price")), _promo_end(promo), pack,
            None if last is None else int(last),
            1 if stock_suspect(m.get("stock"), m.get("s30"), m.get("s90"), last) else 0]


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
    import pandas as pd
    from datetime import date, timedelta
    today = today or date.today()
    sid = int(sid)
    out = {str(c): {"stock": 0.0, "s30": 0.0, "s90": 0.0, "last_days": None} for c in codes}
    d30, d90, d365 = (today - timedelta(days=k) for k in (30, 90, 365))
    for chunk, ph in _chunks(codes):
        df = pd.read_sql(f"SELECT CodArticolo, G{sid} AS g FROM giacenza WHERE CodArticolo IN ({ph})", db.engine,
                         params=tuple(chunk))
        for r in df.to_dict("records"):
            out[str(int(r["CodArticolo"]))]["stock"] = float(r["g"] or 0)
        df = pd.read_sql(f"SELECT CodArticolo, SUM(CASE WHEN Data >= %s THEN Qta ELSE 0 END) AS q30, SUM(Qta) AS q90 "
                         f"FROM vendite_list_{sid} WHERE CodArticolo IN ({ph}) AND Data >= %s GROUP BY CodArticolo",
                         db.engine, params=(d30, *chunk, d90))
        for r in df.to_dict("records"):
            x = out[str(int(r["CodArticolo"]))]
            x["s30"], x["s90"] = float(r["q30"] or 0), float(r["q90"] or 0)
        df = pd.read_sql(f"SELECT CodArticolo, MAX(Data) AS d FROM vendite_list_{sid} WHERE CodArticolo IN ({ph}) "
                         f"AND Qta > 0 AND Data >= %s GROUP BY CodArticolo", db.engine, params=(*chunk, d365))
        for r in df.to_dict("records"):
            out[str(int(r["CodArticolo"]))]["last_days"] = _days(r["d"], today)
    return out


def store_data(settings, rows, quotes, log=print, stores=STORE_IDS):
    """
    rows = build 的目录行。每家店：目录里**所有在数据库里的商品**（Griglia 的、不在 Griglia 只能紧急订的、直厂的）
    都给 库存 / 销量 / 最后卖出 / 进价 / 卖价 / 打折价（light_numbers，三条查询）；
    Griglia 商品 + 替代商品另外按检查清单的算法算建议箱数（compute_suggestions，慢）。
    → {门店号: [门店数据行]}。查数据库（只读），出错的门店跳过（日志里写）。
    """
    import check_rules as CR
    import commands as CMD
    import order_record as REC
    import reorder_service as R
    from datetime import date
    want_grid = {r[0] for r in rows if r[2] in (1, 2)} | {r[4] for r in rows if r[4]}
    want = sorted({r[0] for r in rows} | want_grid)
    if not want:
        return {}
    p = CMD.check_params(settings)
    db = R.make_db(settings.get("db") or None)
    try:
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
            import pandas as pd
            for chunk, ph in _chunks(code_list):
                df = pd.read_sql(f"SELECT CodArticolo, PrezzoVedi1, PrezzoForn, qtasmallbox FROM prodotti "
                                 f"WHERE CodArticolo IN ({ph})", db.engine, params=tuple(chunk))
                for r in df.to_dict("records"):
                    prod[str(int(r["CodArticolo"]))] = r
        except Exception as e:
            log(f"⚠ 扫码枪门店数据：卖价 / 进价没读到（{e}）")
        today = date.today()

        def one(sid):
            nums = light_numbers(db, sid, code_list, today)
            sys_box = {}
            if grid_codes:
                df = R.compute_suggestions(db, sid, grid_codes, p["days"], p["cycle"], None,
                                           use_supplier_cycles=p["use_cfg"], algo=p["algo"])
                res = R.to_rows(df, [], p["days"])
                for x in res:
                    x["file_qty"] = 0                  # 门店还没说要：只要系统自己算的箱数（不套「门店点名要」的保护）
                CR.init_boxes(res, None, p["algo"], settings.get("reorder") or {})
                sys_box = {x["code"]: x.get("sys_box") for x in res}
            try:
                promos = R.query_promos(db, sid, code_list)
            except Exception:
                promos = {}
            out = []
            for code in code_list:
                ean = by_code[code]
                pr = prod.get(code) or {}
                m = dict(nums.get(code) or {}, pack=pr.get("qtasmallbox"))
                t = REC.lookup(transit, sid, ean, code) if transit else None
                tb = int(t["colli"]) if t else 0
                out.append(store_row(ean, m, tb, cheapest(quotes, ean) or pr.get("PrezzoForn"), pr.get("PrezzoVedi1"),
                                     promos.get(code), sys_box.get(code)))
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
        return out
    finally:
        try:
            db.engine.dispose()
        except Exception:
            pass


def _store_cache_path():
    try:
        from settings import app_dir
        return os.path.join(app_dir(), "cache", "scanner_store_data.json")
    except Exception:
        return None


def cached_store_data(settings, rows, quotes, log=print, force=False, path=None, now=None):
    """门店数据 STORE_MAX_AGE 内算过就用上次的（Griglia 页每次刷新都发布，不用每次都查几分钟数据库）"""
    import time
    path = path or _store_cache_path()
    now = now or time.time()
    if path and not force and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                c = json.load(f)
            if now - float(c.get("at") or 0) < STORE_MAX_AGE and c.get("data"):
                return {int(k): v for k, v in c["data"].items()}
        except (OSError, ValueError):
            pass
    t0 = time.time()
    data = store_data(settings, rows, quotes, log)
    log(f"扫码枪门店数据：{len(data)} 家门店，用了 {time.time() - t0:.0f} 秒")
    if path and data:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            json.dump({"at": now, "data": {str(k): v for k, v in data.items()}}, f)
    return data


# ===================================================================== 门店清单（扫码枪做的）
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期天")
KIND_TEXT = {"griglia": "", "sub": "SOSTITUTO", "urgent": "URGENZA", "remind": "DA RICORDARE", "other": ""}


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
    多两列：URGENZA（紧急的写 SI）、MOTIVO（紧急原因 / 替代了哪个商品），检查清单里会显示。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "GRIGLIA"
    ws.append(["Codice EAN", "Descrizione", "Colli", "URGENZA", "MOTIVO", "Tipo", "Inserito da"])
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
    for x in rows:
        e = _ean(x.get("ean"))
        if e:
            final[e] = final.get(e, 0) + int(x.get("fbox") or 0)
        rp = x.get("repl") or {}
        old = _ean(rp.get("old_ean") if isinstance(rp, dict) else "")
        if old and old not in final:
            final[old] = int(x.get("fbox") or 0)
    lines = {}
    for e, asked in (list_lines or {}).items():
        if asked <= 0:
            continue
        got = final.get(_ean(e), 0)
        lines[e] = {"boxes": got}
        if got < asked:
            lines[e]["note"] = "Non ordinato" if got <= 0 else f"Ridotto da {asked} a {got}"
    return {"lines": lines}


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


def pretty_code(code):
    c = str(code or "")
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c
