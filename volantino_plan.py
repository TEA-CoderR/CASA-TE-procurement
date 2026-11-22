# -*- coding: utf-8 -*-
"""
VOLANTINO 备货（2026-11-22 用户：新一期 VOLANTINO 名单（例 MODULO PROMO DIC 26，03/12–20/12）→ 按这些商品以前上 VOLANTINO 时
各门店的销量，算出这一期每家店大约要备多少货，改进五家店的门店清单 DETER1–5）。

用户定：
  ① 只放这周四家批发商报价里有的商品（小家电、玩具等批发商没有的只列出来，不放进清单）；
  ② 够卖整个促销期：以前促销期每天卖几件 × 这一期天数 − 现在库存，按数据库箱规换成箱（至少 1 箱）；
  ③ 以前没上过 VOLANTINO 的：平时每天卖几件 × 这家店的平均放大倍数（同一家店别的商品促销期比平时多卖几倍），标「估算」；
  ④ 写进 DETER1–5 文件：清单里已有的取较大的数（不砍门店写的），没有的加在最后；MOTIVO 列写「VOLANTINO …」
     → 检查清单时照清单订、一定要审核（判断后面「VOLANTINO」）；在途审核时照常扣（这里不扣，免得扣两次）。
     原文件先备份到「1 门店清单\\_旧文件」，可以撤销。

以前的几期：促销文件夹（同步文件夹「5 PROMOZIONE MESILE\\PROMO MESILE」）里每个名单文件（volantino.read_file 认得出商品和日期的），
不算圣诞货（NATALIZIA）、开业促销（APERTURA）、销量报表（Dati Vendite）、订单（Ordine）。文件里没写日期的按数据库促销单
（promozioni）对日期：商品重合最多、至少 30% 的那张。读过的文件记在 cache/volantino/files.json（大小 + 修改时间一样就不再读）。

不依赖界面。数据库只读。
"""
import json
import math
import os
import re
import shutil
from datetime import date, datetime, timedelta

STORE_IDS = (1, 2, 3, 4, 5)
PROMO_SUB = os.path.join("5 PROMOZIONE MESILE", "PROMO MESILE")
SKIP_NAME = re.compile(r"natal|apertura|dati vendite|^ordine|^~|^\$|procedura|programma|新建|scala punti", re.I)
LIST_NAME = re.compile(r"modulo|elenco|volantino|lista|promo", re.I)     # 名单文件名（订单、报价、销量表不算）
MIN_ITEMS = 20               # 文件里少于这么多商品的不当一期 VOLANTINO
HIST_DAYS = 400              # 只看这么多天以内的几期
MAX_PERIODS = 3              # 一个商品用最近几期的平均
BASE_DAYS = 28               # 促销前这么多天算「平时每天卖几件」（放大倍数用）
NORMAL_DAYS = 90             # 现在平时每天卖几件：最近 90 天（去掉这个商品在促销的日子）
LIFT_MIN, LIFT_MAX, LIFT_DEFAULT = 1.0, 5.0, 2.0
LIFT_SAMPLES = 10            # 一家店少于这么多对样本 → 用五家店合起来的放大倍数
DB_MATCH = 0.3               # 没日期的文件和数据库促销单商品重合至少这么多才借它的日期
BIG_LINE_EUR = 1000          # 合理性检查：一家店一个商品 ≥ €1,000 标红
TAG = "VOLANTINO"


_TAG_RE = re.compile(r"VOLANTINO \d{2}/\d{2}-\d{2}/\d{2}")     # reason_text 写的样子（门店写的「Promozione o volantino」不算）


def is_tag(reason):
    """门店清单 MOTIVO 是不是这里写的（检查清单用：VOLANTINO 备货照清单、不标订太多）；门店原因后面接上的也认"""
    return bool(_TAG_RE.search(str(reason or "").upper()))


def promo_root(settings):
    """促销文件夹：设置里写了就用，不然 同步文件夹\\5 PROMOZIONE MESILE\\PROMO MESILE"""
    own = ((settings or {}).get("volantino") or {}).get("promo_root")
    if own and os.path.isdir(own):
        return own
    import sync_paths as SP
    r = SP.root(settings) or SP.find_root() or ""
    p = os.path.join(r, PROMO_SUB) if r else ""
    return p if p and os.path.isdir(p) else ""


def _excel_files(root, depth=4):
    out = []
    if not root or not os.path.isdir(root):
        return out
    base = root.rstrip("\\/").count(os.sep)
    for d, dirs, files in os.walk(root):
        if d.count(os.sep) - base >= depth:
            dirs[:] = []
        for f in files:
            if f.lower().endswith((".xlsx", ".xlsm")) and LIST_NAME.search(f) and not SKIP_NAME.search(f) \
                    and not SKIP_NAME.search(os.path.basename(d)):
                out.append(os.path.join(d, f))
    return out


def newest_list(root):
    """促销文件夹里最近改过的名单文件（选文件窗口默认打开它所在的文件夹）"""
    fs = [f for f in _excel_files(root) if re.search(r"modulo|elenco|volantino", os.path.basename(f), re.I)]
    return max(fs, key=os.path.getmtime) if fs else ""


# ---------------------------------------------------------------- 名单（有缓存）
def _key(ean):
    s = str(ean or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.lstrip("0") if s.isdigit() else ""


def _cache_path():
    try:
        from settings import app_dir
        base = app_dir()
    except Exception:
        base = os.getcwd()
    return os.path.join(base, "cache", "volantino", "files.json")


def _load_cache(path=None):
    try:
        with open(path or _cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(data, path=None):
    p = path or _cache_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        pass


CACHE_VER = 2


_NOT_ORDER = re.compile(r"NON\s*(?:E|È|E')?\s*ORDINABIL", re.I)


def _yellow(fill):
    """格子底色是黄 / 橙黄（FFC000、FFFF00 这种；浅米色 FFF2CC、绿、红都不算）"""
    try:
        if not fill or fill.fill_type != "solid":
            return False
        c = fill.fgColor
        if c is None:
            return False
        if c.type == "theme":
            return c.theme == 7 and (c.tint or 0) <= 0.2          # Office 主题「金色」= FFC000
        if c.type == "indexed":
            return c.indexed in (5, 13, 51, 52)                 # 老调色板的黄 / 金
        rgb = str(c.rgb or "")[-6:]
        r, g, b = int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16)
        return r >= 0xE0 and g >= 0xA0 and b <= 0x80
    except (TypeError, ValueError, AttributeError):
        return False


def not_ordered(path, data):
    """
    这一期名单里标出来不用订的（2026-11-22 用户：名单里标黄的不用订）：条码 / 编号 / 名称格底色是黄色，
    或者这一行写了「NON E ORDINABILE」→ 每个这样的商品 item["skip"] = 原因
    """
    from openpyxl import load_workbook
    rows = {x["row"]: x for x in data["items"] if x.get("row")}
    if not rows or not data.get("sheet"):
        return 0
    cols = data.get("cols") or []
    wb = load_workbook(path, read_only=True)
    n = 0
    try:
        ws = wb[data["sheet"]]
        if hasattr(ws, "reset_dimensions"):
            ws.reset_dimensions()
        for i, row in enumerate(ws.iter_rows(min_row=1, max_row=max(rows)), 1):
            x = rows.get(i)
            if x is None:
                continue
            row = list(row)
            why = None
            if any(_NOT_ORDER.search(str(getattr(c, "value", "") or "")) for c in row):
                why = "NON E ORDINABILE"
            elif any(c < len(row) and _yellow(getattr(row[c], "fill", None)) for c in cols):
                why = "名单里标黄 · 不用订"
            if why:
                x["skip"] = why
                n += 1
    finally:
        wb.close()
    return n


def read_list(path, cache=None, marks=False):
    """一个名单文件 → {"name", "file", "start", "end"（iso 或 None）, "items": [{code, ean, name, price, normal}]}
    marks：这一期的名单，标出不用订的（not_ordered，item["skip"]）"""
    import volantino as V
    st = os.stat(path)
    sig = [CACHE_VER, st.st_size, int(st.st_mtime)]
    if cache is not None and (cache.get(path) or {}).get("sig") == sig:
        return cache[path]["data"]
    p = V.read_file(path)
    data = {"name": p.get("name") or os.path.splitext(os.path.basename(path))[0], "file": path,
            "start": p["start"].isoformat() if p.get("start") else None,
            "end": p["end"].isoformat() if p.get("end") else None,
            "items": [dict(x) for x in p.get("items") or []], "sheet": p.get("sheet"), "cols": p.get("cols") or []}
    if marks:
        not_ordered(path, data)
    if cache is not None:
        cache[path] = {"sig": sig, "data": data}
    return data


def _d(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _db_dates(db, undated, log):
    """没写日期的几期：按数据库促销单（商品重合最多的那张）补日期。改 undated 里的 start / end"""
    if not undated:
        return
    import pandas as pd
    import reorder_service as R
    import volantino as V
    try:
        ps = [p for p in V.db_periods(db, since=date.today() - timedelta(days=HIST_DAYS + 60)) if p.get("n", 0) >= MIN_ITEMS]
    except Exception:
        ps = []
    if not ps:
        return
    ids = [p["promo"] for p in ps]
    ph = ", ".join(["%s"] * len(ids))
    try:
        df = pd.read_sql(f"SELECT promozioni_l AS l, CodArticolo AS c FROM promozioni_list WHERE promozioni_l IN ({ph}) "
                         f"AND (invalid IS NULL OR invalid = 0)", db.engine, params=tuple(ids))
    except Exception as e:
        log(f"⚠ 数据库促销单读不了，没写日期的名单不用：{e}")
        return
    by = {}
    for r in df.to_dict("records"):
        try:
            by.setdefault(int(r["l"]), set()).add(str(int(r["c"])))
        except (TypeError, ValueError):
            continue
    for h in undated:
        eans = [x["ean"] for x in h["items"] if x.get("ean")]
        try:
            codes = set(R.codes_from_eans(db, eans).values()) | {x["code"] for x in h["items"] if x.get("code")}
        except Exception:
            continue
        if not codes:
            continue
        best, share = None, 0.0
        for p in ps:
            s = len(codes & by.get(p["promo"], set())) / len(codes)
            if s > share:
                best, share = p, s
        if best and share >= DB_MATCH:
            h["start"], h["end"] = best["start"], best["end"]
            h["dates_from"] = best["name"]
            log(f"[VOLANTINO 备货] {h['name']}：文件里没写日期，按数据库促销单「{best['name']}」"
                f"（{best['start']} – {best['end']}，商品重合 {share:.0%}）")


def history(root, new, db=None, log=print, cache_path=None, today=None):
    """以前的几期（不含这一期）：[{"name", "file", "start", "end", "eans": set}]，新的在前"""
    today = today or date.today()
    cache = _load_cache(cache_path)
    ns = _d(new.get("start")) or today
    out, undated = [], []
    for f in _excel_files(root):
        if new.get("file") and os.path.normcase(os.path.abspath(f)) == os.path.normcase(os.path.abspath(new["file"])):
            continue
        try:
            h = dict(read_list(f, cache))
        except Exception as e:
            log(f"⚠ 名单读不了，跳过：{os.path.basename(f)}（{e}）")
            continue
        if len(h["items"]) < MIN_ITEMS:
            continue
        (out if h.get("start") else undated).append(h)
    _save_cache(cache, cache_path)
    if undated and db is not None:
        _db_dates(db, undated, log)
    for h in undated:
        if h.get("start"):
            out.append(h)
        else:
            log(f"[VOLANTINO 备货] {h['name']}：看不出是哪几天，不用")
    res, seen = [], set()
    for h in sorted(out, key=lambda h: h["start"], reverse=True):
        s, e = _d(h["start"]), _d(h["end"])
        if not s or not e or s >= ns or (today - s).days > HIST_DAYS or (s, e) in seen:
            continue
        if new.get("start") and h["start"] == new["start"]:
            continue
        seen.add((s, e))
        res.append({"name": h["name"], "file": h["file"], "start": s, "end": e,
                    "eans": {_key(x["ean"]) for x in h["items"] if _key(x.get("ean"))}})
    return res


# ---------------------------------------------------------------- 销量
def _chunks(codes, n=1000):
    codes = [int(c) for c in codes]
    for i in range(0, len(codes), n):
        ch = codes[i:i + n]
        yield ch, ", ".join(["%s"] * len(ch))


def store_open(db, sid):
    import pandas as pd
    try:
        v = pd.read_sql(f"SELECT MIN(Data) AS d FROM vendite_list_{int(sid)}", db.engine)["d"].iloc[0]
        return pd.Timestamp(v).date() if v is not None and str(v) not in ("NaT", "None") else None
    except Exception:
        return None


def period_sales(db, sid, codes, start, end, upto):
    """这一期每个商品卖了几件、促销前 BASE_DAYS 天卖了几件：{编号: (促销期, 促销前)}"""
    import pandas as pd
    out = {}
    e1 = min(end, upto) + timedelta(days=1)
    b0 = start - timedelta(days=BASE_DAYS)
    for ch, ph in _chunks(codes):
        df = pd.read_sql(f"SELECT CodArticolo, SUM(CASE WHEN Data >= %s THEN Qta ELSE 0 END) AS p, "
                         f"SUM(CASE WHEN Data < %s THEN Qta ELSE 0 END) AS b FROM vendite_list_{int(sid)} "
                         f"WHERE CodArticolo IN ({ph}) AND Data >= %s AND Data < %s GROUP BY CodArticolo",
                         db.engine, params=(start, start, *ch, b0, e1))
        for r in df.to_dict("records"):
            out[str(int(r["CodArticolo"]))] = (float(r["p"] or 0), float(r["b"] or 0))
    return out


def daily_sales(db, sid, codes, since):
    """{编号: {日期: 件数}}（最近 NORMAL_DAYS 天）"""
    import pandas as pd
    out = {}
    for ch, ph in _chunks(codes):
        df = pd.read_sql(f"SELECT CodArticolo, DATE(Data) AS d, SUM(Qta) AS q FROM vendite_list_{int(sid)} "
                         f"WHERE CodArticolo IN ({ph}) AND Data >= %s GROUP BY CodArticolo, DATE(Data)",
                         db.engine, params=(*ch, since))
        for r in df.to_dict("records"):
            d = r["d"]
            if isinstance(d, datetime):
                d = d.date()
            elif not isinstance(d, date):
                d = _d(d)
            out.setdefault(str(int(r["CodArticolo"])), {})[d] = float(r["q"] or 0)
    return out


def _median(v):
    v = sorted(v)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def lift_of(pairs):
    """放大倍数：(促销期每天, 平时每天) 两个都 > 0 的中位数，限制在 LIFT_MIN–LIFT_MAX；样本不够 None"""
    r = [p / b for p, b in pairs if p > 0 and b > 0]
    if len(r) < LIFT_SAMPLES:
        return None
    return max(LIFT_MIN, min(LIFT_MAX, _median(r)))


def boxes_for(expected, stock, pack, transit_pcs=0.0):
    """够卖整个促销期要订几箱：(预计 − 现在库存 − 在途件数) ÷ 每箱件数，进上去；要订就至少 1 箱"""
    need = float(expected) - max(0.0, float(stock or 0)) - max(0.0, float(transit_pcs or 0))
    if expected <= 0 or need <= 0:
        return 0
    return max(1, int(math.ceil(need / max(1.0, float(pack or 1)) - 1e-9)))


# ---------------------------------------------------------------- 计算
def compute(db, new, hist, quotes, list_rows, transit=None, stores=STORE_IDS, log=print, progress=None, today=None,
            upto=None, deduct_transit=False):
    """
    new = read_list 的结果（这一期，要有 start / end）；hist = history()；quotes = price_check.best_quotes（这周报价）；
    list_rows = {门店号: parse_order_file 的 rows}（现在清单里写的箱数）；transit = order_record.cached
    → {"days", "rows": [每家店每个商品一行], "skipped": [批发商没有 / 数据库没有的商品], "lift": {门店: 倍数}, "periods": [...]}
    """
    import price_check as PC
    import reorder_service as R
    import order_record as REC
    import pandas as pd
    today = today or date.today()
    upto = upto or (today - timedelta(days=1))
    ps, pe = _d(new.get("start")), _d(new.get("end"))
    if not ps or not pe:
        raise ValueError("这一期名单里看不出促销是哪几天（start / end）")
    days = (pe - ps).days + 1
    say = progress or (lambda *_: None)
    items, skipped, seen = [], [], set()
    for x in new["items"]:
        if x.get("skip"):                                   # 名单里标黄 / NON E ORDINABILE：不用订
            skipped.append({"ean": x.get("ean") or "", "name": x.get("name") or "", "why": x["skip"]})
            continue
        k = _key(x.get("ean"))
        if not k or k in seen:
            if not k:
                skipped.append({"ean": x.get("ean") or "", "name": x.get("name") or "", "why": "名单里没有条码"})
            continue
        seen.add(k)
        q = quotes.get(str(x["ean"]).strip()) or quotes.get(k)
        if not q:
            skipped.append({"ean": x["ean"], "name": x.get("name") or "", "why": "这周批发商报价里没有"})
            continue
        items.append(dict(x, key=k, quote=q))
    say(f"名单 {len(new['items'])} 个商品，批发商有 {len(items)} 个")
    codes = R.codes_from_eans(db, [x["ean"] for x in items])
    for x in list(items):
        c = codes.get(str(x["ean"]).strip()) or x.get("code")
        if not c:
            skipped.append({"ean": x["ean"], "name": x.get("name") or "", "why": "数据库里没有这个商品"})
            items.remove(x)
        else:
            x["code"] = str(c)
    all_codes = sorted({x["code"] for x in items}, key=int)
    prod = {}
    for ch, ph in _chunks(all_codes):
        df = pd.read_sql(f"SELECT CodArticolo, Descrizione, qtasmallbox, PrezzoForn FROM prodotti WHERE CodArticolo IN ({ph})",
                         db.engine, params=tuple(ch))
        for r in df.to_dict("records"):
            prod[str(int(r["CodArticolo"]))] = r
    # 每个商品以前上过哪几期（最近的在前）
    for x in items:
        x["hist"] = [h for h in hist if x["key"] in h["eans"]][:MAX_PERIODS]
        pr = prod.get(x["code"]) or {}
        x["db_name"] = str(pr.get("Descrizione") or "").strip()
        r = {"ean": str(x["ean"]).strip(), "pack": float(pr.get("qtasmallbox") or 0) or None,
             "price": float(pr.get("PrezzoForn") or 0) or None}
        PC.apply_quotes([r], {r["ean"]: dict(x["quote"])})
        x["pack"], x["unit"], x["pack_far"] = float(r.get("pack") or 1), float(r.get("price") or 0), r.get("pack_far")
        x["supplier"] = x["quote"].get("supplier")
    need_p = {}
    for x in items:
        for h in x["hist"]:
            need_p.setdefault(id(h), (h, set()))[1].add(x["code"])
    rows, lifts = [], {}
    pairs_all = []
    per_store = {}
    for n_s, sid in enumerate(stores):
        say(f"查 {R.STORES.get(sid, sid)} 的销量（{n_s + 1} / {len(stores)}）")
        opened = store_open(db, sid)
        sales = {}                                  # (期 id, 编号) → (促销期每天, 平时每天)
        for h, cs in need_p.values():
            if opened and h["start"] < opened + timedelta(days=7):
                continue                            # 那一期这家店还没开（或刚开）：不算
            n_d = (min(h["end"], upto) - h["start"]).days + 1
            if n_d < 3:
                continue
            got = period_sales(db, sid, sorted(cs, key=int), h["start"], h["end"], upto)
            for c in cs:
                p, b = got.get(c, (0.0, 0.0))
                sales[(id(h), c)] = (p / n_d, b / BASE_DAYS, n_d)
        since = upto - timedelta(days=NORMAL_DAYS - 1)
        if opened and opened > since:
            since = opened
        daily = daily_sales(db, sid, all_codes, since)
        try:
            import scanner_catalog as SC
            stock = SC.light_numbers(db, sid, all_codes, today)
        except Exception as e:
            log(f"⚠ {R.STORES.get(sid, sid)} 库存没读到：{e}")
            stock = {}
        pairs = [(v[0], v[1]) for v in sales.values()]
        pairs_all += pairs
        per_store[sid] = (sales, daily, since, stock, pairs)
    lift_all = lift_of(pairs_all) or LIFT_DEFAULT
    for sid in stores:
        sales, daily, since, stock, pairs = per_store[sid]
        lift = lift_of(pairs) or lift_all
        lifts[sid] = round(lift, 2)
        lst = {}
        for r in list_rows.get(sid) or []:
            for k in (("e", _key(r.get("ean"))), ("c", str(r.get("code") or "").strip())):
                if k[1] and k not in lst:
                    lst[k] = r
        n_days = max(1, (upto - since).days + 1)
        for x in items:
            got = [(h, sales.get((id(h), x["code"]))) for h in x["hist"]]
            got = [(h, v) for h, v in got if v is not None]
            # 平时每天：最近 90 天去掉这个商品在促销的日子
            promo_days = set()
            for h in x["hist"]:
                d = h["start"]
                while d <= h["end"]:
                    promo_days.add(d)
                    d += timedelta(days=1)
            dd = daily.get(x["code"], {})
            nd = sum(1 for i in range(n_days) if since + timedelta(days=i) not in promo_days)
            normal = sum(q for d, q in dd.items() if d not in promo_days) / max(1, nd)
            if got and any(v[0] > 0 for _h, v in got):
                per_day = sum(v[0] for _h, v in got) / len(got)
                basis = "hist"
            else:
                per_day = normal * lift
                basis = "est" if normal > 0 else "none"
            expected = per_day * days
            st = (stock.get(x["code"]) or {}).get("stock", 0.0)
            t = REC.lookup(transit, sid, x["ean"], x["code"]) if transit else None
            tb = float(t["colli"]) if t else 0.0
            tp = float(t.get("pcs") or tb * x["pack"]) if t else 0.0
            gross = boxes_for(expected, st, x["pack"])
            net = boxes_for(expected, st, x["pack"], tp)
            lr = lst.get(("e", x["key"])) or lst.get(("c", x["code"]))
            cur = float(lr.get("qty") or 0) if lr else None
            base = []
            if x.get("pack_far"):
                base.append(f"箱规不一致 · 数据库 {x['pack_far']:g} 件")
            if gross and expected > 0 and x["pack"] >= 3 * expected:
                base.append(f"一箱 {x['pack']:g} 件，预计只卖 {expected:.0f} 件")
            rows.append({"store": sid, "code": x["code"], "ean": str(x["ean"]).strip(), "name": x.get("name") or x["db_name"],
                         "db_name": x["db_name"], "price": x.get("price"), "normal_price": x.get("normal"),
                         "basis": basis, "periods": [(h["name"], h["start"].isoformat(), h["end"].isoformat(),
                                                      round(v[0], 3), round(v[1], 3)) for h, v in got],
                         "per_day": round(per_day, 3), "normal": round(normal, 3), "lift": round(lift, 2),
                         "expected": round(expected, 1), "stock": st, "transit": tb, "transit_pcs": tp, "pack": x["pack"],
                         "unit": x["unit"], "supplier": x["supplier"], "list": cur, "in_list": lr is not None,
                         "list_reason": (lr or {}).get("reason") or "",
                         "sugg_gross": gross, "sugg_net": net, "warn_base": base})
    set_deduct(rows, deduct_transit)
    rows.sort(key=lambda r: (r["store"], -r["amount"]))
    return {"days": days, "start": ps.isoformat(), "end": pe.isoformat(), "name": new.get("name"),
            "rows": rows, "skipped": skipped, "lift": lifts, "lift_all": round(lift_all, 2),
            "periods": [(h["name"], h["start"].isoformat(), h["end"].isoformat()) for h in hist], "upto": upto.isoformat()}


def refresh_row(r):
    """改成的箱数变了：重算金额和提醒（合理性检查：金额 ≥ €1,000 标出来）"""
    r["amount"] = round(r["new"] * r["pack"] * r["unit"], 2)
    r["warn"] = ([f"金额 €{r['amount']:,.0f}"] if r["amount"] >= BIG_LINE_EUR else []) + list(r.get("warn_base") or [])


def set_deduct(rows, on):
    """扣不扣在途（2026-11-22 用户要的选项）：建议 = 扣了在途 / 没扣的；人手改过「改成」的行不动"""
    for r in rows:
        r["sugg"] = r["sugg_net"] if on else r["sugg_gross"]
        if not r.get("edited"):
            r["new"] = max(int(math.ceil(r["list"] - 1e-9)) if r.get("list") else 0, r["sugg"])
        refresh_row(r)


def changes(rows):
    """要写进清单的：改成的箱数比清单原来的多 → [行]"""
    return [r for r in rows if r["new"] > (r["list"] or 0)]


NET_TAG = "IN ARRIVO DEDOTTO"


def reason_text(res, deducted=False):
    """写进门店清单 MOTIVO 的话；在途已经扣过 → 后面写「IN ARRIVO DEDOTTO」，审核清单时不再扣一次"""
    d1, d2 = _d(res["start"]), _d(res["end"])
    return f"{TAG} {d1:%d/%m}-{d2:%d/%m}" + (f" · {NET_TAG}" if deducted else "")


def transit_done(reason):
    """门店清单这一行是 VOLANTINO 备货、在途已经扣过了（审核时不再扣）"""
    return is_tag(reason) and NET_TAG in str(reason or "").upper()


# ---------------------------------------------------------------- 写进门店清单
_QTY = re.compile(r"^(quantit|qta|qty|colli|数量|箱数)", re.I)


def _norm(v):
    return re.sub(r"[\s._\-]", "", str(v or "")).lower()


def _locate(ws, fields, header_row):
    """表头行里各字段在第几列（1 起）：{code, ean, name, qty, reason}"""
    head = {}
    for c in range(1, ws.max_column + 1):
        v = ws.cell(header_row, c).value
        if v is None:
            continue
        for f, h in fields.items():
            if f not in head and _norm(v) == _norm(h):
                head[f] = c
    return head


def write_lists(paths, rows, reason, backup_dir=None, now=None):
    """
    paths = {门店号: 清单文件}；rows = changes() 选中的行（每行 store / code / ean / name / new）。
    清单里有的：数量改成 new（只会改大）、MOTIVO 空的写 reason；没有的：加在最后一行下面。
    → undo = [{"path", "backup", "mtime"}]；文件被打开着（~$ 锁文件）抛 PermissionError，什么都不改
    """
    from openpyxl import load_workbook
    import reorder_service as R
    now = now or datetime.now()
    by = {}
    for r in rows:
        by.setdefault(r["store"], []).append(r)
    for sid in by:
        p = paths.get(sid)
        if not p or not os.path.isfile(p):
            raise FileNotFoundError(f"{R.STORES.get(sid, sid)} 的门店清单不在「1 门店清单」里")
        lock = os.path.join(os.path.dirname(p), "~$" + os.path.basename(p))
        if os.path.exists(lock):
            raise PermissionError(f"{os.path.basename(p)} 在 WPS / Excel 里开着，关掉以后再试")
    undo = []
    try:
        for sid, rs in sorted(by.items()):
            p = paths[sid]
            info = R.parse_order_file(p)
            bdir = backup_dir or os.path.join(os.path.dirname(p), "_旧文件")
            os.makedirs(bdir, exist_ok=True)
            stem, ext = os.path.splitext(os.path.basename(p))
            bk = os.path.join(bdir, f"{stem}_{TAG}前_{now:%m%d_%H%M%S}{ext}")
            shutil.copy2(p, bk)
            wb = load_workbook(p)
            ws = wb[info["sheet"]]
            hr = info["header_row"]
            col = _locate(ws, info["fields"], hr)
            if "qty" not in col:
                wb.close()
                raise ValueError(f"{os.path.basename(p)}：找不到数量列")
            if "reason" not in col:
                last = max(c for c in range(1, ws.max_column + 1) if ws.cell(hr, c).value not in (None, "")) \
                    if any(ws.cell(hr, c).value not in (None, "") for c in range(1, ws.max_column + 1)) else 1
                col["reason"] = last + 1
                ws.cell(hr, col["reason"]).value = "MOTIVO"
                src = ws.cell(hr, col["qty"])
                if src.has_style:
                    ws.cell(hr, col["reason"])._style = src._style
            where = {}
            last_row = hr
            for i in range(hr + 1, ws.max_row + 1):
                vals = {f: ws.cell(i, c).value for f, c in col.items()}
                code, ean = R._clean_code(vals.get("code")), R._clean_code(vals.get("ean"))
                if code or ean:
                    last_row = i
                    for k in (("e", _key(ean)), ("c", code)):
                        if k[1] and k not in where:
                            where[k] = i
            for r in rs:
                i = where.get(("e", _key(r["ean"]))) or where.get(("c", str(r["code"])))
                if i:
                    old = ws.cell(i, col["qty"]).value
                    try:
                        oldn = float(str(old).replace(",", ".")) if old not in (None, "") else 0.0
                    except ValueError:
                        oldn = 0.0
                    if r["new"] > oldn:          # 只改大（门店写得更多的照门店的）；改了才写 MOTIVO，门店自己写的原因不盖掉
                        ws.cell(i, col["qty"]).value = int(r["new"])
                        had = str(ws.cell(i, col["reason"]).value or "").strip()
                        if not had:
                            ws.cell(i, col["reason"]).value = reason
                        elif not is_tag(had):   # 门店写的原因留着，后面接上（扣过在途的要让审核知道，不再扣一次）
                            ws.cell(i, col["reason"]).value = f"{had} | {reason}"
                    continue
                last_row += 1
                i = last_row
                for f, c in col.items():
                    tpl = ws.cell(hr + 1, c) if ws.max_row > hr else None
                    if tpl is not None and tpl.has_style:
                        ws.cell(i, c)._style = tpl._style
                if "code" in col:
                    ws.cell(i, col["code"]).value = str(r["code"])
                if "ean" in col:
                    ws.cell(i, col["ean"]).value = str(r["ean"])
                if "name" in col:
                    ws.cell(i, col["name"]).value = r.get("db_name") or r.get("name")
                ws.cell(i, col["qty"]).value = int(r["new"])
                ws.cell(i, col["reason"]).value = reason
            tmp = p + ".casate_tmp"
            wb.save(tmp)
            wb.close()
            os.replace(tmp, p)
            undo.append({"path": p, "backup": bk, "mtime": os.path.getmtime(p), "store": sid, "n": len(rs)})
    except Exception:
        for u in undo:                  # 一半写坏了：已经改的几份换回去
            try:
                shutil.copy2(u["backup"], u["path"])
            except OSError:
                pass
        raise
    return undo


def undo_lists(undo):
    """换回备份；文件在那之后又被改过 / 打开着的不换（返回没换回去的）"""
    left = []
    for u in undo:
        p = u["path"]
        lock = os.path.join(os.path.dirname(p), "~$" + os.path.basename(p))
        if os.path.exists(lock) or (os.path.exists(p) and abs(os.path.getmtime(p) - u["mtime"]) > 2):
            left.append(u)
            continue
        shutil.copy2(u["backup"], p)
    return left


WHY_IT = {"这周批发商报价里没有": "Non nei listini dei grossisti", "数据库里没有这个商品": "Non nel database",
          "名单里没有条码": "Senza codice a barre", "名单里标黄 · 不用订": "Evidenziato in giallo · da non ordinare",
          "NON E ORDINABILE": "Non è ordinabile"}


def export(res, path):
    """计算结果导出 Excel（每家店一页 + 批发商没有的）"""
    import xlsx_report as XR
    import reorder_service as R
    wb = XR.new_book()
    cols = [XR.Col("EAN", 15, "ean", "id"), XR.Col("Cod.Art.", 10, "txt", "id"), XR.Col("Descrizione", 40, "txt", "id"),
            XR.Col("Base", 12, "txt", "cat"), XR.Col("Pz/giorno promo", 11, "txt", "cat"),
            XR.Col("Previsti", 10, "num", "cat"), XR.Col("Giacenza", 10, "num", "price"), XR.Col("In arrivo (colli)", 10, "num", "price"),
            XR.Col("Pz/collo", 9, "num", "price"), XR.Col("Lista (colli)", 10, "num", "sup"), XR.Col("Proposta (colli)", 11, "num", "sup"),
            XR.Col("Nuovo (colli)", 11, "num", "sup"), XR.Col("Importo €", 11, "price", "sup"), XR.Col("Note", 30, "txt", "warn")]
    basis = {"hist": "Storico promo", "est": "Stima", "none": "Nessuna vendita"}
    sub = f"{res['name']} · {res['start']} – {res['end']} ({res['days']} gg)"
    for sid in STORE_IDS:
        rs = [r for r in res["rows"] if r["store"] == sid]
        if not rs:
            continue
        data = [[r["ean"], r["code"], r["name"], basis[r["basis"]], f"{r['per_day']:.1f}", r["expected"], r["stock"], r["transit"],
                 r["pack"], r["list"] if r["list"] is not None else "", r["sugg"], r["new"], r["amount"], " · ".join(r["warn"])]
                for r in rs]
        marks = {(i, 11): "GREEN" for i, r in enumerate(rs) if r["new"] > (r["list"] or 0)}
        marks.update({(i, 12): "RED" for i, r in enumerate(rs) if r["amount"] >= BIG_LINE_EUR})
        XR.add_sheet(wb, R.STORES.get(sid, str(sid)), f"VOLANTINO – {R.STORES.get(sid, sid)}", sub, cols, data, marks=marks,
                     photo_eans=[r["ean"] for r in rs])
    if res["skipped"]:
        XR.add_sheet(wb, "Non dai grossisti", "Articoli non inseriti", sub,
                     [XR.Col("EAN", 15, "ean", "id"), XR.Col("Descrizione", 50, "txt", "id"), XR.Col("Motivo", 30, "txt", "warn")],
                     [[s["ean"], s["name"], WHY_IT.get(s["why"], s["why"])] for s in res["skipped"]])
    wb.save(path)
    return path
