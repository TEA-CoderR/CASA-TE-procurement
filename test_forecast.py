"""新订货算法（forecast.py）"""
import math

import forecast as F

P = dict(F.DEFAULT_PARAMS)   # R = 7，L = 5，服务水平 95%，最多压货 14 天


def test_demand_rate_weights_and_new_products():
    steady = [7.0] * 26                       # 每周 7 件 = 每天 1 件
    d, age = F.demand_rate(steady)
    assert abs(d - 1.0) < 1e-9 and age == 26
    rising = [14.0] * 4 + [7.0] * 22          # 最近 4 周翻倍
    d, _ = F.demand_rate(rising)
    assert 1.0 < d < 2.0                      # 加权：比 1 大，但不会直接跳到 2
    new = [7.0, 7.0] + [0.0] * 24             # 新品，只卖了 2 周 → 按 2 周算，不被 26 周平均拉低
    d, age = F.demand_rate(new)
    assert age == 2 and abs(d - 1.0) < 1e-9
    assert F.demand_rate([0.0] * 26) == (0.0, 0)


def test_poisson_for_slow_movers():
    assert F.poisson_quantile(0.4, 0.95) == 2
    assert F.poisson_quantile(0.0, 0.95) == 0
    # 每月卖 1 件：期间需求 12 天 ≈ 0.4 件 → 目标 2 件（泊松 95%）
    t = F.target_stock(1 / 30, 0.5, 0, "C", dict(P, abc_service=False, cycle_c=1.0))
    assert t["S"] == 2


def test_target_stock_normal_and_cap():
    t = F.target_stock(10.0, 20.0, 0, "B", P)          # 每天 10 件，周波动 20
    mu = 10 * 12
    assert t["mu"] == mu
    assert t["S"] > mu                                 # 有安全库存
    assert (t["S"] - mu) / 10 + 7 / 2 <= 14 + 1e-9     # 平均库存不超过 14 天
    big = F.target_stock(10.0, 500.0, 0, "B", P)       # 波动极大 → 被上限截住
    assert abs(big["S"] - (mu + 10 * (14 - 3.5))) < 1e-9


def test_abc_service_levels_and_pareto():
    assert F.service_for("A", P) > F.service_for("B", P) > F.service_for("C", P)
    t = F.abc_by_revenue({1: 800, 2: 150, 3: 40, 4: 10, 5: 0})
    assert t == {1: "A", 2: "B", 3: "C", 4: "C", 5: "C"}


def test_order_and_negative_stock():
    assert F.order_qty(20, 5) == 15
    assert F.order_qty(20, -10) == 20                   # 负库存当 0
    assert F.order_qty(20, 5, on_order=10) == 5         # 扣在途
    assert F.order_qty(3, 10) == 0


def test_smart_pack_rounding():
    # 缺 13 件、一箱 12 件：零头 1 件 < 30% → 只订 1 箱
    assert F.to_packs(13, 12, 0, 1.0, P) == 1
    # 缺 16 件：零头 4 件 ≥ 3.6 → 2 箱
    assert F.to_packs(16, 12, 0, 1.0, P) == 2
    # 缺 2 件、一箱 24：现有 1 件，每天 0.1 → 12 天要卖 1.2 件 > 1 → 会卖断 → 订 1 箱
    assert F.to_packs(2, 24, 1, 0.1, P) == 1
    # 同样缺 2 件，但现有 5 件够卖到下次到货 → 这次不订
    assert F.to_packs(2, 24, 5, 0.1, P) == 0


def test_seasonal_factor():
    w = [5.0] * 60
    assert F.seasonal_factor(w, 12) == 1.0
    w = [5.0] * 60
    for i in range(50, 52):         # 去年同期接下来 2 周卖得多
        w[i] = 15.0
    assert F.seasonal_factor(w, 12) > 1.5
    assert F.seasonal_factor([1.0] * 30, 12) == 1.0      # 不够一年 → 不调整


def test_decision_consistent_with_qty():
    a = F.analyze([7.0] * 60, 2, "A", P)                 # 每天 1 件，库存 2 件 < 到货 5 天
    assert a["decision"].startswith("🔴") and a["qty"] > 0
    a = F.analyze([7.0] * 60, 8, "B", P)                 # 够卖 8 天：到货前不断，下次到货前会断
    assert a["decision"].startswith("🟡") and a["qty"] > 0
    a = F.analyze([7.0] * 60, 40, "B", P)                # 够卖 40 天 → 不订
    assert a["qty"] == 0 and a["decision"].startswith("🟢")
    a = F.analyze([0.0] * 60, 10, "C", P, total_inv=10)  # 26 周没卖
    assert a["qty"] == 0 and "不动销" in a["decision"]
    # 平均库存天数在用户要求的 7–14 天里：目标库存 / 日均 − 半个下单间隔
    a = F.analyze([7.0] * 60, 0, "B", P)
    avg_days = a["target"] / a["daily"] - P["lead_days"] - P["review_days"] / 2
    assert 3 <= avg_days <= 14
    assert math.isinf(F.analyze([0.0] * 60, 0, "C", P)["cover_days"])


def test_abc_cycle_factor():
    """A 类订够 1.25 个周期、C 类 0.75 个（只改备货天数）"""
    q = dict(P, review_days=30, lead_days=0, max_stock_days=60, abc_service=False)
    a = F.target_stock(10.0, 20.0, 0, "A", q)
    b = F.target_stock(10.0, 20.0, 0, "B", q)
    c = F.target_stock(10.0, 20.0, 0, "C", q)
    assert a["mu"] == 10 * 37.5 and b["mu"] == 10 * 30 and c["mu"] == 10 * 22.5
    assert a["S"] > b["S"] > c["S"]
    same = dict(q, cycle_a=1.0, cycle_c=1.0)
    assert F.target_stock(10.0, 20.0, 0, "A", same)["S"] == F.target_stock(10.0, 20.0, 0, "C", same)["S"]
