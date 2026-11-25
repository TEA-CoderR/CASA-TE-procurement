"""新订货算法（forecast.py）"""
import math

import forecast as F

P = dict(F.DEFAULT_PARAMS, check_days=0)   # R = 7，L = 5，服务水平 95%；这些测试只看周期本身，不用检查间隔


def test_demand_rate_weights_and_new_products():
    steady = [7.0] * 26                       # 每周 7 件 = 每天 1 件
    d, age = F.demand_rate(steady)
    assert abs(d - 1.0) < 1e-9 and age == 26
    rising = [14.0] * 4 + [7.0] * 22          # 最近 4 周翻倍
    d, _ = F.demand_rate(rising)
    assert 1.0 < d < 2.0                      # 加权：比 1 大，但不会直接跳到 2
    new = [7.0, 7.0] + [0.0] * 24             # 新品，只卖了 2 周 → 至少按 4 周平均（第一两周的量不代表以后）
    d, age = F.demand_rate(new)
    assert age == 2 and abs(d - 0.5) < 1e-9
    new6 = [7.0] * 6 + [0.0] * 20             # 新品 6 周：不被 26 周平均拉低
    d, age = F.demand_rate(new6)
    assert age == 6 and abs(d - 1.0) < 1e-9
    old = [1.0] + [0.0] * 29 + [1.0] + [0.0] * 29   # 老商品：半年前卖过，这周偶尔卖 1 件 → 不当新品
    d, age = F.demand_rate(old)
    assert age == 26 and d * 30 < 1.0         # 以前会算成月销 4.3
    assert F.demand_rate([0.0] * 26) == (0.0, 0)


def test_poisson_for_slow_movers():
    assert F.poisson_quantile(0.4, 0.95) == 2
    assert F.poisson_quantile(0.0, 0.95) == 0
    # 每月卖 1 件：期间需求 12 天 ≈ 0.4 件 → 泊松 95% 是 2 件，但安全库存最多 7 天（2026-11-03 慢卖的也限）→ 陈列 1 件
    t = F.target_stock(1 / 30, 0.5, 0, "C", dict(P, abc_service=False, cycle_c=1.0))
    assert t["S"] == 1
    t = F.target_stock(1 / 30, 0.5, 0, "C", dict(P, abc_service=False, cycle_c=1.0, max_safety_days=60))
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
    new = [1.0, 1.0, 0.0] + [0.0] * 23                 # 新品：3 周里 2 周卖 → 还看不出来，最多 B
    assert F.adjust_tier("A", new, P) == ("B", "down")
    once = [0.0] * 9 + [2.0] + [0.0] * 16              # 10 周前第一次卖、只卖过 1 次 → C（以前当新品不降级）
    assert F.adjust_tier("A", once, P) == ("C", "down")
    half = [1.0, 0.0] * 7 + [1.0] * 12                 # 13 周里 7 周卖：稳 → A
    assert F.adjust_tier("A", half, P) == ("A", None)
    some = [1.0, 0.0, 0.0] * 5 + [1.0] * 11            # 13 周里 5 周卖：A → B
    assert F.adjust_tier("A", some, P) == ("B", "down")
    assert F.adjust_tier("C", [1.0] * 4 + [0.0] * 22, P) == ("B", "up")     # 新品每周都卖 → 升 B
    assert F.adjust_tier("B", [0.0] * 26, P) == ("B", None)                  # 没销量：不动


def test_slow_mover_safety_capped_by_max_safety_days():
    """2026-11-03：卖得慢的也受「安全库存最多 7 天」限制（MARLIA 花洒：日均 0.14、周期 60、A 类，以前 17 箱）"""
    p = dict(F.DEFAULT_PARAMS, check_days=0)
    p.update({"review_days": 60, "lead_days": 5, "max_safety_days": 7, "service": 0.95, "abc_service": True,
              "cycle_a": 1.25})
    t = F.target_stock(0.14, 0.4, 1, "A", p)
    assert abs(t["mu"] - 0.14 * 80) < 1e-6
    assert t["S"] <= t["mu"] + 0.14 * 7 + 1e-6
    assert F.order_qty(t["S"], 1) == 12
    # 放宽到 60 天：又回到按泊松分位数（不超过 97.5% 分位数）
    p["max_safety_days"] = 60
    assert F.order_qty(F.target_stock(0.14, 0.4, 1, "A", p)["S"], 1) == 17


def test_check_interval_limits_days_covered():
    """2026-11-07 用户定：采购部一两周就会再看一次 → 每次只备到下次检查（默认 14 天），不按供应商的 30 / 60 天周期压货。
    每天卖 1 件、每箱 6 件、没库存的 A 类：30 天周期以前 7 箱、60 天 13 箱，按 14 天检查 4 箱"""
    def boxes(review, check):
        q = dict(F.DEFAULT_PARAMS, review_days=review, lead_days=0, check_days=check)
        t = F.target_stock(1.0, 4.2, 0, "A", q)
        return F.to_packs(F.order_qty(t["S"], 0), 6, 0, 1.0, q, "A", cycle=review)
    assert boxes(30, 0) == 7 and boxes(60, 0) == 13               # 不用检查间隔：和以前一样
    assert boxes(30, 14) == boxes(60, 14) == 4                    # 供应商周期再长也只备到下次检查
    assert boxes(7, 14) == boxes(7, 0)                            # 周期本来就比检查间隔短：照周期
    assert F.review_used(dict(F.DEFAULT_PARAMS, review_days=60)) == 14 and F.DEFAULT_PARAMS["check_days"] == 14
    assert F.review_used(dict(F.DEFAULT_PARAMS, review_days=30, check_days=0), cycle=60) == 60


def test_slowdown_uses_recent_four_weeks_when_in_stock():
    """规则 A（2026-11-07）：开学季文具 9–10 月卖得多、11 月一下子慢了（一直有货）→ 按最近 4 周算，不再多订"""
    import forecast as F
    p = dict(F.DEFAULT_PARAMS, season=False, review_days=30)
    weekly = [3, 2, 3, 2] + [16] * 8 + [0] * 48          # 最近 4 周 10 件，之前 8 周每周 16 件
    a = F.analyze(weekly, 20, "A", p)
    assert a["slow_from"] is not None and abs(a["daily"] - 10 / 28) < 1e-9
    assert a["qty"] == 0                                  # 20 件按每天 0.36 件够卖 50 多天
    b = F.analyze(weekly, 20, "A", dict(p, slowdown=False))
    assert b["slow_from"] is None and b["qty"] > 0         # 不用规则 A：还会订
    # 库存很少（可能是断货才卖得少）→ 不用规则 A
    c = F.analyze(weekly, 2, "A", p)
    assert c["slow_from"] is None
    # 只是稍微慢一点（没到一半）→ 不用
    d = F.analyze([10, 10, 10, 10] + [16] * 8 + [0] * 48, 60, "A", p)
    assert d["slow_from"] is None
    # 新品（不满 8 周）→ 不用
    e = F.analyze([1, 1, 1, 1, 30, 30] + [0] * 54, 50, "A", p)
    assert e["slow_from"] is None


def test_check_cover_ignores_slowdown():
    """检查门店清单判断砍不砍时，还按原来的加权日均算够卖天数（规则 A 不让砍得更多）"""
    import check_rules as CR
    x = {"daily0": 0.3, "slow_from": 1.2, "inv0": 36}
    assert abs(CR._cover_now(x) - 30) < 1e-9


def test_stockout_weeks_do_not_pull_daily_down():
    """2026-11-25 用户：断货的那几周不拉低日均"""
    import forecast as F
    w = [7, 7, 7, 0, 0, 0, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7]
    filled, n = F.fill_stockouts(w, inv=10)
    assert n == 3 and filled[3:6] == [7, 7, 7]
    assert F.analyze(w, 10, "B", F.DEFAULT_PARAMS)["daily"] > F.demand_rate(w)[0]
    # 最近几周没卖、店里还有货：真卖慢了，不补
    assert F.fill_stockouts([0, 0, 0] + [7] * 23, inv=10)[1] == 0
    # 现在店里没货：最近几周也算断货
    assert F.fill_stockouts([0, 0, 0] + [7] * 23, inv=0)[1] == 3
    # 偶尔才卖的不补；关掉开关不补
    assert F.fill_stockouts([1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0], inv=3)[1] == 0
    assert F.fill_stockouts(w, 10, {"stockout_fill": False})[1] == 0
