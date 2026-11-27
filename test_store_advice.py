"""给门店扫码枪的建议箱数（store_advice，2026-11-23）：一周订一次的节奏 + 几道合理性检查"""
import math
from datetime import date, datetime

import forecast as F
import store_advice as SA

RULE = {"day": 7, "time": "10:00"}          # 星期天 10:00 截止


def _row(daily, inv, pack, tier="B", algo=None, sigma=None, **kw):
    """像 reorder_service.to_rows 的一行：suggested 按 algo 的目标库存算（compute_suggestions 做的事）"""
    t = F.target_stock(daily, daily * 7 * 0.5 if sigma is None else sigma, inv, tier, algo)
    x = {"daily": daily, "inv0": inv, "pack": pack, "tier": tier, "season": 1.0,
         "suggested": max(0, int(math.ceil(t["S"] - inv - 1e-9)))}
    x.update(kw)
    return x


def _ctx(now=datetime(2026, 11, 23, 9, 0), latest=datetime(2026, 11, 22, 20, 10), lead=3):
    return SA.context(latest, RULE, lead, now=now)


def test_calendar_covers_until_the_next_delivery():
    c = _ctx()                    # 星期一早上：数据 = 星期一（星期天的销售同步完），星期天截止，星期三到
    assert (c["dataDate"], c["deadline"], c["gap"], c["horizon"]) == ("2026-11-23", "2026-11-29", 6, 16)
    # 星期六：只剩 1 天到截止
    assert SA.context(datetime(2026, 11, 27, 20), RULE, 3, now=datetime(2026, 11, 28, 9))["horizon"] == 11
    # 星期天截止过了：下星期的
    c = SA.context(datetime(2026, 11, 28, 20), RULE, 3, now=datetime(2026, 11, 29, 12))
    assert c["deadline"] == "2026-12-06" and c["gap"] == 7
    # 星期天截止前：就是今天
    assert SA.next_deadline(datetime(2026, 11, 29, 9, 59), RULE) == date(2026, 11, 29)
    # 数据库一周没同步：照算，但要标出来
    assert SA.context(datetime(2026, 11, 14, 20), RULE, 3, now=datetime(2026, 11, 23, 9))["stale"]


def test_rounding_uses_the_same_period_as_the_target():
    c = _ctx()
    a = SA.algo_for({"engine": "v2", "lead_days": 5, "review_days": 7, "check_days": 14, "cycle_a": 1.25}, c)
    assert F.review_used(a, SA.REVIEW) + a["lead_days"] == c["horizon"]
    assert F.cycle_factor("A", a) == 1.0


def test_advice_follows_the_weekly_calendar():
    """A 类每天卖 2 件、店里 10 件、一箱 6 件。星期一看要够卖到下下个星期三（16 天），星期六看只要 11 天：
    星期一到星期六之间卖掉的，星期六的数据里库存已经少了，所以两天的建议到截止时是一样的货"""
    mon = _ctx()
    sat = SA.context(datetime(2026, 11, 27, 20), RULE, 3, now=datetime(2026, 11, 28, 9))
    am, asat = SA.algo_for(dict(F.DEFAULT_PARAMS), mon), SA.algo_for(dict(F.DEFAULT_PARAMS), sat)
    b_mon, _ = SA.advise(_row(2.0, 20, 6, "A", am), am, mon)
    b_sat, _ = SA.advise(_row(2.0, 10, 6, "A", asat), asat, sat)      # 5 天卖了 10 件
    assert b_mon == b_sat
    # 订完够卖：到下一次到货，不多于 + 安全库存 7 天 + 一箱
    after = (20 + b_mon * 6) / 2.0
    assert mon["horizon"] * 0.9 <= after <= mon["horizon"] + 7 + 3


def test_transit_subtracted_in_pieces_not_wholesaler_boxes():
    c = _ctx()
    a = SA.algo_for(dict(F.DEFAULT_PARAMS), c)
    x = _row(2.0, 0, 6, "B", a)
    b0, _ = SA.advise(x, a, c, transit_pcs=0)
    b1, _ = SA.advise(x, a, c, transit_pcs=24)        # 批发商的 2 箱 × 12 件 = 门店的 4 箱
    assert b0 - b1 == 4
    assert SA.advise(x, a, c, transit_pcs=500)[0] == 0


def test_empty_shelf_of_an_item_that_sells_is_not_left_empty():
    c = _ctx()
    a = SA.algo_for(dict(F.DEFAULT_PARAMS), c)
    # 最近 3 周一件没卖（货架空了），加权日均被拉到 0.05，过去一年每天 1 件
    x = _row(0.05, 1, 6, "B", a, long_daily=1.0, zero_weeks=3)
    b, flags = SA.advise(x, a, c)
    assert "censored" in flags and b >= 2
    # 真的卖不动（库存还很多）：不补算
    x = _row(0.05, 40, 6, "B", a, long_daily=1.0, zero_weeks=3)
    assert "censored" not in SA.advise(x, a, c)[1]


def test_absurd_need_is_capped():
    c = _ctx()
    a = SA.algo_for(dict(F.DEFAULT_PARAMS), c)
    x = _row(1.0, 0, 6, "B", a)
    x["suggested"] = 600                   # 数据出错（一次大单 / 单位错）
    b, flags = SA.advise(x, a, c)
    assert "capped" in flags
    assert (b - 1) * 6 / 1.0 <= c["horizon"] + 7 + SA.CAP_EXTRA


def test_slow_mover_with_stock_gets_nothing_and_no_pack_no_advice():
    c = _ctx()
    a = SA.algo_for(dict(F.DEFAULT_PARAMS), c)
    assert SA.advise(_row(0.1, 6, 12, "C", a), a, c)[0] == 0
    assert SA.advise(_row(1.0, 0, 0, "B", a), a, c)[0] is None


def test_lead_days_from_tracked_deliveries():
    rows = [{"origin": "wholesale", "date": date(2026, 11, d), "arrived": date(2026, 11, d + k)}
            for d, k in ((2, 2), (9, 3), (16, 3), (16, 4), (1, 25))]
    rows.append({"origin": "app", "date": date(2026, 11, 1), "arrived": date(2026, 11, 20)})
    assert SA.lead_from_tracking(rows) == (3, 4)
    assert SA.lead_from_tracking(rows[:2]) == (SA.DEFAULT_LEAD, 2)


def test_under_a_month_gets_one_box_unless_the_box_is_too_big():
    """2026-11-27 用户：一个月的库存可以接受。够卖不到 30 天、一箱到了以后不超过 60 天 → 1 箱"""
    c = _ctx()
    a = SA.algo_for(dict(F.DEFAULT_PARAMS), c)
    x = _row(0.5, 10, 6, "B", a)              # 够卖 20 天，一箱后 32 天
    b, flags = SA.advise(x, a, c)
    assert b == 1 and "month" in flags
    assert SA.advise(_row(0.5, 10, 24, "B", a), a, c)[0] == 0      # 一箱 24 件 → 68 天，太多
    assert SA.advise(_row(0.5, 16, 6, "B", a), a, c)[0] == 0       # 已经够卖 32 天
    assert SA.advise(_row(0.5, 4, 6, "B", a), a, c, transit_pcs=12)[0] == 0   # 含在途 32 天
