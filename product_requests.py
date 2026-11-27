# -*- coding: utf-8 -*-
"""
门店申请（2026-11-27 用户：扫码枪上 SCONOSCIUTO 这种只叫门店「去问办公室」→ 做一个列表，门店在扫码枪上直接申请，
办公室在程序里处理、回复（换新条码 / 不做了 / 其他原因），不用每个都打电话）。不依赖界面。

流程：
  门店（扫码枪）：查不到的商品、Griglia 已停用的、批发商都没货的 → 「Chiedi all'ufficio」：为什么要（顾客在问 /
    替代卖完的商品 / 供应商新品 / 其他）· 想订几箱（可以不填）· 备注 · 可以拍一张照片 → 存在预约平台
    （/api/scanner/requests）。同一家店同一个条码只有一张没处理的申请，再申请 = 改那一张。
  办公室（这里）：每个条码一行（几家店问的合在一起），程序先查好：数据库有没有、这周哪家批发商有、多少钱、哪家直厂有、
    和 Griglia 里哪个商品最像 → 一个建议；办公室选一种回复，程序写一句意大利文给门店，门店扫码枪上看得到：
      use     换成别的商品（多半是 Griglia 里某个商品换了新条码）：以后再扫这个条码，扫码枪显示「SOSTITUITO → 那个商品」
      griglia 加进 Griglia（程序里的 Griglia 直接新加，和「新增商品」一样过检查、能撤销）
      once    只订这一次（算非 Griglia 商品）
      direct  向直厂订
      no      不做这个商品（写原因）
      info    要更多信息（门店在扫码枪上补一句 / 补照片，申请回到待回复）
    use / griglia / once 可以把门店要的箱数直接放进这家店这周的扫码枪清单（用户 2026-11-27 定：自动加进清单）。
    箱数 / 金额照「数量 / 金额合理性检查」：金额 ≥ BIG_LINE_EUR 或箱数 ≥ MAX_BOXES 不让直接放进清单，要先改小。
  撤销 = reopen：回复去掉、放进清单的箱数改回原来的。
"""
from datetime import datetime, timezone

import booking_api as BA

REASON_ZH = {"customers": "顾客在问", "replaces": "替代卖完的商品", "new": "供应商新品", "other": "其他"}
ITEM_ZH = {"unknown": "查不到的商品", "off": "Griglia 已停用", "noquote": "批发商都没货"}
ANSWER_ZH = {"use": "换成别的商品", "griglia": "加进 Griglia", "once": "只订这一次", "direct": "向直厂订",
             "no": "不做这个商品", "info": "要更多信息"}
STATUS_ZH = {"open": "待回复", "info": "等门店补充", "answered": "已回复", "cancelled": "门店已撤回"}
LIST_KINDS = ("use", "griglia", "once")          # 这几种可以把箱数放进门店清单

# 不做的原因（中文给办公室看，意大利文发给门店）
NO_REASONS = [("不在我们的品类里", "Non rientra nel nostro assortimento"),
              ("供应商都没有", "Nessun fornitore ce l'ha"),
              ("价格太高", "Il prezzo è troppo alto"),
              ("季节性 / 已经停产", "È stagionale o fuori produzione")]
# 要更多信息：常问的几句
INFO_ASKS = [("拍一张条码和标签", "Manda una foto dell'etichetta e del codice a barre"),
             ("写清楚是什么", "Scrivi cos'è: marca, tipo e formato"),
             ("顾客要多少", "Quanti pezzi chiedono i clienti?")]

LATE_DAYS = 2            # 超过 2 个工作日没回复 = 红色
MAX_BOXES = 20           # 一次放进清单最多几箱（再多先改小 / 写清楚）
WARN_BOXES = 6           # 超过这个数确认时黄字提醒
SURE = 0.8               # 和 Griglia 某个商品相似度这么高：多半是同一个商品换了条码

STORE_NAMES = {1: "FOIANO", 2: "MARLIA", 3: "S.FILIPPO", 4: "S.MARCO", 5: "S.CONCORDIO", 0: "BMS"}
STORE_CODES = (1, 2, 3, 4, 5)


def _ean(v):
    s = "".join(ch for ch in str(v or "") if ch.isdigit())
    return s.lstrip("0") or s


def store_name(code):
    try:
        return STORE_NAMES.get(int(code), str(code))
    except (TypeError, ValueError):
        return str(code or "")


# ------------------------------------------------------------------ 预约平台
def fetch(settings, status="open,info,answered", days=60, session=None):
    """→ [申请]（每个门店一条；不带照片，hasPhoto 说有没有）"""
    data = BA._call(settings, "GET", "/api/erp/requests", params={"status": status or "all", "days": int(days)},
                    session=session)
    return data.get("requests") or []


def photo(settings, rid, session=None):
    """门店拍的照片（JPEG 字节）；没有 None"""
    import requests
    tok = BA.token()
    if not tok:
        return None
    s = session or requests
    try:
        r = s.get(BA.base_url(settings) + f"/api/erp/requests/{int(rid)}/photo", timeout=BA.TIMEOUT,
                  headers={"Authorization": "Bearer " + tok})
    except Exception:
        return None
    return r.content if r.status_code == 200 and r.content[:2] == b"\xff\xd8" else None


def answer(settings, rid, kind, text, ean=None, name=None, boxes=None, by=None, session=None):
    if kind not in ANSWER_ZH:
        raise ValueError(kind)
    body = {"action": "answer", "kind": kind, "answer": str(text or "").strip()[:300]}
    if ean:
        body["ean"] = _ean(ean)
    if name:
        body["name"] = str(name)[:120]
    if boxes:
        body["boxes"] = int(boxes)
    if by:
        body["by"] = str(by)[:80]
    return BA._call(settings, "POST", f"/api/erp/requests/{int(rid)}", body=body, session=session)


def reopen(settings, rid, session=None):
    """撤销回复：申请回到待回复，放进清单的箱数改回原来的"""
    return BA._call(settings, "POST", f"/api/erp/requests/{int(rid)}", body={"action": "reopen"}, session=session)


# ------------------------------------------------------------------ 整理
def _t(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def waited_days(created, now=None):
    """申请以后过了几个工作日（周一到周五）"""
    import order_tracking as OT
    c = _t(created)
    if c is None:
        return 0
    now = now or datetime.now(timezone.utc)
    return OT.workdays_between(c.astimezone().date(), now.astimezone().date())


def group(reqs, now=None, split=False):
    """
    每个条码一行：几家店问的合在一起；split = 每家门店单独一行（2026-11-28 用户：门店的请求要每家店分开处理，
    回复只发给这一家）。→ [{key, ean, name, kinds, reqs, open, status, boxes, stores, first, waited, late}]
    status：有门店在等回复 open > 等门店补充 info > answered；待回复的排前面，等得久的在前
    """
    by = {}
    for r in reqs or []:
        if r.get("status") == "cancelled":
            continue
        k = (int(r.get("entityCode") or 0), _ean(r.get("ean"))) if split else _ean(r.get("ean"))
        by.setdefault(k, []).append(r)
    out = []
    for key, rs in by.items():
        e = key[1] if split else key
        rs = sorted(rs, key=lambda r: r.get("createdAt") or "")
        opn = [r for r in rs if r.get("status") == "open"]
        info = [r for r in rs if r.get("status") == "info"]
        st = "open" if opn else ("info" if info else "answered")
        cur = opn or info or rs
        first = min((r.get("createdAt") or "") for r in cur)
        waited = waited_days(first, now) if st == "open" else 0
        out.append({"key": f"{key[0]}|{e}" if split else e, "ean": e, "name": next((r.get("name") for r in rs if r.get("name")), ""),
                    "kinds": sorted({r.get("kind") or "unknown" for r in rs}), "reqs": rs,
                    "open": opn + info, "status": st, "boxes": sum(int(r.get("boxes") or 0) for r in opn),
                    "stores": sorted({int(r.get("entityCode") or 0) for r in (opn + info or rs)}),
                    "first": first, "waited": waited, "late": st == "open" and waited >= LATE_DAYS,
                    "last": max((r.get("handledAt") or r.get("updatedAt") or "") for r in rs)})
    rank = {"open": 0, "info": 1}
    todo = sorted((g for g in out if g["status"] != "answered"), key=lambda g: (rank[g["status"]], -g["waited"], g["first"]))
    done = sorted((g for g in out if g["status"] == "answered"), key=lambda g: g["last"], reverse=True)
    return todo + done


def waiting_count(reqs, store=None):
    """待回复的个数（图标栏 / DETERSIVO 第 1 步的提醒）：每家店每个商品算一个；store = 只算这家店"""
    return len({(int(r.get("entityCode") or 0), _ean(r.get("ean"))) for r in reqs or []
                if r.get("status") == "open" and (store is None or int(r.get("entityCode") or 0) == int(store))})


def aliases(reqs):
    """回复「换成别的商品」的：{门店扫的条码: (该订的条码, 名称)}——扫码枪目录里这个条码显示 SOSTITUITO"""
    out = {}
    for r in sorted(reqs or [], key=lambda r: r.get("handledAt") or ""):
        if r.get("status") == "answered" and r.get("answerKind") == "use" and r.get("answerEan"):
            out[_ean(r["ean"])] = (_ean(r["answerEan"]), r.get("answerName") or "")
    return out


# ------------------------------------------------------------------ 程序先查好
def similar(name, ean, grid_items, top=3):
    """Griglia 里最像的几个商品 [{ean, name, score}]（名字 + 规格 + 厂家条码前缀，和找替代商品同一套算法）"""
    import griglia_replace as GR
    name = str(name or "").strip()
    if not name or not grid_items:
        return []
    names = [str((x or {}).get("name") or "") for x in grid_items.values()]
    idf = GR._Idf(names)
    mine = set(GR._words(name))
    out = []
    for e, x in grid_items.items():
        n = str((x or {}).get("name") or "")
        if not n or not mine & set(GR._words(n)):
            continue
        sc = GR.score(name, n, _ean(ean), _ean(e), idf)[0]
        if sc >= GR.MIN_SCORE:
            out.append({"ean": _ean(e), "name": n, "score": round(min(sc, 1.0), 3)})
    out.sort(key=lambda c: -c["score"])
    return out[:top]


def checks(g, grid=None, best=None, scheda=None, db=None):
    """
    一行申请程序查到的：grid = griglia.load 的结果，best = price_check.best_quotes 的格式（{条码: {price, pack, supplier}}，
    报价每家 offers = {条码: [(批发商, 件价, 每箱)]} 可以放在 best["_offers"]），scheda = 直厂索引，
    db = {条码: {code, name, pack, direct, sales: {门店: 30 天销量}}}。
    → {grid, offers, direct, db, name, similar, suggest, why}
    """
    e = g["ean"]
    grid = grid or {}
    items = grid.get("items") or {}
    struck = grid.get("struck") or {}
    gi = {_ean(k): v for k, v in items.items()}
    gs = {_ean(k) for k in struck}
    st = "griglia" if e in gi else ("off" if e in gs else "")
    offers = sorted(((best or {}).get("_offers") or {}).get(e) or [], key=lambda o: o[1])
    d = (db or {}).get(e) or {}
    direct = ((scheda or {}).get("ean") or {}).get(e) or d.get("direct") or ""
    name = g.get("name") or d.get("name") or (offers and (best or {}).get(e, {}).get("desc")) or ""
    note = " ".join(str(r.get("note") or "") for r in g["reqs"])
    sim = [s for s in similar(name or note, e, gi) if s["ean"] != e]
    if sim and sim[0]["score"] >= SURE:
        sug, why = "use", f"和 Griglia 里「{sim[0]['name']}」相似度 {sim[0]['score']:.0%}：多半是换了新条码"
    elif st == "griglia" and offers:
        sug, why = "once", "Griglia 里有、这周批发商也有了"
    elif offers and st != "griglia":
        sug, why = "griglia", f"这周 {len(offers)} 家批发商有，最便宜 €{offers[0][1]:.2f}"
    elif direct:
        sug, why = "direct", f"直厂 {direct} 有"
    elif not d and not offers:
        sug, why = "info", "数据库、报价、直厂都查不到"
    else:
        sug, why = "no", "这周批发商都没有"
    return {"grid": st, "offers": offers, "direct": direct, "db": d, "name": name, "similar": sim,
            "suggest": sug, "why": why}


def offers_index(quotes):
    """{批发商: {条码: q}} → {条码: [(批发商, 件价, 每箱)]}（只算有价格的）"""
    out = {}
    for sup, qs in (quotes or {}).items():
        for e, q in (qs or {}).items():
            try:
                p = float((q or {}).get("price") or 0)
            except (TypeError, ValueError):
                p = 0
            if p > 0:
                try:
                    pk = float(q.get("pack") or 0) or None
                except (TypeError, ValueError):
                    pk = None
                out.setdefault(_ean(e), []).append((sup, p, pk))
    return out


# ------------------------------------------------------------------ 合理性检查（数量 / 金额）
def box_check(boxes, pack=None, price=None):
    """
    放进门店清单前：→ (级别, 说明)。级别 None = 没问题，"warn" = 确认时黄字提醒，"block" = 不让放（先改小）。
    门店可能把件数写成箱数：箱数 ≥ MAX_BOXES 或金额 ≥ BIG_LINE_EUR 一律挡住。
    """
    import service as S
    try:
        b = int(boxes or 0)
    except (TypeError, ValueError):
        return "block", "箱数不是整数"
    if b <= 0:
        return None, ""
    amt = b * float(pack or 0) * float(price or 0) if pack and price else None
    big = getattr(S, "BIG_LINE_EUR", 1000)
    if b >= MAX_BOXES or (amt is not None and amt >= big):
        return "block", (f"{b} 箱" + (f" ≈ €{amt:,.0f}" if amt is not None else "") + "：太多了，可能把件数写成了箱数，先改小")
    if b >= WARN_BOXES or (amt is not None and amt >= big * 0.3):
        return "warn", (f"{b} 箱" + (f" ≈ €{amt:,.0f}" if amt is not None else "") + "：比平时多，确认没写错")
    return None, (f"≈ €{amt:,.2f}" if amt is not None else "")


# ------------------------------------------------------------------ 发给门店的话（意大利文）
def message(kind, name="", boxes=None, reason="", supplier=""):
    """办公室选了回复以后，程序先写好的一句（可以改）"""
    put = f" Messo in lista: {boxes} {'collo' if boxes == 1 else 'colli'}." if boxes else ""
    if kind == "use":
        return f"Ordina al suo posto: {name}.{put}".strip()
    if kind == "griglia":
        return f"Aggiunto in Griglia: da ora si ordina come gli altri.{put}"
    if kind == "once":
        return f"Ok, lo ordiniamo questa settimana.{put}"
    if kind == "direct":
        who = f" {supplier.strip()}" if str(supplier or "").strip() else ""
        return f"Si ordina dal fornitore diretto{who}, con l'ordine diretto del negozio."
    if kind == "no":
        return f"Non lo trattiamo: {reason or 'non rientra nel nostro assortimento'}."
    if kind == "info":
        return reason or INFO_ASKS[0][1]
    return ""


def by_name():
    """回复上写的「谁回复的」：订单跟踪里的名字 / 用户名"""
    try:
        from settings import load_settings
        s = load_settings()
        return (s.get("tracking") or {}).get("me") or s.get("user_name") or ""
    except Exception:
        return ""
