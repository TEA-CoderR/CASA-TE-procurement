"""
门店之间调货：要订的量远小于一箱时，从别的门店调多出来的货，少订一箱。

例：FOIANO 只要 3 件，但一箱 12 件，订一整箱会多出 9 件；S.MARCO 库存 30 件、一个周期只卖 10 件，
多出 20 件 → 从 S.MARCO 调 3 件给 FOIANO，这一箱就不用订了。

规则（设置在 settings.json → store_orders.transfer）：
  · 调出门店留够自己「一个订货周期」的销量（日均 × 这个商品的订货周期天数），多出来的才能调；
    自己这次也要订这个商品的门店不调出
  · 触发条件 mode：
      "waste" 整箱会多出很多时（默认）：订整箱多出的件数 ≥ 一箱的 waste_ratio（默认一半），
              就调零头（要的件数 − 少订一箱后还能到的件数），少订一箱
      "all"   所有要订的商品：别的门店多出来的够多少就调多少，能少订几箱就少订几箱
  · 一家门店调出去的货，总货值（件数 × 件价）≥ min_value（默认 €50）才调，货值太低不值得人工拣货
  · 离得远的门店（far_stores）不调出、也不往那里调
算法：同一家调出门店的货尽量凑在一起（人工一次拣完）——每轮算每家门店能调出的总货值，
     先定货值最高的那家；它够门槛就把这些商品定给它，再算下一家，直到没有门店够门槛。
"""
import math

DEFAULT = {"enabled": True, "mode": "waste", "waste_ratio": 0.5, "min_value": 50.0}


def config(cfg=None):
    return dict(DEFAULT, **(cfg or {}))


def surplus(d):
    """这家店多出来、可以调出的件数：库存 − 日均 × 订货周期（留够自己一个周期）。负库存当 0"""
    if not d:
        return 0
    inv = max(0.0, float(d.get("inv") or 0))
    daily = max(0.0, float(d.get("daily") or 0))
    cycle = float(d.get("cycle") or 30)
    return max(0, int(math.floor(inv - daily * cycle + 1e-9)))


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


def plan(needs, avail, cfg=None, far=()):
    """
    needs: [{"id", "store", "key"(商品), "need", "pack", "colli", "price"(件价), ...}]
    avail: {(门店, 商品): 可调出件数}
    返回 (transfers, new_colli)
      transfers: [{"need_id", "from", "to", "key", "pieces", "value", "colli_before", "colli_after"}]
      new_colli: {need_id: 调货后要订的箱数}
    """
    cfg = config(cfg)
    far = set(far or ())
    min_value = float(cfg.get("min_value") or 0)
    left = {k: int(v) for k, v in (avail or {}).items() if v and v > 0}
    todo = {}
    for n in needs:
        if n["store"] in far:
            continue
        opts = [o for o in options(n["need"], n["pack"], n["colli"], cfg.get("mode"), float(cfg.get("waste_ratio") or 0.5))
                if o[0] > 0]
        if opts:
            todo[n["id"]] = (n, opts)
    donors = sorted({s for (s, _k) in left if s not in far})
    transfers, new_colli = [], {}

    def offer(s):
        """门店 s 这一轮能调出的：[(need, 件数, 新箱数)]，总货值"""
        stock = {}
        picks, value = [], 0.0
        # 货值大的先占库存
        for nid, (n, opts) in sorted(todo.items(), key=lambda x: -x[1][1][0][0] * float(x[1][0].get("price") or 0)):
            if n["store"] == s:
                continue
            k = (s, n["key"])
            have = stock.get(k, left.get(k, 0))
            for t, c2 in opts:                      # 少订得最多、而且库存够的那个
                if t <= have:
                    picks.append((n, t, c2))
                    stock[k] = have - t
                    value += t * float(n.get("price") or 0)
                    break
        return picks, value

    while todo and donors:
        best = None
        for s in donors:
            picks, value = offer(s)
            if picks and (best is None or value > best[2] + 1e-9):
                best = (s, picks, value)
        if best is None or best[2] < min_value - 1e-9:
            break
        s, picks, _ = best
        for n, t, c2 in picks:
            left[(s, n["key"])] = left.get((s, n["key"]), 0) - t
            new_colli[n["id"]] = c2
            transfers.append({"need_id": n["id"], "from": s, "to": n["store"], "key": n["key"], "pieces": t,
                              "value": t * float(n.get("price") or 0), "colli_before": int(n["colli"]),
                              "colli_after": c2, "pack": n["pack"], "need": n["need"]})
            todo.pop(n["id"], None)
        donors.remove(s)                            # 一家门店只算一轮（所有能调的这一轮都定了）
    return transfers, new_colli


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
