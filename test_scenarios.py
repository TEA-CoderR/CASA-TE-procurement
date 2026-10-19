"""批发商比价：几种方案（全部供应商 / 排除 MEGA / 排除 NEW GRIECO）同时算、对比"""
import scenarios as SC

MEGA, GRIECO, MELONI = "MEGA CASH.xlsx", "NEW GRIECO.xlsx", "MELONI.xlsx"
H = [MEGA, GRIECO, MELONI]


def _row(i, qty, prices, pack=6):
    unit = {h: prices.get(h) for h in H}
    return {"row": i, "qty": qty, "ean": f"80{i}", "articolo": f"ART {i}", "unit": unit,
            "packs": {h: pack for h in H}, "ref_pack": pack,
            "prices": {h: (p * pack if p is not None else None) for h, p in unit.items()},
            "amt": {h: (p * pack if p is not None else None) for h, p in unit.items()}}


ROWS = [
    _row(2, 2, {MEGA: 1.0, GRIECO: 1.2, MELONI: 1.5}),     # MEGA 最便宜
    _row(3, 1, {MEGA: 2.0, GRIECO: 1.8}),                  # GRIECO 最便宜
    _row(4, 3, {MEGA: 0.5}),                               # 只有 MEGA 有
    _row(5, 1, {GRIECO: 0.9, MELONI: 1.0}),
    _row(6, 4, {}),                                        # 谁都没有
    _row(7, 0, {MELONI: 1.0}),                             # 不订
]


def test_three_default_scenarios():
    res = SC.compute_scenarios(ROWS, H, [], SC.DEFAULT_SCENARIOS)
    assert [r["name"] for r in res] == ["全部供应商", "排除 MEGA", "排除 NEW GRIECO"]
    allp, nomega, nogrieco = res
    assert allp["assignment"] == {2: MEGA, 3: GRIECO, 4: MEGA, 5: GRIECO, 7: MELONI}
    assert nomega["excluded"] == [MEGA]
    assert nomega["assignment"].get(2) == GRIECO and 4 not in nomega["assignment"]
    assert nogrieco["assignment"].get(3) == MEGA and nogrieco["assignment"].get(5) == MELONI
    # 采购总额 = 箱数 × 每箱件数 × 件价
    assert round(allp["total"], 2) == round(2 * 6 * 1.0 + 1 * 6 * 1.8 + 3 * 6 * 0.5 + 1 * 6 * 0.9, 2)
    # 缺失：谁都没有的 6 号；排除 MEGA 时还缺只有 MEGA 有的 4 号（不订的 7 号不算）
    assert [m["row"] for m in allp["missing"]] == [6]
    assert [(m["row"], m["reason"]) for m in nomega["missing"]] == [(4, "只有被排除的供应商有报价"), (6, "没有任何报价")]
    assert nomega["missing_vs_first"] == 1 and nomega["vs_first"] < 0          # 少买了 4 号，总额反而低
    sup = {x["name"]: x for x in nomega["suppliers"]}
    assert sup[MEGA]["excluded"] and sup[MEGA]["rows"] == 0
    assert SC.recommend(res) == 0                                               # 缺得最少的里面最便宜


def test_scenario_rules_are_restricted():
    rules = [dict(target=MEGA, name="MEGA", min_colli=0, min_amount=1000, mode="all", allow_drop=True,
                  manual_extra=0, drop_extra=0)]
    res = SC.compute_scenarios(ROWS, H, rules, SC.DEFAULT_SCENARIOS[:2])
    assert [r["target"] for r in res[0]["rules"]] == [MEGA]
    assert res[1]["rules"] == []                                                 # 排除 MEGA 时没有 MEGA 的起订规则
    assert res[0]["rules"][0]["status"] in ("已放弃", "未达标", "已凑单达标", "已达标")


def test_compact_and_defs():
    res = SC.compute_scenarios(ROWS, H, [], SC.DEFAULT_SCENARIOS)
    c = SC.compact(res, 2)
    assert [x["chosen"] for x in c] == [False, False, True] and "assignment" not in c[0]
    assert SC.scenario_defs({}) == SC.DEFAULT_SCENARIOS
    assert SC.scenario_defs({"scenarios": [{"name": "X", "exclude": []}]})[0]["name"] == "X"
    assert SC.excluded_headers(H, ["Mega", " "]) == [MEGA]


def test_history_entry_has_store_and_plans():
    from datetime import datetime
    import history
    res = SC.compute_scenarios(ROWS, H, [], SC.DEFAULT_SCENARIOS)
    summary = {"main_file": "确认订单_FOIANO_20261018_1430.xlsx", "suppliers": [], "scenario": res[1]["name"],
               "scenarios": SC.compact(res, 1), "state": {"ordine_file": "/x/ORDINE/a.xlsx"}}
    e = history._entry(summary, "20261018_143000", datetime(2026, 10, 18, 14, 30))
    assert e["store"] == "FOIANO" and e["scenario"] == "排除 MEGA"
    assert [(x["name"], x["chosen"], x["missing"]) for x in e["scenarios"]] == [
        ("全部供应商", False, 1), ("排除 MEGA", True, 2), ("排除 NEW GRIECO", False, 1)]


def test_service_uses_chosen_plan():
    """比价主流程：choose 选了「排除 MEGA」→ 分配按这个方案，结果里记下几种方案和选用的"""
    from openpyxl import Workbook
    import service
    ws = Workbook().active
    seen = {}

    def choose(results):
        seen["names"] = [r["name"] for r in results]
        return 1

    summary = {}
    st = {"min_order_enabled": False, "scenarios_enabled": True}
    a = service._select_suppliers(ROWS, H, ws, lambda *x: None, st, summary, choose=choose)
    assert seen["names"] == ["全部供应商", "排除 MEGA", "排除 NEW GRIECO"]
    assert MEGA not in a.values() and a[2] == GRIECO and 4 not in a
    assert summary["scenario"] == "排除 MEGA" and [x["chosen"] for x in summary["scenarios"]] == [False, True, False]
    assert any(c["source"] == "方案排除" for c in summary["changes"])
    assert service._select_suppliers(ROWS, H, ws, lambda *x: None, st, {}, choose=lambda r: None) is None   # 取消
    b = service._select_suppliers(ROWS, H, ws, lambda *x: None, dict(st, scenarios_enabled=False), {}, choose=choose)
    assert b[2] == MEGA                                                        # 关掉多方案：和以前一样


def test_exclude_by_supplier_name_when_filename_has_no_keyword():
    """MEGA 的文件叫 formattato_059603(2).xlsx：按名称（起订规则 / 自己起的名字）也能排除，不会和「全部」一模一样"""
    import supplier_names as SN
    F = "formattato_059603(2).xlsx"
    rows = [dict(r, unit={(F if h == MEGA else h): v for h, v in r["unit"].items()},
                 packs={(F if h == MEGA else h): v for h, v in r["packs"].items()},
                 prices={(F if h == MEGA else h): v for h, v in r["prices"].items()},
                 amt={(F if h == MEGA else h): v for h, v in r["amt"].items()}) for r in ROWS]
    heads = [F, GRIECO, MELONI]
    st = {"rules": [{"keyword": "formattato_059603(2)", "name": "MEGA CASH"}]}
    labels = SN.labels(heads, st)
    assert labels[F] == "MEGA CASH" and labels[GRIECO] == "NEW GRIECO"
    plain = SC.compute_scenarios(rows, heads, [], SC.DEFAULT_SCENARIOS)
    assert plain[1]["unmatched"] == ["mega"] and plain[1]["same_as"] == 0      # 以前的问题：找不到 → 一模一样
    res = SC.compute_scenarios(rows, heads, [], SC.DEFAULT_SCENARIOS, labels)
    assert res[1]["excluded"] == [F] and res[1]["unmatched"] == [] and res[1]["same_as"] is None
    assert res[1]["suppliers"][0]["label"] == "MEGA CASH"


def test_difference_is_split_into_price_and_lost_items():
    res = SC.compute_scenarios(ROWS, H, [], SC.DEFAULT_SCENARIOS)
    for s in res:
        assert round(s["vs_first"], 6) == round(s["price_diff"] - s["lost_value"] + s["gained_value"], 6)
    nomega = res[1]
    # 排除 MEGA：2 号 MEGA→GRIECO（贵 2×6×0.2），4 号买不到（基准 3×6×0.5）
    assert round(nomega["price_diff"], 2) == round(2 * 6 * 0.2, 2)
    assert nomega["lost_rows"] == 1 and round(nomega["lost_value"], 2) == 9.0
    assert nomega["switched"] == 1 and nomega["colli"] == 2 + 1 + 1 + 0 and nomega["need"] == 5
    d = {m["row"]: m for m in res[0]["diff"]}
    assert set(d) == {2, 3, 4, 5}                          # 6 号谁都没有、7 号不订 → 不算差异
    assert d[4]["sups"] == [MEGA, "", MEGA] and d[4]["amounts"][1] is None


def test_supplier_names_carry_over_and_rule_matching():
    import supplier_names as SN
    from settings import match_rules
    st = {"rules": [{"keyword": "formattato_059603(2)", "name": "MEGA CASH", "enabled": True}], "supplier_names": {}}
    old = ["formattato_059603(2).xlsx", "Listino_01.xlsx"]
    lb = SN.labels(old, st)
    assert SN.carry_over(st, [("formattato_061020.xlsx", "formattato_059603(2).xlsx")], lb)
    assert st["supplier_names"] == {"formattato_061020.xlsx": "MEGA CASH"}
    new = ["formattato_061020.xlsx", "Listino_01.xlsx"]
    assert match_rules(st["rules"], new)[0] == []                     # 只按文件名：下周就找不到了
    m, _ = match_rules(st["rules"], new, SN.labels(new, st))
    assert [h for _, h in m] == ["formattato_061020.xlsx"]
