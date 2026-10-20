"""
分配方案：给界面做「人工决定」用。

流程：
  1. plan = build_plan(rows, rules)
     - 最低价方案（baseline）
     - 每个没达标的起订供应商：
         继续凑单 的采购总额 / 放弃该供应商 的采购总额 / 因它凑单多花了多少
       并标出哪些需要操作员决定（凑单比放弃更贵、或多花超过设定值、或凑不够）
  2. 操作员对需要决定的供应商选「继续凑单 / 放弃」
     final = apply_decisions(rows, rules, decisions)
  3. 操作员还可以在手动调整窗口里逐个商品改供应商
  4. build_report(rows, rules, baseline, final, ...) 生成结果页需要的全部数据
"""
from i18n import tr
import copy

from min_order_optimizer import optimize_min_orders, optimize_min_order, _satisfied, _qty, _total_cost, _amt, _colli


def _totals(rows, assign, target):
    c = a = 0.0
    for r in rows:
        if assign.get(r["row"]) == target:
            q = _qty(r["qty"])
            c += _colli(r, target)
            a += q * (_amt(r, target) or 0)
    return c, a


def _without_supplier(rows, target):
    """把某个供应商的报价全部去掉（= 这次不在它那里下单）"""
    out = []
    for r in rows:
        r2 = dict(r)
        r2["prices"] = {s: (None if s == target else p) for s, p in r["prices"].items()}
        out.append(r2)
    return out


def _force_open(rules):
    return [dict(ru, allow_drop=False) for ru in rules]


def baseline_assignment(rows):
    a, _ = optimize_min_order(rows, None)
    return a


def solve(rows, rules, dropped=()):
    """dropped 里的供应商不下单，其余起订供应商都必须达标。返回 (assignment, 是否全部达标)"""
    rws = rows
    for t in dropped:
        rws = _without_supplier(rws, t)
    rls = _force_open([ru for ru in rules if ru["target"] not in dropped])
    if not rls:
        return baseline_assignment(rws), True
    assign, rep = optimize_min_orders(rws, rls)
    return assign, not rep["warning"]


def build_plan(rows, rules, allow_partial=False):
    """
    allow_partial=False（默认）：所有商品都必须订到，
    放弃某个供应商会让商品买不到时，「放弃」不可选。
    """
    baseline = baseline_assignment(rows)
    base_cost = _total_cost(rows, baseline)
    plan = {"baseline": baseline, "baseline_cost": base_cost, "rules": [], "needs_decision": False}

    # 需要处理的：最低价分到了货但没达标的起订供应商
    active = []
    for ru in rules:
        c, a = _totals(rows, baseline, ru["target"])
        ok = c == 0 or _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"])
        if not ok:
            active.append(ru)

    fill_all, fill_ok = solve(rows, rules)
    fill_cost = _total_cost(rows, fill_all)
    plan["fill_all"] = fill_all
    plan["fill_all_ok"] = fill_ok

    for ru in active:
        t = ru["target"]
        # 不设起订条件时（其他规则照旧）的成本，用来算「因为它凑单多花了多少」
        others = [x for x in rules if x["target"] != t]
        nomin, _ = solve(rows, others)
        nomin_cost = _total_cost(rows, nomin)
        # 放弃该供应商
        drop, drop_ok = solve(rows, rules, dropped=(t,))
        drop_cost = _total_cost(rows, drop)
        lost = [r for r in rows if _qty(r["qty"]) > 0 and baseline.get(r["row"]) is not None
                and drop.get(r["row"]) is None]
        lost_value = sum(_qty(r["qty"]) * (r["prices"].get(t) or 0) for r in lost)

        bc, ba = _totals(rows, baseline, t)
        fc, fa = _totals(rows, fill_all, t)
        extra = fill_cost - nomin_cost
        manual_line = float(ru.get("manual_extra") or 0)   # 超过 → 打开手动调整
        drop_line = float(ru.get("drop_extra") or 0)       # 超过 → 问是否放弃该供应商
        drop_allowed = drop_ok and (allow_partial or not lost)
        # 放弃会少买商品时，把这些商品的价值算进去再比较
        drop_cmp = drop_cost + (lost_value if lost else 0)
        reasons = []
        if not fill_ok:
            reasons.append(tr("凑不够起订条件") + ("" if drop_allowed else tr("，而且有 {0} 个商品只有它有货，不能放弃", len(lost))))
        if fill_ok and drop_allowed and drop_cmp + 1e-9 < fill_cost:
            reasons.append(tr("凑单比放弃该供应商更贵"))
        if fill_ok and drop_line > 0 and extra > drop_line + 1e-9:
            reasons.append(tr("凑单多花 €{0:,.2f}，超过放弃供应商线 €{1:,.2f}", extra, drop_line))
        manual_reasons = []
        if fill_ok and manual_line > 0 and extra > manual_line + 1e-9:
            manual_reasons.append(tr("{0} 凑单多花 €{1:,.2f}，超过手动调整线 €{2:,.2f}", ru.get('name') or t, extra, manual_line))
        info = {
            "target": t, "name": ru.get("name") or t, "min_colli": ru["min_colli"], "min_amount": ru["min_amount"],
            "mode": ru["mode"], "manual_extra": manual_line, "drop_extra": drop_line,
            "before_colli": bc, "before_amount": ba,
            "fill_ok": fill_ok, "fill_total": fill_cost, "fill_colli": fc, "fill_amount": fa, "fill_extra": extra,
            "drop_ok": drop_ok, "drop_total": drop_cost, "drop_extra": drop_cost - base_cost,
            "lost_count": len(lost), "lost_value": lost_value, "drop_cmp": drop_cmp,
            "lost_items": [dict({"row": r["row"], "ean": r.get("ean", ""), "articolo": r.get("articolo", ""),
                                 "qty": _qty(r["qty"]), "price": (r.get("unit") or r["prices"]).get(t)},
                                **({"inv": r.get("inv") or 0, "daily": r.get("daily") or 0}
                                   if r.get("daily") is not None else {})) for r in lost],
            "drop_allowed": drop_allowed, "allow_partial": allow_partial,
            "reasons": reasons, "needs_decision": bool(reasons),
            "manual_reasons": manual_reasons, "needs_manual": bool(manual_reasons),
            # 不问操作员时的默认选择：哪个便宜选哪个；不能放弃时只能凑单（凑不够就按最低价下单）
            "default": "drop" if (drop_allowed and (not fill_ok or drop_cmp < fill_cost)) else "fill",
        }
        plan["rules"].append(info)
        plan["needs_decision"] |= info["needs_decision"]
        plan["needs_manual"] = plan.get("needs_manual", False) or info["needs_manual"]
    return plan


def apply_decisions(rows, rules, decisions):
    """decisions: {供应商: 'fill' / 'drop'}"""
    dropped = tuple(t for t, d in decisions.items() if d == "drop")
    assign, _ = solve(rows, rules, dropped)
    return assign


def auto_assignment(rows, rules):
    """不问操作员时：按每条规则的设置自动取总成本最低"""
    if not rules:
        return baseline_assignment(rows)
    a, _ = optimize_min_orders(rows, rules)
    return a


def rule_status(rows, ru, baseline, final):
    t = ru["target"]
    bc, ba = _totals(rows, baseline, t)
    fc, fa = _totals(rows, final, t)
    if bc == 0 and fc == 0:
        return "未分到货，无需下单", bc, ba, fc, fa
    if fc == 0:
        return "已放弃", bc, ba, fc, fa
    if _satisfied(fc, fa, ru["min_colli"], ru["min_amount"], ru["mode"]):
        before_ok = _satisfied(bc, ba, ru["min_colli"], ru["min_amount"], ru["mode"]) if bc else False
        return ("已达标" if before_ok else "已凑单达标"), bc, ba, fc, fa
    return "未达标", bc, ba, fc, fa


def build_report(rows, rules, baseline, final, sources=None):
    """
    sources: {行号: '自动凑单' / '放弃供应商' / '手动'}，说明每个改动是怎么来的
    """
    sources = sources or {}
    suppliers = []
    for s in (rows[0]["prices"].keys() if rows else []):
        bc, ba = _totals(rows, baseline, s)
        fc, fa = _totals(rows, final, s)
        suppliers.append({"name": s, "before_colli": bc, "before_amount": ba,
                          "after_colli": fc, "after_amount": fa})
    changes = []
    for r in rows:
        old, new = baseline.get(r["row"]), final.get(r["row"])
        if old == new:
            continue
        q = _qty(r["qty"])
        po = r["prices"].get(old) if old else None
        pn = r["prices"].get(new) if new else None
        unit = r.get("unit") or r["prices"]
        packs = r.get("packs") or {}
        changes.append({
            "row": r["row"], "ean": r.get("ean", ""), "articolo": r.get("articolo", ""), "qty": q,
            "old": old, "old_price": unit.get(old) if old else None, "old_pack": packs.get(old) if old else None,
            "new": new, "new_price": unit.get(new) if new else None, "new_pack": packs.get(new) if new else None,
            # 多花多少：按单件价比（同样件数的差价）
            "delta": (pn or 0) * q - (po or 0) * q if (po is not None and pn is not None) else None,
            "source": sources.get(r["row"], tr("自动凑单")),
        })
    rules_rep = []
    for ru in rules:
        st, bc, ba, fc, fa = rule_status(rows, ru, baseline, final)
        rules_rep.append(dict(ru, status=st, before_colli=bc, before_amount=ba, after_colli=fc, after_amount=fa))
    base_cost = _total_cost(rows, baseline)
    final_cost = _total_cost(rows, final)
    return {"suppliers": suppliers, "changes": changes, "rules": rules_rep,
            "baseline_cost": base_cost, "final_cost": final_cost, "extra_cost": final_cost - base_cost}


# =====================================================================
# 按销量加量凑单（修改分配里用）：供应商没达到起订条件时，不换供应商，
# 而是给分到它那里、卖得快的商品多订几箱，加完后库存最多够卖 max_days 天
# （和门店配货「加商品凑单 → 按销量推荐」同一个规则）
# =====================================================================
def _box_pieces(r, s):
    """在供应商 s 那里一箱几件"""
    return float((r.get("packs") or {}).get(s) or r.get("ref_pack") or 1)


def _ordered_pieces(r, s):
    """现在在供应商 s 订了几件"""
    return _colli(r, s) * _box_pieces(r, s)


def _cover(r, pieces):
    daily = float(r.get("daily") or 0)
    if daily <= 0:
        return float("inf")
    return (max(0.0, float(r.get("inv") or 0)) + pieces) / daily


def qty_step(r, s):
    """多订一箱时数量要加多少：按件数订货时加这家一箱的件数，否则加 1 箱"""
    return _box_pieces(r, s) if r.get("pieces") is not None else 1.0


def suggest_items(cands, colli, amount, rule, max_days=60, ignore_days=False, max_steps=3000):
    """
    按销量推荐补齐起订量（门店配货「加商品凑单」同一个规则）：先加卖得最快的（加完够卖天数最少的）；
    只差金额时找一箱就够、多出来最少的；加完库存最多够卖 max_days 天（ignore_days=True 不限）。
    cands: [{"key", "daily", "inv", "have"（已经要到货的件数）, "pack"（每箱件数）, "box"（一箱 €）}]
    colli / amount：这家现在的箱数、金额。
    返回 {"boxes": {key: 加几箱}, "ok", "colli", "amount"（加完后）, "value"（加的金额）}
    """
    mc, ma, mode = rule.get("min_colli") or 0, rule.get("min_amount") or 0, rule.get("mode", "all")
    cands = [x for x in cands if float(x.get("daily") or 0) > 0 and float(x.get("box") or 0) > 0]
    extra = {x["key"]: 0 for x in cands}
    c, a = colli, amount
    for _ in range(max_steps):
        if _satisfied(c, a, mc, ma, mode):
            break
        opts = []
        for x in cands:
            pk = float(x.get("pack") or 1)
            after = _cover(x, float(x.get("have") or 0) + (extra[x["key"]] + 1) * pk)
            if not ignore_days and after > max_days + 1e-9:
                continue
            opts.append((after, float(x["box"]), x["key"]))
        if not opts:
            break
        need_c = bool(mc) and c < mc - 1e-9
        need_a = bool(ma) and a < ma - 1e-9
        if need_a and not need_c:
            gap = ma - a
            enough = [o for o in opts if o[1] >= gap - 1e-9]
            pick = min(enough, key=lambda o: (o[1], o[0])) if enough else min(opts, key=lambda o: (o[0], o[1]))
        else:
            pick = min(opts, key=lambda o: (o[0], o[1]))
        extra[pick[2]] += 1
        c += 1
        a += pick[1]
    return {"boxes": {k: n for k, n in extra.items() if n}, "ok": _satisfied(c, a, mc, ma, mode),
            "colli": c, "amount": a, "value": a - amount}


def suggest_qty_fill(rows, assign, rule, max_days=60, ignore_days=False, max_steps=2000):
    """
    只在已经分给这家的商品上加量（不加新商品）。
    rows: 主订单各行（要有 inv / daily，来自检查门店清单的库存快照）；assign: {行号: 供应商}
    返回 {"adds": {行号: 加的数量（箱，按件数订货时为件）}, "boxes": {行号: 加几箱}, "ok", "colli", "amount",
          "value", "before": (箱, 金额), "candidates": 有销量数据、可以加的商品数}
    """
    t = rule["target"]
    mine = [r for r in rows if assign.get(r["row"]) == t and _qty(r["qty"]) > 0]
    c0, a0 = _totals(rows, assign, t)
    out = {"adds": {}, "boxes": {}, "ok": True, "colli": c0, "amount": a0, "value": 0.0,
           "before": (c0, a0), "candidates": 0}
    if not mine or _satisfied(c0, a0, rule.get("min_colli") or 0, rule.get("min_amount") or 0, rule.get("mode", "all")):
        return out
    cands = [{"key": r["row"], "daily": r.get("daily"), "inv": r.get("inv"), "have": _ordered_pieces(r, t),
              "pack": _box_pieces(r, t), "box": (_amt(r, t) or 0) * qty_step(r, t)}
             for r in mine if r.get("daily") is not None]
    out["candidates"] = sum(1 for x in cands if float(x["daily"] or 0) > 0 and x["box"] > 0)
    res = suggest_items(cands, c0, a0, rule, max_days, ignore_days, max_steps)
    by_row = {r["row"]: r for r in mine}
    out.update(ok=res["ok"], colli=res["colli"], amount=res["amount"], value=res["value"], boxes=res["boxes"])
    out["adds"] = {row: n * qty_step(by_row[row], t) for row, n in res["boxes"].items()}
    return out
