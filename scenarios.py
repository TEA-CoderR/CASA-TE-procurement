"""
批发商比价：同时算几种方案（全部供应商 / 不含 MEGA / 不含 NEW GRIECO …），给出对比，由用户选一种写入。

每个方案 = 排除哪些供应商（按文件名里的关键词，和起订规则一样）。排除的供应商当作没有报价：
  · 商品按剩下的供应商最低价分配，有起订规则的照常自动凑单 / 放弃（和不问操作员时的自动方案一样）
  · 只有被排除的供应商有报价的商品，这个方案里就买不到 → 记为缺失
方案列表存在 settings.json → scenarios；scenarios_enabled 关掉就和以前一样只算一种。
"""
from allocation_plan import auto_assignment, baseline_assignment, build_report, _totals

DEFAULT_SCENARIOS = [
    {"name": "全部供应商", "exclude": []},
    {"name": "不含 MEGA", "exclude": ["mega"]},
    {"name": "不含 NEW GRIECO", "exclude": ["grieco"]},
]


def scenario_defs(settings):
    defs = settings.get("scenarios")
    if not isinstance(defs, list) or not defs:
        defs = [dict(d) for d in DEFAULT_SCENARIOS]
    return defs


def excluded_headers(headers, exclude):
    """文件名里含有任一关键词的供应商（不分大小写）"""
    kws = [str(k).strip().lower() for k in (exclude or []) if str(k).strip()]
    return [h for h in headers if h and any(k in str(h).lower() for k in kws)]


def restrict_rows(rows, excluded):
    """被排除的供应商当作没有报价（不改原来的 rows）"""
    ex = set(excluded)
    if not ex:
        return rows
    out = []
    for r in rows:
        r2 = dict(r)
        for k in ("prices", "amt", "unit"):
            if isinstance(r.get(k), dict):
                r2[k] = {s: (None if s in ex else p) for s, p in r[k].items()}
        out.append(r2)
    return out


def _qty(r):
    try:
        return max(0.0, float(r.get("qty") or 0))
    except (TypeError, ValueError):
        return 0.0


def _priced(r, s):
    return (r.get("prices") or {}).get(s) is not None


def missing_items(rows_all, assign, excluded=()):
    """
    没有分到供应商的商品（要订的量 > 0）：[{row, ean, articolo, qty, reason, offers}]
      reason：没有任何报价 / 只有被排除的供应商有报价 / 起订量放弃了有报价的供应商
    """
    ex = set(excluded)
    out = []
    for r in rows_all:
        if assign.get(r["row"]) or _qty(r) <= 0:
            continue
        offers = [s for s in (r.get("prices") or {}) if _priced(r, s)]
        if not offers:
            reason = "没有任何报价"
        elif all(s in ex for s in offers):
            reason = "只有被排除的供应商有报价"
        else:
            reason = "起订量放弃了有报价的供应商"
        out.append({"row": r["row"], "ean": r.get("ean", ""), "articolo": r.get("articolo", ""),
                    "qty": _qty(r), "reason": reason, "offers": offers})
    return out


def _money(rows, assign):
    """真实采购金额：箱数 × 每箱件数 × 件价（和结果页的采购总额一样）"""
    tot = 0.0
    for r in rows:
        s = assign.get(r["row"])
        if s:
            tot += _qty(r) * (((r.get("amt") or r.get("prices") or {}).get(s)) or 0)
    return tot


def run_scenario(rows, headers, rules, d):
    """算一个方案，返回对比用的结果（含这个方案的 assignment，选中后直接用它写入）"""
    excl = excluded_headers(headers, d.get("exclude"))
    rws = restrict_rows(rows, excl)
    rls = [ru for ru in (rules or []) if ru["target"] not in excl]
    base = baseline_assignment(rws)
    assign = auto_assignment(rws, rls) if rls else base
    rep = build_report(rws, rls, base, assign, {r: "自动凑单" for r in assign if assign.get(r) != base.get(r)})
    sups = []
    for h in headers:
        c, a = _totals(rws, assign, h)
        n = sum(1 for r in rws if assign.get(r["row"]) == h)
        sups.append({"name": h, "rows": n, "colli": c, "amount": a, "excluded": h in excl})
    miss = missing_items(rows, assign, excl)
    return {
        "name": d.get("name") or "方案",
        "exclude": list(d.get("exclude") or []),
        "excluded": excl,
        "assignment": assign,
        "baseline": base,
        "rules": [dict(target=ru["target"], name=ru.get("name"), status=x["status"],
                       after_colli=x["after_colli"], after_amount=x["after_amount"])
                  for ru, x in zip(rls, rep["rules"])],
        "total": _money(rows, assign),
        "priced": sum(1 for r in rows if assign.get(r["row"])),
        "missing": miss,
        "missing_qty": sum(m["qty"] for m in miss),
        "changes": len(rep["changes"]),
        "extra_cost": rep["extra_cost"],
        "suppliers": sups,
    }


def compute_scenarios(rows, headers, rules, defs):
    out = [run_scenario(rows, headers, rules, d) for d in defs]
    # 和第一个方案（一般是「全部供应商」）比
    if out:
        ref = out[0]
        for s in out:
            s["vs_first"] = s["total"] - ref["total"]
            s["missing_vs_first"] = len(s["missing"]) - len(ref["missing"])
    return out


def recommend(results):
    """建议：缺的商品最少的方案里，采购总额最低的那个（缺得一样多才比价钱）"""
    if not results:
        return None
    return min(range(len(results)), key=lambda i: (len(results[i]["missing"]), results[i]["total"]))


def compact(results, chosen=None):
    """存历史记录 / 结果页用的精简版（不含 assignment）"""
    out = []
    for i, s in enumerate(results):
        out.append({k: s[k] for k in ("name", "exclude", "excluded", "rules", "total", "priced", "missing",
                                      "missing_qty", "changes", "extra_cost", "suppliers", "vs_first",
                                      "missing_vs_first") if k in s})
        out[-1]["chosen"] = (i == chosen)
    return out
