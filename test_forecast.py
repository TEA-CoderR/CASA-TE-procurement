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
    assert (t["S"] - mu) / 10 <= 7 + 1e-9               # 安全库存最多够卖 7 天
    big = F.target_stock(10.0, 500.0, 0, "B", P)       # 波动极大 → 被上限截住
    assert abs(big["S"] - (mu + 10 * 7)) < 1e-9
    # 订货周期 30 天时，快销品照样有安全库存（以前「平均压货 14 天」会把它压成 0）
    m = F.target_stock(10.0, 30.0, 0, "B", dict(P, review_days=30, lead_days=0))
    assert m["S"] > m["mu"]


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
    """凑不成整箱：少订后够安全库存（一个周期 × A 90% / B 70% / C 50%）就少订，不够才多订一箱"""
    q = dict(P, review_days=30, lead_days=0)
    # 每天 1 件，一个周期 30 件；缺 13 件、一箱 12 件，现有 20 件
    # 少订 → 20 + 12 = 32 ≥ A 的 27 → 1 箱，不为 1 件多订一箱
    assert F.to_packs(13, 12, 20, 1.0, q, tier="A") == 1
    # 现有 10 件：10 + 12 = 22 < A 的 27 → 2 箱；B 要 21 → 够 → 1 箱；C 要 15 → 1 箱
    assert F.to_packs(13, 12, 10, 1.0, q, tier="A") == 2
    assert F.to_packs(13, 12, 10, 1.0, q, tier="B") == 1
    assert F.to_packs(13, 12, 10, 1.0, q, tier="C") == 1
    # 一箱都不到：缺 2 件、一箱 24，现有 20 件 ≥ C 的 15 → 不订；现有 5 件 → 订 1 箱
    assert F.to_packs(2, 24, 20, 1.0, q, tier="C") == 0
    assert F.to_packs(2, 24, 5, 1.0, q, tier="C") == 1
    # 整箱正好、没有销量（只为陈列量）
    assert F.to_packs(24, 12, 0, 1.0, q) == 2
    assert F.to_packs(3, 12, 0, 0.0, q) == 0
    # 供应商自己的订货周期（60 天）：要的安全库存更多
    assert F.to_packs(13, 12, 20, 1.0, q, tier="B", cycle=60) == 2       # 32 < 60 × 70% = 42


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


def test_adjust_tier_by_sales_weeks():
    """金额定等级，动销兜底：13 周里 ≥ 10 周有卖 → 至少 B；≤ 3 周 → 最多 C；新品不降级"""
    steady = [1.0] * 13 + [1.0] * 13                # 每周都卖
    rare = [0, 0, 5, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0] + [1.0] * 13   # 13 周里只有 2 周卖
    assert F.adjust_tier("C", steady, P) == ("B", "up")
    assert F.adjust_tier("A", steady, P) == ("A", None)
    assert F.adjust_tier("A", rare, P) == ("C", "down")
    assert F.adjust_tier("C", rare, P) == ("C", None)
    new = [1.0, 1.0, 0.0] + [0.0] * 23                 # 新品：3 周里 2 周卖 → 不降级
    assert F.adjust_tier("A", new, P) == ("A", None)
    assert F.adjust_tier("C", [1.0] * 4 + [0.0] * 22, P) == ("B", "up")     # 新品每周都卖 → 升 B
    assert F.adjust_tier("B", [0.0] * 26, P) == ("B", None)                  # 没销量：不动
