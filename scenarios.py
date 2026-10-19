"""
批发商比价：同时算几种方案（全部供应商 / 不含 MEGA / 不含 NEW GRIECO …），给出对比，由用户选一种写入。

每个方案 = 排除哪些供应商（按文件名里的关键词，和起订规则一样）。排除的供应商当作没有报价：
  · 商品按剩下的供应商最低价分配，有起订规则的照常自动凑单 / 放弃（和不问操作员时的自动方案一样）
  · 只有被排除的供应商有报价的商品，这个方案里就买不到 → 记为缺失
方案列表存在 settings.json → scenarios；scenarios_enabled 关掉就和以前一样只算一种。

排除关键词同时按「文件名」和「供应商名称」找（supplier_names：MEGA 的文件叫 formattato_xxx，
名字里没有 mega，只按文件名找会找不到，「不含 MEGA」就和「全部供应商」一模一样）。

和第一个方案（基准）比时，把差额拆成两部分，避免「少买了商品所以总额低」被当成更便宜：
  price_diff  同样买到的商品，换了供应商造成的差价（正 = 更贵）
  lost_value  基准里买得到、这个方案买不到的商品，在基准里的金额（少花的钱 = 少买的货）
  gained_value 反过来（这个方案多买到的）
  total - 基准 total = price_diff - lost_value + gained_value
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


def excluded_headers(headers, exclude, labels=None):
    """文件名或供应商名称里含有任一关键词的供应商（不分大小写）"""
    import supplier_names as SN
    kws = [str(k).strip() for k in (exclude or []) if str(k).strip()]
    labels = labels or {}
    return [h for h in headers if h and any(SN.hits(k, h, labels.get(h)) for k in kws)]


def unmatched_keywords(headers, exclude, labels=None):
    """方案里写了、但这次一个供应商都没找到的排除关键词"""
    import supplier_names as SN
    labels = labels or {}
    return [k for k in (exclude or []) if str(k).strip()
            and not any(SN.hits(k, h, labels.get(h)) for h in headers if h)]


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


def _line(r, s):
    """一个商品在供应商 s 的采购金额：箱数 × 每箱件数 × 件价"""
    return _qty(r) * (((r.get("amt") or r.get("prices") or {}).get(s)) or 0)


def _money(rows, assign, only=None):
    """真实采购金额（和结果页的采购总额一样）；only = 只算这些行号"""
    tot = 0.0
    for r in rows:
        if only is not None and r["row"] not in only:
            continue
        s = assign.get(r["row"])
        if s:
            tot += _line(r, s)
    return tot


def run_scenario(rows, headers, rules, d, labels=None):
    """算一个方案，返回对比用的结果（含这个方案的 assignment，选中后直接用它写入）"""
    labels = labels or {}
    excl = excluded_headers(headers, d.get("exclude"), labels)
    rws = restrict_rows(rows, excl)
    rls = [ru for ru in (rules or []) if ru["target"] not in excl]
    base = baseline_assignment(rws)
    assign = auto_assignment(rws, rls) if rls else base
    rep = build_report(rws, rls, base, assign, {r: "自动凑单" for r in assign if assign.get(r) != base.get(r)})
    sups = []
    for h in headers:
        c, a = _totals(rws, assign, h)
        n = sum(1 for r in rws if assign.get(r["row"]) == h and _qty(r) > 0)
        sups.append({"name": h, "label": labels.get(h) or h, "rows": n, "colli": c, "amount": a,
                     "excluded": h in excl})
    miss = missing_items(rows, assign, excl)
    bought = [r for r in rows if assign.get(r["row"]) and _qty(r) > 0]
    return {
        "name": d.get("name") or "方案",
        "exclude": list(d.get("exclude") or []),
        "excluded": excl,
        "assignment": assign,
        "baseline": base,
        "unmatched": unmatched_keywords(headers, d.get("exclude"), labels),
        "rules": [dict(target=ru["target"], name=ru.get("name"), status=x["status"],
                       min_colli=ru.get("min_colli", 0), min_amount=ru.get("min_amount", 0),
                       mode=ru.get("mode", "all"),
                       before_colli=x.get("before_colli"), before_amount=x.get("before_amount"),
                       after_colli=x["after_colli"], after_amount=x["after_amount"])
                  for ru, x in zip(rls, rep["rules"])],
        "total": _money(rows, assign),
        "priced": sum(1 for r in rows if assign.get(r["row"])),
        "need": sum(1 for r in rows if _qty(r) > 0),
        "colli": sum(_qty(r) for r in bought),
        "n_suppliers": sum(1 for x in sups if x["rows"]),
        "missing": miss,
        "missing_qty": sum(m["qty"] for m in miss),
        "changes": len(rep["changes"]),
        "extra_cost": rep["extra_cost"],
        "suppliers": sups,
    }


def compute_scenarios(rows, headers, rules, defs, labels=None):
    out = [run_scenario(rows, headers, rules, d, labels) for d in defs]
    # 和第一个方案（一般是「全部供应商」）比
    if out:
        ref = out[0]
        ra = ref["assignment"]
        need = [r for r in rows if _qty(r) > 0]
        for k, s in enumerate(out):
            a = s["assignment"]
            s["vs_first"] = s["total"] - ref["total"]
            s["missing_vs_first"] = len(s["missing"]) - len(ref["missing"])
            both = {r["row"] for r in need if a.get(r["row"]) and ra.get(r["row"])}
            lost = {r["row"] for r in need if ra.get(r["row"]) and not a.get(r["row"])}
            gained = {r["row"] for r in need if a.get(r["row"]) and not ra.get(r["row"])}
            s["price_diff"] = _money(rows, a, both) - _money(rows, ra, both)
            s["lost_value"] = _money(rows, ra, lost)
            s["lost_rows"] = len(lost)
            s["gained_value"] = _money(rows, a, gained)
            s["gained_rows"] = len(gained)
            s["switched"] = sum(1 for r in both if a.get(r) != ra.get(r))
            # 和前面哪个方案完全一样（排除的供应商没找到时常见）
            s["same_as"] = next((j for j in range(k) if out[j]["assignment"] == a), None)
        diff = diff_items(rows, [x["assignment"] for x in out], labels)
        for s in out:
            s["diff"] = diff                      # 各方案供应商不一样的商品（同一个列表，对比窗口用）
    return out


def diff_items(rows, assigns, labels=None):
    """
    各方案供应商不一样的商品：[{row, ean, articolo, qty, sups: [每个方案的供应商名称或 ""], amounts: [...]}]
    assigns = 每个方案的 {行号: 供应商}
    """
    labels = labels or {}
    out = []
    for r in rows:
        if _qty(r) <= 0:
            continue
        ss = [a.get(r["row"]) for a in assigns]
        if len(set(ss)) <= 1:
            continue
        out.append({"row": r["row"], "ean": r.get("ean", ""), "articolo": r.get("articolo", ""), "qty": _qty(r),
                    "sups": [(labels.get(s) or s) if s else "" for s in ss],
                    "amounts": [(_line(r, s) if s else None) for s in ss]})
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
        out.append({k: s[k] for k in ("name", "exclude", "excluded", "unmatched", "rules", "total", "priced",
                                      "need", "colli", "n_suppliers", "missing", "missing_qty", "changes",
                                      "extra_cost", "suppliers", "vs_first", "missing_vs_first", "price_diff",
                                      "lost_value", "lost_rows", "gained_value", "gained_rows", "switched",
                                      "same_as") if k in s})
        out[-1]["chosen"] = (i == chosen)
    return out
