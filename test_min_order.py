"""凑单算法测试：python -m pytest test_min_order.py -v"""
import itertools
import random

from min_order_optimizer import optimize_min_order, _satisfied

M = "Mega.xlsx"


def row(r, qty, **prices):
    return {"row": r, "qty": qty, "prices": {"A.xlsx": prices.get("A"), "B.xlsx": prices.get("B"), M: prices.get("M")}}


def test_already_ok():
    rows = [row(2, 250, A=3.0, M=2.9)]
    a, rep = optimize_min_order(rows, M, 200, 700)
    assert rep["action"] == "ok" and a[2] == M


def test_no_mega_items_no_change():
    rows = [row(2, 10, A=1.0, M=2.0)]
    a, rep = optimize_min_order(rows, M, 200, 700)
    assert rep["action"] == "none" and a[2] == "A.xlsx"


def test_fill_picks_cheapest_moves():
    rows = [
        row(2, 50, A=5.0, M=4.0),     # 已在 Mega：50 箱 / €200
        row(3, 100, A=3.0, M=3.0),    # 同价，免费改
        row(4, 60, A=2.0, M=2.5),     # +30
        row(5, 60, A=2.0, M=4.0),     # +120
        row(6, 2, A=1.0, M=1.0, B=0.9),
    ]
    a, rep = optimize_min_order(rows, M, 200, 700, allow_drop=False)
    assert rep["action"] == "fill"
    # 3+4 只有 €650 不够，最优是 3+5（+120）
    assert a[3] == M and a[5] == M and a[4] == "A.xlsx"
    assert rep["after_colli"] >= 200 and rep["after_amount"] >= 700
    assert abs(rep["extra_cost"] - 120) < 1e-6


def test_drop_when_cheaper():
    rows = [
        row(2, 10, A=5.01, M=5.0),    # Mega 只便宜一点
        row(3, 300, A=1.0, M=2.0),    # 凑单很贵
    ]
    a, rep = optimize_min_order(rows, M, 200, 700)
    assert rep["action"] == "drop" and a[2] == "A.xlsx"


def test_drop_impossible_when_mega_exclusive():
    rows = [row(2, 10, M=5.0), row(3, 300, A=1.0, M=2.5)]
    a, rep = optimize_min_order(rows, M, 200, 700)
    assert rep["action"] == "fill" and a[3] == M


def test_infeasible():
    rows = [row(2, 10, M=5.0), row(3, 20, A=1.0, M=2.0)]
    a, rep = optimize_min_order(rows, M, 200, 700)
    assert rep["action"] == "infeasible" and a[3] == "A.xlsx"


def test_mode_any():
    rows = [row(2, 10, A=9, M=8.0), row(3, 300, A=1.0, M=1.1), row(4, 10, A=70, M=71)]
    a, rep = optimize_min_order(rows, M, 200, 700, mode="any", allow_drop=False)
    # 凑金额只需第4行（+10），凑箱数需第3行（+30）
    assert a[4] == M and a[3] == "A.xlsx"


def test_greedy_close_to_optimal_random():
    """随机小样本与穷举最优解比较，贪心不应比最优贵太多"""
    random.seed(1)
    worse = 0
    for _ in range(300):
        rows = [row(2, random.randint(5, 40), A=5.0, M=4.0)]
        for r in range(3, 11):
            pa = round(random.uniform(1, 10), 2)
            rows.append(row(r, random.randint(1, 80), A=pa, M=round(pa * random.uniform(1.0, 1.4), 2)))
        a, rep = optimize_min_order(rows, M, 200, 700, allow_drop=False)
        # 穷举
        cands = rows[1:]
        best = None
        for k in range(len(cands) + 1):
            for comb in itertools.combinations(cands, k):
                c = rows[0]["qty"] + sum(x["qty"] for x in comb)
                am = rows[0]["qty"] * 4.0 + sum(x["qty"] * x["prices"][M] for x in comb)
                if _satisfied(c, am, 200, 700, "all"):
                    cost = sum((x["prices"][M] - x["prices"]["A.xlsx"]) * x["qty"] for x in comb)
                    best = cost if best is None else min(best, cost)
        if best is None:
            assert rep["action"] == "infeasible"
        else:
            assert rep["action"] == "fill"
            if rep["extra_cost"] > best + 1e-6:
                worse += 1
    print("greedy suboptimal cases:", worse, "/ 300")
    assert worse == 0


# ===================== 多供应商 =====================
from min_order_optimizer import optimize_min_orders


def _brute(rows, rules):
    sups = list(rows[0]["prices"].keys())
    best = None
    for combo in itertools.product(*[[s for s in sups if r["prices"][s] is not None] for r in rows]):
        ok = True
        for ru in rules:
            c = sum(r["qty"] for r, s in zip(rows, combo) if s == ru["target"])
            a = sum(r["qty"] * r["prices"][s] for r, s in zip(rows, combo) if s == ru["target"])
            if c > 0 and not _satisfied(c, a, ru["min_colli"], ru["min_amount"], ru["mode"]):
                ok = False
        if ok:
            cost = sum(r["qty"] * r["prices"][s] for r, s in zip(rows, combo))
            best = cost if best is None else min(best, cost)
    return best


def test_multi_single_rule_matches_single():
    rows = [row(2, 50, A=5.0, M=4.0), row(3, 100, A=3.0, M=3.0), row(4, 60, A=2.0, M=2.5),
            row(5, 60, A=2.0, M=4.0)]
    a, rep = optimize_min_orders(rows, [dict(target=M, min_colli=200, min_amount=700, mode="all", allow_drop=False)])
    assert a[3] == M and a[5] == M and abs(rep["extra_cost"] - 120) < 1e-6


def test_multi_vs_bruteforce():
    random.seed(7)
    worse, total = 0, 0
    for _ in range(150):
        rows = []
        for r in range(2, 9):
            base = random.uniform(1, 10)
            rows.append({"row": r, "qty": random.randint(1, 40), "prices": {
                "A.xlsx": round(base * random.uniform(1, 1.3), 2),
                "B.xlsx": round(base * random.uniform(0.9, 1.3), 2) if random.random() < 0.8 else None,
                M: round(base * random.uniform(0.9, 1.3), 2) if random.random() < 0.8 else None}})
        rules = [dict(target=M, min_colli=60, min_amount=200, mode="all", allow_drop=True),
                 dict(target="B.xlsx", min_colli=50, min_amount=0, mode="all", allow_drop=True)]
        a, rep = optimize_min_orders(rows, rules)
        best = _brute(rows, rules)
        # 结果必须满足约束
        for ru in rules:
            c = sum(r["qty"] for r in rows if a.get(r["row"]) == ru["target"])
            am = sum(r["qty"] * r["prices"][ru["target"]] for r in rows if a.get(r["row"]) == ru["target"])
            assert rep["warning"] or c == 0 or _satisfied(c, am, ru["min_colli"], ru["min_amount"], "all")
        if best is not None and not rep["warning"]:
            total += 1
            if rep["final_cost"] > best + 1e-6:
                worse += 1
    print(f"multi suboptimal: {worse}/{total}")
    assert worse == 0


# ===================== 人工决定用的方案 =====================
from allocation_plan import build_plan, apply_decisions, build_report


def _r(r, q, **p):
    return {"row": r, "qty": q, "prices": {"A.xlsx": p.get("A"), M: p.get("M")}, "ean": str(r), "articolo": f"a{r}"}


RULE = dict(target=M, name="MEGA", min_colli=200, min_amount=700, mode="all", allow_drop=True, manual_extra=0, drop_extra=0)


def test_plan_asks_when_fill_costlier_than_drop():
    rows = [_r(2, 10, A=5.01, M=5.0), _r(3, 300, A=1.0, M=2.5)]
    i = build_plan(rows, [RULE])["rules"][0]
    assert i["needs_decision"] and i["default"] == "drop"
    assert abs(i["fill_total"] - 800) < 1e-6 and abs(i["drop_total"] - 350.1) < 1e-6
    # 操作员选「继续凑单」时必须真的凑单
    a = apply_decisions(rows, [RULE], {M: "fill"})
    assert a[2] == M and a[3] == M


def test_plan_asks_when_extra_over_limit():
    rows = [_r(2, 50, A=5.0, M=4.0), _r(3, 100, A=3.0, M=3.0), _r(4, 60, A=2.0, M=4.0)]
    rule = dict(RULE, drop_extra=50)
    i = build_plan(rows, [rule])["rules"][0]
    assert abs(i["fill_extra"] - 120) < 1e-6 and i["needs_decision"]
    i2 = build_plan(rows, [dict(RULE, drop_extra=200)])["rules"][0]
    assert not any("超过设定" in x for x in i2["reasons"])
    assert i2["reasons"] == ["凑单比放弃该供应商更贵"]   # 放弃 +50 比凑单 +120 便宜，仍然要问


def test_plan_lost_items_when_dropping():
    rows = [_r(2, 10, M=5.0), _r(3, 300, A=1.0, M=2.5)]
    i = build_plan(rows, [RULE])["rules"][0]
    assert i["lost_count"] == 1 and i["default"] == "fill"


def test_report_changes_and_supplier_delta():
    rows = [_r(2, 50, A=5.0, M=4.0), _r(3, 100, A=3.0, M=3.0), _r(4, 60, A=2.0, M=4.0)]
    plan = build_plan(rows, [RULE])
    final = apply_decisions(rows, [RULE], {M: "fill"})
    rep = build_report(rows, [RULE], plan["baseline"], final, {4: "手动"})
    sup = {s["name"]: s for s in rep["suppliers"]}
    assert sup[M]["before_colli"] == 50 and sup[M]["after_colli"] == 210
    assert {c["row"] for c in rep["changes"]} == {3, 4}
    assert [c["source"] for c in rep["changes"] if c["row"] == 4] == ["手动"]


# ===================== 部分商品不采购（默认不允许） =====================
def test_drop_not_allowed_when_items_would_be_lost():
    # 第 2 行只有 MEGA 有；凑单很贵（+450）但放弃会少买商品
    rows = [_r(2, 10, M=5.0), _r(3, 300, A=1.0, M=2.5)]
    i = build_plan(rows, [RULE])["rules"][0]
    assert i["lost_count"] == 1 and not i["drop_allowed"] and i["default"] == "fill"
    assert not any("更贵" in x for x in i["reasons"])
    i2 = build_plan(rows, [RULE], allow_partial=True)["rules"][0]
    assert i2["drop_allowed"]


def test_cannot_fill_and_cannot_drop_still_buys_everything():
    rows = [_r(2, 10, M=5.0), _r(3, 20, A=1.0, M=2.0)]      # 怎么凑都不够
    i = build_plan(rows, [RULE])["rules"][0]
    assert not i["fill_ok"] and not i["drop_allowed"] and i["needs_decision"] and i["default"] == "fill"
    a = apply_decisions(rows, [RULE], {M: "fill"})
    assert a.get(2) == M and a.get(3) is not None            # 每个商品都有供应商


# ===================== 两条线：手动调整线 / 放弃供应商线 =====================
def _two_line_rows():
    # 第 2 行只有 MEGA 有（不能放弃），凑单多花 120
    return [_r(2, 50, M=4.0), _r(3, 100, A=3.0, M=3.0), _r(4, 60, A=2.0, M=4.0)]


def test_below_manual_line_is_silent():
    i = build_plan(_two_line_rows(), [dict(RULE, manual_extra=150, drop_extra=300)])["rules"][0]
    assert abs(i["fill_extra"] - 120) < 1e-6
    assert not i["needs_manual"] and not i["needs_decision"]


def test_between_lines_opens_manual_only():
    i = build_plan(_two_line_rows(), [dict(RULE, manual_extra=5, drop_extra=300)])["rules"][0]
    assert i["needs_manual"] and not i["needs_decision"]
    assert "手动调整线" in i["manual_reasons"][0]


def test_above_drop_line_asks_to_drop():
    i = build_plan(_two_line_rows(), [dict(RULE, manual_extra=5, drop_extra=30)])["rules"][0]
    assert i["needs_decision"] and "放弃供应商线" in i["reasons"][0]
    # 超过放弃线也一定超过手动线：分配完以后同样会提示要不要手动改
    assert i["needs_manual"]


def test_old_max_extra_setting_migrates_to_drop_line(tmp_path, monkeypatch):
    import json, settings as S
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"rules": [{"name": "MEGA", "keyword": "mega", "max_extra": 25}]}), encoding="utf-8")
    monkeypatch.setattr(S, "SETTINGS_PATH", str(f))
    r = S.load_settings()["rules"][0]
    assert r["drop_extra"] == 25 and r["manual_extra"] == 0 and "max_extra" not in r


# ---------- 价格是单件价：金额 = 箱数 × 每箱件数 × 件价 ----------
def _pr(row, qty, unit, packs):
    import types, sys
    if "win32com" not in sys.modules:
        w = types.ModuleType("win32com"); c = types.ModuleType("win32com.client"); w.client = c
        sys.modules["win32com"] = w; sys.modules["win32com.client"] = c
    from service import _price_row
    return _price_row(row, qty, unit, packs)


def test_price_row_compares_unit_price_and_amount_uses_pack():
    r = _pr(2, 3, {"A": 1.20, "M": 1.10}, {"A": 24, "M": 6})
    # 比价按单件价：M 便宜
    assert min((p, s) for s, p in r["prices"].items())[1] == "M"
    # 一箱金额按各自的箱规
    assert abs(r["amt"]["A"] - 1.20 * 24) < 1e-9 and abs(r["amt"]["M"] - 1.10 * 6) < 1e-9


def test_min_amount_counts_pieces_per_box():
    # 件价 2€，每箱 12 件：10 箱 = 240€；若按一箱 2€ 算只有 20€
    rows = [_pr(2, 10, {"A": 1.9, "M": 2.0}, {"A": 12, "M": 12}),
            _pr(3, 5, {"A": 3.0, "M": 2.9}, {"A": 6, "M": 6})]
    rule = dict(target="M", name="M", min_colli=0, min_amount=200, mode="all", allow_drop=False)
    a, rep = optimize_min_orders(rows, [rule])
    # 最低价时 M 只有第 3 行：5 箱 × 6 件 × 2.9 = 87€，不够 200€
    # 把第 2 行也给 M：10 箱 × 12 件 × 2.0 = 240€，合计 327€（若按一箱 2€ 算，永远凑不够）
    assert a[3] == "M" and a[2] == "M"
    assert not rep["warning"]
