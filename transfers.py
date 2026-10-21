"""
门店之间调货：要订的量远小于一箱时，从别的门店调多出来的货，少订一箱。

例：FOIANO 只要 3 件，但一箱 12 件，订一整箱会多出 9 件；S.MARCO 库存 30 件、一个周期只卖 10 件，
多出 20 件 → 从 S.MARCO 调 3 件给 FOIANO，这一箱就不用订了。

规则（设置在 settings.json → store_orders.transfer）：
  · 调出门店留够自己 keep_cycles 个订货周期的销量（日均 × 订货周期天数 × keep_cycles，默认 1），多出来的才能调；
    自己这次也要订这个商品的门店不调出
  · 触发条件 mode：
      "waste" 整箱会多出很多时（默认）：订整箱多出的件数 ≥ 一箱的 waste_ratio（默认一半），
              就调零头（要的件数 − 少订一箱后还能到的件数），少订一箱
      "all"   所有要订的商品：别的门店多出来的够多少就调多少，能少订几箱就少订几箱
  · 每个商品要调的货值（调货件数 × 件价）≥ item_min_value（默认 €10）才调：拣货的人工是按商品算的，
    一个 €0.10 的纸杯调 5 个才 €0.50，不值得；调 100 个 €10 就值得
  · 可选：一家门店调出去的货合计 ≥ min_value 才调（默认 0 = 不限；以前的版本只有这一条，默认 €50）
  · 不参与调货的门店（transfer.exclude_stores；没设过时沿用借货的 far_stores）不调出、也不往那里调
算法（2026-10-21 起）：每个商品从多出来最多的那家门店调（货值大的商品先分）；
     一家调出门店合计不够 min_value 的整批不从它调，再看别的门店。
审核时人工改了调货（门店 + 件数）以后，所有调货冻结（freeze）：只按人工定的和冻结的建议调，改一行不会影响别的行。
"""
import math

DEFAULT = {"enabled": True, "mode": "waste", "waste_ratio": 0.5, "item_min_value": 10.0, "min_value": 0.0,
           "keep_cycles": 1.0}


def config(cfg=None):
    """
    调货设置。旧设置（只有「每家调出门店合计 min_value」、没有 item_min_value）：
    改成按单个商品的货值算，旧的合计门槛不再用（规则变了，不沿用以前的 €50）
    """
    cfg = dict(cfg or {})
    if "item_min_value" not in cfg:
        cfg.pop("min_value", None)
    return dict(DEFAULT, **cfg)


def excluded_stores(cfg, fallback=()):
    """不参与调货的门店：调货设置里的 exclude_stores（和借货分开设）；以前没设过 → 沿用借货的远店名单"""
    cfg = cfg or {}
    if "exclude_stores" in cfg:
        return [int(x) for x in cfg.get("exclude_stores") or []]
    return [int(x) for x in fallback or []]


def surplus(d, keep=1.0):
    """这家店多出来、可以调出的件数：库存 − 日均 × 订货周期 × keep（留够自己 keep 个周期，默认 1）。负库存当 0"""
    if not d:
        return 0
    inv = max(0.0, float(d.get("inv") or 0))
    daily = max(0.0, float(d.get("daily") or 0))
    cycle = float(d.get("cycle") or 30)
    keep = 1.0 if keep is None else max(0.0, float(keep))
    return max(0, int(math.floor(inv - daily * cycle * keep + 1e-9)))


def options(need, pack, colli, mode="waste", ratio=0.5):
    """
    一个要订的商品可以怎么用调货少订：[(调货件数, 调货后订几箱)]，越靠前少订得越多。
      need 真正要的件数；pack 每箱件数；colli 现在要订的箱数
    """
    need, pack, colli = float(need or 0), float(pack or 1), int(colli or 0)
    if colli <= 0 or need <= 0 or pack <= 0:
        return []
    if mode == "all":
        out = []
        for c2 in range(0, colli):
            t = need - c2 * pack
            if t > 0:
                out.append((int(math.ceil(t - 1e-9)), c2))
        return out
    over = colli * pack - need                      # 订整箱多出来的件数
    if over <= 0 or over < ratio * pack - 1e-9:
        return []
    t = need - (colli - 1) * pack                   # 少订一箱后还差的零头
    if t <= 0:
        return [(0, colli - 1)] if colli - 1 >= 0 else []
    return [(int(math.ceil(t - 1e-9)), colli - 1)]


def norm_override(v):
    """人工改的调货：旧格式只有件数（int）；新格式 {"from": 调出门店或 None, "pieces": 件数, "auto": 是不是系统建议冻结下来的}"""
    if isinstance(v, dict):
        return {"from": v.get("from"), "pieces": int(v.get("pieces") or 0), "auto": bool(v.get("auto"))}
    return {"from": None, "pieces": int(v or 0), "auto": False}


def plan(needs, avail, cfg=None, far=(), overrides=None, stock=None, auto=True):
    """
    needs: [{"id", "store", "key"(商品), "need", "pack", "colli", "price"(件价), ...}]
    avail: {(门店, 商品): 可调出件数}（多出来的，见 surplus）
    overrides: {need id: 人工定的调货}（审核时改的，int 或 norm_override 的 dict）：
               件数 0 = 这个商品不调（比如过季品）；
               >0 + 指定调出门店 = 就从这家调这么多（不看比例、门槛；只要那家店有这么多库存 stock）；
               >0 没指定门店 = 从多出来最多的那家调（多出来的不够就调不了）
    stock: {(门店, 商品): 库存件数}：人工指定门店时最多调这么多（None = 不限）
    auto:  True = 没人工定的商品由系统建议；False = 只按人工定的调（审核时改过以后，别的行不再跟着变）
    系统建议：每个商品从「多出来最多」的那家门店调（同一个商品别的门店也要的，按货值大的先分）；
             单个商品货值 ≥ item_min_value；一家调出门店合计 ≥ min_value（默认 0 = 不限），
             不够的那家整批不调，它的商品再看别的门店能不能调
    返回 (transfers, new_colli)
      transfers: [{"need_id", "from", "to", "key", "pieces", "value", "colli_before", "colli_after", "manual"}]
      new_colli: {need_id: 调货后要订的箱数}
    """
    cfg = config(cfg)
    far = set(far or ())
    min_value = float(cfg.get("min_value") or 0)
    item_min = float(cfg.get("item_min_value") or 0)
    left0 = {k: int(v) for k, v in (avail or {}).items() if v and v > 0}
    overrides = {k: norm_override(v) for k, v in (overrides or {}).items()}
    transfers, new_colli = [], {}

    def add(n, s, t, c2, manual):
        new_colli[n["id"]] = c2
        transfers.append({"need_id": n["id"], "from": s, "to": n["store"], "key": n["key"], "pieces": t,
                          "value": t * float(n.get("price") or 0), "colli_before": int(n["colli"]),
                          "colli_after": c2, "pack": n["pack"], "need": n["need"], "manual": manual})

    # 1) 人工定的（包括冻结下来的系统建议）：先占货
    left = dict(left0)
    used = {}
    for n in needs:
        o = overrides.get(n["id"])
        if o is None or o["pieces"] <= 0:
            continue
        p, s = o["pieces"], o["from"]
        if o["auto"]:                                    # 冻结下来的建议：要的少了就少调
            p = min(p, int(math.ceil(float(n["need"] or 0) - 1e-9)))
            if p <= 0:
                continue
        if s is None:                                    # 只给了件数：从多出来最多的那家
            cands = [d for (d, k), v in left.items() if k == n["key"] and d != n["store"] and d not in far and v >= p]
            if not cands:
                continue
            s = max(cands, key=lambda d: (left[(d, n["key"])], -d))
        elif s == n["store"]:
            continue
        if stock is not None:
            have = int(max(0, stock.get((s, n["key"]), 0))) - used.get((s, n["key"]), 0)
            p = min(p, max(0, have))
            if p <= 0:
                continue
        used[(s, n["key"])] = used.get((s, n["key"]), 0) + p
        left[(s, n["key"])] = left.get((s, n["key"]), 0) - p
        pack = float(n["pack"] or 1)
        c2 = min(int(n["colli"] or 0), int(math.ceil(max(0.0, float(n["need"] or 0) - p) / pack - 1e-9)))
        add(n, s, p, max(0, c2), not o["auto"])
    if not auto:
        return transfers, new_colli
    # 2) 系统建议：没人工定过的商品
    todo = []
    for n in needs:
        if n["store"] in far or n["id"] in overrides:
            continue
        opts = [o for o in options(n["need"], n["pack"], n["colli"], cfg.get("mode"), float(cfg.get("waste_ratio") or 0.5))
                if o[0] > 0]
        if opts:
            todo.append((n, opts))
    todo.sort(key=lambda x: -x[1][0][0] * float(x[0].get("price") or 0))     # 货值大的先分
    manual_value = {}
    for m in transfers:
        manual_value[m["from"]] = manual_value.get(m["from"], 0.0) + m["value"]
    banned = set()
    while True:
        lft = dict(left)
        picks = []
        for n, opts in todo:
            cands = [d for (d, k), v in lft.items() if k == n["key"] and v > 0 and d != n["store"]
                     and d not in far and d not in banned]
            for t, c2 in opts:
                if item_min and t * float(n.get("price") or 0) < item_min - 1e-9:
                    continue
                ok = [d for d in cands if lft[(d, n["key"])] >= t]
                if ok:
                    s = max(ok, key=lambda d: (lft[(d, n["key"])], -d))   # 多出来最多的那家
                    lft[(s, n["key"])] -= t
                    picks.append((n, s, t, c2))
                    break
        tot = dict(manual_value)
        for n, s, t, _ in picks:
            tot[s] = tot.get(s, 0.0) + t * float(n.get("price") or 0)
        low = {s for s in {p[1] for p in picks} if tot.get(s, 0.0) < min_value - 1e-9}
        if not low:
            break
        banned |= low                                    # 这家合计不够门槛：整批不从它调，再算一遍
    for n, s, t, c2 in picks:
        add(n, s, t, c2, False)
    return transfers, new_colli


def freeze(transfers, overrides, key_of):
    """
    审核时第一次改调货：把现在系统建议的调货都记成「冻结的」人工值（auto=True），以后只按这些调，
    改一行不会让别的行跟着变。key_of(调货) → overrides 的键。返回新的 overrides
    """
    out = dict(overrides or {})
    for m in transfers or []:
        k = key_of(m)
        if k not in out:
            out[k] = {"from": m["from"], "pieces": int(m["pieces"]), "auto": True}
    return out


def unfreeze(overrides):
    """调货设置改了：去掉冻结的系统建议（auto），只留人工改过的，其他的重新按设置建议"""
    return {k: v for k, v in (overrides or {}).items() if not norm_override(v)["auto"]}


def by_donor(transfers):
    """按调出门店汇总：{门店: {"items", "pieces", "value", "boxes_saved"}}"""
    out = {}
    for t in transfers:
        x = out.setdefault(t["from"], {"items": 0, "pieces": 0, "value": 0.0, "boxes_saved": 0})
        x["items"] += 1
        x["pieces"] += t["pieces"]
        x["value"] += t["value"]
        x["boxes_saved"] += t["colli_before"] - t["colli_after"]
    return out
