"""批发商比价：几种方案（全部供应商 / 不含 MEGA / 不含 NEW GRIECO）同时算、对比"""
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
    assert [r["name"] for r in res] == ["全部供应商", "不含 MEGA", "不含 NEW GRIECO"]
    allp, nomega, nogrieco = res
    assert allp["assignment"] == {2: MEGA, 3: GRIECO, 4: MEGA, 5: GRIECO, 7: MELONI}
    assert nomega["excluded"] == [MEGA]
    assert nomega["assignment"].get(2) == GRIECO and 4 not in nomega["assignment"]
    assert nogrieco["assignment"].get(3) == MEGA and nogrieco["assignment"].get(5) == MELONI
    # 采购总额 = 箱数 × 每箱件数 × 件价
    assert round(allp["total"], 2) == round(2 * 6 * 1.0 + 1 * 6 * 1.8 + 3 * 6 * 0.5 + 1 * 6 * 0.9, 2)
    # 缺失：谁都没有的 6 号；不含 MEGA 时还缺只有 MEGA 有的 4 号（不订的 7 号不算）
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
    assert res[1]["rules"] == []                                                 # 不含 MEGA 时没有 MEGA 的起订规则
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
    assert e["store"] == "FOIANO" and e["scenario"] == "不含 MEGA"
    assert [(x["name"], x["chosen"], x["missing"]) for x in e["scenarios"]] == [
        ("全部供应商", False, 1), ("不含 MEGA", True, 2), ("不含 NEW GRIECO", False, 1)]


def test_service_uses_chosen_plan():
    """比价主流程：choose 选了「不含 MEGA」→ 分配按这个方案，结果里记下几种方案和选用的"""
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
    assert seen["names"] == ["全部供应商", "不含 MEGA", "不含 NEW GRIECO"]
    assert MEGA not in a.values() and a[2] == GRIECO and 4 not in a
    assert summary["scenario"] == "不含 MEGA" and [x["chosen"] for x in summary["scenarios"]] == [False, True, False]
    assert any(c["source"] == "方案排除" for c in summary["changes"])
    assert service._select_suppliers(ROWS, H, ws, lambda *x: None, st, {}, choose=lambda r: None) is None   # 取消
    b = service._select_suppliers(ROWS, H, ws, lambda *x: None, dict(st, scenarios_enabled=False), {}, choose=choose)
    assert b[2] == MEGA                                                        # 关掉多方案：和以前一样
